"""Bounded, deterministic candidate discovery over BlueprintService only."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence


_TOKEN = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u9fff]+")
_MAX_HINT_CANDIDATES = 5
_MAX_SCAN_ITEMS = 100


def _text(value: object) -> str:
    return " ".join(str(value or "").split())


def _tokens(value: object) -> set[str]:
    return {item.casefold() for item in _TOKEN.findall(_text(value)) if item}


def _health_projection(item: Mapping[str, object]) -> dict[str, object]:
    raw_health = item.get("health")
    health = dict(raw_health) if isinstance(raw_health, Mapping) else {}
    raw_asset = health.get("asset")
    asset = dict(raw_asset) if isinstance(raw_asset, Mapping) else {}
    raw_evidence = health.get("evidence")
    evidence = dict(raw_evidence) if isinstance(raw_evidence, Mapping) else {}
    return {
        "healthStatus": _text(health.get("status")),
        "reasonCode": _text(health.get("reasonCode")),
        "assetId": _text(asset.get("assetId")),
        "objectPath": _text(asset.get("objectPath")),
        "freshnessStatus": _text(evidence.get("freshnessStatus")),
        "releaseAuthority": bool(evidence.get("releaseAuthority", False)),
        "migrationRequired": bool(evidence.get("migrationRequired", False)),
    }


def _object_suffix_match(object_path: str, term: str) -> bool:
    normalized_path = object_path.casefold()
    normalized_term = term.strip().casefold().strip("/.")
    if not normalized_term or not normalized_path:
        return False
    return (
        normalized_path.endswith(normalized_term)
        or normalized_path.endswith(f"/{normalized_term}.{normalized_term}")
        or normalized_path.endswith(f".{normalized_term}")
    )


def _score(
    *,
    asset: str,
    object_path: str,
    text: str,
    aliases: Sequence[str],
) -> tuple[int, list[str]]:
    target = text.casefold()
    alias_set = {item.casefold() for item in aliases if item}
    signals: list[str] = []
    score = 0
    if asset.casefold() == target:
        signals.append("EXACT_CASEFOLD_NAME")
        score += 400
    if _object_suffix_match(object_path, text):
        signals.append("EXACT_OBJECT_PATH_SUFFIX")
        score += 350
    if asset.casefold() in alias_set or any(
        _object_suffix_match(object_path, alias) for alias in aliases
    ):
        signals.append("EXACT_ALIAS")
        score += 300
    target_tokens = _tokens(text)
    candidate_tokens = _tokens(asset) | _tokens(object_path)
    overlap = len(target_tokens & candidate_tokens)
    if overlap:
        signals.append("TOKEN_OVERLAP")
        score += min(100, overlap * 25)
    elif target and target in asset.casefold():
        signals.append("TOKEN_OVERLAP")
        score += 10
    return score, signals


def discover_candidates(
    blueprint: object,
    hint: Mapping[str, object],
    *,
    aliases: Sequence[str] = (),
) -> list[dict[str, object]]:
    """Return at most five semantically ranked candidates for one target hint."""

    text = _text(hint.get("text"))
    raw_hint_aliases = hint.get("aliases")
    hint_aliases = (
        [_text(item) for item in raw_hint_aliases]
        if isinstance(raw_hint_aliases, Sequence)
        and not isinstance(raw_hint_aliases, (str, bytes, bytearray))
        else []
    )
    all_aliases = tuple(
        dict.fromkeys(
            item for item in (*hint_aliases, *(_text(item) for item in aliases)) if item
        )
    )[:8]
    terms = tuple(
        dict.fromkeys(item for item in (text, *all_aliases, "") if len(item) <= 128)
    )
    found: dict[str, dict[str, object]] = {}
    for term in terms:
        response = blueprint.list_assets(query=term, limit=_MAX_SCAN_ITEMS, cursor="")
        items = response.get("items", []) if isinstance(response, Mapping) else []
        if not isinstance(items, Sequence) or isinstance(
            items, (str, bytes, bytearray)
        ):
            continue
        for item in items[:_MAX_SCAN_ITEMS]:
            if not isinstance(item, Mapping):
                continue
            asset_name = _text(item.get("asset"))
            if not asset_name:
                continue
            projection = _health_projection(item)
            score, signals = _score(
                asset=asset_name,
                object_path=str(projection["objectPath"]),
                text=text,
                aliases=all_aliases,
            )
            if score <= 0:
                continue
            candidate = {
                "asset": asset_name,
                **projection,
                "score": score,
                "signals": signals,
                "ambiguousTopScore": False,
            }
            existing = found.get(asset_name)
            if existing is None or int(candidate["score"]) > int(existing["score"]):
                found[asset_name] = candidate
    ordered = sorted(
        found.values(),
        key=lambda item: (
            -int(item["score"]),
            str(item["asset"]).casefold(),
            str(item["asset"]),
            str(item["objectPath"]),
        ),
    )[:_MAX_HINT_CANDIDATES]
    if ordered:
        top_score = int(ordered[0]["score"])
        ambiguous = sum(int(item["score"]) == top_score for item in ordered) > 1
        for item in ordered:
            item["ambiguousTopScore"] = ambiguous and int(item["score"]) == top_score
    return ordered


def merge_problem_candidates(
    candidates: Sequence[Mapping[str, object]],
    *,
    maximum: int = 20,
) -> list[dict[str, object]]:
    """Deduplicate per-hint candidates and retain deterministic best scores."""

    merged: dict[tuple[str, str], dict[str, object]] = {}
    for raw in candidates:
        candidate = dict(raw)
        key = (str(candidate.get("assetId") or ""), str(candidate.get("asset") or ""))
        existing = merged.get(key)
        if existing is None or int(candidate.get("score") or 0) > int(
            existing.get("score") or 0
        ):
            merged[key] = candidate
    ordered = sorted(
        merged.values(),
        key=lambda item: (
            -int(item.get("score") or 0),
            str(item.get("asset") or "").casefold(),
            str(item.get("asset") or ""),
            str(item.get("objectPath") or ""),
        ),
    )[:maximum]
    if ordered:
        top_score = int(ordered[0].get("score") or 0)
        ambiguous = (
            sum(int(item.get("score") or 0) == top_score for item in ordered) > 1
        )
        for item in ordered:
            item["ambiguousTopScore"] = (
                ambiguous and int(item.get("score") or 0) == top_score
            )
    return ordered


__all__ = ["discover_candidates", "merge_problem_candidates"]
