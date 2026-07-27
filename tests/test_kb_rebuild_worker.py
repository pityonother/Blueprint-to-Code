from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.projections import (  # noqa: E402
    DOMAIN_PROJECTIONS,
    PROJECTION_SCHEMA_SQL,
    PROJECTION_SCHEMA_VERSION,
    compute_projection_artifact_content_digest,
)
from blueprint_translator.kb_vnext import rebuild_worker as rebuild_worker_module  # noqa: E402
from blueprint_translator.kb_vnext.invalidation import (  # noqa: E402
    apply_invalidation_plan,
    plan_invalidation,
)
from blueprint_translator.kb_vnext.rebuild_worker import (  # noqa: E402
    BLOCKED_GAP,
    CoreMaterializerRebuildBackend,
    RebuildBackend,
    RebuildScope,
    drain_rebuild_queue,
    requeue_rebuild_tasks,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    CACHE_SCHEMA_SQL,
    FULL_CORE_SCHEMA_SQL,
)


def _core() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(FULL_CORE_SCHEMA_SQL)
    connection.execute(
        """
        INSERT INTO source_revisions VALUES (
            1, 'test', 'test://source', 'source-sha', 'test',
            'v1', '2026-07-28T00:00:00Z', 'FRESH'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO entities(
            entity_id, canonical_uri, entity_kind, status, confidence
        ) VALUES (
            1, '/Game/Test/Asset.Asset', 'BLUEPRINT_ASSET',
            'CONFIRMED', 'HIGH'
        )
        """
    )
    connection.commit()
    return connection


def _cache() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(CACHE_SCHEMA_SQL)
    return connection


def _queue(
    connection: sqlite3.Connection,
    rows: list[tuple[str, int]],
    *,
    queue_status: str = "PENDING_REBUILD",
    event_status: str = "APPLIED",
) -> None:
    connection.execute(
        """
        INSERT INTO invalidation_events VALUES (
            'event-1', 'TEST', NULL, '{}',
            '2026-07-28T00:00:00Z', ?
        )
        """,
        (event_status,),
    )
    connection.executemany(
        """
        INSERT INTO invalidation_queue VALUES (
            'event-1', ?, ?, 'TEST_DEPENDENCY', ?
        )
        """,
        [
            (downstream_kind, downstream_id, queue_status)
            for downstream_kind, downstream_id in rows
        ],
    )
    connection.commit()


def _seed_role(
    connection: sqlite3.Connection,
    *,
    status: str,
    entity_id: int = 1,
) -> None:
    connection.execute(
        """
        INSERT INTO role_metrics VALUES (
            ?, 'BLUEPRINT', 0, 0.0, 1.0, 0, 0.0, 1.0,
            0, 0.0, 1.0, 0, 0.0, 1.0, 0, 0.0, 1.0,
            NULL, 'NOT_MEASURED', NULL, 'NOT_MEASURED',
            0, 0, 0, 0.0, 1.0, '[]', 'test-role/v1'
        )
        """,
        (entity_id,),
    )
    connection.execute(
        """
        INSERT INTO knowledge_roles VALUES (
            ?, 'catalog_asset', 'HIGH', ?, '[]', 'test-role/v1', 1
        )
        """,
        (entity_id, status),
    )
    connection.execute(
        """
        INSERT INTO knowledge_depth_policies VALUES (
            ?, 'INDEX_ONLY', '[]', 'test-role/v1'
        )
        """,
        (entity_id,),
    )
    connection.commit()


def _seed_integration_core(connection: sqlite3.Connection) -> None:
    connection.executemany(
        """
        INSERT INTO classes(
            class_id, class_path, class_name, module_or_package,
            class_kind, is_native, source_revision_id, status, confidence
        ) VALUES (?, ?, ?, ?, ?, ?, 1, 'IDENTIFIED', 'HIGH')
        """,
        [
            (
                100,
                "/Script/CoreUObject.Object",
                "Object",
                "CoreUObject",
                "NATIVE_UCLASS",
                1,
            ),
            (
                11,
                "/Game/Test/Asset.Asset_C",
                "Asset_C",
                "/Game/Test",
                "BLUEPRINT_GENERATED_CLASS",
                0,
            ),
        ],
    )
    connection.execute(
        """
        INSERT INTO class_edges VALUES (
            11, 100, 'native_parent', 'class-edge://asset/object',
            1, 'CONFIRMED', 'HIGH'
        )
        """
    )
    connection.execute(
        "INSERT INTO class_closure VALUES (100, 100, 0, 'SELF')"
    )
    connection.execute(
        """
        INSERT INTO asset_class_assignments VALUES (
            1, 11, 'GENERATED_CLASS', 'bp://asset/generated-class',
            'EXTRACTED', 'HIGH', 1
        )
        """
    )
    connection.execute(
        """
        INSERT INTO facts(
            fact_id, subject_entity_id, fact_type, fact_name, scope_kind,
            declared_on_entity_id, value_kind, value_integer, status,
            confidence, ontology_version, current, canonical_fact_key
        ) VALUES (
            1, 1, 'DECLARED_DEFAULT', 'Rate', 'DECLARED', 1,
            'INTEGER', 7, 'CONFIRMED', 'HIGH', 'test-ontology/v1',
            0, 'fact://rate'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO fact_evidence VALUES (
            1, 1, 'bp://asset/default/Rate', 'DEFAULT_VALUE_ACTUAL'
        )
        """
    )
    _seed_role(connection, status="STALE")
    connection.execute(
        """
        INSERT INTO domain_memberships VALUES (
            1, 'item_use', 'CLASS_ANCESTRY', 'HIGH', 'STALE',
            'class-category://11/ITEM', 'test-ontology/v1', 1
        )
        """
    )
    connection.execute(
        """
        INSERT INTO edges(
            edge_id, source_entity_id, target_entity_id, edge_type,
            edge_strength, status, confidence, source_revision_id,
            evidence_uri
        ) VALUES (
            1, 1, 1, 'REFERENCES', 'HARD', 'STALE', 'HIGH', 1,
            'bp://asset/reference'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO typed_registrations VALUES (
            'registration-1', '/Game/Test/Asset.Asset',
            '/Game/Test/Asset.Asset', 'item_registration', 'ItemClass',
            'bp://asset/registration', 'DECLARED', 'HIGH', 'STALE',
            1, 'test-registration/v1', 'exact_source_property'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO native_functions VALUES (
            21, 'native://fixture/function', 'UAsset::Rate',
            'ShooterGame.dll', '0x10', 'void Rate()', 'binary', 'pdb',
            'guid/1', '["recipe/v1"]', '["set/v1"]', 0, 0,
            'NOT_RECOVERED', 'STALE', 'HIGH', 1
        )
        """
    )
    connection.execute(
        """
        INSERT INTO native_gold_targets VALUES (
            'native-target-1', 'item_use', 'UAsset::Rate', '0x10',
            'recipe/v1', 21, 'GAP', 'SOURCE_REVISION_STALE'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO native_blueprint_links VALUES (
            'link-1', 1, 'bp://asset/graph', 'Rate', 21,
            'native://fixture/function', 'verified_callsite',
            'CANDIDATE', 'LOW', 1
        )
        """
    )
    for projection_name in DOMAIN_PROJECTIONS:
        connection.execute(
            """
            INSERT INTO projection_runs VALUES (
                ?, 'v2', 'old-hash', 'test-ontology/v1',
                '2026-07-28T00:00:00Z', 0, 'STALE'
            )
            """,
            (projection_name,),
        )
    connection.commit()


def _seed_cache(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO query_snapshots VALUES (
            'snapshot-1', 'query-1', '{}', '{}', 'old-revision',
            'old-token', '2026-07-28T00:00:00Z',
            '2026-07-29T00:00:00Z', 'FRESH'
        )
        """
    )
    connection.commit()


def _write_projection(path: Path, projection_name: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(PROJECTION_SCHEMA_SQL)
        content_digest = compute_projection_artifact_content_digest(
            connection
        )
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [
                ("schema_version", PROJECTION_SCHEMA_VERSION),
                ("projection_name", projection_name),
                ("projection_version", "v2"),
                ("source_revision_set_hash", "fresh-hash"),
                ("ontology_version", "test-ontology/v1"),
                ("built_at", "2026-07-28T01:00:00Z"),
                ("truth_source", "core.sqlite"),
                ("review_version", "test-review/v1"),
                ("review_status", "UNREVIEWED"),
                ("review_config_sha256", "test-review-sha"),
                ("content_digest", content_digest),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def _write_wrong_projection(path: Path, projection_name: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(PROJECTION_SCHEMA_SQL)
        connection.execute(
            """
            INSERT INTO projection_rows VALUES (
                1, 999, 1, '/Game/Fake.Fake', 'STATUS_EFFECT',
                'FakeDuration', 'DERIVED_STATIC', 'NUMBER',
                NULL, 123.0, NULL, NULL, '', 'CONFIRMED', 'HIGH',
                'test-ontology/v1', 'COMPLETE', 0, 'fake-hash'
            )
            """
        )
        content_digest = compute_projection_artifact_content_digest(
            connection
        )
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [
                ("schema_version", PROJECTION_SCHEMA_VERSION),
                ("projection_name", projection_name),
                ("projection_version", "v2"),
                ("source_revision_set_hash", "fake-hash"),
                ("ontology_version", "test-ontology/v1"),
                ("built_at", "2026-07-28T01:00:00Z"),
                ("truth_source", "core.sqlite"),
                ("review_version", "test-review/v1"),
                ("review_status", "UNREVIEWED"),
                ("review_config_sha256", "test-review-sha"),
                ("content_digest", content_digest),
            ],
        )
        connection.commit()
    finally:
        connection.close()


class IntegrationBackend(CoreMaterializerRebuildBackend):
    def __init__(
        self,
        *,
        projection_dir: Path,
        cache_connection: sqlite3.Connection,
    ) -> None:
        super().__init__(
            projection_dir=projection_dir,
            cache_connection=cache_connection,
        )
        self.projection_calls = 0

    def rebuild_registration_entity(self, scope: RebuildScope) -> None:
        scope.core.execute(
            """
            UPDATE typed_registrations
            SET status='CONFIRMED', confidence='HIGH'
            WHERE owner_uri='/Game/Test/Asset.Asset'
               OR target_uri='/Game/Test/Asset.Asset'
            """
        )

    def rebuild_fact(self, scope: RebuildScope) -> None:
        scope.core.execute(
            """
            UPDATE facts
            SET current=1, status='CONFIRMED'
            WHERE fact_id=?
            """,
            (scope.task.downstream_id,),
        )

    def rebuild_edge_entity(self, scope: RebuildScope) -> None:
        scope.core.execute(
            "UPDATE edges SET status='CONFIRMED' WHERE source_entity_id=?",
            (scope.task.downstream_id,),
        )

    def rebuild_native_function(self, scope: RebuildScope) -> None:
        scope.core.execute(
            """
            UPDATE native_functions
            SET status='CONFIRMED'
            WHERE native_function_id=?
            """,
            (scope.task.downstream_id,),
        )
        scope.core.execute(
            """
            UPDATE native_gold_targets
            SET status='CONFIRMED', gap_code=''
            WHERE native_function_id=?
            """,
            (scope.task.downstream_id,),
        )

    def rebuild_blueprint_native_entity(
        self,
        scope: RebuildScope,
    ) -> None:
        scope.core.execute(
            """
            UPDATE native_blueprint_links
            SET status='CONFIRMED', confidence='HIGH'
            WHERE blueprint_entity_id=?
            """,
            (scope.task.downstream_id,),
        )

    def rebuild_role_entity(self, scope: RebuildScope) -> None:
        scope.core.execute(
            """
            UPDATE knowledge_roles
            SET status='CONFIRMED'
            WHERE entity_id=?
            """,
            (scope.task.downstream_id,),
        )

    def rebuild_domain_entity(self, scope: RebuildScope) -> None:
        scope.core.execute(
            """
            UPDATE domain_memberships
            SET status='CONFIRMED'
            WHERE entity_id=?
            """,
            (scope.task.downstream_id,),
        )

    def rebuild_projection(self, scope: RebuildScope) -> None:
        self.projection_calls += 1
        for projection_name in DOMAIN_PROJECTIONS:
            scope.core.execute(
                """
                UPDATE projection_runs
                SET source_revision_set_hash='fresh-hash',
                    built_at='2026-07-28T01:00:00Z',
                    validation_status='VALID'
                WHERE projection_name=?
                """,
                (projection_name,),
            )
            _write_projection(
                scope.projection_dir / f"{projection_name}.sqlite",
                projection_name,
            )

    def rebuild_query_snapshot(self, scope: RebuildScope) -> None:
        assert scope.cache is not None
        for table in (
            "context_packs",
            "answer_plans",
            "materialized_neighborhoods",
            "query_snapshots",
        ):
            scope.cache.execute(f'DELETE FROM "{table}"')


class NoopRoleBackend(RebuildBackend):
    def rebuild_role_entity(self, scope: RebuildScope) -> None:
        return None


class TouchOnlyRoleBackend(RebuildBackend):
    def rebuild_role_entity(self, scope: RebuildScope) -> None:
        scope.core.execute(
            """
            UPDATE knowledge_roles
            SET status=status
            WHERE entity_id=?
            """,
            (scope.task.downstream_id,),
        )


class TempShadowRoleBackend(RebuildBackend):
    def rebuild_role_entity(self, scope: RebuildScope) -> None:
        scope.core.execute(
            """
            CREATE TEMP TABLE knowledge_roles(
                entity_id INTEGER,
                role TEXT,
                confidence TEXT,
                status TEXT,
                reasons_json TEXT,
                classifier_version TEXT,
                source_revision_id INTEGER
            )
            """
        )
        scope.core.execute(
            """
            INSERT INTO temp.knowledge_roles VALUES (
                ?, 'catalog_asset', 'HIGH', 'CONFIRMED',
                '[]', 'shadow/v1', 1
            )
            """,
            (scope.task.downstream_id,),
        )


class CursorEscapeRoleBackend(RebuildBackend):
    def rebuild_role_entity(self, scope: RebuildScope) -> None:
        scope.core.execute(
            """
            UPDATE knowledge_roles
            SET status='CONFIRMED'
            WHERE entity_id=?
            """,
            (scope.task.downstream_id,),
        )
        raw = scope.core.execute("SELECT 1").connection
        raw.set_authorizer(None)
        raw.commit()


class RowFactoryForgeRoleBackend(RebuildBackend):
    def rebuild_role_entity(self, scope: RebuildScope) -> None:
        scope.core.execute(
            """
            UPDATE knowledge_roles
            SET status=status
            WHERE entity_id=?
            """,
            (scope.task.downstream_id,),
        )
        scope.core.row_factory = lambda _cursor, row: row


class SelfOnlyClosureBackend(RebuildBackend):
    def rebuild_class_closure(self, scope: RebuildScope) -> None:
        scope.core.execute(
            "DELETE FROM class_closure WHERE descendant_class_id=?",
            (scope.task.downstream_id,),
        )
        scope.core.execute(
            "INSERT INTO class_closure VALUES (?, ?, 0, 'SELF')",
            (
                scope.task.downstream_id,
                scope.task.downstream_id,
            ),
        )


class RootOnlyClosureBackend(RebuildBackend):
    def rebuild_class_closure(self, scope: RebuildScope) -> None:
        scope.core.execute(
            "DELETE FROM class_closure WHERE descendant_class_id=?",
            (scope.task.downstream_id,),
        )
        scope.core.executemany(
            "INSERT INTO class_closure VALUES (?, ?, ?, ?)",
            [
                (
                    scope.task.downstream_id,
                    scope.task.downstream_id,
                    0,
                    "SELF",
                ),
                (
                    100,
                    scope.task.downstream_id,
                    1,
                    "CONFIRMED",
                ),
            ],
        )


class NoopClosureBackend(RebuildBackend):
    def rebuild_class_closure(self, scope: RebuildScope) -> None:
        del scope


class UpdateOnlyClosureBackend(RebuildBackend):
    def rebuild_class_closure(self, scope: RebuildScope) -> None:
        scope.core.execute(
            """
            UPDATE class_closure
            SET path_status=path_status
            WHERE descendant_class_id=?
            """,
            (scope.task.downstream_id,),
        )


class CommitThenFailBackend(RebuildBackend):
    def rebuild_role_entity(self, scope: RebuildScope) -> None:
        scope.core.execute(
            """
            UPDATE knowledge_roles
            SET status='CONFIRMED'
            WHERE entity_id=?
            """,
            (scope.task.downstream_id,),
        )
        scope.core.commit()
        raise RuntimeError("synthetic failure after commit attempt")

    def rebuild_domain_entity(self, scope: RebuildScope) -> None:
        scope.core.execute(
            """
            UPDATE domain_memberships
            SET status='CONFIRMED'
            WHERE entity_id=?
            """,
            (scope.task.downstream_id,),
        )


class CommentCommitThenFailBackend(RebuildBackend):
    def rebuild_role_entity(self, scope: RebuildScope) -> None:
        scope.core.execute(
            """
            UPDATE knowledge_roles
            SET status='CONFIRMED'
            WHERE entity_id=?
            """,
            (scope.task.downstream_id,),
        )
        scope.core.execute("; -- transaction-control bypass\nCOMMIT")
        raise RuntimeError("synthetic failure after commented commit")


class RepairRoleBackend(RebuildBackend):
    def rebuild_role_entity(self, scope: RebuildScope) -> None:
        scope.core.execute(
            """
            UPDATE knowledge_roles
            SET status='CONFIRMED'
            WHERE entity_id=?
            """,
            (scope.task.downstream_id,),
        )


class RepairDomainBackend(RebuildBackend):
    def rebuild_domain_entity(self, scope: RebuildScope) -> None:
        scope.core.execute(
            """
            UPDATE domain_memberships
            SET status='CONFIRMED'
            WHERE entity_id=?
            """,
            (scope.task.downstream_id,),
        )


class FailingProjectionBackend(RebuildBackend):
    def rebuild_projection(self, scope: RebuildScope) -> None:
        projection_name = tuple(DOMAIN_PROJECTIONS)[
            scope.task.downstream_id - 1
        ]
        scope.core.execute(
            """
            UPDATE projection_runs
            SET validation_status='VALID'
            WHERE projection_name=?
            """,
            (projection_name,),
        )
        _write_projection(
            scope.projection_dir / f"{projection_name}.sqlite",
            projection_name,
        )
        scope.core.commit()
        raise RuntimeError("fail after staged projection build")


class CompleteProjectionBackend(RebuildBackend):
    def rebuild_projection(self, scope: RebuildScope) -> None:
        for projection_name in DOMAIN_PROJECTIONS:
            scope.core.execute(
                """
                UPDATE projection_runs
                SET source_revision_set_hash='fresh-hash',
                    ontology_version='test-ontology/v1',
                    validation_status='VALID'
                WHERE projection_name=?
                """,
                (projection_name,),
            )
            _write_projection(
                scope.projection_dir / f"{projection_name}.sqlite",
                projection_name,
            )


class MetadataOnlyProjectionBackend(RebuildBackend):
    def rebuild_projection(self, scope: RebuildScope) -> None:
        projection_name = tuple(DOMAIN_PROJECTIONS)[
            scope.task.downstream_id - 1
        ]
        scope.core.execute(
            """
            UPDATE projection_runs
            SET source_revision_set_hash='fresh-hash',
                ontology_version='test-ontology/v1',
                validation_status='VALID'
            WHERE projection_name=?
            """,
            (projection_name,),
        )
        artifact = sqlite3.connect(
            scope.projection_dir / f"{projection_name}.sqlite"
        )
        try:
            artifact.execute(
                "CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)"
            )
            artifact.executemany(
                "INSERT INTO metadata VALUES (?, ?)",
                [
                    ("projection_name", projection_name),
                    ("content_digest", "trust-me"),
                ],
            )
            artifact.commit()
        finally:
            artifact.close()


class WrongContentProjectionBackend(RebuildBackend):
    def rebuild_projection(self, scope: RebuildScope) -> None:
        projection_name = tuple(DOMAIN_PROJECTIONS)[
            scope.task.downstream_id - 1
        ]
        scope.core.execute(
            """
            UPDATE projection_runs
            SET source_revision_set_hash='fake-hash',
                ontology_version='test-ontology/v1',
                row_count=1,
                validation_status='VALID'
            WHERE projection_name=?
            """,
            (projection_name,),
        )
        _write_wrong_projection(
            scope.projection_dir / f"{projection_name}.sqlite",
            projection_name,
        )


class CountingQueryBackend(RebuildBackend):
    def __init__(self, *, cache_connection: sqlite3.Connection) -> None:
        super().__init__(cache_connection=cache_connection)
        self.calls = 0

    def rebuild_query_snapshot(self, scope: RebuildScope) -> None:
        self.calls += 1
        assert scope.cache is not None
        for table in (
            "context_packs",
            "answer_plans",
            "materialized_neighborhoods",
            "query_snapshots",
        ):
            scope.cache.execute(f'DELETE FROM "{table}"')


class NoopQueryBackend(RebuildBackend):
    def rebuild_query_snapshot(self, scope: RebuildScope) -> None:
        return None


class RawCacheEscapeBackend(RebuildBackend):
    def rebuild_query_snapshot(self, scope: RebuildScope) -> None:
        self.cache_connection.execute("DELETE FROM query_snapshots")
        self.cache_connection.set_authorizer(None)
        self.cache_connection.commit()


class PublishedPathEscapeBackend(RebuildBackend):
    def rebuild_projection(self, scope: RebuildScope) -> None:
        projection_name = tuple(DOMAIN_PROJECTIONS)[
            scope.task.downstream_id - 1
        ]
        _write_projection(
            self.projection_dir / f"{projection_name}.sqlite",
            projection_name,
        )
        raise RuntimeError("published path should not be reachable")


class SimulatedProcessCrash(BaseException):
    pass


class RebuildWorkerTests(unittest.TestCase):
    def test_real_schema_backend_rebuilds_all_kinds_and_projections_once(
        self,
    ) -> None:
        core = _core()
        cache = _cache()
        _seed_integration_core(core)
        _seed_cache(cache)
        queue_rows = [
            ("CLASS_CLOSURE", 11),
            ("REGISTRATION_ENTITY", 1),
            ("FACT", 1),
            ("EDGE_ENTITY", 1),
            ("NATIVE_FUNCTION", 21),
            ("BLUEPRINT_NATIVE_ENTITY", 1),
            ("EFFECTIVE_ENTITY", 1),
            ("ROLE_ENTITY", 1),
            ("DOMAIN_ENTITY", 1),
            *[
                ("PROJECTION", projection_id)
                for projection_id in range(
                    1,
                    len(DOMAIN_PROJECTIONS) + 1,
                )
            ],
            ("QUERY_SNAPSHOT", 1),
        ]
        _queue(core, queue_rows)

        with tempfile.TemporaryDirectory() as temporary:
            backend = IntegrationBackend(
                projection_dir=Path(temporary),
                cache_connection=cache,
            )
            report = drain_rebuild_queue(
                core,
                backend,
                max_items=len(queue_rows),
                recover_running=False,
            )

        self.assertEqual(report.attempted, len(queue_rows))
        self.assertEqual(report.succeeded, len(queue_rows))
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.blocked_gap, 0)
        self.assertEqual(backend.projection_calls, 1)
        self.assertTrue(
            all(
                status == "SUCCEEDED"
                for (status,) in core.execute(
                    "SELECT status FROM invalidation_queue"
                )
            )
        )
        self.assertEqual(
            core.execute(
                """
                SELECT resolution_status
                FROM effective_facts
                WHERE entity_id=1 AND fact_name='Rate'
                """
            ).fetchone()[0],
            "RESOLVED",
        )
        self.assertEqual(
            cache.execute(
                "SELECT COUNT(*) FROM query_snapshots"
            ).fetchone()[0],
            0,
        )
        payload = json.loads(
            core.execute(
                "SELECT payload_json FROM invalidation_events"
            ).fetchone()[0]
        )
        self.assertEqual(
            len(payload["_rebuildReceipts"]),
            len(queue_rows),
        )
        self.assertTrue(
            all(
                receipt["proof"].startswith("rebuild-proof://")
                for receipt in payload["_rebuildReceipts"].values()
            )
        )

        repeat = drain_rebuild_queue(
            core,
            backend,
            max_items=len(queue_rows),
            recover_running=False,
        )
        self.assertEqual(repeat.attempted, 0)
        self.assertEqual(backend.projection_calls, 1)
        core.close()
        cache.close()

    def test_noop_backend_cannot_self_attest_success(self) -> None:
        core = _core()
        _seed_role(core, status="STALE")
        _queue(core, [("ROLE_ENTITY", 1)])

        report = drain_rebuild_queue(
            core,
            NoopRoleBackend(),
            max_items=1,
            recover_running=False,
        )

        self.assertEqual(report.failed, 1)
        self.assertEqual(report.succeeded, 0)
        self.assertEqual(
            core.execute(
                "SELECT status FROM invalidation_queue"
            ).fetchone()[0],
            "FAILED",
        )
        self.assertEqual(
            core.execute(
                "SELECT status FROM knowledge_roles"
            ).fetchone()[0],
            "STALE",
        )
        core.close()

    def test_unchanged_target_write_cannot_self_attest_success(self) -> None:
        core = _core()
        _seed_role(core, status="CONFIRMED")
        _queue(core, [("ROLE_ENTITY", 1)])

        report = drain_rebuild_queue(
            core,
            TouchOnlyRoleBackend(),
            max_items=1,
            recover_running=False,
        )

        self.assertEqual(report.failed, 1)
        self.assertEqual(report.succeeded, 0)
        self.assertEqual(
            core.execute(
                "SELECT status FROM invalidation_queue"
            ).fetchone()[0],
            "FAILED",
        )
        core.close()

    def test_temp_shadow_table_cannot_forge_verified_main_state(
        self,
    ) -> None:
        core = _core()
        _seed_role(core, status="STALE")
        _queue(core, [("ROLE_ENTITY", 1)])

        report = drain_rebuild_queue(
            core,
            TempShadowRoleBackend(),
            max_items=1,
            recover_running=False,
        )

        self.assertEqual(report.failed, 1)
        self.assertEqual(report.succeeded, 0)
        self.assertEqual(
            core.execute(
                """
                SELECT status
                FROM main.knowledge_roles
                WHERE entity_id=1
                """
            ).fetchone()[0],
            "STALE",
        )
        core.close()

    def test_cursor_does_not_leak_raw_connection_or_commit_control(
        self,
    ) -> None:
        core = _core()
        _seed_role(core, status="STALE")
        _queue(core, [("ROLE_ENTITY", 1)])

        report = drain_rebuild_queue(
            core,
            CursorEscapeRoleBackend(),
            max_items=1,
            recover_running=False,
        )

        self.assertEqual(report.failed, 1)
        self.assertEqual(
            core.execute(
                "SELECT status FROM knowledge_roles"
            ).fetchone()[0],
            "STALE",
        )
        core.close()

    def test_backend_cannot_replace_worker_inspection_row_factory(
        self,
    ) -> None:
        core = _core()
        _seed_role(core, status="STALE")
        _queue(core, [("ROLE_ENTITY", 1)])

        report = drain_rebuild_queue(
            core,
            RowFactoryForgeRoleBackend(),
            max_items=1,
            recover_running=False,
        )

        self.assertEqual(report.failed, 1)
        self.assertIsNone(core.row_factory)
        self.assertEqual(
            core.execute(
                "SELECT status FROM knowledge_roles"
            ).fetchone()[0],
            "STALE",
        )
        core.close()

    def test_class_closure_requires_all_reachable_ancestors(self) -> None:
        core = _core()
        _seed_integration_core(core)
        core.execute(
            "INSERT INTO class_closure VALUES (100, 11, 1, 'CONFIRMED')"
        )
        core.execute(
            "INSERT INTO class_closure VALUES (11, 11, 0, 'SELF')"
        )
        core.commit()
        _queue(core, [("CLASS_CLOSURE", 11)])

        report = drain_rebuild_queue(
            core,
            SelfOnlyClosureBackend(),
            max_items=1,
            recover_running=False,
        )

        self.assertEqual(report.succeeded, 0)
        self.assertEqual(report.failed, 1)
        self.assertIn(
            (100, 11),
            {
                (int(ancestor), int(descendant))
                for ancestor, descendant in core.execute(
                    """
                    SELECT ancestor_class_id, descendant_class_id
                    FROM class_closure
                    """
                )
            },
        )
        core.close()

    def test_class_closure_verifies_every_current_descendant(self) -> None:
        core = _core()
        _seed_integration_core(core)
        core.execute(
            """
            INSERT INTO classes(
                class_id, class_path, class_name, module_or_package,
                class_kind, is_native, source_revision_id, status, confidence
            ) VALUES (
                12, '/Game/Test/Child.Child_C', 'Child_C', '/Game/Test',
                'BLUEPRINT_GENERATED_CLASS', 0, 1, 'IDENTIFIED', 'HIGH'
            )
            """
        )
        core.execute(
            """
            INSERT INTO class_edges VALUES (
                12, 11, 'blueprint_parent', 'class-edge://child/asset',
                1, 'CONFIRMED', 'HIGH'
            )
            """
        )
        core.executemany(
            "INSERT INTO class_closure VALUES (?, ?, ?, ?)",
            [
                (11, 11, 0, "SELF"),
                (12, 12, 0, "SELF"),
            ],
        )
        core.commit()
        _queue(core, [("CLASS_CLOSURE", 11)])

        report = drain_rebuild_queue(
            core,
            RootOnlyClosureBackend(),
            max_items=1,
            recover_running=False,
        )

        self.assertEqual(report.succeeded, 0)
        self.assertEqual(report.failed, 1)
        self.assertEqual(
            list(
                core.execute(
                    """
                    SELECT ancestor_class_id, descendant_class_id
                    FROM class_closure
                    ORDER BY descendant_class_id, ancestor_class_id
                    """
                )
            ),
            [(11, 11), (12, 12), (100, 100)],
        )
        core.close()

    def test_class_closure_event_scope_preserves_removed_descendant(
        self,
    ) -> None:
        core = _core()
        _seed_integration_core(core)
        core.execute(
            """
            INSERT INTO classes(
                class_id, class_path, class_name, module_or_package,
                class_kind, is_native, source_revision_id, status, confidence
            ) VALUES (
                12, '/Game/Test/Child.Child_C', 'Child_C', '/Game/Test',
                'BLUEPRINT_GENERATED_CLASS', 0, 1, 'IDENTIFIED', 'HIGH'
            )
            """
        )
        core.execute(
            """
            INSERT INTO class_edges VALUES (
                12, 11, 'blueprint_parent', 'class-edge://child/asset',
                1, 'CONFIRMED', 'HIGH'
            )
            """
        )
        core.executemany(
            "INSERT INTO class_closure VALUES (?, ?, ?, ?)",
            [
                (11, 11, 0, "SELF"),
                (100, 11, 1, "CONFIRMED"),
                (12, 12, 0, "SELF"),
                (11, 12, 1, "CONFIRMED"),
                (100, 12, 2, "CONFIRMED"),
            ],
        )
        core.commit()
        plan = plan_invalidation(
            core,
            event_kind="CLASS",
            class_ids=[11],
        )
        core.execute(
            "DELETE FROM class_edges WHERE child_class_id=12"
        )
        core.execute(
            """
            INSERT INTO class_edges VALUES (
                12, 100, 'native_parent', 'class-edge://child/object',
                1, 'CONFIRMED', 'HIGH'
            )
            """
        )
        invalidation = apply_invalidation_plan(
            core,
            plan,
            created_at="2026-07-28T00:30:00Z",
        )
        payload = json.loads(
            str(
                core.execute(
                    "SELECT payload_json FROM invalidation_events"
                ).fetchone()[0]
            )
        )
        self.assertEqual(
            payload["_classClosureScopes"]["11"],
            [11, 12],
        )

        report = drain_rebuild_queue(
            core,
            RootOnlyClosureBackend(),
            max_items=1,
            recover_running=False,
        )

        self.assertEqual(report.succeeded, 0)
        self.assertEqual(report.failed, 1)
        self.assertIn(
            (11, 12),
            {
                (int(ancestor), int(descendant))
                for ancestor, descendant in core.execute(
                    """
                    SELECT ancestor_class_id, descendant_class_id
                    FROM class_closure
                    """
                )
            },
        )
        self.assertEqual(
            requeue_rebuild_tasks(
                core,
                event_id=str(invalidation["eventId"]),
                downstream_kind="CLASS_CLOSURE",
                downstream_id=11,
            ),
            1,
        )
        repaired = drain_rebuild_queue(
            core,
            CoreMaterializerRebuildBackend(),
            max_items=1,
            recover_running=False,
        )
        self.assertEqual(repaired.succeeded, 1)
        self.assertNotIn(
            (11, 12),
            {
                (int(ancestor), int(descendant))
                for ancestor, descendant in core.execute(
                    """
                    SELECT ancestor_class_id, descendant_class_id
                    FROM class_closure
                    """
                )
            },
        )
        core.close()

    def test_identical_closure_rebuild_uses_source_revision_receipt(
        self,
    ) -> None:
        core = _core()
        _seed_integration_core(core)
        core.executemany(
            "INSERT INTO class_closure VALUES (?, ?, ?, ?)",
            [
                (11, 11, 0, "SELF"),
                (100, 11, 1, "CONFIRMED"),
            ],
        )
        core.execute(
            """
            INSERT INTO source_revisions VALUES (
                2, 'test', 'test://source/v2', 'source-sha-v2', 'test',
                'v2', '2026-07-28T00:20:00Z', 'FRESH'
            )
            """
        )
        core.execute(
            "UPDATE classes SET source_revision_id=2 WHERE class_id=11"
        )
        core.commit()
        plan = plan_invalidation(
            core,
            event_kind="CLASS",
            class_ids=[11],
        )
        result = apply_invalidation_plan(
            core,
            plan,
            created_at="2026-07-28T00:31:00Z",
        )

        report = drain_rebuild_queue(
            core,
            CoreMaterializerRebuildBackend(),
            max_items=1,
            recover_running=False,
        )

        self.assertEqual(report.succeeded, 1)
        payload = json.loads(
            str(
                core.execute(
                    """
                    SELECT payload_json
                    FROM invalidation_events
                    WHERE event_id=?
                    """,
                    (result["eventId"],),
                ).fetchone()[0]
            )
        )
        receipt = payload["_rebuildReceipts"]["CLASS_CLOSURE:11"]
        self.assertEqual(
            receipt["beforeDigest"],
            receipt["afterDigest"],
        )
        self.assertEqual(
            receipt["verification"]["basis"],
            "CLASS_REBUILT_AGAINST_SOURCE_REVISION",
        )
        self.assertEqual(
            len(receipt["verification"]["sourceRevisionProof"]),
            64,
        )
        self.assertIn(
            "class_closure:DELETE",
            receipt["verification"]["writeOperations"],
        )
        self.assertIn(
            "class_closure:INSERT",
            receipt["verification"]["writeOperations"],
        )
        core.close()

    def test_identical_closure_rejects_noop_and_update_only_backends(
        self,
    ) -> None:
        for backend in (NoopClosureBackend(), UpdateOnlyClosureBackend()):
            with self.subTest(backend=type(backend).__name__):
                core = _core()
                _seed_integration_core(core)
                core.executemany(
                    "INSERT INTO class_closure VALUES (?, ?, ?, ?)",
                    [
                        (11, 11, 0, "SELF"),
                        (100, 11, 1, "CONFIRMED"),
                    ],
                )
                core.commit()
                _queue(core, [("CLASS_CLOSURE", 11)])

                report = drain_rebuild_queue(
                    core,
                    backend,
                    max_items=1,
                    recover_running=False,
                )

                self.assertEqual(report.succeeded, 0)
                self.assertEqual(report.failed, 1)
                core.close()

    def test_backend_commit_is_suppressed_and_failure_rolls_back(
        self,
    ) -> None:
        core = _core()
        _seed_role(core, status="STALE")
        core.execute(
            """
            INSERT INTO domain_memberships VALUES (
                1, 'item_use', 'CLASS_ANCESTRY', 'HIGH', 'STALE',
                'class://1', 'v1', 1
            )
            """
        )
        core.commit()
        _queue(core, [("ROLE_ENTITY", 1), ("DOMAIN_ENTITY", 1)])

        report = drain_rebuild_queue(
            core,
            CommitThenFailBackend(),
            max_items=2,
            recover_running=False,
        )

        self.assertEqual(report.failed, 1)
        self.assertEqual(report.succeeded, 1)
        self.assertEqual(
            core.execute(
                "SELECT status FROM knowledge_roles"
            ).fetchone()[0],
            "STALE",
        )
        self.assertEqual(
            core.execute(
                """
                SELECT status
                FROM invalidation_queue
                WHERE downstream_kind='ROLE_ENTITY'
                """
            ).fetchone()[0],
            "FAILED",
        )
        self.assertEqual(
            core.execute(
                """
                SELECT status
                FROM invalidation_queue
                WHERE downstream_kind='DOMAIN_ENTITY'
                """
            ).fetchone()[0],
            "SUCCEEDED",
        )
        core.close()

    def test_commented_commit_sql_cannot_escape_worker_transaction(
        self,
    ) -> None:
        core = _core()
        _seed_role(core, status="STALE")
        _queue(core, [("ROLE_ENTITY", 1)])

        report = drain_rebuild_queue(
            core,
            CommentCommitThenFailBackend(),
            max_items=1,
            recover_running=False,
        )

        self.assertEqual(report.failed, 1)
        self.assertEqual(
            core.execute(
                "SELECT status FROM knowledge_roles"
            ).fetchone()[0],
            "STALE",
        )
        self.assertEqual(
            core.execute(
                "SELECT status FROM invalidation_queue"
            ).fetchone()[0],
            "FAILED",
        )
        core.close()

    def test_missing_real_operation_is_a_durable_blocked_gap(
        self,
    ) -> None:
        core = _core()
        core.execute(
            """
            INSERT INTO domain_memberships VALUES (
                1, 'item_use', 'CLASS_ANCESTRY', 'HIGH', 'STALE',
                'class://1', 'v1', 1
            )
            """
        )
        core.commit()
        _queue(core, [("DOMAIN_ENTITY", 1)])

        report = drain_rebuild_queue(
            core,
            RebuildBackend(),
            max_items=1,
            recover_running=False,
        )

        self.assertEqual(report.blocked_gap, 1)
        self.assertEqual(
            report.outcomes[0].gap_code,
            "BACKEND_NOT_CONFIGURED_DOMAIN_ENTITY",
        )
        self.assertEqual(
            core.execute(
                "SELECT status FROM invalidation_queue"
            ).fetchone()[0],
            BLOCKED_GAP,
        )
        payload = json.loads(
            core.execute(
                "SELECT payload_json FROM invalidation_events"
            ).fetchone()[0]
        )
        receipt = payload["_rebuildReceipts"]["DOMAIN_ENTITY:1"]
        self.assertEqual(receipt["status"], BLOCKED_GAP)
        self.assertEqual(
            receipt["gapCode"],
            "BACKEND_NOT_CONFIGURED_DOMAIN_ENTITY",
        )
        self.assertTrue(receipt["proof"].startswith("rebuild-proof://"))

        self.assertEqual(
            requeue_rebuild_tasks(
                core,
                statuses=(BLOCKED_GAP,),
                event_id="event-1",
            ),
            1,
        )
        recovered = drain_rebuild_queue(
            core,
            RepairDomainBackend(),
            max_items=1,
            recover_running=False,
        )
        self.assertEqual(recovered.succeeded, 1)
        self.assertEqual(
            core.execute(
                "SELECT status FROM invalidation_events"
            ).fetchone()[0],
            "SUCCEEDED",
        )
        core.close()

    def test_failed_projection_build_never_replaces_published_artifact(
        self,
    ) -> None:
        core = _core()
        projection_name = tuple(DOMAIN_PROJECTIONS)[0]
        core.execute(
            """
            INSERT INTO projection_runs VALUES (
                ?, 'v2', 'old-hash', 'v1',
                '2026-07-28T00:00:00Z', 0, 'STALE'
            )
            """,
            (projection_name,),
        )
        core.commit()
        _queue(core, [("PROJECTION", 1)])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_dir = root / "projections"
            output_dir.mkdir()
            published = output_dir / f"{projection_name}.sqlite"
            _write_projection(published, projection_name)
            before = published.read_bytes()

            report = drain_rebuild_queue(
                core,
                FailingProjectionBackend(
                    projection_dir=output_dir,
                ),
                max_items=1,
                recover_running=False,
            )

            self.assertEqual(report.failed, 1)
            self.assertEqual(published.read_bytes(), before)
            self.assertEqual(
                sorted(path.name for path in root.iterdir()),
                ["projections"],
            )
        self.assertEqual(
            core.execute(
                "SELECT validation_status FROM projection_runs"
            ).fetchone()[0],
            "STALE",
        )
        core.close()

    def test_projection_publish_failure_restores_already_replaced_files(
        self,
    ) -> None:
        core = _core()
        for projection_name in DOMAIN_PROJECTIONS:
            core.execute(
                """
                INSERT INTO projection_runs VALUES (
                    ?, 'v2', 'old-hash', 'v1',
                    '2026-07-28T00:00:00Z', 0, 'STALE'
                )
                """,
                (projection_name,),
            )
        core.commit()
        _queue(core, [("PROJECTION", 1)])

        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "projections"
            output_dir.mkdir()
            names = tuple(DOMAIN_PROJECTIONS)
            first = output_dir / f"{names[0]}.sqlite"
            _write_projection(first, names[0])
            before = first.read_bytes()
            (output_dir / f"{names[1]}.sqlite").mkdir()

            report = drain_rebuild_queue(
                core,
                CompleteProjectionBackend(
                    projection_dir=output_dir,
                ),
                max_items=1,
                recover_running=False,
            )

            self.assertEqual(report.failed, 1)
            self.assertEqual(first.read_bytes(), before)
            self.assertTrue(
                (output_dir / f"{names[1]}.sqlite").is_dir()
            )
        self.assertTrue(
            all(
                status == "STALE"
                for (status,) in core.execute(
                    "SELECT validation_status FROM projection_runs"
                )
            )
        )
        core.close()

    def test_metadata_only_projection_cannot_pass_verification(
        self,
    ) -> None:
        core = _core()
        projection_name = tuple(DOMAIN_PROJECTIONS)[0]
        core.execute(
            """
            INSERT INTO projection_runs VALUES (
                ?, 'v2', 'old-hash', 'test-ontology/v1',
                '2026-07-28T00:00:00Z', 0, 'STALE'
            )
            """,
            (projection_name,),
        )
        core.commit()
        _queue(core, [("PROJECTION", 1)])

        with tempfile.TemporaryDirectory() as temporary:
            report = drain_rebuild_queue(
                core,
                MetadataOnlyProjectionBackend(
                    projection_dir=Path(temporary),
                ),
                max_items=1,
                recover_running=False,
            )

        self.assertEqual(report.succeeded, 0)
        self.assertIn(
            report.outcomes[0].status,
            {"FAILED", BLOCKED_GAP},
        )
        core.close()

    def test_projection_content_must_match_current_core_semantics(
        self,
    ) -> None:
        core = _core()
        projection_name = tuple(DOMAIN_PROJECTIONS)[0]
        core.execute(
            """
            INSERT INTO projection_runs VALUES (
                ?, 'v2', 'old-hash', 'test-ontology/v1',
                '2026-07-28T00:00:00Z', 0, 'STALE'
            )
            """,
            (projection_name,),
        )
        core.commit()
        _queue(core, [("PROJECTION", 1)])

        with tempfile.TemporaryDirectory() as temporary:
            report = drain_rebuild_queue(
                core,
                WrongContentProjectionBackend(
                    projection_dir=Path(temporary),
                ),
                max_items=1,
                recover_running=False,
            )

        self.assertEqual(report.succeeded, 0)
        self.assertEqual(report.blocked_gap, 1)
        self.assertEqual(
            report.outcomes[0].gap_code,
            "PROJECTION_CORE_CONTENT_MISMATCH",
        )
        core.close()

    def test_backend_cannot_bypass_projection_staging_via_published_path(
        self,
    ) -> None:
        core = _core()
        projection_name = tuple(DOMAIN_PROJECTIONS)[0]
        core.execute(
            """
            INSERT INTO projection_runs VALUES (
                ?, 'v2', 'old-hash', 'test-ontology/v1',
                '2026-07-28T00:00:00Z', 0, 'STALE'
            )
            """,
            (projection_name,),
        )
        core.commit()
        _queue(core, [("PROJECTION", 1)])

        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "projections"
            output_dir.mkdir()
            published = output_dir / f"{projection_name}.sqlite"
            _write_projection(published, projection_name)
            before = published.read_bytes()

            report = drain_rebuild_queue(
                core,
                PublishedPathEscapeBackend(
                    projection_dir=output_dir,
                ),
                max_items=1,
                recover_running=False,
            )

            self.assertEqual(report.failed, 1)
            self.assertEqual(published.read_bytes(), before)
        core.close()

    def test_recovered_external_work_without_marker_cannot_skip_backend(
        self,
    ) -> None:
        core = _core()
        cache = _cache()
        _queue(
            core,
            [("QUERY_SNAPSHOT", 1)],
            queue_status="RUNNING",
            event_status="RUNNING",
        )

        report = drain_rebuild_queue(
            core,
            RebuildBackend(cache_connection=cache),
            max_items=1,
        )

        self.assertEqual(report.recovered_running, 1)
        self.assertEqual(report.succeeded, 0)
        self.assertEqual(report.blocked_gap, 1)
        self.assertTrue(report.outcomes[0].task.recovered)
        self.assertFalse(report.outcomes[0].cache_hit)
        self.assertEqual(
            report.outcomes[0].gap_code,
            "BACKEND_NOT_CONFIGURED_QUERY_SNAPSHOT",
        )
        self.assertEqual(
            core.execute(
                "SELECT status FROM invalidation_queue"
            ).fetchone()[0],
            BLOCKED_GAP,
        )
        core.close()
        cache.close()

    def test_fresh_empty_cache_noop_cannot_create_its_own_success_proof(
        self,
    ) -> None:
        core = _core()
        cache = _cache()
        _queue(core, [("QUERY_SNAPSHOT", 1)])

        report = drain_rebuild_queue(
            core,
            NoopQueryBackend(cache_connection=cache),
            max_items=1,
            recover_running=False,
        )

        self.assertEqual(report.failed, 1)
        self.assertEqual(report.succeeded, 0)
        self.assertEqual(
            core.execute(
                "SELECT status FROM invalidation_queue"
            ).fetchone()[0],
            "FAILED",
        )
        marker_count = cache.execute(
            """
            SELECT COUNT(*)
            FROM metadata
            WHERE key LIKE '_rebuild_worker_marker:%'
            """
        ).fetchone()[0]
        self.assertEqual(marker_count, 0)
        core.close()
        cache.close()

    def test_backend_cannot_bypass_guarded_scope_via_raw_cache_attr(
        self,
    ) -> None:
        core = _core()
        cache = _cache()
        _seed_cache(cache)
        _queue(core, [("QUERY_SNAPSHOT", 1)])

        report = drain_rebuild_queue(
            core,
            RawCacheEscapeBackend(cache_connection=cache),
            max_items=1,
            recover_running=False,
        )

        self.assertEqual(report.failed, 1)
        self.assertEqual(
            cache.execute(
                "SELECT COUNT(*) FROM query_snapshots"
            ).fetchone()[0],
            1,
        )
        core.close()
        cache.close()

    def test_external_commit_marker_recovers_crash_without_duplicate_work(
        self,
    ) -> None:
        core = _core()
        cache = _cache()
        _seed_cache(cache)
        _queue(core, [("QUERY_SNAPSHOT", 1)])
        backend = CountingQueryBackend(cache_connection=cache)

        def crash_after_external_commit(
            core_connection: sqlite3.Connection,
            cache_connection: sqlite3.Connection | None,
        ) -> None:
            rebuild_worker_module._end_authorizers(
                core_connection,
                cache_connection,
            )
            assert cache_connection is not None
            cache_connection.commit()
            core_connection.rollback()
            raise SimulatedProcessCrash

        with mock.patch.object(
            rebuild_worker_module,
            "_commit_backend_transactions",
            side_effect=crash_after_external_commit,
        ):
            with self.assertRaises(SimulatedProcessCrash):
                drain_rebuild_queue(
                    core,
                    backend,
                    max_items=1,
                    recover_running=False,
                )

        self.assertEqual(backend.calls, 1)
        self.assertEqual(
            core.execute(
                "SELECT status FROM invalidation_queue"
            ).fetchone()[0],
            "RUNNING",
        )
        self.assertEqual(
            cache.execute(
                "SELECT COUNT(*) FROM query_snapshots"
            ).fetchone()[0],
            0,
        )

        recovered = drain_rebuild_queue(
            core,
            backend,
            max_items=1,
        )

        self.assertEqual(recovered.recovered_running, 1)
        self.assertEqual(recovered.succeeded, 1)
        self.assertTrue(recovered.outcomes[0].cache_hit)
        self.assertEqual(backend.calls, 1)
        core.close()
        cache.close()

    def test_recovered_core_task_must_reexecute_in_guarded_transaction(
        self,
    ) -> None:
        core = _core()
        _seed_role(core, status="CONFIRMED")
        _queue(
            core,
            [("ROLE_ENTITY", 1)],
            queue_status="RUNNING",
            event_status="RUNNING",
        )

        report = drain_rebuild_queue(
            core,
            NoopRoleBackend(),
            max_items=1,
        )

        self.assertEqual(report.recovered_running, 1)
        self.assertEqual(report.failed, 1)
        self.assertEqual(
            core.execute(
                "SELECT status FROM invalidation_queue"
            ).fetchone()[0],
            "FAILED",
        )
        core.close()

    def test_bounded_failure_retry_does_not_reprocess_success(
        self,
    ) -> None:
        core = _core()
        _seed_role(core, status="STALE")
        _queue(core, [("ROLE_ENTITY", 1)])

        failed = drain_rebuild_queue(
            core,
            NoopRoleBackend(),
            max_items=1,
            recover_running=False,
        )
        self.assertEqual(failed.failed, 1)
        self.assertEqual(
            requeue_rebuild_tasks(
                core,
                statuses=("FAILED",),
                event_id="event-1",
            ),
            1,
        )

        repaired = drain_rebuild_queue(
            core,
            RepairRoleBackend(),
            max_items=1,
            recover_running=False,
        )
        repeated = drain_rebuild_queue(
            core,
            RepairRoleBackend(),
            max_items=1,
            recover_running=False,
        )

        self.assertEqual(repaired.succeeded, 1)
        self.assertEqual(repeated.attempted, 0)
        self.assertEqual(
            core.execute(
                "SELECT status FROM invalidation_events"
            ).fetchone()[0],
            "SUCCEEDED",
        )
        core.close()

    def test_max_items_bounds_each_drain(self) -> None:
        core = _core()
        core.execute(
            """
            INSERT INTO entities(
                entity_id, canonical_uri, entity_kind, status, confidence
            ) VALUES (
                2, '/Game/Test/Second.Second', 'BLUEPRINT_ASSET',
                'CONFIRMED', 'HIGH'
            )
            """
        )
        core.commit()
        _seed_role(core, entity_id=1, status="STALE")
        _seed_role(core, entity_id=2, status="STALE")
        _queue(core, [("ROLE_ENTITY", 1), ("ROLE_ENTITY", 2)])

        first = drain_rebuild_queue(
            core,
            RepairRoleBackend(),
            max_items=1,
            recover_running=False,
        )
        second = drain_rebuild_queue(
            core,
            RepairRoleBackend(),
            max_items=1,
            recover_running=False,
        )

        self.assertEqual(first.attempted, 1)
        self.assertEqual(first.remaining_pending, 1)
        self.assertEqual(second.attempted, 1)
        self.assertEqual(second.remaining_pending, 0)
        self.assertEqual(
            core.execute(
                """
                SELECT COUNT(*)
                FROM invalidation_queue
                WHERE status='SUCCEEDED'
                """
            ).fetchone()[0],
            2,
        )
        core.close()

    def test_zero_bound_does_not_recover_or_mutate_running_work(
        self,
    ) -> None:
        core = _core()
        _seed_role(core, status="STALE")
        _queue(
            core,
            [("ROLE_ENTITY", 1)],
            queue_status="RUNNING",
            event_status="RUNNING",
        )

        report = drain_rebuild_queue(
            core,
            RepairRoleBackend(),
            max_items=0,
        )

        self.assertEqual(report.recovered_running, 0)
        self.assertEqual(report.attempted, 0)
        self.assertEqual(report.remaining_running, 1)
        self.assertEqual(
            core.execute(
                "SELECT status FROM invalidation_queue"
            ).fetchone()[0],
            "RUNNING",
        )
        core.close()


if __name__ == "__main__":
    unittest.main()
