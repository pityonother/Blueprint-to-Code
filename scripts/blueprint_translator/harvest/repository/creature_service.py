"""Creature-list projection for the harvest repository."""

from __future__ import annotations

import copy
from collections import OrderedDict
from typing import Any

from .dataset_loader import CREATURE_PAGE_SCHEMA


def _creature_representative_key(creature: dict[str, Any]) -> tuple[int, int, str]:
    """Prefer the shortest base-game identity over mission/variant display names."""

    object_path = str(creature.get("objectPath") or "")
    return (
        0 if object_path.casefold().startswith("/game/earth/dinos/") else (
            1 if object_path.casefold().startswith("/game/primalearth/dinos/") else 2
        ),
        len(object_path),
        object_path.casefold(),
    )


class CreatureServiceMixin:
    def list_creatures(
        self,
        *,
        q: str = "",
        offset: int = 0,
        limit: int = 24,
    ) -> dict[str, Any]:
        """Return one compact row per exact species identity."""

        query = " ".join(str(q or "").casefold().split())
        if len(query) > 100:
            raise ValueError("q must be at most 100 characters")
        bounded_offset = max(0, int(offset))
        bounded_limit = max(1, min(int(limit), 100))
        evaluation_catalog, _engine = self._load_evaluation()

        grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()
        for creature in evaluation_catalog.get("creatures", []):
            if not isinstance(creature, dict):
                continue
            species_key = " ".join(
                str(
                    creature.get("speciesKey")
                    or creature.get("objectPath")
                    or creature.get("name")
                    or ""
                )
                .casefold()
                .split()
            )
            if not species_key:
                continue
            row = grouped.setdefault(
                species_key,
                {
                    "speciesKey": str(creature.get("speciesKey") or species_key),
                    "name": str(creature.get("name") or species_key),
                    "dinoNameTag": str(creature.get("dinoNameTag") or ""),
                    "variantCount": 0,
                    "attackCount": 0,
                    "attackVariantCount": 0,
                    "tameabilityStatuses": set(),
                    "rideabilityStatuses": set(),
                    "_representativePriority": _creature_representative_key(creature),
                },
            )
            priority = _creature_representative_key(creature)
            if priority < row["_representativePriority"]:
                row["name"] = str(creature.get("name") or species_key)
                row["dinoNameTag"] = str(creature.get("dinoNameTag") or "")
                row["_representativePriority"] = priority
            row["variantCount"] += 1
            attacks = creature.get("attacks")
            attack_count = len(attacks) if isinstance(attacks, list) else 0
            row["attackCount"] += attack_count
            if attack_count:
                row["attackVariantCount"] += 1
            for source_name, target_name in (
                ("tameability", "tameabilityStatuses"),
                ("rideability", "rideabilityStatuses"),
            ):
                source = creature.get(source_name)
                status = (
                    str(source.get("status") or "")
                    if isinstance(source, dict)
                    else ""
                )
                if status:
                    row[target_name].add(status)

        items: list[dict[str, Any]] = []
        for row in grouped.values():
            public_row = {
                **{
                    key: value
                    for key, value in row.items()
                    if key != "_representativePriority"
                },
                "tameabilityStatuses": sorted(row["tameabilityStatuses"]),
                "rideabilityStatuses": sorted(row["rideabilityStatuses"]),
            }
            searchable = " ".join(
                str(public_row.get(key) or "")
                for key in ("speciesKey", "name", "dinoNameTag")
            ).casefold()
            if query and query not in searchable:
                continue
            items.append(public_row)
        items.sort(
            key=lambda row: (
                str(row.get("name") or "").casefold(),
                str(row.get("speciesKey") or "").casefold(),
            )
        )
        total = len(items)
        page_items = copy.deepcopy(
            items[bounded_offset : bounded_offset + bounded_limit]
        )
        evaluation_coverage = dict(evaluation_catalog.get("coverage") or {})
        next_offset = (
            bounded_offset + len(page_items)
            if bounded_offset + len(page_items) < total
            else None
        )
        return {
            "schema": CREATURE_PAGE_SCHEMA,
            "dataset": copy.deepcopy(evaluation_catalog.get("dataset") or {}),
            "coverage": {
                "creatureAssets": len(evaluation_catalog.get("creatures", [])),
                "species": len(grouped),
                "claimsAllCreatures": evaluation_coverage.get("claimsAllCreatures")
                is True,
                "claimBlockers": [
                    str(value)
                    for value in evaluation_catalog.get("claimBlockers", [])
                    if str(value)
                ],
            },
            "total": total,
            "offset": bounded_offset,
            "limit": bounded_limit,
            "nextOffset": next_offset,
            "items": page_items,
        }
