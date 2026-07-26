"""Validate report claim manifests against committed source fingerprints.

The validator deliberately accepts a verified sanitized native manifest when
the proprietary full evidence is absent.  It reports that absence as
LOCAL_EVIDENCE_REQUIRED, while fingerprint drift and unverified provenance
fail closed (the latter is an error in formal mode).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


CLAIM_SCHEMA = "blueprint-to-code-report-claims/v1"
SANITIZED_NATIVE_SCHEMA = "blueprint-to-code-sanitized-native-evidence/v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_REF_RE = re.compile(r"^(?:bp|native|runtime)://[^\s]+$")


def _load_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_relative(root: Path, value: object, label: str) -> Path:
    text = str(value or "").strip().replace("\\", "/")
    if not text or Path(text).is_absolute():
        raise ValueError(f"{label} must be a repository-relative path")
    resolved = (root / text).resolve()
    if not _inside(root, resolved):
        raise ValueError(f"{label} escapes the repository")
    return resolved


def _issue(
    severity: str,
    code: str,
    message: str,
    *,
    manifest: Path | None = None,
    claim_id: str | None = None,
) -> dict[str, str]:
    result = {"severity": severity, "code": code, "message": message}
    if manifest is not None:
        result["manifest"] = str(manifest)
    if claim_id:
        result["claimId"] = claim_id
    return result


def _sha(value: object) -> str:
    return str(value or "").strip().casefold()


def _dependency_value(payload: Mapping[str, Any], *keys: str) -> object:
    current: object = payload
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def validate_claim_manifests(
    repository_root: str | Path,
    manifest_paths: Iterable[str | Path],
    *,
    formal: bool = False,
) -> dict[str, Any]:
    """Validate selected manifests and return a stable machine-readable result."""

    root = Path(repository_root).resolve()
    paths = [Path(path).resolve() for path in manifest_paths]
    issues: list[dict[str, str]] = []
    claims_count = 0
    seen_claim_ids: dict[str, Path] = {}
    native_cache: dict[Path, dict[str, Any]] = {}

    for manifest_path in paths:
        try:
            if not _inside(root, manifest_path):
                raise ValueError("claim manifest is outside the repository")
            manifest = _load_object(manifest_path, "claim manifest")
        except Exception as exc:
            issues.append(
                _issue(
                    "ERROR",
                    "CLAIM_MANIFEST_INVALID",
                    str(exc),
                    manifest=manifest_path,
                )
            )
            continue
        if manifest.get("schema") != CLAIM_SCHEMA:
            issues.append(
                _issue(
                    "ERROR",
                    "CLAIM_SCHEMA_INVALID",
                    f"schema must be {CLAIM_SCHEMA}",
                    manifest=manifest_path,
                )
            )
            continue

        try:
            report_path = _resolve_relative(
                root,
                manifest.get("reportPath"),
                "reportPath",
            )
            report_text = report_path.read_text(encoding="utf-8-sig")
        except Exception as exc:
            issues.append(
                _issue(
                    "ERROR",
                    "REPORT_NOT_FOUND",
                    str(exc),
                    manifest=manifest_path,
                )
            )
            report_text = ""

        dependencies = manifest.get("dependencies")
        if not isinstance(dependencies, Mapping):
            issues.append(
                _issue(
                    "ERROR",
                    "CLAIM_DEPENDENCIES_INVALID",
                    "dependencies must be an object",
                    manifest=manifest_path,
                )
            )
            dependencies = {}
        native_dependencies = dependencies.get("nativeEvidenceSets", [])
        if not isinstance(native_dependencies, list):
            issues.append(
                _issue(
                    "ERROR",
                    "CLAIM_DEPENDENCIES_INVALID",
                    "dependencies.nativeEvidenceSets must be an array",
                    manifest=manifest_path,
                )
            )
            native_dependencies = []

        available_native_ids: set[str] = set()
        evidence_set_ids: set[str] = set()
        dependency_by_id: dict[str, Mapping[str, Any]] = {}
        for index, dependency in enumerate(native_dependencies):
            if not isinstance(dependency, Mapping):
                issues.append(
                    _issue(
                        "ERROR",
                        "NATIVE_DEPENDENCY_INVALID",
                        f"nativeEvidenceSets[{index}] must be an object",
                        manifest=manifest_path,
                    )
                )
                continue
            try:
                native_path = _resolve_relative(
                    root,
                    dependency.get("manifestPath"),
                    f"nativeEvidenceSets[{index}].manifestPath",
                )
                native_manifest = native_cache.setdefault(
                    native_path,
                    _load_object(native_path, "sanitized native manifest"),
                )
            except Exception as exc:
                issues.append(
                    _issue(
                        "ERROR",
                        "NATIVE_MANIFEST_INVALID",
                        str(exc),
                        manifest=manifest_path,
                    )
                )
                continue
            if native_manifest.get("schema") != SANITIZED_NATIVE_SCHEMA:
                issues.append(
                    _issue(
                        "ERROR",
                        "NATIVE_MANIFEST_SCHEMA_INVALID",
                        f"{native_path} has an unsupported schema",
                        manifest=manifest_path,
                    )
                )
                continue
            evidence_set_id = str(native_manifest.get("evidenceSetId") or "")
            evidence_set_ids.add(evidence_set_id)
            dependency_by_id[evidence_set_id] = dependency
            if str(dependency.get("evidenceSetId") or "") != evidence_set_id:
                issues.append(
                    _issue(
                        "ERROR",
                        "STALE_NATIVE_BUILD",
                        "native evidence set ID differs from the dependency",
                        manifest=manifest_path,
                    )
                )

            comparisons = (
                (
                    "binarySha256",
                    _dependency_value(native_manifest, "provenance", "binary", "sha256"),
                    "STALE_NATIVE_BUILD",
                ),
                (
                    "pdbSha256",
                    _dependency_value(native_manifest, "provenance", "pdb", "sha256"),
                    "STALE_NATIVE_BUILD",
                ),
                (
                    "recipeId",
                    _dependency_value(
                        native_manifest,
                        "provenance",
                        "generator",
                        "recipeId",
                    ),
                    "STALE_RECIPE",
                ),
                (
                    "recipeSha256",
                    _dependency_value(
                        native_manifest,
                        "provenance",
                        "generator",
                        "recipeSha256",
                    ),
                    "STALE_RECIPE",
                ),
            )
            for key, actual, code in comparisons:
                expected = dependency.get(key)
                if _sha(expected) != _sha(actual):
                    issues.append(
                        _issue(
                            "ERROR",
                            code,
                            f"{key} differs from {native_path.name}",
                            manifest=manifest_path,
                        )
                    )
            expected_scripts = dependency.get("generatorScriptSha256") or {}
            actual_scripts = (
                _dependency_value(
                    native_manifest,
                    "provenance",
                    "generator",
                    "scriptSha256",
                )
                or {}
            )
            if not isinstance(expected_scripts, Mapping) or not isinstance(
                actual_scripts, Mapping
            ):
                issues.append(
                    _issue(
                        "ERROR",
                        "STALE_GENERATOR",
                        "generator script fingerprints must be objects",
                        manifest=manifest_path,
                    )
                )
            else:
                for name, expected in expected_scripts.items():
                    if _sha(expected) != _sha(actual_scripts.get(name)):
                        issues.append(
                            _issue(
                                "ERROR",
                                "STALE_GENERATOR",
                                f"generator script {name!r} changed",
                                manifest=manifest_path,
                            )
                        )

            trust = native_manifest.get("trust")
            trust_status = (
                str(trust.get("status") or "")
                if isinstance(trust, Mapping)
                else ""
            )
            if trust_status != "VERIFIED":
                issues.append(
                    _issue(
                        "ERROR" if formal else "WARNING",
                        "PROVENANCE_UNVERIFIED",
                        f"{native_path.name} trust status is {trust_status or 'missing'}",
                        manifest=manifest_path,
                    )
                )
            if bool(
                _dependency_value(
                    native_manifest,
                    "provenance",
                    "generator",
                    "repositoryDirty",
                )
            ):
                issues.append(
                    _issue(
                        "ERROR" if formal else "WARNING",
                        "STALE_GENERATOR",
                        f"{native_path.name} was generated by a dirty repository",
                        manifest=manifest_path,
                    )
                )

            local_path_value = native_manifest.get("localEvidenceRelativePath")
            if local_path_value:
                try:
                    local_path = _resolve_relative(
                        root,
                        local_path_value,
                        "localEvidenceRelativePath",
                    )
                    if not local_path.exists():
                        issues.append(
                            _issue(
                                "WARNING",
                                "LOCAL_EVIDENCE_REQUIRED",
                                (
                                    f"Full local evidence is absent; rebuild "
                                    f"{evidence_set_id} from its recipe when needed."
                                ),
                                manifest=manifest_path,
                            )
                        )
                except Exception as exc:
                    issues.append(
                        _issue(
                            "ERROR",
                            "NATIVE_MANIFEST_INVALID",
                            str(exc),
                            manifest=manifest_path,
                        )
                    )

            targets = native_manifest.get("targets")
            if not isinstance(targets, list):
                issues.append(
                    _issue(
                        "ERROR",
                        "NATIVE_MANIFEST_INVALID",
                        "targets must be an array",
                        manifest=manifest_path,
                    )
                )
            else:
                for target in targets:
                    if isinstance(target, Mapping):
                        evidence_id = str(target.get("evidenceId") or "")
                        if evidence_id:
                            available_native_ids.add(evidence_id)

        claims = manifest.get("claims")
        if not isinstance(claims, list):
            issues.append(
                _issue(
                    "ERROR",
                    "CLAIMS_INVALID",
                    "claims must be an array",
                    manifest=manifest_path,
                )
            )
            continue
        for index, claim in enumerate(claims):
            claims_count += 1
            if not isinstance(claim, Mapping):
                issues.append(
                    _issue(
                        "ERROR",
                        "CLAIM_INVALID",
                        f"claims[{index}] must be an object",
                        manifest=manifest_path,
                    )
                )
                continue
            claim_id = str(claim.get("claimId") or "").strip()
            if not claim_id.startswith("claim://"):
                issues.append(
                    _issue(
                        "ERROR",
                        "CLAIM_ID_INVALID",
                        "claimId must use claim://",
                        manifest=manifest_path,
                    )
                )
            elif claim_id in seen_claim_ids:
                issues.append(
                    _issue(
                        "ERROR",
                        "DUPLICATE_CLAIM_ID",
                        f"{claim_id} already appears in {seen_claim_ids[claim_id]}",
                        manifest=manifest_path,
                        claim_id=claim_id,
                    )
                )
            else:
                seen_claim_ids[claim_id] = manifest_path

            for field in (
                "summary",
                "status",
                "confidence",
                "assumptions",
                "sourceFingerprints",
                "invalidationConditions",
                "runtimeValidation",
            ):
                if field not in claim:
                    issues.append(
                        _issue(
                            "ERROR",
                            "CLAIM_FIELD_MISSING",
                            f"{claim_id or index} is missing {field}",
                            manifest=manifest_path,
                            claim_id=claim_id,
                        )
                    )
            refs = claim.get("evidenceRefs")
            if not isinstance(refs, list) or not refs:
                issues.append(
                    _issue(
                        "ERROR",
                        "CLAIM_EVIDENCE_MISSING",
                        f"{claim_id or index} must have evidenceRefs",
                        manifest=manifest_path,
                        claim_id=claim_id,
                    )
                )
            else:
                for ref in refs:
                    text = str(ref or "")
                    if not EVIDENCE_REF_RE.fullmatch(text):
                        issues.append(
                            _issue(
                                "ERROR",
                                "EVIDENCE_REF_INVALID",
                                f"invalid evidence ref: {text}",
                                manifest=manifest_path,
                                claim_id=claim_id,
                            )
                        )
                    elif text.startswith("native://") and text not in available_native_ids:
                        issues.append(
                            _issue(
                                "ERROR",
                                "EVIDENCE_REF_NOT_FOUND",
                                f"native evidence ref is not in a dependency: {text}",
                                manifest=manifest_path,
                                claim_id=claim_id,
                            )
                        )

            fingerprints = claim.get("sourceFingerprints")
            if isinstance(fingerprints, Mapping):
                native_set = str(
                    fingerprints.get("nativeEvidenceSetId") or ""
                )
                if native_set and native_set not in evidence_set_ids:
                    issues.append(
                        _issue(
                            "ERROR",
                            "STALE_NATIVE_BUILD",
                            f"{claim_id} references an unknown evidence set",
                            manifest=manifest_path,
                            claim_id=claim_id,
                        )
                    )
                dependency = dependency_by_id.get(native_set)
                if dependency is not None:
                    if (
                        fingerprints.get("binarySha256")
                        and _sha(fingerprints.get("binarySha256"))
                        != _sha(dependency.get("binarySha256"))
                    ):
                        issues.append(
                            _issue(
                                "ERROR",
                                "STALE_NATIVE_BUILD",
                                f"{claim_id} binary fingerprint is stale",
                                manifest=manifest_path,
                                claim_id=claim_id,
                            )
                        )
                    if (
                        fingerprints.get("recipeSha256")
                        and _sha(fingerprints.get("recipeSha256"))
                        != _sha(dependency.get("recipeSha256"))
                    ):
                        issues.append(
                            _issue(
                                "ERROR",
                                "STALE_RECIPE",
                                f"{claim_id} recipe fingerprint is stale",
                                manifest=manifest_path,
                                claim_id=claim_id,
                            )
                        )

            markers = claim.get("reportMarkers")
            if not isinstance(markers, list) or not markers:
                issues.append(
                    _issue(
                        "ERROR",
                        "REPORT_CLAIM_MARKER_MISSING",
                        f"{claim_id or index} needs at least one report marker",
                        manifest=manifest_path,
                        claim_id=claim_id,
                    )
                )
            else:
                for marker in markers:
                    marker_text = str(marker or "")
                    if not marker_text or marker_text not in report_text:
                        issues.append(
                            _issue(
                                "ERROR",
                                "REPORT_CLAIM_MARKER_MISSING",
                                f"report does not contain marker: {marker_text!r}",
                                manifest=manifest_path,
                                claim_id=claim_id,
                            )
                        )

    errors = sum(issue["severity"] == "ERROR" for issue in issues)
    warnings = sum(issue["severity"] == "WARNING" for issue in issues)
    return {
        "schema": "blueprint-to-code-report-claim-validation/v1",
        "ok": errors == 0,
        "formal": bool(formal),
        "summary": {
            "manifests": len(paths),
            "claims": claims_count,
            "errors": errors,
            "warnings": warnings,
        },
        "issues": issues,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate report claim manifests.")
    parser.add_argument("manifests", nargs="*", type=Path)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--formal", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    root = args.root.resolve()
    manifests = list(args.manifests)
    if args.all:
        manifests.extend(sorted((root / "reports" / "manifests").glob("*.json")))
    manifests = list(dict.fromkeys(path.resolve() for path in manifests))
    if not manifests:
        print("No claim manifests selected.", file=sys.stderr)
        return 2
    result = validate_claim_manifests(root, manifests, formal=args.formal)
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
