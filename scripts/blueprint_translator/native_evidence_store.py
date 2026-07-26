"""Version-bound Native Evidence JSON persistence and SQLite indexing.

The JSON document is the portable authority.  SQLite is a disposable,
read-optimized companion that records the exact source JSON SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

from .context_pack import estimate_tokens
from .native_identity import validate_native_evidence_manifest


NATIVE_EVIDENCE_SCHEMA = "blueprint-to-code-native-evidence-set/v2"
NATIVE_MANIFEST_SCHEMA = "blueprint-to-code-native-evidence-manifest/v1"
NATIVE_SQLITE_SCHEMA = "blueprint-to-code-native-evidence-sqlite/v1"
NATIVE_SQLITE_USER_VERSION = 1
NATIVE_INDEX_MAX_TOKENS = 1500

NATIVE_TABLES = (
    "native_evidence_sets",
    "native_binaries",
    "native_symbol_sets",
    "native_functions",
    "native_parameters",
    "native_call_edges",
    "native_call_sites",
    "native_field_accesses",
    "native_constants",
    "native_branches",
    "native_vtable_slots",
    "native_gaps",
    "native_recipe_targets",
)

_HEX = frozenset("0123456789abcdef")


def _compact_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    text = str(value or "").casefold()
    return len(text) == 64 and all(char in _HEX for char in text)


def parse_native_evidence_id(value: object) -> dict[str, str]:
    """Parse ``native://<binary-sha>/<module>/<rva>`` without guessing."""

    text = str(value or "").strip()
    parsed = urlsplit(text)
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "native"
        or not _is_sha256(parsed.netloc)
        or len(parts) != 2
        or not parts[0]
        or not parts[1].casefold().startswith("0x")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"invalid native evidenceId: {text!r}")
    try:
        int(parts[1][2:], 16)
    except ValueError as exc:
        raise ValueError(f"invalid native evidenceId RVA: {text!r}") from exc
    return {
        "evidence_id": text,
        "binary_sha256": parsed.netloc.casefold(),
        "module": parts[0],
        "rva": f"0x{int(parts[1][2:], 16):X}",
    }


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _objects(value: object, label: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{label}[{index}] must be an object")
        rows.append(item)
    return rows


def _required_text(mapping: dict[str, Any], key: str, label: str) -> str:
    value = str(mapping.get(key) or "").strip()
    if not value:
        raise ValueError(f"{label}.{key} is required")
    return value


def _native_functions(root: dict[str, Any]) -> list[dict[str, Any]]:
    """Return formal v2 targets, with a narrow legacy alias for local migration."""

    if "targets" in root:
        return _objects(root.get("targets"), "targets")
    if "functions" in root:
        return _objects(root.get("functions"), "functions")
    raise ValueError("native evidence targets must be an array")


def _recipe_identity(provenance: dict[str, Any]) -> tuple[str, str]:
    generator = _object(provenance.get("generator"), "provenance.generator")
    recipe_id = str(generator.get("recipeId") or "").strip()
    recipe_sha = str(generator.get("recipeSha256") or "").strip().casefold()
    if not recipe_id and isinstance(provenance.get("recipe"), dict):
        legacy = _object(provenance.get("recipe"), "provenance.recipe")
        recipe_id = str(legacy.get("id") or "").strip()
        recipe_sha = str(legacy.get("sha256") or "").strip().casefold()
    if not recipe_id:
        raise ValueError("provenance.generator.recipeId is required")
    if not _is_sha256(recipe_sha):
        raise ValueError("provenance.generator.recipeSha256 must be a SHA-256")
    return recipe_id, recipe_sha


def _generator_identity(provenance: dict[str, Any]) -> tuple[str, str]:
    generator = _object(provenance.get("generator"), "provenance.generator")
    generator_id = str(
        generator.get("repositoryCommit") or generator.get("id") or ""
    ).strip()
    if not generator_id:
        raise ValueError("provenance.generator.repositoryCommit is required")
    explicit_sha = str(generator.get("sha256") or "").strip().casefold()
    generator_sha = (
        explicit_sha
        if _is_sha256(explicit_sha)
        else _sha256_bytes(_compact_json(generator).encode("utf-8"))
    )
    return generator_id, generator_sha


def _provenance_status(root: dict[str, Any]) -> str:
    trust = root.get("trust")
    if isinstance(trust, dict) and str(trust.get("status") or "").strip():
        return str(trust["status"]).strip().upper()
    provenance = _object(root.get("provenance"), "provenance")
    return str(provenance.get("status") or "PROVENANCE_UNVERIFIED").strip().upper()


def _implicit_symbol_set(
    provenance: dict[str, Any],
) -> dict[str, Any]:
    pdb = _object(provenance.get("pdb"), "provenance.pdb")
    pdb_sha = str(pdb.get("sha256") or "").casefold()
    guid = str(pdb.get("guid") or "").casefold()
    age = int(pdb.get("age") or 0)
    matched = pdb.get("matchesBinary") is True or pdb.get("matched") is True
    loaded = pdb.get("loaded") is True
    return {
        "symbolSetId": f"native-symbol-set://{pdb_sha}/{guid}/{age}",
        "source": "PDB" if loaded else "BINARY_ANALYSIS",
        "status": "CONFIRMED" if matched and loaded else "PROVENANCE_UNVERIFIED",
        "confidence": "HIGH" if matched and loaded else "LOW",
    }


def _symbol_sets(
    root: dict[str, Any],
    provenance: dict[str, Any],
) -> list[dict[str, Any]]:
    explicit = _objects(root.get("symbolSets"), "symbolSets")
    return explicit or [_implicit_symbol_set(provenance)]


def _function_owner(row: dict[str, Any]) -> str:
    owner = str(row.get("owner") or "").strip()
    if owner:
        return owner
    qualified = str(row.get("qualifiedName") or "").strip()
    head, separator, _tail = qualified.rpartition("::")
    return head if separator else ""


def _gap_function_id(row: dict[str, Any]) -> str:
    return str(
        row.get("functionEvidenceId") or row.get("evidenceId") or ""
    ).strip()


def _gap_reason_code(row: dict[str, Any]) -> str:
    return str(row.get("reasonCode") or row.get("kind") or "").strip()


def _gap_detail(row: dict[str, Any]) -> str:
    return str(row.get("detail") or row.get("reason") or "").strip()


def _recipe_targets(
    root: dict[str, Any],
    functions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    explicit = _objects(root.get("recipeTargets"), "recipeTargets")
    if explicit:
        return explicit
    return [
        {
            "targetId": _stable_id(
                "native-target",
                root.get("evidenceSetId"),
                row.get("evidenceId"),
            ),
            "selector": {"qualifiedName": row.get("qualifiedName") or ""},
            "expectedCount": 1,
            "resolvedEvidenceIds": [row.get("evidenceId")],
            "status": _status(row),
        }
        for row in functions
    ]


def validate_native_evidence_payload(
    payload: object,
    *,
    formal: bool = True,
) -> dict[str, Any]:
    root = _object(payload, "native evidence")
    if root.get("schema") != NATIVE_EVIDENCE_SCHEMA:
        raise ValueError(
            f"native evidence schema must be {NATIVE_EVIDENCE_SCHEMA!r}"
        )
    evidence_set_id = _required_text(root, "evidenceSetId", "native evidence")
    provenance = _object(root.get("provenance"), "provenance")
    binary = _object(provenance.get("binary"), "provenance.binary")
    binary_sha = _required_text(binary, "sha256", "provenance.binary").casefold()
    module = _required_text(binary, "module", "provenance.binary")
    if not _is_sha256(binary_sha):
        raise ValueError("provenance.binary.sha256 must be a 64-character SHA-256")

    _generator_identity(provenance)
    _recipe_id, recipe_sha = _recipe_identity(provenance)
    if evidence_set_id != f"native-set://{binary_sha}/{recipe_sha}":
        raise ValueError(
            "native evidence evidenceSetId must bind the binary and recipe SHA-256"
        )
    pdb = _object(provenance.get("pdb"), "provenance.pdb")
    if not _is_sha256(pdb.get("sha256")):
        raise ValueError("provenance.pdb.sha256 must be a SHA-256")

    symbol_ids: set[str] = set()
    for index, row in enumerate(_symbol_sets(root, provenance)):
        symbol_id = _required_text(row, "symbolSetId", f"symbolSets[{index}]")
        if symbol_id in symbol_ids:
            raise ValueError(f"duplicate symbolSetId: {symbol_id}")
        symbol_ids.add(symbol_id)

    functions = _native_functions(root)
    function_ids: set[str] = set()
    for index, row in enumerate(functions):
        label = f"targets[{index}]"
        evidence_id = _required_text(row, "evidenceId", label)
        identity = parse_native_evidence_id(evidence_id)
        if identity["binary_sha256"] != binary_sha:
            raise ValueError(
                f"{label}.evidenceId binary SHA does not match provenance"
            )
        if identity["module"].casefold() != module.casefold():
            raise ValueError(
                f"{label}.evidenceId module does not match provenance"
            )
        if evidence_id in function_ids:
            raise ValueError(f"duplicate function evidenceId: {evidence_id}")
        function_ids.add(evidence_id)
        _required_text(row, "name", label)
        _required_text(row, "qualifiedName", label)
        symbol_set_id = str(row.get("symbolSetId") or "")
        if symbol_set_id and symbol_set_id not in symbol_ids:
            raise ValueError(f"{label}.symbolSetId does not exist")
        for call_index, call in enumerate(
            _objects(row.get("calls"), f"{label}.calls")
        ):
            target_id = _required_text(
                call,
                "targetEvidenceId",
                f"{label}.calls[{call_index}]",
            )
            parse_native_evidence_id(target_id)
        for child_name in (
            "parameters",
            "fieldAccesses",
            "constants",
            "branches",
            "vtableSlots",
        ):
            _objects(row.get(child_name), f"{label}.{child_name}")

    for gap in _objects(root.get("gaps"), "gaps"):
        function_id = _gap_function_id(gap)
        if function_id:
            parse_native_evidence_id(function_id)
    _recipe_targets(root, functions)
    _objects(root.get("blueprintLinks"), "blueprintLinks")
    if "targets" in root:
        validate_native_evidence_manifest(root, formal=formal)
    elif formal:
        raise ValueError(
            "formal Native Evidence import requires the v2 targets envelope"
        )
    return root


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE native_evidence_sets (
    evidence_set_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    sqlite_schema TEXT NOT NULL,
    generated_at_utc TEXT NOT NULL,
    provenance_status TEXT NOT NULL,
    source_json_sha256 TEXT NOT NULL,
    source_json_size INTEGER NOT NULL,
    recipe_id TEXT NOT NULL,
    recipe_sha256 TEXT NOT NULL,
    generator_id TEXT NOT NULL,
    generator_sha256 TEXT NOT NULL,
    pdb_sha256 TEXT NOT NULL,
    pdb_guid TEXT NOT NULL,
    pdb_age INTEGER NOT NULL,
    pdb_matched INTEGER NOT NULL CHECK (pdb_matched IN (0, 1)),
    payload_json TEXT NOT NULL
);

CREATE TABLE native_binaries (
    binary_sha256 TEXT PRIMARY KEY,
    evidence_set_id TEXT NOT NULL REFERENCES native_evidence_sets(evidence_set_id) ON DELETE CASCADE,
    module TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE native_symbol_sets (
    symbol_set_id TEXT PRIMARY KEY,
    evidence_set_id TEXT NOT NULL REFERENCES native_evidence_sets(evidence_set_id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE native_functions (
    evidence_id TEXT PRIMARY KEY,
    evidence_set_id TEXT NOT NULL REFERENCES native_evidence_sets(evidence_set_id) ON DELETE CASCADE,
    symbol_set_id TEXT REFERENCES native_symbol_sets(symbol_set_id) ON DELETE SET NULL,
    binary_sha256 TEXT NOT NULL REFERENCES native_binaries(binary_sha256) ON DELETE CASCADE,
    module TEXT NOT NULL,
    rva TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    owner TEXT NOT NULL,
    signature TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    source TEXT NOT NULL,
    decompiled_c TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_native_functions_name ON native_functions(name);
CREATE INDEX idx_native_functions_owner_name ON native_functions(owner, name);

CREATE TABLE native_parameters (
    function_evidence_id TEXT NOT NULL REFERENCES native_functions(evidence_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    name TEXT NOT NULL,
    type_name TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (function_evidence_id, ordinal)
) WITHOUT ROWID;

CREATE TABLE native_call_edges (
    call_edge_id TEXT PRIMARY KEY,
    caller_evidence_id TEXT NOT NULL REFERENCES native_functions(evidence_id) ON DELETE CASCADE,
    callee_evidence_id TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_native_call_edges_caller ON native_call_edges(caller_evidence_id);
CREATE INDEX idx_native_call_edges_callee ON native_call_edges(callee_evidence_id);

CREATE TABLE native_call_sites (
    call_site_id TEXT PRIMARY KEY,
    call_edge_id TEXT NOT NULL REFERENCES native_call_edges(call_edge_id) ON DELETE CASCADE,
    caller_evidence_id TEXT NOT NULL REFERENCES native_functions(evidence_id) ON DELETE CASCADE,
    callee_evidence_id TEXT NOT NULL,
    callsite_rva TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE native_field_accesses (
    field_access_id TEXT PRIMARY KEY,
    function_evidence_id TEXT NOT NULL REFERENCES native_functions(evidence_id) ON DELETE CASCADE,
    owner_type TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_offset TEXT NOT NULL,
    access_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_native_field_accesses_name ON native_field_accesses(field_name);

CREATE TABLE native_constants (
    constant_id TEXT PRIMARY KEY,
    function_evidence_id TEXT NOT NULL REFERENCES native_functions(evidence_id) ON DELETE CASCADE,
    value_json TEXT NOT NULL,
    value_type TEXT NOT NULL,
    context TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE native_branches (
    branch_id TEXT PRIMARY KEY,
    function_evidence_id TEXT NOT NULL REFERENCES native_functions(evidence_id) ON DELETE CASCADE,
    condition_text TEXT NOT NULL,
    true_target_rva TEXT NOT NULL,
    false_target_rva TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE native_vtable_slots (
    vtable_slot_id TEXT PRIMARY KEY,
    function_evidence_id TEXT NOT NULL REFERENCES native_functions(evidence_id) ON DELETE CASCADE,
    owner_type TEXT NOT NULL,
    slot INTEGER NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE native_gaps (
    gap_id TEXT PRIMARY KEY,
    evidence_set_id TEXT NOT NULL REFERENCES native_evidence_sets(evidence_set_id) ON DELETE CASCADE,
    function_evidence_id TEXT,
    status TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    detail TEXT NOT NULL,
    next_probe TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE native_recipe_targets (
    target_id TEXT PRIMARY KEY,
    evidence_set_id TEXT NOT NULL REFERENCES native_evidence_sets(evidence_set_id) ON DELETE CASCADE,
    expected_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    selector_json TEXT NOT NULL,
    resolved_evidence_ids_json TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE native_blueprint_links (
    edge_id TEXT PRIMARY KEY,
    evidence_set_id TEXT NOT NULL REFERENCES native_evidence_sets(evidence_set_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    target_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_native_blueprint_links_target ON native_blueprint_links(target_id);
"""


def _status(row: dict[str, Any], default: str = "CONFIRMED") -> str:
    return str(row.get("status") or default).strip().upper()


def _confidence(row: dict[str, Any]) -> str:
    return str(row.get("confidence") or "").strip().upper()


def _stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(
        "\x1f".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()[:24]
    return f"{prefix}://{digest}"


def _insert_function_children(
    connection: sqlite3.Connection,
    function: dict[str, Any],
) -> None:
    function_id = str(function["evidenceId"])
    for ordinal, row in enumerate(_objects(function.get("parameters"), "parameters")):
        connection.execute(
            "INSERT INTO native_parameters(function_evidence_id, ordinal, name, type_name, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                function_id,
                int(row.get("ordinal", ordinal)),
                str(row.get("name") or ""),
                str(row.get("type") or row.get("typeName") or ""),
                _compact_json(row),
            ),
        )
    for ordinal, row in enumerate(_objects(function.get("calls"), "calls")):
        target_id = str(row["targetEvidenceId"])
        edge_id = str(
            row.get("callEdgeId")
            or _stable_id("native-call-edge", function_id, target_id, ordinal)
        )
        connection.execute(
            "INSERT INTO native_call_edges(call_edge_id, caller_evidence_id, callee_evidence_id, "
            "status, confidence, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                edge_id,
                function_id,
                target_id,
                _status(row),
                _confidence(row),
                _compact_json(row),
            ),
        )
        callsite_rva = str(row.get("callsiteRva") or "")
        call_site_id = str(
            row.get("callSiteId")
            or _stable_id("native-call-site", edge_id, callsite_rva, ordinal)
        )
        connection.execute(
            "INSERT INTO native_call_sites(call_site_id, call_edge_id, caller_evidence_id, "
            "callee_evidence_id, callsite_rva, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                call_site_id,
                edge_id,
                function_id,
                target_id,
                callsite_rva,
                _compact_json(row),
            ),
        )
    for ordinal, row in enumerate(
        _objects(function.get("fieldAccesses"), "fieldAccesses")
    ):
        row_id = str(
            row.get("fieldAccessId")
            or _stable_id(
                "field-access",
                function_id,
                row.get("ownerType"),
                row.get("fieldName"),
                ordinal,
            )
        )
        connection.execute(
            "INSERT INTO native_field_accesses(field_access_id, function_evidence_id, owner_type, "
            "field_name, field_offset, access_kind, status, confidence, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row_id,
                function_id,
                str(row.get("ownerType") or ""),
                str(row.get("fieldName") or ""),
                str(row.get("offset") or ""),
                str(row.get("access") or ""),
                _status(row),
                _confidence(row),
                _compact_json(row),
            ),
        )
    for ordinal, row in enumerate(_objects(function.get("constants"), "constants")):
        row_id = str(
            row.get("constantId")
            or _stable_id("constant", function_id, row.get("value"), ordinal)
        )
        connection.execute(
            "INSERT INTO native_constants(constant_id, function_evidence_id, value_json, "
            "value_type, context, status, confidence, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row_id,
                function_id,
                _compact_json(row.get("value")),
                str(row.get("valueType") or ""),
                str(row.get("context") or ""),
                _status(row),
                _confidence(row),
                _compact_json(row),
            ),
        )
    for ordinal, row in enumerate(_objects(function.get("branches"), "branches")):
        row_id = str(
            row.get("branchId")
            or _stable_id("branch", function_id, row.get("condition"), ordinal)
        )
        connection.execute(
            "INSERT INTO native_branches(branch_id, function_evidence_id, condition_text, "
            "true_target_rva, false_target_rva, status, confidence, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row_id,
                function_id,
                str(row.get("condition") or ""),
                str(row.get("trueTargetRva") or ""),
                str(row.get("falseTargetRva") or ""),
                _status(row),
                _confidence(row),
                _compact_json(row),
            ),
        )
    for ordinal, row in enumerate(
        _objects(function.get("vtableSlots"), "vtableSlots")
    ):
        row_id = str(
            row.get("vtableSlotId")
            or _stable_id("vtable-slot", function_id, row.get("slot"), ordinal)
        )
        connection.execute(
            "INSERT INTO native_vtable_slots(vtable_slot_id, function_evidence_id, owner_type, "
            "slot, status, confidence, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row_id,
                function_id,
                str(row.get("ownerType") or ""),
                int(row.get("slot") or 0),
                _status(row),
                _confidence(row),
                _compact_json(row),
            ),
        )


def _database_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in (*NATIVE_TABLES, "native_blueprint_links")
    }


def build_native_evidence_store(
    source_json: str | Path,
    destination: str | Path,
    *,
    formal: bool = True,
) -> dict[str, Any]:
    """Build one atomic SQLite companion for an authoritative JSON file."""

    source = Path(source_json).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    source_bytes = source.read_bytes()
    try:
        payload = validate_native_evidence_payload(
            json.loads(source_bytes.decode("utf-8-sig")),
            formal=formal,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"native evidence JSON cannot be read: {exc}") from exc
    source_sha = _sha256_bytes(source_bytes)
    provenance = _object(payload["provenance"], "provenance")
    binary = _object(provenance["binary"], "provenance.binary")
    pdb = _object(provenance["pdb"], "provenance.pdb")
    recipe_id, recipe_sha = _recipe_identity(provenance)
    generator_id, generator_sha = _generator_identity(provenance)
    evidence_set_id = str(payload["evidenceSetId"])
    functions = _native_functions(payload)
    symbol_sets = _symbol_sets(payload, provenance)
    implicit_symbol_set_id = str(symbol_sets[0]["symbolSetId"])

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(
        prefix=f".{destination_path.name}.",
        suffix=".tmp",
        dir=destination_path.parent,
    )
    os.close(descriptor)
    temp_path = Path(raw_temp)
    try:
        connection = sqlite3.connect(temp_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(SCHEMA_SQL)
            connection.execute(f"PRAGMA user_version = {NATIVE_SQLITE_USER_VERSION}")
            connection.execute("BEGIN")
            connection.execute(
                "INSERT INTO native_evidence_sets(evidence_set_id, schema_version, sqlite_schema, "
                "generated_at_utc, provenance_status, source_json_sha256, source_json_size, "
                "recipe_id, recipe_sha256, generator_id, generator_sha256, pdb_sha256, pdb_guid, "
                "pdb_age, pdb_matched, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    evidence_set_id,
                    NATIVE_EVIDENCE_SCHEMA,
                    NATIVE_SQLITE_SCHEMA,
                    str(payload.get("generatedAtUtc") or ""),
                    _provenance_status(payload),
                    source_sha,
                    len(source_bytes),
                    recipe_id,
                    recipe_sha,
                    generator_id,
                    generator_sha,
                    str(pdb["sha256"]).casefold(),
                    str(pdb.get("guid") or ""),
                    int(pdb.get("age") or 0),
                    1
                    if (
                        pdb.get("matchesBinary") is True
                        or pdb.get("matched") is True
                    )
                    else 0,
                    _compact_json(provenance),
                ),
            )
            binary_sha = str(binary["sha256"]).casefold()
            connection.execute(
                "INSERT INTO native_binaries(binary_sha256, evidence_set_id, module, size_bytes, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    binary_sha,
                    evidence_set_id,
                    str(binary["module"]),
                    int(binary.get("size") or binary.get("sizeBytes") or 0),
                    _compact_json(binary),
                ),
            )
            for row in symbol_sets:
                connection.execute(
                    "INSERT INTO native_symbol_sets(symbol_set_id, evidence_set_id, source, status, "
                    "confidence, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(row["symbolSetId"]),
                        evidence_set_id,
                        str(row.get("source") or ""),
                        _status(row),
                        _confidence(row),
                        _compact_json(row),
                    ),
                )
            for row in functions:
                identity = parse_native_evidence_id(row["evidenceId"])
                symbol_set_id = (
                    str(row.get("symbolSetId") or "") or implicit_symbol_set_id
                )
                connection.execute(
                    "INSERT INTO native_functions(evidence_id, evidence_set_id, symbol_set_id, "
                    "binary_sha256, module, rva, name, qualified_name, owner, signature, status, "
                    "confidence, source, decompiled_c, payload_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        identity["evidence_id"],
                        evidence_set_id,
                        symbol_set_id,
                        identity["binary_sha256"],
                        identity["module"],
                        identity["rva"],
                        str(row.get("name") or ""),
                        str(row.get("qualifiedName") or ""),
                        _function_owner(row),
                        str(row.get("signature") or ""),
                        _status(row),
                        _confidence(row),
                        str(row.get("source") or row.get("symbolSource") or ""),
                        str(row.get("decompiledC") or ""),
                        _compact_json(
                            {
                                key: value
                                for key, value in row.items()
                                if key
                                not in {
                                    "parameters",
                                    "calls",
                                    "fieldAccesses",
                                    "constants",
                                    "branches",
                                    "vtableSlots",
                                    "decompiledC",
                                }
                            }
                        ),
                    ),
                )
                _insert_function_children(connection, row)
            for ordinal, row in enumerate(_objects(payload.get("gaps"), "gaps")):
                gap_id = str(
                    row.get("gapId")
                    or _stable_id(
                        "native-gap",
                        evidence_set_id,
                        _gap_function_id(row),
                        _gap_reason_code(row),
                        ordinal,
                    )
                )
                connection.execute(
                    "INSERT INTO native_gaps(gap_id, evidence_set_id, function_evidence_id, status, "
                    "reason_code, detail, next_probe, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        gap_id,
                        evidence_set_id,
                        _gap_function_id(row) or None,
                        _status(row, "NOT_RECOVERED"),
                        _gap_reason_code(row),
                        _gap_detail(row),
                        str(row.get("nextProbe") or ""),
                        _compact_json(row),
                    ),
                )
            for ordinal, row in enumerate(_recipe_targets(payload, functions)):
                target_id = str(
                    row.get("targetId")
                    or _stable_id("native-target", evidence_set_id, ordinal)
                )
                connection.execute(
                    "INSERT INTO native_recipe_targets(target_id, evidence_set_id, expected_count, "
                    "status, selector_json, resolved_evidence_ids_json, payload_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        target_id,
                        evidence_set_id,
                        int(row.get("expectedCount") or 0),
                        _status(row),
                        _compact_json(row.get("selector") or {}),
                        _compact_json(row.get("resolvedEvidenceIds") or []),
                        _compact_json(row),
                    ),
                )
            for ordinal, row in enumerate(
                _objects(payload.get("blueprintLinks"), "blueprintLinks")
            ):
                edge_id = str(
                    row.get("edgeId")
                    or _stable_id(
                        "edge",
                        row.get("sourceId"),
                        row.get("relation"),
                        row.get("targetId"),
                        ordinal,
                    )
                )
                connection.execute(
                    "INSERT INTO native_blueprint_links(edge_id, evidence_set_id, source_id, relation, "
                    "target_id, status, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        edge_id,
                        evidence_set_id,
                        str(row.get("sourceId") or ""),
                        str(row.get("relation") or "CALLS_NATIVE"),
                        str(row.get("targetId") or ""),
                        _status(row),
                        _compact_json(row),
                    ),
                )
            counts = _database_counts(connection)
            connection.commit()
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                raise ValueError(
                    f"native evidence database foreign key errors: {foreign_keys[:3]}"
                )
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise ValueError(
                    f"native evidence database integrity check failed: {integrity}"
                )
        finally:
            connection.close()
        os.replace(temp_path, destination_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return {
        "database_path": str(destination_path),
        "evidence_set_id": evidence_set_id,
        "source_sha256": source_sha,
        "source_size_bytes": len(source_bytes),
        "schema": NATIVE_EVIDENCE_SCHEMA,
        "sqlite_schema": NATIVE_SQLITE_SCHEMA,
        "formal_validation": formal,
        "trust_status": _provenance_status(payload),
        "counts": counts,
        "payload": payload,
    }


def render_native_index(
    payload: dict[str, Any],
    *,
    source_sha256: str,
    max_tokens: int = NATIVE_INDEX_MAX_TOKENS,
) -> str:
    provenance = _object(payload.get("provenance"), "provenance")
    binary = _object(provenance.get("binary"), "provenance.binary")
    recipe_id, recipe_sha = _recipe_identity(provenance)
    functions = _native_functions(payload)
    gaps = _objects(payload.get("gaps"), "gaps")

    def render(function_rows: Iterable[dict[str, Any]], gap_rows: Iterable[dict[str, Any]]) -> str:
        function_lines = [
            (
                f"- `{row.get('qualifiedName', row.get('name', ''))}` "
                f"— `{row.get('evidenceId', '')}` "
                f"[{_status(row)} / {_confidence(row) or 'UNSPECIFIED'}]"
            )
            for row in function_rows
        ]
        gap_lines = [
            (
                f"- `{_gap_reason_code(row) or 'UNKNOWN'}` "
                f"[{_status(row, 'NOT_RECOVERED')}]: "
                f"{_gap_detail(row)[:180]}"
            )
            for row in gap_rows
        ]
        return "\n".join(
            [
                "# Native Evidence Index",
                "",
                f"- Evidence set: `{payload.get('evidenceSetId', '')}`",
                f"- Source fingerprint: `{source_sha256}`",
                f"- Binary: `{binary.get('module', '')}` / `{binary.get('sha256', '')}`",
                f"- Recipe: `{recipe_id}` / `{recipe_sha}`",
                f"- Provenance: `{_provenance_status(payload)}`",
                f"- Functions available: {len(functions)}",
                "",
                "## Target functions",
                "",
                *(function_lines or ["- No functions were imported."]),
                "",
                "## Evidence gaps",
                "",
                *(gap_lines or ["- No gaps were recorded."]),
                "",
                "## Next bounded queries",
                "",
                "- `query_native_evidence.py --evidence-dir <dir> overview --budget 700`",
                "- `query_native_evidence.py --evidence-dir <dir> search --query <name> --budget 900`",
                "- `query_native_evidence.py --evidence-dir <dir> gaps --budget 800`",
                "",
                "Full decompiler bodies are intentionally omitted. Use a bounded `function` query with an explicit snippet request.",
                "",
            ]
        )

    selected_functions = list(functions)
    selected_gaps = list(gaps)
    text = render(selected_functions, selected_gaps)
    while estimate_tokens(text) > max_tokens and (
        len(selected_functions) > 1 or selected_gaps
    ):
        if selected_gaps:
            selected_gaps.pop()
        elif len(selected_functions) > 1:
            selected_functions.pop()
        text = render(selected_functions, selected_gaps)
    if estimate_tokens(text) > max_tokens:
        raise ValueError("native index shell exceeds its token budget")
    return text


def _atomic_bytes(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        Path(raw).unlink(missing_ok=True)
        raise
    return Path(raw)


def _publish_artifacts(staged: list[tuple[Path, Path]]) -> None:
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for _source, destination in staged:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                backup = destination.with_name(
                    f".{destination.name}.{uuid.uuid4().hex}.backup"
                )
                os.replace(destination, backup)
                backups[destination] = backup
        for source, destination in staged:
            os.replace(source, destination)
            published.append(destination)
    except Exception:
        for destination in reversed(published):
            destination.unlink(missing_ok=True)
        for destination, backup in backups.items():
            if backup.exists():
                os.replace(backup, destination)
        raise
    finally:
        for backup in backups.values():
            backup.unlink(missing_ok=True)
        for source, _destination in staged:
            source.unlink(missing_ok=True)


def write_native_evidence_artifacts(
    source_json: str | Path,
    evidence_dir: str | Path,
    *,
    formal: bool = True,
) -> dict[str, Any]:
    """Publish source JSON, SQLite, manifest, and compact index as one set."""

    source = Path(source_json).expanduser().resolve()
    destination = Path(evidence_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    staging_root = Path(
        tempfile.mkdtemp(prefix=".native-evidence-", dir=destination.parent)
    )
    try:
        staged_source = staging_root / "evidence.full.json"
        shutil.copyfile(source, staged_source)
        staged_database = staging_root / "evidence.sqlite"
        result = build_native_evidence_store(
            staged_source,
            staged_database,
            formal=formal,
        )
        index_text = render_native_index(
            result["payload"],
            source_sha256=str(result["source_sha256"]),
        )
        staged_index = staging_root / "native_index.md"
        staged_index.write_text(index_text, encoding="utf-8", newline="\n")
        manifest = {
            "schema": NATIVE_MANIFEST_SCHEMA,
            "evidenceSetId": result["evidence_set_id"],
            "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
            "trust": {
                "status": result["trust_status"],
                "formalValidation": result["formal_validation"],
            },
            "source": {
                "path": "evidence.full.json",
                "sha256": result["source_sha256"],
                "sizeBytes": result["source_size_bytes"],
            },
            "sqlite": {
                "path": "evidence.sqlite",
                "schema": NATIVE_SQLITE_SCHEMA,
                "userVersion": NATIVE_SQLITE_USER_VERSION,
                "sha256": sha256_file(staged_database),
            },
            "index": {
                "path": "output/native_index.md",
                "estimatedTokens": estimate_tokens(index_text),
                "maxEstimatedTokens": NATIVE_INDEX_MAX_TOKENS,
            },
            "counts": result["counts"],
        }
        staged_manifest = staging_root / "evidence.manifest.json"
        staged_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        final_source = destination / "evidence.full.json"
        final_database = destination / "evidence.sqlite"
        final_manifest = destination / "evidence.manifest.json"
        final_index = destination / "output" / "native_index.md"
        _publish_artifacts(
            [
                (staged_source, final_source),
                (staged_database, final_database),
                (staged_manifest, final_manifest),
                (staged_index, final_index),
            ]
        )
        return {
            **{key: value for key, value in result.items() if key != "payload"},
            "source_path": str(final_source),
            "database_path": str(final_database),
            "manifest_path": str(final_manifest),
            "index_path": str(final_index),
        }
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
