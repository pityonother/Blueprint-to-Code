#!/usr/bin/env python3
"""Inventory validation duplication and freeze BTC acceptance baseline counts.

This command is deliberately read-only.  It describes the current workflow and
provided acceptance artifacts; it does not run validators or change a pointer.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any


SCHEMA = "blueprint-to-code.validation-flow-audit.v1"
EXPECTED_BTC_BASELINE = {
    "samples": 62,
    "formallyQueryable": 62,
    "physicalAssetInputs": 18,
    "realQueries": 14,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workflow",
        type=Path,
        default=Path(".github/workflows/ci.yml"),
        help="CI workflow to inventory (default: .github/workflows/ci.yml)",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path("scripts"),
        help="Python source tree to scan for local SHA-256 helpers",
    )
    parser.add_argument("--btc-result", type=Path)
    parser.add_argument("--input-bindings", type=Path)
    parser.add_argument("--query-matrix", type=Path)
    parser.add_argument(
        "--duration",
        action="append",
        default=[],
        metavar="NAME=SECONDS",
        help="Attach a measured baseline duration; may be repeated",
    )
    parser.add_argument("--output", type=Path, help="Also write the JSON audit")
    return parser


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _parse_durations(values: list[str]) -> dict[str, float]:
    durations: dict[str, float] = {}
    for value in values:
        name, separator, seconds_text = value.partition("=")
        if not separator or not name.strip() or not seconds_text.strip():
            raise ValueError("--duration must use NAME=SECONDS")
        try:
            seconds = float(seconds_text)
        except ValueError as exc:
            raise ValueError("--duration must use NAME=SECONDS") from exc
        if seconds < 0:
            raise ValueError("--duration SECONDS must be non-negative")
        durations[name.strip()] = seconds
    return dict(sorted(durations.items()))


def _workflow_audit(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    pytest_commands = [
        line.strip().removeprefix("run:").strip()
        for line in text.splitlines()
        if "python -m pytest" in line
    ]
    full_suite = any(
        re.fullmatch(r"python -m pytest(?:\s+-[^\s]+)*", command)
        for command in pytest_commands
    )
    redundant_pytest = max(0, len(pytest_commands) - 1) if full_suite else 0
    changed_ruff = "python -m ruff check --" in text
    full_ruff = "python -m ruff check scripts tests" in text
    return {
        "pytestInvocationCount": len(pytest_commands),
        "redundantPytestInvocationCount": redundant_pytest,
        "hasFullPytestSuite": full_suite,
        "duplicatesFullTreeRuff": changed_ruff and full_ruff,
        "ruffInvocationCount": text.count("python -m ruff check"),
    }


def _looks_like_sha256_file_helper(name: str) -> bool:
    """Match the repeated ``[_]sha256_file`` implementations we may unify later."""

    return name.lower().lstrip("_") == "sha256_file"


def _hash_helper_audit(root: Path) -> dict[str, object]:
    definitions: list[dict[str, object]] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                _looks_like_sha256_file_helper(node.name)
            ):
                definitions.append(
                    {
                        "file": path.relative_to(root).as_posix(),
                        "name": node.name,
                        "line": node.lineno,
                    }
                )
    return {
        "definitionCount": len(definitions),
        "fileCount": len({item["file"] for item in definitions}),
        "definitions": definitions,
    }


def _btc_baseline(
    *,
    result_path: Path | None,
    input_bindings_path: Path | None,
    query_matrix_path: Path | None,
) -> dict[str, object]:
    baseline: dict[str, object] = {
        "samples": None,
        "formallyQueryable": None,
        "physicalAssetInputs": None,
        "realQueries": None,
    }
    if result_path is not None:
        result = _load_object(result_path, "BTC result")
        counts = result.get("counts")
        if not isinstance(counts, dict):
            raise ValueError("BTC result counts must be a JSON object")
        baseline["samples"] = counts.get("samples")
        baseline["formallyQueryable"] = counts.get("ready")
    if input_bindings_path is not None:
        bindings = _load_object(input_bindings_path, "input bindings")
        inputs = bindings.get("assetSourceInputs")
        if not isinstance(inputs, list):
            raise ValueError("input bindings assetSourceInputs must be a JSON array")
        baseline["physicalAssetInputs"] = len(inputs)
    if query_matrix_path is not None:
        matrix = _load_object(query_matrix_path, "query matrix")
        rows = matrix.get("rows")
        if not isinstance(rows, list):
            raise ValueError("query matrix rows must be a JSON array")
        baseline["realQueries"] = len(rows)
    baseline["expected"] = EXPECTED_BTC_BASELINE
    baseline["matchesExpected"] = all(
        baseline[key] == expected
        for key, expected in EXPECTED_BTC_BASELINE.items()
        if baseline[key] is not None
    )
    return baseline


def build_audit(args: argparse.Namespace) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "workflow": _workflow_audit(args.workflow),
        "hashHelpers": _hash_helper_audit(args.source_root),
        "durationsSeconds": _parse_durations(args.duration),
        "btcBaseline": _btc_baseline(
            result_path=args.btc_result,
            input_bindings_path=args.input_bindings,
            query_matrix_path=args.query_matrix,
        ),
        "readOnly": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        payload = build_audit(args)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
