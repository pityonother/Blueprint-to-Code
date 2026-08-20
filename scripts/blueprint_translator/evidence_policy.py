"""Single fail-closed decision core for validated Blueprint Evidence state."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, Literal, Protocol


POLICY_VERSION: Final = "1"
EvidencePurpose = Literal["formal_query", "publish", "benchmark", "draft_query"]
_STRICT_PURPOSES: Final = frozenset({"formal_query", "publish", "benchmark"})
_REQUIRES_SEMANTIC_FACTS: Final = frozenset({"formal_query", "publish", "draft_query"})
_PURPOSES: Final = _STRICT_PURPOSES | {"draft_query"}
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
SEMANTIC_COUNT_FIELDS: Final = (
    "nodes",
    "pins",
    "links",
    "classDefaults",
    "assetFields",
    "edgeObservations",
)


class EvidenceState(Protocol):
    """Structural input accepted by :func:`evaluate_evidence`."""

    database_path: Path
    source_kind: str
    release_authority: bool
    freshness_status: str
    migration_required: bool
    manifest_sha256: str | None
    pointer_sha256: str | None
    database_sha256: str
    database_bytes: int
    manifest_content_sha256: str
    manifest_bytes: int
    agent_index_sha256: str
    agent_index_bytes: int
    semantic_fact_count: int
    non_upgradeable_gaps: tuple[str, ...]


class EvidencePolicyError(ValueError):
    """Fail-closed policy refusal carrying one stable machine reason."""

    def __init__(self, decision: "EvidenceDecision") -> None:
        self.code = decision.reason_code
        self.decision = decision
        super().__init__(f"{self.code}: {decision.public_status_zh}")


@dataclass(frozen=True)
class EvidenceDecision:
    """Stable policy result; it intentionally carries no answer-closure claim."""

    allowed: bool
    purpose: EvidencePurpose
    reason_code: str
    reason_codes: tuple[str, ...]
    binding_digest: str
    binding_summary: dict[str, object]
    evidence_availability: str
    public_status_zh: str
    non_upgradeable_gaps: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def semantic_fact_count(counts: object) -> int:
    """Count semantic rows without treating identity metadata as evidence facts."""

    if not isinstance(counts, dict):
        return 0
    total = 0
    for key in SEMANTIC_COUNT_FIELDS:
        value = counts.get(key, 0)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            total += value
    return total


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _binding_summary(state: EvidenceState) -> dict[str, object]:
    """Return a path-free binding projection suitable for public receipts."""

    return {
        "policyVersion": POLICY_VERSION,
        "sourceKind": state.source_kind,
        "freshnessStatus": state.freshness_status,
        "releaseAuthority": bool(state.release_authority),
        "migrationRequired": bool(state.migration_required),
        "manifestSha256": state.manifest_sha256,
        "pointerSha256": state.pointer_sha256,
        "databaseSha256": state.database_sha256,
        "databaseBytes": state.database_bytes,
        "manifestContentSha256": state.manifest_content_sha256,
        "manifestBytes": state.manifest_bytes,
        "agentIndexSha256": state.agent_index_sha256,
        "agentIndexBytes": state.agent_index_bytes,
        "semanticFactCount": state.semantic_fact_count,
    }


def _binding_digest(summary: dict[str, object]) -> str:
    raw = json.dumps(
        summary,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def evaluate_evidence(
    state: EvidenceState,
    purpose: EvidencePurpose = "formal_query",
) -> EvidenceDecision:
    """Evaluate one already-resolved state without touching the filesystem.

    Resolution remains the input gate: it verifies paths, bytes, SQLite schema,
    source bindings, and pointer identity.  This function is the sole semantic
    policy gate used after resolution.  It never converts gaps into evidence and
    never asserts whether a user's question is fully answered.
    """

    if purpose not in _PURPOSES:
        raise ValueError(f"unsupported evidence purpose: {purpose}")

    reasons: list[str] = []
    if not _valid_sha256(state.database_sha256) or state.database_bytes <= 0:
        reasons.append("DATABASE_BINDING_INVALID")
    if (
        not _valid_sha256(state.manifest_content_sha256)
        or state.manifest_bytes <= 0
    ):
        reasons.append("MANIFEST_CONTENT_BINDING_INVALID")
    if not _valid_sha256(state.agent_index_sha256) or state.agent_index_bytes <= 0:
        reasons.append("AGENT_INDEX_BINDING_INVALID")
    if state.semantic_fact_count <= 0 and purpose in _REQUIRES_SEMANTIC_FACTS:
        reasons.append("EVIDENCE_EMPTY")

    if state.freshness_status == "STALE":
        reasons.append("EVIDENCE_STALE")
    elif state.freshness_status == "SOURCE_UNAVAILABLE":
        reasons.append("SOURCE_UNAVAILABLE")
    elif state.freshness_status != "FRESH":
        reasons.append("FRESHNESS_STATUS_UNSUPPORTED")

    if purpose in _STRICT_PURPOSES:
        if state.source_kind != "INDEXED_V3_CURRENT":
            reasons.append("SOURCE_KIND_NOT_CURRENT")
        if not state.release_authority:
            reasons.append("RELEASE_AUTHORITY_MISSING")
        if state.migration_required:
            reasons.append("MIGRATION_REQUIRED")
        if not _valid_sha256(state.manifest_sha256):
            reasons.append("MANIFEST_BINDING_MISSING")
        if not _valid_sha256(state.pointer_sha256):
            reasons.append("POINTER_BINDING_MISSING")

    normalized_reasons = tuple(dict.fromkeys(reasons))
    allowed = not normalized_reasons
    if allowed and purpose in _STRICT_PURPOSES and state.semantic_fact_count > 0:
        availability = "FORMAL_QUERY"
        public_status = "可正式查询"
    elif state.semantic_fact_count <= 0:
        availability = "IDENTITY_ONLY"
        public_status = "只识别资产身份"
    else:
        availability = "UNAVAILABLE"
        public_status = "当前工具无法读取"

    summary = _binding_summary(state)
    return EvidenceDecision(
        allowed=allowed,
        purpose=purpose,
        reason_code=normalized_reasons[0] if normalized_reasons else "ALLOWED",
        reason_codes=normalized_reasons,
        binding_digest=_binding_digest(summary),
        binding_summary=summary,
        evidence_availability=availability,
        public_status_zh=public_status,
        non_upgradeable_gaps=tuple(state.non_upgradeable_gaps),
    )


def require_evidence(
    state: EvidenceState,
    purpose: EvidencePurpose = "formal_query",
) -> EvidenceDecision:
    """Return the decision or raise its stable, path-free refusal."""

    decision = evaluate_evidence(state, purpose=purpose)
    if not decision.allowed:
        raise EvidencePolicyError(decision)
    return decision


__all__ = [
    "EvidenceDecision",
    "EvidencePolicyError",
    "EvidencePurpose",
    "POLICY_VERSION",
    "SEMANTIC_COUNT_FIELDS",
    "evaluate_evidence",
    "require_evidence",
    "semantic_fact_count",
]
