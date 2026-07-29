import json
from pathlib import Path

from scripts import run_ark_kb_vnext_gates as gates


def test_report_uris_are_build_scoped_for_configured_immutable_root(
    tmp_path: Path,
) -> None:
    (tmp_path / "current.json").write_text("{}", encoding="utf-8")

    assert gates._report_uris(tmp_path, "build-1") == (
        "reports/build-1/quality_gates.json",
        "reports/build-1/query_benchmark.json",
    )


def test_report_uris_are_build_scoped_for_direct_immutable_snapshot(
    tmp_path: Path,
) -> None:
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")

    assert gates._report_uris(tmp_path, "build-2") == (
        "reports/build-2/quality_gates.json",
        "reports/build-2/query_benchmark.json",
    )


def test_report_uris_remain_compatible_with_legacy_layout(
    tmp_path: Path,
) -> None:
    assert gates._report_uris(tmp_path, "legacy-build") == (
        "reports/quality_gates.json",
        "reports/query_benchmark.json",
    )


def test_diagnostic_report_metadata_is_build_scoped_for_immutable_root(
    tmp_path: Path,
) -> None:
    (tmp_path / "current.json").write_text("{}", encoding="utf-8")
    benchmark = {
        "diagnosticArtifacts": {
            "caseResults": {"sha256": "a" * 64},
            "failureMatrix": {"sha256": "b" * 64},
        }
    }

    assert gates._diagnostic_report_metadata(
        tmp_path,
        "build-3",
        benchmark,
    ) == {
        "caseResultsUri": (
            "reports/build-3/query_case_results.jsonl"
        ),
        "caseResultsSha256": "a" * 64,
        "failureMatrixUri": (
            "reports/build-3/query_failure_matrix.json"
        ),
        "failureMatrixSha256": "b" * 64,
    }


def test_diagnostic_report_metadata_is_empty_for_legacy_benchmark(
    tmp_path: Path,
) -> None:
    assert gates._diagnostic_report_metadata(
        tmp_path,
        "legacy-build",
        {"total": 120},
    ) == {}


def test_main_prints_published_diagnostic_uris_and_digests(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    snapshot_root = tmp_path / "vnext"
    snapshot_root.mkdir()
    (snapshot_root / "current.json").write_text("{}", encoding="utf-8")
    discovery_database = tmp_path / "evidence.sqlite"
    report = {
        "schema": "ark-kb-quality-gates/v2",
        "buildId": "build-4",
        "summary": {"cutoverEligible": False},
        "benchmark": {
            "diagnosticArtifacts": {
                "caseResults": {"sha256": "c" * 64},
                "failureMatrix": {"sha256": "d" * 64},
            }
        },
    }
    monkeypatch.setattr(
        gates,
        "evaluate_quality_gates",
        lambda **_: report,
    )
    monkeypatch.setattr(
        gates,
        "publish_gate_report",
        lambda **_: {
            "mode": "shadow",
            "defaultQuerySource": "legacy",
        },
    )

    exit_code = gates.main(
        [
            "--snapshot-root",
            str(snapshot_root),
            "--discovery-database",
            str(discovery_database),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["reportUri"] == (
        "reports/build-4/quality_gates.json"
    )
    assert payload["benchmarkUri"] == (
        "reports/build-4/query_benchmark.json"
    )
    assert payload["caseResultsUri"] == (
        "reports/build-4/query_case_results.jsonl"
    )
    assert payload["caseResultsSha256"] == "c" * 64
    assert payload["failureMatrixUri"] == (
        "reports/build-4/query_failure_matrix.json"
    )
    assert payload["failureMatrixSha256"] == "d" * 64
