from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import blueprint_tool_server as tool_server  # noqa: E402
from blueprint_translator.evidence_query import EvidenceQueryService  # noqa: E402
from blueprint_translator.evidence_publication import (  # noqa: E402
    migrate_v2_evidence_to_v3,
)
from blueprint_translator.evidence_schema import (  # noqa: E402
    ensure_evidence_schema,
    make_asset_id,
    make_revision_id,
)


def _write_minimal_evidence_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    object_path = "/Game/Test/TimingAsset.TimingAsset"
    parser_version = "fixture-parser"
    schema_version = "ark.blueprint.evidence.v2"
    source_path = "@memory/http_fixture"
    source_bytes = b"http-fixture-source"
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    source_hashes = {source_path: source_sha256}
    asset_id = make_asset_id(object_path)
    revision_id = make_revision_id(
        source_hashes,
        parser_version=parser_version,
        schema_version=schema_version,
    )
    source_fingerprint = hashlib.sha256(
        json.dumps(
            sorted(source_hashes.items()),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    connection = sqlite3.connect(database_path)
    try:
        ensure_evidence_schema(connection)
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
                object_path,
                source_fingerprint,
                parser_version,
                schema_version,
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
        connection.execute(
            "INSERT INTO source_manifest(revision_id, path, sha256, size_bytes, source_kind) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                revision_id,
                source_path,
                source_sha256,
                len(source_bytes),
                "in_memory_fixture",
            ),
        )
        connection.commit()
    finally:
        connection.close()
    manifest = {
        "schema": "ark.blueprint.evidence.v2",
        "asset_id": asset_id,
        "asset_name": "TimingAsset",
        "object_path": object_path,
        "revision_id": revision_id,
        "source_fingerprint": source_fingerprint,
        "parser_version": parser_version,
        "counts": {"graphs": 1, "nodes": 1, "pins": 0, "links": 0},
        "database": "evidence.sqlite",
        "agent_index": "../output/agent_index.md",
        "legacy_artifacts_deleted": False,
    }
    (database_path.parent / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    output_dir = database_path.parent.parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "agent_index.md").write_text(
        f"# TimingAsset Evidence\n\n- Revision: `{revision_id}`\n",
        encoding="utf-8",
    )


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

        metadata_keys = {
            "sourceKind",
            "freshnessStatus",
            "releaseAuthority",
            "migrationRequired",
            "manifestSha256",
            "pointerSha256",
        }
        self.assertEqual(
            [{key: value for key, value in result.items() if key not in metadata_keys} for result in actual],
            expected,
        )
        for result in actual:
            self.assertEqual(result["sourceKind"], "INDEXED_V2_COMPATIBILITY")
            self.assertEqual(result["freshnessStatus"], "SOURCE_UNAVAILABLE")
            self.assertFalse(result["releaseAuthority"])
            self.assertTrue(result["migrationRequired"])
            self.assertIsNone(result["manifestSha256"])
            self.assertIsNone(result["pointerSha256"])

    def test_v3_asset_summary_and_query_expose_manifest_bound_identity(self):
        published = migrate_v2_evidence_to_v3(self.asset_dir, prune_v2=True)

        with patch.object(tool_server, "CAPTURE_ROOT", self.capture_root):
            assets = tool_server.list_assets()
        result = tool_server.query_asset_evidence(
            self.capture_root,
            "TimingAsset",
            {"operation": "overview", "budgetTokens": 1000},
        )

        self.assertEqual(len(assets), 1)
        summary = assets[0]
        for payload in (summary, result):
            self.assertEqual(payload["sourceKind"], "INDEXED_V3_CURRENT")
            self.assertEqual(payload["freshnessStatus"], "SOURCE_UNAVAILABLE")
            self.assertTrue(payload["releaseAuthority"])
            self.assertFalse(payload["migrationRequired"])
            self.assertEqual(payload["manifestSha256"], published.manifest_sha256)
            self.assertEqual(payload["pointerSha256"], published.pointer_sha256)
        self.assertEqual(summary["evidenceRevision"], published.revision_id)
        self.assertTrue(summary["hasEvidenceStore"])

    def test_agent_index_report_uses_pointed_revision_not_compatibility_copy(self):
        published = migrate_v2_evidence_to_v3(self.asset_dir)
        compatibility_index = self.asset_dir / "output" / "agent_index.md"
        compatibility_index.write_text("# STALE COMPATIBILITY COPY\n", encoding="utf-8")

        for target in ("agent_index", "output/agent_index.md"):
            with self.subTest(target=target):
                result = tool_server.query_report_for_request(
                    self.asset_dir,
                    target,
                    mode="full",
                    budget=1000,
                )

                self.assertIn("TimingAsset Evidence", result["content"])
                self.assertNotIn("STALE COMPATIBILITY COPY", result["content"])
                self.assertEqual(result["sourceKind"], "INDEXED_V3_CURRENT")
                self.assertEqual(result["manifestSha256"], published.manifest_sha256)
                self.assertEqual(result["pointerSha256"], published.pointer_sha256)
                self.assertNotIn(str(self.asset_dir), json.dumps({
                    key: result[key]
                    for key in (
                        "sourceKind",
                        "freshnessStatus",
                        "releaseAuthority",
                        "migrationRequired",
                        "manifestSha256",
                        "pointerSha256",
                    )
                }))

    def test_corrupt_v3_pointer_never_falls_back_to_valid_v2_compatibility(self):
        migrate_v2_evidence_to_v3(self.asset_dir)
        pointer_path = self.asset_dir / "evidence" / "current.json"
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        pointer["manifestSha256"] = "0" * 64
        pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "(?i)(manifest|pointer|sha256)"):
            tool_server.query_asset_evidence(
                self.capture_root,
                "TimingAsset",
                {"operation": "overview", "budgetTokens": 1000},
            )
        with self.assertRaisesRegex(ValueError, "(?i)(manifest|pointer|sha256)"):
            tool_server.asset_summary(self.asset_dir)
        with self.assertRaisesRegex(ValueError, "(?i)(manifest|pointer|sha256)"):
            tool_server.query_report_for_request(
                self.asset_dir,
                "agent_index",
                mode="full",
                budget=1000,
            )

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
