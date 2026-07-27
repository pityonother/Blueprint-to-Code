from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.adapters import (  # noqa: E402
    ADAPTER_VERSION,
)
from blueprint_translator.kb_vnext.quality_gates import (  # noqa: E402
    QUALITY_GATE_SCHEMA,
    _class_closure_metrics,
    _effective_candidate_metrics,
    _integrity_metrics,
    _privacy_scan,
    _query_benchmark_gates,
    _registration_confidence_gate,
    _registration_confidence_metrics,
    _typed_map_usage_gates,
    _typed_map_usage_metrics,
    publish_gate_report,
)
from blueprint_translator.kb_vnext.ontology import (  # noqa: E402
    load_ontology,
)
from blueprint_translator.kb_vnext.projections import (  # noqa: E402
    PROJECTION_SCHEMA_SQL,
    PROJECTION_SCHEMA_VERSION,
    compute_projection_artifact_content_digest,
)
from blueprint_translator.kb_vnext.semantic_quality import (  # noqa: E402
    _semantic_fact_metrics,
    _semantic_projection_metrics,
    semantic_quality_gates,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    FULL_CORE_SCHEMA_SQL,
)


PROJECTION_NAMES = (
    "buff_effects",
    "loot_entries",
    "item_properties",
    "status_values",
    "harvest_rules",
    "mission_rewards",
)
CURRENT_ONTOLOGY_VERSION = load_ontology(PROJECT_ROOT / "ontology").version
CURRENT_ADAPTER_VERSION = ADAPTER_VERSION
PROJECTION_ADAPTER_RULES = {
    "buff_effects": ("buffs", "buff.timing.v1"),
    "loot_entries": ("loot", "loot.numeric-config.v1"),
    "item_properties": ("primal_items", "item.number-property.v1"),
    "status_values": (
        "status_components",
        "status.numeric-value.v1",
    ),
    "harvest_rules": ("harvest", "harvest.resource-rules.v1"),
    "mission_rewards": ("missions", "mission.currency-reward.v1"),
}
PROJECTION_FACT_TYPES = {
    "buff_effects": "STATUS_EFFECT",
    "loot_entries": "LOOT_ENTRY",
    "item_properties": "ITEM_PROPERTY",
    "status_values": "STATUS_VALUE",
    "harvest_rules": "HARVEST_RULE",
    "mission_rewards": "MISSION_REWARD",
}
PROJECTION_SOURCE_MODES = {
    name: (
        "LEGACY_TABLE"
        if name in {"buff_effects", "status_values"}
        else "CORE_TYPED_FACT"
    )
    for name in PROJECTION_NAMES
}


def _semantic_core_fixture() -> sqlite3.Connection:
    core = sqlite3.connect(":memory:")
    core.executescript(
        """
        CREATE TABLE facts(
            fact_id INTEGER PRIMARY KEY,
            fact_type TEXT NOT NULL,
            value_kind TEXT NOT NULL,
            value_text TEXT,
            value_number REAL,
            value_integer INTEGER,
            value_json TEXT,
            status TEXT NOT NULL,
            current INTEGER NOT NULL,
            fact_name TEXT NOT NULL DEFAULT '',
            subject_entity_id INTEGER NOT NULL DEFAULT 1,
            declared_on_entity_id INTEGER,
            scope_kind TEXT NOT NULL DEFAULT 'DECLARED',
            unit TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL DEFAULT 'HIGH',
            ontology_version TEXT NOT NULL DEFAULT 'fixture-placeholder'
        );
        CREATE TABLE metadata(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE entities(
            entity_id INTEGER PRIMARY KEY,
            canonical_uri TEXT NOT NULL
        );
        CREATE TABLE source_revisions(
            revision_id INTEGER PRIMARY KEY,
            source_kind TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            freshness_status TEXT NOT NULL
        );
        CREATE TABLE fact_evidence(
            fact_id INTEGER NOT NULL,
            source_revision_id INTEGER NOT NULL,
            evidence_uri TEXT NOT NULL,
            evidence_role TEXT NOT NULL
        );
        CREATE TABLE effective_facts(
            entity_id INTEGER NOT NULL,
            fact_type TEXT NOT NULL,
            fact_name TEXT NOT NULL,
            fact_id INTEGER,
            resolution_status TEXT NOT NULL
        );
        CREATE TABLE effective_fact_candidates(
            entity_id INTEGER NOT NULL,
            fact_type TEXT NOT NULL,
            fact_name TEXT NOT NULL,
            candidate_fact_id INTEGER NOT NULL,
            declared_on_entity_id INTEGER NOT NULL,
            inheritance_depth INTEGER NOT NULL,
            path_status TEXT NOT NULL,
            selected INTEGER NOT NULL,
            rejection_reason TEXT NOT NULL,
            PRIMARY KEY(
                entity_id, fact_type, fact_name, candidate_fact_id
            )
        );
        CREATE TABLE projection_runs(
            projection_name TEXT NOT NULL,
            projection_version TEXT NOT NULL,
            source_revision_set_hash TEXT NOT NULL,
            ontology_version TEXT NOT NULL,
            built_at TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            validation_status TEXT NOT NULL
        );
        CREATE TABLE semantic_adapter_runs(
            adapter_id TEXT NOT NULL,
            adapter_version TEXT NOT NULL,
            validation_status TEXT NOT NULL
        );
        CREATE TABLE semantic_adapter_decisions(
            decision_key TEXT PRIMARY KEY,
            adapter_id TEXT NOT NULL,
            adapter_version TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            source_mode TEXT NOT NULL,
            property_name TEXT NOT NULL,
            decision_status TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            source_fact_id INTEGER NOT NULL,
            semantic_fact_id INTEGER NOT NULL,
            legacy_lineage_id INTEGER,
            source_revision_id INTEGER NOT NULL,
            evidence_uri TEXT NOT NULL
        );

        INSERT INTO entities VALUES (1, '/Game/Test/Fixture.Fixture');
        INSERT INTO source_revisions VALUES (
            1, 'blueprint_evidence', 'ark.blueprint.evidence.v2', 'FRESH'
        );
        INSERT INTO source_revisions VALUES (
            2, 'blueprint_evidence', 'ark.blueprint.evidence.v2', 'STALE'
        );
        INSERT INTO metadata VALUES (
            'ontology_version', 'fixture-placeholder'
        );

        INSERT INTO facts(
            fact_id, fact_type, value_kind, value_text, value_number,
            value_integer, value_json, status, current
        ) VALUES
            (1, 'ITEM_PROPERTY', 'NUMBER', NULL, 2.5, NULL, NULL,
             'CONFIRMED', 1),
            (2, 'DECLARED_DEFAULT', 'FINGERPRINT', 'hash', NULL, NULL, NULL,
             'CONFIRMED', 1),
            (3, 'DECLARED_DEFAULT', 'NUMBER', NULL, 1.0, NULL, NULL,
             'LEGACY_UNVERIFIED', 1),
            (4, 'DECLARED_DEFAULT', 'UNKNOWN', NULL, NULL, NULL, NULL,
             'NOT_RECOVERED', 1),
            (5, 'STATUS_EFFECT', 'CONFIRMED_EMPTY', NULL, NULL, NULL, NULL,
             'CONFIRMED_EMPTY', 1),
            (6, 'FORMULA', 'TEXT', 'x * 2', NULL, NULL, NULL,
             'VERIFIED', 1);

        INSERT INTO fact_evidence VALUES
            (1, 1, 'fixture://item/weight', 'FIXTURE'),
            (2, 1, 'fixture://default/fingerprint', 'FIXTURE'),
            (3, 1, 'fixture://legacy/value', 'FIXTURE'),
            (4, 1, 'fixture://missing/value', 'FIXTURE'),
            (5, 1, 'fixture://status/empty', 'FIXTURE'),
            (6, 2, 'fixture://formula/stale', 'FIXTURE');

        INSERT INTO effective_facts VALUES
            (1, 'EFFECTIVE_DEFAULT', 'One', 1, 'RESOLVED'),
            (1, 'EFFECTIVE_DEFAULT', 'Two', 2, 'RESOLVED'),
            (1, 'EFFECTIVE_DEFAULT', 'Three', 3, 'RESOLVED'),
            (1, 'EFFECTIVE_DEFAULT', 'Four', 4, 'RESOLVED'),
            (1, 'EFFECTIVE_DEFAULT', 'Five', 5, 'RESOLVED'),
            (1, 'EFFECTIVE_DEFAULT', 'Six', NULL, 'AMBIGUOUS_INHERITANCE'),
            (1, 'EFFECTIVE_DEFAULT', 'Gap', NULL, 'PARENT_CHAIN_OPEN');
        """
    )
    return core


def _projection_fixture(
    snapshot_root: Path,
) -> tuple[sqlite3.Connection, dict[str, object]]:
    core = _semantic_core_fixture()
    core.execute(
        """
        UPDATE metadata SET value=?
        WHERE key='ontology_version'
        """,
        (CURRENT_ONTOLOGY_VERSION,),
    )
    core.execute("DELETE FROM effective_facts")
    core.execute("DELETE FROM fact_evidence")
    core.execute("DELETE FROM facts")
    core.execute("DELETE FROM entities")
    core.executemany(
        "INSERT INTO entities VALUES (?, ?)",
        [
            (fact_id, f"/Game/Test/{name}.{name}")
            for fact_id, name in enumerate(PROJECTION_NAMES, start=1)
        ],
    )
    core.executemany(
        """
        INSERT INTO facts(
            fact_id, fact_type, value_kind, value_text, value_number,
            value_integer, value_json, status, current, fact_name,
            subject_entity_id, scope_kind, unit, confidence,
            ontology_version
        ) VALUES (
            ?, ?, 'NUMBER', NULL, 1.0, NULL, NULL, 'CONFIRMED', 1,
            ?, ?, 'DERIVED_STATIC', '', 'HIGH', ?
        )
        """,
        [
            (1, "STATUS_EFFECT", "Fact1", 1, CURRENT_ONTOLOGY_VERSION),
            (2, "LOOT_ENTRY", "Fact2", 2, CURRENT_ONTOLOGY_VERSION),
            (3, "ITEM_PROPERTY", "Fact3", 3, CURRENT_ONTOLOGY_VERSION),
            (4, "STATUS_VALUE", "Fact4", 4, CURRENT_ONTOLOGY_VERSION),
            (5, "HARVEST_RULE", "Fact5", 5, CURRENT_ONTOLOGY_VERSION),
            (6, "MISSION_REWARD", "Fact6", 6, CURRENT_ONTOLOGY_VERSION),
        ],
    )
    core.executemany(
        """
        INSERT INTO facts(
            fact_id, fact_type, value_kind, value_text, value_number,
            value_integer, value_json, status, current, fact_name,
            subject_entity_id, scope_kind, unit, confidence,
            ontology_version
        ) VALUES (
            ?, 'DECLARED_DEFAULT', 'NUMBER', NULL, 1.0,
            NULL, NULL, 'CONFIRMED', 1, ?, ?, 'DECLARED', '',
            'HIGH', ?
        )
        """,
        [
            (
                100 + fact_id,
                f"Fact{fact_id}",
                fact_id,
                CURRENT_ONTOLOGY_VERSION,
            )
            for fact_id in range(1, 7)
        ],
    )
    core.executemany(
        "INSERT INTO fact_evidence VALUES (?, 1, ?, ?)",
        [
            (
                fact_id,
                f"fixture://projection/{fact_id}",
                "SEMANTIC_ADAPTER:"
                f"{PROJECTION_ADAPTER_RULES[name][1]}",
            )
            for fact_id, name in enumerate(PROJECTION_NAMES, start=1)
        ]
        + [
            (
                100 + fact_id,
                f"fixture://projection/{fact_id}",
                "DEFAULT_VALUE_ACTUAL",
            )
            for fact_id in range(1, 7)
        ],
    )
    core.executemany(
        """
        INSERT INTO effective_facts VALUES (
            1, 'EFFECTIVE_DEFAULT', ?, ?, 'RESOLVED'
        )
        """,
        [(f"Fact{fact_id}", fact_id) for fact_id in range(1, 7)],
    )
    core.executemany(
        """
        INSERT INTO semantic_adapter_runs VALUES (?, ?, 'VALID')
        """,
        [
            (adapter_id, CURRENT_ADAPTER_VERSION)
            for adapter_id, _rule_id in PROJECTION_ADAPTER_RULES.values()
        ],
    )
    core.executemany(
        """
        INSERT INTO semantic_adapter_decisions(
            decision_key, adapter_id, adapter_version, rule_id,
            source_mode, property_name, decision_status, reason_code,
            source_fact_id, semantic_fact_id, legacy_lineage_id,
            source_revision_id, evidence_uri
        ) VALUES (
            ?, ?, ?, ?, ?, ?, 'PROMOTED',
            'VERIFIED', ?, ?, ?, 1, ?
        )
        """,
        [
            (
                f"fixture-decision://{fact_id}",
                PROJECTION_ADAPTER_RULES[name][0],
                CURRENT_ADAPTER_VERSION,
                PROJECTION_ADAPTER_RULES[name][1],
                PROJECTION_SOURCE_MODES[name],
                f"Fact{fact_id}",
                100 + fact_id,
                fact_id,
                (
                    fact_id
                    if PROJECTION_SOURCE_MODES[name] == "LEGACY_TABLE"
                    else None
                ),
                f"fixture://projection/{fact_id}",
            )
            for fact_id, name in enumerate(PROJECTION_NAMES, start=1)
        ],
    )
    revision_hashes = {
        name: hashlib.sha256(
            f"fixture-revision:{name}".encode("utf-8")
        ).hexdigest()
        for name in PROJECTION_NAMES
    }
    core.executemany(
        """
        INSERT INTO projection_runs VALUES (
            ?, 'v2', ?, ?,
            '2026-07-27T00:00:00+00:00', 1, 'VALID'
        )
        """,
        [
            (name, revision_hashes[name], CURRENT_ONTOLOGY_VERSION)
            for name in PROJECTION_NAMES
        ],
    )
    exports = snapshot_root / "domain_exports"
    exports.mkdir(parents=True)
    review_contract = {
        "schema": "ark-kb-projection-review/v1",
        "version": "fixture-review/v1",
        "projections": {
            name: [
                {
                    "reviewId": f"fixture-review://{fact_id}",
                    "canonicalUri": f"/Game/Test/{name}.{name}",
                    "factType": PROJECTION_FACT_TYPES[name],
                    "factName": f"Fact{fact_id}",
                    "valueKind": "NUMBER",
                    "valueNumber": 1.0,
                    "evidenceUri": f"fixture://projection/{fact_id}",
                }
            ]
            for fact_id, name in enumerate(PROJECTION_NAMES, start=1)
        },
    }
    review_path = snapshot_root / "projection_review.v1.json"
    review_path.write_text(
        json.dumps(
            review_contract,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    review_config_sha256 = hashlib.sha256(
        review_path.read_bytes()
    ).hexdigest()
    projection_entries: dict[str, object] = {}
    for fact_id, name in enumerate(PROJECTION_NAMES, start=1):
        path = exports / f"{name}.sqlite"
        projection = sqlite3.connect(path)
        try:
            projection.executescript(PROJECTION_SCHEMA_SQL)
            projection.executemany(
                "INSERT INTO metadata VALUES (?, ?)",
                (
                    ("schema_version", PROJECTION_SCHEMA_VERSION),
                    ("projection_name", name),
                    ("projection_version", "v2"),
                    ("source_revision_set_hash", revision_hashes[name]),
                    ("ontology_version", CURRENT_ONTOLOGY_VERSION),
                    ("built_at", "2026-07-27T00:00:00+00:00"),
                    ("truth_source", "core.sqlite"),
                    ("review_version", "fixture-review/v1"),
                    ("review_status", "FIXTURE_EXACT"),
                    ("review_config_sha256", review_config_sha256),
                ),
            )
            projection.execute(
                """
                INSERT INTO projection_rows(
                    fact_id, entity_id, canonical_uri, fact_type,
                    fact_name, scope_kind, value_kind, value_number,
                    unit, status, confidence, ontology_version,
                    completeness_status,
                    evidence_count, source_revision_set_hash
                ) VALUES (
                    ?, ?, ?, ?, ?, 'DERIVED_STATIC', 'NUMBER', 1.0,
                    '', 'CONFIRMED', 'HIGH', ?, 'COMPLETE', 1, ?
                )
                """,
                (
                    fact_id,
                    fact_id,
                    f"/Game/Test/{name}.{name}",
                    PROJECTION_FACT_TYPES[name],
                    f"Fact{fact_id}",
                    CURRENT_ONTOLOGY_VERSION,
                    revision_hashes[name],
                ),
            )
            projection.execute(
                """
                INSERT INTO projection_evidence VALUES (
                    ?, 1, ?, ?, 'FRESH'
                )
                """,
                (
                    fact_id,
                    f"fixture://projection/{fact_id}",
                    "SEMANTIC_ADAPTER:"
                    f"{PROJECTION_ADAPTER_RULES[name][1]}",
                ),
            )
            projection.execute(
                """
                INSERT INTO projection_lineage VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, 'VERIFIED'
                )
                """,
                (
                    f"fixture-decision://{fact_id}",
                    fact_id,
                    100 + fact_id,
                    (
                        fact_id
                        if PROJECTION_SOURCE_MODES[name] == "LEGACY_TABLE"
                        else None
                    ),
                    PROJECTION_ADAPTER_RULES[name][0],
                    CURRENT_ADAPTER_VERSION,
                    PROJECTION_ADAPTER_RULES[name][1],
                    PROJECTION_SOURCE_MODES[name],
                ),
            )
            projection.execute(
                """
                INSERT INTO projection_reviews VALUES (
                    ?, ?, 'FIXTURE_EXACT', ?, 'fixture-review/v1'
                )
                """,
                (
                    f"fixture-review://{fact_id}",
                    fact_id,
                    f"fixture://projection/{fact_id}",
                ),
            )
            content_digest = (
                compute_projection_artifact_content_digest(projection)
            )
            projection.execute(
                "INSERT INTO metadata VALUES ('content_digest', ?)",
                (content_digest,),
            )
            projection.commit()
        finally:
            projection.close()
        table_counts = {
            "metadata": 11,
            "projection_evidence": 1,
            "projection_lineage": 1,
            "projection_reviews": 1,
            "projection_rows": 1,
        }
        projection_entries[name] = {
            "path": path.name,
            "schemaVersion": PROJECTION_SCHEMA_VERSION,
            "projectionVersion": "v2",
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "foreignKeyViolations": 0,
            "tableCounts": table_counts,
            "rows": 1,
            "evidenceRows": 1,
            "lineageRows": 1,
            "integrity": "ok",
            "sourceRevisionSetHash": revision_hashes[name],
            "contentDigest": content_digest,
            "ontologyVersion": CURRENT_ONTOLOGY_VERSION,
            "validationStatus": "VALID",
            "reviewVersion": "fixture-review/v1",
            "reviewConfigSha256": review_config_sha256,
            "reviewStatus": "FIXTURE_EXACT",
            "reviewedRows": 1,
            "reviewFailures": [],
            "completeRows": 1,
            "partialRows": 0,
            "unspecifiedRows": 0,
        }
    manifest: dict[str, object] = {
        "ontologyVersion": CURRENT_ONTOLOGY_VERSION,
        "counts": {
            "domainProjections": projection_entries
        }
    }
    return core, manifest


def _set_artifact_ontology_version(
    *,
    snapshot_root: Path,
    manifest: dict[str, object],
    projection_names: tuple[str, ...],
    ontology_version: str,
) -> None:
    entries = manifest["counts"]["domainProjections"]
    for projection_name in projection_names:
        path = (
            snapshot_root
            / "domain_exports"
            / f"{projection_name}.sqlite"
        )
        projection = sqlite3.connect(path)
        try:
            projection.execute(
                """
                UPDATE metadata SET value=?
                WHERE key='ontology_version'
                """,
                (ontology_version,),
            )
            projection.commit()
        finally:
            projection.close()
        entry = entries[projection_name]
        entry["bytes"] = path.stat().st_size
        entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()


class KnowledgeQualityGateTests(unittest.TestCase):
    def _manifest_root(self, root: Path) -> Path:
        manifests = root / "manifests"
        manifests.mkdir()
        manifest = {
            "schema": "ark-kb-vnext-snapshot/v1",
            "buildId": "fixture-build",
            "cutover": {
                "mode": "shadow",
                "defaultQuerySource": "legacy",
            },
        }
        for name in ("current.json", "fixture-build.json"):
            (manifests / name).write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
        return manifests

    def _report(self, *, eligible: bool) -> dict[str, object]:
        return {
            "schema": QUALITY_GATE_SCHEMA,
            "buildId": "fixture-build",
            "summary": {
                "total": 1,
                "passed": 1 if eligible else 0,
                "failed": 0 if eligible else 1,
                "cutoverEligible": eligible,
                "recommendation": (
                    "ready_for_default"
                    if eligible
                    else "keep_legacy_shadow"
                ),
            },
            "gates": [
                {
                    "id": "fixture.gate",
                    "passed": eligible,
                    "critical": True,
                }
            ],
            "benchmark": {"total": 120},
        }

    def test_benchmark_v2_gates_replace_legacy_self_selected_metrics(self):
        benchmark = {
            "schema": "ark-kb-query-benchmark/v2",
            "total": 120,
            "goldSet": {
                "selectionMode": "MANUAL_FIXED",
                "generatedFromCore": False,
                "fixedGoldCases": 120,
                "humanGoldCases": 120,
                "corpusReadyForCutover": True,
            },
            "protocolComplianceRate": 1.0,
            "semanticExactMatchRate": 0.95,
            "usableValueAnswerRate": 0.95,
            "evidenceBackedCompleteRate": 0.95,
            "expectedGapMatchedRate": 1.0,
            "wrongAnswerRate": 0.0,
            "unexpectedAmbiguousAnswerRate": 0.0,
            "staleLeakRate": 0.0,
            "candidateEdgeCompleteRate": 0.0,
            "identityOnlyNotCountedAsSemantic": True,
            "storagePathCoverage": {
                "core": True,
                "search": True,
                "cache": True,
                "complete": True,
            },
            "completeOrBoundedRate": 0.0,
            "simpleDbOnlyRate": 0.0,
            "unresolved": 120,
        }

        gates = _query_benchmark_gates(benchmark)

        gate_ids = {str(gate["id"]) for gate in gates}
        self.assertNotIn("queries.complete_or_bounded", gate_ids)
        self.assertNotIn("queries.simple_db_only", gate_ids)
        self.assertNotIn("queries.no_silent_unresolved", gate_ids)
        self.assertTrue(all(bool(gate["passed"]) for gate in gates))

    def test_registration_gate_rejects_noncomplete_high_confidence(self):
        core = sqlite3.connect(":memory:")
        core.executescript(
            """
            CREATE TABLE typed_registrations(
                status TEXT NOT NULL,
                confidence TEXT NOT NULL
            );
            CREATE TABLE edges(
                edge_type TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence TEXT NOT NULL
            );
            CREATE TABLE domain_memberships(
                membership_kind TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence TEXT NOT NULL
            );
            INSERT INTO typed_registrations VALUES
                ('LEGACY_UNVERIFIED', 'HIGH'),
                ('CONFIRMED', 'HIGH');
            INSERT INTO edges VALUES
                ('REGISTERS', 'CANDIDATE', 'CONFIRMED'),
                ('REFERENCES', 'CANDIDATE', 'HIGH');
            INSERT INTO domain_memberships VALUES
                ('TYPED_REGISTRATION', 'LEGACY_UNVERIFIED', 'HIGH'),
                ('CLASS_ANCESTRY', 'CANDIDATE', 'HIGH');
            """
        )

        metrics = _registration_confidence_metrics(core)
        failed = _registration_confidence_gate(metrics)
        core.execute(
            """
            UPDATE typed_registrations
            SET confidence='LOW'
            WHERE status='LEGACY_UNVERIFIED'
            """
        )
        core.execute(
            """
            UPDATE edges
            SET confidence='LOW'
            WHERE edge_type='REGISTERS' AND status='CANDIDATE'
            """
        )
        core.execute(
            """
            UPDATE domain_memberships
            SET confidence='LOW'
            WHERE membership_kind='TYPED_REGISTRATION'
              AND status='LEGACY_UNVERIFIED'
            """
        )
        passed = _registration_confidence_gate(
            _registration_confidence_metrics(core)
        )
        core.close()

        self.assertEqual(
            metrics,
            {
                "typedRegistrations": 1,
                "registrationEdges": 1,
                "typedMemberships": 1,
                "total": 3,
            },
        )
        self.assertEqual(
            failed["id"],
            "registrations.noncomplete_high_confidence",
        )
        self.assertFalse(failed["passed"])
        self.assertTrue(passed["passed"])

    def test_benchmark_v2_gates_fail_closed_for_current_corpus_gaps(self):
        gates = _query_benchmark_gates(
            {
                "schema": "ark-kb-query-benchmark/v2",
                "total": 120,
                "goldSet": {
                    "selectionMode": "MANUAL_FIXED",
                    "generatedFromCore": False,
                    "fixedGoldCases": 120,
                    "humanGoldCases": 5,
                    "corpusReadyForCutover": False,
                },
                "protocolComplianceRate": 1.0,
                "semanticExactMatchRate": 0.0,
                "usableValueAnswerRate": 0.0,
                "evidenceBackedCompleteRate": 0.0,
                "expectedGapMatchedRate": 1.0,
                "wrongAnswerRate": 0.0,
                "unexpectedAmbiguousAnswerRate": 0.0,
                "staleLeakRate": 0.0,
                "candidateEdgeCompleteRate": 0.0,
                "identityOnlyNotCountedAsSemantic": True,
                "storagePathCoverage": {
                    "core": True,
                    "search": True,
                    "cache": True,
                    "complete": True,
                },
            }
        )
        by_id = {str(gate["id"]): gate for gate in gates}

        self.assertFalse(by_id["queries.human_gold_cases"]["passed"])
        self.assertFalse(by_id["queries.corpus_ready_for_cutover"]["passed"])
        self.assertFalse(by_id["queries.semantic_exact_match"]["passed"])
        self.assertFalse(by_id["queries.usable_value_answer"]["passed"])
        self.assertFalse(
            by_id["queries.evidence_backed_complete"]["passed"]
        )
        self.assertTrue(by_id["queries.protocol_compliance"]["passed"])
        self.assertTrue(by_id["queries.no_wrong_answers"]["passed"])

    def test_benchmark_storage_paths_fail_closed_until_all_are_exercised(
        self,
    ):
        benchmark = {
            "schema": "ark-kb-query-benchmark/v2",
            "goldSet": {
                "selectionMode": "MANUAL_FIXED",
                "generatedFromCore": False,
                "fixedGoldCases": 120,
                "humanGoldCases": 120,
                "corpusReadyForCutover": True,
            },
            "protocolComplianceRate": 1.0,
            "semanticExactMatchRate": 1.0,
            "usableValueAnswerRate": 1.0,
            "evidenceBackedCompleteRate": 1.0,
            "expectedGapMatchedRate": 1.0,
            "wrongAnswerRate": 0.0,
            "unexpectedAmbiguousAnswerRate": 0.0,
            "staleLeakRate": 0.0,
            "candidateEdgeCompleteRate": 0.0,
            "identityOnlyNotCountedAsSemantic": True,
            "storagePathCoverage": {
                "core": True,
                "search": False,
                "cache": False,
                "complete": False,
            },
        }

        by_id = {
            str(gate["id"]): gate
            for gate in _query_benchmark_gates(benchmark)
        }

        self.assertFalse(
            by_id["queries.storage_paths_covered"]["passed"]
        )

    def test_benchmark_v2_gates_fail_closed_without_metric_key_errors(self):
        gates = _query_benchmark_gates({})

        self.assertTrue(gates)
        self.assertTrue(all(not bool(gate["passed"]) for gate in gates))

    def test_typed_map_metrics_fail_closed_when_core_v4_contract_is_absent(self):
        core = sqlite3.connect(":memory:")
        core.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")

        metrics = _typed_map_usage_metrics(core)
        gates = _typed_map_usage_gates(metrics)

        self.assertFalse(metrics["capability"])
        self.assertEqual(
            metrics["error"],
            "TYPED_MAP_USAGE_CAPABILITY_MISSING",
        )
        self.assertTrue(all(not bool(gate["passed"]) for gate in gates))
        core.close()

    def test_typed_map_metrics_validate_confirmed_candidate_and_stale_rows(
        self,
    ):
        core = sqlite3.connect(":memory:")
        core.executescript(FULL_CORE_SCHEMA_SQL)
        core.execute(
            "INSERT INTO metadata VALUES ('schema_version', 'ark-kb-core/v4')"
        )
        core.executemany(
            """
            INSERT INTO source_revisions(
                revision_id, source_kind, source_uri, source_fingerprint,
                producer_version, schema_version, generated_at,
                freshness_status
            ) VALUES (?, 'map_fixture', ?, ?, 'test', 'v1',
                      '2026-07-27T00:00:00Z', ?)
            """,
            [
                (1, "map://fresh", "fresh-sha", "FRESH"),
                (2, "map://stale", "stale-sha", "STALE"),
            ],
        )
        core.executemany(
            """
            INSERT INTO entities(
                entity_id, canonical_uri, entity_kind, display_name,
                internal_name, status, confidence
            ) VALUES (?, ?, 'MAP_ASSET', '', '', 'CONFIRMED', 'HIGH')
            """,
            [
                (1, "/Game/Maps/Test.Test"),
                (2, "/Game/Test/A.A"),
                (3, "/Game/Test/B.B"),
                (4, "/Game/Test/C.C"),
            ],
        )
        core.executemany(
            """
            INSERT INTO edges(
                edge_id, source_entity_id, target_entity_id, edge_type,
                edge_strength, status, confidence, source_revision_id,
                evidence_uri, source_property, source_graph
            ) VALUES (?, 1, ?, 'MAP_DIRECT_REFERENCE', 'HARD', ?, 'HIGH',
                      ?, ?, 'AssetRegistryDependency', '')
            """,
            [
                (
                    1,
                    2,
                    "CONFIRMED",
                    1,
                    "map-evidence://asset-registry/confirmed",
                ),
                (
                    2,
                    3,
                    "CANDIDATE",
                    1,
                    "map-evidence://asset-registry/candidate",
                ),
                (
                    3,
                    4,
                    "STALE",
                    2,
                    "map-evidence://asset-registry/stale",
                ),
            ],
        )
        core.executemany(
            """
            INSERT INTO map_usage_edge_evidence(
                map_usage_id, edge_id, source_item_id, evidence_layer,
                map_family, map_kind, source_evidence_status, usage_status,
                freshness_status, claims_complete_map_usage,
                claims_spawn_coordinates, evidence_count,
                evidence_examples_json, extractor_version
            ) VALUES (?, ?, ?, 'ASSET_REGISTRY_HARD_PACKAGE_DEPENDENCY',
                      'test', 'PLAYABLE_MAP_EVIDENCE', ?, ?, ?, 0, 0, 1,
                      '[]', 'ark-kb-map-usage/v1')
            """,
            [
                (
                    "map-confirmed",
                    1,
                    "item-confirmed",
                    "CONFIRMED",
                    "CONFIRMED",
                    "FRESH",
                ),
                (
                    "map-candidate",
                    2,
                    "item-candidate",
                    "CANDIDATE",
                    "CANDIDATE",
                    "FRESH",
                ),
                (
                    "map-stale",
                    3,
                    "item-stale",
                    "CONFIRMED",
                    "CONFIRMED",
                    "STALE",
                ),
            ],
        )

        metrics = _typed_map_usage_metrics(core)
        gates = _typed_map_usage_gates(metrics)

        self.assertTrue(metrics["capability"])
        self.assertEqual(metrics["typedEdgeCount"], 3)
        self.assertEqual(metrics["confirmedCount"], 1)
        self.assertEqual(metrics["candidateCount"], 1)
        self.assertEqual(metrics["staleCount"], 1)
        self.assertEqual(metrics["invalidConfirmedRows"], 0)
        self.assertFalse(metrics["domainMembershipFallbackUsed"])
        self.assertTrue(all(bool(gate["passed"]) for gate in gates))
        core.close()

    def test_failed_gate_report_keeps_legacy_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifests = self._manifest_root(root)
            cutover = publish_gate_report(
                snapshot_root=root,
                report=self._report(eligible=False),
            )
            current = json.loads(
                (manifests / "current.json").read_text(encoding="utf-8")
            )
            build_manifest = json.loads(
                (manifests / "fixture-build.json").read_text(
                    encoding="utf-8"
                )
            )
            report_sha = hashlib.sha256(
                (root / "reports" / "quality_gates.json").read_bytes()
            ).hexdigest()
            self.assertEqual(current, build_manifest)
            self.assertEqual(
                current["qualityGates"]["sha256"],
                report_sha,
            )
        self.assertEqual(cutover["mode"], "shadow")
        self.assertEqual(cutover["defaultQuerySource"], "legacy")
        self.assertEqual(current["qualityGates"]["failed"], 1)

    def test_passing_gate_report_is_only_path_to_vnext_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._manifest_root(root)
            cutover = publish_gate_report(
                snapshot_root=root,
                report=self._report(eligible=True),
            )
        self.assertEqual(cutover["mode"], "ready")
        self.assertEqual(cutover["defaultQuerySource"], "vnext")

    def test_class_closure_gate_prefers_generated_then_asset_assignment(self):
        core = sqlite3.connect(":memory:")
        core.executescript(
            """
            CREATE TABLE knowledge_depth_policies(
                entity_id INTEGER PRIMARY KEY,
                depth_policy TEXT NOT NULL
            );
            CREATE TABLE asset_class_assignments(
                entity_id INTEGER NOT NULL,
                class_id INTEGER NOT NULL,
                assignment_kind TEXT NOT NULL
            );
            CREATE TABLE class_gaps(
                class_id INTEGER NOT NULL,
                gap_kind TEXT NOT NULL
            );

            INSERT INTO knowledge_depth_policies VALUES (1, 'DEEP');
            INSERT INTO knowledge_depth_policies VALUES (2, 'SEMANTIC');
            INSERT INTO knowledge_depth_policies VALUES (3, 'DEEP');
            INSERT INTO knowledge_depth_policies VALUES (4, 'DEEP');
            INSERT INTO knowledge_depth_policies VALUES (5, 'INDEX_ONLY');

            INSERT INTO asset_class_assignments VALUES
                (1, 101, 'GENERATED_CLASS'),
                (1, 201, 'ASSET_CLASS'),
                (2, 202, 'ASSET_CLASS'),
                (3, 203, 'ASSET_CLASS'),
                (5, 205, 'ASSET_CLASS');

            INSERT INTO class_gaps VALUES
                (201, 'NATIVE_ROOT_NOT_REACHED'),
                (203, 'NATIVE_ROOT_NOT_REACHED'),
                (205, 'NATIVE_ROOT_NOT_REACHED');
            """
        )

        metrics = _class_closure_metrics(core)

        self.assertEqual(metrics["classApplicableCount"], 3)
        self.assertEqual(metrics["classClosedCount"], 2)
        self.assertEqual(metrics["classNotApplicableCount"], 1)
        self.assertEqual(metrics["classOpenCount"], 1)
        self.assertAlmostEqual(metrics["closureRate"], 2 / 3)
        core.close()

    def test_semantic_fact_metrics_exclude_non_usable_values_and_require_fresh_evidence(self):
        core = _semantic_core_fixture()

        metrics = _semantic_fact_metrics(core)

        self.assertEqual(metrics["totalFacts"], 6)
        self.assertEqual(metrics["semanticFacts"], 0)
        self.assertEqual(metrics["usableValueFacts"], 3)
        self.assertEqual(metrics["freshEvidenceSemanticFacts"], 0)
        self.assertAlmostEqual(metrics["usableValueFactRate"], 0.5)
        self.assertEqual(metrics["semanticFreshEvidenceRate"], 0.0)
        self.assertEqual(metrics["totalEffectiveFacts"], 7)
        self.assertEqual(metrics["usableEffectiveFacts"], 2)
        self.assertAlmostEqual(metrics["effectiveUsableValueRate"], 2 / 7)
        core.close()

    def test_semantic_fact_metrics_require_payload_matching_value_kind(self):
        core = _semantic_core_fixture()
        core.execute("DELETE FROM effective_facts")
        core.execute("DELETE FROM fact_evidence")
        core.execute("DELETE FROM facts")
        valid_rows = [
            (1, "BOOLEAN", None, None, 0, None, "CONFIRMED", 1),
            (2, "INTEGER", None, None, 42, None, "CONFIRMED", 1),
            (3, "NUMBER", None, 2.5, None, None, "CONFIRMED", 1),
            (4, "TEXT", "", None, None, None, "CONFIRMED", 1),
            (
                5,
                "ENTITY_REF",
                "/Game/Test/Target.Target",
                None,
                None,
                None,
                "CONFIRMED",
                1,
            ),
            (
                6,
                "JSON",
                None,
                None,
                None,
                '{"items":[1,true]}',
                "VERIFIED",
                1,
            ),
            (
                7,
                "CONFIRMED_EMPTY",
                None,
                None,
                None,
                None,
                "CONFIRMED_EMPTY",
                1,
            ),
        ]
        cross_wired_rows = [
            (8, "BOOLEAN", "false", None, 0, None, "CONFIRMED", 1),
            (9, "INTEGER", "42", None, 42, None, "CONFIRMED", 1),
            (10, "NUMBER", "2.5", 2.5, None, None, "CONFIRMED", 1),
            (11, "TEXT", "text", None, 1, None, "CONFIRMED", 1),
            (
                12,
                "ENTITY_REF",
                "/Game/Test/Target.Target",
                1.0,
                None,
                None,
                "CONFIRMED",
                1,
            ),
            (
                13,
                "JSON",
                "wrong-column",
                None,
                None,
                '{"valid":true}',
                "CONFIRMED",
                1,
            ),
            (
                14,
                "CONFIRMED_EMPTY",
                None,
                None,
                None,
                "[]",
                "CONFIRMED_EMPTY",
                1,
            ),
        ]
        core.executemany(
            """
            INSERT INTO facts(
                fact_id, fact_type, value_kind, value_text, value_number,
                value_integer, value_json, status, current
            ) VALUES (
                ?, 'DECLARED_DEFAULT', ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [*valid_rows, *cross_wired_rows],
        )

        metrics = _semantic_fact_metrics(core)

        self.assertEqual(metrics["totalFacts"], 14)
        self.assertEqual(metrics["semanticFacts"], 0)
        self.assertEqual(metrics["usableValueFacts"], 7)
        self.assertAlmostEqual(metrics["usableValueFactRate"], 0.5)
        core.close()

    def test_semantic_fact_metrics_count_only_active_adapter_owned_facts(self):
        with tempfile.TemporaryDirectory() as temporary:
            core, _manifest = _projection_fixture(Path(temporary))

            metrics = _semantic_fact_metrics(core)

            self.assertEqual(metrics["totalFacts"], 12)
            self.assertEqual(metrics["usableValueFacts"], 12)
            self.assertEqual(metrics["semanticFacts"], 6)
            self.assertEqual(metrics["freshEvidenceSemanticFacts"], 6)
            self.assertEqual(metrics["semanticFreshEvidenceRate"], 1.0)

            core.execute(
                """
                UPDATE source_revisions
                SET freshness_status='STALE'
                WHERE revision_id=1
                """
            )
            stale_metrics = _semantic_fact_metrics(core)
            self.assertEqual(stale_metrics["semanticFacts"], 6)
            self.assertEqual(
                stale_metrics["freshEvidenceSemanticFacts"],
                0,
            )
            self.assertEqual(
                stale_metrics["semanticFreshEvidenceRate"],
                0.0,
            )
            core.close()

    def test_privacy_scan_detects_raw_and_json_escaped_local_paths(self):
        payload = {
            "nested": {
                "path": r"C:\Users\learner\private\report.json",
            }
        }

        self.assertEqual(
            _privacy_scan(payload),
            ["windows_absolute_path"],
        )
        self.assertEqual(
            _privacy_scan(json.dumps(payload)),
            ["windows_absolute_path"],
        )
        self.assertEqual(
            _privacy_scan(
                {
                    "asset": "/Game/Test/Asset.Asset",
                    "contract": "ontology/projection_review.v1.json",
                }
            ),
            [],
        )

    def test_effective_candidate_gate_matches_one_selection_only_to_resolved_row(
        self,
    ):
        core = _semantic_core_fixture()
        core.execute("DELETE FROM effective_facts")
        core.execute(
            """
            UPDATE facts
            SET
                fact_type='DECLARED_DEFAULT',
                fact_name=CASE fact_id
                    WHEN 1 THEN 'Resolved'
                    WHEN 2 THEN 'Gap'
                    ELSE fact_name
                END,
                subject_entity_id=1,
                declared_on_entity_id=1,
                scope_kind='DECLARED',
                current=1
            WHERE fact_id IN (1, 2)
            """
        )
        core.executemany(
            """
            INSERT INTO effective_facts VALUES (
                1, 'EFFECTIVE_DEFAULT', ?, ?, ?
            )
            """,
            [
                ("Resolved", 1, "RESOLVED"),
                ("Gap", None, "PARENT_CHAIN_OPEN"),
            ],
        )
        core.executemany(
            """
            INSERT INTO effective_fact_candidates VALUES (
                1, 'EFFECTIVE_DEFAULT', ?, ?, 1, 0,
                'SELF', ?, ?
            )
            """,
            [
                ("Resolved", 1, 1, ""),
                ("Gap", 2, 0, "PARENT_CHAIN_OPEN"),
            ],
        )

        valid = _effective_candidate_metrics(core)

        self.assertTrue(valid["consistent"])
        self.assertEqual(valid["invalidSelectionRows"], 0)
        self.assertEqual(valid["orphanCandidateRows"], 0)
        self.assertEqual(valid["invalidCandidateLineageRows"], 0)

        core.execute(
            """
            UPDATE effective_fact_candidates
            SET declared_on_entity_id=2
            WHERE fact_name='Resolved'
            """
        )
        invalid_lineage = _effective_candidate_metrics(core)
        self.assertFalse(invalid_lineage["consistent"])
        self.assertEqual(
            invalid_lineage["invalidCandidateLineageRows"],
            1,
        )
        core.execute(
            """
            UPDATE effective_fact_candidates
            SET declared_on_entity_id=1
            WHERE fact_name='Resolved'
            """
        )

        core.execute(
            "UPDATE facts SET declared_on_entity_id=NULL WHERE fact_id=1"
        )
        null_declared_on = _effective_candidate_metrics(core)
        self.assertFalse(null_declared_on["consistent"])
        self.assertEqual(
            null_declared_on["invalidCandidateLineageRows"],
            1,
        )
        core.execute(
            "UPDATE facts SET declared_on_entity_id=1 WHERE fact_id=1"
        )

        core.execute(
            """
            UPDATE effective_fact_candidates
            SET candidate_fact_id=2
            WHERE fact_name='Resolved'
            """
        )
        mismatched = _effective_candidate_metrics(core)
        self.assertFalse(mismatched["consistent"])
        self.assertEqual(mismatched["invalidSelectionRows"], 1)
        core.execute(
            """
            UPDATE effective_fact_candidates
            SET candidate_fact_id=1
            WHERE fact_name='Resolved'
            """
        )

        core.execute(
            """
            UPDATE effective_fact_candidates
            SET selected=1, rejection_reason=''
            WHERE fact_name='Gap'
            """
        )
        invalid_selection = _effective_candidate_metrics(core)
        self.assertFalse(invalid_selection["consistent"])
        self.assertEqual(invalid_selection["invalidSelectionRows"], 1)

        core.execute(
            """
            UPDATE effective_fact_candidates
            SET selected=0, rejection_reason='PARENT_CHAIN_OPEN'
            WHERE fact_name='Gap'
            """
        )
        core.execute(
            "DELETE FROM effective_facts WHERE fact_name='Gap'"
        )
        orphan = _effective_candidate_metrics(core)
        self.assertFalse(orphan["consistent"])
        self.assertEqual(orphan["orphanCandidateRows"], 1)
        core.close()

    def test_projection_metrics_require_reviewed_nonzero_usable_fresh_rows(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        snapshot_root = Path(temporary.name)
        core, manifest = _projection_fixture(snapshot_root)

        ready = _semantic_projection_metrics(
            core,
            manifest,
            snapshot_root=snapshot_root,
            expected_ontology_version=CURRENT_ONTOLOGY_VERSION,
        )

        self.assertTrue(all(item["ready"] for item in ready.values()))
        self.assertEqual(ready["buff_effects"]["freshEvidenceRows"], 1)
        self.assertEqual(ready["status_values"]["usableRows"], 1)

        domain_manifest = manifest["counts"]["domainProjections"]
        domain_manifest["loot_entries"]["reviewStatus"] = "UNREVIEWED"
        core.execute(
            """
            UPDATE facts
            SET value_kind='FINGERPRINT', value_number=NULL,
                value_text='hash', status='CONFIRMED_FINGERPRINT_ONLY'
            WHERE fact_type='ITEM_PROPERTY'
            """
        )
        core.execute(
            """
            UPDATE fact_evidence SET source_revision_id=2
            WHERE fact_id=(SELECT fact_id FROM facts
                           WHERE fact_type='MISSION_REWARD')
            """
        )
        core.execute(
            """
            UPDATE projection_runs SET row_count=0
            WHERE projection_name='harvest_rules'
            """
        )
        domain_manifest["harvest_rules"]["rows"] = 0

        blocked = _semantic_projection_metrics(
            core,
            manifest,
            snapshot_root=snapshot_root,
            expected_ontology_version=CURRENT_ONTOLOGY_VERSION,
        )

        self.assertFalse(blocked["loot_entries"]["ready"])
        self.assertFalse(blocked["item_properties"]["ready"])
        self.assertFalse(blocked["mission_rewards"]["ready"])
        self.assertFalse(blocked["harvest_rules"]["ready"])
        core.close()

    def test_semantic_quality_gates_are_critical_and_fail_closed(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        snapshot_root = Path(temporary.name)
        core, manifest = _projection_fixture(snapshot_root)

        gates = semantic_quality_gates(
            core,
            manifest,
            snapshot_root=snapshot_root,
            expected_ontology_version=CURRENT_ONTOLOGY_VERSION,
        )

        self.assertEqual(len(gates), 10)
        self.assertTrue(all(gate["critical"] for gate in gates))
        self.assertTrue(all(gate["passed"] for gate in gates))

        manifest["counts"]["domainProjections"]["buff_effects"].pop(
            "reviewedRows"
        )
        blocked = semantic_quality_gates(
            core,
            manifest,
            snapshot_root=snapshot_root,
            expected_ontology_version=CURRENT_ONTOLOGY_VERSION,
        )
        by_id = {gate["id"]: gate for gate in blocked}
        self.assertFalse(
            by_id["projections.buff_effects.semantic_ready"]["passed"]
        )
        core.close()

    def test_projection_metrics_require_v2_active_lineage_and_current_source(self):
        cases = (
            (
                "v1",
                lambda core, manifest: (
                    core.execute(
                        """
                        UPDATE projection_runs
                        SET projection_version='v1'
                        WHERE projection_name='buff_effects'
                        """
                    ),
                    manifest["counts"]["domainProjections"][
                        "buff_effects"
                    ].update({"projectionVersion": "v1"}),
                ),
                "buff_effects",
            ),
            (
                "no-lineage",
                lambda core, _manifest: core.execute(
                    """
                    DELETE FROM semantic_adapter_decisions
                    WHERE semantic_fact_id=2
                    """
                ),
                "loot_entries",
            ),
            (
                "source-revoked",
                lambda core, _manifest: core.execute(
                    "UPDATE facts SET current=0 WHERE fact_id=103"
                ),
                "item_properties",
            ),
            (
                "fact-ontology-stale",
                lambda core, _manifest: core.execute(
                    """
                    UPDATE facts
                    SET ontology_version='ark-fact-types/v1'
                    WHERE fact_id IN (1, 101)
                    """
                ),
                "buff_effects",
            ),
        )
        for label, mutate, projection_name in cases:
            with self.subTest(case=label):
                with tempfile.TemporaryDirectory() as temp_dir:
                    snapshot_root = Path(temp_dir)
                    core, manifest = _projection_fixture(snapshot_root)
                    mutate(core, manifest)

                    metrics = _semantic_projection_metrics(
                        core,
                        manifest,
                        snapshot_root=snapshot_root,
                        expected_ontology_version=CURRENT_ONTOLOGY_VERSION,
                    )

                    self.assertFalse(metrics[projection_name]["ready"])
                    core.close()

    def test_projection_artifacts_missing_corrupt_or_replaced_fail_closed(self):
        cases = ("missing", "corrupt", "replaced")
        for mutation in cases:
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as temp_dir:
                    snapshot_root = Path(temp_dir)
                    core, manifest = _projection_fixture(snapshot_root)
                    exports = snapshot_root / "domain_exports"
                    buff = exports / "buff_effects.sqlite"
                    if mutation == "missing":
                        buff.unlink()
                    elif mutation == "corrupt":
                        buff.write_bytes(b"not-a-sqlite-database")
                    else:
                        shutil.copyfile(
                            exports / "loot_entries.sqlite",
                            buff,
                        )

                    metrics = _semantic_projection_metrics(
                        core,
                        manifest,
                        snapshot_root=snapshot_root,
                        expected_ontology_version=CURRENT_ONTOLOGY_VERSION,
                    )
                    integrity = _integrity_metrics(snapshot_root)

                    self.assertFalse(metrics["buff_effects"]["ready"])
                    self.assertFalse(
                        metrics["buff_effects"]["artifactVerified"]
                    )
                    projection_key = "domain_exports/buff_effects.sqlite"
                    self.assertIn(projection_key, integrity)
                    self.assertFalse(
                        bool(integrity[projection_key]["verified"])
                    )
                    core.close()

    def test_projection_artifact_content_tamper_fails_with_new_file_digest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_root = Path(temp_dir)
            core, manifest = _projection_fixture(snapshot_root)
            path = (
                snapshot_root
                / "domain_exports"
                / "buff_effects.sqlite"
            )
            projection = sqlite3.connect(path)
            try:
                projection.execute(
                    """
                    UPDATE projection_rows
                    SET value_number=999999.0
                    """
                )
                tampered_content_digest = (
                    compute_projection_artifact_content_digest(
                        projection
                    )
                )
                projection.execute(
                    """
                    UPDATE metadata
                    SET value=?
                    WHERE key='content_digest'
                    """,
                    (tampered_content_digest,),
                )
                projection.commit()
            finally:
                projection.close()

            entry = manifest["counts"]["domainProjections"][
                "buff_effects"
            ]
            entry["contentDigest"] = tampered_content_digest
            entry["bytes"] = path.stat().st_size
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

            metrics = _semantic_projection_metrics(
                core,
                manifest,
                snapshot_root=snapshot_root,
                expected_ontology_version=CURRENT_ONTOLOGY_VERSION,
            )

            self.assertFalse(metrics["buff_effects"]["ready"])
            self.assertFalse(
                metrics["buff_effects"]["artifact"]["contentDigestMatches"]
            )
            self.assertEqual(
                metrics["buff_effects"]["artifact"]["contentDigest"],
                tampered_content_digest,
            )
            self.assertNotEqual(
                metrics["buff_effects"]["artifact"][
                    "expectedContentDigest"
                ],
                tampered_content_digest,
            )
            core.close()

    def test_projection_fake_review_fails_with_recomputed_summaries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_root = Path(temp_dir)
            core, manifest = _projection_fixture(snapshot_root)
            path = (
                snapshot_root
                / "domain_exports"
                / "buff_effects.sqlite"
            )
            projection = sqlite3.connect(path)
            try:
                projection.execute(
                    """
                    INSERT INTO projection_reviews VALUES (
                        'fake-review', 1, 'FIXTURE_EXACT',
                        'fixture://projection/1', 'fixture-review/v1'
                    )
                    """
                )
                tampered_content_digest = (
                    compute_projection_artifact_content_digest(
                        projection
                    )
                )
                projection.execute(
                    """
                    UPDATE metadata
                    SET value=?
                    WHERE key='content_digest'
                    """,
                    (tampered_content_digest,),
                )
                projection.commit()
            finally:
                projection.close()

            entry = manifest["counts"]["domainProjections"][
                "buff_effects"
            ]
            entry["contentDigest"] = tampered_content_digest
            entry["tableCounts"]["projection_reviews"] = 2
            entry["bytes"] = path.stat().st_size
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

            metrics = _semantic_projection_metrics(
                core,
                manifest,
                snapshot_root=snapshot_root,
                expected_ontology_version=CURRENT_ONTOLOGY_VERSION,
            )

            artifact = metrics["buff_effects"]["artifact"]
            self.assertFalse(metrics["buff_effects"]["ready"])
            self.assertFalse(artifact["reviewContractMatches"])
            self.assertFalse(artifact["contentDigestMatches"])
            self.assertEqual(
                artifact["contentDigest"],
                tampered_content_digest,
            )
            core.close()

    def test_projection_artifact_ontology_must_match_core_manifest_and_loader(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_root = Path(temp_dir)
            core, manifest = _projection_fixture(snapshot_root)
            _set_artifact_ontology_version(
                snapshot_root=snapshot_root,
                manifest=manifest,
                projection_names=("buff_effects",),
                ontology_version="mismatched-ontology",
            )

            metrics = _semantic_projection_metrics(
                core,
                manifest,
                snapshot_root=snapshot_root,
                expected_ontology_version=CURRENT_ONTOLOGY_VERSION,
            )

            self.assertFalse(metrics["buff_effects"]["ready"])
            self.assertFalse(metrics["buff_effects"]["artifactVerified"])
            core.close()

    def test_old_fact_types_v1_snapshot_fails_against_current_loader(self):
        old_ontology_version = CURRENT_ONTOLOGY_VERSION.replace(
            "ark-fact-types/v2",
            "ark-fact-types/v1",
        )
        self.assertNotEqual(old_ontology_version, CURRENT_ONTOLOGY_VERSION)
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_root = Path(temp_dir)
            core, manifest = _projection_fixture(snapshot_root)
            core.execute(
                """
                UPDATE metadata SET value=?
                WHERE key='ontology_version'
                """,
                (old_ontology_version,),
            )
            core.execute(
                "UPDATE projection_runs SET ontology_version=?",
                (old_ontology_version,),
            )
            manifest["ontologyVersion"] = old_ontology_version
            entries = manifest["counts"]["domainProjections"]
            for entry in entries.values():
                entry["ontologyVersion"] = old_ontology_version
            _set_artifact_ontology_version(
                snapshot_root=snapshot_root,
                manifest=manifest,
                projection_names=PROJECTION_NAMES,
                ontology_version=old_ontology_version,
            )

            metrics = _semantic_projection_metrics(
                core,
                manifest,
                snapshot_root=snapshot_root,
                expected_ontology_version=CURRENT_ONTOLOGY_VERSION,
            )

            self.assertTrue(
                all(not item["ready"] for item in metrics.values())
            )
            self.assertTrue(
                all(
                    not item["ontologyVersionsMatch"]
                    for item in metrics.values()
                )
            )
            core.close()


if __name__ == "__main__":
    unittest.main()
