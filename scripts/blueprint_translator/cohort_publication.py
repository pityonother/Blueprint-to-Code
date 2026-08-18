"""Publish a reviewed, cross-category Blueprint Evidence cohort.

The cohort publisher is deliberately a thin orchestrator around the existing
Evidence v3 and Interpretation v1 publishers.  It validates every source and
destination identity before the first mutation, then publishes one asset at a
time with the existing immutable revision and pointer-CAS contracts.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from .evidence_publication import (
    _lexical_absolute,
    _require_plain_directory,
    _require_plain_path_chain,
    publish_prepared_evidence_revision,
)
from .evidence_repository import (
    ResolvedEvidenceState,
    evidence_manifest_payload,
    resolve_asset_evidence_state,
)
from .interpretation_publication import (
    build_interpretation,
    inspect_interpretation_health,
    publish_interpretation,
)


PLAN_SCHEMA: Final = "blueprint-to-code.evidence-cohort-plan/v1"
RECEIPT_SCHEMA: Final = "blueprint-to-code.evidence-cohort-publication/v1"
_CATEGORY_RE: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_COHORT_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SEMANTIC_COUNT_FIELDS: Final = (
    "nodes",
    "pins",
    "links",
    "classDefaults",
    "edgeObservations",
)


class CohortPublicationError(RuntimeError):
    """Fail-closed cohort error with a stable machine-readable code."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class _PlanAsset:
    asset: str
    source_relative: PurePosixPath
    object_path: str
    category_code: str
    represented_object_count: int


@dataclass(frozen=True)
class _PreparedAsset:
    plan: _PlanAsset
    source_dir: Path
    destination_dir: Path
    state: ResolvedEvidenceState
    semantic_fact_count: int
    interpretation_digest: str


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CohortPublicationError(
                "COHORT_PLAN_INVALID",
                f"duplicate JSON key is forbidden: {key}",
            )
        result[key] = value
    return result


def _load_plan(path: Path) -> tuple[str, tuple[_PlanAsset, ...]]:
    _require_plain_path_chain(path, label="cohort plan")
    if not path.is_file():
        raise CohortPublicationError("COHORT_PLAN_INVALID", "plan file is missing")
    if path.stat().st_size > 4 * 1024 * 1024:
        raise CohortPublicationError("COHORT_PLAN_INVALID", "plan exceeds 4 MiB")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {value}")
            ),
        )
    except CohortPublicationError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise CohortPublicationError(
            "COHORT_PLAN_INVALID", "plan must be strict UTF-8 JSON"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema") != PLAN_SCHEMA:
        raise CohortPublicationError(
            "COHORT_PLAN_INVALID", f"schema must be {PLAN_SCHEMA}"
        )
    cohort_id = str(payload.get("cohortId") or "")
    if not _COHORT_ID_RE.fullmatch(cohort_id):
        raise CohortPublicationError("COHORT_PLAN_INVALID", "cohortId is invalid")
    raw_assets = payload.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets or len(raw_assets) > 100:
        raise CohortPublicationError(
            "COHORT_PLAN_INVALID", "assets must contain between 1 and 100 entries"
        )

    assets: list[_PlanAsset] = []
    seen_assets: set[str] = set()
    seen_sources: set[str] = set()
    seen_objects: set[str] = set()
    for index, raw in enumerate(raw_assets):
        if not isinstance(raw, dict):
            raise CohortPublicationError(
                "COHORT_PLAN_INVALID", f"assets[{index}] must be an object"
            )
        asset = str(raw.get("asset") or "")
        if (
            not asset
            or len(asset) > 255
            or asset in {".", ".."}
            or any(character in asset for character in "/\\:\0")
            or any(ord(character) < 32 or ord(character) == 127 for character in asset)
        ):
            raise CohortPublicationError(
                "COHORT_PLAN_INVALID", f"assets[{index}].asset is invalid"
            )
        raw_source = raw.get("sourceAssetDir")
        if not isinstance(raw_source, str) or not raw_source or "\\" in raw_source:
            raise CohortPublicationError(
                "COHORT_PLAN_INVALID",
                f"assets[{index}].sourceAssetDir must be a relative POSIX path",
            )
        source_relative = PurePosixPath(raw_source)
        if (
            source_relative.is_absolute()
            or any(part in {"", ".", ".."} for part in source_relative.parts)
            or source_relative.name != asset
        ):
            raise CohortPublicationError(
                "COHORT_PLAN_INVALID",
                f"assets[{index}].sourceAssetDir escapes its source root or mismatches asset",
            )
        object_path = str(raw.get("objectPath") or "")
        if (
            not object_path.startswith("/")
            or "." not in object_path
            or len(object_path) > 4096
            or any(ord(character) < 32 or ord(character) == 127 for character in object_path)
        ):
            raise CohortPublicationError(
                "COHORT_PLAN_INVALID", f"assets[{index}].objectPath is invalid"
            )
        category_code = str(raw.get("categoryCode") or "")
        if not _CATEGORY_RE.fullmatch(category_code):
            raise CohortPublicationError(
                "COHORT_PLAN_INVALID", f"assets[{index}].categoryCode is invalid"
            )
        represented = raw.get("representedObjectCount")
        if (
            not isinstance(represented, int)
            or isinstance(represented, bool)
            or represented < 1
        ):
            raise CohortPublicationError(
                "COHORT_PLAN_INVALID",
                f"assets[{index}].representedObjectCount must be a positive integer",
            )

        asset_key = asset.casefold()
        source_key = source_relative.as_posix().casefold()
        object_key = object_path.casefold()
        if asset_key in seen_assets or source_key in seen_sources or object_key in seen_objects:
            raise CohortPublicationError(
                "COHORT_PLAN_INVALID", "asset, sourceAssetDir, and objectPath must be unique"
            )
        seen_assets.add(asset_key)
        seen_sources.add(source_key)
        seen_objects.add(object_key)
        assets.append(
            _PlanAsset(
                asset=asset,
                source_relative=source_relative,
                object_path=object_path,
                category_code=category_code,
                represented_object_count=represented,
            )
        )
    return cohort_id, tuple(assets)


def _manifest_semantic_fact_count(manifest: dict[str, Any]) -> int:
    counts = manifest.get("counts")
    if not isinstance(counts, dict):
        return 0
    total = 0
    for key in _SEMANTIC_COUNT_FIELDS:
        value = counts.get(key, 0)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            total += value
    return total


def _destination_identity(destination: Path) -> str | None:
    if not destination.exists():
        return None
    _require_plain_path_chain(destination, label="cohort destination asset")
    _require_plain_directory(destination, label="cohort destination asset")
    try:
        state = resolve_asset_evidence_state(destination, allow_stale=True)
        manifest = evidence_manifest_payload(state)
    except Exception as exc:
        raise CohortPublicationError(
            "COHORT_DESTINATION_INVALID",
            f"{destination.name} exists without valid indexed Evidence",
        ) from exc
    return str(manifest.get("objectPath") or "")


def _prepare_assets(
    assets: tuple[_PlanAsset, ...],
    *,
    source_root: Path,
    capture_root: Path,
    budget: int,
) -> tuple[_PreparedAsset, ...]:
    prepared: list[_PreparedAsset] = []
    for asset in assets:
        source_dir = source_root.joinpath(*asset.source_relative.parts)
        destination = capture_root / asset.asset
        try:
            state = resolve_asset_evidence_state(source_dir)
            manifest = evidence_manifest_payload(state)
        except Exception as exc:
            raise CohortPublicationError(
                "COHORT_SOURCE_INVALID",
                f"{asset.asset} source Evidence did not validate",
            ) from exc
        if (
            state.source_kind != "INDEXED_V3_CURRENT"
            or not state.release_authority
            or state.migration_required
            or state.freshness_status != "FRESH"
        ):
            raise CohortPublicationError(
                "COHORT_SOURCE_NOT_AUTHORITATIVE",
                f"{asset.asset} requires FRESH current v3 release authority",
            )
        if str(manifest.get("objectPath") or "") != asset.object_path:
            raise CohortPublicationError(
                "COHORT_SOURCE_IDENTITY_MISMATCH",
                f"{asset.asset} source objectPath differs from the reviewed plan",
            )
        semantic_fact_count = _manifest_semantic_fact_count(manifest)
        if semantic_fact_count <= 0:
            raise CohortPublicationError(
                "COHORT_SOURCE_HAS_NO_SEMANTIC_FACTS",
                f"{asset.asset} is an identity-only capture",
            )
        destination_object_path = _destination_identity(destination)
        if destination_object_path is not None and destination_object_path != asset.object_path:
            raise CohortPublicationError(
                "COHORT_DESTINATION_IDENTITY_CONFLICT",
                f"{asset.asset} already belongs to another objectPath",
            )
        try:
            preview = build_interpretation(source_dir, budget=budget)
        except Exception as exc:
            raise CohortPublicationError(
                "COHORT_INTERPRETATION_PREFLIGHT_FAILED",
                f"{asset.asset} Interpretation could not be built within the review budget",
            ) from exc
        prepared.append(
            _PreparedAsset(
                plan=asset,
                source_dir=source_dir,
                destination_dir=destination,
                state=state,
                semantic_fact_count=semantic_fact_count,
                interpretation_digest=preview.semantic_digest,
            )
        )
    return tuple(prepared)


def publish_evidence_cohort(
    *,
    plan_path: str | os.PathLike[str],
    source_root: str | os.PathLike[str],
    capture_root: str | os.PathLike[str],
    budget: int = 100_000,
) -> dict[str, object]:
    """Publish every reviewed asset and return a path-free completion receipt."""

    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        raise CohortPublicationError("COHORT_PLAN_INVALID", "budget must be positive")
    plan = _lexical_absolute(plan_path)
    sources = _lexical_absolute(source_root)
    captures = _lexical_absolute(capture_root)
    cohort_id, assets = _load_plan(plan)
    try:
        _require_plain_path_chain(sources, label="cohort source root")
        _require_plain_directory(sources, label="cohort source root")
        _require_plain_path_chain(captures, label="cohort capture root")
    except (OSError, ValueError) as exc:
        raise CohortPublicationError(
            "COHORT_ROOT_INVALID", "source or destination root is invalid"
        ) from exc
    if sources == captures or sources in captures.parents or captures in sources.parents:
        raise CohortPublicationError(
            "COHORT_ROOTS_OVERLAP", "source and destination roots must be disjoint"
        )
    prepared = _prepare_assets(
        assets,
        source_root=sources,
        capture_root=captures,
        budget=budget,
    )

    captures.mkdir(parents=True, exist_ok=True)
    _require_plain_directory(captures, label="cohort capture root")
    receipts: list[dict[str, object]] = []
    for item in prepared:
        manifest = evidence_manifest_payload(item.state)
        try:
            evidence = publish_prepared_evidence_revision(
                asset_dir=item.destination_dir,
                database_path=item.state.database_path,
                agent_index_bytes=item.state.agent_index_raw,
                asset_id=str(manifest.get("assetId") or ""),
                object_path=item.plan.object_path,
                require_fresh=True,
            )
            interpretation = publish_interpretation(
                item.destination_dir,
                budget=budget,
                expected_semantic_digest=item.interpretation_digest,
            )
            health = inspect_interpretation_health(item.destination_dir)
        except Exception as exc:
            raise CohortPublicationError(
                "COHORT_ASSET_PUBLICATION_FAILED",
                f"{item.plan.asset} did not reach READY",
            ) from exc
        if (
            health.get("status") != "READY"
            or health.get("evidence", {}).get("freshnessStatus") != "FRESH"
            or health.get("evidence", {}).get("releaseAuthority") is not True
        ):
            raise CohortPublicationError(
                "COHORT_ASSET_NOT_READY",
                f"{item.plan.asset} failed the final authoritative health gate",
            )
        receipts.append(
            {
                "asset": item.plan.asset,
                "categoryCode": item.plan.category_code,
                "objectPath": item.plan.object_path,
                "representedObjectCount": item.plan.represented_object_count,
                "semanticFactCount": item.semantic_fact_count,
                "status": "READY",
                "freshnessStatus": "FRESH",
                "releaseAuthority": True,
                "evidenceRevisionId": evidence.revision_id,
                "evidenceManifestSha256": evidence.manifest_sha256,
                "evidencePointerSha256": evidence.pointer_sha256,
                "evidenceReused": evidence.reused_existing,
                "interpretationRevisionId": interpretation.revision_id,
                "interpretationManifestSha256": interpretation.manifest_sha256,
                "interpretationPointerSha256": interpretation.pointer_sha256,
                "interpretationReused": interpretation.reused,
            }
        )
    return {
        "schema": RECEIPT_SCHEMA,
        "cohortId": cohort_id,
        "status": "COMPLETE",
        "ready": len(receipts),
        "total": len(assets),
        "representedObjectCount": sum(
            asset.represented_object_count for asset in assets
        ),
        "assets": receipts,
    }


__all__ = [
    "CohortPublicationError",
    "PLAN_SCHEMA",
    "RECEIPT_SCHEMA",
    "publish_evidence_cohort",
]
