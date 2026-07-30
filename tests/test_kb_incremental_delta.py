from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import update_ark_kb_vnext as update_runner  # noqa: E402

from blueprint_translator.kb_vnext.blueprint_ingest import (  # noqa: E402
    BlueprintIngestResult,
)
from blueprint_translator.kb_vnext import (  # noqa: E402
    incremental_delta as incremental_delta_module,
)
from blueprint_translator.kb_vnext.incremental_delta import (  # noqa: E402
    BLOCKED_GAP,
    FOUNDATION_VERIFIED,
    TEST_ONLY,
    AddOnlyBlueprintDelta,
    AddOnlyDeltaBlockedGap,
    build_add_only_blueprint_delta,
    build_add_only_delta_receipt,
    logical_database_state,
    validate_add_only_delta_receipt,
)
from blueprint_translator.kb_vnext.invalidation import (  # noqa: E402
    InvalidationBlockedGap,
    InvalidationPlan,
    plan_additive_asset_invalidation,
)
from blueprint_translator.kb_vnext.projections import (  # noqa: E402
    DOMAIN_PROJECTIONS,
)
from blueprint_translator.kb_vnext.source_manifest import (  # noqa: E402
    SourceChange,
    SourceDiff,
    SourceRevision,
    source_id,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    FULL_CORE_SCHEMA_SQL,
)


CORE_SOURCE_FINGERPRINT = "c" * 64
DIFF_SOURCE_FINGERPRINT = "d" * 64
ENTITY_URI = "/Game/Test/Added.Added"


def _revision(
    *,
    fingerprint: str = DIFF_SOURCE_FINGERPRINT,
    source_uri: str = "capture://Added",
) -> SourceRevision:
    return SourceRevision(
        source_id=source_id("BLUEPRINT_EVIDENCE", source_uri),
        source_kind="BLUEPRINT_EVIDENCE",
        source_uri=source_uri,
        fingerprint=fingerprint,
        size_bytes=128,
        entity_uri=ENTITY_URI,
        revision_label="test-only-revision",
    )


def _add_only_diff(revision: SourceRevision | None = None) -> SourceDiff:
    current = revision or _revision()
    return SourceDiff(
        added=(
            SourceChange(
                change_kind="ADDED",
                source_id=current.source_id,
                previous=None,
                current=current,
            ),
        )
    )


def _core_pair(tmp_path: Path) -> tuple[sqlite3.Connection, sqlite3.Connection]:
    base_path = tmp_path / "base.sqlite"
    staged_path = tmp_path / "staged.sqlite"
    base = sqlite3.connect(base_path)
    base.execute("PRAGMA foreign_keys=ON")
    base.executescript(FULL_CORE_SCHEMA_SQL)
    base.execute(
        """
        INSERT INTO source_revisions VALUES (
            1, 'discovery', 'discovery://test-only',
            'test-only-discovery-sha', 'test-only-producer',
            'test-only-schema', '2026-07-29T00:00:00Z', 'FRESH'
        )
        """
    )
    base.execute(
        """
        INSERT INTO entities(
            entity_id, canonical_uri, entity_kind, status, confidence
        ) VALUES (
            1, ?, 'BLUEPRINT_ASSET', 'CONFIRMED', 'HIGH'
        )
        """,
        (ENTITY_URI,),
    )
    base.commit()
    staged = sqlite3.connect(staged_path)
    base.backup(staged)
    staged.execute("PRAGMA foreign_keys=ON")
    return base, staged


def _materialize_test_only_rows(
    staged: sqlite3.Connection,
    *,
    include_fact: bool = True,
) -> BlueprintIngestResult:
    staged.execute(
        """
        INSERT INTO source_revisions VALUES (
            2, 'blueprint_evidence', 'bp://test-asset@test-revision',
            ?, 'test-only-parser', 'test-only-evidence-schema',
            '2026-07-29T01:00:00Z', 'FRESH'
        )
        """,
        (CORE_SOURCE_FINGERPRINT,),
    )
    fact_ids: frozenset[int] = frozenset()
    if include_fact:
        staged.execute(
            """
            INSERT INTO facts(
                fact_id, subject_entity_id, fact_type, fact_name,
                scope_kind, declared_on_entity_id, value_kind,
                value_integer, status, confidence, ontology_version,
                current, canonical_fact_key
            ) VALUES (
                101, 1, 'DECLARED_DEFAULT', 'Rate', 'DECLARED', 1,
                'INTEGER', 7, 'CONFIRMED', 'HIGH',
                'test-only-ontology', 1, 'fact://test-only/rate'
            )
            """
        )
        staged.execute(
            """
            INSERT INTO fact_evidence VALUES (
                101, 2, 'bp://test-asset@test-revision/default/Rate',
                'DEFAULT_VALUE_ACTUAL'
            )
            """
        )
        fact_ids = frozenset({101})
    staged.commit()
    return BlueprintIngestResult(
        counts={
            "freshAssets": 1,
            "sourceRevisions": 1,
            "declaredFacts": len(fact_ids),
            "factEvidence": len(fact_ids),
        },
        covered_properties=(
            frozenset({(ENTITY_URI, "Rate")})
            if include_fact
            else frozenset()
        ),
        freshness_gap_assets=frozenset(),
        untrusted_assets=frozenset(),
        fact_ids=fact_ids,
        entity_ids=frozenset({1}),
    )


def _artifact_binding(
    tmp_path: Path,
    *,
    source_uri: str = "capture://Added",
    revision_label: str = "test-revision",
    asset_name: str = "Added",
    asset_id: str = "test-asset",
    entity_uri: str = ENTITY_URI,
    manifest_bytes: bytes | None = None,
) -> tuple[Path, SourceRevision, dict[str, object]]:
    root = tmp_path / "authorized-artifacts"
    path = root / "evidence" / asset_name / "evidence.sqlite"
    path.parent.mkdir(parents=True)
    evidence = sqlite3.connect(path)
    evidence.execute(
        """
        CREATE TABLE asset_revisions(
            revision_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            asset_name TEXT NOT NULL,
            object_path TEXT NOT NULL,
            source_fingerprint TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            generated_at TEXT NOT NULL
        )
        """
    )
    evidence.execute(
        """
        INSERT INTO asset_revisions VALUES (
            ?, ?, ?, ?, ?,
            'test-only-parser', 'test-only-evidence-schema',
            '2026-07-29T01:00:00Z'
        )
        """,
        (
            revision_label,
            asset_id,
            asset_name,
            entity_uri,
            CORE_SOURCE_FINGERPRINT,
        ),
    )
    evidence.commit()
    evidence.close()
    artifact_bytes = path.read_bytes()
    if manifest_bytes is not None:
        (path.parent / "manifest.json").write_bytes(manifest_bytes)
    aggregate = hashlib.sha256()
    aggregate.update(b"evidence.sqlite\0")
    aggregate.update(artifact_bytes)
    aggregate.update(b"\n")
    if manifest_bytes is not None:
        aggregate.update(b"manifest.json\0")
        aggregate.update(manifest_bytes)
        aggregate.update(b"\n")
    revision = SourceRevision(
        source_id=source_id("BLUEPRINT_EVIDENCE", source_uri),
        source_kind="BLUEPRINT_EVIDENCE",
        source_uri=source_uri,
        fingerprint=aggregate.hexdigest(),
        size_bytes=len(artifact_bytes),
        entity_uri=entity_uri,
        revision_label=revision_label,
    )
    binding = {
        "sourceId": revision.source_id,
        "sourceFingerprint": revision.fingerprint,
        "artifactUri": (
            f"artifact://evidence/{asset_name}/evidence.sqlite"
        ),
        "artifactSha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "artifactBytes": len(artifact_bytes),
        "trustContext": TEST_ONLY,
    }
    return root, revision, binding


def _delta_fixture(
    tmp_path: Path,
) -> tuple[
    sqlite3.Connection,
    sqlite3.Connection,
    SourceDiff,
    BlueprintIngestResult,
    Path,
    dict[str, object],
]:
    base, staged = _core_pair(tmp_path)
    result = _materialize_test_only_rows(staged)
    artifact_root, revision, binding = _artifact_binding(tmp_path)
    diff = _add_only_diff(revision)
    return base, staged, diff, result, artifact_root, binding


def _two_blueprint_fixture(
    tmp_path: Path,
    *,
    second_has_evidence: bool,
) -> tuple[
    sqlite3.Connection,
    sqlite3.Connection,
    SourceDiff,
    BlueprintIngestResult,
    Path,
    tuple[dict[str, object], ...],
]:
    second_entity_uri = "/Game/Test/AddedTwo.AddedTwo"
    base, staged = _core_pair(tmp_path)
    for connection in (base, staged):
        connection.execute(
            """
            INSERT INTO entities(
                entity_id, canonical_uri, entity_kind, status, confidence
            ) VALUES (
                2, ?, 'BLUEPRINT_ASSET', 'CONFIRMED', 'HIGH'
            )
            """,
            (second_entity_uri,),
        )
        connection.commit()
    artifact_root, first_revision, first_binding = _artifact_binding(
        tmp_path
    )
    _, second_revision, second_binding = _artifact_binding(
        tmp_path,
        source_uri="capture://AddedTwo",
        revision_label="test-revision-two",
        asset_name="AddedTwo",
        asset_id="test-asset-two",
        entity_uri=second_entity_uri,
    )
    staged.executemany(
        """
        INSERT INTO source_revisions VALUES (
            ?, 'blueprint_evidence', ?, ?,
            'test-only-parser', 'test-only-evidence-schema',
            '2026-07-29T01:00:00Z', 'FRESH'
        )
        """,
        (
            (
                2,
                "bp://test-asset@test-revision",
                CORE_SOURCE_FINGERPRINT,
            ),
            (
                3,
                "bp://test-asset-two@test-revision-two",
                CORE_SOURCE_FINGERPRINT,
            ),
        ),
    )
    fact_rows = [
        (
            101,
            1,
            "Rate",
            7,
            "fact://test-only/rate",
        )
    ]
    if second_has_evidence:
        fact_rows.append(
            (
                102,
                2,
                "Radius",
                9,
                "fact://test-only/radius",
            )
        )
    staged.executemany(
        """
        INSERT INTO facts(
            fact_id, subject_entity_id, fact_type, fact_name,
            scope_kind, declared_on_entity_id, value_kind,
            value_integer, status, confidence, ontology_version,
            current, canonical_fact_key
        ) VALUES (
            ?, ?, 'DECLARED_DEFAULT', ?, 'DECLARED', ?,
            'INTEGER', ?, 'CONFIRMED', 'HIGH',
            'test-only-ontology', 1, ?
        )
        """,
        [
            (
                fact_id,
                entity_id,
                fact_name,
                entity_id,
                value,
                canonical_key,
            )
            for fact_id, entity_id, fact_name, value, canonical_key in fact_rows
        ],
    )
    evidence_rows = [
        (
            101,
            2,
            "bp://test-asset@test-revision/default/Rate",
            "DEFAULT_VALUE_ACTUAL",
        )
    ]
    if second_has_evidence:
        evidence_rows.append(
            (
                102,
                3,
                "bp://test-asset-two@test-revision-two/default/Radius",
                "DEFAULT_VALUE_ACTUAL",
            )
        )
    staged.executemany(
        "INSERT INTO fact_evidence VALUES (?, ?, ?, ?)",
        evidence_rows,
    )
    staged.commit()
    fact_ids = frozenset(row[0] for row in fact_rows)
    result = BlueprintIngestResult(
        counts={
            "freshAssets": 2,
            "sourceRevisions": 2,
            "declaredFacts": len(fact_ids),
            "factEvidence": len(fact_ids),
        },
        covered_properties=frozenset(
            (
                ENTITY_URI if entity_id == 1 else second_entity_uri,
                fact_name,
            )
            for _fact_id, entity_id, fact_name, _value, _key in fact_rows
        ),
        freshness_gap_assets=frozenset(),
        untrusted_assets=frozenset(),
        fact_ids=fact_ids,
        entity_ids=frozenset({1, 2}),
    )
    diff = SourceDiff(
        added=tuple(
            SourceChange(
                change_kind="ADDED",
                source_id=revision.source_id,
                previous=None,
                current=revision,
            )
            for revision in (first_revision, second_revision)
        )
    )
    return (
        base,
        staged,
        diff,
        result,
        artifact_root,
        (first_binding, second_binding),
    )


def _materialize_complete_dependency_scope(
    staged: sqlite3.Connection,
    *,
    source_revision_ids: tuple[int, ...],
    entity_ids: tuple[int, ...],
) -> None:
    placeholders = ",".join("?" for _ in source_revision_ids)
    fact_ids = tuple(
        int(row[0])
        for row in staged.execute(
            f"""
            SELECT DISTINCT fact_id
            FROM fact_evidence
            WHERE source_revision_id IN ({placeholders})
            ORDER BY fact_id
            """,
            source_revision_ids,
        )
    )
    update_runner.materialize_additive_asset_dependency_scope(
        staged,
        source_revision_ids=source_revision_ids,
        entity_ids=entity_ids,
        fact_ids=fact_ids,
        actual_write_tables=(
            "fact_evidence",
            "facts",
            "source_revisions",
        ),
    )


_TOUCHED_TABLE = {
    "FACT": "facts",
    "EFFECTIVE_ENTITY": "effective_facts",
    "ROLE_ENTITY": "knowledge_roles",
    "DOMAIN_ENTITY": "domain_memberships",
    "PROJECTION": "projection_runs",
    "QUERY_SNAPSHOT": "query_snapshots",
}

_QUERY_CACHE_TABLES = (
    "answer_plans",
    "context_packs",
    "materialized_neighborhoods",
    "query_snapshots",
)
_QUERY_CACHE_DELETE_OPERATIONS = tuple(
    f"{table}:DELETE" for table in _QUERY_CACHE_TABLES
)


def _content_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _reproof_backend_receipt(
    receipt: dict[str, object],
) -> dict[str, object]:
    body = dict(receipt)
    body.pop("proof", None)
    receipt["proof"] = "rebuild-proof://" + _content_sha256(body)
    return receipt


def _explicit_whole_cache_backend_receipt(
    receipt: dict[str, object],
) -> dict[str, object]:
    result = copy.deepcopy(receipt)
    result["afterDigest"] = result["beforeDigest"]
    result["touchedTables"] = list(_QUERY_CACHE_TABLES)
    result["verification"] = {
        "basis": "EXPLICIT_WHOLE_CACHE_INVALIDATION",
        "coreWriteChanges": 0,
        "writeOperations": list(_QUERY_CACHE_DELETE_OPERATIONS),
        "rowScope": {
            "mode": "EXPLICIT_WHOLE_CACHE_BATCH",
            "eventId": result["eventId"],
            "targetId": result["downstreamId"],
            "tables": list(_QUERY_CACHE_TABLES),
        },
    }
    return _reproof_backend_receipt(result)


def _backend_terminal_receipt(
    *,
    kind: str,
    target_id: int,
    reason: str,
    status: str = "SUCCEEDED",
    gap_code: str = "",
    event_id: str = "test-only-additive-event",
) -> dict[str, object]:
    touched = [] if status == BLOCKED_GAP else [_TOUCHED_TABLE[kind]]
    if kind == "QUERY_SNAPSHOT":
        row_scope: dict[str, object] = {
            "mode": "EXPLICIT_WHOLE_CACHE_BATCH",
            "eventId": event_id,
            "targetId": target_id,
            "tables": sorted(
                {
                    "query_snapshots",
                    "context_packs",
                    "answer_plans",
                    "materialized_neighborhoods",
                }
            ),
        }
    elif kind == "PROJECTION":
        row_scope = {
            "mode": "EXPLICIT_PROJECTION_BATCH",
            "eventId": event_id,
            "targetId": target_id,
            "projectionNames": list(DOMAIN_PROJECTIONS),
        }
    else:
        row_scope = {
            "mode": "TASK_TARGET_ID",
            "targetId": target_id,
        }
    before = hashlib.sha256(f"{kind}:{target_id}:before".encode()).hexdigest()
    after = (
        before
        if status == BLOCKED_GAP
        else hashlib.sha256(f"{kind}:{target_id}:after".encode()).hexdigest()
    )
    body: dict[str, object] = {
        "schema": "ark-kb-rebuild-receipt/v1",
        "eventId": event_id,
        "downstreamKind": kind,
        "downstreamId": target_id,
        "dependencyReason": reason,
        "status": status,
        "beforeDigest": before,
        "afterDigest": after,
        "complete": status == "SUCCEEDED",
        "gapCode": gap_code,
        "detail": "",
        "touchedTables": touched,
        "recovered": False,
        "cacheHit": False,
        "projectionBatch": {},
        "verification": (
            {
                "basis": "TARGET_STATE_CHANGED",
                "coreWriteChanges": 1,
                "writeOperations": [f"{touched[0]}:INSERT"],
                "rowScope": row_scope,
            }
            if touched
            else {}
        ),
    }
    return {
        **body,
        "proof": "rebuild-proof://" + _content_sha256(body),
    }


def _successful_backend_receipts(
    plan: InvalidationPlan,
) -> list[dict[str, object]]:
    return [
        _backend_terminal_receipt(
            kind=kind,
            target_id=target_id,
            reason=plan.reasons[kind],
        )
        for kind, values in sorted(plan.downstream.items())
        for target_id in values
    ]


def _persist_backend_event(
    connection: sqlite3.Connection,
    plan: InvalidationPlan,
    receipts: list[dict[str, object]],
    *,
    event_id: str = "test-only-additive-event",
) -> str:
    by_task = {
        (
            str(receipt["downstreamKind"]),
            int(receipt["downstreamId"]),
        ): receipt
        for receipt in receipts
    }
    payload = {
        **{
            kind: list(values)
            for kind, values in sorted(plan.downstream.items())
        },
        "_rebuildReceipts": {
            f"{kind}:{target_id}": receipt
            for (kind, target_id), receipt in sorted(by_task.items())
        },
    }
    queue_rows = [
        (
            event_id,
            kind,
            target_id,
            plan.reasons[kind],
            str(by_task.get((kind, target_id), {}).get("status")
                or "PENDING_REBUILD"),
        )
        for kind, values in sorted(plan.downstream.items())
        for target_id in values
    ]
    statuses = {str(row[4]) for row in queue_rows}
    if statuses == {"SUCCEEDED"}:
        event_status = "SUCCEEDED"
    elif BLOCKED_GAP in statuses:
        event_status = BLOCKED_GAP
    else:
        event_status = "PENDING_REBUILD"
    connection.execute(
        """
        INSERT INTO invalidation_events(
            event_id, event_kind, upstream_revision_id,
            payload_json, created_at, status
        ) VALUES (?, 'ASSET', NULL, ?, ?, ?)
        """,
        (
            event_id,
            json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            "2026-07-29T02:00:00Z",
            event_status,
        ),
    )
    connection.executemany(
        """
        INSERT INTO invalidation_queue(
            event_id, downstream_kind, downstream_id,
            dependency_reason, status
        ) VALUES (?, ?, ?, ?, ?)
        """,
        queue_rows,
    )
    connection.commit()
    return event_id


def _receipt_sha256(receipt: dict[str, object]) -> str:
    return str(receipt["proof"]).removeprefix("delta-proof://")


def _ready_receipt_fixture(
    tmp_path: Path,
    *,
    explicit_query_invalidation: bool = False,
    blocked_kinds: frozenset[str] = frozenset(),
) -> tuple[
    sqlite3.Connection,
    sqlite3.Connection,
    AddOnlyBlueprintDelta,
    InvalidationPlan,
    str,
]:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    delta = build_add_only_blueprint_delta(
        base,
        staged,
        source_diff=diff,
        ingest_result=result,
        artifact_root=artifact_root,
        artifact_bindings=(binding,),
        trust_context=TEST_ONLY,
    )
    _materialize_complete_dependency_scope(
        staged,
        source_revision_ids=delta.source_revision_ids,
        entity_ids=delta.entity_ids,
    )
    plan = plan_additive_asset_invalidation(
        staged,
        fact_ids=delta.fact_ids,
        entity_ids=delta.entity_ids,
        source_revision_ids=delta.source_revision_ids,
        actual_write_tables=delta.changed_tables,
    )
    terminal = _successful_backend_receipts(plan)
    if explicit_query_invalidation:
        terminal = [
            (
                _explicit_whole_cache_backend_receipt(receipt)
                if receipt["downstreamKind"] == "QUERY_SNAPSHOT"
                else receipt
            )
            for receipt in terminal
        ]
    if blocked_kinds:
        terminal = [
            (
                _backend_terminal_receipt(
                    kind=str(receipt["downstreamKind"]),
                    target_id=int(receipt["downstreamId"]),
                    reason=str(receipt["dependencyReason"]),
                    status=BLOCKED_GAP,
                    gap_code=(
                        "BACKEND_NOT_CONFIGURED_"
                        + str(receipt["downstreamKind"])
                    ),
                )
                if receipt["downstreamKind"] in blocked_kinds
                else receipt
            )
            for receipt in terminal
        ]
    event_id = _persist_backend_event(
        staged,
        plan,
        terminal,
    )
    return base, staged, delta, plan, event_id


def test_builds_artifact_bound_delta_from_actual_durable_writes(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )

        assert delta.changed_tables == (
            "fact_evidence",
            "facts",
            "source_revisions",
        )
        assert delta.entity_ids == (1,)
        assert delta.fact_ids == (101,)
        assert delta.source_revision_ids == (2,)
        assert delta.source_diff_sha256 == hashlib.sha256(
            delta.source_diff_json
        ).hexdigest()
        assert delta.before_database_sha256 != delta.after_database_sha256
        assert (
            logical_database_state(base).database_sha256
            == delta.before_database_sha256
        )
        assert (
            logical_database_state(staged).database_sha256
            == delta.after_database_sha256
        )
        assert delta.artifacts[0]["artifactSha256"] == binding[
            "artifactSha256"
        ]
    finally:
        staged.close()
        base.close()


def test_two_blueprint_batch_is_rejected_even_when_each_has_evidence(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, bindings = (
        _two_blueprint_fixture(
            tmp_path,
            second_has_evidence=True,
        )
    )
    try:
        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_blueprint_delta(
                base,
                staged,
                source_diff=diff,
                ingest_result=result,
                artifact_root=artifact_root,
                artifact_bindings=bindings,
                trust_context=TEST_ONLY,
            )

        assert caught.value.gap_code == (
            "SOURCE_DIFF_REQUIRES_SINGLE_BLUEPRINT"
        )
    finally:
        staged.close()
        base.close()


def test_second_blueprint_without_fact_evidence_cannot_be_masked(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, bindings = (
        _two_blueprint_fixture(
            tmp_path,
            second_has_evidence=False,
        )
    )
    try:
        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_blueprint_delta(
                base,
                staged,
                source_diff=diff,
                ingest_result=result,
                artifact_root=artifact_root,
                artifact_bindings=bindings,
                trust_context=TEST_ONLY,
            )

        assert caught.value.gap_code == (
            "SOURCE_DIFF_REQUIRES_SINGLE_BLUEPRINT"
        )
    finally:
        staged.close()
        base.close()


def test_add_only_asset_plan_includes_complete_derived_dependency_scope(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        _materialize_complete_dependency_scope(
            staged,
            source_revision_ids=delta.source_revision_ids,
            entity_ids=delta.entity_ids,
        )
        plan = plan_additive_asset_invalidation(
            staged,
            fact_ids=delta.fact_ids,
            entity_ids=delta.entity_ids,
            source_revision_ids=delta.source_revision_ids,
            actual_write_tables=delta.changed_tables,
        )

        assert plan.event_kind == "ASSET"
        assert plan.downstream["EFFECTIVE_ENTITY"] == (1,)
        assert plan.downstream["FACT"] == (101,)
        assert plan.downstream["ROLE_ENTITY"] == (1,)
        assert plan.downstream["DOMAIN_ENTITY"] == (1,)
        assert plan.downstream["PROJECTION"] == tuple(
            range(1, len(DOMAIN_PROJECTIONS) + 1)
        )
        assert plan.downstream["QUERY_SNAPSHOT"] == (2,)
    finally:
        staged.close()
        base.close()


def test_production_materializer_builds_exact_additive_dependency_scope(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    staged_path = Path(
        str(staged.execute("PRAGMA database_list").fetchone()[2])
    )
    observer = sqlite3.connect(staged_path)
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        plan = update_runner.materialize_additive_asset_dependency_scope(
            staged,
            source_revision_ids=delta.source_revision_ids,
            entity_ids=delta.entity_ids,
            fact_ids=delta.fact_ids,
            actual_write_tables=delta.changed_tables,
        )

        assert plan.downstream == {
            "DOMAIN_ENTITY": (1,),
            "EFFECTIVE_ENTITY": (1,),
            "FACT": (101,),
            "PROJECTION": tuple(
                range(1, len(DOMAIN_PROJECTIONS) + 1)
            ),
            "QUERY_SNAPSHOT": (2,),
            "ROLE_ENTITY": (1,),
        }
        assert plan.affected_count == 11
        assert "EDGE_ENTITY" not in plan.downstream
        assert staged.in_transaction is True
        assert observer.execute(
            """
            SELECT COUNT(*) FROM invalidation_dependencies
            WHERE upstream_revision_id=2
            """
        ).fetchone()[0] == 0

        rows = staged.execute(
            """
            SELECT upstream_revision_id, downstream_kind,
                   downstream_id, dependency_reason
            FROM invalidation_dependencies
            WHERE upstream_revision_id=2
            ORDER BY downstream_kind, downstream_id
            """
        ).fetchall()
        assert rows == [
            (2, "DOMAIN_ENTITY", 1, "ADDITIVE_DOMAIN_INPUT"),
            *[
                (
                    2,
                    "PROJECTION",
                    projection_id,
                    "ADDITIVE_FACT_PROJECTION",
                )
                for projection_id in range(
                    1, len(DOMAIN_PROJECTIONS) + 1
                )
            ],
            (2, "QUERY_SNAPSHOT", 2, "ADDITIVE_QUERY_CACHE"),
            (2, "ROLE_ENTITY", 1, "ADDITIVE_ROLE_INPUT"),
        ]
        staged.rollback()
        assert staged.execute(
            """
            SELECT COUNT(*) FROM invalidation_dependencies
            WHERE upstream_revision_id=2
            """
        ).fetchone()[0] == 0
    finally:
        observer.close()
        staged.close()
        base.close()


def test_production_materializer_preserves_other_revision_dependencies(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    for connection in (base, staged):
        connection.execute(
            """
            INSERT INTO invalidation_dependencies VALUES (
                1, 'ROLE_ENTITY', 1, 'EXISTING_OTHER_REVISION'
            )
            """
        )
        connection.commit()
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        update_runner.materialize_additive_asset_dependency_scope(
            staged,
            source_revision_ids=delta.source_revision_ids,
            entity_ids=delta.entity_ids,
            fact_ids=delta.fact_ids,
            actual_write_tables=delta.changed_tables,
        )

        assert staged.execute(
            """
            SELECT downstream_kind, downstream_id, dependency_reason
            FROM invalidation_dependencies
            WHERE upstream_revision_id=1
            """
        ).fetchall() == [
            ("ROLE_ENTITY", 1, "EXISTING_OTHER_REVISION")
        ]
    finally:
        staged.rollback()
        staged.close()
        base.close()


def test_production_materializer_rolls_back_partial_dependency_scope(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        staged.execute(
            """
            CREATE TRIGGER reject_additive_projection
            BEFORE INSERT ON invalidation_dependencies
            WHEN NEW.upstream_revision_id=2
             AND NEW.downstream_kind='PROJECTION'
            BEGIN
              SELECT RAISE(ABORT, 'injected dependency failure');
            END
            """
        )
        staged.commit()

        with pytest.raises(
            sqlite3.IntegrityError,
            match="injected dependency failure",
        ):
            update_runner.materialize_additive_asset_dependency_scope(
                staged,
                source_revision_ids=delta.source_revision_ids,
                entity_ids=delta.entity_ids,
                fact_ids=delta.fact_ids,
                actual_write_tables=delta.changed_tables,
            )

        assert staged.in_transaction is False
        assert staged.execute(
            """
            SELECT COUNT(*) FROM invalidation_dependencies
            WHERE upstream_revision_id=2
            """
        ).fetchone()[0] == 0
    finally:
        staged.close()
        base.close()


@pytest.mark.parametrize(
    ("kind", "target", "reason"),
    (
        ("QUERY_SNAPSHOT", 1, "ADDITIVE_QUERY_CACHE"),
        ("ROLE_ENTITY", 999, "ADDITIVE_ROLE_INPUT"),
        ("DOMAIN_ENTITY", 999, "ADDITIVE_DOMAIN_INPUT"),
        ("PROJECTION", 999, "ADDITIVE_FACT_PROJECTION"),
    ),
)
def test_strict_additive_plan_rejects_wrong_dependency_scope(
    tmp_path: Path,
    kind: str,
    target: int,
    reason: str,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        _materialize_complete_dependency_scope(
            staged,
            source_revision_ids=delta.source_revision_ids,
            entity_ids=delta.entity_ids,
        )
        staged.execute(
            """
            INSERT INTO invalidation_dependencies VALUES (?, ?, ?, ?)
            """,
            (2, kind, target, reason),
        )
        staged.commit()

        with pytest.raises(InvalidationBlockedGap):
            plan_additive_asset_invalidation(
                staged,
                fact_ids=delta.fact_ids,
                entity_ids=delta.entity_ids,
                source_revision_ids=delta.source_revision_ids,
                actual_write_tables=delta.changed_tables,
            )
    finally:
        staged.close()
        base.close()


def test_strict_additive_plan_rejects_query_snapshot_revision_replay(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        staged.execute(
            """
            INSERT INTO invalidation_dependencies VALUES (
                1, 'QUERY_SNAPSHOT', 2, 'ADDITIVE_QUERY_CACHE'
            )
            """
        )
        staged.commit()

        with pytest.raises(
            InvalidationBlockedGap,
            match="replay",
        ):
            update_runner.materialize_additive_asset_dependency_scope(
                staged,
                source_revision_ids=delta.source_revision_ids,
                entity_ids=delta.entity_ids,
                fact_ids=delta.fact_ids,
                actual_write_tables=delta.changed_tables,
            )
    finally:
        staged.close()
        base.close()


def test_receipt_is_content_addressed_and_never_claims_publication(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        _materialize_complete_dependency_scope(
            staged,
            source_revision_ids=delta.source_revision_ids,
            entity_ids=delta.entity_ids,
        )
        plan = plan_additive_asset_invalidation(
            staged,
            fact_ids=delta.fact_ids,
            entity_ids=delta.entity_ids,
            source_revision_ids=delta.source_revision_ids,
            actual_write_tables=delta.changed_tables,
        )
        event_id = _persist_backend_event(
            staged,
            plan,
            _successful_backend_receipts(plan),
        )
        receipt = build_add_only_delta_receipt(
            delta,
            plan,
            backend_connection=staged,
            backend_event_id=event_id,
        )

        assert receipt["status"] == FOUNDATION_VERIFIED
        assert receipt["published"] is False
        assert receipt["e4Scenario2Complete"] is False
        assert receipt["blockedGaps"] == []
        assert str(receipt["proof"]).startswith("delta-proof://")
        validated = validate_add_only_delta_receipt(
            receipt,
            expected_receipt_sha256=_receipt_sha256(receipt),
        )
        assert validated["proof"] == receipt["proof"]
        assert validated["status"] == receipt["status"]
    finally:
        staged.close()
        base.close()


def test_missing_backend_is_blocked_gap_not_noop_success(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        _materialize_complete_dependency_scope(
            staged,
            source_revision_ids=delta.source_revision_ids,
            entity_ids=delta.entity_ids,
        )
        plan = plan_additive_asset_invalidation(
            staged,
            fact_ids=delta.fact_ids,
            entity_ids=delta.entity_ids,
            source_revision_ids=delta.source_revision_ids,
            actual_write_tables=delta.changed_tables,
        )
        terminal = _successful_backend_receipts(plan)
        terminal = [
            value
            for value in terminal
            if not (
                value["downstreamKind"] == "QUERY_SNAPSHOT"
                and value["downstreamId"] == 2
            )
        ]
        event_id = _persist_backend_event(staged, plan, terminal)
        receipt = build_add_only_delta_receipt(
            delta,
            plan,
            backend_connection=staged,
            backend_event_id=event_id,
        )

        assert receipt["status"] == BLOCKED_GAP
        assert receipt["blockedGaps"] == [
            "BACKEND_TERMINAL_RECEIPT_MISSING_QUERY_SNAPSHOT_2"
        ]
        assert receipt["published"] is False
        assert receipt["e4Scenario2Complete"] is False
        validated = validate_add_only_delta_receipt(
            receipt,
            expected_receipt_sha256=_receipt_sha256(receipt),
        )
        assert validated["proof"] == receipt["proof"]
        assert validated["status"] == receipt["status"]
    finally:
        staged.close()
        base.close()


def test_content_addressed_backend_receipt_cannot_prove_noop_outcome(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        _materialize_complete_dependency_scope(
            staged,
            source_revision_ids=delta.source_revision_ids,
            entity_ids=delta.entity_ids,
        )
        plan = plan_additive_asset_invalidation(
            staged,
            fact_ids=delta.fact_ids,
            entity_ids=delta.entity_ids,
            source_revision_ids=delta.source_revision_ids,
            actual_write_tables=delta.changed_tables,
        )
        terminal = _successful_backend_receipts(plan)
        attacked = terminal[0]
        attacked["afterDigest"] = attacked["beforeDigest"]
        attacked_body = dict(attacked)
        attacked_body.pop("proof")
        attacked["proof"] = (
            "rebuild-proof://" + _content_sha256(attacked_body)
        )
        event_id = _persist_backend_event(staged, plan, terminal)

        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_delta_receipt(
                delta,
                plan,
                backend_connection=staged,
                backend_event_id=event_id,
            )

        assert caught.value.gap_code == (
            "BACKEND_TERMINAL_OUTCOME_UNPROVEN"
        )
    finally:
        staged.close()
        base.close()


def test_explicit_whole_cache_invalidation_is_accepted_without_hiding_gaps(
    tmp_path: Path,
) -> None:
    base, staged, delta, plan, event_id = _ready_receipt_fixture(
        tmp_path,
        explicit_query_invalidation=True,
        blocked_kinds=frozenset(
            {"ROLE_ENTITY", "DOMAIN_ENTITY", "PROJECTION"}
        ),
    )
    try:
        receipt = build_add_only_delta_receipt(
            delta,
            plan,
            backend_connection=staged,
            backend_event_id=event_id,
        )

        assert receipt["schema"] == (
            "ark-kb-add-only-blueprint-delta-receipt/v2"
        )
        assert receipt["status"] == BLOCKED_GAP
        assert receipt["published"] is False
        assert receipt["e4Scenario2Complete"] is False
        terminal = receipt["backendTerminalReceipts"]
        query = next(
            item
            for item in terminal
            if item["downstreamKind"] == "QUERY_SNAPSHOT"
        )
        assert query["status"] == "SUCCEEDED"
        assert query["beforeDigest"] == query["afterDigest"]
        assert query["touchedTables"] == list(_QUERY_CACHE_TABLES)
        assert query["verification"] == {
            "basis": "EXPLICIT_WHOLE_CACHE_INVALIDATION",
            "coreWriteChanges": 0,
            "writeOperations": list(_QUERY_CACHE_DELETE_OPERATIONS),
            "rowScope": {
                "mode": "EXPLICIT_WHOLE_CACHE_BATCH",
                "eventId": event_id,
                "targetId": delta.source_revision_ids[0],
                "tables": list(_QUERY_CACHE_TABLES),
            },
        }
        blocked = [
            item for item in terminal if item["status"] == BLOCKED_GAP
        ]
        assert len(blocked) == 8
        assert {
            item["downstreamKind"] for item in blocked
        } == {"ROLE_ENTITY", "DOMAIN_ENTITY", "PROJECTION"}
    finally:
        staged.close()
        base.close()


@pytest.mark.parametrize(
    "attack",
    (
        "non-query-kind",
        "changed-digest",
        "forged-basis",
        "touched-missing",
        "touched-extra",
        "touched-metadata",
        "operation-missing",
        "operation-extra",
        "operation-insert",
        "operation-update",
        "operation-duplicate",
        "core-nonzero",
        "core-bool",
        "core-float",
        "core-string",
        "cache-hit",
        "recovered",
        "incomplete",
        "gap-code",
        "detail",
        "projection-batch",
        "row-scope-entity-id",
        "row-scope-event",
        "row-scope-tables",
        "target-state-basis",
        "operations-empty",
        "event-identity",
        "verification-field-added",
        "verification-field-deleted",
        "receipt-field-added",
        "receipt-field-deleted",
    ),
)
def test_explicit_whole_cache_invalidation_fails_closed(
    tmp_path: Path,
    attack: str,
) -> None:
    base, staged, delta, plan, event_id = _ready_receipt_fixture(
        tmp_path,
        explicit_query_invalidation=True,
    )
    try:
        payload = json.loads(
            str(
                staged.execute(
                    """
                    SELECT payload_json
                    FROM invalidation_events
                    WHERE event_id=?
                    """,
                    (event_id,),
                ).fetchone()[0]
            )
        )
        receipts = payload["_rebuildReceipts"]
        key = f"QUERY_SNAPSHOT:{delta.source_revision_ids[0]}"
        attacked = receipts[key]
        if attack == "non-query-kind":
            key = f"FACT:{delta.fact_ids[0]}"
            attacked = receipts[key]
            attacked["afterDigest"] = attacked["beforeDigest"]
            attacked["touchedTables"] = list(_QUERY_CACHE_TABLES)
            attacked["verification"] = {
                "basis": "EXPLICIT_WHOLE_CACHE_INVALIDATION",
                "coreWriteChanges": 0,
                "writeOperations": list(
                    _QUERY_CACHE_DELETE_OPERATIONS
                ),
                "rowScope": {
                    "mode": "EXPLICIT_WHOLE_CACHE_BATCH",
                    "eventId": event_id,
                    "targetId": attacked["downstreamId"],
                    "tables": list(_QUERY_CACHE_TABLES),
                },
            }
        elif attack == "changed-digest":
            attacked["afterDigest"] = "a" * 64
        elif attack == "forged-basis":
            attacked["verification"]["basis"] = (
                "explicit_whole_cache_invalidation"
            )
        elif attack == "touched-missing":
            attacked["touchedTables"] = list(_QUERY_CACHE_TABLES[:-1])
        elif attack == "touched-extra":
            attacked["touchedTables"] = [
                *_QUERY_CACHE_TABLES,
                "extra_cache",
            ]
        elif attack == "touched-metadata":
            attacked["touchedTables"] = [
                *_QUERY_CACHE_TABLES,
                "metadata",
            ]
        elif attack == "operation-missing":
            attacked["verification"]["writeOperations"] = list(
                _QUERY_CACHE_DELETE_OPERATIONS[:-1]
            )
        elif attack == "operation-extra":
            attacked["verification"]["writeOperations"] = [
                *_QUERY_CACHE_DELETE_OPERATIONS,
                "metadata:DELETE",
            ]
        elif attack == "operation-insert":
            attacked["verification"]["writeOperations"][0] = (
                "answer_plans:INSERT"
            )
        elif attack == "operation-update":
            attacked["verification"]["writeOperations"][0] = (
                "answer_plans:UPDATE"
            )
        elif attack == "operation-duplicate":
            attacked["verification"]["writeOperations"].append(
                _QUERY_CACHE_DELETE_OPERATIONS[0]
            )
        elif attack == "core-nonzero":
            attacked["verification"]["coreWriteChanges"] = 1
        elif attack == "core-bool":
            attacked["verification"]["coreWriteChanges"] = False
        elif attack == "core-float":
            attacked["verification"]["coreWriteChanges"] = 0.0
        elif attack == "core-string":
            attacked["verification"]["coreWriteChanges"] = "0"
        elif attack == "cache-hit":
            attacked["cacheHit"] = True
        elif attack == "recovered":
            attacked["recovered"] = True
        elif attack == "incomplete":
            attacked["complete"] = False
        elif attack == "gap-code":
            attacked["gapCode"] = "FORGED_GAP"
        elif attack == "detail":
            attacked["detail"] = "forged detail"
        elif attack == "projection-batch":
            attacked["projectionBatch"] = {"1": "a" * 64}
        elif attack == "row-scope-entity-id":
            attacked["verification"]["rowScope"]["targetId"] = (
                delta.entity_ids[0]
            )
        elif attack == "row-scope-event":
            attacked["verification"]["rowScope"]["eventId"] = (
                "forged-event"
            )
        elif attack == "row-scope-tables":
            attacked["verification"]["rowScope"]["tables"] = list(
                _QUERY_CACHE_TABLES[:-1]
            )
        elif attack == "target-state-basis":
            attacked["verification"]["basis"] = "TARGET_STATE_CHANGED"
        elif attack == "operations-empty":
            attacked["verification"]["writeOperations"] = []
        elif attack == "event-identity":
            attacked["eventId"] = "forged-event"
        elif attack == "verification-field-added":
            attacked["verification"]["extra"] = True
        elif attack == "verification-field-deleted":
            del attacked["verification"]["coreWriteChanges"]
        elif attack == "receipt-field-added":
            attacked["extra"] = True
        elif attack == "receipt-field-deleted":
            del attacked["detail"]
        else:
            raise AssertionError(f"unknown attack: {attack}")
        receipts[key] = _reproof_backend_receipt(attacked)
        staged.execute(
            """
            UPDATE invalidation_events
            SET payload_json=?
            WHERE event_id=?
            """,
            (
                json.dumps(
                    payload,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                event_id,
            ),
        )
        staged.commit()

        with pytest.raises(AddOnlyDeltaBlockedGap):
            build_add_only_delta_receipt(
                delta,
                plan,
                backend_connection=staged,
                backend_event_id=event_id,
            )
    finally:
        staged.close()
        base.close()


@pytest.mark.parametrize(
    "kind",
    (
        "FACT",
        "EFFECTIVE_ENTITY",
        "ROLE_ENTITY",
        "DOMAIN_ENTITY",
        "PROJECTION",
    ),
)
def test_ordinary_backend_noop_receipts_remain_rejected(
    tmp_path: Path,
    kind: str,
) -> None:
    base, staged, delta, plan, event_id = _ready_receipt_fixture(tmp_path)
    try:
        payload = json.loads(
            str(
                staged.execute(
                    """
                    SELECT payload_json
                    FROM invalidation_events
                    WHERE event_id=?
                    """,
                    (event_id,),
                ).fetchone()[0]
            )
        )
        attacked = next(
            receipt
            for receipt in payload["_rebuildReceipts"].values()
            if receipt["downstreamKind"] == kind
        )
        attacked["afterDigest"] = attacked["beforeDigest"]
        _reproof_backend_receipt(attacked)
        staged.execute(
            """
            UPDATE invalidation_events
            SET payload_json=?
            WHERE event_id=?
            """,
            (
                json.dumps(
                    payload,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                event_id,
            ),
        )
        staged.commit()

        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_delta_receipt(
                delta,
                plan,
                backend_connection=staged,
                backend_event_id=event_id,
            )

        assert caught.value.gap_code == (
            "BACKEND_TERMINAL_OUTCOME_UNPROVEN"
        )
    finally:
        staged.close()
        base.close()


@pytest.mark.parametrize(
    "attack",
    (
        "unrelated-table",
        "touched-operation-mismatch",
        "invalid-operation-kind",
        "wrong-row-scope",
    ),
)
def test_backend_receipt_tables_are_exactly_canonical_and_consistent(
    tmp_path: Path,
    attack: str,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        _materialize_complete_dependency_scope(
            staged,
            source_revision_ids=delta.source_revision_ids,
            entity_ids=delta.entity_ids,
        )
        plan = plan_additive_asset_invalidation(
            staged,
            fact_ids=delta.fact_ids,
            entity_ids=delta.entity_ids,
            source_revision_ids=delta.source_revision_ids,
            actual_write_tables=delta.changed_tables,
        )
        terminal = _successful_backend_receipts(plan)
        attacked = next(
            receipt
            for receipt in terminal
            if receipt["downstreamKind"] == "FACT"
        )
        if attack == "unrelated-table":
            attacked["touchedTables"] = ["facts", "entities"]
            attacked["verification"] = {
                "basis": "TARGET_STATE_CHANGED",
                "coreWriteChanges": 2,
                "writeOperations": [
                    "facts:INSERT",
                    "entities:UPDATE",
                ],
            }
        elif attack == "touched-operation-mismatch":
            attacked["touchedTables"] = ["facts"]
            attacked["verification"] = {
                "basis": "TARGET_STATE_CHANGED",
                "coreWriteChanges": 1,
                "writeOperations": ["fact_evidence:INSERT"],
            }
        elif attack == "invalid-operation-kind":
            attacked["touchedTables"] = ["facts"]
            attacked["verification"] = {
                "basis": "TARGET_STATE_CHANGED",
                "coreWriteChanges": 1,
                "writeOperations": ["facts:FORGED"],
            }
        else:
            attacked["verification"]["rowScope"] = {
                "mode": "TASK_TARGET_ID",
                "targetId": 999999,
            }
        attacked_body = dict(attacked)
        attacked_body.pop("proof")
        attacked["proof"] = (
            "rebuild-proof://" + _content_sha256(attacked_body)
        )
        event_id = _persist_backend_event(staged, plan, terminal)

        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_delta_receipt(
                delta,
                plan,
                backend_connection=staged,
                backend_event_id=event_id,
            )

        assert caught.value.gap_code == (
            "BACKEND_TERMINAL_OUTCOME_UNPROVEN"
        )
    finally:
        staged.close()
        base.close()


def test_backend_event_payload_rejects_duplicate_json_keys(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        _materialize_complete_dependency_scope(
            staged,
            source_revision_ids=delta.source_revision_ids,
            entity_ids=delta.entity_ids,
        )
        plan = plan_additive_asset_invalidation(
            staged,
            fact_ids=delta.fact_ids,
            entity_ids=delta.entity_ids,
            source_revision_ids=delta.source_revision_ids,
            actual_write_tables=delta.changed_tables,
        )
        event_id = _persist_backend_event(
            staged,
            plan,
            _successful_backend_receipts(plan),
        )
        raw_payload = str(
            staged.execute(
                """
                SELECT payload_json
                FROM invalidation_events
                WHERE event_id=?
                """,
                (event_id,),
            ).fetchone()[0]
        )
        staged.execute(
            """
            UPDATE invalidation_events
            SET payload_json=?
            WHERE event_id=?
            """,
            ('{"FACT":[999],' + raw_payload[1:], event_id),
        )
        staged.commit()

        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_delta_receipt(
                delta,
                plan,
                backend_connection=staged,
                backend_event_id=event_id,
            )

        assert caught.value.gap_code == "BACKEND_EVENT_INVALID"
    finally:
        staged.close()
        base.close()


def test_receipt_rejects_plan_not_bound_to_verified_delta(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        empty_plan = InvalidationPlan(
            event_kind="ASSET",
            upstream_revision_id=None,
            downstream={},
            reasons={},
        )

        with pytest.raises(
            AddOnlyDeltaBlockedGap,
            match="invalidation plan",
        ) as caught:
            build_add_only_delta_receipt(
                delta,
                empty_plan,
                backend_connection=staged,
                backend_event_id="missing-test-event",
            )

        assert caught.value.gap_code == "DELTA_INVALIDATION_PLAN_MISMATCH"
    finally:
        staged.close()
        base.close()


def test_test_only_artifact_binding_requires_explicit_test_context(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        with pytest.raises(
            AddOnlyDeltaBlockedGap,
            match="independent signed authorization",
        ) as caught:
            build_add_only_blueprint_delta(
                base,
                staged,
                source_diff=diff,
                ingest_result=result,
                artifact_root=artifact_root,
                artifact_bindings=(binding,),
            )

        assert caught.value.status == BLOCKED_GAP
        assert caught.value.gap_code == (
            "PRODUCTION_ARTIFACT_AUTHORIZATION_REQUIRED"
        )

    finally:
        staged.close()
        base.close()


@pytest.mark.parametrize(
    ("mutate_binding", "expected_gap"),
    (
        (
            lambda binding: {
                **binding,
                "artifactUri": "artifact://../outside.sqlite",
            },
            "ARTIFACT_URI_UNSAFE",
        ),
        (
            lambda binding: {
                **binding,
                "artifactSha256": "0" * 64,
            },
            "ARTIFACT_SHA256_MISMATCH",
        ),
    ),
)
def test_artifact_path_and_bytes_fail_closed(
    tmp_path: Path,
    mutate_binding: object,
    expected_gap: str,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        bad_binding = mutate_binding(binding)  # type: ignore[operator]
        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_blueprint_delta(
                base,
                staged,
                source_diff=diff,
                ingest_result=result,
                artifact_root=artifact_root,
                artifact_bindings=(bad_binding,),
                trust_context=TEST_ONLY,
            )

        assert caught.value.status == BLOCKED_GAP
        assert caught.value.gap_code == expected_gap
    finally:
        staged.close()
        base.close()


def test_broad_or_unrelated_table_write_fails_closed(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    staged.execute(
        """
        INSERT INTO entities(
            entity_id, canonical_uri, entity_kind, status, confidence
        ) VALUES (
            2, '/Game/Test/Unrelated.Unrelated',
            'BLUEPRINT_ASSET', 'CONFIRMED', 'HIGH'
        )
        """
    )
    staged.execute(
        """
        INSERT INTO edges VALUES (
            201, 1, 2, 'UNRELATED', 'DIRECT', 'CONFIRMED',
            'HIGH', 2, 'bp://test/unrelated', '', ''
        )
        """
    )
    staged.commit()
    try:
        with pytest.raises(
            AddOnlyDeltaBlockedGap,
            match="durable write scope",
        ) as caught:
            build_add_only_blueprint_delta(
                base,
                staged,
                source_diff=diff,
                ingest_result=result,
                artifact_root=artifact_root,
                artifact_bindings=(binding,),
                trust_context=TEST_ONLY,
            )

        assert caught.value.gap_code == (
            "ADDITIVE_ASSET_WRITE_SCOPE_UNSUPPORTED"
        )
    finally:
        staged.close()
        base.close()


def test_update_or_delete_diff_is_not_reinterpreted_as_add_only(
    tmp_path: Path,
) -> None:
    base, staged, _, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    previous = _revision(fingerprint="a" * 64)
    current = _revision(fingerprint="b" * 64)
    diff = SourceDiff(
        changed=(
            SourceChange(
                change_kind="CHANGED",
                source_id=current.source_id,
                previous=previous,
                current=current,
            ),
        )
    )
    try:
        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_blueprint_delta(
                base,
                staged,
                source_diff=diff,
                ingest_result=result,
                artifact_root=artifact_root,
                artifact_bindings=(binding,),
                trust_context=TEST_ONLY,
            )

        assert caught.value.gap_code == "SOURCE_DIFF_NOT_ADD_ONLY_BLUEPRINT"
    finally:
        staged.close()
        base.close()


def test_source_revision_without_fact_evidence_is_not_successful_work(
    tmp_path: Path,
) -> None:
    base, staged = _core_pair(tmp_path)
    result = _materialize_test_only_rows(staged, include_fact=False)
    artifact_root, revision, binding = _artifact_binding(tmp_path)
    diff = _add_only_diff(revision)
    try:
        with pytest.raises(
            AddOnlyDeltaBlockedGap,
            match="fact evidence",
        ) as caught:
            build_add_only_blueprint_delta(
                base,
                staged,
                source_diff=diff,
                ingest_result=result,
                artifact_root=artifact_root,
                artifact_bindings=(binding,),
                trust_context=TEST_ONLY,
            )

        assert caught.value.gap_code == (
            "ADDITIVE_ASSET_FACT_EVIDENCE_MISSING"
        )
    finally:
        staged.close()
        base.close()


def test_receipt_tamper_is_rejected_after_recomputed_business_fields(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        _materialize_complete_dependency_scope(
            staged,
            source_revision_ids=delta.source_revision_ids,
            entity_ids=delta.entity_ids,
        )
        plan = plan_additive_asset_invalidation(
            staged,
            fact_ids=delta.fact_ids,
            entity_ids=delta.entity_ids,
            source_revision_ids=delta.source_revision_ids,
            actual_write_tables=delta.changed_tables,
        )
        event_id = _persist_backend_event(
            staged,
            plan,
            _successful_backend_receipts(plan),
        )
        receipt = build_add_only_delta_receipt(
            delta,
            plan,
            backend_connection=staged,
            backend_event_id=event_id,
        )
        expected_receipt_sha256 = _receipt_sha256(receipt)
        tampered = copy.deepcopy(receipt)
        tampered["changedTables"] = ["facts"]
        tampered_body = dict(tampered)
        tampered_body.pop("proof")
        tampered["proof"] = (
            "delta-proof://" + _content_sha256(tampered_body)
        )

        with pytest.raises(
            AddOnlyDeltaBlockedGap,
            match="out-of-band",
        ) as caught:
            validate_add_only_delta_receipt(
                tampered,
                expected_receipt_sha256=expected_receipt_sha256,
            )

        assert caught.value.gap_code == (
            "OUT_OF_BAND_RECEIPT_SHA256_MISMATCH"
        )
    finally:
        staged.close()
        base.close()


@pytest.mark.parametrize(
    "ddl",
    (
        "CREATE TABLE attack_table(id INTEGER PRIMARY KEY)",
        "CREATE INDEX attack_index ON facts(fact_name)",
        """
        CREATE TRIGGER attack_trigger AFTER INSERT ON facts
        BEGIN
            SELECT NEW.fact_id;
        END
        """,
        "CREATE VIEW attack_view AS SELECT fact_id FROM facts",
    ),
)
def test_logical_database_digest_covers_all_durable_schema_objects(
    ddl: str,
) -> None:
    base = sqlite3.connect(":memory:")
    staged = sqlite3.connect(":memory:")
    try:
        base.executescript(FULL_CORE_SCHEMA_SQL)
        staged.executescript(FULL_CORE_SCHEMA_SQL)
        staged.execute(ddl)

        assert (
            logical_database_state(base).database_sha256
            != logical_database_state(staged).database_sha256
        )
    finally:
        staged.close()
        base.close()


@pytest.mark.parametrize(
    ("create_ddl", "drop_ddl"),
    (
        (
            "CREATE TABLE attack_table(id INTEGER PRIMARY KEY)",
            "DROP TABLE attack_table",
        ),
        (
            "CREATE INDEX attack_index ON facts(fact_name)",
            "DROP INDEX attack_index",
        ),
        (
            """
            CREATE TRIGGER attack_trigger AFTER INSERT ON facts
            BEGIN
                SELECT NEW.fact_id;
            END
            """,
            "DROP TRIGGER attack_trigger",
        ),
        (
            "CREATE VIEW attack_view AS SELECT fact_id FROM facts",
            "DROP VIEW attack_view",
        ),
    ),
)
def test_logical_database_digest_detects_dropped_schema_objects(
    create_ddl: str,
    drop_ddl: str,
) -> None:
    base = sqlite3.connect(":memory:")
    staged = sqlite3.connect(":memory:")
    try:
        base.executescript(FULL_CORE_SCHEMA_SQL)
        staged.executescript(FULL_CORE_SCHEMA_SQL)
        base.execute(create_ddl)
        staged.execute(create_ddl)
        staged.execute(drop_ddl)

        assert (
            logical_database_state(base).database_sha256
            != logical_database_state(staged).database_sha256
        )
    finally:
        staged.close()
        base.close()


def test_source_diff_fingerprint_must_match_observed_artifact_bundle(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    current = diff.added[0].current
    assert current is not None
    bad_revision = SourceRevision(
        source_id=current.source_id,
        source_kind=current.source_kind,
        source_uri=current.source_uri,
        fingerprint="0" * 64,
        size_bytes=current.size_bytes,
        entity_uri=current.entity_uri,
        revision_label=current.revision_label,
    )
    bad_diff = _add_only_diff(bad_revision)
    bad_binding = {**binding, "sourceFingerprint": "0" * 64}
    try:
        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_blueprint_delta(
                base,
                staged,
                source_diff=bad_diff,
                ingest_result=result,
                artifact_root=artifact_root,
                artifact_bindings=(bad_binding,),
                trust_context=TEST_ONLY,
            )

        assert caught.value.gap_code == "SOURCE_DIFF_AGGREGATE_MISMATCH"
    finally:
        staged.close()
        base.close()


@pytest.mark.parametrize(
    ("revision_label", "size_delta", "expected_gap"),
    (
        (
            "forged-revision-label",
            0,
            "SOURCE_DIFF_REVISION_LABEL_MISMATCH",
        ),
        (
            "test-revision",
            1,
            "SOURCE_DIFF_SIZE_MISMATCH",
        ),
    ),
)
def test_source_diff_identity_must_match_frozen_evidence_bytes(
    tmp_path: Path,
    revision_label: str,
    size_delta: int,
    expected_gap: str,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    current = diff.added[0].current
    assert current is not None
    bad_revision = SourceRevision(
        source_id=current.source_id,
        source_kind=current.source_kind,
        source_uri=current.source_uri,
        fingerprint=current.fingerprint,
        size_bytes=current.size_bytes + size_delta,
        entity_uri=current.entity_uri,
        revision_label=revision_label,
    )
    try:
        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_blueprint_delta(
                base,
                staged,
                source_diff=_add_only_diff(bad_revision),
                ingest_result=result,
                artifact_root=artifact_root,
                artifact_bindings=(binding,),
                trust_context=TEST_ONLY,
            )

        assert caught.value.gap_code == expected_gap
    finally:
        staged.close()
        base.close()


def test_adjacent_manifest_bytes_are_bound_into_source_diff_aggregate(
    tmp_path: Path,
) -> None:
    base, staged = _core_pair(tmp_path)
    result = _materialize_test_only_rows(staged)
    artifact_root, revision, binding = _artifact_binding(
        tmp_path,
        manifest_bytes=b'{"version":1}\n',
    )
    manifest = (
        artifact_root / "evidence" / "Added" / "manifest.json"
    )
    manifest.write_bytes(b'{"version":2}\n')
    try:
        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_blueprint_delta(
                base,
                staged,
                source_diff=_add_only_diff(revision),
                ingest_result=result,
                artifact_root=artifact_root,
                artifact_bindings=(binding,),
                trust_context=TEST_ONLY,
            )

        assert caught.value.gap_code == "SOURCE_DIFF_AGGREGATE_MISMATCH"
    finally:
        staged.close()
        base.close()


def test_evidence_identity_uses_the_same_frozen_bytes_after_path_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    artifact_path = (
        artifact_root / "evidence" / "Added" / "evidence.sqlite"
    )
    freeze_file = incremental_delta_module._freeze_file
    swapped = False

    def freeze_then_swap(path: Path) -> object:
        nonlocal swapped
        frozen = freeze_file(path)
        if path.name == "evidence.sqlite" and not swapped:
            path.write_bytes(b"attacker-replaced-after-read")
            swapped = True
        return frozen

    monkeypatch.setattr(
        incremental_delta_module,
        "_freeze_file",
        freeze_then_swap,
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )

        assert swapped is True
        assert artifact_path.read_bytes() == b"attacker-replaced-after-read"
        assert delta.artifacts[0]["artifactSha256"] == binding[
            "artifactSha256"
        ]
    finally:
        staged.close()
        base.close()


def test_new_fact_rejects_non_confirmed_or_non_high_quality(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    staged.execute(
        """
        UPDATE facts
        SET status='NOT_RECOVERED', confidence='LOW'
        WHERE fact_id=101
        """
    )
    staged.commit()
    try:
        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_blueprint_delta(
                base,
                staged,
                source_diff=diff,
                ingest_result=result,
                artifact_root=artifact_root,
                artifact_bindings=(binding,),
                trust_context=TEST_ONLY,
            )

        assert caught.value.gap_code == "ADDITIVE_ASSET_FACT_QUALITY_INVALID"
    finally:
        staged.close()
        base.close()


def test_new_source_revision_must_be_fresh(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    staged.execute(
        """
        UPDATE source_revisions
        SET freshness_status='STALE'
        WHERE revision_id=2
        """
    )
    staged.commit()
    try:
        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_blueprint_delta(
                base,
                staged,
                source_diff=diff,
                ingest_result=result,
                artifact_root=artifact_root,
                artifact_bindings=(binding,),
                trust_context=TEST_ONLY,
            )

        assert caught.value.gap_code == (
            "ADDITIVE_ASSET_INGEST_NOT_FRESH"
        )
    finally:
        staged.close()
        base.close()


def test_new_fact_must_be_current(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    staged.execute("UPDATE facts SET current=0 WHERE fact_id=101")
    staged.commit()
    try:
        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_blueprint_delta(
                base,
                staged,
                source_diff=diff,
                ingest_result=result,
                artifact_root=artifact_root,
                artifact_bindings=(binding,),
                trust_context=TEST_ONLY,
            )

        assert caught.value.gap_code == "ADDITIVE_ASSET_FACT_QUALITY_INVALID"
    finally:
        staged.close()
        base.close()


@pytest.mark.parametrize(
    ("evidence_uri", "evidence_role"),
    (
        (
            "fixture://invented",
            "DEFAULT_VALUE_ACTUAL",
        ),
        (
            "bp://test-asset@test-revision/default/Rate",
            "INVENTED_ROLE",
        ),
    ),
)
def test_new_fact_rejects_untrusted_evidence_uri_or_role(
    tmp_path: Path,
    evidence_uri: str,
    evidence_role: str,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    staged.execute(
        """
        UPDATE fact_evidence
        SET evidence_uri=?,
            evidence_role=?
        WHERE fact_id=101
        """,
        (evidence_uri, evidence_role),
    )
    staged.commit()
    try:
        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_blueprint_delta(
                base,
                staged,
                source_diff=diff,
                ingest_result=result,
                artifact_root=artifact_root,
                artifact_bindings=(binding,),
                trust_context=TEST_ONLY,
            )

        assert caught.value.gap_code == (
            "ADDITIVE_ASSET_FACT_EVIDENCE_INVALID"
        )
    finally:
        staged.close()
        base.close()


def test_additive_plan_blocks_when_derived_dependency_scope_is_unproven(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )

        with pytest.raises(
            InvalidationBlockedGap,
            match="derived dependenc",
        ) as caught:
            plan_additive_asset_invalidation(
                staged,
                fact_ids=delta.fact_ids,
                entity_ids=delta.entity_ids,
                source_revision_ids=delta.source_revision_ids,
                actual_write_tables=delta.changed_tables,
            )

        assert caught.value.gap_code == (
            "ADDITIVE_ASSET_DERIVED_DEPENDENCIES_UNPROVEN"
        )
    finally:
        staged.close()
        base.close()


def test_collision_radius_cannot_stop_at_fact_and_effective_tasks(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    staged.execute(
        "UPDATE facts SET fact_name='CollisionRadius' WHERE fact_id=101"
    )
    staged.commit()
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        _materialize_complete_dependency_scope(
            staged,
            source_revision_ids=delta.source_revision_ids,
            entity_ids=delta.entity_ids,
        )
        staged.execute(
            """
            DELETE FROM invalidation_dependencies
            WHERE upstream_revision_id=2
              AND downstream_kind='PROJECTION'
            """
        )
        staged.commit()

        with pytest.raises(
            InvalidationBlockedGap,
            match="2:PROJECTION",
        ) as caught:
            plan_additive_asset_invalidation(
                staged,
                fact_ids=delta.fact_ids,
                entity_ids=delta.entity_ids,
                source_revision_ids=delta.source_revision_ids,
                actual_write_tables=delta.changed_tables,
            )

        assert caught.value.gap_code == (
            "ADDITIVE_ASSET_DERIVED_DEPENDENCIES_UNPROVEN"
        )
    finally:
        staged.close()
        base.close()


def test_receipt_validation_requires_out_of_band_expected_sha256(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        _materialize_complete_dependency_scope(
            staged,
            source_revision_ids=delta.source_revision_ids,
            entity_ids=delta.entity_ids,
        )
        plan = plan_additive_asset_invalidation(
            staged,
            fact_ids=delta.fact_ids,
            entity_ids=delta.entity_ids,
            source_revision_ids=delta.source_revision_ids,
            actual_write_tables=delta.changed_tables,
        )
        event_id = _persist_backend_event(
            staged,
            plan,
            _successful_backend_receipts(plan),
        )
        receipt = build_add_only_delta_receipt(
            delta,
            plan,
            backend_connection=staged,
            backend_event_id=event_id,
        )

        with pytest.raises(TypeError):
            validate_add_only_delta_receipt(receipt)  # type: ignore[call-arg]
        with pytest.raises(
            AddOnlyDeltaBlockedGap,
            match="out-of-band",
        ):
            validate_add_only_delta_receipt(
                receipt,
                expected_receipt_sha256="0" * 64,
            )
    finally:
        staged.close()
        base.close()


def test_receipt_builder_rejects_manual_reduced_plan_bypass(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        _materialize_complete_dependency_scope(
            staged,
            source_revision_ids=delta.source_revision_ids,
            entity_ids=delta.entity_ids,
        )
        reduced = InvalidationPlan(
            event_kind="ASSET",
            upstream_revision_id=None,
            downstream={
                "FACT": delta.fact_ids,
                "EFFECTIVE_ENTITY": delta.entity_ids,
            },
            reasons={
                "FACT": "ADDED_BLUEPRINT_FACT_EVIDENCE",
                "EFFECTIVE_ENTITY": "ADDED_DECLARED_DEFAULT_OR_PARENT",
            },
        )
        event_id = _persist_backend_event(
            staged,
            reduced,
            _successful_backend_receipts(reduced),
        )

        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_delta_receipt(
                delta,
                reduced,
                backend_connection=staged,
                backend_event_id=event_id,
            )

        assert caught.value.gap_code == (
            "DELTA_INVALIDATION_PLAN_MISMATCH"
        )
    finally:
        staged.close()
        base.close()


def test_receipt_builder_rejects_truth_table_drift_after_delta(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        _materialize_complete_dependency_scope(
            staged,
            source_revision_ids=delta.source_revision_ids,
            entity_ids=delta.entity_ids,
        )
        plan = plan_additive_asset_invalidation(
            staged,
            fact_ids=delta.fact_ids,
            entity_ids=delta.entity_ids,
            source_revision_ids=delta.source_revision_ids,
            actual_write_tables=delta.changed_tables,
        )
        staged.execute(
            "UPDATE facts SET value_integer=999 WHERE fact_id=101"
        )
        staged.commit()
        event_id = _persist_backend_event(
            staged,
            plan,
            _successful_backend_receipts(plan),
        )

        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_delta_receipt(
                delta,
                plan,
                backend_connection=staged,
                backend_event_id=event_id,
            )

        assert caught.value.gap_code == "DELTA_TRUTH_TABLE_DRIFT"
    finally:
        staged.close()
        base.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sourceRevisionIds", []),
        ("sourceRevisionIds", [2, 2]),
        ("entityIds", []),
        ("entityIds", [True]),
        ("factIds", [999999]),
        ("artifacts", []),
    ],
)
def test_receipt_validator_rejects_rehashed_semantic_scope_tamper(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        _materialize_complete_dependency_scope(
            staged,
            source_revision_ids=delta.source_revision_ids,
            entity_ids=delta.entity_ids,
        )
        plan = plan_additive_asset_invalidation(
            staged,
            fact_ids=delta.fact_ids,
            entity_ids=delta.entity_ids,
            source_revision_ids=delta.source_revision_ids,
            actual_write_tables=delta.changed_tables,
        )
        event_id = _persist_backend_event(
            staged,
            plan,
            _successful_backend_receipts(plan),
        )
        receipt = build_add_only_delta_receipt(
            delta,
            plan,
            backend_connection=staged,
            backend_event_id=event_id,
        )
        attacked = copy.deepcopy(receipt)
        attacked[field] = value
        body = dict(attacked)
        body.pop("proof")
        attacked["proof"] = "delta-proof://" + _content_sha256(body)

        with pytest.raises(AddOnlyDeltaBlockedGap):
            validate_add_only_delta_receipt(
                attacked,
                expected_receipt_sha256=_receipt_sha256(attacked),
            )
    finally:
        staged.close()
        base.close()


def test_receipt_validator_rejects_rehashed_equal_database_digests(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        _materialize_complete_dependency_scope(
            staged,
            source_revision_ids=delta.source_revision_ids,
            entity_ids=delta.entity_ids,
        )
        plan = plan_additive_asset_invalidation(
            staged,
            fact_ids=delta.fact_ids,
            entity_ids=delta.entity_ids,
            source_revision_ids=delta.source_revision_ids,
            actual_write_tables=delta.changed_tables,
        )
        event_id = _persist_backend_event(
            staged,
            plan,
            _successful_backend_receipts(plan),
        )
        receipt = build_add_only_delta_receipt(
            delta,
            plan,
            backend_connection=staged,
            backend_event_id=event_id,
        )
        attacked = copy.deepcopy(receipt)
        attacked["beforeDatabaseSha256"] = attacked[
            "afterDatabaseSha256"
        ]
        body = dict(attacked)
        body.pop("proof")
        attacked["proof"] = "delta-proof://" + _content_sha256(body)

        with pytest.raises(AddOnlyDeltaBlockedGap):
            validate_add_only_delta_receipt(
                attacked,
                expected_receipt_sha256=_receipt_sha256(attacked),
            )
    finally:
        staged.close()
        base.close()


def test_production_context_cannot_be_self_declared_by_binding(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    production_binding = dict(binding)
    production_binding["trustContext"] = "PRODUCTION"
    try:
        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_blueprint_delta(
                base,
                staged,
                source_diff=diff,
                ingest_result=result,
                artifact_root=artifact_root,
                artifact_bindings=(production_binding,),
            )

        assert caught.value.gap_code == (
            "PRODUCTION_ARTIFACT_AUTHORIZATION_REQUIRED"
        )

    finally:
        staged.close()
        base.close()


def test_receipt_builder_rejects_replaced_production_delta(
    tmp_path: Path,
) -> None:
    base, staged, delta, plan, event_id = _ready_receipt_fixture(tmp_path)
    try:
        forged = replace(delta, trust_context="PRODUCTION")

        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_delta_receipt(
                forged,
                plan,
                backend_connection=staged,
                backend_event_id=event_id,
            )

        assert caught.value.gap_code == (
            "PRODUCTION_ARTIFACT_AUTHORIZATION_REQUIRED"
        )

        forged_artifact = replace(
            delta,
            artifacts=(
                {
                    **dict(delta.artifacts[0]),
                    "trustContext": "PRODUCTION",
                },
            ),
        )
        with pytest.raises(AddOnlyDeltaBlockedGap) as artifact_caught:
            build_add_only_delta_receipt(
                forged_artifact,
                plan,
                backend_connection=staged,
                backend_event_id=event_id,
            )

        assert artifact_caught.value.gap_code == (
            "PRODUCTION_ARTIFACT_AUTHORIZATION_REQUIRED"
        )
    finally:
        staged.close()
        base.close()


def test_receipt_builder_holds_cross_process_database_snapshot_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, staged, delta, plan, event_id = _ready_receipt_fixture(tmp_path)
    database_path = Path(
        str(staged.execute("PRAGMA database_list").fetchone()[2])
    )
    original = incremental_delta_module.plan_additive_asset_invalidation
    blocked: list[bool] = []

    def competing_write_then_plan(
        connection: sqlite3.Connection,
        **kwargs: object,
    ) -> InvalidationPlan:
        competing = sqlite3.connect(database_path, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                competing.execute(
                    "UPDATE facts SET value_integer=999 WHERE fact_id=101"
                )
                competing.commit()
            competing.rollback()
            blocked.append(True)
        finally:
            competing.close()
        return original(connection, **kwargs)

    monkeypatch.setattr(
        incremental_delta_module,
        "plan_additive_asset_invalidation",
        competing_write_then_plan,
    )
    try:
        receipt = build_add_only_delta_receipt(
            delta,
            plan,
            backend_connection=staged,
            backend_event_id=event_id,
        )
        current = logical_database_state(staged)

        assert blocked == [True]
        assert (
            staged.execute(
                "SELECT value_integer FROM facts WHERE fact_id=101"
            ).fetchone()[0]
            == 7
        )
        assert receipt["status"] == FOUNDATION_VERIFIED
        assert (
            receipt["protectedTableSha256"]["facts"]
            == current.table_sha256["facts"]
        )
        assert (
            receipt["receiptDatabaseSha256"] == current.database_sha256
        )
    finally:
        staged.close()
        base.close()


def test_delta_builder_holds_cross_process_database_snapshot_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    database_path = Path(
        str(staged.execute("PRAGMA database_list").fetchone()[2])
    )
    original = incremental_delta_module._source_revision_ids
    blocked: list[bool] = []

    def competing_write_then_resolve(
        base_connection: sqlite3.Connection,
        staged_connection: sqlite3.Connection,
        artifacts: object,
    ) -> tuple[int, ...]:
        competing = sqlite3.connect(database_path, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                competing.execute(
                    "UPDATE facts SET value_integer=999 WHERE fact_id=101"
                )
                competing.commit()
            competing.rollback()
            blocked.append(True)
        finally:
            competing.close()
        return original(
            base_connection,
            staged_connection,
            artifacts,
        )

    monkeypatch.setattr(
        incremental_delta_module,
        "_source_revision_ids",
        competing_write_then_resolve,
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        current = logical_database_state(staged)

        assert blocked == [True]
        assert (
            staged.execute(
                "SELECT value_integer FROM facts WHERE fact_id=101"
            ).fetchone()[0]
            == 7
        )
        assert delta.after_database_sha256 == current.database_sha256
    finally:
        staged.close()
        base.close()


def test_receipt_builder_rejects_non_integer_durable_queue_id(
    tmp_path: Path,
) -> None:
    base, staged, delta, plan, event_id = _ready_receipt_fixture(tmp_path)
    staged.execute(
        """
        UPDATE invalidation_queue
        SET downstream_id=1.9
        WHERE event_id=?
          AND downstream_kind='EFFECTIVE_ENTITY'
          AND downstream_id=1
        """,
        (event_id,),
    )
    staged.commit()
    try:
        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_delta_receipt(
                delta,
                plan,
                backend_connection=staged,
                backend_event_id=event_id,
            )

        assert caught.value.gap_code == "BACKEND_EVENT_INVALID"
    finally:
        staged.close()
        base.close()


def test_receipt_builder_rejects_float_event_payload_id(
    tmp_path: Path,
) -> None:
    base, staged, delta, plan, event_id = _ready_receipt_fixture(tmp_path)
    payload = json.loads(
        str(
            staged.execute(
                """
                SELECT payload_json
                FROM invalidation_events
                WHERE event_id=?
                """,
                (event_id,),
            ).fetchone()[0]
        )
    )
    payload["FACT"] = [101.0]
    staged.execute(
        """
        UPDATE invalidation_events
        SET payload_json=?
        WHERE event_id=?
        """,
        (
            json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            event_id,
        ),
    )
    staged.commit()
    try:
        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_delta_receipt(
                delta,
                plan,
                backend_connection=staged,
                backend_event_id=event_id,
            )

        assert caught.value.gap_code == "BACKEND_EVENT_PLAN_MISMATCH"
    finally:
        staged.close()
        base.close()


def test_receipt_builder_rejects_boolean_backend_row_scope_id(
    tmp_path: Path,
) -> None:
    base, staged, diff, result, artifact_root, binding = _delta_fixture(
        tmp_path
    )
    try:
        delta = build_add_only_blueprint_delta(
            base,
            staged,
            source_diff=diff,
            ingest_result=result,
            artifact_root=artifact_root,
            artifact_bindings=(binding,),
            trust_context=TEST_ONLY,
        )
        _materialize_complete_dependency_scope(
            staged,
            source_revision_ids=delta.source_revision_ids,
            entity_ids=delta.entity_ids,
        )
        plan = plan_additive_asset_invalidation(
            staged,
            fact_ids=delta.fact_ids,
            entity_ids=delta.entity_ids,
            source_revision_ids=delta.source_revision_ids,
            actual_write_tables=delta.changed_tables,
        )
        receipts = _successful_backend_receipts(plan)
        attacked = next(
            receipt
            for receipt in receipts
            if receipt["downstreamKind"] == "EFFECTIVE_ENTITY"
        )
        attacked["verification"]["rowScope"]["targetId"] = True
        attacked_body = dict(attacked)
        attacked_body.pop("proof")
        attacked["proof"] = (
            "rebuild-proof://" + _content_sha256(attacked_body)
        )
        event_id = _persist_backend_event(staged, plan, receipts)

        with pytest.raises(AddOnlyDeltaBlockedGap) as caught:
            build_add_only_delta_receipt(
                delta,
                plan,
                backend_connection=staged,
                backend_event_id=event_id,
            )

        assert caught.value.gap_code == (
            "BACKEND_TERMINAL_OUTCOME_UNPROVEN"
        )
    finally:
        staged.close()
        base.close()


def test_validated_receipt_is_recursively_immutable_and_detached(
    tmp_path: Path,
) -> None:
    base, staged, delta, plan, event_id = _ready_receipt_fixture(tmp_path)
    try:
        receipt = build_add_only_delta_receipt(
            delta,
            plan,
            backend_connection=staged,
            backend_event_id=event_id,
        )
        validated = validate_add_only_delta_receipt(
            receipt,
            expected_receipt_sha256=_receipt_sha256(receipt),
        )
        receipt["artifacts"][0]["trustContext"] = "PRODUCTION"
        receipt["invalidationPlan"]["downstream"]["FACT"][0] = 999999

        assert validated["artifacts"][0]["trustContext"] == TEST_ONLY
        assert validated["invalidationPlan"]["downstream"]["FACT"] == (101,)
        with pytest.raises(TypeError):
            validated["artifacts"][0]["trustContext"] = "PRODUCTION"
        with pytest.raises(TypeError):
            validated["invalidationPlan"]["downstream"]["FACT"][0] = 999999
    finally:
        staged.close()
        base.close()
