from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.native_gold_set import (  # noqa: E402
    load_native_gold_set,
    materialize_native_gold_set,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    FULL_CORE_SCHEMA_SQL,
)


CONFIG = PROJECT_ROOT / "ontology" / "native_gold_set.v1.json"


def _core() -> sqlite3.Connection:
    core = sqlite3.connect(":memory:")
    core.execute("PRAGMA foreign_keys=ON")
    core.executescript(FULL_CORE_SCHEMA_SQL)
    core.execute(
        """
        INSERT INTO source_revisions VALUES (
            1, 'discovery', 'discovery://fixture', 'sha', 'test', 'v1',
            '2026-07-27T00:00:00Z', 'FRESH'
        )
        """
    )
    core.execute(
        """
        INSERT INTO entities(
            entity_id, canonical_uri, entity_kind, status, confidence
        ) VALUES (
            1, '/Game/Test/BP_Loot.BP_Loot', 'BLUEPRINT_ASSET',
            'CONFIRMED', 'HIGH'
        )
        """
    )
    return core


def _discovery(*, mismatched_binary: bool = False) -> sqlite3.Connection:
    source = sqlite3.connect(":memory:")
    source.executescript(
        """
        CREATE TABLE native_symbols(
            native_evidence_id TEXT PRIMARY KEY,
            module_name TEXT NOT NULL,
            binary_sha256 TEXT NOT NULL,
            pdb_sha256 TEXT NOT NULL,
            pdb_guid_age TEXT NOT NULL,
            qualified_name TEXT NOT NULL,
            simple_name TEXT NOT NULL,
            signature TEXT NOT NULL,
            rva TEXT NOT NULL,
            pdb_loaded INTEGER NOT NULL,
            caller_count INTEGER NOT NULL,
            callee_count INTEGER NOT NULL,
            confidence TEXT NOT NULL,
            recipe_ids_json TEXT NOT NULL,
            evidence_set_ids_json TEXT NOT NULL
        );
        CREATE TABLE native_field_accesses(
            access_id TEXT PRIMARY KEY,
            native_evidence_id TEXT NOT NULL,
            field_name TEXT NOT NULL,
            field_offset TEXT NOT NULL,
            access_kind TEXT NOT NULL,
            containing_type TEXT NOT NULL,
            source_instruction_or_slice_id TEXT NOT NULL,
            confidence TEXT NOT NULL
        );
        CREATE TABLE blueprint_native_edges(
            edge_id TEXT PRIMARY KEY,
            blueprint_asset_path TEXT NOT NULL,
            blueprint_graph_evidence_id TEXT NOT NULL,
            blueprint_function_name TEXT NOT NULL,
            native_evidence_id TEXT NOT NULL,
            resolution_method TEXT NOT NULL,
            confidence TEXT NOT NULL,
            status TEXT NOT NULL
        );
        """
    )
    source.execute(
        """
        INSERT INTO native_symbols VALUES (
            'native://fixture/generate-crate',
            'ShooterGameEditor-ShooterGame.dll',
            ?,
            '5285ae571d09fde9183a491f6bdef6e10a143857dd8b7fa5f9e6755b9c01bc16',
            'b63263f4-93dd-4e82-a597-81e704da2a86/2',
            'UPrimalInventoryComponent::GenerateCrateItems',
            'GenerateCrateItems',
            'bool __thiscall GenerateCrateItems(float)',
            '0x13A1420', 1, 4, 43,
            'HIGH',
            '["ark-loot-quality/v1"]',
            '["native-set://fixture"]'
        )
        """,
        (
            (
                "wrong-build"
                if mismatched_binary
                else "b0e67e1e7625dd89a30b5a1df7652a44b9b142b045f820c419b8b51bbe3d7d2a"
            ),
        ),
    )
    source.execute(
        """
        INSERT INTO native_field_accesses VALUES (
            'field-1', 'native://fixture/generate-crate',
            'ItemRating', '0x20', 'READ',
            'FItemEntry',
            'native-slice://fixture/0x13A1450', 'HIGH'
        )
        """
    )
    source.executemany(
        "INSERT INTO blueprint_native_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "name-only",
                "/Game/Test/BP_Loot.BP_Loot",
                "bp://fixture/graph",
                "GenerateCrateItems",
                "native://fixture/generate-crate",
                "exact_simple_name_candidate",
                "LOW",
                "NAME_ONLY_CANDIDATE",
            ),
            (
                "verified",
                "/Game/Test/BP_Loot.BP_Loot",
                "bp://fixture/callsite",
                "GenerateCrateItems",
                "native://fixture/generate-crate",
                "verified_callsite",
                "HIGH",
                "CONFIRMED",
            ),
        ],
    )
    return source


class KnowledgeNativeGoldSetTests(unittest.TestCase):
    def test_manifest_has_bounded_exact_targets(self):
        payload = load_native_gold_set(CONFIG)
        self.assertEqual(len(payload["targets"]), 20)
        self.assertEqual(
            len(
                {
                    (target["qualifiedSymbol"], target["rva"])
                    for target in payload["targets"]
                }
            ),
            20,
        )

    def test_exact_build_binding_confirms_function_and_reviewable_field_access(self):
        source = _discovery()
        core = _core()
        result = materialize_native_gold_set(
            source,
            core,
            config_path=CONFIG,
            generated_at="2026-07-27T00:00:00Z",
        )
        self.assertEqual(result["nativeGoldTargets"], 20)
        self.assertEqual(result["nativeConfirmedFunctions"], 1)
        self.assertEqual(result["nativeTargetGaps"], 19)
        self.assertEqual(result["nativeConfirmedFieldAccesses"], 1)
        self.assertEqual(result["blueprintNativeConfirmedLinks"], 1)
        self.assertEqual(result["blueprintNativeCandidateLinks"], 1)
        function = core.execute(
            """
            SELECT qualified_symbol, rva, status, callsite_status
            FROM native_functions
            """
        ).fetchone()
        self.assertEqual(
            function,
            (
                "UPrimalInventoryComponent::GenerateCrateItems",
                "0x13A1420",
                "CONFIRMED",
                "AVAILABLE_VIA_EVIDENCE_STORE",
            ),
        )
        statuses = dict(
            core.execute(
                "SELECT link_id, status FROM native_blueprint_links"
            )
        )
        self.assertEqual(statuses["name-only"], "CANDIDATE")
        self.assertEqual(statuses["verified"], "CONFIRMED")
        source.close()
        core.close()

    def test_low_confidence_native_symbol_is_not_confirmed(self):
        source = _discovery()
        source.execute(
            "UPDATE native_symbols SET confidence='LOW'"
        )
        core = _core()

        result = materialize_native_gold_set(
            source,
            core,
            config_path=CONFIG,
            generated_at="2026-07-27T00:00:00Z",
        )

        self.assertEqual(result["nativeConfirmedFunctions"], 0)
        self.assertEqual(
            core.execute(
                """
                SELECT status, gap_code
                FROM native_gold_targets
                WHERE target_id='loot-generate-crate'
                """
            ).fetchone(),
            ("GAP", "SYMBOL_CONFIDENCE_INSUFFICIENT"),
        )
        self.assertEqual(
            core.execute(
                "SELECT COUNT(*) FROM native_functions"
            ).fetchone()[0],
            0,
        )
        source.close()
        core.close()

    def test_unrecovered_signature_sentinels_are_not_confirmed(self):
        for signature in ("UNKNOWN", "NOT_RECOVERED"):
            with self.subTest(signature=signature):
                source = _discovery()
                source.execute(
                    "UPDATE native_symbols SET signature=?",
                    (signature,),
                )
                core = _core()

                result = materialize_native_gold_set(
                    source,
                    core,
                    config_path=CONFIG,
                    generated_at="2026-07-27T00:00:00Z",
                )

                self.assertEqual(result["nativeConfirmedFunctions"], 0)
                self.assertEqual(
                    core.execute(
                        """
                        SELECT gap_code
                        FROM native_gold_targets
                        WHERE target_id='loot-generate-crate'
                        """
                    ).fetchone()[0],
                    "SIGNATURE_NOT_RECOVERED",
                )
                source.close()
                core.close()

    def test_incomplete_field_identity_is_never_counted_confirmed(self):
        cases = (
            ("field_name", ""),
            ("field_offset", ""),
            ("field_offset", "UNKNOWN"),
            ("access_kind", ""),
            ("containing_type", ""),
            ("containing_type", "UNKNOWN"),
        )
        for column, value in cases:
            with self.subTest(column=column, value=value):
                source = _discovery()
                source.execute(
                    f"""
                    UPDATE native_field_accesses
                    SET {column}=?
                    WHERE access_id='field-1'
                    """,
                    (value,),
                )
                core = _core()

                result = materialize_native_gold_set(
                    source,
                    core,
                    config_path=CONFIG,
                    generated_at="2026-07-27T00:00:00Z",
                )

                self.assertEqual(
                    result["nativeConfirmedFieldAccesses"],
                    0,
                )
                self.assertEqual(
                    core.execute(
                        """
                        SELECT status
                        FROM native_field_accesses
                        """
                    ).fetchone()[0],
                    "AMBIGUOUS",
                )
                source.close()
                core.close()

    def test_field_instruction_requires_recovered_explicit_uri(self):
        invalid_values = (
            "UNKNOWN",
            "NOT_RECOVERED",
            "SOURCE_NOT_AVAILABLE",
            "instruction-123",
            "native-slice://",
        )
        for instruction_id in invalid_values:
            with self.subTest(instruction_id=instruction_id):
                source = _discovery()
                source.execute(
                    """
                    UPDATE native_field_accesses
                    SET source_instruction_or_slice_id=?
                    WHERE access_id='field-1'
                    """,
                    (instruction_id,),
                )
                core = _core()

                result = materialize_native_gold_set(
                    source,
                    core,
                    config_path=CONFIG,
                    generated_at="2026-07-27T00:00:00Z",
                )

                self.assertEqual(
                    result["nativeConfirmedFieldAccesses"],
                    0,
                )
                self.assertEqual(
                    core.execute(
                        "SELECT COUNT(*) FROM native_field_accesses"
                    ).fetchone()[0],
                    0,
                )
                source.close()
                core.close()

    def test_confirmed_link_requires_recovered_graph_uri_and_function(self):
        cases = (
            ("UNKNOWN", "GenerateCrateItems"),
            ("NOT_RECOVERED", "GenerateCrateItems"),
            ("SOURCE_NOT_AVAILABLE", "GenerateCrateItems"),
            ("EventGraph", "GenerateCrateItems"),
            ("ftp://fixture/callsite", "GenerateCrateItems"),
            (
                "blueprint-graph://unresolved/callsite",
                "GenerateCrateItems",
            ),
            ("bp://fixture/callsite", ""),
            ("bp://fixture/callsite", "UNKNOWN"),
            ("bp://fixture/callsite", "NOT_RECOVERED"),
        )
        for graph_uri, function_name in cases:
            with self.subTest(
                graph_uri=graph_uri,
                function_name=function_name,
            ):
                source = _discovery()
                source.execute(
                    """
                    UPDATE blueprint_native_edges
                    SET blueprint_graph_evidence_id=?,
                        blueprint_function_name=?
                    WHERE edge_id='verified'
                    """,
                    (graph_uri, function_name),
                )
                core = _core()

                result = materialize_native_gold_set(
                    source,
                    core,
                    config_path=CONFIG,
                    generated_at="2026-07-27T00:00:00Z",
                )

                self.assertEqual(
                    result["blueprintNativeConfirmedLinks"],
                    0,
                )
                self.assertEqual(
                    core.execute(
                        """
                        SELECT status
                        FROM native_blueprint_links
                        WHERE link_id='verified'
                        """
                    ).fetchone()[0],
                    "CANDIDATE",
                )
                source.close()
                core.close()

    def test_blueprint_graph_scheme_is_accepted_for_confirmed_link(self):
        source = _discovery()
        source.execute(
            """
            UPDATE blueprint_native_edges
            SET blueprint_graph_evidence_id=
                'blueprint-graph://fixture/callsite'
            WHERE edge_id='verified'
            """
        )
        core = _core()

        result = materialize_native_gold_set(
            source,
            core,
            config_path=CONFIG,
            generated_at="2026-07-27T00:00:00Z",
        )

        self.assertEqual(result["blueprintNativeConfirmedLinks"], 1)
        self.assertEqual(
            core.execute(
                """
                SELECT status
                FROM native_blueprint_links
                WHERE link_id='verified'
                """
            ).fetchone()[0],
            "CONFIRMED",
        )
        source.close()
        core.close()

    def test_low_confidence_field_access_is_not_counted_confirmed(self):
        source = _discovery()
        source.execute(
            """
            UPDATE native_field_accesses
            SET confidence='LOW'
            WHERE access_id='field-1'
            """
        )
        core = _core()

        result = materialize_native_gold_set(
            source,
            core,
            config_path=CONFIG,
            generated_at="2026-07-27T00:00:00Z",
        )

        self.assertEqual(result["nativeConfirmedFunctions"], 1)
        self.assertEqual(result["nativeConfirmedFieldAccesses"], 0)
        self.assertEqual(
            core.execute(
                """
                SELECT status, confidence
                FROM native_field_accesses
                WHERE field_name='ItemRating'
                """
            ).fetchone(),
            ("AMBIGUOUS", "LOW"),
        )
        source.close()
        core.close()

    def test_mismatched_binary_fails_closed(self):
        source = _discovery(mismatched_binary=True)
        core = _core()
        result = materialize_native_gold_set(
            source,
            core,
            config_path=CONFIG,
            generated_at="2026-07-27T00:00:00Z",
        )
        self.assertEqual(result["nativeConfirmedFunctions"], 0)
        self.assertEqual(
            core.execute(
                """
                SELECT gap_code FROM native_gold_targets
                WHERE target_id='loot-generate-crate'
                """
            ).fetchone()[0],
            "BINARY_MISMATCH",
        )
        self.assertEqual(
            core.execute(
                "SELECT COUNT(*) FROM native_blueprint_links WHERE status='CONFIRMED'"
            ).fetchone()[0],
            0,
        )
        source.close()
        core.close()

    def test_verified_callsite_with_low_confidence_stays_candidate(self):
        source = _discovery()
        source.execute(
            """
            UPDATE blueprint_native_edges
            SET confidence='LOW'
            WHERE edge_id='verified'
            """
        )
        core = _core()

        result = materialize_native_gold_set(
            source,
            core,
            config_path=CONFIG,
            generated_at="2026-07-27T00:00:00Z",
        )

        self.assertEqual(result["blueprintNativeConfirmedLinks"], 0)
        self.assertEqual(result["blueprintNativeCandidateLinks"], 2)
        self.assertEqual(
            core.execute(
                """
                SELECT status, confidence
                FROM native_blueprint_links
                WHERE link_id='verified'
                """
            ).fetchone(),
            ("CANDIDATE", "LOW"),
        )
        source.close()
        core.close()

    def test_native_revision_covers_signature_and_field_access_inputs(self):
        def materialize(
            source: sqlite3.Connection,
        ) -> tuple[str, str, str]:
            core = _core()
            materialize_native_gold_set(
                source,
                core,
                config_path=CONFIG,
                generated_at="2026-07-27T00:00:00Z",
            )
            fingerprint = core.execute(
                """
                SELECT source_fingerprint
                FROM source_revisions
                WHERE source_kind='native_evidence'
                """
            ).fetchone()[0]
            signature = core.execute(
                "SELECT signature FROM native_functions"
            ).fetchone()[0]
            field_name = core.execute(
                "SELECT field_name FROM native_field_accesses"
            ).fetchone()[0]
            core.close()
            return str(fingerprint), str(signature), str(field_name)

        baseline_source = _discovery()
        signature_source = _discovery()
        signature_source.execute(
            """
            UPDATE native_symbols
            SET signature='bool __thiscall GenerateCrateItems(double)'
            """
        )
        field_source = _discovery()
        field_source.execute(
            """
            UPDATE native_field_accesses
            SET field_name='ItemQuality'
            WHERE access_id='field-1'
            """
        )

        baseline = materialize(baseline_source)
        signature_variant = materialize(signature_source)
        field_variant = materialize(field_source)

        self.assertNotEqual(signature_variant[0], baseline[0])
        self.assertNotEqual(field_variant[0], baseline[0])
        self.assertNotEqual(signature_variant[1], baseline[1])
        self.assertNotEqual(field_variant[2], baseline[2])
        baseline_source.close()
        signature_source.close()
        field_source.close()


if __name__ == "__main__":
    unittest.main()
