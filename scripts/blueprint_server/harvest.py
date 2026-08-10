"""Harvest query and build-request adapters for the control center."""

from __future__ import annotations

import re
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs

from blueprint_translator.harvest_build_jobs import (
    HarvestBuildAlreadyRunning,
    HarvestBuildArgumentError,
    HarvestBuildJobNotFound,
)
from blueprint_translator.harvest_evaluation_catalog import (
    AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
    METRIC_OBSERVED_PER_NODE,
    METRIC_OBSERVED_PER_SECOND,
    METRIC_STATIC_CYCLE_SPEED,
    METRIC_STATIC_TOTAL,
    POLICY_CONFIRMED,
    POLICY_INCLUDE_CONDITIONAL,
    VARIANT_ALL,
    VARIANT_BEST_DISCOVERED_EXPLORATORY,
    VARIANT_CANONICAL,
)
from blueprint_translator.harvest_node_repository import (
    HarvestDatasetInvalid,
    HarvestDatasetNotBuilt,
)
from blueprint_translator.harvest_runtime_observations import (
    HarvestRuntimeProfileError,
)
from blueprint_translator.resource_nodes import NODE_PAGE_MAX_LIMIT

from .reports import parse_report_query_int
from .request import ApiProblem


def resolve_harvest_image_path(image_identity: str, image_root: Path) -> Path:
    """Resolve one immutable image by lowercase SHA-256 identity only."""

    if re.fullmatch(r"[0-9a-f]{64}", str(image_identity or "")) is None:
        raise ValueError("Invalid harvest image identity.")
    resolved_root = Path(image_root).resolve()
    candidate = (resolved_root / f"{image_identity}.jpg").resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("Harvest image resolves outside the cache root.") from exc
    if not candidate.is_file():
        raise FileNotFoundError("Harvest image was not found.")
    return candidate


def _harvest_dataset_problem(exc: Exception) -> ApiProblem:
    if isinstance(exc, HarvestDatasetNotBuilt):
        return ApiProblem(
            HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "ok": False,
                "code": exc.code,
                "error": "资源节点索引尚未生成，请先运行 build_ark_resource_node_catalog.py。",
            },
        )
    if isinstance(exc, HarvestDatasetInvalid):
        return ApiProblem(
            HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "ok": False,
                "code": exc.code,
                "error": "资源节点索引无效，请重新生成。",
            },
        )
    raise exc


def query_harvest_nodes_for_request(
    query: str,
    *,
    repository: object,
) -> dict[str, object]:
    values = parse_qs(query)
    try:
        offset = max(
            0,
            parse_report_query_int(
                values.get("offset", [""])[0],
                "offset",
                0,
            ),
        )
        limit = min(
            NODE_PAGE_MAX_LIMIT,
            max(
                1,
                parse_report_query_int(
                    values.get("limit", [""])[0],
                    "limit",
                    24,
                ),
            ),
        )
        return repository.list_nodes(  # type: ignore[attr-defined]
            q=values.get("q", [""])[0],
            map_name=values.get("map", [""])[0],
            only_map_family=values.get("onlyMapFamily", [""])[0],
            resource=values.get("resource", [""])[0],
            offset=offset,
            limit=limit,
        )
    except (HarvestDatasetNotBuilt, HarvestDatasetInvalid) as exc:
        raise _harvest_dataset_problem(exc) from exc
    except ValueError as exc:
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "INVALID_HARVEST_NODE_FILTER",
                "error": "Invalid resource-node filter.",
            },
        ) from exc


def query_harvest_node_for_request(
    node_id: str,
    *,
    repository: object,
) -> dict[str, object]:
    if not node_id:
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "RESOURCE_NODE_ID_REQUIRED",
                "error": "缺少资源节点 ID。",
            },
        )
    try:
        return repository.get_node(node_id)  # type: ignore[attr-defined]
    except KeyError as exc:
        raise ApiProblem(
            HTTPStatus.NOT_FOUND,
            {
                "ok": False,
                "code": "RESOURCE_NODE_NOT_FOUND",
                "error": "资源节点不存在。",
            },
        ) from exc
    except (HarvestDatasetNotBuilt, HarvestDatasetInvalid) as exc:
        raise _harvest_dataset_problem(exc) from exc


def _harvest_runtime_ranking_options(
    values: dict[str, list[str]],
    metric: str,
) -> dict[str, object]:
    raw_preliminary_values = values.get("includePreliminary", [])
    if len(raw_preliminary_values) > 1:
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "INVALID_HARVEST_INCLUDE_PRELIMINARY",
                "error": "includePreliminary must be exactly true or false.",
            },
        )
    include_preliminary = False
    if raw_preliminary_values:
        raw_preliminary = raw_preliminary_values[0].strip()
        if raw_preliminary not in {"true", "false"}:
            raise ApiProblem(
                HTTPStatus.BAD_REQUEST,
                {
                    "ok": False,
                    "code": "INVALID_HARVEST_INCLUDE_PRELIMINARY",
                    "error": "includePreliminary must be exactly true or false.",
                },
            )
        include_preliminary = raw_preliminary == "true"

    raw_profile_values = values.get("runtimeProfileId", [])
    if len(raw_profile_values) > 1:
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "INVALID_HARVEST_RUNTIME_PROFILE",
                "error": "runtimeProfileId must identify exactly one runtime profile.",
            },
        )
    runtime_profile_id = (
        raw_profile_values[0].strip() if raw_profile_values else ""
    )

    options: dict[str, object] = {}
    if runtime_profile_id:
        options["runtime_profile_id"] = runtime_profile_id
    if metric in {METRIC_OBSERVED_PER_NODE, METRIC_OBSERVED_PER_SECOND}:
        options["include_preliminary"] = include_preliminary
    elif raw_preliminary_values:
        options["include_preliminary"] = include_preliminary
    return options


def _harvest_runtime_profile_problem(exc: ValueError) -> ApiProblem | None:
    detail = str(exc).strip()
    normalized = detail.casefold()
    code = str(getattr(exc, "code", "")).strip()
    if not code.startswith("HARVEST_RUNTIME_PROFILE_"):
        if "runtimeprofileid" not in normalized:
            return None
        if "multiple runtime profiles" in normalized:
            code = "HARVEST_RUNTIME_PROFILE_REQUIRED"
        elif "not found" in normalized or "unknown" in normalized:
            code = "HARVEST_RUNTIME_PROFILE_NOT_FOUND"
        else:
            return None

    if code == "HARVEST_RUNTIME_PROFILE_NOT_FOUND":
        error = "The requested runtimeProfileId was not found."
    else:
        error = (
            "Observed ranking requires runtimeProfileId when multiple "
            "comparable runtime profiles are available."
        )
    return ApiProblem(
        HTTPStatus.BAD_REQUEST,
        {"ok": False, "code": code, "error": error},
    )


def query_harvest_ranking_for_request(
    query: str,
    *,
    repository: object,
) -> dict[str, object]:
    values = parse_qs(query, keep_blank_values=True)
    node_id = values.get("nodeId", [""])[0].strip()
    node_resource_id = values.get("nodeResourceId", [""])[0].strip()
    if not node_id or not node_resource_id:
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "NODE_RESOURCE_ID_REQUIRED",
                "error": "排名查询必须同时提供 nodeId 和 nodeResourceId。",
            },
        )
    limit = min(
        10,
        max(
            1,
            parse_report_query_int(
                values.get("limit", [""])[0],
                "limit",
                10,
            ),
        ),
    )
    evidence_policy = values.get("policy", [POLICY_CONFIRMED])[0].strip()
    variant_policy = values.get("variantPolicy", [VARIANT_CANONICAL])[0].strip()
    metric = values.get("metric", [METRIC_STATIC_TOTAL])[0].strip()
    availability_policy = values.get(
        "availabilityPolicy",
        [AVAILABILITY_GLOBAL_TRANSFER_ALLOWED],
    )[0].strip()
    allowed_values = {
        "policy": {POLICY_CONFIRMED, POLICY_INCLUDE_CONDITIONAL},
        "variantPolicy": {
            VARIANT_CANONICAL,
            VARIANT_ALL,
            VARIANT_BEST_DISCOVERED_EXPLORATORY,
        },
        "metric": {
            METRIC_STATIC_TOTAL,
            METRIC_STATIC_CYCLE_SPEED,
            METRIC_OBSERVED_PER_NODE,
            METRIC_OBSERVED_PER_SECOND,
        },
        "availabilityPolicy": {AVAILABILITY_GLOBAL_TRANSFER_ALLOWED},
    }
    requested_values = {
        "policy": evidence_policy,
        "variantPolicy": variant_policy,
        "metric": metric,
        "availabilityPolicy": availability_policy,
    }
    if any(
        requested_values[name] not in allowed
        for name, allowed in allowed_values.items()
    ):
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "INVALID_HARVEST_RANKING_POLICY",
                "error": "Invalid harvest ranking policy.",
            },
        )
    runtime_options = _harvest_runtime_ranking_options(values, metric)
    try:
        return repository.rankings(  # type: ignore[attr-defined]
            node_id,
            node_resource_id,
            limit=limit,
            evidence_policy=evidence_policy,
            variant_policy=variant_policy,
            metric=metric,
            availability_policy=availability_policy,
            **runtime_options,
        )
    except KeyError as exc:
        code = str(exc).strip("'")
        raise ApiProblem(
            HTTPStatus.NOT_FOUND,
            {
                "ok": False,
                "code": code,
                "error": "资源节点或资源条目不存在。",
            },
        ) from exc
    except (HarvestDatasetNotBuilt, HarvestDatasetInvalid) as exc:
        raise _harvest_dataset_problem(exc) from exc
    except HarvestRuntimeProfileError as exc:
        runtime_problem = _harvest_runtime_profile_problem(exc)
        if runtime_problem is not None:
            raise runtime_problem from exc
        raise
    except ValueError as exc:
        runtime_problem = _harvest_runtime_profile_problem(exc)
        if runtime_problem is not None:
            raise runtime_problem from exc
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "INVALID_HARVEST_RANKING_POLICY",
                "error": "Invalid harvest ranking policy.",
            },
        ) from exc


def query_harvest_creatures_for_request(
    query: str,
    *,
    repository: object,
) -> dict[str, object]:
    values = parse_qs(query)
    offset = max(
        0,
        parse_report_query_int(
            values.get("offset", [""])[0],
            "offset",
            0,
        ),
    )
    limit = min(
        100,
        max(
            1,
            parse_report_query_int(
                values.get("limit", [""])[0],
                "limit",
                24,
            ),
        ),
    )
    try:
        return repository.list_creatures(  # type: ignore[attr-defined]
            q=values.get("q", [""])[0],
            offset=offset,
            limit=limit,
        )
    except (HarvestDatasetNotBuilt, HarvestDatasetInvalid) as exc:
        raise _harvest_dataset_problem(exc) from exc


def query_harvest_creature_specialties_for_request(
    species_key: str,
    query: str,
    *,
    repository: object,
) -> dict[str, object]:
    if not species_key:
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "HARVEST_SPECIES_KEY_REQUIRED",
                "error": "A creature species key is required.",
            },
        )
    values = parse_qs(query, keep_blank_values=True)
    offset = max(
        0,
        parse_report_query_int(
            values.get("offset", [""])[0],
            "offset",
            0,
        ),
    )
    limit = min(
        100,
        max(
            1,
            parse_report_query_int(
                values.get("limit", [""])[0],
                "limit",
                24,
            ),
        ),
    )
    evidence_policy = values.get("policy", [POLICY_CONFIRMED])[0].strip()
    variant_policy = values.get("variantPolicy", [VARIANT_CANONICAL])[0].strip()
    metric = values.get("metric", [METRIC_STATIC_TOTAL])[0].strip()
    availability_policy = values.get(
        "availabilityPolicy",
        [AVAILABILITY_GLOBAL_TRANSFER_ALLOWED],
    )[0].strip()
    runtime_options = _harvest_runtime_ranking_options(values, metric)
    try:
        return repository.creature_specialties(  # type: ignore[attr-defined]
            species_key,
            offset=offset,
            limit=limit,
            evidence_policy=evidence_policy,
            variant_policy=variant_policy,
            metric=metric,
            availability_policy=availability_policy,
            **runtime_options,
        )
    except KeyError as exc:
        raise ApiProblem(
            HTTPStatus.NOT_FOUND,
            {
                "ok": False,
                "code": "HARVEST_SPECIES_NOT_FOUND",
                "error": "The requested creature species was not found.",
            },
        ) from exc
    except (HarvestDatasetNotBuilt, HarvestDatasetInvalid) as exc:
        raise _harvest_dataset_problem(exc) from exc
    except HarvestRuntimeProfileError as exc:
        runtime_problem = _harvest_runtime_profile_problem(exc)
        if runtime_problem is not None:
            raise runtime_problem from exc
        raise
    except ValueError as exc:
        runtime_problem = _harvest_runtime_profile_problem(exc)
        if runtime_problem is not None:
            raise runtime_problem from exc
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "INVALID_HARVEST_RANKING_POLICY",
                "error": "Invalid harvest ranking policy.",
            },
        ) from exc


def _harvest_build_problem(exc: Exception) -> ApiProblem:
    if isinstance(exc, HarvestBuildArgumentError):
        status = HTTPStatus.BAD_REQUEST
        message = "Invalid harvest build request."
    elif isinstance(exc, HarvestBuildAlreadyRunning):
        status = HTTPStatus.CONFLICT
        message = "A harvest build is already running."
    elif isinstance(exc, HarvestBuildJobNotFound):
        status = HTTPStatus.NOT_FOUND
        message = "The harvest build job was not found."
    else:
        raise exc
    payload: dict[str, object] = {
        "ok": False,
        "code": exc.code,
        "error": message,
    }
    job_id = getattr(exc, "job_id", None)
    if job_id:
        payload["jobId"] = job_id
    return ApiProblem(status, payload)


def query_harvest_build_for_request(
    query: str,
    *,
    build_manager: object,
) -> dict[str, object] | None:
    values = parse_qs(query)
    job_id = values.get("jobId", [""])[0].strip() or None
    try:
        return build_manager.get(job_id)  # type: ignore[attr-defined]
    except HarvestBuildJobNotFound as exc:
        if job_id is None and exc.job_id is None:
            return None
        raise _harvest_build_problem(exc) from exc
    except (
        HarvestBuildArgumentError,
        HarvestBuildAlreadyRunning,
    ) as exc:
        raise _harvest_build_problem(exc) from exc


def start_harvest_build_for_request(
    body: dict[str, object],
    *,
    build_manager: object,
) -> dict[str, object]:
    if set(body) != {"options"}:
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "HARVEST_BUILD_REQUEST_INVALID",
                "error": (
                    "The harvest build request must contain only an options object."
                ),
            },
        )
    options = body.get("options")
    if not isinstance(options, dict):
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "HARVEST_BUILD_REQUEST_INVALID",
                "error": "The harvest build options value must be an object.",
            },
        )
    if options:
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "HARVEST_BUILD_OPTIONS_FORBIDDEN",
                "error": (
                    "Public harvest builds do not accept configuration overrides."
                ),
            },
        )
    try:
        return build_manager.start(options)  # type: ignore[attr-defined]
    except (
        HarvestBuildArgumentError,
        HarvestBuildAlreadyRunning,
        HarvestBuildJobNotFound,
    ) as exc:
        raise _harvest_build_problem(exc) from exc


def cancel_harvest_build_for_request(
    job_id: str,
    *,
    build_manager: object,
) -> dict[str, object]:
    if not job_id:
        raise _harvest_build_problem(
            HarvestBuildArgumentError("A harvest build job id is required.")
        )
    try:
        return build_manager.cancel(job_id)  # type: ignore[attr-defined]
    except (
        HarvestBuildArgumentError,
        HarvestBuildAlreadyRunning,
        HarvestBuildJobNotFound,
    ) as exc:
        raise _harvest_build_problem(exc) from exc


__all__ = [
    "_harvest_build_problem",
    "_harvest_dataset_problem",
    "_harvest_runtime_profile_problem",
    "_harvest_runtime_ranking_options",
    "cancel_harvest_build_for_request",
    "query_harvest_build_for_request",
    "query_harvest_creature_specialties_for_request",
    "query_harvest_creatures_for_request",
    "query_harvest_node_for_request",
    "query_harvest_nodes_for_request",
    "query_harvest_ranking_for_request",
    "resolve_harvest_image_path",
    "start_harvest_build_for_request",
]
