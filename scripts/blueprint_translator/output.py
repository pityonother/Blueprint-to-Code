"""Output path and chunk-writing helpers."""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict, deque
from pathlib import Path

from .config import ARK_GLOSSARY, CONTEXT_TEMPLATE
from .core import parse_blueprint_text
from .flow import ordered_nodes_by_exec
from .models import NodeInfo
from .renderers import render_prompt_file, render_report
from .utils import now_stamp

def default_output_dir(prefix: str = "blueprint_translation") -> Path:
    return Path.home() / "Desktop" / f"{prefix}_{now_stamp()}"


def resolve_output_paths(args: argparse.Namespace, compare: bool = False) -> dict[str, Path]:
    if args.output_dir:
        out_dir = Path(os.path.expandvars(args.output_dir)).expanduser()
    elif args.output:
        out_dir = Path(os.path.expandvars(args.output)).expanduser().parent
    else:
        out_dir = default_output_dir("blueprint_compare" if compare else "blueprint_translation")
    out_dir.mkdir(parents=True, exist_ok=True)
    report_name = "compare_report.md" if compare else "report.md"
    report = Path(os.path.expandvars(args.output)).expanduser() if args.output and not compare else out_dir / report_name
    return {
        "dir": out_dir,
        "report": report,
        "prompt": out_dir / ("compare_prompt.md" if compare else "prompt.md"),
        "json": out_dir / ("compare.json" if compare else "parsed.json"),
        "compact": out_dir / "compact.txt",
        "exec_flow": out_dir / "exec_flow.md",
        "data_flow": out_dir / "data_flow.md",
        "diagnostics_report": out_dir / "diagnostics_report.md",
        "diagnostics_json": out_dir / "diagnostics.json",
        "capture_quality_report": out_dir / "capture_quality_report.md",
        "capture_quality_json": out_dir / "capture_quality.json",
        "defaults_suggestions": out_dir / "defaults_suggestions.json",
        "components_suggestions": out_dir / "components_suggestions.json",
        "next_actions": out_dir / "next_actions.md",
        "pseudocode": out_dir / "pseudocode.md",
        "cpp": out_dir / "cpp_reference.md",
        "compare_summary": out_dir / "compare_summary.md",
        "asset_report": out_dir / "asset_report.md",
        "asset_json": out_dir / "asset.json",
        "call_graph": out_dir / "call_graph.md",
        "graph_reports": out_dir / "graph_reports",
    }


def write_glossary(out_dir: Path) -> None:
    (out_dir / "ark_glossary.json").write_text(json.dumps(ARK_GLOSSARY, ensure_ascii=False, indent=2), encoding="utf-8")


def maybe_write_context_template(args: argparse.Namespace, out_dir: Path) -> bool:
    if not args.make_context_template:
        return False
    target = out_dir / "context_template.md"
    target.write_text(CONTEXT_TEMPLATE, encoding="utf-8")
    print(f"Wrote context template: {target}")
    return True


def chunk_nodes(nodes: list[NodeInfo], payload: dict[str, object], chunk_by: str, max_chars: int) -> list[list[NodeInfo]]:
    if not nodes:
        return []
    if chunk_by == "exec-root":
        ordered = ordered_nodes_by_exec(nodes, payload["exec_flow"])
        return split_node_groups_by_chars([ordered], max_chars)
    if chunk_by == "connected-component":
        groups = connected_components(nodes)
        return split_node_groups_by_chars(groups, max_chars)
    groups: list[list[NodeInfo]] = []
    current: list[NodeInfo] = []
    current_chars = 0
    for node in nodes:
        if current and current_chars + len(node.raw) > max_chars:
            groups.append(current)
            current = []
            current_chars = 0
        current.append(node)
        current_chars += len(node.raw)
    if current:
        groups.append(current)
    return groups


def connected_components(nodes: list[NodeInfo]) -> list[list[NodeInfo]]:
    by_name = {node.name: node for node in nodes}
    graph: dict[str, set[str]] = defaultdict(set)
    for node in nodes:
        graph[node.name]
        for pin in node.pins:
            for link in pin.links:
                target = link.get("target_node", "")
                if target in by_name:
                    graph[node.name].add(target)
                    graph[target].add(node.name)
    groups: list[list[NodeInfo]] = []
    seen: set[str] = set()
    for node in nodes:
        if node.name in seen:
            continue
        queue = deque([node.name])
        seen.add(node.name)
        names: list[str] = []
        while queue:
            name = queue.popleft()
            names.append(name)
            for next_name in graph[name]:
                if next_name not in seen:
                    seen.add(next_name)
                    queue.append(next_name)
        groups.append([by_name[name] for name in names if name in by_name])
    return groups


def split_node_groups_by_chars(groups: list[list[NodeInfo]], max_chars: int) -> list[list[NodeInfo]]:
    chunks: list[list[NodeInfo]] = []
    for group in groups:
        current: list[NodeInfo] = []
        current_chars = 0
        for node in group:
            if current and current_chars + len(node.raw) > max_chars:
                chunks.append(current)
                current = []
                current_chars = 0
            current.append(node)
            current_chars += len(node.raw)
        if current:
            chunks.append(current)
    return chunks


def write_chunks(args: argparse.Namespace, out_dir: Path, nodes: list[NodeInfo], payload: dict[str, object], keywords: list[str], asset_name: str, graph_name: str, profile: str, provider: str) -> None:
    chunks = chunk_nodes(nodes, payload, args.chunk_by, max(args.max_chars, 1000))
    chunks_dir = out_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    index_lines = ["# Blueprint Chunks", "", f"- Chunk by: {args.chunk_by}", f"- Max chars: {args.max_chars}", f"- Total chunks: {len(chunks)}", ""]
    for i, group in enumerate(chunks, start=1):
        raw = "\n".join(node.raw for node in group)
        cleaned, group_nodes, group_payload = parse_blueprint_text(text=raw, source=f"chunk {i}", asset_name=asset_name, graph_name=graph_name, keywords=keywords, keep_guids=args.keep_guids, include_raw=args.include_raw, context=payload.get("context", {}))
        base = f"chunk_{i:03d}"
        report_path = chunks_dir / f"{base}_report.md"
        prompt_path = chunks_dir / f"{base}_prompt.md"
        report_path.write_text(render_report(mode="summary", source=f"chunk {i}", raw_text=raw, cleaned_text=cleaned, nodes=group_nodes, payload=group_payload, keywords=keywords, asset_name=asset_name, graph_name=graph_name, ask=args.ask or "", profile=profile, provider=provider, max_cleaned_lines=args.max_cleaned_lines), encoding="utf-8")
        prompt_path.write_text(render_prompt_file(group_nodes, group_payload, keywords, cleaned, asset_name, graph_name, args.ask or "", profile, provider, args.max_cleaned_lines), encoding="utf-8")
        index_lines.append(f"- [{base}_report.md]({base}_report.md): {len(group)} nodes")
    (chunks_dir / "index.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
