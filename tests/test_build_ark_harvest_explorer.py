import io
import signal
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

NODE_CATALOG_SHA256 = "a" * 64
EVALUATION_CATALOG_SHA256 = "b" * 64

from build_ark_harvest_explorer import (  # noqa: E402
    _dataset_paths,
    _expected_query_payload,
    _validated_independent_verification,
    main,
    parse_args,
    plan_commands,
    promote_staged_dataset,
)
import build_ark_harvest_explorer as build_explorer  # noqa: E402


def _metric_aware_verification() -> dict[str, object]:
    selected = 128
    static_metrics = (
        "staticCompleteNodeTargetYield",
        "staticYieldPerAttackCycleSecond",
    )
    runtime_metrics = ("observedYieldPerNode", "observedYieldPerSecond")
    contracts = {
        "staticCompleteNodeTargetYield": {
            "scoreBasis": "STATIC_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE",
            "unit": "target_resource_units/node",
            "runtime": False,
        },
        "staticYieldPerAttackCycleSecond": {
            "scoreBasis": "STATIC_TARGET_RESOURCE_UNITS_PER_ATTACK_CYCLE_SECOND",
            "unit": "target_resource_units/attack_cycle_second",
            "runtime": False,
        },
        "observedYieldPerNode": {
            "scoreBasis": "OBSERVED_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE",
            "unit": "target_resource_units/node",
            "runtime": True,
        },
        "observedYieldPerSecond": {
            "scoreBasis": "OBSERVED_TARGET_RESOURCE_UNITS_PER_SECOND",
            "unit": "target_resource_units/second",
            "runtime": True,
        },
    }
    forward = {
        **{
            metric: {
                "status": "VERIFIED",
                "targetsSelected": selected,
                "targetsCompared": selected,
            }
            for metric in static_metrics
        },
        **{
            metric: {
                "status": "SKIPPED_WITH_REASON",
                "reason": "CONTROLLED_RUNTIME_FIXTURE_AND_PROFILE_REQUIRED",
                "targetsSelected": selected,
                "targetsCompared": 0,
            }
            for metric in runtime_metrics
        },
    }
    return {
        "schema": "blueprint-to-code.harvest-independent-verification/v2",
        "status": "PASS",
        "methodology": {
            "metricContracts": contracts,
            "metricsAttempted": list(contracts),
        },
        "inputs": {
            "nodeCatalogSha256": NODE_CATALOG_SHA256,
            "evaluationCatalogSha256": EVALUATION_CATALOG_SHA256,
        },
        "selection": {"targetsSelected": selected},
        "coverageByDirection": {"forward": forward},
        "comparison": {"targetsCompared": 256, "mismatchCount": 0},
    }


class BuildArkHarvestExplorerTests(unittest.TestCase):
    def validate_verification(self, payload: dict[str, object]) -> dict[str, int]:
        return _validated_independent_verification(
            payload,
            expected_node_catalog_sha256=NODE_CATALOG_SHA256,
            expected_evaluation_catalog_sha256=EVALUATION_CATALOG_SHA256,
        )

    def test_query_contract_is_exactly_derived_from_full_report(self):
        full = {
            "schema": "ark-harvest-ranking/v2",
            "generatedAt": "now",
            "datasetRevision": "revision",
            "scanManifestHash": "manifest",
            "methodology": {"formulaVersion": "v1"},
            "coverage": {"rows": 1},
            "bestRows": [{"resource": "Metal"}],
            "rows": [{"large": "not copied"}],
        }

        query = _expected_query_payload(full)

        self.assertEqual(query["bestRows"], full["bestRows"])
        self.assertNotIn("rows", query)
        self.assertEqual(query["querySchema"], "ark-harvest-ranking-query/v2")

    def test_metric_aware_verification_accepts_static_pass_and_runtime_skip(self):
        summary = self.validate_verification(_metric_aware_verification())

        self.assertEqual(
            summary,
            {
                "targetsSelected": 128,
                "metricComparisons": 256,
                "staticMetricsVerified": 2,
            },
        )

    def test_metric_aware_verification_rejects_old_single_metric_total(self):
        payload = _metric_aware_verification()
        payload["comparison"]["targetsCompared"] = 128

        with self.assertRaisesRegex(ValueError, "comparison is inconsistent"):
            self.validate_verification(payload)

    def test_metric_aware_verification_requires_reason_for_runtime_skip(self):
        payload = _metric_aware_verification()
        runtime = payload["coverageByDirection"]["forward"][
            "observedYieldPerNode"
        ]
        runtime.pop("reason")

        with self.assertRaisesRegex(ValueError, "metric coverage did not pass"):
            self.validate_verification(payload)

    def test_metric_aware_verification_requires_exact_v2_metrics(self):
        payload = _metric_aware_verification()
        payload["methodology"]["metricContracts"] = {
            "fabricatedMetric": {"runtime": False}
        }

        with self.assertRaisesRegex(ValueError, "metric contract is not exact v2"):
            self.validate_verification(payload)

    def test_metric_aware_verification_rejects_coerced_counts(self):
        for invalid in (True, 1.5, "128"):
            with self.subTest(invalid=invalid):
                payload = _metric_aware_verification()
                payload["selection"]["targetsSelected"] = invalid
                with self.assertRaisesRegex(ValueError, "non-negative integer"):
                    self.validate_verification(payload)

    def test_independent_verification_is_bound_to_both_current_catalogs(self):
        payload = _metric_aware_verification()
        for field, changed_sha in (
            ("nodeCatalogSha256", "c" * 64),
            ("evaluationCatalogSha256", "d" * 64),
        ):
            with self.subTest(field=field):
                changed = {
                    **payload,
                    "inputs": {**payload["inputs"], field: changed_sha},
                }
                with self.assertRaisesRegex(ValueError, "current catalogs"):
                    self.validate_verification(changed)

    def test_independent_verification_requires_valid_input_hashes(self):
        payload = _metric_aware_verification()
        payload["inputs"]["nodeCatalogSha256"] = "not-a-sha"

        with self.assertRaisesRegex(ValueError, "current catalogs"):
            self.validate_verification(payload)

    def test_plan_builds_evaluation_catalog_before_final_node_catalog(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            args = parse_args(
                [
                    "--devkit-root",
                    str(temp / "DevKit"),
                    "--output-dir",
                    str(temp / "rankings"),
                    "--catalog-output",
                    str(temp / "nodes" / "catalog.json"),
                    "--scan-cache",
                    str(temp / "nodes" / "scan-cache.json"),
                    "--creature-scan-cache",
                    str(temp / "rankings" / "creature-scan-cache.json"),
                    "--map-scan-cache",
                    str(temp / "nodes" / "map-scan-cache.json"),
                    "--image-cache-root",
                    str(temp / "nodes" / "images"),
                    "--creature-file",
                    str(temp / "creatures.json"),
                ]
            )

            commands = plan_commands(args, python_executable="python-test")

        self.assertEqual(len(commands), 8)
        self.assertTrue(commands[0][1].endswith("rank_ark_harvest.py"))
        self.assertIn("--all-resources", commands[0])
        self.assertIn("--discover-all-components", commands[0])

        self.assertTrue(commands[1][1].endswith("build_ark_resource_node_catalog.py"))
        self.assertEqual(commands[1][commands[1].index("--discover-root") + 1], ".")
        self.assertIn("--skip-map-scan", commands[1])
        self.assertIn("--skip-images", commands[1])

        manifest = str(temp / "nodes" / "referenced_harvest_components.txt")
        self.assertEqual(
            commands[1][commands[1].index("--component-manifest-output") + 1],
            manifest,
        )
        self.assertEqual(
            commands[1][commands[1].index("--output") + 1],
            str(temp / "nodes" / "catalog.preliminary.json"),
        )
        self.assertNotEqual(
            commands[1][commands[1].index("--output") + 1],
            commands[4][commands[4].index("--output") + 1],
        )
        self.assertTrue(commands[2][1].endswith("rank_ark_harvest.py"))
        self.assertEqual(
            commands[2][commands[2].index("--extra-component-file") + 1],
            manifest,
        )

        self.assertTrue(commands[3][1].endswith("build_ark_harvest_evaluation_catalog.py"))
        evaluation_output = str(temp / "rankings" / "harvest_evaluation_catalog.json")
        self.assertEqual(
            commands[3][commands[3].index("--output") + 1],
            evaluation_output,
        )
        self.assertEqual(
            commands[3][commands[3].index("--scan-cache") + 1],
            str(temp / "rankings" / "creature-scan-cache.json"),
        )

        self.assertTrue(commands[4][1].endswith("build_ark_resource_node_catalog.py"))
        self.assertEqual(commands[4][commands[4].index("--map-root") + 1], ".")
        self.assertNotIn("--skip-map-scan", commands[4])
        self.assertNotIn("--skip-images", commands[4])
        self.assertEqual(
            commands[4][commands[4].index("--evaluation-catalog") + 1],
            evaluation_output,
        )
        self.assertEqual(
            commands[4][commands[4].index("--map-scan-cache") + 1],
            str(temp / "nodes" / "map-scan-cache.json"),
        )

        self.assertTrue(commands[5][1].endswith("build_harvest_catalog_sqlite.py"))
        self.assertEqual(
            commands[5][commands[5].index("--catalog") + 1],
            str(temp / "nodes" / "catalog.json"),
        )
        self.assertEqual(
            commands[5][commands[5].index("--output") + 1],
            str(temp / "nodes" / "harvest_catalog.sqlite"),
        )

        self.assertTrue(commands[6][1].endswith("verify_ark_harvest_rankings.py"))
        self.assertEqual(
            commands[6][commands[6].index("--sample-size") + 1],
            "128",
        )
        self.assertEqual(
            commands[6][commands[6].index("--seed") + 1],
            "phase5-acceptance-v1",
        )

        self.assertTrue(commands[7][1].endswith("verify_ark_harvest_report.py"))
        self.assertTrue(
            commands[7][commands[7].index("--full") + 1].endswith(
                "harvest_ranking_all_resources.full.json"
            )
        )
        self.assertTrue(
            commands[7][commands[7].index("--ai") + 1].endswith(
                "harvest_ranking_all_resources.ai.json"
            )
        )

        for command in commands:
            self.assertEqual(command[0], "python-test")
        ranking_commands = [commands[0], commands[2]]
        for command in ranking_commands:
            self.assertEqual(
                command[command.index("--creature-file") + 1],
                str(temp / "creatures.json"),
            )
        self.assertNotIn("--creature-file", commands[1])
        self.assertNotIn("--creature-file", commands[3])
        self.assertNotIn("--creature-file", commands[4])
        self.assertNotIn("--creature-file", commands[5])
        self.assertNotIn("--creature-file", commands[6])
        self.assertNotIn("--creature-file", commands[7])

    def test_final_pass_honors_optional_skip_switches(self):
        args = parse_args(["--skip-map-scan", "--skip-images"])

        commands = plan_commands(args, python_executable="python-test")

        self.assertIn("--skip-map-scan", commands[4])
        self.assertIn("--skip-images", commands[4])

    @mock.patch("build_ark_harvest_explorer.subprocess.run")
    def test_dry_run_prints_plan_without_executing(self, run_mock):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["--dry-run"])

        self.assertEqual(exit_code, 0)
        run_mock.assert_not_called()
        self.assertEqual(output.getvalue().count("/8]"), 8)

    @mock.patch("build_ark_harvest_explorer.promote_staged_dataset")
    @mock.patch(
        "build_ark_harvest_explorer.validate_staged_dataset",
        return_value={"datasetRevision": "revision"},
    )
    @mock.patch("build_ark_harvest_explorer.subprocess.run")
    def test_execution_uses_checked_staged_subprocesses_before_promotion(
        self,
        run_mock,
        validate_mock,
        promote_mock,
    ):
        with redirect_stdout(io.StringIO()):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertEqual(run_mock.call_count, 8)
        for call in run_mock.call_args_list:
            self.assertEqual(call.kwargs["cwd"], str(ROOT))
            self.assertIs(call.kwargs["check"], True)
        first_command = run_mock.call_args_list[0].args[0]
        staged_output = Path(first_command[first_command.index("--output-dir") + 1])
        self.assertIn(".tmp_harvest_build_", str(staged_output))
        validate_mock.assert_called_once()
        promote_mock.assert_called_once()

    def test_promotion_copies_the_complete_staged_bundle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            staged = parse_args(
                [
                    "--output-dir",
                    str(root / "staged" / "rankings"),
                    "--catalog-output",
                    str(root / "staged" / "nodes" / "catalog.json"),
                ]
            )
            final = parse_args(
                [
                    "--output-dir",
                    str(root / "final" / "rankings"),
                    "--catalog-output",
                    str(root / "final" / "nodes" / "catalog.json"),
                ]
            )
            for index, path in enumerate(_dataset_paths(staged).values()):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"artifact-{index}", encoding="utf-8")

            promote_staged_dataset(staged, final)

            for staged_path, final_path in zip(
                _dataset_paths(staged).values(),
                _dataset_paths(final).values(),
                strict=True,
            ):
                self.assertEqual(final_path.read_bytes(), staged_path.read_bytes())
                self.assertEqual(list(final_path.parent.glob(f".{final_path.name}.*.next")), [])

    def test_termination_during_promotion_is_deferred_until_complete_new_bundle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            staged = parse_args(
                [
                    "--output-dir",
                    str(root / "staged" / "rankings"),
                    "--catalog-output",
                    str(root / "staged" / "nodes" / "catalog.json"),
                ]
            )
            final = parse_args(
                [
                    "--output-dir",
                    str(root / "final" / "rankings"),
                    "--catalog-output",
                    str(root / "final" / "nodes" / "catalog.json"),
                ]
            )
            for index, path in enumerate(_dataset_paths(staged).values()):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"new-revision-{index}", encoding="utf-8")
            for index, path in enumerate(_dataset_paths(final).values()):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"old-revision-{index}", encoding="utf-8")

            real_atomic_copy = build_explorer._atomic_copy
            copy_count = 0

            def request_termination_during_fourth_copy(source, destination):
                nonlocal copy_count
                copy_count += 1
                real_atomic_copy(source, destination)
                if copy_count == 4:
                    handler = signal.getsignal(signal.SIGTERM)
                    self.assertTrue(callable(handler))
                    handler(signal.SIGTERM, None)

            output = io.StringIO()
            with mock.patch(
                "build_ark_harvest_explorer._atomic_copy",
                side_effect=request_termination_during_fourth_copy,
            ), redirect_stdout(output):
                deferred = build_explorer._promote_staged_dataset_uninterruptibly(
                    staged,
                    final,
                )

            self.assertTrue(deferred)
            self.assertIn("[promotion-critical] begin", output.getvalue())
            self.assertIn("[promotion-critical] commit-complete", output.getvalue())
            for index, final_path in enumerate(_dataset_paths(final).values()):
                self.assertEqual(
                    final_path.read_text(encoding="utf-8"),
                    f"new-revision-{index}",
                )

    def test_promotion_failure_restores_the_complete_previous_bundle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            staged = parse_args(
                [
                    "--output-dir",
                    str(root / "staged" / "rankings"),
                    "--catalog-output",
                    str(root / "staged" / "nodes" / "catalog.json"),
                ]
            )
            final = parse_args(
                [
                    "--output-dir",
                    str(root / "final" / "rankings"),
                    "--catalog-output",
                    str(root / "final" / "nodes" / "catalog.json"),
                ]
            )
            for index, path in enumerate(_dataset_paths(staged).values()):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"new-artifact-{index}", encoding="utf-8")
            for index, path in enumerate(_dataset_paths(final).values()):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"old-artifact-{index}", encoding="utf-8")

            from build_ark_harvest_explorer import _atomic_copy as real_atomic_copy

            copy_count = 0

            def fail_during_fourth_copy(source, destination):
                nonlocal copy_count
                copy_count += 1
                if copy_count == 4:
                    raise OSError("injected promotion failure")
                real_atomic_copy(source, destination)

            with mock.patch(
                "build_ark_harvest_explorer._atomic_copy",
                side_effect=fail_during_fourth_copy,
            ):
                with self.assertRaisesRegex(OSError, "injected promotion failure"):
                    promote_staged_dataset(staged, final)

            for index, final_path in enumerate(_dataset_paths(final).values()):
                self.assertEqual(
                    final_path.read_text(encoding="utf-8"),
                    f"old-artifact-{index}",
                )
                self.assertEqual(
                    list(final_path.parent.glob(f".{final_path.name}.*.backup")),
                    [],
                )
                self.assertEqual(
                    list(final_path.parent.glob(f".{final_path.name}.*.next")),
                    [],
                )

    def test_incomplete_rollback_preserves_backups_and_reports_their_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            staged = parse_args(
                [
                    "--output-dir",
                    str(root / "staged" / "rankings"),
                    "--catalog-output",
                    str(root / "staged" / "nodes" / "catalog.json"),
                ]
            )
            final = parse_args(
                [
                    "--output-dir",
                    str(root / "final" / "rankings"),
                    "--catalog-output",
                    str(root / "final" / "nodes" / "catalog.json"),
                ]
            )
            staged_paths = _dataset_paths(staged)
            final_paths = _dataset_paths(final)
            for index, path in enumerate(staged_paths.values()):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"new-artifact-{index}", encoding="utf-8")
            for index, path in enumerate(final_paths.values()):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"old-artifact-{index}", encoding="utf-8")

            from build_ark_harvest_explorer import _atomic_copy as real_atomic_copy

            copy_count = 0

            def fail_during_fourth_copy(source, destination):
                nonlocal copy_count
                if (
                    source.name.endswith(".backup")
                    and Path(destination) == final_paths["evaluation"]
                ):
                    raise OSError("injected rollback failure")
                copy_count += 1
                if copy_count == 4:
                    raise OSError("injected promotion failure")
                real_atomic_copy(source, destination)

            real_path_replace = Path.replace

            def fail_evaluation_backup_restore(source, destination):
                if (
                    source.name.endswith(".backup")
                    and Path(destination) == final_paths["evaluation"]
                ):
                    raise OSError("injected rollback failure")
                return real_path_replace(source, destination)

            with mock.patch(
                "build_ark_harvest_explorer._atomic_copy",
                side_effect=fail_during_fourth_copy,
            ), mock.patch.object(Path, "replace", fail_evaluation_backup_restore):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "rollback was incomplete",
                ) as raised:
                    promote_staged_dataset(staged, final)

            backup_paths = [
                next(path.parent.glob(f".{path.name}.*.backup"))
                for path in final_paths.values()
            ]
            for index, backup_path in enumerate(backup_paths):
                self.assertTrue(backup_path.is_file())
                self.assertEqual(
                    backup_path.read_text(encoding="utf-8"),
                    f"old-artifact-{index}",
                )
                self.assertIn(str(backup_path), str(raised.exception))
            self.assertEqual(
                final_paths["evaluation"].read_text(encoding="utf-8"),
                "new-artifact-5",
            )


if __name__ == "__main__":
    unittest.main()
