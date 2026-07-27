from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_review_subset import (  # noqa: E402
    REVIEW_SCHEMA,
    export_review_subset,
)


ASSET_COLUMNS = """
    object_path TEXT PRIMARY KEY,
    asset_name TEXT NOT NULL,
    asset_class_path TEXT NOT NULL,
    generated_class_path TEXT NOT NULL,
    parent_class_path TEXT NOT NULL,
    native_parent_class_path TEXT NOT NULL,
    blueprint_kind TEXT NOT NULL,
    identity_status TEXT NOT NULL,
    identity_confidence TEXT NOT NULL,
    capture_exists INTEGER NOT NULL,
    evidence_freshness TEXT NOT NULL,
    descendant_count INTEGER NOT NULL,
    referencer_count INTEGER NOT NULL,
    component_reuse_count INTEGER NOT NULL,
    cross_domain_reference_count INTEGER NOT NULL,
    registry_usage_count INTEGER NOT NULL,
    native_call_count INTEGER NOT NULL,
    unresolved_native_call_count INTEGER NOT NULL,
    query_hit_count INTEGER,
    existing_report_count INTEGER,
    provisional_tier INTEGER NOT NULL,
    provisional_reasons_json TEXT NOT NULL,
    is_blueprint INTEGER NOT NULL,
    is_data_asset INTEGER
"""


def _fixture_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            f"""
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE assets({ASSET_COLUMNS});
            CREATE TABLE query_corpus(
                query_id TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                primary_domain TEXT NOT NULL
            );
            CREATE TABLE sample_membership(
                object_path TEXT NOT NULL,
                selection_reason TEXT NOT NULL,
                source_rank INTEGER NOT NULL,
                PRIMARY KEY(object_path, selection_reason)
            );
            CREATE TABLE system_registrations(
                registration_id TEXT PRIMARY KEY,
                owner_object_path TEXT NOT NULL,
                registration_type TEXT NOT NULL,
                target_object_path TEXT NOT NULL,
                confidence TEXT NOT NULL
            );
            CREATE TABLE blueprint_native_edges(
                edge_id TEXT PRIMARY KEY,
                blueprint_asset_path TEXT NOT NULL,
                blueprint_function_name TEXT NOT NULL,
                native_evidence_id TEXT NOT NULL,
                resolution_method TEXT NOT NULL,
                confidence TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE existing_knowledge_tables(
                database_name TEXT NOT NULL,
                table_name TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                distinct_asset_count INTEGER NOT NULL,
                source_asset_count INTEGER NOT NULL,
                stale_row_count INTEGER NOT NULL,
                duplicate_key_count INTEGER NOT NULL,
                distinct_asset_status TEXT NOT NULL,
                stale_count_status TEXT NOT NULL,
                duplicate_count_status TEXT NOT NULL,
                PRIMARY KEY(database_name, table_name)
            );
            CREATE TABLE coverage(
                object_path TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                ambiguous_count INTEGER NOT NULL,
                not_recovered_count INTEGER NOT NULL,
                source_not_available_count INTEGER NOT NULL,
                stale_count INTEGER NOT NULL,
                failure_reason TEXT NOT NULL,
                PRIMARY KEY(object_path, stage)
            );
            """
        )
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [
                ("schema", "blueprint-to-code-kb-discovery/v1"),
                ("generated_at_utc", "2026-07-27T00:00:00+00:00"),
            ],
        )
        for index in range(1, 9):
            visual = index == 8
            data_asset = index == 7
            connection.execute(
                """
                INSERT INTO assets VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    f"/Game/Test/Asset_{index}.Asset_{index}",
                    f"Asset_{index}",
                    (
                        "/Script/Engine.Texture2D"
                        if visual
                        else "/Script/Engine.Blueprint"
                    ),
                    f"/Game/Test/Asset_{index}.Asset_{index}_C",
                    (
                        "/Script/Engine.PrimaryDataAsset"
                        if data_asset
                        else "/Script/Engine.Actor"
                    ),
                    "/Script/Engine.Object",
                    "Blueprint",
                    "CONFIRMED",
                    "HIGH",
                    int(index < 4),
                    "STALE" if index == 4 else "FRESH",
                    index,
                    index * 2,
                    index * 3,
                    index % 4,
                    index % 3,
                    int(index == 2),
                    int(index == 3),
                    index,
                    index // 2,
                    index % 4,
                    json.dumps(["fixture"]),
                    int(not visual),
                    int(data_asset),
                ),
            )
        connection.execute(
            "INSERT INTO query_corpus VALUES ('q1', 'How?', 'buff')"
        )
        connection.execute(
            """
            INSERT INTO sample_membership VALUES (
                '/Game/Test/Asset_1.Asset_1', 'fixture', 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO system_registrations VALUES (
                'r1', '/Game/Test/Asset_1.Asset_1', 'buff_registration',
                '/Game/Test/Asset_2.Asset_2', 'HIGH'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO blueprint_native_edges VALUES (
                'n1', '/Game/Test/Asset_2.Asset_2', 'DoThing',
                'native://fixture/1', 'callsite_and_symbol', 'CONFIRMED',
                'CONFIRMED'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO existing_knowledge_tables VALUES (
                'buffs.sqlite', 'buff_assets', 1, 1, 1, 0, 0,
                'MEASURED', 'MEASURED', 'MEASURED'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO coverage VALUES (
                '/Game/Test/Asset_4.Asset_4', 'blueprint_evidence', 'STALE',
                0, 0, 0, 1, 'SOURCE_CHANGED'
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


class KnowledgeReviewSubsetTests(unittest.TestCase):
    def test_exports_bounded_reproducible_sql_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "kb_discovery.sqlite"
            output = root / "review"
            _fixture_database(database)

            first = export_review_subset(
                database_path=database,
                output_dir=output,
                project_root=PROJECT_ROOT,
                limit=2,
                source_commit="fixture-commit",
            )
            self.assertEqual(first["status"], "complete")
            required = {
                "README.md",
                "discovery_manifest.json",
                "discovery_report.md",
                "kb_discovery_schema.sql",
                "query_corpus.jsonl",
                "representative_sample_manifest.json",
                "top_descendant_assets.csv",
                "top_referenced_assets.csv",
                "top_component_reuse_assets.csv",
                "top_cross_domain_assets.csv",
                "top_registration_targets.csv",
                "top_native_boundary_candidates.csv",
                "current_provisional_tiers.csv",
                "class_identity_coverage.csv",
                "system_registration_summary.csv",
                "existing_kb_coverage.csv",
                "stale_and_high_gap_assets.csv",
                "data_asset_classification_candidates.csv",
            }
            self.assertEqual(
                required,
                {
                    path.name
                    for path in output.iterdir()
                    if path.is_file()
                },
            )
            manifest = json.loads(
                (output / "discovery_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schema"], REVIEW_SCHEMA)
            self.assertEqual(manifest["source"]["sourceCommit"], "fixture-commit")
            self.assertEqual(
                manifest["source"]["databaseSha256"],
                hashlib.sha256(database.read_bytes()).hexdigest(),
            )
            self.assertIn(
                "top structural and demand signals within provisional tier",
                (output / "current_provisional_tiers.csv").read_text(
                    encoding="utf-8"
                ),
            )
            with (output / "top_descendant_assets.csv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                self.assertLessEqual(len(list(csv.DictReader(handle))), 2)
            self.assertNotIn(
                str(root),
                "\n".join(
                    path.read_text(encoding="utf-8")
                    for path in output.iterdir()
                    if path.is_file()
                ),
            )

            first_hashes = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in output.iterdir()
                if path.is_file()
            }
            second = export_review_subset(
                database_path=database,
                output_dir=output,
                project_root=PROJECT_ROOT,
                limit=2,
                source_commit="fixture-commit",
            )
            self.assertEqual(first["rowCounts"], second["rowCounts"])
            second_hashes = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in output.iterdir()
                if path.is_file()
            }
            self.assertEqual(first_hashes, second_hashes)

    def test_rejects_limit_above_publication_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "kb_discovery.sqlite"
            _fixture_database(database)
            with self.assertRaisesRegex(ValueError, "between 1 and 300"):
                export_review_subset(
                    database_path=database,
                    output_dir=root / "review",
                    project_root=PROJECT_ROOT,
                    limit=301,
                    source_commit="fixture",
                )


if __name__ == "__main__":
    unittest.main()
