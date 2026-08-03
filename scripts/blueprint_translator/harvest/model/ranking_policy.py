"""Stable ordering policy for raw Harvest evaluation rows."""

from __future__ import annotations

from typing import Any, Iterable

def rank_harvest_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    status_order = {"RANKED": 0, "UNRANKED": 1, "INCOMPATIBLE": 2}

    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        status = str(row.get("rankingStatus") or "UNRANKED")
        score = row.get("estimatedYieldPerNode")
        numeric_score = float(score) if isinstance(score, (int, float)) else float("-inf")
        return (
            status_order.get(status, 9),
            -numeric_score,
            str(row.get("creature") or ""),
            str(row.get("attackName") or ""),
            str(row.get("component") or ""),
        )

    return sorted((dict(row) for row in rows), key=key)
