"""Fail-closed import of a small, exact Native function gold set."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Mapping


NATIVE_GOLD_SCHEMA = "ark-kb-native-gold-set/v1"
CONFIRMED_EDGE_METHODS = {
    "exact_native_evidence_id",
    "verified_callsite",
    "verified_program_slice",
}


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name=?
            """,
            (table,),
        ).fetchone()
        is not None
    )


def load_native_gold_set(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != NATIVE_GOLD_SCHEMA:
        raise ValueError("Unsupported native gold-set schema")
    targets = payload.get("targets")
    if not isinstance(targets, list) or not 20 <= len(targets) <= 50:
        raise ValueError("Native gold set must contain 20 to 50 targets")
    target_ids: set[str] = set()
    identities: set[tuple[str, str]] = set()
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("Native gold-set target must be an object")
        required = ("id", "domain", "qualifiedSymbol", "rva", "recipeId")
        if any(not str(target.get(key) or "").strip() for key in required):
            raise ValueError("Native gold-set target identity is incomplete")
        target_id = str(target["id"])
        identity = (str(target["qualifiedSymbol"]), str(target["rva"]))
        if target_id in target_ids or identity in identities:
            raise ValueError("Native gold-set targets must be unique")
        target_ids.add(target_id)
        identities.add(identity)
    for key in (
        "binarySha256",
        "pdbSha256",
        "pdbGuidAge",
        "moduleName",
        "version",
    ):
        if not str(payload.get(key) or "").strip():
            raise ValueError(f"Native gold-set {key} is required")
    return payload


def _list(value: object) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item)]


def _native_revision(
    connection: sqlite3.Connection,
    *,
    symbol: Mapping[str, object],
    gold_version: str,
    generated_at: str,
) -> int:
    fingerprint_payload = [
        symbol["binary_sha256"],
        symbol["pdb_sha256"],
        symbol["pdb_guid_age"],
        *_list(symbol["recipe_ids_json"]),
    ]
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    source_uri = str(symbol["native_evidence_id"])
    connection.execute(
        """
        INSERT OR IGNORE INTO source_revisions(
            source_kind, source_uri, source_fingerprint,
            producer_version, schema_version, generated_at,
            freshness_status
        ) VALUES (
            'native_evidence', ?, ?, ?, ?, ?, 'FRESH'
        )
        """,
        (
            source_uri,
            fingerprint,
            gold_version,
            NATIVE_GOLD_SCHEMA,
            generated_at,
        ),
    )
    return int(
        connection.execute(
            """
            SELECT revision_id FROM source_revisions
            WHERE source_kind='native_evidence'
              AND source_uri=? AND source_fingerprint=?
            """,
            (source_uri, fingerprint),
        ).fetchone()[0]
    )


def _identity_gap(
    target: Mapping[str, object],
    rows: list[sqlite3.Row],
    config: Mapping[str, object],
) -> str:
    if not rows:
        return "EXACT_SYMBOL_NOT_FOUND"
    if len(rows) > 1:
        return "AMBIGUOUS_EXACT_SYMBOL"
    row = rows[0]
    checks = (
        (row["binary_sha256"], config["binarySha256"], "BINARY_MISMATCH"),
        (row["pdb_sha256"], config["pdbSha256"], "PDB_MISMATCH"),
        (row["pdb_guid_age"], config["pdbGuidAge"], "PDB_GUID_AGE_MISMATCH"),
        (row["module_name"], config["moduleName"], "MODULE_MISMATCH"),
        (row["rva"], target["rva"], "RVA_MISMATCH"),
    )
    for actual, expected, gap in checks:
        if str(actual).casefold() != str(expected).casefold():
            return gap
    if str(target["recipeId"]) not in _list(row["recipe_ids_json"]):
        return "RECIPE_MISMATCH"
    if not _list(row["evidence_set_ids_json"]):
        return "EVIDENCE_SET_NOT_BOUND"
    if int(row["pdb_loaded"] or 0) != 1:
        return "PDB_NOT_LOADED"
    if not str(row["signature"] or "").strip():
        return "SIGNATURE_NOT_RECOVERED"
    if not str(row["native_evidence_id"] or "").startswith("native://"):
        return "NATIVE_EVIDENCE_ID_INVALID"
    return ""


def materialize_native_gold_set(
    discovery: sqlite3.Connection,
    core: sqlite3.Connection,
    *,
    config_path: Path,
    generated_at: str,
) -> dict[str, int]:
    """Confirm only exact build-bound functions; keep weaker links candidates."""

    config = load_native_gold_set(config_path)
    if not _table_exists(discovery, "native_symbols"):
        return {
            "nativeGoldTargets": 0,
            "nativeConfirmedFunctions": 0,
            "nativeTargetGaps": 0,
            "nativeConfirmedFieldAccesses": 0,
            "blueprintNativeConfirmedLinks": 0,
            "blueprintNativeCandidateLinks": 0,
        }
    discovery.row_factory = sqlite3.Row
    core.execute("DELETE FROM native_blueprint_links")
    core.execute("DELETE FROM native_field_accesses")
    core.execute("DELETE FROM native_gold_targets")
    core.execute("DELETE FROM native_functions")
    confirmed_by_uri: dict[str, int] = {}
    confirmed_targets = 0
    gap_targets = 0
    for raw_target in config["targets"]:
        target = dict(raw_target)
        rows = list(
            discovery.execute(
                """
                SELECT * FROM native_symbols
                WHERE qualified_name=? AND rva=?
                ORDER BY native_evidence_id
                """,
                (target["qualifiedSymbol"], target["rva"]),
            )
        )
        gap = _identity_gap(target, rows, config)
        native_function_id: int | None = None
        if not gap:
            symbol = dict(rows[0])
            revision_id = _native_revision(
                core,
                symbol=symbol,
                gold_version=str(config["version"]),
                generated_at=generated_at,
            )
            callsite_status = (
                "AVAILABLE_VIA_EVIDENCE_STORE"
                if int(symbol["caller_count"] or 0)
                + int(symbol["callee_count"] or 0)
                > 0
                else "NOT_RECOVERED"
            )
            core.execute(
                """
                INSERT INTO native_functions(
                    canonical_uri, qualified_symbol, module_name, rva,
                    signature, binary_sha256, pdb_sha256, pdb_guid_age,
                    recipe_ids_json, evidence_set_ids_json, caller_count,
                    callee_count, callsite_status, status, confidence,
                    source_revision_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          'CONFIRMED', 'HIGH', ?)
                """,
                (
                    symbol["native_evidence_id"],
                    symbol["qualified_name"],
                    symbol["module_name"],
                    symbol["rva"],
                    symbol["signature"],
                    symbol["binary_sha256"],
                    symbol["pdb_sha256"],
                    symbol["pdb_guid_age"],
                    symbol["recipe_ids_json"],
                    symbol["evidence_set_ids_json"],
                    symbol["caller_count"],
                    symbol["callee_count"],
                    callsite_status,
                    revision_id,
                ),
            )
            native_function_id = int(core.execute("SELECT last_insert_rowid()").fetchone()[0])
            confirmed_by_uri[str(symbol["native_evidence_id"])] = native_function_id
            confirmed_targets += 1
        else:
            gap_targets += 1
        core.execute(
            """
            INSERT INTO native_gold_targets(
                target_id, domain_id, qualified_symbol, expected_rva,
                recipe_id, native_function_id, status, gap_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target["id"],
                target["domain"],
                target["qualifiedSymbol"],
                target["rva"],
                target["recipeId"],
                native_function_id,
                "CONFIRMED" if native_function_id else "GAP",
                gap,
            ),
        )
    field_count = 0
    for native_uri, native_function_id in confirmed_by_uri.items():
        if not _table_exists(discovery, "native_field_accesses"):
            break
        for row in discovery.execute(
            """
            SELECT * FROM native_field_accesses
            WHERE native_evidence_id=?
            ORDER BY access_id
            """,
            (native_uri,),
        ):
            instruction_uri = str(
                row["source_instruction_or_slice_id"] or ""
            ).strip()
            if not instruction_uri:
                continue
            confidence = str(row["confidence"] or "").upper()
            status = (
                "CONFIRMED"
                if confidence in {"HIGH", "CONFIRMED"}
                else "AMBIGUOUS"
            )
            core.execute(
                """
                INSERT INTO native_field_accesses(
                    native_function_id, field_name, field_offset,
                    access_kind, instruction_or_slice_uri, status,
                    confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    native_function_id,
                    row["field_name"],
                    row["field_offset"],
                    row["access_kind"],
                    instruction_uri,
                    status,
                    confidence or "UNKNOWN",
                ),
            )
            field_count += 1
    entity_ids = {
        str(uri): int(entity_id)
        for uri, entity_id in core.execute(
            "SELECT canonical_uri, entity_id FROM entities"
        )
    }
    candidate_links = 0
    confirmed_links = 0
    blueprint_rows = (
        discovery.execute(
            "SELECT * FROM blueprint_native_edges ORDER BY edge_id"
        )
        if _table_exists(discovery, "blueprint_native_edges")
        else ()
    )
    for row in blueprint_rows:
        entity_id = entity_ids.get(str(row["blueprint_asset_path"]))
        if entity_id is None:
            continue
        method = str(row["resolution_method"] or "")
        native_function_id = confirmed_by_uri.get(
            str(row["native_evidence_id"])
        )
        confirmed = (
            native_function_id is not None
            and method in CONFIRMED_EDGE_METHODS
            and str(row["status"] or "").upper() == "CONFIRMED"
        )
        status = "CONFIRMED" if confirmed else "CANDIDATE"
        if confirmed:
            confirmed_links += 1
        else:
            candidate_links += 1
        core.execute(
            """
            INSERT INTO native_blueprint_links(
                link_id, blueprint_entity_id,
                blueprint_graph_evidence_uri, blueprint_function_name,
                native_function_id, native_evidence_uri,
                resolution_method, status, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["edge_id"],
                entity_id,
                row["blueprint_graph_evidence_id"],
                row["blueprint_function_name"],
                native_function_id,
                row["native_evidence_id"],
                method,
                status,
                (
                    "HIGH"
                    if confirmed
                    else str(row["confidence"] or "LOW").upper()
                ),
            ),
        )
    core.commit()
    return {
        "nativeGoldTargets": len(config["targets"]),
        "nativeConfirmedFunctions": confirmed_targets,
        "nativeTargetGaps": gap_targets,
        "nativeConfirmedFieldAccesses": field_count,
        "blueprintNativeConfirmedLinks": confirmed_links,
        "blueprintNativeCandidateLinks": candidate_links,
    }
