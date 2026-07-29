from __future__ import annotations

import copy
import hashlib
import json
import sys
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.narrow_gates import (  # noqa: E402
    ENGINEERING_DIAGNOSTIC,
    NARROW_GATE_CHECK_IDS,
    NARROW_GATE_DETAIL_CODES,
    NARROW_GATE_REPORT_SCHEMA,
    NarrowGateContractError,
    NarrowGateObservation,
    UpdateBaseline,
    build_narrow_gate_diagnostic_report,
    narrow_gate_diagnostic_report_sha256,
    parse_and_validate_narrow_gate_diagnostic_report_bytes,
    validate_narrow_gate_diagnostic_report,
)


EXPECTED_IDS = (
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
EXPECTED_DETAIL_CODES = (
    "SELECTED_SOURCE_DIFF_EXACT",
    "CHANGED_REVISIONS_FRESH",
    "NO_STALE_CANDIDATE_LEGACY_PROMOTION",
    "NO_ORPHAN_ROWS",
    "EFFECTIVE_DEPENDENCIES_EXACT",
    "REGISTRATIONS_RESOLVABLE",
    "PROJECTIONS_CORE_ARTIFACT_MATCH",
    "SEARCH_AFFECTED_ENTITIES_EXACT",
    "CACHE_OLD_STATE_ABSENT",
    "SQLITE_SEALED_INTEGRITY",
    "CURRENT_BASE_UNCHANGED",
)


def _baseline() -> UpdateBaseline:
    return UpdateBaseline(
        base_build_id="20260730T010203-a1b2c3d4e5f6",
        base_pointer_sha256="1" * 64,
        base_manifest_sha256="2" * 64,
        base_source_manifest_fingerprint="3" * 64,
        candidate_source_manifest_fingerprint="4" * 64,
        source_diff_sha256="5" * 64,
        delta_receipt_sha256="6" * 64,
    )


def _observations() -> tuple[NarrowGateObservation, ...]:
    return tuple(
        NarrowGateObservation(
            gate_id=gate_id,
            observation_count=index,
            evidence_sha256=hashlib.sha256(
                ("TEST_ONLY:" + gate_id).encode("utf-8")
            ).hexdigest(),
        )
        for index, gate_id in enumerate(EXPECTED_IDS, start=1)
    )


def _report() -> dict[str, object]:
    return build_narrow_gate_diagnostic_report(
        update_baseline=_baseline(),
        observations=_observations(),
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _rehash(report: dict[str, object]) -> str:
    body = copy.deepcopy(report)
    body.pop("proof", None)
    body_sha256 = hashlib.sha256(_canonical(body)).hexdigest()
    report["proof"] = "narrow-gate-proof://" + body_sha256
    return hashlib.sha256(_canonical(report)).hexdigest()


def _validate(
    report: dict[str, object],
    *,
    expected_report_sha256: str | None = None,
) -> MappingProxyType:
    return validate_narrow_gate_diagnostic_report(
        report,
        expected_report_sha256=(
            expected_report_sha256
            or narrow_gate_diagnostic_report_sha256(report)
        ),
        expected_update_baseline=_baseline(),
    )


def test_fixed_gate_ids_and_detail_codes_are_exact() -> None:
    assert NARROW_GATE_CHECK_IDS == EXPECTED_IDS
    assert (
        tuple(NARROW_GATE_DETAIL_CODES[gate_id] for gate_id in EXPECTED_IDS)
        == EXPECTED_DETAIL_CODES
    )


def test_builder_emits_all_pass_fail_closed_claims_and_validates() -> None:
    report = _report()

    assert report["schema"] == NARROW_GATE_REPORT_SCHEMA
    assert report["evidenceClass"] == ENGINEERING_DIAGNOSTIC
    assert report["published"] is False
    assert report["e4Scenario2Complete"] is False
    assert report["claimsGlobal75"] is False
    assert report["productionAuthority"] is False
    assert report["cutoverEligible"] is False
    assert report["mode"] == "shadow"
    assert report["defaultQuerySource"] == "legacy"
    assert report["summary"] == {
        "total": 11,
        "passed": 11,
        "failed": 0,
    }
    assert [check["id"] for check in report["checks"]] == list(EXPECTED_IDS)
    assert all(check["critical"] is True for check in report["checks"])
    assert all(check["passed"] is True for check in report["checks"])
    assert [check["details"]["detailCode"] for check in report["checks"]] == (
        list(EXPECTED_DETAIL_CODES)
    )

    validated = _validate(report)
    assert isinstance(validated, MappingProxyType)
    assert validated["updateBaseline"]["sourceDiffSha256"] == "5" * 64
    assert validated["checks"][0]["digests"]["evidenceSha256"] == (
        _observations()[0].evidence_sha256
    )
    with pytest.raises(TypeError):
        validated["published"] = True
    with pytest.raises(TypeError):
        validated["checks"][0]["details"]["observationCount"] = 0


@pytest.mark.parametrize(
    "observations",
    [
        _observations()[:1],
        (
            replace(_observations()[0], gate_id="fixture.narrow"),
            *_observations()[1:],
        ),
        (
            *_observations()[:-1],
            replace(_observations()[-1], gate_id=EXPECTED_IDS[0]),
        ),
        (
            *_observations(),
            NarrowGateObservation(
                gate_id="incremental.extra",
                observation_count=1,
                evidence_sha256="a" * 64,
            ),
        ),
    ],
    ids=("single-fixture", "substituted-id", "duplicate-id", "extra-id"),
)
def test_builder_rejects_incomplete_or_noncanonical_gate_sets(
    observations: tuple[NarrowGateObservation, ...],
) -> None:
    with pytest.raises(
        NarrowGateContractError,
        match="exactly the fixed 11 gate IDs",
    ):
        build_narrow_gate_diagnostic_report(
            update_baseline=_baseline(),
            observations=observations,
        )


def test_builder_rejects_caller_supplied_pass_payloads() -> None:
    caller_attested_payloads = [
        {
            "id": gate_id,
            "critical": True,
            "passed": True,
            "details": {
                "detailCode": NARROW_GATE_DETAIL_CODES[gate_id],
                "observationCount": 1,
            },
            "digests": {"evidenceSha256": "a" * 64},
        }
        for gate_id in EXPECTED_IDS
    ]

    with pytest.raises(
        NarrowGateContractError,
        match="NarrowGateObservation",
    ):
        build_narrow_gate_diagnostic_report(
            update_baseline=_baseline(),
            observations=caller_attested_payloads,
        )


def test_builder_and_validator_reject_replayed_fixture_digest() -> None:
    replayed = tuple(
        replace(observation, evidence_sha256="a" * 64)
        for observation in _observations()
    )
    with pytest.raises(
        NarrowGateContractError,
        match="distinct evidence digest",
    ):
        build_narrow_gate_diagnostic_report(
            update_baseline=_baseline(),
            observations=replayed,
        )

    report = _report()
    for check in report["checks"]:
        check["digests"]["evidenceSha256"] = "a" * 64
    expected_report_sha256 = _rehash(report)
    with pytest.raises(
        NarrowGateContractError,
        match="distinct evidence digest",
    ):
        _validate(
            report,
            expected_report_sha256=expected_report_sha256,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda report: report["checks"].__setitem__(
            slice(1, None),
            [],
        ),
        lambda report: report["checks"][0].__setitem__(
            "id",
            "fixture.narrow",
        ),
        lambda report: report["checks"][-1].__setitem__(
            "id",
            EXPECTED_IDS[0],
        ),
        lambda report: report["checks"].append(copy.deepcopy(report["checks"][0])),
    ],
    ids=("single-check", "substituted-id", "duplicate-id", "extra-id"),
)
def test_validator_rejects_noncanonical_gate_sets_even_after_rehash(
    mutation,
) -> None:
    report = _report()
    mutation(report)
    expected_report_sha256 = _rehash(report)

    with pytest.raises(NarrowGateContractError):
        _validate(
            report,
            expected_report_sha256=expected_report_sha256,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda report: report.__setitem__("published", "false"),
        lambda report: report["checks"][0].__setitem__("critical", "true"),
        lambda report: report["checks"][0].__setitem__("passed", 1),
        lambda report: report["checks"][0]["details"].__setitem__(
            "observationCount",
            "1",
        ),
        lambda report: report["checks"][0]["details"].__setitem__(
            "observationCount",
            True,
        ),
        lambda report: report["summary"].__setitem__("total", "11"),
    ],
    ids=(
        "string-bool-claim",
        "string-bool-critical",
        "integer-bool-passed",
        "string-count",
        "bool-count",
        "string-summary-count",
    ),
)
def test_validator_rejects_bool_and_integer_coercion(mutation) -> None:
    report = _report()
    mutation(report)
    expected_report_sha256 = _rehash(report)

    with pytest.raises(NarrowGateContractError):
        _validate(
            report,
            expected_report_sha256=expected_report_sha256,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda report: report.__setitem__("status", "READY"),
        lambda report: report["updateBaseline"].__setitem__("extra", "x"),
        lambda report: report["checks"][0].__setitem__("extra", "x"),
        lambda report: report["checks"][0]["details"].__setitem__("extra", 1),
        lambda report: report["checks"][0]["digests"].__setitem__(
            "extraSha256",
            "a" * 64,
        ),
        lambda report: report["summary"].__setitem__("extra", 0),
    ],
    ids=(
        "top-level",
        "baseline",
        "check",
        "details",
        "digests",
        "summary",
    ),
)
def test_validator_rejects_unknown_fields(mutation) -> None:
    report = _report()
    mutation(report)
    expected_report_sha256 = _rehash(report)

    with pytest.raises(NarrowGateContractError):
        _validate(
            report,
            expected_report_sha256=expected_report_sha256,
        )


def test_validator_rejects_summary_forgery_after_rehash() -> None:
    report = _report()
    report["summary"] = {"total": 11, "passed": 10, "failed": 1}
    expected_report_sha256 = _rehash(report)

    with pytest.raises(
        NarrowGateContractError,
        match="summary",
    ):
        _validate(
            report,
            expected_report_sha256=expected_report_sha256,
        )


def test_validator_rejects_invalid_internal_proof_with_matching_artifact_sha() -> None:
    report = _report()
    report["proof"] = "narrow-gate-proof://" + "0" * 64
    tampered_artifact_sha256 = hashlib.sha256(_canonical(report)).hexdigest()

    with pytest.raises(
        NarrowGateContractError,
        match="proof",
    ):
        _validate(
            report,
            expected_report_sha256=tampered_artifact_sha256,
        )


def test_validator_rejects_self_rehash_attack_against_oob_artifact_sha() -> None:
    report = _report()
    trusted_report_sha256 = narrow_gate_diagnostic_report_sha256(report)
    report["checks"][0]["digests"]["evidenceSha256"] = "f" * 64
    _rehash(report)

    with pytest.raises(NarrowGateContractError) as caught:
        _validate(
            report,
            expected_report_sha256=trusted_report_sha256,
        )
    assert caught.value.code == "OUT_OF_BAND_REPORT_SHA256_MISMATCH"


@pytest.mark.parametrize(
    ("attribute", "json_field"),
    [
        ("base_build_id", "baseBuildId"),
        ("base_pointer_sha256", "basePointerSha256"),
        ("base_manifest_sha256", "baseManifestSha256"),
        (
            "base_source_manifest_fingerprint",
            "baseSourceManifestFingerprint",
        ),
        (
            "candidate_source_manifest_fingerprint",
            "candidateSourceManifestFingerprint",
        ),
        ("source_diff_sha256", "sourceDiffSha256"),
        ("delta_receipt_sha256", "deltaReceiptSha256"),
    ],
)
def test_validator_rejects_each_update_baseline_misbinding(
    attribute: str,
    json_field: str,
) -> None:
    report = _report()
    replacement = "wrong-build" if attribute == "base_build_id" else "f" * 64
    report["updateBaseline"][json_field] = replacement
    attacker_selected_sha256 = _rehash(report)

    with pytest.raises(NarrowGateContractError) as caught:
        _validate(
            report,
            expected_report_sha256=attacker_selected_sha256,
        )
    assert caught.value.code == "UPDATE_BASELINE_MISMATCH"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("published", True),
        ("e4Scenario2Complete", True),
        ("claimsGlobal75", True),
        ("productionAuthority", True),
        ("cutoverEligible", True),
        ("mode", "vnext"),
        ("defaultQuerySource", "vnext"),
    ],
)
def test_validator_rejects_ready_or_vnext_claims(
    field: str,
    value: object,
) -> None:
    report = _report()
    report[field] = value
    attacker_selected_sha256 = _rehash(report)

    with pytest.raises(NarrowGateContractError):
        _validate(
            report,
            expected_report_sha256=attacker_selected_sha256,
        )


def test_validator_rejects_invalid_oob_sha_types_and_values() -> None:
    report = _report()
    for invalid in (True, 1, "A" * 64, "short"):
        with pytest.raises(NarrowGateContractError):
            validate_narrow_gate_diagnostic_report(
                report,
                expected_report_sha256=invalid,
                expected_update_baseline=_baseline(),
            )


class _SplitViewMapping(Mapping[str, object]):
    def __init__(
        self,
        safe: dict[str, object],
        malicious: dict[str, object],
    ) -> None:
        self.safe = safe
        self.malicious = malicious
        self.items_calls = 0

    def __getitem__(self, key: str) -> object:
        return self.malicious[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.malicious)

    def __len__(self) -> int:
        return len(self.malicious)

    def items(self) -> object:
        self.items_calls += 1
        view = self.safe if self.items_calls == 1 else self.malicious
        return view.items()


def test_validator_snapshots_split_view_mapping_once() -> None:
    safe = _report()
    malicious = copy.deepcopy(safe)
    malicious["published"] = True
    malicious["cutoverEligible"] = True
    malicious["productionAuthority"] = True
    malicious["status"] = "READY"
    split = _SplitViewMapping(safe, malicious)

    validated = validate_narrow_gate_diagnostic_report(
        split,
        expected_report_sha256=narrow_gate_diagnostic_report_sha256(safe),
        expected_update_baseline=_baseline(),
    )

    assert split.items_calls == 1
    assert validated["published"] is False
    assert validated["cutoverEligible"] is False
    assert validated["productionAuthority"] is False
    assert "status" not in validated


def test_validator_rejects_cyclic_or_excessively_nested_mappings_fail_closed() -> None:
    trusted = _report()
    trusted_sha256 = narrow_gate_diagnostic_report_sha256(trusted)

    cyclic = copy.deepcopy(trusted)
    cyclic["cycle"] = cyclic

    nested = copy.deepcopy(trusted)
    branch: dict[str, object] = {}
    nested["nested"] = branch
    for _ in range(70):
        child: dict[str, object] = {}
        branch["child"] = child
        branch = child

    for attack in (cyclic, nested):
        with pytest.raises(NarrowGateContractError):
            validate_narrow_gate_diagnostic_report(
                attack,
                expected_report_sha256=trusted_sha256,
                expected_update_baseline=_baseline(),
            )


def test_strict_raw_parser_rejects_noncanonical_numeric_and_duplicate_json() -> None:
    report = _report()
    canonical = _canonical(report)
    expected_sha256 = hashlib.sha256(canonical).hexdigest()
    validated = parse_and_validate_narrow_gate_diagnostic_report_bytes(
        canonical,
        expected_report_sha256=expected_sha256,
        expected_update_baseline=_baseline(),
    )
    assert validated["productionAuthority"] is False

    attacks = (
        canonical + b"\n",
        canonical.replace(
            b'"observationCount":1',
            b'"observationCount":1.0',
            1,
        ),
        canonical.replace(
            b'"observationCount":1',
            b'"observationCount":' + (b"9" * 5000),
            1,
        ),
        b'{"schema":"duplicate",' + canonical[1:],
    )
    for attack in attacks:
        with pytest.raises(NarrowGateContractError):
            parse_and_validate_narrow_gate_diagnostic_report_bytes(
                attack,
                expected_report_sha256=expected_sha256,
                expected_update_baseline=_baseline(),
            )

    deeply_nested = b'{"nested":' + (b"[" * 1000) + b"0" + (b"]" * 1000) + b"}"
    with pytest.raises(NarrowGateContractError):
        parse_and_validate_narrow_gate_diagnostic_report_bytes(
            deeply_nested,
            expected_report_sha256=expected_sha256,
            expected_update_baseline=_baseline(),
        )


def test_json_schema_is_strict_and_accepts_only_canonical_report() -> None:
    schema = json.loads(
        (
            PROJECT_ROOT / "schemas" / "kb_production_narrow_gate_report_v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    report = _report()
    validator.validate(report)

    assert len(schema["properties"]["checks"]["prefixItems"]) == 11
    assert [
        check_schema["properties"]["id"]["const"]
        for check_schema in schema["properties"]["checks"]["prefixItems"]
    ] == list(EXPECTED_IDS)
    assert schema["additionalProperties"] is False
    assert "Structural validation only" in schema["$comment"]
    assert schema["properties"]["updateBaseline"]["additionalProperties"] is (False)
    assert schema["properties"]["productionAuthority"]["const"] is False

    attacks = []
    for mutation in (
        lambda value: value.__setitem__("published", "false"),
        lambda value: value.__setitem__("productionAuthority", True),
        lambda value: value["checks"][0]["details"].__setitem__(
            "observationCount",
            "1",
        ),
        lambda value: value["checks"][0]["digests"].__setitem__(
            "unknownSha256",
            "a" * 64,
        ),
        lambda value: value["checks"][0].__setitem__(
            "id",
            "fixture.narrow",
        ),
        lambda value: value.__setitem__("mode", "vnext"),
    ):
        attack = copy.deepcopy(report)
        mutation(attack)
        _rehash(attack)
        attacks.append(attack)

    for attack in attacks:
        with pytest.raises(ValidationError):
            validator.validate(attack)

    trailing_digest = copy.deepcopy(report)
    trailing_digest["updateBaseline"]["basePointerSha256"] += "\n"
    with pytest.raises(ValidationError):
        validator.validate(trailing_digest)

    trailing_build_id = copy.deepcopy(report)
    trailing_build_id["updateBaseline"]["baseBuildId"] += "\n"
    with pytest.raises(ValidationError):
        validator.validate(trailing_build_id)

    trailing_proof = copy.deepcopy(report)
    trailing_proof["proof"] += "\n"
    with pytest.raises(ValidationError):
        validator.validate(trailing_proof)

    integral_float = copy.deepcopy(report)
    integral_float["checks"][0]["details"]["observationCount"] = 1.0
    validator.validate(integral_float)
    with pytest.raises(NarrowGateContractError):
        _validate(
            integral_float,
            expected_report_sha256=_rehash(integral_float),
        )
