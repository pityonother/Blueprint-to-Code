"""Command-line argument parsing and dispatch."""

from __future__ import annotations

import argparse

from .asset import run_asset_binary_translate, run_asset_translate
from .capture import CAPTURE_GRAPH_TYPES, run_capture_asset
from .compare import run_asset_compare, run_compare
from .config import PROFILE_CONFIG
from .translate import run_translate

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Translate ARK DevKit / Unreal Blueprint clipboard text into reports, flow graphs, prompts, JSON, and diffs.")
    parser.add_argument("--input", "-i", help="Optional .txt file. If omitted, read Windows clipboard.")
    parser.add_argument("--asset-dir", help="Directory containing one Blueprint asset capture with graphs/*.txt and optional defaults/components sidecars.")
    parser.add_argument("--asset-binary", help="Blueprint Object Path to read graph content directly from a local .uasset/.uexp package.")
    parser.add_argument("--asset-binary-no-report", action="store_true", help="Only write uasset graph extraction files; do not run the asset report afterward.")
    parser.add_argument("--content-root", action="append", default=[], help="Extra ARK DevKit Content root for --asset-binary or uasset lookup.")
    parser.add_argument("--uasset-max-graphs", type=int, default=0, help="Debug limit for --asset-binary graph reads. 0 means all graphs.")
    parser.add_argument("--capture-asset", metavar="ASSET_DIR_OR_NAME", help="Start a clipboard capture workflow that builds an asset directory with graphs/*.txt and manifest.json.")
    parser.add_argument("--capture-root", help="Root directory for --capture-asset when a simple asset name is provided. Default: captures/ under the current directory.")
    parser.add_argument("--capture-once", metavar="GRAPH_NAME", help="Capture one graph from --input or clipboard, update manifest.json, then optionally run the asset report.")
    parser.add_argument("--capture-graph-type", choices=CAPTURE_GRAPH_TYPES, help="Graph type for --capture-once. Defaults to a name-based guess.")
    parser.add_argument("--capture-no-report", action="store_true", help="Only save captured graph files and manifest.json; do not run the asset report after capture.")
    parser.add_argument("--capture-overwrite", action="store_true", help="Overwrite an existing captured graph and save a backup under graphs/_backups/.")
    parser.add_argument("--output", "-o", help="Legacy report.md output path. Other files go beside it.")
    parser.add_argument("--output-dir", help="Directory for generated files.")
    parser.add_argument("--report-level", choices=["compact", "standard", "debug"], default="standard", help="Asset report verbosity. standard avoids large debug JSON; debug writes every intermediate artifact.")
    parser.add_argument("--keep-stale-output", action="store_true", help="Do not remove old generated asset output files before writing the selected report set.")
    parser.add_argument("--asset-name", help="Blueprint asset name/path label.")
    parser.add_argument("--graph-name", help="Graph/function/event graph label.")
    parser.add_argument("--ask", help="Question to place at the top of prompt.md.")
    parser.add_argument("--mode", choices=["summary", "pseudocode", "cpp", "prompt", "all"], default="all")
    parser.add_argument("--json", action="store_true", help="Accepted for compatibility; parsed.json is always written.")
    parser.add_argument("--compare", nargs=2, metavar=("OLD", "NEW"), help="Compare two parsed JSON files or two Blueprint .txt files.")
    parser.add_argument("--compare-asset", nargs=2, metavar=("OLD_ASSET_DIR", "NEW_ASSET_DIR"), help="Compare two Blueprint asset capture directories, including graphs and sidecars.")
    parser.add_argument("--keyword", action="append", default=[], help="Extra keyword to search for. Can be passed multiple times.")
    parser.add_argument("--keep-guids", action="store_true", help="Keep raw GUIDs in cleaned Blueprint text.")
    parser.add_argument("--include-raw", action="store_true", help="Include raw node text inside parsed.json.")
    parser.add_argument("--max-cleaned-lines", type=int, default=900, help="Maximum cleaned Blueprint lines in report/prompt.")
    parser.add_argument("--chunk", action="store_true", help="Write chunked reports/prompts under chunks/.")
    parser.add_argument("--max-chars", type=int, default=20000, help="Maximum raw node characters per chunk.")
    parser.add_argument("--chunk-by", choices=["exec-root", "connected-component", "comment"], default="exec-root")
    parser.add_argument("--defaults-file", help="Optional sidecar file with class defaults.")
    parser.add_argument("--components-file", help="Optional sidecar file with component list/details.")
    parser.add_argument("--notes-file", help="Optional sidecar notes or test observations.")
    parser.add_argument("--parent-class", help="Parent class context.")
    parser.add_argument("--interfaces", help="Comma/semicolon separated interface names.")
    parser.add_argument("--tags", help="Comma/semicolon separated tags.")
    parser.add_argument("--make-context-template", action="store_true", help="Write context_template.md to the output directory.")
    parser.add_argument("--profile", choices=sorted(PROFILE_CONFIG), default="ark")
    parser.add_argument("--provider", choices=["none", "ollama", "lmstudio", "openai", "anthropic"], default="none")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.asset_binary:
        return run_asset_binary_translate(args)
    if args.capture_asset:
        return run_capture_asset(args)
    if args.compare_asset:
        return run_asset_compare(args)
    if args.compare:
        return run_compare(args)
    if args.asset_dir:
        return run_asset_translate(args)
    return run_translate(args)
