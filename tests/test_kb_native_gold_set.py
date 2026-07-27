from __future__ import annotations

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

from blueprint_translator.kb_vnext.native_gold_set import (  # noqa: E402
    _blueprint_graph_revision,
    load_native_gold_set,
    materialize_native_gold_set,
)
from blueprint_translator.kb_vnext.native_ingest import (  # noqa: E402
    load_native_evidence_corpus,
)
from blueprint_translator.kb_vnext.invalidation import (  # noqa: E402
    plan_invalidation,
    rebuild_invalidation_dependencies,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    FULL_CORE_SCHEMA_SQL,
)


CONFIG = PROJECT_ROOT / "ontology" / "native_gold_set.v1.json"
REAL_NATIVE_ROOT = PROJECT_ROOT / "native_evidence"


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
    def test_exact_reference_uri_reuses_real_capture_source_revision(self):
        core = _core()
        source_uri = "bp://asset@revision"
        core.execute(
            """
            INSERT INTO source_revisions(
                revision_id, source_kind, source_uri, source_fingerprint,
                producer_version, schema_version, generated_at,
                freshness_status
            ) VALUES (
                576398, 'blueprint_evidence', ?, ?, 'capture-reader',
                'ark.blueprint.evidence.v2',
                '2026-07-27T00:00:00Z', 'FRESH'
            )
            """,
            (source_uri, "a" * 64),
        )
        expected = core.execute(
            """
            SELECT revision_id FROM source_revisions
            WHERE source_uri=?
            """,
            (source_uri,),
        ).fetchone()[0]

        actual = _blueprint_graph_revision(
            core,
            row={
                "edge_id": "verified",
                "blueprint_graph_evidence_id": (
                    f"{source_uri}/g/1/n/2/reference/function/exact"
                ),
            },
            gold_version="fixture",
            generated_at="2026-07-27T00:00:00Z",
        )

        self.assertEqual(actual, expected)
        self.assertEqual(actual, 576398)
        self.assertEqual(
            core.execute(
                """
                SELECT COUNT(*) FROM source_revisions
                WHERE source_kind='blueprint_evidence'
                """
            ).fetchone()[0],
            1,
        )
        discovery = _discovery()
        discovery.execute(
            """
            UPDATE blueprint_native_edges
            SET blueprint_graph_evidence_id=?
            WHERE edge_id='verified'
            """,
            (f"{source_uri}/g/1/n/2/reference/function/exact",),
        )
        materialize_native_gold_set(
            discovery,
            core,
            config_path=CONFIG,
            generated_at="2026-07-27T00:00:00Z",
        )
        self.assertEqual(
            core.execute(
                """
                SELECT blueprint_graph_source_revision_id
                FROM native_blueprint_links
                WHERE link_id='verified' AND status='CONFIRMED'
                """
            ).fetchone()[0],
            576398,
        )
        discovery.close()
        core.close()

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

    def test_manifest_rejects_noncanonical_build_and_target_identities(self):
        variants = {
            "uppercase_binary_sha": lambda payload: payload.__setitem__(
                "binarySha256",
                str(payload["binarySha256"]).upper(),
            ),
            "uppercase_pdb_sha": lambda payload: payload.__setitem__(
                "pdbSha256",
                str(payload["pdbSha256"]).upper(),
            ),
            "uppercase_pdb_guid": lambda payload: payload.__setitem__(
                "pdbGuidAge",
                str(payload["pdbGuidAge"]).upper(),
            ),
            "leading_zero_rva": lambda payload: payload["targets"][0].__setitem__(
                "rva",
                "0x0" + str(payload["targets"][0]["rva"])[2:],
            ),
            "signed_rva": lambda payload: payload["targets"][0].__setitem__(
                "rva",
                "0x+" + str(payload["targets"][0]["rva"])[2:],
            ),
        }
        baseline = json.loads(CONFIG.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for label, mutate in variants.items():
                with self.subTest(identity=label):
                    payload = json.loads(json.dumps(baseline))
                    mutate(payload)
                    path = root / f"{label}.json"
                    path.write_text(
                        json.dumps(payload),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        ValueError,
                        "canonical|SHA-256|GUID|RVA",
                    ):
                        load_native_gold_set(path)

    def test_explicit_missing_native_root_never_falls_back_to_discovery_symbols(
        self,
    ):
        source = _discovery()
        core = _core()
        with tempfile.TemporaryDirectory() as temp_dir:
            result = materialize_native_gold_set(
                source,
                core,
                config_path=CONFIG,
                generated_at="2026-07-27T00:00:00Z",
                native_root=Path(temp_dir) / "missing-native-root",
            )

        self.assertEqual(result["nativeEvidenceSets"], 0)
        self.assertEqual(result["nativeEvidenceFunctions"], 0)
        self.assertEqual(result["nativeGoldTargets"], 20)
        self.assertEqual(result["nativeConfirmedFunctions"], 0)
        self.assertEqual(result["nativeTargetGaps"], 20)
        self.assertEqual(
            core.execute(
                """
                SELECT COUNT(*) FROM source_revisions
                WHERE source_kind='native_evidence'
                """
            ).fetchone()[0],
            0,
        )
        source.close()
        core.close()

    @unittest.skipUnless(
        len(
            list(
                REAL_NATIVE_ROOT.glob(
                    "stores/*/ark-*/evidence.manifest.json"
                )
            )
        )
        >= 2,
        "real production Native Evidence stores are not present",
    )
    def test_real_corpus_materializes_per_store_revisions_and_all_gold_targets(
        self,
    ):
        corpus = load_native_evidence_corpus(REAL_NATIVE_ROOT)
        gold = load_native_gold_set(CONFIG)
        expected_fanout: dict[str, int] = {}
        for target in gold["targets"]:
            function = corpus.match_gold_target(
                target["recipeId"],
                target["qualifiedSymbol"],
                target["rva"],
            )
            self.assertIsNotNone(function)
            assert function is not None
            matching_origins = [
                origin
                for origin in function.origins
                if origin.recipe_id == target["recipeId"]
            ]
            self.assertEqual(len(function.origins), 1)
            self.assertEqual(len(matching_origins), 1)
            evidence_set_id = matching_origins[0].evidence_set_id
            expected_fanout[evidence_set_id] = (
                expected_fanout.get(evidence_set_id, 0) + 1
            )
        source = _discovery()
        core = _core()

        result = materialize_native_gold_set(
            source,
            core,
            config_path=CONFIG,
            generated_at="2099-01-01T00:00:00Z",
            native_root=REAL_NATIVE_ROOT,
        )

        self.assertEqual(result["nativeEvidenceSets"], 2)
        self.assertEqual(result["nativeEvidenceFunctions"], 204)
        self.assertEqual(result["nativeGoldTargets"], 20)
        self.assertEqual(result["nativeConfirmedFunctions"], 20)
        self.assertEqual(result["nativeTargetGaps"], 0)
        self.assertEqual(result["nativeConfirmedFieldAccesses"], 0)
        self.assertEqual(result["blueprintNativeConfirmedLinks"], 0)
        self.assertEqual(result["blueprintNativeCandidateLinks"], 2)
        revisions = list(
            core.execute(
                """
                SELECT source_uri, source_fingerprint, producer_version,
                       schema_version, generated_at, freshness_status
                FROM source_revisions
                WHERE source_kind='native_evidence'
                ORDER BY source_uri
                """
            )
        )
        expected_revisions = sorted(
            (
                evidence_set.evidence_set_id,
                evidence_set.source_sha256,
                evidence_set.generator_commit,
                "blueprint-to-code-native-evidence-set/v2",
                evidence_set.generated_at,
                "FRESH",
            )
            for evidence_set in corpus.evidence_sets
        )
        self.assertEqual(revisions, expected_revisions)
        self.assertNotIn(
            "2099-01-01T00:00:00Z",
            {row[4] for row in revisions},
        )
        self.assertEqual(
            core.execute(
                """
                SELECT COUNT(*), COUNT(DISTINCT function.source_revision_id)
                FROM native_gold_targets AS target
                JOIN native_functions AS function
                  ON function.native_function_id=target.native_function_id
                JOIN source_revisions AS revision
                  ON revision.revision_id=function.source_revision_id
                WHERE target.status='CONFIRMED'
                  AND target.gap_code=''
                  AND function.status='CONFIRMED'
                  AND function.confidence='HIGH'
                  AND revision.source_kind='native_evidence'
                  AND revision.freshness_status='FRESH'
                """
            ).fetchone(),
            (20, 2),
        )
        actual_fanout = dict(
            core.execute(
                """
                SELECT revision.source_uri, COUNT(*)
                FROM native_functions AS function
                JOIN source_revisions AS revision
                  ON revision.revision_id=function.source_revision_id
                GROUP BY revision.source_uri
                """
            )
        )
        self.assertEqual(actual_fanout, expected_fanout)
        self.assertEqual(sorted(actual_fanout.values()), [4, 16])
        self.assertEqual(
            core.execute(
                "SELECT COUNT(*) FROM native_field_accesses"
            ).fetchone()[0],
            0,
        )
        rebuild_invalidation_dependencies(core)
        self.assertEqual(
            core.execute(
                """
                SELECT COUNT(*), COUNT(DISTINCT dependency.upstream_revision_id)
                FROM invalidation_dependencies AS dependency
                JOIN source_revisions AS revision
                  ON revision.revision_id=dependency.upstream_revision_id
                WHERE dependency.downstream_kind='NATIVE_FUNCTION'
                  AND dependency.dependency_reason='NATIVE_BUILD_BINDING'
                  AND revision.source_kind='native_evidence'
                """
            ).fetchone(),
            (20, 2),
        )
        native_plans = []
        for revision_id, _source_uri in core.execute(
            """
            SELECT revision_id, source_uri
            FROM source_revisions
            WHERE source_kind='native_evidence'
            ORDER BY source_uri
            """
        ):
            plan = plan_invalidation(
                core,
                event_kind="NATIVE",
                upstream_revision_id=int(revision_id),
            )
            native_plans.append(
                set(plan.downstream["NATIVE_FUNCTION"])
            )
        self.assertEqual(
            sorted(len(function_ids) for function_ids in native_plans),
            [4, 16],
        )
        self.assertTrue(native_plans[0].isdisjoint(native_plans[1]))
        self.assertEqual(len(native_plans[0] | native_plans[1]), 20)
        source.close()
        core.close()

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

    def test_verified_callsite_with_unbound_capture_revision_stays_candidate(
        self,
    ):
        source = _discovery()
        source.execute(
            """
            UPDATE blueprint_native_edges
            SET blueprint_graph_evidence_id=?
            WHERE edge_id='verified'
            """,
            (
                "bp://asset@stale-revision/g/1/n/2/"
                "reference/function/exact",
            ),
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
                SELECT link.status, revision.freshness_status
                FROM native_blueprint_links AS link
                JOIN source_revisions AS revision
                  ON revision.revision_id =
                     link.blueprint_graph_source_revision_id
                WHERE link.link_id='verified'
                """
            ).fetchone(),
            ("CANDIDATE", "STALE"),
        )
        source.close()
        core.close()

    def test_verified_callsite_with_incomplete_fresh_revision_stays_candidate(
        self,
    ):
        source_uri = "bp://asset@incomplete-revision"
        graph_evidence_uri = (
            f"{source_uri}/g/1/n/2/reference/function/exact"
        )
        source = _discovery()
        source.execute(
            """
            UPDATE blueprint_native_edges
            SET blueprint_graph_evidence_id=?
            WHERE edge_id='verified'
            """,
            (graph_evidence_uri,),
        )
        core = _core()
        core.execute(
            """
            INSERT INTO source_revisions(
                source_kind, source_uri, source_fingerprint,
                producer_version, schema_version, generated_at,
                freshness_status
            ) VALUES (
                'blueprint_evidence', ?, 'UNKNOWN', 'UNKNOWN', 'UNKNOWN',
                '2026-07-27T00:00:00Z', 'FRESH'
            )
            """,
            (source_uri,),
        )

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
                SELECT link.status, revision.freshness_status
                FROM native_blueprint_links AS link
                JOIN source_revisions AS revision
                  ON revision.revision_id =
                     link.blueprint_graph_source_revision_id
                WHERE link.link_id='verified'
                """
            ).fetchone(),
            ("CANDIDATE", "FRESH"),
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
