"""Path-free, bounded GET routes for Blueprint Interpretation Contract v1."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, unquote_to_bytes

from blueprint_translator.evidence_publication import (
    _lexical_absolute,
    _require_plain_directory,
    _require_plain_path_chain,
)

from .request import ApiProblem, problem


ASSET_LIST_SCHEMA = "blueprint-to-code.blueprint-asset-list-response/v1"
EVIDENCE_HEALTH_SCHEMA = "blueprint-to-code.blueprint-evidence-health-response/v1"
INTERPRETATION_RESPONSE_SCHEMA = (
    "blueprint-to-code.blueprint-interpretation-response/v1"
)
STATEMENT_RESPONSE_SCHEMA = "blueprint-to-code.blueprint-statement-response/v1"
TRACE_RESPONSE_SCHEMA = "blueprint-to-code.blueprint-trace-response/v1"
GAPS_RESPONSE_SCHEMA = "blueprint-to-code.blueprint-gaps-response/v1"

DEFAULT_PAGE_LIMIT = 25
MAX_PAGE_LIMIT = 100
MAX_QUERY_FIELDS = 32
MAX_CURSOR_CHARACTERS = 4096
MAX_IDENTIFIER_CHARACTERS = 1024
MAX_HINT_PREVIEW = 20

_WINDOWS_ABSOLUTE = re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:[\\/]")
_UNC_PATH = re.compile(r"(?<![A-Za-z0-9_])\\\\[^\\\s]+[\\/]")
_POSIX_LOCAL_PATH = re.compile(
    r"(?<![:/<A-Za-z0-9_])/(?!Game/|Script/|Engine/|Plugin/|Plugins/)[^\s]+"
)
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_STATEMENT_KINDS = frozenset(
    {"EVENT", "CALL", "BRANCH", "SET", "RETURN", "DELEGATE", "LOOP", "GAP"}
)
_STATEMENT_STATUSES = frozenset(
    {
        "CONFIRMED",
        "HEURISTIC",
        "SOURCE_NOT_AVAILABLE",
        "NOT_RECOVERED",
        "AMBIGUOUS",
    }
)
_GAP_STATUSES = frozenset(
    {"SOURCE_NOT_AVAILABLE", "NOT_RECOVERED", "AMBIGUOUS"}
)
_GAP_CODE = re.compile(r"[A-Z][A-Z0-9_]*")


@dataclass(frozen=True, slots=True)
class BlueprintRouteResult:
    status: HTTPStatus
    payload: dict[str, object]


def _default_load_current(asset_dir: Path) -> object:
    from blueprint_translator.interpretation_publication import (
        load_current_interpretation,
    )

    return load_current_interpretation(asset_dir)


def _default_inspect_health(asset_dir: Path) -> dict[str, object]:
    from blueprint_translator.interpretation_publication import (
        inspect_interpretation_health,
    )

    return inspect_interpretation_health(asset_dir)


def _strict_unquote(value: str, *, label: str) -> str:
    if _PERCENT_ESCAPE.search(value):
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_QUERY_INVALID",
            f"{label} contains an invalid percent escape.",
        )
    try:
        decoded = unquote_to_bytes(value).decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_QUERY_INVALID",
            f"{label} must be valid UTF-8.",
        ) from exc
    if not decoded or len(decoded) > MAX_IDENTIFIER_CHARACTERS or _CONTROL_CHARACTER.search(decoded):
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_QUERY_INVALID",
            f"{label} is invalid.",
        )
    return decoded


def _asset_identifier(raw_value: str) -> str:
    try:
        value = _strict_unquote(raw_value, label="Asset identifier")
    except ApiProblem as exc:
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_ASSET_ID_INVALID",
            "Asset identifier must be one capture directory name.",
        ) from exc
    candidate = Path(value)
    if (
        len(value) > 255
        or candidate.is_absolute()
        or candidate.name != value
        or value in {".", ".."}
        or any(marker in value for marker in ("/", "\\", ":"))
    ):
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_ASSET_ID_INVALID",
            "Asset identifier must be one capture directory name.",
        )
    return value


def _capture_root(value: str | os.PathLike[str]) -> Path:
    root = _lexical_absolute(value)
    try:
        _require_plain_path_chain(root, label="capture root")
        _require_plain_directory(root, label="capture root")
    except ValueError as exc:
        raise problem(
            HTTPStatus.CONFLICT,
            "BLUEPRINT_CAPTURE_ROOT_INVALID",
            "The Blueprint capture root is unavailable.",
        ) from exc
    return root


def _resolve_asset(capture_root: str | os.PathLike[str], identifier: str) -> Path:
    root = _capture_root(capture_root)
    asset_dir = _lexical_absolute(root / identifier)
    try:
        common = os.path.commonpath((os.fspath(root), os.fspath(asset_dir)))
    except ValueError as exc:
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_ASSET_ID_INVALID",
            "Asset identifier must stay inside the capture root.",
        ) from exc
    if os.path.normcase(common) != os.path.normcase(os.fspath(root)):
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_ASSET_ID_INVALID",
            "Asset identifier must stay inside the capture root.",
        )
    if not asset_dir.exists():
        raise problem(
            HTTPStatus.NOT_FOUND,
            "BLUEPRINT_ASSET_NOT_FOUND",
            "Blueprint asset was not found.",
        )
    try:
        _require_plain_path_chain(asset_dir, label="asset directory")
        _require_plain_directory(asset_dir, label="asset directory")
    except ValueError as exc:
        raise problem(
            HTTPStatus.CONFLICT,
            "BLUEPRINT_ASSET_INVALID",
            "Blueprint asset directory is not safe to read.",
        ) from exc
    return asset_dir


def _query_values(query: str, *, allowed: set[str]) -> dict[str, list[str]]:
    try:
        values = parse_qs(
            query,
            keep_blank_values=True,
            strict_parsing=False,
            max_num_fields=MAX_QUERY_FIELDS,
        )
    except ValueError as exc:
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_QUERY_INVALID",
            "Blueprint query parameters are invalid.",
        ) from exc
    unknown = set(values) - allowed
    if unknown:
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_QUERY_INVALID",
            "Blueprint query contains unsupported parameters.",
        )
    return {str(key): [str(item) for item in raw] for key, raw in values.items()}


def _single(values: Mapping[str, Sequence[str]], name: str, default: str = "") -> str:
    raw = list(values.get(name, ()))
    if len(raw) > 1:
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_QUERY_INVALID",
            f"{name} must be provided at most once.",
        )
    return raw[0] if raw else default


def _page_limit(values: Mapping[str, Sequence[str]]) -> int:
    raw = _single(values, "limit", str(DEFAULT_PAGE_LIMIT)).strip()
    try:
        limit = int(raw, 10)
    except ValueError as exc:
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_QUERY_INVALID",
            "limit must be an integer.",
        ) from exc
    if limit < 1 or limit > MAX_PAGE_LIMIT:
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_QUERY_INVALID",
            f"limit must be between 1 and {MAX_PAGE_LIMIT}.",
        )
    return limit


def _cursor_encode(payload: Mapping[str, object]) -> str:
    raw = json.dumps(
        dict(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _cursor_decode(raw_value: str) -> dict[str, object]:
    value = str(raw_value or "").strip()
    if not value or len(value) > MAX_CURSOR_CHARACTERS:
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_CURSOR_INVALID",
            "Blueprint cursor is invalid.",
        )
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        if len(raw) > MAX_CURSOR_CHARACTERS:
            raise ValueError("decoded cursor is too large")
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_CURSOR_INVALID",
            "Blueprint cursor is invalid.",
        ) from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_CURSOR_INVALID",
            "Blueprint cursor is invalid.",
        )
    return payload


def _digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _member(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _mapping(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise problem(
            HTTPStatus.CONFLICT,
            "BLUEPRINT_INTERPRETATION_INVALID",
            f"Current interpretation {label} is invalid.",
        )
    return {str(key): item for key, item in value.items()}


def _collection(value: object, *keys: str) -> list[dict[str, object]]:
    candidate = value
    if isinstance(candidate, Mapping):
        for key in ("items", *keys):
            if key in candidate:
                candidate = candidate[key]
                break
    if candidate is None:
        return []
    if not isinstance(candidate, Sequence) or isinstance(candidate, (str, bytes, bytearray)):
        raise problem(
            HTTPStatus.CONFLICT,
            "BLUEPRINT_INTERPRETATION_INVALID",
            "Current interpretation collection is invalid.",
        )
    result: list[dict[str, object]] = []
    for item in candidate:
        if not isinstance(item, Mapping):
            raise problem(
                HTTPStatus.CONFLICT,
                "BLUEPRINT_INTERPRETATION_INVALID",
                "Current interpretation collection contains an invalid item.",
            )
        result.append({str(key): child for key, child in item.items()})
    return result


def _path_free(value: object) -> None:
    if isinstance(value, Path):
        raise problem(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "BLUEPRINT_RESPONSE_INVALID",
            "Blueprint response contains a non-public value.",
        )
    if isinstance(value, str):
        if (
            _WINDOWS_ABSOLUTE.search(value)
            or _UNC_PATH.search(value)
            or _POSIX_LOCAL_PATH.search(value)
        ):
            raise problem(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "BLUEPRINT_RESPONSE_INVALID",
                "Blueprint response contains a non-public value.",
            )
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _path_free(str(key))
            _path_free(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _path_free(item)


def _core_error(exc: Exception) -> ApiProblem:
    raw_code = str(getattr(exc, "code", "") or "").strip().upper()
    if not raw_code:
        text = str(exc).strip()
        raw_code = text.split(":", 1)[0].strip().upper() if text else ""
    if "STALE_SOURCE" in raw_code or raw_code == "EVIDENCE_STALE":
        return problem(
            HTTPStatus.CONFLICT,
            "BLUEPRINT_EVIDENCE_STALE",
            "Blueprint evidence sources changed after publication.",
        )
    if raw_code in {"NO_EVIDENCE", "EVIDENCE_CURRENT_POINTER_MISSING"}:
        return problem(
            HTTPStatus.NOT_FOUND,
            "BLUEPRINT_EVIDENCE_NOT_FOUND",
            "Current Blueprint evidence was not found.",
        )
    if "INTERPRETATION" in raw_code and "MISSING" in raw_code:
        return problem(
            HTTPStatus.NOT_FOUND,
            "BLUEPRINT_INTERPRETATION_NOT_FOUND",
            "Current Blueprint interpretation was not found.",
        )
    if (
        "NOT_AUTHORITY" in raw_code
        or "NOT_AUTHORITATIVE" in raw_code
        or raw_code == "EVIDENCE_V3_REQUIRED"
    ):
        return problem(
            HTTPStatus.CONFLICT,
            "BLUEPRINT_EVIDENCE_NOT_AUTHORITATIVE",
            "Current Blueprint evidence is not release authority.",
        )
    if any(
        marker in raw_code
        for marker in (
            "STALE_EVIDENCE",
            "EVIDENCE_MISMATCH",
            "EVIDENCE_REVISION",
            "EVIDENCE_MANIFEST",
        )
    ):
        return problem(
            HTTPStatus.CONFLICT,
            "BLUEPRINT_INTERPRETATION_STALE",
            "Current interpretation does not match current Blueprint evidence.",
        )
    if isinstance(exc, FileNotFoundError):
        return problem(
            HTTPStatus.NOT_FOUND,
            "BLUEPRINT_INTERPRETATION_NOT_FOUND",
            "Current Blueprint interpretation was not found.",
        )
    return problem(
        HTTPStatus.CONFLICT,
        "BLUEPRINT_INTERPRETATION_INVALID",
        "Current Blueprint interpretation is invalid.",
    )


def _load_current(asset_dir: Path, loader: Callable[[Path], object]) -> object:
    try:
        return loader(asset_dir)
    except ApiProblem:
        raise
    except Exception as exc:
        raise _core_error(exc) from exc


def _identity(asset_name: str, state: object) -> dict[str, object]:
    interpretation = _mapping(_member(state, "interpretation"), label="payload")
    manifest = _mapping(_member(state, "manifest", {}), label="manifest")
    revision_id = str(
        _member(state, "revision_id", "") or manifest.get("revisionId") or ""
    )
    identity = {
        "asset": {
            "name": asset_name,
            "assetId": str(interpretation.get("assetId") or manifest.get("assetId") or ""),
            "objectPath": str(
                interpretation.get("objectPath") or manifest.get("objectPath") or ""
            ),
        },
        "evidence": {
            "revisionId": str(
                interpretation.get("evidenceRevisionId")
                or manifest.get("evidenceRevisionId")
                or ""
            ),
            "manifestSha256": str(
                interpretation.get("evidenceManifestSha256")
                or manifest.get("evidenceManifestSha256")
                or ""
            ),
        },
        "interpretation": {
            "revisionId": revision_id,
            "manifestSha256": str(_member(state, "manifest_sha256", "") or ""),
            "pointerSha256": str(_member(state, "pointer_sha256", "") or ""),
            "semanticDigest": str(interpretation.get("semanticDigest") or ""),
            "interpreterVersion": str(interpretation.get("interpreterVersion") or ""),
            "schemaVersion": str(
                interpretation.get("schemaVersion") or interpretation.get("schema") or ""
            ),
            "generatedAt": str(interpretation.get("generatedAt") or ""),
        },
    }
    _path_free(identity)
    return identity


def _bounded_text(value: object, *, maximum: int) -> str:
    text = str(value or "")
    return text if len(text) <= maximum else text[: maximum - 1] + "…"


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _string_list(value: object, *, maximum: int = 4096) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_bounded_text(item, maximum=MAX_IDENTIFIER_CHARACTERS) for item in value[:maximum]]


def _public_health(value: Mapping[str, object], *, asset_name: str) -> dict[str, object]:
    asset = value.get("asset") if isinstance(value.get("asset"), Mapping) else {}
    evidence = (
        value.get("evidence") if isinstance(value.get("evidence"), Mapping) else {}
    )
    interpretation = (
        value.get("interpretation")
        if isinstance(value.get("interpretation"), Mapping)
        else {}
    )
    return {
        "status": _bounded_text(value.get("status"), maximum=64) or "UNKNOWN",
        "reasonCode": _bounded_text(value.get("reasonCode"), maximum=128),
        "asset": {
            "name": _bounded_text(asset.get("name") or asset_name, maximum=255),
            "assetId": _bounded_text(asset.get("assetId"), maximum=24),
            "objectPath": _bounded_text(asset.get("objectPath"), maximum=4096),
        },
        "evidence": {
            "revisionId": _bounded_text(evidence.get("revisionId"), maximum=24),
            "manifestSha256": _bounded_text(
                evidence.get("manifestSha256"), maximum=64
            ),
            "pointerSha256": _bounded_text(
                evidence.get("pointerSha256"), maximum=64
            ),
            "freshnessStatus": _bounded_text(
                evidence.get("freshnessStatus"), maximum=64
            ),
            "releaseAuthority": bool(evidence.get("releaseAuthority", False)),
            "migrationRequired": bool(evidence.get("migrationRequired", False)),
        },
        "interpretation": {
            "status": _bounded_text(interpretation.get("status"), maximum=64),
            "revisionId": _bounded_text(
                interpretation.get("revisionId"), maximum=24
            ),
            "manifestSha256": _bounded_text(
                interpretation.get("manifestSha256"), maximum=64
            ),
            "pointerSha256": _bounded_text(
                interpretation.get("pointerSha256"), maximum=64
            ),
            "semanticDigest": _bounded_text(
                interpretation.get("semanticDigest"), maximum=64
            ),
            "interpreterVersion": _bounded_text(
                interpretation.get("interpreterVersion"), maximum=128
            ),
            "schemaVersion": _bounded_text(
                interpretation.get("schemaVersion"), maximum=128
            ),
            "generatedAt": _bounded_text(
                interpretation.get("generatedAt"), maximum=128
            ),
        },
    }


def _statement_projection(item: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": _bounded_text(item.get("id"), maximum=256),
        "kind": _bounded_text(item.get("kind"), maximum=32),
        "text": _bounded_text(item.get("text"), maximum=32_768),
        "status": _bounded_text(item.get("status"), maximum=64),
        "evidenceRefs": _string_list(item.get("evidenceRefs")),
        "gapRefs": _string_list(item.get("gapRefs")),
        "graphRef": _bounded_text(item.get("graphRef"), maximum=MAX_IDENTIFIER_CHARACTERS),
        "nodeRef": _bounded_text(item.get("nodeRef"), maximum=MAX_IDENTIFIER_CHARACTERS),
        "sourceOrder": _nonnegative_int(item.get("sourceOrder")),
    }


def _gap_projection(item: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": _bounded_text(item.get("id"), maximum=256),
        "code": _bounded_text(item.get("code"), maximum=128),
        "status": _bounded_text(item.get("status"), maximum=64),
        "graphRef": _bounded_text(item.get("graphRef"), maximum=MAX_IDENTIFIER_CHARACTERS),
        "nodeRef": _bounded_text(item.get("nodeRef"), maximum=MAX_IDENTIFIER_CHARACTERS),
        "pinRef": _bounded_text(item.get("pinRef"), maximum=MAX_IDENTIFIER_CHARACTERS),
        "detail": _bounded_text(item.get("detail"), maximum=32_768),
        "evidenceRefs": _string_list(item.get("evidenceRefs")),
        "source": _bounded_text(item.get("source"), maximum=128),
    }


def _cursor_binding(identity: Mapping[str, object]) -> dict[str, str]:
    evidence = _mapping(identity.get("evidence"), label="identity evidence")
    interpretation = _mapping(
        identity.get("interpretation"), label="identity interpretation"
    )
    return {
        "evidenceRevisionId": str(evidence.get("revisionId") or ""),
        "evidenceManifestSha256": str(evidence.get("manifestSha256") or ""),
        "interpretationRevisionId": str(interpretation.get("revisionId") or ""),
        "interpretationManifestSha256": str(
            interpretation.get("manifestSha256") or ""
        ),
    }


def _paginate(
    items: Sequence[dict[str, object]],
    *,
    endpoint: str,
    filters: Mapping[str, object],
    identity: Mapping[str, object],
    limit: int,
    raw_cursor: str,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    binding = _cursor_binding(identity)
    query_digest = _digest(filters)
    offset = 0
    if raw_cursor:
        cursor = _cursor_decode(raw_cursor)
        if any(str(cursor.get(key) or "") != value for key, value in binding.items()):
            raise problem(
                HTTPStatus.CONFLICT,
                "BLUEPRINT_CURSOR_STALE",
                "Blueprint cursor belongs to another published revision.",
            )
        if cursor.get("endpoint") != endpoint or cursor.get("query") != query_digest:
            raise problem(
                HTTPStatus.BAD_REQUEST,
                "BLUEPRINT_CURSOR_QUERY_MISMATCH",
                "Blueprint cursor belongs to another query.",
            )
        raw_offset = cursor.get("offset")
        if not isinstance(raw_offset, int) or isinstance(raw_offset, bool) or raw_offset < 0:
            raise problem(
                HTTPStatus.BAD_REQUEST,
                "BLUEPRINT_CURSOR_INVALID",
                "Blueprint cursor is invalid.",
            )
        offset = raw_offset
        if offset > len(items):
            raise problem(
                HTTPStatus.CONFLICT,
                "BLUEPRINT_CURSOR_STALE",
                "Blueprint cursor no longer identifies this collection.",
            )
    page_items = list(items[offset : offset + limit])
    next_offset = offset + len(page_items)
    next_cursor = None
    if page_items and next_offset < len(items):
        next_cursor = _cursor_encode(
            {
                "v": 1,
                "endpoint": endpoint,
                "query": query_digest,
                "offset": next_offset,
                **binding,
            }
        )
    return page_items, {
        "limit": limit,
        "returned": len(page_items),
        "total": len(items),
        "nextCursor": next_cursor,
    }


def _asset_names(capture_root: str | os.PathLike[str]) -> list[str]:
    lexical = _lexical_absolute(capture_root)
    if not lexical.exists():
        return []
    root = _capture_root(lexical)
    names: list[str] = []
    for candidate in root.iterdir():
        if candidate.name.startswith("_"):
            continue
        try:
            _require_plain_path_chain(candidate, label="asset directory")
            _require_plain_directory(candidate, label="asset directory")
        except ValueError:
            continue
        names.append(candidate.name)
    return sorted(names, key=lambda value: (value.casefold(), value))


def _asset_list(
    query: str,
    *,
    capture_root: str | os.PathLike[str],
    inspect_health: Callable[[Path], dict[str, object]],
) -> BlueprintRouteResult:
    values = _query_values(query, allowed={"q", "limit", "cursor"})
    needle = _single(values, "q").strip().casefold()
    if len(needle) > 128 or _CONTROL_CHARACTER.search(needle):
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_QUERY_INVALID",
            "q is invalid.",
        )
    limit = _page_limit(values)
    raw_cursor = _single(values, "cursor")
    names = [name for name in _asset_names(capture_root) if needle in name.casefold()]
    query_digest = _digest({"q": needle})
    collection_digest = _digest(names)
    start = 0
    if raw_cursor:
        cursor = _cursor_decode(raw_cursor)
        if cursor.get("endpoint") != "assets" or cursor.get("query") != query_digest:
            raise problem(
                HTTPStatus.BAD_REQUEST,
                "BLUEPRINT_CURSOR_QUERY_MISMATCH",
                "Blueprint cursor belongs to another query.",
            )
        if cursor.get("collection") != collection_digest:
            raise problem(
                HTTPStatus.CONFLICT,
                "BLUEPRINT_CURSOR_STALE",
                "Blueprint asset collection changed after this cursor was issued.",
            )
        last = str(cursor.get("last") or "")
        if last not in names:
            raise problem(
                HTTPStatus.CONFLICT,
                "BLUEPRINT_CURSOR_STALE",
                "Blueprint asset cursor no longer identifies this collection.",
            )
        start = names.index(last) + 1
    selected_names = names[start : start + limit]
    items: list[dict[str, object]] = []
    root = _capture_root(capture_root) if selected_names else _lexical_absolute(capture_root)
    for name in selected_names:
        try:
            health = inspect_health(root / name)
            if not isinstance(health, Mapping):
                raise TypeError("health result must be an object")
            public_health = _public_health(health, asset_name=name)
            _path_free(public_health)
        except Exception:
            public_health = _public_health(
                {
                    "status": "INVALID",
                    "reasonCode": "BLUEPRINT_HEALTH_UNAVAILABLE",
                },
                asset_name=name,
            )
        items.append({"asset": name, "health": public_health})
    next_cursor = None
    if selected_names and start + len(selected_names) < len(names):
        next_cursor = _cursor_encode(
            {
                "v": 1,
                "endpoint": "assets",
                "query": query_digest,
                "collection": collection_digest,
                "last": selected_names[-1],
            }
        )
    payload: dict[str, object] = {
        "ok": True,
        "schema": ASSET_LIST_SCHEMA,
        "items": items,
        "page": {
            "limit": limit,
            "returned": len(items),
            "total": len(names),
            "nextCursor": next_cursor,
        },
    }
    _path_free(payload)
    return BlueprintRouteResult(HTTPStatus.OK, payload)


def _health(
    asset_name: str,
    asset_dir: Path,
    *,
    inspect_health: Callable[[Path], dict[str, object]],
) -> BlueprintRouteResult:
    try:
        health = inspect_health(asset_dir)
    except ApiProblem:
        raise
    except Exception as exc:
        raise _core_error(exc) from exc
    if not isinstance(health, Mapping):
        raise problem(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "BLUEPRINT_RESPONSE_INVALID",
            "Blueprint health response is invalid.",
        )
    payload: dict[str, object] = {
        "ok": True,
        "schema": EVIDENCE_HEALTH_SCHEMA,
        "asset": asset_name,
        "health": _public_health(health, asset_name=asset_name),
    }
    _path_free(payload)
    return BlueprintRouteResult(HTTPStatus.OK, payload)


def _statements(state: object) -> list[dict[str, object]]:
    interpretation = _mapping(_member(state, "interpretation"), label="payload")
    items = [
        _statement_projection(item)
        for item in _collection(interpretation.get("statements"), "statements")
    ]
    return sorted(
        items,
        key=lambda item: (
            str(item.get("graphRef") or ""),
            int(item.get("sourceOrder") or 0),
            str(item.get("id") or ""),
        ),
    )


def _interpretation_summary(
    interpretation: Mapping[str, object],
    statements: Sequence[Mapping[str, object]],
    hints: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    raw = (
        interpretation.get("assetSummary")
        if isinstance(interpretation.get("assetSummary"), Mapping)
        else {}
    )
    raw_statuses = (
        raw.get("graphStatusCounts")
        if isinstance(raw.get("graphStatusCounts"), Mapping)
        else {}
    )
    graph_statuses = {
        _bounded_text(key, maximum=64): _nonnegative_int(value)
        for key, value in sorted(raw_statuses.items(), key=lambda row: str(row[0]))[:32]
    }
    return {
        "assetName": _bounded_text(raw.get("assetName"), maximum=255),
        "graphCount": _nonnegative_int(raw.get("graphCount")),
        "nodeCount": _nonnegative_int(raw.get("nodeCount")),
        "pinCount": _nonnegative_int(raw.get("pinCount")),
        "edgeCount": _nonnegative_int(raw.get("edgeCount")),
        "diagnosticGapCount": _nonnegative_int(raw.get("diagnosticGapCount")),
        "statementCount": len(statements),
        "confirmedStatementCount": sum(
            statement.get("status") == "CONFIRMED" for statement in statements
        ),
        "heuristicReviewHintCount": len(hints),
        "heuristicReviewHintsTruncated": len(hints) > MAX_HINT_PREVIEW,
        "graphStatusCounts": graph_statuses,
    }


def _hint_projection(item: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": _bounded_text(item.get("id"), maximum=256),
        "topic": _bounded_text(item.get("topic"), maximum=128),
        "text": _bounded_text(item.get("text"), maximum=4096),
        "basis": _bounded_text(item.get("basis"), maximum=128),
        "confidence": _bounded_text(item.get("confidence"), maximum=64),
        "notEvidence": bool(item.get("notEvidence", False)),
        "reviewRef": _bounded_text(
            item.get("reviewRef"), maximum=MAX_IDENTIFIER_CHARACTERS
        ),
    }


def _interpretation(
    asset_name: str,
    asset_dir: Path,
    query: str,
    *,
    load_current: Callable[[Path], object],
) -> BlueprintRouteResult:
    values = _query_values(
        query,
        allowed={"status", "kind", "graphRef", "limit", "cursor"},
    )
    statuses = tuple(dict.fromkeys(value.strip().upper() for value in values.get("status", ()) if value.strip()))
    kinds = tuple(dict.fromkeys(value.strip().upper() for value in values.get("kind", ()) if value.strip()))
    if any(value not in _STATEMENT_STATUSES for value in statuses) or any(
        value not in _STATEMENT_KINDS for value in kinds
    ):
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_QUERY_INVALID",
            "Statement status or kind filter is invalid.",
        )
    graph_ref = _single(values, "graphRef").strip()
    if graph_ref and (len(graph_ref) > MAX_IDENTIFIER_CHARACTERS or not graph_ref.startswith("bp://")):
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_QUERY_INVALID",
            "graphRef must be an exact bp:// reference.",
        )
    state = _load_current(asset_dir, load_current)
    identity = _identity(asset_name, state)
    interpretation = _mapping(_member(state, "interpretation"), label="payload")
    all_statements = _statements(state)
    all_hints = [
        _hint_projection(item)
        for item in _collection(
            interpretation.get("heuristicReviewHints"), "heuristicReviewHints"
        )
    ]
    items = [
        item
        for item in all_statements
        if (not statuses or str(item.get("status") or "").upper() in statuses)
        and (not kinds or str(item.get("kind") or "").upper() in kinds)
        and (not graph_ref or str(item.get("graphRef") or "") == graph_ref)
    ]
    filters = {"status": statuses, "kind": kinds, "graphRef": graph_ref}
    page_items, page = _paginate(
        items,
        endpoint="interpretation",
        filters=filters,
        identity=identity,
        limit=_page_limit(values),
        raw_cursor=_single(values, "cursor"),
    )
    payload: dict[str, object] = {
        "ok": True,
        "schema": INTERPRETATION_RESPONSE_SCHEMA,
        "identity": identity,
        "summary": _interpretation_summary(
            interpretation, all_statements, all_hints
        ),
        "heuristicReviewHints": all_hints[:MAX_HINT_PREVIEW],
        "filters": {
            "statuses": list(statuses),
            "kinds": list(kinds),
            "graphRef": graph_ref,
        },
        "items": page_items,
        "page": page,
    }
    _path_free(payload)
    return BlueprintRouteResult(HTTPStatus.OK, payload)


def _trace_statement_projection(item: Mapping[str, object]) -> dict[str, object]:
    return {
        "traceKind": "STATEMENT",
        "statementId": _bounded_text(item.get("statementId"), maximum=256),
        "graphRef": _bounded_text(item.get("graphRef"), maximum=MAX_IDENTIFIER_CHARACTERS),
        "nodeRef": _bounded_text(item.get("nodeRef"), maximum=MAX_IDENTIFIER_CHARACTERS),
        "evidenceRefs": _string_list(item.get("evidenceRefs")),
        "gapRefs": _string_list(item.get("gapRefs")),
    }


def _trace_line_projection(item: Mapping[str, object]) -> dict[str, object]:
    line = _nonnegative_int(item.get("line") or item.get("pseudocodeLine"))
    return {
        "traceKind": "PSEUDOCODE_LINE",
        "statementId": _bounded_text(item.get("statementId"), maximum=256),
        "evidenceRefs": _string_list(item.get("evidenceRefs")),
        "pseudocodeLine": line,
        "line": line,
        "startByte": _nonnegative_int(item.get("startByte")),
        "endByte": _nonnegative_int(item.get("endByte")),
        "executable": bool(item.get("executable", bool(item.get("statementId")))),
    }


def _trace_items(state: object) -> list[dict[str, object]]:
    trace = _member(state, "trace", [])
    if isinstance(trace, Mapping):
        statement_rows = [
            _trace_statement_projection(row)
            for row in _collection(trace.get("statements", []), "statements")
        ]
        line_rows = [
            _trace_line_projection(row)
            for row in _collection(
                trace.get("pseudocodeLines", []), "pseudocodeLines"
            )
        ]
        rows = [*statement_rows, *line_rows]
    else:
        rows = [
            (
                _trace_line_projection(row)
                if row.get("pseudocodeLine") is not None or row.get("line") is not None
                else _trace_statement_projection(row)
            )
            for row in _collection(trace, "pseudocodeLines", "trace", "entries")
        ]
    return sorted(
        rows,
        key=lambda item: (
            str(item.get("statementId") or ""),
            int(item.get("pseudocodeLine") or item.get("line") or 0),
            str(item.get("traceKind") or ""),
            _digest(item),
        ),
    )


def _statement(
    asset_name: str,
    asset_dir: Path,
    raw_statement_id: str,
    query: str,
    *,
    load_current: Callable[[Path], object],
) -> BlueprintRouteResult:
    values = _query_values(query, allowed={"limit", "cursor"})
    statement_id = _strict_unquote(raw_statement_id, label="Statement id")
    if not statement_id.startswith("statement://"):
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_QUERY_INVALID",
            "Statement id must use the statement:// scheme.",
        )
    state = _load_current(asset_dir, load_current)
    matches = [item for item in _statements(state) if item.get("id") == statement_id]
    if not matches:
        raise problem(
            HTTPStatus.NOT_FOUND,
            "BLUEPRINT_STATEMENT_NOT_FOUND",
            "Blueprint statement was not found.",
        )
    if len(matches) != 1:
        raise problem(
            HTTPStatus.CONFLICT,
            "BLUEPRINT_INTERPRETATION_INVALID",
            "Current interpretation contains duplicate statement identities.",
        )
    identity = _identity(asset_name, state)
    traces = [item for item in _trace_items(state) if item.get("statementId") == statement_id]
    page_items, page = _paginate(
        traces,
        endpoint="statement",
        filters={"statementId": statement_id},
        identity=identity,
        limit=_page_limit(values),
        raw_cursor=_single(values, "cursor"),
    )
    payload: dict[str, object] = {
        "ok": True,
        "schema": STATEMENT_RESPONSE_SCHEMA,
        "identity": identity,
        "statement": matches[0],
        "items": page_items,
        "page": page,
    }
    _path_free(payload)
    return BlueprintRouteResult(HTTPStatus.OK, payload)


def _trace(
    asset_name: str,
    asset_dir: Path,
    query: str,
    *,
    load_current: Callable[[Path], object],
) -> BlueprintRouteResult:
    values = _query_values(
        query,
        allowed={"statementId", "evidenceRef", "pseudocodeLine", "limit", "cursor"},
    )
    statement_id = _single(values, "statementId").strip()
    evidence_ref = _single(values, "evidenceRef").strip()
    line_text = _single(values, "pseudocodeLine").strip()
    pseudocode_line: int | None = None
    if line_text:
        try:
            pseudocode_line = int(line_text, 10)
        except ValueError as exc:
            raise problem(
                HTTPStatus.BAD_REQUEST,
                "BLUEPRINT_QUERY_INVALID",
                "pseudocodeLine must be a positive integer.",
            ) from exc
        if pseudocode_line < 1:
            raise problem(
                HTTPStatus.BAD_REQUEST,
                "BLUEPRINT_QUERY_INVALID",
                "pseudocodeLine must be a positive integer.",
            )
    for value, scheme, label in (
        (statement_id, "statement://", "statementId"),
        (evidence_ref, "bp://", "evidenceRef"),
    ):
        if value and (len(value) > MAX_IDENTIFIER_CHARACTERS or not value.startswith(scheme)):
            raise problem(
                HTTPStatus.BAD_REQUEST,
                "BLUEPRINT_QUERY_INVALID",
                f"{label} is invalid.",
            )
    state = _load_current(asset_dir, load_current)
    identity = _identity(asset_name, state)
    items = [
        item
        for item in _trace_items(state)
        if (not statement_id or item.get("statementId") == statement_id)
        and (
            not evidence_ref
            or evidence_ref in item.get("evidenceRefs", ())
        )
        and (
            pseudocode_line is None
            or int(item.get("pseudocodeLine") or 0) == pseudocode_line
        )
    ]
    filters = {
        "statementId": statement_id,
        "evidenceRef": evidence_ref,
        "pseudocodeLine": pseudocode_line,
    }
    page_items, page = _paginate(
        items,
        endpoint="trace",
        filters=filters,
        identity=identity,
        limit=_page_limit(values),
        raw_cursor=_single(values, "cursor"),
    )
    payload: dict[str, object] = {
        "ok": True,
        "schema": TRACE_RESPONSE_SCHEMA,
        "identity": identity,
        "filters": filters,
        "items": page_items,
        "page": page,
    }
    _path_free(payload)
    return BlueprintRouteResult(HTTPStatus.OK, payload)


def _gap_items(state: object) -> list[dict[str, object]]:
    return sorted(
        [
            _gap_projection(item)
            for item in _collection(_member(state, "gaps", []), "gaps", "items")
        ],
        key=lambda item: (
            str(item.get("graphRef") or ""),
            str(item.get("code") or ""),
            str(item.get("id") or ""),
        ),
    )


def _gaps(
    asset_name: str,
    asset_dir: Path,
    query: str,
    *,
    load_current: Callable[[Path], object],
) -> BlueprintRouteResult:
    values = _query_values(
        query,
        allowed={"graphRef", "code", "status", "limit", "cursor"},
    )
    graph_ref = _single(values, "graphRef").strip()
    code = _single(values, "code").strip().upper()
    status = _single(values, "status").strip().upper()
    if graph_ref and (len(graph_ref) > MAX_IDENTIFIER_CHARACTERS or not graph_ref.startswith("bp://")):
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_QUERY_INVALID",
            "graphRef must be an exact bp:// reference.",
        )
    if (
        code
        and (len(code) > 128 or _GAP_CODE.fullmatch(code) is None)
    ) or (
        status
        and (len(status) > 128 or status not in _GAP_STATUSES)
    ):
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "BLUEPRINT_QUERY_INVALID",
            "Gap filter is invalid.",
        )
    state = _load_current(asset_dir, load_current)
    identity = _identity(asset_name, state)
    items = [
        item
        for item in _gap_items(state)
        if (not graph_ref or item.get("graphRef") == graph_ref)
        and (not code or str(item.get("code") or "").upper() == code)
        and (not status or str(item.get("status") or "").upper() == status)
    ]
    filters = {"graphRef": graph_ref, "code": code, "status": status}
    page_items, page = _paginate(
        items,
        endpoint="gaps",
        filters=filters,
        identity=identity,
        limit=_page_limit(values),
        raw_cursor=_single(values, "cursor"),
    )
    payload: dict[str, object] = {
        "ok": True,
        "schema": GAPS_RESPONSE_SCHEMA,
        "identity": identity,
        "filters": filters,
        "items": page_items,
        "page": page,
    }
    _path_free(payload)
    return BlueprintRouteResult(HTTPStatus.OK, payload)


def blueprint_get_payload(
    path: str,
    query: str,
    *,
    capture_root: str | os.PathLike[str],
    load_current: Callable[[Path], object] | None = None,
    inspect_health: Callable[[Path], dict[str, object]] | None = None,
) -> BlueprintRouteResult | None:
    """Match one Blueprint GET endpoint and return its path-free response."""

    if path == "/api/blueprint/assets":
        return _asset_list(
            query,
            capture_root=capture_root,
            inspect_health=inspect_health or _default_inspect_health,
        )
    prefix = "/api/blueprint/assets/"
    if not path.startswith(prefix):
        if path.startswith("/api/blueprint"):
            raise problem(
                HTTPStatus.NOT_FOUND,
                "API_ENDPOINT_NOT_FOUND",
                "Unknown Blueprint API endpoint.",
            )
        return None

    remainder = path.removeprefix(prefix)
    parts = remainder.split("/") if remainder else []
    if not parts:
        raise problem(
            HTTPStatus.NOT_FOUND,
            "API_ENDPOINT_NOT_FOUND",
            "Unknown Blueprint API endpoint.",
        )
    asset_name = _asset_identifier(parts[0])
    asset_dir = _resolve_asset(capture_root, asset_name)
    loader = load_current or _default_load_current
    health_loader = inspect_health or _default_inspect_health

    if parts[1:] == ["evidence", "health"]:
        return _health(asset_name, asset_dir, inspect_health=health_loader)
    if parts[1:] == ["interpretation"]:
        return _interpretation(
            asset_name, asset_dir, query, load_current=loader
        )
    if parts[1:] == ["trace"]:
        return _trace(asset_name, asset_dir, query, load_current=loader)
    if parts[1:] == ["gaps"]:
        return _gaps(asset_name, asset_dir, query, load_current=loader)
    if len(parts) == 3 and parts[1] == "statements" and parts[2]:
        return _statement(
            asset_name,
            asset_dir,
            parts[2],
            query,
            load_current=loader,
        )
    raise problem(
        HTTPStatus.NOT_FOUND,
        "API_ENDPOINT_NOT_FOUND",
        "Unknown Blueprint API endpoint.",
    )


__all__ = [
    "ASSET_LIST_SCHEMA",
    "BlueprintRouteResult",
    "EVIDENCE_HEALTH_SCHEMA",
    "GAPS_RESPONSE_SCHEMA",
    "INTERPRETATION_RESPONSE_SCHEMA",
    "STATEMENT_RESPONSE_SCHEMA",
    "TRACE_RESPONSE_SCHEMA",
    "blueprint_get_payload",
]
