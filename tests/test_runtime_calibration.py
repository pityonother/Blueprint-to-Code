from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.runtime_calibration import (  # noqa: E402
    compare_runtime_observations,
    render_runtime_comparison_markdown,
)


def _observation(
    *,
    final_values: list[float],
    clamp: bool,
    base_damage: float = 50.0,
    health: float = 100.0,
    quantity_multiplier: float = 1.0,
    unsupported: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema": "blueprint-to-code-runtime-observation-set/v1",
        "observationSetId": "runtime://fixture/harvest-test",
        "synthetic": True,
        "environment": {
            "gameBuild": "synthetic",
            "serverSettings": {"HarvestAmountMultiplier": 1.0},
            "mods": [],
            "map": "FixtureMap",
            "notes": "Synthetic fixture; not a real game result.",
        },
        "subject": {
            "nodeId": "fixture-node",
            "resourceId": "fixture-resource",
            "speciesKey": "fixture-species",
            "attackId": "fixture-attack",
        },
        "staticModel": {
            "modelVersion": "harvest-estimated-yield-per-node/v1-native-static-profile",
            "inputs": {
                "base_damage": base_damage,
                "damage_multiplier": 1.0,
                "harvest_quantity_multiplier": quantity_multiplier,
                "max_harvest_health": health,
                "harvest_health_give_resource_interval": 40.0,
                "resource_weight_share": 1.0,
                "minimum_quantity": 1.0,
                "maximum_quantity": 1.0,
                "quantity_random_power": 1.0,
                "clamp_resource_harvest_damage": clamp,
                "harvest_amount_scale": 2.0,
            },
            "unsupportedDynamicBranches": unsupported or [],
        },
        "policy": {
            "absoluteTolerance": 0.01,
            "relativeTolerance": 0.001,
            "minimumTrialsForConfirmation": 3,
        },
        "trials": [
            {
                "trialId": f"trial-{index + 1:03d}",
                "hits": [],
                "finalResourceUnits": value,
            }
            for index, value in enumerate(final_values)
        ],
    }


class RuntimeCalibrationTests(unittest.TestCase):
    def test_three_matching_trials_are_runtime_confirmed(self):
        result = compare_runtime_observations(
            _observation(final_values=[4.0, 4.0, 4.0], clamp=True)
        )

        self.assertEqual(result["status"], "RUNTIME_CONFIRMED")
        self.assertEqual(result["prediction"]["estimatedYieldPerNode"], 4.0)
        self.assertEqual(result["observations"]["mean"], 4.0)
        self.assertEqual(result["observations"]["variance"], 0.0)

    def test_unclamped_final_hit_and_small_sample_are_runtime_calibrated(self):
        result = compare_runtime_observations(
            _observation(
                final_values=[7.0, 7.0],
                clamp=False,
                base_damage=80.0,
            )
        )

        self.assertEqual(result["prediction"]["estimatedYieldPerNode"], 7.0)
        self.assertEqual(result["status"], "RUNTIME_CALIBRATED")

    def test_floor_and_truncation_boundary_uses_the_static_model(self):
        result = compare_runtime_observations(
            _observation(
                final_values=[12.0, 12.0, 12.0],
                clamp=True,
                base_damage=100.0,
                health=200.0,
                quantity_multiplier=1.2,
            )
        )

        self.assertEqual(result["prediction"]["estimatedYieldPerNode"], 12.0)
        self.assertEqual(result["status"], "RUNTIME_CONFIRMED")

    def test_mismatch_is_runtime_diverged_with_explicit_error(self):
        result = compare_runtime_observations(
            _observation(final_values=[8.0, 8.0, 8.0], clamp=True)
        )

        self.assertEqual(result["status"], "RUNTIME_DIVERGED")
        self.assertEqual(result["comparison"]["absoluteError"], 4.0)
        self.assertGreater(result["comparison"]["relativeError"], 0.0)

    def test_unsupported_dynamic_branch_does_not_emit_a_fake_score(self):
        result = compare_runtime_observations(
            _observation(
                final_values=[999.0],
                clamp=True,
                unsupported=["BPOverrideHarvestYield"],
            )
        )

        self.assertEqual(result["status"], "UNSUPPORTED_DYNAMIC_BRANCH")
        self.assertIsNone(result["prediction"]["estimatedYieldPerNode"])
        self.assertIsNone(result["comparison"])

    def test_no_trials_remains_static_reversed(self):
        result = compare_runtime_observations(
            _observation(final_values=[], clamp=True)
        )

        self.assertEqual(result["status"], "STATIC_REVERSED")
        self.assertEqual(result["observations"]["count"], 0)

    def test_markdown_labels_synthetic_data_and_status(self):
        comparison = compare_runtime_observations(
            _observation(final_values=[4.0, 4.0, 4.0], clamp=True)
        )

        markdown = render_runtime_comparison_markdown(comparison)

        self.assertIn("RUNTIME_CONFIRMED", markdown)
        self.assertIn("synthetic", markdown.casefold())
        self.assertNotIn("真实游戏已验证", markdown)

    def test_committed_synthetic_fixtures_cover_required_runtime_boundaries(self):
        fixture_root = ROOT / "tests" / "fixtures" / "runtime_observations"
        expected = {
            "harvest-linear-match.json": "RUNTIME_CONFIRMED",
            "harvest-clamped-final-hit.json": "RUNTIME_CALIBRATED",
            "harvest-unclamped-final-hit.json": "RUNTIME_CALIBRATED",
            "harvest-floor-trunc-boundary.json": "RUNTIME_CONFIRMED",
            "harvest-diverged.json": "RUNTIME_DIVERGED",
            "harvest-unsupported-branch.json": "UNSUPPORTED_DYNAMIC_BRANCH",
        }

        actual = {}
        for name in expected:
            payload = json.loads(
                (fixture_root / name).read_text(encoding="utf-8")
            )
            actual[name] = compare_runtime_observations(payload)["status"]

        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
