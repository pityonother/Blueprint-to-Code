"""Opt-in, response-private timing diagnostics for KB vNext benchmarks."""

from __future__ import annotations

import math
import time
from collections import defaultdict
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import Callable, Iterator


TIMING_SCHEMA = "ark-kb-segment-timing/v1"
TIMING_SEGMENTS = frozenset(
    {
        "pointerManifestResolution",
        "connectionAcquire",
        "factRequirementPlanning",
        "identityLookup",
        "factQuery",
        "effectiveFactQuery",
        "relationshipQuery",
        "sourceRevisionValidation",
        "evidenceHydration",
        "answerContextSerialization",
        "cacheValidation",
        "cacheWrite",
        "plannerTotal",
    }
)


@dataclass
class _ActiveSample:
    segment: str
    started: float
    query_count: int = 0


@dataclass(frozen=True)
class _CompletedSample:
    duration_ms: float
    query_count: int


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


class SegmentTiming:
    """Collect inclusive segment timings only when explicitly instantiated."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._clock = clock
        self._active: list[_ActiveSample] = []
        self._samples: dict[str, list[_CompletedSample]] = defaultdict(list)

    @contextmanager
    def measure(self, segment: str) -> Iterator[None]:
        if segment not in TIMING_SEGMENTS:
            raise ValueError(f"Unknown timing segment: {segment}")
        active = _ActiveSample(segment=segment, started=self._clock())
        self._active.append(active)
        try:
            yield
        finally:
            finished = self._clock()
            popped = self._active.pop()
            if popped is not active:
                raise AssertionError("Timing segments closed out of order")
            self._samples[segment].append(
                _CompletedSample(
                    duration_ms=max(0.0, (finished - active.started) * 1_000),
                    query_count=active.query_count,
                )
            )

    def record_query(self, _statement: str) -> None:
        """Count each SQLite trace callback in every active inclusive segment."""

        for active in self._active:
            active.query_count += 1

    def report(self) -> dict[str, object]:
        segments: dict[str, dict[str, object]] = {}
        for segment in sorted(self._samples):
            samples = self._samples[segment]
            durations = [sample.duration_ms for sample in samples]
            segments[segment] = {
                "samples": len(samples),
                "queryCount": sum(
                    sample.query_count for sample in samples
                ),
                "p50": round(_percentile(durations, 0.50), 3),
                "p95": round(_percentile(durations, 0.95), 3),
                "p99": round(_percentile(durations, 0.99), 3),
                "maximum": round(max(durations), 3),
            }
        return {
            "schema": TIMING_SCHEMA,
            "clock": "time.perf_counter",
            "queryCountMode": "inclusive_sqlite_trace",
            "segments": segments,
        }


def measure_segment(
    timing: SegmentTiming | None,
    segment: str,
):
    """Return a zero-cost no-op context unless profiling was requested."""

    return timing.measure(segment) if timing is not None else nullcontext()
