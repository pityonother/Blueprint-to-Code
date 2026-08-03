"""Declarative native-analysis recipe and recipe-export contracts.

This module is intentionally dependency-free.  Ghidra performs collection, but
Python independently validates every selector result before it can become a
Native Evidence v2 artifact.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path, PurePath
from typing import Any, Mapping

from .native_identity import (
    NativeIdentityError,
    create_native_evidence_manifest,
    validate_native_evidence_manifest,
)


RECIPE_SCHEMA = "blueprint-to-code-native-analysis-recipe/v1"
RECIPE_DOCUMENT_SCHEMA = (
    "blueprint-to-code-native-analysis-recipe-document/v1"
)
RECIPE_EXPORT_SCHEMA = "blueprint-to-code-native-recipe-export/v1"
IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
RVA_PATTERN = re.compile(r"^0x[0-9a-fA-F]+$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

RECIPE_KEYS = {
    "schema",
    "recipeId",
    "description",
    "binaryModule",
    "requirements",
    "targets",
    "fieldQueries",
    "vtableQueries",
    "budgets",
}
TARGET_KEYS = {"id", "selector", "expectedMatches", "exports"}
SELECTOR_KEYS = {
    "qualifiedName",
    "signature",
    "rva",
    "simpleName",
    "allowSimpleName",
    "regex",
}
EXPORT_KEYS = {
    "decompile",
    "callersDepth",
    "calleesDepth",
    "constants",
    "fieldAccesses",
    "branches",
    "vtable",
}
BUDGET_KEYS = {
    "maxFunctions",
    "maxCallEdges",
    "maxFieldAccesses",
    "maxConstants",
    "maxVtableMatches",
    "maxDecompiledCharactersPerFunction",
    "maxTotalDecompiledCharacters",
}


def _fail(code: str, message: str, **details: Any) -> None:
    raise NativeIdentityError(code, message, details=details)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("NATIVE_RECIPE_SCHEMA_INVALID", f"{label} must be an object.")
    return value


def _objects(value: Any, label: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        _fail("NATIVE_RECIPE_SCHEMA_INVALID", f"{label} must be an array.")
    rows: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        rows.append(_mapping(item, f"{label}[{index}]"))
    return rows


def _only_keys(
    value: Mapping[str, Any],
    allowed: set[str],
    label: str,
) -> None:
    unexpected = sorted(str(key) for key in value if key not in allowed)
    if unexpected:
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            f"{label} contains unsupported fields: {', '.join(unexpected)}.",
        )


def _required_text(value: Mapping[str, Any], key: str, label: str) -> str:
    text = str(value.get(key) or "").strip()
    if not text:
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            f"{label}.{key} is required.",
        )
    return text


def _expected_matches(value: Mapping[str, Any], label: str) -> int:
    count = value.get("expectedMatches")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            f"{label}.expectedMatches must be a positive integer.",
        )
    return count


def _canonical_qualified_name(value: Any) -> str:
    text = str(value or "").replace("\\", "/").replace("::", "/")
    return re.sub(r"/+", "/", text).lstrip("/")


def _normalize_signature(value: Any) -> str:
    canonical = _canonical_qualified_name(value)
    return re.sub(r"\s+", "", canonical).replace("__cdecl", "")


def _candidate_matches_selector(
    candidate: Mapping[str, Any],
    selector: Mapping[str, Any],
    *,
    formal: bool,
) -> bool:
    if "rva" in selector:
        try:
            return int(str(candidate.get("rva") or ""), 16) == int(
                str(selector["rva"]),
                16,
            )
        except ValueError:
            return False
    if "qualifiedName" in selector:
        if _canonical_qualified_name(candidate.get("qualifiedName")) != (
            _canonical_qualified_name(selector["qualifiedName"])
        ):
            return False
        if "signature" in selector:
            actual_signature = candidate.get(
                "canonicalSignature",
                candidate.get("signature"),
            )
            return _normalize_signature(actual_signature) == (
                _normalize_signature(selector["signature"])
            )
        return True
    if "simpleName" in selector:
        return (
            selector.get("allowSimpleName") is True
            and str(candidate.get("name") or "") == str(selector["simpleName"])
        )
    if "regex" in selector:
        if formal:
            return False
        return re.search(
            str(selector["regex"]),
            str(candidate.get("qualifiedName") or ""),
        ) is not None
    return False


def _validate_selector(
    value: Any,
    *,
    label: str,
    formal: bool,
) -> Mapping[str, Any]:
    selector = _mapping(value, label)
    _only_keys(selector, SELECTOR_KEYS, label)
    qualified = str(selector.get("qualifiedName") or "").strip()
    signature = str(selector.get("signature") or "").strip()
    rva = str(selector.get("rva") or "").strip()
    simple = str(selector.get("simpleName") or "").strip()
    regex = str(selector.get("regex") or "").strip()
    modes = sum(bool(item) for item in (qualified, rva, simple, regex))
    if modes != 1:
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            f"{label} must select exactly one of qualifiedName, rva, "
            "simpleName, or regex.",
        )
    if signature and not qualified:
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            f"{label}.signature requires qualifiedName.",
        )
    if rva and not RVA_PATTERN.fullmatch(rva):
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            f"{label}.rva must be canonical hexadecimal such as 0x1000.",
        )
    if simple and selector.get("allowSimpleName") is not True:
        _fail(
            "NATIVE_RECIPE_SELECTOR_FORBIDDEN",
            f"{label}.simpleName requires allowSimpleName=true.",
        )
    if regex:
        if formal:
            _fail(
                "NATIVE_RECIPE_SELECTOR_FORBIDDEN",
                f"{label}.regex is discovery-only and forbidden in formal mode.",
            )
        try:
            re.compile(regex)
        except re.error as exc:
            _fail(
                "NATIVE_RECIPE_SCHEMA_INVALID",
                f"{label}.regex is invalid: {exc}.",
            )
    return selector


def _validate_target(
    value: Mapping[str, Any],
    *,
    index: int,
    formal: bool,
) -> str:
    label = f"targets[{index}]"
    _only_keys(value, TARGET_KEYS, label)
    target_id = _required_text(value, "id", label)
    if not IDENTIFIER_PATTERN.fullmatch(target_id):
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            f"{label}.id must use lowercase letters, digits, dots, "
            "underscores, or hyphens.",
        )
    _expected_matches(value, label)
    _validate_selector(value.get("selector"), label=f"{label}.selector", formal=formal)
    exports = _mapping(value.get("exports"), f"{label}.exports")
    _only_keys(exports, EXPORT_KEYS, f"{label}.exports")
    for key in (
        "decompile",
        "constants",
        "fieldAccesses",
        "branches",
        "vtable",
    ):
        if key in exports and not isinstance(exports[key], bool):
            _fail(
                "NATIVE_RECIPE_SCHEMA_INVALID",
                f"{label}.exports.{key} must be boolean.",
            )
    for key in ("callersDepth", "calleesDepth"):
        depth = exports.get(key, 0)
        if not isinstance(depth, int) or isinstance(depth, bool) or not 0 <= depth <= 5:
            _fail(
                "NATIVE_RECIPE_SCHEMA_INVALID",
                f"{label}.exports.{key} must be an integer from 0 to 5.",
            )
    return target_id


def _validate_query(
    value: Mapping[str, Any],
    *,
    label: str,
    kind: str,
) -> str:
    if kind == "field":
        allowed = {
            "id",
            "structureName",
            "fieldName",
            "functionTargetIds",
            "expectedMatches",
        }
        required = ("structureName", "fieldName")
    else:
        allowed = {
            "id",
            "className",
            "slotOffset",
            "expectedMatches",
        }
        required = ("className", "slotOffset")
    _only_keys(value, allowed, label)
    query_id = _required_text(value, "id", label)
    if not IDENTIFIER_PATTERN.fullmatch(query_id):
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            f"{label}.id has unsupported characters.",
        )
    _expected_matches(value, label)
    for key in required:
        _required_text(value, key, label)
    if kind == "field" and "functionTargetIds" in value:
        target_ids = value["functionTargetIds"]
        if (
            not isinstance(target_ids, list)
            or not target_ids
            or any(not isinstance(item, str) or not item for item in target_ids)
        ):
            _fail(
                "NATIVE_RECIPE_SCHEMA_INVALID",
                f"{label}.functionTargetIds must be a non-empty string array.",
            )
    if kind == "vtable" and not RVA_PATTERN.fullmatch(
        str(value.get("slotOffset") or "")
    ):
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            f"{label}.slotOffset must be canonical hexadecimal.",
        )
    return query_id


def validate_native_recipe(
    recipe: Mapping[str, Any],
    *,
    formal: bool,
) -> Mapping[str, Any]:
    """Validate a recipe without accessing a native binary."""

    root = _mapping(recipe, "recipe")
    _only_keys(root, RECIPE_KEYS, "recipe")
    if root.get("schema") != RECIPE_SCHEMA:
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            f"recipe.schema must be {RECIPE_SCHEMA}.",
        )
    recipe_id = _required_text(root, "recipeId", "recipe")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*/v[1-9][0-9]*", recipe_id):
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            "recipe.recipeId must be a stable lower-case name ending in /vN.",
        )
    _required_text(root, "description", "recipe")
    binary_module = _required_text(root, "binaryModule", "recipe")
    if PurePath(binary_module).name != binary_module or "/" in binary_module:
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            "recipe.binaryModule must be a file name without a local path.",
        )

    requirements = _mapping(root.get("requirements"), "recipe.requirements")
    _only_keys(
        requirements,
        {"pdbRequired", "formalProvenanceRequired"},
        "recipe.requirements",
    )
    for key in ("pdbRequired", "formalProvenanceRequired"):
        if not isinstance(requirements.get(key), bool):
            _fail(
                "NATIVE_RECIPE_SCHEMA_INVALID",
                f"recipe.requirements.{key} must be boolean.",
            )

    targets = _objects(root.get("targets"), "recipe.targets")
    if not targets:
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            "recipe.targets must contain at least one target.",
        )
    target_ids: set[str] = set()
    for index, target in enumerate(targets):
        target_id = _validate_target(target, index=index, formal=formal)
        if target_id in target_ids:
            _fail(
                "NATIVE_RECIPE_SCHEMA_INVALID",
                f"Duplicate recipe target id: {target_id}.",
            )
        target_ids.add(target_id)

    query_ids: set[str] = set()
    query_rows_by_kind: dict[str, list[Mapping[str, Any]]] = {}
    for key, kind in (("fieldQueries", "field"), ("vtableQueries", "vtable")):
        rows = _objects(root.get(key), f"recipe.{key}")
        query_rows_by_kind[kind] = rows
        for index, row in enumerate(rows):
            query_id = _validate_query(
                row,
                label=f"{key}[{index}]",
                kind=kind,
            )
            if query_id in query_ids or query_id in target_ids:
                _fail(
                    "NATIVE_RECIPE_SCHEMA_INVALID",
                    f"Duplicate recipe query id: {query_id}.",
                )
            query_ids.add(query_id)
            if kind == "field":
                unknown = sorted(
                    set(row.get("functionTargetIds") or []) - target_ids
                )
                if unknown:
                    _fail(
                        "NATIVE_RECIPE_SCHEMA_INVALID",
                        f"{key}[{index}] references unknown targets: "
                        f"{', '.join(unknown)}.",
                    )

    field_requested = {
        str(target["id"])
        for target in targets
        if _mapping(target.get("exports"), "target exports").get(
            "fieldAccesses"
        )
        is True
    }
    field_queries = query_rows_by_kind["field"]
    if field_requested:
        if not field_queries:
            _fail(
                "NATIVE_RECIPE_SCHEMA_INVALID",
                "fieldAccesses=true requires at least one field query.",
            )
        applies_to_all = any(
            "functionTargetIds" not in query for query in field_queries
        )
        covered_targets = {
            str(target_id)
            for query in field_queries
            for target_id in query.get("functionTargetIds") or []
        }
        uncovered = sorted(
            set() if applies_to_all else field_requested - covered_targets
        )
        if uncovered:
            _fail(
                "NATIVE_RECIPE_SCHEMA_INVALID",
                "fieldAccesses=true targets are not covered by a field query: "
                f"{', '.join(uncovered)}.",
            )

    if any(
        _mapping(target.get("exports"), "target exports").get("vtable")
        is True
        for target in targets
    ) and not query_rows_by_kind["vtable"]:
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            "vtable=true requires at least one vtable query.",
        )

    budgets = _mapping(root.get("budgets"), "recipe.budgets")
    _only_keys(budgets, BUDGET_KEYS, "recipe.budgets")
    expected_target_matches = sum(
        int(target["expectedMatches"]) for target in targets
    )
    expected_field_matches = sum(
        int(query["expectedMatches"])
        for query in query_rows_by_kind["field"]
    )
    expected_vtable_matches = sum(
        int(query["expectedMatches"])
        for query in query_rows_by_kind["vtable"]
    )
    minimums = {
        "maxFunctions": expected_target_matches + expected_vtable_matches,
        "maxCallEdges": 0,
        "maxFieldAccesses": expected_field_matches,
        "maxConstants": 0,
        "maxVtableMatches": expected_vtable_matches,
        "maxDecompiledCharactersPerFunction": 1,
        "maxTotalDecompiledCharacters": 1,
    }
    maximums = {
        "maxFunctions": 5000,
        "maxCallEdges": 100000,
        "maxFieldAccesses": 100000,
        "maxConstants": 100000,
        "maxVtableMatches": 10000,
        "maxDecompiledCharactersPerFunction": 500000,
        "maxTotalDecompiledCharacters": 5000000,
    }
    for key in BUDGET_KEYS:
        number = budgets.get(key)
        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or number < minimums[key]
            or number > maximums[key]
        ):
            _fail(
                "NATIVE_RECIPE_SCHEMA_INVALID",
                f"recipe.budgets.{key} is outside the supported range.",
            )
    if (
        budgets["maxTotalDecompiledCharacters"]
        < budgets["maxDecompiledCharactersPerFunction"]
    ):
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            "Total decompile budget cannot be smaller than the per-function budget.",
        )
    return recipe


def requires_registered_binary_hashes(
    recipe: Mapping[str, Any],
    *,
    registered_module: str,
) -> bool:
    """Return whether a recipe targets the toolchain's registered ARK module.

    Public or synthetic modules still require full PE/PDB identity validation,
    but their freshly built hashes cannot be pre-registered as ARK artifacts.
    """

    module = str(recipe.get("binaryModule") or "").strip()
    registered_name = PurePath(
        str(registered_module).replace("\\", "/")
    ).name
    if not module or not registered_name:
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            "Both recipe and registered binary module names are required.",
        )
    return module.casefold() == registered_name.casefold()


def load_native_recipe(
    path: str | Path,
    *,
    formal: bool,
) -> dict[str, Any]:
    """Read, hash, and validate a recipe while keeping its local path private."""

    recipe_path = Path(path)
    try:
        source = recipe_path.read_bytes()
        payload = json.loads(source.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            f"Could not read native recipe JSON: {exc}.",
        )
    validate_native_recipe(payload, formal=formal)
    return {
        "schema": RECIPE_DOCUMENT_SCHEMA,
        "sha256": hashlib.sha256(source).hexdigest(),
        "recipe": payload,
    }


def _validated_recipe_document(
    document: Mapping[str, Any],
    *,
    formal: bool,
) -> tuple[Mapping[str, Any], str]:
    root = _mapping(document, "recipe document")
    if root.get("schema") != RECIPE_DOCUMENT_SCHEMA:
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            "Recipe document schema is invalid.",
        )
    recipe = _mapping(root.get("recipe"), "recipe document recipe")
    validate_native_recipe(recipe, formal=formal)
    recipe_sha = str(root.get("sha256") or "").lower()
    if not SHA256_PATTERN.fullmatch(recipe_sha):
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            "Recipe document SHA-256 is invalid.",
        )
    return recipe, recipe_sha


def _validate_candidates(
    result: Mapping[str, Any],
    *,
    label: str,
    expected: int,
    selector: Mapping[str, Any] | None = None,
    formal: bool,
) -> list[str]:
    match_count = result.get("matchCount")
    resolved = result.get("resolvedEvidenceIds")
    candidates = result.get("candidates")
    if (
        not isinstance(match_count, int)
        or not isinstance(resolved, list)
        or any(not isinstance(item, str) or not item for item in resolved)
        or not isinstance(candidates, list)
    ):
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            f"{label} result shape is invalid.",
        )
    accepted_count = 0
    accepted_ids: list[str] = []
    for index, candidate_value in enumerate(candidates):
        candidate = _mapping(candidate_value, f"{label}.candidates[{index}]")
        accepted = candidate.get("accepted")
        rejection_reason = candidate.get("rejectionReason")
        if not isinstance(accepted, bool) or not isinstance(
            rejection_reason, str
        ):
            _fail(
                "NATIVE_RECIPE_SCHEMA_INVALID",
                f"{label}.candidates[{index}] must preserve acceptance and "
                "rejectionReason.",
            )
        if accepted:
            if rejection_reason:
                _fail(
                    "NATIVE_RECIPE_SCHEMA_INVALID",
                    f"{label}.candidates[{index}] was accepted with a "
                    "rejectionReason.",
                )
            accepted_count += 1
            evidence_id = str(candidate.get("evidenceId") or "")
            if not evidence_id:
                _fail(
                    "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
                    f"{label}.candidates[{index}] was accepted without an "
                    "evidenceId.",
                )
            accepted_ids.append(evidence_id)
        elif not rejection_reason.strip():
            _fail(
                "NATIVE_RECIPE_SCHEMA_INVALID",
                f"{label}.candidates[{index}] rejected without a reason.",
            )
        if selector is not None:
            independently_matched = _candidate_matches_selector(
                candidate,
                selector,
                formal=formal,
            )
            if independently_matched is not accepted:
                _fail(
                    "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
                    f"{label}.candidates[{index}] acceptance disagrees with "
                    "independent selector validation.",
                )
    if (
        match_count != expected
        or len(resolved) != expected
        or accepted_count != expected
    ):
        _fail(
            "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
            f"{label} expected {expected} matches but resolved "
            f"{match_count} ({accepted_count} accepted candidates).",
            target=label,
            expectedMatches=expected,
            matchCount=match_count,
            acceptedCandidates=accepted_count,
        )
    if len(set(resolved)) != len(resolved):
        _fail(
            "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
            f"{label} resolved duplicate evidence IDs.",
        )
    if (
        len(set(accepted_ids)) != len(accepted_ids)
        or set(accepted_ids) != set(resolved)
    ):
        _fail(
            "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
            f"{label} accepted candidates do not match resolvedEvidenceIds.",
        )
    return list(resolved)


def _gap(value: Mapping[str, Any], ordinal: int) -> dict[str, Any]:
    reason = str(
        value.get("reasonCode") or value.get("kind") or "SOURCE_NOT_AVAILABLE"
    )
    return {
        "gapId": f"native-gap://recipe/{ordinal:04d}",
        "functionEvidenceId": value.get("functionEvidenceId")
        or value.get("evidenceId"),
        "status": str(value.get("status") or "NOT_RECOVERED"),
        "reasonCode": reason,
        "detail": str(value.get("detail") or value.get("reason") or reason),
        "nextProbe": str(value.get("nextProbe") or ""),
    }


def create_native_recipe_evidence_manifest(
    raw_export: Mapping[str, Any],
    *,
    recipe_document: Mapping[str, Any],
    identity: Mapping[str, Any],
    ghidra: Mapping[str, Any],
    java: Mapping[str, Any],
    generator: Mapping[str, Any],
    formal: bool,
) -> dict[str, Any]:
    """Verify one Ghidra recipe export and wrap it as Native Evidence v2."""

    recipe, recipe_sha = _validated_recipe_document(
        recipe_document,
        formal=formal,
    )
    raw = _mapping(raw_export, "recipe export")
    if raw.get("schema") != RECIPE_EXPORT_SCHEMA:
        _fail(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            "Ghidra recipe export schema is invalid.",
        )
    binary = _mapping(identity.get("binary"), "native identity binary")
    _mapping(identity.get("pdb"), "native identity PDB")
    if (
        str(raw.get("program") or "").casefold()
        != str(binary.get("module") or "").casefold()
        or str(raw.get("binarySha256") or "").lower()
        != str(binary.get("sha256") or "").lower()
        or str(recipe.get("binaryModule") or "").casefold()
        != str(binary.get("module") or "").casefold()
    ):
        _fail(
            "NATIVE_PROJECT_PROGRAM_HASH_MISMATCH",
            "Recipe, Ghidra program, and current binary identity differ.",
        )
    if (
        raw.get("recipeId") != recipe.get("recipeId")
        or str(raw.get("recipeSha256") or "").lower() != recipe_sha
    ):
        _fail(
            "NATIVE_EVIDENCE_PROVENANCE_MISMATCH",
            "Ghidra recipe identity differs from the current recipe file.",
        )

    generator_input = dict(_mapping(generator, "generator provenance"))
    if (
        generator_input.get("recipeId") != recipe.get("recipeId")
        or str(generator_input.get("recipeSha256") or "").lower() != recipe_sha
    ):
        _fail(
            "NATIVE_EVIDENCE_PROVENANCE_MISMATCH",
            "Generator provenance is bound to another recipe.",
        )

    results = _objects(raw.get("targetResults"), "recipe export targetResults")
    results_by_id: dict[str, Mapping[str, Any]] = {}
    for result in results:
        target_id = _required_text(result, "targetId", "target result")
        if target_id in results_by_id:
            _fail(
                "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
                f"Duplicate target result: {target_id}.",
            )
        results_by_id[target_id] = result

    recipe_targets: list[dict[str, Any]] = []
    resolved_ids: set[str] = set()
    resolved_ids_by_target: dict[str, set[str]] = {}
    for target in _objects(recipe.get("targets"), "recipe targets"):
        target_id = str(target["id"])
        result = results_by_id.get(target_id)
        if result is None:
            _fail(
                "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
                f"Missing target result: {target_id}.",
            )
        if (
            result.get("selector") != target.get("selector")
            or result.get("expectedMatches") != target.get("expectedMatches")
            or result.get("exports") != target.get("exports")
        ):
            _fail(
                "NATIVE_EVIDENCE_PROVENANCE_MISMATCH",
                f"Target result {target_id} does not echo the recipe contract.",
            )
        resolved = _validate_candidates(
            result,
            label=target_id,
            expected=int(target["expectedMatches"]),
            selector=_mapping(target["selector"], f"{target_id}.selector"),
            formal=formal,
        )
        resolved_ids.update(resolved)
        resolved_ids_by_target[target_id] = set(resolved)
        recipe_targets.append(
            {
                "targetId": target_id,
                "selector": deepcopy(target["selector"]),
                "exports": deepcopy(target["exports"]),
                "expectedCount": int(target["expectedMatches"]),
                "resolvedEvidenceIds": resolved,
                "candidates": deepcopy(result.get("candidates") or []),
                "status": "CONFIRMED",
            }
        )
    unexpected_results = sorted(set(results_by_id) - {
        str(target["id"]) for target in recipe["targets"]
    })
    if unexpected_results:
        _fail(
            "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
            f"Unexpected target results: {', '.join(unexpected_results)}.",
        )

    functions = _objects(raw.get("functions"), "recipe export functions")
    functions_by_id: dict[str, Mapping[str, Any]] = {}
    for function in functions:
        evidence_id = str(function.get("evidenceId") or "")
        if not evidence_id:
            continue
        if evidence_id in functions_by_id:
            _fail(
                "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
                f"Duplicate exported function evidence ID: {evidence_id}.",
            )
        functions_by_id[evidence_id] = function

    for key, recipe_key in (
        ("fieldQueryResults", "fieldQueries"),
        ("vtableQueryResults", "vtableQueries"),
    ):
        result_rows = _objects(raw.get(key), f"recipe export {key}")
        query_results: dict[str, Mapping[str, Any]] = {}
        for row in result_rows:
            query_id = str(row.get("queryId") or "")
            if not query_id:
                _fail(
                    "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
                    f"{key} contains an empty queryId.",
                )
            if query_id in query_results:
                _fail(
                    "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
                    f"Duplicate query result: {query_id}.",
                )
            query_results[query_id] = row
        recipe_queries = _objects(
            recipe.get(recipe_key),
            f"recipe {recipe_key}",
        )
        recipe_query_ids = {str(query["id"]) for query in recipe_queries}
        unexpected_query_ids = sorted(set(query_results) - recipe_query_ids)
        if unexpected_query_ids:
            _fail(
                "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
                f"Unexpected {recipe_key} results: "
                f"{', '.join(unexpected_query_ids)}.",
            )
        for query in recipe_queries:
            query_id = str(query["id"])
            result = query_results.get(query_id)
            if result is None:
                _fail(
                    "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
                    f"Missing query result: {query_id}.",
                )
            if result.get("expectedMatches") != query.get("expectedMatches"):
                _fail(
                    "NATIVE_EVIDENCE_PROVENANCE_MISMATCH",
                    f"Query result {query_id} changed expectedMatches.",
                )
            if recipe_key == "fieldQueries":
                expected_contract = {
                    "structureName": query.get("structureName"),
                    "fieldName": query.get("fieldName"),
                    "functionTargetIds": list(
                        query.get("functionTargetIds") or []
                    ),
                }
            else:
                expected_contract = {
                    "className": query.get("className"),
                    "slotOffset": query.get("slotOffset"),
                }
            actual_contract = {
                name: result.get(name) for name in expected_contract
            }
            if actual_contract != expected_contract:
                _fail(
                    "NATIVE_EVIDENCE_PROVENANCE_MISMATCH",
                    f"Query result {query_id} does not echo its recipe "
                    "contract.",
                )
            query_resolved = _validate_candidates(
                result,
                label=query_id,
                expected=int(query["expectedMatches"]),
                formal=formal,
            )
            if (
                recipe_key == "fieldQueries"
                and "functionTargetIds" in query
            ):
                allowed_ids: set[str] = set()
                for target_id in query["functionTargetIds"]:
                    allowed_ids.update(
                        resolved_ids_by_target.get(str(target_id), set())
                    )
                unexpected_resolved = sorted(
                    set(query_resolved) - allowed_ids
                )
                if unexpected_resolved:
                    _fail(
                        "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
                        f"Field query {query_id} resolved functions outside "
                        "its declared functionTargetIds.",
                    )
            for evidence_id in query_resolved:
                function = functions_by_id.get(evidence_id)
                if function is None:
                    _fail(
                        "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
                        f"Query {query_id} resolved a function absent from "
                        "the exported function set.",
                    )
                if recipe_key == "fieldQueries":
                    evidence_rows = function.get("fieldAccesses")
                    exact_match = isinstance(evidence_rows, list) and any(
                        isinstance(row, Mapping)
                        and row.get("queryId") == query_id
                        and row.get("structureName")
                        == query.get("structureName")
                        and row.get("fieldName") == query.get("fieldName")
                        for row in evidence_rows
                    )
                else:
                    evidence_rows = function.get("vtableSlots")
                    exact_match = isinstance(evidence_rows, list) and any(
                        isinstance(row, Mapping)
                        and row.get("queryId") == query_id
                        and row.get("targetEvidenceId") == evidence_id
                        and row.get("ownerType") == query.get("className")
                        and row.get("slotOffset") == query.get("slotOffset")
                        for row in evidence_rows
                    )
                if not exact_match:
                    _fail(
                        "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
                        f"Query {query_id} has no exact evidence row on "
                        f"{evidence_id}.",
                    )

    function_ids = set(functions_by_id)
    if not resolved_ids.issubset(function_ids):
        _fail(
            "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
            "A resolved target is absent from the exported function set.",
        )

    legacy_export = {
        "schema": "blueprint-to-code-native-targets/v1",
        "program": raw.get("program"),
        "binarySha256": raw.get("binarySha256"),
        "languageId": raw.get("languageId"),
        "compilerSpecId": raw.get("compilerSpecId"),
        "pdbLoaded": raw.get("pdbLoaded"),
        "pdbGuid": raw.get("pdbGuid"),
        "pdbAge": raw.get("pdbAge"),
        "patterns": [target["id"] for target in recipe["targets"]],
        "functions": deepcopy(functions),
    }
    manifest = create_native_evidence_manifest(
        legacy_export,
        identity=identity,
        ghidra=ghidra,
        java=java,
        generator=generator_input,
        formal=formal,
    )
    raw_gaps = list(_objects(raw.get("gaps"), "recipe export gaps"))
    for function in functions:
        for function_gap in _objects(
            function.get("gaps") or [],
            "recipe export function gaps",
        ):
            raw_gaps.append(function_gap)
    manifest["selection"] = {
        "recipeId": recipe.get("recipeId"),
        "recipeSha256": recipe_sha,
        "targetCount": len(recipe_targets),
        "resolvedFunctionCount": len(functions),
        "fieldQueryResults": deepcopy(raw.get("fieldQueryResults") or []),
        "vtableQueryResults": deepcopy(raw.get("vtableQueryResults") or []),
    }
    manifest["recipeTargets"] = recipe_targets
    manifest["gaps"] = [
        _gap(value, index) for index, value in enumerate(raw_gaps)
    ]
    validate_native_evidence_manifest(
        manifest,
        expected_identity=identity,
        formal=formal,
    )
    return manifest
