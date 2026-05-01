"""Single-graph translation command workflow."""

from __future__ import annotations

import argparse
import json
import sys

from .context import context_from_args
from .core import parse_blueprint_text
from .diagnostics import diagnostics_payload, render_diagnostics_report
from .output import maybe_write_context_template, resolve_output_paths, write_chunks, write_glossary
from .renderers import (
    render_compact,
    render_cpp_reference,
    render_data_flow,
    render_exec_flow,
    render_prompt_file,
    render_pseudocode,
    render_report,
)
from .utils import profile_keywords, read_text

def run_translate(args: argparse.Namespace) -> int:
    paths = resolve_output_paths(args)
    if maybe_write_context_template(args, paths["dir"]) and not args.input and not clipboard_has_text_request(args):
        return 0
    keywords = profile_keywords(args.profile, args.keyword)
    raw_text, source = read_text(args.input)
    if not raw_text.strip():
        print("No Blueprint text found. Copy nodes in Unreal with Ctrl+C or pass --input.", file=sys.stderr)
        return 2
    context = context_from_args(args)
    cleaned, nodes, payload = parse_blueprint_text(text=raw_text, source=source, asset_name=args.asset_name or "", graph_name=args.graph_name or "", keywords=keywords, keep_guids=args.keep_guids, include_raw=args.include_raw, context=context)
    paths["report"].write_text(render_report(mode=args.mode, source=source, raw_text=raw_text, cleaned_text=cleaned, nodes=nodes, payload=payload, keywords=keywords, asset_name=args.asset_name or "", graph_name=args.graph_name or "", ask=args.ask or "", profile=args.profile, provider=args.provider, max_cleaned_lines=max(args.max_cleaned_lines, 50)), encoding="utf-8")
    paths["prompt"].write_text(render_prompt_file(nodes, payload, keywords, cleaned, args.asset_name or "", args.graph_name or "", args.ask or "", args.profile, args.provider, max(args.max_cleaned_lines, 50)), encoding="utf-8")
    paths["json"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["compact"].write_text(render_compact(payload, nodes, payload["data_flow"], args.asset_name or "", args.graph_name or "", args.ask or ""), encoding="utf-8")
    paths["exec_flow"].write_text(render_exec_flow(nodes, payload["exec_flow"], payload["data_flow"]), encoding="utf-8")
    paths["data_flow"].write_text(render_data_flow(payload["data_flow"]), encoding="utf-8")
    paths["diagnostics_report"].write_text(render_diagnostics_report(payload), encoding="utf-8")
    paths["diagnostics_json"].write_text(json.dumps(diagnostics_payload(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    paths["pseudocode"].write_text(render_pseudocode(nodes, payload["exec_flow"], payload["data_flow"]), encoding="utf-8")
    paths["cpp"].write_text(render_cpp_reference(nodes, payload["exec_flow"], payload["data_flow"], args.asset_name or "", args.graph_name or ""), encoding="utf-8")
    write_glossary(paths["dir"])
    if args.chunk:
        write_chunks(args, paths["dir"], nodes, payload, keywords, args.asset_name or "", args.graph_name or "", args.profile, args.provider)
    if args.provider != "none":
        print(f"Provider '{args.provider}' is reserved. No model call was made; prompt.md was generated.")
    print(f"Wrote output directory: {paths['dir']}")
    for label in ("report", "prompt", "json", "compact", "exec_flow", "data_flow", "diagnostics_report", "diagnostics_json", "pseudocode", "cpp"):
        print(f"- {label}: {paths[label]}")
    print(f"Parsed nodes: {len(nodes)}")
    print(f"Confidence: {payload['diagnostics']['confidence_level']}")
    return 0


def clipboard_has_text_request(args: argparse.Namespace) -> bool:
    return bool(args.input or args.ask or args.asset_name or args.graph_name)
