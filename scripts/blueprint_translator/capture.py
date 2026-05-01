"""Clipboard capture workflow for building multi-graph asset directories."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
from pathlib import Path
from typing import Iterable

from .utils import read_clipboard, read_text, safe_filename, split_csvish

CAPTURE_GRAPH_TYPES = ("EventGraph", "Function", "Macro", "ConstructionScript", "Unknown")


def resolve_capture_asset_dir(capture_asset: str, capture_root: str | None = None) -> Path:
    raw = Path(os.path.expandvars(capture_asset)).expanduser()
    if capture_root:
        root = Path(os.path.expandvars(capture_root)).expanduser()
        return root / safe_filename(raw.name or capture_asset, "BlueprintAsset")
    if raw.is_absolute() or raw.parent != Path(".") or any(separator in capture_asset for separator in ("\\", "/")):
        return raw
    return Path("captures") / safe_filename(capture_asset, "BlueprintAsset")


def infer_graph_type(graph_name: str) -> str:
    lowered = graph_name.lower()
    if "construction" in lowered:
        return "ConstructionScript"
    if "macro" in lowered or lowered.startswith("macro_"):
        return "Macro"
    if lowered == "eventgraph" or "eventgraph" in lowered or lowered.startswith("event_"):
        return "EventGraph"
    if lowered.startswith(("function_", "func_")) or "function" in lowered:
        return "Function"
    return "Unknown"


def blueprint_capture_warnings(text: str) -> list[str]:
    warnings: list[str] = []
    stripped = text.strip()
    if not stripped:
        return ["Clipboard/input is empty."]
    begin_count = stripped.count("Begin Object")
    end_count = stripped.count("End Object")
    if begin_count == 0:
        warnings.append("No 'Begin Object' blocks were found; this does not look like copied Blueprint node text.")
    if begin_count != end_count:
        warnings.append(f"Begin Object / End Object count differs ({begin_count} vs {end_count}).")
    if "CustomProperties Pin" not in stripped:
        warnings.append("No CustomProperties Pin lines were found; pins and links may be unavailable.")
    return warnings


def graph_capture_path(asset_dir: Path, graph_name: str) -> Path:
    return asset_dir / "graphs" / f"{safe_filename(graph_name, 'Graph')}.txt"


def manifest_graph_records(manifest: dict[str, object]) -> list[dict[str, object]]:
    graphs = manifest.get("graphs", [])
    if isinstance(graphs, dict):
        records: list[dict[str, object]] = []
        for name, value in graphs.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("name", str(name))
            else:
                item = {"name": str(name), "path": str(value)}
            records.append(item)
        return records
    if isinstance(graphs, list):
        return [dict(item) for item in graphs if isinstance(item, dict)]
    return []


def load_capture_manifest(asset_dir: Path) -> dict[str, object]:
    manifest_path = asset_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8-sig"))


def upsert_graph_record(records: list[dict[str, object]], record: dict[str, object]) -> list[dict[str, object]]:
    graph_name = str(record.get("name") or record.get("graph_name") or "").lower()
    graph_path = str(record.get("path") or "").replace("\\", "/").lower()
    result: list[dict[str, object]] = []
    replaced = False
    for existing in records:
        existing_name = str(existing.get("name") or existing.get("graph_name") or "").lower()
        existing_path = str(existing.get("path") or "").replace("\\", "/").lower()
        if (graph_name and existing_name == graph_name) or (graph_path and existing_path == graph_path):
            result.append(record)
            replaced = True
        else:
            result.append(existing)
    if not replaced:
        result.append(record)
    return result


def write_capture_manifest(
    asset_dir: Path,
    asset_name: str,
    records: Iterable[dict[str, object]],
    *,
    parent_class: str = "",
    interfaces: Iterable[str] = (),
    tags: Iterable[str] = (),
) -> Path:
    manifest: dict[str, object] = {
        "asset_name": asset_name,
        "graphs": list(records),
    }
    if parent_class:
        manifest["parent_class"] = parent_class
    interface_values = [str(item) for item in interfaces if str(item)]
    if interface_values:
        manifest["interfaces"] = interface_values
    tag_values = [str(item) for item in tags if str(item)]
    if tag_values:
        manifest["tags"] = tag_values
    path = asset_dir / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_captured_graph(asset_dir: Path, graph_name: str, graph_type: str, text: str) -> dict[str, object]:
    if not graph_name.strip():
        raise ValueError("Graph name is required.")
    if not text.strip():
        raise ValueError("Blueprint capture text is empty.")
    asset_dir.mkdir(parents=True, exist_ok=True)
    graphs_dir = asset_dir / "graphs"
    graphs_dir.mkdir(parents=True, exist_ok=True)
    graph_path = graph_capture_path(asset_dir, graph_name)
    graph_path.write_text(text.lstrip("\ufeff"), encoding="utf-8")
    return {
        "name": graph_name,
        "type": graph_type or infer_graph_type(graph_name),
        "path": graph_path.relative_to(asset_dir).as_posix(),
        "captured_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "characters": len(text),
        "warnings": blueprint_capture_warnings(text),
    }


def maybe_write_capture_sidecars(asset_dir: Path) -> None:
    defaults_path = asset_dir / "defaults.json"
    if not defaults_path.exists():
        defaults_path.write_text(json.dumps({"variables": {}, "classDefaults": {}}, indent=2), encoding="utf-8")
    components_path = asset_dir / "components.json"
    if not components_path.exists():
        components_path.write_text(json.dumps({"components": []}, indent=2), encoding="utf-8")
    notes_path = asset_dir / "notes.md"
    if not notes_path.exists():
        notes_path.write_text(
            "# Capture Notes\n\n"
            "Use this file to preserve things you confirm in ARK DevKit.\n\n"
            "Examples:\n\n"
            "```text\n"
            "inherited: ClearJump, GetGlidingPitch\n"
            "native: Delay, FormatAsTime\n"
            "ignore missing graph: FooBar\n"
            "SomeFunction: parent - implemented by Dino_Character_BP\n"
            "```\n",
            encoding="utf-8",
        )


def _read_capture_text(args: argparse.Namespace) -> tuple[str, str]:
    if args.input:
        return read_text(args.input)
    return read_clipboard().lstrip("\ufeff"), "Windows clipboard"


def _print_capture_warnings(warnings: Iterable[str]) -> None:
    values = list(warnings)
    if not values:
        return
    print("Capture warnings:")
    for warning in values:
        print(f"- {warning}")


def _commit_capture(
    args: argparse.Namespace,
    asset_dir: Path,
    asset_name: str,
    records: list[dict[str, object]],
    graph_name: str,
    graph_type: str,
    text: str,
) -> list[dict[str, object]]:
    record = save_captured_graph(asset_dir, graph_name, graph_type, text)
    records = upsert_graph_record(records, record)
    write_capture_manifest(
        asset_dir,
        asset_name,
        records,
        parent_class=args.parent_class or "",
        interfaces=split_csvish(args.interfaces),
        tags=split_csvish(args.tags),
    )
    print(f"Saved graph: {asset_dir / record['path']}")
    _print_capture_warnings(record.get("warnings", []))
    return records


def _interactive_capture(args: argparse.Namespace, asset_dir: Path, asset_name: str, records: list[dict[str, object]]) -> list[dict[str, object]]:
    print(f"Capture directory: {asset_dir}")
    print("For each Blueprint page: select all nodes in Unreal/ARK DevKit, copy, then return here.")
    print("Leave graph name empty to finish.")
    while True:
        try:
            graph_name = input("Graph name: ").strip()
        except EOFError:
            break
        if not graph_name:
            break
        default_type = infer_graph_type(graph_name)
        graph_type = input(f"Graph type [{default_type}]: ").strip() or default_type
        if graph_type not in CAPTURE_GRAPH_TYPES:
            print(f"Unknown graph type '{graph_type}', using Unknown.")
            graph_type = "Unknown"
        input("Copy the graph page now, then press Enter to read clipboard...")
        while True:
            text = read_clipboard().lstrip("\ufeff")
            warnings = blueprint_capture_warnings(text)
            if warnings:
                _print_capture_warnings(warnings)
                choice = input("Save anyway, retry, skip, or finish? [s/r/k/q]: ").strip().lower() or "s"
                if choice.startswith("r"):
                    input("Copy again, then press Enter...")
                    continue
                if choice.startswith("k"):
                    break
                if choice.startswith("q"):
                    return records
            records = _commit_capture(args, asset_dir, asset_name, records, graph_name, graph_type, text)
            break
    return records


def run_capture_asset(args: argparse.Namespace) -> int:
    asset_dir = resolve_capture_asset_dir(args.capture_asset, args.capture_root)
    manifest = load_capture_manifest(asset_dir)
    records = manifest_graph_records(manifest)
    asset_name = args.asset_name or str(manifest.get("asset_name") or asset_dir.name)
    asset_dir.mkdir(parents=True, exist_ok=True)
    (asset_dir / "graphs").mkdir(parents=True, exist_ok=True)

    if args.capture_once:
        text, source = _read_capture_text(args)
        graph_type = args.capture_graph_type or infer_graph_type(args.capture_once)
        print(f"Capturing {args.capture_once} from {source}")
        records = _commit_capture(args, asset_dir, asset_name, records, args.capture_once, graph_type, text)
    else:
        records = _interactive_capture(args, asset_dir, asset_name, records)
        write_capture_manifest(
            asset_dir,
            asset_name,
            records,
            parent_class=args.parent_class or "",
            interfaces=split_csvish(args.interfaces),
            tags=split_csvish(args.tags),
        )

    maybe_write_capture_sidecars(asset_dir)
    print(f"Manifest: {asset_dir / 'manifest.json'}")
    print(f"Captured graphs: {len(records)}")
    if args.capture_no_report:
        print(f"Next: python scripts\\bp_clipboard_to_prompt.py --asset-dir \"{asset_dir}\"")
        return 0

    from .asset import run_asset_translate

    args.asset_dir = str(asset_dir)
    args.asset_name = asset_name
    if not args.output_dir:
        args.output_dir = str(asset_dir / "output")
    return run_asset_translate(args)
