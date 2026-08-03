from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from blueprint_translator.evidence_publication import (  # noqa: E402
    migrate_v2_evidence_to_v3,
)
from blueprint_translator.evidence_repository import (  # noqa: E402
    resolve_asset_evidence_state,
)
from blueprint_translator.evidence_revision import (  # noqa: E402
    EvidenceArtifactInvalid,
)
from blueprint_translator.evidence_writer import (  # noqa: E402
    write_evidence_artifacts_from_payload,
)
from blueprint_translator.kb_vnext import snapshot  # noqa: E402
from blueprint_translator.kb_vnext import source_manifest  # noqa: E402


def _payload(name: str) -> dict[str, object]:
    return {
        "asset_name": name,
        "asset_path": f"/Game/Test/{name}.{name}",
        "graphs": [
            {
                "graph": "EventGraph",
                "graph_type": "EventGraph",
                "export_index": 1,
                "status": "complete",
                "confidence": "high",
                "node_count": 0,
                "pin_count": 0,
                "link_count": 0,
                "coverage": {},
                "warnings": [],
                "payload": {
                    "metadata": {
                        "asset_name": name,
                        "graph_name": "EventGraph",
                        "graph_type": "EventGraph",
                        "uasset_export_index": 1,
                        "uasset_read_status": "complete",
                        "confidence": "high",
                    },
                    "nodes": [],
                },
            }
        ],
        "class_defaults": {"variables": {}},
    }


def _capture(capture_root: Path, name: str, *, prune_v2: bool) -> Path:
    asset_dir = capture_root / name
    source = asset_dir / f"{name}.uasset"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"kb-live-evidence-v3-fixture")
    write_evidence_artifacts_from_payload(
        f"/Game/Test/{name}.{name}",
        source,
        _payload(name),
        asset_dir,
        publish_v3=False,
    )
    migrate_v2_evidence_to_v3(asset_dir, prune_v2=prune_v2)
    return asset_dir


def _semantic_hashes() -> dict[str, str]:
    return {
        key: hashlib.sha256(key.encode("utf-8")).hexdigest()
        for key in source_manifest.SNAPSHOT_SEMANTIC_INPUT_KEYS
    }


def test_live_resolver_reads_pruned_v3_and_reports_authority(tmp_path: Path) -> None:
    capture_root = tmp_path / "captures"
    asset_dir = _capture(capture_root, "BP_Current", prune_v2=True)

    states = source_manifest.resolve_live_capture_evidence_states(capture_root)

    assert len(states) == 1
    state = states[0]
    assert state.asset_dir == asset_dir.resolve()
    assert state.source_kind == "INDEXED_V3_CURRENT"
    assert state.release_authority is True
    assert state.migration_required is False
    assert state.database_path.parent.parent.name == "revisions"
    assert not (asset_dir / "evidence" / "evidence.sqlite").exists()


def test_live_resolver_fails_closed_on_tampered_current_with_v2_present(
    tmp_path: Path,
) -> None:
    capture_root = tmp_path / "captures"
    asset_dir = _capture(capture_root, "BP_Tampered", prune_v2=False)
    pointer = asset_dir / "evidence" / "current.json"
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    payload["manifestSha256"] = "0" * 64
    pointer.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvidenceArtifactInvalid):
        source_manifest.resolve_live_capture_evidence_states(capture_root)


def test_source_scan_and_snapshot_digest_follow_pruned_v3_current(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    capture_root = tmp_path / "captures"
    asset_dir = _capture(capture_root, "BP_Scanned", prune_v2=True)
    monkeypatch.setattr(
        source_manifest,
        "load_native_evidence_corpus",
        lambda _root: SimpleNamespace(evidence_sets=()),
    )

    manifest = source_manifest.scan_source_manifest(
        semantic_input_hashes=_semantic_hashes(),
        capture_root=capture_root,
        native_root=tmp_path / "native",
        runtime_root=tmp_path / "runtime",
        generated_at="2026-08-03T00:00:00+00:00",
    )
    blueprint = [
        entry
        for entry in manifest.entries
        if entry.source_kind == "BLUEPRINT_EVIDENCE"
    ]
    baseline = snapshot._capture_semantic_inputs_sha256(capture_root)

    assert len(blueprint) == 1
    assert blueprint[0].source_uri == "capture://BP_Scanned"
    assert blueprint[0].entity_uri == "/Game/Test/BP_Scanned.BP_Scanned"
    assert len(baseline) == 64

    pointer = json.loads(
        (asset_dir / "evidence" / "current.json").read_text(encoding="utf-8")
    )
    revision_dir = asset_dir / "evidence" / "revisions" / pointer["revisionId"]
    (revision_dir / "agent_index.md").write_text("# tampered\n", encoding="utf-8")

    with pytest.raises(EvidenceArtifactInvalid):
        snapshot._capture_semantic_inputs_sha256(capture_root)


def test_snapshot_digest_uses_manifest_and_index_bytes_from_resolved_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    capture_root = tmp_path / "captures"
    asset_dir = _capture(capture_root, "BP_BoundDigest", prune_v2=True)
    baseline = snapshot._capture_semantic_inputs_sha256(capture_root)
    real_resolve = snapshot.resolve_live_capture_evidence_states

    def resolve_then_replace(root: Path):
        states = real_resolve(root)
        state = states[0]
        state.manifest_path.write_text(
            json.dumps({"objectPath": "/Game/Tampered.Tampered"}),
            encoding="utf-8",
        )
        state.agent_index_path.write_text(
            "# tampered after resolution\n",
            encoding="utf-8",
        )
        return states

    monkeypatch.setattr(
        snapshot,
        "resolve_live_capture_evidence_states",
        resolve_then_replace,
    )

    observed = snapshot._capture_semantic_inputs_sha256(capture_root)

    assert observed == baseline
    assert asset_dir.is_dir()


def test_missing_capture_root_is_empty_but_existing_non_directory_is_rejected(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing-captures"
    assert len(snapshot._capture_semantic_inputs_sha256(missing)) == 64

    not_a_directory = tmp_path / "captures-file"
    not_a_directory.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ValueError, match="real directory"):
        snapshot._capture_semantic_inputs_sha256(not_a_directory)


def test_live_fingerprint_binds_publication_metadata_and_all_artifacts(
    tmp_path: Path,
) -> None:
    capture_root = tmp_path / "captures"
    asset_dir = _capture(capture_root, "BP_Fingerprint", prune_v2=True)
    state = resolve_asset_evidence_state(asset_dir)
    baseline = source_manifest.live_capture_evidence_fingerprint(state)

    variants = (
        replace(state, freshness_status="STALE"),
        replace(state, pointer_sha256="f" * 64),
        replace(state, manifest_content_sha256="e" * 64),
        replace(state, agent_index_sha256="d" * 64),
        replace(state, database_sha256="c" * 64),
    )
    assert all(
        source_manifest.live_capture_evidence_fingerprint(variant) != baseline
        for variant in variants
    )


def test_capture_identity_rejects_database_bytes_swapped_after_resolution(
    tmp_path: Path,
) -> None:
    capture_root = tmp_path / "captures"
    asset_dir = _capture(capture_root, "BP_Swapped", prune_v2=True)
    state = resolve_asset_evidence_state(asset_dir)
    state.database_path.write_bytes(state.database_path.read_bytes() + b"tampered")

    with pytest.raises(
        ValueError,
        match=r"(?i)((size|hash).*(drifted|differs)|manifest)",
    ):
        source_manifest._capture_identity(state)
