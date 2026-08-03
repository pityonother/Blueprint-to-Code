from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.context_pack import (  # noqa: E402
    DEFAULT_CONTEXT_BUDGET,
    MIN_CONTEXT_BUDGET,
    build_asset_memory_card,
    build_default_context_pack,
    context_query_terms,
    render_asset_memory_card,
    render_context_pack,
    estimate_tokens,
)
from blueprint_translator.evidence_repository import (  # noqa: E402
    open_asset_repository,
    resolve_asset_evidence_state,
)
from blueprint_translator.formulas import build_formula_candidates, render_formula_candidates  # noqa: E402


def _lexical_absolute(path: str | os.PathLike[str]) -> Path:
    """Return an absolute path without following links or reparse points."""

    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def write_json(path: Path, payload: object) -> None:
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2, default=list))


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_name = handle.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name and Path(temporary_name).exists():
            Path(temporary_name).unlink()


def read_required_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ValueError(f"Required capture file is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Capture JSON is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Capture JSON must contain an object: {path}")
    return payload


def read_optional_json(path: Path) -> dict[str, object]:
    return read_required_json(path) if path.is_file() else {}


def graph_payload_from_capture(graph: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        "graph_name": graph.get("graph", ""),
        "graph_type": graph.get("graph_type", ""),
        "confidence": graph.get("confidence", "unknown"),
        "node_count": graph.get("node_count", len(graph.get("nodes", []) or [])),
        "pin_count": graph.get("pin_count", 0),
        "link_count": graph.get("link_count", 0),
        "link_resolution_counts": graph.get("link_resolution_counts", {}),
        "uasset_read_status": graph.get("status", ""),
    }
    payload = {
        "metadata": metadata,
        "nodes": graph.get("nodes", []),
        "function_calls": [
            node
            for node in graph.get("nodes", []) or []
            if isinstance(node, dict) and node.get("function")
        ],
        "variable_gets": [
            node
            for node in graph.get("nodes", []) or []
            if isinstance(node, dict) and node.get("variable") and "VariableGet" in str(node.get("class") or node.get("node_type") or "")
        ],
        "variable_sets": [
            node
            for node in graph.get("nodes", []) or []
            if isinstance(node, dict) and node.get("variable") and "VariableSet" in str(node.get("class") or node.get("node_type") or "")
        ],
    }
    return payload


def load_asset_payload(asset_dir: Path) -> dict[str, object]:
    asset_dir = _lexical_absolute(asset_dir)
    graph_nodes = read_required_json(asset_dir / "uasset_graph_nodes.json")
    class_defaults = read_optional_json(asset_dir / "uasset_class_defaults.json")
    graph_rows = graph_nodes.get("graphs", [])
    if not isinstance(graph_rows, list):
        raise ValueError("uasset_graph_nodes.json field 'graphs' must be a list")
    variables = class_defaults.get("variables", {})
    if not isinstance(variables, dict):
        raise ValueError("uasset_class_defaults.json field 'variables' must be an object")
    invalid_variables = [name for name, value in variables.items() if not isinstance(value, dict)]
    if invalid_variables:
        raise ValueError(f"class default variable {invalid_variables[0]!r} must contain an object")
    if not (graph_nodes.get("asset_name") or graph_nodes.get("asset_path") or graph_rows or variables):
        raise ValueError("capture does not contain an asset identity, graphs, or class defaults")
    graphs = []
    for graph_index, graph in enumerate(graph_rows):
        if not isinstance(graph, dict):
            raise ValueError(f"graphs[{graph_index}] must contain an object")
        if "nodes" in graph and not isinstance(graph.get("nodes"), list):
            raise ValueError(f"graph {graph.get('graph') or '<unnamed>'!r} field 'nodes' must be a list")
        graphs.append(
            {
                "graph_name": graph.get("graph", ""),
                "graph_type": graph.get("graph_type", ""),
                "source": str(asset_dir / "uasset_graph_nodes.json"),
                "source_kind": "uasset_binary",
                "node_count": graph.get("node_count", len(graph.get("nodes", []) or [])),
                "confidence": graph.get("confidence", "unknown"),
                "payload": graph_payload_from_capture(graph),
            }
        )
    return {
        "metadata": {
            "generated": graph_nodes.get("generated", ""),
            "asset_dir": str(asset_dir),
            "asset_name": graph_nodes.get("asset_name") or class_defaults.get("asset_name") or asset_dir.name,
            "asset_path": graph_nodes.get("asset_path", ""),
            "graph_count": len(graphs),
            "node_count": graph_nodes.get("node_count", 0),
            "default_variable_count": len(variables),
        },
        "class_defaults": class_defaults,
        "graphs": graphs,
        "uasset_binary": {
            "present": bool(graph_nodes),
            "asset_path": graph_nodes.get("asset_path", ""),
            "uasset_path": graph_nodes.get("uasset_path", ""),
            "asset_name": graph_nodes.get("asset_name", ""),
            "class_defaults": class_defaults,
        },
        "call_graph": {},
        "diagnostics": {},
    }


def _source_fingerprint(asset_dir: Path) -> str:
    rows = []
    for name in ("uasset_graph_nodes.json", "uasset_class_defaults.json"):
        path = asset_dir / name
        if path.is_file():
            stat = path.stat()
            rows.append((name, stat.st_size, stat.st_mtime_ns))
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _context_artifact_paths(
    output_dir: Path,
    question: str,
    budget: int,
    source_fingerprint: str,
) -> tuple[Path, Path]:
    if not question.strip():
        return output_dir / "context_pack.json", output_dir / "context_pack.md"
    digest = hashlib.sha256(
        json.dumps(
            {"question": question.strip(), "budget": budget, "source": source_fingerprint},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    query_dir = output_dir / "context_queries" / digest
    return query_dir / "context_pack.json", query_dir / "context_pack.md"


def _repository_entity(repository: object, evidence_ref: str) -> dict[str, object]:
    response = repository.query(  # type: ignore[attr-defined]
        {
            "operation": "entity",
            "selector": {"ref": evidence_ref},
            "budgetTokens": 1000,
        }
    )
    items = response.get("items", [])
    return items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}


_REPOSITORY_QUERY_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "decided",
    "does",
    "how",
    "is",
    "the",
    "this",
    "what",
    "where",
}


def _repository_question_hits(
    repository: object,
    question: str,
    budget: int,
) -> tuple[list[str], list[dict[str, object]]]:
    terms = [
        term
        for term in context_query_terms(question)
        if term.casefold() not in _REPOSITORY_QUERY_STOP_WORDS
    ][:8]
    hits_by_ref: dict[str, dict[str, object]] = {}
    query_budget = min(max(budget, MIN_CONTEXT_BUDGET), 1200)
    for term in terms:
        response = repository.query(  # type: ignore[attr-defined]
            {
                "operation": "search",
                "query": term,
                "kinds": ["graph", "node", "default", "diagnostic"],
                "pageSize": 12,
                "budgetTokens": query_budget,
            }
        )
        for row in response.get("items", []):
            if not isinstance(row, dict) or not row.get("ref"):
                continue
            evidence_ref = str(row["ref"])
            hit = hits_by_ref.setdefault(
                evidence_ref,
                {
                    "kind": str(row.get("kind") or "evidence"),
                    "id": evidence_ref,
                    "name": str(row.get("name") or ""),
                    "graph_ref": str(row.get("graphRef") or ""),
                    "summary": str(row.get("summary") or ""),
                    "matched_terms": [],
                },
            )
            matched_terms = hit["matched_terms"]
            if isinstance(matched_terms, list) and term not in matched_terms:
                matched_terms.append(term)
    kind_priority = {"graph": 0, "node": 1, "default": 2, "diagnostic": 3}
    hits = sorted(
        hits_by_ref.values(),
        key=lambda row: (
            -len(row.get("matched_terms", [])),  # type: ignore[arg-type]
            kind_priority.get(str(row.get("kind")), 9),
            str(row.get("name") or "").casefold(),
            str(row.get("id") or ""),
        ),
    )
    return terms, hits[:16]


def build_pack_from_repository(
    asset_dir: Path,
    *,
    question: str = "",
    budget: int = DEFAULT_CONTEXT_BUDGET,
) -> dict[str, object]:
    """Build a bounded, question-aware navigation pack through the Evidence Repository."""

    root = _lexical_absolute(asset_dir)
    if budget < MIN_CONTEXT_BUDGET:
        raise ValueError(f"Budget must be at least {MIN_CONTEXT_BUDGET} estimated tokens.")
    bounded_question = str(question or "").strip()[:500]
    with open_asset_repository(root) as repository:
        overview = repository.query({"operation": "overview", "budgetTokens": min(800, budget)})
        query_terms, question_hits = _repository_question_hits(repository, bounded_question, budget)
        if question_hits:
            graph_refs = list(
                dict.fromkeys(
                    str(row.get("id") if row.get("kind") == "graph" else row.get("graph_ref") or "")
                    for row in question_hits
                    if str(row.get("id") if row.get("kind") == "graph" else row.get("graph_ref") or "")
                )
            )
            graph_rows = [{"ref": ref, "name": ""} for ref in graph_refs]
            default_rows = [
                {"ref": row["id"], "name": row.get("name", "")}
                for row in question_hits
                if row.get("kind") == "default"
            ]
            diagnostic_rows = [
                {"ref": row["id"]}
                for row in question_hits
                if row.get("kind") == "diagnostic"
            ]
        else:
            graph_rows = [
                {"ref": row.get("ref", ""), "name": row.get("name", "")}
                for row in repository.graph_summaries()[:12]
            ]
            default_rows = [
                {"ref": row.get("ref", ""), "name": row.get("name", "")}
                for row in repository.default_summaries(include_values=False)[:12]
            ]
            diagnostic_rows = [
                {"ref": row.get("ref", "")}
                for row in repository.gap_summaries()[:12]
            ]

        node_names_by_graph: dict[str, list[str]] = {}
        for row in question_hits:
            if row.get("kind") != "node" or not row.get("graph_ref"):
                continue
            node_names_by_graph.setdefault(str(row["graph_ref"]), []).append(str(row.get("name") or ""))

        key_graphs: list[dict[str, object]] = []
        for row in graph_rows[:6]:
            if not row.get("ref"):
                continue
            entity = _repository_entity(repository, str(row["ref"]))
            counts = entity.get("counts") if isinstance(entity.get("counts"), dict) else {}
            key_graphs.append(
                {
                    "ref": row["ref"],
                    "graph": entity.get("name", row.get("name", "")),
                    "graph_type": entity.get("graphType", ""),
                    "node_count": counts.get("nodes", 0),
                    "confidence": entity.get("confidence", ""),
                    "status": entity.get("status", ""),
                    "functions": node_names_by_graph.get(str(row["ref"]), [])[:5],
                    "variables": [],
                    "events": [],
                }
            )

        key_defaults: list[dict[str, object]] = []
        for row in default_rows[:6]:
            if not row.get("ref"):
                continue
            entity = _repository_entity(repository, str(row["ref"]))
            key_defaults.append(
                {
                    "ref": row["ref"],
                    "name": entity.get("name", row.get("name", "")),
                    "value": entity.get("value"),
                    "type": entity.get("typeName", ""),
                    "confidence": entity.get("confidence", ""),
                }
            )

        gaps = []
        for row in diagnostic_rows[:6]:
            if not row.get("ref"):
                continue
            entity = _repository_entity(repository, str(row["ref"]))
            if entity:
                gaps.append(entity)

    asset = overview.get("asset") if isinstance(overview.get("asset"), dict) else {}
    summary = overview.get("summary") if isinstance(overview.get("summary"), dict) else {}
    query_text = query_terms[0] if query_terms else "<name>"
    next_query = (
        'runtime\\python\\python.exe scripts\\query_blueprint_evidence.py '
        f'--asset-dir "{root}" search --query "{query_text}" --budget 800'
    )
    pack: dict[str, object] = {
        "schema": "ark.context_pack.v2",
        "asset_name": asset.get("name", root.name),
        "object_path": asset.get("objectPath", ""),
        "revision_id": asset.get("revisionId", ""),
        "purpose": "question_answering" if bounded_question else "evidence_navigation",
        "budget": budget,
        "estimated_tokens": 0,
        "budget_enforced": True,
        "question": bounded_question,
        "query_terms": query_terms,
        "source_counts": {
            "formula_candidates": 0,
            "unresolved": int(summary.get("gapCount") or 0),
            "key_defaults": int(summary.get("defaultCount") or 0),
            "key_graphs": int(summary.get("graphCount") or 0),
        },
        "evidence_counts": {
            key: int(summary.get(key) or 0)
            for key in (
                "graphCount",
                "nodeCount",
                "pinCount",
                "wireCount",
                "linkObservationCount",
                "defaultCount",
                "gapCount",
            )
        },
        "included_sections": ["key_graphs", "key_defaults", "gaps", "evidence_pointers"],
        "player_summary": (
            f"Indexed Blueprint evidence: {summary.get('graphCount', 0)} graphs, "
            f"{summary.get('nodeCount', 0)} nodes, {summary.get('pinCount', 0)} pins, "
            f"{summary.get('wireCount', 0)} canonical wires."
        ),
        "confirmed_mechanisms": [],
        "formula_candidates": [],
        "unresolved": [],
        "key_defaults": key_defaults,
        "key_graphs": key_graphs,
        "gaps": gaps,
        "evidence_pointers": [],
        "next_query": next_query,
        "omitted": [
            "Node, Pin, Wire, Property, and raw observation bodies are available but not returned in this index.",
            "Use the bounded query command below to retrieve exact evidence by bp:// reference.",
        ],
    }
    pointer_hits = question_hits[:10]

    def refresh_pointers() -> None:
        candidates = [
            *(
                {
                    "kind": row.get("kind", "evidence"),
                    "id": row.get("id", ""),
                    **({"graph": row.get("name", "")} if row.get("kind") == "graph" else {}),
                }
                for row in pointer_hits
            ),
            *({"kind": "graph", "id": row["ref"], "graph": row["graph"]} for row in pack["key_graphs"]),  # type: ignore[index]
            *({"kind": "default", "id": row["ref"]} for row in pack["key_defaults"]),  # type: ignore[index]
            *({"kind": "diagnostic", "id": row.get("ref", "")} for row in pack["gaps"]),  # type: ignore[index]
        ]
        seen: set[str] = set()
        pointers = []
        for row in candidates:
            evidence_ref = str(row.get("id") or "")
            if not evidence_ref or evidence_ref in seen:
                continue
            seen.add(evidence_ref)
            pointers.append(row)
        pack["evidence_pointers"] = pointers

    def update_estimate() -> int:
        estimate = 0
        for _attempt in range(6):
            pack["estimated_tokens"] = estimate
            updated = estimate_tokens(render_context_pack(pack))
            if updated == estimate:
                break
            estimate = updated
        pack["estimated_tokens"] = estimate_tokens(render_context_pack(pack))
        return int(pack["estimated_tokens"])

    refresh_pointers()
    while update_estimate() > budget:
        removable = next(
            (section for section in ("gaps", "key_defaults", "key_graphs") if len(pack[section]) > 1),  # type: ignore[arg-type]
            None,
        )
        if removable is not None:
            pack[removable].pop()  # type: ignore[union-attr]
        elif pointer_hits:
            pointer_hits.pop()
        else:
            raise ValueError("repository context pack base content exceeds the requested budget")
        refresh_pointers()
    update_estimate()
    return pack


def build_pack(asset_dir: Path, question: str, budget: int) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    asset_dir = _lexical_absolute(asset_dir)
    output_dir = asset_dir / "output"
    indexed_evidence = False
    for marker in (
        asset_dir / "evidence" / "current.json",
        asset_dir / "evidence" / "evidence.sqlite",
    ):
        try:
            marker.lstat()
        except FileNotFoundError:
            continue
        indexed_evidence = True
        break
    if indexed_evidence:
        # A declared indexed generation must validate or fail closed.  Missing
        # revision artifacts are corruption, not permission to read legacy
        # sidecars implicitly.
        resolve_asset_evidence_state(asset_dir)
        if output_dir.exists() and not output_dir.resolve().is_relative_to(asset_dir):
            raise ValueError("output directory must stay inside the capture directory")
        context_pack = build_pack_from_repository(asset_dir, question=question, budget=budget)
        source_fingerprint = str(context_pack.get("revision_id") or "indexed")
        context_json_path, context_markdown_path = _context_artifact_paths(
            output_dir,
            question,
            budget,
            source_fingerprint,
        )
        context_pack["artifact_path"] = str(context_markdown_path.resolve())
        write_json(context_json_path, context_pack)
        write_text_atomic(context_markdown_path, render_context_pack(context_pack))
        return {}, {}, context_pack
    if output_dir.exists() and not output_dir.resolve().is_relative_to(asset_dir):
        raise ValueError("output directory must stay inside the capture directory")
    asset_payload = load_asset_payload(asset_dir)
    formula_path = output_dir / "formula_candidates.json"
    memory_path = output_dir / "asset_memory_card.json"
    formula_payload = build_formula_candidates(asset_payload)
    memory_card = build_asset_memory_card(asset_payload, formula_payload)
    source_fingerprint = _source_fingerprint(asset_dir)
    context_json_path, context_markdown_path = _context_artifact_paths(
        output_dir,
        question,
        budget,
        source_fingerprint,
    )
    if not context_markdown_path.parent.resolve(strict=False).is_relative_to(asset_dir):
        raise ValueError("context artifact directory must stay inside the capture directory")
    formula_snapshot_path = (
        context_markdown_path.parent / "formula_candidates.md"
        if question.strip()
        else output_dir / "formula_candidates.md"
    )
    filtered_pointers: list[dict[str, object]] = []
    for pointer in memory_card.get("evidence_pointers", []) or []:
        if not isinstance(pointer, dict) or pointer.get("kind") != "report":
            if isinstance(pointer, dict):
                filtered_pointers.append(pointer)
            continue
        original_path = Path(str(pointer.get("path") or ""))
        if original_path.name == "formula_candidates.md":
            filtered_pointers.append({**pointer, "path": str(formula_snapshot_path.resolve())})
        elif original_path.is_file():
            filtered_pointers.append(pointer)
    memory_card["evidence_pointers"] = filtered_pointers
    context_pack = build_default_context_pack(
        asset_payload,
        formula_payload,
        memory_card,
        budget=budget,
        question=question,
    )
    context_pack["artifact_path"] = str(context_markdown_path.resolve())
    context_pack["source_fingerprint"] = source_fingerprint

    if not question.strip():
        write_json(formula_path, formula_payload)
        write_text_atomic(output_dir / "formula_candidates.md", render_formula_candidates(formula_payload))
        write_json(memory_path, memory_card)
        write_text_atomic(output_dir / "asset_memory_card.md", render_asset_memory_card(memory_card))
    else:
        write_json(context_markdown_path.parent / "formula_candidates.json", formula_payload)
        write_text_atomic(formula_snapshot_path, render_formula_candidates(formula_payload))
        write_json(context_markdown_path.parent / "asset_memory_card.json", memory_card)
        write_text_atomic(context_markdown_path.parent / "asset_memory_card.md", render_asset_memory_card(memory_card))
    write_json(context_json_path, context_pack)
    write_text_atomic(context_markdown_path, render_context_pack(context_pack))
    return formula_payload, memory_card, context_pack


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a compact context pack for one captured Blueprint asset.")
    parser.add_argument("--asset-dir", type=Path, required=True)
    parser.add_argument("--question", default="")
    parser.add_argument(
        "--budget",
        type=int,
        default=DEFAULT_CONTEXT_BUDGET,
        help=(
            f"Maximum estimated Markdown tokens "
            f"(minimum: {MIN_CONTEXT_BUDGET}, default: {DEFAULT_CONTEXT_BUDGET})."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    asset_dir = args.asset_dir.expanduser()
    if not asset_dir.is_dir():
        print(f"Asset directory not found: {asset_dir}", file=sys.stderr)
        return 2
    if int(args.budget or 0) < MIN_CONTEXT_BUDGET:
        print(f"Budget must be at least {MIN_CONTEXT_BUDGET} estimated tokens.", file=sys.stderr)
        return 2
    try:
        _formula_payload, _memory_card, context_pack = build_pack(asset_dir, args.question, int(args.budget or 0))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Wrote context pack: {context_pack.get('artifact_path', '')}")
    print(f"Asset: {context_pack.get('asset_name', '')}")
    print(f"Estimated tokens: {context_pack.get('estimated_tokens', 0)} / {context_pack.get('budget', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
