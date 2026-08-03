#!/usr/bin/env python3
"""Audit exact Harvest ranking dominance without publishing generated evidence."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from blueprint_translator.harvest_dominance_audit import (
    audit_harvest_rankings,
    render_harvest_dominance_markdown,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NODE_CATALOG = (
    PROJECT_ROOT / "analysis" / "harvest_nodes" / "resource_node_catalog.json"
)
DEFAULT_EVALUATION_CATALOG = (
    PROJECT_ROOT
    / "analysis"
    / "harvest_rankings"
    / "harvest_evaluation_catalog.json"
)
DEFAULT_SQLITE_CATALOG = (
    PROJECT_ROOT / "analysis" / "harvest_nodes" / "harvest_catalog.sqlite"
)
DEFAULT_AUDIT_ROOT = PROJECT_ROOT / "analysis" / "harvest_rankings" / "audits"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit exact node/resource and unique evaluation-key winners for one species."
        )
    )
    parser.add_argument("--node-catalog", type=Path, default=DEFAULT_NODE_CATALOG)
    parser.add_argument(
        "--evaluation-catalog", type=Path, default=DEFAULT_EVALUATION_CATALOG
    )
    parser.add_argument("--sqlite-catalog", type=Path, default=DEFAULT_SQLITE_CATALOG)
    parser.add_argument("--species", default="dreadnoughtus")
    parser.add_argument(
        "--json-out",
        type=Path,
        default=DEFAULT_AUDIT_ROOT / "dreadnoughtus-dominance.json",
    )
    parser.add_argument(
        "--markdown-out",
        type=Path,
        default=DEFAULT_AUDIT_ROOT / "dreadnoughtus-dominance.md",
    )
    return parser.parse_args(argv)


def _atomic_write(path: Path, content: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_harvest_rankings(
        node_catalog_path=args.node_catalog,
        evaluation_catalog_path=args.evaluation_catalog,
        sqlite_catalog_path=args.sqlite_catalog,
        species_query=args.species,
    )
    _atomic_write(
        args.json_out,
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _atomic_write(args.markdown_out, render_harvest_dominance_markdown(report))
    print(
        json.dumps(
            {
                "ok": True,
                "schema": report.get("schema"),
                "species": report.get("targetSpecies", {}).get("speciesKey"),
                "topOccurrences": report.get("targetSpecies", {}).get(
                    "topOccurrences"
                ),
                "topUniqueEvaluationKeys": report.get("targetSpecies", {}).get(
                    "topUniqueEvaluationKeys"
                ),
                "jsonOut": str(args.json_out),
                "markdownOut": str(args.markdown_out),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
