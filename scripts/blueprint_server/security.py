"""Network, origin, and token policy for the local control center."""

from __future__ import annotations

import hmac
import os
import re
import secrets
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlparse

from .request import ApiProblem, DEFAULT_MAX_BODY_BYTES, problem


LOCAL_HTTP_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
SESSION_HEADER = "X-Blueprint-Session"
NON_BROWSER_HEADER = "X-Blueprint-Client"
NON_BROWSER_VALUE = "non-browser"
_DEFAULT_HOME = str(Path.home().resolve())
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/]|\\\\(?:\?\\)?)[^\r\n]*"
)


def is_loopback_host(host: str) -> bool:
    return str(host or "").strip().strip("[]").casefold() in LOCAL_HTTP_HOSTS


def validate_bind_options(
    host: str,
    *,
    allow_remote: bool,
    auth_token: str | None,
) -> bool:
    """Validate startup exposure and return whether this is remote mode."""

    normalized_host = str(host or "").strip()
    if not normalized_host:
        raise ValueError("Host must not be empty.")
    remote = not is_loopback_host(normalized_host)
    if remote and not allow_remote:
        raise ValueError(
            "Non-loopback binding requires --allow-remote and --auth-token."
        )
    if remote and not str(auth_token or "").strip():
        raise ValueError(
            "Non-loopback binding requires a non-empty --auth-token."
        )
    return remote


def _authority(value: str, *, origin: bool) -> tuple[str, str, int] | None:
    try:
        parsed = urlparse(value if origin else f"http://{value}")
        port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
    except ValueError:
        return None
    if (
        (origin and parsed.scheme.casefold() not in {"http", "https"})
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return None
    hostname = str(parsed.hostname or "").casefold()
    if not hostname:
        return None
    return parsed.scheme.casefold(), hostname, int(port)


def redact_sensitive_text(
    value: object,
    *,
    secrets_to_hide: tuple[str, ...] = (),
    path_roots: tuple[Path | str, ...] = (),
    redact_absolute_paths: bool = False,
) -> str:
    """Remove known secrets and local roots from public text and logs."""

    text = str(value or "")
    for secret in sorted(
        {item for item in secrets_to_hide if item},
        key=len,
        reverse=True,
    ):
        text = text.replace(secret, "<redacted-token>")
    roots = {
        os.path.abspath(os.path.expanduser(str(root)))
        for root in path_roots
        if str(root)
    }
    roots.add(_DEFAULT_HOME)
    for root in sorted(roots, key=len, reverse=True):
        for spelling in {root, root.replace("\\", "/")}:
            if spelling:
                text = re.sub(
                    re.escape(spelling),
                    "<local-path>",
                    text,
                    flags=re.IGNORECASE,
                )
    if redact_absolute_paths:
        text = _WINDOWS_ABSOLUTE_PATH.sub("<local-path>", text)
    return text


@dataclass(slots=True)
class SecurityPolicy:
    bind_host: str
    allow_remote: bool = False
    auth_token: str | None = field(default=None, repr=False)
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    session_token: str = field(
        default_factory=lambda: secrets.token_urlsafe(32),
        repr=False,
    )
    remote: bool = field(init=False)

    def __post_init__(self) -> None:
        self.bind_host = str(self.bind_host or "").strip()
        self.auth_token = str(self.auth_token or "").strip() or None
        self.remote = validate_bind_options(
            self.bind_host,
            allow_remote=bool(self.allow_remote),
            auth_token=self.auth_token,
        )
        if self.max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive.")
        if not self.session_token:
            raise ValueError("session_token must not be empty.")

    @property
    def secrets_to_hide(self) -> tuple[str, ...]:
        return tuple(
            value
            for value in (self.session_token, self.auth_token)
            if value
        )

    def redact(self, value: object, *path_roots: Path | str) -> str:
        return redact_sensitive_text(
            value,
            secrets_to_hide=self.secrets_to_hide,
            path_roots=tuple(path_roots),
        )

    def _request_host(
        self,
        headers: object,
        *,
        server_port: int,
    ) -> tuple[str, str, int]:
        get_header = getattr(headers, "get", None)
        if not callable(get_header):
            raise problem(
                HTTPStatus.FORBIDDEN,
                "HOST_FORBIDDEN",
                "Request host is not allowed.",
            )
        host = _authority(str(get_header("Host") or "").strip(), origin=False)
        if host is None or host[2] != int(server_port):
            raise problem(
                HTTPStatus.FORBIDDEN,
                "HOST_FORBIDDEN",
                "Request host is not allowed.",
            )
        if not self.remote and host[1] not in LOCAL_HTTP_HOSTS:
            raise problem(
                HTTPStatus.FORBIDDEN,
                "HOST_FORBIDDEN",
                "Request host is not allowed.",
            )
        return host

    def _validate_remote_auth(self, headers: object) -> None:
        if not self.remote:
            return
        get_header = getattr(headers, "get", None)
        authorization = (
            str(get_header("Authorization") or "").strip()
            if callable(get_header)
            else ""
        )
        expected = f"Bearer {self.auth_token}"
        if not hmac.compare_digest(authorization, expected):
            raise problem(
                HTTPStatus.FORBIDDEN,
                "REMOTE_AUTH_REQUIRED",
                "Remote requests require a valid bearer token.",
            )

    def _validate_optional_origin(
        self,
        headers: object,
        request_host: tuple[str, str, int],
    ) -> None:
        get_header = getattr(headers, "get", None)
        raw_origin = (
            str(get_header("Origin") or "").strip()
            if callable(get_header)
            else ""
        )
        if not raw_origin:
            return
        if _authority(raw_origin, origin=True) != request_host:
            raise problem(
                HTTPStatus.FORBIDDEN,
                "ORIGIN_FORBIDDEN",
                "Cross-origin requests are not allowed.",
            )

    def validate_get_request(
        self,
        headers: object,
        *,
        server_port: int,
    ) -> None:
        request_host = self._request_host(headers, server_port=server_port)
        self._validate_optional_origin(headers, request_host)
        self._validate_remote_auth(headers)

    def validate_session_request(
        self,
        headers: object,
        *,
        server_port: int,
    ) -> None:
        self.validate_get_request(headers, server_port=server_port)

    def validate_post_request(
        self,
        headers: object,
        *,
        server_port: int,
    ) -> None:
        request_host = self._request_host(headers, server_port=server_port)
        get_header = getattr(headers, "get", None)
        if not callable(get_header):
            raise problem(
                HTTPStatus.BAD_REQUEST,
                "REQUEST_HEADERS_INVALID",
                "Request headers are invalid.",
            )

        media_type = (
            str(get_header("Content-Type") or "")
            .split(";", 1)[0]
            .strip()
            .casefold()
        )
        if media_type != "application/json":
            raise problem(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "JSON_CONTENT_TYPE_REQUIRED",
                "POST requests require Content-Type: application/json.",
            )

        supplied_session = str(get_header(SESSION_HEADER) or "").strip()
        if not supplied_session:
            raise problem(
                HTTPStatus.FORBIDDEN,
                "SESSION_TOKEN_REQUIRED",
                f"POST requests require {SESSION_HEADER}.",
            )
        if not hmac.compare_digest(supplied_session, self.session_token):
            raise problem(
                HTTPStatus.FORBIDDEN,
                "SESSION_TOKEN_INVALID",
                "The Blueprint session token is invalid.",
            )

        raw_origin = str(get_header("Origin") or "").strip()
        if raw_origin:
            if _authority(raw_origin, origin=True) != request_host:
                raise problem(
                    HTTPStatus.FORBIDDEN,
                    "ORIGIN_FORBIDDEN",
                    "Cross-origin POST requests are not allowed.",
                )
        elif (
            str(get_header(NON_BROWSER_HEADER) or "").strip().casefold()
            != NON_BROWSER_VALUE
        ):
            raise problem(
                HTTPStatus.FORBIDDEN,
                "ORIGIN_REQUIRED",
                "Browser POST requests require a same-origin Origin header.",
            )

        self._validate_remote_auth(headers)


__all__ = [
    "ApiProblem",
    "LOCAL_HTTP_HOSTS",
    "NON_BROWSER_HEADER",
    "NON_BROWSER_VALUE",
    "SESSION_HEADER",
    "SecurityPolicy",
    "is_loopback_host",
    "redact_sensitive_text",
    "validate_bind_options",
]
