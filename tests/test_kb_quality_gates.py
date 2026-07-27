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

from blueprint_translator.kb_vnext.quality_gates import (  # noqa: E402
    QUALITY_GATE_SCHEMA,
    _class_closure_metrics,
    publish_gate_report,
)
from blueprint_translator.kb_vnext.semantic_quality import (  # noqa: E402
    _semantic_fact_metrics,
    _semantic_projection_metrics,
    semantic_quality_gates,
)


PROJECTION_NAMES = (
    "buff_effects",
    "loot_entries",
    "item_properties",
    "status_values",
    "harvest_rules",
    "mission_rewards",
)


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
            current INTEGER NOT NULL
        );
        CREATE TABLE source_revisions(
            revision_id INTEGER PRIMARY KEY,
            freshness_status TEXT NOT NULL
        );
        CREATE TABLE fact_evidence(
            fact_id INTEGER NOT NULL,
            source_revision_id INTEGER NOT NULL,
            evidence_uri TEXT NOT NULL
        );
        CREATE TABLE effective_facts(
            entity_id INTEGER NOT NULL,
            fact_type TEXT NOT NULL,
            fact_name TEXT NOT NULL,
            fact_id INTEGER NOT NULL,
            resolution_status TEXT NOT NULL
        );
        CREATE TABLE projection_runs(
            projection_name TEXT NOT NULL,
            projection_version TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            validation_status TEXT NOT NULL
        );

        INSERT INTO source_revisions VALUES (1, 'FRESH');
        INSERT INTO source_revisions VALUES (2, 'STALE');

        INSERT INTO facts VALUES
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
            (1, 1, 'fixture://item/weight'),
            (2, 1, 'fixture://default/fingerprint'),
            (3, 1, 'fixture://legacy/value'),
            (4, 1, 'fixture://missing/value'),
            (5, 1, 'fixture://status/empty'),
            (6, 2, 'fixture://formula/stale');

        INSERT INTO effective_facts VALUES
            (1, 'EFFECTIVE_DEFAULT', 'One', 1, 'RESOLVED'),
            (1, 'EFFECTIVE_DEFAULT', 'Two', 2, 'RESOLVED'),
            (1, 'EFFECTIVE_DEFAULT', 'Three', 3, 'RESOLVED'),
            (1, 'EFFECTIVE_DEFAULT', 'Four', 4, 'RESOLVED'),
            (1, 'EFFECTIVE_DEFAULT', 'Five', 5, 'RESOLVED'),
            (1, 'EFFECTIVE_DEFAULT', 'Six', 6, 'AMBIGUOUS_INHERITANCE');
        """
    )
    return core


def _projection_fixture() -> tuple[sqlite3.Connection, dict[str, object]]:
    core = _semantic_core_fixture()
    core.execute("DELETE FROM effective_facts")
    core.execute("DELETE FROM fact_evidence")
    core.execute("DELETE FROM facts")
    core.executemany(
        """
        INSERT INTO facts VALUES (
            ?, ?, 'NUMBER', NULL, 1.0, NULL, NULL, 'CONFIRMED', 1
        )
        """,
        [
            (1, "STATUS_EFFECT"),
            (2, "LOOT_ENTRY"),
            (3, "ITEM_PROPERTY"),
            (4, "HARVEST_RULE"),
            (5, "MISSION_REWARD"),
        ],
    )
    core.executemany(
        "INSERT INTO fact_evidence VALUES (?, 1, ?)",
        [
            (fact_id, f"fixture://projection/{fact_id}")
            for fact_id in range(1, 6)
        ],
    )
    core.executemany(
        """
        INSERT INTO effective_facts VALUES (
            1, 'EFFECTIVE_DEFAULT', ?, ?, 'RESOLVED'
        )
        """,
        [(f"Fact{fact_id}", fact_id) for fact_id in range(1, 6)],
    )
    core.executemany(
        "INSERT INTO projection_runs VALUES (?, 'v1', 1, 'VALID')",
        [(name,) for name in PROJECTION_NAMES],
    )
    manifest: dict[str, object] = {
        "counts": {
            "domainProjections": {
                name: {
                    "rows": 1,
                    "reviewedRows": 1,
                    "reviewStatus": "FIXTURE_EXACT",
                    "validationStatus": "VALID",
                }
                for name in PROJECTION_NAMES
            }
        }
    }
    return core, manifest


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
        self.assertEqual(metrics["semanticFacts"], 3)
        self.assertEqual(metrics["usableValueFacts"], 3)
        self.assertEqual(metrics["freshEvidenceSemanticFacts"], 2)
        self.assertAlmostEqual(metrics["usableValueFactRate"], 0.5)
        self.assertAlmostEqual(metrics["semanticFreshEvidenceRate"], 2 / 3)
        self.assertEqual(metrics["totalEffectiveFacts"], 6)
        self.assertEqual(metrics["usableEffectiveFacts"], 2)
        self.assertAlmostEqual(metrics["effectiveUsableValueRate"], 1 / 3)
        core.close()

    def test_projection_metrics_require_reviewed_nonzero_usable_fresh_rows(self):
        core, manifest = _projection_fixture()

        ready = _semantic_projection_metrics(core, manifest)

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

        blocked = _semantic_projection_metrics(core, manifest)

        self.assertFalse(blocked["loot_entries"]["ready"])
        self.assertFalse(blocked["item_properties"]["ready"])
        self.assertFalse(blocked["mission_rewards"]["ready"])
        self.assertFalse(blocked["harvest_rules"]["ready"])
        core.close()

    def test_semantic_quality_gates_are_critical_and_fail_closed(self):
        core, manifest = _projection_fixture()

        gates = semantic_quality_gates(core, manifest)

        self.assertEqual(len(gates), 10)
        self.assertTrue(all(gate["critical"] for gate in gates))
        self.assertTrue(all(gate["passed"] for gate in gates))

        manifest["counts"]["domainProjections"]["buff_effects"].pop(
            "reviewedRows"
        )
        blocked = semantic_quality_gates(core, manifest)
        by_id = {gate["id"]: gate for gate in blocked}
        self.assertFalse(
            by_id["projections.buff_effects.semantic_ready"]["passed"]
        )
        core.close()


if __name__ == "__main__":
    unittest.main()
