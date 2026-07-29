"""Bounded JSON request parsing and structured API errors."""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import BinaryIO


DEFAULT_MAX_BODY_BYTES = 1024 * 1024
DEFAULT_DISCARD_OVERAGE_BYTES = 64 * 1024


class ApiProblem(Exception):
    """An expected HTTP failure with a stable, JSON-safe public payload."""

    def __init__(self, status: HTTPStatus, payload: dict[str, object]):
        super().__init__(str(payload.get("error") or status.phrase))
        self.status = status
        self.payload = payload


def problem(status: HTTPStatus, code: str, message: str) -> ApiProblem:
    return ApiProblem(
        status,
        {
            "ok": False,
            "code": code,
            "error": message,
        },
    )


def content_length(headers: object, *, max_body_bytes: int) -> int:
    get_header = getattr(headers, "get", None)
    if not callable(get_header):
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "REQUEST_HEADERS_INVALID",
            "Request headers are invalid.",
        )
    if str(get_header("Transfer-Encoding") or "").strip():
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "TRANSFER_ENCODING_UNSUPPORTED",
            "Transfer-Encoding is not supported.",
        )
    raw_length = str(get_header("Content-Length") or "").strip()
    if not raw_length:
        raise problem(
            HTTPStatus.LENGTH_REQUIRED,
            "CONTENT_LENGTH_REQUIRED",
            "Content-Length is required.",
        )
    try:
        length = int(raw_length, 10)
    except ValueError as exc:
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "CONTENT_LENGTH_INVALID",
            "Content-Length must be a non-negative integer.",
        ) from exc
    if length <= 0:
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "REQUEST_BODY_REQUIRED",
            "A JSON object request body is required.",
        )
    if length > max_body_bytes:
        raise problem(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            "REQUEST_BODY_TOO_LARGE",
            f"Request body exceeds the {max_body_bytes}-byte limit.",
        )
    return length


def read_json_object(
    stream: BinaryIO,
    headers: object,
    *,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> dict[str, object]:
    """Read exactly one bounded UTF-8 JSON object from an HTTP request."""

    length = content_length(headers, max_body_bytes=max_body_bytes)
    raw = stream.read(length)
    if len(raw) != length:
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "REQUEST_BODY_INCOMPLETE",
            "Request body ended before Content-Length bytes were received.",
        )
    try:
        text = raw.decode("utf-8-sig")
        data = json.loads(text)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "REQUEST_JSON_INVALID",
            "Request body must contain valid UTF-8 JSON.",
        ) from exc
    if not isinstance(data, dict):
        raise problem(
            HTTPStatus.BAD_REQUEST,
            "REQUEST_BODY_OBJECT_REQUIRED",
            "Request body must be a JSON object.",
        )
    return data


def discard_bounded_body(
    stream: BinaryIO,
    headers: object,
    *,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    overage_bytes: int = DEFAULT_DISCARD_OVERAGE_BYTES,
) -> bool:
    """Stream-discard a declared body without allocating it.

    This is used only after an early rejection so Windows can close the socket
    gracefully instead of resetting a client that is still sending a small
    body.  Invalid, chunked, or materially oversized bodies are never awaited.
    The caller owns the short socket timeout.
    """

    get_header = getattr(headers, "get", None)
    if not callable(get_header):
        return False
    if str(get_header("Transfer-Encoding") or "").strip():
        return False
    try:
        length = int(str(get_header("Content-Length") or "").strip(), 10)
    except ValueError:
        return False
    discard_limit = int(max_body_bytes) + int(overage_bytes)
    if length < 0 or length > discard_limit:
        return False
    remaining = length
    while remaining:
        chunk = stream.read(min(64 * 1024, remaining))
        if not chunk:
            return False
        remaining -= len(chunk)
    return True


__all__ = [
    "ApiProblem",
    "DEFAULT_DISCARD_OVERAGE_BYTES",
    "DEFAULT_MAX_BODY_BYTES",
    "content_length",
    "discard_bounded_body",
    "problem",
    "read_json_object",
]
