"""Pure response construction for the local control-center HTTP handler."""

from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus


@dataclass(frozen=True, slots=True)
class PreparedResponse:
    status: HTTPStatus
    headers: tuple[tuple[str, str], ...]
    body: bytes


def encode_json_response(payload: object) -> bytes:
    """Encode API JSON without presentation whitespace."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def prepare_json_response(
    payload: object,
    status: HTTPStatus = HTTPStatus.OK,
    *,
    close_connection: bool = False,
) -> PreparedResponse:
    body = encode_json_response(payload)
    headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    if close_connection:
        headers.append(("Connection", "close"))
    return PreparedResponse(
        status=status,
        headers=tuple(headers),
        body=body,
    )


def error_payload(message: str) -> dict[str, object]:
    return {
        "ok": False,
        "error": message,
    }


def static_content_type(mime_type: str | None) -> str:
    normalized = mime_type or "application/octet-stream"
    if normalized.startswith("text/") or normalized in {
        "application/javascript",
        "application/json",
        "image/svg+xml",
    }:
        return f"{normalized}; charset=utf-8"
    return normalized


__all__ = [
    "PreparedResponse",
    "encode_json_response",
    "error_payload",
    "prepare_json_response",
    "static_content_type",
]
