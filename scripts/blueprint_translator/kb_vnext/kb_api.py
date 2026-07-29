"""Safe read/query service for ARK Knowledge Base vNext HTTP endpoints."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from http import HTTPStatus
from pathlib import Path
from typing import Mapping
from urllib.parse import urlencode

from .kb_context import build_bounded_context_pack
from .query_planner import (
    ANSWER_MODES,
    CANDIDATE_EXPLANATION_SCHEMA_MIGRATION_REQUIRED,
    QueryRequirements,
    effective_class_evidence_freshness,
    is_valid_generic_evidence_uri,
    load_effective_class_evidence,
    load_effective_candidate_explanations,
    plan_query,
    source_revision_is_fresh,
)
from .projections import (
    DOMAIN_PROJECTIONS,
    PROJECTION_SCHEMA_VERSION,
)
from .schema_capabilities import CORE_SCHEMA_VERSION, core_schema_capabilities
from .snapshot import (
    SNAPSHOT_SEMANTIC_INPUT_KEYS,
    SNAPSHOT_SOURCE_KIND,
    SNAPSHOT_SOURCE_URI,
    active_stale_source_count,
    normalize_snapshot_generated_at,
    resolve_current_snapshot,
    semantic_inputs_sha256,
    snapshot_build_id,
    validate_snapshot_journal_safety,
    validate_snapshot_database_schemas,
    validate_snapshot_projection_bindings,
    validate_snapshot_runtime_health_summary,
    validate_sealed_snapshot_quality,
    validate_snapshot_source_identity,
)
from .storage import (
    CACHE_SCHEMA_VERSION,
    CATALOG_SCHEMA_VERSION,
    SEARCH_SCHEMA_VERSION,
)


MAX_PAGE_SIZE = 100
MAX_CURSOR = 1_000_000
MAX_SEARCH_CANDIDATES = 500
MAX_FUZZY_CANDIDATES = 200
MAX_FUZZY_ALIASES_PER_ENTITY = 20
MIN_FUZZY_SCORE = 0.55
SNAPSHOT_SCHEMA = "ark-kb-vnext-snapshot/v1"
SNAPSHOT_DATABASE_SCHEMAS = {
    "catalog.sqlite": CATALOG_SCHEMA_VERSION,
    "core.sqlite": CORE_SCHEMA_VERSION,
    "search.sqlite": SEARCH_SCHEMA_VERSION,
    "cache.sqlite": CACHE_SCHEMA_VERSION,
}
IMMUTABLE_SNAPSHOT_DATABASE_SCHEMAS = {
    name: schema
    for name, schema in SNAPSHOT_DATABASE_SCHEMAS.items()
    if name != "cache.sqlite"
}
DOMAIN_EXPORT_DATABASES = {
    f"domain_exports/{projection_name}.sqlite": projection_name
    for projection_name in DOMAIN_PROJECTIONS
}


class KnowledgeApiError(ValueError):
    def __init__(
        self,
        status: HTTPStatus,
        code: str,
        message: str,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _bounded_int(
    value: object,
    *,
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value if value not in (None, "") else default)
    except (TypeError, ValueError) as exc:
        raise KnowledgeApiError(
            HTTPStatus.BAD_REQUEST,
            "REQUEST_INVALID",
            f"{name} must be an integer.",
        ) from exc
    if not minimum <= parsed <= maximum:
        raise KnowledgeApiError(
            HTTPStatus.BAD_REQUEST,
            "REQUEST_INVALID",
            f"{name} must be between {minimum} and {maximum}.",
        )
    return parsed


def _search_terms(value: str) -> tuple[str, ...]:
    return tuple(
        match.group(0)
        for match in re.finditer(r"[^\W_]+", value, flags=re.UNICODE)
    )


def _fts_phrase(terms: tuple[str, ...]) -> str:
    return '"' + " ".join(terms) + '"'


def _fts_prefix(terms: tuple[str, ...]) -> str:
    return " AND ".join(f'"{term}"*' for term in terms)


def _fuzzy_similarity(query: str, values: list[str]) -> float:
    normalized = query.casefold()
    return max(
        (
            SequenceMatcher(
                None,
                normalized,
                value.casefold(),
                autojunk=False,
            ).ratio()
            for value in values
            if value
        ),
        default=0.0,
    )


def _parse_aware_timestamp(value: object) -> datetime | None:
    text = str(value or "")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _source_revision(
    *,
    revision_id: object,
    source_kind: object,
    source_uri: object,
    source_fingerprint: object,
    producer_version: object,
    schema_version: object,
    generated_at: object,
    freshness: object,
) -> dict[str, object] | None:
    if revision_id is None:
        return None
    return {
        "revisionId": int(revision_id),
        "sourceKind": str(source_kind or ""),
        "sourceUri": str(source_uri or ""),
        "sourceFingerprint": str(source_fingerprint or ""),
        "producerVersion": str(producer_version or ""),
        "schemaVersion": str(schema_version or ""),
        "generatedAt": str(generated_at or ""),
        "freshness": str(freshness or "UNKNOWN").upper(),
    }


def _sql_source_revision_is_fresh(
    source_kind: object,
    source_uri: object,
    source_fingerprint: object,
    producer_version: object,
    schema_version: object,
    generated_at: object,
    freshness: object,
) -> int:
    return int(
        source_revision_is_fresh(
            {
                "sourceKind": source_kind,
                "sourceUri": source_uri,
                "sourceFingerprint": source_fingerprint,
                "producerVersion": producer_version,
                "schemaVersion": schema_version,
                "generatedAt": generated_at,
                "freshness": freshness,
            },
            require_revision_id=False,
        )
    )


def _sql_evidence_uri_is_recovered(value: object) -> int:
    return int(is_valid_generic_evidence_uri(value))


def _record_freshness(
    revision_freshness: object,
    *,
    status: object = "",
) -> str:
    if str(status or "").upper() == "STALE":
        return "STALE"
    freshness = str(revision_freshness or "UNKNOWN").upper()
    return freshness if freshness in {"FRESH", "STALE"} else "UNKNOWN"


def _revision_freshness(
    source_revision: object,
    *,
    status: object = "",
) -> str:
    if str(status or "").upper() == "STALE":
        return "STALE"
    if isinstance(source_revision, Mapping) and str(
        source_revision.get("freshness") or ""
    ).upper() == "STALE":
        return "STALE"
    return (
        "FRESH"
        if source_revision_is_fresh(source_revision)
        else "UNKNOWN"
    )


def _evidence_freshness(
    evidence_uri: object,
    source_revision: object,
    *,
    status: object = "",
) -> str:
    revision_freshness = _revision_freshness(
        source_revision,
        status=status,
    )
    if revision_freshness == "STALE":
        return "STALE"
    if (
        revision_freshness == "FRESH"
        and is_valid_generic_evidence_uri(evidence_uri)
    ):
        return "FRESH"
    return "UNKNOWN"


def _aggregate_freshness(values: list[object]) -> str:
    normalized = [str(value or "UNKNOWN").upper() for value in values]
    if any(value == "STALE" for value in normalized):
        return "STALE"
    if normalized and all(value == "FRESH" for value in normalized):
        return "FRESH"
    return "UNKNOWN"


def _evidence_set_freshness(
    evidence: list[dict[str, object]],
) -> str:
    """Treat revisions as alternative proofs for one semantic record."""

    values = [
        str(item.get("freshness") or "UNKNOWN").upper()
        for item in evidence
        if is_valid_generic_evidence_uri(item.get("evidenceUri"))
    ]
    if "FRESH" in values:
        return "FRESH"
    if "STALE" in values:
        return "STALE"
    return "UNKNOWN"


def _database_runtime_state(
    path: Path,
    *,
    expected_schema: str,
    required_tables: frozenset[str] = frozenset(),
    require_schema: bool = True,
) -> dict[str, object]:
    if not path.is_file():
        return {
            "healthy": False,
            "integrity": "missing",
            "metadata": {},
            "tables": frozenset(),
        }
    try:
        with closing(
            sqlite3.connect(
                f"file:{path.resolve().as_posix()}?mode=ro",
                uri=True,
            )
        ) as connection:
            opened = (
                connection.execute(
                    "PRAGMA schema_version"
                ).fetchone()
                is not None
            )
            metadata = dict(
                connection.execute("SELECT key, value FROM metadata")
            )
            tables = frozenset(
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type IN ('table', 'view')
                    """
                )
            )
    except (OSError, sqlite3.DatabaseError):
        return {
            "healthy": False,
            "integrity": "error",
            "metadata": {},
            "tables": frozenset(),
        }
    return {
        "healthy": (
            opened
            and required_tables <= tables
            and (
                not require_schema
                or str(metadata.get("schema_version") or "")
                == expected_schema
            )
        ),
        "integrity": "deferred_to_query_digest",
        "metadata": metadata,
        "tables": tables,
    }


def _is_sha256(value: object) -> bool:
    normalized = str(value or "")
    return len(normalized) == 64 and all(
        character in "0123456789abcdef"
        for character in normalized.lower()
    )


def _is_ontology_version(value: object) -> bool:
    normalized = str(value or "")
    parts = normalized.split("|")
    return (
        bool(normalized)
        and "ark-fact-types/v2" in parts
        and all(
            part.startswith("ark-")
            and "/v" in part
            and part.rsplit("/v", 1)[1].isdigit()
            for part in parts
        )
    )


def _declared_size_matches(
    declared: Mapping[str, object],
    path: Path,
    *,
    mutable: bool,
) -> bool:
    if mutable:
        return path.is_file()
    try:
        declared_size = int(declared.get("bytes") or -1)
        return path.is_file() and declared_size == path.stat().st_size
    except (OSError, TypeError, ValueError, OverflowError):
        return False


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _file_change_token(path: Path) -> int:
    """Return an inode change token that ordinary timestamp restore cannot hide."""

    stat = path.stat()
    if os.name != "nt":
        return stat.st_ctime_ns

    try:
        import ctypes
        from ctypes import wintypes

        class FileBasicInfo(ctypes.Structure):
            _fields_ = [
                ("CreationTime", ctypes.c_longlong),
                ("LastAccessTime", ctypes.c_longlong),
                ("LastWriteTime", ctypes.c_longlong),
                ("ChangeTime", ctypes.c_longlong),
                ("FileAttributes", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        get_info = kernel32.GetFileInformationByHandleEx
        get_info.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        get_info.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = create_file(
            str(path),
            0,
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,
            0x00000080,
            None,
        )
        if handle == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error())
        try:
            info = FileBasicInfo()
            if not get_info(
                handle,
                0,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                raise OSError(ctypes.get_last_error())
            return int(info.ChangeTime)
        finally:
            close_handle(handle)
    except (AttributeError, OSError, ValueError):
        return stat.st_ctime_ns


def _file_stat_identity(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return (
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_dev,
        stat.st_ino,
        _file_change_token(path),
    )


def _provenance_gaps(
    items: list[dict[str, object]],
    *,
    label: str,
) -> list[dict[str, object]]:
    freshness = [
        str(item.get("freshness") or "UNKNOWN").upper() for item in items
    ]
    gaps: list[dict[str, object]] = []
    if "STALE" in freshness:
        gaps.append(
            {
                "code": "STALE_SOURCE",
                "detail": f"{label}:STALE_SOURCE_REVISION",
            }
        )
    if "UNKNOWN" in freshness:
        gaps.append(
            {
                "code": "PROVENANCE_UNKNOWN",
                "detail": f"{label}:SOURCE_REVISION_REQUIRED",
            }
        )
    return gaps


class VNextKnowledgeService:
    def __init__(
        self,
        root: Path,
        *,
        _allow_unsealed_benchmark: bool = False,
    ) -> None:
        self.configured_root = root.resolve()
        # Builder-only context for an isolated pre-seal copy. Normal
        # services never set this and continue to require a sealed report.
        self._allow_unsealed_benchmark = bool(
            _allow_unsealed_benchmark
        )
        self._snapshot_resolution_error = ""
        self._snapshot_layout = "unresolved"
        try:
            location = resolve_current_snapshot(self.configured_root)
        except (OSError, ValueError) as exc:
            self.root = self.configured_root
            self.manifest_path = (
                self.configured_root / "current.json"
                if (self.configured_root / "current.json").exists()
                else self.configured_root / "manifests" / "current.json"
            )
            self._immutable_manifest_path = self.manifest_path
            self._snapshot_resolution_error = str(exc)
        else:
            self.root = location.snapshot_dir
            self.manifest_path = location.manifest_path
            self._snapshot_layout = location.layout
            self._immutable_manifest_path = (
                location.manifest_path
                if location.layout == "immutable-v2"
                else (
                    location.root
                    / "manifests"
                    / f"{location.build_id}.json"
                )
            )
        self.core_path = self.root / "core.sqlite"
        self.search_path = self.root / "search.sqlite"
        self.cache_path = self.root / "cache.sqlite"
        self._database_digest_cache: dict[
            str,
            tuple[tuple[object, ...], str],
        ] = {}
        self._immutable_file_baselines: dict[
            str,
            tuple[int, int, int, int, int],
        ] = {}
        for name in (
            *IMMUTABLE_SNAPSHOT_DATABASE_SCHEMAS,
            *DOMAIN_EXPORT_DATABASES,
        ):
            try:
                self._immutable_file_baselines[name] = (
                    _file_stat_identity(self.root / name)
                )
            except OSError:
                pass
        self._cache_hits = 0
        self._cache_misses = 0
        self._immutable_structure_error = ""
        if self._snapshot_layout == "immutable-v2":
            try:
                immutable_manifest = json.loads(
                    self.manifest_path.read_text(encoding="utf-8")
                )
                if not isinstance(immutable_manifest, Mapping):
                    raise ValueError(
                        "immutable manifest must be an object"
                    )
                validate_snapshot_database_schemas(self.root)
                validate_snapshot_projection_bindings(
                    snapshot_dir=self.root,
                    manifest=immutable_manifest,
                )
            except (OSError, sqlite3.DatabaseError, ValueError) as exc:
                self._immutable_structure_error = str(exc)

    def _declared_database_matches(
        self,
        name: str,
        declared: Mapping[str, object],
        *,
        verify_digest: bool = True,
    ) -> bool:
        """Bind immutable stores to manifest bytes without rehashing reads."""

        path = self.root / name
        mutable = name == "cache.sqlite"
        if not _is_sha256(declared.get("sha256")) or not (
            _declared_size_matches(declared, path, mutable=mutable)
        ):
            return False
        if mutable:
            return True
        if (
            not verify_digest
            and self._snapshot_layout != "immutable-v2"
        ):
            verify_digest = True
        expected = str(declared["sha256"]).lower()
        try:
            stat_identity = _file_stat_identity(path)
        except OSError:
            return False
        if not verify_digest:
            return (
                self._immutable_file_baselines.get(name)
                == stat_identity
            )
        cache_key = (
            expected,
            *stat_identity,
        )
        cached = self._database_digest_cache.get(name)
        if cached is not None and cached[0] == cache_key:
            return cached[1] == expected
        try:
            actual = _file_sha256(path)
        except OSError:
            return False
        self._database_digest_cache[name] = (cache_key, actual)
        return actual == expected

    def _snapshot_binding_error(
        self,
        *,
        verify_database_hashes: bool = True,
    ) -> str | None:
        """Validate immutable runtime artifacts and their build identity."""

        if self._snapshot_resolution_error:
            return (
                "current snapshot pointer is invalid: "
                + self._snapshot_resolution_error
            )
        if self._immutable_structure_error:
            return (
                "immutable snapshot structure is invalid: "
                + self._immutable_structure_error
            )
        try:
            validate_snapshot_journal_safety(
                self.root,
                require_delete=False,
                include_cache=False,
            )
        except ValueError as exc:
            return f"immutable snapshot journal is unsafe: {exc}"
        try:
            raw_manifest = json.loads(
                self.manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return "current snapshot manifest is unreadable"
        if not isinstance(raw_manifest, Mapping):
            return "current snapshot manifest must be an object"
        manifest = raw_manifest
        build_id = str(manifest.get("buildId") or "")
        immutable_path = self._immutable_manifest_path
        try:
            immutable_manifest = json.loads(
                immutable_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return "immutable snapshot manifest is unreadable"
        if (
            manifest.get("schema") != SNAPSHOT_SCHEMA
            or not build_id
            or not isinstance(immutable_manifest, Mapping)
            or immutable_manifest != manifest
        ):
            return "current and immutable snapshot manifests do not match"
        if self._snapshot_layout == "immutable-v2":
            try:
                validate_snapshot_source_identity(manifest)
                if not self._allow_unsealed_benchmark:
                    validate_sealed_snapshot_quality(
                        snapshot_dir=self.root,
                        manifest=manifest,
                    )
            except ValueError as exc:
                return f"sealed snapshot quality binding is invalid: {exc}"

        source = manifest.get("source")
        databases = manifest.get("databases")
        if not isinstance(source, Mapping) or not isinstance(
            databases, Mapping
        ):
            return "snapshot source and databases must be objects"
        source_inputs = source.get("inputs")
        if not isinstance(source_inputs, Mapping):
            return "snapshot source inputs must be an object"
        semantic_fingerprint = str(source.get("sha256") or "")
        discovery_fingerprint = str(
            source_inputs.get("discovery") or ""
        )
        generated_at = str(manifest.get("generatedAt") or "")
        try:
            normalized_generated_at = normalize_snapshot_generated_at(
                generated_at
            )
        except ValueError:
            normalized_generated_at = ""
        if (
            str(source.get("kind") or "") != SNAPSHOT_SOURCE_KIND
            or str(source.get("uri") or "") != SNAPSHOT_SOURCE_URI
            or not _is_sha256(semantic_fingerprint)
            or not _is_sha256(discovery_fingerprint)
            or not normalized_generated_at
            or generated_at != normalized_generated_at
        ):
            return "snapshot source identity is incomplete"
        if (
            set(source_inputs) != SNAPSHOT_SEMANTIC_INPUT_KEYS
            or any(
                not _is_sha256(value)
                for value in source_inputs.values()
            )
        ):
            return "snapshot semantic input set is incomplete"
        if (
            semantic_inputs_sha256(source_inputs)
            != semantic_fingerprint.lower()
        ):
            return "snapshot source fingerprint does not match its inputs"
        if build_id != snapshot_build_id(
            generated_at,
            semantic_fingerprint,
        ):
            return "snapshot build ID does not match its source fingerprint"

        immutable_names = frozenset(
            IMMUTABLE_SNAPSHOT_DATABASE_SCHEMAS
        )
        expected_export_names = frozenset(DOMAIN_EXPORT_DATABASES)
        declared_names = set(databases)
        allowed_names = (
            immutable_names
            | expected_export_names
            | {"cache.sqlite"}
        )
        declared_export_names = frozenset(
            name
            for name in declared_names
            if str(name).startswith("domain_exports/")
        )
        if (
            not immutable_names <= declared_names
            or declared_export_names != expected_export_names
            or any(name not in allowed_names for name in declared_names)
        ):
            return "snapshot database declarations are incomplete"

        core_metadata_for_health: Mapping[str, object] | None = None
        for (
            name,
            expected_schema,
        ) in IMMUTABLE_SNAPSHOT_DATABASE_SCHEMAS.items():
            path = self.root / name
            declared = databases.get(name)
            if (
                not isinstance(declared, Mapping)
                or not self._declared_database_matches(
                    name,
                    declared,
                    verify_digest=verify_database_hashes,
                )
            ):
                return f"{name} does not match its manifest declaration"
            try:
                with closing(
                    sqlite3.connect(
                        f"file:{path.resolve().as_posix()}?mode=ro",
                        uri=True,
                    )
                ) as connection:
                    metadata = dict(
                        connection.execute(
                            "SELECT key, value FROM metadata"
                        )
                    )
            except (OSError, sqlite3.DatabaseError):
                return f"{name} is unreadable"
            if name == "core.sqlite":
                core_metadata_for_health = metadata
            if (
                name != "core.sqlite"
                and str(metadata.get("schema_version") or "")
                != expected_schema
            ):
                return f"{name} schema does not match the snapshot"
            expected_source = (
                discovery_fingerprint
                if name in {"catalog.sqlite", "core.sqlite"}
                else semantic_fingerprint
            )
            if (
                str(metadata.get("source_fingerprint") or "")
                != expected_source
                or str(metadata.get("snapshot_build_id") or "")
                != build_id
                or str(
                    metadata.get("snapshot_source_fingerprint") or ""
                )
                != semantic_fingerprint
                or str(metadata.get("generated_at") or "")
                != generated_at
            ):
                return f"{name} build identity does not match the snapshot"
        if (
            self._snapshot_layout == "immutable-v2"
            and "runtimeHealth" in manifest
        ):
            try:
                validate_snapshot_runtime_health_summary(
                    manifest=manifest,
                    core_metadata=core_metadata_for_health or {},
                )
            except ValueError as exc:
                return f"sealed runtime health binding is invalid: {exc}"
        for name, projection_name in DOMAIN_EXPORT_DATABASES.items():
            path = self.root / name
            declared = databases.get(name)
            if not isinstance(declared, Mapping):
                return f"{name} is not declared by the snapshot"
            manifest_ontology_version = str(
                manifest.get("ontologyVersion") or ""
            )
            declared_ontology_version = str(
                declared.get("ontologyVersion") or ""
            )
            projection_version = str(
                declared.get("projectionVersion") or ""
            )
            content_digest = str(
                declared.get("contentDigest") or ""
            )
            review_config_sha256 = str(
                declared.get("reviewConfigSha256") or ""
            )
            source_revision_set_hash = str(
                declared.get("sourceRevisionSetHash") or ""
            )
            if (
                str(declared.get("schemaVersion") or "")
                != PROJECTION_SCHEMA_VERSION
                or projection_version != "v2"
                or not _is_ontology_version(
                    manifest_ontology_version
                )
                or declared_ontology_version
                != manifest_ontology_version
                or not _is_sha256(content_digest)
                or not _is_sha256(review_config_sha256)
                or not _is_sha256(source_revision_set_hash)
                or not self._declared_database_matches(
                    name,
                    declared,
                    verify_digest=verify_database_hashes,
                )
            ):
                return f"{name} does not match its manifest declaration"
            try:
                with closing(
                    sqlite3.connect(
                        f"file:{path.resolve().as_posix()}?mode=ro",
                        uri=True,
                    )
                ) as connection:
                    metadata = dict(
                        connection.execute(
                            "SELECT key, value FROM metadata"
                        )
                    )
            except (OSError, sqlite3.DatabaseError):
                return f"{name} is unreadable"
            if (
                str(metadata.get("schema_version") or "")
                != PROJECTION_SCHEMA_VERSION
                or str(metadata.get("projection_name") or "")
                != projection_name
                or str(metadata.get("projection_version") or "")
                != projection_version
                or str(metadata.get("ontology_version") or "")
                != manifest_ontology_version
                or str(metadata.get("built_at") or "")
                != generated_at
                or str(metadata.get("truth_source") or "")
                != "core.sqlite"
                or str(metadata.get("review_config_sha256") or "")
                != review_config_sha256
                or str(metadata.get("content_digest") or "")
                != content_digest
                or str(metadata.get("source_revision_set_hash") or "")
                != source_revision_set_hash
                or str(metadata.get("snapshot_build_id") or "")
                != build_id
                or str(
                    metadata.get("snapshot_source_fingerprint") or ""
                )
                != semantic_fingerprint
            ):
                return f"{name} metadata does not match the snapshot"
        return None

    def _core(
        self,
        *,
        validate_snapshot: bool = True,
    ) -> sqlite3.Connection:
        if not self.core_path.is_file():
            raise KnowledgeApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "KB_VNEXT_NOT_BUILT",
                "ARK Knowledge Base vNext snapshot is not available.",
            )
        if validate_snapshot and (
            binding_error := self._snapshot_binding_error()
        ):
            raise KnowledgeApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "KB_VNEXT_SNAPSHOT_INVALID",
                f"Snapshot runtime binding failed: {binding_error}.",
            )
        connection = sqlite3.connect(
            f"file:{self.core_path.as_posix()}?mode=ro",
            uri=True,
        )
        connection.create_function(
            "source_revision_is_fresh",
            7,
            _sql_source_revision_is_fresh,
        )
        connection.create_function(
            "evidence_uri_is_recovered",
            1,
            _sql_evidence_uri_is_recovered,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    def _search(
        self,
        *,
        validate_snapshot: bool = True,
    ) -> sqlite3.Connection:
        if not self.search_path.is_file():
            raise KnowledgeApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "KB_VNEXT_NOT_BUILT",
                "ARK Knowledge Base vNext search store is not available.",
            )
        if validate_snapshot and (
            binding_error := self._snapshot_binding_error()
        ):
            raise KnowledgeApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "KB_VNEXT_SNAPSHOT_INVALID",
                f"Snapshot runtime binding failed: {binding_error}.",
            )
        connection = sqlite3.connect(
            f"file:{self.search_path.as_posix()}?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    def _cache_outcome(
        self,
        *,
        hit: bool,
        reason: str,
    ) -> dict[str, object]:
        if hit:
            self._cache_hits += 1
        else:
            self._cache_misses += 1
        return {
            "status": "HIT" if hit else "MISS",
            "reason": reason,
            "hits": self._cache_hits,
            "misses": self._cache_misses,
        }

    def _cache_metrics(self) -> dict[str, int]:
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
        }

    def _cache_snapshot_identity(
        self,
        cache: sqlite3.Connection,
    ) -> tuple[str, str] | None:
        try:
            manifest = json.loads(
                self.manifest_path.read_text(encoding="utf-8")
            )
            metadata = dict(
                cache.execute("SELECT key, value FROM metadata")
            )
        except (OSError, json.JSONDecodeError, sqlite3.DatabaseError):
            return None
        if not isinstance(manifest, Mapping):
            return None
        source = manifest.get("source")
        if not isinstance(source, Mapping):
            return None
        build_id = str(manifest.get("buildId") or "")
        source_fingerprint = str(source.get("sha256") or "")
        generated_at = str(manifest.get("generatedAt") or "")
        if (
            not build_id
            or not _is_sha256(source_fingerprint)
            or str(metadata.get("schema_version") or "")
            != CACHE_SCHEMA_VERSION
            or str(metadata.get("source_fingerprint") or "")
            != source_fingerprint
            or str(metadata.get("snapshot_build_id") or "")
            != build_id
            or str(
                metadata.get("snapshot_source_fingerprint") or ""
            )
            != source_fingerprint
            or str(metadata.get("generated_at") or "")
            != generated_at
            or str(metadata.get("disposable") or "").casefold()
            != "true"
        ):
            return None
        return build_id, source_fingerprint

    @staticmethod
    def _cached_revision_ids(
        response: Mapping[str, object],
    ) -> tuple[int, ...]:
        revision_ids: set[int] = set()

        def visit(value: object) -> None:
            if isinstance(value, Mapping):
                for key, child in value.items():
                    if (
                        key in {"sourceRevisionId", "revisionId"}
                        and child is not None
                    ):
                        try:
                            revision_ids.add(int(child))
                        except (TypeError, ValueError):
                            pass
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        # Revisions may be attached to identity, facts, relationships, or
        # top-level Evidence.  The cache binding must cover every claim, not
        # only the response's flattened Evidence page.
        visit(response)
        return tuple(sorted(revision_ids))

    @staticmethod
    def _source_revision_set_hash(
        core: sqlite3.Connection,
        revision_ids: tuple[int, ...],
    ) -> str | None:
        if not revision_ids:
            rows: list[dict[str, object]] = []
        else:
            placeholders = ",".join("?" for _ in revision_ids)
            rows = [
                {
                    "revisionId": int(row[0]),
                    "sourceKind": str(row[1]),
                    "sourceUri": str(row[2]),
                    "sourceFingerprint": str(row[3]),
                    "producerVersion": str(row[4]),
                    "schemaVersion": str(row[5]),
                    "generatedAt": str(row[6]),
                    "freshness": str(row[7]),
                }
                for row in core.execute(
                    f"""
                    SELECT revision_id, source_kind, source_uri,
                           source_fingerprint, producer_version,
                           schema_version, generated_at, freshness_status
                    FROM source_revisions
                    WHERE revision_id IN ({placeholders})
                    ORDER BY revision_id
                    """,
                    revision_ids,
                )
            ]
            if len(rows) != len(revision_ids):
                return None
        return hashlib.sha256(
            json.dumps(
                rows,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def _current_invalidation_token(
        self,
        *,
        build_id: str,
        revision_hash: str,
    ) -> str:
        # Cache lookup must not become O(total invalidation history).  SQLite's
        # file change counter plus the WAL identity form a conservative
        # snapshot-wide invalidation generation: every committed Core change
        # invalidates cached answers, while the source-revision hash above
        # still binds each answer to its claim-specific revisions.
        paths = (self.core_path, self.core_path.with_name("core.sqlite-wal"))
        file_generation: list[dict[str, object]] = []
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            try:
                change_token = _file_change_token(path)
            except OSError:
                continue
            change_counter = ""
            if path == self.core_path:
                try:
                    with path.open("rb") as handle:
                        header = handle.read(28)
                    change_counter = header[24:28].hex()
                except OSError:
                    change_counter = ""
            file_generation.append(
                {
                    "name": path.name,
                    "bytes": stat.st_size,
                    "modifiedNs": stat.st_mtime_ns,
                    "changeToken": change_token,
                    "sqliteChangeCounter": change_counter,
                }
            )
        return hashlib.sha256(
            json.dumps(
                {
                    "buildId": build_id,
                    "sourceRevisionSet": revision_hash,
                    "coreGeneration": file_generation,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _cache_request_identity(
        request: Mapping[str, object],
    ) -> tuple[str, str]:
        request_json = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return (
            request_json,
            hashlib.sha256(request_json.encode("utf-8")).hexdigest(),
        )

    def _read_cached_query(
        self,
        request: Mapping[str, object],
    ) -> tuple[dict[str, object] | None, str]:
        if not self.cache_path.is_file():
            return None, "CACHE_UNAVAILABLE"
        request_json, fingerprint = self._cache_request_identity(request)
        try:
            cache = sqlite3.connect(
                f"file:{self.cache_path.as_posix()}?mode=ro",
                uri=True,
            )
            cache.row_factory = sqlite3.Row
            cache.execute("PRAGMA query_only=ON")
        except sqlite3.DatabaseError:
            return None, "CACHE_UNAVAILABLE"
        try:
            identity = self._cache_snapshot_identity(cache)
            if identity is None:
                return None, "BUILD_MISMATCH"
            build_id, _source_fingerprint = identity
            row = cache.execute(
                """
                SELECT snapshot_id, request_json, response_json,
                       source_revision_set_hash, invalidation_token,
                       created_at, expires_at, status
                FROM query_snapshots
                WHERE query_fingerprint=?
                """,
                (fingerprint,),
            ).fetchone()
        except sqlite3.DatabaseError:
            return None, "CACHE_UNAVAILABLE"
        finally:
            cache.close()
        if row is None:
            return None, "NOT_FOUND"
        if (
            str(row["snapshot_id"])
            != "query-snapshot://" + fingerprint
            or str(row["request_json"]) != request_json
            or str(row["status"]) != "VALID"
        ):
            return None, "ENTRY_INVALID"
        created_at = _parse_aware_timestamp(row["created_at"])
        expires_at = _parse_aware_timestamp(row["expires_at"])
        now = datetime.now(UTC)
        if (
            created_at is None
            or expires_at is None
            or created_at > now
            or expires_at <= now
            or expires_at <= created_at
        ):
            return None, "EXPIRED"
        try:
            raw_response = json.loads(str(row["response_json"]))
        except json.JSONDecodeError:
            return None, "ENTRY_INVALID"
        if not isinstance(raw_response, Mapping):
            return None, "ENTRY_INVALID"
        response = dict(raw_response)
        required_response_fields = {
            "status",
            "route",
            "evidence",
            "contextPack",
            "gap",
        }
        if not required_response_fields <= set(response):
            return None, "ENTRY_INVALID"
        revision_ids = self._cached_revision_ids(response)
        try:
            with closing(self._core()) as core:
                revision_hash = self._source_revision_set_hash(
                    core,
                    revision_ids,
                )
                if (
                    revision_hash is None
                    or revision_hash
                    != str(row["source_revision_set_hash"])
                ):
                    return None, "SOURCE_REVISION_SET_CHANGED"
                invalidation_token = self._current_invalidation_token(
                    build_id=build_id,
                    revision_hash=revision_hash,
                )
        except sqlite3.DatabaseError:
            return None, "CACHE_UNAVAILABLE"
        if invalidation_token != str(row["invalidation_token"]):
            return None, "INVALIDATION_TOKEN_CHANGED"
        response.pop("cache", None)
        return response, "VALID"

    def _page(
        self,
        *,
        items: list[dict[str, object]],
        total: int,
        limit: int,
        cursor: int,
        path: str,
        query: Mapping[str, object],
        freshness: str = "UNKNOWN",
        evidence: list[dict[str, object]] | None = None,
        gaps: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        next_cursor = cursor + len(items)
        next_query = ""
        if next_cursor < total:
            next_query = path + "?" + urlencode(
                {
                    **{
                        key: value
                        for key, value in query.items()
                        if value not in (None, "")
                    },
                    "limit": limit,
                    "cursor": next_cursor,
                }
            )
        return {
            "items": items,
            "returned": len(items),
            "omitted": max(0, total - cursor - len(items)),
            "nextQuery": next_query,
            "freshness": freshness,
            "evidence": evidence or [],
            "gap": gaps or [],
        }

    def health(self) -> dict[str, object]:
        if not self.core_path.is_file() or not self.manifest_path.is_file():
            return {
                "available": False,
                "status": "NOT_BUILT",
                "buildId": "",
                "cutover": {
                    "mode": "shadow",
                    "defaultQuerySource": "legacy",
                },
                "returned": 0,
                "omitted": 0,
                "nextQuery": "",
                "freshness": "UNKNOWN",
                "evidence": [],
                "capabilities": {
                    "effectiveCandidateExplanations": False,
                    "semanticAdapterDerivations": False,
                    "typedMapUsageEvidence": False,
                    "queryProvenance": False,
                },
                "cacheMetrics": self._cache_metrics(),
                "gap": [
                    {
                        "code": "KB_VNEXT_NOT_BUILT",
                        "detail": "Run a full vNext snapshot build.",
                    }
                ],
            }
        try:
            raw_manifest = json.loads(
                self.manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            raw_manifest = {}
        manifest = (
            dict(raw_manifest) if isinstance(raw_manifest, Mapping) else {}
        )
        build_id = str(manifest.get("buildId") or "")
        immutable_manifest = self._immutable_manifest_path
        try:
            raw_immutable_payload = json.loads(
                immutable_manifest.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            raw_immutable_payload = None
        immutable_payload = (
            dict(raw_immutable_payload)
            if isinstance(raw_immutable_payload, Mapping)
            else None
        )
        source = manifest.get("source")
        source_mapping = source if isinstance(source, Mapping) else {}
        source_inputs = source_mapping.get("inputs")
        input_mapping = (
            source_inputs if isinstance(source_inputs, Mapping) else {}
        )
        manifest_consistent = (
            manifest.get("schema") == SNAPSHOT_SCHEMA
            and bool(build_id)
            and immutable_payload == manifest
            and isinstance(source, Mapping)
            and isinstance(source_inputs, Mapping)
        )
        binding_error = self._snapshot_binding_error(
            verify_database_hashes=False,
        )

        core_state = _database_runtime_state(
            self.core_path,
            expected_schema=CORE_SCHEMA_VERSION,
            require_schema=False,
        )
        catalog_state = _database_runtime_state(
            self.root / "catalog.sqlite",
            expected_schema=CATALOG_SCHEMA_VERSION,
        )
        search_state = _database_runtime_state(
            self.root / "search.sqlite",
            expected_schema=SEARCH_SCHEMA_VERSION,
        )
        cache_state = _database_runtime_state(
            self.cache_path,
            expected_schema=CACHE_SCHEMA_VERSION,
            required_tables=frozenset(
                {
                    "query_snapshots",
                    "context_packs",
                    "answer_plans",
                    "materialized_neighborhoods",
                }
            ),
        )
        database_states = {
            "catalog.sqlite": catalog_state,
            "core.sqlite": core_state,
            "search.sqlite": search_state,
        }
        manifest_databases = manifest.get("databases")
        generated_at = str(manifest.get("generatedAt") or "")
        discovery_fingerprint = str(
            input_mapping.get("discovery")
            or source_mapping.get("sha256")
            or ""
        )
        semantic_fingerprint = str(source_mapping.get("sha256") or "")
        databases_consistent = (
            isinstance(manifest_databases, Mapping)
            and {
                "catalog.sqlite",
                "core.sqlite",
                "search.sqlite",
            }
            <= set(manifest_databases)
            and all(
                bool(state["healthy"]) for state in database_states.values()
            )
        )
        for name, state in database_states.items():
            metadata = state["metadata"]
            if not isinstance(metadata, Mapping):
                databases_consistent = False
                continue
            expected_fingerprint = (
                discovery_fingerprint
                if name in {"catalog.sqlite", "core.sqlite"}
                else semantic_fingerprint
            )
            if expected_fingerprint and str(
                metadata.get("source_fingerprint") or ""
            ) != expected_fingerprint:
                databases_consistent = False
            if str(metadata.get("snapshot_build_id") or "") != build_id:
                databases_consistent = False
            if str(
                metadata.get("snapshot_source_fingerprint") or ""
            ) != semantic_fingerprint:
                databases_consistent = False
            if generated_at and str(
                metadata.get("generated_at") or ""
            ) != generated_at:
                databases_consistent = False
            if isinstance(manifest_databases, Mapping):
                declared = manifest_databases.get(name)
                if not isinstance(declared, Mapping):
                    databases_consistent = False
                elif not self._declared_database_matches(
                    name,
                    declared,
                    verify_digest=False,
                ):
                    databases_consistent = False

        cache_metadata = cache_state["metadata"]
        cache_declared = (
            manifest_databases.get("cache.sqlite")
            if isinstance(manifest_databases, Mapping)
            else None
        )
        cache_consistent = (
            bool(cache_state["healthy"])
            and isinstance(cache_metadata, Mapping)
            and isinstance(cache_declared, Mapping)
            and self._declared_database_matches(
                "cache.sqlite",
                cache_declared,
            )
            and str(cache_metadata.get("source_fingerprint") or "")
            == semantic_fingerprint
            and str(cache_metadata.get("snapshot_build_id") or "")
            == build_id
            and str(
                cache_metadata.get("snapshot_source_fingerprint") or ""
            )
            == semantic_fingerprint
            and str(cache_metadata.get("generated_at") or "")
            == generated_at
            and str(cache_metadata.get("disposable") or "").lower()
            == "true"
        )

        metadata = (
            dict(core_state["metadata"])
            if isinstance(core_state["metadata"], Mapping)
            else {}
        )
        capabilities: dict[str, object] = {
            "schemaVersion": str(metadata.get("schema_version") or ""),
            "effectiveCandidateExplanations": False,
            "semanticAdapterDerivations": False,
            "typedMapUsageEvidence": False,
            "queryProvenance": False,
            "compatible": False,
        }
        sealed_active_stale_sources: int | None = None
        if (
            self._snapshot_layout == "immutable-v2"
            and "runtimeHealth" in manifest
        ):
            try:
                sealed_active_stale_sources = (
                    validate_snapshot_runtime_health_summary(
                        manifest=manifest,
                        core_metadata=metadata,
                    )
                )
            except ValueError:
                # The binding error above already makes the snapshot INVALID.
                sealed_active_stale_sources = 0
        active_stale_sources = 0
        if bool(core_state["healthy"]):
            with closing(self._core(validate_snapshot=False)) as core:
                capabilities = core_schema_capabilities(core)
                if capabilities["compatible"]:
                    active_stale_sources = (
                        sealed_active_stale_sources
                        if sealed_active_stale_sources is not None
                        else active_stale_source_count(core)
                    )
        compatible = bool(capabilities["compatible"])
        sources_fresh = active_stale_sources == 0
        runtime_valid = (
            manifest_consistent
            and databases_consistent
            and binding_error is None
        )
        available = runtime_valid and compatible and sources_fresh
        status = (
            "INVALID"
            if not runtime_valid
            else (
                "MIGRATION_REQUIRED"
                if not compatible
                else (
                    "STALE"
                    if not sources_fresh
                    else (
                        "READY"
                        if cache_consistent
                        else "DEGRADED_CACHE"
                    )
                )
            )
        )
        gaps = []
        if not manifest_consistent:
            gaps.append(
                {
                    "code": "KB_VNEXT_MANIFEST_MISMATCH",
                    "detail": (
                        "current.json must match its immutable build manifest."
                    ),
                }
            )
        if not databases_consistent:
            gaps.append(
                {
                    "code": "KB_VNEXT_DATABASE_INVALID",
                    "detail": (
                        "Snapshot databases must match the manifest and "
                        "required runtime schemas."
                    ),
                }
            )
        if binding_error is not None:
            gaps.append(
                {
                    "code": "KB_VNEXT_SNAPSHOT_INVALID",
                    "detail": (
                        "Snapshot runtime binding failed: "
                        f"{binding_error}."
                    ),
                }
            )
        if runtime_valid and not compatible:
            gaps.append(
                {
                    "code": "KB_VNEXT_SCHEMA_MIGRATION_REQUIRED",
                    "detail": (
                        "Build an ark-kb-core/v4 snapshot before enabling "
                        "vNext provenance-aware reads."
                    ),
                }
            )
        if runtime_valid and compatible and not sources_fresh:
            gaps.append(
                {
                    "code": "KB_VNEXT_STALE_SOURCE",
                    "detail": (
                        f"{active_stale_sources} active semantic rows lack "
                        "fresh, recovered provenance."
                    ),
                }
            )
        if available and not cache_consistent:
            gaps.append(
                {
                    "code": "KB_VNEXT_CACHE_DEGRADED",
                    "detail": (
                        "The disposable query cache is unavailable or does "
                        "not match the active snapshot; semantic reads "
                        "continue without cache persistence."
                    ),
                }
            )
        return {
            "available": available,
            "status": status,
            "buildId": str(manifest.get("buildId") or ""),
            "schemaVersion": str(capabilities["schemaVersion"]),
            "ontologyVersion": str(metadata.get("ontology_version") or ""),
            "cutover": manifest.get(
                "cutover",
                {"mode": "shadow", "defaultQuerySource": "legacy"},
            ),
            "returned": int(available),
            "omitted": 0,
            "nextQuery": "",
            "freshness": (
                "FRESH"
                if available
                else (
                    "STALE"
                    if runtime_valid and not sources_fresh
                    else "UNKNOWN"
                )
            ),
            "evidence": [
                {
                    "sourceUri": str(
                        source_mapping.get("uri", "")
                    ),
                    "sha256": str(
                        source_mapping.get("sha256", "")
                    ),
                }
            ],
            "capabilities": {
                "effectiveCandidateExplanations": bool(
                    capabilities["effectiveCandidateExplanations"]
                ),
                "semanticAdapterDerivations": bool(
                    capabilities["semanticAdapterDerivations"]
                ),
                "typedMapUsageEvidence": bool(
                    capabilities["typedMapUsageEvidence"]
                ),
                "queryProvenance": bool(
                    capabilities["queryProvenance"]
                ),
            },
            "cacheMetrics": self._cache_metrics(),
            "gap": gaps,
        }

    def _rank_search_candidates(
        self,
        search: sqlite3.Connection,
        query: str,
    ) -> list[dict[str, object]]:
        ranked: dict[int, dict[str, object]] = {}

        def add(
            rows: list[tuple[int, str]],
            *,
            match_type: str,
            base_score: float,
        ) -> None:
            for index, (entity_id, matched_alias) in enumerate(rows):
                if (
                    entity_id in ranked
                    or len(ranked) >= MAX_SEARCH_CANDIDATES
                ):
                    continue
                candidate: dict[str, object] = {
                    "entityId": entity_id,
                    "matchType": match_type,
                    "score": round(
                        max(0.0, base_score - index / 1_000_000),
                        6,
                    ),
                }
                if matched_alias:
                    candidate["matchedAlias"] = matched_alias
                ranked[entity_id] = candidate

        exact_uri_rows = [
            (int(row[0]), "")
            for row in search.execute(
                """
                SELECT entity_id
                FROM entity_search_meta
                WHERE canonical_uri=?
                ORDER BY entity_id
                LIMIT ?
                """,
                (query, MAX_SEARCH_CANDIDATES),
            )
        ]
        add(
            exact_uri_rows,
            match_type="EXACT_CANONICAL_URI",
            base_score=1.0,
        )
        exact_alias_rows = [
            (int(row[0]), str(row[1]))
            for row in search.execute(
                """
                SELECT entity_id, alias
                FROM search_aliases
                WHERE alias=?
                ORDER BY
                  CASE confidence
                    WHEN 'HIGH' THEN 0
                    WHEN 'MEDIUM' THEN 1
                    ELSE 2
                  END,
                  entity_id
                LIMIT ?
                """,
                (query, MAX_SEARCH_CANDIDATES),
            )
        ]
        add(
            exact_alias_rows,
            match_type="EXACT_ALIAS",
            base_score=0.98,
        )

        terms = _search_terms(query)

        def fts_rows(expression: str) -> list[tuple[int, str]]:
            if not expression:
                return []
            return [
                (int(row[0]), "")
                for row in search.execute(
                    """
                    SELECT CAST(entity_id AS INTEGER), bm25(entities_fts)
                    FROM entities_fts
                    WHERE entities_fts MATCH ?
                    ORDER BY bm25(entities_fts), CAST(entity_id AS INTEGER)
                    LIMIT ?
                    """,
                    (expression, MAX_SEARCH_CANDIDATES),
                )
            ]

        if terms:
            add(
                fts_rows(_fts_phrase(terms)),
                match_type="FTS_PHRASE",
                base_score=0.9,
            )
            add(
                fts_rows(_fts_prefix(terms)),
                match_type="FTS_PREFIX",
                base_score=0.8,
            )

            anchor = terms[0][: min(2, len(terms[0]))]
            fuzzy_ids = [
                int(row[0])
                for row in search.execute(
                    """
                    SELECT CAST(entity_id AS INTEGER)
                    FROM entities_fts
                    WHERE entities_fts MATCH ?
                    ORDER BY bm25(entities_fts), CAST(entity_id AS INTEGER)
                    LIMIT ?
                    """,
                    (_fts_prefix((anchor,)), MAX_FUZZY_CANDIDATES),
                )
            ]
            alias_anchor_ids = [
                int(row[0])
                for row in search.execute(
                    """
                    SELECT entity_id
                    FROM search_aliases
                    WHERE alias>=? AND alias<?
                    ORDER BY alias, entity_id
                    LIMIT ?
                    """,
                    (
                        anchor,
                        anchor + "\U0010ffff",
                        MAX_FUZZY_CANDIDATES,
                    ),
                )
            ]
            fuzzy_ids = list(
                dict.fromkeys([*fuzzy_ids, *alias_anchor_ids])
            )[:MAX_FUZZY_CANDIDATES]
            fuzzy_ids = [
                entity_id
                for entity_id in fuzzy_ids
                if entity_id not in ranked
            ]
            if fuzzy_ids:
                placeholders = ",".join("?" for _ in fuzzy_ids)
                meta = {
                    int(row["entity_id"]): row
                    for row in search.execute(
                        f"""
                        SELECT entity_id, canonical_uri,
                               display_name, internal_name
                        FROM entity_search_meta
                        WHERE entity_id IN ({placeholders})
                        """,
                        fuzzy_ids,
                    )
                }
                aliases: dict[int, list[str]] = {}
                for row in search.execute(
                    f"""
                    WITH ranked_aliases AS (
                      SELECT
                        entity_id, alias,
                        ROW_NUMBER() OVER (
                          PARTITION BY entity_id
                          ORDER BY alias
                        ) AS alias_rank
                      FROM search_aliases
                      WHERE entity_id IN ({placeholders})
                    )
                    SELECT entity_id, alias
                    FROM ranked_aliases
                    WHERE alias_rank<=?
                    ORDER BY entity_id, alias
                    """,
                    (
                        *fuzzy_ids,
                        MAX_FUZZY_ALIASES_PER_ENTITY,
                    ),
                ):
                    aliases.setdefault(int(row[0]), []).append(str(row[1]))
                fuzzy: list[tuple[float, int]] = []
                for entity_id in fuzzy_ids:
                    row = meta.get(entity_id)
                    if row is None:
                        continue
                    canonical_uri = str(row["canonical_uri"])
                    score = _fuzzy_similarity(
                        query,
                        [
                            canonical_uri,
                            canonical_uri.rsplit("/", 1)[-1],
                            str(row["display_name"]),
                            str(row["internal_name"]),
                            *aliases.get(entity_id, []),
                        ],
                    )
                    if score >= MIN_FUZZY_SCORE:
                        fuzzy.append((score, entity_id))
                for score, entity_id in sorted(
                    fuzzy,
                    key=lambda item: (-item[0], item[1]),
                ):
                    if len(ranked) >= MAX_SEARCH_CANDIDATES:
                        break
                    ranked[entity_id] = {
                        "entityId": entity_id,
                        "matchType": "FUZZY_CANDIDATE",
                        "score": round(score * 0.7, 6),
                    }

        return list(ranked.values())

    def search_entities(
        self,
        *,
        query: str,
        limit: object = 25,
        cursor: object = 0,
    ) -> dict[str, object]:
        query = query.strip()
        if not query or len(query) > 500:
            raise KnowledgeApiError(
                HTTPStatus.BAD_REQUEST,
                "REQUEST_INVALID",
                "q is required and must be at most 500 characters.",
            )
        page_size = _bounded_int(
            limit,
            name="limit",
            default=25,
            minimum=1,
            maximum=MAX_PAGE_SIZE,
        )
        offset = _bounded_int(
            cursor,
            name="cursor",
            default=0,
            minimum=0,
            maximum=MAX_CURSOR,
        )
        with closing(self._search()) as search:
            ranked = self._rank_search_candidates(search, query)
        total = len(ranked)
        page = ranked[offset : offset + page_size]
        entity_ids = [int(item["entityId"]) for item in page]
        rows_by_id: dict[int, sqlite3.Row] = {}
        if entity_ids:
            placeholders = ",".join("?" for _ in entity_ids)
            with closing(self._core(validate_snapshot=False)) as core:
                rows_by_id = {
                    int(row["entity_id"]): row
                    for row in core.execute(
                f"""
                SELECT
                    entity.entity_id, entity.canonical_uri,
                    entity.entity_kind, entity.display_name,
                    entity.internal_name, entity.status,
                    entity.confidence,
                    revision.revision_id AS identity_revision_id,
                    revision.source_kind AS identity_source_kind,
                    revision.source_uri AS identity_source_uri,
                    revision.source_fingerprint
                        AS identity_source_fingerprint,
                    revision.producer_version AS identity_producer_version,
                    revision.schema_version AS identity_schema_version,
                    revision.generated_at AS identity_generated_at,
                    revision.freshness_status AS identity_freshness
                FROM entities AS entity
                LEFT JOIN packages AS package
                  ON package.package_id=entity.package_id
                LEFT JOIN source_revisions AS revision
                  ON revision.revision_id=package.current_revision_id
                WHERE entity.entity_id IN ({placeholders})
                """,
                        entity_ids,
                    )
                }
        items = []
        for candidate in page:
            row = rows_by_id.get(int(candidate["entityId"]))
            if row is None:
                continue
            source_revision = _source_revision(
                revision_id=row["identity_revision_id"],
                source_kind=row["identity_source_kind"],
                source_uri=row["identity_source_uri"],
                source_fingerprint=row["identity_source_fingerprint"],
                producer_version=row["identity_producer_version"],
                schema_version=row["identity_schema_version"],
                generated_at=row["identity_generated_at"],
                freshness=row["identity_freshness"],
            )
            freshness = _revision_freshness(
                source_revision,
                status=row["status"],
            )
            item = {
                "entityId": int(row["entity_id"]),
                "canonicalUri": str(row["canonical_uri"]),
                "entityKind": str(row["entity_kind"]),
                "displayName": str(row["display_name"] or ""),
                "internalName": str(row["internal_name"] or ""),
                "status": str(row["status"]),
                "confidence": str(row["confidence"]),
                "sourceRevision": source_revision,
                "freshness": freshness,
                "matchType": str(candidate["matchType"]),
                "score": float(candidate["score"]),
            }
            if candidate.get("matchedAlias"):
                item["matchedAlias"] = str(candidate["matchedAlias"])
            items.append(item)
        evidence = [
            {
                "entityId": item["entityId"],
                "evidenceUri": item["sourceRevision"]["sourceUri"],
                "evidenceRole": "IDENTITY_SOURCE",
                "sourceRevisionId": item["sourceRevision"]["revisionId"],
                "sourceRevision": item["sourceRevision"],
                "freshness": item["freshness"],
            }
            for item in items
            if isinstance(item.get("sourceRevision"), Mapping)
        ]
        return self._page(
            items=items,
            total=total,
            limit=page_size,
            cursor=offset,
            path="/api/kb/entities/search",
            query={"q": query},
            freshness=_aggregate_freshness(
                [item["freshness"] for item in items]
            ),
            evidence=evidence,
            gaps=_provenance_gaps(items, label="ENTITY_IDENTITY"),
        )

    def _entity_exists(
        self, core: sqlite3.Connection, entity_id: int
    ) -> sqlite3.Row:
        row = core.execute(
            """
            SELECT
                entity.*,
                revision.revision_id AS identity_revision_id,
                revision.source_kind AS identity_source_kind,
                revision.source_uri AS identity_source_uri,
                revision.source_fingerprint
                    AS identity_source_fingerprint,
                revision.producer_version AS identity_producer_version,
                revision.schema_version AS identity_schema_version,
                revision.generated_at AS identity_generated_at,
                revision.freshness_status AS identity_freshness
            FROM entities AS entity
            LEFT JOIN packages AS package
              ON package.package_id=entity.package_id
            LEFT JOIN source_revisions AS revision
              ON revision.revision_id=package.current_revision_id
            WHERE entity.entity_id=?
            """,
            (entity_id,),
        ).fetchone()
        if row is None:
            raise KnowledgeApiError(
                HTTPStatus.NOT_FOUND,
                "KB_ENTITY_NOT_FOUND",
                "Knowledge entity was not found.",
            )
        return row

    def entity(self, entity_id: int) -> dict[str, object]:
        with closing(self._core()) as core:
            capabilities = core_schema_capabilities(core)
            if not capabilities["compatible"]:
                raise KnowledgeApiError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "KB_VNEXT_SCHEMA_MIGRATION_REQUIRED",
                    (
                        "Build an ark-kb-core/v4 snapshot before using "
                        "provenance-aware entity reads."
                    ),
                )
            row = self._entity_exists(core, entity_id)
            identity_source = _source_revision(
                revision_id=row["identity_revision_id"],
                source_kind=row["identity_source_kind"],
                source_uri=row["identity_source_uri"],
                source_fingerprint=row["identity_source_fingerprint"],
                producer_version=row["identity_producer_version"],
                schema_version=row["identity_schema_version"],
                generated_at=row["identity_generated_at"],
                freshness=row["identity_freshness"],
            )
            identity_freshness = _revision_freshness(
                identity_source,
                status=row["status"],
            )
            role_rows = list(
                core.execute(
                    """
                    SELECT
                        role.role, role.confidence, role.status,
                        role.reasons_json,
                        revision.revision_id,
                        revision.source_kind,
                        revision.source_uri,
                        revision.source_fingerprint,
                        revision.producer_version,
                        revision.schema_version,
                        revision.generated_at,
                        revision.freshness_status
                    FROM knowledge_roles AS role
                    LEFT JOIN source_revisions AS revision
                      ON revision.revision_id=role.source_revision_id
                    WHERE role.entity_id=?
                    ORDER BY role.role
                    """,
                    (entity_id,),
                )
            )
            roles = []
            for item in role_rows:
                source_revision = _source_revision(
                    revision_id=item[4],
                    source_kind=item[5],
                    source_uri=item[6],
                    source_fingerprint=item[7],
                    producer_version=item[8],
                    schema_version=item[9],
                    generated_at=item[10],
                    freshness=item[11],
                )
                roles.append(
                    {
                        "role": str(item[0]),
                        "confidence": str(item[1]),
                        "status": str(item[2]),
                        "reasons": json.loads(str(item[3])),
                        "sourceRevision": source_revision,
                        "freshness": _revision_freshness(
                            source_revision,
                            status=item[2],
                        ),
                    }
                )
            domain_rows = list(
                core.execute(
                    """
                    SELECT
                        membership.domain_id,
                        membership.membership_kind,
                        membership.confidence,
                        membership.status,
                        membership.evidence_id,
                        revision.revision_id,
                        revision.source_kind,
                        revision.source_uri,
                        revision.source_fingerprint,
                        revision.producer_version,
                        revision.schema_version,
                        revision.generated_at,
                        revision.freshness_status
                    FROM domain_memberships AS membership
                    LEFT JOIN source_revisions AS revision
                      ON revision.revision_id=
                         membership.source_revision_id
                    WHERE membership.entity_id=?
                    ORDER BY
                        membership.domain_id,
                        membership.membership_kind
                    """,
                    (entity_id,),
                )
            )
            domains = []
            for item in domain_rows:
                source_revision = _source_revision(
                    revision_id=item[5],
                    source_kind=item[6],
                    source_uri=item[7],
                    source_fingerprint=item[8],
                    producer_version=item[9],
                    schema_version=item[10],
                    generated_at=item[11],
                    freshness=item[12],
                )
                domains.append(
                    {
                        "domainId": str(item[0]),
                        "membershipKind": str(item[1]),
                        "confidence": str(item[2]),
                        "status": str(item[3]),
                        "evidenceUri": str(item[4]),
                        "sourceRevision": source_revision,
                        "freshness": _revision_freshness(
                            source_revision,
                            status=item[3],
                        ),
                    }
                )
        entity = {
            "entityId": int(row["entity_id"]),
            "canonicalUri": str(row["canonical_uri"]),
            "entityKind": str(row["entity_kind"]),
            "displayName": str(row["display_name"] or ""),
            "internalName": str(row["internal_name"] or ""),
            "status": str(row["status"]),
            "confidence": str(row["confidence"]),
            "sourceRevision": identity_source,
            "freshness": identity_freshness,
        }
        evidence: list[dict[str, object]] = []
        if identity_source is not None:
            evidence.append(
                {
                    "entityId": entity_id,
                    "evidenceUri": identity_source["sourceUri"],
                    "evidenceRole": "IDENTITY_SOURCE",
                    "sourceRevisionId": identity_source["revisionId"],
                    "sourceRevision": identity_source,
                    "freshness": identity_freshness,
                }
            )
        evidence.extend(
            {
                "entityId": entity_id,
                "knowledgeRole": role["role"],
                "evidenceUri": role["sourceRevision"]["sourceUri"],
                "evidenceRole": "ROLE_CLASSIFICATION_SOURCE",
                "sourceRevisionId": role["sourceRevision"]["revisionId"],
                "sourceRevision": role["sourceRevision"],
                "freshness": role["freshness"],
            }
            for role in roles
            if isinstance(role.get("sourceRevision"), Mapping)
        )
        evidence.extend(
            {
                "entityId": entity_id,
                "domainId": domain["domainId"],
                "evidenceUri": domain["evidenceUri"],
                "evidenceRole": "DOMAIN_MEMBERSHIP",
                "sourceRevisionId": domain["sourceRevision"]["revisionId"],
                "sourceRevision": domain["sourceRevision"],
                "freshness": domain["freshness"],
            }
            for domain in domains
            if isinstance(domain.get("sourceRevision"), Mapping)
        )
        records = [entity, *roles, *domains]
        return {
            "entity": entity,
            "roles": roles,
            "domains": domains,
            "returned": 1 + len(roles) + len(domains),
            "omitted": 0,
            "nextQuery": "",
            "freshness": _aggregate_freshness(
                [item["freshness"] for item in records]
            ),
            "evidence": evidence,
            "gap": _provenance_gaps(
                records,
                label="ENTITY_DETAIL",
            ),
        }

    def entity_collection(
        self,
        entity_id: int,
        *,
        kind: str,
        limit: object = 50,
        cursor: object = 0,
    ) -> dict[str, object]:
        page_size = _bounded_int(
            limit,
            name="limit",
            default=50,
            minimum=1,
            maximum=MAX_PAGE_SIZE,
        )
        offset = _bounded_int(
            cursor,
            name="cursor",
            default=0,
            minimum=0,
            maximum=MAX_CURSOR,
        )
        candidate_schema_unavailable = False
        collection_freshness = "UNKNOWN"
        provenance_gaps: list[dict[str, object]] = []
        fact_evidence: list[dict[str, object]] = []
        class_evidence: list[dict[str, object]] = []
        class_freshness = "UNKNOWN"
        with closing(self._core()) as core:
            self._entity_exists(core, entity_id)
            if kind == "facts":
                count_sql = (
                    "SELECT COUNT(*) FROM facts "
                    "WHERE subject_entity_id=? AND current=1"
                )
                rows_sql = """
                    SELECT
                        fact_id, fact_type, fact_name, scope_kind,
                        value_kind, value_text, value_number,
                        value_integer, value_json, unit, status, confidence
                    FROM facts
                    WHERE subject_entity_id=? AND current=1
                    ORDER BY fact_type, fact_name, fact_id
                    LIMIT ? OFFSET ?
                """
                total = int(core.execute(count_sql, (entity_id,)).fetchone()[0])
                rows = list(
                    core.execute(rows_sql, (entity_id, page_size, offset))
                )
                items = [
                    {
                        "factId": int(row[0]),
                        "factType": str(row[1]),
                        "factName": str(row[2]),
                        "scopeKind": str(row[3]),
                        "valueKind": str(row[4]),
                        "valueText": row[5],
                        "valueNumber": row[6],
                        "valueInteger": row[7],
                        "valueJson": row[8],
                        "unit": str(row[9]),
                        "status": str(row[10]),
                        "confidence": str(row[11]),
                    }
                    for row in rows
                ]
                fact_ids = [int(row[0]) for row in rows]
                evidence = self._evidence_for_facts(core, fact_ids)
                evidence_by_fact: dict[int, list[dict[str, object]]] = {}
                for item in evidence:
                    evidence_by_fact.setdefault(
                        int(item["factId"]), []
                    ).append(item)
                for item in items:
                    fact_evidence = evidence_by_fact.get(
                        int(item["factId"]), []
                    )
                    revisions_by_id = {
                        int(revision["revisionId"]): revision
                        for evidence_item in fact_evidence
                        if isinstance(
                            revision := evidence_item.get(
                                "sourceRevision"
                            ),
                            Mapping,
                        )
                    }
                    revisions = [
                        revisions_by_id[revision_id]
                        for revision_id in sorted(revisions_by_id)
                    ]
                    item["sourceRevision"] = (
                        revisions[0] if len(revisions) == 1 else None
                    )
                    item["sourceRevisions"] = revisions
                    item["freshness"] = _record_freshness(
                        _evidence_set_freshness(fact_evidence),
                        status=item["status"],
                    )
                collection_freshness = _aggregate_freshness(
                    [item["freshness"] for item in items]
                )
                provenance_gaps = _provenance_gaps(
                    items,
                    label="FACT",
                )
            elif kind == "relationships":
                total = int(
                    core.execute(
                        """
                        SELECT COUNT(*) FROM edges
                        WHERE source_entity_id=? OR target_entity_id=?
                        """,
                        (entity_id, entity_id),
                    ).fetchone()[0]
                )
                rows = core.execute(
                    """
                    SELECT
                        edge.edge_id, source.canonical_uri,
                        target.canonical_uri, edge.edge_type,
                        edge.edge_strength, edge.status, edge.confidence,
                        edge.evidence_uri,
                        revision.revision_id,
                        revision.source_kind,
                        revision.source_uri,
                        revision.source_fingerprint,
                        revision.producer_version,
                        revision.schema_version,
                        revision.generated_at,
                        revision.freshness_status
                    FROM edges AS edge
                    JOIN entities AS source
                      ON source.entity_id=edge.source_entity_id
                    JOIN entities AS target
                      ON target.entity_id=edge.target_entity_id
                    LEFT JOIN source_revisions AS revision
                      ON revision.revision_id=edge.source_revision_id
                    WHERE edge.source_entity_id=? OR edge.target_entity_id=?
                    ORDER BY edge.edge_id
                    LIMIT ? OFFSET ?
                    """,
                    (entity_id, entity_id, page_size, offset),
                )
                items = []
                evidence = []
                for row in rows:
                    source_revision = _source_revision(
                        revision_id=row[8],
                        source_kind=row[9],
                        source_uri=row[10],
                        source_fingerprint=row[11],
                        producer_version=row[12],
                        schema_version=row[13],
                        generated_at=row[14],
                        freshness=row[15],
                    )
                    freshness = _evidence_freshness(
                        row[7],
                        source_revision,
                        status=row[5],
                    )
                    edge_evidence = {
                        "edgeId": int(row[0]),
                        "evidenceUri": str(row[7]),
                        "evidenceRole": "EDGE_EVIDENCE",
                        "sourceRevisionId": (
                            source_revision["revisionId"]
                            if source_revision is not None
                            else None
                        ),
                        "sourceRevision": source_revision,
                        "freshness": freshness,
                    }
                    items.append({
                        "edgeId": int(row[0]),
                        "sourceUri": str(row[1]),
                        "targetUri": str(row[2]),
                        "edgeType": str(row[3]),
                        "edgeStrength": str(row[4]),
                        "status": str(row[5]),
                        "confidence": str(row[6]),
                        "evidenceUri": str(row[7]),
                        "sourceRevisionId": (
                            source_revision["revisionId"]
                            if source_revision is not None
                            else None
                        ),
                        "sourceRevision": source_revision,
                        "freshness": freshness,
                        "evidence": [edge_evidence],
                    })
                    evidence.append(edge_evidence)
                collection_freshness = _aggregate_freshness(
                    [item["freshness"] for item in items]
                )
                provenance_gaps = _provenance_gaps(
                    items,
                    label="RELATIONSHIP",
                )
            elif kind == "coverage":
                total = int(
                    core.execute(
                        "SELECT COUNT(*) FROM coverage WHERE entity_id=?",
                        (entity_id,),
                    ).fetchone()[0]
                )
                rows = core.execute(
                    """
                    SELECT * FROM coverage
                    WHERE entity_id=? ORDER BY stage
                    LIMIT ? OFFSET ?
                    """,
                    (entity_id, page_size, offset),
                )
                items = [
                    {
                        "stage": str(row["stage"]),
                        "status": str(row["status"]),
                        "confirmed": int(row["confirmed_count"]),
                        "heuristic": int(row["heuristic_count"]),
                        "ambiguous": int(row["ambiguous_count"]),
                        "notRecovered": int(row["not_recovered_count"]),
                        "sourceNotAvailable": int(
                            row["source_not_available_count"]
                        ),
                        "stale": int(row["stale_count"]),
                        "failureReason": str(row["failure_reason"]),
                    }
                    for row in rows
                ]
                evidence = []
                collection_freshness = (
                    "STALE"
                    if any(
                        item["status"] == "STALE"
                        or int(item["stale"]) > 0
                        for item in items
                    )
                    else "UNKNOWN"
                )
            elif kind == "effective-defaults":
                total = int(
                    core.execute(
                        "SELECT COUNT(*) FROM effective_facts WHERE entity_id=?",
                        (entity_id,),
                    ).fetchone()[0]
                )
                rows = core.execute(
                    """
                    SELECT
                        effective.fact_name, effective.fact_id,
                        effective.inherited_from_entity_id,
                        effective.resolution_chain_json,
                        effective.resolution_status,
                        effective.source_revision_set_hash,
                        fact.value_kind, fact.value_text, fact.value_number,
                        fact.value_integer, fact.value_json, fact.status,
                        fact.confidence
                    FROM effective_facts AS effective
                    LEFT JOIN facts AS fact
                      ON fact.fact_id=effective.fact_id
                     AND fact.current=1
                    WHERE effective.entity_id=?
                    ORDER BY effective.fact_name
                    LIMIT ? OFFSET ?
                    """,
                    (entity_id, page_size, offset),
                )
                rows = list(rows)
                items = [
                    {
                        "factName": str(row[0]),
                        "factId": (
                            int(row[1]) if row[1] is not None else None
                        ),
                        "inheritedFromEntityId": row[2],
                        "resolutionChain": json.loads(str(row[3])),
                        "resolutionStatus": str(row[4]),
                        "sourceRevisionSetHash": str(row[5]),
                        "valueKind": (
                            str(row[6]) if row[6] is not None else None
                        ),
                        "valueText": row[7],
                        "valueNumber": row[8],
                        "valueInteger": row[9],
                        "valueJson": row[10],
                        "status": (
                            str(row[11]) if row[11] is not None else None
                        ),
                        "confidence": (
                            str(row[12]) if row[12] is not None else None
                        ),
                    }
                    for row in rows
                ]
                candidate_explanations = (
                    load_effective_candidate_explanations(
                        core,
                        entity_id=entity_id,
                        fact_names=(
                            str(item["factName"]) for item in items
                        ),
                    )
                )
                for item in items:
                    item.update(
                        candidate_explanations[str(item["factName"])]
                    )
                candidate_schema_unavailable = any(
                    item.get("candidateExplanationStatus")
                    == CANDIDATE_EXPLANATION_SCHEMA_MIGRATION_REQUIRED
                    for item in items
                )
                fact_evidence = self._evidence_for_facts(
                    core, [row[1] for row in rows]
                )
                class_evidence = load_effective_class_evidence(
                    core,
                    entity_id=entity_id,
                    effective_facts=items,
                )
                class_freshness = effective_class_evidence_freshness(
                    class_evidence
                )
                evidence = [*fact_evidence, *class_evidence]
            else:
                raise AssertionError(kind)
        stale_evidence_fact_ids: set[int] = set()
        unknown_evidence_fact_ids: set[int] = set()
        if kind == "effective-defaults":
            returned_fact_ids = {
                int(fact_id)
                for item in items
                if (fact_id := item.get("factId")) is not None
            }
            evidenced_fact_ids = {
                int(fact_id)
                for item in fact_evidence
                if (fact_id := item.get("factId")) is not None
                and is_valid_generic_evidence_uri(
                    item.get("evidenceUri")
                )
            }
            fresh_fact_ids = {
                int(fact_id)
                for item in fact_evidence
                if (fact_id := item.get("factId")) is not None
                and is_valid_generic_evidence_uri(
                    item.get("evidenceUri")
                )
                and str(item.get("freshness") or "").upper() == "FRESH"
            }
            stale_evidence_fact_ids = (
                returned_fact_ids - fresh_fact_ids
            ) & evidenced_fact_ids
            unknown_evidence_fact_ids = (
                returned_fact_ids - evidenced_fact_ids
            )
            gaps = [
                {
                    "code": "COVERAGE_OPEN",
                    "detail": str(item["resolutionStatus"]),
                }
                for item in items
                if item["factId"] is None
                or item["resolutionStatus"] != "RESOLVED"
            ]
            gaps.extend(
                {
                    "code": "COVERAGE_OPEN",
                    "detail": (
                        "EFFECTIVE_DEFAULT:"
                        + str(item["factName"])
                        + ":CURRENT_FACT_MISSING"
                    ),
                }
                for item in items
                if item["factId"] is not None
                and (
                    item.get("valueKind") is None
                    or item.get("status") is None
                )
            )
            if candidate_schema_unavailable:
                gaps.append(
                    {
                        "code": "KB_VNEXT_SCHEMA_MIGRATION_REQUIRED",
                        "detail": (
                            "Core v2 effective candidate lineage is "
                            "required for a complete explanation."
                        ),
                    }
                )
            gaps.extend(
                {
                    "code": "STALE_SOURCE",
                    "detail": (
                        "EFFECTIVE_DEFAULT:"
                        + str(item["factName"])
                        + ":FRESH_EVIDENCE_REQUIRED"
                    ),
                }
                for item in items
                if item.get("factId") in (
                    stale_evidence_fact_ids
                    | unknown_evidence_fact_ids
                )
            )
            has_resolved_effective = any(
                item.get("factId") is not None
                and item.get("resolutionStatus") == "RESOLVED"
                for item in items
            )
            if has_resolved_effective and class_freshness == "STALE":
                gaps.append(
                    {
                        "code": "STALE_SOURCE",
                        "detail": (
                            "EFFECTIVE_DEFAULT:"
                            "STALE_EFFECTIVE_CLASS_EVIDENCE"
                        ),
                    }
                )
            elif has_resolved_effective and class_freshness != "FRESH":
                gaps.append(
                    {
                        "code": "COVERAGE_OPEN",
                        "detail": (
                            "EFFECTIVE_DEFAULT:"
                            "CLASS_EVIDENCE_REQUIRED"
                        ),
                    }
                )
        else:
            gaps = [
                {
                    "code": "COVERAGE_OPEN",
                    "detail": item.get("failureReason", ""),
                }
                for item in items
                if item.get("status")
                in {
                    "UNKNOWN",
                    "AMBIGUOUS",
                    "NOT_RECOVERED",
                    "SOURCE_NOT_AVAILABLE",
                    "STALE",
                }
            ]
            gaps.extend(provenance_gaps)
        stale = any(
            item.get("status") == "STALE"
            or item.get("resolutionStatus") == "STALE"
            for item in items
        )
        unresolved_effective = (
            kind == "effective-defaults"
            and (
                candidate_schema_unavailable
                or any(
                    item.get("factId") is None
                    or item.get("resolutionStatus") != "RESOLVED"
                    or item.get("valueKind") is None
                    or item.get("status") is None
                    for item in items
                )
            )
        )
        return self._page(
            items=items,
            total=total,
            limit=page_size,
            cursor=offset,
            path=f"/api/kb/entities/{entity_id}/{kind}",
            query={},
            freshness=(
                (
                    "STALE"
                    if (
                        stale
                        or stale_evidence_fact_ids
                        or class_freshness == "STALE"
                    )
                    else (
                        "UNKNOWN"
                        if (
                            unresolved_effective
                            or unknown_evidence_fact_ids
                            or (
                                any(
                                    item.get("factId") is not None
                                    and item.get("resolutionStatus")
                                    == "RESOLVED"
                                    for item in items
                                )
                                and class_freshness != "FRESH"
                            )
                        )
                        else "FRESH"
                    )
                )
                if kind == "effective-defaults"
                else collection_freshness
            ),
            evidence=evidence,
            gaps=gaps,
        )

    def _evidence_for_facts(
        self,
        core: sqlite3.Connection,
        fact_ids: list[int | None],
    ) -> list[dict[str, object]]:
        values = sorted(
            {int(fact_id) for fact_id in fact_ids if fact_id is not None}
        )
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        result = []
        for row in core.execute(
            f"""
            SELECT
                evidence.fact_id, evidence.source_revision_id,
                evidence.evidence_uri, evidence.evidence_role,
                revision.revision_id,
                revision.source_kind,
                revision.source_uri,
                revision.source_fingerprint,
                revision.producer_version,
                revision.schema_version,
                revision.generated_at,
                revision.freshness_status
            FROM fact_evidence AS evidence
            LEFT JOIN source_revisions AS revision
              ON revision.revision_id=evidence.source_revision_id
            WHERE evidence.fact_id IN ({placeholders})
            ORDER BY evidence.fact_id, evidence.evidence_uri
            """,
            values,
        ):
            source_revision = _source_revision(
                revision_id=row[4],
                source_kind=row[5],
                source_uri=row[6],
                source_fingerprint=row[7],
                producer_version=row[8],
                schema_version=row[9],
                generated_at=row[10],
                freshness=row[11],
            )
            result.append({
                "factId": int(row[0]),
                "sourceRevisionId": int(row[1]),
                "evidenceUri": str(row[2]),
                "evidenceRole": str(row[3]),
                "sourceRevision": source_revision,
                "freshness": _evidence_freshness(
                    row[2],
                    source_revision,
                ),
            })
        return result

    def query(self, body: Mapping[str, object]) -> dict[str, object]:
        allowed = {
            "entity",
            "answerMode",
            "factTypes",
            "factNames",
            "edgeTypes",
            "requiresNative",
            "requiresRuntime",
            "requiresMapEvidence",
            "evidenceLimit",
            "budgetTokens",
        }
        if extra := set(body) - allowed:
            raise KnowledgeApiError(
                HTTPStatus.BAD_REQUEST,
                "REQUEST_INVALID",
                f"Unsupported query fields: {sorted(extra)}",
            )
        entity_query = str(body.get("entity") or "").strip()
        if not entity_query or len(entity_query) > 500:
            raise KnowledgeApiError(
                HTTPStatus.BAD_REQUEST,
                "REQUEST_INVALID",
                "entity is required and must be at most 500 characters.",
            )

        def string_array(key: str, maximum: int = 20) -> tuple[str, ...]:
            raw = body.get(key, [])
            if not isinstance(raw, list) or len(raw) > maximum:
                raise KnowledgeApiError(
                    HTTPStatus.BAD_REQUEST,
                    "REQUEST_INVALID",
                    f"{key} must be an array with at most {maximum} items.",
                )
            values = tuple(str(item).strip() for item in raw)
            if any(not value or len(value) > 160 for value in values):
                raise KnowledgeApiError(
                    HTTPStatus.BAD_REQUEST,
                    "REQUEST_INVALID",
                    f"{key} contains an invalid value.",
                )
            return values

        evidence_limit = _bounded_int(
            body.get("evidenceLimit"),
            name="evidenceLimit",
            default=50,
            minimum=1,
            maximum=100,
        )
        budget = _bounded_int(
            body.get("budgetTokens"),
            name="budgetTokens",
            default=2_000,
            minimum=300,
            maximum=2_000,
        )
        raw_answer_mode = body.get("answerMode")
        answer_mode: str | None
        if raw_answer_mode is None:
            answer_mode = None
        elif (
            not isinstance(raw_answer_mode, str)
            or raw_answer_mode not in ANSWER_MODES
        ):
            raise KnowledgeApiError(
                HTTPStatus.BAD_REQUEST,
                "REQUEST_INVALID",
                "answerMode must be one of "
                + ", ".join(sorted(ANSWER_MODES))
                + ".",
            )
        else:
            answer_mode = raw_answer_mode
        request = QueryRequirements(
            entity_query=entity_query,
            fact_types=string_array("factTypes"),
            fact_names=string_array("factNames"),
            edge_types=string_array("edgeTypes"),
            requires_native=bool(body.get("requiresNative", False)),
            requires_runtime=bool(body.get("requiresRuntime", False)),
            requires_map_evidence=bool(
                body.get("requiresMapEvidence", False)
            ),
            evidence_limit=evidence_limit,
            answer_mode=answer_mode,
        )
        cached_response, cache_reason = self._read_cached_query(body)
        if cached_response is not None:
            cached_response["cache"] = self._cache_outcome(
                hit=True,
                reason=cache_reason,
            )
            return cached_response
        with closing(self._core()) as core, closing(self._search()) as search:
            capabilities = core_schema_capabilities(core)
            try:
                result = plan_query(
                    core,
                    request,
                    search_connection=search,
                )
            except sqlite3.DatabaseError:
                if capabilities["compatible"]:
                    raise
                inferred_mode = answer_mode or (
                    "FACT"
                    if request.fact_types
                    else (
                        "RELATIONSHIP"
                        if request.edge_types
                        else (
                            "MECHANISM"
                            if (
                                request.requires_native
                                or request.requires_runtime
                                or request.requires_map_evidence
                            )
                            else None
                        )
                    )
                )
                result = {
                    "answerMode": inferred_mode,
                    "status": "GAP",
                    "route": "EVIDENCE_REQUIRED",
                    "entity": None,
                    "entityCandidates": [],
                    "facts": [],
                    "relationships": [],
                    "evidence": [],
                    "returned": 0,
                    "omitted": 0,
                    "freshness": "UNKNOWN",
                    "missingRequirements": [],
                    "recommendedProbes": [],
                }
            if not capabilities["compatible"]:
                migration_gap = {
                    "code": "SCHEMA_MIGRATION_REQUIRED",
                    "requirement": "ark-kb-core/v4 query provenance",
                }
                missing = [
                    item
                    for item in result.get("missingRequirements", [])
                    if isinstance(item, dict)
                ]
                if migration_gap not in missing:
                    missing.append(migration_gap)
                probes = [
                    item
                    for item in result.get("recommendedProbes", [])
                    if isinstance(item, dict)
                ]
                if not any(
                    item.get("operation") == "rebuild_core_v4_snapshot"
                    for item in probes
                ):
                    probes.append(
                        {
                            "probeType": "schema_migration",
                            "operation": "rebuild_core_v4_snapshot",
                            "budgetTokens": 300,
                        }
                    )
                has_partial = bool(
                    result.get("facts") or result.get("relationships")
                )
                result.update(
                    {
                        "status": "PARTIAL" if has_partial else "GAP",
                        "route": "EVIDENCE_REQUIRED",
                        "freshness": (
                            "STALE"
                            if result.get("freshness") == "STALE"
                            else "UNKNOWN"
                        ),
                        "missingRequirements": missing,
                        "recommendedProbes": probes,
                    }
                )
        pack = build_bounded_context_pack(
            result, budget_tokens=budget
        )
        response = {
            **result,
            "contextPack": pack,
            "nextQuery": "",
            "gap": result["missingRequirements"],
        }
        self._cache_query(body, response)
        response["cache"] = self._cache_outcome(
            hit=False,
            reason=cache_reason,
        )
        return response

    def _cache_query(
        self,
        request: Mapping[str, object],
        response: Mapping[str, object],
    ) -> None:
        if not self.cache_path.is_file():
            return
        request_json, fingerprint = self._cache_request_identity(request)
        cacheable_response = dict(response)
        cacheable_response.pop("cache", None)
        response_json = json.dumps(
            cacheable_response,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        revision_ids = self._cached_revision_ids(cacheable_response)
        try:
            with closing(self._core()) as core:
                revision_hash = self._source_revision_set_hash(
                    core,
                    revision_ids,
                )
                if revision_hash is None:
                    return
                raw_manifest = json.loads(
                    self.manifest_path.read_text(encoding="utf-8")
                )
                if not isinstance(raw_manifest, Mapping):
                    return
                build_id = str(raw_manifest.get("buildId") or "")
                if not build_id:
                    return
                invalidation_token = self._current_invalidation_token(
                    build_id=build_id,
                    revision_hash=revision_hash,
                )
        except (
            KnowledgeApiError,
            OSError,
            json.JSONDecodeError,
            sqlite3.DatabaseError,
        ):
            return
        snapshot_id = "query-snapshot://" + fingerprint
        context_pack_id = "context-pack://" + fingerprint
        now = datetime.now(UTC)
        created_at = now.isoformat(timespec="seconds")
        expires_at = (now + timedelta(hours=1)).isoformat(
            timespec="seconds"
        )
        try:
            cache = sqlite3.connect(self.cache_path)
        except sqlite3.DatabaseError:
            return
        try:
            if self._cache_snapshot_identity(cache) is None:
                return
            cache.execute(
                """
                INSERT OR REPLACE INTO query_snapshots(
                    snapshot_id, query_fingerprint, request_json,
                    response_json, source_revision_set_hash,
                    invalidation_token, created_at, expires_at, status
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, 'VALID'
                )
                """,
                (
                    snapshot_id,
                    fingerprint,
                    request_json,
                    response_json,
                    revision_hash,
                    invalidation_token,
                    created_at,
                    expires_at,
                ),
            )
            pack = cacheable_response["contextPack"]
            cache.execute(
                """
                INSERT OR REPLACE INTO context_packs VALUES (
                    ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    context_pack_id,
                    snapshot_id,
                    str(pack["content"]),
                    int(pack["estimatedTokens"]),
                    int(pack["returned"]),
                    int(pack["omitted"]),
                    created_at,
                ),
            )
            cache.commit()
        except sqlite3.DatabaseError:
            cache.rollback()
        finally:
            cache.close()
