from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext import (  # noqa: E402
    update_baseline as baseline_module,
)
from blueprint_translator.kb_vnext.pointer_cas import (  # noqa: E402
    PointerCASDestinationError,
    capture_current_snapshot_baseline,
)
from blueprint_translator.kb_vnext.source_manifest import (  # noqa: E402
    SourceChange,
    SourceDiff,
    SourceManifest,
    SourceRevision,
    canonical_source_diff_bytes,
    source_diff_sha256,
    source_id,
    source_manifest_binding,
)
from blueprint_translator.kb_vnext.update_baseline import (  # noqa: E402
    UpdateBaselineBlockedGap,
    build_update_baseline,
    freeze_additive_blueprint_input,
    inspect_prepublication_delta_receipt,
    stage_snapshot_from_baseline,
    validate_final_source_manifest,
)


GENERATED_AT = "2026-07-30T00:00:00+00:00"


def _revision(
    source_kind: str,
    source_uri: str,
    fingerprint: str,
    *,
    size_bytes: int = 0,
    entity_uri: str = "",
    revision_label: str = "",
) -> SourceRevision:
    return SourceRevision(
        source_id=source_id(source_kind, source_uri),
        source_kind=source_kind,
        source_uri=source_uri,
        fingerprint=fingerprint,
        size_bytes=size_bytes,
        entity_uri=entity_uri,
        revision_label=revision_label,
    )


def _manifest(*entries: SourceRevision) -> SourceManifest:
    return SourceManifest(
        entries=tuple(entries),
        generated_at=GENERATED_AT,
    )


def _base_manifest() -> SourceManifest:
    return _manifest(
        _revision(
            "SEMANTIC_INPUT",
            "semantic-input://captures",
            "1" * 64,
        )
    )


def _pointer_bytes(build_id: str, *, indent: int | None = None) -> bytes:
    return (
        json.dumps(
            {
                "buildId": build_id,
                "snapshotRelativePath": f"snapshots/{build_id}",
            },
            indent=indent,
            sort_keys=True,
        )
        + ("\n" if indent is not None else "")
    ).encode("utf-8")


def _write_snapshot(
    root: Path,
    build_id: str,
    source_manifest: SourceManifest,
) -> Path:
    snapshot = root / "snapshots" / build_id
    snapshot.mkdir(parents=True)
    (snapshot / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "ark-kb-vnext-snapshot/v1",
                "buildId": build_id,
                "incrementalUpdate": source_manifest_binding(
                    source_manifest
                ),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (snapshot / "core.sqlite").write_bytes(b"immutable-core")
    (snapshot / "cache.sqlite").write_bytes(b"immutable-cache")
    exports = snapshot / "domain_exports"
    exports.mkdir()
    (exports / "fixture.json").write_text("{}", encoding="utf-8")
    return snapshot


def _captured(
    tmp_path: Path,
    source_manifest: SourceManifest | None = None,
):
    root = tmp_path / "vnext"
    root.mkdir(parents=True)
    _write_snapshot(
        root,
        "build-a",
        source_manifest or _base_manifest(),
    )
    (root / "current.json").write_bytes(_pointer_bytes("build-a"))
    return root, capture_current_snapshot_baseline(root)


def _receipt_bytes(
    source_diff_digest: str,
    *,
    proof_digest: str = "3" * 64,
    extra: dict[str, object] | None = None,
) -> bytes:
    value: dict[str, object] = {
        "schema": "ark-kb-add-only-blueprint-delta-receipt/v2",
        "trustContext": "TEST_ONLY",
        "sourceDiffSha256": source_diff_digest,
        "published": False,
        "e4Scenario2Complete": False,
        "proof": f"delta-proof://{proof_digest}",
    }
    value.update(extra or {})
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_canonical_source_diff_has_one_strict_serializer() -> None:
    old = _base_manifest()
    new = _manifest(
        _revision(
            "SEMANTIC_INPUT",
            "semantic-input://captures",
            "2" * 64,
        )
    )
    from blueprint_translator.kb_vnext.source_manifest import (
        compare_source_manifests,
    )

    diff = compare_source_manifests(old, new)
    encoded = canonical_source_diff_bytes(diff)

    assert source_diff_sha256(diff) == hashlib.sha256(encoded).hexdigest()
    assert encoded == json.dumps(
        diff.payload(),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    with pytest.raises(ValueError, match="schema"):
        canonical_source_diff_bytes(replace(diff, schema="attacker"))


def test_canonical_source_diff_rejects_manual_order_and_identity() -> None:
    first = _revision(
        "BLUEPRINT_EVIDENCE",
        "capture://A",
        "a" * 64,
        entity_uri="/Game/A.A_C",
        revision_label="revision-a",
    )
    second = _revision(
        "BLUEPRINT_EVIDENCE",
        "capture://B",
        "b" * 64,
        entity_uri="/Game/B.B_C",
        revision_label="revision-b",
    )
    manually_reversed = SourceDiff(
        added=(
            SourceChange("ADDED", second.source_id, None, second),
            SourceChange("ADDED", first.source_id, None, first),
        )
    )
    mismatched = SourceDiff(
        added=(
            SourceChange("DELETED", first.source_id, None, first),
        )
    )

    with pytest.raises(ValueError, match="canonical order"):
        canonical_source_diff_bytes(manually_reversed)
    with pytest.raises(ValueError, match="ADDED"):
        canonical_source_diff_bytes(mismatched)


def test_source_manifest_rejects_mutable_entries() -> None:
    revision = _revision(
        "SEMANTIC_INPUT",
        "semantic-input://captures",
        "1" * 64,
    )
    with pytest.raises(ValueError, match="immutable revision tuple"):
        SourceManifest(
            entries=[revision],  # type: ignore[arg-type]
            generated_at=GENERATED_AT,
        )


def test_update_baseline_binds_raw_base_and_canonical_diff(
    tmp_path: Path,
) -> None:
    root, current = _captured(tmp_path)
    candidate = _manifest(
        _revision(
            "SEMANTIC_INPUT",
            "semantic-input://captures",
            "2" * 64,
        )
    )

    baseline = build_update_baseline(
        snapshot_root=root,
        candidate_source_manifest=candidate,
    )

    assert baseline.base_build_id == "build-a"
    assert (
        baseline.base_pointer_sha256
        == hashlib.sha256((root / "current.json").read_bytes()).hexdigest()
    )
    assert (
        baseline.base_manifest_sha256
        == hashlib.sha256(
            (current.snapshot_dir / "manifest.json").read_bytes()
        ).hexdigest()
    )
    assert (
        baseline.base_source_manifest_fingerprint
        == _base_manifest().fingerprint
    )
    assert baseline.candidate_source_manifest_fingerprint == (
        candidate.fingerprint
    )
    assert baseline.source_diff_sha256 == hashlib.sha256(
        baseline.source_diff_bytes
    ).hexdigest()
    assert baseline.evidence_class == "UNSIGNED_LOCAL_UPDATE_BASELINE"
    assert baseline.tree_validated is False
    assert baseline.production_authority is False
    assert baseline.published is False
    assert baseline.e4_scenario_2_complete is False
    assert baseline.payload()["productionAuthority"] is False
    assert baseline.payload()["treeValidated"] is False
    with pytest.raises(Exception):
        baseline.base_build_id = "attacker"  # type: ignore[misc]


def test_update_baseline_cannot_accept_manual_diff_injection(
    tmp_path: Path,
) -> None:
    root, _current = _captured(tmp_path)
    candidate = _manifest(
        _revision(
            "SEMANTIC_INPUT",
            "semantic-input://captures",
            "2" * 64,
        )
    )
    baseline = build_update_baseline(
        snapshot_root=root,
        candidate_source_manifest=candidate,
    )

    with pytest.raises(ValueError, match="source diff"):
        replace(
            baseline,
            source_diff=SourceDiff(),
            source_diff_bytes=canonical_source_diff_bytes(SourceDiff()),
            source_diff_sha256=source_diff_sha256(SourceDiff()),
        )


def test_update_baseline_rejects_forged_or_foreign_raw_base_binding(
    tmp_path: Path,
) -> None:
    root, _current = _captured(tmp_path / "first")
    _other_root, other_current = _captured(
        tmp_path / "other",
        source_manifest=_manifest(
            _revision(
                "SEMANTIC_INPUT",
                "semantic-input://captures",
                "9" * 64,
            )
        ),
    )
    baseline = build_update_baseline(
        snapshot_root=root,
        candidate_source_manifest=_base_manifest(),
    )

    with pytest.raises(PointerCASDestinationError):
        replace(baseline, current_snapshot=other_current)
    forged = replace(
        baseline.current_snapshot,
        snapshot_dir=tmp_path / "never-existed" / "build-a",
    )
    with pytest.raises(PointerCASDestinationError):
        replace(baseline, current_snapshot=forged)


def test_stage_fails_closed_until_reparse_safe_copy_is_available(
    tmp_path: Path,
) -> None:
    root, _current = _captured(tmp_path)
    baseline = build_update_baseline(
        snapshot_root=root,
        candidate_source_manifest=_base_manifest(),
    )
    destination = tmp_path / "staged"

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        stage_snapshot_from_baseline(
            baseline,
            destination=destination,
        )
    assert caught.value.gap_code == "REPARSE_SAFE_STAGING_COPY_UNAVAILABLE"
    assert not destination.exists()


def test_freeze_additive_input_fails_before_any_filesystem_side_effect(
    tmp_path: Path,
) -> None:
    root, _current = _captured(tmp_path / "baseline")
    baseline = build_update_baseline(
        snapshot_root=root,
        candidate_source_manifest=_base_manifest(),
    )
    capture_root = tmp_path / "missing-captures"
    quarantine_root = tmp_path / "must-not-exist"

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        freeze_additive_blueprint_input(
            baseline,
            capture_root=capture_root,
            quarantine_root=quarantine_root,
        )

    assert caught.value.gap_code == (
        "REPARSE_SAFE_ADDITIVE_QUARANTINE_UNAVAILABLE"
    )
    assert not capture_root.exists()
    assert not quarantine_root.exists()


def test_final_source_rescan_requires_exact_payload(
    tmp_path: Path,
) -> None:
    root, _current = _captured(tmp_path)
    candidate = _manifest(
        _revision(
            "SEMANTIC_INPUT",
            "semantic-input://captures",
            "2" * 64,
        )
    )
    baseline = build_update_baseline(
        snapshot_root=root,
        candidate_source_manifest=candidate,
    )

    assert validate_final_source_manifest(baseline, candidate) is candidate
    same_fingerprint_new_timestamp = replace(
        candidate,
        generated_at="2026-07-30T00:00:01+00:00",
    )
    with pytest.raises(UpdateBaselineBlockedGap, match="changed"):
        validate_final_source_manifest(
            baseline,
            same_fingerprint_new_timestamp,
        )


def test_receipt_inspection_requires_raw_oob_before_internal_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _current = _captured(tmp_path)
    candidate = _manifest(
        _revision(
            "SEMANTIC_INPUT",
            "semantic-input://captures",
            "2" * 64,
        )
    )
    baseline = build_update_baseline(
        snapshot_root=root,
        candidate_source_manifest=candidate,
    )
    raw = _receipt_bytes(baseline.source_diff_sha256)
    expected_raw = hashlib.sha256(raw).hexdigest()
    called: dict[str, object] = {}

    def validate(
        receipt: dict[str, object],
        *,
        expected_receipt_sha256: str,
    ):
        called["expectedContentSha256"] = expected_receipt_sha256
        return MappingProxyType(dict(receipt))

    monkeypatch.setattr(
        baseline_module,
        "validate_add_only_delta_receipt",
        validate,
    )
    inspection = inspect_prepublication_delta_receipt(
        baseline,
        receipt_bytes=raw,
        expected_receipt_raw_sha256=expected_raw,
    )

    assert called["expectedContentSha256"] == "3" * 64
    assert inspection.expected_receipt_raw_sha256 == expected_raw
    assert inspection.receipt_artifact_sha256 == expected_raw
    assert inspection.source_diff_sha256 == baseline.source_diff_sha256
    assert inspection.trust_context == "TEST_ONLY"
    assert inspection.base_binding_verified is False
    assert inspection.production_authority is False
    assert inspection.published is False
    assert inspection.e4_scenario_2_complete is False
    assert inspection.schema == (
        "ark-kb-prepublication-delta-inspection/v1"
    )
    assert inspection.evidence_class == (
        "UNSIGNED_LOCAL_PREPUBLICATION_INSPECTION"
    )
    payload = inspection.payload()
    assert payload["baseBindingVerified"] is False
    assert payload["productionAuthority"] is False
    assert set(payload) == {
        "schema",
        "evidenceClass",
        "sourceDiffSha256",
        "expectedReceiptRawSha256",
        "receiptArtifactSha256",
        "receiptContentSha256",
        "trustContext",
        "baseBindingVerified",
        "productionAuthority",
        "published",
        "e4Scenario2Complete",
    }
    with pytest.raises(ValueError, match="inspection contract"):
        replace(inspection, base_binding_verified=True)
    with pytest.raises(ValueError, match="inspection contract"):
        replace(inspection, production_authority=True)


def test_receipt_self_rehash_missing_oob_and_replay_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _current = _captured(tmp_path)
    candidate = _manifest(
        _revision(
            "SEMANTIC_INPUT",
            "semantic-input://captures",
            "2" * 64,
        )
    )
    baseline = build_update_baseline(
        snapshot_root=root,
        candidate_source_manifest=candidate,
    )
    original = _receipt_bytes(baseline.source_diff_sha256)
    expected_raw = hashlib.sha256(original).hexdigest()
    attacked = _receipt_bytes(
        baseline.source_diff_sha256,
        proof_digest="4" * 64,
        extra={"attacker": True},
    )
    with pytest.raises(UpdateBaselineBlockedGap) as missing:
        inspect_prepublication_delta_receipt(
            baseline,
            receipt_bytes=original,
            expected_receipt_raw_sha256="",
        )
    assert missing.value.gap_code == (
        "MISSING_OUT_OF_BAND_DELTA_RECEIPT_SHA256"
    )
    with pytest.raises(UpdateBaselineBlockedGap) as self_rehash:
        inspect_prepublication_delta_receipt(
            baseline,
            receipt_bytes=attacked,
            expected_receipt_raw_sha256=expected_raw,
        )
    assert self_rehash.value.gap_code == (
        "OUT_OF_BAND_DELTA_RECEIPT_SHA256_MISMATCH"
    )

    replayed = _receipt_bytes("5" * 64)
    replayed_sha = hashlib.sha256(replayed).hexdigest()
    monkeypatch.setattr(
        baseline_module,
        "validate_add_only_delta_receipt",
        lambda receipt, *, expected_receipt_sha256: MappingProxyType(
            dict(receipt)
        ),
    )
    with pytest.raises(UpdateBaselineBlockedGap) as replay:
        inspect_prepublication_delta_receipt(
            baseline,
            receipt_bytes=replayed,
            expected_receipt_raw_sha256=replayed_sha,
        )
    assert replay.value.gap_code == "DELTA_RECEIPT_SOURCE_DIFF_MISMATCH"


def test_receipt_strict_json_unknown_fields_and_test_only_production_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _current = _captured(tmp_path)
    baseline = build_update_baseline(
        snapshot_root=root,
        candidate_source_manifest=_base_manifest(),
    )
    duplicate = (
        b'{"schema":"x","schema":"y","proof":"delta-proof://'
        + b"3" * 64
        + b'"}'
    )
    floating = b'{"numeric":1.0}'
    deeply_nested = (
        b'{"nested":' + (b"[" * 2000) + b"0" + (b"]" * 2000) + b"}"
    )
    for malformed in (duplicate, floating, deeply_nested):
        with pytest.raises(UpdateBaselineBlockedGap) as malformed_error:
            inspect_prepublication_delta_receipt(
                baseline,
                receipt_bytes=malformed,
                expected_receipt_raw_sha256=hashlib.sha256(
                    malformed
                ).hexdigest(),
            )
        assert malformed_error.value.gap_code == (
            "DELTA_RECEIPT_ARTIFACT_INVALID"
        )

    unknown = _receipt_bytes(
        baseline.source_diff_sha256,
        extra={"unknown": True},
    )
    with pytest.raises(UpdateBaselineBlockedGap):
        inspect_prepublication_delta_receipt(
            baseline,
            receipt_bytes=unknown,
            expected_receipt_raw_sha256=hashlib.sha256(
                unknown
            ).hexdigest(),
        )

    valid = _receipt_bytes(baseline.source_diff_sha256)
    monkeypatch.setattr(
        baseline_module,
        "validate_add_only_delta_receipt",
        lambda receipt, *, expected_receipt_sha256: MappingProxyType(
            dict(receipt)
        ),
    )
    with pytest.raises(UpdateBaselineBlockedGap) as production:
        inspect_prepublication_delta_receipt(
            baseline,
            receipt_bytes=valid,
            expected_receipt_raw_sha256=hashlib.sha256(valid).hexdigest(),
            production=True,
        )
    assert production.value.gap_code == (
        "PRODUCTION_ARTIFACT_AUTHORIZATION_REQUIRED"
    )
    with pytest.raises(UpdateBaselineBlockedGap) as ambiguous:
        inspect_prepublication_delta_receipt(
            baseline,
            receipt_bytes=valid,
            expected_receipt_raw_sha256=hashlib.sha256(valid).hexdigest(),
            production=0,  # type: ignore[arg-type]
        )
    assert ambiguous.value.gap_code == "UPDATE_BASELINE_CONTRACT_INVALID"


def test_receipt_artifact_has_a_hard_size_bound(
    tmp_path: Path,
) -> None:
    root, _current = _captured(tmp_path)
    baseline = build_update_baseline(
        snapshot_root=root,
        candidate_source_manifest=_base_manifest(),
    )
    oversized = b"{" + b" " * (
        baseline_module.MAX_DELTA_RECEIPT_BYTES + 1
    )
    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        inspect_prepublication_delta_receipt(
            baseline,
            receipt_bytes=oversized,
            expected_receipt_raw_sha256=hashlib.sha256(
                oversized
            ).hexdigest(),
        )
    assert caught.value.gap_code == "DELTA_RECEIPT_ARTIFACT_INVALID"
