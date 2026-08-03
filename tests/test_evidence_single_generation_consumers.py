from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import import_captures_to_knowledge_dbs as knowledge_import  # noqa: E402
import read_priority_assets  # noqa: E402
import review_processed_asset_quality  # noqa: E402
import blueprint_translator.evidence_repository as evidence_repository_module  # noqa: E402
from blueprint_translator.evidence_repository import (  # noqa: E402
    ResolvedEvidenceState,
    resolve_asset_evidence_state,
)
from blueprint_translator.evidence_writer import (  # noqa: E402
    write_evidence_artifacts_from_payload,
)


def _payload(name: str, *, graph_count: int) -> dict[str, object]:
    graphs: list[dict[str, object]] = []
    for export_index in range(graph_count):
        graph_name = f"GenerationGraph_{export_index}"
        graphs.append(
            {
                "graph": graph_name,
                "graph_type": "Function",
                "export_index": export_index,
                "status": "complete",
                "confidence": "high",
                "payload": {
                    "metadata": {
                        "asset_name": name,
                        "graph_name": graph_name,
                        "graph_type": "Function",
                        "uasset_export_index": export_index,
                        "uasset_read_status": "complete",
                        "confidence": "high",
                    },
                    "nodes": [],
                },
            }
        )
    return {
        "asset_name": name,
        "asset_path": f"/Game/Test/{name}.{name}",
        "graphs": graphs,
        "class_defaults": {"variables": {}},
    }


def _make_first_generation(root: Path, name: str) -> tuple[Path, Path, Path]:
    asset_dir = root / "captures" / name
    source_a = root / "sources" / f"{name}-a.uasset"
    source_b = root / "sources" / f"{name}-b.uasset"
    source_a.parent.mkdir(parents=True)
    source_a.write_bytes(b"single-generation-fixture-a")
    source_b.write_bytes(b"single-generation-fixture-b")
    payload = _payload(name, graph_count=1)
    write_evidence_artifacts_from_payload(
        str(payload["asset_path"]),
        source_a,
        payload,
        asset_dir,
    )
    return asset_dir, source_a, source_b


def _resolve_a_then_publish_b(
    asset_dir: Path,
    source_b: Path,
    state_box: dict[str, ResolvedEvidenceState],
):
    advanced = False

    def resolve(path: str | Path, *, allow_stale: bool = False) -> ResolvedEvidenceState:
        nonlocal advanced
        state_a = resolve_asset_evidence_state(path, allow_stale=allow_stale)
        if not advanced:
            advanced = True
            state_box["a"] = state_a
            payload_b = _payload(asset_dir.name, graph_count=2)
            write_evidence_artifacts_from_payload(
                str(payload_b["asset_path"]),
                source_b,
                payload_b,
                asset_dir,
            )
            state_box["b"] = resolve_asset_evidence_state(
                asset_dir,
                allow_stale=allow_stale,
            )
            if state_box["a"].database_path == state_box["b"].database_path:
                raise AssertionError("fixture did not advance the current revision")
        return state_a

    return resolve


class EvidenceSingleGenerationConsumerTests(unittest.TestCase):
    def test_business_import_keeps_queries_on_the_revision_it_resolved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir, _source_a, source_b = _make_first_generation(
                root,
                "ImportGenerationFixture",
            )
            states: dict[str, ResolvedEvidenceState] = {}

            with patch.object(
                evidence_repository_module,
                "resolve_asset_evidence_state",
                side_effect=_resolve_a_then_publish_b(asset_dir, source_b, states),
            ):
                capture = knowledge_import.load_capture(asset_dir)

        self.assertEqual(capture["paths"]["evidence_store"], states["a"].database_path)
        self.assertEqual(
            capture["graph_nodes"]["revision_id"],
            states["a"].database_path.parent.name,
        )
        self.assertEqual(capture["graph_nodes"]["graph_count"], 1)
        self.assertNotEqual(states["a"].database_path, states["b"].database_path)

    def test_priority_reader_keeps_queries_on_the_revision_it_resolved(self):
        name = "PriorityGenerationFixture"
        object_path = f"/Game/Test/{name}.{name}"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir, source_a, source_b = _make_first_generation(root, name)
            states: dict[str, ResolvedEvidenceState] = {}

            with (
                patch.object(read_priority_assets, "CAPTURE_ROOT", asset_dir.parent),
                patch.object(
                    read_priority_assets,
                    "object_path_to_uasset_path",
                    return_value=(source_a, []),
                ),
                patch.object(
                    read_priority_assets,
                    "ledger_db_path",
                    return_value=root / "ledger.sqlite",
                ),
                patch.object(
                    read_priority_assets,
                    "processed_current_for_path",
                    return_value=False,
                ),
                patch.object(
                    read_priority_assets,
                    "resolve_asset_evidence_state",
                    side_effect=_resolve_a_then_publish_b(asset_dir, source_b, states),
                ),
            ):
                result = read_priority_assets.read_asset(
                    object_path,
                    max_graphs=0,
                    analyze=False,
                    report_level="standard",
                    force=False,
                )

        self.assertEqual(result["revision_id"], states["a"].database_path.parent.name)
        self.assertEqual(result["graph_count"], 1)
        self.assertNotEqual(states["a"].database_path, states["b"].database_path)

    def test_processed_review_keeps_queries_on_the_revision_it_resolved(self):
        name = "ReviewGenerationFixture"
        object_path = f"/Game/Test/{name}.{name}"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir, _source_a, source_b = _make_first_generation(root, name)
            states: dict[str, ResolvedEvidenceState] = {}
            resolve_with_advance = _resolve_a_then_publish_b(asset_dir, source_b, states)

            with patch.object(
                review_processed_asset_quality,
                "resolve_indexed_evidence",
                side_effect=lambda path: resolve_with_advance(path),
            ):
                result = review_processed_asset_quality.row_to_result(
                    {
                        "object_path": object_path,
                        "asset_name": name,
                        "capture_dir": str(asset_dir),
                        "read_status": "read",
                    },
                    analyze_missing=False,
                    analyze_all=False,
                    report_level="standard",
                )

        self.assertEqual(result["graph_count"], 1)
        self.assertEqual(result["status_counts"], {"complete": 1})
        self.assertNotEqual(states["a"].database_path, states["b"].database_path)


if __name__ == "__main__":
    unittest.main()
