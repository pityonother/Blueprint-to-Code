"""Compare versioned static harvest predictions with bounded runtime observations.

Runtime observations are an overlay on the evidence-derived static model.  They
never rewrite Blueprint or native evidence, and synthetic fixtures are carried
through to every output so they cannot be mistaken for real game measurements.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from typing import Any

from .harvest_ranking import estimate_complete_node_yield


OBSERVATION_SCHEMA = "blueprint-to-code-runtime-observation-set/v1"
COMPARISON_SCHEMA = "blueprint-to-code-runtime-comparison/v1"

STATIC_REVERSED = "STATIC_REVERSED"
RUNTIME_CALIBRATED = "RUNTIME_CALIBRATED"
RUNTIME_CONFIRMED = "RUNTIME_CONFIRMED"
RUNTIME_DIVERGED = "RUNTIME_DIVERGED"
UNSUPPORTED_DYNAMIC_BRANCH = "UNSUPPORTED_DYNAMIC_BRANCH"
INSUFFICIENT_OBSERVATIONS = "INSUFFICIENT_OBSERVATIONS"


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _trial_values(payload: Mapping[str, Any]) -> list[float]:
    trials = payload.get("trials")
    if not isinstance(trials, list):
        raise ValueError("trials must be an array")
    values: list[float] = []
    trial_ids: set[str] = set()
    for index, trial in enumerate(trials):
        row = _mapping(trial, f"trials[{index}]")
        trial_id = str(row.get("trialId") or "").strip()
        if not trial_id:
            raise ValueError(f"trials[{index}].trialId is required")
        if trial_id in trial_ids:
            raise ValueError(f"duplicate trialId: {trial_id}")
        trial_ids.add(trial_id)
        hits = row.get("hits")
        if not isinstance(hits, list):
            raise ValueError(f"trials[{index}].hits must be an array")
        value = _finite_number(
            row.get("finalResourceUnits"),
            f"trials[{index}].finalResourceUnits",
        )
        if value < 0:
            raise ValueError(
                f"trials[{index}].finalResourceUnits cannot be negative"
            )
        values.append(value)
    return values


def _base_result(
    payload: Mapping[str, Any],
    *,
    model_version: str,
    synthetic: bool,
) -> dict[str, Any]:
    return {
        "schema": COMPARISON_SCHEMA,
        "observationSetId": str(payload.get("observationSetId") or ""),
        "synthetic": synthetic,
        "environment": dict(_mapping(payload.get("environment"), "environment")),
        "subject": dict(_mapping(payload.get("subject"), "subject")),
        "modelVersion": model_version,
    }


def compare_runtime_observations(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deterministic comparison without claiming more than was measured."""

    if not isinstance(payload, Mapping):
        raise ValueError("observation payload must be an object")
    if payload.get("schema") != OBSERVATION_SCHEMA:
        raise ValueError(f"schema must be {OBSERVATION_SCHEMA}")
    observation_set_id = str(payload.get("observationSetId") or "").strip()
    if not observation_set_id.startswith("runtime://"):
        raise ValueError("observationSetId must use runtime://")

    synthetic = bool(payload.get("synthetic"))
    static_model = _mapping(payload.get("staticModel"), "staticModel")
    model_version = str(static_model.get("modelVersion") or "").strip()
    if not model_version:
        raise ValueError("staticModel.modelVersion is required")
    unsupported = static_model.get("unsupportedDynamicBranches")
    if unsupported is None:
        unsupported = []
    if not isinstance(unsupported, list) or any(
        not isinstance(item, str) or not item.strip() for item in unsupported
    ):
        raise ValueError(
            "staticModel.unsupportedDynamicBranches must be an array of names"
        )

    result = _base_result(
        payload,
        model_version=model_version,
        synthetic=synthetic,
    )
    values = _trial_values(payload)
    if unsupported:
        result.update(
            {
                "status": UNSUPPORTED_DYNAMIC_BRANCH,
                "prediction": {
                    "estimatedYieldPerNode": None,
                    "details": None,
                },
                "observations": {
                    "count": len(values),
                    "mean": statistics.fmean(values) if values else None,
                    "variance": statistics.pvariance(values) if values else None,
                },
                "comparison": None,
                "gaps": [
                    {
                        "status": UNSUPPORTED_DYNAMIC_BRANCH,
                        "branches": list(unsupported),
                        "detail": (
                            "The static profile does not model these runtime "
                            "branches; no calibration score was produced."
                        ),
                    }
                ],
            }
        )
        return result

    inputs = _mapping(static_model.get("inputs"), "staticModel.inputs")
    prediction_details = estimate_complete_node_yield(**dict(inputs))
    predicted = float(prediction_details["estimatedYieldPerNode"])
    observations = {
        "count": len(values),
        "mean": statistics.fmean(values) if values else None,
        "variance": statistics.pvariance(values) if values else None,
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
    }
    result["prediction"] = {
        "estimatedYieldPerNode": predicted,
        "details": prediction_details,
    }
    result["observations"] = observations

    if not values:
        result.update(
            {
                "status": STATIC_REVERSED,
                "comparison": None,
                "gaps": [
                    {
                        "status": INSUFFICIENT_OBSERVATIONS,
                        "detail": "No runtime trials were supplied.",
                    }
                ],
            }
        )
        return result

    policy = _mapping(payload.get("policy") or {}, "policy")
    absolute_tolerance = _finite_number(
        policy.get("absoluteTolerance", 0.0),
        "policy.absoluteTolerance",
    )
    relative_tolerance = _finite_number(
        policy.get("relativeTolerance", 0.0),
        "policy.relativeTolerance",
    )
    minimum_trials = int(policy.get("minimumTrialsForConfirmation", 3))
    if absolute_tolerance < 0 or relative_tolerance < 0:
        raise ValueError("runtime tolerances cannot be negative")
    if minimum_trials < 1:
        raise ValueError("minimumTrialsForConfirmation must be positive")

    observed_mean = float(observations["mean"])
    absolute_error = abs(observed_mean - predicted)
    relative_error = (
        absolute_error / abs(predicted)
        if not math.isclose(predicted, 0.0, abs_tol=1e-12)
        else (0.0 if math.isclose(observed_mean, 0.0, abs_tol=1e-12) else math.inf)
    )
    allowed_error = max(absolute_tolerance, abs(predicted) * relative_tolerance)
    matches = absolute_error <= allowed_error + 1e-12
    if not matches:
        status = RUNTIME_DIVERGED
    elif len(values) >= minimum_trials:
        status = RUNTIME_CONFIRMED
    elif len(values) >= 2:
        status = RUNTIME_CALIBRATED
    else:
        status = INSUFFICIENT_OBSERVATIONS

    result.update(
        {
            "status": status,
            "comparison": {
                "absoluteError": absolute_error,
                "relativeError": relative_error,
                "allowedError": allowed_error,
                "withinTolerance": matches,
                "absoluteTolerance": absolute_tolerance,
                "relativeTolerance": relative_tolerance,
                "minimumTrialsForConfirmation": minimum_trials,
            },
            "gaps": (
                []
                if status in {RUNTIME_CALIBRATED, RUNTIME_CONFIRMED}
                else [
                    {
                        "status": status,
                        "detail": (
                            "Observed mean differs from the static prediction."
                            if status == RUNTIME_DIVERGED
                            else "More matching trials are required."
                        ),
                    }
                ]
            ),
        }
    )
    return result


def render_runtime_comparison_markdown(comparison: Mapping[str, Any]) -> str:
    """Render a small auditable report; never imply synthetic data is real."""

    status = str(comparison.get("status") or "UNKNOWN")
    synthetic = bool(comparison.get("synthetic"))
    prediction = _mapping(comparison.get("prediction") or {}, "prediction")
    observations = _mapping(comparison.get("observations") or {}, "observations")
    detail = comparison.get("comparison")
    lines = [
        "# Runtime calibration comparison",
        "",
        f"- Status: `{status}`",
        f"- Observation set: `{comparison.get('observationSetId', '')}`",
        f"- Model: `{comparison.get('modelVersion', '')}`",
        f"- Data kind: `{'synthetic fixture' if synthetic else 'runtime observation'}`",
        f"- Predicted complete-node yield: `{prediction.get('estimatedYieldPerNode')}`",
        f"- Trial count: `{observations.get('count', 0)}`",
        f"- Observed mean: `{observations.get('mean')}`",
        f"- Observed variance: `{observations.get('variance')}`",
    ]
    if isinstance(detail, Mapping):
        lines.extend(
            [
                f"- Absolute error: `{detail.get('absoluteError')}`",
                f"- Relative error: `{detail.get('relativeError')}`",
                f"- Within tolerance: `{detail.get('withinTolerance')}`",
            ]
        )
    lines.extend(
        [
            "",
            (
                "> This file contains synthetic fixture results and is not a real "
                "game measurement."
                if synthetic
                else "> Runtime observations calibrate one recorded environment; "
                "they do not prove every game branch."
            ),
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "COMPARISON_SCHEMA",
    "INSUFFICIENT_OBSERVATIONS",
    "OBSERVATION_SCHEMA",
    "RUNTIME_CALIBRATED",
    "RUNTIME_CONFIRMED",
    "RUNTIME_DIVERGED",
    "STATIC_REVERSED",
    "UNSUPPORTED_DYNAMIC_BRANCH",
    "compare_runtime_observations",
    "render_runtime_comparison_markdown",
]
