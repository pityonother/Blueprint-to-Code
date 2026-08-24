"""Targeted loot membership and delivery-mode projections.

Loot entry weights and Blueprint conversion chances live at different stages of
ARK's reward selection.  This module exposes the serialized values without
turning either one into an unsupported overall drop probability.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zlib
from collections.abc import Iterator, Mapping, Sequence


_ENTRY_MARKERS = {
    "ChanceToActuallyGiveItem",
    "ChanceToBeBlueprintOverride",
    "EntryWeight",
    "ItemEntryName",
    "bForceBlueprint",
}

_SET_FIELDS = (
    "ItemSetName",
    "SetWeight",
    "MinNumItems",
    "MaxNumItems",
    "NumItemsPower",
    "bItemsRandomWithoutReplacement",
)


def _load_value(row: sqlite3.Row) -> object:
    codec = str(row["value_codec"] or "json")
    if codec == "json":
        return json.loads(str(row["value_json"]))
    if codec == "zlib-json-utf8":
        return json.loads(zlib.decompress(bytes(row["value_blob"])).decode("utf-8"))
    raise ValueError(f"unsupported evidence value codec: {codec}")


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _looks_like_loot_entry(value: Mapping[str, object]) -> bool:
    items = value.get("Items")
    class_strings = value.get("ItemClassStrings")
    has_items = _is_sequence(items) or _is_sequence(class_strings)
    return has_items and bool(_ENTRY_MARKERS.intersection(value))


def _walk_entries(
    value: object,
    *,
    path: tuple[object, ...] = (),
    item_set: Mapping[str, object] | None = None,
) -> Iterator[tuple[Mapping[str, object], tuple[object, ...], Mapping[str, object] | None]]:
    if isinstance(value, Mapping):
        next_item_set = (
            value
            if _is_sequence(value.get("ItemEntries"))
            else item_set
        )
        if _looks_like_loot_entry(value):
            yield value, path, item_set
        for key, child in value.items():
            yield from _walk_entries(
                child,
                path=(*path, str(key)),
                item_set=next_item_set,
            )
    elif _is_sequence(value):
        for index, child in enumerate(value):
            yield from _walk_entries(
                child,
                path=(*path, index),
                item_set=item_set,
            )


def _object_path(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        for key in ("object_path", "objectPath", "name", "path"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return ""


def _entry_items(
    entry: Mapping[str, object],
    entry_path: tuple[object, ...],
) -> Iterator[tuple[str, tuple[object, ...]]]:
    for field in ("Items", "ItemClassStrings"):
        values = entry.get(field)
        if not _is_sequence(values):
            continue
        for index, value in enumerate(values):
            item_class = _object_path(value)
            if item_class:
                yield item_class, (*entry_path, field, index)


def _match_kind(item_class: str, query: str) -> str:
    normalized_item = item_class.casefold()
    normalized_query = query.casefold()
    if normalized_item == normalized_query:
        return "EXACT_OBJECT_PATH"
    leaf = item_class.rsplit("/", 1)[-1]
    object_name, _, class_name = leaf.partition(".")
    variants = {
        leaf.casefold(),
        object_name.casefold(),
        class_name.casefold(),
        class_name.removesuffix("_C").casefold(),
    }
    if normalized_query in variants:
        return "EXACT_CLASS_NAME"
    return "SUBSTRING"


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _unlock_evidence(entry: Mapping[str, object]) -> dict[str, object]:
    fields = {
        str(key): value
        for key, value in entry.items()
        if "unlock" in str(key).casefold()
        and any(token in str(key).casefold() for token in ("engram", "tekgram"))
        and value not in (None, False, 0, "", "None")
    }
    if fields:
        return {"status": "EVIDENCED", "fields": fields}
    return {
        "status": "NOT_EVIDENCED",
        "reasonCode": "NO_EXPLICIT_ENGRAM_UNLOCK_FIELD",
    }


def _delivery_semantics(entry: Mapping[str, object]) -> dict[str, object]:
    unlock = _unlock_evidence(entry)
    if unlock["status"] == "EVIDENCED":
        return {
            "status": "CONFIRMED",
            "rewardType": "TEKGRAM_UNLOCK",
            "tekgramUnlock": unlock,
        }
    force_blueprint = entry.get("bForceBlueprint") is True
    blueprint_chance = _number(entry.get("ChanceToBeBlueprintOverride"))
    if force_blueprint:
        blueprint_chance = 1.0
    if blueprint_chance is None or not 0.0 <= blueprint_chance <= 1.0:
        return {
            "status": "NOT_RECOVERED",
            "reasonCode": "BLUEPRINT_DELIVERY_CHANCE_NOT_RECOVERED",
            "rewardType": "UNKNOWN_DELIVERY_MODE",
            "tekgramUnlock": unlock,
        }
    finished_chance = 1.0 - blueprint_chance
    if blueprint_chance == 0.0:
        reward_type = "PHYSICAL_ITEM_ONLY"
    elif blueprint_chance == 1.0:
        reward_type = "PHYSICAL_BLUEPRINT_ONLY"
    else:
        reward_type = "ITEM_OR_PHYSICAL_BLUEPRINT"
    return {
        "status": "CONFIRMED",
        "rewardType": reward_type,
        "finishedItemChanceConditional": finished_chance,
        "physicalBlueprintChanceConditional": blueprint_chance,
        "probabilityScope": "CONDITIONAL_ON_ENTRY_SELECTION_AND_ITEM_GRANT",
        "tekgramUnlock": unlock,
    }


def _set_context(item_set: Mapping[str, object] | None) -> dict[str, object]:
    if item_set is None:
        return {}
    return {
        key: item_set[key]
        for key in _SET_FIELDS
        if key in item_set
    }


def _entry_selection(entry: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {
        "probabilityStatus": "NOT_COMPUTED",
        "reasonCode": "FULL_ITEM_SET_SELECTION_CONTEXT_REQUIRED",
    }
    weight = _number(entry.get("EntryWeight"))
    if weight is not None:
        result["weight"] = weight
    return result


def _reward_item(
    row: sqlite3.Row,
    *,
    entry: Mapping[str, object],
    entry_path: tuple[object, ...],
    item_set: Mapping[str, object] | None,
    item_class: str,
    item_path: tuple[object, ...],
    query: str,
) -> dict[str, object]:
    default_ref = str(row["default_ref"])
    path_json = json.dumps(item_path, ensure_ascii=True, separators=(",", ":"))
    suffix = hashlib.sha256(path_json.encode("utf-8")).hexdigest()[:16]
    semantics = _delivery_semantics(entry)
    item: dict[str, object] = {
        "ref": f"{default_ref}/loot-reward/{suffix}",
        "kind": "lootRewardEntry",
        "status": semantics.pop("status"),
        "itemClass": item_class,
        "itemMatchKind": _match_kind(item_class, query),
        "defaultRef": default_ref,
        "defaultName": str(row["name"]),
        "entryPath": list(entry_path),
        "valuePath": list(item_path),
        "entryName": str(entry.get("ItemEntryName") or ""),
        "entrySelection": _entry_selection(entry),
        "itemSet": _set_context(item_set),
        "evidenceRefs": [default_ref],
        **semantics,
    }
    chance_to_give = _number(entry.get("ChanceToActuallyGiveItem"))
    if chance_to_give is not None:
        item["chanceToActuallyGiveItemConditional"] = chance_to_give
    confidence = str(row["confidence"] or "")
    if confidence and confidence.casefold() != "high":
        item["confidence"] = confidence
    return item


def discover_loot_rewards(
    connection: sqlite3.Connection,
    item_query: str,
) -> dict[str, object]:
    """Search decoded class defaults for exact loot-entry membership evidence."""

    normalized_query = item_query.strip().casefold()
    rows = connection.execute(
        "SELECT default_ref, name, value_json, value_codec, value_blob, confidence "
        "FROM class_defaults ORDER BY default_ref"
    ).fetchall()
    items: list[dict[str, object]] = []
    searched_defaults = 0
    unreadable_defaults = 0
    searched_entries = 0
    for row in rows:
        try:
            value = _load_value(row)
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError, zlib.error):
            unreadable_defaults += 1
            default_ref = str(row["default_ref"])
            items.append(
                {
                    "ref": f"{default_ref}/loot-reward/value-gap",
                    "kind": "lootRewardGap",
                    "status": "NOT_RECOVERED",
                    "reasonCode": "LOOT_DEFAULT_VALUE_NOT_READABLE",
                    "defaultRef": default_ref,
                    "defaultName": str(row["name"]),
                    "evidenceRefs": [default_ref],
                }
            )
            continue
        searched_defaults += 1
        for entry, entry_path, item_set in _walk_entries(value):
            searched_entries += 1
            for item_class, item_path in _entry_items(entry, entry_path):
                if normalized_query not in item_class.casefold():
                    continue
                items.append(
                    _reward_item(
                        row,
                        entry=entry,
                        entry_path=entry_path,
                        item_set=item_set,
                        item_class=item_class,
                        item_path=item_path,
                        query=item_query.strip(),
                    )
                )
    items.sort(
        key=lambda item: (
            0 if item.get("kind") == "lootRewardEntry" else 1,
            str(item.get("defaultRef")),
            json.dumps(item.get("valuePath", []), ensure_ascii=True),
            str(item.get("ref")),
        )
    )
    return {
        "items": items,
        "defaultsAvailable": len(rows),
        "searchedDefaults": searched_defaults,
        "unreadableDefaults": unreadable_defaults,
        "searchedEntries": searched_entries,
        "matchingEntries": sum(
            1 for item in items if item.get("kind") == "lootRewardEntry"
        ),
    }


__all__ = ["discover_loot_rewards"]
