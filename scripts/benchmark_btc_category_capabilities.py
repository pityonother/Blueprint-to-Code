"""Build a fail-closed BTC capability matrix from classified ARK samples.

This benchmark deliberately separates four questions that older reports mixed:

1. Is the Evidence container valid and authoritative?
2. Did the reader recover business content?
3. Can a fixed player question be closed with resolvable evidence citations?
4. Is the result published through the canonical MCP-facing catalog?

The command is read-only.  It never captures, publishes, migrates, or prunes an
asset.  A reviewed assessment is required before content can be promoted to a
closed answer; a green validator or READY badge is not enough.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.evidence_repository import (  # noqa: E402
    is_release_ready_evidence,
    resolve_asset_evidence_state,
)
from blueprint_translator.evidence_values import project_default_value  # noqa: E402
from blueprint_translator.interpretation_publication import (  # noqa: E402
    inspect_interpretation_health,
)
from blueprint_translator.public_paths import (  # noqa: E402
    public_value_is_path_free,
)


SCHEMA = "ark.btc.category-capability-benchmark.v1"
ALLOWED_CLOSURE_STATUSES = {
    "CLOSED_EXACT",
    "CLOSED_HEURISTIC",
    "PARTIAL",
    "IDENTITY_ONLY",
    "UNSUPPORTED",
    "FAILED",
    "NOT_RUN",
}
GAP_STATUSES = {"NOT_RECOVERED", "SOURCE_NOT_AVAILABLE", "AMBIGUOUS"}


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def _file_binding(path: Path, payload: object | None = None) -> dict[str, object]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    binding: dict[str, object] = {
        "sha256": digest.hexdigest(),
        "bytes": size,
    }
    if isinstance(payload, Mapping) and payload.get("schema"):
        binding["schema"] = str(payload["schema"])
    return binding


def validate_sample_plan(plan: Mapping[str, object]) -> list[dict[str, object]]:
    samples_value = plan.get("samples")
    if not isinstance(samples_value, Sequence) or isinstance(
        samples_value, (str, bytes)
    ):
        raise ValueError("sample plan samples must be an array")
    samples: list[dict[str, object]] = []
    indices: set[int] = set()
    paths: set[str] = set()
    for raw in samples_value:
        if not isinstance(raw, Mapping):
            raise ValueError("sample plan entries must be objects")
        sample = dict(raw)
        try:
            index = int(sample.get("sampleIndex"))
        except (TypeError, ValueError) as exc:
            raise ValueError("sampleIndex must be an integer") from exc
        if index <= 0:
            raise ValueError("sampleIndex must be positive")
        if index in indices:
            raise ValueError(f"duplicate sampleIndex: {index}")
        indices.add(index)
        target_path = str(sample.get("targetPath") or "").strip()
        if not target_path:
            raise ValueError(f"sample {index} has no targetPath")
        if target_path in paths:
            raise ValueError(f"duplicate targetPath: {target_path}")
        paths.add(target_path)
        groups = sample.get("coverageCategoryGroups")
        if not isinstance(groups, Sequence) or isinstance(groups, (str, bytes)):
            raise ValueError(f"sample {index} has no category groups")
        if not groups:
            raise ValueError(f"sample {index} has no category groups")
        samples.append(sample)
    declared = plan.get("sampleCount")
    if declared is not None and int(declared) != len(samples):
        raise ValueError(
            f"sampleCount mismatch: declared={declared} actual={len(samples)}"
        )
    return sorted(samples, key=lambda item: int(item["sampleIndex"]))


def _scalar(connection: sqlite3.Connection, sql: str) -> int:
    row = connection.execute(sql).fetchone()
    return int(row[0] if row is not None else 0)


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _rows(
    connection: sqlite3.Connection,
    table: str,
    columns: str,
) -> list[sqlite3.Row]:
    if not _table_exists(connection, table):
        return []
    return list(connection.execute(f'SELECT {columns} FROM "{table}"'))


def _search_records(connection: sqlite3.Connection) -> list[dict[str, str]]:
    specs = (
        ("graph", "graphs", "graph_ref", "name, graph_type, status, confidence"),
        (
            "node",
            "nodes",
            "node_ref",
            "name, label, class_name, node_type, function_name, variable_name, event_name, comment",
        ),
        (
            "pin",
            "pins",
            "pin_ref",
            "name, direction, category, subcategory, default_value_json, default_object",
        ),
        (
            "default",
            "class_defaults",
            "default_ref",
            "name, type_name, value_json, source, confidence",
        ),
        (
            "reference",
            "references",
            "reference_ref",
            "kind, name, target_ref, classification, confidence",
        ),
        (
            "diagnostic",
            "diagnostics",
            "diagnostic_ref",
            "status, reason_code, title, detail, next_probe",
        ),
    )
    records: list[dict[str, str]] = []
    for kind, table, ref_col, text_cols in specs:
        columns = f"{ref_col}, {text_cols}"
        for row in _rows(connection, table, columns):
            values = [str(value or "") for value in row]
            records.append(
                {
                    "kind": kind,
                    "ref": values[0],
                    "text": " | ".join(values[1:]),
                }
            )
    for row in _rows(
        connection,
        "properties",
        "property_ref, owner_kind, name, type_name, value_json, source, confidence",
    ):
        owner_kind = str(row["owner_kind"] or "")
        records.append(
            {
                "kind": "asset_field" if owner_kind == "asset" else "property",
                "ref": str(row["property_ref"] or ""),
                "text": " | ".join(str(value or "") for value in row[2:]),
            }
        )
    return records


def _evidence_truth_index(
    connection: sqlite3.Connection,
    records: Sequence[Mapping[str, str]],
) -> dict[str, dict[str, object]]:
    index = {
        str(record.get("ref") or ""): {
            "kind": str(record.get("kind") or ""),
        }
        for record in records
        if str(record.get("ref") or "").startswith("bp://")
    }
    for row in _rows(
        connection,
        "class_defaults",
        "default_ref, type_name, value_json, value_codec, extra_json",
    ):
        codec = str(row["value_codec"] or "json")
        value_loaded = codec == "json"
        value: object = None
        if value_loaded:
            try:
                value = json.loads(str(row["value_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                value_loaded = False
        try:
            extra = json.loads(str(row["extra_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            extra = {}
        projection = project_default_value(
            str(row["type_name"] or ""),
            value,
            extra,
            value_loaded=value_loaded,
        )
        index[str(row["default_ref"])] = {
            "kind": "default",
            "valueStatus": str(projection.get("valueStatus") or "NOT_RECOVERED"),
            "valueUsable": projection.get("valueUsable") is True,
        }
    for row in _rows(
        connection,
        "properties",
        "property_ref, owner_kind, extra_json",
    ):
        if str(row["owner_kind"] or "") != "asset":
            continue
        try:
            extra = json.loads(str(row["extra_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            extra = {}
        usable = (
            isinstance(extra, Mapping)
            and extra.get("confirmed_value_usable") is True
        )
        index[str(row["property_ref"])] = {
            "kind": "asset_field",
            "valueStatus": "CONFIRMED" if usable else "NOT_RECOVERED",
            "valueUsable": usable,
        }
    for row in _rows(connection, "edges", "edge_ref, resolution_status"):
        index[str(row["edge_ref"])] = {
            "kind": "edge",
            "resolutionStatus": str(row["resolution_status"] or ""),
        }
    return index


def profile_blueprint_evidence(asset_dir: str | Path) -> dict[str, object]:
    root = Path(asset_dir).expanduser().resolve()
    state = resolve_asset_evidence_state(root, allow_stale=True)
    authority_ready = is_release_ready_evidence(state)
    uri = f"file:{state.database_path.as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        identity_row = connection.execute(
            "SELECT asset_id, asset_name, object_path, revision_id FROM asset_revisions LIMIT 1"
        ).fetchone()
        if identity_row is None:
            raise ValueError("evidence database has no asset identity")

        counts = {
            "graphCount": _scalar(connection, "SELECT COUNT(*) FROM graphs"),
            "nodeCount": _scalar(connection, "SELECT COUNT(*) FROM nodes"),
            "pinCount": _scalar(connection, "SELECT COUNT(*) FROM pins"),
            "edgeCount": _scalar(connection, "SELECT COUNT(*) FROM edges"),
            "linkObservationCount": _scalar(
                connection, "SELECT COUNT(*) FROM edge_observations"
            ),
            "defaultCount": _scalar(
                connection, "SELECT COUNT(*) FROM class_defaults"
            ),
            "propertyCount": _scalar(connection, "SELECT COUNT(*) FROM properties"),
            "assetFieldCount": _scalar(
                connection,
                "SELECT COUNT(*) FROM properties WHERE owner_kind = 'asset'",
            ),
            "referenceCount": _scalar(connection, 'SELECT COUNT(*) FROM "references"'),
            "diagnosticCount": _scalar(
                connection, "SELECT COUNT(*) FROM diagnostics"
            ),
        }
        business_fact_count = (
            counts["nodeCount"]
            + counts["defaultCount"]
            + counts["propertyCount"]
            + counts["referenceCount"]
        )
        counts["businessFactCount"] = business_fact_count
        counts["silentEmpty"] = bool(
            counts["graphCount"] == 0 and business_fact_count == 0
        )

        graph_statuses = Counter(
            str(row[0] or "UNKNOWN").upper()
            for row in _rows(connection, "graphs", "status")
        )
        native_pin_count = _scalar(
            connection, "SELECT COUNT(*) FROM pins WHERE native_pin_id <> ''"
        )
        persistent_guid_count = _scalar(
            connection, "SELECT COUNT(*) FROM pins WHERE persistent_guid <> ''"
        )
        exact_link_count = _scalar(
            connection,
            "SELECT COUNT(*) FROM edges WHERE lower(resolution_status) IN "
            "('resolved_pin','resolved_pin_exact')",
        )
        heuristic_link_count = _scalar(
            connection,
            "SELECT COUNT(*) FROM edges WHERE lower(resolution_status) LIKE '%heuristic%'",
        )
        ambiguous_link_count = _scalar(
            connection,
            "SELECT COUNT(*) FROM edge_observations "
            "WHERE lower(COALESCE(NULLIF(resolution_status,''), status, '')) = 'ambiguous'",
        )
        unresolved_link_count = _scalar(
            connection,
            "SELECT COUNT(*) FROM edge_observations "
            "WHERE lower(COALESCE(NULLIF(resolution_status,''), status, '')) "
            "NOT IN ('resolved_pin','resolved_pin_exact','resolved_pin_heuristic','ambiguous')",
        )
        diagnostic_statuses = Counter(
            str(row[0] or "UNKNOWN").upper()
            for row in _rows(connection, "diagnostics", "status")
        )
        records = _search_records(connection)
        evidence_index = _evidence_truth_index(connection, records)
        available_refs = sorted(evidence_index)
    finally:
        connection.close()

    return {
        "assetDir": str(root),
        "asset": {
            "assetId": str(identity_row["asset_id"]),
            "name": str(identity_row["asset_name"]),
            "objectPath": str(identity_row["object_path"]),
            "revisionId": str(identity_row["revision_id"]),
        },
        "authority": {
            "sourceKind": state.source_kind,
            "freshnessStatus": state.freshness_status,
            "releaseAuthority": state.release_authority,
            "migrationRequired": state.migration_required,
            "manifestSha256": state.manifest_sha256,
            "pointerSha256": state.pointer_sha256,
            "captureIntegrityStatus": "PASS" if authority_ready else "DEGRADED",
        },
        "content": {
            **counts,
            "graphStatusCounts": dict(sorted(graph_statuses.items())),
            "diagnosticStatusCounts": dict(sorted(diagnostic_statuses.items())),
            "sampleEvidenceRefs": available_refs[:25],
        },
        "identity": {
            "nativePinIdExactCount": native_pin_count,
            "persistentGuidExactCount": persistent_guid_count,
            "pinIdentityUnavailableCount": max(
                0, int(counts["pinCount"]) - native_pin_count
            ),
            "exactLinkCount": exact_link_count,
            "heuristicLinkCount": heuristic_link_count,
            "ambiguousLinkCount": ambiguous_link_count,
            "notRecoveredLinkCount": unresolved_link_count,
        },
        "gaps": {
            "blockingStatusCount": sum(
                diagnostic_statuses.get(status, 0) for status in GAP_STATUSES
            ),
            "statusCounts": dict(sorted(diagnostic_statuses.items())),
        },
        "_searchRecords": records,
        "_availableEvidenceRefs": available_refs,
        "_evidenceTruthIndex": evidence_index,
    }


def _signal_results(
    profile: Mapping[str, object],
    contract: Mapping[str, object],
) -> list[dict[str, object]]:
    records_value = profile.get("_searchRecords")
    records = records_value if isinstance(records_value, Sequence) else []
    groups_value = contract.get("requiredSignalGroups")
    groups = groups_value if isinstance(groups_value, Sequence) else []
    results: list[dict[str, object]] = []
    for raw in groups:
        if not isinstance(raw, Mapping):
            continue
        terms_value = raw.get("terms")
        terms = (
            [str(value) for value in terms_value if str(value).strip()]
            if isinstance(terms_value, Sequence)
            and not isinstance(terms_value, (str, bytes))
            else []
        )
        hits: list[dict[str, str]] = []
        for record in records:
            if not isinstance(record, Mapping):
                continue
            haystack = str(record.get("text") or "").casefold()
            if any(term.casefold() in haystack for term in terms):
                hits.append(
                    {
                        "kind": str(record.get("kind") or ""),
                        "ref": str(record.get("ref") or ""),
                    }
                )
        minimum = max(1, int(raw.get("minimumMatches") or 1))
        results.append(
            {
                "id": str(raw.get("id") or "signal"),
                "terms": terms,
                "minimumMatches": minimum,
                "matchCount": len(hits),
                "satisfied": len(hits) >= minimum,
                "evidence": hits[:10],
            }
        )
    return results


def _reviewed_claim_citations(
    assessment: Mapping[str, object],
) -> list[list[str]]:
    claims_value = assessment.get("claims")
    claims = (
        claims_value
        if isinstance(claims_value, Sequence)
        and not isinstance(claims_value, (str, bytes))
        else []
    )
    citation_groups: list[list[str]] = []
    for claim in claims:
        if not isinstance(claim, Mapping):
            citation_groups.append([])
            continue
        values = claim.get("evidenceRefs")
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            citation_groups.append([str(value) for value in values if str(value)])
        else:
            citation_groups.append([])
    return citation_groups


def _citation_is_exact(
    profile: Mapping[str, object],
    ref: str,
) -> bool:
    raw_index = profile.get("_evidenceTruthIndex")
    index = raw_index if isinstance(raw_index, Mapping) else {}
    raw_metadata = index.get(ref)
    metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    kind = str(metadata.get("kind") or "")
    if kind == "default":
        return bool(
            str(metadata.get("valueStatus") or "") == "CONFIRMED"
            and metadata.get("valueUsable") is True
        )
    if kind == "edge":
        return str(metadata.get("resolutionStatus") or "").casefold() in {
            "resolved_pin",
            "resolved_pin_exact",
        }
    # The benchmark has no equally strict truth predicate yet for diagnostics,
    # fuzzy references, graph summaries, or heuristic pin identities.  They
    # remain useful for PARTIAL/CLOSED_HEURISTIC review, but cannot establish
    # CLOSED_EXACT until a dedicated gate is implemented and tested.
    return False


def _effective_question_contract(
    question_contract: Mapping[str, object],
    reviewed_assessment: Mapping[str, object] | None,
) -> tuple[dict[str, object], dict[str, object]]:
    effective = dict(question_contract)
    if reviewed_assessment is not None:
        override = reviewed_assessment.get("questionOverride")
        if override is not None and not isinstance(override, Mapping):
            raise ValueError("questionOverride must be an object")
        if isinstance(override, Mapping):
            for key in ("questionZh", "requiredSignalGroups", "requiresExactFlow"):
                if key in override:
                    effective[key] = override[key]
    category_question = str(question_contract.get("questionZh") or "")
    sample_question = str(effective.get("questionZh") or "")
    question_metadata = {
        "questionZh": sample_question,
        "categoryQuestionZh": category_question,
        "wasNarrowedForSample": effective != dict(question_contract),
    }
    return effective, question_metadata


def classify_blueprint_sample(
    profile: Mapping[str, object],
    *,
    question_contract: Mapping[str, object],
    reviewed_assessment: Mapping[str, object] | None = None,
) -> dict[str, object]:
    effective_contract, question_metadata = _effective_question_contract(
        question_contract,
        reviewed_assessment,
    )
    authority = profile.get("authority")
    content = profile.get("content")
    identity = profile.get("identity")
    authority_map = authority if isinstance(authority, Mapping) else {}
    content_map = content if isinstance(content, Mapping) else {}
    identity_map = identity if isinstance(identity, Mapping) else {}
    blockers: list[str] = []
    if authority_map.get("captureIntegrityStatus") != "PASS":
        blockers.append("EVIDENCE_NOT_AUTHORITATIVE")
    identity_boundary = profile.get("identityBoundary")
    if isinstance(identity_boundary, Mapping) and str(
        identity_boundary.get("status") or ""
    ) == "ALIASED_NOT_AUTHORITY_BOUND":
        blockers.append("ALIASED_IDENTITY_NOT_AUTHORITY_BOUND")
    if bool(content_map.get("silentEmpty")):
        return {
            "captureIntegrityStatus": str(
                authority_map.get("captureIntegrityStatus") or "FAILED"
            ),
            "contentRecoveryStatus": "IDENTITY_ONLY",
            "benchmarkClosureStatus": "IDENTITY_ONLY",
            "question": {
                **question_metadata,
                "signalGroups": _signal_results(profile, effective_contract),
            },
            "claims": [],
            "blockingGaps": [],
            "blockerCodes": sorted(set([*blockers, "SILENT_EMPTY"])),
        }

    signals = _signal_results(profile, effective_contract)
    if any(not bool(item["satisfied"]) for item in signals):
        blockers.append("REQUIRED_BUSINESS_SIGNAL_NOT_RECOVERED")
    if bool(effective_contract.get("requiresExactFlow")) and int(
        identity_map.get("exactLinkCount") or 0
    ) == 0:
        blockers.append("EXACT_EXECUTION_FLOW_NOT_RECOVERED")

    result: dict[str, object] = {
        "captureIntegrityStatus": str(
            authority_map.get("captureIntegrityStatus") or "FAILED"
        ),
        "contentRecoveryStatus": "CONTENT_RECOVERED",
        "benchmarkClosureStatus": "PARTIAL",
        "question": {
            **question_metadata,
            "signalGroups": signals,
        },
        "claims": [],
        "blockingGaps": [],
        "blockerCodes": blockers,
    }
    if reviewed_assessment is None:
        result["blockerCodes"] = sorted(
            set([*blockers, "QUESTION_ASSESSMENT_NOT_REVIEWED"])
        )
        return result

    requested_status = str(reviewed_assessment.get("status") or "PARTIAL")
    if requested_status not in ALLOWED_CLOSURE_STATUSES:
        raise ValueError(f"unsupported reviewed assessment status: {requested_status}")
    claims_value = reviewed_assessment.get("claims")
    claims = (
        list(claims_value)
        if isinstance(claims_value, Sequence)
        and not isinstance(claims_value, (str, bytes))
        else []
    )
    blocking_value = reviewed_assessment.get("blockingGaps")
    blocking_gaps = (
        [str(value) for value in blocking_value]
        if isinstance(blocking_value, Sequence)
        and not isinstance(blocking_value, (str, bytes))
        else []
    )
    available = {
        str(value)
        for value in profile.get("_availableEvidenceRefs", [])
        if str(value)
    }
    claim_citations = _reviewed_claim_citations(reviewed_assessment)
    citations = [ref for citation_group in claim_citations for ref in citation_group]
    if requested_status in {"CLOSED_EXACT", "CLOSED_HEURISTIC"} and not claims:
        blockers.append("CLOSED_STATUS_WITHOUT_CLAIMS")
    if requested_status in {"CLOSED_EXACT", "CLOSED_HEURISTIC"}:
        if not citations:
            blockers.append("CLOSED_STATUS_WITHOUT_CITATIONS")
        if any(not citation_group for citation_group in claim_citations):
            blockers.append("CLOSED_CLAIM_WITHOUT_CITATIONS")
    if any(ref not in available for ref in citations):
        result.update(
            {
                "benchmarkClosureStatus": "FAILED",
                "claims": claims,
                "blockingGaps": blocking_gaps,
                "blockerCodes": sorted(set([*blockers, "CITATION_BINDING_FAILED"])),
            }
        )
        return result
    if requested_status == "CLOSED_EXACT" and any(
        not _citation_is_exact(profile, ref) for ref in citations
    ):
        result.update(
            {
                "benchmarkClosureStatus": "FAILED",
                "claims": claims,
                "blockingGaps": blocking_gaps,
                "blockerCodes": sorted(
                    set([*blockers, "CITATION_NOT_EXACT"])
                ),
            }
        )
        return result
    if blocking_gaps:
        blockers.append("QUESTION_HAS_BLOCKING_GAPS")
    if requested_status == "CLOSED_EXACT" and blockers:
        requested_status = "PARTIAL"
    if requested_status == "CLOSED_HEURISTIC":
        if bool(effective_contract.get("requiresExactFlow")) and int(
            identity_map.get("exactLinkCount") or 0
        ) == 0 and int(identity_map.get("heuristicLinkCount") or 0) == 0:
            blockers.append("HEURISTIC_EXECUTION_FLOW_NOT_RECOVERED")
        allowed_heuristic_blockers = {"EXACT_EXECUTION_FLOW_NOT_RECOVERED"}
        if set(blockers).difference(allowed_heuristic_blockers):
            requested_status = "PARTIAL"
    result.update(
        {
            "benchmarkClosureStatus": requested_status,
            "claims": claims,
            "blockingGaps": blocking_gaps,
            "blockerCodes": sorted(set(blockers)),
        }
    )
    return result


def _category_group(sample: Mapping[str, object]) -> dict[str, str]:
    groups = sample.get("coverageCategoryGroups")
    if not isinstance(groups, Sequence) or not groups:
        raise ValueError(f"sample {sample.get('sampleIndex')} has no category group")
    raw = groups[0]
    if not isinstance(raw, Mapping):
        raise ValueError(f"sample {sample.get('sampleIndex')} category is invalid")
    return {
        "code": str(raw.get("code") or ""),
        "labelZh": str(raw.get("labelZh") or ""),
        "status": str(raw.get("status") or ""),
    }


def _question_contract(
    contracts: Mapping[str, object],
    group: Mapping[str, str],
) -> dict[str, object]:
    exact_key = f"{group['code']}:{group['status']}"
    value = contracts.get(exact_key, contracts.get(group["code"]))
    if not isinstance(value, Mapping):
        raise ValueError(f"missing question contract for {exact_key}")
    return dict(value)


def _native_result(
    profile: Mapping[str, object] | None,
    *,
    question_contract: Mapping[str, object],
    reviewed_assessment: Mapping[str, object] | None = None,
) -> dict[str, object]:
    _effective_contract, question_metadata = _effective_question_contract(
        question_contract,
        reviewed_assessment,
    )
    if profile is None or int(profile.get("symbolCount") or 0) == 0:
        return {
            "captureIntegrityStatus": str(
                (profile or {}).get("trustStatus") or "NOT_AVAILABLE"
            ),
            "contentRecoveryStatus": "UNSUPPORTED",
            "benchmarkClosureStatus": "UNSUPPORTED",
            "question": question_metadata,
            "claims": [],
            "blockingGaps": ["NATIVE_SYMBOL_OR_RECIPE_NOT_AVAILABLE"],
            "blockerCodes": ["ROUTING_ADAPTER_INCOMPLETE"],
        }
    if reviewed_assessment is None:
        return {
            "captureIntegrityStatus": str(profile.get("trustStatus") or "UNKNOWN"),
            "contentRecoveryStatus": "SYMBOL_SUPPORT_ONLY",
            "benchmarkClosureStatus": "PARTIAL",
            "question": question_metadata,
            "claims": [],
            "blockingGaps": ["FOCUSED_NATIVE_RECIPE_NOT_RUN"],
            "blockerCodes": [
                "NATIVE_IMPLEMENTATION_NOT_RECOVERED",
                "QUESTION_ASSESSMENT_NOT_REVIEWED",
            ],
        }
    requested_status = str(reviewed_assessment.get("status") or "PARTIAL")
    if requested_status not in ALLOWED_CLOSURE_STATUSES:
        raise ValueError(f"unsupported reviewed assessment status: {requested_status}")
    claims_value = reviewed_assessment.get("claims")
    claims = (
        list(claims_value)
        if isinstance(claims_value, Sequence)
        and not isinstance(claims_value, (str, bytes))
        else []
    )
    blocking_value = reviewed_assessment.get("blockingGaps")
    blocking_gaps = (
        [str(value) for value in blocking_value]
        if isinstance(blocking_value, Sequence)
        and not isinstance(blocking_value, (str, bytes))
        else []
    )
    available: set[str] = set()
    evidence_rows = profile.get("evidenceRefs")
    if isinstance(evidence_rows, Sequence) and not isinstance(
        evidence_rows, (str, bytes)
    ):
        for item in evidence_rows:
            if isinstance(item, Mapping) and str(item.get("evidenceRef") or ""):
                available.add(str(item["evidenceRef"]))
            elif isinstance(item, str) and item:
                available.add(item)
    claim_citations = _reviewed_claim_citations(reviewed_assessment)
    citations = [ref for citation_group in claim_citations for ref in citation_group]
    if any(ref not in available for ref in citations):
        return {
            "captureIntegrityStatus": str(profile.get("trustStatus") or "UNKNOWN"),
            "contentRecoveryStatus": "FAILED",
            "benchmarkClosureStatus": "FAILED",
            "question": question_metadata,
            "claims": claims,
            "blockingGaps": blocking_gaps,
            "blockerCodes": ["CITATION_BINDING_FAILED"],
        }
    blockers: list[str] = []
    if requested_status in {"CLOSED_EXACT", "CLOSED_HEURISTIC"} and not claims:
        blockers.append("CLOSED_STATUS_WITHOUT_CLAIMS")
    if requested_status in {"CLOSED_EXACT", "CLOSED_HEURISTIC"}:
        if not citations:
            blockers.append("CLOSED_STATUS_WITHOUT_CITATIONS")
        if any(not citation_group for citation_group in claim_citations):
            blockers.append("CLOSED_CLAIM_WITHOUT_CITATIONS")
    if blocking_gaps:
        blockers.append("QUESTION_HAS_BLOCKING_GAPS")
    if requested_status in {"CLOSED_EXACT", "CLOSED_HEURISTIC"} and blockers:
        requested_status = "PARTIAL"
    content_status = {
        "IDENTITY_ONLY": "IDENTITY_ONLY",
        "UNSUPPORTED": "UNSUPPORTED",
        "FAILED": "FAILED",
        "NOT_RUN": "NOT_RUN",
    }.get(requested_status, "SYMBOL_SUPPORT_ONLY")
    if requested_status == "PARTIAL":
        blockers.append("NATIVE_IMPLEMENTATION_NOT_RECOVERED")
    elif requested_status == "IDENTITY_ONLY":
        blockers.append("NATIVE_BUSINESS_CONTENT_NOT_RECOVERED")
    elif requested_status == "UNSUPPORTED":
        blockers.append("ROUTING_ADAPTER_INCOMPLETE")
    return {
        "captureIntegrityStatus": str(profile.get("trustStatus") or "UNKNOWN"),
        "contentRecoveryStatus": content_status,
        "benchmarkClosureStatus": requested_status,
        "question": question_metadata,
        "claims": claims,
        "blockingGaps": blocking_gaps,
        "blockerCodes": sorted(set(blockers)),
    }


def _public_profile(profile: Mapping[str, object] | None) -> object:
    if profile is None:
        return None
    public = {
        key: value
        for key, value in profile.items()
        if not str(key).startswith("_") and str(key) != "assetDir"
    }
    if not public_value_is_path_free(public):
        raise ValueError("public Evidence profile contains a machine-local path")
    return public


def build_capability_report(
    *,
    sample_plan: Mapping[str, object],
    question_contracts: Mapping[str, object],
    blueprint_profiles: Mapping[str, Mapping[str, object]],
    canonical_ready_profiles: Mapping[str, Mapping[str, object]] | None = None,
    canonical_ready_paths: set[str] | None = None,
    native_profiles: Mapping[str, Mapping[str, object]] | None = None,
    data_asset_profiles: Mapping[str, Mapping[str, object]] | None = None,
    reviewed_assessments: Mapping[str, Mapping[str, object]] | None = None,
    input_bindings: Mapping[str, object] | None = None,
) -> dict[str, object]:
    samples = validate_sample_plan(sample_plan)
    if canonical_ready_paths:
        raise ValueError(
            "canonical_ready_paths cannot bind READY to an Evidence revision; "
            "pass canonical_ready_profiles"
        )
    canonical_profiles = canonical_ready_profiles or {}
    native_by_path = native_profiles or {}
    data_by_path = data_asset_profiles or {}
    assessments = reviewed_assessments or {}
    rows: list[dict[str, object]] = []
    for sample in samples:
        group = _category_group(sample)
        contract = _question_contract(question_contracts, group)
        target_path = str(sample["targetPath"])
        action = str(sample.get("recommendedAction") or "")
        batch_profile = blueprint_profiles.get(target_path)
        canonical_profile = canonical_profiles.get(target_path)
        if canonical_profile is not None:
            canonical_asset = canonical_profile.get("asset")
            canonical_asset_map = (
                canonical_asset if isinstance(canonical_asset, Mapping) else {}
            )
            canonical_authority = canonical_profile.get("authority")
            canonical_authority_map = (
                canonical_authority
                if isinstance(canonical_authority, Mapping)
                else {}
            )
            if (
                str(canonical_asset_map.get("objectPath") or "") != target_path
                or not str(canonical_asset_map.get("revisionId") or "")
                or canonical_authority_map.get("captureIntegrityStatus") != "PASS"
            ):
                raise ValueError(
                    f"canonical READY profile is not identity-bound: {target_path}"
                )
        profile = canonical_profile or batch_profile
        evidence_profile: Mapping[str, object] | None = profile
        effective_reader = "BLUEPRINT_EVIDENCE"
        ready = canonical_profile is not None
        if action == "SKIP_EXISTING_CONFIRMED_CLASSIFICATION" and profile is None:
            result = {
                "captureIntegrityStatus": "NOT_RUN",
                "contentRecoveryStatus": "NOT_RUN",
                "benchmarkClosureStatus": "NOT_RUN",
                "question": {"questionZh": contract.get("questionZh", "")},
                "claims": [],
                "blockingGaps": ["SAMPLE_SKIPPED_BY_PRIOR_CLASSIFICATION"],
                "blockerCodes": ["NOT_RUN_BY_POLICY"],
            }
        elif target_path in data_by_path:
            data_profile = data_by_path[target_path]
            evidence_profile = data_profile
            effective_reader = "DATA_ASSET_EVIDENCE"
            has_facts = int(data_profile.get("businessFactCount") or 0) > 0
            formally_queryable = (
                has_facts
                and data_profile.get("formallyQueryable") is True
                and data_profile.get("captureIntegrityStatus") == "PASS"
            )
            ready = formally_queryable
            status = "PARTIAL" if has_facts else "IDENTITY_ONLY"
            result = {
                "captureIntegrityStatus": str(
                    data_profile.get("captureIntegrityStatus") or "PASS"
                ),
                "contentRecoveryStatus": (
                    "CONTENT_RECOVERED" if has_facts else "IDENTITY_ONLY"
                ),
                "benchmarkClosureStatus": status,
                "question": {"questionZh": contract.get("questionZh", "")},
                "claims": [],
                "blockingGaps": (
                    (
                        []
                        if formally_queryable
                        else ["DATA_ASSET_EVIDENCE_NOT_CANONICAL"]
                    )
                    if has_facts
                    else ["GENERIC_DATA_ASSET_FIELDS_NOT_RECOVERED"]
                ),
                "blockerCodes": (
                    ["QUESTION_ASSESSMENT_NOT_REVIEWED"]
                    if has_facts
                    else ["SILENT_EMPTY", "BUSINESS_FIELD_EXTRACTION_UNSUPPORTED"]
                ),
            }
        elif str(sample.get("targetKind") or "") == "NATIVE_CLASS":
            native_profile = native_by_path.get(target_path)
            evidence_profile = native_profile
            effective_reader = "NATIVE_EVIDENCE"
            ready = bool(
                native_profile is not None
                and native_profile.get("formallyQueryable") is True
            )
            result = _native_result(
                native_profile,
                question_contract=contract,
                reviewed_assessment=assessments.get(str(sample["sampleIndex"])),
            )
        elif profile is None:
            result = {
                "captureIntegrityStatus": "NOT_RUN",
                "contentRecoveryStatus": "NOT_RUN",
                "benchmarkClosureStatus": "NOT_RUN",
                "question": {"questionZh": contract.get("questionZh", "")},
                "claims": [],
                "blockingGaps": ["NO_EXACT_CAPTURE_FOUND"],
                "blockerCodes": ["NOT_RUN"],
            }
        else:
            result = classify_blueprint_sample(
                profile,
                question_contract=contract,
                reviewed_assessment=assessments.get(str(sample["sampleIndex"])),
            )
        rows.append(
            {
                "sampleIndex": int(sample["sampleIndex"]),
                "clusterId": str(sample.get("clusterId") or ""),
                "plannedStratum": group,
                "technicalKind": str(sample.get("targetKind") or ""),
                "targetPath": target_path,
                "exactClassPath": str(sample.get("exactClassPath") or ""),
                "memberObjectCount": int(sample.get("memberObjectCount") or 0),
                "route": {
                    "plannedAction": action,
                    "effectiveReader": effective_reader,
                    "evidenceOrigin": (
                        "CANONICAL_CURRENT" if ready else "BATCH_OR_AUXILIARY"
                    ),
                },
                "evidence": _public_profile(evidence_profile),
                "p0": {
                    "ready": ready,
                    "evidenceRevisionId": (
                        str(data_by_path[target_path].get("evidenceRevisionId") or "")
                        if ready and target_path in data_by_path
                        else (
                            str(
                                native_by_path[target_path].get(
                                    "evidenceRevisionId"
                                )
                                or ""
                            )
                            if ready and effective_reader == "NATIVE_EVIDENCE"
                            else (
                                str(profile.get("asset", {}).get("revisionId") or "")
                                if ready and isinstance(profile, Mapping)
                                else ""
                            )
                        )
                    ),
                },
                "result": result,
            }
        )

    statuses = Counter(
        str(row["result"]["benchmarkClosureStatus"]) for row in rows
    )
    coverage_value = sample_plan.get("coverage")
    coverage = coverage_value if isinstance(coverage_value, Mapping) else {}
    ready_closed_exact = sum(
        1
        for row in rows
        if bool(row["p0"]["ready"])
        and row["result"]["benchmarkClosureStatus"] == "CLOSED_EXACT"
    )
    ready_partial = sum(
        1
        for row in rows
        if bool(row["p0"]["ready"])
        and row["result"]["benchmarkClosureStatus"] == "PARTIAL"
    )
    return {
        "schema": SCHEMA,
        "status": "BASELINE_COMPLETE_WITH_EXPLICIT_GAPS",
        "classificationLineage": {
            "samplePlanSchema": str(sample_plan.get("schema") or ""),
            "selectionAlgorithm": str(
                sample_plan.get("selectionAlgorithm") or ""
            ),
            "sampleCount": len(rows),
            "categoryGroupsCovered": int(
                coverage.get("categoryGroupsCovered") or 0
            ),
            "categoryGroupsTotal": int(
                coverage.get("categoryGroupsTotal") or 0
            ),
        },
        "inputBindings": dict(input_bindings or {}),
        "counts": {
            "samples": len(rows),
            "categoryStatusStrata": len(
                {
                    (
                        str(row["plannedStratum"]["code"]),
                        str(row["plannedStratum"]["status"]),
                    )
                    for row in rows
                }
            ),
            "categoryCodes": len(
                {str(row["plannedStratum"]["code"]) for row in rows}
            ),
            "ready": sum(1 for row in rows if bool(row["p0"]["ready"])),
            "readyClosedExact": ready_closed_exact,
            "readyPartial": ready_partial,
            "nonReadyClosedExact": statuses.get("CLOSED_EXACT", 0)
            - ready_closed_exact,
            "closedExact": statuses.get("CLOSED_EXACT", 0),
            "closedHeuristic": statuses.get("CLOSED_HEURISTIC", 0),
            "partial": statuses.get("PARTIAL", 0),
            "identityOnly": statuses.get("IDENTITY_ONLY", 0),
            "unsupported": statuses.get("UNSUPPORTED", 0),
            "failed": statuses.get("FAILED", 0),
            "notRun": statuses.get("NOT_RUN", 0),
        },
        "boundariesZh": [
            "READY 只证明权威发布链，不等于内容闭环。",
            "validator PASS 只证明证据容器完整，不等于恢复了业务字段。",
            "没有逐问题人工复核且引用重查通过时，内容不得自动提升为 CLOSED。",
            "相似类名只用于抽样导航；结论仅允许在 exact class 范围传播。",
        ],
        "samples": rows,
    }


def discover_blueprint_profiles(
    capture_roots: Iterable[str | Path],
) -> dict[str, dict[str, object]]:
    profiles: dict[str, dict[str, object]] = {}
    for raw_root in capture_roots:
        root = Path(raw_root).expanduser().resolve()
        if not root.exists():
            continue
        pointers = sorted(root.rglob("evidence/current.json"))
        for pointer in pointers:
            asset_dir = pointer.parent.parent
            try:
                profile = profile_blueprint_evidence(asset_dir)
            except (OSError, ValueError):
                continue
            object_path = str(profile["asset"]["objectPath"])
            existing = profiles.get(object_path)
            if existing is None:
                profiles[object_path] = profile
                continue
            existing_ready = (
                existing.get("authority", {}).get("captureIntegrityStatus") == "PASS"
            )
            incoming_ready = (
                profile.get("authority", {}).get("captureIntegrityStatus") == "PASS"
            )
            if incoming_ready and not existing_ready:
                profiles[object_path] = profile
    return profiles


def apply_batch_aliases(
    profiles: Mapping[str, Mapping[str, object]],
    capture_roots: Iterable[str | Path],
) -> dict[str, dict[str, object]]:
    """Add explicit registry aliases without treating them as exact identity."""

    mapped = {str(key): dict(value) for key, value in profiles.items()}
    for raw_root in capture_roots:
        plan_path = Path(raw_root).expanduser().resolve() / "batch_plan.json"
        if not plan_path.exists():
            continue
        plan = _load_json(plan_path)
        if not isinstance(plan, Mapping):
            continue
        samples = plan.get("samples")
        if not isinstance(samples, Sequence):
            continue
        for item in samples:
            if not isinstance(item, Mapping):
                continue
            registry_path = str(item.get("sourceRegistryObjectPath") or "")
            resolved_path = str(item.get("targetPath") or "")
            if not registry_path or not resolved_path or registry_path == resolved_path:
                continue
            profile = mapped.get(resolved_path)
            if profile is None:
                continue
            alias_profile = dict(profile)
            alias_profile["identityBoundary"] = {
                "status": "ALIASED_NOT_AUTHORITY_BOUND",
                "requestedRegistryObjectPath": registry_path,
                "resolvedObjectPath": resolved_path,
            }
            mapped[registry_path] = alias_profile
    return mapped


def discover_canonical_ready_profiles(
    capture_root: str | Path,
) -> dict[str, dict[str, object]]:
    root = Path(capture_root).expanduser().resolve()
    ready: dict[str, dict[str, object]] = {}
    if not root.exists():
        return ready
    for pointer in sorted(root.glob("*/evidence/current.json")):
        asset_dir = pointer.parent.parent
        try:
            health = inspect_interpretation_health(asset_dir)
            profile = profile_blueprint_evidence(asset_dir)
        except (OSError, ValueError):
            continue
        if str(health.get("status") or "") != "READY":
            continue
        health_asset = health.get("asset")
        health_evidence = health.get("evidence")
        asset = health_asset if isinstance(health_asset, Mapping) else {}
        evidence = (
            health_evidence if isinstance(health_evidence, Mapping) else {}
        )
        profile_asset = profile.get("asset")
        profile_authority = profile.get("authority")
        profile_asset_map = (
            profile_asset if isinstance(profile_asset, Mapping) else {}
        )
        profile_authority_map = (
            profile_authority if isinstance(profile_authority, Mapping) else {}
        )
        object_path = str(asset.get("objectPath") or "")
        if not object_path:
            continue
        if (
            str(profile_asset_map.get("objectPath") or "") != object_path
            or str(profile_asset_map.get("assetId") or "")
            != str(asset.get("assetId") or "")
            or str(profile_asset_map.get("revisionId") or "")
            != str(evidence.get("revisionId") or "")
            or str(profile_authority_map.get("manifestSha256") or "")
            != str(evidence.get("manifestSha256") or "")
            or str(profile_authority_map.get("pointerSha256") or "")
            != str(evidence.get("pointerSha256") or "")
            or profile_authority_map.get("captureIntegrityStatus") != "PASS"
        ):
            continue
        ready[object_path] = profile
    return ready


def profile_native_evidence(
    evidence_json: str | Path,
    target_paths: Iterable[str],
) -> dict[str, dict[str, object]]:
    payload = _load_json(evidence_json)
    if not isinstance(payload, Mapping):
        raise ValueError("native evidence must be an object")
    trust = payload.get("trust")
    trust_status = (
        str(trust.get("status") or "UNKNOWN") if isinstance(trust, Mapping) else "UNKNOWN"
    )
    schema = str(payload.get("schema") or "")
    evidence_set_id = str(payload.get("evidenceSetId") or "")
    provenance_value = payload.get("provenance")
    provenance = provenance_value if isinstance(provenance_value, Mapping) else {}
    pdb_value = provenance.get("pdb")
    pdb = pdb_value if isinstance(pdb_value, Mapping) else {}
    source_is_verified = (
        schema == "blueprint-to-code-native-evidence-set/v2"
        and evidence_set_id.startswith("native-set://")
        and trust_status == "VERIFIED"
        and pdb.get("loaded") is True
        and pdb.get("matchesBinary") is True
    )
    targets_value = payload.get("targets")
    targets = targets_value if isinstance(targets_value, Sequence) else []
    result: dict[str, dict[str, object]] = {}
    for target_path in target_paths:
        leaf = str(target_path).rsplit(".", 1)[-1]
        evidence_rows: dict[str, dict[str, str]] = {}
        for target in targets:
            if not isinstance(target, Mapping):
                continue
            candidates: list[object] = [target]
            for key in ("incomingCallers", "callSites"):
                values = target.get(key)
                if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                    candidates.extend(values)
            for candidate in candidates:
                if not isinstance(candidate, Mapping):
                    continue
                text = " | ".join(str(value) for value in candidate.values())
                if leaf.casefold() not in text.casefold():
                    continue
                evidence_id = str(candidate.get("evidenceId") or "")
                if not evidence_id.startswith("native://"):
                    continue
                evidence_rows[evidence_id] = {
                    "evidenceRef": evidence_id,
                    "qualifiedName": str(
                        candidate.get("qualifiedName") or candidate.get("name") or ""
                    ),
                }
        result[str(target_path)] = {
            "trustStatus": trust_status,
            "symbolCount": len(evidence_rows),
            "evidenceRefs": list(evidence_rows.values())[:50],
            "formallyQueryable": source_is_verified and bool(evidence_rows),
            "evidenceRevisionId": evidence_set_id,
        }
    return result


def discover_data_asset_profiles(
    capture_roots: Iterable[str | Path],
    *,
    canonical_ready_profiles: Mapping[str, Mapping[str, object]] | None = None,
) -> dict[str, dict[str, object]]:
    canonical = canonical_ready_profiles or {}
    planned: dict[str, list[str]] = defaultdict(list)
    batch_profiles: dict[str, dict[str, object]] = {}
    for raw_root in capture_roots:
        plan_path = Path(raw_root).expanduser().resolve() / "batch_plan.json"
        if not plan_path.exists():
            continue
        plan = _load_json(plan_path)
        if not isinstance(plan, Mapping):
            continue
        samples = plan.get("samples")
        if not isinstance(samples, Sequence):
            continue
        if not any(isinstance(item, Mapping) and item.get("sourceClass") for item in samples):
            continue
        profiles = discover_blueprint_profiles([plan_path.parent])
        batch_profiles.update(profiles)
        plan_targets: dict[str, list[str]] = defaultdict(list)
        for item in samples:
            if not isinstance(item, Mapping):
                continue
            source_class = str(item.get("sourceClass") or "")
            target_path = str(item.get("targetPath") or "")
            if (
                source_class
                and target_path
                and target_path not in plan_targets[source_class]
            ):
                plan_targets[source_class].append(target_path)
        for source_class, target_paths in plan_targets.items():
            planned[source_class] = target_paths
    result: dict[str, dict[str, object]] = {}
    for source_class, target_paths in planned.items():
        objects: list[dict[str, object]] = []
        for target_path in target_paths:
            is_canonical = target_path in canonical
            profile = canonical.get(target_path) or batch_profiles.get(target_path)
            if profile is None:
                continue
            asset_value = profile.get("asset")
            asset = asset_value if isinstance(asset_value, Mapping) else {}
            authority_value = profile.get("authority")
            authority = (
                authority_value if isinstance(authority_value, Mapping) else {}
            )
            content_value = profile.get("content")
            content = content_value if isinstance(content_value, Mapping) else {}
            gaps_value = profile.get("gaps")
            gaps = gaps_value if isinstance(gaps_value, Mapping) else {}
            object_path = str(asset.get("objectPath") or "")
            revision_id = str(asset.get("revisionId") or "")
            identity_bound = object_path == target_path and bool(revision_id)
            asset_field_count = int(content.get("assetFieldCount") or 0)
            records_value = profile.get("_searchRecords")
            records = (
                records_value
                if isinstance(records_value, Sequence)
                and not isinstance(records_value, (str, bytes))
                else []
            )
            evidence_refs = sorted(
                {
                    str(record.get("ref") or "")
                    for record in records
                    if isinstance(record, Mapping)
                    and str(record.get("kind") or "")
                    in {"asset_field", "graph", "node", "property"}
                    and str(record.get("ref") or "").startswith("bp://")
                }
            )
            canonical_ready = (
                is_canonical
                and identity_bound
                and authority.get("captureIntegrityStatus") == "PASS"
                and asset_field_count > 0
            )
            objects.append(
                {
                    "objectPath": target_path,
                    "revisionId": revision_id,
                    "assetFieldCount": asset_field_count,
                    "graphCount": int(content.get("graphCount") or 0),
                    "nodeCount": int(content.get("nodeCount") or 0),
                    "pinCount": int(content.get("pinCount") or 0),
                    "linkCount": int(content.get("edgeCount") or 0),
                    "blockingGapCount": int(
                        gaps.get("blockingStatusCount") or 0
                    ),
                    "canonicalCurrent": canonical_ready,
                    "evidenceRefs": evidence_refs[:50],
                }
            )
        ready_objects = [
            item for item in objects if item.get("canonicalCurrent") is True
        ]
        revision_ids = sorted(
            str(item.get("revisionId") or "")
            for item in ready_objects
            if str(item.get("revisionId") or "")
        )
        aggregate_revision = (
            "aggregate-"
            + hashlib.sha256(
                json.dumps(
                    revision_ids,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:24]
            if revision_ids
            else ""
        )
        formally_queryable = bool(target_paths) and (
            len(ready_objects) == len(target_paths)
        )
        result[source_class] = {
            "captureIntegrityStatus": (
                "PASS"
                if formally_queryable
                else "DEGRADED"
            ),
            "sourceClassObjectPath": source_class,
            "plannedObjectCount": len(target_paths),
            "objectSampleCount": len(objects),
            "readyObjectCount": len(ready_objects),
            "businessFactCount": sum(
                int(item.get("assetFieldCount") or 0) for item in objects
            ),
            "formallyQueryable": formally_queryable,
            "evidenceRevisionId": aggregate_revision,
            "revisionIds": revision_ids,
            "objects": objects,
        }
    return result


def render_report_zh(report: Mapping[str, object]) -> str:
    counts = report.get("counts")
    count_map = counts if isinstance(counts, Mapping) else {}
    samples_value = report.get("samples")
    sample_rows = (
        [item for item in samples_value if isinstance(item, Mapping)]
        if isinstance(samples_value, Sequence)
        and not isinstance(samples_value, (str, bytes))
        else []
    )
    lineage_value = report.get("classificationLineage")
    lineage = lineage_value if isinstance(lineage_value, Mapping) else {}
    bindings_value = report.get("inputBindings")
    bindings = bindings_value if isinstance(bindings_value, Mapping) else {}
    sample_plan_binding_value = bindings.get("samplePlan")
    sample_plan_binding = (
        sample_plan_binding_value
        if isinstance(sample_plan_binding_value, Mapping)
        else {}
    )
    lines = [
        "# BTC 全类别资产自动读取能力基线",
        "",
        "这份报告测的是 BTC 能否把不同类别资产读到可回答、可引用、可重查的结论闭环。",
        "READY 和 validator PASS 只作为独立检查，不代替内容闭环。",
        "",
        "## 总览",
        "",
        f"- 固定样本：{count_map.get('samples', 0)}",
        f"- 类别+状态分层：{count_map.get('categoryStatusStrata', 0)}",
        f"- 窄问题精确闭环：{count_map.get('closedExact', 0)}",
        f"- 窄问题含推测闭环：{count_map.get('closedHeuristic', 0)}",
        f"- 部分读取：{count_map.get('partial', 0)}",
        f"- 只有身份：{count_map.get('identityOnly', 0)}",
        f"- 当前不支持：{count_map.get('unsupported', 0)}",
        f"- 读取失败：{count_map.get('failed', 0)}",
        f"- 尚未测试：{count_map.get('notRun', 0)}",
        f"- 已在 P0 首页 READY：{count_map.get('ready', 0)}",
        f"  - READY 且窄问题精确闭环：{count_map.get('readyClosedExact', 0)}",
        f"  - READY 但仍只能部分回答：{count_map.get('readyPartial', 0)}",
        f"  - 未发布但窄问题已精确闭环：{count_map.get('nonReadyClosedExact', 0)}",
        "",
        "## 分类样本来源",
        "",
        f"- 抽样计划：`{lineage.get('samplePlanSchema', '')}`。",
        f"- 抽样算法：`{lineage.get('selectionAlgorithm', '')}`。",
        "- 分类分层覆盖：{covered}/{total}。".format(
            covered=lineage.get("categoryGroupsCovered", 0),
            total=lineage.get("categoryGroupsTotal", 0),
        ),
        f"- 固定计划内容 SHA-256：`{sample_plan_binding.get('sha256', '')}`。",
        "- 每个 Blueprint Evidence 行都保留 clusterId、精确目标路径、玩家问题和"
        " Evidence revision；READY 行强制改用 canonical current Evidence，批次证据"
        "不能冒充当前发布版本。DataAsset、Native 和未运行样本按各自证据边界记录。",
        "",
        "## 按类别汇总",
        "",
        "| 中文类别 | 代码 | 样本数 | 闭环情况 | READY |",
        "|---|---|---:|---|---:|",
    ]
    category_rows: dict[tuple[str, str], list[Mapping[str, object]]] = defaultdict(
        list
    )
    for raw in sample_rows:
        group = raw.get("plannedStratum")
        group_map = group if isinstance(group, Mapping) else {}
        code = str(group_map.get("code") or "")
        label = str(group_map.get("labelZh") or code)
        category_rows[(code, label)].append(raw)
    for (code, label), rows in sorted(
        category_rows.items(), key=lambda item: (item[0][1], item[0][0])
    ):
        category_statuses = Counter(
            str((row.get("result") or {}).get("benchmarkClosureStatus") or "")
            for row in rows
            if isinstance(row.get("result"), Mapping)
        )
        status_text = ", ".join(
            f"{status}={count}"
            for status, count in sorted(category_statuses.items())
            if status
        )
        ready_count = sum(
            1
            for row in rows
            if isinstance(row.get("p0"), Mapping)
            and bool(row["p0"].get("ready"))
        )
        lines.append(
            f"| {label} | `{code}` | {len(rows)} | {status_text} | {ready_count} |"
        )
    lines.extend(
        [
            "",
            "## 逐样本结果",
            "",
            "| # | 中文类别 | 样本 | 内容 | 闭环 | READY | 主要阻断 |",
            "|---:|---|---|---|---|:---:|---|",
        ]
    )
    for raw in sample_rows:
        group = raw.get("plannedStratum")
        group_map = group if isinstance(group, Mapping) else {}
        result = raw.get("result")
        result_map = result if isinstance(result, Mapping) else {}
        blockers = result_map.get("blockerCodes")
        blocker_text = (
            ", ".join(str(value) for value in blockers)
            if isinstance(blockers, Sequence)
            and not isinstance(blockers, (str, bytes))
            else ""
        )
        p0 = raw.get("p0")
        ready = bool(p0.get("ready")) if isinstance(p0, Mapping) else False
        lines.append(
            "| {index} | {label} | `{target}` | {content} | {closure} | {ready} | {blockers} |".format(
                index=raw.get("sampleIndex", ""),
                label=group_map.get("labelZh") or group_map.get("code") or "",
                target=str(raw.get("targetPath") or "").rsplit("/", 1)[-1],
                content=result_map.get("contentRecoveryStatus", ""),
                closure=result_map.get("benchmarkClosureStatus", ""),
                ready="是" if ready else "否",
                blockers=blocker_text,
            )
        )
    lines.extend(
        [
            "",
            "## 逐样本玩家问题与证据",
            "",
        ]
    )
    for raw in sample_rows:
        group = raw.get("plannedStratum")
        group_map = group if isinstance(group, Mapping) else {}
        result = raw.get("result")
        result_map = result if isinstance(result, Mapping) else {}
        question = result_map.get("question")
        question_map = question if isinstance(question, Mapping) else {}
        claims = result_map.get("claims")
        claim_rows = (
            [item for item in claims if isinstance(item, Mapping)]
            if isinstance(claims, Sequence) and not isinstance(claims, (str, bytes))
            else []
        )
        blocking_gaps = result_map.get("blockingGaps")
        gap_rows = (
            [str(item) for item in blocking_gaps]
            if isinstance(blocking_gaps, Sequence)
            and not isinstance(blocking_gaps, (str, bytes))
            else []
        )
        blocker_codes_value = result_map.get("blockerCodes")
        blocker_codes = (
            [str(item) for item in blocker_codes_value]
            if isinstance(blocker_codes_value, Sequence)
            and not isinstance(blocker_codes_value, (str, bytes))
            else []
        )
        closure_status = str(result_map.get("benchmarkClosureStatus") or "")
        label = group_map.get("labelZh") or group_map.get("code") or ""
        target = str(raw.get("targetPath") or "").rsplit("/", 1)[-1]
        lines.extend(
            [
                f"### #{raw.get('sampleIndex', '')} {label} — `{target}`",
                "",
                f"- 玩家问题：{question_map.get('questionZh') or '未定义'}",
                f"- 结果：`{result_map.get('benchmarkClosureStatus', '')}`；内容：`{result_map.get('contentRecoveryStatus', '')}`",
            ]
        )
        if claim_rows:
            lines.append("- 已确认结论：")
            for claim in claim_rows:
                lines.append(f"  - {claim.get('textZh') or ''}")
                refs = claim.get("evidenceRefs")
                if isinstance(refs, Sequence) and not isinstance(
                    refs, (str, bytes)
                ):
                    for ref in refs:
                        lines.append(f"    - `{ref}`")
        else:
            lines.append("- 已确认结论：本问题尚未形成可发布闭环。")
        if gap_rows:
            lines.append("- 阻断：")
            lines.extend(f"  - {gap}" for gap in gap_rows)
        elif closure_status not in {"CLOSED_EXACT", "CLOSED_HEURISTIC"}:
            if blocker_codes:
                lines.append(
                    "- 阻断：结构化失败码："
                    + ", ".join(f"`{code}`" for code in blocker_codes)
                    + "。"
                )
            else:
                lines.append("- 阻断：尚未记录详细说明；不能视为无阻断。")
        else:
            lines.append("- 阻断：本次所选窄问题无阻断；不代表整个资产机制已完整恢复。")
        lines.append("")
    lines.extend(
        [
            "## 判定边界",
            "",
            "- `CLOSED_EXACT`：核心问题的每个结论都有当前版本的精确证据，引用可重查。",
            "- `CLOSED_HEURISTIC`：能回答，但核心链路仍含推测连接。",
            "- `PARTIAL`：读到了业务内容，但核心字段、连接、关联资产或查询入口仍缺。",
            "- `IDENTITY_ONLY`：只知道它是谁，没读出玩法或配置内容。",
            "- `UNSUPPORTED`：当前没有适配这个资产类型的读取器。",
            "- `FAILED`：按设计应支持，但解析、公开查询或引用重查失败。",
            "- `NOT_RUN`：尚未用 BTC 实测，既有分类不能冒充本次能力通过。",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-plan", type=Path, required=True)
    parser.add_argument("--question-contracts", type=Path, required=True)
    parser.add_argument("--capture-root", type=Path, action="append", default=[])
    parser.add_argument("--canonical-capture-root", type=Path)
    parser.add_argument("--native-evidence-json", type=Path)
    parser.add_argument("--reviewed-assessments", type=Path)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    sample_plan = _load_json(args.sample_plan)
    contracts_payload = _load_json(args.question_contracts)
    contracts = (
        contracts_payload.get("contracts", contracts_payload)
        if isinstance(contracts_payload, Mapping)
        else contracts_payload
    )
    if not isinstance(sample_plan, Mapping) or not isinstance(contracts, Mapping):
        raise ValueError("sample plan and question contracts must be JSON objects")
    samples = validate_sample_plan(sample_plan)
    profiles = apply_batch_aliases(
        discover_blueprint_profiles(args.capture_root),
        args.capture_root,
    )
    ready_profiles = (
        discover_canonical_ready_profiles(args.canonical_capture_root)
        if args.canonical_capture_root is not None
        else {}
    )
    native_targets = [
        str(item["targetPath"])
        for item in samples
        if str(item.get("targetKind") or "") == "NATIVE_CLASS"
    ]
    native_profiles = (
        profile_native_evidence(args.native_evidence_json, native_targets)
        if args.native_evidence_json is not None
        else {}
    )
    data_profiles = discover_data_asset_profiles(
        args.capture_root,
        canonical_ready_profiles=ready_profiles,
    )
    reviewed_payload = (
        _load_json(args.reviewed_assessments)
        if args.reviewed_assessments is not None
        else {}
    )
    reviewed = (
        reviewed_payload.get("samples", reviewed_payload)
        if isinstance(reviewed_payload, Mapping)
        else {}
    )
    if not isinstance(reviewed, Mapping):
        raise ValueError("reviewed assessments must be a JSON object")
    report = build_capability_report(
        sample_plan=sample_plan,
        question_contracts=contracts,
        blueprint_profiles=profiles,
        canonical_ready_profiles=ready_profiles,
        native_profiles=native_profiles,
        data_asset_profiles=data_profiles,
        reviewed_assessments={str(key): value for key, value in reviewed.items()},
        input_bindings={
            "samplePlan": _file_binding(args.sample_plan, sample_plan),
            "questionContracts": _file_binding(
                args.question_contracts,
                contracts_payload,
            ),
            "reviewedAssessments": (
                _file_binding(args.reviewed_assessments, reviewed_payload)
                if args.reviewed_assessments is not None
                else None
            ),
            "nativeEvidence": (
                _file_binding(args.native_evidence_json)
                if args.native_evidence_json is not None
                else None
            ),
            "generator": _file_binding(Path(__file__).resolve()),
        },
    )
    _write_json(args.output_json, report)
    if args.output_markdown is not None:
        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_markdown.write_text(render_report_zh(report), encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
