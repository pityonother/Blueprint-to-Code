#!/usr/bin/env python
"""Resolve Blueprint call sites against one version-bound Native Evidence set."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.hybrid_evidence import (  # noqa: E402
    build_hybrid_evidence_payload,
    extract_blueprint_calls,
    write_hybrid_evidence_artifacts,
)
from blueprint_translator.native_evidence_repository import (  # noqa: E402
    open_native_evidence_repository,
)


CALLS_SCHEMA = "blueprint-to-code-blueprint-native-calls/v1"


def _lexical_absolute(path: str | os.PathLike[str]) -> Path:
    """Return an absolute path without following links or reparse points."""

    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def _read_calls(path: Path) -> tuple[list[dict[str, object]], dict[str, str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Blueprint call JSON cannot be read: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Blueprint call JSON must contain an object")
    if payload.get("schema") != CALLS_SCHEMA:
        raise ValueError(f"Blueprint call JSON schema must be {CALLS_SCHEMA!r}")
    calls_value = payload.get("calls")
    if not isinstance(calls_value, list) or any(
        not isinstance(row, dict) for row in calls_value
    ):
        raise ValueError("Blueprint call JSON calls must be an array of objects")
    revision = str(payload.get("blueprintRevisionId") or "")
    fingerprint = str(payload.get("blueprintSourceFingerprint") or "")
    if not revision or not fingerprint:
        raise ValueError(
            "Blueprint call JSON must include revision and source fingerprint"
        )
    return [dict(row) for row in calls_value], {
        "revisionId": revision,
        "sourceFingerprint": fingerprint,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create project-level Blueprint-to-Native edges without selecting "
            "ambiguous short-name matches."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--asset-dir", type=Path)
    source.add_argument("--calls-json", type=Path)
    parser.add_argument("--native-evidence-dir", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("analysis") / "evidence_graph",
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.calls_json is not None:
            calls, identity = _read_calls(args.calls_json.resolve())
        else:
            calls, identity = extract_blueprint_calls(
                _lexical_absolute(args.asset_dir)
            )
        with open_native_evidence_repository(
            args.native_evidence_dir.resolve()
        ) as native:
            payload = build_hybrid_evidence_payload(
                blueprint_calls=calls,
                native_functions=native.list_functions(),
                blueprint_revision_id=identity["revisionId"],
                blueprint_source_fingerprint=identity["sourceFingerprint"],
                native_evidence_set_id=native.evidence_set_id,
                native_source_fingerprint=native.source_sha256,
            )
        result = write_hybrid_evidence_artifacts(
            payload,
            args.output_dir.resolve(),
        )
        status_counts: dict[str, int] = {}
        for edge in payload["edges"]:
            status = str(edge.get("status") or "")
            status_counts[status] = status_counts.get(status, 0) + 1
        response: dict[str, Any] = {
            "ok": True,
            "edgeCount": len(payload["edges"]),
            "statusCounts": status_counts,
            **result,
        }
        exit_code = 0
    except Exception as exc:
        response = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        exit_code = 1
    print(
        json.dumps(
            response,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
            default=str,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
