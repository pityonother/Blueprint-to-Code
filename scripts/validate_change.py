#!/usr/bin/env python3
"""Run one risk-aware validation plan and write a machine-readable receipt."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Mapping, Sequence


RECEIPT_SCHEMA: Final = "blueprint-to-code.change-validation-receipt.v1"
CACHE_SCHEMA: Final = "blueprint-to-code.content-identity-cache.v1"
RISK_ORDER: Final = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
INTEGRITY_ROLES: Final = frozenset({"source", "database", "manifest", "pointer"})
ARKDEV_MCP_QUERY_TESTS: Final = (
    "tests/test_arkdev_native_class_probe.py",
    "tests/test_arkdev_mcp_blueprint_tools.py",
    "tests/test_arkdev_mcp_contracts.py",
    "tests/test_arkdev_mcp_editor_binding.py",
    "tests/test_arkdev_mcp_editor_bridge_file.py",
    "tests/test_arkdev_mcp_editor_stdio.py",
    "tests/test_arkdev_mcp_patch_plan.py",
    "tests/test_arkdev_mcp_server.py",
    "tests/test_arkdev_mcp_stdio.py",
    "tests/test_arkdev_mcp_task_context.py",
    "tests/test_arkdev_mcp_task_research.py",
    "tests/test_arkdev_mcp_task_stdio.py",
    "tests/test_arkdev_mcp_windows.py",
    "tests/test_devkit_editor_bridge_source_contract.py",
)


@dataclass(frozen=True)
class Classification:
    selected_profile: str
    risk: str
    affected_profiles: tuple[str, ...]
    capabilities: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CommandSpec:
    identifier: str
    argv: tuple[str, ...]
    reason: str


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_config(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"validation config is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != (
        "blueprint-to-code.validation-profiles.v1"
    ):
        raise ValueError("validation config schema is unsupported")
    profiles = payload.get("profiles")
    unknown = payload.get("unknown")
    if not isinstance(profiles, dict) or not profiles or not isinstance(unknown, dict):
        raise ValueError("validation config requires profiles and unknown policy")
    for name, raw in {**profiles, "unknown": unknown}.items():
        if not isinstance(raw, dict) or raw.get("risk") not in RISK_ORDER:
            raise ValueError(f"validation profile {name} has invalid risk")
        capabilities = raw.get("capabilities")
        if not isinstance(capabilities, list) or not all(
            isinstance(value, str) and value for value in capabilities
        ):
            raise ValueError(f"validation profile {name} has invalid capabilities")
        if name != "unknown":
            patterns = raw.get("patterns")
            if not isinstance(patterns, list) or not all(
                isinstance(value, str) and value for value in patterns
            ):
                raise ValueError(f"validation profile {name} has invalid patterns")
    return payload


def _matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path.replace("\\", "/"), pattern)


def classify_changed_files(
    changed_files: Sequence[str],
    config: Mapping[str, Any],
) -> Classification:
    profiles = config["profiles"]
    affected: set[str] = set()
    reasons: list[str] = []
    unknown_files: list[str] = []
    for raw_path in changed_files:
        path = str(raw_path).replace("\\", "/")
        matches = {
            name
            for name, profile in profiles.items()
            if any(_matches(path, pattern) for pattern in profile["patterns"])
        }
        if matches:
            affected.update(matches)
        else:
            unknown_files.append(path)
    if unknown_files or not changed_files:
        affected.add("unknown")
        reasons.append("UNKNOWN_FILE_CLASS")

    def profile_payload(name: str) -> Mapping[str, object]:
        return config["unknown"] if name == "unknown" else profiles[name]

    highest = max(
        affected,
        key=lambda name: (
            RISK_ORDER[str(profile_payload(name)["risk"])],
            name == "unknown",
            -list(profiles).index(name) if name in profiles else 0,
        ),
    )
    capabilities = sorted(
        {
            str(capability)
            for name in affected
            for capability in profile_payload(name)["capabilities"]
        }
    )
    reasons.extend(f"MATCHED_{name.upper()}" for name in sorted(affected - {"unknown"}))
    return Classification(
        selected_profile=highest,
        risk=str(profile_payload(highest)["risk"]),
        affected_profiles=tuple(sorted(affected)),
        capabilities=tuple(capabilities),
        reasons=tuple(reasons),
    )


def apply_requested_profile(
    detected: Classification,
    requested: str,
    config: Mapping[str, Any],
) -> Classification:
    if requested == "auto":
        return detected
    profiles = config["profiles"]
    if requested not in profiles:
        raise ValueError(f"unknown validation profile: {requested}")
    requested_risk = str(profiles[requested]["risk"])
    if RISK_ORDER[requested_risk] < RISK_ORDER[detected.risk]:
        raise ValueError(
            f"explicit profile {requested} cannot downgrade detected {detected.risk} risk"
        )
    affected = tuple(sorted(set(detected.affected_profiles) | {requested}))
    capabilities = tuple(
        sorted(
            set(detected.capabilities)
            | {str(value) for value in profiles[requested]["capabilities"]}
        )
    )
    return replace(
        detected,
        selected_profile=requested,
        risk=requested_risk,
        affected_profiles=affected,
        capabilities=capabilities,
        reasons=tuple([*detected.reasons, f"EXPLICIT_{requested.upper()}"]),
    )


def _npm_command() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def build_command_plan(
    classification: Classification,
    *,
    changed_files: Sequence[str],
    base_sha: str,
    python_command: str,
) -> list[CommandSpec]:
    commands: list[CommandSpec] = [
        CommandSpec(
            "git-diff-check",
            ("git", "diff", "--check", base_sha, "--"),
            "Reject whitespace and conflict-marker damage in the complete change.",
        )
    ]
    changed_python = sorted(
        path for path in changed_files if path.endswith(".py") and Path(path).is_file()
    )
    if changed_python:
        commands.append(
            CommandSpec(
                "python-ruff-changed",
                (python_command, "-m", "ruff", "check", "--", *changed_python),
                "Lint each changed Python file once.",
            )
        )

    capabilities = set(classification.capabilities)
    if "full-python" in capabilities:
        commands.append(
            CommandSpec(
                "python-full-suite",
                (python_command, "-m", "pytest", "-q"),
                "L2/L3 or unknown changes require the complete Python suite.",
            )
        )
    elif "query" in capabilities:
        commands.append(
            CommandSpec(
                "python-query-contracts",
                (
                    python_command,
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_evidence_policy.py",
                    "tests/test_evidence_query.py",
                    "tests/test_evidence_cli.py",
                    *ARKDEV_MCP_QUERY_TESTS,
                ),
                "Exercise the real query CLI and MCP contract without a full-suite duplicate.",
            )
        )
    elif "docs" in capabilities:
        commands.append(
            CommandSpec(
                "python-doc-contracts",
                (python_command, "tests/test_documentation_consistency.py"),
                "Docs-only changes run their path and documentation contract.",
            )
        )

    if "frontend" in capabilities:
        for identifier, script in (
            ("frontend-api-contract", "tests/api_frontend_contract.mjs"),
            ("frontend-blueprint-contract", "tests/blueprint_frontend_contract.mjs"),
            ("frontend-core-contract", "tests/frontend_core_contract.mjs"),
            ("frontend-harvest-contract", "tests/harvest_frontend_contract.mjs"),
            ("frontend-knowledge-contract", "tests/knowledge_frontend_contract.mjs"),
        ):
            commands.append(
                CommandSpec(
                    identifier,
                    ("node", script),
                    "Run one distinct frontend contract node.",
                )
            )
        commands.append(
            CommandSpec(
                "frontend-build",
                (_npm_command(), "run", "build"),
                "Prove the production frontend bundle compiles.",
            )
        )
    if "release" in classification.affected_profiles:
        commands.append(
            CommandSpec(
                "frontend-audit",
                (_npm_command(), "audit", "--audit-level=high"),
                "Release validation retains the dependency security gate.",
            )
        )

    identifiers = [command.identifier for command in commands]
    if len(identifiers) != len(set(identifiers)):
        raise AssertionError("validation plan contains duplicate command identifiers")
    return commands


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(_canonical_bytes(payload) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def content_identity_cache(
    inputs: Mapping[str, Path],
    cache_dir: Path,
) -> dict[str, object]:
    if set(inputs) != INTEGRITY_ROLES:
        raise ValueError(
            "content identity cache requires exactly source, database, manifest, and pointer"
        )
    from blueprint_translator.evidence_policy import POLICY_VERSION
    from blueprint_translator.evidence_schema import (
        EVIDENCE_SCHEMA_VERSION,
        LEGACY_CAPTURE_PARSER_VERSION,
    )
    from blueprint_translator.evidence_writer import DIRECT_PAYLOAD_PARSER_VERSION

    bindings: dict[str, dict[str, object]] = {}
    for role in sorted(inputs):
        path = inputs[role]
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"integrity input {role} must be one non-empty file")
        sha256, size = _sha256_file(path)
        bindings[role] = {"sha256": sha256, "bytes": size}
    identity = {
        "bindings": bindings,
        "versions": {
            "directParser": DIRECT_PAYLOAD_PARSER_VERSION,
            "legacyParser": LEGACY_CAPTURE_PARSER_VERSION,
            "evidenceSchema": EVIDENCE_SCHEMA_VERSION,
            "policy": POLICY_VERSION,
        },
    }
    key = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    cache_path = cache_dir / f"{key}.json"
    hit = False
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            cached = None
        hit = bool(
            isinstance(cached, dict)
            and cached.get("schema") == CACHE_SCHEMA
            and cached.get("key") == key
            and cached.get("identity") == identity
        )
    if not hit:
        _atomic_write_json(
            cache_path,
            {"schema": CACHE_SCHEMA, "key": key, "identity": identity},
        )
    return {
        "enabled": True,
        "key": key,
        "hit": hit,
        "identity": identity,
        "scope": "CONTENT_IDENTICAL_ASSET_INTEGRITY_ONLY",
        "usedToSkipCommands": [],
    }


def _run_git(repo: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"git {' '.join(arguments)} failed: {detail}")
    return completed.stdout


def resolve_commit(repo: Path, revision: str) -> str:
    return _run_git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}").decode(
        "ascii"
    ).strip()


def discover_changed_files(repo: Path, base_sha: str) -> list[str]:
    changed_raw = _run_git(
        repo,
        "diff",
        "--name-only",
        "--diff-filter=ACMRT",
        "-z",
        base_sha,
        "--",
    )
    untracked_raw = _run_git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    return sorted(
        {
            os.fsdecode(item).replace("\\", "/")
            for raw in (changed_raw, untracked_raw)
            for item in raw.split(b"\0")
            if item
        }
    )


def _changed_file_bindings(repo: Path, files: Sequence[str]) -> list[dict[str, object]]:
    bindings: list[dict[str, object]] = []
    for relative in files:
        path = repo.joinpath(*relative.split("/"))
        if not path.is_file():
            bindings.append({"path": relative, "sha256": None, "bytes": 0})
            continue
        sha256, size = _sha256_file(path)
        bindings.append({"path": relative, "sha256": sha256, "bytes": size})
    return bindings


def _parse_integrity_inputs(values: Sequence[str]) -> dict[str, Path]:
    inputs: dict[str, Path] = {}
    for value in values:
        role, separator, path_text = value.partition("=")
        if not separator or role not in INTEGRITY_ROLES or not path_text:
            raise ValueError(
                "--integrity-input must use source|database|manifest|pointer=PATH"
            )
        if role in inputs:
            raise ValueError(f"duplicate integrity input role: {role}")
        inputs[role] = Path(path_text)
    return inputs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Git base commit or revision")
    parser.add_argument(
        "--profile",
        default="auto",
        choices=("auto", "docs", "query", "frontend", "parser", "publication", "native", "release"),
    )
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("validation_profiles.json"),
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--python-command", default="python")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--integrity-input", action="append", default=[])
    parser.add_argument("--cache-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    started_wall = datetime.now(UTC)
    started = time.perf_counter()
    try:
        repo = args.repo.resolve()
        config = load_config(args.config)
        base_sha = resolve_commit(repo, args.base)
        head_sha = resolve_commit(repo, "HEAD")
        changed_files = discover_changed_files(repo, base_sha)
        detected = classify_changed_files(changed_files, config)
        classification = apply_requested_profile(detected, args.profile, config)
        commands = build_command_plan(
            classification,
            changed_files=changed_files,
            base_sha=base_sha,
            python_command=args.python_command,
        )
        integrity_inputs = _parse_integrity_inputs(args.integrity_input)
        if integrity_inputs and args.cache_dir is None:
            raise ValueError("--cache-dir is required with --integrity-input")
        cache: dict[str, object] = {
            "enabled": False,
            "hit": False,
            "scope": "CONTENT_IDENTICAL_ASSET_INTEGRITY_ONLY",
            "usedToSkipCommands": [],
        }
        if integrity_inputs:
            cache = content_identity_cache(integrity_inputs, args.cache_dir)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    command_receipts: list[dict[str, object]] = []
    failed = False
    for command in commands:
        if failed:
            command_receipts.append(
                {
                    "id": command.identifier,
                    "argv": list(command.argv),
                    "reason": command.reason,
                    "status": "SKIPPED_AFTER_FAILURE",
                    "durationSeconds": 0.0,
                    "exitCode": None,
                }
            )
            continue
        if args.dry_run:
            command_receipts.append(
                {
                    "id": command.identifier,
                    "argv": list(command.argv),
                    "reason": command.reason,
                    "status": "PLANNED",
                    "durationSeconds": 0.0,
                    "exitCode": None,
                }
            )
            continue
        command_started = time.perf_counter()
        try:
            completed = subprocess.run(command.argv, cwd=repo, check=False)
            exit_code = int(completed.returncode)
            error_code = None
        except OSError as exc:
            exit_code = 127
            error_code = type(exc).__name__
        duration = round(time.perf_counter() - command_started, 3)
        status = "PASS" if exit_code == 0 else "FAIL"
        command_receipt: dict[str, object] = {
            "id": command.identifier,
            "argv": list(command.argv),
            "reason": command.reason,
            "status": status,
            "durationSeconds": duration,
            "exitCode": exit_code,
        }
        if error_code is not None:
            command_receipt["errorCode"] = error_code
        command_receipts.append(command_receipt)
        failed = exit_code != 0

    full_command = next(
        (item for item in command_receipts if item["id"] == "python-full-suite"),
        None,
    )
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "PLANNED" if args.dry_run else ("FAIL" if failed else "PASS"),
        "startedAt": started_wall.isoformat().replace("+00:00", "Z"),
        "finishedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "durationSeconds": round(time.perf_counter() - started, 3),
        "baseSha": base_sha,
        "headSha": head_sha,
        "requestedProfile": args.profile,
        "selectedProfile": classification.selected_profile,
        "riskLevel": classification.risk,
        "affectedProfiles": list(classification.affected_profiles),
        "classificationReasons": list(classification.reasons),
        "changedFiles": _changed_file_bindings(repo, changed_files),
        "commands": command_receipts,
        "fullRunRequired": "full-python" in classification.capabilities,
        "fullRun": bool(full_command and full_command["status"] == "PASS"),
        "contentIdentityCache": cache,
        "cacheHits": 1 if cache.get("hit") is True else 0,
    }
    _atomic_write_json(args.receipt, receipt)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
