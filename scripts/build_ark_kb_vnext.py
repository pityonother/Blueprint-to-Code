from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.snapshot import (  # noqa: E402
    build_vnext_snapshot,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an atomic ARK Knowledge Base vNext snapshot."
    )
    parser.add_argument(
        "--discovery-database",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--legacy-kb-root",
        type=Path,
        default=PROJECT_ROOT / "knowledge_base" / "db",
    )
    parser.add_argument(
        "--capture-root",
        type=Path,
        default=PROJECT_ROOT / "captures",
    )
    parser.add_argument(
        "--native-root",
        type=Path,
        default=PROJECT_ROOT / "native_evidence",
    )
    parser.add_argument(
        "--map-evidence-catalog",
        type=Path,
        default=(
            PROJECT_ROOT
            / "analysis"
            / "harvest_nodes"
            / "resource_node_catalog.json"
        ),
        help=(
            "Revision-validated resource-node catalog used only for typed "
            "PCG and World Partition map evidence."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "knowledge_base" / "vnext",
    )
    parser.add_argument("--full-snapshot", action="store_true")
    return parser


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = build_vnext_snapshot(
        project_root=PROJECT_ROOT,
        discovery_database=_absolute(args.discovery_database),
        legacy_kb_root=_absolute(args.legacy_kb_root),
        capture_root=_absolute(args.capture_root),
        native_root=_absolute(args.native_root),
        map_evidence_path=_absolute(args.map_evidence_catalog),
        output_dir=_absolute(args.output),
        full_snapshot=args.full_snapshot,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
