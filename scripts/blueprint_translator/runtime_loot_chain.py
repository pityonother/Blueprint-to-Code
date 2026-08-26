"""Compose per-asset evidence queries into one conservative runtime loot chain."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


RUNTIME_LOOT_CHAIN_SCHEMA = "blueprint-to-code.runtime-loot-chain/v1"


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _items(probe: Mapping[str, object]) -> list[Mapping[str, object]]:
    result = _mapping(probe.get("result"))
    value = result.get("items")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _asset(probe: Mapping[str, object]) -> Mapping[str, object]:
    return _mapping(probe.get("asset"))


def _object_path(probe: Mapping[str, object]) -> str:
    return str(_asset(probe).get("objectPath") or "")


def _evidence_refs(items: Sequence[Mapping[str, object]]) -> list[str]:
    refs: list[str] = []
    for item in items:
        values = item.get("evidenceRefs")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            continue
        refs.extend(str(value) for value in values if str(value))
    return list(dict.fromkeys(refs))


def _generated_class_owner(actor_class: object) -> str:
    value = str(actor_class or "").strip()
    if "'" in value:
        parts = value.split("'")
        if len(parts) >= 2 and parts[1].strip():
            value = parts[1].strip()
    value = value.strip("'")
    package, separator, object_name = value.partition(".")
    if not separator:
        return value.removesuffix("_C")
    return f"{package}.{object_name.removesuffix('_C')}"


def _asset_public_identity(probe: Mapping[str, object]) -> dict[str, object]:
    asset = _asset(probe)
    return {
        key: asset[key]
        for key in (
            "name",
            "objectPath",
            "revisionId",
            "sourceKind",
            "freshnessStatus",
            "releaseAuthority",
        )
        if key in asset
    }


def compose_runtime_loot_chain(
    *,
    event_name: str,
    item_query: str,
    emitter_probe: Mapping[str, object],
    receiver_probes: Sequence[Mapping[str, object]],
    reward_probes: Sequence[Mapping[str, object]],
    expected_receiver_object_paths: Sequence[str] = (),
    expected_reward_object_paths: Sequence[str] = (),
) -> dict[str, object]:
    """Join exact per-asset projections while preserving every open boundary."""

    gaps: list[dict[str, object]] = []
    gap_keys: set[tuple[str, str, str]] = set()

    def add_gap(
        reason_code: str,
        *,
        step: str,
        detail: str,
        object_path: str = "",
    ) -> None:
        key = (reason_code, step, object_path)
        if key in gap_keys:
            return
        gap_keys.add(key)
        gap: dict[str, object] = {
            "status": "NOT_RECOVERED",
            "reasonCode": reason_code,
            "step": step,
            "detail": detail,
        }
        if object_path:
            gap["objectPath"] = object_path
        gaps.append(gap)

    emitter_items = _items(emitter_probe)
    confirmed_emitters = [
        item
        for item in emitter_items
        if item.get("signalKind") == "global_level_event_emit"
        and item.get("status") == "CONFIRMED"
        and str(item.get("eventName") or "").casefold() == event_name.casefold()
    ]
    if confirmed_emitters:
        emit_step: dict[str, object] = {
            "id": "event_emit",
            "status": "CONFIRMED",
            "eventName": event_name,
            "candidateCount": len(confirmed_emitters),
            "evidenceRefs": _evidence_refs(confirmed_emitters),
        }
    else:
        emit_step = {
            "id": "event_emit",
            "status": "NOT_RECOVERED",
            "eventName": event_name,
            "reasonCode": "GLOBAL_EVENT_EMITTER_NOT_RECOVERED",
        }
        add_gap(
            "GLOBAL_EVENT_EMITTER_NOT_RECOVERED",
            step="event_emit",
            detail="No exact unlinked CallGlobalLevelEvent literal matched the requested event.",
        )

    receiver_items = [item for probe in receiver_probes for item in _items(probe)]
    receiver_matches = [
        _mapping(_mapping(probe.get("result")).get("match"))
        for probe in receiver_probes
    ]
    receiver_nodes = sum(
        int(match.get("receiverNodes") or 0) for match in receiver_matches
    )
    confirmed_receiver_nodes = sum(
        int(
            match.get("confirmedReceiverNodes")
            if "confirmedReceiverNodes" in match
            else match.get("receiverNodes") or 0
        )
        for match in receiver_matches
    )
    receiver_refs = list(
        dict.fromkeys(
            str(item.get("receiverNodeRef"))
            for item in receiver_items
            if str(item.get("receiverNodeRef") or "")
        )
    )
    if confirmed_receiver_nodes:
        receiver_step: dict[str, object] = {
            "id": "event_receiver",
            "status": "CONFIRMED",
            "eventName": event_name,
            "assetsInspected": len(receiver_probes),
            "receiverNodes": receiver_nodes,
            "confirmedReceiverNodes": confirmed_receiver_nodes,
            "receiverNodeRefs": receiver_refs[:20],
        }
    elif receiver_nodes:
        receiver_step = {
            "id": "event_receiver",
            "status": "NOT_RECOVERED",
            "eventName": event_name,
            "assetsInspected": len(receiver_probes),
            "receiverNodes": receiver_nodes,
            "confirmedReceiverNodes": 0,
            "reasonCode": "RECEIVER_NODE_IDENTITY_UNAVAILABLE",
        }
        add_gap(
            "RECEIVER_NODE_IDENTITY_UNAVAILABLE",
            step="event_receiver",
            detail="Matching Custom Events exist, but none has authoritative package-node identity.",
        )
    else:
        reason = (
            "GLOBAL_EVENT_RECEIVER_NOT_INDEXED"
            if receiver_probes
            else "RECEIVER_SOURCE_NOT_PROVIDED"
        )
        receiver_step = {
            "id": "event_receiver",
            "status": "NOT_RECOVERED",
            "eventName": event_name,
            "assetsInspected": len(receiver_probes),
            "receiverNodes": 0,
            "confirmedReceiverNodes": 0,
            "reasonCode": reason,
        }
        add_gap(
            reason,
            step="event_receiver",
            detail=(
                "The inspected receiver assets contain no exact matching Custom Event receiver."
                if receiver_probes
                else "No receiver evidence asset was provided to the chain query."
            ),
        )

    actual_receiver_paths = {
        _object_path(probe).casefold()
        for probe in receiver_probes
        if _object_path(probe)
    }
    for expected_path in expected_receiver_object_paths:
        if str(expected_path).casefold() in actual_receiver_paths:
            continue
        add_gap(
            "RECEIVER_SOURCE_NOT_AVAILABLE",
            step="event_receiver",
            detail="An expected receiver asset was not available to the chain query.",
            object_path=str(expected_path),
        )

    confirmed_routes = [
        item
        for item in receiver_items
        if item.get("kind") == "runtimeRoute"
        and item.get("status") == "CONFIRMED"
        and str(item.get("eventName") or "").casefold() == event_name.casefold()
    ]
    actor_classes = list(
        dict.fromkeys(
            str(item.get("actorClass"))
            for item in confirmed_routes
            if str(item.get("actorClass") or "")
        )
    )
    if confirmed_routes:
        spawn_step: dict[str, object] = {
            "id": "spawn_actor",
            "status": "CONFIRMED",
            "confirmedRoutes": len(confirmed_routes),
            "actorClasses": actor_classes[:20],
            "pathAuthority": "EXACT_NORMALIZED_EXEC_EDGES",
            "evidenceRefs": _evidence_refs(confirmed_routes),
        }
    else:
        spawn_step = {
            "id": "spawn_actor",
            "status": "NOT_RECOVERED",
            "confirmedRoutes": 0,
            "reasonCode": "SPAWN_ROUTE_NOT_RECOVERED",
        }
        add_gap(
            "SPAWN_ROUTE_NOT_RECOVERED",
            step="spawn_actor",
            detail="No exact receiver-to-SpawnActor route with authoritative Class Pin identity was recovered.",
        )
        for item in receiver_items:
            reason = str(item.get("reasonCode") or "")
            if (
                item.get("kind") == "runtimeRouteGap"
                and reason
                and reason != "GLOBAL_EVENT_RECEIVER_NOT_INDEXED"
            ):
                add_gap(
                    reason,
                    step="spawn_actor",
                    detail=str(item.get("detail") or "Runtime route evidence is incomplete."),
                )

    reward_items = [item for probe in reward_probes for item in _items(probe)]
    confirmed_rewards = [
        item
        for item in reward_items
        if item.get("kind") == "lootRewardEntry"
        and item.get("status") == "CONFIRMED"
    ]
    reward_matches: list[dict[str, object]] = []
    for probe in reward_probes:
        source_object_path = _object_path(probe)
        for item in _items(probe):
            if item.get("kind") != "lootRewardEntry" or item.get("status") != "CONFIRMED":
                continue
            match: dict[str, object] = {
                "sourceObjectPath": source_object_path,
            }
            for key in (
                "itemClass",
                "rewardType",
                "finishedItemChanceConditional",
                "physicalBlueprintChanceConditional",
                "probabilityScope",
                "entrySelection",
                "tekgramUnlock",
            ):
                if key in item:
                    match[key] = item[key]
            reward_matches.append(match)
    if item_query and confirmed_rewards:
        reward_step: dict[str, object] = {
            "id": "reward_membership",
            "status": "CONFIRMED",
            "itemQuery": item_query,
            "sourcesInspected": len(reward_probes),
            "matchingEntries": len(confirmed_rewards),
            "rewardTypes": list(
                dict.fromkeys(
                    str(item.get("rewardType"))
                    for item in confirmed_rewards
                    if str(item.get("rewardType") or "")
                )
            ),
            "matches": reward_matches[:20],
            "evidenceRefs": _evidence_refs(confirmed_rewards),
        }
    elif item_query:
        reason = (
            "REWARD_SOURCE_NOT_PROVIDED"
            if not reward_probes
            else "REWARD_MEMBERSHIP_NOT_RECOVERED"
        )
        reward_step = {
            "id": "reward_membership",
            "status": "NOT_RECOVERED",
            "itemQuery": item_query,
            "sourcesInspected": len(reward_probes),
            "matchingEntries": 0,
            "reasonCode": reason,
        }
        add_gap(
            reason,
            step="reward_membership",
            detail="The requested item was not confirmed in the provided reward-source defaults.",
        )
    else:
        reward_step = {
            "id": "reward_membership",
            "status": "NOT_APPLICABLE",
            "reasonCode": "ITEM_QUERY_NOT_REQUESTED",
        }

    actual_reward_paths = {
        _object_path(probe).casefold()
        for probe in reward_probes
        if _object_path(probe)
    }
    for expected_path in expected_reward_object_paths:
        if str(expected_path).casefold() in actual_reward_paths:
            continue
        add_gap(
            "REWARD_SOURCE_NOT_AVAILABLE",
            step="reward_membership",
            detail="An expected map-specific reward source was not available to the chain query.",
            object_path=str(expected_path),
        )

    reward_probe_by_path = {
        _object_path(probe).casefold(): probe
        for probe in reward_probes
        if _object_path(probe)
    }
    bound_pairs: list[dict[str, object]] = []
    for route in confirmed_routes:
        actor_class = str(route.get("actorClass") or "")
        owner_path = _generated_class_owner(actor_class)
        reward_probe = reward_probe_by_path.get(owner_path.casefold())
        if reward_probe is None:
            continue
        probe_rewards = [
            item
            for item in _items(reward_probe)
            if item.get("kind") == "lootRewardEntry"
            and item.get("status") == "CONFIRMED"
        ]
        if not probe_rewards:
            continue
        bound_pairs.append(
            {
                "actorClass": actor_class,
                "rewardObjectPath": owner_path,
                "matchingEntries": len(probe_rewards),
                "evidenceRefs": list(
                    dict.fromkeys(
                        [
                            *_evidence_refs([route]),
                            *_evidence_refs(probe_rewards),
                        ]
                    )
                ),
            }
        )

    if not item_query:
        binding_step: dict[str, object] = {
            "id": "spawn_to_reward_source",
            "status": "NOT_APPLICABLE",
            "reasonCode": "ITEM_QUERY_NOT_REQUESTED",
        }
    elif bound_pairs:
        binding_step = {
            "id": "spawn_to_reward_source",
            "status": "CONFIRMED",
            "bindingAuthority": "EXACT_GENERATED_CLASS_TO_ASSET_OBJECT_PATH",
            "boundPairs": bound_pairs[:20],
            "evidenceRefs": list(
                dict.fromkeys(
                    ref
                    for pair in bound_pairs
                    for ref in pair["evidenceRefs"]
                )
            ),
        }
    elif not confirmed_routes:
        binding_step = {
            "id": "spawn_to_reward_source",
            "status": "NOT_RECOVERED",
            "reasonCode": "SPAWN_ROUTE_NOT_RECOVERED",
        }
    else:
        binding_step = {
            "id": "spawn_to_reward_source",
            "status": "NOT_RECOVERED",
            "reasonCode": "SPAWNED_CLASS_TO_REWARD_SOURCE_NOT_BOUND",
        }
        add_gap(
            "SPAWNED_CLASS_TO_REWARD_SOURCE_NOT_BOUND",
            step="spawn_to_reward_source",
            detail="Reward membership exists, but no reward source is the exact asset owning a spawned generated class.",
        )

    steps = [
        emit_step,
        receiver_step,
        spawn_step,
        reward_step,
        binding_step,
    ]
    required_steps = steps[:3] + (steps[3:] if item_query else [])
    complete = all(step["status"] == "CONFIRMED" for step in required_steps) and not gaps
    any_confirmed = any(step["status"] == "CONFIRMED" for step in steps)
    status = "COMPLETE" if complete else ("PARTIAL" if any_confirmed else "NOT_RECOVERED")
    evidence_refs = list(
        dict.fromkeys(
            str(ref)
            for step in steps
            for ref in (
                step.get("evidenceRefs", [])
                if isinstance(step.get("evidenceRefs"), Sequence)
                and not isinstance(step.get("evidenceRefs"), (str, bytes))
                else []
            )
        )
    )
    return {
        "schema": RUNTIME_LOOT_CHAIN_SCHEMA,
        "status": status,
        "query": {
            "eventName": event_name,
            "itemQuery": item_query,
            "scope": "PROVIDED_BOUND_EVIDENCE_ASSETS_ONLY",
        },
        "assets": {
            "emitter": _asset_public_identity(emitter_probe),
            "receivers": [
                _asset_public_identity(probe) for probe in receiver_probes
            ],
            "rewardSources": [
                _asset_public_identity(probe) for probe in reward_probes
            ],
        },
        "steps": steps,
        "gaps": gaps,
        "evidenceRefs": evidence_refs,
    }


__all__ = ["RUNTIME_LOOT_CHAIN_SCHEMA", "compose_runtime_loot_chain"]
