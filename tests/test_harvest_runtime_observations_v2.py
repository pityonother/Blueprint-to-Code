from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.harvest_runtime_observations import (  # noqa: E402
    HarvestRuntimeProfileError,
    canonical_environment_fingerprint,
    load_harvest_runtime_observations,
    validate_harvest_runtime_observation,
)
from blueprint_translator.harvest_ranking import YIELD_MODEL_VERSION  # noqa: E402


PROFILE_ISLAND = "profile://test/island-1x"
PROFILE_SCORCHED = "profile://test/scorched-2x"
CANONICAL_ENVIRONMENT_FIELDS = (
    "gameBuild",
    "map",
    "sessionType",
    "HarvestAmountMultiplier",
    "otherHarvestMultipliers",
    "mods",
    "creature",
    "buffs",
    "genes",
    "worldState",
    "nodeFreshnessContract",
    "measurementMethod",
)


def _canonical_environment_fingerprint(environment: dict[str, object]) -> str:
    canonical_environment = {
        name: environment[name] for name in CANONICAL_ENVIRONMENT_FIELDS
    }
    canonical = json.dumps(
        canonical_environment,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _environment(
    *,
    game_build: str = "test-build",
    map_name: str = "TheIsland",
    harvest_amount_multiplier: float = 1.0,
) -> dict[str, object]:
    return {
        "gameBuild": game_build,
        "map": map_name,
        "sessionType": "SINGLE_PLAYER",
        "HarvestAmountMultiplier": harvest_amount_multiplier,
        "otherHarvestMultipliers": {
            "DinoHarvestingDamageMultiplier": 1.0,
            "PlayerHarvestingDamageMultiplier": 1.0,
        },
        "mods": [{"id": "mod-test", "version": "1.0.0"}],
        "creature": {
            "level": 150,
            "meleePercent": 100.0,
            "relevantStats": {"weight": 500.0},
        },
        "buffs": [],
        "genes": [],
        "worldState": {
            "mission": "NONE",
            "weather": "CLEAR",
        },
        "nodeFreshnessContract": "FRESH_COMPLETE_NODE",
        "measurementMethod": {
            "kind": "INVENTORY_DELTA",
            "randomQuantity": False,
            "recommendedSampleCount": 3,
        },
        "notes": "Controlled test payload.",
    }


def _payload(
    *,
    synthetic: bool,
    runtime_profile_id: str = PROFILE_ISLAND,
    environment: dict[str, object] | None = None,
    trial_count: int = 3,
    species_key: str = "AnKy",
    creature_object_path: str = "/Game/Dinos/Anky.Anky_C",
    attack_index: int = 2,
) -> dict[str, object]:
    runtime_environment = deepcopy(environment or _environment())
    return {
        "schema": "blueprint-to-code.harvest-runtime-observation/v2",
        "observationSetId": "runtime://test/exact-row",
        "runtimeProfileId": runtime_profile_id,
        "environmentFingerprint": _canonical_environment_fingerprint(
            runtime_environment
        ),
        "synthetic": synthetic,
        "environment": runtime_environment,
        "subject": {
            "nodeId": "node",
            "nodeResourceId": "resource",
            "speciesKey": species_key,
            "creatureObjectPath": creature_object_path,
            "attackIndex": attack_index,
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
                "trialId": f"trial-{index + 1}",
                "durationSeconds": (8.0 + index) / 2.0,
                "hits": [],
                "finalResourceUnits": 8.0 + index,
            }
            for index in range(trial_count)
        ],
    }


class HarvestRuntimeObservationsV2Tests(unittest.TestCase):
    def test_validator_derives_node_and_second_metrics_from_trials(self):
        result = validate_harvest_runtime_observation(_payload(synthetic=False))

        self.assertEqual(result["observedYieldPerNode"], 9.0)
        self.assertEqual(result["observedYieldPerSecond"], 2.0)
        self.assertEqual(result["runtimeStatus"], "OBSERVED_CONFIRMED")
        self.assertEqual(result["subject"]["speciesKey"], "anky")

    def test_runtime_profile_id_and_environment_fingerprint_are_required(self):
        payload = _payload(synthetic=False)

        for field in ("runtimeProfileId", "environmentFingerprint"):
            with self.subTest(field=field):
                missing = deepcopy(payload)
                missing.pop(field)
                with self.assertRaisesRegex(ValueError, field):
                    validate_harvest_runtime_observation(missing)

    def test_environment_fingerprint_is_canonical_and_rejects_environment_drift(self):
        payload = _payload(synthetic=False)
        environment = payload["environment"]
        assert isinstance(environment, dict)
        payload["environment"] = {
            key: environment[key] for key in reversed(list(environment))
        }

        validated = validate_harvest_runtime_observation(payload)
        self.assertEqual(
            validated["environmentFingerprint"],
            _canonical_environment_fingerprint(environment),
        )

        drifted = deepcopy(payload)
        drifted["environment"]["map"] = "ScorchedEarth"
        with self.assertRaisesRegex(ValueError, "environmentFingerprint.*environment"):
            validate_harvest_runtime_observation(drifted)

        uppercase = deepcopy(payload)
        uppercase["environmentFingerprint"] = str(
            uppercase["environmentFingerprint"]
        ).upper()
        with self.assertRaisesRegex(ValueError, "lowercase SHA-256"):
            validate_harvest_runtime_observation(uppercase)

    def test_every_schema_closed_object_rejects_unknown_fields(self):
        schema = json.loads(
            (ROOT / "schemas" / "harvest_runtime_observation_v2.schema.json").read_text(
                encoding="utf-8"
            )
        )

        def closed_object_paths(
            node: object,
            path: str = "$",
        ) -> set[str]:
            if not isinstance(node, dict):
                return set()
            paths = {path} if node.get("additionalProperties") is False else set()
            properties = node.get("properties")
            if isinstance(properties, dict):
                for name, child in properties.items():
                    paths.update(closed_object_paths(child, f"{path}.{name}"))
            items = node.get("items")
            if isinstance(items, dict):
                paths.update(closed_object_paths(items, f"{path}[]"))
            return paths

        cases: tuple[tuple[str, tuple[str | int, ...]], ...] = (
            ("$", ()),
            ("$.environment", ("environment",)),
            ("$.environment.mods[]", ("environment", "mods", 0)),
            ("$.environment.creature", ("environment", "creature")),
            (
                "$.environment.measurementMethod",
                ("environment", "measurementMethod"),
            ),
            ("$.subject", ("subject",)),
            ("$.staticModel", ("staticModel",)),
            ("$.trials[]", ("trials", 0)),
        )
        self.assertEqual(
            closed_object_paths(schema),
            {schema_path for schema_path, _path in cases},
        )

        for schema_path, object_path in cases:
            with self.subTest(schema_path=schema_path):
                payload = _payload(synthetic=False)
                target: object = payload
                for part in object_path:
                    if isinstance(part, int):
                        assert isinstance(target, list)
                        target = target[part]
                    else:
                        assert isinstance(target, dict)
                        target = target[part]
                assert isinstance(target, dict)
                target["unexpectedField"] = "must fail closed"
                if object_path and object_path[0] == "environment":
                    payload["environmentFingerprint"] = (
                        _canonical_environment_fingerprint(payload["environment"])
                    )
                with self.assertRaisesRegex(ValueError, "unknown field"):
                    validate_harvest_runtime_observation(payload)

    def test_all_numeric_inputs_reject_nan_and_infinity(self):
        numeric_paths: tuple[tuple[str, tuple[str | int, ...]], ...] = (
            (
                "environment.HarvestAmountMultiplier",
                ("environment", "HarvestAmountMultiplier"),
            ),
            (
                "environment.otherHarvestMultipliers",
                (
                    "environment",
                    "otherHarvestMultipliers",
                    "DinoHarvestingDamageMultiplier",
                ),
            ),
            ("environment.creature.level", ("environment", "creature", "level")),
            (
                "environment.creature.meleePercent",
                ("environment", "creature", "meleePercent"),
            ),
            (
                "environment.creature.relevantStats",
                ("environment", "creature", "relevantStats", "weight"),
            ),
            (
                "environment.worldState.dynamicNumber",
                ("environment", "worldState", "dynamicNumber"),
            ),
            (
                "environment.measurementMethod.recommendedSampleCount",
                ("environment", "measurementMethod", "recommendedSampleCount"),
            ),
            ("subject.attackIndex", ("subject", "attackIndex")),
            ("trials.durationSeconds", ("trials", 0, "durationSeconds")),
            ("trials.finalResourceUnits", ("trials", 0, "finalResourceUnits")),
        )

        def assign_path(
            payload: dict[str, object],
            path: tuple[str | int, ...],
            value: float,
        ) -> None:
            target: object = payload
            for part in path[:-1]:
                if isinstance(part, int):
                    assert isinstance(target, list)
                    target = target[part]
                else:
                    assert isinstance(target, dict)
                    target = target[part]
            final = path[-1]
            if isinstance(final, int):
                assert isinstance(target, list)
                target[final] = value
            else:
                assert isinstance(target, dict)
                target[final] = value

        for special in (float("nan"), float("inf"), float("-inf")):
            for label, path in numeric_paths:
                with self.subTest(label=label, special=repr(special)):
                    payload = _payload(synthetic=False)
                    assign_path(payload, path, special)
                    if path[0] == "environment":
                        payload["environmentFingerprint"] = (
                            _canonical_environment_fingerprint(payload["environment"])
                        )
                    with self.assertRaises(ValueError):
                        validate_harvest_runtime_observation(payload)

            with self.subTest(label="trials.hits.dynamicNumber", special=repr(special)):
                payload = _payload(synthetic=False)
                payload["trials"][0]["hits"] = [{"damageShown": special}]
                with self.assertRaises(ValueError):
                    validate_harvest_runtime_observation(payload)

    def test_fingerprint_requires_a_fully_validated_canonical_environment(self):
        environment = _environment()
        expected = _canonical_environment_fingerprint(environment)

        self.assertEqual(canonical_environment_fingerprint(environment), expected)

        notes_changed = deepcopy(environment)
        notes_changed["notes"] = "Notes are validated but not fingerprinted."
        self.assertEqual(canonical_environment_fingerprint(notes_changed), expected)

        unknown = deepcopy(environment)
        unknown["unexpectedField"] = "must not be silently omitted"
        with self.assertRaisesRegex(ValueError, "unknown field"):
            canonical_environment_fingerprint(unknown)

        incomplete = deepcopy(environment)
        incomplete.pop("map")
        with self.assertRaisesRegex(ValueError, "environment.map is required"):
            canonical_environment_fingerprint(incomplete)

        non_finite = deepcopy(environment)
        non_finite["worldState"]["dynamicNumber"] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            canonical_environment_fingerprint(non_finite)

    def test_checked_in_schema_and_example_expose_the_complete_v2_contract(self):
        schema = json.loads(
            (ROOT / "schemas" / "harvest_runtime_observation_v2.schema.json").read_text(
                encoding="utf-8"
            )
        )
        example = json.loads(
            (
                ROOT
                / "examples"
                / "harvest_runtime_observation_v2.example.json"
            ).read_text(encoding="utf-8")
        )

        self.assertTrue(
            {"runtimeProfileId", "environmentFingerprint"}.issubset(
                schema["required"]
            )
        )
        self.assertEqual(
            set(schema["properties"]["environment"]["required"]),
            set(CANONICAL_ENVIRONMENT_FIELDS),
        )
        validated = validate_harvest_runtime_observation(example)
        self.assertTrue(validated["synthetic"])
        self.assertEqual(validated["runtimeStatus"], "SYNTHETIC_NOT_PUBLISHABLE")
        self.assertEqual(
            validated["environmentFingerprint"],
            _canonical_environment_fingerprint(example["environment"]),
        )

    def test_canonical_environment_requires_every_comparability_dimension(self):
        payload = _payload(synthetic=False)

        for field in CANONICAL_ENVIRONMENT_FIELDS:
            with self.subTest(field=field):
                missing = deepcopy(payload)
                missing["environment"].pop(field)
                with self.assertRaisesRegex(ValueError, f"environment.{field}"):
                    validate_harvest_runtime_observation(missing)

    def test_random_quantity_sample_recommendation_is_validated_but_never_fabricated(self):
        payload = _payload(synthetic=False, trial_count=3)
        payload["environment"]["measurementMethod"].update(
            {
                "randomQuantity": True,
                "recommendedSampleCount": 20,
            }
        )
        payload["environmentFingerprint"] = _canonical_environment_fingerprint(
            payload["environment"]
        )

        result = validate_harvest_runtime_observation(payload)

        self.assertEqual(result["trialCount"], 3)
        self.assertEqual(
            result["environment"]["measurementMethod"]["recommendedSampleCount"],
            20,
        )

        invalid = deepcopy(payload)
        invalid["environment"]["measurementMethod"]["recommendedSampleCount"] = 0
        invalid["environmentFingerprint"] = _canonical_environment_fingerprint(
            invalid["environment"]
        )
        with self.assertRaisesRegex(ValueError, "recommendedSampleCount"):
            validate_harvest_runtime_observation(invalid)

    def test_same_runtime_profile_rejects_conflicting_environment_fingerprints(self):
        island = _payload(synthetic=False)
        scorched = _payload(
            synthetic=False,
            runtime_profile_id=PROFILE_ISLAND,
            environment=_environment(
                map_name="ScorchedEarth",
                harvest_amount_multiplier=2.0,
            ),
            species_key="Doed",
            creature_object_path="/Game/Dinos/Doed.Doed_C",
            attack_index=0,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "island.json").write_text(json.dumps(island), encoding="utf-8")
            (root / "scorched.json").write_text(
                json.dumps(scorched), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                ValueError,
                "runtimeProfileId.*conflicting environmentFingerprint",
            ):
                load_harvest_runtime_observations(root)

    def test_different_runtime_profiles_can_hold_the_same_subject_without_mixing(self):
        island = _payload(synthetic=False, runtime_profile_id=PROFILE_ISLAND)
        scorched = _payload(
            synthetic=False,
            runtime_profile_id=PROFILE_SCORCHED,
            environment=_environment(
                map_name="ScorchedEarth",
                harvest_amount_multiplier=2.0,
            ),
        )
        for trial in scorched["trials"]:
            trial["finalResourceUnits"] += 12.0
            trial["durationSeconds"] = trial["finalResourceUnits"] / 2.0

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "island.json").write_text(json.dumps(island), encoding="utf-8")
            (root / "scorched.json").write_text(
                json.dumps(scorched), encoding="utf-8"
            )
            island_index = load_harvest_runtime_observations(
                root,
                runtime_profile_id=PROFILE_ISLAND,
            )
            scorched_index = load_harvest_runtime_observations(
                root,
                runtime_profile_id=PROFILE_SCORCHED,
            )

        island_row = next(iter(island_index.rows.values()))
        scorched_row = next(iter(scorched_index.rows.values()))
        self.assertEqual(island_row["runtimeProfileId"], PROFILE_ISLAND)
        self.assertEqual(scorched_row["runtimeProfileId"], PROFILE_SCORCHED)
        self.assertEqual(island_row["observedYieldPerNode"], 9.0)
        self.assertEqual(scorched_row["observedYieldPerNode"], 21.0)

    def test_one_trial_is_preliminary_and_three_trials_are_confirmed(self):
        preliminary = validate_harvest_runtime_observation(
            _payload(synthetic=False, trial_count=1)
        )
        confirmed = validate_harvest_runtime_observation(
            _payload(synthetic=False, trial_count=3)
        )

        self.assertEqual(preliminary["trialCount"], 1)
        self.assertEqual(preliminary["runtimeStatus"], "OBSERVED_PRELIMINARY")
        self.assertEqual(confirmed["trialCount"], 3)
        self.assertEqual(confirmed["runtimeStatus"], "OBSERVED_CONFIRMED")

    def test_preliminary_rows_are_excluded_by_default_and_explicitly_opted_in(self):
        payload = _payload(synthetic=False, trial_count=1)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "preliminary.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            default_index = load_harvest_runtime_observations(root)
            opted_in_index = load_harvest_runtime_observations(
                root,
                include_preliminary=True,
            )

        self.assertEqual(default_index.rows, {})
        self.assertEqual(len(opted_in_index.rows), 1)
        self.assertEqual(
            next(iter(opted_in_index.rows.values()))["runtimeStatus"],
            "OBSERVED_PRELIMINARY",
        )

    def test_unique_runtime_profile_is_selected_automatically(self):
        payload = _payload(synthetic=False)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "confirmed.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            index = load_harvest_runtime_observations(root)
            discovery = load_harvest_runtime_observations(
                root,
                allow_unselected_profiles=True,
            )

        self.assertEqual(index.coverage["runtimeProfilesAvailable"], [PROFILE_ISLAND])
        self.assertEqual(index.coverage["runtimeProfileSelected"], PROFILE_ISLAND)
        self.assertEqual(discovery.rows, {})
        self.assertIsNone(discovery.coverage["runtimeProfileSelected"])

    def test_multiple_runtime_profiles_require_explicit_selection(self):
        island = _payload(synthetic=False, runtime_profile_id=PROFILE_ISLAND)
        scorched = _payload(
            synthetic=False,
            runtime_profile_id=PROFILE_SCORCHED,
            environment=_environment(
                map_name="ScorchedEarth",
                harvest_amount_multiplier=2.0,
            ),
            species_key="Doed",
            creature_object_path="/Game/Dinos/Doed.Doed_C",
            attack_index=0,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "island.json").write_text(json.dumps(island), encoding="utf-8")
            (root / "scorched.json").write_text(
                json.dumps(scorched), encoding="utf-8"
            )
            with self.assertRaises(HarvestRuntimeProfileError) as raised:
                load_harvest_runtime_observations(root)

        self.assertEqual(
            raised.exception.code,
            "HARVEST_RUNTIME_PROFILE_REQUIRED",
        )
        self.assertIn("runtimeProfileId", str(raised.exception))

    def test_unknown_explicit_runtime_profile_has_stable_error_code(self):
        payload = _payload(synthetic=False, runtime_profile_id=PROFILE_ISLAND)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "island.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            with self.assertRaises(HarvestRuntimeProfileError) as raised:
                load_harvest_runtime_observations(
                    root,
                    runtime_profile_id="profile://test/not-found",
                )

        self.assertEqual(
            raised.exception.code,
            "HARVEST_RUNTIME_PROFILE_NOT_FOUND",
        )
        self.assertIn("runtimeProfileId", str(raised.exception))

    def test_static_discovery_can_list_profiles_without_selecting_or_loading_rows(self):
        island = _payload(synthetic=False, runtime_profile_id=PROFILE_ISLAND)
        scorched = _payload(
            synthetic=False,
            runtime_profile_id=PROFILE_SCORCHED,
            environment=_environment(
                map_name="ScorchedEarth",
                harvest_amount_multiplier=2.0,
            ),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "island.json").write_text(json.dumps(island), encoding="utf-8")
            (root / "scorched.json").write_text(
                json.dumps(scorched), encoding="utf-8"
            )
            index = load_harvest_runtime_observations(
                root,
                allow_unselected_profiles=True,
            )

        self.assertEqual(index.rows, {})
        self.assertEqual(
            index.coverage["runtimeProfilesAvailable"],
            [PROFILE_ISLAND, PROFILE_SCORCHED],
        )
        self.assertIsNone(index.coverage["runtimeProfileSelected"])

    def test_runtime_coverage_reports_profiles_tiers_and_exclusions(self):
        confirmed = _payload(synthetic=False, runtime_profile_id=PROFILE_ISLAND)
        preliminary = _payload(
            synthetic=False,
            runtime_profile_id=PROFILE_ISLAND,
            trial_count=1,
            species_key="Doed",
            creature_object_path="/Game/Dinos/Doed.Doed_C",
            attack_index=0,
        )
        other_profile = _payload(
            synthetic=False,
            runtime_profile_id=PROFILE_SCORCHED,
            environment=_environment(
                map_name="ScorchedEarth",
                harvest_amount_multiplier=2.0,
            ),
        )
        synthetic = _payload(
            synthetic=True,
            runtime_profile_id=PROFILE_ISLAND,
            species_key="Synthetic",
            creature_object_path="/Game/Dinos/Synthetic.Synthetic_C",
            attack_index=0,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name, payload in (
                ("confirmed.json", confirmed),
                ("preliminary.json", preliminary),
                ("other-profile.json", other_profile),
                ("synthetic.json", synthetic),
            ):
                (root / name).write_text(json.dumps(payload), encoding="utf-8")
            index = load_harvest_runtime_observations(
                root,
                runtime_profile_id=PROFILE_ISLAND,
            )

        self.assertEqual(
            index.coverage,
            {
                "runtimeProfilesAvailable": [PROFILE_ISLAND, PROFILE_SCORCHED],
                "runtimeProfileSelected": PROFILE_ISLAND,
                "publishableConfirmedRows": 1,
                "preliminaryRows": 1,
                "syntheticExcluded": 1,
                "profileMismatchExcluded": 1,
            },
        )

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
