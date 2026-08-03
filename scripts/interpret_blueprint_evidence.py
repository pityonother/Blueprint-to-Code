"""Generate and publish an Evidence-bound Blueprint interpretation revision."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


CLI_RESULT_SCHEMA = "blueprint-to-code.interpretation-cli-result/v1"
DEFAULT_BUDGET = 32_000
ARTIFACT_NAMES = [
    "interpretation.json",
    "interpretation.md",
    "trace.json",
    "gaps.json",
    "pseudocode.txt",
    "manifest.json",
]
_WINDOWS_ABSOLUTE = re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:[\\/]")
_UNC_PATH = re.compile(r"(?<![A-Za-z0-9_])\\\\[^\\\s]+[\\/]")
_POSIX_LOCAL_PATH = re.compile(
    r"(?<![:/A-Za-z0-9_])/(?!Game/|Script/|Engine/|Plugin/|Plugins/)[^\s]+"
)


def _strict_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("value must be exactly true or false")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build and atomically publish Blueprint Interpretation Contract v1 "
            "from manifest-bound Evidence Publication v3."
        )
    )
    parser.add_argument("--asset-dir", type=Path, required=True)
    parser.add_argument(
        "--graph",
        help="Exact bp:// graph ref used only to project printed JSON output.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown", "pseudocode", "all"),
        default="all",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=DEFAULT_BUDGET,
        help="Deterministic interpreter work-unit budget.",
    )
    parser.add_argument("--fail-on-gap", action="store_true")
    parser.add_argument(
        "--allow-stale",
        nargs="?",
        const=True,
        type=_strict_bool,
        default=False,
    )
    parser.add_argument(
        "--allow-legacy-fallback",
        nargs="?",
        const=True,
        type=_strict_bool,
        default=False,
    )
    args = parser.parse_args(argv)
    if args.budget <= 0:
        parser.error("--budget must be positive")
    if args.graph is not None:
        args.graph = str(args.graph).strip()
        if not args.graph.startswith("bp://") or len(args.graph) > 1024:
            parser.error("--graph must be an exact bp:// reference")
    return args


def _publish_interpretation(
    asset_dir: Path,
    *,
    budget: int,
    fail_on_gap: bool,
    allow_stale: bool,
    allow_legacy_fallback: bool,
    expected_semantic_digest: str | None = None,
) -> object:
    from blueprint_translator.interpretation_publication import (
        publish_interpretation,
    )

    return publish_interpretation(
        asset_dir,
        budget=budget,
        fail_on_gap=fail_on_gap,
        allow_stale=allow_stale,
        allow_legacy_fallback=allow_legacy_fallback,
        expected_semantic_digest=expected_semantic_digest,
    )


def _build_interpretation_preview(
    asset_dir: Path,
    *,
    budget: int,
    allow_stale: bool,
    allow_legacy_fallback: bool,
) -> object:
    from blueprint_translator.interpretation_publication import build_interpretation

    return build_interpretation(
        asset_dir,
        budget=budget,
        allow_stale=allow_stale,
        allow_legacy_fallback=allow_legacy_fallback,
    )


def _load_current_interpretation(asset_dir: Path) -> object:
    from blueprint_translator.interpretation_publication import (
        load_current_interpretation,
    )

    return load_current_interpretation(asset_dir)


def _member(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"INTERPRETATION_OUTPUT_INVALID: {label} must be an object")
    return {str(key): item for key, item in value.items()}


def _loaded_result(asset_dir: Path, published: object) -> object:
    if isinstance(_member(published, "interpretation"), Mapping):
        return published
    return _load_current_interpretation(asset_dir)


def _graph_refs(interpretation: Mapping[str, object]) -> set[str]:
    refs = {
        str(row.get("graphRef") or "")
        for row in interpretation.get("statements", [])
        if isinstance(row, Mapping) and row.get("graphRef")
    }
    for section_name in ("controlFlow", "dataFlow"):
        section = interpretation.get(section_name)
        if not isinstance(section, Mapping):
            continue
        graphs = section.get("graphs", [])
        if not isinstance(graphs, Sequence) or isinstance(graphs, (str, bytes)):
            continue
        for graph in graphs:
            if isinstance(graph, Mapping):
                ref = str(graph.get("graphRef") or graph.get("ref") or "")
                if ref:
                    refs.add(ref)
    return refs


def _belongs_to_graph(value: object, graph_ref: str) -> bool:
    ref = str(value or "")
    return ref == graph_ref or ref.startswith(f"{graph_ref}/")


def _graph_projection(
    interpretation: Mapping[str, object], graph_ref: str | None
) -> dict[str, object]:
    projected = json.loads(
        json.dumps(
            dict(interpretation),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    if not graph_ref:
        return projected
    if graph_ref not in _graph_refs(interpretation):
        raise ValueError("GRAPH_NOT_FOUND: graph ref is not present in the interpretation")
    statements = projected.get("statements", [])
    if isinstance(statements, list):
        projected["statements"] = [
            row
            for row in statements
            if isinstance(row, dict) and row.get("graphRef") == graph_ref
        ]
    hints = projected.get("heuristicReviewHints", [])
    if isinstance(hints, list):
        projected["heuristicReviewHints"] = [
            row
            for row in hints
            if isinstance(row, dict)
            and _belongs_to_graph(row.get("reviewRef"), graph_ref)
        ]
    for section_name in ("controlFlow", "dataFlow"):
        section = projected.get(section_name)
        if isinstance(section, dict) and isinstance(section.get("graphs"), list):
            section["graphs"] = [
                row
                for row in section["graphs"]
                if isinstance(row, dict)
                and (row.get("graphRef") or row.get("ref")) == graph_ref
            ]
    data_flow = projected.get("dataFlow")
    if isinstance(data_flow, dict):
        shared = data_flow.get("sharedExpressions")
        if isinstance(shared, list):
            data_flow["sharedExpressions"] = [
                row
                for row in shared
                if isinstance(row, dict)
                and _belongs_to_graph(row.get("sourceNodeRef"), graph_ref)
            ]
        component_refs = data_flow.get("componentRefs")
        if isinstance(component_refs, list):
            data_flow["componentRefs"] = [
                row
                for row in component_refs
                if isinstance(row, dict) and row.get("graphRef") == graph_ref
            ]
    summary = projected.get("assetSummary")
    if isinstance(summary, dict):
        inventory = summary.get("graphInventory")
        selected_inventory = (
            [
                row
                for row in inventory
                if isinstance(row, dict) and row.get("graphRef") == graph_ref
            ]
            if isinstance(inventory, list)
            else []
        )
        summary["graphInventory"] = selected_inventory
        summary["graphCount"] = len(selected_inventory)
        summary["nodeCount"] = sum(
            int(row.get("nodeCount") or 0) for row in selected_inventory
        )
        summary["pinCount"] = sum(
            int(row.get("pinCount") or 0) for row in selected_inventory
        )
        graph_status_counts: dict[str, int] = {}
        for row in selected_inventory:
            status = str(row.get("status") or "UNKNOWN")
            graph_status_counts[status] = graph_status_counts.get(status, 0) + 1
        summary["graphStatusCounts"] = dict(sorted(graph_status_counts.items()))
        for key in ("entries", "variableReads", "variableWrites", "delegateBindings", "macros"):
            rows = summary.get(key)
            if isinstance(rows, list):
                summary[key] = [
                    row
                    for row in rows
                    if isinstance(row, dict)
                    and _belongs_to_graph(row.get("nodeRef"), graph_ref)
                ]
        local_calls = summary.get("confirmedLocalCalls")
        if isinstance(local_calls, list):
            summary["confirmedLocalCalls"] = [
                row
                for row in local_calls
                if isinstance(row, dict)
                and _belongs_to_graph(row.get("referenceRef"), graph_ref)
            ]
        external_calls = summary.get("externalOrMissingCallableBodies")
        if isinstance(external_calls, list):
            summary["externalOrMissingCallableBodies"] = [
                row
                for row in external_calls
                if isinstance(row, dict) and row.get("graphRef") == graph_ref
            ]
        edge_refs: set[str] = set()
        control_flow = projected.get("controlFlow")
        if isinstance(control_flow, dict):
            for graph in control_flow.get("graphs", []):
                if not isinstance(graph, dict):
                    continue
                for node in graph.get("nodes", []):
                    if not isinstance(node, dict):
                        continue
                    for successor in node.get("successors", []):
                        if isinstance(successor, dict) and successor.get("edgeRef"):
                            edge_refs.add(str(successor["edgeRef"]))
        if isinstance(data_flow, dict):
            for graph in data_flow.get("graphs", []):
                if not isinstance(graph, dict):
                    continue
                for edge in graph.get("edges", []):
                    if isinstance(edge, dict) and edge.get("edgeRef"):
                        edge_refs.add(str(edge["edgeRef"]))
        summary["edgeCount"] = len(edge_refs)
        projected_statements = projected.get("statements", [])
        if isinstance(projected_statements, list):
            summary["diagnosticGapCount"] = sum(
                isinstance(row, dict) and row.get("kind") == "GAP"
                for row in projected_statements
            )
    projected["selection"] = {
        "graphRefs": [graph_ref],
        "publicationScope": "ASSET",
    }
    return projected


def _assert_path_free(value: object) -> None:
    if isinstance(value, Path):
        raise ValueError("PUBLIC_OUTPUT_PATH: local Path value is not public")
    if isinstance(value, str):
        if (
            _WINDOWS_ABSOLUTE.search(value)
            or _UNC_PATH.search(value)
            or _POSIX_LOCAL_PATH.search(value)
        ):
            raise ValueError("PUBLIC_OUTPUT_PATH: local absolute path is not public")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_path_free(str(key))
            _assert_path_free(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _assert_path_free(item)


def _receipt(published: object, loaded: object, graph_ref: str | None) -> dict[str, object]:
    interpretation = _mapping(
        _member(loaded, "interpretation"), label="interpretation"
    )
    manifest = _mapping(_member(loaded, "manifest", {}), label="manifest")
    receipt: dict[str, object] = {
        "ok": True,
        "schema": CLI_RESULT_SCHEMA,
        "revisionId": str(
            _member(loaded, "revision_id", "")
            or _member(published, "revision_id", "")
            or manifest.get("revisionId")
            or ""
        ),
        "manifestSha256": str(
            _member(loaded, "manifest_sha256", "")
            or _member(published, "manifest_sha256", "")
            or ""
        ),
        "pointerSha256": str(
            _member(loaded, "pointer_sha256", "")
            or _member(published, "pointer_sha256", "")
            or ""
        ),
        "semanticDigest": str(
            interpretation.get("semanticDigest")
            or _member(published, "semantic_digest", "")
            or ""
        ),
        "evidenceRevisionId": str(
            interpretation.get("evidenceRevisionId")
            or manifest.get("evidenceRevisionId")
            or _member(published, "evidence_revision_id", "")
            or ""
        ),
        "evidenceManifestSha256": str(
            interpretation.get("evidenceManifestSha256")
            or manifest.get("evidenceManifestSha256")
            or _member(published, "evidence_manifest_sha256", "")
            or ""
        ),
        "created": bool(_member(published, "created", False)),
        "reused": bool(_member(published, "reused", False)),
        "publicationScope": "ASSET",
        "graphProjection": graph_ref,
        "artifacts": list(ARTIFACT_NAMES),
    }
    _assert_path_free(receipt)
    return receipt


def _error_code(exc: Exception) -> tuple[str, str, int]:
    raw = str(getattr(exc, "code", "") or "").strip().upper()
    if not raw:
        text = str(exc).strip()
        raw = text.split(":", 1)[0].strip().upper() if text else ""
    if "STALE" in raw or raw == "EVIDENCE_REVISION_CHANGED":
        return "EVIDENCE_STALE", "Blueprint evidence is stale.", 4
    if any(
        marker in raw
        for marker in (
            "NO_EVIDENCE",
            "NOT_AUTHORITY",
            "NOT_AUTHORITATIVE",
            "EVIDENCE_V3_REQUIRED",
        )
    ):
        return (
            "EVIDENCE_NOT_AUTHORITATIVE",
            "Authoritative Blueprint Evidence v3 is required.",
            4,
        )
    if "GAPS_PRESENT" in raw or ("GAP" in raw and "FAIL" in raw):
        return "INTERPRETATION_GAPS_PRESENT", "Interpretation contains gaps.", 3
    if "BUDGET" in raw:
        return "INTERPRETATION_BUDGET_EXCEEDED", "Interpretation budget was exceeded.", 2
    if "GRAPH_NOT_FOUND" in raw:
        return "GRAPH_NOT_FOUND", "Requested graph was not found.", 2
    if "GRAPH_FORMAT_UNSUPPORTED" in raw:
        return (
            "GRAPH_FORMAT_UNSUPPORTED",
            "Graph projection requires JSON or receipt output.",
            2,
        )
    if "PREVIEW_CHANGED" in raw:
        return (
            "INTERPRETATION_INPUT_CHANGED",
            "Interpretation input changed after preflight.",
            4,
        )
    if any(marker in raw for marker in ("PUBLICATION", "COLLISION", "CONFLICT", "CAS")):
        return "INTERPRETATION_PUBLICATION_FAILED", "Interpretation publication failed.", 5
    if "PUBLIC_OUTPUT_PATH" in raw or "OUTPUT_INVALID" in raw:
        return "INTERPRETATION_OUTPUT_INVALID", "Interpretation output is invalid.", 5
    return "INTERPRETATION_FAILED", "Interpretation could not be produced.", 2


def _print_json(value: object, *, stream: object | None = None) -> None:
    output = sys.stdout if stream is None else stream
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        file=output,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        if args.graph and args.format in {"markdown", "pseudocode"}:
            raise ValueError(
                "GRAPH_FORMAT_UNSUPPORTED: --graph projection requires --format json or all"
            )
        preview = None
        expected_semantic_digest = None
        if args.graph or args.format != "all":
            preview = _build_interpretation_preview(
                args.asset_dir,
                budget=args.budget,
                allow_stale=bool(args.allow_stale),
                allow_legacy_fallback=bool(args.allow_legacy_fallback),
            )
            preview_interpretation = _mapping(
                _member(preview, "interpretation"),
                label="preview interpretation",
            )
            projected_preview = _graph_projection(
                preview_interpretation,
                args.graph,
            )
            expected_semantic_digest = str(
                preview_interpretation.get("semanticDigest")
                or _member(preview, "semantic_digest", "")
                or ""
            )
            if args.format == "json":
                _assert_path_free(projected_preview)
            elif args.format == "markdown":
                _assert_path_free(_member(preview, "markdown", ""))
            elif args.format == "pseudocode":
                _assert_path_free(_member(preview, "pseudocode", ""))
        published = _publish_interpretation(
            args.asset_dir,
            budget=args.budget,
            fail_on_gap=bool(args.fail_on_gap),
            allow_stale=bool(args.allow_stale),
            allow_legacy_fallback=bool(args.allow_legacy_fallback),
            expected_semantic_digest=expected_semantic_digest,
        )
        loaded = _loaded_result(args.asset_dir, published)
        interpretation = _mapping(
            _member(loaded, "interpretation"), label="interpretation"
        )
        projected = _graph_projection(interpretation, args.graph)
        if args.format == "json":
            _assert_path_free(projected)
            _print_json(projected)
        elif args.format == "markdown":
            markdown = str(_member(loaded, "markdown", "") or "")
            _assert_path_free(markdown)
            sys.stdout.write(markdown)
            if markdown and not markdown.endswith("\n"):
                sys.stdout.write("\n")
        elif args.format == "pseudocode":
            pseudocode = str(_member(loaded, "pseudocode", "") or "")
            _assert_path_free(pseudocode)
            sys.stdout.write(pseudocode)
            if pseudocode and not pseudocode.endswith("\n"):
                sys.stdout.write("\n")
        else:
            _print_json(_receipt(published, loaded, args.graph))
        return 0
    except Exception as exc:
        code, message, exit_code = _error_code(exc)
        _print_json(
            {"ok": False, "code": code, "error": message},
            stream=sys.stderr,
        )
        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
