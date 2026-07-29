#!/usr/bin/env python3
"""Build the complete ARK resource-node Explorer dataset in staged evidence passes."""

from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.harvest_node_repository import HarvestNodeRepository  # noqa: E402
from blueprint_translator.harvest_catalog_sqlite import SQLiteHarvestCatalog  # noqa: E402
from blueprint_translator.harvest_evaluation_catalog import (  # noqa: E402
    EVALUATION_CATALOG_SCHEMA,
)
from blueprint_translator.harvest_report_validation import validate_harvest_report  # noqa: E402


DEFAULT_DEVKIT_ROOT = Path(r"C:\Program Files\Epic Games\ARKDevkit")
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "analysis" / "harvest_rankings"
DEFAULT_CATALOG_OUTPUT = (
    PROJECT_ROOT / "analysis" / "harvest_nodes" / "resource_node_catalog.json"
)
DEFAULT_SCAN_CACHE = (
    PROJECT_ROOT / "analysis" / "harvest_nodes" / "resource_node_scan_cache.json"
)
DEFAULT_CREATURE_SCAN_CACHE = (
    PROJECT_ROOT / "analysis" / "harvest_rankings" / "creature_asset_scan_cache.json"
)
DEFAULT_MAP_SCAN_CACHE = (
    PROJECT_ROOT / "analysis" / "harvest_nodes" / "map_reference_scan_cache.json"
)
DEFAULT_IMAGE_CACHE_ROOT = PROJECT_ROOT / "analysis" / "harvest_nodes" / "images"

PROMOTION_BEGIN_LINE = "[promotion-critical] begin"
PROMOTION_COMMIT_COMPLETE_LINE = "[promotion-critical] commit-complete"
PROMOTION_END_LINE = "[promotion-critical] end"


class _DeferredTerminationSignals:
    """Temporarily record termination requests without interrupting promotion."""

    def __init__(self) -> None:
        self.received: list[int] = []
        self.previous: dict[int, Any] = {}

    def _handle(self, signal_number: int, _frame: Any) -> None:
        self.received.append(signal_number)

    def __enter__(self) -> _DeferredTerminationSignals:
        candidates = [signal.SIGINT, signal.SIGTERM]
        sigbreak = getattr(signal, "SIGBREAK", None)
        if sigbreak is not None:
            candidates.append(sigbreak)
        for signal_number in candidates:
            try:
                previous = signal.getsignal(signal_number)
                signal.signal(signal_number, self._handle)
            except (OSError, RuntimeError, ValueError):
                continue
            self.previous[signal_number] = previous
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        for signal_number, previous in self.previous.items():
            signal.signal(signal_number, previous)


def _promote_staged_dataset_uninterruptibly(
    staged_args: argparse.Namespace,
    final_args: argparse.Namespace,
) -> bool:
    """Finish promotion/rollback before honoring a process-tree cancellation.

    Returning ``True`` means a termination signal arrived while promotion was
    in progress.  A successful promotion is still a committed success; the
    manager exposes the late cancellation separately instead of claiming that
    the newly committed dataset was cancelled.
    """

    with _DeferredTerminationSignals() as deferred:
        print(PROMOTION_BEGIN_LINE, flush=True)
        try:
            promote_staged_dataset(staged_args, final_args)
        except BaseException:
            print(PROMOTION_END_LINE, flush=True)
            raise
        print(PROMOTION_COMMIT_COMPLETE_LINE, flush=True)
        print(PROMOTION_END_LINE, flush=True)
    return bool(deferred.received)


def _dataset_paths(args: argparse.Namespace) -> dict[str, Path]:
    output_dir = Path(args.output_dir)
    catalog_output = Path(args.catalog_output)
    return {
        "full": output_dir / "harvest_ranking_all_resources.full.json",
        "ai": output_dir / "harvest_ranking_all_resources.ai.json",
        "query": output_dir / "harvest_ranking_all_resources.query.json",
        "markdown": output_dir / "harvest_ranking_all_resources.md",
        "resourceCatalog": output_dir / "resource_catalog.json",
        "evaluation": output_dir / "harvest_evaluation_catalog.json",
        "evaluationAi": output_dir / "harvest_evaluation_catalog.ai.json",
        "nodeCatalog": catalog_output,
        "componentManifest": catalog_output.parent / "referenced_harvest_components.txt",
        "sqliteCatalog": catalog_output.parent / "harvest_catalog.sqlite",
        "independentVerification": (
            output_dir / "harvest_ranking_independent_verification.json"
        ),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build all-resource rankings, discover exact node components, rebuild rankings, "
            "then produce and verify the final Explorer catalog."
        )
    )
    parser.add_argument("--devkit-root", type=Path, default=DEFAULT_DEVKIT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--catalog-output", type=Path, default=DEFAULT_CATALOG_OUTPUT)
    parser.add_argument("--scan-cache", type=Path, default=DEFAULT_SCAN_CACHE)
    parser.add_argument(
        "--creature-scan-cache", type=Path, default=DEFAULT_CREATURE_SCAN_CACHE
    )
    parser.add_argument("--map-scan-cache", type=Path, default=DEFAULT_MAP_SCAN_CACHE)
    parser.add_argument("--image-cache-root", type=Path, default=DEFAULT_IMAGE_CACHE_ROOT)
    parser.add_argument(
        "--creature-file",
        type=Path,
        help="Optional JSON creature list passed to both ranking passes.",
    )
    parser.add_argument(
        "--skip-map-scan",
        action="store_true",
        help="Skip map references in the final catalog pass.",
    )
    parser.add_argument(
        "--skip-images",
        action="store_true",
        help="Skip thumbnail extraction in the final catalog pass.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the complete staged command plan without executing it.",
    )
    return parser.parse_args(argv)


def plan_commands(
    args: argparse.Namespace,
    *,
    python_executable: str | None = None,
) -> list[list[str]]:
    """Return the complete ordered build plan without reading or writing files."""

    python = python_executable or sys.executable
    rank_script = str(SCRIPT_DIR / "rank_ark_harvest.py")
    catalog_script = str(SCRIPT_DIR / "build_ark_resource_node_catalog.py")
    evaluation_script = str(SCRIPT_DIR / "build_ark_harvest_evaluation_catalog.py")
    sqlite_script = str(SCRIPT_DIR / "build_harvest_catalog_sqlite.py")
    independent_verify_script = str(SCRIPT_DIR / "verify_ark_harvest_rankings.py")
    verify_script = str(SCRIPT_DIR / "verify_ark_harvest_report.py")

    output_dir = Path(args.output_dir)
    catalog_output = Path(args.catalog_output)
    preliminary_catalog_output = catalog_output.with_name(
        f"{catalog_output.stem}.preliminary{catalog_output.suffix}"
    )
    component_manifest = catalog_output.parent / "referenced_harvest_components.txt"
    full_report = output_dir / "harvest_ranking_all_resources.full.json"
    ai_report = output_dir / "harvest_ranking_all_resources.ai.json"
    evaluation_catalog = output_dir / "harvest_evaluation_catalog.json"
    evaluation_ai = output_dir / "harvest_evaluation_catalog.ai.json"
    sqlite_catalog = catalog_output.parent / "harvest_catalog.sqlite"
    independent_verification = (
        output_dir / "harvest_ranking_independent_verification.json"
    )

    def ranking_command() -> list[str]:
        command = [
            python,
            rank_script,
            "--devkit-root",
            str(args.devkit_root),
            "--all-resources",
            "--discover-all-components",
            "--output-dir",
            str(output_dir),
        ]
        if args.creature_file:
            command.extend(["--creature-file", str(args.creature_file)])
        return command

    first_ranking = ranking_command()
    preliminary_catalog = [
        python,
        catalog_script,
        "--devkit-root",
        str(args.devkit_root),
        "--ranking-report",
        str(full_report),
        "--discover-root",
        ".",
        "--skip-map-scan",
        "--skip-images",
        "--scan-cache",
        str(args.scan_cache),
        "--output",
        str(preliminary_catalog_output),
        "--component-manifest-output",
        str(component_manifest),
    ]

    final_ranking = ranking_command()
    final_ranking.extend(["--extra-component-file", str(component_manifest)])

    build_evaluation = [
        python,
        evaluation_script,
        "--devkit-root",
        str(args.devkit_root),
        "--ranking-report",
        str(full_report),
        "--output",
        str(evaluation_catalog),
        "--ai-output",
        str(evaluation_ai),
        "--scan-cache",
        str(args.creature_scan_cache),
    ]

    final_catalog = [
        python,
        catalog_script,
        "--devkit-root",
        str(args.devkit_root),
        "--ranking-report",
        str(full_report),
        "--evaluation-catalog",
        str(evaluation_catalog),
        "--discover-root",
        ".",
        "--map-root",
        ".",
        "--map-scan-cache",
        str(args.map_scan_cache),
        "--scan-cache",
        str(args.scan_cache),
        "--image-cache-root",
        str(args.image_cache_root),
        "--output",
        str(catalog_output),
        "--component-manifest-output",
        str(component_manifest),
    ]
    if args.skip_map_scan:
        final_catalog.append("--skip-map-scan")
    if args.skip_images:
        final_catalog.append("--skip-images")

    build_sqlite = [
        python,
        sqlite_script,
        "--catalog",
        str(catalog_output),
        "--output",
        str(sqlite_catalog),
    ]

    independent_verification_command = [
        python,
        independent_verify_script,
        "--node-catalog",
        str(catalog_output),
        "--evaluation-catalog",
        str(evaluation_catalog),
        "--ranking-catalog",
        str(output_dir / "harvest_ranking_all_resources.query.json"),
        "--output",
        str(independent_verification),
        "--sample-size",
        "128",
        "--seed",
        "phase5-acceptance-v1",
        "--limit",
        "10",
    ]

    verification = [
        python,
        verify_script,
        "--full",
        str(full_report),
        "--ai",
        str(ai_report),
    ]
    return [
        first_ranking,
        preliminary_catalog,
        final_ranking,
        build_evaluation,
        final_catalog,
        build_sqlite,
        independent_verification_command,
        verification,
    ]


def _expected_query_payload(full_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": full_payload.get("schema"),
        "querySchema": "ark-harvest-ranking-query/v2",
        "generatedAt": full_payload.get("generatedAt"),
        "datasetRevision": full_payload.get("datasetRevision"),
        "scanManifestHash": full_payload.get("scanManifestHash"),
        "methodology": full_payload.get("methodology"),
        "coverage": full_payload.get("coverage"),
        "bestRows": full_payload.get("bestRows"),
    }


def validate_staged_dataset(args: argparse.Namespace) -> dict[str, Any]:
    """Validate every staged artifact used by AI and by the live repository."""

    paths = _dataset_paths(args)
    full_text = paths["full"].read_text(encoding="utf-8")
    ai_text = paths["ai"].read_text(encoding="utf-8")
    full_payload = json.loads(full_text)
    ai_payload = json.loads(ai_text)
    validation = validate_harvest_report(
        full_payload,
        ai_payload,
        full_path=paths["full"],
        full_characters=len(full_text),
        ai_characters=len(ai_text),
    )
    if not validation.get("valid"):
        raise ValueError(f"Staged full/AI validation failed: {validation.get('errors')}")

    query_payload = json.loads(paths["query"].read_text(encoding="utf-8"))
    if query_payload != _expected_query_payload(full_payload):
        raise ValueError("Staged query index does not exactly match the full report.")

    evaluation_text = paths["evaluation"].read_text(encoding="utf-8")
    evaluation_payload = json.loads(evaluation_text)
    if evaluation_payload.get("schema") != EVALUATION_CATALOG_SCHEMA:
        raise ValueError("Staged evaluation catalog schema is invalid.")
    evaluation_dataset = evaluation_payload.get("dataset")
    evaluation_revision = (
        str(evaluation_dataset.get("revision") or "")
        if isinstance(evaluation_dataset, dict)
        else ""
    )
    component_revision = (
        str(evaluation_dataset.get("componentDatasetRevision") or "")
        if isinstance(evaluation_dataset, dict)
        else ""
    )
    if len(evaluation_revision) != 64 or any(
        character not in "0123456789abcdef" for character in evaluation_revision
    ):
        raise ValueError("Staged evaluation catalog revision is invalid.")
    if component_revision != str(full_payload.get("datasetRevision") or ""):
        raise ValueError("Staged evaluation catalog references a different component dataset.")
    if "rows" in evaluation_payload or "bestRows" in evaluation_payload:
        raise ValueError("Staged evaluation catalog contains a Cartesian ranking payload.")
    if not isinstance(evaluation_payload.get("creatures"), list) or not isinstance(
        evaluation_payload.get("components"), list
    ):
        raise ValueError("Staged evaluation catalog is missing creature/component facts.")
    if len(evaluation_text.encode("utf-8")) >= 8 * 1024 * 1024:
        raise ValueError("Staged evaluation catalog exceeds the 8 MiB performance budget.")

    evaluation_ai = json.loads(paths["evaluationAi"].read_text(encoding="utf-8"))
    if evaluation_ai.get("schema") != "ark-harvest-evaluation-catalog-ai/v2":
        raise ValueError("Staged evaluation AI summary schema is invalid.")
    if evaluation_ai.get("dataset") != evaluation_dataset:
        raise ValueError("Staged evaluation AI summary does not match the detail catalog.")

    independent_verification = json.loads(
        paths["independentVerification"].read_text(encoding="utf-8")
    )
    if (
        independent_verification.get("schema")
        != "blueprint-to-code.harvest-independent-verification/v2"
        or independent_verification.get("status") != "PASS"
    ):
        raise ValueError("Staged independent ranking verification did not pass.")
    independent_selection = independent_verification.get("selection")
    independent_comparison = independent_verification.get("comparison")
    if (
        not isinstance(independent_selection, dict)
        or not isinstance(independent_comparison, dict)
        or int(independent_selection.get("targetsSelected") or 0) < 1
        or int(independent_comparison.get("targetsCompared") or 0)
        != int(independent_selection.get("targetsSelected") or 0)
        or int(independent_comparison.get("mismatchCount") or 0) != 0
    ):
        raise ValueError("Staged independent ranking verification checked no targets.")

    sqlite_catalog = SQLiteHarvestCatalog(paths["sqliteCatalog"])
    sqlite_catalog.assert_matches_source(paths["nodeCatalog"])
    sqlite_page = sqlite_catalog.list_nodes(limit=1)

    repository = HarvestNodeRepository(
        paths["nodeCatalog"],
        paths["query"],
        paths["evaluation"],
        sqlite_catalog_path=paths["sqliteCatalog"],
    )
    page = repository.list_nodes(limit=1)
    nodes = json.loads(paths["nodeCatalog"].read_text(encoding="utf-8")).get("nodes")
    ranked_smoke = False
    for node in nodes if isinstance(nodes, list) else []:
        resources = node.get("resources", {}).get("items", []) if isinstance(node, dict) else []
        if not resources:
            continue
        repository.get_node(str(node.get("id") or ""))
        repository.rankings(
            str(node.get("id") or ""),
            str(resources[0].get("nodeResourceId") or ""),
            limit=1,
        )
        ranked_smoke = True
        break
    if not ranked_smoke:
        raise ValueError("Staged node catalog contains no queryable resource entry.")
    if page.get("total") != sqlite_page.get("total"):
        raise ValueError("Staged SQLite and repository node totals do not match.")
    return {
        "datasetRevision": query_payload.get("datasetRevision"),
        "evaluationRevision": evaluation_revision,
        "nodes": page.get("total"),
        "creatureAssets": evaluation_payload.get("coverage", {}).get(
            "creatureAssetsCataloged"
        ),
        "fullRows": validation.get("checks", {}).get("fullRows"),
        "independentTargets": independent_comparison.get("targetsCompared"),
        "sqliteBytes": paths["sqliteCatalog"].stat().st_size,
    }


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.next")
    try:
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _backup_existing_target(destination: Path) -> Path:
    """Create a cheap same-directory rollback copy without replacing an older backup."""

    for serial in range(1000):
        backup = destination.with_name(
            f".{destination.name}.{os.getpid()}.{serial}.backup"
        )
        if backup.exists():
            continue
        try:
            os.link(destination, backup)
        except OSError:
            if backup.exists():
                continue
            shutil.copy2(destination, backup)
        return backup
    raise RuntimeError(f"Unable to allocate rollback backup for {destination}")


def promote_staged_dataset(staged_args: argparse.Namespace, final_args: argparse.Namespace) -> None:
    """Promote a verified bundle and restore every prior artifact if any copy fails."""

    staged = _dataset_paths(staged_args)
    final = _dataset_paths(final_args)
    promotion_order = (
        "full",
        "ai",
        "evaluation",
        "evaluationAi",
        "markdown",
        "resourceCatalog",
        "componentManifest",
        "query",
        "nodeCatalog",
        "sqliteCatalog",
        "independentVerification",
    )
    backups: dict[str, Path | None] = {}
    try:
        for key in promotion_order:
            destination = final[key]
            backups[key] = (
                _backup_existing_target(destination) if destination.exists() else None
            )
    except BaseException:
        for backup in backups.values():
            if backup is not None and backup.exists():
                backup.unlink()
        raise

    attempted: list[str] = []
    promotion_succeeded = False
    rollback_complete = False
    try:
        for key in promotion_order:
            attempted.append(key)
            _atomic_copy(staged[key], final[key])
        promotion_succeeded = True
    except BaseException as promotion_error:
        rollback_errors: list[str] = []
        for key in reversed(attempted):
            destination = final[key]
            backup = backups[key]
            try:
                if backup is not None and backup.exists():
                    _atomic_copy(backup, destination)
                elif destination.exists():
                    destination.unlink()
            except OSError as exc:
                rollback_errors.append(f"{key}: {exc}")
        if rollback_errors:
            preserved_backups = [
                str(backup)
                for backup in backups.values()
                if backup is not None and backup.exists()
            ]
            raise RuntimeError(
                "Dataset promotion failed and rollback was incomplete: "
                + "; ".join(rollback_errors)
                + ". Preserved backups: "
                + "; ".join(preserved_backups)
            ) from promotion_error
        rollback_complete = True
        raise
    finally:
        if promotion_succeeded or rollback_complete:
            for backup in backups.values():
                if backup is not None and backup.exists():
                    backup.unlink()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run:
        commands = plan_commands(args)
        for index, command in enumerate(commands, start=1):
            print(f"[{index}/{len(commands)}] {subprocess.list2cmdline(command)}")
        return 0

    with tempfile.TemporaryDirectory(prefix=".tmp_harvest_build_", dir=PROJECT_ROOT) as temp_dir:
        staging_root = Path(temp_dir)
        staged_args = argparse.Namespace(**vars(args))
        staged_args.output_dir = staging_root / "rankings"
        staged_args.catalog_output = staging_root / "nodes" / "resource_node_catalog.json"
        commands = plan_commands(staged_args)
        for index, command in enumerate(commands, start=1):
            print(f"[{index}/{len(commands)}] {Path(command[1]).name}", flush=True)
            subprocess.run(command, cwd=str(PROJECT_ROOT), check=True)
        summary = validate_staged_dataset(staged_args)
        promotion_cancellation_deferred = _promote_staged_dataset_uninterruptibly(
            staged_args,
            args,
        )
    print(
        json.dumps(
            {
                "ok": True,
                "promoted": True,
                "promotionCancellationDeferred": promotion_cancellation_deferred,
                **summary,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
