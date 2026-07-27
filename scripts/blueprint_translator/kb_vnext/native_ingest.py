"""Immutable, fail-closed view over canonical Native Evidence stores.

The portable JSON document remains authoritative.  Every production store is
also opened through :class:`NativeEvidenceRepository` so its manifest, source
hash, SQLite hash, integrity, foreign keys, schema, counts, and evidence-set
identity are checked before any semantic row is exposed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, unquote, urlsplit

from ..native_evidence_repository import (
    NativeEvidenceArtifactInvalid,
    NativeEvidenceRepository,
)
from ..native_evidence_store import (
    parse_native_evidence_id,
    validate_native_evidence_payload,
)


NATIVE_EVIDENCE_INPUT_SCHEMA = "ark-kb-native-evidence-input/v1"
_HEX = frozenset("0123456789abcdef")
_CONFIRMED_FUNCTION_CONFIDENCE = frozenset(
    {
        "CONFIRMED",
        "HIGH",
        "PDB-SYMBOL-PLUS-DECOMPILER",
        "pdb-symbol-plus-decompiler",
    }
)
_UNRECOVERED = frozenset(
    {
        "UNKNOWN",
        "NOT_RECOVERED",
        "UNRESOLVED",
        "SOURCE_NOT_AVAILABLE",
    }
)


class NativeEvidenceCorpusInvalid(ValueError):
    """A non-test Native Evidence store cannot be trusted or deduplicated."""


@dataclass(frozen=True)
class NativeEvidenceSet:
    """Path-free identity and trust metadata for one selected evidence set."""

    evidence_set_id: str
    recipe_id: str
    recipe_sha256: str
    source_sha256: str
    sqlite_sha256: str
    generated_at: str
    generator_commit: str
    binary_sha256: str
    module: str
    pdb_sha256: str
    pdb_guid: str
    pdb_age: int
    pdb_loaded: bool
    pdb_matches_binary: bool
    trust_status: str
    formal_validation: bool
    symbol_source: str
    symbol_status: str
    symbol_confidence: str

    @property
    def pdb_guid_age(self) -> str:
        return f"{self.pdb_guid}/{self.pdb_age}"


@dataclass(frozen=True)
class NativeEvidenceOrigin:
    """Raw function assertion plus the evidence-set provenance that made it."""

    recipe_id: str
    recipe_sha256: str
    evidence_set_id: str
    source_sha256: str
    generated_at: str
    raw_status: str
    raw_confidence: str
    raw_symbol_source: str
    symbol_source: str
    symbol_status: str
    symbol_confidence: str
    normalized_status: str
    normalized_confidence: str


@dataclass(frozen=True)
class NativeFieldAccess:
    """A direct field assertion; it never inherits its function's trust."""

    field_access_id: str
    owner_type: str
    field_name: str
    offset: str
    access: str
    raw_status: str
    raw_confidence: str
    normalized_status: str
    normalized_confidence: str
    recipe_id: str
    evidence_set_id: str


@dataclass(frozen=True)
class NativeBlueprintLink:
    """A Blueprint/native relation with its own, non-inherited status."""

    edge_id: str
    source_id: str
    relation: str
    target_id: str
    raw_status: str
    raw_confidence: str
    normalized_status: str
    normalized_confidence: str
    recipe_id: str
    evidence_set_id: str


@dataclass(frozen=True)
class NativeFunction:
    """Canonical native function deduplicated across selected recipes."""

    canonical_uri: str
    name: str
    qualified_symbol: str
    owner: str
    module: str
    rva: str
    signature: str
    canonical_signature: str
    normalized_status: str
    normalized_confidence: str
    callers: tuple[str, ...]
    callees: tuple[str, ...]
    field_accesses: tuple[NativeFieldAccess, ...]
    origins: tuple[NativeEvidenceOrigin, ...]


@dataclass(frozen=True)
class NativeEvidenceCorpus:
    """Path-free immutable corpus consumed by snapshot/materialization code."""

    evidence_sets: tuple[NativeEvidenceSet, ...]
    functions: tuple[NativeFunction, ...]
    blueprint_links: tuple[NativeBlueprintLink, ...]
    available: bool
    input_sha256: str

    def match_gold_target(
        self,
        recipe_id: str,
        qualified_symbol: str,
        rva: str,
    ) -> NativeFunction | None:
        """Return one exact recipe/symbol/RVA match, otherwise fail closed."""

        recipe = str(recipe_id).strip()
        symbol = str(qualified_symbol).strip()
        try:
            canonical_rva = _normalize_rva(rva)
        except ValueError:
            return None
        matches = [
            function
            for function in self.functions
            if function.qualified_symbol == symbol
            and function.rva == canonical_rva
            and any(
                origin.recipe_id == recipe for origin in function.origins
            )
        ]
        return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True)
class _SymbolSet:
    symbol_set_id: str
    source: str
    status: str
    confidence: str


@dataclass(frozen=True)
class _ValidatedStore:
    relative_name: str
    evidence_set: NativeEvidenceSet
    payload: Mapping[str, Any]
    generated_sort: datetime
    symbol_sets: tuple[_SymbolSet, ...]


def _compact_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_sha256(value: object) -> bool:
    text = str(value or "").casefold()
    return len(text) == 64 and all(character in _HEX for character in text)


def _json_object_from_bytes(content: bytes, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise NativeEvidenceCorpusInvalid(
            f"{label} is not readable JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise NativeEvidenceCorpusInvalid(f"{label} must be a JSON object")
    return payload


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise NativeEvidenceCorpusInvalid(
            f"{label} cannot be read: {exc}"
        ) from exc
    return _json_object_from_bytes(content, label)


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise NativeEvidenceCorpusInvalid(f"{label} must be an object")
    return value


def _objects(value: object, label: str) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise NativeEvidenceCorpusInvalid(f"{label} must be an array")
    rows: list[Mapping[str, Any]] = []
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise NativeEvidenceCorpusInvalid(
                f"{label}[{index}] must be an object"
            )
        rows.append(row)
    return tuple(rows)


def _required_text(
    mapping: Mapping[str, Any],
    key: str,
    label: str,
) -> str:
    value = str(mapping.get(key) or "").strip()
    if not value:
        raise NativeEvidenceCorpusInvalid(f"{label}.{key} is required")
    return value


def _required_canonical_text(
    mapping: Mapping[str, Any],
    key: str,
    label: str,
) -> str:
    raw = mapping.get(key)
    if (
        not isinstance(raw, str)
        or not raw
        or raw != raw.strip()
    ):
        raise NativeEvidenceCorpusInvalid(
            f"{label}.{key} must be canonical non-empty text"
        )
    return raw


def _required_canonical_sha256(
    mapping: Mapping[str, Any],
    key: str,
    label: str,
) -> str:
    value = _required_canonical_text(mapping, key, label)
    if value != value.casefold() or not _is_sha256(value):
        raise NativeEvidenceCorpusInvalid(
            f"{label}.{key} must be a canonical SHA-256"
        )
    return value


def _safe_source_path(
    store: Path,
    manifest: Mapping[str, Any],
    label: str,
) -> Path:
    source = _mapping(manifest.get("source"), f"{label}.source")
    relative = str(source.get("path") or "").strip()
    if not relative:
        raise NativeEvidenceCorpusInvalid(f"{label}.source.path is required")
    root = store.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise NativeEvidenceCorpusInvalid(
            f"{label}.source.path escapes its artifact directory"
        ) from exc
    return candidate


def _peek_recipe_id(manifest_path: Path, relative_name: str) -> str:
    """Read only enough authoritative JSON to recognize a test recipe."""

    manifest = _read_json_object(
        manifest_path,
        f"Native Evidence manifest {relative_name}",
    )
    source_path = _safe_source_path(
        manifest_path.parent,
        manifest,
        f"Native Evidence manifest {relative_name}",
    )
    payload = _read_json_object(
        source_path,
        f"Native Evidence source {relative_name}",
    )
    provenance = _mapping(
        payload.get("provenance"),
        f"Native Evidence source {relative_name}.provenance",
    )
    generator = _mapping(
        provenance.get("generator"),
        f"Native Evidence source {relative_name}.provenance.generator",
    )
    return _required_text(
        generator,
        "recipeId",
        f"Native Evidence source {relative_name}.provenance.generator",
    )


def _parse_generated_at(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError as exc:
        raise NativeEvidenceCorpusInvalid(
            f"{label} generatedAtUtc is invalid"
        ) from exc
    if parsed.tzinfo is None:
        raise NativeEvidenceCorpusInvalid(
            f"{label} generatedAtUtc must be timezone-aware"
        )
    return parsed.astimezone(timezone.utc)


def _normalize_rva(value: object) -> str:
    text = str(value or "").strip()
    if not text.casefold().startswith("0x"):
        raise ValueError("RVA must use hexadecimal 0x notation")
    try:
        numeric = int(text[2:], 16)
    except ValueError as exc:
        raise ValueError("RVA is not hexadecimal") from exc
    if numeric < 0:
        raise ValueError("RVA cannot be negative")
    return f"0x{numeric:X}"


def _complete_text(value: object) -> bool:
    text = str(value or "").strip()
    normalized = text.upper().replace("-", "_").replace(" ", "_")
    return bool(text) and normalized not in _UNRECOVERED


def _complete_uri(value: object) -> bool:
    text = str(value or "").strip()
    if not _complete_text(text) or any(character.isspace() for character in text):
        return False
    parsed = urlsplit(text)
    if not parsed.scheme or parsed.query or parsed.fragment:
        return False
    identities = [
        unquote(parsed.netloc),
        *(unquote(part) for part in parsed.path.split("/") if part),
    ]
    return bool(identities) and all(_complete_text(part) for part in identities)


def _symbol_sets(
    payload: Mapping[str, Any],
    pdb: Mapping[str, Any],
) -> tuple[_SymbolSet, ...]:
    explicit = _objects(payload.get("symbolSets"), "symbolSets")
    if explicit:
        def canonical_claim(value: object) -> str:
            raw = str(value or "")
            return raw if raw and raw == raw.strip().upper() else ""

        return tuple(
            sorted(
                (
                    _SymbolSet(
                        symbol_set_id=_required_text(
                            row,
                            "symbolSetId",
                            f"symbolSets[{index}]",
                        ),
                        source=canonical_claim(row.get("source")),
                        status=canonical_claim(row.get("status")),
                        confidence=canonical_claim(
                            row.get("confidence")
                        ),
                    )
                    for index, row in enumerate(explicit)
                ),
                key=lambda row: row.symbol_set_id,
            )
        )
    pdb_sha = _required_text(pdb, "sha256", "provenance.pdb").casefold()
    pdb_guid = _required_text(pdb, "guid", "provenance.pdb").casefold()
    pdb_age = int(pdb.get("age") or 0)
    loaded = pdb.get("loaded") is True
    matched = pdb.get("matchesBinary") is True
    return (
        _SymbolSet(
            symbol_set_id=(
                f"native-symbol-set://{pdb_sha}/{pdb_guid}/{pdb_age}"
            ),
            source="PDB" if loaded else "BINARY_ANALYSIS",
            status=(
                "CONFIRMED"
                if loaded and matched
                else "PROVENANCE_UNVERIFIED"
            ),
            confidence="HIGH" if loaded and matched else "LOW",
        ),
    )


def _symbol_summary(
    symbol_sets: tuple[_SymbolSet, ...],
) -> tuple[str, str, str]:
    values = {
        (row.source, row.status, row.confidence) for row in symbol_sets
    }
    return next(iter(values)) if len(values) == 1 else ("MIXED",) * 3


def _sqlite_rows_match_authority(
    repository: NativeEvidenceRepository,
    payload: Mapping[str, Any],
    label: str,
) -> None:
    """Reject a resealed companion whose function projection is not JSON."""

    targets = _objects(payload.get("targets"), f"{label}.targets")
    expected: dict[str, tuple[str, ...]] = {}
    for row in targets:
        qualified = str(row.get("qualifiedName") or "")
        owner = str(row.get("owner") or "").strip()
        if not owner:
            owner, separator, _name = qualified.rpartition("::")
            owner = owner if separator else ""
        evidence_id = str(row.get("evidenceId") or "")
        expected[evidence_id] = (
            str(row.get("name") or ""),
            qualified,
            owner,
            _normalize_rva(row.get("rva")),
            str(row.get("signature") or ""),
            str(row.get("status") or "CONFIRMED").strip().upper(),
            str(row.get("confidence") or "").strip().upper(),
            str(
                row.get("source") or row.get("symbolSource") or ""
            ),
        )
    actual = {
        str(row["evidenceId"]): (
            str(row["name"]),
            str(row["qualifiedName"]),
            str(row["owner"]),
            _normalize_rva(row["rva"]),
            str(row["signature"]),
            str(row["status"]).strip().upper(),
            str(row["confidence"]).strip().upper(),
            str(row["source"]),
        )
        for row in repository.list_functions()
    }
    if actual != expected:
        raise NativeEvidenceCorpusInvalid(
            f"{label} SQLite function projection conflicts with authoritative JSON"
        )


def _validated_store(
    native_root: Path,
    manifest_path: Path,
) -> _ValidatedStore:
    relative_name = manifest_path.relative_to(native_root).as_posix()
    try:
        with NativeEvidenceRepository.open(
            manifest_path.parent
        ) as repository:
            try:
                source_bytes = repository.source_path.read_bytes()
            except OSError as exc:
                raise NativeEvidenceCorpusInvalid(
                    f"production Native Evidence source {relative_name} "
                    f"cannot be read: {exc}"
                ) from exc
            if _sha256_bytes(source_bytes) != repository.source_sha256:
                raise NativeEvidenceCorpusInvalid(
                    f"production Native Evidence artifact {relative_name} "
                    "changed while it was being validated"
                )
            payload = _json_object_from_bytes(
                source_bytes,
                f"production Native Evidence source {relative_name}",
            )
            validate_native_evidence_payload(payload, formal=True)
            if (
                repository.trust_status != "VERIFIED"
                or repository.formal_validation is not True
            ):
                raise NativeEvidenceCorpusInvalid(
                    f"production Native Evidence artifact {relative_name} "
                    "is not formal VERIFIED evidence"
                )
            if payload.get("evidenceSetId") != repository.evidence_set_id:
                raise NativeEvidenceCorpusInvalid(
                    f"production Native Evidence artifact {relative_name} "
                    "has conflicting evidence-set identities"
                )
            generated_at = _required_text(
                payload,
                "generatedAtUtc",
                f"production Native Evidence source {relative_name}",
            )
            _sqlite_rows_match_authority(
                repository,
                payload,
                f"production Native Evidence artifact {relative_name}",
            )
            manifest = repository.manifest
            sqlite_meta = _mapping(
                manifest.get("sqlite"),
                f"Native Evidence manifest {relative_name}.sqlite",
            )
            sqlite_sha256 = _required_text(
                sqlite_meta,
                "sha256",
                f"Native Evidence manifest {relative_name}.sqlite",
            ).casefold()
            if not _is_sha256(sqlite_sha256):
                raise NativeEvidenceCorpusInvalid(
                    f"Native Evidence manifest {relative_name}.sqlite.sha256 "
                    "is invalid"
                )
            provenance = _mapping(
                payload.get("provenance"),
                f"Native Evidence source {relative_name}.provenance",
            )
            generator = _mapping(
                provenance.get("generator"),
                f"Native Evidence source {relative_name}.provenance.generator",
            )
            binary = _mapping(
                provenance.get("binary"),
                f"Native Evidence source {relative_name}.provenance.binary",
            )
            pdb = _mapping(
                provenance.get("pdb"),
                f"Native Evidence source {relative_name}.provenance.pdb",
            )
            recipe_id = _required_canonical_text(
                generator,
                "recipeId",
                f"Native Evidence source {relative_name}.provenance.generator",
            )
            recipe_sha256 = _required_canonical_sha256(
                generator,
                "recipeSha256",
                f"Native Evidence source {relative_name}.provenance.generator",
            )
            binary_sha256 = _required_canonical_sha256(
                binary,
                "sha256",
                f"Native Evidence source {relative_name}.provenance.binary",
            )
            pdb_sha256 = _required_canonical_sha256(
                pdb,
                "sha256",
                f"Native Evidence source {relative_name}.provenance.pdb",
            )
            if not all(
                _is_sha256(value)
                for value in (
                    recipe_sha256,
                    binary_sha256,
                    pdb_sha256,
                    repository.source_sha256,
                )
            ):
                raise NativeEvidenceCorpusInvalid(
                    f"production Native Evidence artifact {relative_name} "
                    "contains a malformed SHA-256 identity"
                )
            symbols = _symbol_sets(payload, pdb)
            symbol_source, symbol_status, symbol_confidence = (
                _symbol_summary(symbols)
            )
            evidence_set = NativeEvidenceSet(
                evidence_set_id=repository.evidence_set_id,
                recipe_id=recipe_id,
                recipe_sha256=recipe_sha256,
                source_sha256=repository.source_sha256,
                sqlite_sha256=sqlite_sha256,
                generated_at=generated_at,
                generator_commit=_required_text(
                    generator,
                    "repositoryCommit",
                    (
                        f"Native Evidence source {relative_name}"
                        ".provenance.generator"
                    ),
                ),
                binary_sha256=binary_sha256,
                module=_required_canonical_text(
                    binary,
                    "module",
                    (
                        f"Native Evidence source {relative_name}"
                        ".provenance.binary"
                    ),
                ),
                pdb_sha256=pdb_sha256,
                pdb_guid=_required_canonical_text(
                    pdb,
                    "guid",
                    (
                        f"Native Evidence source {relative_name}"
                        ".provenance.pdb"
                    ),
                ).casefold(),
                pdb_age=int(pdb.get("age") or 0),
                pdb_loaded=pdb.get("loaded") is True,
                pdb_matches_binary=pdb.get("matchesBinary") is True,
                trust_status=repository.trust_status,
                formal_validation=repository.formal_validation,
                symbol_source=symbol_source,
                symbol_status=symbol_status,
                symbol_confidence=symbol_confidence,
            )
    except NativeEvidenceCorpusInvalid:
        raise
    except (
        NativeEvidenceArtifactInvalid,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        raise NativeEvidenceCorpusInvalid(
            f"production Native Evidence artifact {relative_name} is invalid: "
            f"{exc}"
        ) from exc
    return _ValidatedStore(
        relative_name=relative_name,
        evidence_set=evidence_set,
        payload=payload,
        generated_sort=_parse_generated_at(generated_at, relative_name),
        symbol_sets=symbols,
    )


def _discover_validated_stores(native_root: Path) -> tuple[_ValidatedStore, ...]:
    manifests = sorted(
        native_root.glob("stores/*/*/evidence.manifest.json"),
        key=lambda path: path.relative_to(native_root).as_posix().casefold(),
    )
    stores: list[_ValidatedStore] = []
    for manifest_path in manifests:
        relative_name = manifest_path.relative_to(native_root).as_posix()
        recipe_id = _peek_recipe_id(manifest_path, relative_name)
        if recipe_id.strip().casefold().startswith("test-"):
            continue
        stores.append(_validated_store(native_root, manifest_path))
    return tuple(stores)


def _selected_stores(
    stores: tuple[_ValidatedStore, ...],
) -> tuple[_ValidatedStore, ...]:
    by_recipe: dict[str, list[_ValidatedStore]] = {}
    for store in stores:
        key = store.evidence_set.recipe_id.strip().casefold()
        by_recipe.setdefault(key, []).append(store)
    selected: list[_ValidatedStore] = []
    for key in sorted(by_recipe):
        candidates = by_recipe[key]
        spellings = {
            candidate.evidence_set.recipe_id for candidate in candidates
        }
        if len(spellings) != 1:
            raise NativeEvidenceCorpusInvalid(
                "production Native Evidence recipe identity conflict: "
                + ", ".join(sorted(spellings))
            )
        latest = max(candidate.generated_sort for candidate in candidates)
        finalists = [
            candidate
            for candidate in candidates
            if candidate.generated_sort == latest
        ]
        if len(finalists) > 1:
            identities = {
                (
                    row.evidence_set.evidence_set_id,
                    row.evidence_set.source_sha256,
                    row.evidence_set.sqlite_sha256,
                    row.evidence_set.generated_at,
                )
                for row in finalists
            }
            if len(identities) != 1:
                raise NativeEvidenceCorpusInvalid(
                    "production Native Evidence canonical store conflict for "
                    f"recipe {candidates[0].evidence_set.recipe_id}"
                )
        selected.append(
            min(finalists, key=lambda row: row.relative_name.casefold())
        )
    evidence_set_ids: set[str] = set()
    for store in selected:
        evidence_set_id = store.evidence_set.evidence_set_id
        if evidence_set_id in evidence_set_ids:
            raise NativeEvidenceCorpusInvalid(
                "production Native Evidence evidence-set identity collision: "
                f"{evidence_set_id}"
            )
        evidence_set_ids.add(evidence_set_id)
    return tuple(
        sorted(
            selected,
            key=lambda row: (
                row.evidence_set.recipe_id.casefold(),
                row.evidence_set.evidence_set_id,
            ),
        )
    )


def _input_document(
    evidence_sets: tuple[NativeEvidenceSet, ...],
) -> dict[str, object]:
    return {
        "schema": NATIVE_EVIDENCE_INPUT_SCHEMA,
        "available": bool(evidence_sets),
        "evidenceSets": [
            {
                "evidenceSetId": row.evidence_set_id,
                "recipeId": row.recipe_id,
                "recipeSha256": row.recipe_sha256,
                "sourceSha256": row.source_sha256,
                "sqliteSha256": row.sqlite_sha256,
                "generatedAtUtc": row.generated_at,
                "trust": {
                    "status": row.trust_status,
                    "formalValidation": row.formal_validation,
                },
                "binary": {
                    "sha256": row.binary_sha256,
                    "module": row.module,
                },
                "pdb": {
                    "sha256": row.pdb_sha256,
                    "guid": row.pdb_guid,
                    "age": row.pdb_age,
                    "loaded": row.pdb_loaded,
                    "matchesBinary": row.pdb_matches_binary,
                },
            }
            for row in evidence_sets
        ],
    }


def _input_sha256(evidence_sets: tuple[NativeEvidenceSet, ...]) -> str:
    return _sha256_bytes(_compact_json(_input_document(evidence_sets)))


EMPTY_NATIVE_EVIDENCE_INPUT_SHA256 = _input_sha256(())


def _symbol_set_for_target(
    store: _ValidatedStore,
    target: Mapping[str, Any],
) -> _SymbolSet | None:
    explicit_id = str(target.get("symbolSetId") or "").strip()
    if explicit_id:
        matches = [
            row
            for row in store.symbol_sets
            if row.symbol_set_id == explicit_id
        ]
        return matches[0] if len(matches) == 1 else None
    return store.symbol_sets[0] if len(store.symbol_sets) == 1 else None


def _native_function_uri_is_complete(
    value: object,
    *,
    binary_sha256: str,
    module: str,
    rva: str,
) -> bool:
    text = str(value or "").strip()
    try:
        identity = parse_native_evidence_id(text)
    except ValueError:
        return False
    canonical_uri = (
        f"native://{binary_sha256}/{quote(module, safe='')}/{rva}"
    )
    return (
        text == canonical_uri
        and identity["binary_sha256"] == binary_sha256
        and identity["module"].casefold() == module.casefold()
        and identity["rva"] == rva
        and _complete_uri(text)
    )


def _function_origin(
    store: _ValidatedStore,
    target: Mapping[str, Any],
    *,
    canonical_uri: str,
    qualified_symbol: str,
    signature: str,
    module: str,
    rva: str,
) -> NativeEvidenceOrigin:
    evidence_set = store.evidence_set
    raw_status = str(target.get("status") or "")
    raw_confidence = str(target.get("confidence") or "")
    raw_symbol_source = str(
        target.get("source") or target.get("symbolSource") or ""
    )
    symbol_set = _symbol_set_for_target(store, target)
    symbol_source = symbol_set.source if symbol_set else ""
    symbol_status = symbol_set.status if symbol_set else ""
    symbol_confidence = symbol_set.confidence if symbol_set else ""
    confirmed = (
        evidence_set.formal_validation
        and evidence_set.trust_status == "VERIFIED"
        and evidence_set.pdb_loaded
        and evidence_set.pdb_matches_binary
        and raw_status == "CONFIRMED"
        and raw_confidence in _CONFIRMED_FUNCTION_CONFIDENCE
        and symbol_source == "PDB"
        and symbol_status == "CONFIRMED"
        and symbol_confidence == "HIGH"
        and _native_function_uri_is_complete(
            canonical_uri,
            binary_sha256=evidence_set.binary_sha256,
            module=module,
            rva=rva,
        )
        and _complete_text(qualified_symbol)
        and _complete_text(signature)
    )
    return NativeEvidenceOrigin(
        recipe_id=evidence_set.recipe_id,
        recipe_sha256=evidence_set.recipe_sha256,
        evidence_set_id=evidence_set.evidence_set_id,
        source_sha256=evidence_set.source_sha256,
        generated_at=evidence_set.generated_at,
        raw_status=raw_status,
        raw_confidence=raw_confidence,
        raw_symbol_source=raw_symbol_source,
        symbol_source=symbol_source,
        symbol_status=symbol_status,
        symbol_confidence=symbol_confidence,
        normalized_status="CONFIRMED" if confirmed else "CANDIDATE",
        normalized_confidence="HIGH" if confirmed else "LOW",
    )


def _relation_uris(
    target: Mapping[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    callers: set[str] = set()
    callees: set[str] = set()
    for row in _objects(target.get("calls"), "target.calls"):
        value = str(
            row.get("targetEvidenceId")
            or row.get("calleeEvidenceId")
            or ""
        ).strip()
        if _complete_uri(value):
            callees.add(value)
    for row in _objects(
        target.get("calledFunctions"),
        "target.calledFunctions",
    ):
        value = str(row.get("evidenceId") or "").strip()
        if _complete_uri(value):
            callees.add(value)
    for field in ("callSites", "incomingCallers"):
        for row in _objects(target.get(field), f"target.{field}"):
            value = str(
                row.get("callerEvidenceId")
                or row.get("evidenceId")
                or ""
            ).strip()
            if _complete_uri(value):
                callers.add(value)
    return tuple(sorted(callers)), tuple(sorted(callees))


def _field_accesses(
    store: _ValidatedStore,
    target: Mapping[str, Any],
) -> tuple[NativeFieldAccess, ...]:
    rows: list[NativeFieldAccess] = []
    for index, raw in enumerate(
        _objects(target.get("fieldAccesses"), "target.fieldAccesses")
    ):
        field_access_id = str(raw.get("fieldAccessId") or "").strip()
        owner_type = str(raw.get("ownerType") or "").strip()
        field_name = str(raw.get("fieldName") or "").strip()
        offset = str(raw.get("offset") or "").strip()
        access = str(raw.get("access") or "").strip()
        raw_status = str(raw.get("status") or "")
        raw_confidence = str(raw.get("confidence") or "")
        direct_confirmed = (
            raw_status == "CONFIRMED"
            and raw_confidence == "HIGH"
            and _complete_uri(field_access_id)
            and all(
                _complete_text(value)
                for value in (owner_type, field_name, offset, access)
            )
        )
        if not field_access_id:
            field_access_id = (
                f"field-access-gap://{store.evidence_set.evidence_set_id}/"
                f"{index}"
            )
        rows.append(
            NativeFieldAccess(
                field_access_id=field_access_id,
                owner_type=owner_type,
                field_name=field_name,
                offset=offset,
                access=access,
                raw_status=raw_status,
                raw_confidence=raw_confidence,
                normalized_status=(
                    "CONFIRMED" if direct_confirmed else "CANDIDATE"
                ),
                normalized_confidence=(
                    "HIGH" if direct_confirmed else "LOW"
                ),
                recipe_id=store.evidence_set.recipe_id,
                evidence_set_id=store.evidence_set.evidence_set_id,
            )
        )
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                row.field_access_id,
                row.recipe_id,
                row.evidence_set_id,
            ),
        )
    )


def _function_from_target(
    store: _ValidatedStore,
    target: Mapping[str, Any],
) -> NativeFunction:
    evidence_set = store.evidence_set
    canonical_uri = _required_canonical_text(
        target,
        "evidenceId",
        "native target",
    )
    identity = parse_native_evidence_id(canonical_uri)
    module = identity["module"]
    rva = identity["rva"]
    if not _native_function_uri_is_complete(
        canonical_uri,
        binary_sha256=evidence_set.binary_sha256,
        module=module,
        rva=rva,
    ):
        raise NativeEvidenceCorpusInvalid(
            f"native function identity is not canonical: {canonical_uri}"
        )
    if (
        identity["binary_sha256"] != evidence_set.binary_sha256
        or module.casefold() != evidence_set.module.casefold()
    ):
        raise NativeEvidenceCorpusInvalid(
            f"native function identity conflicts with evidence set: "
            f"{canonical_uri}"
        )
    raw_declared_rva = target.get("rva")
    declared_rva = _normalize_rva(raw_declared_rva)
    if (
        not isinstance(raw_declared_rva, str)
        or raw_declared_rva != declared_rva
    ):
        raise NativeEvidenceCorpusInvalid(
            f"native function declared RVA is not canonical: "
            f"{canonical_uri}"
        )
    if rva != declared_rva:
        raise NativeEvidenceCorpusInvalid(
            f"native function RVA conflict: {canonical_uri}"
        )
    name = _required_canonical_text(
        target,
        "name",
        f"native target {canonical_uri}",
    )
    qualified_symbol = _required_canonical_text(
        target,
        "qualifiedName",
        f"native target {canonical_uri}",
    )
    raw_owner = target.get("owner")
    owner = str(raw_owner or "")
    if owner and (
        not isinstance(raw_owner, str)
        or owner != owner.strip()
    ):
        raise NativeEvidenceCorpusInvalid(
            f"native target {canonical_uri}.owner must be canonical text"
        )
    if not owner:
        owner, separator, _tail = qualified_symbol.rpartition("::")
        owner = owner if separator else ""
    signature = _required_canonical_text(
        target,
        "signature",
        f"native target {canonical_uri}",
    )
    raw_canonical_signature = target.get("canonicalSignature")
    canonical_signature = str(raw_canonical_signature or "")
    if canonical_signature and (
        not isinstance(raw_canonical_signature, str)
        or canonical_signature != canonical_signature.strip()
    ):
        raise NativeEvidenceCorpusInvalid(
            f"native target {canonical_uri}.canonicalSignature "
            "must be canonical text"
        )
    callers, callees = _relation_uris(target)
    origin = _function_origin(
        store,
        target,
        canonical_uri=canonical_uri,
        qualified_symbol=qualified_symbol,
        signature=signature,
        module=module,
        rva=rva,
    )
    return NativeFunction(
        canonical_uri=canonical_uri,
        name=name,
        qualified_symbol=qualified_symbol,
        owner=owner,
        module=module,
        rva=rva,
        signature=signature,
        canonical_signature=canonical_signature,
        normalized_status=origin.normalized_status,
        normalized_confidence=origin.normalized_confidence,
        callers=callers,
        callees=callees,
        field_accesses=_field_accesses(store, target),
        origins=(origin,),
    )


def _function_identity(
    function: NativeFunction,
) -> tuple[object, ...]:
    return (
        function.canonical_uri,
        function.name,
        function.qualified_symbol,
        function.owner,
        function.module,
        function.rva,
        function.signature,
        function.canonical_signature,
        function.callers,
        function.callees,
        tuple(
            (
                row.field_access_id,
                row.owner_type,
                row.field_name,
                row.offset,
                row.access,
            )
            for row in function.field_accesses
        ),
    )


def _merge_functions(
    stores: tuple[_ValidatedStore, ...],
) -> tuple[NativeFunction, ...]:
    merged: dict[str, NativeFunction] = {}
    for store in stores:
        for target in _objects(
            store.payload.get("targets"),
            f"{store.relative_name}.targets",
        ):
            candidate = _function_from_target(store, target)
            current = merged.get(candidate.canonical_uri)
            if current is None:
                merged[candidate.canonical_uri] = candidate
                continue
            if _function_identity(current) != _function_identity(candidate):
                raise NativeEvidenceCorpusInvalid(
                    "overlapping native function conflict for "
                    f"{candidate.canonical_uri}"
                )
            origins = tuple(
                sorted(
                    (*current.origins, *candidate.origins),
                    key=lambda row: (
                        row.recipe_id.casefold(),
                        row.evidence_set_id,
                        row.source_sha256,
                    ),
                )
            )
            field_accesses = tuple(
                sorted(
                    (*current.field_accesses, *candidate.field_accesses),
                    key=lambda row: (
                        row.field_access_id,
                        row.recipe_id.casefold(),
                        row.evidence_set_id,
                    ),
                )
            )
            confirmed = any(
                origin.normalized_status == "CONFIRMED"
                and origin.normalized_confidence == "HIGH"
                for origin in origins
            )
            merged[candidate.canonical_uri] = replace(
                current,
                normalized_status=(
                    "CONFIRMED" if confirmed else "CANDIDATE"
                ),
                normalized_confidence="HIGH" if confirmed else "LOW",
                field_accesses=field_accesses,
                origins=origins,
            )
    return tuple(merged[key] for key in sorted(merged))


def _blueprint_links(
    stores: tuple[_ValidatedStore, ...],
) -> tuple[NativeBlueprintLink, ...]:
    links: list[NativeBlueprintLink] = []
    for store in stores:
        for index, raw in enumerate(
            _objects(
                store.payload.get("blueprintLinks"),
                f"{store.relative_name}.blueprintLinks",
            )
        ):
            source_id = str(raw.get("sourceId") or "").strip()
            target_id = str(raw.get("targetId") or "").strip()
            relation = str(
                raw.get("relation") or "CALLS_NATIVE"
            ).strip()
            raw_status = str(raw.get("status") or "")
            raw_confidence = str(raw.get("confidence") or "")
            edge_id = str(raw.get("edgeId") or "").strip()
            direct_confirmed = (
                raw_status == "CONFIRMED"
                and raw_confidence == "HIGH"
                and _complete_uri(edge_id)
                and _complete_uri(source_id)
                and _complete_uri(target_id)
                and _complete_text(relation)
            )
            if not edge_id:
                edge_id = (
                    f"native-blueprint-link-gap://"
                    f"{store.evidence_set.evidence_set_id}/{index}"
                )
            links.append(
                NativeBlueprintLink(
                    edge_id=edge_id,
                    source_id=source_id,
                    relation=relation,
                    target_id=target_id,
                    raw_status=raw_status,
                    raw_confidence=raw_confidence,
                    normalized_status=(
                        "CONFIRMED" if direct_confirmed else "CANDIDATE"
                    ),
                    normalized_confidence=(
                        "HIGH" if direct_confirmed else "LOW"
                    ),
                    recipe_id=store.evidence_set.recipe_id,
                    evidence_set_id=store.evidence_set.evidence_set_id,
                )
            )
    return tuple(
        sorted(
            links,
            key=lambda row: (
                row.edge_id,
                row.recipe_id.casefold(),
                row.evidence_set_id,
            ),
        )
    )


def load_native_evidence_corpus(
    native_root: str | Path,
) -> NativeEvidenceCorpus:
    """Load selected production stores into one immutable, path-free corpus.

    Only ``stores/*/*/evidence.manifest.json`` is scanned.  A recipe whose
    authoritative ``recipeId`` begins with ``test-`` (case-insensitive after
    trimming) is excluded before repository validation.  Every other discovered
    store must validate, even if an older store for that recipe is not selected.
    """

    root = Path(native_root).expanduser()
    if not root.exists():
        return NativeEvidenceCorpus(
            evidence_sets=(),
            functions=(),
            blueprint_links=(),
            available=False,
            input_sha256=EMPTY_NATIVE_EVIDENCE_INPUT_SHA256,
        )
    if not root.is_dir():
        raise NativeEvidenceCorpusInvalid(
            "Native Evidence root exists but is not a directory"
        )
    root = root.resolve()
    stores = _selected_stores(_discover_validated_stores(root))
    evidence_sets = tuple(store.evidence_set for store in stores)
    return NativeEvidenceCorpus(
        evidence_sets=evidence_sets,
        functions=_merge_functions(stores),
        blueprint_links=_blueprint_links(stores),
        available=bool(evidence_sets),
        input_sha256=_input_sha256(evidence_sets),
    )


def native_evidence_input_sha256(native_root: str | Path) -> str:
    """Return the canonical selected-production input fingerprint."""

    return load_native_evidence_corpus(native_root).input_sha256


__all__ = [
    "EMPTY_NATIVE_EVIDENCE_INPUT_SHA256",
    "NATIVE_EVIDENCE_INPUT_SCHEMA",
    "NativeBlueprintLink",
    "NativeEvidenceCorpus",
    "NativeEvidenceCorpusInvalid",
    "NativeEvidenceOrigin",
    "NativeEvidenceSet",
    "NativeFieldAccess",
    "NativeFunction",
    "load_native_evidence_corpus",
    "native_evidence_input_sha256",
]
