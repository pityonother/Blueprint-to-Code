import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_ark_knowledge_base as kb  # noqa: E402
import read_priority_assets  # noqa: E402
import review_processed_asset_quality  # noqa: E402
from blueprint_translator.evidence_schema import ensure_evidence_schema  # noqa: E402


def asset(name: str, asset_type: str, **extra):
    return {
        "asset_name": name,
        "object_path": extra.pop("object_path", f"/Game/Test/{name}.{name}"),
        "relative_path": extra.pop("relative_path", f"Test/{name}.uasset"),
        "asset_type": asset_type,
        "domain": extra.pop("domain", "test"),
        "captured": extra.pop("captured", False),
        "has_uexp": extra.pop("has_uexp", False),
        **extra,
    }


def write_indexed_asset(asset_dir: Path, statuses: list[str], *, link_observation_count: int = 0) -> None:
    database_path = asset_dir / "evidence" / "evidence.sqlite"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        ensure_evidence_schema(connection)
        asset_id = "asset-priority-v2"
        revision_id = "revision-priority-v2"
        connection.execute(
            "INSERT INTO asset_revisions("
            "revision_id, asset_id, asset_name, object_path, source_fingerprint, "
            "parser_version, schema_version, generated_at, uasset_path"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revision_id,
                asset_id,
                asset_dir.name,
                f"/Game/Test/{asset_dir.name}.{asset_dir.name}",
                "fixture-fingerprint",
                "fixture-parser",
                "ark.blueprint.evidence.v2",
                "2026-07-19T00:00:00+00:00",
                "",
            ),
        )
        graph_refs: list[str] = []
        for export_index, status in enumerate(statuses):
            graph_ref = f"bp://{asset_id}@{revision_id}/g/{export_index}"
            graph_refs.append(graph_ref)
            connection.execute(
                "INSERT INTO graphs("
                "graph_ref, revision_id, export_index, name, graph_type, status, confidence"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (graph_ref, revision_id, export_index, f"Graph_{export_index}", "Function", status, "high"),
            )
        for ordinal in range(link_observation_count):
            connection.execute(
                "INSERT INTO edge_observations(observation_ref, graph_ref) VALUES (?, ?)",
                (f"{graph_refs[0]}/observation/{ordinal}", graph_refs[0]),
            )
        connection.commit()
    finally:
        connection.close()
    output_dir = asset_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "agent_index.md").write_text("# Indexed evidence\n", encoding="utf-8")


class PriorityTargetTests(unittest.TestCase):
    def test_existing_v2_asset_uses_real_link_count_and_does_not_require_legacy_reports(self):
        object_path = "/Game/Test/IndexedOnly.IndexedOnly"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture_root = root / "captures"
            asset_dir = capture_root / "IndexedOnly"
            write_indexed_asset(asset_dir, ["complete", "complete"], link_observation_count=7)
            uasset_path = root / "IndexedOnly.uasset"

            with (
                patch.object(read_priority_assets, "CAPTURE_ROOT", capture_root),
                patch.object(read_priority_assets, "object_path_to_uasset_path", return_value=(uasset_path, [])),
                patch.object(read_priority_assets, "ledger_db_path", return_value=root / "ledger.sqlite"),
                patch.object(read_priority_assets, "processed_current_for_path", return_value=False),
            ):
                result = read_priority_assets.read_asset(
                    object_path,
                    max_graphs=0,
                    analyze=True,
                    report_level="standard",
                    force=False,
                )
            quality = read_priority_assets.evaluate_asset_quality(result)

        self.assertEqual(result["status"], "existing_indexed")
        self.assertEqual(result["link_count"], 7)
        self.assertEqual(result["status_counts"], {"complete": 2})
        self.assertTrue(quality["report_files"]["evidence_store"])
        self.assertTrue(quality["report_files"]["agent_index"])
        self.assertNotIn("read_failed", quality["quality_flags"])
        self.assertNotIn("reports_missing", quality["quality_flags"])
        self.assertEqual(quality["verdict"], "good")

    def test_complete_legacy_reports_remain_valid_when_index_publication_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset_dir = Path(tmp) / "DualFallback"
            write_indexed_asset(asset_dir, ["complete"])
            output_dir = asset_dir / "output"
            (output_dir / "agent_index.md").unlink()
            (output_dir / "behavior_summary.md").write_text("# Behavior\n", encoding="utf-8")
            (output_dir / "diagnostics_report.md").write_text("# Diagnostics\n", encoding="utf-8")
            (output_dir / "capture_quality_report.md").write_text("# Capture quality\n", encoding="utf-8")
            (output_dir / "asset_report.md").write_text("# Asset\n", encoding="utf-8")

            quality = read_priority_assets.evaluate_asset_quality(
                {
                    "asset_name": "DualFallback",
                    "asset_path": "/Game/Test/DualFallback.DualFallback",
                    "asset_dir": str(asset_dir),
                    "status": "read",
                    "graph_count": 1,
                    "status_counts": {"complete": 1},
                }
            )

        self.assertNotIn("reports_missing", quality["quality_flags"])

    def test_processed_v2_review_loads_graph_statuses_and_link_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset_dir = Path(tmp) / "ReviewedIndexed"
            write_indexed_asset(asset_dir, ["complete", "heuristic"], link_observation_count=9)
            row = {
                "object_path": "/Game/Test/ReviewedIndexed.ReviewedIndexed",
                "asset_name": "ReviewedIndexed",
                "capture_dir": str(asset_dir),
                "read_status": "read",
            }

            with patch.object(
                review_processed_asset_quality,
                "analyze_asset",
                side_effect=AssertionError("indexed reports should satisfy analyze-missing"),
            ):
                result = review_processed_asset_quality.row_to_result(
                    row,
                    analyze_missing=True,
                    analyze_all=False,
                    report_level="standard",
                )

        self.assertEqual(result["graph_count"], 2)
        self.assertEqual(result["link_count"], 9)
        self.assertEqual(result["status_counts"], {"complete": 1, "heuristic": 1})

    def test_primal_item_include_names_do_not_become_only_allow_list(self):
        priority = kb.build_priority_targets(
            {
                "assets": [
                    asset("PrimalItemResource_TestCrystal", "primal_item_blueprint"),
                    asset("PrimalItemSaddle_Test", "primal_item_blueprint"),
                ]
            }
        )

        names = {
            item["asset_name"]
            for item in priority["groups"]["primal_item_blueprint"]["candidates"]
        }

        self.assertIn("PrimalItemResource_TestCrystal", names)
        self.assertNotIn("PrimalItemSaddle_Test", names)

    def test_fixed_priority_asset_can_rerun_when_processed_or_failed(self):
        feather_path = (
            "/Game/ASA/Dinos/Gigantoraptor/PrimalItemResource_GigantoraptorFeather."
            "PrimalItemResource_GigantoraptorFeather"
        )
        treasure_path = (
            "/Game/ASA/Dinos/ShoulderDragon/Chest/PrimalItem_TreasureMap_ShoulderDragon."
            "PrimalItem_TreasureMap_ShoulderDragon"
        )
        priority = kb.build_priority_targets(
            {
                "assets": [
                    asset(
                        "PrimalItem_TreasureMap_ShoulderDragon",
                        "primal_item_blueprint",
                        object_path=treasure_path,
                        relative_path="ASA/Dinos/ShoulderDragon/Chest/PrimalItem_TreasureMap_ShoulderDragon.uasset",
                    ),
                    asset(
                        "PrimalItemResource_GigantoraptorFeather",
                        "primal_item_blueprint",
                        object_path=feather_path,
                        relative_path="ASA/Dinos/Gigantoraptor/PrimalItemResource_GigantoraptorFeather.uasset",
                        processed_current=True,
                        failed_current=True,
                        failure_count=2,
                    )
                ]
            }
        )

        group = priority["groups"]["primal_item_blueprint"]
        first_batch_names = [item["asset_name"] for item in group["first_batch"]]

        self.assertIn(feather_path, priority["deep_read_queue"])
        self.assertIn(treasure_path, priority["deep_read_queue"])
        self.assertIn("PrimalItemResource_GigantoraptorFeather", first_batch_names)
        self.assertIn("PrimalItem_TreasureMap_ShoulderDragon", first_batch_names)
        self.assertEqual(group["failed_candidates"], [])
        feather = next(item for item in group["first_batch"] if item["asset_name"] == "PrimalItemResource_GigantoraptorFeather")
        self.assertGreaterEqual(feather["score"], 1000)
        self.assertTrue(feather["force_include"])

    def test_related_priority_asset_can_bypass_exclude_and_processed_skip(self):
        related_path = "/Game/Test/PrimalItemSaddle_Related.PrimalItemSaddle_Related"
        priority = kb.build_priority_targets(
            {
                "assets": [
                    asset(
                        "PrimalItemSaddle_Related",
                        "primal_item_blueprint",
                        object_path=related_path,
                        processed_current=True,
                    )
                ]
            },
            related_priority={
                "assets": {
                    related_path: {
                        "object_path": related_path,
                        "asset_name": "PrimalItemSaddle_Related",
                        "sources": [
                            {
                                "source_type": "formula_candidates.next_probe",
                                "source_detail": "TreasureMap",
                            }
                        ],
                    }
                }
            },
        )

        group = priority["groups"]["primal_item_blueprint"]
        first_batch = group["first_batch"]

        self.assertIn(related_path, priority["deep_read_queue"])
        self.assertEqual(len(first_batch), 1)
        self.assertTrue(first_batch[0]["force_include"])
        self.assertTrue(first_batch[0]["related_priority"])
        self.assertIn("related asset probe", first_batch[0]["reasons"])
        self.assertEqual(priority["related_priority"]["matched_asset_count"], 1)

    def test_collect_related_priority_sources_from_formulas_and_reference_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            formula_dir = root / "captures" / "SourceAsset" / "output"
            formula_dir.mkdir(parents=True)
            (formula_dir / "formula_candidates.json").write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "next_probe": [
                                    {
                                        "object_path": "/Game/Test/Buff_Related.Buff_Related_C",
                                    }
                                ]
                            }
                        ],
                        "unresolved_formulas": [
                            {
                                "required_next_probe": [
                                    "/Game/Test/Loot_Related"
                                ]
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            db_dir = root / "db"
            db_dir.mkdir()
            connection = sqlite3.connect(db_dir / "primal_items.sqlite")
            try:
                connection.execute(
                    """
                    CREATE TABLE item_references (
                        object_path TEXT NOT NULL,
                        reference_path TEXT NOT NULL,
                        reference_type TEXT NOT NULL DEFAULT '',
                        source_property TEXT NOT NULL DEFAULT '',
                        confidence TEXT NOT NULL DEFAULT 'unknown'
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO item_references (
                        object_path, reference_path, reference_type, source_property, confidence
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "/Game/Test/Source.Source",
                        "/Game/Test/Item_Related.Item_Related",
                        "object",
                        "TestRef",
                        "medium",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            related = kb.collect_related_priority_sources(root / "captures", db_dir)

        assets = related["assets"]
        self.assertIn("/Game/Test/Buff_Related.Buff_Related", assets)
        self.assertIn("/Game/Test/Loot_Related.Loot_Related", assets)
        self.assertIn("/Game/Test/Item_Related.Item_Related", assets)
        self.assertEqual(related["path_count"], 3)


if __name__ == "__main__":
    unittest.main()
