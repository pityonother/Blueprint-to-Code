from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.fact_store import (  # noqa: E402
    FactValue,
    materialize_declared_defaults,
    store_fact,
)
from blueprint_translator.kb_vnext.ontology import load_ontology  # noqa: E402
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    FULL_CORE_SCHEMA_SQL,
)
from blueprint_translator.kb_vnext.projections import (  # noqa: E402
    DOMAIN_PROJECTIONS,
    build_domain_projections,
)


def _core() -> sqlite3.Connection:
    core = sqlite3.connect(":memory:")
    core.execute("PRAGMA foreign_keys=ON")
    core.executescript(FULL_CORE_SCHEMA_SQL)
    core.execute(
        """
        INSERT INTO source_revisions VALUES (
            1, 'fixture', 'fixture://discovery', 'sha', 'test', 'v1',
            '2026-07-27T00:00:00Z', 'FRESH'
        )
        """
    )
    core.execute(
        """
        INSERT INTO entities(
            entity_id, canonical_uri, entity_kind, status, confidence
        ) VALUES (
            1, '/Game/Test/BP_Base.BP_Base', 'BLUEPRINT_ASSET',
            'CONFIRMED', 'HIGH'
        )
        """
    )
    return core


def _discovery() -> sqlite3.Connection:
    source = sqlite3.connect(":memory:")
    source.executescript(
        """
        CREATE TABLE default_property_surface(
            surface_id TEXT PRIMARY KEY,
            asset_object_path TEXT NOT NULL,
            property_name TEXT NOT NULL,
            property_type TEXT NOT NULL,
            has_value INTEGER NOT NULL,
            value_status TEXT NOT NULL,
            value_fingerprint TEXT NOT NULL,
            source_evidence_id TEXT NOT NULL,
            confidence TEXT NOT NULL
        );
        """
    )
    return source


class KnowledgeFactStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ontology = load_ontology(PROJECT_ROOT / "ontology")

    def test_identical_fact_merges_independent_evidence(self):
        core = _core()
        source = _discovery()
        source.executemany(
            "INSERT INTO default_property_surface VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "s1",
                    "/Game/Test/BP_Base.BP_Base",
                    "Damage",
                    "FloatProperty",
                    1,
                    "CONFIRMED_FINGERPRINT_ONLY",
                    "abc123",
                    "bp://fixture/default/damage",
                    "HIGH",
                ),
                (
                    "s2",
                    "/Game/Test/BP_Base.BP_Base",
                    "Damage",
                    "FloatProperty",
                    1,
                    "CONFIRMED_FINGERPRINT_ONLY",
                    "abc123",
                    "capture://fixture/default/damage",
                    "HIGH",
                ),
            ],
        )
        result = materialize_declared_defaults(
            source,
            core,
            ontology=self.ontology,
            source_revision_id=1,
        )
        self.assertEqual(result["declaredFacts"], 1)
        self.assertEqual(result["factEvidence"], 2)
        row = core.execute(
            """
            SELECT fact_name, value_kind, value_text, status
            FROM facts
            """
        ).fetchone()
        self.assertEqual(
            row,
            (
                "Damage",
                "FINGERPRINT",
                "abc123",
                "CONFIRMED_FINGERPRINT_ONLY",
            ),
        )
        core.close()
        source.close()

    def test_unknown_and_stale_never_become_zero_or_empty_values(self):
        core = _core()
        source = _discovery()
        source.executemany(
            "INSERT INTO default_property_surface VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "s1",
                    "/Game/Test/BP_Base.BP_Base",
                    "Items",
                    "ArrayProperty",
                    0,
                    "NOT_RECOVERED",
                    "",
                    "bp://fixture/default/items",
                    "LOW",
                ),
                (
                    "s2",
                    "/Game/Test/BP_Base.BP_Base",
                    "Rate",
                    "FloatProperty",
                    0,
                    "STALE",
                    "",
                    "bp://fixture/default/rate",
                    "LOW",
                ),
            ],
        )
        materialize_declared_defaults(
            source,
            core,
            ontology=self.ontology,
            source_revision_id=1,
        )
        rows = list(
            core.execute(
                """
                SELECT fact_name, value_kind, value_text, value_number,
                       value_integer, value_json, status
                FROM facts ORDER BY fact_name
                """
            )
        )
        self.assertEqual(rows[0], ("Items", "UNKNOWN", None, None, None, None, "NOT_RECOVERED"))
        self.assertEqual(rows[1], ("Rate", "UNKNOWN", None, None, None, None, "STALE"))
        self.assertNotIn("CONFIRMED", {row[-1] for row in rows})
        core.close()
        source.close()

    def test_fact_requires_evidence_and_missing_status_rejects_placeholder(self):
        core = _core()
        with self.assertRaisesRegex(ValueError, "evidence URI"):
            store_fact(
                core,
                ontology=self.ontology,
                subject_entity_id=1,
                fact_type="DECLARED_DEFAULT",
                fact_name="Rate",
                scope_kind="DECLARED",
                declared_on_entity_id=1,
                value=FactValue("UNKNOWN"),
                status="NOT_RECOVERED",
                confidence="LOW",
                source_revision_id=1,
                evidence_uri="",
                evidence_role="DEFAULT_VALUE_GAP",
            )
        with self.assertRaisesRegex(ValueError, "no zero or empty placeholder"):
            store_fact(
                core,
                ontology=self.ontology,
                subject_entity_id=1,
                fact_type="DECLARED_DEFAULT",
                fact_name="Rate",
                scope_kind="DECLARED",
                declared_on_entity_id=1,
                value=FactValue("NUMBER", value_number=0.0),
                status="NOT_RECOVERED",
                confidence="LOW",
                source_revision_id=1,
                evidence_uri="bp://fixture/default/rate",
                evidence_role="DEFAULT_VALUE_GAP",
            )
        core.close()

    def test_domain_projection_is_a_provenance_backed_core_read_model(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            core_path = root / "core.sqlite"
            core = sqlite3.connect(core_path)
            core.execute("PRAGMA foreign_keys=ON")
            core.executescript(FULL_CORE_SCHEMA_SQL)
            core.execute(
                """
                INSERT INTO source_revisions VALUES (
                    1, 'fixture', 'fixture://capture', 'sha', 'test', 'v1',
                    '2026-07-27T00:00:00Z', 'FRESH'
                )
                """
            )
            core.execute(
                """
                INSERT INTO entities(
                    entity_id, canonical_uri, entity_kind, status, confidence
                ) VALUES (
                    1, '/Game/Test/Item.Item', 'BLUEPRINT_ASSET',
                    'CONFIRMED', 'HIGH'
                )
                """
            )
            store_fact(
                core,
                ontology=self.ontology,
                subject_entity_id=1,
                fact_type="ITEM_PROPERTY",
                fact_name="Weight",
                scope_kind="DERIVED_STATIC",
                declared_on_entity_id=1,
                value=FactValue("NUMBER", value_number=2.5),
                status="CONFIRMED",
                confidence="HIGH",
                source_revision_id=1,
                evidence_uri="bp://fixture/item/weight",
                evidence_role="DIRECT_FIELD",
            )
            core.commit()
            core.close()
            result = build_domain_projections(
                core_path=core_path,
                output_dir=root / "exports",
                generated_at="2026-07-27T00:00:00Z",
                ontology_version=self.ontology.version,
            )
            self.assertEqual(set(result), set(DOMAIN_PROJECTIONS))
            self.assertEqual(result["item_properties"]["rows"], 1)
            projection = sqlite3.connect(
                root / "exports" / "item_properties.sqlite"
            )
            try:
                row = projection.execute(
                    """
                    SELECT fact_id, fact_name, value_number,
                           evidence_count, source_revision_set_hash
                    FROM projection_rows
                    """
                ).fetchone()
                self.assertEqual(row[:4], (1, "Weight", 2.5, 1))
                self.assertTrue(row[4])
                self.assertEqual(
                    projection.execute(
                        """
                        SELECT value FROM metadata
                        WHERE key='truth_source'
                        """
                    ).fetchone()[0],
                    "core.sqlite",
                )
            finally:
                projection.close()


if __name__ == "__main__":
    unittest.main()
