"""Validate the release governance registry without hiding historical errors."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_report_claims import validate_claim_manifests  # noqa: E402


REGISTRY_SCHEMA = "blueprint-to-code-report-registry/v1"
VALIDATION_SCHEMA = "blueprint-to-code-report-registry-validation/v1"
ACTIVE_FORMAL = "ACTIVE_FORMAL"
HISTORICAL_PROVENANCE_INCOMPLETE = "HISTORICAL_PROVENANCE_INCOMPLETE"
DIAGNOSTIC = "DIAGNOSTIC"
STATUSES = frozenset(
    {ACTIVE_FORMAL, HISTORICAL_PROVENANCE_INCOMPLETE, DIAGNOSTIC}
)


def _issue(code: str, message: str, *, path: str = "") -> dict[str, str]:
    issue = {
        "severity": "ERROR",
        "code": code,
        "message": message,
        "registryStatus": "REGISTRY",
    }
    if path:
        issue["path"] = path
    return issue


def _load_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _relative_file(
    root: Path,
    value: object,
    *,
    label: str,
    parent: str,
    suffix: str,
) -> tuple[str, Path]:
    text = str(value or "").strip().replace("\\", "/")
    pure = PurePosixPath(text)
    if (
        not text
        or pure.is_absolute()
        or ".." in pure.parts
        or pure.as_posix() != text
        or not text.startswith(parent)
        or not text.endswith(suffix)
    ):
        raise ValueError(f"{label} must be a canonical repository-relative {suffix} path")
    lexical = root.joinpath(*pure.parts)
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} does not exist") from exc
    if not _inside(root, resolved) or lexical.is_symlink() or not resolved.is_file():
        raise ValueError(f"{label} must be a plain file inside the repository")
    return text, resolved


def _public_manifest_path(root: Path, value: object) -> str:
    try:
        path = Path(str(value)).resolve()
        return path.relative_to(root).as_posix()
    except (OSError, ValueError):
        return "UNREGISTERED_MANIFEST"


def validate_report_registry(
    repository_root: str | Path,
    registry_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return a strict release-gate result with active and historical scopes."""

    root = Path(repository_root).resolve()
    selected = Path(registry_path) if registry_path is not None else root / "reports" / "report_registry.json"
    if not selected.is_absolute():
        selected = root / selected
    selected = selected.resolve()
    issues: list[dict[str, str]] = []
    entries: list[dict[str, str]] = []
    report_paths: dict[str, str] = {}
    manifest_paths: dict[Path, tuple[str, str]] = {}

    if not _inside(root, selected):
        issues.append(
            _issue(
                "REPORT_REGISTRY_PATH_INVALID",
                "Report registry must stay inside the repository.",
            )
        )
        payload: Mapping[str, Any] = {}
    else:
        try:
            payload = _load_object(selected, "report registry")
        except Exception:
            issues.append(
                _issue(
                    "REPORT_REGISTRY_INVALID",
                    "Report registry could not be loaded as a JSON object.",
                )
            )
            payload = {}

    if set(payload) != {"schema", "releasePolicy", "reports"}:
        issues.append(
            _issue(
                "REPORT_REGISTRY_INVALID",
                "Registry must contain exactly schema, releasePolicy, and reports.",
            )
        )
    if payload.get("schema") != REGISTRY_SCHEMA:
        issues.append(
            _issue(
                "REPORT_REGISTRY_SCHEMA_INVALID",
                f"Registry schema must be {REGISTRY_SCHEMA}.",
            )
        )
    policy = payload.get("releasePolicy")
    if not isinstance(policy, Mapping) or dict(policy) != {
        "activeFormalErrorsMustEqual": 0,
        "historicalIssuesRemainVisible": True,
    }:
        issues.append(
            _issue(
                "REPORT_REGISTRY_POLICY_INVALID",
                "Release policy must require zero active errors and visible historical issues.",
            )
        )
    raw_entries = payload.get("reports")
    if not isinstance(raw_entries, list) or not raw_entries:
        issues.append(
            _issue(
                "REPORT_REGISTRY_INVALID",
                "Registry reports must be a non-empty array.",
            )
        )
        raw_entries = []

    for index, raw_entry in enumerate(raw_entries):
        label = f"reports[{index}]"
        if not isinstance(raw_entry, Mapping):
            issues.append(
                _issue("REPORT_REGISTRY_ENTRY_INVALID", f"{label} must be an object.")
            )
            continue
        status = str(raw_entry.get("status") or "").strip()
        expected_keys = (
            {"reportPath", "status", "reason"}
            if status == DIAGNOSTIC
            else {"reportPath", "status", "claimManifest", "reason"}
        )
        if set(raw_entry) != expected_keys:
            issues.append(
                _issue(
                    "REPORT_REGISTRY_ENTRY_INVALID",
                    f"{label} has unsupported or missing fields.",
                )
            )
        if status not in STATUSES:
            issues.append(
                _issue(
                    "REPORT_REGISTRY_STATUS_INVALID",
                    f"{label}.status is unsupported.",
                )
            )
        reason = str(raw_entry.get("reason") or "").strip()
        if not reason or len(reason) > 1024:
            issues.append(
                _issue(
                    "REPORT_REGISTRY_REASON_INVALID",
                    f"{label}.reason must be bounded non-empty text.",
                )
            )
        try:
            report_text, report_file = _relative_file(
                root,
                raw_entry.get("reportPath"),
                label=f"{label}.reportPath",
                parent="reports/",
                suffix=".md",
            )
            if report_file.parent != root / "reports" or report_file.name == "README.md":
                raise ValueError(f"{label}.reportPath must name one top-level report")
            if report_text in report_paths:
                raise ValueError(f"{label}.reportPath is duplicated")
            report_paths[report_text] = status
        except Exception:
            issues.append(
                _issue(
                    "REPORT_REGISTRY_REPORT_INVALID",
                    "Registry report path or report file is invalid.",
                    path="<redacted-registry-path>",
                )
            )
            continue

        public_entry = {
            "reportPath": report_text,
            "status": status,
            "reason": reason,
        }
        if status != DIAGNOSTIC:
            try:
                manifest_text, manifest_file = _relative_file(
                    root,
                    raw_entry.get("claimManifest"),
                    label=f"{label}.claimManifest",
                    parent="reports/manifests/",
                    suffix=".claims.json",
                )
                if manifest_file.parent != root / "reports" / "manifests":
                    raise ValueError(
                        f"{label}.claimManifest must name one claim manifest"
                    )
                if manifest_file in manifest_paths:
                    raise ValueError(f"{label}.claimManifest is duplicated")
                manifest_payload = _load_object(manifest_file, "claim manifest")
                if manifest_payload.get("reportPath") != report_text:
                    raise ValueError(
                        f"{label}.claimManifest does not bind the registered report"
                    )
                manifest_paths[manifest_file] = (status, report_text)
                public_entry["claimManifest"] = manifest_text
            except Exception:
                issues.append(
                    _issue(
                        "REPORT_REGISTRY_MANIFEST_INVALID",
                        "Registry claim manifest path or content is invalid.",
                        path="<redacted-registry-path>",
                    )
                )
        entries.append(public_entry)

    discovered_reports = {
        path.relative_to(root).as_posix()
        for path in (root / "reports").glob("*.md")
        if path.name != "README.md"
    }
    registered_reports = set(report_paths)
    for missing in sorted(discovered_reports - registered_reports):
        issues.append(
            _issue(
                "REPORT_REGISTRY_REPORT_UNREGISTERED",
                "Every committed top-level report must be registered.",
                path=missing,
            )
        )
    for extra in sorted(registered_reports - discovered_reports):
        issues.append(
            _issue(
                "REPORT_REGISTRY_REPORT_MISSING",
                "Registered report is not a committed top-level report.",
                path=extra,
            )
        )

    discovered_manifests = {
        path.resolve()
        for path in (root / "reports" / "manifests").glob("*.claims.json")
    }
    for missing in sorted(discovered_manifests - set(manifest_paths)):
        issues.append(
            _issue(
                "REPORT_REGISTRY_MANIFEST_UNREGISTERED",
                "Every committed claim manifest must be bound by the registry.",
                path=missing.relative_to(root).as_posix(),
            )
        )
    for extra in sorted(set(manifest_paths) - discovered_manifests):
        issues.append(
            _issue(
                "REPORT_REGISTRY_MANIFEST_MISSING",
                "Registered claim manifest is not committed.",
                path=extra.relative_to(root).as_posix(),
            )
        )

    claim_result = validate_claim_manifests(
        root,
        sorted(manifest_paths),
        formal=True,
    )
    claim_issues: list[dict[str, str]] = []
    for raw_issue in claim_result["issues"]:
        issue = {str(key): str(value) for key, value in raw_issue.items()}
        manifest_file = Path(issue.get("manifest", "")).resolve()
        status, report_path = manifest_paths.get(
            manifest_file,
            (ACTIVE_FORMAL, "UNREGISTERED_REPORT"),
        )
        issue["manifest"] = _public_manifest_path(root, manifest_file)
        issue["reportPath"] = report_path
        issue["registryStatus"] = status
        claim_issues.append(issue)
    issues.extend(claim_issues)

    def count(scope: str, severity: str) -> int:
        return sum(
            issue.get("registryStatus") == scope
            and issue.get("severity") == severity
            for issue in claim_issues
        )

    registry_errors = sum(
        issue.get("registryStatus") == "REGISTRY"
        and issue.get("severity") == "ERROR"
        for issue in issues
    )
    active_errors = count(ACTIVE_FORMAL, "ERROR")
    active_warnings = count(ACTIVE_FORMAL, "WARNING")
    historical_errors = count(HISTORICAL_PROVENANCE_INCOMPLETE, "ERROR")
    historical_warnings = count(HISTORICAL_PROVENANCE_INCOMPLETE, "WARNING")
    diagnostic_errors = count(DIAGNOSTIC, "ERROR")
    diagnostic_warnings = count(DIAGNOSTIC, "WARNING")
    total_errors = sum(issue.get("severity") == "ERROR" for issue in issues)
    total_warnings = sum(issue.get("severity") == "WARNING" for issue in issues)
    gate_passed = registry_errors == 0 and active_errors == 0

    return {
        "schema": VALIDATION_SCHEMA,
        "ok": gate_passed,
        "formal": True,
        "registry": selected.relative_to(root).as_posix()
        if _inside(root, selected)
        else "INVALID",
        "gate": {
            "policy": "ACTIVE_FORMAL errors = 0",
            "activeFormalErrors": active_errors,
            "passed": gate_passed,
        },
        "summary": {
            "reports": len(entries),
            "activeFormalReports": sum(
                entry["status"] == ACTIVE_FORMAL for entry in entries
            ),
            "historicalReports": sum(
                entry["status"] == HISTORICAL_PROVENANCE_INCOMPLETE
                for entry in entries
            ),
            "diagnosticReports": sum(
                entry["status"] == DIAGNOSTIC for entry in entries
            ),
            "manifests": int(claim_result["summary"]["manifests"]),
            "claims": int(claim_result["summary"]["claims"]),
            "registryErrors": registry_errors,
            "activeErrors": active_errors,
            "activeWarnings": active_warnings,
            "historicalErrors": historical_errors,
            "historicalWarnings": historical_warnings,
            "diagnosticErrors": diagnostic_errors,
            "diagnosticWarnings": diagnostic_warnings,
            "errors": total_errors,
            "warnings": total_warnings,
        },
        "reports": entries,
        "issues": issues,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the governed formal report release registry."
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    result = validate_report_registry(args.root, args.registry)
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
            separators=None if args.pretty else (",", ":"),
        )
    )
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
