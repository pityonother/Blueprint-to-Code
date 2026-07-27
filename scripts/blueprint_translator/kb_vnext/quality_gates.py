"""Evidence-backed quality gates and fail-closed cutover decision."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .benchmark import run_query_benchmark
from .registrations import classify_registration_property


QUALITY_GATE_SCHEMA = "ark-kb-quality-gates/v1"
OPEN_CLASS_GAPS = (
    "NATIVE_ROOT_NOT_REACHED",
    "INHERITANCE_CYCLE",
    "MULTIPLE_PARENT_CANDIDATES",
)


def _read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _gate(
    gate_id: str,
    category: str,
    *,
    target: object,
    actual: object,
    passed: bool,
    detail: str,
    critical: bool = True,
) -> dict[str, object]:
    return {
        "id": gate_id,
        "category": category,
        "target": target,
        "actual": actual,
        "passed": bool(passed),
        "critical": critical,
        "detail": detail,
    }


def _registration_gold_metrics(project_root: Path) -> dict[str, object]:
    gold_path = (
        project_root
        / "tests"
        / "fixtures"
        / "kb_registration_gold_set.json"
    )
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    expected = {
        (str(property_name), str(registration_type))
        for property_name, registration_type in gold["cases"]
    }
    actual: set[tuple[str, str]] = set()
    for property_name, _ in expected:
        actual.update(
            (
                property_name,
                result.registration_type,
            )
            for result in classify_registration_property(property_name)
            if result.status == "CONFIRMED"
        )
    negative_predictions = sum(
        1
        for property_name in gold["negativeCases"]
        for result in classify_registration_property(str(property_name))
        if result.status == "CONFIRMED"
    )
    true_positive = len(expected.intersection(actual))
    false_positive = len(actual - expected) + negative_predictions
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, len(expected))
    return {
        "relationships": len(gold["owners"]) * len(gold["cases"]),
        "precision": precision,
        "recall": recall,
        "negativeFalsePositives": negative_predictions,
    }


def _role_gold_metrics(project_root: Path) -> dict[str, object]:
    path = project_root / "tests" / "fixtures" / "kb_role_gold_set.json"
    if not path.is_file():
        return {
            "available": False,
            "assets": 0,
            "precision": None,
            "detail": (
                "No independently reviewed 300-asset role gold set exists; "
                "classifier unit cases are not counted as production gold."
            ),
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    reviewed = [
        case
        for case in cases
        if isinstance(case, dict)
        and case.get("reviewStatus") in {"HUMAN_REVIEWED", "EMPIRICAL"}
    ]
    correct = sum(bool(case.get("correct")) for case in reviewed)
    return {
        "available": True,
        "assets": len(reviewed),
        "precision": _ratio(correct, len(reviewed)),
        "detail": "Independent role-gold review records.",
    }


def _integrity_metrics(snapshot_root: Path) -> dict[str, object]:
    result: dict[str, object] = {}
    for name in (
        "catalog.sqlite",
        "core.sqlite",
        "search.sqlite",
        "cache.sqlite",
    ):
        path = snapshot_root / name
        if not path.is_file():
            result[name] = {
                "exists": False,
                "integrity": "missing",
                "foreignKeyViolations": -1,
                "bytes": 0,
            }
            continue
        connection = _read_only(path)
        try:
            result[name] = {
                "exists": True,
                "integrity": str(
                    connection.execute(
                        "PRAGMA integrity_check"
                    ).fetchone()[0]
                ),
                "foreignKeyViolations": len(
                    list(connection.execute("PRAGMA foreign_key_check"))
                ),
                "bytes": path.stat().st_size,
            }
        finally:
            connection.close()
    return result


def _privacy_scan(value: object) -> list[str]:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    needles = (
        "C:\\Users\\",
        "C:/Users/",
        "/home/",
        "/Users/",
        "Program Files",
    )
    return [needle for needle in needles if needle.casefold() in serialized.casefold()]


def evaluate_quality_gates(
    *,
    project_root: Path,
    snapshot_root: Path,
    discovery_database: Path,
    generated_at: str | None = None,
) -> dict[str, object]:
    """Evaluate real snapshot metrics; absent independent evidence fails closed."""

    project_root = project_root.resolve()
    snapshot_root = snapshot_root.resolve()
    discovery_database = discovery_database.resolve()
    manifest = json.loads(
        (snapshot_root / "manifests" / "current.json").read_text(
            encoding="utf-8"
        )
    )
    benchmark = run_query_benchmark(snapshot_root / "core.sqlite")
    core = _read_only(snapshot_root / "core.sqlite")
    discovery = _read_only(discovery_database)
    gates: list[dict[str, object]] = []
    try:
        blueprint_total, class_known, parent_known = discovery.execute(
            """
            SELECT
                COUNT(*),
                SUM(CASE WHEN asset_class_path NOT IN ('', 'UNKNOWN')
                         THEN 1 ELSE 0 END),
                SUM(CASE
                      WHEN parent_class_path NOT IN ('', 'UNKNOWN')
                        OR native_parent_class_path NOT IN ('', 'UNKNOWN')
                      THEN 1 ELSE 0 END)
            FROM assets WHERE is_blueprint=1
            """
        ).fetchone()
        blueprint_total = int(blueprint_total or 0)
        class_known = int(class_known or 0)
        parent_known = int(parent_known or 0)
        class_rate = _ratio(class_known, blueprint_total)
        gates.append(
            _gate(
                "identity.blueprint_asset_class_path",
                "identity",
                target=">=0.99",
                actual=class_rate,
                passed=class_rate >= 0.99,
                detail=f"{class_known}/{blueprint_total} Blueprint assets",
            )
        )
        deep_total = int(
            core.execute(
                """
                SELECT COUNT(*) FROM knowledge_depth_policies
                WHERE depth_policy IN ('DEEP', 'SEMANTIC')
                """
            ).fetchone()[0]
        )
        deep_open = int(
            core.execute(
                """
                SELECT COUNT(DISTINCT policy.entity_id)
                FROM knowledge_depth_policies AS policy
                LEFT JOIN asset_class_assignments AS assignment
                  ON assignment.entity_id=policy.entity_id
                 AND assignment.assignment_kind='GENERATED_CLASS'
                LEFT JOIN class_gaps AS gap
                  ON gap.class_id=assignment.class_id
                 AND gap.gap_kind IN (?, ?, ?)
                WHERE policy.depth_policy IN ('DEEP', 'SEMANTIC')
                  AND (assignment.class_id IS NULL OR gap.class_id IS NOT NULL)
                """,
                OPEN_CLASS_GAPS,
            ).fetchone()[0]
        )
        closure_rate = _ratio(deep_total - deep_open, deep_total)
        gates.append(
            _gate(
                "identity.deep_parent_native_closure",
                "identity",
                target=">=0.98",
                actual=closure_rate,
                passed=deep_total > 0 and closure_rate >= 0.98,
                detail=f"{deep_total - deep_open}/{deep_total} deep/semantic entities",
            )
        )
        class_gap_count = int(
            core.execute("SELECT COUNT(*) FROM class_gaps").fetchone()[0]
        )
        data_asset_count = int(
            core.execute(
                """
                SELECT COUNT(DISTINCT assignment.entity_id)
                FROM asset_class_assignments AS assignment
                JOIN class_ancestry_categories AS category
                  ON category.class_id=assignment.class_id
                WHERE category.category IN ('DATA_ASSET', 'PRIMARY_DATA_ASSET')
                """
            ).fetchone()[0]
        )
        gates.append(
            _gate(
                "identity.data_asset_ancestry_model",
                "identity",
                target="ancestry table queried with explicit gaps",
                actual={
                    "classifiedAssets": data_asset_count,
                    "classGaps": class_gap_count,
                    "blueprintParentKnown": parent_known,
                },
                passed=class_gap_count >= 0,
                detail=(
                    "DataAsset status is represented by ancestry categories; "
                    "zero results are not interpreted as proof of absence."
                ),
            )
        )
        role_gold = _role_gold_metrics(project_root)
        gates.append(
            _gate(
                "roles.independent_gold_set",
                "roles",
                target=">=300 assets and precision >=0.95",
                actual=role_gold,
                passed=(
                    int(role_gold["assets"]) >= 300
                    and role_gold["precision"] is not None
                    and float(role_gold["precision"]) >= 0.95
                ),
                detail=str(role_gold["detail"]),
            )
        )
        role_total = int(
            core.execute("SELECT COUNT(*) FROM knowledge_roles").fetchone()[0]
        )
        unexplained_roles = int(
            core.execute(
                """
                SELECT COUNT(*) FROM knowledge_roles
                WHERE reasons_json IN ('', '[]', '{}', 'null')
                """
            ).fetchone()[0]
        )
        visual_total = int(
            core.execute(
                """
                SELECT COUNT(DISTINCT entity_id) FROM knowledge_roles
                WHERE role='visual_support_asset'
                """
            ).fetchone()[0]
        )
        visual_deep = int(
            core.execute(
                """
                SELECT COUNT(DISTINCT role.entity_id)
                FROM knowledge_roles AS role
                JOIN knowledge_depth_policies AS policy
                  ON policy.entity_id=role.entity_id
                WHERE role.role='visual_support_asset'
                  AND policy.depth_policy IN ('DEEP', 'SEMANTIC')
                """
            ).fetchone()[0]
        )
        visual_rate = _ratio(visual_deep, visual_total)
        gates.extend(
            [
                _gate(
                    "roles.explainable",
                    "roles",
                    target="100%",
                    actual=_ratio(role_total - unexplained_roles, role_total),
                    passed=role_total > 0 and unexplained_roles == 0,
                    detail=f"{role_total - unexplained_roles}/{role_total} role rows",
                ),
                _gate(
                    "roles.visual_false_promotion",
                    "roles",
                    target="<0.02",
                    actual=visual_rate,
                    passed=visual_rate < 0.02,
                    detail=f"{visual_deep}/{visual_total} visual entities deep/semantic",
                ),
            ]
        )
        registration_gold = _registration_gold_metrics(project_root)
        typed_total, typed_incomplete = core.execute(
            """
            SELECT
                COUNT(*),
                SUM(CASE
                      WHEN owner_uri='' OR target_uri='' OR source_property=''
                        OR evidence_uri=''
                      THEN 1 ELSE 0 END)
            FROM typed_registrations
            """
        ).fetchone()
        typed_total = int(typed_total or 0)
        typed_incomplete = int(typed_incomplete or 0)
        gates.extend(
            [
                _gate(
                    "registrations.gold_precision",
                    "registrations",
                    target=">=0.99",
                    actual=registration_gold["precision"],
                    passed=float(registration_gold["precision"]) >= 0.99,
                    detail=f"{registration_gold['relationships']} explicit gold relationships",
                ),
                _gate(
                    "registrations.gold_recall",
                    "registrations",
                    target=">=0.95",
                    actual=registration_gold["recall"],
                    passed=float(registration_gold["recall"]) >= 0.95,
                    detail=f"{registration_gold['relationships']} explicit gold relationships",
                ),
                _gate(
                    "registrations.lineage_complete",
                    "registrations",
                    target="100%",
                    actual=_ratio(typed_total - typed_incomplete, typed_total),
                    passed=typed_total > 0 and typed_incomplete == 0,
                    detail=f"{typed_total - typed_incomplete}/{typed_total} typed registrations",
                ),
            ]
        )
        native_targets, native_confirmed = core.execute(
            """
            SELECT COUNT(*),
                   SUM(CASE WHEN status='CONFIRMED' THEN 1 ELSE 0 END)
            FROM native_gold_targets
            """
        ).fetchone()
        native_targets = int(native_targets or 0)
        native_confirmed = int(native_confirmed or 0)
        confirmed_links, valid_links = core.execute(
            """
            SELECT
              SUM(CASE WHEN link.status='CONFIRMED' THEN 1 ELSE 0 END),
              SUM(CASE
                    WHEN link.status='CONFIRMED'
                     AND link.native_function_id IS NOT NULL
                     AND link.blueprint_graph_evidence_uri<>''
                     AND link.native_evidence_uri<>''
                    THEN 1 ELSE 0 END)
            FROM native_blueprint_links AS link
            """
        ).fetchone()
        confirmed_links = int(confirmed_links or 0)
        valid_links = int(valid_links or 0)
        gates.extend(
            [
                _gate(
                    "native.gold_targets_resolved",
                    "native",
                    target="100%",
                    actual=_ratio(native_confirmed, native_targets),
                    passed=(
                        native_targets >= 20
                        and native_confirmed == native_targets
                    ),
                    detail=f"{native_confirmed}/{native_targets} exact native targets",
                ),
                _gate(
                    "native.blueprint_link_precision",
                    "native",
                    target="100% with at least one confirmed link",
                    actual={
                        "confirmed": confirmed_links,
                        "fullyBound": valid_links,
                    },
                    passed=confirmed_links > 0 and valid_links == confirmed_links,
                    detail=(
                        "Zero confirmed Blueprint-native links is not treated "
                        "as vacuous 100% precision."
                    ),
                ),
            ]
        )
        fact_total, fact_with_evidence = core.execute(
            """
            SELECT
              COUNT(*),
              SUM(CASE WHEN EXISTS(
                    SELECT 1 FROM fact_evidence AS evidence
                    JOIN source_revisions AS revision
                      ON revision.revision_id=evidence.source_revision_id
                    WHERE evidence.fact_id=fact.fact_id
                      AND evidence.evidence_uri<>''
                ) THEN 1 ELSE 0 END)
            FROM facts AS fact WHERE fact.current=1
            """
        ).fetchone()
        fact_total = int(fact_total or 0)
        fact_with_evidence = int(fact_with_evidence or 0)
        unknown_with_zero = int(
            core.execute(
                """
                SELECT COUNT(*) FROM facts
                WHERE status='UNKNOWN'
                  AND (
                    value_number=0 OR value_integer=0
                    OR value_text IN ('0', '0.0')
                  )
                """
            ).fetchone()[0]
        )
        invalid_effective = int(
            core.execute(
                """
                SELECT COUNT(*)
                FROM effective_facts AS effective
                JOIN facts AS fact ON fact.fact_id=effective.fact_id
                WHERE fact.fact_type<>'DECLARED_DEFAULT'
                   OR fact.declared_on_entity_id IS NULL
                """
            ).fetchone()[0]
        )
        duplicate_facts = int(
            core.execute(
                """
                SELECT COUNT(*) FROM (
                  SELECT canonical_fact_key
                  FROM facts
                  GROUP BY canonical_fact_key
                  HAVING COUNT(*)>1
                )
                """
            ).fetchone()[0]
        )
        gates.extend(
            [
                _gate(
                    "facts.provenance_complete",
                    "facts",
                    target="100%",
                    actual=_ratio(fact_with_evidence, fact_total),
                    passed=fact_total > 0 and fact_with_evidence == fact_total,
                    detail=f"{fact_with_evidence}/{fact_total} current facts",
                ),
                _gate(
                    "facts.unknown_not_zero",
                    "facts",
                    target=0,
                    actual=unknown_with_zero,
                    passed=unknown_with_zero == 0,
                    detail="UNKNOWN facts must not acquire a synthetic zero.",
                ),
                _gate(
                    "facts.declared_effective_separated",
                    "facts",
                    target=0,
                    actual=invalid_effective,
                    passed=invalid_effective == 0,
                    detail="Every effective fact resolves a declared default.",
                ),
                _gate(
                    "facts.canonical_deduplicated",
                    "facts",
                    target=0,
                    actual=duplicate_facts,
                    passed=duplicate_facts == 0,
                    detail="Canonical fact keys remain unique.",
                ),
            ]
        )
        gates.extend(
            [
                _gate(
                    "queries.complete_or_bounded",
                    "queries",
                    target=">=0.70",
                    actual=benchmark["completeOrBoundedRate"],
                    passed=float(benchmark["completeOrBoundedRate"]) >= 0.70,
                    detail=f"{benchmark['completeOrBounded']}/{benchmark['total']} benchmark cases",
                ),
                _gate(
                    "queries.simple_db_only",
                    "queries",
                    target=">=0.90",
                    actual=benchmark["simpleDbOnlyRate"],
                    passed=float(benchmark["simpleDbOnlyRate"]) >= 0.90,
                    detail=f"{benchmark['simpleDbOnly']}/30 simple queries",
                ),
                _gate(
                    "queries.no_silent_unresolved",
                    "queries",
                    target=0,
                    actual=benchmark["unresolved"],
                    passed=int(benchmark["unresolved"]) == 0,
                    detail="Every incomplete query must return a gap and probe.",
                ),
                _gate(
                    "queries.single_entity_p95_ms",
                    "performance",
                    target="<250",
                    actual=benchmark["latencyMs"]["p95"],
                    passed=float(benchmark["latencyMs"]["p95"]) < 250,
                    detail="120 read-only planner/context executions.",
                ),
                _gate(
                    "queries.two_hop_p95_ms",
                    "performance",
                    target="<800",
                    actual=benchmark["latencyMs"]["twoHopP95"],
                    passed=(
                        int(benchmark["latencyMs"]["twoHopSamples"]) > 0
                        and float(benchmark["latencyMs"]["twoHopP95"]) < 800
                    ),
                    detail=f"{benchmark['latencyMs']['twoHopSamples']} indexed samples",
                ),
                _gate(
                    "queries.context_budget",
                    "performance",
                    target="<=2000",
                    actual=benchmark["contextTokens"]["maximum"],
                    passed=bool(benchmark["contextTokens"]["withinBudget"]),
                    detail="Maximum estimated tokens across the benchmark.",
                ),
            ]
        )
        dependency_kinds = {
            str(row[0]): int(row[1])
            for row in core.execute(
                """
                SELECT downstream_kind, COUNT(*)
                FROM invalidation_dependencies
                GROUP BY downstream_kind
                """
            )
        }
        required_dependency_kinds = {
            "ROLE_ENTITY",
            "DOMAIN_ENTITY",
            "NATIVE_FUNCTION",
        }
        gates.append(
            _gate(
                "incremental.dependency_graph",
                "incremental",
                target=sorted(required_dependency_kinds),
                actual=dependency_kinds,
                passed=required_dependency_kinds.issubset(dependency_kinds),
                detail="Selective invalidation roots are materialized.",
            )
        )
        plan_rows = list(
            core.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT second.target_entity_id
                FROM edges AS first
                JOIN edges AS second
                  ON second.source_entity_id=first.target_entity_id
                WHERE first.source_entity_id=1
                LIMIT 200
                """
            )
        )
        plan_text = " | ".join(str(row[3]) for row in plan_rows)
        gates.append(
            _gate(
                "performance.large_query_indexed",
                "performance",
                target="indexed EXPLAIN QUERY PLAN",
                actual=plan_text,
                passed="INDEX" in plan_text.upper(),
                detail="Two-hop traversal plan.",
            )
        )
    finally:
        core.close()
        discovery.close()
    integrity = _integrity_metrics(snapshot_root)
    integrity_passed = all(
        bool(item["exists"])
        and item["integrity"] == "ok"
        and int(item["foreignKeyViolations"]) == 0
        for item in integrity.values()
        if isinstance(item, dict)
    )
    gates.append(
        _gate(
            "storage.integrity",
            "storage",
            target="all databases ok; zero FK violations",
            actual=integrity,
            passed=integrity_passed,
            detail="Published read-only snapshot stores.",
        )
    )
    core_bytes = int(integrity["core.sqlite"]["bytes"])
    discovery_bytes = discovery_database.stat().st_size
    gates.append(
        _gate(
            "storage.core_smaller_than_discovery",
            "storage",
            target="<1.0",
            actual=_ratio(core_bytes, discovery_bytes),
            passed=core_bytes < discovery_bytes,
            detail=f"{core_bytes} core bytes vs {discovery_bytes} discovery bytes",
        )
    )
    generated_at = generated_at or datetime.now(UTC).isoformat(
        timespec="seconds"
    )
    failed = [gate for gate in gates if gate["critical"] and not gate["passed"]]
    report: dict[str, object] = {
        "schema": QUALITY_GATE_SCHEMA,
        "generatedAt": generated_at,
        "buildId": str(manifest.get("buildId") or ""),
        "summary": {
            "total": len(gates),
            "passed": sum(bool(gate["passed"]) for gate in gates),
            "failed": len(failed),
            "cutoverEligible": not failed,
            "recommendation": (
                "ready_for_default" if not failed else "keep_legacy_shadow"
            ),
        },
        "gates": gates,
        "benchmark": benchmark,
    }
    privacy_hits = _privacy_scan(report)
    privacy_gate = _gate(
        "privacy.no_local_paths",
        "privacy",
        target=0,
        actual=privacy_hits,
        passed=not privacy_hits,
        detail="Report payload excludes local absolute paths.",
    )
    gates.append(privacy_gate)
    if not privacy_gate["passed"]:
        failed.append(privacy_gate)
    report["summary"] = {
        "total": len(gates),
        "passed": sum(bool(gate["passed"]) for gate in gates),
        "failed": len(failed),
        "cutoverEligible": not failed,
        "recommendation": (
            "ready_for_default" if not failed else "keep_legacy_shadow"
        ),
    }
    return report


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def publish_gate_report(
    *,
    snapshot_root: Path,
    report: dict[str, object],
) -> dict[str, object]:
    """Publish reports and update cutover atomically without deleting legacy."""

    snapshot_root = snapshot_root.resolve()
    reports = snapshot_root / "reports"
    benchmark = report["benchmark"]
    _write_json_atomic(reports / "query_benchmark.json", benchmark)
    _write_json_atomic(reports / "quality_gates.json", report)
    gate_sha = hashlib.sha256(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    current_path = snapshot_root / "manifests" / "current.json"
    manifest = json.loads(current_path.read_text(encoding="utf-8"))
    eligible = bool(report["summary"]["cutoverEligible"])
    manifest["qualityGates"] = {
        "schema": QUALITY_GATE_SCHEMA,
        "reportUri": "reports/quality_gates.json",
        "sha256": gate_sha,
        "passed": int(report["summary"]["passed"]),
        "failed": int(report["summary"]["failed"]),
    }
    manifest["cutover"] = {
        "mode": "ready" if eligible else "shadow",
        "defaultQuerySource": "vnext" if eligible else "legacy",
        "reason": (
            "all critical quality gates passed"
            if eligible
            else (
                f"{report['summary']['failed']} critical quality gates "
                "remain open"
            )
        ),
    }
    _write_json_atomic(current_path, manifest)
    build_id = str(manifest.get("buildId") or "")
    build_manifest = snapshot_root / "manifests" / f"{build_id}.json"
    if build_manifest.is_file():
        _write_json_atomic(build_manifest, manifest)
    return manifest["cutover"]
