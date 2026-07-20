from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import blueprint_tool_server as tool_server  # noqa: E402
from blueprint_translator.evidence_query import EvidenceQueryService  # noqa: E402
from blueprint_translator.evidence_schema import ensure_evidence_schema  # noqa: E402


def _write_minimal_evidence_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        ensure_evidence_schema(connection)
        asset_id = "asset-http-fixture"
        revision_id = "revision-http-fixture"
        graph_ref = f"bp://{asset_id}@{revision_id}/g/7"
        node_ref = f"{graph_ref}/n/1"
        connection.execute(
            "INSERT INTO asset_revisions("
            "revision_id, asset_id, asset_name, object_path, source_fingerprint, "
            "parser_version, schema_version, generated_at, uasset_path"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revision_id,
                asset_id,
                "TimingAsset",
                "/Game/Test/TimingAsset.TimingAsset",
                "fixture-fingerprint",
                "fixture-parser",
                "ark.blueprint.evidence.v2",
                "2026-07-11T00:00:00+00:00",
                "",
            ),
        )
        connection.execute(
            "INSERT INTO graphs("
            "graph_ref, revision_id, export_index, name, graph_type, status, confidence, node_count"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (graph_ref, revision_id, 7, "EventGraph", "EventGraph", "complete", "high", 1),
        )
        connection.execute(
            "INSERT INTO nodes("
            "node_ref, graph_ref, local_index, node_identity, package_index, export_index, "
            "name, label, class_name, node_type, function_name, source, confidence"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                node_ref,
                graph_ref,
                0,
                "native:1",
                1,
                0,
                "GetGameTimeInSeconds",
                "Get Game Time in Seconds",
                "K2Node_CallFunction",
                "K2Node_CallFunction",
                "GetGameTimeInSeconds",
                "fixture",
                "high",
            ),
        )
        connection.execute(
            "INSERT INTO search_entities(ref, revision_id, kind, name, graph_ref, summary, search_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                node_ref,
                revision_id,
                "node",
                "GetGameTimeInSeconds",
                graph_ref,
                "K2Node_CallFunction GetGameTimeInSeconds",
                "GetGameTimeInSeconds game time seconds",
            ),
        )
        connection.commit()
    finally:
        connection.close()


class EvidenceHttpContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        root = Path(self._temporary.name)
        self.capture_root = root / "captures"
        self.asset_dir = self.capture_root / "TimingAsset"
        self.database_path = self.asset_dir / "evidence" / "evidence.sqlite"
        _write_minimal_evidence_database(self.database_path)

    def test_query_asset_evidence_matches_service_for_overview_and_search(self):
        requests = [
            {"operation": "overview", "budgetTokens": 1000},
            {
                "operation": "search",
                "query": "GetGameTimeInSeconds",
                "kinds": ["node"],
                "budgetTokens": 1000,
            },
        ]

        with EvidenceQueryService.open(self.database_path) as service:
            expected = [service.query(request) for request in requests]
        actual = [
            tool_server.query_asset_evidence(self.capture_root, "TimingAsset", request)
            for request in requests
        ]

        self.assertEqual(actual, expected)

    def test_query_asset_evidence_rejects_database_paths_and_capture_root_escape(self):
        outside_database = Path(self._temporary.name) / "outside" / "evidence.sqlite"
        _write_minimal_evidence_database(outside_database)

        invalid_asset_identifiers = [
            str(self.database_path),
            "TimingAsset/evidence/evidence.sqlite",
            "../outside",
            str(outside_database),
        ]
        for asset in invalid_asset_identifiers:
            with self.subTest(asset=asset):
                with self.assertRaisesRegex(ValueError, "(?i)(asset|identifier|capture|path)"):
                    tool_server.query_asset_evidence(
                        self.capture_root,
                        asset,
                        {"operation": "overview", "budgetTokens": 1000},
                    )

    def test_agent_index_is_a_known_report_and_open_target(self):
        expected = ("output", "agent_index.md")

        self.assertEqual(tool_server.REPORT_FILES["agent_index"], expected)
        self.assertEqual(tool_server.REPORT_TARGETS["agent_index"], expected)
        self.assertEqual(tool_server.OPEN_TARGETS["agent_index"], expected)


if __name__ == "__main__":
    unittest.main()
