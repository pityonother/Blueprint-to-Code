"""Project-level Blueprint ↔ Native evidence resolution and persistence."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .evidence_repository import (
    evidence_state_metadata,
    open_bound_evidence_database,
    resolve_asset_evidence_state,
)
from .evidence_schema import parse_evidence_ref
from .native_evidence_store import parse_native_evidence_id, sha256_file


HYBRID_EVIDENCE_SCHEMA = "blueprint-to-code-hybrid-edges/v1"
HYBRID_MANIFEST_SCHEMA = "blueprint-to-code-hybrid-manifest/v1"
HYBRID_SQLITE_SCHEMA = "blueprint-to-code-hybrid-sqlite/v1"
HYBRID_SQLITE_USER_VERSION = 1


class HybridEvidenceArtifactInvalid(ValueError):
    """The hybrid JSON/manifest/SQLite artifact set is inconsistent."""


def _compact_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


FORWARD_RELATIONS = frozenset({"CALLS_NATIVE", "REFERENCES_NATIVE"})
INVERSE_RELATION = "CALLED_BY_BLUEPRINT"


def _stable_edge_id(
    source_id: str,
    relation: str,
    evidence_set_id: str,
) -> str:
    digest = hashlib.sha256(
        f"{source_id}\x1f{relation}\x1f{evidence_set_id}".encode("utf-8")
    ).hexdigest()[:24]
    return f"edge://{digest}"


def _objects(value: object, label: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            raise ValueError(f"{label}[{index}] must be an object")
        rows.append(row)
    return rows


def _signature_hints(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        raise ValueError("signatureHints must be a string or array")
    result = []
    seen: set[str] = set()
    for item in values:
        hint = str(item or "").strip()
        folded = hint.casefold()
        if hint and folded not in seen:
            seen.add(folded)
            result.append(hint)
    return result


def _candidate_summary(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "evidenceId": str(row.get("evidenceId") or ""),
        "name": str(row.get("name") or ""),
        "qualifiedName": str(row.get("qualifiedName") or ""),
        "owner": str(row.get("owner") or ""),
        "signature": str(row.get("signature") or ""),
        "status": str(row.get("status") or ""),
        "confidence": str(row.get("confidence") or ""),
    }


def _call_relation(call: Mapping[str, object]) -> str:
    explicit = str(call.get("relation") or "").strip().upper().replace("-", "_")
    kind = str(call.get("kind") or call.get("callKind") or "")
    normalized_kind = kind.strip().upper().replace("-", "_")
    if explicit:
        relation = explicit
    elif normalized_kind in {
        "REFERENCE",
        "REFERENCES",
        "REFERENCES_NATIVE",
        "NATIVE_REFERENCE",
    }:
        relation = "REFERENCES_NATIVE"
    else:
        relation = "CALLS_NATIVE"
    if relation not in FORWARD_RELATIONS:
        raise ValueError(
            "Blueprint-to-Native relation must be CALLS_NATIVE or "
            "REFERENCES_NATIVE"
        )
    return relation


def _call_implementation(call: Mapping[str, object]) -> tuple[str, str | None]:
    implementation = str(
        call.get("implementation")
        or call.get("implementationKind")
        or ""
    ).strip().upper().replace("-", "_")
    kind = str(call.get("kind") or call.get("callKind") or "")
    normalized_kind = kind.strip().upper().replace("-", "_")
    combined = " ".join((implementation, normalized_kind))
    if "MACRO" in combined:
        return implementation or normalized_kind or "MACRO", "BLUEPRINT_MACRO_NOT_NATIVE"
    if implementation in {
        "BLUEPRINT",
        "BLUEPRINT_FUNCTION",
        "BLUEPRINT_IMPLEMENTED",
        "BLUEPRINT_ONLY",
    } or normalized_kind in {
        "BLUEPRINT_FUNCTION",
        "BLUEPRINT_IMPLEMENTED",
    }:
        return (
            implementation or normalized_kind,
            "BLUEPRINT_IMPLEMENTATION_NOT_NATIVE",
        )
    return implementation or "UNKNOWN", None


def resolve_blueprint_native_edge(
    call: Mapping[str, object],
    native_functions: Iterable[Mapping[str, object]],
    *,
    blueprint_revision_id: str,
    blueprint_source_fingerprint: str,
    native_evidence_set_id: str,
    native_source_fingerprint: str,
) -> dict[str, Any]:
    source_id = str(call.get("evidenceId") or "").strip()
    if not source_id:
        raise ValueError("Blueprint call evidenceId is required")
    parsed = parse_evidence_ref(source_id)
    if parsed.get("kind") not in {"node", "pin"}:
        raise ValueError("Blueprint call evidenceId must identify a node or pin")
    member_name = str(call.get("memberName") or "").strip()
    if not member_name:
        raise ValueError("Blueprint call memberName is required")
    relation = _call_relation(call)
    implementation, implementation_gap = _call_implementation(call)
    owner = str(call.get("owner") or "").strip()
    hints = _signature_hints(call.get("signatureHints"))
    functions = [dict(row) for row in native_functions]

    short_matches = [
        row
        for row in functions
        if str(row.get("name") or "").casefold() == member_name.casefold()
    ]
    owner_matches = (
        [
            row
            for row in short_matches
            if str(row.get("owner") or "").casefold() == owner.casefold()
        ]
        if owner
        else short_matches
    )
    signature_matches = owner_matches
    if hints:
        signature_matches = [
            row
            for row in owner_matches
            if all(
                hint.casefold()
                in (
                    f"{row.get('qualifiedName', '')} "
                    f"{row.get('signature', '')}"
                ).casefold()
                for hint in hints
            )
        ]

    gaps: list[str] = []
    target_id = ""
    match_method = "owner-member-signature"
    if implementation_gap is not None:
        status = "NOT_RECOVERED"
        candidates = []
        gaps.append(implementation_gap)
        match_method = "implementation-kind-rejected"
    elif not short_matches:
        status = "SOURCE_NOT_AVAILABLE"
        candidates: list[dict[str, object]] = []
        gaps.append("NATIVE_MEMBER_NOT_FOUND")
    elif not owner:
        status = "AMBIGUOUS"
        candidates = short_matches
        gaps.append("BLUEPRINT_MEMBER_OWNER_REQUIRED")
        match_method = "short-name-candidates-only"
    elif not owner_matches:
        status = "SOURCE_NOT_AVAILABLE"
        candidates = short_matches
        gaps.append("NATIVE_OWNER_MEMBER_NOT_FOUND")
        match_method = "owner-member-no-match"
    elif hints and not signature_matches:
        status = "SOURCE_NOT_AVAILABLE"
        candidates = owner_matches
        gaps.append("NATIVE_SIGNATURE_HINT_NO_MATCH")
        match_method = "owner-member-signature-no-match"
    elif len(signature_matches) == 1:
        status = "CONFIRMED"
        candidates = signature_matches
        target_id = str(signature_matches[0].get("evidenceId") or "")
        parse_native_evidence_id(target_id)
    else:
        status = "AMBIGUOUS"
        candidates = signature_matches
        gaps.append("MULTIPLE_NATIVE_CANDIDATES")

    return {
        "edgeId": _stable_edge_id(
            source_id,
            relation,
            native_evidence_set_id,
        ),
        "sourceId": source_id,
        "relation": relation,
        "targetId": target_id,
        "status": status,
        "resolution": {
            "blueprintMemberName": member_name,
            "blueprintOwner": owner,
            "blueprintKind": str(call.get("kind") or call.get("callKind") or ""),
            "blueprintImplementation": implementation,
            "signatureHints": hints,
            "nativeQualifiedName": (
                str(candidates[0].get("qualifiedName") or "")
                if status == "CONFIRMED"
                else ""
            ),
            "matchMethod": match_method,
            "candidateCount": len(candidates),
            "candidates": [_candidate_summary(row) for row in candidates],
        },
        "dependencies": {
            "blueprintRevisionId": blueprint_revision_id,
            "blueprintSourceFingerprint": blueprint_source_fingerprint,
            "nativeEvidenceSetId": native_evidence_set_id,
            "nativeSourceFingerprint": native_source_fingerprint,
        },
        "evidenceSetId": native_evidence_set_id,
        "gaps": gaps,
    }


def build_hybrid_evidence_payload(
    *,
    blueprint_calls: Iterable[Mapping[str, object]],
    native_functions: Iterable[Mapping[str, object]],
    blueprint_revision_id: str,
    blueprint_source_fingerprint: str,
    native_evidence_set_id: str,
    native_source_fingerprint: str,
) -> dict[str, Any]:
    if not blueprint_revision_id:
        raise ValueError("blueprint_revision_id is required")
    if not blueprint_source_fingerprint:
        raise ValueError("blueprint_source_fingerprint is required")
    if not native_evidence_set_id:
        raise ValueError("native_evidence_set_id is required")
    if not native_source_fingerprint:
        raise ValueError("native_source_fingerprint is required")
    functions = [dict(row) for row in native_functions]
    edges = [
        resolve_blueprint_native_edge(
            call,
            functions,
            blueprint_revision_id=blueprint_revision_id,
            blueprint_source_fingerprint=blueprint_source_fingerprint,
            native_evidence_set_id=native_evidence_set_id,
            native_source_fingerprint=native_source_fingerprint,
        )
        for call in blueprint_calls
    ]
    edge_ids = [str(edge["edgeId"]) for edge in edges]
    if len(edge_ids) != len(set(edge_ids)):
        raise ValueError("Blueprint calls contain duplicate evidence IDs")
    return {
        "schema": HYBRID_EVIDENCE_SCHEMA,
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "dependencies": {
            "blueprintRevisionId": blueprint_revision_id,
            "blueprintSourceFingerprint": blueprint_source_fingerprint,
            "nativeEvidenceSetId": native_evidence_set_id,
            "nativeSourceFingerprint": native_source_fingerprint,
        },
        "edges": edges,
    }


def mark_stale_edges(
    edges: Iterable[Mapping[str, object]],
    *,
    current_blueprint_revision_id: str,
    current_blueprint_source_fingerprint: str,
    current_native_source_fingerprint: str,
    current_native_evidence_set_id: str | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for value in edges:
        edge = copy.deepcopy(dict(value))
        dependencies = edge.get("dependencies")
        dependencies = dependencies if isinstance(dependencies, dict) else {}
        stale_reasons: list[str] = []
        if (
            str(dependencies.get("blueprintRevisionId") or "")
            != current_blueprint_revision_id
        ):
            stale_reasons.append("STALE_BLUEPRINT_REVISION")
        if (
            str(dependencies.get("blueprintSourceFingerprint") or "")
            != current_blueprint_source_fingerprint
        ):
            stale_reasons.append("STALE_BLUEPRINT_SOURCE")
        if (
            str(dependencies.get("nativeSourceFingerprint") or "")
            != current_native_source_fingerprint
        ):
            stale_reasons.append("STALE_NATIVE_EVIDENCE")
        if current_native_evidence_set_id is not None and (
            str(dependencies.get("nativeEvidenceSetId") or "")
            != current_native_evidence_set_id
        ):
            stale_reasons.append("STALE_NATIVE_EVIDENCE_SET")
        if stale_reasons:
            edge["originalStatus"] = edge.get("status")
            edge["status"] = "STALE"
            gaps = [
                str(item)
                for item in edge.get("gaps", [])
                if str(item)
            ]
            edge["gaps"] = list(dict.fromkeys([*gaps, *stale_reasons]))
        result.append(edge)
    return result


def validate_hybrid_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("hybrid evidence must be an object")
    if payload.get("schema") != HYBRID_EVIDENCE_SCHEMA:
        raise ValueError(f"hybrid schema must be {HYBRID_EVIDENCE_SCHEMA!r}")
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, dict):
        raise ValueError("hybrid dependencies must be an object")
    for key in (
        "blueprintRevisionId",
        "blueprintSourceFingerprint",
        "nativeEvidenceSetId",
        "nativeSourceFingerprint",
    ):
        if not str(dependencies.get(key) or ""):
            raise ValueError(f"hybrid dependencies.{key} is required")
    edge_ids: set[str] = set()
    for index, edge in enumerate(_objects(payload.get("edges"), "edges")):
        edge_id = str(edge.get("edgeId") or "")
        if not edge_id:
            raise ValueError(f"edges[{index}].edgeId is required")
        if edge_id in edge_ids:
            raise ValueError(f"duplicate hybrid edgeId: {edge_id}")
        edge_ids.add(edge_id)
        source_id = str(edge.get("sourceId") or "")
        relation = str(edge.get("relation") or "")
        if relation not in FORWARD_RELATIONS:
            raise ValueError(
                f"edges[{index}].relation must be a Blueprint-to-Native relation"
            )
        parse_evidence_ref(source_id)
        target_id = str(edge.get("targetId") or "")
        status = str(edge.get("status") or "")
        if target_id:
            parse_native_evidence_id(target_id)
        if status == "CONFIRMED" and not target_id:
            raise ValueError(f"edges[{index}] CONFIRMED without targetId")
        resolution = edge.get("resolution")
        if not isinstance(resolution, dict):
            raise ValueError(f"edges[{index}].resolution must be an object")
        candidates = _objects(resolution.get("candidates"), "resolution.candidates")
        if int(resolution.get("candidateCount") or 0) != len(candidates):
            raise ValueError(f"edges[{index}] candidateCount does not match candidates")
    return payload


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE hybrid_metadata (
    metadata_id INTEGER PRIMARY KEY CHECK (metadata_id = 1),
    sqlite_schema TEXT NOT NULL,
    source_json_sha256 TEXT NOT NULL,
    source_json_size INTEGER NOT NULL,
    blueprint_revision_id TEXT NOT NULL,
    blueprint_source_fingerprint TEXT NOT NULL,
    native_evidence_set_id TEXT NOT NULL,
    native_source_fingerprint TEXT NOT NULL,
    generated_at_utc TEXT NOT NULL
);

CREATE TABLE hybrid_edges (
    edge_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    target_id TEXT NOT NULL,
    status TEXT NOT NULL,
    blueprint_member_name TEXT NOT NULL,
    blueprint_owner TEXT NOT NULL,
    native_qualified_name TEXT NOT NULL,
    match_method TEXT NOT NULL,
    candidate_count INTEGER NOT NULL,
    resolution_json TEXT NOT NULL,
    dependencies_json TEXT NOT NULL,
    gaps_json TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_hybrid_edges_source ON hybrid_edges(source_id);
CREATE INDEX idx_hybrid_edges_target ON hybrid_edges(target_id);
CREATE INDEX idx_hybrid_edges_status ON hybrid_edges(status);

CREATE TABLE hybrid_edge_candidates (
    edge_id TEXT NOT NULL REFERENCES hybrid_edges(edge_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    native_evidence_id TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    owner TEXT NOT NULL,
    signature TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (edge_id, ordinal)
) WITHOUT ROWID;
"""


def _build_hybrid_database(
    payload: dict[str, Any],
    source_path: Path,
    destination: Path,
) -> dict[str, Any]:
    source_bytes = source_path.read_bytes()
    source_sha = _sha256_bytes(source_bytes)
    dependencies = payload["dependencies"]
    descriptor, raw = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temp_path = Path(raw)
    try:
        connection = sqlite3.connect(temp_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(SCHEMA_SQL)
            connection.execute(f"PRAGMA user_version = {HYBRID_SQLITE_USER_VERSION}")
            connection.execute("BEGIN")
            connection.execute(
                "INSERT INTO hybrid_metadata(metadata_id, sqlite_schema, source_json_sha256, "
                "source_json_size, blueprint_revision_id, blueprint_source_fingerprint, "
                "native_evidence_set_id, native_source_fingerprint, generated_at_utc) "
                "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    HYBRID_SQLITE_SCHEMA,
                    source_sha,
                    len(source_bytes),
                    str(dependencies["blueprintRevisionId"]),
                    str(dependencies["blueprintSourceFingerprint"]),
                    str(dependencies["nativeEvidenceSetId"]),
                    str(dependencies["nativeSourceFingerprint"]),
                    str(payload.get("generatedAtUtc") or ""),
                ),
            )
            for edge in payload["edges"]:
                resolution = edge["resolution"]
                candidates = resolution.get("candidates") or []
                connection.execute(
                    "INSERT INTO hybrid_edges(edge_id, source_id, relation, target_id, status, "
                    "blueprint_member_name, blueprint_owner, native_qualified_name, match_method, "
                    "candidate_count, resolution_json, dependencies_json, gaps_json, payload_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(edge["edgeId"]),
                        str(edge["sourceId"]),
                        str(edge.get("relation") or "CALLS_NATIVE"),
                        str(edge.get("targetId") or ""),
                        str(edge.get("status") or ""),
                        str(resolution.get("blueprintMemberName") or ""),
                        str(resolution.get("blueprintOwner") or ""),
                        str(resolution.get("nativeQualifiedName") or ""),
                        str(resolution.get("matchMethod") or ""),
                        len(candidates),
                        _compact_json(resolution),
                        _compact_json(edge.get("dependencies") or {}),
                        _compact_json(edge.get("gaps") or []),
                        _compact_json(edge),
                    ),
                )
                for ordinal, candidate in enumerate(candidates):
                    connection.execute(
                        "INSERT INTO hybrid_edge_candidates(edge_id, ordinal, native_evidence_id, "
                        "qualified_name, owner, signature, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            str(edge["edgeId"]),
                            ordinal,
                            str(candidate.get("evidenceId") or ""),
                            str(candidate.get("qualifiedName") or ""),
                            str(candidate.get("owner") or ""),
                            str(candidate.get("signature") or ""),
                            _compact_json(candidate),
                        ),
                    )
            counts = {
                "edges": int(
                    connection.execute("SELECT COUNT(*) FROM hybrid_edges").fetchone()[0]
                ),
                "candidates": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM hybrid_edge_candidates"
                    ).fetchone()[0]
                ),
            }
            connection.commit()
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                raise ValueError(f"hybrid database foreign key errors: {foreign_keys[:3]}")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise ValueError(f"hybrid database integrity check failed: {integrity}")
        finally:
            connection.close()
        os.replace(temp_path, destination)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return {
        "source_sha256": source_sha,
        "source_size_bytes": len(source_bytes),
        "counts": counts,
    }


def _publish(staged: list[tuple[Path, Path]]) -> None:
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for _source, destination in staged:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                backup = destination.with_name(
                    f".{destination.name}.{uuid.uuid4().hex}.backup"
                )
                os.replace(destination, backup)
                backups[destination] = backup
        for source, destination in staged:
            os.replace(source, destination)
            published.append(destination)
    except Exception:
        for destination in reversed(published):
            destination.unlink(missing_ok=True)
        for destination, backup in backups.items():
            if backup.exists():
                os.replace(backup, destination)
        raise
    finally:
        for backup in backups.values():
            backup.unlink(missing_ok=True)
        for source, _destination in staged:
            source.unlink(missing_ok=True)


def write_hybrid_evidence_artifacts(
    payload: object,
    output_dir: str | Path,
) -> dict[str, Any]:
    root_payload = validate_hybrid_payload(payload)
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".hybrid-evidence-", dir=destination.parent))
    try:
        source_path = staging / "hybrid_edges.json"
        source_path.write_text(
            json.dumps(root_payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        database_path = staging / "hybrid_evidence.sqlite"
        result = _build_hybrid_database(root_payload, source_path, database_path)
        manifest = {
            "schema": HYBRID_MANIFEST_SCHEMA,
            "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
            "source": {
                "path": "hybrid_edges.json",
                "sha256": result["source_sha256"],
                "sizeBytes": result["source_size_bytes"],
            },
            "sqlite": {
                "path": "hybrid_evidence.sqlite",
                "schema": HYBRID_SQLITE_SCHEMA,
                "userVersion": HYBRID_SQLITE_USER_VERSION,
                "sha256": sha256_file(database_path),
            },
            "dependencies": root_payload["dependencies"],
            "counts": result["counts"],
        }
        manifest_path = staging / "hybrid_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        final_source = destination / "hybrid_edges.json"
        final_database = destination / "hybrid_evidence.sqlite"
        final_manifest = destination / "hybrid_manifest.json"
        _publish(
            [
                (source_path, final_source),
                (database_path, final_database),
                (manifest_path, final_manifest),
            ]
        )
        return {
            **result,
            "source_path": str(final_source),
            "database_path": str(final_database),
            "manifest_path": str(final_manifest),
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HybridEvidenceArtifactInvalid(f"{label} cannot be read: {exc}") from exc
    if not isinstance(payload, dict):
        raise HybridEvidenceArtifactInvalid(f"{label} must contain an object")
    return payload


def _inside(root: Path, value: object, label: str) -> Path:
    candidate = (root / str(value or "")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HybridEvidenceArtifactInvalid(f"{label} path escapes artifact root") from exc
    return candidate


class HybridEvidenceRepository:
    def __init__(
        self,
        *,
        root: Path,
        source_path: Path,
        database_path: Path,
        manifest: dict[str, Any],
        connection: sqlite3.Connection,
    ) -> None:
        self.root = root
        self.source_path = source_path
        self.database_path = database_path
        self.manifest = manifest
        self.source_sha256 = str(manifest["source"]["sha256"])
        self._connection = connection
        self._closed = False

    @classmethod
    def open(cls, output_dir: str | Path) -> "HybridEvidenceRepository":
        root = Path(output_dir).expanduser().resolve()
        manifest = _read_object(root / "hybrid_manifest.json", "hybrid manifest")
        if manifest.get("schema") != HYBRID_MANIFEST_SCHEMA:
            raise HybridEvidenceArtifactInvalid("hybrid manifest schema is invalid")
        source_meta = manifest.get("source")
        sqlite_meta = manifest.get("sqlite")
        if not isinstance(source_meta, dict) or not isinstance(sqlite_meta, dict):
            raise HybridEvidenceArtifactInvalid("hybrid manifest metadata is incomplete")
        source_path = _inside(root, source_meta.get("path"), "hybrid source")
        database_path = _inside(root, sqlite_meta.get("path"), "hybrid SQLite")
        try:
            actual_source_hash = sha256_file(source_path)
        except OSError as exc:
            raise HybridEvidenceArtifactInvalid(
                f"hybrid source cannot be verified: {exc}"
            ) from exc
        if actual_source_hash != str(source_meta.get("sha256") or ""):
            raise HybridEvidenceArtifactInvalid("hybrid source JSON hash mismatch")
        try:
            actual_database_hash = sha256_file(database_path)
        except OSError as exc:
            raise HybridEvidenceArtifactInvalid(
                f"hybrid SQLite cannot be verified: {exc}"
            ) from exc
        if actual_database_hash != str(sqlite_meta.get("sha256") or ""):
            raise HybridEvidenceArtifactInvalid("hybrid SQLite hash mismatch")
        if sqlite_meta.get("schema") != HYBRID_SQLITE_SCHEMA:
            raise HybridEvidenceArtifactInvalid("hybrid SQLite schema is invalid")

        connection = sqlite3.connect(
            f"{database_path.as_uri()}?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA query_only = ON")
            if int(connection.execute("PRAGMA user_version").fetchone()[0]) != HYBRID_SQLITE_USER_VERSION:
                raise HybridEvidenceArtifactInvalid(
                    "hybrid SQLite user_version is invalid"
                )
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise HybridEvidenceArtifactInvalid(
                    f"hybrid SQLite integrity check failed: {integrity}"
                )
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise HybridEvidenceArtifactInvalid(
                    "hybrid SQLite foreign key check failed"
                )
            metadata = connection.execute(
                "SELECT * FROM hybrid_metadata WHERE metadata_id = 1"
            ).fetchone()
            if metadata is None:
                raise HybridEvidenceArtifactInvalid("hybrid metadata is missing")
            if str(metadata["sqlite_schema"]) != HYBRID_SQLITE_SCHEMA:
                raise HybridEvidenceArtifactInvalid(
                    "hybrid SQLite metadata schema is invalid"
                )
            if str(metadata["source_json_sha256"]) != actual_source_hash:
                raise HybridEvidenceArtifactInvalid(
                    "hybrid SQLite source hash mismatch"
                )
            if int(metadata["source_json_size"]) != source_path.stat().st_size:
                raise HybridEvidenceArtifactInvalid(
                    "hybrid SQLite source size mismatch"
                )
            counts = manifest.get("counts")
            if not isinstance(counts, dict):
                raise HybridEvidenceArtifactInvalid("hybrid manifest counts are missing")
            actual_counts = {
                "edges": int(
                    connection.execute("SELECT COUNT(*) FROM hybrid_edges").fetchone()[0]
                ),
                "candidates": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM hybrid_edge_candidates"
                    ).fetchone()[0]
                ),
            }
            if actual_counts != {
                "edges": int(counts.get("edges", -1)),
                "candidates": int(counts.get("candidates", -1)),
            }:
                raise HybridEvidenceArtifactInvalid(
                    "hybrid manifest counts differ from SQLite"
                )
        except Exception:
            connection.close()
            raise
        return cls(
            root=root,
            source_path=source_path,
            database_path=database_path,
            manifest=manifest,
            connection=connection,
        )

    def list_edges(
        self,
        *,
        source_id: str = "",
        target_id: str = "",
        status: str = "",
        relation: str = "",
    ) -> list[dict[str, Any]]:
        if self._closed:
            raise RuntimeError("HybridEvidenceRepository is closed")
        normalized_relation = relation.strip().upper().replace("-", "_")
        if normalized_relation == INVERSE_RELATION:
            rows = self._connection.execute(
                "SELECT payload_json FROM hybrid_edges "
                "WHERE relation = 'CALLS_NATIVE' "
                "AND status = 'CONFIRMED' AND trim(target_id) <> '' "
                "ORDER BY target_id, source_id, edge_id"
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                forward = json.loads(str(row["payload_json"]))
                if not isinstance(forward, dict):
                    continue
                inverse_source = str(forward.get("targetId") or "")
                inverse_target = str(forward.get("sourceId") or "")
                inverse_status = str(forward.get("status") or "")
                if source_id and inverse_source != source_id:
                    continue
                if target_id and inverse_target != target_id:
                    continue
                if status and inverse_status != status:
                    continue
                resolution = copy.deepcopy(forward.get("resolution") or {})
                if isinstance(resolution, dict):
                    resolution["inverseOf"] = str(forward.get("edgeId") or "")
                result.append(
                    {
                        **copy.deepcopy(forward),
                        "edgeId": _stable_edge_id(
                            inverse_source,
                            INVERSE_RELATION,
                            str(forward.get("evidenceSetId") or ""),
                        ),
                        "sourceId": inverse_source,
                        "relation": INVERSE_RELATION,
                        "targetId": inverse_target,
                        "resolution": resolution,
                        "derived": True,
                    }
                )
            return result
        if normalized_relation and normalized_relation not in FORWARD_RELATIONS:
            raise ValueError(f"unsupported hybrid relation: {relation}")
        clauses: list[str] = []
        parameters: list[object] = []
        if source_id:
            clauses.append("source_id = ?")
            parameters.append(source_id)
        if target_id:
            clauses.append("target_id = ?")
            parameters.append(target_id)
        if status:
            clauses.append("status = ?")
            parameters.append(status)
        if normalized_relation:
            clauses.append("relation = ?")
            parameters.append(normalized_relation)
        sql = "SELECT payload_json FROM hybrid_edges"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY source_id, edge_id"
        rows = self._connection.execute(sql, tuple(parameters)).fetchall()
        result = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if isinstance(payload, dict):
                result.append(payload)
        return result

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def __enter__(self) -> "HybridEvidenceRepository":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


def open_hybrid_evidence_repository(
    output_dir: str | Path,
) -> HybridEvidenceRepository:
    return HybridEvidenceRepository.open(output_dir)


_OWNER_KEYS = {
    "blueprintmemberowner",
    "functionowner",
    "memberowner",
    "memberparent",
    "owner",
    "ownerclass",
    "parentclass",
}
_SIGNATURE_KEYS = {
    "functionsignature",
    "memberreference",
    "signature",
    "signaturehint",
    "signaturehints",
}
_IMPLEMENTATION_KEYS = {
    "implementation",
    "implementationkind",
    "memberimplementation",
}
_RELATION_KEYS = {
    "nativerelation",
    "relation",
}


def _find_metadata_values(
    value: object,
    keys: set[str],
) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).replace("_", "").casefold() in keys:
                if isinstance(nested, list):
                    found.extend(str(item) for item in nested if str(item).strip())
                elif not isinstance(nested, (dict, list)) and str(nested).strip():
                    found.append(str(nested))
            found.extend(_find_metadata_values(nested, keys))
    elif isinstance(value, list):
        for item in value:
            found.extend(_find_metadata_values(item, keys))
    return list(dict.fromkeys(found))


def extract_blueprint_calls(
    asset_dir: str | Path,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    evidence_state = resolve_asset_evidence_state(asset_dir)
    with open_bound_evidence_database(evidence_state) as connection:
        identity = connection.execute(
            "SELECT revision_id, source_fingerprint FROM asset_revisions LIMIT 1"
        ).fetchone()
        if identity is None:
            raise ValueError("Blueprint evidence database has no revision")
        rows = connection.execute(
            "SELECT node_ref, function_name, class_name, node_type, "
            "control_kind, macro_name, semantic_json, extra_json "
            "FROM nodes WHERE trim(function_name) <> '' "
            "ORDER BY node_ref"
        ).fetchall()
    calls: list[dict[str, object]] = []
    for row in rows:
        metadata: dict[str, Any] = {}
        for column in ("semantic_json", "extra_json"):
            try:
                decoded = json.loads(str(row[column] or "{}"))
            except json.JSONDecodeError:
                decoded = {}
            if isinstance(decoded, dict):
                metadata[column] = decoded
        owners = _find_metadata_values(metadata, _OWNER_KEYS)
        signatures = _find_metadata_values(metadata, _SIGNATURE_KEYS)
        implementations = _find_metadata_values(metadata, _IMPLEMENTATION_KEYS)
        relations = _find_metadata_values(metadata, _RELATION_KEYS)
        node_kind = next(
            (
                str(row[key])
                for key in ("control_kind", "node_type", "class_name")
                if str(row[key] or "").strip()
            ),
            "",
        )
        macro_name = str(row["macro_name"] or "").strip()
        if macro_name or "macro" in node_kind.casefold():
            node_kind = "MACRO"
            implementation = "MACRO"
        else:
            implementation = (
                implementations[0] if len(implementations) == 1 else ""
            )
        metadata_gaps = (
            [] if len(owners) == 1 else ["BLUEPRINT_MEMBER_OWNER_REQUIRED"]
        )
        if len(implementations) > 1:
            metadata_gaps.append("BLUEPRINT_IMPLEMENTATION_AMBIGUOUS")
        calls.append(
            {
                "evidenceId": str(row["node_ref"]),
                "memberName": str(row["function_name"]),
                "owner": owners[0] if len(owners) == 1 else "",
                "signatureHints": signatures,
                "kind": node_kind,
                "implementation": implementation,
                "relation": relations[0] if len(relations) == 1 else "",
                "metadataGaps": metadata_gaps,
            }
        )
    return calls, {
        "revisionId": str(identity["revision_id"]),
        "sourceFingerprint": str(identity["source_fingerprint"]),
        **evidence_state_metadata(evidence_state),
    }
