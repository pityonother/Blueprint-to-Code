from __future__ import annotations

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

from blueprint_translator.kb_vnext.adapters import (  # noqa: E402
    ADAPTER_VERSION,
)
from blueprint_translator.kb_vnext.fact_store import (  # noqa: E402
    FactValue,
    store_fact,
)
from blueprint_translator.kb_vnext.ontology import (  # noqa: E402
    load_ontology,
)
from blueprint_translator.kb_vnext.projections import (  # noqa: E402
    DOMAIN_PROJECTIONS,
    PROJECTION_SCHEMA_VERSION,
    build_domain_projection,
    build_domain_projections,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    FULL_CORE_SCHEMA_SQL,
)


GENERATED_AT = "2026-07-27T00:00:00+00:00"
CURRENT_ADAPTER_VERSION = ADAPTER_VERSION
FACT_ADAPTER_RULES = {
    "STATUS_EFFECT": ("buffs", "buff.timing.v1"),
    "STATUS_VALUE": (
        "status_components",
        "status.numeric-value.v1",
    ),
    "ITEM_PROPERTY": ("primal_items", "item.number-property.v1"),
}
LEGACY_ONLY_FACT_TYPES = {"STATUS_EFFECT", "STATUS_VALUE"}


class DomainProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ontology = load_ontology(PROJECT_ROOT / "ontology")

    def _core(
        self,
        path: Path,
        *,
        fresh_revision_id: int = 1,
        fresh_fingerprint: str = "fresh-sha",
    ) -> sqlite3.Connection:
        core = sqlite3.connect(path)
        core.execute("PRAGMA foreign_keys=ON")
        core.executescript(FULL_CORE_SCHEMA_SQL)
        core.executemany(
            """
            INSERT INTO source_revisions VALUES (
                ?, 'blueprint_evidence', ?, ?, 'fixture',
                'ark.blueprint.evidence.v2', ?, ?
            )
            """,
            (
                (
                    fresh_revision_id,
                    "fixture://fresh",
                    fresh_fingerprint,
                    GENERATED_AT,
                    "FRESH",
                ),
                (
                    fresh_revision_id + 1,
                    "fixture://stale",
                    "stale-sha",
                    GENERATED_AT,
                    "STALE",
                ),
            ),
        )
        core.executemany(
            """
            INSERT INTO entities(
                entity_id, canonical_uri, entity_kind, status, confidence
            ) VALUES (?, ?, 'BLUEPRINT_ASSET', 'CONFIRMED', 'HIGH')
            """,
            (
                (1, "/Game/Test/Buff_Test.Buff_Test"),
                (2, "/Game/Test/Status_Test.Status_Test"),
                (3, "/Game/Test/Item_Stale.Item_Stale"),
            ),
        )
        return core

    def test_single_projection_build_does_not_rewrite_other_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            core = self._core(root / "core.sqlite")
            core.commit()
            output = root / "domain_exports"
            output.mkdir()
            unrelated = output / "loot_entries.sqlite"
            unrelated.write_bytes(b"unrelated-sentinel")

            result = build_domain_projection(
                core=core,
                projection_name="buff_effects",
                output_path=output / "buff_effects.sqlite",
                generated_at=GENERATED_AT,
                ontology_version=self.ontology.version,
                review_path=None,
                snapshot_build_id="candidate-build",
                snapshot_source_fingerprint="a" * 64,
            )

            self.assertEqual(result["path"], "buff_effects.sqlite")
            self.assertEqual(unrelated.read_bytes(), b"unrelated-sentinel")
            self.assertEqual(
                core.execute(
                    "SELECT projection_name FROM projection_runs"
                ).fetchall(),
                [("buff_effects",)],
            )
            projection = sqlite3.connect(output / "buff_effects.sqlite")
            try:
                metadata = dict(
                    projection.execute("SELECT key, value FROM metadata")
                )
            finally:
                projection.close()
            self.assertEqual(metadata["snapshot_build_id"], "candidate-build")
            self.assertEqual(
                metadata["snapshot_source_fingerprint"], "a" * 64
            )
            core.close()

    def _fact(
        self,
        core: sqlite3.Connection,
        *,
        entity_id: int,
        fact_type: str,
        fact_name: str,
        value: FactValue,
        revision_id: int = 1,
        evidence_uri: str,
    ) -> int:
        _adapter_id, rule_id = FACT_ADAPTER_RULES[fact_type]
        return store_fact(
            core,
            ontology=self.ontology,
            subject_entity_id=entity_id,
            fact_type=fact_type,
            fact_name=fact_name,
            scope_kind="DERIVED_STATIC",
            declared_on_entity_id=entity_id,
            value=value,
            status="CONFIRMED",
            confidence="HIGH",
            source_revision_id=revision_id,
            evidence_uri=evidence_uri,
            evidence_role=f"SEMANTIC_ADAPTER:{rule_id}",
        )

    def _promoted_fact(
        self,
        core: sqlite3.Connection,
        *,
        entity_id: int,
        fact_type: str,
        fact_name: str,
        value: FactValue,
        revision_id: int = 1,
        evidence_uri: str,
        partial: bool = False,
    ) -> tuple[int, int]:
        adapter_id, rule_id = FACT_ADAPTER_RULES[fact_type]
        source_mode = (
            "LEGACY_TABLE"
            if fact_type in LEGACY_ONLY_FACT_TYPES
            else "CORE_TYPED_FACT"
        )
        source_fact_id = store_fact(
            core,
            ontology=self.ontology,
            subject_entity_id=entity_id,
            fact_type="DECLARED_DEFAULT",
            fact_name=fact_name,
            scope_kind="DECLARED",
            declared_on_entity_id=entity_id,
            value=value,
            status="CONFIRMED",
            confidence="HIGH",
            source_revision_id=revision_id,
            evidence_uri=evidence_uri,
            evidence_role="DEFAULT_VALUE_ACTUAL",
        )
        semantic_fact_id = self._fact(
            core,
            entity_id=entity_id,
            fact_type=fact_type,
            fact_name=fact_name,
            value=value,
            revision_id=revision_id,
            evidence_uri=evidence_uri,
        )
        legacy_lineage_id: int | None = None
        if source_mode == "LEGACY_TABLE":
            legacy_lineage_id = semantic_fact_id
            core.execute(
                """
                INSERT INTO legacy_lineage VALUES (
                    ?, 'FACT', ?, 'fixture.sqlite', 'fixture_rows',
                    ?, ?, ?, 'VERIFIED', ?
                )
                """,
                (
                    legacy_lineage_id,
                    source_fact_id,
                    str(source_fact_id),
                    core.execute(
                        """
                        SELECT canonical_uri
                        FROM entities
                        WHERE entity_id=?
                        """,
                        (entity_id,),
                    ).fetchone()[0],
                    evidence_uri,
                    revision_id,
                ),
            )
        core.execute(
            """
            INSERT OR IGNORE INTO semantic_adapter_runs VALUES (
                ?, ?, ?, 0, 0, 0, 'VALID'
            )
            """,
            (adapter_id, CURRENT_ADAPTER_VERSION, GENERATED_AT),
        )
        core.execute(
            """
            INSERT INTO semantic_adapter_decisions(
                decision_key, adapter_id, adapter_version, rule_id,
                source_mode, object_path, property_name, decision_status,
                reason_code, source_fact_id, semantic_fact_id,
                legacy_lineage_id, source_revision_id, evidence_uri,
                decided_at
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?, ?, 'PROMOTED', ?, ?, ?,
                ?, ?, ?, ?
            )
            """,
            (
                f"fixture-decision://{semantic_fact_id}",
                adapter_id,
                CURRENT_ADAPTER_VERSION,
                rule_id,
                source_mode,
                core.execute(
                    "SELECT canonical_uri FROM entities WHERE entity_id=?",
                    (entity_id,),
                ).fetchone()[0],
                fact_name,
                "VERIFIED_PARTIAL" if partial else "VERIFIED",
                source_fact_id,
                semantic_fact_id,
                legacy_lineage_id,
                revision_id,
                evidence_uri,
                GENERATED_AT,
            ),
        )
        return semantic_fact_id, source_fact_id

    def test_projection_v2_separates_status_and_exports_fresh_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            core_path = root / "core.sqlite"
            core = self._core(core_path)
            buff_id, _ = self._promoted_fact(
                core,
                entity_id=1,
                fact_type="STATUS_EFFECT",
                fact_name="DeactivateAfterTime",
                value=FactValue("NUMBER", value_number=150.0),
                evidence_uri="bp://fixture/buff/duration",
            )
            status_id, _ = self._promoted_fact(
                core,
                entity_id=2,
                fact_type="STATUS_VALUE",
                fact_name="MovingStaminaRecoveryRateMultiplier",
                value=FactValue("NUMBER", value_number=0.5),
                evidence_uri="bp://fixture/status/recovery",
            )
            self._promoted_fact(
                core,
                entity_id=3,
                fact_type="ITEM_PROPERTY",
                fact_name="BaseItemWeight",
                value=FactValue("NUMBER", value_number=5.0),
                revision_id=2,
                evidence_uri="bp://fixture/item/stale",
            )
            core.commit()
            core.close()

            review_path = root / "projection_review.json"
            review_path.write_text(
                json.dumps(
                    {
                        "schema": "ark-kb-projection-review/v1",
                        "version": "fixture-review/v1",
                        "projections": {
                            "buff_effects": [
                                {
                                    "reviewId": "buff-duration",
                                    "canonicalUri": (
                                        "/Game/Test/Buff_Test.Buff_Test"
                                    ),
                                    "factType": "STATUS_EFFECT",
                                    "factName": "DeactivateAfterTime",
                                    "valueKind": "NUMBER",
                                    "valueNumber": 150.0,
                                    "evidenceUri": (
                                        "bp://fixture/buff/duration"
                                    ),
                                }
                            ]
                        },
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )

            result = build_domain_projections(
                core_path=core_path,
                output_dir=root / "exports",
                generated_at=GENERATED_AT,
                ontology_version=self.ontology.version,
                review_path=review_path,
            )

            self.assertEqual(
                DOMAIN_PROJECTIONS["status_values"],
                ("STATUS_VALUE",),
            )
            self.assertEqual(result["buff_effects"]["rows"], 1)
            self.assertEqual(result["status_values"]["rows"], 1)
            self.assertEqual(result["item_properties"]["rows"], 0)
            self.assertEqual(
                result["buff_effects"]["reviewStatus"],
                "FIXTURE_EXACT",
            )
            self.assertEqual(result["buff_effects"]["reviewedRows"], 1)
            self.assertEqual(
                result["status_values"]["reviewStatus"],
                "UNREVIEWED",
            )
            buff_manifest = result["buff_effects"]
            self.assertEqual(buff_manifest["projectionVersion"], "v2")
            self.assertGreater(buff_manifest["bytes"], 0)
            self.assertRegex(str(buff_manifest["sha256"]), r"^[0-9a-f]{64}$")
            self.assertRegex(
                str(buff_manifest["contentDigest"]),
                r"^[0-9a-f]{64}$",
            )
            self.assertRegex(
                str(buff_manifest["reviewConfigSha256"]),
                r"^[0-9a-f]{64}$",
            )
            self.assertEqual(buff_manifest["foreignKeyViolations"], 0)
            self.assertEqual(
                buff_manifest["tableCounts"],
                {
                    "metadata": 11,
                    "projection_evidence": 1,
                    "projection_lineage": 1,
                    "projection_reviews": 1,
                    "projection_rows": 1,
                },
            )

            buff = sqlite3.connect(root / "exports" / "buff_effects.sqlite")
            try:
                self.assertEqual(
                    buff.execute(
                        """
                        SELECT value FROM metadata
                        WHERE key='schema_version'
                        """
                    ).fetchone()[0],
                    PROJECTION_SCHEMA_VERSION,
                )
                row = buff.execute(
                    """
                    SELECT fact_id, evidence_count
                    FROM projection_rows
                    """
                ).fetchone()
                self.assertEqual(row, (buff_id, 1))
                self.assertEqual(
                    buff.execute(
                        """
                        SELECT fact_id, source_revision_id, evidence_uri,
                               freshness_status
                        FROM projection_evidence
                        """
                    ).fetchone(),
                    (
                        buff_id,
                        1,
                        "bp://fixture/buff/duration",
                        "FRESH",
                    ),
                )
                self.assertEqual(
                    buff.execute(
                        """
                        SELECT completeness_status
                        FROM projection_rows
                        """
                    ).fetchone()[0],
                    "COMPLETE",
                )
                self.assertEqual(
                    buff.execute(
                        """
                        SELECT ontology_version
                        FROM projection_rows
                        """
                    ).fetchone()[0],
                    self.ontology.version,
                )
                self.assertEqual(
                    buff.execute(
                        """
                        SELECT value FROM metadata
                        WHERE key='content_digest'
                        """
                    ).fetchone()[0],
                    buff_manifest["contentDigest"],
                )
                self.assertEqual(
                    buff.execute(
                        """
                        SELECT review_id, review_status
                        FROM projection_reviews
                        """
                    ).fetchone(),
                    ("buff-duration", "FIXTURE_EXACT"),
                )
            finally:
                buff.close()

            status = sqlite3.connect(
                root / "exports" / "status_values.sqlite"
            )
            try:
                self.assertEqual(
                    status.execute(
                        "SELECT fact_id FROM projection_rows"
                    ).fetchone()[0],
                    status_id,
                )
            finally:
                status.close()

            projection_path = root / "exports" / "buff_effects.sqlite"
            self.assertEqual(
                buff_manifest["bytes"],
                projection_path.stat().st_size,
            )
            self.assertEqual(
                buff_manifest["sha256"],
                hashlib.sha256(projection_path.read_bytes()).hexdigest(),
            )

    def test_projection_excludes_unlineaged_and_revoked_semantic_facts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            core_path = root / "core.sqlite"
            core = self._core(core_path)
            active_id, _ = self._promoted_fact(
                core,
                entity_id=1,
                fact_type="STATUS_EFFECT",
                fact_name="ActiveDuration",
                value=FactValue("NUMBER", value_number=10.0),
                evidence_uri="bp://fixture/buff/active",
            )
            self._fact(
                core,
                entity_id=1,
                fact_type="STATUS_EFFECT",
                fact_name="NoLineageDuration",
                value=FactValue("NUMBER", value_number=20.0),
                evidence_uri="bp://fixture/buff/no-lineage",
            )
            _, revoked_source_id = self._promoted_fact(
                core,
                entity_id=1,
                fact_type="STATUS_EFFECT",
                fact_name="RevokedDuration",
                value=FactValue("NUMBER", value_number=30.0),
                evidence_uri="bp://fixture/buff/revoked",
            )
            stale_ontology_id, stale_ontology_source_id = (
                self._promoted_fact(
                    core,
                    entity_id=1,
                    fact_type="STATUS_EFFECT",
                    fact_name="OldOntologyDuration",
                    value=FactValue("NUMBER", value_number=40.0),
                    evidence_uri="bp://fixture/buff/old-ontology",
                )
            )
            core.execute(
                "UPDATE facts SET current=0 WHERE fact_id=?",
                (revoked_source_id,),
            )
            core.execute(
                """
                UPDATE facts
                SET ontology_version='ark-fact-types/v1'
                WHERE fact_id IN (?, ?)
                """,
                (stale_ontology_id, stale_ontology_source_id),
            )
            core.commit()
            core.close()

            result = build_domain_projections(
                core_path=core_path,
                output_dir=root / "exports",
                generated_at=GENERATED_AT,
                ontology_version=self.ontology.version,
            )

            self.assertEqual(result["buff_effects"]["rows"], 1)
            self.assertEqual(result["buff_effects"]["lineageRows"], 1)
            self.assertEqual(result["buff_effects"]["unspecifiedRows"], 0)
            projection = sqlite3.connect(
                root / "exports" / "buff_effects.sqlite"
            )
            try:
                self.assertEqual(
                    projection.execute(
                        """
                        SELECT fact_id, completeness_status
                        FROM projection_rows
                        """
                    ).fetchone(),
                    (active_id, "COMPLETE"),
                )
            finally:
                projection.close()

    def test_projection_rejects_unregistered_or_mismatched_provenance(self):
        cases = (
            "unknown-adapter",
            "old-adapter-version",
            "cross-domain-rule",
            "wrong-source-role",
            "wrong-semantic-role",
            "wrong-source-kind",
            "wrong-source-schema",
            "wrong-source-fact-type",
            "property-name-mismatch",
            "fact-name-mismatch",
            "subject-mismatch",
            "declared-on-mismatch",
            "value-kind-mismatch",
            "value-payload-mismatch",
            "unit-mismatch",
            "status-mismatch",
            "confidence-mismatch",
        )
        for case in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    core_path = root / "core.sqlite"
                    core = self._core(core_path)
                    semantic_id, source_id = self._promoted_fact(
                        core,
                        entity_id=1,
                        fact_type="STATUS_EFFECT",
                        fact_name="DeactivateAfterTime",
                        value=FactValue("NUMBER", value_number=150.0),
                        evidence_uri="bp://fixture/buff/duration",
                    )
                    if case == "unknown-adapter":
                        core.execute(
                            """
                            UPDATE semantic_adapter_decisions
                            SET adapter_id='unknown'
                            """
                        )
                        core.execute(
                            """
                            INSERT INTO semantic_adapter_runs VALUES (
                                'unknown', ?, ?, 0, 0, 0, 'VALID'
                            )
                            """,
                            (CURRENT_ADAPTER_VERSION, GENERATED_AT),
                        )
                    elif case == "old-adapter-version":
                        core.execute(
                            """
                            UPDATE semantic_adapter_decisions
                            SET adapter_version='ark-kb-semantic-adapter/v0'
                            """
                        )
                        core.execute(
                            """
                            INSERT INTO semantic_adapter_runs VALUES (
                                'buffs', 'ark-kb-semantic-adapter/v0',
                                ?, 0, 0, 0, 'VALID'
                            )
                            """,
                            (GENERATED_AT,),
                        )
                    elif case == "cross-domain-rule":
                        core.execute(
                            """
                            UPDATE semantic_adapter_decisions
                            SET adapter_id='primal_items',
                                rule_id='item.number-property.v1'
                            """
                        )
                        core.execute(
                            """
                            UPDATE fact_evidence
                            SET evidence_role=(
                                'SEMANTIC_ADAPTER:'
                                || 'item.number-property.v1'
                            )
                            WHERE fact_id=?
                            """,
                            (semantic_id,),
                        )
                        core.execute(
                            """
                            INSERT INTO semantic_adapter_runs VALUES (
                                'primal_items', ?, ?, 0, 0, 0, 'VALID'
                            )
                            """,
                            (CURRENT_ADAPTER_VERSION, GENERATED_AT),
                        )
                    elif case == "wrong-source-role":
                        core.execute(
                            """
                            UPDATE fact_evidence
                            SET evidence_role='DEFAULT_VALUE'
                            WHERE fact_id=?
                            """,
                            (source_id,),
                        )
                    elif case == "wrong-semantic-role":
                        core.execute(
                            """
                            UPDATE fact_evidence
                            SET evidence_role='SEMANTIC_ADAPTER:wrong'
                            WHERE fact_id=?
                            """,
                            (semantic_id,),
                        )
                    elif case == "wrong-source-kind":
                        core.execute(
                            """
                            UPDATE source_revisions
                            SET source_kind='legacy'
                            WHERE revision_id=1
                            """
                        )
                    elif case == "wrong-source-schema":
                        core.execute(
                            """
                            UPDATE source_revisions
                            SET schema_version='ark.blueprint.evidence.v1'
                            WHERE revision_id=1
                            """
                        )
                    elif case == "wrong-source-fact-type":
                        core.execute(
                            """
                            UPDATE facts
                            SET fact_type='FORMULA'
                            WHERE fact_id=?
                            """,
                            (source_id,),
                        )
                    elif case == "property-name-mismatch":
                        core.execute(
                            """
                            UPDATE semantic_adapter_decisions
                            SET property_name='OtherProperty'
                            """
                        )
                    elif case == "fact-name-mismatch":
                        core.execute(
                            """
                            UPDATE facts
                            SET fact_name='OtherProperty'
                            WHERE fact_id=?
                            """,
                            (source_id,),
                        )
                    elif case == "subject-mismatch":
                        core.execute(
                            """
                            UPDATE facts
                            SET subject_entity_id=2
                            WHERE fact_id=?
                            """,
                            (source_id,),
                        )
                    elif case == "declared-on-mismatch":
                        core.execute(
                            """
                            UPDATE facts
                            SET declared_on_entity_id=2
                            WHERE fact_id=?
                            """,
                            (source_id,),
                        )
                    elif case == "value-kind-mismatch":
                        core.execute(
                            """
                            UPDATE facts
                            SET value_kind='INTEGER',
                                value_number=NULL,
                                value_integer=150
                            WHERE fact_id=?
                            """,
                            (source_id,),
                        )
                    elif case == "value-payload-mismatch":
                        core.execute(
                            """
                            UPDATE facts
                            SET value_number=999.0
                            WHERE fact_id=?
                            """,
                            (semantic_id,),
                        )
                    elif case == "unit-mismatch":
                        core.execute(
                            """
                            UPDATE facts
                            SET unit='seconds'
                            WHERE fact_id=?
                            """,
                            (source_id,),
                        )
                    elif case == "status-mismatch":
                        core.execute(
                            """
                            UPDATE facts
                            SET status='VERIFIED'
                            WHERE fact_id=?
                            """,
                            (source_id,),
                        )
                    else:
                        core.execute(
                            """
                            UPDATE facts
                            SET confidence='MEDIUM'
                            WHERE fact_id=?
                            """,
                            (source_id,),
                        )
                    core.commit()
                    core.close()

                    result = build_domain_projections(
                        core_path=core_path,
                        output_dir=root / "exports",
                        generated_at=GENERATED_AT,
                        ontology_version=self.ontology.version,
                    )

                    self.assertEqual(result["buff_effects"]["rows"], 0)

    def test_legacy_only_rules_cannot_bypass_lineage_as_core_typed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            core_path = root / "core.sqlite"
            core = self._core(core_path)
            buff_id, _ = self._promoted_fact(
                core,
                entity_id=1,
                fact_type="STATUS_EFFECT",
                fact_name="DeactivateAfterTime",
                value=FactValue("NUMBER", value_number=150.0),
                evidence_uri="bp://fixture/buff/duration",
            )
            status_id, _ = self._promoted_fact(
                core,
                entity_id=2,
                fact_type="STATUS_VALUE",
                fact_name="MovingStaminaRecoveryRateMultiplier",
                value=FactValue("NUMBER", value_number=0.5),
                evidence_uri="bp://fixture/status/recovery",
            )
            core.execute(
                """
                UPDATE semantic_adapter_decisions
                SET source_mode='CORE_TYPED_FACT',
                    legacy_lineage_id=NULL
                WHERE semantic_fact_id IN (?, ?)
                """,
                (buff_id, status_id),
            )
            core.commit()
            core.close()

            result = build_domain_projections(
                core_path=core_path,
                output_dir=root / "exports",
                generated_at=GENERATED_AT,
                ontology_version=self.ontology.version,
            )

            self.assertEqual(result["buff_effects"]["rows"], 0)
            self.assertEqual(result["status_values"]["rows"], 0)

    def test_projection_revision_hash_uses_stable_source_identity(self):
        def build(
            root: Path,
            *,
            revision_id: int,
            fingerprint: str,
        ) -> str:
            root.mkdir(parents=True)
            core_path = root / "core.sqlite"
            core = self._core(
                core_path,
                fresh_revision_id=revision_id,
                fresh_fingerprint=fingerprint,
            )
            self._promoted_fact(
                core,
                entity_id=1,
                fact_type="STATUS_EFFECT",
                fact_name="DeactivateAfterTime",
                value=FactValue("NUMBER", value_number=150.0),
                revision_id=revision_id,
                evidence_uri="bp://fixture/buff/duration",
            )
            core.commit()
            core.close()
            result = build_domain_projections(
                core_path=core_path,
                output_dir=root / "exports",
                generated_at=GENERATED_AT,
                ontology_version=self.ontology.version,
            )
            return str(result["buff_effects"]["sourceRevisionSetHash"])

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = build(root / "first", revision_id=1, fingerprint="same")
            renumbered = build(
                root / "renumbered",
                revision_id=41,
                fingerprint="same",
            )
            changed = build(
                root / "changed",
                revision_id=1,
                fingerprint="changed",
            )

            self.assertEqual(first, renumbered)
            self.assertNotEqual(first, changed)


if __name__ == "__main__":
    unittest.main()
