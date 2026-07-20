from __future__ import annotations

import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_ark_knowledge_base as knowledge_builder  # noqa: E402
import import_captures_to_knowledge_dbs as knowledge_import  # noqa: E402
from blueprint_translator.evidence_writer import write_evidence_artifacts_from_payload  # noqa: E402


def _make_indexed_capture(root: Path) -> Path:
    asset_dir = root / "IndexedKnowledgeAsset"
    graph_payload = {
        "metadata": {
            "asset_name": "IndexedKnowledgeAsset",
            "graph_name": "EventGraph",
            "graph_type": "EventGraph",
            "uasset_export_index": 7,
            "uasset_read_status": "partial",
            "confidence": "medium",
        },
        "nodes": [
            {
                "package_index": 11,
                "name": "K2Node_CallFunction_0",
                "label": "ExternalGameplayCall",
                "class_name": "K2Node_CallFunction",
                "node_type": "K2Node_CallFunction",
                "function": "ExternalGameplayCall",
                "source": "uasset_binary",
                "confidence": "medium",
                "pins": [],
                "properties": {},
            }
        ],
    }
    payload = {
        "asset_path": "/Game/Test/IndexedKnowledgeAsset.IndexedKnowledgeAsset",
        "asset_name": "IndexedKnowledgeAsset",
        "class_defaults": {
            "variables": {
                "ConfirmedValue": {
                    "value": 2.0,
                    "type": "FloatProperty",
                    "source": "uasset_cdo",
                    "confidence": "high",
                },
                "StoredXPRewards": {
                    "value": [],
                    "type": "ArrayProperty",
                    "source": "uasset_cdo",
                    "confidence": "low",
                    "array_parse": {"parsed": False, "raw_size": 96},
                },
            }
        },
        "graphs": [
            {
                "graph": "EventGraph",
                "graph_type": "EventGraph",
                "export_index": 7,
                "status": "partial",
                "confidence": "medium",
                "failure_categories": ["missing_target_pin_id"],
                "coverage": {"node_pin_coverage": 0.0},
                "warnings": ["target pin was not recovered"],
                "payload": graph_payload,
            }
        ],
    }
    write_evidence_artifacts_from_payload(
        payload["asset_path"],
        None,
        payload,
        asset_dir,
    )
    return asset_dir


def _make_gap_only_indexed_capture(root: Path) -> Path:
    asset_dir = root / "GapOnlyAsset"
    write_evidence_artifacts_from_payload(
        "/Game/Test/GapOnlyAsset.GapOnlyAsset",
        None,
        {
            "asset_path": "/Game/Test/GapOnlyAsset.GapOnlyAsset",
            "asset_name": "GapOnlyAsset",
            "class_defaults": {
                "variables": {
                    "UnparsedEntries": {
                        "value": [],
                        "type": "ArrayProperty",
                        "source": "uasset_cdo",
                        "confidence": "low",
                        "array_parse": {"parsed": False, "raw_size": 48},
                    }
                }
            },
            "graphs": [],
        },
        asset_dir,
    )
    return asset_dir


def _make_lionfish_scale_gap_capture(root: Path) -> Path:
    asset_dir = root / "LionfishScaleGapAsset"
    write_evidence_artifacts_from_payload(
        "/Game/Test/LionfishScaleGapAsset.LionfishScaleGapAsset",
        None,
        {
            "asset_path": "/Game/Test/LionfishScaleGapAsset.LionfishScaleGapAsset",
            "asset_name": "LionfishScaleGapAsset",
            "class_defaults": {"variables": {}},
            "graphs": [],
        },
        asset_dir,
    )
    database_path = asset_dir / "evidence" / "evidence.sqlite"
    connection = sqlite3.connect(database_path)
    try:
        asset_id, revision_id = connection.execute(
            "SELECT asset_id, revision_id FROM asset_revisions LIMIT 1"
        ).fetchone()
        scope_ref = f"bp://{asset_id}@{revision_id}"
        rows = []
        for index in range(26_461):
            status = "NOT_RECOVERED" if index < 26_460 else "SOURCE_NOT_AVAILABLE"
            reason = "lionfish_bulk_gap" if index < 26_460 else "rare_native_gap"
            rows.append(
                (
                    f"{scope_ref}/diagnostic/synthetic-{index:05d}",
                    revision_id,
                    "asset",
                    scope_ref,
                    status,
                    reason,
                    "warning",
                    f"Synthetic gap {index}",
                    f"Synthetic gap detail {index}",
                    "Inspect the missing source evidence.",
                    "[]",
                    "{}",
                )
            )
        connection.executemany(
            "INSERT INTO diagnostics("
            "diagnostic_ref, revision_id, scope_kind, scope_ref, status, reason_code, "
            "severity, title, detail, next_probe, evidence_json, raw_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return asset_dir


class EvidenceKnowledgeConsumerTests(unittest.TestCase):
    def test_import_projection_does_not_duplicate_one_gap_as_failed_and_partial(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _make_indexed_capture(Path(temp_dir))
            capture = knowledge_import.load_capture(asset_dir)

        failed = capture["failed_graphs"].get("graphs", [])
        partial = capture["partial_graphs"].get("graphs", [])
        self.assertEqual(failed, [])
        graph_gaps = [row for row in partial if row.get("primary_reason") == "missing_target_pin_id"]
        self.assertEqual(len(graph_gaps), 1)
        self.assertTrue(graph_gaps[0]["evidence_ref"].startswith("bp://"))

    def test_import_projection_preserves_gap_but_excludes_unusable_placeholder_from_consumers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _make_indexed_capture(Path(temp_dir))
            capture = knowledge_import.load_capture(asset_dir)

        unresolved = capture["class_defaults"]["variables"]["StoredXPRewards"]
        self.assertEqual(unresolved["value"], [])
        self.assertEqual(unresolved["value_status"], "NOT_RECOVERED")
        self.assertFalse(unresolved["value_usable"])
        self.assertIn(
            "array_property_not_decoded",
            {row.get("primary_reason") for row in capture["partial_graphs"]["graphs"]},
        )
        default_gap = next(
            row
            for row in capture["partial_graphs"]["graphs"]
            if row.get("primary_reason") == "array_property_not_decoded"
        )
        self.assertEqual(default_gap["scope_kind"], "default")
        self.assertEqual(default_gap["name"], "StoredXPRewards")
        usable = knowledge_import.variables_from_capture(capture)
        self.assertIn("ConfirmedValue", usable)
        self.assertNotIn("StoredXPRewards", usable)

        connection = sqlite3.connect(":memory:")
        try:
            connection.execute(
                "CREATE TABLE unresolved_work ("
                "id INTEGER PRIMARY KEY, object_path TEXT, work_type TEXT, detail TEXT, source_json TEXT, status TEXT)"
            )
            knowledge_import.insert_unresolved_work(connection, "/Game/Test/IndexedKnowledgeAsset", capture)
            rows = connection.execute(
                "SELECT work_type, detail FROM unresolved_work ORDER BY id"
            ).fetchall()
        finally:
            connection.close()
        self.assertIn(
            "default_value_gap",
            {row[0] for row in rows if "StoredXPRewards" in row[1]},
        )

    def test_repository_knowledge_projection_preserves_external_calls_for_native_catalog(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset_dir = _make_indexed_capture(root)
            asset = knowledge_builder.summarize_asset_from_repository(asset_dir, root)
            catalog = knowledge_builder.build_native_catalog({asset["asset_name"]: asset})

        unresolved = asset["unresolved_calls"]
        self.assertIn("ExternalGameplayCall", {row["function"] for row in unresolved})
        functions = {row["function"]: row for row in catalog["functions"]}
        self.assertIn("ExternalGameplayCall", functions)
        self.assertTrue(functions["ExternalGameplayCall"]["examples"])

    def test_repository_knowledge_projection_keeps_unusable_default_out_of_focus_facts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset_dir = _make_indexed_capture(root)
            asset = knowledge_builder.summarize_asset_from_repository(asset_dir, root)
            focus = knowledge_builder.build_system_focus("xp", {asset["asset_name"]: asset})

        unresolved = asset["default_variables"]["StoredXPRewards"]
        self.assertEqual(unresolved["value_status"], "NOT_RECOVERED")
        self.assertFalse(unresolved["value_usable"])
        self.assertIn(
            "array_property_not_decoded",
            {row["kind"] for row in asset["quality_caveats"]},
        )
        self.assertNotIn(
            "IndexedKnowledgeAsset.StoredXPRewards",
            {row["id"] for row in focus["facts"]},
        )

    def test_repository_knowledge_projection_preserves_large_gap_coverage_and_rare_groups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset_dir = _make_lionfish_scale_gap_capture(root)
            asset = knowledge_builder.summarize_asset_from_repository(asset_dir, root)

        gap_summary = asset["gap_summary"]
        self.assertEqual(gap_summary["total"], 26_461)
        self.assertEqual(gap_summary["returned"], 200)
        self.assertEqual(gap_summary["omitted"], 26_261)
        self.assertTrue(gap_summary["truncated"])
        self.assertEqual(gap_summary["by_status"]["NOT_RECOVERED"], 26_460)
        self.assertEqual(gap_summary["by_reason"]["rare_native_gap"], 1)
        caveats = {row["kind"]: row for row in asset["quality_caveats"]}
        self.assertEqual(caveats["lionfish_bulk_gap"]["count"], 26_460)
        self.assertEqual(caveats["rare_native_gap"]["count"], 1)
        self.assertTrue(caveats["rare_native_gap"]["examples"])

    def test_business_import_keeps_gap_only_asset_as_unresolved_work(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            capture_dir = _make_gap_only_indexed_capture(root / "captures")
            db_path = root / "status.sqlite"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "CREATE TABLE status_assets ("
                    "object_path TEXT, asset_name TEXT, processed_current INTEGER, "
                    "captured INTEGER, capture_dir TEXT)"
                )
                connection.execute(
                    "INSERT INTO status_assets VALUES (?, ?, 1, 1, ?)",
                    (
                        "/Game/Test/GapOnlyAsset.GapOnlyAsset",
                        "GapOnlyAsset",
                        str(capture_dir),
                    ),
                )
                connection.execute(
                    "CREATE TABLE read_sources ("
                    "object_path TEXT PRIMARY KEY, capture_dir TEXT, package_json TEXT, "
                    "graph_nodes_json TEXT, class_defaults_json TEXT, last_read_at TEXT, "
                    "read_status TEXT)"
                )
                connection.execute(
                    "CREATE TABLE unresolved_work ("
                    "id INTEGER PRIMARY KEY, object_path TEXT, work_type TEXT, "
                    "detail TEXT, source_json TEXT, status TEXT)"
                )
                connection.commit()
            finally:
                connection.close()

            summary = knowledge_import.import_business_database(
                db_path,
                "status_component_blueprint",
                knowledge_import.CATEGORY_DATABASES["status_component_blueprint"],
                root / "captures",
                clear_existing=False,
            )
            connection = sqlite3.connect(db_path)
            try:
                unresolved = connection.execute(
                    "SELECT work_type, detail FROM unresolved_work"
                ).fetchall()
            finally:
                connection.close()

        self.assertEqual(summary["assets_imported"], 1)
        self.assertEqual(summary["skipped_no_payload"], 0)
        self.assertEqual(summary["unresolved_imported"], 1)
        self.assertEqual(unresolved[0][0], "default_value_gap")
        self.assertIn("UnparsedEntries", unresolved[0][1])


if __name__ == "__main__":
    unittest.main()
