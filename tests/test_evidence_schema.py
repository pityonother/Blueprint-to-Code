import re
import sqlite3
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.evidence_schema import (  # noqa: E402
    ensure_evidence_schema,
    make_asset_id,
    make_graph_ref,
    make_node_ref,
    make_pin_ref,
    make_revision_id,
    parse_evidence_ref,
)


class EvidenceIdTests(unittest.TestCase):
    def test_search_projection_has_completeness_metadata_and_revision_kind_index(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)

        ensure_evidence_schema(connection)

        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        search_indexes = {
            str(row[1])
            for row in connection.execute("PRAGMA index_list(search_entities)")
        }
        self.assertIn("search_materialization", tables)
        self.assertIn("idx_search_entities_revision_kind", search_indexes)

    def test_asset_id_is_deterministic_and_uses_the_normalized_object_path(self):
        canonical = "/Game/Test/Fixture_BP.Fixture_BP"

        asset_id = make_asset_id(canonical)

        self.assertEqual(asset_id, make_asset_id(canonical))
        self.assertEqual(asset_id, make_asset_id(f"  {canonical}  "))
        self.assertNotEqual(asset_id, make_asset_id("/Game/Test/Other_BP.Other_BP"))
        self.assertRegex(asset_id, re.compile(r"^[0-9a-f]+$"))

    def test_revision_id_is_order_independent_but_changes_with_evidence_or_versions(self):
        source_hashes = {
            "Fixture_BP.uasset": "a" * 64,
            "Fixture_BP.uexp": "b" * 64,
            "Fixture_BP.ubulk": "c" * 64,
        }

        revision_id = make_revision_id(
            source_hashes,
            parser_version="parser-2",
            schema_version="evidence-2",
        )

        self.assertEqual(
            revision_id,
            make_revision_id(
                dict(reversed(list(source_hashes.items()))),
                parser_version="parser-2",
                schema_version="evidence-2",
            ),
        )
        self.assertNotEqual(
            revision_id,
            make_revision_id(
                {**source_hashes, "Fixture_BP.uexp": "d" * 64},
                parser_version="parser-2",
                schema_version="evidence-2",
            ),
        )
        self.assertNotEqual(
            revision_id,
            make_revision_id(
                source_hashes,
                parser_version="parser-3",
                schema_version="evidence-2",
            ),
        )
        self.assertNotEqual(
            revision_id,
            make_revision_id(
                source_hashes,
                parser_version="parser-2",
                schema_version="evidence-3",
            ),
        )

    def test_composite_refs_keep_same_named_graphs_and_colliding_pin_ids_distinct(self):
        asset_id = make_asset_id("/Game/Test/Fixture_BP.Fixture_BP")
        revision_id = make_revision_id(
            {"Fixture_BP.uasset": "a" * 64},
            parser_version="parser-2",
            schema_version="evidence-2",
        )

        graph_7_ref = make_graph_ref(asset_id, revision_id, 7)
        graph_8_ref = make_graph_ref(asset_id, revision_id, 8)
        node_in_graph_7_ref = make_node_ref(graph_7_ref, 1)
        node_in_graph_8_ref = make_node_ref(graph_8_ref, 1)
        pin_in_graph_7_ref = make_pin_ref(node_in_graph_7_ref, 0)
        pin_in_graph_8_ref = make_pin_ref(node_in_graph_8_ref, 0)

        self.assertNotEqual(graph_7_ref, graph_8_ref)
        self.assertNotEqual(node_in_graph_7_ref, node_in_graph_8_ref)
        self.assertNotEqual(pin_in_graph_7_ref, pin_in_graph_8_ref)
        self.assertEqual(
            pin_in_graph_7_ref,
            f"bp://{asset_id}@{revision_id}/g/7/n/1/p/0",
        )

        parsed = parse_evidence_ref(pin_in_graph_7_ref)
        self.assertEqual(parsed["kind"], "pin")
        self.assertEqual(parsed["asset_id"], asset_id)
        self.assertEqual(parsed["revision_id"], revision_id)
        self.assertEqual(parsed["graph_export_index"], 7)
        self.assertEqual(parsed["node_id"], "1")
        self.assertEqual(parsed["pin_ordinal"], 0)

    def test_parse_evidence_ref_rejects_non_evidence_and_incomplete_refs(self):
        invalid_refs = [
            "https://example.invalid/g/7",
            "bp://asset-only",
            "bp://asset@revision/g/not-an-integer",
            "bp://asset@revision/g/7/n/1/p/not-an-integer",
            "bp://asset@revision/g/7/extra",
        ]

        for ref in invalid_refs:
            with self.subTest(ref=ref):
                with self.assertRaises(ValueError):
                    parse_evidence_ref(ref)


if __name__ == "__main__":
    unittest.main()
