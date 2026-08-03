"""Validate and index exact, non-synthetic Harvest runtime observations."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .harvest_ranking import YIELD_MODEL_VERSION


HARVEST_RUNTIME_OBSERVATION_SCHEMA = (
    "blueprint-to-code.harvest-runtime-observation/v2"
)
MINIMUM_CONFIRMED_TRIALS = 3
RUNTIME_STATUS_OBSERVED_PRELIMINARY = "OBSERVED_PRELIMINARY"
RUNTIME_STATUS_OBSERVED_CONFIRMED = "OBSERVED_CONFIRMED"
# Compatibility alias for consumers that imported the former single observed tier.
RUNTIME_STATUS_OBSERVED = RUNTIME_STATUS_OBSERVED_CONFIRMED
RUNTIME_STATUS_NOT_MEASURED = "NOT_MEASURED"

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

_ROOT_FIELDS = frozenset(
    {
        "schema",
        "observationSetId",
        "runtimeProfileId",
        "environmentFingerprint",
        "synthetic",
        "environment",
        "subject",
        "staticModel",
        "trials",
    }
)
_ENVIRONMENT_FIELDS = frozenset({*CANONICAL_ENVIRONMENT_FIELDS, "notes"})
_MOD_FIELDS = frozenset({"id", "version"})
_CREATURE_FIELDS = frozenset({"level", "meleePercent", "relevantStats"})
_MEASUREMENT_METHOD_FIELDS = frozenset(
    {"kind", "randomQuantity", "recommendedSampleCount"}
)
_SUBJECT_FIELDS = frozenset(
    {"nodeId", "nodeResourceId", "speciesKey", "creatureObjectPath", "attackIndex"}
)
_STATIC_MODEL_FIELDS = frozenset(
    {
        "modelVersion",
        "extractorVersion",
        "policyVersion",
        "nodeCatalogRevision",
        "evaluationCatalogRevision",
        "componentCatalogRevision",
    }
)
_TRIAL_FIELDS = frozenset(
    {"trialId", "durationSeconds", "hits", "finalResourceUnits"}
)


class HarvestRuntimeProfileError(ValueError):
    """A stable, machine-readable runtime-profile selection failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _reject_unknown_fields(
    value: Mapping[str, Any],
    allowed_fields: frozenset[str],
    label: str,
) -> None:
    unknown = sorted(
        (repr(name) for name in value if not isinstance(name, str) or name not in allowed_fields)
    )
    if unknown:
        suffix = "s" if len(unknown) != 1 else ""
        raise ValueError(
            f"{label} contains unknown field{suffix}: {', '.join(unknown)}"
        )


def _validate_json_value(value: object, label: str) -> None:
    """Reject non-JSON values and every non-finite number, including open objects."""

    if value is None or isinstance(value, (bool, str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} must be finite")
        return
    if isinstance(value, Mapping):
        for name, child in value.items():
            if not isinstance(name, str):
                raise ValueError(f"{label} object keys must be strings")
            if not name.strip():
                raise ValueError(f"{label} object keys cannot be empty")
            _validate_json_value(child, f"{label}.{name}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_json_value(child, f"{label}[{index}]")
        return
    raise ValueError(f"{label} must contain only JSON values")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _finite(value: object, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    if positive and number <= 0:
        raise ValueError(f"{label} must be positive")
    if not positive and number < 0:
        raise ValueError(f"{label} cannot be negative")
    return number


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _string_array(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    for index, item in enumerate(value):
        _text(item, f"{label}[{index}]")
        if not isinstance(item, str):
            raise ValueError(f"{label}[{index}] must be a string")
    return value


def canonical_environment_fingerprint(environment: Mapping[str, Any]) -> str:
    """Return the v2 SHA-256 identity for comparable runtime conditions."""

    validated = _validate_environment(_object(environment, "environment"))
    return _validated_environment_fingerprint(validated)


def _validated_environment_fingerprint(environment: Mapping[str, Any]) -> str:
    """Hash an environment only after the caller completed strict validation."""

    canonical_environment: dict[str, Any] = {}
    for field in CANONICAL_ENVIRONMENT_FIELDS:
        if field not in environment:
            raise ValueError(f"environment.{field} is required")
        canonical_environment[field] = environment[field]
    try:
        canonical = json.dumps(
            canonical_environment,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("environment must contain canonical JSON values") from exc
    return hashlib.sha256(canonical).hexdigest()


def _validate_environment(environment: Mapping[str, Any]) -> dict[str, Any]:
    _reject_unknown_fields(environment, _ENVIRONMENT_FIELDS, "environment")
    _validate_json_value(environment, "environment")
    for field in CANONICAL_ENVIRONMENT_FIELDS:
        if field not in environment:
            raise ValueError(f"environment.{field} is required")

    for field in (
        "gameBuild",
        "map",
        "sessionType",
        "nodeFreshnessContract",
    ):
        _text(environment.get(field), f"environment.{field}")

    _finite(
        environment.get("HarvestAmountMultiplier"),
        "environment.HarvestAmountMultiplier",
    )

    other_multipliers = _object(
        environment.get("otherHarvestMultipliers"),
        "environment.otherHarvestMultipliers",
    )
    for name, value in other_multipliers.items():
        _text(name, "environment.otherHarvestMultipliers key")
        _finite(value, f"environment.otherHarvestMultipliers.{name}")

    mods = environment.get("mods")
    if not isinstance(mods, list):
        raise ValueError("environment.mods must be an array")
    mod_ids: set[str] = set()
    for index, raw_mod in enumerate(mods):
        mod = _object(raw_mod, f"environment.mods[{index}]")
        _reject_unknown_fields(mod, _MOD_FIELDS, f"environment.mods[{index}]")
        mod_id = _text(mod.get("id"), f"environment.mods[{index}].id")
        _text(mod.get("version"), f"environment.mods[{index}].version")
        if mod_id.casefold() in mod_ids:
            raise ValueError(f"duplicate environment.mods id: {mod_id}")
        mod_ids.add(mod_id.casefold())

    creature = _object(environment.get("creature"), "environment.creature")
    _reject_unknown_fields(creature, _CREATURE_FIELDS, "environment.creature")
    _positive_integer(creature.get("level"), "environment.creature.level")
    _finite(creature.get("meleePercent"), "environment.creature.meleePercent")
    relevant_stats = _object(
        creature.get("relevantStats"),
        "environment.creature.relevantStats",
    )
    for name, value in relevant_stats.items():
        _text(name, "environment.creature.relevantStats key")
        _finite(value, f"environment.creature.relevantStats.{name}")

    _string_array(environment.get("buffs"), "environment.buffs")
    _string_array(environment.get("genes"), "environment.genes")

    world_state = _object(environment.get("worldState"), "environment.worldState")
    for name in world_state:
        _text(name, "environment.worldState key")

    measurement = _object(
        environment.get("measurementMethod"),
        "environment.measurementMethod",
    )
    _reject_unknown_fields(
        measurement,
        _MEASUREMENT_METHOD_FIELDS,
        "environment.measurementMethod",
    )
    _text(measurement.get("kind"), "environment.measurementMethod.kind")
    if not isinstance(measurement.get("randomQuantity"), bool):
        raise ValueError(
            "environment.measurementMethod.randomQuantity must be a boolean"
        )
    _positive_integer(
        measurement.get("recommendedSampleCount"),
        "environment.measurementMethod.recommendedSampleCount",
    )

    if "notes" in environment:
        _text(environment.get("notes"), "environment.notes")
    return copy.deepcopy(dict(environment))


def validate_harvest_runtime_observation(
    payload: Mapping[str, Any],
    *,
    expected_model_version: str = YIELD_MODEL_VERSION,
) -> dict[str, Any]:
    """Validate exact identities and derive bounded observation summaries."""

    root = _object(payload, "root")
    _reject_unknown_fields(root, _ROOT_FIELDS, "root")
    _validate_json_value(root, "root")

    if payload.get("schema") != HARVEST_RUNTIME_OBSERVATION_SCHEMA:
        raise ValueError(f"schema must be {HARVEST_RUNTIME_OBSERVATION_SCHEMA}")
    observation_set_id = _text(payload.get("observationSetId"), "observationSetId")
    if not observation_set_id.startswith("runtime://"):
        raise ValueError("observationSetId must use runtime://")
    runtime_profile_id = _text(payload.get("runtimeProfileId"), "runtimeProfileId")
    environment_fingerprint = _text(
        payload.get("environmentFingerprint"), "environmentFingerprint"
    )
    if re.fullmatch(r"[0-9a-f]{64}", environment_fingerprint) is None:
        raise ValueError("environmentFingerprint must be a lowercase SHA-256 hex digest")
    synthetic = payload.get("synthetic")
    if not isinstance(synthetic, bool):
        raise ValueError("synthetic must be a boolean")

    environment = _validate_environment(
        _object(payload.get("environment"), "environment")
    )
    computed_fingerprint = _validated_environment_fingerprint(environment)
    if environment_fingerprint != computed_fingerprint:
        raise ValueError(
            "environmentFingerprint does not match canonical environment"
        )

    subject = _object(payload.get("subject"), "subject")
    _reject_unknown_fields(subject, _SUBJECT_FIELDS, "subject")
    subject_fields = {
        "nodeId": _text(subject.get("nodeId"), "subject.nodeId"),
        "nodeResourceId": _text(
            subject.get("nodeResourceId"), "subject.nodeResourceId"
        ),
        "speciesKey": _text(subject.get("speciesKey"), "subject.speciesKey").casefold(),
        "creatureObjectPath": _text(
            subject.get("creatureObjectPath"), "subject.creatureObjectPath"
        ),
        "attackIndex": subject.get("attackIndex"),
    }
    attack_index = subject_fields["attackIndex"]
    if (
        not isinstance(attack_index, int)
        or isinstance(attack_index, bool)
        or attack_index < 0
    ):
        raise ValueError("subject.attackIndex must be a non-negative integer")

    static_model = _object(payload.get("staticModel"), "staticModel")
    _reject_unknown_fields(static_model, _STATIC_MODEL_FIELDS, "staticModel")
    model_version = _text(
        static_model.get("modelVersion"), "staticModel.modelVersion"
    )
    if model_version != expected_model_version:
        raise ValueError("staticModel.modelVersion does not match this runtime")
    for name in (
        "extractorVersion",
        "policyVersion",
        "nodeCatalogRevision",
        "evaluationCatalogRevision",
        "componentCatalogRevision",
    ):
        _text(static_model.get(name), f"staticModel.{name}")
    static_identity = {
        name: str(static_model.get(name) or "")
        for name in (
            "extractorVersion",
            "policyVersion",
            "nodeCatalogRevision",
            "evaluationCatalogRevision",
            "componentCatalogRevision",
        )
    }

    trials = payload.get("trials")
    if not isinstance(trials, list) or not trials:
        raise ValueError("trials must contain at least one trial")
    trial_ids: set[str] = set()
    yield_per_node: list[float] = []
    yield_per_second: list[float] = []
    for index, raw_trial in enumerate(trials):
        trial = _object(raw_trial, f"trials[{index}]")
        _reject_unknown_fields(trial, _TRIAL_FIELDS, f"trials[{index}]")
        trial_id = _text(trial.get("trialId"), f"trials[{index}].trialId")
        if trial_id in trial_ids:
            raise ValueError(f"duplicate trialId: {trial_id}")
        trial_ids.add(trial_id)
        final_units = _finite(
            trial.get("finalResourceUnits"),
            f"trials[{index}].finalResourceUnits",
        )
        duration = _finite(
            trial.get("durationSeconds"),
            f"trials[{index}].durationSeconds",
            positive=True,
        )
        hits = trial.get("hits")
        if not isinstance(hits, list):
            raise ValueError(f"trials[{index}].hits must be an array")
        for hit_index, hit in enumerate(hits):
            _object(hit, f"trials[{index}].hits[{hit_index}]")
        yield_per_node.append(final_units)
        yield_per_second.append(final_units / duration)

    trial_count = len(trials)
    if synthetic:
        runtime_status = "SYNTHETIC_NOT_PUBLISHABLE"
    elif trial_count >= MINIMUM_CONFIRMED_TRIALS:
        runtime_status = RUNTIME_STATUS_OBSERVED_CONFIRMED
    else:
        runtime_status = RUNTIME_STATUS_OBSERVED_PRELIMINARY

    return {
        "schema": HARVEST_RUNTIME_OBSERVATION_SCHEMA,
        "observationSetId": observation_set_id,
        "runtimeProfileId": runtime_profile_id,
        "environmentFingerprint": environment_fingerprint,
        "environment": environment,
        "synthetic": synthetic,
        "subject": subject_fields,
        "modelVersion": model_version,
        "staticIdentity": static_identity,
        "trialCount": trial_count,
        "observedYieldPerNode": statistics.fmean(yield_per_node),
        "observedYieldPerSecond": statistics.fmean(yield_per_second),
        "runtimeStatus": runtime_status,
    }


@dataclass(frozen=True)
class HarvestRuntimeObservationIndex:
    rows: dict[tuple[str, str, str, str, int], dict[str, Any]]
    revision: str
    files_scanned: int
    synthetic_excluded: int
    runtime_profiles_available: tuple[str, ...] = ()
    runtime_profile_selected: str | None = None
    publishable_confirmed_rows: int = 0
    preliminary_rows: int = 0
    profile_mismatch_excluded: int = 0

    @property
    def coverage(self) -> dict[str, Any]:
        """Return the stable public runtime-coverage shape."""

        return {
            "runtimeProfilesAvailable": list(self.runtime_profiles_available),
            "runtimeProfileSelected": self.runtime_profile_selected,
            "publishableConfirmedRows": self.publishable_confirmed_rows,
            "preliminaryRows": self.preliminary_rows,
            "syntheticExcluded": self.synthetic_excluded,
            "profileMismatchExcluded": self.profile_mismatch_excluded,
        }


def _runtime_subject_key(
    validated: Mapping[str, Any],
) -> tuple[str, str, str, str, int]:
    subject = validated["subject"]
    return (
        str(subject["nodeId"]),
        str(subject["nodeResourceId"]),
        str(subject["speciesKey"]).casefold(),
        str(subject["creatureObjectPath"]).casefold(),
        int(subject["attackIndex"]),
    )


def load_harvest_runtime_observations(
    root: Path,
    *,
    expected_model_version: str = YIELD_MODEL_VERSION,
    expected_identity: Mapping[str, str] | None = None,
    runtime_profile_id: str | None = None,
    include_preliminary: bool = False,
    allow_unselected_profiles: bool = False,
) -> HarvestRuntimeObservationIndex:
    """Load an explicitly comparable runtime profile into the public overlay."""

    root = Path(root)
    if root.exists() and not root.is_dir():
        raise ValueError("Harvest runtime observation root must be a directory")

    fingerprints: list[str] = []
    files_scanned = 0
    validated_files: list[tuple[Path, dict[str, Any]]] = []
    profile_fingerprints: dict[str, str] = {}
    profile_subject_keys: set[
        tuple[str, tuple[str, str, str, str, int]]
    ] = set()

    paths = (
        sorted(root.glob("*.json"), key=lambda value: value.name.casefold())
        if root.exists()
        else []
    )
    for path in paths:
        files_scanned += 1
        raw = path.read_bytes()
        fingerprints.append(f"{path.name}:{hashlib.sha256(raw).hexdigest()}")
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid runtime observation {path.name}: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise ValueError(
                f"Invalid runtime observation {path.name}: root must be an object"
            )
        validated = validate_harvest_runtime_observation(
            payload, expected_model_version=expected_model_version
        )
        if expected_identity is not None:
            actual_identity = validated["staticIdentity"]
            for name, expected in expected_identity.items():
                if str(actual_identity.get(name) or "") != str(expected or ""):
                    raise ValueError(
                        f"Runtime observation {path.name} {name} "
                        "does not match the active dataset"
                    )

        profile_id = str(validated["runtimeProfileId"])
        profile_fingerprint = str(validated["environmentFingerprint"])
        previous_fingerprint = profile_fingerprints.setdefault(
            profile_id, profile_fingerprint
        )
        if previous_fingerprint != profile_fingerprint:
            raise ValueError(
                f"runtimeProfileId {profile_id!r} has conflicting "
                "environmentFingerprint values"
            )

        if not validated["synthetic"]:
            profile_key = (profile_id, _runtime_subject_key(validated))
            if profile_key in profile_subject_keys:
                raise ValueError(
                    "Duplicate exact runtime ranking identity within runtimeProfileId; "
                    "combine its trials in one set"
                )
            profile_subject_keys.add(profile_key)
        validated_files.append((path, validated))

    available_profiles = tuple(
        sorted(
            {
                str(validated["runtimeProfileId"])
                for _, validated in validated_files
                if not validated["synthetic"]
            }
        )
    )
    if runtime_profile_id is not None:
        requested_profile = str(runtime_profile_id).strip()
        if requested_profile not in available_profiles:
            raise HarvestRuntimeProfileError(
                "HARVEST_RUNTIME_PROFILE_NOT_FOUND",
                f"Requested runtimeProfileId {requested_profile!r} is not available.",
            )
        selected_profile: str | None = requested_profile
    elif allow_unselected_profiles:
        selected_profile = None
    elif len(available_profiles) > 1:
        raise HarvestRuntimeProfileError(
            "HARVEST_RUNTIME_PROFILE_REQUIRED",
            "Multiple runtime profiles are available; select runtimeProfileId.",
        )
    elif available_profiles:
        selected_profile = available_profiles[0]
    else:
        selected_profile = None

    rows: dict[tuple[str, str, str, str, int], dict[str, Any]] = {}
    synthetic_excluded = 0
    publishable_confirmed_rows = 0
    preliminary_rows = 0
    profile_mismatch_excluded = 0
    for _, validated in validated_files:
        if validated["synthetic"]:
            synthetic_excluded += 1
            continue
        if selected_profile is None:
            continue
        if validated["runtimeProfileId"] != selected_profile:
            profile_mismatch_excluded += 1
            continue
        if validated["runtimeStatus"] == RUNTIME_STATUS_OBSERVED_PRELIMINARY:
            preliminary_rows += 1
            if not include_preliminary:
                continue
        else:
            publishable_confirmed_rows += 1
        rows[_runtime_subject_key(validated)] = validated

    revision = hashlib.sha256("\n".join(fingerprints).encode("utf-8")).hexdigest()
    return HarvestRuntimeObservationIndex(
        rows=rows,
        revision=revision,
        files_scanned=files_scanned,
        synthetic_excluded=synthetic_excluded,
        runtime_profiles_available=available_profiles,
        runtime_profile_selected=selected_profile,
        publishable_confirmed_rows=publishable_confirmed_rows,
        preliminary_rows=preliminary_rows,
        profile_mismatch_excluded=profile_mismatch_excluded,
    )


def observation_for_ranking_row(
    index: HarvestRuntimeObservationIndex,
    *,
    node_id: str,
    node_resource_id: str,
    species_key: str,
    creature_object_path: str,
    attack_index: int,
) -> dict[str, Any] | None:
    return index.rows.get(
        (
            node_id,
            node_resource_id,
            species_key.casefold(),
            creature_object_path.casefold(),
            attack_index,
        )
    )


__all__ = [
    "CANONICAL_ENVIRONMENT_FIELDS",
    "HARVEST_RUNTIME_OBSERVATION_SCHEMA",
    "HarvestRuntimeObservationIndex",
    "HarvestRuntimeProfileError",
    "MINIMUM_CONFIRMED_TRIALS",
    "RUNTIME_STATUS_NOT_MEASURED",
    "RUNTIME_STATUS_OBSERVED",
    "RUNTIME_STATUS_OBSERVED_CONFIRMED",
    "RUNTIME_STATUS_OBSERVED_PRELIMINARY",
    "canonical_environment_fingerprint",
    "load_harvest_runtime_observations",
    "observation_for_ranking_row",
    "validate_harvest_runtime_observation",
]
