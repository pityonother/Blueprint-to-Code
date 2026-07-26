from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.devkit_paths import (  # noqa: E402
    first_existing_devkit_content_root,
)
from blueprint_translator.kb_discovery import (  # noqa: E402
    build_discovery_bundle,
    run_devkit_registry_export,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a sanitized, incremental ARK knowledge-base discovery bundle."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "knowledge_base" / "discovery_bundle",
        help="Final discovery bundle directory.",
    )
    parser.add_argument(
        "--content-root",
        type=Path,
        help="ARK DevKit ShooterGame/Content root. Auto-detected when omitted.",
    )
    parser.add_argument(
        "--captures-root",
        type=Path,
        default=PROJECT_ROOT / "captures",
    )
    parser.add_argument(
        "--native-root",
        type=Path,
        default=PROJECT_ROOT / "native_evidence",
    )
    parser.add_argument(
        "--knowledge-db-dir",
        type=Path,
        default=PROJECT_ROOT / "knowledge_base" / "db",
    )
    parser.add_argument(
        "--registry-snapshot",
        type=Path,
        help=(
            "Existing DevKit Registry working snapshot. Defaults to the "
            "incremental work directory beside the output."
        ),
    )
    parser.add_argument(
        "--skip-registry-export",
        action="store_true",
        help="Do not launch ShooterGameEditor-Cmd; reuse an existing snapshot.",
    )
    parser.add_argument(
        "--registry-no-dependencies",
        action="store_true",
        help="Export Registry identities without the package dependency graph.",
    )
    parser.add_argument(
        "--registry-timeout-seconds",
        type=int,
        help="Optional DevKit exporter timeout. By default it may run to completion.",
    )
    parser.add_argument(
        "--include-existing-evidence",
        action="store_true",
        help="Read existing Blueprint Evidence Stores and knowledge databases.",
    )
    parser.add_argument(
        "--include-native-boundaries",
        action="store_true",
        help="Read bounded, validated native Evidence Stores.",
    )
    parser.add_argument(
        "--parse-identity-fallback",
        action="store_true",
        help=(
            "Parse every package header when Registry identity is unavailable. "
            "This is slower and remains a secondary source."
        ),
    )
    parser.add_argument(
        "--build-zip",
        action="store_true",
        help="Build and verify knowledge_base/discovery_bundle.zip.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    content_root = args.content_root
    if content_root is None:
        content_root = first_existing_devkit_content_root()
    if content_root is None or not content_root.is_dir():
        raise SystemExit("ARK DevKit Content root was not found. Pass --content-root.")

    output = args.output
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    registry_snapshot = args.registry_snapshot
    if registry_snapshot is None:
        registry_snapshot = (
            output.resolve().parent / ".kb_discovery_work" / "registry_snapshot"
        )
    elif not registry_snapshot.is_absolute():
        registry_snapshot = PROJECT_ROOT / registry_snapshot

    registry_result: dict[str, object] = {
        "status": "SKIPPED",
        "reason": "skip-registry-export",
    }
    if not args.skip_registry_export:
        registry_result = run_devkit_registry_export(
            project_root=PROJECT_ROOT,
            content_root=content_root,
            snapshot_dir=registry_snapshot,
            include_dependencies=not args.registry_no_dependencies,
            timeout_seconds=args.registry_timeout_seconds,
        )
        print(
            json.dumps(
                {"registryExport": registry_result},
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )

    result = build_discovery_bundle(
        project_root=PROJECT_ROOT,
        output_dir=output,
        content_root=content_root,
        captures_root=args.captures_root,
        native_root=args.native_root,
        knowledge_db_dir=args.knowledge_db_dir,
        registry_snapshot_dir=registry_snapshot,
        include_existing_evidence=args.include_existing_evidence,
        include_native_boundaries=args.include_native_boundaries,
        build_zip=args.build_zip,
        parse_identity=args.parse_identity_fallback,
    )
    print(
        json.dumps(
            {
                "registryExport": registry_result,
                "bundle": result,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if result.get("status") == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
