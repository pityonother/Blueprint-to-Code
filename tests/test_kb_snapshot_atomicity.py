from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext import snapshot as snapshot_module  # noqa: E402
from blueprint_translator.kb_vnext import benchmark as benchmark_module  # noqa: E402
from blueprint_translator.kb_vnext.projections import (  # noqa: E402
    DOMAIN_PROJECTIONS,
    PROJECTION_SCHEMA_SQL,
    PROJECTION_SCHEMA_VERSION,
    compute_projection_artifact_content_digest,
)
from blueprint_translator.kb_vnext.quality_contract import (  # noqa: E402
    QUALITY_GATE_CONTRACT,
)
from blueprint_translator.kb_vnext.incremental_publisher import (  # noqa: E402
    IncrementalPublicationNotReplaced,
    IncrementalPublicationUncertain,
    publish_incremental_shadow_snapshot,
    reseal_incremental_snapshot_candidate,
    seal_incremental_narrow_gate_report,
    verify_incremental_shadow_publication,
)
from blueprint_translator.kb_vnext.narrow_gates import (  # noqa: E402
    NARROW_GATE_CHECK_IDS,
    NarrowGateObservation,
    UpdateBaseline as NarrowGateUpdateBaseline,
    build_narrow_gate_diagnostic_report,
)
from blueprint_translator.kb_vnext.narrow_gate_runner import (  # noqa: E402
    ProductionNarrowGateInputs,
    run_production_narrow_gates,
)
from blueprint_translator.kb_vnext.schema_capabilities import (  # noqa: E402
    CORE_SCHEMA_VERSION,
)
from blueprint_translator.kb_vnext.source_manifest import (  # noqa: E402
    SourceManifest,
    SourceRevision,
    source_id,
    source_manifest_binding,
    source_manifest_from_binding,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    CACHE_SCHEMA_VERSION,
    CATALOG_SCHEMA_VERSION,
    SEARCH_SCHEMA_VERSION,
)


DATABASE_NAMES = (
    "catalog.sqlite",
    "core.sqlite",
    "search.sqlite",
    "cache.sqlite",
)
ONTOLOGY_VERSION = (
    "ark-domains/v1|ark-roles/v1|ark-edge-types/v2|ark-fact-types/v2"
)
SOURCE_INPUTS = {
    "discovery": "d" * 64,
    "captures": "c" * 64,
    "classHierarchyContract": "b" * 64,
    "semanticProducerContract": "e" * 64,
    "legacy": "1" * 64,
    "ontology": "2" * 64,
    "benchmarkGold": "3" * 64,
    "qualityGold": "4" * 64,
    "mapEvidence": "5" * 64,
    "nativeEvidence": "6" * 64,
}


def _fixture_build_id(time_text: str) -> str:
    return snapshot_module.snapshot_build_id(
        f"2026-07-28T{time_text}+00:00",
        snapshot_module.semantic_inputs_sha256(SOURCE_INPUTS),
    )


def _generated_at(build_id: str) -> str:
    parsed = datetime.strptime(
        build_id.split("-", 1)[0],
        "%Y%m%dT%H%M%S",
    ).replace(tzinfo=UTC)
    return parsed.isoformat(timespec="seconds")


def _valid_fixture_burn_in_attestation() -> dict[str, object]:
    scenarios = {
        "blueprintModified": True,
        "blueprintAdded": True,
        "blueprintDeleted": True,
        "registrationTargetChanged": True,
        "classParentChanged": True,
        "nativeEvidenceUpdated": True,
        "runtimeSummaryUpdated": True,
        "workerCrash": True,
        "narrowGateFailure": True,
        "pointerPreSwapCrash": True,
        "concurrentReaders": True,
        "unchangedCacheHit": True,
    }
    return {
        "schema": "ark-kb-burn-in-attestation/v1",
        "policyVersion": "ark-kb-burn-in-policy/v1",
        "status": "PASSED",
        "attestedAt": "2026-07-29T00:00:00Z",
        "toolVersion": "fixture-only/v1",
        "review": {
            "reviewerType": "HUMAN_OPERATOR",
            "reviewerId": "fixture-human-reviewer",
            "reviewedAt": "2026-07-29T00:00:00Z",
            "decision": "APPROVED",
        },
        "sealedSnapshots": [
            {
                "buildId": f"fixture-pass-{index}",
                "qualityReportSha256": str(index) * 64,
                "passedAt": f"2026-07-2{index}T00:00:00Z",
                "qualityReportCutoverEligible": True,
                "sealedInSnapshotManifest": True,
            }
            for index in range(1, 4)
        ],
        "legacyVnextDiffDisposition": {
            "complete": True,
            "undispositioned": 0,
            "wrongAnswers": 0,
            "staleLeaks": 0,
            "candidateCompletions": 0,
        },
        "rollbackDrill": {
            "passed": True,
            "fromBuildId": "fixture-pass-3",
            "toBuildId": "fixture-pass-2",
            "completedAt": "2026-07-29T00:00:00Z",
        },
        "concurrentReaderDrill": {
            "passed": True,
            "mixedBuildObservations": 0,
            "completedAt": "2026-07-29T00:00:00Z",
        },
        "incrementalProduction": {
            "passed": True,
            "scenarios": scenarios,
        },
    }


def _staging(
    root: Path,
    build_id: str,
    *,
    sqlite_payload: bool = False,
) -> tuple[Path, dict[str, object]]:
    del sqlite_payload
    staging = root / ".build" / f"{build_id}.fixture"
    staging.mkdir(parents=True)
    generated_at = _generated_at(build_id)
    source_sha256 = snapshot_module.semantic_inputs_sha256(SOURCE_INPUTS)
    main_metadata = {
        "catalog.sqlite": (
            CATALOG_SCHEMA_VERSION,
            SOURCE_INPUTS["discovery"],
        ),
        "core.sqlite": (
            CORE_SCHEMA_VERSION,
            SOURCE_INPUTS["discovery"],
        ),
        "search.sqlite": (SEARCH_SCHEMA_VERSION, source_sha256),
        "cache.sqlite": (CACHE_SCHEMA_VERSION, source_sha256),
    }
    main_schemas = {
        "catalog.sqlite": snapshot_module.FULL_CATALOG_SCHEMA_SQL,
        "core.sqlite": snapshot_module.FULL_CORE_SCHEMA_SQL,
        "search.sqlite": snapshot_module.SEARCH_SCHEMA_SQL,
        "cache.sqlite": snapshot_module.CACHE_SCHEMA_SQL,
    }
    for name in DATABASE_NAMES:
        path = staging / name
        connection = sqlite3.connect(path)
        try:
            connection.executescript(main_schemas[name])
            metadata_rows = [
                ("schema_version", main_metadata[name][0]),
                ("source_fingerprint", main_metadata[name][1]),
                ("generated_at", generated_at),
                ("snapshot_build_id", build_id),
                ("snapshot_source_fingerprint", source_sha256),
            ]
            if name == "core.sqlite":
                metadata_rows.extend(
                    [
                        (
                            "runtime_health_schema",
                            snapshot_module.RUNTIME_HEALTH_SCHEMA,
                        ),
                        ("runtime_health_active_stale_sources", "0"),
                        ("runtime_health_build_id", build_id),
                        (
                            "runtime_health_source_sha256",
                            source_sha256,
                        ),
                    ]
                )
            connection.executemany(
                "INSERT INTO metadata VALUES(?, ?)",
                metadata_rows,
            )
            connection.commit()
        finally:
            connection.close()
    exports = staging / "domain_exports"
    exports.mkdir()
    review_config_sha256 = "7" * 64
    source_revision_set_hash = "8" * 64
    projection_metrics: dict[str, dict[str, object]] = {}
    for projection_name in DOMAIN_PROJECTIONS:
        export_path = exports / f"{projection_name}.sqlite"
        connection = sqlite3.connect(export_path)
        try:
            connection.executescript(PROJECTION_SCHEMA_SQL)
            content_digest = compute_projection_artifact_content_digest(
                connection
            )
            connection.executemany(
                "INSERT INTO metadata VALUES(?, ?)",
                [
                    ("schema_version", PROJECTION_SCHEMA_VERSION),
                    ("projection_name", projection_name),
                    ("projection_version", "v2"),
                    (
                        "source_revision_set_hash",
                        source_revision_set_hash,
                    ),
                    ("ontology_version", ONTOLOGY_VERSION),
                    ("built_at", generated_at),
                    ("truth_source", "core.sqlite"),
                    ("review_version", "fixture-v1"),
                    ("review_status", "FIXTURE_EMPTY"),
                    (
                        "review_config_sha256",
                        review_config_sha256,
                    ),
                    ("content_digest", content_digest),
                    ("snapshot_build_id", build_id),
                    (
                        "snapshot_source_fingerprint",
                        source_sha256,
                    ),
                ],
            )
            connection.commit()
        finally:
            connection.close()
        metrics = snapshot_module.database_metrics(export_path)
        metrics.update(
            {
                "schemaVersion": PROJECTION_SCHEMA_VERSION,
                "projectionVersion": "v2",
                "ontologyVersion": ONTOLOGY_VERSION,
                "contentDigest": content_digest,
                "reviewConfigSha256": review_config_sha256,
                "sourceRevisionSetHash": source_revision_set_hash,
                "validationStatus": "VALID",
            }
        )
        projection_metrics[
            f"domain_exports/{projection_name}.sqlite"
        ] = metrics
    core = sqlite3.connect(staging / "core.sqlite")
    try:
        core.executemany(
            """
            INSERT INTO projection_runs(
                projection_name,
                projection_version,
                source_revision_set_hash,
                ontology_version,
                built_at,
                row_count,
                validation_status
            ) VALUES (?, 'v2', ?, ?, ?, 0, 'VALID')
            """,
            [
                (
                    projection_name,
                    source_revision_set_hash,
                    ONTOLOGY_VERSION,
                    generated_at,
                )
                for projection_name in DOMAIN_PROJECTIONS
            ],
        )
        core.commit()
    finally:
        core.close()

    source_manifest = SourceManifest(
        entries=tuple(
            SourceRevision(
                source_id=source_id(
                    "SEMANTIC_INPUT",
                    f"semantic-input://{key}",
                ),
                source_kind="SEMANTIC_INPUT",
                source_uri=f"semantic-input://{key}",
                fingerprint=fingerprint,
            )
            for key, fingerprint in SOURCE_INPUTS.items()
        )
        + (
            SourceRevision(
                source_id=source_id(
                    "SEMANTIC_INPUT",
                    "semantic-input://runtimeObservations",
                ),
                source_kind="SEMANTIC_INPUT",
                source_uri="semantic-input://runtimeObservations",
                fingerprint="9" * 64,
            ),
        ),
        generated_at=generated_at,
    )
    benchmark = {"schema": "ark-kb-query-benchmark/v2", "queries": []}
    gates = [
        {
            "id": gate_id,
            "category": category,
            "critical": critical,
            "passed": False,
            "target": True,
            "actual": False,
            "detail": "Atomicity fixture intentionally remains shadow.",
        }
        for gate_id, category, critical in sorted(QUALITY_GATE_CONTRACT)
    ]
    failed_count = len(gates)
    report = {
        "schema": "ark-kb-quality-gates/v1",
        "buildId": build_id,
        "summary": {
            "total": len(gates),
            "passed": 0,
            "failed": failed_count,
            "cutoverEligible": False,
            "recommendation": "keep_legacy_shadow",
        },
        "gates": gates,
        "benchmark": benchmark,
    }
    reports = staging / "reports"
    snapshot_module._write_json(reports / "quality_gates.json", report)
    snapshot_module._write_json(reports / "query_benchmark.json", benchmark)
    databases = {
        name: snapshot_module.database_metrics(staging / name)
        for name in DATABASE_NAMES
    }
    databases.update(projection_metrics)
    manifest = {
        "schema": snapshot_module.SNAPSHOT_SCHEMA,
        "buildId": build_id,
        "generatedAt": generated_at,
        "source": {
            "kind": snapshot_module.SNAPSHOT_SOURCE_KIND,
            "uri": snapshot_module.SNAPSHOT_SOURCE_URI,
            "sha256": source_sha256,
            "inputs": SOURCE_INPUTS,
        },
        "ontologyVersion": ONTOLOGY_VERSION,
        "incrementalUpdate": source_manifest_binding(source_manifest),
        "databases": databases,
        "runtimeHealth": {
            "schema": snapshot_module.RUNTIME_HEALTH_SCHEMA,
            "buildId": build_id,
            "sourceSha256": source_sha256,
            "activeStaleSources": 0,
            "sealedInSnapshotManifest": True,
        },
        "qualityGates": {
            "schema": report["schema"],
            "reportUri": "reports/quality_gates.json",
            "sha256": snapshot_module._sha256_file(
                reports / "quality_gates.json"
            ),
            "benchmarkUri": "reports/query_benchmark.json",
            "benchmarkSha256": snapshot_module._sha256_file(
                reports / "query_benchmark.json"
            ),
            "passed": 0,
            "failed": failed_count,
            "cutoverEligible": False,
            "sealedInSnapshotManifest": True,
        },
        "cutover": {
            "mode": "shadow",
            "defaultQuerySource": "legacy",
        },
    }
    return staging, manifest


def _promote_snapshot(
    *,
    staging: Path,
    output_dir: Path,
    manifest: dict[str, object],
    expected_current_pointer: (
        snapshot_module.CurrentPointerBaseline | None
    ) = None,
) -> dict[str, object]:
    baseline = (
        expected_current_pointer
        if expected_current_pointer is not None
        else snapshot_module.read_current_pointer_baseline(output_dir)
    )
    return snapshot_module._promote_snapshot(
        staging=staging,
        output_dir=output_dir,
        manifest=manifest,
        expected_current_pointer=baseline,
    )


def _attach_query_diagnostics(
    staging: Path,
    manifest: dict[str, object],
) -> None:
    build_id = str(manifest["buildId"])
    corpus_sha256 = SOURCE_INPUTS["benchmarkGold"]
    case_result = {
        "schema": benchmark_module.QUERY_CASE_RESULT_SCHEMA,
        "caseId": "fixture-query-001",
        "category": "FACT",
        "domain": "item_use",
        "failureClass": "PASS",
        "failureClasses": [],
        "protocolViolations": [],
        "leakage": {
            "stale": False,
            "candidate": False,
            "legacy": False,
        },
        "latencySpansMs": {"total": 1.0},
    }
    case_bytes = benchmark_module.query_case_results_jsonl_bytes(
        [case_result]
    )
    matrix = benchmark_module.build_query_failure_matrix(
        [case_result],
        build_id=build_id,
        corpus_sha256=corpus_sha256,
    )
    matrix_bytes = (
        benchmark_module.query_failure_matrix_json_bytes(matrix)
    )
    benchmark = {
        "schema": "ark-kb-query-benchmark/v2",
        "total": 1,
        "goldSet": {"sha256": corpus_sha256},
        "results": [case_result],
        "diagnosticArtifacts": {
            "schema": benchmark_module.QUERY_DIAGNOSTICS_SCHEMA,
            "buildId": build_id,
            "buildBinding": "SNAPSHOT_METADATA",
            "corpusSha256": corpus_sha256,
            "caseResults": {
                "schema": benchmark_module.QUERY_CASE_RESULT_SCHEMA,
                "uri": "reports/query_case_results.jsonl",
                "sha256": hashlib.sha256(case_bytes).hexdigest(),
                "count": 1,
            },
            "failureMatrix": {
                "schema": benchmark_module.QUERY_FAILURE_MATRIX_SCHEMA,
                "uri": "reports/query_failure_matrix.json",
                "sha256": hashlib.sha256(matrix_bytes).hexdigest(),
                "caseCount": 1,
            },
        },
    }
    reports = staging / "reports"
    report_path = reports / "quality_gates.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["benchmark"] = benchmark
    snapshot_module._write_json(report_path, report)
    benchmark_path = reports / "query_benchmark.json"
    snapshot_module._write_json(benchmark_path, benchmark)
    case_path = reports / "query_case_results.jsonl"
    case_path.write_bytes(case_bytes)
    matrix_path = reports / "query_failure_matrix.json"
    matrix_path.write_bytes(matrix_bytes)
    quality = dict(manifest["qualityGates"])
    quality.update(
        {
            "sha256": snapshot_module._sha256_file(report_path),
            "benchmarkSha256": snapshot_module._sha256_file(
                benchmark_path
            ),
            "diagnosticsSchema": (
                benchmark_module.QUERY_DIAGNOSTICS_SCHEMA
            ),
            "caseResultsUri": (
                "reports/query_case_results.jsonl"
            ),
            "caseResultsSha256": snapshot_module._sha256_file(
                case_path
            ),
            "failureMatrixUri": (
                "reports/query_failure_matrix.json"
            ),
            "failureMatrixSha256": snapshot_module._sha256_file(
                matrix_path
            ),
        }
    )
    quality["diagnosticsBindingSha256"] = (
        snapshot_module._query_diagnostics_binding_sha256(
            build_id=build_id,
            corpus_sha256=corpus_sha256,
            quality_report_sha256=str(quality["sha256"]),
            benchmark_report_sha256=str(
                quality["benchmarkSha256"]
            ),
            case_results_sha256=str(
                quality["caseResultsSha256"]
            ),
            failure_matrix_sha256=str(
                quality["failureMatrixSha256"]
            ),
        )
    )
    manifest["qualityGates"] = quality


class ImmutableSnapshotPublicationTests(unittest.TestCase):
    def test_incremental_publisher_distinguishes_pre_and_post_switch_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            staging = (
                root
                / ".incremental-staging"
                / ("c" * 32)
                / "snapshot"
            )
            staging.mkdir(parents=True)
            (root / "snapshots").mkdir()
            pointer_bytes = json.dumps(
                {
                    "buildId": "base-fixture",
                    "snapshotRelativePath": "snapshots/base-fixture",
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            (root / "current.json").write_bytes(pointer_bytes)
            expected_pointer = snapshot_module.read_current_pointer_baseline(root)
            baseline = NarrowGateUpdateBaseline(
                base_build_id="base-fixture",
                base_pointer_sha256=expected_pointer.pointer_sha256,
                base_manifest_sha256="2" * 64,
                base_source_manifest_fingerprint="3" * 64,
                candidate_source_manifest_fingerprint="4" * 64,
                source_diff_sha256="5" * 64,
                delta_receipt_sha256="6" * 64,
            )
            source_manifest = SourceManifest(
                entries=(),
                generated_at="2026-07-28T02:03:04+00:00",
            )

            for mutate_pointer, expected_error in (
                (False, IncrementalPublicationNotReplaced),
                (True, IncrementalPublicationUncertain),
            ):
                (root / "current.json").write_bytes(pointer_bytes)

                def fail_after_boundary(**kwargs: object) -> dict[str, object]:
                    del kwargs
                    if mutate_pointer:
                        (root / "current.json").write_bytes(b"{}")
                    raise RuntimeError("fixture crash")

                with (
                    patch(
                        "blueprint_translator.kb_vnext.incremental_publisher."
                        "_validate_incremental_binding"
                    ),
                    patch(
                        "blueprint_translator.kb_vnext.incremental_publisher."
                        "_validate_staged_snapshot_for_promotion"
                    ),
                    patch(
                        "blueprint_translator.kb_vnext.incremental_publisher."
                        "_promote_snapshot",
                        side_effect=fail_after_boundary,
                    ),
                ):
                    with self.assertRaises(expected_error):
                        publish_incremental_shadow_snapshot(
                            staging=staging,
                            output_dir=root,
                            manifest={"buildId": "candidate-fixture"},
                            expected_current_pointer=expected_pointer,
                            expected_current_manifest_sha256="2" * 64,
                            expected_update_baseline=baseline,
                            expected_candidate_source_manifest=(
                                source_manifest
                            ),
                        )
                if not mutate_pointer:
                    self.assertEqual(
                        (root / "current.json").read_bytes(),
                        pointer_bytes,
                    )

    def test_production_narrow_runner_computes_fixed_11_from_candidate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            build_id = _fixture_build_id("02:03:04")
            created, manifest = _staging(root, build_id)
            temporary_root = root / ".incremental-staging" / ("b" * 32)
            staging = temporary_root / "snapshot"
            temporary_root.mkdir(parents=True, exist_ok=True)
            created.rename(staging)
            core_path = staging / "core.sqlite"
            with closing(sqlite3.connect(core_path)) as core:
                core.execute(
                    """
                    INSERT INTO source_revisions VALUES (
                        1, 'discovery', 'discovery://fixture', ?,
                        'fixture/v1', 'fixture/v1', ?, 'FRESH'
                    )
                    """,
                    ("a" * 64, manifest["generatedAt"]),
                )
                core.execute(
                    """
                    INSERT INTO entities(
                        entity_id, canonical_uri, entity_kind, status,
                        confidence, display_name, internal_name
                    ) VALUES (
                        1, '/Game/Test/Added.Added', 'BLUEPRINT_ASSET',
                        'CONFIRMED', 'HIGH', 'Added', 'Added'
                    )
                    """
                )
                core.commit()
            with closing(sqlite3.connect(staging / "search.sqlite")) as search:
                search.execute(
                    "INSERT INTO entity_search_meta VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        1,
                        "/Game/Test/Added.Added",
                        "BLUEPRINT_ASSET",
                        "Added",
                        "Added",
                        "CONFIRMED",
                    ),
                )
                search.execute(
                    "INSERT INTO entities_fts VALUES (?, ?, ?, ?, '')",
                    (1, "/Game/Test/Added.Added", "Added", "Added"),
                )
                search.commit()
            with closing(sqlite3.connect(staging / "cache.sqlite")) as cache:
                cache.execute(
                    "INSERT OR REPLACE INTO metadata VALUES ('disposable', 'true')"
                )
                cache.commit()
            manifest["databases"]["core.sqlite"] = (
                snapshot_module.database_metrics(core_path)
            )
            manifest["databases"]["search.sqlite"] = (
                snapshot_module.database_metrics(staging / "search.sqlite")
            )
            manifest["databases"]["cache.sqlite"] = (
                snapshot_module.database_metrics(staging / "cache.sqlite")
            )
            snapshot_module._write_json(staging / "manifest.json", manifest)
            base_snapshot = root / "base-fixture"
            base_snapshot.mkdir()
            shutil.copy2(staging / "search.sqlite", base_snapshot / "search.sqlite")
            candidate_source_manifest = source_manifest_from_binding(
                manifest["incrementalUpdate"]
            )
            fake_baseline = SimpleNamespace(
                base_build_id="base-fixture",
                base_pointer_sha256="1" * 64,
                base_manifest_sha256="2" * 64,
                base_source_manifest_fingerprint="3" * 64,
                candidate_source_manifest_fingerprint=(
                    candidate_source_manifest.fingerprint
                ),
                source_diff_sha256="4" * 64,
                candidate_source_manifest=candidate_source_manifest,
                current_snapshot=SimpleNamespace(snapshot_dir=base_snapshot),
            )
            manifest["previousSnapshot"] = {
                "buildId": fake_baseline.base_build_id,
                "manifestSha256": fake_baseline.base_manifest_sha256,
            }
            inspection = SimpleNamespace(
                payload=lambda: {
                    "status": "FOUNDATION_VERIFIED",
                    "baseBindingVerified": True,
                    "blockedGapCount": 0,
                }
            )
            worker = {
                "attempted": 12,
                "succeeded": 12,
                "failed": 0,
                "blocked_gap": 0,
                "remaining_pending": 0,
                "remaining_running": 0,
                "drained": True,
                "outcomes": tuple(
                    {
                        "status": "SUCCEEDED",
                        "proof": f"rebuild-proof://{index:064x}",
                    }
                    for index in range(1, 13)
                ),
            }

            with (
                patch(
                    "blueprint_translator.kb_vnext.narrow_gate_runner."
                    "validate_final_source_manifest"
                ),
                patch(
                    "blueprint_translator.kb_vnext.narrow_gate_runner."
                    "inspect_base_bound_prepublication_delta_receipt",
                    return_value=inspection,
                ),
                patch(
                    "blueprint_translator.kb_vnext.narrow_gate_runner."
                    "validate_update_baseline_identity"
                ),
            ):
                result = run_production_narrow_gates(
                    ProductionNarrowGateInputs(
                        baseline=fake_baseline,
                        staged_snapshot=SimpleNamespace(
                            snapshot_dir=staging,
                            temporary_root=temporary_root,
                        ),
                        frozen_input=object(),
                        candidate_source_manifest=(
                            candidate_source_manifest
                        ),
                        candidate_manifest=manifest,
                        delta_receipt_bytes=b"fixture",
                        delta_receipt_sha256="5" * 64,
                        worker_report=worker,
                        changed_source_revision_ids=(1,),
                        affected_entity_ids=(1,),
                    )
                )

            self.assertEqual(len(result.observations), 11)
            self.assertEqual(
                tuple(value.gate_id for value in result.observations),
                NARROW_GATE_CHECK_IDS,
            )
            self.assertEqual(
                result.report["summary"],
                {"total": 11, "passed": 11, "failed": 0},
            )
            self.assertEqual(
                hashlib.sha256(result.report_bytes).hexdigest(),
                result.report_sha256,
            )

    def test_incremental_shadow_publisher_seals_report_and_cas_swaps(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            base_id = _fixture_build_id("01:02:03")
            base_staging, base_manifest = _staging(root, base_id)
            _promote_snapshot(
                staging=base_staging,
                output_dir=root,
                manifest=base_manifest,
            )
            expected_pointer = snapshot_module.read_current_pointer_baseline(root)
            base_manifest_sha256 = hashlib.sha256(
                (root / "snapshots" / base_id / "manifest.json").read_bytes()
            ).hexdigest()

            candidate_id = _fixture_build_id("02:03:04")
            candidate_staging, candidate_manifest = _staging(root, candidate_id)
            reserved = (
                root
                / ".incremental-staging"
                / ("a" * 32)
                / "snapshot"
            )
            reserved.parent.mkdir(parents=True)
            candidate_staging.rename(reserved)
            candidate_manifest["previousSnapshot"] = {
                "buildId": base_id,
                "manifestSha256": base_manifest_sha256,
            }
            candidate_source_manifest = source_manifest_from_binding(
                candidate_manifest["incrementalUpdate"]
            )
            baseline = NarrowGateUpdateBaseline(
                base_build_id=base_id,
                base_pointer_sha256=expected_pointer.pointer_sha256,
                base_manifest_sha256=base_manifest_sha256,
                base_source_manifest_fingerprint=(
                    source_manifest_from_binding(
                        base_manifest["incrementalUpdate"]
                    ).fingerprint
                ),
                candidate_source_manifest_fingerprint=(
                    candidate_source_manifest.fingerprint
                ),
                source_diff_sha256="d" * 64,
                delta_receipt_sha256="e" * 64,
            )
            report = build_narrow_gate_diagnostic_report(
                update_baseline=baseline,
                observations=tuple(
                    NarrowGateObservation(
                        gate_id=gate_id,
                        observation_count=index,
                        evidence_sha256=hashlib.sha256(
                            gate_id.encode("utf-8")
                        ).hexdigest(),
                    )
                    for index, gate_id in enumerate(
                        NARROW_GATE_CHECK_IDS,
                        start=1,
                    )
                ),
            )
            report_bytes = json.dumps(
                report,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            report_sha256 = hashlib.sha256(report_bytes).hexdigest()
            candidate_manifest = seal_incremental_narrow_gate_report(
                staging=reserved,
                manifest=candidate_manifest,
                report_bytes=report_bytes,
                report_sha256=report_sha256,
                update_baseline=baseline,
            )
            observed_before_cas: list[str | None] = []

            def observe_source_revalidation_boundary() -> None:
                observed_before_cas.append(
                    snapshot_module.read_current_pointer_baseline(
                        root
                    ).build_id
                )

            receipt = publish_incremental_shadow_snapshot(
                staging=reserved,
                output_dir=root,
                manifest=candidate_manifest,
                expected_current_pointer=expected_pointer,
                expected_current_manifest_sha256=base_manifest_sha256,
                expected_update_baseline=baseline,
                expected_candidate_source_manifest=(
                    candidate_source_manifest
                ),
                before_pointer_cas=observe_source_revalidation_boundary,
            )

            self.assertEqual(receipt["status"], "REPLACED")
            self.assertTrue(receipt["published"])
            self.assertFalse(receipt["productionAuthority"])
            self.assertFalse(receipt["cutoverEligible"])
            self.assertEqual(receipt["mode"], "shadow")
            self.assertEqual(receipt["defaultQuerySource"], "legacy")
            self.assertEqual(observed_before_cas, [base_id])
            self.assertEqual(
                receipt["pointerCAS"],
                {
                    "operation": "INCREMENTAL_SHADOW_PUBLICATION",
                    "beforeBuildId": base_id,
                    "afterBuildId": candidate_id,
                    "beforePointerSha256": (
                        expected_pointer.pointer_sha256
                    ),
                    "afterPointerSha256": (
                        snapshot_module.read_current_pointer_baseline(
                            root
                        ).pointer_sha256
                    ),
                    "pointerUpdated": True,
                    "independentlyVerified": True,
                    "recoveredAfterFailure": False,
                },
            )
            self.assertTrue(receipt["proof"].startswith("publication-proof://"))
            self.assertTrue(
                verify_incremental_shadow_publication(
                    output_dir=root,
                    build_id=candidate_id,
                    expected_update_baseline=baseline,
                    expected_candidate_source_manifest=(
                        candidate_source_manifest
                    ),
                )
            )

    def test_incremental_reseal_rebinds_every_database_to_new_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            base_id = _fixture_build_id("01:02:03")
            staging, base_manifest = _staging(root, base_id)
            generated_at = "2026-07-28T02:03:04+00:00"
            candidate_source_manifest = SourceManifest(
                entries=source_manifest_from_binding(
                    base_manifest["incrementalUpdate"]
                ).entries,
                generated_at=generated_at,
            )
            candidate_id = snapshot_module.snapshot_build_id(
                generated_at,
                snapshot_module.semantic_inputs_sha256(SOURCE_INPUTS),
            )
            quality_report = json.loads(
                (staging / "reports" / "quality_gates.json").read_text(
                    encoding="utf-8"
                )
            )
            quality_report["buildId"] = candidate_id
            base_manifest_sha256 = hashlib.sha256(
                json.dumps(
                    base_manifest,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()

            with patch(
                "blueprint_translator.kb_vnext.incremental_publisher."
                "_evaluate_staged_quality_gates",
                return_value=quality_report,
            ):
                resealed = reseal_incremental_snapshot_candidate(
                    staging=staging,
                    base_manifest=base_manifest,
                    base_manifest_sha256=base_manifest_sha256,
                    candidate_source_manifest=candidate_source_manifest,
                    project_root=PROJECT_ROOT,
                    discovery_database=staging / "catalog.sqlite",
                )

            self.assertEqual(resealed.manifest["buildId"], candidate_id)
            self.assertEqual(
                resealed.manifest["previousSnapshot"],
                {
                    "buildId": base_id,
                    "manifestSha256": base_manifest_sha256,
                },
            )
            self.assertEqual(resealed.manifest["cutover"]["mode"], "shadow")
            self.assertEqual(
                resealed.manifest["cutover"]["defaultQuerySource"],
                "legacy",
            )
            self.assertEqual(
                set(resealed.identity_only_databases),
                {"catalog.sqlite", "search.sqlite"},
            )
            for relative_name in (
                *DATABASE_NAMES,
                *(
                    f"domain_exports/{name}.sqlite"
                    for name in DOMAIN_PROJECTIONS
                ),
            ):
                with closing(sqlite3.connect(staging / relative_name)) as db:
                    metadata = dict(db.execute("SELECT key, value FROM metadata"))
                self.assertEqual(metadata["snapshot_build_id"], candidate_id)
                self.assertEqual(
                    metadata["snapshot_source_fingerprint"],
                    resealed.manifest["source"]["sha256"],
                )
            snapshot_module._validate_staged_snapshot_for_promotion(
                staging=staging,
                manifest=resealed.manifest,
            )

    def test_query_diagnostic_byte_tamper_blocks_pointer_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            build_id = _fixture_build_id("01:02:03")
            staging, manifest = _staging(root, build_id)
            _attach_query_diagnostics(staging, manifest)
            case_path = (
                staging / "reports" / "query_case_results.jsonl"
            )
            case_path.write_bytes(case_path.read_bytes() + b" ")

            with self.assertRaisesRegex(
                ValueError,
                "diagnostic report hash",
            ):
                _promote_snapshot(
                    staging=staging,
                    output_dir=root,
                    manifest=manifest,
                )

            self.assertFalse((root / "current.json").exists())
            self.assertFalse((root / "snapshots" / build_id).exists())

    def test_query_diagnostic_binding_tamper_fails_after_digest_update(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            build_id = _fixture_build_id("01:02:03")
            staging, manifest = _staging(root, build_id)
            _attach_query_diagnostics(staging, manifest)
            matrix_path = (
                staging / "reports" / "query_failure_matrix.json"
            )
            matrix = json.loads(
                matrix_path.read_text(encoding="utf-8")
            )
            matrix["buildId"] = "attacker-build"
            snapshot_module._write_json(matrix_path, matrix)
            quality = dict(manifest["qualityGates"])
            quality["failureMatrixSha256"] = (
                snapshot_module._sha256_file(matrix_path)
            )
            diagnostics = json.loads(
                (
                    staging / "reports" / "query_benchmark.json"
                ).read_text(encoding="utf-8")
            )["diagnosticArtifacts"]
            quality["diagnosticsBindingSha256"] = (
                snapshot_module._query_diagnostics_binding_sha256(
                    build_id=build_id,
                    corpus_sha256=str(
                        diagnostics["corpusSha256"]
                    ),
                    quality_report_sha256=str(quality["sha256"]),
                    benchmark_report_sha256=str(
                        quality["benchmarkSha256"]
                    ),
                    case_results_sha256=str(
                        quality["caseResultsSha256"]
                    ),
                    failure_matrix_sha256=str(
                        quality["failureMatrixSha256"]
                    ),
                )
            )
            manifest["qualityGates"] = quality

            with self.assertRaisesRegex(
                ValueError,
                "diagnostic artifact content",
            ):
                _promote_snapshot(
                    staging=staging,
                    output_dir=root,
                    manifest=manifest,
                )

            self.assertFalse((root / "current.json").exists())
            self.assertFalse((root / "snapshots" / build_id).exists())

    def test_pointer_rejects_extra_fields_and_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            build_id = _fixture_build_id("01:02:03")
            cases = (
                {
                    "buildId": build_id,
                    "snapshotRelativePath": f"snapshots/{build_id}",
                    "manifest": "attacker-controlled",
                },
                {
                    "buildId": build_id,
                    "snapshotRelativePath": "snapshots/../outside",
                },
                {
                    "buildId": build_id,
                    "snapshotRelativePath": f"snapshots\\{build_id}",
                },
            )
            for pointer in cases:
                with self.subTest(pointer=pointer):
                    (root / "current.json").write_text(
                        json.dumps(pointer),
                        encoding="utf-8",
                    )
                    with self.assertRaises(ValueError):
                        snapshot_module.resolve_current_snapshot(root)

    def test_existing_immutable_snapshot_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            build_id = _fixture_build_id("01:02:03")
            staging, manifest = _staging(root, build_id)
            _promote_snapshot(
                staging=staging,
                output_dir=root,
                manifest=manifest,
            )
            published_core = (
                root / "snapshots" / build_id / "core.sqlite"
            )
            original = published_core.read_bytes()
            replacement, replacement_manifest = _staging(root, build_id)
            (replacement / "core.sqlite").write_text(
                "replacement",
                encoding="utf-8",
            )

            with self.assertRaises(FileExistsError):
                _promote_snapshot(
                    staging=replacement,
                    output_dir=root,
                    manifest=replacement_manifest,
                )

            self.assertEqual(published_core.read_bytes(), original)
            self.assertEqual(
                snapshot_module.resolve_current_snapshot(root).build_id,
                build_id,
            )

    def test_publish_moves_one_complete_directory_then_swaps_small_pointer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            build_id = _fixture_build_id("01:02:03")
            staging, manifest = _staging(root, build_id)

            pointer_receipt = _promote_snapshot(
                staging=staging,
                output_dir=root,
                manifest=manifest,
            )

            pointer = json.loads(
                (root / "current.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                pointer,
                {
                    "buildId": build_id,
                    "snapshotRelativePath": f"snapshots/{build_id}",
                },
            )
            location = snapshot_module.resolve_current_snapshot(root)
            self.assertEqual(location.build_id, build_id)
            self.assertEqual(
                location.snapshot_dir,
                (root / "snapshots" / build_id).resolve(),
            )
            self.assertEqual(location.manifest, manifest)
            self.assertEqual(
                pointer_receipt["schema"],
                "ark-kb-current-pointer-cas-receipt/v1",
            )
            self.assertIsNone(
                pointer_receipt["beforePointerSha256"]
            )
            self.assertEqual(
                pointer_receipt["afterPointerSha256"],
                hashlib.sha256(
                    (root / "current.json").read_bytes()
                ).hexdigest(),
            )
            self.assertEqual(
                pointer_receipt["afterBuildId"],
                build_id,
            )
            for name in DATABASE_NAMES:
                self.assertFalse((root / name).exists())
                self.assertTrue((location.snapshot_dir / name).is_file())
            for projection_name in DOMAIN_PROJECTIONS:
                self.assertTrue(
                    (
                        location.snapshot_dir
                        / "domain_exports"
                        / f"{projection_name}.sqlite"
                    ).is_file()
                )

    def test_crash_before_pointer_swap_preserves_old_current_snapshot(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            old_id = _fixture_build_id("01:02:03")
            old_staging, old_manifest = _staging(root, old_id)
            _promote_snapshot(
                staging=old_staging,
                output_dir=root,
                manifest=old_manifest,
            )
            new_id = _fixture_build_id("02:03:04")
            new_staging, new_manifest = _staging(root, new_id)

            with (
                patch.object(
                    snapshot_module,
                    "_write_current_pointer",
                    side_effect=RuntimeError("simulated crash"),
                ),
                self.assertRaisesRegex(RuntimeError, "simulated crash"),
            ):
                _promote_snapshot(
                    staging=new_staging,
                    output_dir=root,
                    manifest=new_manifest,
                )

            current = snapshot_module.resolve_current_snapshot(root)
            self.assertEqual(current.build_id, old_id)
            orphan = root / "snapshots" / new_id
            self.assertTrue((orphan / "manifest.json").is_file())
            for name in DATABASE_NAMES:
                self.assertTrue((orphan / name).is_file())

    def test_promotion_expected_raw_pointer_conflict_preserves_newer_bytes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            old_id = _fixture_build_id("01:02:03")
            old_staging, old_manifest = _staging(root, old_id)
            _promote_snapshot(
                staging=old_staging,
                output_dir=root,
                manifest=old_manifest,
            )
            expected = snapshot_module.read_current_pointer_baseline(root)
            concurrent_bytes = (
                json.dumps(
                    {
                        "buildId": old_id,
                        "snapshotRelativePath": f"snapshots/{old_id}",
                    },
                    indent=4,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            (root / "current.json").write_bytes(concurrent_bytes)
            new_id = _fixture_build_id("02:03:04")
            new_staging, new_manifest = _staging(root, new_id)

            with self.assertRaisesRegex(
                ValueError,
                "current pointer changed before locked CAS",
            ):
                _promote_snapshot(
                    staging=new_staging,
                    output_dir=root,
                    manifest=new_manifest,
                    expected_current_pointer=expected,
                )

            self.assertEqual(
                (root / "current.json").read_bytes(),
                concurrent_bytes,
            )
            self.assertTrue(
                (root / "snapshots" / new_id / "manifest.json").is_file()
            )

    def test_corrupt_staging_is_rejected_before_pointer_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            old_id = _fixture_build_id("01:02:03")
            old_staging, old_manifest = _staging(root, old_id)
            _promote_snapshot(
                staging=old_staging,
                output_dir=root,
                manifest=old_manifest,
            )
            new_id = _fixture_build_id("02:03:04")
            new_staging, new_manifest = _staging(root, new_id)
            (new_staging / "core.sqlite").write_text(
                "not sqlite",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "database manifest mismatch",
            ):
                _promote_snapshot(
                    staging=new_staging,
                    output_dir=root,
                    manifest=new_manifest,
                )

            self.assertEqual(
                snapshot_module.resolve_current_snapshot(root).build_id,
                old_id,
            )
            self.assertFalse((root / "snapshots" / new_id).exists())
            self.assertTrue(new_staging.is_dir())

    def test_unsealed_quality_report_is_rejected_before_pointer_swap(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            old_id = _fixture_build_id("01:02:03")
            old_staging, old_manifest = _staging(root, old_id)
            _promote_snapshot(
                staging=old_staging,
                output_dir=root,
                manifest=old_manifest,
            )
            new_id = _fixture_build_id("02:03:04")
            new_staging, new_manifest = _staging(root, new_id)
            quality = dict(new_manifest["qualityGates"])
            quality["sealedInSnapshotManifest"] = False
            new_manifest["qualityGates"] = quality

            with self.assertRaisesRegex(
                ValueError,
                "quality gates are not sealed",
            ):
                _promote_snapshot(
                    staging=new_staging,
                    output_dir=root,
                    manifest=new_manifest,
                )

            self.assertEqual(
                snapshot_module.resolve_current_snapshot(root).build_id,
                old_id,
            )
            self.assertFalse((root / "snapshots" / new_id).exists())

    def test_substituted_gate_contract_cannot_enable_cutover(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            old_id = _fixture_build_id("01:02:03")
            old_staging, old_manifest = _staging(root, old_id)
            _promote_snapshot(
                staging=old_staging,
                output_dir=root,
                manifest=old_manifest,
            )
            new_id = _fixture_build_id("02:03:04")
            new_staging, new_manifest = _staging(root, new_id)
            report_path = (
                new_staging / "reports" / "quality_gates.json"
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["gates"] = [
                {
                    "id": "attacker.self_attested",
                    "category": "attacker",
                    "critical": False,
                    "passed": True,
                    "target": True,
                    "actual": True,
                    "detail": "Substituted gate must not authorize cutover.",
                }
            ]
            report["summary"] = {
                "total": 1,
                "passed": 1,
                "failed": 0,
                "cutoverEligible": True,
                "recommendation": "ready_for_default",
            }
            snapshot_module._write_json(report_path, report)
            quality = dict(new_manifest["qualityGates"])
            quality.update(
                {
                    "sha256": snapshot_module._sha256_file(report_path),
                    "passed": 1,
                    "failed": 0,
                    "cutoverEligible": True,
                }
            )
            new_manifest["qualityGates"] = quality
            new_manifest["cutover"] = {
                "mode": "ready",
                "defaultQuerySource": "vnext",
            }

            with self.assertRaisesRegex(
                ValueError,
                "gate contract",
            ):
                _promote_snapshot(
                    staging=new_staging,
                    output_dir=root,
                    manifest=new_manifest,
                )

            self.assertEqual(
                snapshot_module.resolve_current_snapshot(root).build_id,
                old_id,
            )
            self.assertFalse((root / "snapshots" / new_id).exists())

    def test_active_stale_sources_with_v1_burn_in_remain_shadow(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            build_id = _fixture_build_id("01:02:03")
            staging, manifest = _staging(root, build_id)
            core_path = staging / "core.sqlite"
            core = sqlite3.connect(core_path)
            try:
                core.execute(
                    """
                    UPDATE metadata SET value='1'
                    WHERE key='runtime_health_active_stale_sources'
                    """
                )
                core.commit()
            finally:
                core.close()
            manifest["databases"]["core.sqlite"] = (
                snapshot_module.database_metrics(core_path)
            )
            runtime_health = dict(manifest["runtimeHealth"])
            runtime_health["activeStaleSources"] = 1
            manifest["runtimeHealth"] = runtime_health

            report_path = staging / "reports" / "quality_gates.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            for gate in report["gates"]:
                gate["passed"] = True
                gate["actual"] = gate["target"]
            report["summary"] = {
                "total": len(report["gates"]),
                "passed": len(report["gates"]),
                "failed": 0,
                "cutoverEligible": True,
                "recommendation": "ready_for_default",
            }
            burn_in_source = root / "fixture-burn-in.json"
            snapshot_module._write_json(
                burn_in_source,
                _valid_fixture_burn_in_attestation(),
            )
            burn_in = snapshot_module._stage_burn_in_attestation(
                staging=staging,
                source_path=burn_in_source,
            )
            manifest = snapshot_module._seal_staged_quality_report(
                staging=staging,
                manifest=manifest,
                report=report,
                burn_in=burn_in,
            )

            self.assertEqual(
                manifest["burnIn"]["status"],
                "DIAGNOSTIC_ONLY_V1",
            )
            self.assertFalse(
                manifest["qualityGates"]["cutoverEligible"],
            )
            self.assertEqual(manifest["cutover"]["mode"], "shadow")
            self.assertEqual(
                manifest["cutover"]["defaultQuerySource"],
                "legacy",
            )

            _promote_snapshot(
                staging=staging,
                output_dir=root,
                manifest=manifest,
            )

            self.assertTrue((root / "current.json").is_file())
            self.assertTrue((root / "snapshots" / build_id).is_dir())

    def test_unknown_benchmark_schema_cannot_be_sealed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            build_id = _fixture_build_id("01:02:03")
            staging, manifest = _staging(root, build_id)
            benchmark_path = (
                staging / "reports" / "query_benchmark.json"
            )
            report_path = staging / "reports" / "quality_gates.json"
            benchmark = json.loads(
                benchmark_path.read_text(encoding="utf-8")
            )
            benchmark["schema"] = "ark-kb-query-benchmark/unknown"
            snapshot_module._write_json(benchmark_path, benchmark)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["benchmark"] = benchmark
            snapshot_module._write_json(report_path, report)
            quality = dict(manifest["qualityGates"])
            quality["sha256"] = snapshot_module._sha256_file(
                report_path
            )
            quality["benchmarkSha256"] = snapshot_module._sha256_file(
                benchmark_path
            )
            manifest["qualityGates"] = quality

            with self.assertRaisesRegex(
                ValueError,
                "quality report identity",
            ):
                _promote_snapshot(
                    staging=staging,
                    output_dir=root,
                    manifest=manifest,
                )

            self.assertFalse((root / "current.json").exists())
            self.assertFalse((root / "snapshots" / build_id).exists())

    def test_metadata_only_main_store_cannot_be_published(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            build_id = _fixture_build_id("01:02:03")
            staging, manifest = _staging(root, build_id)
            core = sqlite3.connect(staging / "core.sqlite")
            try:
                core.execute("DROP TABLE entities")
                core.commit()
            finally:
                core.close()
            manifest["databases"]["core.sqlite"] = (
                snapshot_module.database_metrics(
                    staging / "core.sqlite"
                )
            )

            with self.assertRaisesRegex(
                ValueError,
                "schema contract is incomplete",
            ):
                _promote_snapshot(
                    staging=staging,
                    output_dir=root,
                    manifest=manifest,
                )

            self.assertFalse((root / "current.json").exists())
            self.assertFalse((root / "snapshots" / build_id).exists())

    def test_nonempty_wal_sidecar_cannot_be_published(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            build_id = _fixture_build_id("01:02:03")
            staging, manifest = _staging(root, build_id)
            (staging / "core.sqlite-wal").write_bytes(
                b"unsealed-logical-content"
            )

            with self.assertRaisesRegex(
                ValueError,
                "WAL sidecar",
            ):
                _promote_snapshot(
                    staging=staging,
                    output_dir=root,
                    manifest=manifest,
                )

            self.assertFalse((root / "current.json").exists())
            self.assertFalse((root / "snapshots" / build_id).exists())

    def test_projection_from_another_build_cannot_be_substituted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            build_a = _fixture_build_id("01:02:03")
            staging_a, manifest_a = _staging(root, build_a)
            build_b = _fixture_build_id("02:03:04")
            staging_b, manifest_b = _staging(root, build_b)
            relative_name = "domain_exports/buff_effects.sqlite"
            shutil.copy2(
                staging_b / relative_name,
                staging_a / relative_name,
            )
            manifest_a["databases"][relative_name] = (
                manifest_b["databases"][relative_name]
            )

            with self.assertRaisesRegex(
                ValueError,
                "metadata mismatch|not bound",
            ):
                _promote_snapshot(
                    staging=staging_a,
                    output_dir=root,
                    manifest=manifest_a,
                )

            self.assertFalse((root / "current.json").exists())
            self.assertFalse((root / "snapshots" / build_a).exists())

    def test_open_old_snapshot_survives_new_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            old_id = _fixture_build_id("01:02:03")
            old_staging, old_manifest = _staging(
                root,
                old_id,
                sqlite_payload=True,
            )
            _promote_snapshot(
                staging=old_staging,
                output_dir=root,
                manifest=old_manifest,
            )
            old_location = snapshot_module.resolve_current_snapshot(root)
            old_connection = sqlite3.connect(
                old_location.snapshot_dir / "core.sqlite"
            )
            try:
                new_id = _fixture_build_id("02:03:04")
                new_staging, new_manifest = _staging(
                    root,
                    new_id,
                    sqlite_payload=True,
                )
                _promote_snapshot(
                    staging=new_staging,
                    output_dir=root,
                    manifest=new_manifest,
                )

                self.assertEqual(
                    old_connection.execute(
                        """
                        SELECT value FROM metadata
                        WHERE key='snapshot_build_id'
                        """
                    ).fetchone()[0],
                    old_id,
                )
                current = snapshot_module.resolve_current_snapshot(root)
                self.assertEqual(current.build_id, new_id)
                with closing(
                    sqlite3.connect(
                        current.snapshot_dir / "core.sqlite"
                    )
                ) as new_connection:
                    self.assertEqual(
                        new_connection.execute(
                            """
                            SELECT value FROM metadata
                            WHERE key='snapshot_build_id'
                            """
                        ).fetchone()[0],
                        new_id,
                    )
            finally:
                old_connection.close()

    def test_concurrent_resolvers_never_mix_database_build_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            root.mkdir()
            old_id = _fixture_build_id("01:02:03")
            old_staging, old_manifest = _staging(
                root,
                old_id,
                sqlite_payload=True,
            )
            _promote_snapshot(
                staging=old_staging,
                output_dir=root,
                manifest=old_manifest,
            )
            errors: list[object] = []
            stop = threading.Event()

            def read_snapshots() -> None:
                while not stop.is_set():
                    try:
                        location = snapshot_module.resolve_current_snapshot(
                            root
                        )
                        observed = set()
                        for name in DATABASE_NAMES:
                            with closing(
                                sqlite3.connect(
                                    location.snapshot_dir / name
                                )
                            ) as connection:
                                observed.add(
                                    connection.execute(
                                        """
                                        SELECT value FROM metadata
                                        WHERE key='snapshot_build_id'
                                        """
                                    ).fetchone()[0]
                                )
                        if observed != {location.build_id}:
                            errors.append(
                                (location.build_id, sorted(observed))
                            )
                    except Exception as exc:  # pragma: no cover - diagnostic
                        errors.append(exc)
                        stop.set()

            readers = [
                threading.Thread(target=read_snapshots)
                for _ in range(4)
            ]
            for reader in readers:
                reader.start()
            try:
                new_id = _fixture_build_id("02:03:04")
                new_staging, new_manifest = _staging(
                    root,
                    new_id,
                    sqlite_payload=True,
                )
                _promote_snapshot(
                    staging=new_staging,
                    output_dir=root,
                    manifest=new_manifest,
                )
            finally:
                stop.set()
                for reader in readers:
                    reader.join(timeout=5)

            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
