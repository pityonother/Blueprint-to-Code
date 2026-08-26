"""Derive bounded runtime-routing signals from authoritative Node/Pin evidence.

The projections in this module are deliberately conservative.  A literal Pin
default is usable only when the Pin has exact native identity and no normalized
authoritative wire.  Unresolved edge observations remain gaps, but they do not
erase an otherwise exact unlinked literal.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping


def _json_object(value: object) -> dict[str, object]:
    try:
        loaded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(loaded) if isinstance(loaded, Mapping) else {}


def _json_value(value: object) -> object:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _pins(
    connection: sqlite3.Connection,
    node_ref: str,
    *,
    name: str,
) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT pin_ref, native_pin_id, name, direction, category, "
        "default_value_json, default_object, resolution_json "
        "FROM pins WHERE node_ref = ? AND name = ? ORDER BY ordinal, pin_ref",
        (node_ref, name),
    ).fetchall()


def _has_exact_native_identity(pin: sqlite3.Row) -> bool:
    if not str(pin["native_pin_id"] or "").strip():
        return False
    resolution = _json_object(pin["resolution_json"])
    return str(resolution.get("native_pin_id_authority") or "").upper() == "EXACT"


def _authoritative_edge_count(
    connection: sqlite3.Connection,
    pin_ref: str,
) -> int:
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM edges WHERE source_pin_ref = ? OR target_pin_ref = ?",
            (pin_ref, pin_ref),
        ).fetchone()[0]
    )


def _gap(
    row: sqlite3.Row,
    *,
    signal_kind: str,
    reason_code: str,
    detail: str,
    status: str = "NOT_RECOVERED",
    pin_ref: str = "",
) -> dict[str, object]:
    node_ref = str(row["node_ref"])
    evidence_refs = [node_ref]
    if pin_ref:
        evidence_refs.append(pin_ref)
    item: dict[str, object] = {
        "ref": f"{node_ref}/runtime-signal/{signal_kind}",
        "kind": "runtimeSignalGap",
        "signalKind": signal_kind,
        "status": status,
        "reasonCode": reason_code,
        "detail": detail,
        "graphRef": str(row["graph_ref"]),
        "graphName": str(row["graph_name"]),
        "nodeRef": node_ref,
        "nodeName": str(row["node_name"]),
        "evidenceRefs": evidence_refs,
    }
    if pin_ref:
        item["valuePinRef"] = pin_ref
    return item


def _global_level_event_signal(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> dict[str, object]:
    pins = _pins(connection, str(row["node_ref"]), name="EventName")
    if len(pins) != 1:
        return _gap(
            row,
            signal_kind="global_level_event_emit",
            reason_code=(
                "EVENT_NAME_PIN_IDENTITY_AMBIGUOUS"
                if len(pins) > 1
                else "EVENT_NAME_PIN_IDENTITY_UNAVAILABLE"
            ),
            detail="CallGlobalLevelEvent does not expose one exact EventName input Pin.",
            status="AMBIGUOUS" if len(pins) > 1 else "NOT_RECOVERED",
        )
    pin = pins[0]
    pin_ref = str(pin["pin_ref"])
    if str(pin["direction"] or "") != "EGPD_Input" or not _has_exact_native_identity(pin):
        return _gap(
            row,
            signal_kind="global_level_event_emit",
            reason_code="EVENT_NAME_PIN_IDENTITY_UNAVAILABLE",
            detail="The EventName Pin lacks exact native input-Pin identity.",
            pin_ref=pin_ref,
        )
    if _authoritative_edge_count(connection, pin_ref):
        return _gap(
            row,
            signal_kind="global_level_event_emit",
            reason_code="DYNAMIC_EVENT_NAME_NOT_RESOLVED",
            detail="EventName is supplied by an authoritative wire; its runtime value is not a literal default.",
            pin_ref=pin_ref,
        )
    event_name = _json_value(pin["default_value_json"])
    if not isinstance(event_name, str) or not event_name.strip() or event_name.strip().casefold() == "none":
        return _gap(
            row,
            signal_kind="global_level_event_emit",
            reason_code="EVENT_NAME_LITERAL_NOT_RECOVERED",
            detail="The exact unlinked EventName Pin has no usable literal value.",
            pin_ref=pin_ref,
        )
    node_ref = str(row["node_ref"])
    return {
        "ref": f"{node_ref}/runtime-signal/global_level_event_emit",
        "kind": "runtimeSignal",
        "signalKind": "global_level_event_emit",
        "status": "CONFIRMED",
        "eventName": event_name.strip(),
        "valueSource": "UNLINKED_PIN_DEFAULT",
        "pinIdentityAuthority": "EXACT",
        "graphRef": str(row["graph_ref"]),
        "graphName": str(row["graph_name"]),
        "nodeRef": node_ref,
        "nodeName": str(row["node_name"]),
        "valuePinRef": pin_ref,
        "evidenceRefs": [node_ref, pin_ref],
    }


def _spawn_actor_signal(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> dict[str, object]:
    pins = _pins(connection, str(row["node_ref"]), name="Class")
    if len(pins) != 1:
        return _gap(
            row,
            signal_kind="spawn_actor",
            reason_code=(
                "SPAWN_CLASS_PIN_IDENTITY_AMBIGUOUS"
                if len(pins) > 1
                else "SPAWN_CLASS_PIN_IDENTITY_UNAVAILABLE"
            ),
            detail="SpawnActor does not expose one exact Class input Pin.",
            status="AMBIGUOUS" if len(pins) > 1 else "NOT_RECOVERED",
        )
    pin = pins[0]
    pin_ref = str(pin["pin_ref"])
    if str(pin["direction"] or "") != "EGPD_Input" or not _has_exact_native_identity(pin):
        return _gap(
            row,
            signal_kind="spawn_actor",
            reason_code="SPAWN_CLASS_PIN_IDENTITY_UNAVAILABLE",
            detail="The SpawnActor Class Pin lacks exact native input-Pin identity.",
            pin_ref=pin_ref,
        )
    if _authoritative_edge_count(connection, pin_ref):
        return _gap(
            row,
            signal_kind="spawn_actor",
            reason_code="DYNAMIC_SPAWN_CLASS_NOT_RESOLVED",
            detail="SpawnActor Class is supplied by an authoritative wire and was not resolved to a literal class.",
            pin_ref=pin_ref,
        )
    actor_class = str(pin["default_object"] or "").strip()
    if not actor_class:
        default_value = _json_value(pin["default_value_json"])
        actor_class = default_value.strip() if isinstance(default_value, str) else ""
    if not actor_class or actor_class.casefold() == "none":
        return _gap(
            row,
            signal_kind="spawn_actor",
            reason_code="SPAWN_CLASS_LITERAL_NOT_RECOVERED",
            detail="The exact unlinked SpawnActor Class Pin has no usable literal class.",
            pin_ref=pin_ref,
        )
    node_ref = str(row["node_ref"])
    return {
        "ref": f"{node_ref}/runtime-signal/spawn_actor",
        "kind": "runtimeSignal",
        "signalKind": "spawn_actor",
        "status": "CONFIRMED",
        "actorClass": actor_class,
        "valueSource": "UNLINKED_PIN_DEFAULT",
        "pinIdentityAuthority": "EXACT",
        "graphRef": str(row["graph_ref"]),
        "graphName": str(row["graph_name"]),
        "nodeRef": node_ref,
        "nodeName": str(row["node_name"]),
        "valuePinRef": pin_ref,
        "evidenceRefs": [node_ref, pin_ref],
    }


def discover_runtime_signals(
    connection: sqlite3.Connection,
) -> list[dict[str, object]]:
    """Return conservative runtime signal projections in stable evidence order."""

    rows = connection.execute(
        "SELECT n.node_ref, n.graph_ref, n.name AS node_name, n.class_name, "
        "n.function_name, g.name AS graph_name "
        "FROM nodes n JOIN graphs g ON g.graph_ref = n.graph_ref "
        "WHERE n.function_name = 'CallGlobalLevelEvent' "
        "OR n.class_name = 'K2Node_SpawnActorFromClass' "
        "ORDER BY n.graph_ref, n.node_ref"
    ).fetchall()
    items: list[dict[str, object]] = []
    for row in rows:
        if str(row["function_name"]) == "CallGlobalLevelEvent":
            items.append(_global_level_event_signal(connection, row))
        elif str(row["class_name"]) == "K2Node_SpawnActorFromClass":
            items.append(_spawn_actor_signal(connection, row))
    return items


__all__ = ["discover_runtime_signals"]
