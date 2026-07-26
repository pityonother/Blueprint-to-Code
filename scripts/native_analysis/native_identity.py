#!/usr/bin/env python
"""CLI adapter for native build identity and provenance contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SCRIPTS_ROOT.parent
sys.path.insert(0, str(SCRIPTS_ROOT))

from blueprint_translator.native_identity import (  # noqa: E402
    NativeIdentityError,
    build_native_identity,
    create_native_evidence_manifest,
    create_native_project_manifest,
    validate_native_evidence_manifest,
    validate_native_project_manifest,
)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NativeIdentityError(
            "NATIVE_EXPORT_SCHEMA_INVALID",
            f"Could not read JSON input: {exc}",
        ) from exc


def _json_text(payload: Any, pretty: bool) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2 if pretty else None,
        sort_keys=pretty,
        separators=None if pretty else (",", ":"),
    )


def _emit(payload: Any, *, output: Path | None, pretty: bool) -> None:
    text = _json_text(payload, pretty)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_provenance(repository_root: Path) -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=repository_root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise NativeIdentityError(
            "NATIVE_EVIDENCE_PROVENANCE_MISMATCH",
            "Could not determine generator Git provenance.",
        ) from exc
    return {
        "repositoryCommit": commit,
        "repositoryDirty": bool(status.strip()),
    }


def _identity_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return build_native_identity(
        args.dll,
        args.pdb,
        project_prefix=args.project_prefix,
        project_hash_length=args.project_hash_length,
    )


def _command_build(args: argparse.Namespace) -> dict[str, Any]:
    return _identity_from_args(args)


def _command_project_manifest(args: argparse.Namespace) -> dict[str, Any]:
    return create_native_project_manifest(_identity_from_args(args))


def _command_validate_project(args: argparse.Namespace) -> dict[str, Any]:
    identity = _identity_from_args(args)
    manifest = _read_json(args.manifest)
    validate_native_project_manifest(manifest, expected_identity=identity)
    return {"ok": True, "project": identity["project"]}


def _command_validate_evidence(args: argparse.Namespace) -> dict[str, Any]:
    identity = _identity_from_args(args) if args.dll and args.pdb else None
    manifest = _read_json(args.manifest)
    validate_native_evidence_manifest(
        manifest,
        expected_identity=identity,
        formal=not args.experimental,
    )
    return {
        "ok": True,
        "evidenceSetId": manifest["evidenceSetId"],
        "trustStatus": manifest["trust"]["status"],
    }


def _command_wrap_legacy(args: argparse.Namespace) -> dict[str, Any]:
    identity = _identity_from_args(args)
    raw_export = _read_json(args.raw_export)
    toolchain = _read_json(args.toolchain)
    patterns = raw_export.get("patterns") or []
    recipe_bytes = json.dumps(
        {
            "recipeId": args.recipe_id,
            "patterns": patterns,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    recipe_sha = hashlib.sha256(recipe_bytes).hexdigest()
    analysis_options_sha = hashlib.sha256(
        json.dumps(
            {
                "analysisTimeoutSeconds": raw_export.get(
                    "analysisTimeoutSeconds"
                ),
                "decompileTimeoutSeconds": raw_export.get(
                    "decompileTimeoutSeconds"
                ),
                "pdbUniversal": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    generator = {
        **_git_provenance(args.repository_root),
        "recipeId": args.recipe_id,
        "recipeSha256": recipe_sha,
        "scriptSha256": {
            "runner": _file_sha256(args.runner),
            "exporter": _file_sha256(args.exporter),
            "pdbConfigurator": _file_sha256(args.pdb_configurator),
        },
    }
    ghidra_config = toolchain.get("ghidra") or {}
    java_config = toolchain.get("java") or {}
    return create_native_evidence_manifest(
        raw_export,
        identity=identity,
        ghidra={
            "version": args.ghidra_version or ghidra_config.get("version"),
            "releaseAssetSha256": ghidra_config.get("sha256"),
            "analysisOptionsSha256": analysis_options_sha,
        },
        java={
            "vendor": args.java_vendor or java_config.get("distribution"),
            "version": args.java_version or java_config.get("version"),
        },
        generator=generator,
        formal=not args.experimental,
    )


def _add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dll", type=Path, required=True)
    parser.add_argument("--pdb", type=Path, required=True)
    parser.add_argument("--project-prefix", default="ShooterGameNative")
    parser.add_argument("--project-hash-length", type=int, default=12)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and validate native binary/PDB provenance."
    )
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--output", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build")
    _add_identity_arguments(build)
    build.set_defaults(handler=_command_build)

    project = subparsers.add_parser("project-manifest")
    _add_identity_arguments(project)
    project.set_defaults(handler=_command_project_manifest)

    validate_project = subparsers.add_parser("validate-project")
    _add_identity_arguments(validate_project)
    validate_project.add_argument("--manifest", type=Path, required=True)
    validate_project.set_defaults(handler=_command_validate_project)

    validate_evidence = subparsers.add_parser("validate-evidence")
    validate_evidence.add_argument("--manifest", type=Path, required=True)
    validate_evidence.add_argument("--dll", type=Path)
    validate_evidence.add_argument("--pdb", type=Path)
    validate_evidence.add_argument("--project-prefix", default="ShooterGameNative")
    validate_evidence.add_argument("--project-hash-length", type=int, default=12)
    validate_evidence.add_argument("--experimental", action="store_true")
    validate_evidence.set_defaults(handler=_command_validate_evidence)

    wrap = subparsers.add_parser("wrap-legacy")
    _add_identity_arguments(wrap)
    wrap.add_argument("--raw-export", type=Path, required=True)
    wrap.add_argument("--toolchain", type=Path, required=True)
    wrap.add_argument(
        "--repository-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    wrap.add_argument(
        "--runner",
        type=Path,
        default=Path(__file__).with_name("Import-ShooterGameNative.ps1"),
    )
    wrap.add_argument(
        "--exporter",
        type=Path,
        default=Path(__file__).with_name("ghidra") / "ExportNativeTargets.java",
    )
    wrap.add_argument(
        "--pdb-configurator",
        type=Path,
        default=Path(__file__).with_name("ghidra") / "ConfigurePdbAnalyzer.java",
    )
    wrap.add_argument(
        "--recipe-id",
        default="legacy-hardcoded-native-targets/v1",
    )
    wrap.add_argument("--ghidra-version")
    wrap.add_argument("--java-vendor")
    wrap.add_argument("--java-version")
    wrap.add_argument("--experimental", action="store_true")
    wrap.set_defaults(handler=_command_wrap_legacy)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "dll", None) is None and getattr(args, "pdb", None):
        parser.error("--dll and --pdb must be provided together")
    if getattr(args, "dll", None) and getattr(args, "pdb", None) is None:
        parser.error("--dll and --pdb must be provided together")
    try:
        payload = args.handler(args)
        _emit(payload, output=args.output, pretty=args.pretty)
        return 0
    except NativeIdentityError as exc:
        print(_json_text(exc.to_diagnostic(), True), file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        diagnostic = NativeIdentityError(
            "NATIVE_TOOL_MISSING",
            str(exc),
        )
        print(_json_text(diagnostic.to_diagnostic(), True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
