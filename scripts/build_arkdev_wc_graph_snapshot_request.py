"""Build the explicit, bounded request for one ARK DevKit WC Graph probe."""

from __future__ import annotations

import argparse
from pathlib import Path

from arkdev_scripting_probe.contracts import canonical_json
from arkdev_scripting_probe.wc_graph_snapshot import (
    MAX_NODES,
    MAX_PINS_PER_NODE,
    REQUEST_SCHEMA,
    validate_wc_snapshot_request,
)


DEFAULT_FILENAME = "wc-graph-snapshot-request.json"


def build_request(
    *,
    asset_object_path: str,
    graph_name: str,
    max_nodes: int = 12,
    max_pins_per_node: int = MAX_PINS_PER_NODE,
) -> dict[str, object]:
    return validate_wc_snapshot_request(
        {
            "schema": REQUEST_SCHEMA,
            "assetObjectPath": asset_object_path,
            "graphName": graph_name,
            "maxNodes": max_nodes,
            "maxPinsPerNode": max_pins_per_node,
        }
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write one path-free ARK DevKit WC Graph snapshot request.",
    )
    parser.add_argument("--asset-object-path", required=True)
    parser.add_argument("--graph-name", required=True)
    parser.add_argument("--max-nodes", type=int, default=12)
    parser.add_argument(
        "--max-pins-per-node",
        type=int,
        default=MAX_PINS_PER_NODE,
    )
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    request = build_request(
        asset_object_path=args.asset_object_path,
        graph_name=args.graph_name,
        max_nodes=args.max_nodes,
        max_pins_per_node=args.max_pins_per_node,
    )
    repo_root = Path(__file__).resolve().parents[1]
    output = args.output or repo_root / ".arkdev-probe" / DEFAULT_FILENAME
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical_json(request) + "\n", encoding="utf-8")
    print(f"ARKDEV_WC_GRAPH_SNAPSHOT_REQUEST={output.resolve()}")
    print(f"MAX_NODES_BOUND={MAX_NODES}")
    print(f"MAX_PINS_PER_NODE_BOUND={MAX_PINS_PER_NODE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_request", "main"]
