from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.harvest_runtime_observations import (  # noqa: E402
    RUNTIME_STATUS_OBSERVED,
    load_harvest_runtime_observations,
    validate_harvest_runtime_observation,
)
from blueprint_translator.harvest_ranking import YIELD_MODEL_VERSION  # noqa: E402


def _payload(*, synthetic: bool) -> dict[str, object]:
    return {
        "schema": "blueprint-to-code.harvest-runtime-observation/v2",
        "observationSetId": "runtime://test/exact-row",
        "synthetic": synthetic,
        "environment": {
            "gameBuild": "test-build",
            "serverSettings": {"HarvestAmountMultiplier": 1.0},
            "mods": [],
            "map": "TheIsland",
            "notes": "Controlled test payload.",
        },
        "subject": {
            "nodeId": "node",
            "nodeResourceId": "resource",
            "speciesKey": "AnKy",
            "creatureObjectPath": "/Game/Dinos/Anky.Anky_C",
            "attackIndex": 2,
        },
        "staticModel": {
            "modelVersion": YIELD_MODEL_VERSION,
            "extractorVersion": "extractor/v1",
            "policyVersion": "policy/v2",
            "nodeCatalogRevision": "1" * 64,
            "evaluationCatalogRevision": "2" * 64,
            "componentCatalogRevision": "3" * 64,
        },
        "trials": [
            {
                "trialId": "one",
                "durationSeconds": 4.0,
                "hits": [],
                "finalResourceUnits": 8.0,
            },
            {
                "trialId": "two",
                "durationSeconds": 5.0,
                "hits": [],
                "finalResourceUnits": 10.0,
            },
        ],
    }


class HarvestRuntimeObservationsV2Tests(unittest.TestCase):
    def test_validator_derives_node_and_second_metrics_from_trials(self):
        result = validate_harvest_runtime_observation(_payload(synthetic=False))

        self.assertEqual(result["observedYieldPerNode"], 9.0)
        self.assertEqual(result["observedYieldPerSecond"], 2.0)
        self.assertEqual(result["runtimeStatus"], RUNTIME_STATUS_OBSERVED)
        self.assertEqual(result["subject"]["speciesKey"], "anky")

    def test_synthetic_is_valid_as_a_fixture_but_excluded_from_public_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "synthetic.json").write_text(
                json.dumps(_payload(synthetic=True)), encoding="utf-8"
            )
            index = load_harvest_runtime_observations(root)

        self.assertEqual(index.rows, {})
        self.assertEqual(index.synthetic_excluded, 1)

    def test_wrong_model_identity_and_duplicate_exact_keys_fail_closed(self):
        wrong = _payload(synthetic=False)
        wrong["staticModel"]["modelVersion"] = "stale-model"
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_harvest_runtime_observation(wrong)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("one.json", "two.json"):
                payload = deepcopy(_payload(synthetic=False))
                payload["observationSetId"] = f"runtime://test/{name}"
                (root / name).write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate exact"):
                load_harvest_runtime_observations(root)

    def test_active_dataset_identity_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "real.json").write_text(
                json.dumps(_payload(synthetic=False)), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "does not match the active dataset"):
                load_harvest_runtime_observations(
                    root,
                    expected_identity={"nodeCatalogRevision": "9" * 64},
                )


if __name__ == "__main__":
    unittest.main()
