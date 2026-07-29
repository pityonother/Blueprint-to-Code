from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from collections import deque
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


FRESH_REVISION_A = 1
STALE_REVISION = 2
FRESH_REVISION_B = 3

NATIVE_ROOT_CLASS = 100
BASE_CLASS = 11
CHILD_CLASS = 12
LEAF_CLASS = 13
OTHER_CLASS = 14


def _insert_class(
    connection: sqlite3.Connection,
    *,
    class_id: int,
    name: str,
    is_native: bool = False,
) -> None:
    if is_native and name == "Object":
        class_path = "/Script/CoreUObject.Object"
        module_or_package = "CoreUObject"
    elif is_native:
        class_path = f"/Script/Test.{name}"
        module_or_package = "Test"
    else:
        class_path = f"/Game/Test/{name}.{name}_C"
        module_or_package = "/Game/Test"
    connection.execute(
        """
        INSERT INTO classes(
            class_id, class_path, class_name, module_or_package,
            class_kind, is_native, source_revision_id, status, confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'IDENTIFIED', 'HIGH')
        """,
        (
            class_id,
            class_path,
            name if is_native else f"{name}_C",
            module_or_package,
            "NATIVE" if is_native else "BLUEPRINT_GENERATED_CLASS",
            int(is_native),
            FRESH_REVISION_A,
        ),
    )


def _insert_blueprint_entity(
    connection: sqlite3.Connection,
    *,
    entity_id: int,
    class_id: int,
    name: str,
    assignment_status: str = "EXTRACTED",
) -> None:
    connection.execute(
        """
        INSERT INTO entities(
            entity_id, canonical_uri, entity_kind, status, confidence
        ) VALUES (?, ?, 'BLUEPRINT_ASSET', 'CONFIRMED', 'HIGH')
        """,
        (entity_id, f"/Game/Test/{name}.{name}"),
    )
    _insert_class(
        connection,
        class_id=class_id,
        name=name,
    )
    connection.execute(
        """
        INSERT INTO asset_class_assignments(
            entity_id, class_id, assignment_kind, evidence_uri,
            status, confidence
        ) VALUES (?, ?, 'GENERATED_CLASS', ?, ?, 'HIGH')
        """,
        (
            entity_id,
            class_id,
            f"bp://fixture/class-assignment/{entity_id}",
            assignment_status,
        ),
    )


def _insert_edge(
    connection: sqlite3.Connection,
    child_class_id: int,
    parent_class_id: int,
    *,
    evidence_id: str | None = None,
    status: str = "CONFIRMED",
) -> str:
    evidence_id = evidence_id or (
        f"class-edge://fixture/{child_class_id}/{parent_class_id}"
    )
    connection.execute(
        """
        INSERT INTO class_edges(
            child_class_id, parent_class_id, edge_kind, evidence_id,
            source_revision_id, status, confidence
        ) VALUES (?, ?, 'blueprint_parent', ?, ?, ?, 'HIGH')
        """,
        (
            child_class_id,
            parent_class_id,
            evidence_id,
            FRESH_REVISION_A,
            status,
        ),
    )
    return evidence_id


def _rebuild_fixture_closure(connection: sqlite3.Connection) -> None:
    """Build an exact shortest-path closure for the fixture's real edges."""

    class_ids = {
        int(row[0]) for row in connection.execute("SELECT class_id FROM classes")
    }
    parents: dict[int, list[int]] = {}
    for child_id, parent_id in connection.execute(
        """
        SELECT child_class_id, parent_class_id
        FROM class_edges
        WHERE status='CONFIRMED'
        ORDER BY child_class_id, parent_class_id
        """
    ):
        parents.setdefault(int(child_id), []).append(int(parent_id))

    rows: list[tuple[int, int, int, str]] = []
    for descendant_id in sorted(class_ids):
        distance = {descendant_id: 0}
        shortest_path_count = {descendant_id: 1}
        queue: deque[int] = deque([descendant_id])
        while queue:
            child_id = queue.popleft()
            for parent_id in parents.get(child_id, ()):
                proposed = distance[child_id] + 1
                if parent_id not in distance:
                    distance[parent_id] = proposed
                    shortest_path_count[parent_id] = shortest_path_count[
                        child_id
                    ]
                    queue.append(parent_id)
                elif proposed == distance[parent_id]:
                    shortest_path_count[parent_id] = min(
                        2,
                        shortest_path_count[parent_id]
                        + shortest_path_count[child_id],
                    )
        for ancestor_id, depth in sorted(
            distance.items(), key=lambda item: (item[1], item[0])
        ):
            if depth == 0:
                path_status = "SELF"
            elif shortest_path_count[ancestor_id] == 1:
                path_status = "CONFIRMED"
            else:
                path_status = "AMBIGUOUS"
            rows.append(
                (ancestor_id, descendant_id, depth, path_status)
            )

    connection.execute("DELETE FROM class_closure")
    connection.executemany(
        """
        INSERT INTO class_closure(
            ancestor_class_id, descendant_class_id, depth, path_status
        ) VALUES (?, ?, ?, ?)
        """,
        rows,
    )


def _fixture() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(FULL_CORE_SCHEMA_SQL)
    connection.executemany(
        """
        INSERT INTO source_revisions(
            revision_id, source_kind, source_uri, source_fingerprint,
            producer_version, schema_version, generated_at, freshness_status
        ) VALUES (?, 'blueprint_evidence', ?, ?, 'fixture', 'v1',
                  '2026-07-27T00:00:00Z', ?)
        """,
        [
            (
                FRESH_REVISION_A,
                "bp://fixture/revision/fresh-a",
                "sha256:fresh-a",
                "FRESH",
            ),
            (
                STALE_REVISION,
                "bp://fixture/revision/stale",
                "sha256:stale",
                "STALE",
            ),
            (
                FRESH_REVISION_B,
                "bp://fixture/revision/fresh-b",
                "sha256:fresh-b",
                "FRESH",
            ),
        ],
    )
    _insert_class(
        connection,
        class_id=NATIVE_ROOT_CLASS,
        name="Object",
        is_native=True,
    )
    for entity_id, class_id, name in (
        (1, BASE_CLASS, "Base"),
        (2, CHILD_CLASS, "Child"),
        (3, LEAF_CLASS, "Leaf"),
        (4, OTHER_CLASS, "Other"),
    ):
        _insert_blueprint_entity(
            connection,
            entity_id=entity_id,
            class_id=class_id,
            name=name,
        )
    _insert_edge(connection, BASE_CLASS, NATIVE_ROOT_CLASS)
    _insert_edge(connection, CHILD_CLASS, BASE_CLASS)
    _insert_edge(connection, LEAF_CLASS, CHILD_CLASS)
    _insert_edge(connection, OTHER_CLASS, NATIVE_ROOT_CLASS)
    _rebuild_fixture_closure(connection)
    return connection


class KnowledgeEffectiveDefaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ontology = load_ontology(PROJECT_ROOT / "ontology")

    def test_identified_class_and_extracted_assignment_are_verified(self):
        connection = _fixture()
        fact_id = self._fact(
            connection,
            1,
            "Rate",
            FactValue("INTEGER", value_integer=7),
        )

        materialize_effective_defaults(connection)

        row = self._effective_row(connection, 2, "Rate")
        self.assertEqual(row["resolution_status"], "RESOLVED")
        self.assertEqual(row["fact_id"], fact_id)
        connection.close()

    def _fact(
        self,
        connection: sqlite3.Connection,
        entity_id: int,
        name: str,
        value: FactValue,
        status: str = "CONFIRMED",
        *,
        revision_ids: tuple[int, ...] = (FRESH_REVISION_A,),
    ) -> int:
        if not revision_ids:
            raise ValueError("Effective-default fixtures require evidence")
        fact_id = store_fact(
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
            source_revision_id=revision_ids[0],
            evidence_uri=(
                f"bp://fixture/fact/{entity_id}/{name}/{revision_ids[0]}"
            ),
            evidence_role="DEFAULT_VALUE",
        )
        for revision_id in revision_ids[1:]:
            connection.execute(
                """
                INSERT INTO fact_evidence(
                    fact_id, source_revision_id, evidence_uri, evidence_role
                ) VALUES (?, ?, ?, 'DEFAULT_VALUE')
                """,
                (
                    fact_id,
                    revision_id,
                    f"bp://fixture/fact/{entity_id}/{name}/{revision_id}",
                ),
            )
        return fact_id

    def _effective_row(
        self,
        connection: sqlite3.Connection,
        entity_id: int,
        name: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT
                entity_id, fact_name, fact_id, inherited_from_entity_id,
                resolution_chain_json, resolution_status,
                source_revision_set_hash
            FROM effective_facts
            WHERE entity_id=? AND fact_type='EFFECTIVE_DEFAULT'
              AND fact_name=?
            """,
            (entity_id, name),
        ).fetchone()
        self.assertIsNotNone(row)
        return row

    def _candidate_rows(
        self,
        connection: sqlite3.Connection,
        entity_id: int,
        name: str,
    ) -> list[tuple[object, ...]]:
        return [
            tuple(row)
            for row in connection.execute(
                """
                SELECT
                    candidate_fact_id, declared_on_entity_id,
                    inheritance_depth, path_status, selected,
                    rejection_reason
                FROM effective_fact_candidates
                WHERE entity_id=? AND fact_type='EFFECTIVE_DEFAULT'
                  AND fact_name=?
                ORDER BY
                    inheritance_depth, declared_on_entity_id,
                    candidate_fact_id
                """,
                (entity_id, name),
            )
        ]

    def _assert_unresolved(
        self,
        row: sqlite3.Row,
        *,
        status: str,
        start_class_id: int,
    ) -> None:
        self.assertIsNone(row["fact_id"])
        self.assertIsNone(row["inherited_from_entity_id"])
        self.assertEqual(row["resolution_status"], status)
        self.assertEqual(
            json.loads(row["resolution_chain_json"]),
            {
                "schema": "ark-kb-effective-path/v1",
                "startClassId": start_class_id,
                "declaredOnClassId": None,
                "declaredOnEntityId": None,
                "overrideDepth": None,
                "classes": [],
                "edges": [],
                "nativeRootProof": None,
            },
        )

    def test_schema_preserves_unresolved_rows_and_all_candidate_lineage(self):
        connection = _fixture()
        effective_columns = {
            str(row["name"]): row
            for row in connection.execute(
                "PRAGMA table_info(effective_facts)"
            )
        }
        self.assertEqual(effective_columns["fact_id"]["notnull"], 0)
        candidate_columns = [
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(effective_fact_candidates)"
            )
        ]
        self.assertEqual(
            candidate_columns,
            [
                "entity_id",
                "fact_type",
                "fact_name",
                "candidate_fact_id",
                "declared_on_entity_id",
                "inheritance_depth",
                "path_status",
                "selected",
                "rejection_reason",
            ],
        )
        connection.close()

    def test_not_recovered_child_falls_back_to_fresh_typed_parent(self):
        connection = _fixture()
        parent_fact = self._fact(
            connection,
            1,
            "Rate",
            FactValue("INTEGER", value_integer=7),
        )
        child_gap = self._fact(
            connection,
            2,
            "Rate",
            FactValue("UNKNOWN"),
            "NOT_RECOVERED",
        )

        materialize_effective_defaults(connection)

        row = self._effective_row(connection, 2, "Rate")
        self.assertEqual(row["fact_id"], parent_fact)
        self.assertEqual(row["inherited_from_entity_id"], 1)
        self.assertEqual(row["resolution_status"], "RESOLVED")
        self.assertEqual(
            tuple(
                connection.execute(
                    """
                    SELECT value_kind, value_integer
                    FROM facts WHERE fact_id=?
                    """,
                    (row["fact_id"],),
                ).fetchone()
            ),
            ("INTEGER", 7),
        )
        self.assertEqual(
            self._candidate_rows(connection, 2, "Rate"),
            [
                (
                    child_gap,
                    2,
                    0,
                    "SELF",
                    0,
                    "UNUSABLE_FACT_STATUS",
                ),
                (parent_fact, 1, 1, "CONFIRMED", 1, ""),
            ],
        )
        connection.close()

    def test_fingerprint_child_does_not_hide_parent_boolean_false(self):
        connection = _fixture()
        parent_fact = self._fact(
            connection,
            1,
            "Enabled",
            FactValue("BOOLEAN", value_integer=0),
        )
        child_fingerprint = self._fact(
            connection,
            2,
            "Enabled",
            FactValue("FINGERPRINT", value_text="sha256:child"),
            "CONFIRMED_FINGERPRINT_ONLY",
        )

        materialize_effective_defaults(connection)

        row = self._effective_row(connection, 2, "Enabled")
        self.assertEqual(row["fact_id"], parent_fact)
        self.assertEqual(row["resolution_status"], "RESOLVED")
        self.assertEqual(
            tuple(
                connection.execute(
                    """
                    SELECT value_kind, value_integer, status
                    FROM facts WHERE fact_id=?
                    """,
                    (row["fact_id"],),
                ).fetchone()
            ),
            ("BOOLEAN", 0, "CONFIRMED"),
        )
        self.assertEqual(
            self._candidate_rows(connection, 2, "Enabled"),
            [
                (
                    child_fingerprint,
                    2,
                    0,
                    "SELF",
                    0,
                    "UNUSABLE_VALUE_KIND",
                ),
                (parent_fact, 1, 1, "CONFIRMED", 1, ""),
            ],
        )
        connection.close()

    def test_confirmed_empty_is_a_usable_effective_value(self):
        connection = _fixture()
        empty_fact = self._fact(
            connection,
            1,
            "Items",
            FactValue("CONFIRMED_EMPTY"),
            "CONFIRMED_EMPTY",
        )

        materialize_effective_defaults(connection)

        row = self._effective_row(connection, 3, "Items")
        self.assertEqual(row["fact_id"], empty_fact)
        self.assertEqual(row["inherited_from_entity_id"], 1)
        self.assertEqual(row["resolution_status"], "RESOLVED")
        self.assertEqual(
            tuple(
                connection.execute(
                    """
                    SELECT value_kind, value_text, value_number,
                           value_integer, value_json
                    FROM facts WHERE fact_id=?
                    """,
                    (row["fact_id"],),
                ).fetchone()
            ),
            ("CONFIRMED_EMPTY", None, None, None, None),
        )
        self.assertEqual(
            self._candidate_rows(connection, 3, "Items"),
            [(empty_fact, 1, 2, "CONFIRMED", 1, "")],
        )
        connection.close()

    def test_near_stale_declaration_does_not_hide_farther_fresh_value(self):
        connection = _fixture()
        parent_fact = self._fact(
            connection,
            1,
            "Rate",
            FactValue("NUMBER", value_number=2.5),
        )
        stale_child = self._fact(
            connection,
            2,
            "Rate",
            FactValue("NUMBER", value_number=9.5),
            revision_ids=(STALE_REVISION,),
        )

        materialize_effective_defaults(connection)

        row = self._effective_row(connection, 3, "Rate")
        self.assertEqual(row["fact_id"], parent_fact)
        self.assertEqual(row["inherited_from_entity_id"], 1)
        self.assertEqual(row["resolution_status"], "RESOLVED")
        self.assertEqual(
            self._candidate_rows(connection, 3, "Rate"),
            [
                (
                    stale_child,
                    2,
                    1,
                    "CONFIRMED",
                    0,
                    "NO_FRESH_EVIDENCE",
                ),
                (parent_fact, 1, 2, "CONFIRMED", 1, ""),
            ],
        )
        connection.close()

    def test_mixed_fresh_and_stale_evidence_keeps_typed_fact_usable(self):
        connection = _fixture()
        mixed_fact = self._fact(
            connection,
            1,
            "Weight",
            FactValue("NUMBER", value_number=4.25),
            revision_ids=(STALE_REVISION, FRESH_REVISION_B),
        )

        materialize_effective_defaults(connection)

        row = self._effective_row(connection, 2, "Weight")
        self.assertEqual(row["fact_id"], mixed_fact)
        self.assertEqual(row["resolution_status"], "RESOLVED")
        self.assertEqual(
            {
                str(evidence_row[0])
                for evidence_row in connection.execute(
                    """
                    SELECT revision.freshness_status
                    FROM fact_evidence AS evidence
                    JOIN source_revisions AS revision
                      ON revision.revision_id=evidence.source_revision_id
                    WHERE evidence.fact_id=?
                    """,
                    (mixed_fact,),
                )
            },
            {"FRESH", "STALE"},
        )
        self.assertEqual(
            self._candidate_rows(connection, 2, "Weight"),
            [(mixed_fact, 1, 1, "CONFIRMED", 1, "")],
        )
        connection.close()

    def test_source_revision_hash_uses_stable_identity_not_local_row_id(self):
        first = _fixture()
        self._fact(
            first,
            1,
            "Rate",
            FactValue("INTEGER", value_integer=7),
            revision_ids=(FRESH_REVISION_A,),
        )
        materialize_effective_defaults(first)
        first_hash = self._effective_row(
            first, 2, "Rate"
        )["source_revision_set_hash"]

        second = _fixture()
        second.execute(
            """
            UPDATE source_revisions
            SET revision_id=101
            WHERE revision_id=?
            """,
            (FRESH_REVISION_A,),
        )
        second.execute(
            """
            UPDATE classes SET source_revision_id=101
            WHERE source_revision_id=?
            """,
            (FRESH_REVISION_A,),
        )
        second.execute(
            """
            UPDATE class_edges SET source_revision_id=101
            WHERE source_revision_id=?
            """,
            (FRESH_REVISION_A,),
        )
        self._fact(
            second,
            1,
            "Rate",
            FactValue("INTEGER", value_integer=7),
            revision_ids=(101,),
        )
        materialize_effective_defaults(second)
        second_hash = self._effective_row(
            second, 2, "Rate"
        )["source_revision_set_hash"]

        self.assertEqual(second_hash, first_hash)
        first.close()
        second.close()

    def test_native_root_class_proof_must_be_confirmed_high_and_fresh(self):
        mutations = {
            "stale revision": (
                """
                UPDATE classes
                SET source_revision_id=?
                WHERE class_id=?
                """,
                (STALE_REVISION, NATIVE_ROOT_CLASS),
            ),
            "unknown status": (
                """
                UPDATE classes
                SET status='UNKNOWN'
                WHERE class_id=?
                """,
                (NATIVE_ROOT_CLASS,),
            ),
            "low confidence": (
                """
                UPDATE classes
                SET confidence='LOW'
                WHERE class_id=?
                """,
                (NATIVE_ROOT_CLASS,),
            ),
        }
        for label, (sql, parameters) in mutations.items():
            with self.subTest(label=label):
                connection = _fixture()
                base_fact = self._fact(
                    connection,
                    1,
                    "Rate",
                    FactValue("INTEGER", value_integer=7),
                )
                connection.execute(sql, parameters)

                materialize_effective_defaults(connection)

                row = self._effective_row(connection, 2, "Rate")
                self._assert_unresolved(
                    row,
                    status="PARENT_CHAIN_OPEN",
                    start_class_id=CHILD_CLASS,
                )
                self.assertEqual(
                    self._candidate_rows(connection, 2, "Rate"),
                    [
                        (
                            base_fact,
                            1,
                            1,
                            "CONFIRMED",
                            0,
                            "PARENT_CHAIN_OPEN",
                        )
                    ],
                )
                connection.close()

    def test_invalid_nearest_native_boundary_cannot_be_skipped_for_fresh_root(
        self,
    ):
        connection = _fixture()
        _insert_class(
            connection,
            class_id=101,
            name="NativeBase",
            is_native=True,
        )
        connection.execute(
            "UPDATE classes SET source_revision_id=? WHERE class_id=101",
            (STALE_REVISION,),
        )
        connection.execute(
            """
            DELETE FROM class_edges
            WHERE child_class_id=? AND parent_class_id=?
            """,
            (BASE_CLASS, NATIVE_ROOT_CLASS),
        )
        _insert_edge(connection, BASE_CLASS, 101)
        _insert_edge(connection, 101, NATIVE_ROOT_CLASS)
        _rebuild_fixture_closure(connection)
        base_fact = self._fact(
            connection,
            1,
            "Rate",
            FactValue("INTEGER", value_integer=7),
        )

        materialize_effective_defaults(connection)

        row = self._effective_row(connection, 2, "Rate")
        self._assert_unresolved(
            row,
            status="PARENT_CHAIN_OPEN",
            start_class_id=CHILD_CLASS,
        )
        self.assertEqual(
            self._candidate_rows(connection, 2, "Rate"),
            [
                (
                    base_fact,
                    1,
                    1,
                    "CONFIRMED",
                    0,
                    "PARENT_CHAIN_OPEN",
                )
            ],
        )
        connection.close()

    def test_native_root_revision_identity_is_in_the_hash_and_proof(self):
        connection = _fixture()
        self._fact(
            connection,
            1,
            "Rate",
            FactValue("INTEGER", value_integer=7),
        )
        materialize_effective_defaults(connection)
        before = self._effective_row(connection, 2, "Rate")
        before_hash = str(before["source_revision_set_hash"])
        before_chain = json.loads(before["resolution_chain_json"])

        connection.execute(
            """
            UPDATE classes
            SET source_revision_id=?
            WHERE class_id=?
            """,
            (FRESH_REVISION_B, NATIVE_ROOT_CLASS),
        )
        materialize_effective_defaults(connection)

        after = self._effective_row(connection, 2, "Rate")
        after_chain = json.loads(after["resolution_chain_json"])
        self.assertNotEqual(after["source_revision_set_hash"], before_hash)
        self.assertEqual(
            before_chain["nativeRootProof"]["sourceRevision"],
            {
                "sourceKind": "blueprint_evidence",
                "sourceUri": "bp://fixture/revision/fresh-a",
                "sourceFingerprint": "sha256:fresh-a",
                "producerVersion": "fixture",
                "schemaVersion": "v1",
                "generatedAt": "2026-07-27T00:00:00Z",
                "freshnessStatus": "FRESH",
            },
        )
        self.assertEqual(
            after_chain["nativeRootProof"]["sourceRevision"],
            {
                "sourceKind": "blueprint_evidence",
                "sourceUri": "bp://fixture/revision/fresh-b",
                "sourceFingerprint": "sha256:fresh-b",
                "producerVersion": "fixture",
                "schemaVersion": "v1",
                "generatedAt": "2026-07-27T00:00:00Z",
                "freshnessStatus": "FRESH",
            },
        )
        connection.close()

    def test_same_depth_parents_are_ambiguous_even_with_identical_values(self):
        connection = _fixture()
        _insert_blueprint_entity(
            connection, entity_id=20, class_id=120, name="ParentA"
        )
        _insert_blueprint_entity(
            connection, entity_id=21, class_id=121, name="ParentB"
        )
        _insert_blueprint_entity(
            connection, entity_id=22, class_id=122, name="MultiChild"
        )
        _insert_edge(connection, 120, NATIVE_ROOT_CLASS)
        _insert_edge(connection, 121, NATIVE_ROOT_CLASS)
        _insert_edge(connection, 122, 120)
        _insert_edge(connection, 122, 121)
        _rebuild_fixture_closure(connection)
        parent_a = self._fact(
            connection,
            20,
            "Rate",
            FactValue("INTEGER", value_integer=7),
        )
        parent_b = self._fact(
            connection,
            21,
            "Rate",
            FactValue("INTEGER", value_integer=7),
        )

        materialize_effective_defaults(connection)

        row = self._effective_row(connection, 22, "Rate")
        self._assert_unresolved(
            row,
            status="AMBIGUOUS_INHERITANCE",
            start_class_id=122,
        )
        self.assertEqual(
            self._candidate_rows(connection, 22, "Rate"),
            [
                (
                    parent_a,
                    20,
                    1,
                    "CONFIRMED",
                    0,
                    "SAME_DEPTH_CONFLICT",
                ),
                (
                    parent_b,
                    21,
                    1,
                    "CONFIRMED",
                    0,
                    "SAME_DEPTH_CONFLICT",
                ),
            ],
        )
        connection.close()

    def test_unique_declaration_with_two_shortest_paths_is_ambiguous(self):
        connection = _fixture()
        _insert_blueprint_entity(
            connection, entity_id=30, class_id=130, name="DiamondOwner"
        )
        _insert_class(connection, class_id=131, name="DiamondLeft")
        _insert_class(connection, class_id=132, name="DiamondRight")
        _insert_blueprint_entity(
            connection, entity_id=33, class_id=133, name="DiamondLeaf"
        )
        _insert_edge(connection, 130, NATIVE_ROOT_CLASS)
        _insert_edge(connection, 131, 130)
        _insert_edge(connection, 132, 130)
        _insert_edge(connection, 133, 131)
        _insert_edge(connection, 133, 132)
        _rebuild_fixture_closure(connection)
        owner_fact = self._fact(
            connection,
            30,
            "Rate",
            FactValue("INTEGER", value_integer=11),
        )

        materialize_effective_defaults(connection)

        row = self._effective_row(connection, 33, "Rate")
        self._assert_unresolved(
            row,
            status="AMBIGUOUS_INHERITANCE",
            start_class_id=133,
        )
        self.assertEqual(
            self._candidate_rows(connection, 33, "Rate"),
            [
                (
                    owner_fact,
                    30,
                    2,
                    "AMBIGUOUS",
                    0,
                    "AMBIGUOUS_PATH",
                )
            ],
        )
        connection.close()

    def test_native_root_gap_blocks_an_otherwise_usable_self_declaration(self):
        connection = _fixture()
        _insert_blueprint_entity(
            connection, entity_id=40, class_id=140, name="OpenChain"
        )
        connection.execute(
            """
            INSERT INTO class_gaps(class_id, gap_kind, detail, status)
            VALUES (
                140, 'NATIVE_ROOT_NOT_REACHED',
                'fixture parent export stopped before a native root',
                'NOT_RECOVERED'
            )
            """
        )
        _rebuild_fixture_closure(connection)
        self_fact = self._fact(
            connection,
            40,
            "Rate",
            FactValue("INTEGER", value_integer=3),
        )

        materialize_effective_defaults(connection)

        row = self._effective_row(connection, 40, "Rate")
        self._assert_unresolved(
            row,
            status="PARENT_CHAIN_OPEN",
            start_class_id=140,
        )
        self.assertEqual(
            self._candidate_rows(connection, 40, "Rate"),
            [
                (
                    self_fact,
                    40,
                    0,
                    "SELF",
                    0,
                    "PARENT_CHAIN_OPEN",
                )
            ],
        )
        connection.close()

    def test_unconfirmed_generated_class_assignment_is_not_resolved(self):
        connection = _fixture()
        _insert_blueprint_entity(
            connection,
            entity_id=50,
            class_id=150,
            name="UnverifiedAssignment",
            assignment_status="NOT_RECOVERED",
        )
        _insert_edge(connection, 150, NATIVE_ROOT_CLASS)
        _rebuild_fixture_closure(connection)
        self_fact = self._fact(
            connection,
            50,
            "Rate",
            FactValue("INTEGER", value_integer=5),
        )

        materialize_effective_defaults(connection)

        row = self._effective_row(connection, 50, "Rate")
        self._assert_unresolved(
            row,
            status="ASSIGNMENT_UNVERIFIED",
            start_class_id=150,
        )
        self.assertEqual(
            self._candidate_rows(connection, 50, "Rate"),
            [
                (
                    self_fact,
                    50,
                    0,
                    "SELF",
                    0,
                    "ASSIGNMENT_UNVERIFIED",
                )
            ],
        )
        connection.close()

    def test_resolution_chain_contains_only_the_unique_selected_path(self):
        connection = _fixture()
        base_fact = self._fact(
            connection,
            1,
            "Rate",
            FactValue("INTEGER", value_integer=1),
        )
        child_fact = self._fact(
            connection,
            2,
            "Rate",
            FactValue("INTEGER", value_integer=2),
        )

        materialize_effective_defaults(connection)

        row = self._effective_row(connection, 3, "Rate")
        self.assertEqual(row["fact_id"], child_fact)
        self.assertEqual(
            json.loads(row["resolution_chain_json"]),
            {
                "schema": "ark-kb-effective-path/v1",
                "startClassId": LEAF_CLASS,
                "declaredOnClassId": CHILD_CLASS,
                "declaredOnEntityId": 2,
                "overrideDepth": 1,
                "classes": [LEAF_CLASS, CHILD_CLASS],
                "edges": [
                    {
                        "childClassId": LEAF_CLASS,
                        "parentClassId": CHILD_CLASS,
                        "edgeKind": "blueprint_parent",
                        "evidenceIds": [
                            "class-edge://fixture/13/12"
                        ],
                        "status": "CONFIRMED",
                    }
                ],
                "nativeRootProof": {
                    "schema": "ark-kb-native-root-proof/v1",
                    "startClassId": LEAF_CLASS,
                    "rootClassId": NATIVE_ROOT_CLASS,
                    "classes": [
                        LEAF_CLASS,
                        CHILD_CLASS,
                        BASE_CLASS,
                        NATIVE_ROOT_CLASS,
                    ],
                    "edges": [
                        {
                            "childClassId": LEAF_CLASS,
                            "parentClassId": CHILD_CLASS,
                            "edgeKind": "blueprint_parent",
                            "evidenceIds": [
                                "class-edge://fixture/13/12"
                            ],
                            "status": "CONFIRMED",
                        },
                        {
                            "childClassId": CHILD_CLASS,
                            "parentClassId": BASE_CLASS,
                            "edgeKind": "blueprint_parent",
                            "evidenceIds": [
                                "class-edge://fixture/12/11"
                            ],
                            "status": "CONFIRMED",
                        },
                        {
                            "childClassId": BASE_CLASS,
                            "parentClassId": NATIVE_ROOT_CLASS,
                            "edgeKind": "blueprint_parent",
                            "evidenceIds": [
                                "class-edge://fixture/11/100"
                            ],
                            "status": "CONFIRMED",
                        },
                    ],
                    "sourceRevision": {
                        "sourceKind": "blueprint_evidence",
                        "sourceUri": "bp://fixture/revision/fresh-a",
                        "sourceFingerprint": "sha256:fresh-a",
                        "producerVersion": "fixture",
                        "schemaVersion": "v1",
                        "generatedAt": "2026-07-27T00:00:00Z",
                        "freshnessStatus": "FRESH",
                    },
                },
            },
        )
        self.assertEqual(
            self._candidate_rows(connection, 3, "Rate"),
            [
                (child_fact, 2, 1, "CONFIRMED", 1, ""),
                (
                    base_fact,
                    1,
                    2,
                    "CONFIRMED",
                    0,
                    "SHADOWED_BY_NEARER_USABLE",
                ),
            ],
        )
        connection.close()

    def test_remote_multiple_parent_does_not_mislabel_self_path_ambiguous(self):
        connection = _fixture()
        _insert_blueprint_entity(
            connection, entity_id=60, class_id=160, name="RemoteBranch"
        )
        _insert_blueprint_entity(
            connection, entity_id=61, class_id=161, name="SecondBranch"
        )
        _insert_blueprint_entity(
            connection, entity_id=62, class_id=162, name="SelfOverride"
        )
        _insert_edge(connection, 160, NATIVE_ROOT_CLASS)
        _insert_edge(connection, 160, 161)
        _insert_edge(connection, 161, NATIVE_ROOT_CLASS)
        _insert_edge(connection, 162, 160)
        _rebuild_fixture_closure(connection)
        self_fact = self._fact(
            connection,
            62,
            "Rate",
            FactValue("INTEGER", value_integer=42),
        )

        materialize_effective_defaults(connection)

        row = self._effective_row(connection, 62, "Rate")
        self._assert_unresolved(
            row,
            status="AMBIGUOUS_INHERITANCE",
            start_class_id=162,
        )
        self.assertEqual(
            self._candidate_rows(connection, 62, "Rate"),
            [
                (
                    self_fact,
                    62,
                    0,
                    "SELF",
                    0,
                    "MULTIPLE_PARENT_CANDIDATES",
                )
            ],
        )
        connection.close()

    def test_multiple_parent_ambiguity_precedes_invalid_native_boundary(self):
        connection = _fixture()
        _insert_blueprint_entity(
            connection, entity_id=70, class_id=170, name="RemoteBranch"
        )
        _insert_blueprint_entity(
            connection, entity_id=71, class_id=171, name="SecondBranch"
        )
        _insert_blueprint_entity(
            connection, entity_id=72, class_id=172, name="SelfOverride"
        )
        _insert_edge(connection, 170, NATIVE_ROOT_CLASS)
        _insert_edge(connection, 170, 171)
        _insert_edge(connection, 171, NATIVE_ROOT_CLASS)
        _insert_edge(connection, 172, 170)
        _rebuild_fixture_closure(connection)
        connection.execute(
            """
            UPDATE classes
            SET source_revision_id=?
            WHERE class_id=?
            """,
            (STALE_REVISION, NATIVE_ROOT_CLASS),
        )
        self_fact = self._fact(
            connection,
            72,
            "Rate",
            FactValue("INTEGER", value_integer=42),
        )

        materialize_effective_defaults(connection)

        row = self._effective_row(connection, 72, "Rate")
        self._assert_unresolved(
            row,
            status="AMBIGUOUS_INHERITANCE",
            start_class_id=172,
        )
        self.assertEqual(
            self._candidate_rows(connection, 72, "Rate"),
            [
                (
                    self_fact,
                    72,
                    0,
                    "SELF",
                    0,
                    "MULTIPLE_PARENT_CANDIDATES",
                )
            ],
        )
        connection.close()

    def test_two_current_usable_declarations_on_same_owner_are_ambiguous(self):
        connection = _fixture()
        first_fact = self._fact(
            connection,
            1,
            "Rate",
            FactValue("INTEGER", value_integer=1),
        )
        second_fact = self._fact(
            connection,
            1,
            "Rate",
            FactValue("INTEGER", value_integer=2),
            revision_ids=(FRESH_REVISION_B,),
        )

        materialize_effective_defaults(connection)

        row = self._effective_row(connection, 2, "Rate")
        self._assert_unresolved(
            row,
            status="AMBIGUOUS_DECLARATION",
            start_class_id=CHILD_CLASS,
        )
        self.assertEqual(
            self._candidate_rows(connection, 2, "Rate"),
            [
                (
                    first_fact,
                    1,
                    1,
                    "CONFIRMED",
                    0,
                    "AMBIGUOUS_DECLARATION",
                ),
                (
                    second_fact,
                    1,
                    1,
                    "CONFIRMED",
                    0,
                    "AMBIGUOUS_DECLARATION",
                ),
            ],
        )
        connection.close()

    def test_changed_class_ids_remains_a_class_wide_compatibility_entrypoint(
        self,
    ):
        connection = _fixture()
        self._fact(
            connection,
            1,
            "Rate",
            FactValue("INTEGER", value_integer=1),
        )
        self._fact(
            connection,
            1,
            "Label",
            FactValue("TEXT", value_text="base"),
        )
        materialize_effective_defaults(connection)
        connection.executescript(
            """
            CREATE TEMP TABLE class_wide_insert_log(
                entity_id INTEGER NOT NULL,
                fact_name TEXT NOT NULL
            );
            CREATE TEMP TRIGGER log_class_wide_effective_insert
            AFTER INSERT ON effective_facts
            BEGIN
              INSERT INTO class_wide_insert_log
              VALUES (NEW.entity_id, NEW.fact_name);
            END;
            """
        )

        result = materialize_effective_defaults(
            connection,
            changed_class_ids=[BASE_CLASS],
        )

        self.assertEqual(result["affectedEntities"], 3)
        self.assertEqual(
            {
                (int(row["entity_id"]), str(row["fact_name"]))
                for row in connection.execute(
                    """
                    SELECT entity_id, fact_name
                    FROM class_wide_insert_log
                    """
                )
            },
            {
                (1, "Label"),
                (1, "Rate"),
                (2, "Label"),
                (2, "Rate"),
                (3, "Label"),
                (3, "Rate"),
            },
        )
        connection.close()

    def test_full_rebuild_replaces_stale_resolved_row_after_assignment_removal(
        self,
    ):
        connection = _fixture()
        base_fact = self._fact(
            connection,
            1,
            "Rate",
            FactValue("INTEGER", value_integer=7),
        )
        materialize_effective_defaults(connection)
        self.assertEqual(
            self._effective_row(connection, 2, "Rate")["fact_id"],
            base_fact,
        )
        connection.execute(
            """
            DELETE FROM asset_class_assignments
            WHERE entity_id=2 AND assignment_kind='GENERATED_CLASS'
            """
        )

        result = materialize_effective_defaults(connection)

        self.assertGreaterEqual(result["affectedEntities"], 4)
        row = self._effective_row(connection, 2, "Rate")
        self._assert_unresolved(
            row,
            status="NOT_RECOVERED",
            start_class_id=None,
        )
        self.assertEqual(
            self._candidate_rows(connection, 2, "Rate"),
            [],
        )
        connection.close()

    def test_full_rebuild_cleans_candidate_only_key_after_assignment_removal(
        self,
    ):
        connection = _fixture()
        self._fact(
            connection,
            1,
            "Rate",
            FactValue("INTEGER", value_integer=7),
        )
        materialize_effective_defaults(connection)
        connection.execute(
            """
            DELETE FROM effective_facts
            WHERE entity_id=2 AND fact_type='EFFECTIVE_DEFAULT'
              AND fact_name='Rate'
            """
        )
        connection.execute(
            """
            DELETE FROM asset_class_assignments
            WHERE entity_id=2 AND assignment_kind='GENERATED_CLASS'
            """
        )

        materialize_effective_defaults(connection)

        row = self._effective_row(connection, 2, "Rate")
        self._assert_unresolved(
            row,
            status="NOT_RECOVERED",
            start_class_id=None,
        )
        self.assertEqual(
            self._candidate_rows(connection, 2, "Rate"),
            [],
        )
        connection.close()

    def test_full_rebuild_is_bounded_for_more_than_25000_affected_entities(
        self,
    ):
        connection = _fixture()
        entity_count = 25_001
        first_entity_id = 10_000
        connection.executemany(
            """
            INSERT INTO entities(
                entity_id, canonical_uri, entity_kind, status, confidence
            ) VALUES (?, ?, 'BLUEPRINT_ASSET', 'CONFIRMED', 'HIGH')
            """,
            (
                (
                    entity_id,
                    f"/Game/Mass/Entity_{entity_id}.Entity_{entity_id}",
                )
                for entity_id in range(
                    first_entity_id,
                    first_entity_id + entity_count,
                )
            ),
        )
        connection.executemany(
            """
            INSERT INTO effective_facts(
                entity_id, fact_type, fact_name, fact_id,
                inherited_from_entity_id, resolution_chain_json,
                resolution_status, source_revision_set_hash
            ) VALUES (
                ?, 'EFFECTIVE_DEFAULT', 'Legacy', NULL, NULL, '{}',
                'NOT_RECOVERED', 'legacy'
            )
            """,
            (
                (entity_id,)
                for entity_id in range(
                    first_entity_id,
                    first_entity_id + entity_count,
                )
            ),
        )
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 512)

        result = materialize_effective_defaults(connection)

        self.assertEqual(result["workKeys"], entity_count)
        self.assertEqual(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM effective_facts
                WHERE fact_name='Legacy'
                  AND fact_id IS NULL
                  AND resolution_status='NOT_RECOVERED'
                """
            ).fetchone()[0],
            entity_count,
        )
        sample = self._effective_row(connection, first_entity_id, "Legacy")
        self._assert_unresolved(
            sample,
            status="NOT_RECOVERED",
            start_class_id=None,
        )
        connection.close()

    def test_incremental_rebuild_is_property_scoped_idempotent_and_bounded(self):
        connection = _fixture()
        old_rate = self._fact(
            connection,
            1,
            "Rate",
            FactValue("INTEGER", value_integer=1),
        )
        label_fact = self._fact(
            connection,
            1,
            "Label",
            FactValue("TEXT", value_text="base"),
        )
        other_fact = self._fact(
            connection,
            4,
            "Rate",
            FactValue("INTEGER", value_integer=99),
        )
        materialize_effective_defaults(connection)
        unrelated_before = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT * FROM effective_facts
                WHERE entity_id=4
                ORDER BY fact_type, fact_name
                """
            )
        ]
        unrelated_candidates_before = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT * FROM effective_fact_candidates
                WHERE entity_id=4
                ORDER BY fact_type, fact_name, candidate_fact_id
                """
            )
        ]
        connection.executescript(
            """
            CREATE TEMP TABLE effective_touch_log(
                action TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                fact_name TEXT NOT NULL
            );
            CREATE TEMP TRIGGER log_effective_delete
            AFTER DELETE ON effective_facts
            BEGIN
              INSERT INTO effective_touch_log
              VALUES ('DELETE', OLD.entity_id, OLD.fact_name);
            END;
            CREATE TEMP TRIGGER log_effective_insert
            AFTER INSERT ON effective_facts
            BEGIN
              INSERT INTO effective_touch_log
              VALUES ('INSERT', NEW.entity_id, NEW.fact_name);
            END;
            """
        )
        connection.execute(
            "UPDATE facts SET current=0 WHERE fact_id=?",
            (old_rate,),
        )
        base_rate_fact_id = self._fact(
            connection,
            1,
            "Rate",
            FactValue("INTEGER", value_integer=8),
            revision_ids=(FRESH_REVISION_B,),
        )

        result = materialize_effective_defaults(
            connection,
            changed_fact_ids=[base_rate_fact_id],
        )

        self.assertEqual(result["affectedEntities"], 3)
        self.assertEqual(
            {
                (str(row["action"]), int(row["entity_id"]), str(row["fact_name"]))
                for row in connection.execute(
                    "SELECT action, entity_id, fact_name FROM effective_touch_log"
                )
            },
            {
                ("DELETE", 1, "Rate"),
                ("INSERT", 1, "Rate"),
                ("DELETE", 2, "Rate"),
                ("INSERT", 2, "Rate"),
                ("DELETE", 3, "Rate"),
                ("INSERT", 3, "Rate"),
            },
        )
        self.assertEqual(
            {
                int(row[0])
                for row in connection.execute(
                    """
                    SELECT fact_id FROM effective_facts
                    WHERE entity_id IN (1, 2, 3) AND fact_name='Rate'
                    """
                )
            },
            {base_rate_fact_id},
        )
        self.assertEqual(
            {
                int(row[0])
                for row in connection.execute(
                    """
                    SELECT fact_id FROM effective_facts
                    WHERE entity_id IN (1, 2, 3) AND fact_name='Label'
                    """
                )
            },
            {label_fact},
        )
        self.assertEqual(
            [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT * FROM effective_facts
                    WHERE entity_id=4
                    ORDER BY fact_type, fact_name
                    """
                )
            ],
            unrelated_before,
        )
        self.assertEqual(
            [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT * FROM effective_fact_candidates
                    WHERE entity_id=4
                    ORDER BY fact_type, fact_name, candidate_fact_id
                    """
                )
            ],
            unrelated_candidates_before,
        )

        state_after_first_rebuild = {
            "effective": [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT * FROM effective_facts
                    ORDER BY entity_id, fact_type, fact_name
                    """
                )
            ],
            "candidates": [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT * FROM effective_fact_candidates
                    ORDER BY
                        entity_id, fact_type, fact_name,
                        inheritance_depth, candidate_fact_id
                    """
                )
            ],
        }
        materialize_effective_defaults(
            connection,
            changed_fact_ids=[base_rate_fact_id],
        )
        state_after_second_rebuild = {
            "effective": [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT * FROM effective_facts
                    ORDER BY entity_id, fact_type, fact_name
                    """
                )
            ],
            "candidates": [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT * FROM effective_fact_candidates
                    ORDER BY
                        entity_id, fact_type, fact_name,
                        inheritance_depth, candidate_fact_id
                    """
                )
            ],
        }
        self.assertEqual(
            state_after_second_rebuild,
            state_after_first_rebuild,
        )
        self.assertEqual(
            self._effective_row(connection, 4, "Rate")["fact_id"],
            other_fact,
        )
        connection.close()


if __name__ == "__main__":
    unittest.main()
