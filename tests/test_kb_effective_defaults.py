from __future__ import annotations

import json
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
    materialize_effective_defaults,
    store_fact,
)
from blueprint_translator.kb_vnext.ontology import load_ontology  # noqa: E402
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    FULL_CORE_SCHEMA_SQL,
)


def _fixture() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(FULL_CORE_SCHEMA_SQL)
    connection.execute(
        """
        INSERT INTO source_revisions VALUES (
            1, 'fixture', 'fixture://capture', 'sha', 'test', 'v1',
            '2026-07-27T00:00:00Z', 'FRESH'
        )
        """
    )
    connection.executemany(
        """
        INSERT INTO entities(
            entity_id, canonical_uri, entity_kind, status, confidence
        ) VALUES (?, ?, 'BLUEPRINT_ASSET', 'CONFIRMED', 'HIGH')
        """,
        [
            (1, "/Game/Test/Base.Base"),
            (2, "/Game/Test/Child.Child"),
            (3, "/Game/Test/Leaf.Leaf"),
            (4, "/Game/Test/Other.Other"),
        ],
    )
    connection.executemany(
        """
        INSERT INTO classes(
            class_id, class_path, class_name, module_or_package,
            class_kind, is_native, status, confidence
        ) VALUES (?, ?, ?, '/Game/Test', 'BLUEPRINT_GENERATED_CLASS',
                  0, 'CONFIRMED', 'HIGH')
        """,
        [
            (11, "/Game/Test/Base.Base_C", "Base_C"),
            (12, "/Game/Test/Child.Child_C", "Child_C"),
            (13, "/Game/Test/Leaf.Leaf_C", "Leaf_C"),
            (14, "/Game/Test/Other.Other_C", "Other_C"),
        ],
    )
    connection.executemany(
        """
        INSERT INTO class_closure VALUES (?, ?, ?, 'CONFIRMED')
        """,
        [
            (11, 11, 0),
            (11, 12, 1),
            (12, 12, 0),
            (11, 13, 2),
            (12, 13, 1),
            (13, 13, 0),
            (14, 14, 0),
        ],
    )
    connection.executemany(
        """
        INSERT INTO asset_class_assignments(
            entity_id, class_id, assignment_kind, evidence_uri,
            status, confidence
        ) VALUES (?, ?, 'GENERATED_CLASS', 'fixture://class',
                  'CONFIRMED', 'HIGH')
        """,
        [(1, 11), (2, 12), (3, 13), (4, 14)],
    )
    return connection


class KnowledgeEffectiveDefaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ontology = load_ontology(PROJECT_ROOT / "ontology")

    def _fact(
        self,
        connection: sqlite3.Connection,
        entity_id: int,
        name: str,
        value: FactValue,
        status: str,
    ) -> int:
        return store_fact(
            connection,
            ontology=self.ontology,
            subject_entity_id=entity_id,
            fact_type="DECLARED_DEFAULT",
            fact_name=name,
            scope_kind="DECLARED",
            declared_on_entity_id=entity_id,
            value=value,
            status=status,
            confidence="HIGH",
            source_revision_id=1,
            evidence_uri=f"bp://fixture/{entity_id}/{name}",
            evidence_role="DEFAULT_VALUE",
        )

    def test_nearest_declared_override_and_inheritance_chain(self):
        connection = _fixture()
        base_fact = self._fact(
            connection,
            1,
            "Rate",
            FactValue("FINGERPRINT", value_text="base"),
            "CONFIRMED_FINGERPRINT_ONLY",
        )
        child_fact = self._fact(
            connection,
            2,
            "Rate",
            FactValue("FINGERPRINT", value_text="child"),
            "CONFIRMED_FINGERPRINT_ONLY",
        )
        result = materialize_effective_defaults(connection)
        self.assertEqual(result["affectedEntities"], 4)
        rows = {
            row[0]: row[1:]
            for row in connection.execute(
                """
                SELECT entity_id, fact_id, inherited_from_entity_id,
                       resolution_chain_json, resolution_status
                FROM effective_facts
                WHERE fact_name='Rate'
                ORDER BY entity_id
                """
            )
        }
        self.assertEqual(rows[1][0], base_fact)
        self.assertIsNone(rows[1][1])
        self.assertEqual(rows[2][0], child_fact)
        self.assertIsNone(rows[2][1])
        self.assertEqual(rows[3][0], child_fact)
        self.assertEqual(rows[3][1], 2)
        chain = json.loads(rows[3][2])
        self.assertEqual(chain["overrideDepth"], 1)
        self.assertEqual(rows[3][3], "FINGERPRINT_ONLY")
        connection.close()

    def test_confirmed_empty_is_distinct_from_unrecovered_placeholder(self):
        connection = _fixture()
        empty_fact = self._fact(
            connection,
            1,
            "Items",
            FactValue("CONFIRMED_EMPTY"),
            "CONFIRMED_EMPTY",
        )
        missing_fact = self._fact(
            connection,
            2,
            "Items",
            FactValue("UNKNOWN"),
            "NOT_RECOVERED",
        )
        materialize_effective_defaults(connection)
        child = connection.execute(
            """
            SELECT fact_id, inherited_from_entity_id, resolution_status
            FROM effective_facts
            WHERE entity_id=2 AND fact_name='Items'
            """
        ).fetchone()
        leaf = connection.execute(
            """
            SELECT fact_id, inherited_from_entity_id, resolution_status
            FROM effective_facts
            WHERE entity_id=3 AND fact_name='Items'
            """
        ).fetchone()
        self.assertEqual(child, (missing_fact, None, "NOT_RECOVERED"))
        self.assertEqual(leaf, (missing_fact, 2, "NOT_RECOVERED"))
        self.assertNotEqual(child[0], empty_fact)
        connection.close()

    def test_incremental_recompute_only_touches_changed_descendants(self):
        connection = _fixture()
        self._fact(
            connection,
            1,
            "Rate",
            FactValue("FINGERPRINT", value_text="base"),
            "CONFIRMED_FINGERPRINT_ONLY",
        )
        other_fact = self._fact(
            connection,
            4,
            "Rate",
            FactValue("FINGERPRINT", value_text="other"),
            "CONFIRMED_FINGERPRINT_ONLY",
        )
        materialize_effective_defaults(connection)
        before = connection.execute(
            """
            SELECT fact_id, source_revision_set_hash
            FROM effective_facts
            WHERE entity_id=4 AND fact_name='Rate'
            """
        ).fetchone()
        result = materialize_effective_defaults(
            connection, changed_class_ids=[11]
        )
        after = connection.execute(
            """
            SELECT fact_id, source_revision_set_hash
            FROM effective_facts
            WHERE entity_id=4 AND fact_name='Rate'
            """
        ).fetchone()
        self.assertEqual(result["affectedEntities"], 3)
        self.assertEqual(before, after)
        self.assertEqual(after[0], other_fact)
        connection.close()


if __name__ == "__main__":
    unittest.main()
