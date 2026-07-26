#!/usr/bin/env python
"""Validate one or more committed/local native evidence v2 manifests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.native_identity import (  # noqa: E402
    NativeIdentityError,
    build_native_identity,
    validate_native_evidence_manifest,
)


def _manifest_paths(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(path.rglob("*.json"))
    raise NativeIdentityError(
        "NATIVE_TOOL_MISSING",
        "Native evidence path does not exist.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--evidence-dir",
        "--manifest",
        dest="evidence_path",
        type=Path,
        required=True,
    )
    parser.add_argument("--dll", type=Path)
    parser.add_argument("--pdb", type=Path)
    parser.add_argument("--experimental", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    if bool(args.dll) != bool(args.pdb):
        parser.error("--dll and --pdb must be provided together")

    try:
        identity = (
            build_native_identity(args.dll, args.pdb)
            if args.dll and args.pdb
            else None
        )
        paths = _manifest_paths(args.evidence_path)
        if not paths:
            raise NativeIdentityError(
                "NATIVE_EXPORT_SCHEMA_INVALID",
                "Native evidence directory contains no JSON manifests.",
            )
        validated = []
        skipped = []
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise NativeIdentityError(
                    "NATIVE_EXPORT_SCHEMA_INVALID",
                    f"Could not read native evidence JSON: {path.name}",
                ) from exc
            if payload.get("schema") != "blueprint-to-code-native-evidence-set/v2":
                if args.evidence_path.is_dir():
                    skipped.append(path.name)
                    continue
                raise NativeIdentityError(
                    "NATIVE_EXPORT_SCHEMA_INVALID",
                    "Native evidence manifest schema must be v2.",
                )
            validate_native_evidence_manifest(
                payload,
                expected_identity=identity,
                formal=not args.experimental,
            )
            validated.append(
                {
                    "file": path.name,
                    "evidenceSetId": payload["evidenceSetId"],
                    "trustStatus": payload["trust"]["status"],
                }
            )
        if not validated:
            raise NativeIdentityError(
                "NATIVE_EXPORT_SCHEMA_INVALID",
                "No native evidence v2 manifests were found.",
            )
        result = {
            "ok": True,
            "validatedCount": len(validated),
            "validated": validated,
            "skippedNonV2Count": len(skipped),
        }
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2 if args.pretty else None,
                sort_keys=args.pretty,
            )
        )
        return 0
    except NativeIdentityError as exc:
        print(
            json.dumps(
                exc.to_diagnostic(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError) as exc:
        diagnostic = NativeIdentityError(
            "NATIVE_TOOL_MISSING",
            str(exc),
        )
        print(
            json.dumps(
                diagnostic.to_diagnostic(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
