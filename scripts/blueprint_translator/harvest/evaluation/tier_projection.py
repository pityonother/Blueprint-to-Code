"""Evidence-tier ordering, competition ranking, and bounded projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .aggregation import _metric_value, _stable_row_identity
from .contracts import POLICY_INCLUDE_CONDITIONAL


@dataclass(frozen=True)
class TierProjection:
    confirmed_all: list[dict[str, Any]]
    conditional_all: list[dict[str, Any]]
    confirmed_items: list[dict[str, Any]]
    conditional_items: list[dict[str, Any]]
    compatibility_items: list[dict[str, Any]]


def _ranked_tier(
    rows: list[dict[str, Any]],
    *,
    metric: str,
) -> list[dict[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda row: (
            -float(_metric_value(row, metric) or 0.0),
            *_stable_row_identity(row),
        ),
    )
    previous_score: float | None = None
    previous_rank = 0
    top_score = _metric_value(ordered[0], metric) if ordered else None
    result: list[dict[str, Any]] = []
    for ordinal, source_row in enumerate(ordered, start=1):
        row = dict(source_row)
        score = _metric_value(row, metric)
        if score is None:
            continue
        if previous_score is None or score != previous_score:
            previous_rank = ordinal
            previous_score = score
        row["rank"] = previous_rank
        row["relativeToNodeTopPercent"] = (
            round(min(100.0, max(0.0, score / top_score * 100.0)), 6)
            if top_score is not None and top_score > 0
            else 0.0
        )
        row["relativeBasisTier"] = row.get("rankingTier")
        result.append(row)
    return result


def project_tiers(
    rows: list[dict[str, Any]],
    *,
    metric: str,
    evidence_policy: str,
    limit: int,
) -> TierProjection:
    confirmed_all = _ranked_tier(
        [row for row in rows if row.get("rankingTier") == "CONFIRMED"],
        metric=metric,
    )
    conditional_all = _ranked_tier(
        [row for row in rows if row.get("rankingTier") != "CONFIRMED"],
        metric=metric,
    )
    bounded_limit = max(1, min(int(limit), 10))
    confirmed_items = confirmed_all[:bounded_limit]
    conditional_items = (
        conditional_all[:bounded_limit]
        if evidence_policy == POLICY_INCLUDE_CONDITIONAL
        else []
    )
    # The legacy alias remains confirmed-only so older clients cannot flatten
    # a conditional winner into the primary ranking.
    compatibility_items = list(confirmed_items)
    return TierProjection(
        confirmed_all=confirmed_all,
        conditional_all=conditional_all,
        confirmed_items=confirmed_items,
        conditional_items=conditional_items,
        compatibility_items=compatibility_items,
    )
