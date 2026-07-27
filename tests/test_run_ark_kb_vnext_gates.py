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
