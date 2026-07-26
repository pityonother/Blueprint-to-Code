from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


REQUIRED_TABLE_COLUMNS = {
    "assets": {
        "object_path",
        "package_path",
        "asset_name",
        "asset_class_path",
        "blueprint_kind",
        "generated_class_path",
        "parent_class_path",
        "native_parent_class_path",
        "mount_point",
        "top_folder",
        "plugin_or_dlc",
        "is_blueprint",
        "is_data_only_blueprint",
        "is_map",
        "is_data_asset",
        "is_data_table",
        "is_function_library",
        "is_blueprint_interface",
        "is_user_defined_struct",
        "is_user_defined_enum",
        "is_editor_only",
        "has_uasset",
        "has_uexp",
        "has_ubulk",
        "file_size_total",
        "source_fingerprint",
        "source_modified",
        "capture_exists",
        "evidence_revision",
        "evidence_freshness",
        "parse_status",
        "parse_confidence",
        "graph_count",
        "function_count",
        "event_count",
        "macro_count",
        "variable_count",
        "component_count",
        "default_property_count",
        "dependency_count",
        "referencer_count",
        "hard_referencer_count",
        "soft_referencer_count",
        "descendant_count",
        "implemented_by_count",
        "map_usage_count",
        "registry_usage_count",
        "cross_domain_reference_count",
        "native_call_count",
        "unresolved_native_call_count",
        "estimated_deep_read_cost",
        "provisional_tier",
        "provisional_reasons_json",
    },
    "class_edges": {
        "child_class_path",
        "parent_class_path",
        "edge_kind",
        "inheritance_depth",
        "source_kind",
        "confidence",
    },
    "interfaces": {
        "owner_object_path",
        "interface_class_path",
        "source_kind",
        "confidence",
    },
    "components": {
        "owner_object_path",
        "component_name",
        "component_class_path",
        "component_object_path",
        "is_inherited",
        "source_property",
        "confidence",
    },
    "asset_references": {
        "source_object_path",
        "target_object_path",
        "edge_kind",
        "reference_strength",
        "source_property",
        "source_graph",
        "source_function",
        "source_evidence_id",
        "confidence",
    },
    "graphs": {
        "asset_object_path",
        "graph_evidence_id",
        "graph_name",
        "graph_type",
        "status",
        "confidence",
        "node_count",
        "pin_count",
        "wire_count",
        "native_call_count",
        "external_asset_reference_count",
        "gap_count",
    },
    "blueprint_functions": {
        "asset_object_path",
        "function_name",
        "function_kind",
        "graph_evidence_id",
        "replication_kind",
        "is_pure",
        "is_override",
        "declaring_class_path",
        "call_count_out",
        "call_count_in",
        "native_boundary",
        "confidence",
    },
    "default_property_surface": {
        "asset_object_path",
        "property_name",
        "property_type",
        "declaring_class_path",
        "has_value",
        "value_status",
        "value_fingerprint",
        "is_object_reference",
        "is_array",
        "is_map",
        "is_struct",
        "source_evidence_id",
    },
    "system_registrations": {
        "owner_object_path",
        "registration_type",
        "target_object_path",
        "source_property",
        "source_evidence_id",
        "confidence",
    },
    "native_symbols": {
        "native_evidence_id",
        "module_name",
        "binary_sha256",
        "pdb_sha256",
        "pdb_guid_age",
        "qualified_name",
        "simple_name",
        "owner_class",
        "signature",
        "rva",
        "symbol_source",
        "pdb_loaded",
        "decompile_status",
        "caller_count",
        "callee_count",
        "field_access_count",
        "called_by_blueprint_count",
        "confidence",
    },
    "blueprint_native_edges": {
        "blueprint_asset_path",
        "blueprint_graph_evidence_id",
        "blueprint_function_name",
        "native_evidence_id",
        "resolution_method",
        "confidence",
    },
    "native_field_accesses": {
        "native_evidence_id",
        "field_name",
        "field_offset",
        "access_kind",
        "containing_type",
        "source_instruction_or_slice_id",
        "confidence",
    },
    "coverage": {
        "object_path",
        "stage",
        "status",
        "confirmed_count",
        "heuristic_count",
        "ambiguous_count",
        "not_recovered_count",
        "source_not_available_count",
        "stale_count",
        "last_attempt_at",
        "failure_reason",
    },
    "existing_knowledge_tables": {
        "database_name",
        "table_name",
        "row_count",
        "distinct_asset_count",
        "source_asset_count",
        "stale_row_count",
        "duplicate_key_count",
    },
    "query_corpus": {
        "query_id",
        "question",
        "source",
        "target_audience",
        "expected_answer_type",
        "primary_domain",
        "secondary_domains_json",
        "requires_blueprint",
        "requires_defaults",
        "requires_references",
        "requires_map_evidence",
        "requires_native",
        "requires_runtime_validation",
        "existing_report_path",
    },
}


def _write_fake_asset(
    content_root: Path, relative: str, payload: bytes = b"asset"
) -> Path:
    path = content_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _create_blueprint_store(captures_root: Path) -> Path:
    asset_name = "Buff_Test"
    object_path = "/Game/Test/Buff_Test.Buff_Test"
    capture = captures_root / asset_name
    store = capture / "evidence" / "evidence.sqlite"
    store.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(store)
    try:
        connection.executescript(
            """
            CREATE TABLE asset_revisions (
                revision_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL,
                asset_name TEXT NOT NULL,
                object_path TEXT NOT NULL,
                source_fingerprint TEXT NOT NULL,
                parser_version TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                uasset_path TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE graphs (
                graph_ref TEXT PRIMARY KEY,
                revision_id TEXT NOT NULL,
                export_index INTEGER NOT NULL,
                name TEXT NOT NULL,
                graph_type TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence TEXT NOT NULL,
                node_count INTEGER NOT NULL,
                pin_count INTEGER NOT NULL,
                link_observation_count INTEGER NOT NULL,
                coverage_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            CREATE TABLE nodes (
                node_ref TEXT PRIMARY KEY,
                graph_ref TEXT NOT NULL,
                function_name TEXT NOT NULL,
                event_name TEXT NOT NULL,
                macro_name TEXT NOT NULL,
                confidence TEXT NOT NULL
            );
            CREATE TABLE pins (pin_ref TEXT PRIMARY KEY, node_ref TEXT NOT NULL);
            CREATE TABLE edges (
                edge_ref TEXT PRIMARY KEY,
                graph_ref TEXT NOT NULL,
                source_pin_ref TEXT NOT NULL,
                target_pin_ref TEXT NOT NULL,
                kind TEXT NOT NULL,
                confidence TEXT NOT NULL,
                resolution_status TEXT NOT NULL
            );
            CREATE TABLE class_defaults (
                default_ref TEXT PRIMARY KEY,
                revision_id TEXT NOT NULL,
                name TEXT NOT NULL,
                type_name TEXT NOT NULL,
                value_json TEXT NOT NULL,
                value_codec TEXT NOT NULL,
                confidence TEXT NOT NULL,
                source TEXT NOT NULL,
                extra_json TEXT NOT NULL
            );
            CREATE TABLE properties (property_ref TEXT PRIMARY KEY);
            CREATE TABLE "references" (
                reference_ref TEXT PRIMARY KEY,
                graph_ref TEXT NOT NULL,
                node_ref TEXT,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                target_ref TEXT NOT NULL,
                classification TEXT NOT NULL,
                confidence TEXT NOT NULL
            );
            CREATE TABLE diagnostics (
                diagnostic_ref TEXT PRIMARY KEY,
                revision_id TEXT NOT NULL,
                scope_kind TEXT NOT NULL,
                scope_ref TEXT NOT NULL,
                status TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT NOT NULL,
                next_probe TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                raw_json TEXT NOT NULL
            );
            CREATE TABLE coverage (
                scope_ref TEXT PRIMARY KEY,
                revision_id TEXT NOT NULL,
                scope_kind TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence TEXT NOT NULL,
                metrics_json TEXT NOT NULL
            );
            CREATE TABLE source_manifest (
                revision_id TEXT NOT NULL,
                path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                source_kind TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO asset_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "bp://fixture/revision/1",
                "bp://fixture",
                asset_name,
                object_path,
                "a" * 64,
                "fixture-parser",
                "ark.blueprint.evidence.v2",
                "2026-07-27T00:00:00+00:00",
                r"C:\Users\secret\ARKDevkit\Buff_Test.uasset",
            ),
        )
        connection.execute(
            "INSERT INTO graphs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "bp://fixture/graph/EventGraph",
                "bp://fixture/revision/1",
                1,
                "EventGraph",
                "EventGraph",
                "complete",
                "medium",
                1,
                2,
                1,
                "{}",
                "[]",
                "{}",
            ),
        )
        connection.execute(
            "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?)",
            (
                "bp://fixture/node/1",
                "bp://fixture/graph/EventGraph",
                "GenerateCrateItems",
                "ReceiveBeginPlay",
                "",
                "medium",
            ),
        )
        connection.executemany(
            "INSERT INTO pins VALUES (?, ?)",
            (
                ("bp://fixture/pin/1", "bp://fixture/node/1"),
                ("bp://fixture/pin/2", "bp://fixture/node/1"),
            ),
        )
        connection.execute(
            "INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "bp://fixture/edge/1",
                "bp://fixture/graph/EventGraph",
                "bp://fixture/pin/1",
                "bp://fixture/pin/2",
                "exec",
                "medium",
                "resolved_pin",
            ),
        )
        connection.execute(
            "INSERT INTO class_defaults VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "bp://fixture/default/Duration",
                "bp://fixture/revision/1",
                "Duration",
                "FloatProperty",
                "10.0",
                "json",
                "high",
                "uasset",
                "{}",
            ),
        )
        connection.execute(
            "INSERT INTO class_defaults VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "bp://fixture/default/UnparsedArray",
                "bp://fixture/revision/1",
                "UnparsedArray",
                "ArrayProperty",
                "[]",
                "json",
                "high",
                "uasset",
                json.dumps({"array_parse": {"parsed": False}}),
            ),
        )
        connection.executemany(
            'INSERT INTO "references" VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (
                (
                    "bp://fixture/ref/game",
                    "bp://fixture/graph/EventGraph",
                    "bp://fixture/node/1",
                    "object",
                    "PrimalItem_Test",
                    "/Game/Test/PrimalItem_Test.PrimalItem_Test",
                    "external_asset",
                    "medium",
                ),
                (
                    "bp://fixture/ref/native",
                    "bp://fixture/graph/EventGraph",
                    "bp://fixture/node/1",
                    "call",
                    "GenerateCrateItems",
                    "",
                    "ark_native_or_parent_implementation",
                    "low",
                ),
            ),
        )
        connection.execute(
            "INSERT INTO diagnostics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "bp://fixture/diagnostic/1",
                "bp://fixture/revision/1",
                "graph",
                "bp://fixture/graph/EventGraph",
                "open",
                "SOURCE_NOT_AVAILABLE",
                "warning",
                "Native body unavailable",
                "Native implementation is outside this asset.",
                "Query native evidence.",
                "[]",
                "{}",
            ),
        )
        connection.execute(
            "INSERT INTO coverage VALUES (?, ?, ?, ?, ?, ?)",
            (
                "bp://fixture/coverage/1",
                "bp://fixture/revision/1",
                "graph",
                "partial",
                "medium",
                "{}",
            ),
        )
        connection.execute(
            "INSERT INTO source_manifest VALUES (?, ?, ?, ?, ?)",
            (
                "bp://fixture/revision/1",
                r"C:\Users\secret\captures\Buff_Test\uasset_package.json",
                "b" * 64,
                5,
                "package_binary",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    manifest = {
        "schema": "ark.blueprint.evidence.v2",
        "asset_id": "bp://fixture",
        "asset_name": asset_name,
        "object_path": object_path,
        "revision_id": "bp://fixture/revision/1",
        "source_fingerprint": "a" * 64,
        "parser_version": "fixture-parser",
        "counts": {"graphs": 1, "nodes": 1, "pins": 2, "edges": 1},
        "database": "evidence.sqlite",
        "agent_index": "../output/agent_index.md",
    }
    (capture / "evidence" / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (capture / "uasset_exports.json").write_text(
        json.dumps(
            [
                {
                    "package_index": 1,
                    "class_index": -1,
                    "class_name": "Blueprint",
                    "super_index": 0,
                    "outer_index": 0,
                    "object_name": asset_name,
                },
                {
                    "package_index": 2,
                    "class_index": -2,
                    "class_name": "BlueprintGeneratedClass",
                    "super_index": -3,
                    "outer_index": 0,
                    "object_name": f"{asset_name}_C",
                },
                {
                    "package_index": 3,
                    "class_index": -4,
                    "class_name": "InventoryComponent",
                    "super_index": 0,
                    "outer_index": 2,
                    "outer_name": f"{asset_name}_C",
                    "object_name": "MyInventoryComponent_GEN_VARIABLE",
                },
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    output = capture / "output"
    output.mkdir()
    (output / "agent_index.md").write_text(
        "# Buff Test\n\nDerived fixture index.\n",
        encoding="utf-8",
    )
    return store


def _create_native_store(
    native_root: Path,
    *,
    store_name: str,
    generated_at: str,
    evidence_set_id: str,
    function_name: str,
) -> Path:
    store = native_root / "stores" / "binary-fixture" / store_name
    store.mkdir(parents=True, exist_ok=True)
    database = store / "evidence.sqlite"
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE native_evidence_sets (
                evidence_set_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                sqlite_schema TEXT NOT NULL,
                generated_at_utc TEXT NOT NULL,
                provenance_status TEXT NOT NULL,
                source_json_sha256 TEXT NOT NULL,
                source_json_size INTEGER NOT NULL,
                recipe_id TEXT NOT NULL,
                recipe_sha256 TEXT NOT NULL,
                generator_id TEXT NOT NULL,
                generator_sha256 TEXT NOT NULL,
                pdb_sha256 TEXT NOT NULL,
                pdb_guid TEXT NOT NULL,
                pdb_age INTEGER NOT NULL,
                pdb_matched INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE native_recipe_targets (
                target_id TEXT PRIMARY KEY,
                evidence_set_id TEXT NOT NULL,
                expected_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                selector_json TEXT NOT NULL,
                resolved_evidence_ids_json TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE native_functions (
                evidence_id TEXT PRIMARY KEY,
                evidence_set_id TEXT NOT NULL,
                symbol_set_id TEXT NOT NULL,
                binary_sha256 TEXT NOT NULL,
                module TEXT NOT NULL,
                rva TEXT NOT NULL,
                name TEXT NOT NULL,
                qualified_name TEXT NOT NULL,
                owner TEXT NOT NULL,
                signature TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence TEXT NOT NULL,
                source TEXT NOT NULL,
                decompiled_c TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE native_gaps (
                gap_id TEXT PRIMARY KEY,
                evidence_set_id TEXT NOT NULL,
                function_evidence_id TEXT NOT NULL,
                status TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                detail TEXT NOT NULL,
                next_probe TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE native_call_edges (
                call_edge_id TEXT PRIMARY KEY,
                caller_evidence_id TEXT NOT NULL,
                callee_evidence_id TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE native_field_accesses (
                field_access_id TEXT PRIMARY KEY,
                function_evidence_id TEXT NOT NULL,
                owner_type TEXT NOT NULL,
                field_name TEXT NOT NULL,
                field_offset TEXT NOT NULL,
                access_kind TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE native_blueprint_links (
                edge_id TEXT PRIMARY KEY,
                evidence_set_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                target_id TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        evidence_id = f"native://fixture/{function_name}"
        payload = {
            "binary": {"module": "Fixture.dll", "sha256": "e" * 64},
            "ghidra": {"version": "12.1.2"},
            "java": {"version": "21.0.11+10-LTS"},
            "pdb": {
                "sha256": "d" * 64,
                "guid": "fixture-guid",
                "age": 2,
                "loaded": True,
                "matchesBinary": True,
            },
        }
        connection.execute(
            "INSERT INTO native_evidence_sets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                evidence_set_id,
                "v2",
                "v1",
                generated_at,
                "VERIFIED",
                "f" * 64,
                999,
                "ark-loot-quality-v1",
                "c" * 64,
                "fixture-generator",
                "2" * 64,
                "d" * 64,
                "fixture-guid",
                2,
                1,
                json.dumps(payload),
            ),
        )
        connection.execute(
            "INSERT INTO native_recipe_targets VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "generate-crate-items",
                evidence_set_id,
                1,
                "CONFIRMED",
                json.dumps({"qualifiedName": f"Fixture::{function_name}"}),
                json.dumps([evidence_id]),
                "{}",
            ),
        )
        connection.execute(
            "INSERT INTO native_functions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                evidence_id,
                evidence_set_id,
                "native-symbol-set://fixture",
                "e" * 64,
                "Fixture.dll",
                "0x1000",
                function_name,
                f"Fixture::{function_name}",
                "Fixture",
                f"void {function_name}()",
                "CONFIRMED",
                "HIGH",
                "IMPORTED",
                r"pseudo C contains C:\Users\secret and must never ship",
                json.dumps({"localPath": r"C:\Users\secret\native.bin"}),
            ),
        )
        connection.execute(
            "INSERT INTO native_gaps VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "native-gap://fixture/1",
                evidence_set_id,
                evidence_id,
                "NOT_RECOVERED",
                "BUDGET_EXCEEDED",
                "Bounded traversal stopped.",
                "Use a narrower recipe before raising the budget.",
                "{}",
            ),
        )
        connection.execute(
            "INSERT INTO native_field_accesses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "native-field://fixture/1",
                evidence_id,
                "Fixture",
                "Quality",
                "0x20",
                "READ",
                "CONFIRMED",
                "HIGH",
                "{}",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    manifest = {
        "schema": "blueprint-to-code-native-evidence-manifest/v1",
        "evidenceSetId": evidence_set_id,
        "generatedAtUtc": generated_at,
        "source": {"path": "evidence.full.json", "sha256": "f" * 64, "sizeBytes": 999},
        "sqlite": {"path": "evidence.sqlite", "sha256": "1" * 64, "userVersion": 1},
        "trust": {"status": "VERIFIED", "formalValidation": True},
        "counts": {
            "native_evidence_sets": 1,
            "native_recipe_targets": 1,
            "native_functions": 1,
            "native_gaps": 1,
        },
    }
    (store / "evidence.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return store


def _create_existing_knowledge_db(db_dir: Path) -> None:
    db_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_dir / "buffs.sqlite")
    try:
        connection.executescript(
            """
            CREATE TABLE buff_assets (
                object_path TEXT PRIMARY KEY,
                asset_name TEXT NOT NULL,
                processed_current INTEGER NOT NULL
            );
            CREATE TABLE read_sources (
                object_path TEXT PRIMARY KEY,
                capture_dir TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO buff_assets VALUES (?, ?, ?)",
            ("/Game/Test/Buff_Test.Buff_Test", "Buff_Test", 1),
        )
        connection.execute(
            "INSERT INTO read_sources VALUES (?, ?)",
            ("/Game/Test/Buff_Test.Buff_Test", r"C:\Users\secret\captures\Buff_Test"),
        )
        connection.commit()
    finally:
        connection.close()


class KnowledgeDiscoveryBundleTests(unittest.TestCase):
    def test_inventory_scan_resumes_and_reports_incremental_changes(self):
        from blueprint_translator.kb_discovery import scan_devkit_inventory

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            content_root = root / "ShooterGame" / "Content"
            first = _write_fake_asset(content_root, "ZZ_RootAsset.uasset")
            removed = _write_fake_asset(
                content_root,
                "AA_Nested/Buff_Test.uasset",
            )
            state = root / "work" / "discovery_state.sqlite"

            interrupted = scan_devkit_inventory(
                state,
                content_root,
                batch_size=1,
                max_assets=1,
                parse_identity=False,
            )
            self.assertFalse(interrupted["complete"])
            self.assertEqual(interrupted["processedThisCall"], 1)

            resumed = scan_devkit_inventory(
                state,
                content_root,
                batch_size=1,
                parse_identity=False,
            )
            self.assertTrue(resumed["complete"])
            self.assertEqual(resumed["uassetCount"], 2)
            self.assertTrue(resumed["resumed"])

            first.write_bytes(b"changed")
            _write_fake_asset(
                content_root,
                "BB_Nested/PrimalItem_Test.uasset",
            )
            removed.unlink()
            incremental = scan_devkit_inventory(
                state,
                content_root,
                batch_size=2,
                parse_identity=False,
            )
            self.assertTrue(incremental["complete"])
            self.assertEqual(incremental["uassetCount"], 2)
            self.assertEqual(incremental["added"], 1)
            self.assertEqual(incremental["changed"], 1)
            self.assertEqual(incremental["deleted"], 1)

    def test_bundle_matches_required_contract_and_excludes_sensitive_sources(self):
        from blueprint_translator.kb_discovery import (
            build_discovery_bundle,
            verify_discovery_bundle,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            content_root = root / "ARKDevkit" / "Projects" / "ShooterGame" / "Content"
            _write_fake_asset(content_root, "Test/Buff_Test.uasset")
            _write_fake_asset(content_root, "Test/PrimalItem_Test.uasset")
            _write_fake_asset(content_root, "Maps/TestMap.umap")
            captures = root / "captures"
            _create_blueprint_store(captures)
            native_root = root / "native_evidence"
            _create_native_store(
                native_root,
                store_name="older",
                generated_at="2026-07-26T00:00:00+00:00",
                evidence_set_id="native-set://fixture/older",
                function_name="OldGenerateCrateItems",
            )
            _create_native_store(
                native_root,
                store_name="newer",
                generated_at="2026-07-27T00:00:00+00:00",
                evidence_set_id="native-set://fixture/newer",
                function_name="GenerateCrateItems",
            )
            knowledge_db_dir = root / "knowledge_base" / "db"
            _create_existing_knowledge_db(knowledge_db_dir)
            reports = root / "reports"
            reports.mkdir()
            (reports / "fixture_report.md").write_text(
                "# Why does this Buff call native loot generation?\n",
                encoding="utf-8",
            )
            output_dir = root / "knowledge_base" / "discovery_bundle"

            result = build_discovery_bundle(
                project_root=root,
                output_dir=output_dir,
                content_root=content_root,
                captures_root=captures,
                native_root=native_root,
                knowledge_db_dir=knowledge_db_dir,
                include_existing_evidence=True,
                include_native_boundaries=True,
                build_zip=True,
                parse_identity=False,
                generated_at="2026-07-27T01:00:00+00:00",
            )

            self.assertEqual(result["status"], "complete")
            expected_files = {
                "README_FOR_REVIEW.md",
                "discovery_manifest.json",
                "kb_discovery.sqlite",
                "kb_discovery_schema.sql",
                "discovery_report.md",
                "asset_inventory_preview.csv",
                "top_background_candidates.csv",
                "unresolved_and_unknown.csv",
                "query_corpus.jsonl",
                "representative_samples/sample_manifest.json",
                "SHA256SUMS.txt",
            }
            relative_files = {
                path.relative_to(output_dir).as_posix()
                for path in output_dir.rglob("*")
                if path.is_file()
            }
            self.assertTrue(expected_files.issubset(relative_files))
            self.assertTrue(
                any(
                    name.startswith("representative_samples/")
                    and name != "representative_samples/sample_manifest.json"
                    for name in relative_files
                )
            )

            manifest = json.loads(
                (output_dir / "discovery_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schema"], "blueprint-to-code-kb-discovery/v1")
            self.assertTrue(manifest["devkitSnapshot"]["contentRootRedacted"])
            self.assertEqual(
                manifest["devkitSnapshot"]["assetCount"],
                3,
            )
            self.assertTrue(manifest["extractors"])
            self.assertNotIn(str(root), json.dumps(manifest))

            database = output_dir / "kb_discovery.sqlite"
            connection = sqlite3.connect(database)
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                self.assertEqual(integrity, "ok")
                for table, required_columns in REQUIRED_TABLE_COLUMNS.items():
                    actual = {
                        row[1]
                        for row in connection.execute(f'PRAGMA table_info("{table}")')
                    }
                    self.assertTrue(
                        required_columns.issubset(actual),
                        f"{table} missing {sorted(required_columns - actual)}",
                    )
                self.assertGreaterEqual(
                    connection.execute("SELECT COUNT(*) FROM query_corpus").fetchone()[
                        0
                    ],
                    30,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM native_symbols WHERE simple_name=?",
                        ("OldGenerateCrateItems",),
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM native_symbols WHERE simple_name=?",
                        ("GenerateCrateItems",),
                    ).fetchone()[0],
                    1,
                )
                self.assertGreaterEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM asset_references"
                    ).fetchone()[0],
                    1,
                )
                self.assertGreaterEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM blueprint_native_edges"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT value_status
                        FROM default_property_surface
                        WHERE property_name='UnparsedArray'
                        """
                    ).fetchone()[0],
                    "NOT_RECOVERED",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM assets WHERE native_call_count > 0"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM blueprint_native_edges
                        WHERE native_evidence_id='native://UNRESOLVED'
                        """
                    ).fetchone()[0],
                    0,
                )
                schema_text = "\n".join(
                    row[0] or ""
                    for row in connection.execute(
                        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
                    )
                )
                self.assertNotIn("decompiled_c", schema_text)
            finally:
                connection.close()

            query_lines = [
                json.loads(line)
                for line in (output_dir / "query_corpus.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            self.assertGreaterEqual(len(query_lines), 30)
            self.assertTrue(any(row["requires_native"] for row in query_lines))
            self.assertTrue(any(row["requires_map_evidence"] for row in query_lines))
            self.assertTrue(
                any(row["requires_runtime_validation"] for row in query_lines)
            )

            audit = verify_discovery_bundle(
                output_dir,
                zip_path=output_dir.with_suffix(".zip"),
            )
            self.assertTrue(audit["passed"], audit)
            with zipfile.ZipFile(output_dir.with_suffix(".zip")) as archive:
                archive_files = {
                    name for name in archive.namelist() if not name.endswith("/")
                }
                self.assertEqual(
                    archive_files,
                    {f"discovery_bundle/{name}" for name in relative_files},
                )
                text_payload = b"\n".join(
                    archive.read(name)
                    for name in archive_files
                    if not name.endswith(".sqlite")
                ).decode("utf-8")
            self.assertNotIn("OldGenerateCrateItems", text_payload)
            self.assertNotIn("decompiled_c", text_payload)
            self.assertNotIn(r"C:\Users", text_payload)
            self.assertNotIn(str(root), text_payload)
            self.assertFalse(
                any(
                    name.lower().endswith(
                        (".uasset", ".uexp", ".ubulk", ".dll", ".pdb", ".gpr")
                    )
                    for name in archive_files
                )
            )

    def test_registry_identity_overrides_filename_and_preserves_dependency_kinds(self):
        from blueprint_translator.kb_discovery import build_discovery_bundle

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            content_root = root / "ARKDevkit" / "Projects" / "ShooterGame" / "Content"
            _write_fake_asset(content_root, "Misleading/Buff_LooksLikeItem.uasset")
            _write_fake_asset(content_root, "Actual/TargetAsset.uasset")
            registry = root / "registry"
            registry.mkdir()
            asset_rows = [
                {
                    "object_path": "/Misleading/Buff_LooksLikeItem.Buff_LooksLikeItem",
                    "package_name": "/Misleading/Buff_LooksLikeItem",
                    "package_path": "/Misleading",
                    "asset_name": "Buff_LooksLikeItem",
                    "asset_class_path": "/Script/Engine.DataTable",
                    "package_flags": 0,
                    "tags": {},
                },
                {
                    "object_path": "/Actual/TargetAsset.TargetAsset",
                    "package_name": "/Actual/TargetAsset",
                    "package_path": "/Actual",
                    "asset_name": "TargetAsset",
                    "asset_class_path": "/Script/Engine.Blueprint",
                    "package_flags": 0,
                    "tags": {
                        "GeneratedClass": "/Actual/TargetAsset.TargetAsset_C",
                        "ParentClass": "/Script/Engine.Actor",
                        "NativeParentClass": "/Script/Engine.Actor",
                        "BlueprintType": "BPTYPE_Normal",
                    },
                },
            ]
            registry_assets_path = registry / "registry_assets.jsonl"
            registry_assets_path.write_text(
                "".join(json.dumps(row) + "\n" for row in asset_rows),
                encoding="utf-8",
            )
            dependency_rows = [
                {
                    "source_package_name": "/Misleading/Buff_LooksLikeItem",
                    "target_package_name": "/Actual/TargetAsset",
                    "dependency_type": "soft_package",
                    "source_kind": "asset_registry",
                    "confidence": "HIGH",
                }
            ]
            registry_dependencies_path = registry / "registry_dependencies.jsonl"
            registry_dependencies_path.write_text(
                "".join(json.dumps(row) + "\n" for row in dependency_rows),
                encoding="utf-8",
            )
            (registry / "registry_manifest.json").write_text(
                json.dumps(
                    {
                        "schema": "ark.kb.registry-snapshot.v1",
                        "status": "COMPLETE",
                        "asset_count": 2,
                        "dependency_count": 1,
                        "inventory_signature": "f" * 64,
                        "source": {"engine_version": "5.5.4"},
                        "output_integrity": {
                            "assets_sha256": hashlib.sha256(
                                registry_assets_path.read_bytes()
                            ).hexdigest(),
                            "assets_bytes": (registry_assets_path.stat().st_size),
                            "dependencies_sha256": hashlib.sha256(
                                registry_dependencies_path.read_bytes()
                            ).hexdigest(),
                            "dependencies_bytes": (
                                registry_dependencies_path.stat().st_size
                            ),
                        },
                    }
                ),
                encoding="utf-8",
            )
            output = root / "knowledge_base" / "discovery_bundle"
            build_discovery_bundle(
                project_root=root,
                output_dir=output,
                content_root=content_root,
                registry_snapshot_dir=registry,
                include_existing_evidence=False,
                include_native_boundaries=False,
            )

            connection = sqlite3.connect(output / "kb_discovery.sqlite")
            try:
                identity = connection.execute(
                    """
                    SELECT asset_class_path, is_data_table,
                           identity_source_kind
                    FROM assets
                    WHERE object_path=?
                    """,
                    ("/Misleading/Buff_LooksLikeItem.Buff_LooksLikeItem",),
                ).fetchone()
                self.assertEqual(identity[0], "/Script/Engine.DataTable")
                self.assertEqual(identity[1], 1)
                self.assertEqual(identity[2], "unreal_asset_registry")
                edge = connection.execute(
                    """
                    SELECT source_object_path, target_object_path,
                           reference_strength, source_kind
                    FROM asset_references
                    WHERE source_kind='unreal_asset_registry'
                    """
                ).fetchone()
                self.assertEqual(
                    edge,
                    (
                        "/Misleading/Buff_LooksLikeItem",
                        "/Actual/TargetAsset",
                        "soft",
                        "unreal_asset_registry",
                    ),
                )
                parent = connection.execute(
                    """
                    SELECT parent_class_path, edge_kind
                    FROM class_edges
                    WHERE child_class_path=?
                    """,
                    ("/Actual/TargetAsset.TargetAsset_C",),
                ).fetchone()
                self.assertEqual(parent, ("/Script/Engine.Actor", "native_parent"))
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0],
                    2,
                )
            finally:
                connection.close()

            registry_assets_path.write_text(
                registry_assets_path.read_text(encoding="utf-8") + "{}\n",
                encoding="utf-8",
            )
            from blueprint_translator.kb_discovery import load_registry_snapshot

            with self.assertRaisesRegex(
                ValueError,
                "REGISTRY_ASSETS_(SHA256|BYTES|COUNT)_MISMATCH",
            ):
                load_registry_snapshot(registry)

    def test_registry_type_flags_require_authoritative_type_evidence(self):
        from blueprint_translator.kb_discovery import _registry_identity

        ordinary = _registry_identity(
            {
                "asset_class_path": "/Script/Engine.Blueprint",
                "tags": {
                    "GeneratedClass": (
                        "/Game/Helpers/BP_InterfaceDataAssetHelper."
                        "BP_InterfaceDataAssetHelper_C"
                    ),
                    "ParentClass": "/Script/Engine.Actor",
                    "BlueprintType": "BPTYPE_Normal",
                },
            }
        )
        interface = _registry_identity(
            {
                "asset_class_path": "/Script/Engine.Blueprint",
                "tags": {"BlueprintType": "BPTYPE_Interface"},
            }
        )
        function_library = _registry_identity(
            {
                "asset_class_path": "/Script/Engine.Blueprint",
                "tags": {"BlueprintType": "BPTYPE_FunctionLibrary"},
            }
        )
        data_asset = _registry_identity(
            {
                "asset_class_path": "/Script/Engine.DataAsset",
                "tags": {},
            }
        )

        self.assertEqual(ordinary["is_blueprint_interface"], 0)
        self.assertEqual(ordinary["is_data_asset"], 0)
        self.assertEqual(interface["is_blueprint_interface"], 1)
        self.assertEqual(function_library["is_function_library"], 1)
        self.assertEqual(data_asset["is_data_asset"], 1)

    def test_unknown_blueprint_applicability_stays_not_measured(self):
        import blueprint_translator.kb_discovery as discovery

        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(discovery.DISCOVERY_SCHEMA_SQL)
        assets = {
            "/Game/Unknown.Unknown": {
                "object_path": "/Game/Unknown.Unknown",
                "asset_class_path": "UNKNOWN",
                "identity_status": "UNKNOWN",
                "identity_source_kind": "filesystem_metadata",
                "is_blueprint": None,
            },
            "/Game/Table.Table": {
                "object_path": "/Game/Table.Table",
                "asset_class_path": "/Script/Engine.DataTable",
                "identity_status": "EXTRACTED",
                "identity_source_kind": "unreal_asset_registry",
                "is_blueprint": 0,
            },
        }
        try:
            for object_path, row in assets.items():
                connection.execute(
                    """
                    INSERT INTO assets(
                        object_path, package_path, asset_name, mount_point,
                        top_folder, plugin_or_dlc, source_fingerprint,
                        source_modified, asset_class_path, is_blueprint,
                        relative_logical_path, file_extension
                    ) VALUES (?, ?, ?, '/Game', '', 'Game', ?, '', ?, ?, ?, '.uasset')
                    """,
                    (
                        object_path,
                        object_path.rsplit(".", 1)[0],
                        object_path.rsplit(".", 1)[-1],
                        hashlib.sha256(object_path.encode()).hexdigest(),
                        row["asset_class_path"],
                        row["is_blueprint"],
                        object_path.lstrip("/").rsplit(".", 1)[0],
                    ),
                )
            discovery._populate_coverage(
                connection,
                assets,
                [],
                "2026-07-27T00:00:00+00:00",
            )
            unknown = connection.execute(
                """
                SELECT status, failure_reason
                FROM coverage
                WHERE object_path='/Game/Unknown.Unknown'
                  AND stage='blueprint_evidence'
                """
            ).fetchone()
            confirmed_non_blueprint = connection.execute(
                """
                SELECT status, failure_reason
                FROM coverage
                WHERE object_path='/Game/Table.Table'
                  AND stage='blueprint_evidence'
                """
            ).fetchone()
            self.assertEqual(
                tuple(unknown),
                ("NOT_MEASURED", "BLUEPRINT_APPLICABILITY_UNKNOWN"),
            )
            self.assertEqual(
                tuple(confirmed_non_blueprint),
                ("NOT_APPLICABLE", "ASSET_NOT_BLUEPRINT"),
            )
        finally:
            connection.close()

    def test_sample_gap_rules_require_real_coverage_state(self):
        import blueprint_translator.kb_discovery as discovery

        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(discovery.DISCOVERY_SCHEMA_SQL)
        try:
            for name in ("Clean", "Gap", "Unmeasured"):
                object_path = f"/Game/{name}.{name}"
                connection.execute(
                    """
                    INSERT INTO assets(
                        object_path, package_path, asset_name, mount_point,
                        top_folder, plugin_or_dlc, source_fingerprint,
                        source_modified, asset_class_path, is_blueprint,
                        capture_exists, evidence_freshness, graph_count,
                        default_property_count, identity_source_kind,
                        identity_confidence, relative_logical_path,
                        file_extension
                    ) VALUES (
                        ?, ?, ?, '/Game', '', 'Game', ?, '',
                        '/Script/Engine.Blueprint', 1, 1, 'FRESH', 1, 1,
                        'unreal_asset_registry', 'HIGH', ?, '.uasset'
                    )
                    """,
                    (
                        object_path,
                        object_path.rsplit(".", 1)[0],
                        name,
                        hashlib.sha256(object_path.encode()).hexdigest(),
                        object_path.lstrip("/").rsplit(".", 1)[0],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO coverage(
                        object_path, stage, status, confirmed_count,
                        heuristic_count, ambiguous_count, not_recovered_count,
                        source_not_available_count, stale_count, last_attempt_at,
                        failure_reason
                    ) VALUES (?, 'asset_identity', 'EXTRACTED', 1, 0, 0, 0, 0, 0, '', '')
                    """,
                    (object_path,),
                )
            connection.executemany(
                """
                INSERT INTO coverage(
                    object_path, stage, status, confirmed_count,
                    heuristic_count, ambiguous_count, not_recovered_count,
                    source_not_available_count, stale_count, last_attempt_at,
                    failure_reason
                ) VALUES (?, 'blueprint_evidence', ?, 1, 0, 0, ?, 0, 0, '', '')
                """,
                (
                    ("/Game/Clean.Clean", "FRESH", 0),
                    ("/Game/Gap.Gap", "NOT_RECOVERED", 1),
                    ("/Game/Unmeasured.Unmeasured", "NOT_MEASURED", 0),
                ),
            )
            selection = discovery._select_representative_samples(connection)
            reasons_by_path = {
                row["object_path"]: {
                    reason["reason"] for reason in row["selection_reasons"]
                }
                for row in selection["samples"]
            }
            self.assertIn(
                "complete_fresh_evidence",
                reasons_by_path["/Game/Clean.Clean"],
            )
            self.assertNotIn(
                "complete_fresh_evidence",
                reasons_by_path["/Game/Gap.Gap"],
            )
            self.assertNotIn(
                "complete_fresh_evidence",
                reasons_by_path["/Game/Unmeasured.Unmeasured"],
            )
            self.assertIn(
                "high_gap_or_stale_evidence",
                reasons_by_path["/Game/Gap.Gap"],
            )
            self.assertIn(
                "high_gap_or_stale_evidence",
                reasons_by_path["/Game/Unmeasured.Unmeasured"],
            )
            self.assertNotIn(
                "high_gap_or_stale_evidence",
                reasons_by_path["/Game/Clean.Clean"],
            )
        finally:
            connection.close()

    def test_native_provenance_never_falls_back_to_another_binary(self):
        from blueprint_translator.kb_discovery import (
            _select_current_native_provenance,
        )

        payloads = [
            {
                "binary_sha256": "a" * 64,
                "pdb_sha256": "b" * 64,
                "pdb_guid_age": "old-guid",
                "ghidra_version": "old-ghidra",
            }
        ]
        payload, status, pdb_matches = _select_current_native_provenance(
            "c" * 64,
            "d" * 64,
            payloads,
        )
        self.assertEqual(payload, {})
        self.assertEqual(status, "CURRENT_BINARY_WITHOUT_MATCHING_EVIDENCE")
        self.assertFalse(pdb_matches)

        payload, status, pdb_matches = _select_current_native_provenance(
            "a" * 64,
            "d" * 64,
            payloads,
        )
        self.assertEqual(payload["binary_sha256"], "a" * 64)
        self.assertEqual(status, "CURRENT_BINARY_MATCHED_PDB_NOT_MATCHED")
        self.assertFalse(pdb_matches)

        _, status, pdb_matches = _select_current_native_provenance(
            "a" * 64,
            "b" * 64,
            payloads,
        )
        self.assertEqual(status, "CURRENT_BINARY_AND_PDB_MATCHED")
        self.assertTrue(pdb_matches)

    def test_cache_identity_changes_when_extractor_changes(self):
        import blueprint_translator.kb_discovery as discovery

        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir)
            main_source = source_root / "main.py"
            dependency_source = source_root / "dependency.py"
            main_source.write_text("main-v1", encoding="utf-8")
            dependency_source.write_text("dependency-v1", encoding="utf-8")
            before = discovery._compute_extractor_cache_token(
                [main_source, dependency_source]
            )
            dependency_source.write_text("dependency-v2", encoding="utf-8")
            after = discovery._compute_extractor_cache_token(
                [main_source, dependency_source]
            )
            self.assertNotEqual(before, after)
            self.assertEqual(
                after,
                discovery._compute_extractor_cache_token(
                    [dependency_source, main_source]
                ),
            )
            self.assertTrue(
                {
                    "kb_discovery.py",
                    "asset_ledger.py",
                    "evidence_values.py",
                    "uasset_graphs.py",
                }.issubset(
                    {path.name for path in discovery._extractor_cache_source_paths()}
                )
            )

            state = Path(temp_dir) / "state.sqlite"
            connection = discovery._open_state(state)
            original_token = discovery.EXTRACTOR_CACHE_TOKEN
            try:
                discovery._cache_put(
                    connection,
                    "fixture",
                    "source",
                    "input-fingerprint",
                    {"value": 1},
                )
                connection.commit()
                self.assertEqual(
                    discovery._cache_get(
                        connection,
                        "fixture",
                        "source",
                        "input-fingerprint",
                    ),
                    {"value": 1},
                )
                discovery.EXTRACTOR_CACHE_TOKEN = "changed-extractor"
                self.assertIsNone(
                    discovery._cache_get(
                        connection,
                        "fixture",
                        "source",
                        "input-fingerprint",
                    )
                )
            finally:
                discovery.EXTRACTOR_CACHE_TOKEN = original_token
                connection.close()

    def test_hash_file_covers_every_bundle_member_except_itself(self):
        from blueprint_translator.kb_discovery import write_sha256sums

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "a.txt").write_text("a", encoding="utf-8")
            nested = root / "nested"
            nested.mkdir()
            (nested / "b.json").write_text("{}", encoding="utf-8")
            sums_path = write_sha256sums(root)
            rows = {}
            for line in sums_path.read_text(encoding="utf-8").splitlines():
                digest, relative = line.split("  ", 1)
                rows[relative] = digest

            self.assertEqual(set(rows), {"a.txt", "nested/b.json"})
            self.assertEqual(
                rows["a.txt"],
                hashlib.sha256((root / "a.txt").read_bytes()).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
