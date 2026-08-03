"""Runtime profile selection and observation eligibility policy."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from ...harvest_runtime_observations import (
    MINIMUM_CONFIRMED_TRIALS,
    RUNTIME_STATUS_OBSERVED_CONFIRMED,
    RUNTIME_STATUS_OBSERVED_PRELIMINARY,
    HarvestRuntimeProfileError,
)


def _runtime_profile_context(
    runtime_observations: Mapping[
        tuple[str, str, str, str, int], Mapping[str, Any]
    ]
    | None,
    *,
    requested_profile_id: str | None,
    runtime_metric: bool,
    validated_profiles_available: Iterable[str] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Select one comparable direct-call profile and report stable coverage."""

    observations = [
        row
        for row in (runtime_observations or {}).values()
        if isinstance(row, Mapping)
    ]
    available_profiles = sorted(
        {
            normalized
            for value in (
                validated_profiles_available
                if validated_profiles_available is not None
                else (
                    row.get("runtimeProfileId")
                    for row in observations
                    if row.get("synthetic") is False
                )
            )
            if (normalized := str(value or "").strip())
        }
    )
    selected_profile = (
        str(requested_profile_id).strip()
        if requested_profile_id is not None
        else None
    )
    if runtime_metric:
        if selected_profile is not None:
            if selected_profile not in available_profiles:
                raise HarvestRuntimeProfileError(
                    "HARVEST_RUNTIME_PROFILE_NOT_FOUND",
                    f"Requested runtimeProfileId {selected_profile!r} is not available.",
                )
        elif len(available_profiles) > 1:
            raise HarvestRuntimeProfileError(
                "HARVEST_RUNTIME_PROFILE_REQUIRED",
                "Multiple runtime profiles are available; select runtimeProfileId.",
            )
        elif available_profiles:
            selected_profile = available_profiles[0]

    synthetic_excluded = 0
    publishable_confirmed_rows = 0
    preliminary_rows = 0
    profile_mismatch_excluded = 0
    for observation in observations:
        if observation.get("synthetic") is not False:
            if observation.get("synthetic") is True:
                synthetic_excluded += 1
            continue
        observation_profile = str(
            observation.get("runtimeProfileId") or ""
        ).strip()
        if selected_profile is None:
            continue
        if observation_profile != selected_profile:
            profile_mismatch_excluded += 1
            continue
        runtime_status = str(observation.get("runtimeStatus") or "")
        trial_count = observation.get("trialCount")
        if (
            runtime_status == RUNTIME_STATUS_OBSERVED_CONFIRMED
            and isinstance(trial_count, int)
            and not isinstance(trial_count, bool)
            and trial_count >= MINIMUM_CONFIRMED_TRIALS
        ):
            publishable_confirmed_rows += 1
        elif (
            runtime_status == RUNTIME_STATUS_OBSERVED_PRELIMINARY
            and isinstance(trial_count, int)
            and not isinstance(trial_count, bool)
            and 0 < trial_count < MINIMUM_CONFIRMED_TRIALS
        ):
            preliminary_rows += 1

    return selected_profile, {
        "runtimeProfilesAvailable": available_profiles,
        "runtimeProfileSelected": selected_profile,
        "publishableConfirmedRows": publishable_confirmed_rows,
        "preliminaryRows": preliminary_rows,
        "syntheticExcluded": synthetic_excluded,
        "profileMismatchExcluded": profile_mismatch_excluded,
    }


def _eligible_runtime_observation(
    observation: object,
    *,
    runtime_profile_id: str | None,
    include_preliminary: bool,
) -> Mapping[str, Any] | None:
    """Reject injected runtime rows that bypass the validated file loader."""

    if (
        not isinstance(observation, Mapping)
        or observation.get("synthetic") is not False
        or runtime_profile_id is None
        or str(observation.get("runtimeProfileId") or "").strip()
        != runtime_profile_id
    ):
        return None
    trial_count = observation.get("trialCount")
    if (
        not isinstance(trial_count, int)
        or isinstance(trial_count, bool)
        or trial_count <= 0
    ):
        return None
    runtime_status = str(observation.get("runtimeStatus") or "")
    if runtime_status == RUNTIME_STATUS_OBSERVED_CONFIRMED:
        if trial_count < MINIMUM_CONFIRMED_TRIALS:
            return None
    elif runtime_status == RUNTIME_STATUS_OBSERVED_PRELIMINARY:
        if trial_count >= MINIMUM_CONFIRMED_TRIALS or not include_preliminary:
            return None
    else:
        return None
    for field in ("observedYieldPerNode", "observedYieldPerSecond"):
        value = observation.get(field)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            return None
    return observation
