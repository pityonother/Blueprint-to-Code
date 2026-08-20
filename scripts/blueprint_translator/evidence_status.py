"""Chinese user-status projection for independent Evidence and closure axes."""

from __future__ import annotations

from typing import Final


EVIDENCE_AVAILABILITIES: Final = frozenset(
    {"FORMAL_QUERY", "IDENTITY_ONLY", "UNAVAILABLE"}
)
ANSWER_CLOSURES: Final = frozenset({"COMPLETE", "PARTIAL", "NOT_REVIEWED"})


def _require_availability(value: str) -> str:
    normalized = str(value)
    if normalized not in EVIDENCE_AVAILABILITIES:
        raise ValueError(f"unsupported evidence availability: {normalized}")
    return normalized


def project_query_status_zh(
    evidence_availability: str,
    *,
    purpose: str,
    allowed: bool,
) -> str:
    """Project a query-policy decision without making an answer claim."""

    availability = _require_availability(evidence_availability)
    if allowed and purpose == "draft_query":
        return "只能回答一部分"
    if availability == "IDENTITY_ONLY":
        return "只识别资产身份"
    if allowed and availability == "FORMAL_QUERY":
        return "可正式查询"
    return "当前工具无法读取"


def project_sample_status_zh(
    evidence_availability: str,
    answer_closure: str,
) -> str:
    """Project one user-facing sample status while retaining both machine axes."""

    availability = _require_availability(evidence_availability)
    closure = str(answer_closure)
    if closure not in ANSWER_CLOSURES:
        raise ValueError(f"unsupported answer closure: {closure}")
    if closure == "NOT_REVIEWED":
        return "尚未测试"
    if availability == "IDENTITY_ONLY":
        return "只识别资产身份"
    if availability == "UNAVAILABLE":
        return "当前工具无法读取"
    if closure == "PARTIAL":
        return "只能回答一部分"
    return "可正式查询"


__all__ = [
    "ANSWER_CLOSURES",
    "EVIDENCE_AVAILABILITIES",
    "project_query_status_zh",
    "project_sample_status_zh",
]
