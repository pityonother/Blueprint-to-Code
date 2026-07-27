"""Atomic parallel-snapshot builder for ARK Knowledge Base vNext."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .ontology import load_ontology
from .storage import (
    CACHE_SCHEMA_SQL,
    CATALOG_SCHEMA_SQL,
    FULL_CORE_SCHEMA_SQL,
    SEARCH_SCHEMA_SQL,
    build_cache_database,
    build_catalog_database,
    build_core_database,
    build_search_database,
    database_metrics,
)


DATABASE_NAMES = (
    "catalog.sqlite",
    "core.sqlite",
    "search.sqlite",
    "cache.sqlite",
)
SNAPSHOT_SCHEMA = "ark-kb-vnext-snapshot/v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _write_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _validate_output_root(output_dir: Path) -> None:
    if not output_dir.exists():
        return
    existing = [item for item in output_dir.iterdir() if item.name != ".build"]
    if not existing:
        return
    marker = output_dir / "manifests" / "current.json"
    if not marker.is_file():
        raise ValueError(
            f"Refusing to modify non-vNext directory without manifest: {output_dir}"
        )
    payload = json.loads(marker.read_text(encoding="utf-8"))
    if payload.get("schema") != SNAPSHOT_SCHEMA:
        raise ValueError(f"Unknown vNext manifest schema in {output_dir}")


def _promote_snapshot(
    *,
    staging: Path,
    output_dir: Path,
    manifest: dict[str, object],
) -> None:
    manifests = output_dir / "manifests"
    snapshots = output_dir / "snapshots"
    domain_exports = output_dir / "domain_exports"
    manifests.mkdir(parents=True, exist_ok=True)
    snapshots.mkdir(parents=True, exist_ok=True)
    domain_exports.mkdir(parents=True, exist_ok=True)
    current = manifests / "current.json"
    if current.is_file():
        previous = json.loads(current.read_text(encoding="utf-8"))
        previous_id = str(previous.get("buildId") or "unknown")
        archive = snapshots / previous_id
        archive.mkdir(parents=True, exist_ok=True)
        for name in DATABASE_NAMES:
            source = output_dir / name
            if source.is_file():
                os.replace(source, archive / name)
        _write_json(archive / "manifest.json", previous)
    for name in DATABASE_NAMES:
        os.replace(staging / name, output_dir / name)
    build_id = str(manifest["buildId"])
    _write_json(manifests / f"{build_id}.json", manifest)
    _write_json(current, manifest)
    _write_text(manifests / "catalog_schema.sql", CATALOG_SCHEMA_SQL)
    _write_text(manifests / "core_schema.sql", FULL_CORE_SCHEMA_SQL)
    _write_text(manifests / "search_schema.sql", SEARCH_SCHEMA_SQL)
    _write_text(manifests / "cache_schema.sql", CACHE_SCHEMA_SQL)


def build_vnext_snapshot(
    *,
    project_root: Path,
    discovery_database: Path,
    legacy_kb_root: Path,
    capture_root: Path,
    native_root: Path,
    output_dir: Path,
    full_snapshot: bool = False,
    generated_at: str | None = None,
) -> dict[str, object]:
    """Build all four stores in staging, validate, then atomically promote."""

    del capture_root, native_root
    project_root = project_root.resolve()
    discovery_database = discovery_database.resolve()
    output_dir = output_dir.resolve()
    if not full_snapshot:
        raise ValueError("--full-snapshot is required for the first vNext build")
    if not discovery_database.is_file():
        raise FileNotFoundError(discovery_database)
    _validate_output_root(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_root = output_dir / ".build"
    work_root.mkdir(parents=True, exist_ok=True)
    generated_at = generated_at or datetime.now(UTC).isoformat(timespec="seconds")
    discovery_sha = _sha256_file(discovery_database)
    build_id = (
        generated_at.replace("-", "").replace(":", "").replace("+00:00", "")
        + "-"
        + discovery_sha[:12]
    )
    staging = Path(
        tempfile.mkdtemp(prefix=f"{build_id}.", dir=work_root)
    )
    ontology = load_ontology(project_root / "ontology")
    try:
        catalog_counts = build_catalog_database(
            discovery_path=discovery_database,
            output_path=staging / "catalog.sqlite",
            source_fingerprint=discovery_sha,
            generated_at=generated_at,
        )
        core_counts = build_core_database(
            discovery_path=discovery_database,
            output_path=staging / "core.sqlite",
            source_fingerprint=discovery_sha,
            generated_at=generated_at,
            ontology=ontology,
            legacy_kb_root=legacy_kb_root,
        )
        search_counts = build_search_database(
            core_path=staging / "core.sqlite",
            output_path=staging / "search.sqlite",
            source_fingerprint=discovery_sha,
            generated_at=generated_at,
        )
        cache_counts = build_cache_database(
            output_path=staging / "cache.sqlite",
            source_fingerprint=discovery_sha,
            generated_at=generated_at,
        )
        metrics = {
            name: database_metrics(staging / name)
            for name in DATABASE_NAMES
        }
        failures = {
            name: value
            for name, value in metrics.items()
            if value["integrity"] != "ok"
            or int(value["foreignKeyViolations"]) != 0
        }
        if failures:
            raise ValueError(f"vNext database validation failed: {failures}")
        manifest: dict[str, object] = {
            "schema": SNAPSHOT_SCHEMA,
            "buildId": build_id,
            "generatedAt": generated_at,
            "source": {
                "kind": "discovery",
                "uri": "discovery://ark/full-snapshot",
                "sha256": discovery_sha,
            },
            "ontologyVersion": ontology.version,
            "counts": {
                "catalog": catalog_counts,
                "core": core_counts,
                "search": search_counts,
                "cache": cache_counts,
            },
            "databases": metrics,
            "cutover": {
                "mode": "shadow",
                "defaultQuerySource": "legacy",
                "reason": "quality gates have not run yet",
            },
        }
        _promote_snapshot(
            staging=staging,
            output_dir=output_dir,
            manifest=manifest,
        )
        return {
            "status": "complete",
            "buildId": build_id,
            "output": str(output_dir),
            "sourceSha256": discovery_sha,
            "counts": manifest["counts"],
            "databases": metrics,
            "cutover": manifest["cutover"],
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)
