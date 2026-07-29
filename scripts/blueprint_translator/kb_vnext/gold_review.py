"""Fail-closed review-pack and receipt validation for ARK KB gold sets.

This module validates review infrastructure only.  It never invents reviewer
identities and never writes query, registration, or role production gold.
"""

from __future__ import annotations

import copy
import hashlib
import heapq
import json
import re
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path


PACK_SCHEMA = "ark-kb-gold-review-pack/v1"
REVIEW_SCHEMA = "ark-kb-gold-review/v1"
VALIDATION_SCHEMA = "ark-kb-gold-review-validation/v1"
QUERY_REVIEW_PROVENANCE_SCHEMA = "ark-kb-query-review-provenance/v1"
REVIEWER_REGISTRY_SCHEMA = "ark-kb-trusted-reviewer-registry/v1"
REGISTRATION_REVIEW_SOURCE_SCHEMA = (
    "ark-kb-registration-review-source/v1"
)
ROLE_REVIEW_SOURCE_SCHEMA = "ark-kb-role-review-source/v1"
READY_TO_FREEZE = "READY_TO_FREEZE"
BLOCKED_BY_INDEPENDENT_REVIEW = "BLOCKED_BY_INDEPENDENT_REVIEW"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PACK_FIELDS = frozenset(
    {
        "schema",
        "packId",
        "kind",
        "authorId",
        "authorKeyFingerprint",
        "createdAt",
        "toolVersion",
        "seed",
        "selectionRule",
        "sourceManifestSha256",
        "candidates",
        "packSha256",
    }
)
_CANDIDATE_FIELDS = frozenset(
    {"caseId", "payload", "candidateSha256"}
)
_REVIEW_FIELDS = frozenset(
    {
        "schema",
        "packId",
        "packSha256",
        "caseId",
        "candidateSha256",
        "reviewerId",
        "reviewerKeyFingerprint",
        "reviewerRole",
        "round",
        "reviewedAt",
        "verdict",
        "answer",
        "evidence",
        "rationale",
        "toolVersion",
        "contentSha256",
    }
)
_EVIDENCE_FIELDS = frozenset(
    {"uri", "sourceRevisionSha256", "freshness"}
)
_REVIEWER_ROLES = frozenset({"REVIEWER", "ADJUDICATOR"})
_VERDICTS = frozenset(
    {"CONFIRMED", "REJECTED", "EXPECTED_GAP", "UNRESOLVED"}
)
_EVIDENCE_FRESHNESS = frozenset(
    {"FRESH", "STALE", "NOT_RECOVERED"}
)
_PACK_KINDS = frozenset({"query", "registration", "role"})
_LEAKAGE_PREFIXES = (
    "expected",
    "route",
    "prediction",
    "confidence",
    "knowledgeroles",
    "currentroles",
    "currentanswer",
)
_REGISTRATION_PAYLOAD_FIELDS = frozenset(
    {
        "ownerUri",
        "targetUri",
        "registrationType",
        "sourceProperty",
        "evidenceUri",
        "sourceKind",
    }
)
_REGISTRATION_SOURCE_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "generatedFromCore",
        "generatedFromClassifier",
        "sourceIdentity",
        "candidates",
    }
)
_REGISTRATION_SOURCE_IDENTITY_FIELDS = frozenset(
    {
        "databaseSchema",
        "generatedAt",
        "sourceInventoryId",
        "sourceFingerprint",
        "sourceRowSetSha256",
        "sourceRowCount",
    }
)
_ROLE_PAYLOAD_FIELDS = frozenset(
    {
        "canonicalUri",
        "assetName",
        "assetClassPath",
        "blueprintKind",
        "parentClassPath",
        "nativeParentClassPath",
        "domain",
        "pluginOrDlc",
        "assetType",
        "ancestryCohort",
        "degreeCohort",
        "identityStatus",
        "identitySourceKind",
        "evidenceFreshness",
        "evidenceUri",
        "selectionCohort",
    }
)
_ROLE_DEGREE_FIELDS = frozenset(
    {
        "referencer",
        "descendant",
        "mapUsage",
        "registryUsage",
        "componentReuse",
        "crossDomain",
    }
)
_ROLE_SOURCE_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "generatedFromCore",
        "generatedFromClassifier",
        "sourceIdentity",
        "candidates",
    }
)
_ROLE_SOURCE_IDENTITY_FIELDS = frozenset(
    {
        "databaseSchema",
        "generatedAt",
        "sourceInventoryId",
        "sourceFingerprint",
        "eligibleRowSetSha256",
        "eligibleRowCount",
        "selectedCandidateCount",
        "seed",
        "candidateLimit",
        "selectionRule",
    }
)
_DEGREE_BUCKETS = frozenset({"ZERO", "LOW", "MEDIUM", "HIGH"})
_EVIDENCE_SOURCE_FRESHNESS = frozenset(
    {"FRESH", "STALE", "NOT_AVAILABLE"}
)
_SUPPORTED_DISCOVERY_SCHEMAS = frozenset(
    {
        "blueprint-to-code-kb-discovery/v1",
        "blueprint-to-code-kb-discovery/v2",
    }
)
_FORBIDDEN_SOURCE_MARKERS = (
    "classifier",
    "kb_vnext_core",
    "semantic_core",
)


class GoldReviewError(ValueError):
    """Raised when a pack or review receipt violates the review contract."""


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise GoldReviewError(
            "review content must be canonical JSON data"
        ) from error


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _required_text(
    value: object,
    *,
    field: str,
) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise GoldReviewError(f"{field} must be a non-empty string")
    return normalized


def _validate_timestamp(value: object, *, field: str) -> str:
    normalized = _required_text(value, field=field)
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as error:
        raise GoldReviewError(
            f"{field} must be an ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None:
        raise GoldReviewError(f"{field} must include a timezone")
    return normalized


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _reject_prediction_leakage(value: object, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = _normalized_key(key)
            if any(
                normalized.startswith(prefix)
                for prefix in _LEAKAGE_PREFIXES
            ):
                raise GoldReviewError(
                    f"prediction leakage field {key!r} at {path}.{key}"
                )
            _reject_prediction_leakage(
                child,
                path=f"{path}.{key}",
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_prediction_leakage(
                child,
                path=f"{path}[{index}]",
            )


def _validate_candidate_payload(
    kind: str,
    payload: Mapping[str, object],
    *,
    path: str,
) -> None:
    _reject_prediction_leakage(payload, path=path)
    if kind == "role":
        if set(payload) != _ROLE_PAYLOAD_FIELDS:
            raise GoldReviewError(
                "role payload fields do not match v1 contract"
            )
        for field in sorted(_ROLE_PAYLOAD_FIELDS - {"degreeCohort"}):
            _required_text(
                payload.get(field),
                field=f"{path}.{field}",
            )
        degree = payload.get("degreeCohort")
        if not isinstance(degree, Mapping) or set(
            degree
        ) != _ROLE_DEGREE_FIELDS:
            raise GoldReviewError(
                "role degree cohort fields do not match v1 contract"
            )
        if any(
            str(value).upper() not in _DEGREE_BUCKETS
            for value in degree.values()
        ):
            raise GoldReviewError("unsupported role degree cohort")
        if (
            str(payload.get("evidenceFreshness") or "").upper()
            not in _EVIDENCE_SOURCE_FRESHNESS
        ):
            raise GoldReviewError(
                "unsupported role source evidence freshness"
            )
        return
    if kind != "registration":
        return
    if set(payload) != _REGISTRATION_PAYLOAD_FIELDS:
        raise GoldReviewError(
            "registration payload fields do not match v1 contract"
        )
    for field in sorted(_REGISTRATION_PAYLOAD_FIELDS):
        _required_text(
            payload.get(field),
            field=f"{path}.{field}",
        )
    source_kind = _normalized_key(payload.get("sourceKind"))
    if any(
        marker in source_kind
        for marker in _FORBIDDEN_SOURCE_MARKERS
    ):
        raise GoldReviewError(
            "registration candidates require an independent source"
        )


def _candidate_identity(candidate: Mapping[str, object]) -> dict[str, object]:
    return {
        "caseId": candidate.get("caseId"),
        "payload": candidate.get("payload"),
    }


def candidate_content_sha256(candidate: Mapping[str, object]) -> str:
    """Return the digest covering one case identity and blind payload."""

    return _sha256_json(_candidate_identity(candidate))


def _pack_identity(pack: Mapping[str, object]) -> dict[str, object]:
    candidates = pack.get("candidates")
    candidate_hashes = (
        [
            candidate.get("candidateSha256")
            for candidate in candidates
            if isinstance(candidate, Mapping)
        ]
        if isinstance(candidates, list)
        else []
    )
    return {
        "kind": pack.get("kind"),
        "authorId": pack.get("authorId"),
        "authorKeyFingerprint": pack.get("authorKeyFingerprint"),
        "seed": pack.get("seed"),
        "selectionRule": pack.get("selectionRule"),
        "sourceManifestSha256": pack.get("sourceManifestSha256"),
        "candidateSha256s": candidate_hashes,
    }


def _pack_content(pack: Mapping[str, object]) -> dict[str, object]:
    return {
        key: copy.deepcopy(value)
        for key, value in pack.items()
        if key != "packSha256"
    }


def pack_content_sha256(pack: Mapping[str, object]) -> str:
    """Return the digest covering a complete pack except its digest field."""

    return _sha256_json(_pack_content(pack))


def review_content_sha256(review: Mapping[str, object]) -> str:
    """Return the digest covering a review receipt except its digest field."""

    return _sha256_json(
        {
            key: copy.deepcopy(value)
            for key, value in review.items()
            if key != "contentSha256"
        }
    )


def _evidence_uris(value: object) -> list[str]:
    found: set[str] = set()

    def visit(current: object) -> None:
        if isinstance(current, Mapping):
            for key, child in current.items():
                if _normalized_key(key) == "evidenceuri":
                    uri = str(child or "").strip()
                    if uri:
                        found.add(uri)
                else:
                    visit(child)
        elif isinstance(current, list):
            for child in current:
                visit(child)

    visit(value)
    return sorted(found)


def query_candidate_from_gold_case(
    raw_case: Mapping[str, object],
) -> dict[str, object]:
    """Project one fixed query case into a prediction-free review candidate."""

    if not isinstance(raw_case, Mapping):
        raise GoldReviewError("query gold case must be an object")
    case_id = _required_text(raw_case.get("id"), field="query case id")
    requirements = raw_case.get("requirements")
    expected = raw_case.get("expected")
    if not isinstance(requirements, Mapping):
        raise GoldReviewError(
            f"query case {case_id} requirements must be an object"
        )
    if not isinstance(expected, Mapping):
        raise GoldReviewError(
            f"query case {case_id} expected must be an object"
        )
    payload = {
        "question": _required_text(
            raw_case.get("question"),
            field=f"query case {case_id} question",
        ),
        "category": _required_text(
            raw_case.get("category"),
            field=f"query case {case_id} category",
        ),
        "primaryDomain": _required_text(
            raw_case.get("primaryDomain"),
            field=f"query case {case_id} primaryDomain",
        ),
        "entity": _required_text(
            raw_case.get("entity"),
            field=f"query case {case_id} entity",
        ),
        "requirements": copy.deepcopy(dict(requirements)),
        "evidenceUris": _evidence_uris(expected),
    }
    _reject_prediction_leakage(
        payload,
        path=f"queryCase[{case_id}]",
    )
    return {"caseId": case_id, "payload": payload}


def _sqlite_read_only(path: Path) -> sqlite3.Connection:
    try:
        resolved = path.resolve(strict=True)
        connection = sqlite3.connect(
            f"{resolved.as_uri()}?mode=ro",
            uri=True,
        )
    except (OSError, sqlite3.Error) as error:
        raise GoldReviewError(
            f"cannot open Discovery database read-only: {path}"
        ) from error
    connection.row_factory = sqlite3.Row
    return connection


def _discovery_metadata(
    connection: sqlite3.Connection,
) -> dict[str, str]:
    try:
        rows = connection.execute(
            "SELECT key, value FROM metadata ORDER BY key"
        ).fetchall()
    except sqlite3.Error as error:
        raise GoldReviewError(
            "Discovery database metadata table is unavailable"
        ) from error
    metadata = {str(row["key"]): str(row["value"]) for row in rows}
    if metadata.get("schema") not in _SUPPORTED_DISCOVERY_SCHEMAS:
        raise GoldReviewError("unsupported Discovery database schema")
    _validate_timestamp(
        metadata.get("generated_at_utc"),
        field="Discovery generated_at_utc",
    )
    return metadata


def registration_review_source_from_sqlite(
    database_path: Path,
) -> dict[str, object]:
    """Extract blind typed registrations from a read-only Discovery DB."""

    connection = _sqlite_read_only(database_path)
    try:
        metadata = _discovery_metadata(connection)
        try:
            inventory = connection.execute(
                """
                SELECT source_id, source_fingerprint
                FROM source_inventory
                WHERE source_id = ?
                """,
                ("source://existing-knowledge-databases",),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT
                    registration_id,
                    owner_object_path,
                    registration_type,
                    target_object_path,
                    source_property,
                    source_evidence_id,
                    confidence,
                    source_kind
                FROM system_registrations
                ORDER BY registration_id
                """
            ).fetchall()
        except sqlite3.Error as error:
            raise GoldReviewError(
                "Discovery typed-registration source is unavailable"
            ) from error
    finally:
        connection.close()
    if inventory is None or not _is_sha256(
        inventory["source_fingerprint"]
    ):
        raise GoldReviewError(
            "Discovery registration source fingerprint is unavailable"
        )
    if not rows:
        raise GoldReviewError(
            "Discovery database has no typed registrations"
        )

    raw_rows: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []
    for row in rows:
        raw_row = {
            key: row[key]
            for key in (
                "registration_id",
                "owner_object_path",
                "registration_type",
                "target_object_path",
                "source_property",
                "source_evidence_id",
                "confidence",
                "source_kind",
            )
        }
        raw_rows.append(raw_row)
        candidates.append(
            {
                "caseId": _required_text(
                    row["registration_id"],
                    field="registration_id",
                ),
                "payload": {
                    "ownerUri": _required_text(
                        row["owner_object_path"],
                        field="owner_object_path",
                    ),
                    "targetUri": _required_text(
                        row["target_object_path"],
                        field="target_object_path",
                    ),
                    "registrationType": _required_text(
                        row["registration_type"],
                        field="registration_type",
                    ),
                    "sourceProperty": _required_text(
                        row["source_property"],
                        field="source_property",
                    ),
                    "evidenceUri": _required_text(
                        row["source_evidence_id"],
                        field="source_evidence_id",
                    ),
                    "sourceKind": _required_text(
                        row["source_kind"],
                        field="source_kind",
                    ),
                },
            }
        )

    source_manifest: dict[str, object] = {
        "schema": REGISTRATION_REVIEW_SOURCE_SCHEMA,
        "kind": "registration",
        "generatedFromCore": False,
        "generatedFromClassifier": False,
        "sourceIdentity": {
            "databaseSchema": metadata["schema"],
            "generatedAt": metadata["generated_at_utc"],
            "sourceInventoryId": str(inventory["source_id"]),
            "sourceFingerprint": str(inventory["source_fingerprint"]),
            "sourceRowSetSha256": _sha256_json(raw_rows),
            "sourceRowCount": len(raw_rows),
        },
        "candidates": candidates,
    }
    return validate_registration_review_source(source_manifest)


def validate_registration_review_source(
    source_manifest: Mapping[str, object],
) -> dict[str, object]:
    """Validate an independently sourced blind registration manifest."""

    if (
        not isinstance(source_manifest, Mapping)
        or set(source_manifest) != _REGISTRATION_SOURCE_FIELDS
        or source_manifest.get("schema")
        != REGISTRATION_REVIEW_SOURCE_SCHEMA
        or source_manifest.get("kind") != "registration"
    ):
        raise GoldReviewError(
            "registration review source fields do not match v1 contract"
        )
    if (
        source_manifest.get("generatedFromCore") is not False
        or source_manifest.get("generatedFromClassifier") is not False
    ):
        raise GoldReviewError(
            "registration candidates require an independent source"
        )
    identity = source_manifest.get("sourceIdentity")
    if (
        not isinstance(identity, Mapping)
        or set(identity) != _REGISTRATION_SOURCE_IDENTITY_FIELDS
        or identity.get("databaseSchema")
        not in _SUPPORTED_DISCOVERY_SCHEMAS
    ):
        raise GoldReviewError(
            "registration source identity is malformed"
        )
    _validate_timestamp(
        identity.get("generatedAt"),
        field="registration source generatedAt",
    )
    _required_text(
        identity.get("sourceInventoryId"),
        field="registration source inventory ID",
    )
    for field in ("sourceFingerprint", "sourceRowSetSha256"):
        if not _is_sha256(identity.get(field)):
            raise GoldReviewError(
                f"registration source {field} must be SHA-256"
            )
    row_count = identity.get("sourceRowCount")
    candidates = source_manifest.get("candidates")
    if (
        isinstance(row_count, bool)
        or not isinstance(row_count, int)
        or row_count < 1
        or not isinstance(candidates, list)
        or len(candidates) != row_count
    ):
        raise GoldReviewError(
            "registration source row count does not match candidates"
        )
    case_ids: set[str] = set()
    for index, candidate in enumerate(candidates):
        if (
            not isinstance(candidate, Mapping)
            or set(candidate) != {"caseId", "payload"}
            or not isinstance(candidate.get("payload"), Mapping)
        ):
            raise GoldReviewError(
                "registration source candidate is malformed"
            )
        case_id = _required_text(
            candidate.get("caseId"),
            field=f"registration candidate {index + 1} caseId",
        )
        if case_id in case_ids:
            raise GoldReviewError(
                f"duplicate registration caseId: {case_id}"
            )
        _validate_candidate_payload(
            "registration",
            candidate["payload"],
            path=f"registrationSource.candidates[{index}].payload",
        )
        case_ids.add(case_id)
    return copy.deepcopy(dict(source_manifest))


def build_registration_review_pack(
    *,
    source_manifest: Mapping[str, object],
    author_id: str,
    author_key_fingerprint: str,
    seed: str,
    created_at: str,
    tool_version: str,
    limit: int = 120,
) -> dict[str, object]:
    """Build a blind pack from independent typed registration rows."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise GoldReviewError("registration candidate limit must be positive")
    normalized_source = validate_registration_review_source(source_manifest)
    candidates = list(normalized_source["candidates"])
    candidates.sort(
        key=lambda candidate: (
            hashlib.sha256(
                (
                    f"{seed}\0{candidate['caseId']}"
                ).encode("utf-8")
            ).hexdigest(),
            str(candidate["caseId"]),
        )
    )
    selected = candidates[:limit]
    return build_review_pack(
        kind="registration",
        author_id=author_id,
        author_key_fingerprint=author_key_fingerprint,
        seed=seed,
        selection_rule=(
            "INDEPENDENT_TYPED_REGISTRATIONS_STABLE_HASH_V1_"
            f"LIMIT_{limit}"
        ),
        source_manifest_sha256=_sha256_json(normalized_source),
        candidates=selected,
        created_at=created_at,
        tool_version=tool_version,
    )


def _degree_bucket(value: object) -> str:
    if isinstance(value, bool):
        count = 0
    else:
        try:
            count = max(0, int(value or 0))
        except (TypeError, ValueError):
            count = 0
    if count == 0:
        return "ZERO"
    if count <= 4:
        return "LOW"
    if count <= 24:
        return "MEDIUM"
    return "HIGH"


def _role_asset_type(row: Mapping[str, object]) -> str:
    ordered_flags = (
        ("is_map", "MAP"),
        ("is_data_table", "DATA_TABLE"),
        ("is_data_asset", "DATA_ASSET"),
        ("is_function_library", "FUNCTION_LIBRARY"),
        ("is_blueprint_interface", "BLUEPRINT_INTERFACE"),
        ("is_user_defined_struct", "USER_DEFINED_STRUCT"),
        ("is_user_defined_enum", "USER_DEFINED_ENUM"),
        ("is_blueprint", "BLUEPRINT"),
    )
    for field, label in ordered_flags:
        if row.get(field) == 1:
            return label
    asset_class = str(row.get("asset_class_path") or "")
    if any(
        marker in asset_class
        for marker in (
            "Texture",
            "Material",
            "StaticMesh",
            "SkeletalMesh",
            "Niagara",
            "Particle",
        )
    ):
        return "VISUAL_ASSET"
    if any(
        marker in asset_class
        for marker in ("Sound", "Audio", "Dialogue")
    ):
        return "AUDIO_ASSET"
    return "OTHER"


def _role_ancestry_cohort(row: Mapping[str, object]) -> str:
    for field in ("native_parent_class_path", "parent_class_path"):
        value = str(row.get(field) or "").strip()
        if value and value != "UNKNOWN" and value.startswith("/Script/"):
            return value
    return _required_text(
        row.get("asset_class_path"),
        field="role asset class path",
    )


def _role_candidate_from_asset(
    row: Mapping[str, object],
) -> tuple[dict[str, object], str]:
    object_path = _required_text(
        row.get("object_path"),
        field="role object_path",
    )
    degree = {
        "referencer": _degree_bucket(row.get("referencer_count")),
        "descendant": _degree_bucket(row.get("descendant_count")),
        "mapUsage": _degree_bucket(row.get("map_usage_count")),
        "registryUsage": _degree_bucket(row.get("registry_usage_count")),
        "componentReuse": _degree_bucket(
            row.get("component_reuse_count")
        ),
        "crossDomain": _degree_bucket(
            row.get("cross_domain_reference_count")
        ),
    }
    total_degree = sum(
        max(0, int(row.get(field) or 0))
        for field in (
            "referencer_count",
            "descendant_count",
            "map_usage_count",
            "registry_usage_count",
            "component_reuse_count",
            "cross_domain_reference_count",
        )
    )
    asset_type = _role_asset_type(row)
    ancestry = _role_ancestry_cohort(row)
    domain = _required_text(
        row.get("top_folder"),
        field="role top_folder",
    )
    stratum = "\0".join(
        (
            asset_type,
            domain,
            _degree_bucket(total_degree),
            ancestry,
        )
    )
    cohort = (
        "cohort-"
        + hashlib.sha256(stratum.encode("utf-8")).hexdigest()[:16]
    )
    evidence_freshness = _required_text(
        row.get("evidence_freshness"),
        field="role evidence_freshness",
    ).upper()
    if evidence_freshness == "SOURCE_NOT_AVAILABLE":
        evidence_freshness = "NOT_AVAILABLE"
    payload = {
        "canonicalUri": object_path,
        "assetName": _required_text(
            row.get("asset_name"),
            field="role asset_name",
        ),
        "assetClassPath": _required_text(
            row.get("asset_class_path"),
            field="role asset_class_path",
        ),
        "blueprintKind": _required_text(
            row.get("blueprint_kind"),
            field="role blueprint_kind",
        ),
        "parentClassPath": _required_text(
            row.get("parent_class_path"),
            field="role parent_class_path",
        ),
        "nativeParentClassPath": _required_text(
            row.get("native_parent_class_path"),
            field="role native_parent_class_path",
        ),
        "domain": domain,
        "pluginOrDlc": _required_text(
            row.get("plugin_or_dlc"),
            field="role plugin_or_dlc",
        ),
        "assetType": asset_type,
        "ancestryCohort": ancestry,
        "degreeCohort": degree,
        "identityStatus": _required_text(
            row.get("identity_status"),
            field="role identity_status",
        ),
        "identitySourceKind": _required_text(
            row.get("identity_source_kind"),
            field="role identity_source_kind",
        ),
        "evidenceFreshness": evidence_freshness,
        "evidenceUri": (
            "discovery://assets/"
            + hashlib.sha256(object_path.encode("utf-8")).hexdigest()
        ),
        "selectionCohort": cohort,
    }
    _validate_candidate_payload(
        "role",
        payload,
        path=f"roleAsset[{object_path}]",
    )
    return {"caseId": object_path, "payload": payload}, stratum


def role_review_source_from_sqlite(
    database_path: Path,
    *,
    seed: str,
    limit: int = 360,
) -> dict[str, object]:
    """Select role candidates from observable independent Discovery fields."""

    normalized_seed = _required_text(seed, field="role seed")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise GoldReviewError("role candidate limit must be positive")
    connection = _sqlite_read_only(database_path)
    try:
        metadata = _discovery_metadata(connection)
        try:
            inventory = connection.execute(
                """
                SELECT source_id, source_fingerprint
                FROM source_inventory
                WHERE source_id = ?
                """,
                ("source://filesystem-inventory",),
            ).fetchone()
            cursor = connection.execute(
                """
                SELECT
                    object_path,
                    asset_name,
                    asset_class_path,
                    blueprint_kind,
                    parent_class_path,
                    native_parent_class_path,
                    top_folder,
                    plugin_or_dlc,
                    is_blueprint,
                    is_map,
                    is_data_asset,
                    is_data_table,
                    is_function_library,
                    is_blueprint_interface,
                    is_user_defined_struct,
                    is_user_defined_enum,
                    identity_status,
                    identity_source_kind,
                    evidence_freshness,
                    referencer_count,
                    descendant_count,
                    map_usage_count,
                    registry_usage_count,
                    component_reuse_count,
                    cross_domain_reference_count
                FROM assets
                WHERE identity_status = 'EXTRACTED'
                  AND object_path <> ''
                ORDER BY object_path
                """
            )
        except sqlite3.Error as error:
            raise GoldReviewError(
                "Discovery observable role source is unavailable"
            ) from error
        if inventory is None or not _is_sha256(
            inventory["source_fingerprint"]
        ):
            raise GoldReviewError(
                "Discovery role source fingerprint is unavailable"
            )

        row_set_hasher = hashlib.sha256()
        eligible_count = 0
        best_by_stratum: dict[
            str,
            tuple[int, str, dict[str, object]],
        ] = {}
        global_best: list[
            tuple[int, str, dict[str, object]]
        ] = []
        for sqlite_row in cursor:
            raw_row = {key: sqlite_row[key] for key in sqlite_row.keys()}
            row_set_hasher.update(_canonical_json_bytes(raw_row))
            row_set_hasher.update(b"\n")
            candidate, stratum = _role_candidate_from_asset(raw_row)
            case_id = str(candidate["caseId"])
            rank = int(
                hashlib.sha256(
                    f"{normalized_seed}\0{case_id}".encode("utf-8")
                ).hexdigest(),
                16,
            )
            observed = best_by_stratum.get(stratum)
            if observed is None or (rank, case_id) < (
                observed[0],
                observed[1],
            ):
                best_by_stratum[stratum] = (
                    rank,
                    case_id,
                    candidate,
                )
            heapq.heappush(
                global_best,
                (-rank, case_id, candidate),
            )
            if len(global_best) > limit:
                heapq.heappop(global_best)
            eligible_count += 1
    finally:
        connection.close()
    if eligible_count < 1:
        raise GoldReviewError(
            "Discovery database has no eligible role entities"
        )

    stratified = sorted(
        best_by_stratum.items(),
        key=lambda item: (
            hashlib.sha256(
                f"{normalized_seed}\0{item[0]}".encode("utf-8")
            ).hexdigest(),
            item[1][0],
            item[1][1],
        ),
    )
    selected: list[dict[str, object]] = [
        value[2] for _, value in stratified[:limit]
    ]
    selected_ids = {str(item["caseId"]) for item in selected}
    if len(selected) < min(limit, eligible_count):
        fallback = sorted(
            (
                (-negative_rank, case_id, candidate)
                for negative_rank, case_id, candidate in global_best
            ),
            key=lambda item: (item[0], item[1]),
        )
        for _, case_id, candidate in fallback:
            if case_id in selected_ids:
                continue
            selected.append(candidate)
            selected_ids.add(case_id)
            if len(selected) >= min(limit, eligible_count):
                break

    selection_rule = (
        "INDEPENDENT_DISCOVERY_OBSERVABLE_STRATIFIED_V1_"
        f"LIMIT_{limit}"
    )
    source_manifest: dict[str, object] = {
        "schema": ROLE_REVIEW_SOURCE_SCHEMA,
        "kind": "role",
        "generatedFromCore": False,
        "generatedFromClassifier": False,
        "sourceIdentity": {
            "databaseSchema": metadata["schema"],
            "generatedAt": metadata["generated_at_utc"],
            "sourceInventoryId": str(inventory["source_id"]),
            "sourceFingerprint": str(inventory["source_fingerprint"]),
            "eligibleRowSetSha256": row_set_hasher.hexdigest(),
            "eligibleRowCount": eligible_count,
            "selectedCandidateCount": len(selected),
            "seed": normalized_seed,
            "candidateLimit": limit,
            "selectionRule": selection_rule,
        },
        "candidates": selected,
    }
    return validate_role_review_source(source_manifest)


def validate_role_review_source(
    source_manifest: Mapping[str, object],
) -> dict[str, object]:
    """Validate an independent observable role-candidate manifest."""

    if (
        not isinstance(source_manifest, Mapping)
        or set(source_manifest) != _ROLE_SOURCE_FIELDS
        or source_manifest.get("schema") != ROLE_REVIEW_SOURCE_SCHEMA
        or source_manifest.get("kind") != "role"
    ):
        raise GoldReviewError(
            "role review source fields do not match v1 contract"
        )
    if (
        source_manifest.get("generatedFromCore") is not False
        or source_manifest.get("generatedFromClassifier") is not False
    ):
        raise GoldReviewError(
            "role candidates require an independent source"
        )
    identity = source_manifest.get("sourceIdentity")
    if (
        not isinstance(identity, Mapping)
        or set(identity) != _ROLE_SOURCE_IDENTITY_FIELDS
        or identity.get("databaseSchema")
        not in _SUPPORTED_DISCOVERY_SCHEMAS
    ):
        raise GoldReviewError("role source identity is malformed")
    _validate_timestamp(
        identity.get("generatedAt"),
        field="role source generatedAt",
    )
    _required_text(
        identity.get("sourceInventoryId"),
        field="role source inventory ID",
    )
    _required_text(identity.get("seed"), field="role source seed")
    _required_text(
        identity.get("selectionRule"),
        field="role source selection rule",
    )
    for field in ("sourceFingerprint", "eligibleRowSetSha256"):
        if not _is_sha256(identity.get(field)):
            raise GoldReviewError(f"role source {field} must be SHA-256")
    eligible_count = identity.get("eligibleRowCount")
    selected_count = identity.get("selectedCandidateCount")
    candidate_limit = identity.get("candidateLimit")
    candidates = source_manifest.get("candidates")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in (eligible_count, selected_count, candidate_limit)
    ):
        raise GoldReviewError("role source counts must be positive")
    if (
        not isinstance(candidates, list)
        or len(candidates) != selected_count
        or selected_count > eligible_count
        or selected_count > candidate_limit
    ):
        raise GoldReviewError(
            "role source counts do not match candidates"
        )
    expected_rule = (
        "INDEPENDENT_DISCOVERY_OBSERVABLE_STRATIFIED_V1_"
        f"LIMIT_{candidate_limit}"
    )
    if identity.get("selectionRule") != expected_rule:
        raise GoldReviewError("role source selection rule is malformed")
    case_ids: set[str] = set()
    for index, candidate in enumerate(candidates):
        if (
            not isinstance(candidate, Mapping)
            or set(candidate) != {"caseId", "payload"}
            or not isinstance(candidate.get("payload"), Mapping)
        ):
            raise GoldReviewError("role source candidate is malformed")
        case_id = _required_text(
            candidate.get("caseId"),
            field=f"role candidate {index + 1} caseId",
        )
        if case_id in case_ids:
            raise GoldReviewError(f"duplicate role caseId: {case_id}")
        _validate_candidate_payload(
            "role",
            candidate["payload"],
            path=f"roleSource.candidates[{index}].payload",
        )
        if candidate["payload"].get("canonicalUri") != case_id:
            raise GoldReviewError(
                "role caseId must match canonicalUri"
            )
        case_ids.add(case_id)
    return copy.deepcopy(dict(source_manifest))


def build_role_review_pack(
    *,
    source_manifest: Mapping[str, object],
    author_id: str,
    author_key_fingerprint: str,
    seed: str,
    created_at: str,
    tool_version: str,
) -> dict[str, object]:
    """Build a blind role pack from observable independent identity data."""

    normalized_source = validate_role_review_source(source_manifest)
    identity = normalized_source["sourceIdentity"]
    normalized_seed = _required_text(seed, field="seed")
    if identity["seed"] != normalized_seed:
        raise GoldReviewError(
            "role pack seed does not match source selection seed"
        )
    candidates = list(normalized_source["candidates"])
    candidates.sort(
        key=lambda candidate: (
            hashlib.sha256(
                (
                    f"{normalized_seed}\0{candidate['caseId']}"
                ).encode("utf-8")
            ).hexdigest(),
            str(candidate["caseId"]),
        )
    )
    return build_review_pack(
        kind="role",
        author_id=author_id,
        author_key_fingerprint=author_key_fingerprint,
        seed=normalized_seed,
        selection_rule=str(identity["selectionRule"]),
        source_manifest_sha256=_sha256_json(normalized_source),
        candidates=candidates,
        created_at=created_at,
        tool_version=tool_version,
    )


def build_review_pack(
    *,
    kind: str,
    author_id: str,
    author_key_fingerprint: str,
    seed: str,
    selection_rule: str,
    source_manifest_sha256: str,
    candidates: Sequence[Mapping[str, object]],
    created_at: str,
    tool_version: str,
) -> dict[str, object]:
    """Build one deterministic prediction-free review pack."""

    normalized_kind = _required_text(kind, field="kind").casefold()
    if normalized_kind not in _PACK_KINDS:
        raise GoldReviewError(
            f"unsupported review kind: {normalized_kind}"
        )
    normalized_author = _required_text(author_id, field="authorId")
    normalized_author_key = _required_text(
        author_key_fingerprint,
        field="authorKeyFingerprint",
    )
    normalized_seed = _required_text(seed, field="seed")
    normalized_rule = _required_text(
        selection_rule,
        field="selectionRule",
    )
    normalized_tool = _required_text(
        tool_version,
        field="toolVersion",
    )
    normalized_created_at = _validate_timestamp(
        created_at,
        field="createdAt",
    )
    if not _is_sha256(source_manifest_sha256):
        raise GoldReviewError(
            "sourceManifestSha256 must be 64 lowercase hex digits"
        )
    if not candidates:
        raise GoldReviewError("review pack requires at least one candidate")

    normalized_candidates: list[dict[str, object]] = []
    case_ids: set[str] = set()
    for index, raw_candidate in enumerate(candidates):
        if not isinstance(raw_candidate, Mapping):
            raise GoldReviewError(
                f"candidate {index + 1} must be an object"
            )
        if set(raw_candidate) != {"caseId", "payload"}:
            raise GoldReviewError(
                "candidate input must contain only caseId and payload"
            )
        case_id = _required_text(
            raw_candidate.get("caseId"),
            field=f"candidate {index + 1} caseId",
        )
        if case_id in case_ids:
            raise GoldReviewError(f"duplicate caseId: {case_id}")
        payload = raw_candidate.get("payload")
        if not isinstance(payload, Mapping):
            raise GoldReviewError(
                f"candidate {case_id} payload must be an object"
            )
        _validate_candidate_payload(
            normalized_kind,
            payload,
            path=f"candidates[{index}].payload",
        )
        candidate = {
            "caseId": case_id,
            "payload": copy.deepcopy(dict(payload)),
        }
        candidate["candidateSha256"] = candidate_content_sha256(candidate)
        normalized_candidates.append(candidate)
        case_ids.add(case_id)

    pack: dict[str, object] = {
        "schema": PACK_SCHEMA,
        "kind": normalized_kind,
        "authorId": normalized_author,
        "authorKeyFingerprint": normalized_author_key,
        "createdAt": normalized_created_at,
        "toolVersion": normalized_tool,
        "seed": normalized_seed,
        "selectionRule": normalized_rule,
        "sourceManifestSha256": source_manifest_sha256,
        "candidates": normalized_candidates,
    }
    pack["packId"] = (
        f"{normalized_kind}-{_sha256_json(_pack_identity(pack))[:16]}"
    )
    pack["packSha256"] = pack_content_sha256(pack)
    validate_review_pack(pack)
    return pack


def build_query_review_pack(
    *,
    gold_set_path: Path,
    author_id: str,
    author_key_fingerprint: str,
    seed: str,
    created_at: str,
    tool_version: str,
) -> dict[str, object]:
    """Export every manually fixed query as a deterministic blind candidate."""

    try:
        source_bytes = gold_set_path.read_bytes()
        raw = json.loads(source_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GoldReviewError(
            f"cannot read query gold set: {gold_set_path}"
        ) from error
    if (
        not isinstance(raw, Mapping)
        or raw.get("schema") != "ark-kb-query-gold-set/v1"
        or raw.get("selectionMode") != "MANUAL_FIXED"
        or raw.get("generatedFromCore") is not False
    ):
        raise GoldReviewError(
            "query review export requires the manually fixed gold corpus"
        )
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise GoldReviewError("query gold corpus requires cases")
    candidates = [
        query_candidate_from_gold_case(raw_case)
        for raw_case in raw_cases
        if isinstance(raw_case, Mapping)
    ]
    if len(candidates) != len(raw_cases):
        raise GoldReviewError("query gold corpus contains a malformed case")
    candidates.sort(
        key=lambda candidate: (
            hashlib.sha256(
                (
                    f"{seed}\0{candidate['caseId']}"
                ).encode("utf-8")
            ).hexdigest(),
            str(candidate["caseId"]),
        )
    )
    return build_review_pack(
        kind="query",
        author_id=author_id,
        author_key_fingerprint=author_key_fingerprint,
        seed=seed,
        selection_rule="MANUAL_FIXED_ALL_CASES",
        source_manifest_sha256=hashlib.sha256(source_bytes).hexdigest(),
        candidates=candidates,
        created_at=created_at,
        tool_version=tool_version,
    )


def validate_review_pack(
    pack: Mapping[str, object],
) -> dict[str, object]:
    """Validate pack shape, blindness, identity, and content hashes."""

    if not isinstance(pack, Mapping):
        raise GoldReviewError("review pack must be an object")
    if set(pack) != _PACK_FIELDS:
        raise GoldReviewError("review pack fields do not match v1 contract")
    if pack.get("schema") != PACK_SCHEMA:
        raise GoldReviewError("unexpected review pack schema")
    kind = _required_text(pack.get("kind"), field="kind").casefold()
    if kind not in _PACK_KINDS:
        raise GoldReviewError(f"unsupported review kind: {kind}")
    _required_text(pack.get("authorId"), field="authorId")
    _required_text(
        pack.get("authorKeyFingerprint"),
        field="authorKeyFingerprint",
    )
    _required_text(pack.get("seed"), field="seed")
    _required_text(pack.get("selectionRule"), field="selectionRule")
    _required_text(pack.get("toolVersion"), field="toolVersion")
    _validate_timestamp(pack.get("createdAt"), field="createdAt")
    if not _is_sha256(pack.get("sourceManifestSha256")):
        raise GoldReviewError(
            "sourceManifestSha256 must be 64 lowercase hex digits"
        )
    candidates = pack.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise GoldReviewError("review pack requires candidates")

    case_ids: set[str] = set()
    candidate_hashes: set[str] = set()
    for index, candidate in enumerate(candidates):
        if (
            not isinstance(candidate, Mapping)
            or set(candidate) != _CANDIDATE_FIELDS
        ):
            raise GoldReviewError(
                f"candidate {index + 1} fields do not match v1 contract"
            )
        case_id = _required_text(
            candidate.get("caseId"),
            field=f"candidate {index + 1} caseId",
        )
        if case_id in case_ids:
            raise GoldReviewError(f"duplicate caseId: {case_id}")
        payload = candidate.get("payload")
        if not isinstance(payload, Mapping):
            raise GoldReviewError(
                f"candidate {case_id} payload must be an object"
            )
        _validate_candidate_payload(
            kind,
            payload,
            path=f"candidates[{index}].payload",
        )
        observed_hash = candidate.get("candidateSha256")
        expected_hash = candidate_content_sha256(candidate)
        if observed_hash != expected_hash:
            raise GoldReviewError(
                f"candidate SHA-256 mismatch for {case_id}"
            )
        if observed_hash in candidate_hashes:
            raise GoldReviewError(
                f"duplicate candidate SHA-256 for {case_id}"
            )
        case_ids.add(case_id)
        candidate_hashes.add(str(observed_hash))

    expected_pack_id = (
        f"{kind}-{_sha256_json(_pack_identity(pack))[:16]}"
    )
    if pack.get("packId") != expected_pack_id:
        raise GoldReviewError("review pack ID does not match candidates")
    if pack.get("packSha256") != pack_content_sha256(pack):
        raise GoldReviewError("review pack SHA-256 mismatch")
    return copy.deepcopy(dict(pack))


def load_trusted_reviewer_registry(path: Path) -> dict[str, str]:
    """Load a human-managed reviewer ID to key-fingerprint registry."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GoldReviewError(
            f"cannot read trusted reviewer registry: {path}"
        ) from error
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"schema", "reviewers"}
        or raw.get("schema") != REVIEWER_REGISTRY_SCHEMA
        or not isinstance(raw.get("reviewers"), list)
    ):
        raise GoldReviewError("trusted reviewer registry is malformed")
    reviewers: dict[str, str] = {}
    key_owners: dict[str, str] = {}
    for item in raw["reviewers"]:
        if (
            not isinstance(item, Mapping)
            or set(item)
            != {"reviewerId", "reviewerKeyFingerprint"}
        ):
            raise GoldReviewError(
                "trusted reviewer registry entry is malformed"
            )
        reviewer_id = _required_text(
            item.get("reviewerId"),
            field="trusted reviewerId",
        )
        reviewer_key = _required_text(
            item.get("reviewerKeyFingerprint"),
            field="trusted reviewerKeyFingerprint",
        )
        if reviewer_id in reviewers:
            raise GoldReviewError(
                f"duplicate trusted reviewerId: {reviewer_id}"
            )
        if reviewer_key in key_owners:
            raise GoldReviewError(
                "trusted reviewer key fingerprint is not unique"
            )
        reviewers[reviewer_id] = reviewer_key
        key_owners[reviewer_key] = reviewer_id
    if not reviewers:
        raise GoldReviewError("trusted reviewer registry is empty")
    return reviewers


def _validate_evidence(
    evidence: object,
    *,
    verdict: str,
) -> None:
    if not isinstance(evidence, list) or not evidence:
        raise GoldReviewError("review requires at least one evidence item")
    freshness_values: list[str] = []
    for index, item in enumerate(evidence):
        if (
            not isinstance(item, Mapping)
            or set(item) != _EVIDENCE_FIELDS
        ):
            raise GoldReviewError(
                f"evidence {index + 1} fields do not match v1 contract"
            )
        _required_text(item.get("uri"), field=f"evidence {index + 1} uri")
        if not _is_sha256(item.get("sourceRevisionSha256")):
            raise GoldReviewError(
                "evidence sourceRevisionSha256 must be 64 lowercase "
                "hex digits"
            )
        freshness = str(item.get("freshness") or "").upper()
        if freshness not in _EVIDENCE_FRESHNESS:
            raise GoldReviewError("unsupported evidence freshness")
        freshness_values.append(freshness)
    if verdict == "CONFIRMED" and any(
        value != "FRESH" for value in freshness_values
    ):
        raise GoldReviewError(
            "confirmed review requires fresh evidence"
        )


def _validate_review_receipt(
    pack: Mapping[str, object],
    review: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(review, Mapping) or set(review) != _REVIEW_FIELDS:
        raise GoldReviewError(
            "review receipt fields do not match v1 contract"
        )
    if review.get("schema") != REVIEW_SCHEMA:
        raise GoldReviewError("unexpected review receipt schema")
    if (
        review.get("packId") != pack.get("packId")
        or review.get("packSha256") != pack.get("packSha256")
    ):
        raise GoldReviewError("review receipt pack identity mismatch")

    candidates = {
        str(candidate["caseId"]): candidate
        for candidate in pack["candidates"]
        if isinstance(candidate, Mapping)
    }
    case_id = _required_text(review.get("caseId"), field="caseId")
    candidate = candidates.get(case_id)
    if candidate is None:
        raise GoldReviewError(f"unknown review caseId: {case_id}")
    if review.get("candidateSha256") != candidate.get(
        "candidateSha256"
    ):
        raise GoldReviewError("review candidate SHA-256 mismatch")

    reviewer_id = _required_text(
        review.get("reviewerId"),
        field="reviewerId",
    )
    reviewer_key = _required_text(
        review.get("reviewerKeyFingerprint"),
        field="reviewerKeyFingerprint",
    )
    if (
        reviewer_id == pack.get("authorId")
        or reviewer_key == pack.get("authorKeyFingerprint")
    ):
        raise GoldReviewError("author cannot review own case")
    role = str(review.get("reviewerRole") or "").upper()
    if role not in _REVIEWER_ROLES:
        raise GoldReviewError("unsupported reviewer role")
    round_number = review.get("round")
    if (
        isinstance(round_number, bool)
        or not isinstance(round_number, int)
        or round_number < 1
    ):
        raise GoldReviewError("review round must be a positive integer")
    _validate_timestamp(review.get("reviewedAt"), field="reviewedAt")
    verdict = str(review.get("verdict") or "").upper()
    if verdict not in _VERDICTS:
        raise GoldReviewError("unsupported review verdict")
    if not isinstance(review.get("answer"), Mapping):
        raise GoldReviewError("review answer must be an object")
    _validate_evidence(review.get("evidence"), verdict=verdict)
    _required_text(review.get("rationale"), field="rationale")
    _required_text(review.get("toolVersion"), field="toolVersion")
    if review.get("contentSha256") != review_content_sha256(review):
        raise GoldReviewError("review content SHA-256 mismatch")
    return copy.deepcopy(dict(review))


def _review_answer_identity(review: Mapping[str, object]) -> str:
    return _sha256_json(
        {
            "verdict": str(review.get("verdict") or "").upper(),
            "answer": review.get("answer"),
        }
    )


def validate_review_set(
    pack: Mapping[str, object],
    reviews: Sequence[Mapping[str, object]],
    *,
    trusted_reviewers: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Validate independent review rounds without creating production gold."""

    normalized_pack = validate_review_pack(pack)
    normalized_reviews = [
        _validate_review_receipt(normalized_pack, review)
        for review in reviews
    ]

    receipt_keys: set[tuple[str, str, str, int]] = set()
    reviewer_keys: dict[str, str] = {}
    key_reviewers: dict[str, str] = {}
    for review in normalized_reviews:
        reviewer_id = str(review["reviewerId"])
        reviewer_key = str(review["reviewerKeyFingerprint"])
        receipt_key = (
            str(review["caseId"]),
            reviewer_id,
            str(review["reviewerRole"]),
            int(review["round"]),
        )
        if receipt_key in receipt_keys:
            raise GoldReviewError("duplicate review receipt")
        receipt_keys.add(receipt_key)
        previous_key = reviewer_keys.setdefault(reviewer_id, reviewer_key)
        if previous_key != reviewer_key:
            raise GoldReviewError(
                "reviewer ID uses multiple key fingerprints"
            )
        previous_reviewer = key_reviewers.setdefault(
            reviewer_key,
            reviewer_id,
        )
        if previous_reviewer != reviewer_id:
            raise GoldReviewError(
                "reviewer key fingerprint reused by multiple reviewer IDs"
            )
        if trusted_reviewers is not None:
            trusted_key = trusted_reviewers.get(reviewer_id)
            if trusted_key != reviewer_key:
                raise GoldReviewError(
                    f"untrusted reviewer identity: {reviewer_id}"
                )

    by_case: dict[str, list[dict[str, object]]] = defaultdict(list)
    for review in normalized_reviews:
        by_case[str(review["caseId"])].append(review)

    gaps: set[str] = set()
    reviewed_cases = 0
    if trusted_reviewers is None:
        gaps.add("TRUSTED_REVIEWER_REGISTRY_REQUIRED")

    for candidate in normalized_pack["candidates"]:
        case_id = str(candidate["caseId"])
        case_reviews = by_case.get(case_id, [])
        primary = [
            review
            for review in case_reviews
            if review["reviewerRole"] == "REVIEWER"
        ]
        reviewer_ids = {str(review["reviewerId"]) for review in primary}
        reviewer_keys_for_case = {
            str(review["reviewerKeyFingerprint"]) for review in primary
        }
        rounds = [int(review["round"]) for review in primary]
        if (
            len(primary) != len(reviewer_ids)
            or len(primary) != len(reviewer_keys_for_case)
        ):
            raise GoldReviewError(
                f"duplicate reviewer for {case_id}"
            )
        if len(rounds) != len(set(rounds)):
            raise GoldReviewError(
                f"duplicate review round for {case_id}"
            )
        if (
            len(primary) < 2
            or len(reviewer_ids) < 2
            or len(reviewer_keys_for_case) < 2
            or len(set(rounds)) < 2
        ):
            gaps.add(f"TWO_INDEPENDENT_REVIEWS_REQUIRED:{case_id}")
            continue

        answers = {_review_answer_identity(review) for review in primary}
        if len(answers) > 1:
            adjudicators = [
                review
                for review in case_reviews
                if review["reviewerRole"] == "ADJUDICATOR"
            ]
            if not adjudicators:
                gaps.add(
                    f"INDEPENDENT_ADJUDICATION_REQUIRED:{case_id}"
                )
                continue
            if len(adjudicators) != 1:
                raise GoldReviewError(
                    f"exactly one adjudicator is required for {case_id}"
                )
            adjudicator = adjudicators[0]
            if (
                str(adjudicator["reviewerId"]) in reviewer_ids
                or str(adjudicator["reviewerKeyFingerprint"])
                in reviewer_keys_for_case
            ):
                raise GoldReviewError(
                    "adjudicator must be independent from both reviewers"
                )
        reviewed_cases += 1

    status = READY_TO_FREEZE if not gaps else BLOCKED_BY_INDEPENDENT_REVIEW
    return {
        "schema": VALIDATION_SCHEMA,
        "packId": normalized_pack["packId"],
        "packSha256": normalized_pack["packSha256"],
        "kind": normalized_pack["kind"],
        "candidateCases": len(normalized_pack["candidates"]),
        "reviewedCases": reviewed_cases,
        "reviewCount": len(normalized_reviews),
        "status": status,
        "gaps": sorted(gaps),
    }


def validate_query_review_provenance(
    raw_case: Mapping[str, object],
    provenance: Mapping[str, object],
    *,
    trusted_reviewers: Mapping[str, str],
) -> dict[str, object]:
    """Bind one EMPIRICAL query answer to its blind pack and reviews."""

    if (
        not isinstance(provenance, Mapping)
        or set(provenance) != {"schema", "pack", "reviews"}
        or provenance.get("schema") != QUERY_REVIEW_PROVENANCE_SCHEMA
        or not isinstance(provenance.get("pack"), Mapping)
        or not isinstance(provenance.get("reviews"), list)
    ):
        raise GoldReviewError(
            "EMPIRICAL requires validated review provenance"
        )
    pack = provenance["pack"]
    reviews = provenance["reviews"]
    validation = validate_review_set(
        pack,
        reviews,
        trusted_reviewers=trusted_reviewers,
    )
    if (
        validation["status"] != READY_TO_FREEZE
        or pack.get("kind") != "query"
    ):
        raise GoldReviewError(
            "EMPIRICAL requires validated review provenance"
        )

    expected_candidate = query_candidate_from_gold_case(raw_case)
    case_id = str(expected_candidate["caseId"])
    candidate = next(
        (
            item
            for item in pack["candidates"]
            if isinstance(item, Mapping)
            and item.get("caseId") == case_id
        ),
        None,
    )
    if (
        not isinstance(candidate, Mapping)
        or candidate.get("candidateSha256")
        != candidate_content_sha256(expected_candidate)
    ):
        raise GoldReviewError(
            "query review candidate does not match the gold case"
        )

    case_reviews = [
        review
        for review in reviews
        if isinstance(review, Mapping)
        and review.get("caseId") == case_id
    ]
    primary = [
        review
        for review in case_reviews
        if review.get("reviewerRole") == "REVIEWER"
    ]
    primary_answers = {
        _review_answer_identity(review): review.get("answer")
        for review in primary
    }
    if len(primary_answers) == 1:
        resolved_answer = next(iter(primary_answers.values()))
    else:
        adjudicators = [
            review
            for review in case_reviews
            if review.get("reviewerRole") == "ADJUDICATOR"
        ]
        resolved_answer = (
            adjudicators[0].get("answer")
            if len(adjudicators) == 1
            else None
        )
    if _sha256_json(resolved_answer) != _sha256_json(
        raw_case.get("expected")
    ):
        raise GoldReviewError(
            "reviewed query answer does not match expected gold"
        )

    empirical_evidence = [
        item
        for review in case_reviews
        for item in review.get("evidence", [])
        if isinstance(item, Mapping)
        and str(item.get("freshness") or "").upper() == "FRESH"
        and str(item.get("uri") or "").startswith(
            ("runtime://", "empirical://")
        )
    ]
    if not empirical_evidence:
        raise GoldReviewError(
            "EMPIRICAL review requires fresh runtime evidence"
        )
    return validation


__all__ = [
    "BLOCKED_BY_INDEPENDENT_REVIEW",
    "PACK_SCHEMA",
    "QUERY_REVIEW_PROVENANCE_SCHEMA",
    "REGISTRATION_REVIEW_SOURCE_SCHEMA",
    "ROLE_REVIEW_SOURCE_SCHEMA",
    "READY_TO_FREEZE",
    "REVIEW_SCHEMA",
    "REVIEWER_REGISTRY_SCHEMA",
    "VALIDATION_SCHEMA",
    "GoldReviewError",
    "build_query_review_pack",
    "build_registration_review_pack",
    "build_review_pack",
    "build_role_review_pack",
    "candidate_content_sha256",
    "load_trusted_reviewer_registry",
    "pack_content_sha256",
    "query_candidate_from_gold_case",
    "registration_review_source_from_sqlite",
    "review_content_sha256",
    "role_review_source_from_sqlite",
    "validate_query_review_provenance",
    "validate_registration_review_source",
    "validate_review_pack",
    "validate_review_set",
    "validate_role_review_source",
]
