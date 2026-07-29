"""Strict report contract for the fixed Stage 14 production narrow gates.

This module packages already-computed diagnostic observations.  It does not
run a production update, infer that a gate passed from caller-supplied flags,
publish a snapshot, or establish E4/cutover evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .signed_receipts import SignedReceiptError, canonical_json_bytes


NARROW_GATE_REPORT_SCHEMA = "ark-kb-production-narrow-gate-report/v1"
ENGINEERING_DIAGNOSTIC = "ENGINEERING_DIAGNOSTIC"
NARROW_GATE_PROOF_PREFIX = "narrow-gate-proof://"

NARROW_GATE_CHECK_IDS = (
    "incremental.selected_source_diff_exact",
    "incremental.changed_revisions_fresh",
    "incremental.no_stale_candidate_legacy_promotion",
    "incremental.no_orphan_rows",
    "incremental.effective_dependencies_exact",
    "incremental.registrations_resolvable",
    "incremental.projections_core_artifact_match",
    "incremental.search_affected_entities_exact",
    "incremental.cache_old_state_absent",
    "incremental.sqlite_sealed_integrity",
    "incremental.current_base_unchanged",
)
NARROW_GATE_DETAIL_CODES = MappingProxyType(
    {
        "incremental.selected_source_diff_exact": ("SELECTED_SOURCE_DIFF_EXACT"),
        "incremental.changed_revisions_fresh": "CHANGED_REVISIONS_FRESH",
        "incremental.no_stale_candidate_legacy_promotion": (
            "NO_STALE_CANDIDATE_LEGACY_PROMOTION"
        ),
        "incremental.no_orphan_rows": "NO_ORPHAN_ROWS",
        "incremental.effective_dependencies_exact": ("EFFECTIVE_DEPENDENCIES_EXACT"),
        "incremental.registrations_resolvable": ("REGISTRATIONS_RESOLVABLE"),
        "incremental.projections_core_artifact_match": (
            "PROJECTIONS_CORE_ARTIFACT_MATCH"
        ),
        "incremental.search_affected_entities_exact": (
            "SEARCH_AFFECTED_ENTITIES_EXACT"
        ),
        "incremental.cache_old_state_absent": "CACHE_OLD_STATE_ABSENT",
        "incremental.sqlite_sealed_integrity": "SQLITE_SEALED_INTEGRITY",
        "incremental.current_base_unchanged": "CURRENT_BASE_UNCHANGED",
    }
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BUILD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._-]{0,127}$")
_REPORT_FIELDS = frozenset(
    {
        "schema",
        "evidenceClass",
        "updateBaseline",
        "checks",
        "summary",
        "published",
        "e4Scenario2Complete",
        "claimsGlobal75",
        "productionAuthority",
        "cutoverEligible",
        "mode",
        "defaultQuerySource",
        "proof",
    }
)
_BASELINE_FIELDS = frozenset(
    {
        "baseBuildId",
        "basePointerSha256",
        "baseManifestSha256",
        "baseSourceManifestFingerprint",
        "candidateSourceManifestFingerprint",
        "sourceDiffSha256",
        "deltaReceiptSha256",
    }
)
_CHECK_FIELDS = frozenset({"id", "critical", "passed", "details", "digests"})
_DETAIL_FIELDS = frozenset({"detailCode", "observationCount"})
_DIGEST_FIELDS = frozenset({"evidenceSha256"})
_SUMMARY_FIELDS = frozenset({"total", "passed", "failed"})
_SUMMARY = {"total": 11, "passed": 11, "failed": 0}
_MAX_REPORT_BYTES = 1024 * 1024
_MAX_JSON_DEPTH = 64


class NarrowGateContractError(ValueError):
    """Raised when a narrow-gate report fails closed."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class UpdateBaseline:
    """Out-of-band update identity that every narrow-gate report must bind."""

    base_build_id: str
    base_pointer_sha256: str
    base_manifest_sha256: str
    base_source_manifest_fingerprint: str
    candidate_source_manifest_fingerprint: str
    source_diff_sha256: str
    delta_receipt_sha256: str


@dataclass(frozen=True, slots=True)
class NarrowGateObservation:
    """Typed digest of one externally computed narrow-gate observation.

    There is deliberately no caller-controlled ``passed`` or ``critical``
    field.  The builder accepts only the complete canonical set and emits the
    contract's fixed all-critical/all-pass report shape.
    """

    gate_id: str
    observation_count: int
    evidence_sha256: str


def _error(code: str, message: str) -> NarrowGateContractError:
    return NarrowGateContractError(code, message)


def _exact_fields(
    value: Mapping[object, object],
    expected: frozenset[str],
    *,
    field: str,
) -> None:
    if frozenset(value) != expected:
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            f"{field} fields are invalid",
        )


def _required_sha256(value: object, *, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            f"{field} must be a lowercase SHA-256 hex digest",
        )
    return value


def _required_build_id(value: object, *, field: str) -> str:
    if type(value) is not str or _BUILD_ID.fullmatch(value) is None:
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            f"{field} must be a safe build ID",
        )
    return value


def _required_nonnegative_integer(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            f"{field} must be a non-negative integer",
        )
    return value


def _plain_json(
    value: object,
    *,
    _depth: int = 0,
    _ancestors: frozenset[int] = frozenset(),
) -> object:
    if _depth > _MAX_JSON_DEPTH:
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            "report exceeds the maximum JSON nesting depth",
        )
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in _ancestors:
            raise _error(
                "NARROW_GATE_REPORT_INVALID",
                "report contains a cyclic object",
            )
        try:
            items = tuple(value.items())
        except NarrowGateContractError:
            raise
        except Exception as error:
            raise _error(
                "NARROW_GATE_REPORT_INVALID",
                "report object could not be snapshotted",
            ) from error
        if any(type(key) is not str for key, _ in items):
            raise _error(
                "NARROW_GATE_REPORT_INVALID",
                "report object keys must be strings",
            )
        child_ancestors = _ancestors | {identity}
        return {
            key: _plain_json(
                child,
                _depth=_depth + 1,
                _ancestors=child_ancestors,
            )
            for key, child in items
        }
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in _ancestors:
            raise _error(
                "NARROW_GATE_REPORT_INVALID",
                "report contains a cyclic array",
            )
        try:
            children = tuple(value)
        except Exception as error:
            raise _error(
                "NARROW_GATE_REPORT_INVALID",
                "report array could not be snapshotted",
            ) from error
        child_ancestors = _ancestors | {identity}
        return [
            _plain_json(
                child,
                _depth=_depth + 1,
                _ancestors=child_ancestors,
            )
            for child in children
        ]
    return value


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return canonical_json_bytes(_plain_json(value))
    except SignedReceiptError as error:
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            "report must contain canonical JSON values",
        ) from error


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_json(child) for key, child in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(child) for child in value)
    return value


def _baseline_payload(value: UpdateBaseline) -> dict[str, str]:
    if type(value) is not UpdateBaseline:
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            "update_baseline must be an UpdateBaseline",
        )
    return {
        "baseBuildId": _required_build_id(
            value.base_build_id,
            field="updateBaseline.baseBuildId",
        ),
        "basePointerSha256": _required_sha256(
            value.base_pointer_sha256,
            field="updateBaseline.basePointerSha256",
        ),
        "baseManifestSha256": _required_sha256(
            value.base_manifest_sha256,
            field="updateBaseline.baseManifestSha256",
        ),
        "baseSourceManifestFingerprint": _required_sha256(
            value.base_source_manifest_fingerprint,
            field="updateBaseline.baseSourceManifestFingerprint",
        ),
        "candidateSourceManifestFingerprint": _required_sha256(
            value.candidate_source_manifest_fingerprint,
            field="updateBaseline.candidateSourceManifestFingerprint",
        ),
        "sourceDiffSha256": _required_sha256(
            value.source_diff_sha256,
            field="updateBaseline.sourceDiffSha256",
        ),
        "deltaReceiptSha256": _required_sha256(
            value.delta_receipt_sha256,
            field="updateBaseline.deltaReceiptSha256",
        ),
    }


def _validated_baseline_payload(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            "updateBaseline must be an object",
        )
    _exact_fields(value, _BASELINE_FIELDS, field="updateBaseline")
    return {
        "baseBuildId": _required_build_id(
            value.get("baseBuildId"),
            field="updateBaseline.baseBuildId",
        ),
        "basePointerSha256": _required_sha256(
            value.get("basePointerSha256"),
            field="updateBaseline.basePointerSha256",
        ),
        "baseManifestSha256": _required_sha256(
            value.get("baseManifestSha256"),
            field="updateBaseline.baseManifestSha256",
        ),
        "baseSourceManifestFingerprint": _required_sha256(
            value.get("baseSourceManifestFingerprint"),
            field="updateBaseline.baseSourceManifestFingerprint",
        ),
        "candidateSourceManifestFingerprint": _required_sha256(
            value.get("candidateSourceManifestFingerprint"),
            field="updateBaseline.candidateSourceManifestFingerprint",
        ),
        "sourceDiffSha256": _required_sha256(
            value.get("sourceDiffSha256"),
            field="updateBaseline.sourceDiffSha256",
        ),
        "deltaReceiptSha256": _required_sha256(
            value.get("deltaReceiptSha256"),
            field="updateBaseline.deltaReceiptSha256",
        ),
    }


def _check_payload(
    observation: NarrowGateObservation,
    *,
    expected_gate_id: str,
) -> dict[str, object]:
    if type(observation) is not NarrowGateObservation:
        raise _error(
            "NARROW_GATE_OBSERVATION_INVALID",
            "observations must contain only NarrowGateObservation values",
        )
    if type(observation.gate_id) is not str:
        raise _error(
            "NARROW_GATE_OBSERVATION_INVALID",
            "gate_id must be a string",
        )
    if observation.gate_id != expected_gate_id:
        raise _error(
            "NARROW_GATE_SET_INVALID",
            "observations must contain exactly the fixed 11 gate IDs "
            "in canonical order",
        )
    observation_count = _required_nonnegative_integer(
        observation.observation_count,
        field=f"{expected_gate_id}.observation_count",
    )
    evidence_sha256 = _required_sha256(
        observation.evidence_sha256,
        field=f"{expected_gate_id}.evidence_sha256",
    )
    return {
        "id": expected_gate_id,
        "critical": True,
        "passed": True,
        "details": {
            "detailCode": NARROW_GATE_DETAIL_CODES[expected_gate_id],
            "observationCount": observation_count,
        },
        "digests": {"evidenceSha256": evidence_sha256},
    }


def build_narrow_gate_diagnostic_report(
    *,
    update_baseline: UpdateBaseline,
    observations: Iterable[NarrowGateObservation],
) -> dict[str, object]:
    """Build a diagnostic-only envelope without production authority."""

    baseline_payload = _baseline_payload(update_baseline)
    try:
        materialized = tuple(observations)
    except TypeError as error:
        raise _error(
            "NARROW_GATE_OBSERVATION_INVALID",
            "observations must be iterable",
        ) from error
    if any(type(value) is not NarrowGateObservation for value in materialized):
        raise _error(
            "NARROW_GATE_OBSERVATION_INVALID",
            "observations must contain only NarrowGateObservation values",
        )
    observed_ids = tuple(value.gate_id for value in materialized)
    if observed_ids != NARROW_GATE_CHECK_IDS:
        raise _error(
            "NARROW_GATE_SET_INVALID",
            "observations must contain exactly the fixed 11 gate IDs "
            "in canonical order",
        )
    checks = [
        _check_payload(observation, expected_gate_id=gate_id)
        for gate_id, observation in zip(
            NARROW_GATE_CHECK_IDS,
            materialized,
            strict=True,
        )
    ]
    if len({check["digests"]["evidenceSha256"] for check in checks}) != len(
        checks
    ):
        raise _error(
            "NARROW_GATE_EVIDENCE_REPLAY",
            "every narrow gate requires a distinct evidence digest",
        )
    body: dict[str, object] = {
        "schema": NARROW_GATE_REPORT_SCHEMA,
        "evidenceClass": ENGINEERING_DIAGNOSTIC,
        "updateBaseline": baseline_payload,
        "checks": checks,
        "summary": dict(_SUMMARY),
        "published": False,
        "e4Scenario2Complete": False,
        "claimsGlobal75": False,
        "productionAuthority": False,
        "cutoverEligible": False,
        "mode": "shadow",
        "defaultQuerySource": "legacy",
    }
    body["proof"] = NARROW_GATE_PROOF_PREFIX + _sha256_json(body)
    return body


def narrow_gate_diagnostic_report_sha256(
    report: Mapping[str, object],
) -> str:
    """Return the SHA-256 of the complete canonical report artifact."""

    if not isinstance(report, Mapping):
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            "report must be an object",
        )
    return _sha256_json(report)


def _validate_check(value: object, *, index: int, gate_id: str) -> None:
    field = f"checks[{index}]"
    if not isinstance(value, Mapping):
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            f"{field} must be an object",
        )
    _exact_fields(value, _CHECK_FIELDS, field=field)
    if type(value.get("id")) is not str or value.get("id") != gate_id:
        raise _error(
            "NARROW_GATE_SET_INVALID",
            "report must contain exactly the fixed 11 gate IDs in canonical order",
        )
    if value.get("critical") is not True or value.get("passed") is not True:
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            f"{field} must be critical=true and passed=true",
        )

    details = value.get("details")
    if not isinstance(details, Mapping):
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            f"{field}.details must be an object",
        )
    _exact_fields(details, _DETAIL_FIELDS, field=f"{field}.details")
    if (
        type(details.get("detailCode")) is not str
        or details.get("detailCode") != NARROW_GATE_DETAIL_CODES[gate_id]
    ):
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            f"{field}.details.detailCode is invalid",
        )
    _required_nonnegative_integer(
        details.get("observationCount"),
        field=f"{field}.details.observationCount",
    )

    digests = value.get("digests")
    if not isinstance(digests, Mapping):
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            f"{field}.digests must be an object",
        )
    _exact_fields(digests, _DIGEST_FIELDS, field=f"{field}.digests")
    _required_sha256(
        digests.get("evidenceSha256"),
        field=f"{field}.digests.evidenceSha256",
    )


def _validate_summary(value: object) -> None:
    if not isinstance(value, Mapping):
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            "summary must be an object",
        )
    _exact_fields(value, _SUMMARY_FIELDS, field="summary")
    for field, expected in _SUMMARY.items():
        observed = value.get(field)
        if type(observed) is not int or observed != expected:
            raise _error(
                "NARROW_GATE_REPORT_INVALID",
                "summary must be exactly total=11, passed=11, failed=0",
            )


def _require_fixed_claims(report: Mapping[str, object]) -> None:
    string_claims = {
        "schema": NARROW_GATE_REPORT_SCHEMA,
        "evidenceClass": ENGINEERING_DIAGNOSTIC,
        "mode": "shadow",
        "defaultQuerySource": "legacy",
    }
    for field, expected in string_claims.items():
        if type(report.get(field)) is not str or report.get(field) != expected:
            raise _error(
                "NARROW_GATE_REPORT_INVALID",
                f"{field} must equal {expected}",
            )
    for field in (
        "published",
        "e4Scenario2Complete",
        "claimsGlobal75",
        "productionAuthority",
        "cutoverEligible",
    ):
        if report.get(field) is not False:
            raise _error(
                "NARROW_GATE_REPORT_INVALID",
                f"{field} must be false",
            )


def validate_narrow_gate_diagnostic_report(
    report: Mapping[str, object],
    *,
    expected_report_sha256: str,
    expected_update_baseline: UpdateBaseline,
) -> Mapping[str, object]:
    """Validate a report against independent artifact and baseline bindings.

    ``expected_report_sha256`` must come from an out-of-band trusted manifest
    or orchestrator record.  Computing it from ``report`` at the call site
    defeats the trust boundary and is explicitly outside this validator.
    """

    expected_artifact_sha256 = _required_sha256(
        expected_report_sha256,
        field="expected_report_sha256",
    )
    expected_baseline = _baseline_payload(expected_update_baseline)
    if not isinstance(report, Mapping):
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            "report must be an object",
        )
    snapshot = _plain_json(report)
    if not isinstance(snapshot, dict):
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            "report snapshot must remain an object",
        )
    _exact_fields(snapshot, _REPORT_FIELDS, field="report")
    _require_fixed_claims(snapshot)

    observed_baseline = _validated_baseline_payload(
        snapshot.get("updateBaseline")
    )
    if observed_baseline != expected_baseline:
        raise _error(
            "UPDATE_BASELINE_MISMATCH",
            "report updateBaseline does not match the trusted update",
        )

    checks = snapshot.get("checks")
    if type(checks) is not list or len(checks) != len(NARROW_GATE_CHECK_IDS):
        raise _error(
            "NARROW_GATE_SET_INVALID",
            "report must contain exactly the fixed 11 gate IDs in canonical order",
        )
    for index, (gate_id, check) in enumerate(
        zip(NARROW_GATE_CHECK_IDS, checks, strict=True)
    ):
        _validate_check(check, index=index, gate_id=gate_id)
    evidence_digests = [
        check["digests"]["evidenceSha256"]
        for check in checks
        if isinstance(check, Mapping)
        and isinstance(check.get("digests"), Mapping)
    ]
    if len(evidence_digests) != len(set(evidence_digests)):
        raise _error(
            "NARROW_GATE_EVIDENCE_REPLAY",
            "every narrow gate requires a distinct evidence digest",
        )
    _validate_summary(snapshot.get("summary"))

    proof = snapshot.get("proof")
    body = {key: value for key, value in snapshot.items() if key != "proof"}
    expected_proof = NARROW_GATE_PROOF_PREFIX + _sha256_json(body)
    if type(proof) is not str or proof != expected_proof:
        raise _error(
            "NARROW_GATE_REPORT_PROOF_INVALID",
            "report proof does not match the canonical report body",
        )
    observed_artifact_sha256 = narrow_gate_diagnostic_report_sha256(snapshot)
    if observed_artifact_sha256 != expected_artifact_sha256:
        raise _error(
            "OUT_OF_BAND_REPORT_SHA256_MISMATCH",
            "out-of-band expected report SHA-256 does not match",
        )

    frozen = _freeze_json(snapshot)
    if not isinstance(frozen, Mapping):
        raise AssertionError("validated report freeze must remain a mapping")
    return frozen


def parse_and_validate_narrow_gate_diagnostic_report_bytes(
    payload: bytes,
    *,
    expected_report_sha256: str,
    expected_update_baseline: UpdateBaseline,
) -> Mapping[str, object]:
    """Strictly parse one canonical diagnostic artifact, then validate it."""

    if type(payload) is not bytes or not 0 < len(payload) <= _MAX_REPORT_BYTES:
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            "report artifact bytes are missing or oversized",
        )

    def reject_float(value: str) -> object:
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            f"floating JSON numbers are forbidden: {value}",
        )

    def reject_constant(value: str) -> object:
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            f"non-finite JSON constants are forbidden: {value}",
        )

    def strict_object(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _error(
                    "NARROW_GATE_REPORT_INVALID",
                    f"duplicate JSON key: {key}",
                )
            result[key] = value
        return result

    try:
        parsed = json.loads(
            payload.decode("utf-8"),
            parse_float=reject_float,
            parse_constant=reject_constant,
            object_pairs_hook=strict_object,
        )
    except NarrowGateContractError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            "report artifact is not strict UTF-8 JSON",
        ) from error
    if not isinstance(parsed, dict):
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            "report artifact must be a JSON object",
        )
    if payload != _canonical_json_bytes(parsed):
        raise _error(
            "NARROW_GATE_REPORT_INVALID",
            "report artifact must use canonical JSON bytes",
        )
    return validate_narrow_gate_diagnostic_report(
        parsed,
        expected_report_sha256=expected_report_sha256,
        expected_update_baseline=expected_update_baseline,
    )
