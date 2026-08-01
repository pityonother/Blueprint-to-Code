"""Validate and index exact, non-synthetic Harvest runtime observations."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .harvest_ranking import YIELD_MODEL_VERSION


HARVEST_RUNTIME_OBSERVATION_SCHEMA = (
    "blueprint-to-code.harvest-runtime-observation/v2"
)
RUNTIME_STATUS_OBSERVED = "OBSERVED_CONTROLLED_ENVIRONMENT"
RUNTIME_STATUS_NOT_MEASURED = "NOT_MEASURED"


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _text(value: object, label: str) -> str:
    text = str(value or "").strip()
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


def validate_harvest_runtime_observation(
    payload: Mapping[str, Any],
    *,
    expected_model_version: str = YIELD_MODEL_VERSION,
) -> dict[str, Any]:
    """Validate exact identities and derive bounded observation summaries."""

    if payload.get("schema") != HARVEST_RUNTIME_OBSERVATION_SCHEMA:
        raise ValueError(f"schema must be {HARVEST_RUNTIME_OBSERVATION_SCHEMA}")
    observation_set_id = _text(payload.get("observationSetId"), "observationSetId")
    if not observation_set_id.startswith("runtime://"):
        raise ValueError("observationSetId must use runtime://")
    synthetic = payload.get("synthetic")
    if not isinstance(synthetic, bool):
        raise ValueError("synthetic must be a boolean")
    environment = _object(payload.get("environment"), "environment")
    for name in ("gameBuild", "map", "notes"):
        _text(environment.get(name), f"environment.{name}")
    if not isinstance(environment.get("serverSettings"), Mapping):
        raise ValueError("environment.serverSettings must be an object")
    mods = environment.get("mods")
    if not isinstance(mods, list) or any(not isinstance(value, str) for value in mods):
        raise ValueError("environment.mods must be an array of strings")

    subject = _object(payload.get("subject"), "subject")
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
        yield_per_node.append(final_units)
        yield_per_second.append(final_units / duration)

    return {
        "schema": HARVEST_RUNTIME_OBSERVATION_SCHEMA,
        "observationSetId": observation_set_id,
        "synthetic": synthetic,
        "subject": subject_fields,
        "modelVersion": model_version,
        "staticIdentity": static_identity,
        "trialCount": len(trials),
        "observedYieldPerNode": statistics.fmean(yield_per_node),
        "observedYieldPerSecond": statistics.fmean(yield_per_second),
        "runtimeStatus": (
            "SYNTHETIC_NOT_PUBLISHABLE" if synthetic else RUNTIME_STATUS_OBSERVED
        ),
    }


@dataclass(frozen=True)
class HarvestRuntimeObservationIndex:
    rows: dict[tuple[str, str, str, str, int], dict[str, Any]]
    revision: str
    files_scanned: int
    synthetic_excluded: int


def load_harvest_runtime_observations(
    root: Path,
    *,
    expected_model_version: str = YIELD_MODEL_VERSION,
    expected_identity: Mapping[str, str] | None = None,
) -> HarvestRuntimeObservationIndex:
    """Load exact public overlays; synthetic examples remain excluded."""

    root = Path(root)
    if not root.exists():
        return HarvestRuntimeObservationIndex({}, hashlib.sha256(b"").hexdigest(), 0, 0)
    if not root.is_dir():
        raise ValueError("Harvest runtime observation root must be a directory")
    rows: dict[tuple[str, str, str, str, int], dict[str, Any]] = {}
    fingerprints: list[str] = []
    files_scanned = 0
    synthetic_excluded = 0
    for path in sorted(root.glob("*.json"), key=lambda value: value.name.casefold()):
        files_scanned += 1
        raw = path.read_bytes()
        fingerprints.append(f"{path.name}:{hashlib.sha256(raw).hexdigest()}")
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid runtime observation {path.name}: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise ValueError(f"Invalid runtime observation {path.name}: root must be an object")
        validated = validate_harvest_runtime_observation(
            payload, expected_model_version=expected_model_version
        )
        if expected_identity is not None:
            actual_identity = validated["staticIdentity"]
            for name, expected in expected_identity.items():
                if str(actual_identity.get(name) or "") != str(expected or ""):
                    raise ValueError(
                        f"Runtime observation {path.name} {name} does not match the active dataset"
                    )
        if validated["synthetic"]:
            synthetic_excluded += 1
            continue
        subject = validated["subject"]
        key = (
            str(subject["nodeId"]),
            str(subject["nodeResourceId"]),
            str(subject["speciesKey"]).casefold(),
            str(subject["creatureObjectPath"]).casefold(),
            int(subject["attackIndex"]),
        )
        if key in rows:
            raise ValueError(
                "Duplicate exact runtime ranking identity; combine its trials in one set"
            )
        rows[key] = validated
    revision = hashlib.sha256("\n".join(fingerprints).encode("utf-8")).hexdigest()
    return HarvestRuntimeObservationIndex(
        rows=rows,
        revision=revision,
        files_scanned=files_scanned,
        synthetic_excluded=synthetic_excluded,
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
    "HARVEST_RUNTIME_OBSERVATION_SCHEMA",
    "HarvestRuntimeObservationIndex",
    "RUNTIME_STATUS_NOT_MEASURED",
    "RUNTIME_STATUS_OBSERVED",
    "load_harvest_runtime_observations",
    "observation_for_ranking_row",
    "validate_harvest_runtime_observation",
]
