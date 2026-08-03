"""Validated runtime-observation overlay loading and isolation."""

from __future__ import annotations

from pathlib import Path

from ...harvest_runtime_observations import (
    HarvestRuntimeObservationIndex,
    HarvestRuntimeProfileError,
    load_harvest_runtime_observations,
)
from .caches import RUNTIME_OBSERVATION_CACHE_CAPACITY
from .dataset_loader import HarvestDatasetInvalid


class RuntimeOverlayMixin:
    def _load_runtime_observations(
        self,
        expected_identity: dict[str, str] | None = None,
        *,
        runtime_profile_id: str | None = None,
        include_preliminary: bool = False,
        allow_unselected_profiles: bool = False,
    ) -> HarvestRuntimeObservationIndex:
        root = self.runtime_observation_root
        if root is None or not root.exists():
            return load_harvest_runtime_observations(
                Path("__harvest_runtime_observations_absent__"),
                expected_identity=expected_identity,
                runtime_profile_id=runtime_profile_id,
                include_preliminary=include_preliminary,
                allow_unselected_profiles=allow_unselected_profiles,
            )
        if not root.is_dir():
            raise HarvestDatasetInvalid(
                "Harvest runtime observation root must be a directory."
            )
        signature = tuple(
            (path.name, path.stat().st_mtime_ns, path.stat().st_size)
            for path in sorted(
                root.glob("*.json"), key=lambda value: value.name.casefold()
            )
        )
        expected_signature = tuple(sorted((expected_identity or {}).items()))
        request_signature = (
            str(runtime_profile_id).strip() if runtime_profile_id is not None else None,
            bool(include_preliminary),
            bool(allow_unselected_profiles),
        )
        dataset_signature = (signature, expected_signature)
        cache_key = (signature, expected_signature, request_signature)
        with self._lock:
            if dataset_signature != self._runtime_dataset_signature:
                self._runtime_dataset_signature = dataset_signature
                self._runtime_observation_cache.clear()
                self._lazy_ranking_cache.clear()
                self._top_baseline_cache.clear()
                self._creature_pair_cache.clear()
                self._v2_tier_baseline_cache.clear()
                self._specialty_response_cache.clear()
            cached = self._runtime_observation_cache.pop(cache_key, None)
            if cached is not None:
                self._runtime_observation_cache[cache_key] = cached
                return cached
            try:
                index = load_harvest_runtime_observations(
                    root,
                    expected_identity=expected_identity,
                    runtime_profile_id=runtime_profile_id,
                    include_preliminary=include_preliminary,
                    allow_unselected_profiles=allow_unselected_profiles,
                )
            except HarvestRuntimeProfileError:
                raise
            except (OSError, ValueError) as exc:
                raise HarvestDatasetInvalid(str(exc)) from exc
            self._runtime_observation_cache[cache_key] = index
            while (
                len(self._runtime_observation_cache)
                > RUNTIME_OBSERVATION_CACHE_CAPACITY
            ):
                self._runtime_observation_cache.popitem(last=False)
            return index
