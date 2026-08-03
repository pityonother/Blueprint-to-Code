from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..evidence_repository import ResolvedEvidenceState


INTERPRETATION_SCHEMA = "blueprint-to-code.blueprint-interpretation/v1"
TRACE_SCHEMA = "blueprint-to-code.blueprint-interpretation-trace/v1"
GAPS_SCHEMA = "blueprint-to-code.blueprint-interpretation-gaps/v1"
MANIFEST_SCHEMA = "blueprint-to-code.blueprint-interpretation-manifest/v1"
CURRENT_SCHEMA = "blueprint-to-code.blueprint-interpretation-current/v1"
INTERPRETER_VERSION = "blueprint-interpreter/1.0.0"
PSEUDOCODE_HEADER = (
    "EVIDENCE-DERIVED PSEUDOCODE — NOT ORIGINAL C++ — NOT GUARANTEED COMPILABLE"
)
SEMANTIC_DIGEST_DOMAIN = (
    "blueprint-to-code.blueprint-interpretation-semantic-digest/v1\n"
)

STATEMENT_KINDS = frozenset(
    {"EVENT", "CALL", "BRANCH", "SET", "RETURN", "DELEGATE", "LOOP", "GAP"}
)
STATEMENT_STATUSES = frozenset(
    {"CONFIRMED", "HEURISTIC", "SOURCE_NOT_AVAILABLE", "NOT_RECOVERED", "AMBIGUOUS"}
)


class InterpretationPublicationError(RuntimeError):
    """Stable, code-bearing error for interpretation generation/publication."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        self.message = str(message)
        super().__init__(f"{self.code}: {self.message}")


class InterpretationArtifactInvalid(InterpretationPublicationError):
    """An immutable Interpretation pointer, manifest, or artifact failed validation."""


@dataclass(frozen=True)
class InterpretationBuild:
    interpretation: dict[str, Any]
    markdown: str
    trace: dict[str, Any]
    gaps: dict[str, Any]
    pseudocode: str
    semantic_digest: str
    evidence_state: ResolvedEvidenceState


@dataclass(frozen=True)
class PublishedInterpretation:
    asset_dir: Path
    revision_dir: Path
    pointer_path: Path
    revision_id: str
    manifest_sha256: str
    pointer_sha256: str
    semantic_digest: str
    evidence_revision_id: str
    evidence_manifest_sha256: str
    created: bool
    reused: bool


@dataclass(frozen=True)
class LoadedInterpretation:
    asset_dir: Path
    revision_dir: Path
    pointer_path: Path
    revision_id: str
    manifest_sha256: str
    pointer_sha256: str
    manifest: dict[str, Any]
    interpretation: dict[str, Any]
    trace: dict[str, Any]
    gaps: dict[str, Any]
    pseudocode: str
    markdown: str


FaultInjector = Callable[[str], None]


def canonical_json_bytes(value: object, *, newline: bool = True) -> bytes:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return raw + (b"\n" if newline else b"")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def semantic_digest(value: object) -> str:
    digest = hashlib.sha256()
    digest.update(SEMANTIC_DIGEST_DOMAIN.encode("utf-8"))
    digest.update(canonical_json_bytes(value, newline=False))
    return digest.hexdigest()


def stable_id(prefix: str, value: object, *, length: int = 24) -> str:
    digest = sha256_bytes(canonical_json_bytes(value, newline=False))[:length]
    return f"{prefix}{digest}"


def artifact_descriptor(path: str, raw: bytes) -> dict[str, object]:
    return {
        "path": path,
        "bytes": len(raw),
        "sha256": sha256_bytes(raw),
    }


def call_fault(fault_injector: FaultInjector | None, checkpoint: str) -> None:
    if fault_injector is not None:
        fault_injector(checkpoint)


__all__ = [
    "CURRENT_SCHEMA",
    "FaultInjector",
    "GAPS_SCHEMA",
    "INTERPRETATION_SCHEMA",
    "INTERPRETER_VERSION",
    "MANIFEST_SCHEMA",
    "PSEUDOCODE_HEADER",
    "SEMANTIC_DIGEST_DOMAIN",
    "STATEMENT_KINDS",
    "STATEMENT_STATUSES",
    "TRACE_SCHEMA",
    "InterpretationArtifactInvalid",
    "InterpretationBuild",
    "InterpretationPublicationError",
    "LoadedInterpretation",
    "PublishedInterpretation",
    "artifact_descriptor",
    "call_fault",
    "canonical_json_bytes",
    "semantic_digest",
    "sha256_bytes",
    "stable_id",
]
