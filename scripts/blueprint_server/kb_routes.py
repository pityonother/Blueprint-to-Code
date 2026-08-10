"""HTTP-shaped adapters for the vNext knowledge service."""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus
from urllib.parse import parse_qs, unquote

from blueprint_translator.kb_vnext.kb_api import KnowledgeApiError

from .request import ApiProblem


def _kb_api_problem(exc: KnowledgeApiError) -> ApiProblem:
    return ApiProblem(
        exc.status,
        {
            "ok": False,
            "code": exc.code,
            "error": exc.message,
        },
    )


def _kb_query_value(
    values: dict[str, list[str]],
    key: str,
    default: str = "",
) -> str:
    raw = values.get(key, [default])
    return raw[0] if raw else default


def kb_get_payload(
    path: str,
    query: str,
    *,
    service: object,
    get_job: Callable[[str], dict[str, object]],
) -> dict[str, object] | None:
    try:
        values = parse_qs(query, keep_blank_values=True)
        if path == "/api/kb/health":
            return service.health()  # type: ignore[attr-defined]
        if path == "/api/kb/entities/search":
            return service.search_entities(  # type: ignore[attr-defined]
                query=_kb_query_value(values, "q"),
                limit=_kb_query_value(values, "limit", "25"),
                cursor=_kb_query_value(values, "cursor", "0"),
            )
        prefix = "/api/kb/entities/"
        if path.startswith(prefix):
            remainder = unquote(path.removeprefix(prefix)).strip("/")
            parts = remainder.split("/")
            if not parts[0].isdigit() or int(parts[0]) <= 0:
                raise KnowledgeApiError(
                    HTTPStatus.BAD_REQUEST,
                    "REQUEST_INVALID",
                    "Entity id must be a positive integer.",
                )
            entity_id = int(parts[0])
            if len(parts) == 1:
                return service.entity(entity_id)  # type: ignore[attr-defined]
            if len(parts) == 2 and parts[1] in {
                "facts",
                "relationships",
                "coverage",
                "effective-defaults",
            }:
                return service.entity_collection(  # type: ignore[attr-defined]
                    entity_id,
                    kind=parts[1],
                    limit=_kb_query_value(values, "limit", "50"),
                    cursor=_kb_query_value(values, "cursor", "0"),
                )
            raise KnowledgeApiError(
                HTTPStatus.NOT_FOUND,
                "API_ENDPOINT_NOT_FOUND",
                "Unknown knowledge endpoint.",
            )
        if path.startswith("/api/kb/jobs/"):
            job_id = unquote(path.removeprefix("/api/kb/jobs/")).strip("/")
            return {
                "job": get_job(job_id),
                "returned": 1,
                "omitted": 0,
                "nextQuery": "",
                "freshness": "FRESH",
                "evidence": [],
                "gap": [],
            }
        return None
    except KnowledgeApiError as exc:
        raise _kb_api_problem(exc) from exc


__all__ = [
    "_kb_api_problem",
    "_kb_query_value",
    "kb_get_payload",
]
