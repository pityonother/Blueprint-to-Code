from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..evidence_publication import _lexical_absolute, _require_plain_path_chain
from ..evidence_repository import (
    ResolvedEvidenceState,
    _read_bound_file_bytes,
    evidence_manifest_payload,
    open_bound_evidence_database,
    resolve_asset_evidence_state,
)
from .contracts import (
    CURRENT_SCHEMA,
    GAPS_SCHEMA,
    INTERPRETATION_SCHEMA,
    INTERPRETER_VERSION,
    MANIFEST_SCHEMA,
    PSEUDOCODE_HEADER,
    STATEMENT_KINDS,
    STATEMENT_STATUSES,
    TRACE_SCHEMA,
    InterpretationArtifactInvalid,
    LoadedInterpretation,
    canonical_json_bytes,
    semantic_digest,
    sha256_bytes,
)
from .engine import _build_from_source
from .render import gaps_payload, render_markdown, render_pseudocode_and_trace
from .source import load_interpretation_source


_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_REVISION_RE = re.compile(r"[0-9a-f]{24}")
_ASSET_RE = re.compile(r"[0-9a-f]{24}")
_POINTER_KEYS = {
    "schema",
    "revisionId",
    "manifest",
    "manifestSha256",
    "evidenceRevisionId",
    "evidenceManifestSha256",
}
_MANIFEST_KEYS = {
    "schema",
    "revisionId",
    "assetId",
    "objectPath",
    "evidenceRevisionId",
    "evidenceManifestSha256",
    "interpreterVersion",
    "schemaVersion",
    "semanticDigest",
    "generatedAt",
    "artifacts",
}
_ARTIFACT_FILES = {
    "interpretationJson": ("interpretation.json", 16 * 1024 * 1024),
    "interpretationMarkdown": ("interpretation.md", 8 * 1024 * 1024),
    "trace": ("trace.json", 16 * 1024 * 1024),
    "gaps": ("gaps.json", 8 * 1024 * 1024),
    "pseudocode": ("pseudocode.txt", 8 * 1024 * 1024),
}
_REVISION_FILE_NAMES = {
    "manifest.json",
    *(filename for filename, _maximum in _ARTIFACT_FILES.values()),
}
_INTERPRETATION_KEYS = {
    "schema",
    "assetId",
    "objectPath",
    "evidenceRevisionId",
    "evidenceManifestSha256",
    "interpreterVersion",
    "schemaVersion",
    "selection",
    "assetSummary",
    "controlFlow",
    "dataFlow",
    "statements",
    "heuristicReviewHints",
    "semanticDigest",
    "generatedAt",
}
_STATEMENT_KEYS = {
    "id",
    "kind",
    "text",
    "status",
    "evidenceRefs",
    "gapRefs",
    "graphRef",
    "nodeRef",
    "sourceOrder",
}
_GAP_KEYS = {
    "id",
    "code",
    "status",
    "graphRef",
    "nodeRef",
    "pinRef",
    "detail",
    "evidenceRefs",
    "source",
}
_TRACE_STATEMENT_KEYS = {
    "statementId",
    "graphRef",
    "nodeRef",
    "evidenceRefs",
    "gapRefs",
}
_TRACE_LINE_KEYS = {
    "line",
    "startByte",
    "endByte",
    "executable",
    "statementId",
    "evidenceRefs",
}


def _invalid(code: str, message: str) -> InterpretationArtifactInvalid:
    return InterpretationArtifactInvalid(code, message)


def _require_exact_revision_files(revision_dir: Path) -> None:
    try:
        entries = list(revision_dir.iterdir())
    except OSError as exc:
        raise _invalid(
            "INTERPRETATION_REVISION_FILES_INVALID",
            "Interpretation revision entries could not be inspected safely.",
        ) from exc
    names = {entry.name for entry in entries}
    if len(entries) != len(names) or names != _REVISION_FILE_NAMES:
        raise _invalid(
            "INTERPRETATION_REVISION_FILES_INVALID",
            "Interpretation revision must contain exactly the v1 manifest and artifacts.",
        )
    for entry in entries:
        try:
            _require_plain_path_chain(
                entry,
                label="interpretation revision artifact",
            )
            regular_file = entry.is_file()
        except (OSError, ValueError) as exc:
            raise _invalid(
                "INTERPRETATION_REVISION_FILES_INVALID",
                "Interpretation revision entries must be plain regular files.",
            ) from exc
        if not regular_file:
            raise _invalid(
                "INTERPRETATION_REVISION_FILES_INVALID",
                "Interpretation revision entries must be plain regular files.",
            )


def _json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {constant}")
            ),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise _invalid(
            "INTERPRETATION_JSON_INVALID",
            f"{label} is not strict UTF-8 JSON.",
        ) from exc
    if not isinstance(value, dict):
        raise _invalid(
            "INTERPRETATION_JSON_INVALID",
            f"{label} must be a JSON object.",
        )
    return value


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _required_text(
    payload: dict[str, Any],
    key: str,
    *,
    label: str,
    maximum: int = 4096,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise _invalid(
            "INTERPRETATION_FIELDS_INVALID",
            f"{label}.{key} must be non-empty bounded text.",
        )
    return value


def _required_hash(payload: dict[str, Any], key: str, *, label: str) -> str:
    value = _required_text(payload, key, label=label, maximum=64)
    if _SHA256_RE.fullmatch(value) is None:
        raise _invalid(
            "INTERPRETATION_HASH_INVALID",
            f"{label}.{key} must be lowercase SHA-256.",
        )
    return value


def _decode_text(raw: bytes, *, label: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _invalid(
            "INTERPRETATION_TEXT_INVALID",
            f"{label} must be UTF-8 text.",
        ) from exc


def _required_ref_list(
    value: object,
    *,
    label: str,
    evidence_refs: frozenset[str],
) -> list[str]:
    if not isinstance(value, list):
        raise _invalid(
            "INTERPRETATION_REFERENCES_INVALID",
            f"{label} must be an array of exact Evidence refs.",
        )
    refs: list[str] = []
    for raw_ref in value:
        if not isinstance(raw_ref, str) or raw_ref not in evidence_refs:
            raise _invalid(
                "INTERPRETATION_REFERENCES_INVALID",
                f"{label} contains a non-existent Evidence ref.",
            )
        refs.append(raw_ref)
    if len(refs) != len(set(refs)):
        raise _invalid(
            "INTERPRETATION_REFERENCES_INVALID",
            f"{label} contains duplicate Evidence refs.",
        )
    return refs


def _required_optional_ref(
    value: object,
    *,
    label: str,
    evidence_refs: frozenset[str],
) -> str:
    if value == "":
        return ""
    if not isinstance(value, str) or value not in evidence_refs:
        raise _invalid(
            "INTERPRETATION_REFERENCES_INVALID",
            f"{label} must be empty or an exact Evidence ref.",
        )
    return value


def _evidence_binding(state: ResolvedEvidenceState) -> tuple[str, str, str]:
    manifest = evidence_manifest_payload(state)
    revision_id = str(manifest.get("revisionId") or "")
    manifest_sha = str(state.manifest_sha256 or "")
    pointer_sha = str(state.pointer_sha256 or "")
    return revision_id, manifest_sha, pointer_sha


def _require_authoritative_evidence(state: ResolvedEvidenceState) -> None:
    if (
        state.source_kind != "INDEXED_V3_CURRENT"
        or not state.release_authority
        or state.migration_required
        or not state.manifest_sha256
        or not state.pointer_sha256
    ):
        raise _invalid(
            "INTERPRETATION_EVIDENCE_NOT_AUTHORITATIVE",
            "Interpretation requires one authoritative current v3 Evidence revision.",
        )


def _read_artifacts(
    revision_dir: Path,
    manifest: dict[str, Any],
) -> dict[str, bytes]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(_ARTIFACT_FILES):
        raise _invalid(
            "INTERPRETATION_MANIFEST_FIELDS_INVALID",
            "Interpretation manifest artifacts do not match the v1 contract.",
        )
    loaded: dict[str, bytes] = {}
    for key, (expected_name, maximum) in _ARTIFACT_FILES.items():
        descriptor = artifacts.get(key)
        if not isinstance(descriptor, dict) or set(descriptor) != {"path", "bytes", "sha256"}:
            raise _invalid(
                "INTERPRETATION_MANIFEST_FIELDS_INVALID",
                f"Artifact descriptor {key} is invalid.",
            )
        relative = _required_text(descriptor, "path", label=key, maximum=64)
        if relative != expected_name:
            raise _invalid(
                "INTERPRETATION_ARTIFACT_PATH_INVALID",
                f"Artifact {key} must use its canonical revision-local name.",
            )
        size = descriptor.get("bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0 or size > maximum:
            raise _invalid(
                "INTERPRETATION_ARTIFACT_SIZE_INVALID",
                f"Artifact {key} has an invalid byte count.",
            )
        expected_sha = _required_hash(descriptor, "sha256", label=key)
        artifact_path = revision_dir / expected_name
        raw = _read_bound_file_bytes(
            artifact_path,
            label=f"interpretation artifact {key}",
            maximum_size=maximum,
        )
        if len(raw) != size:
            raise _invalid(
                "INTERPRETATION_ARTIFACT_SIZE_MISMATCH",
                f"Artifact {key} does not match its manifest byte count.",
            )
        if sha256_bytes(raw) != expected_sha:
            raise _invalid(
                "INTERPRETATION_ARTIFACT_HASH_MISMATCH",
                f"Artifact {key} does not match its manifest hash.",
            )
        loaded[key] = raw
    return loaded


def _evidence_refs(state: ResolvedEvidenceState) -> frozenset[str]:
    queries = (
        ("graphs", "graph_ref"),
        ("nodes", "node_ref"),
        ("pins", "pin_ref"),
        ("edges", "edge_ref"),
        ("edge_observations", "observation_ref"),
        ("class_defaults", "default_ref"),
        ("diagnostics", "diagnostic_ref"),
        ('"references"', "reference_ref"),
    )
    with open_bound_evidence_database(state) as connection:
        return frozenset(
            str(row[0])
            for table, column in queries
            for row in connection.execute(f"SELECT {column} FROM {table}")
        )


def _validate_contract(
    *,
    manifest: dict[str, Any],
    interpretation: dict[str, Any],
    trace: dict[str, Any],
    gaps: dict[str, Any],
    pseudocode: str,
    markdown: str,
    evidence_refs: frozenset[str],
) -> None:
    if (
        set(interpretation) != _INTERPRETATION_KEYS
        or interpretation.get("schema") != INTERPRETATION_SCHEMA
    ):
        raise _invalid(
            "INTERPRETATION_SCHEMA_INVALID",
            "Interpretation JSON fields do not match the v1 schema.",
        )
    if set(trace) != {
        "schema",
        "assetId",
        "evidenceRevisionId",
        "evidenceManifestSha256",
        "semanticDigest",
        "statements",
        "pseudocodeLines",
    } or trace.get("schema") != TRACE_SCHEMA:
        raise _invalid(
            "INTERPRETATION_SCHEMA_INVALID",
            "Trace fields do not match the v1 schema.",
        )
    if set(gaps) != {
        "schema",
        "assetId",
        "evidenceRevisionId",
        "evidenceManifestSha256",
        "semanticDigest",
        "counts",
        "items",
    } or gaps.get("schema") != GAPS_SCHEMA:
        raise _invalid(
            "INTERPRETATION_SCHEMA_INVALID",
            "Gap fields do not match the v1 schema.",
        )
    asset_id = _required_text(manifest, "assetId", label="manifest", maximum=24)
    if _ASSET_RE.fullmatch(asset_id) is None:
        raise _invalid(
            "INTERPRETATION_IDENTITY_INVALID",
            "Interpretation assetId must be 24 lowercase hexadecimal characters.",
        )
    evidence_revision = _required_text(
        manifest, "evidenceRevisionId", label="manifest", maximum=24
    )
    if _REVISION_RE.fullmatch(evidence_revision) is None:
        raise _invalid(
            "INTERPRETATION_IDENTITY_INVALID",
            "Interpretation Evidence revision id is invalid.",
        )
    _required_text(manifest, "objectPath", label="manifest")
    _required_hash(manifest, "evidenceManifestSha256", label="manifest")
    _required_hash(manifest, "semanticDigest", label="manifest")
    _required_text(manifest, "generatedAt", label="manifest", maximum=128)
    if manifest.get("interpreterVersion") != INTERPRETER_VERSION:
        raise _invalid(
            "INTERPRETATION_VERSION_INVALID",
            "Interpretation interpreter version is not supported.",
        )
    if manifest.get("schemaVersion") != INTERPRETATION_SCHEMA:
        raise _invalid(
            "INTERPRETATION_VERSION_INVALID",
            "Interpretation schemaVersion is not supported.",
        )
    identity_keys = (
        "assetId",
        "objectPath",
        "evidenceRevisionId",
        "evidenceManifestSha256",
        "interpreterVersion",
        "schemaVersion",
        "semanticDigest",
        "generatedAt",
    )
    for key in identity_keys:
        if interpretation.get(key) != manifest.get(key):
            raise _invalid(
                "INTERPRETATION_IDENTITY_MISMATCH",
                f"Interpretation identity {key} differs from its manifest.",
            )
    for document, label in ((trace, "trace"), (gaps, "gaps")):
        for key in ("assetId", "evidenceRevisionId", "evidenceManifestSha256", "semanticDigest"):
            if document.get(key) != interpretation.get(key):
                raise _invalid(
                    "INTERPRETATION_IDENTITY_MISMATCH",
                    f"{label} identity {key} differs from Interpretation JSON.",
                )
    gap_items = gaps.get("items")
    if not isinstance(gap_items, list):
        raise _invalid(
            "INTERPRETATION_GAPS_INVALID",
            "gaps.items must be an array.",
        )
    gap_by_id: dict[str, dict[str, Any]] = {}
    observed_counts: dict[str, int] = {}
    for gap in gap_items:
        if not isinstance(gap, dict) or set(gap) != _GAP_KEYS:
            raise _invalid(
                "INTERPRETATION_GAPS_INVALID",
                "Every gap must match the v1 gap contract.",
            )
        gap_id = _required_text(gap, "id", label="gap", maximum=256)
        if not gap_id.startswith("gap://") or gap_id in gap_by_id:
            raise _invalid(
                "INTERPRETATION_GAPS_INVALID",
                "Gap identities must be unique gap:// refs.",
            )
        status = gap.get("status")
        if status not in {"SOURCE_NOT_AVAILABLE", "NOT_RECOVERED", "AMBIGUOUS"}:
            raise _invalid(
                "INTERPRETATION_GAPS_INVALID",
                "Gap status is outside the v1 non-confirmed contract.",
            )
        code = _required_text(gap, "code", label="gap", maximum=128)
        _required_text(gap, "detail", label="gap", maximum=32_768)
        _required_text(gap, "source", label="gap", maximum=128)
        _required_optional_ref(
            gap.get("graphRef"), label="gap.graphRef", evidence_refs=evidence_refs
        )
        _required_optional_ref(
            gap.get("nodeRef"), label="gap.nodeRef", evidence_refs=evidence_refs
        )
        _required_optional_ref(
            gap.get("pinRef"), label="gap.pinRef", evidence_refs=evidence_refs
        )
        _required_ref_list(
            gap.get("evidenceRefs"),
            label="gap.evidenceRefs",
            evidence_refs=evidence_refs,
        )
        gap_by_id[gap_id] = gap
        observed_counts[code] = observed_counts.get(code, 0) + 1
    if gaps.get("counts") != dict(sorted(observed_counts.items())):
        raise _invalid(
            "INTERPRETATION_GAPS_INVALID",
            "Gap counts do not match the immutable gap items.",
        )
    projection = {
        key: value
        for key, value in interpretation.items()
        if key not in {"semanticDigest", "generatedAt"}
    }
    projection["gaps"] = gap_items
    if semantic_digest(projection) != interpretation.get("semanticDigest"):
        raise _invalid(
            "INTERPRETATION_SEMANTIC_DIGEST_MISMATCH",
            "Interpretation semantic digest does not match its semantic content.",
        )
    statements = interpretation.get("statements")
    if not isinstance(statements, list):
        raise _invalid(
            "INTERPRETATION_STATEMENTS_INVALID",
            "Interpretation statements must be an array.",
        )
    statement_by_id: dict[str, dict[str, Any]] = {}
    source_orders: set[int] = set()
    gap_statement_counts = {gap_id: 0 for gap_id in gap_by_id}
    for statement in statements:
        if not isinstance(statement, dict) or set(statement) != _STATEMENT_KEYS:
            raise _invalid(
                "INTERPRETATION_STATEMENTS_INVALID",
                "Every Interpretation statement must match the v1 contract.",
            )
        statement_id = _required_text(
            statement, "id", label="statement", maximum=256
        )
        if not statement_id.startswith("statement://") or statement_id in statement_by_id:
            raise _invalid(
                "INTERPRETATION_STATEMENTS_INVALID",
                "Statement identities must be unique statement:// refs.",
            )
        if statement.get("kind") not in STATEMENT_KINDS:
            raise _invalid(
                "INTERPRETATION_STATEMENTS_INVALID",
                "Statement kind is outside the v1 contract.",
            )
        if statement.get("status") not in STATEMENT_STATUSES:
            raise _invalid(
                "INTERPRETATION_STATEMENTS_INVALID",
                "Statement status is outside the v1 contract.",
            )
        _required_text(statement, "text", label="statement", maximum=32_768)
        refs = _required_ref_list(
            statement.get("evidenceRefs"),
            label="statement.evidenceRefs",
            evidence_refs=evidence_refs,
        )
        if statement.get("status") == "CONFIRMED" and not refs:
            raise _invalid(
                "CONFIRMED_STATEMENT_WITHOUT_EVIDENCE",
                "A confirmed statement lacks exact current Evidence refs.",
            )
        _required_optional_ref(
            statement.get("graphRef"),
            label="statement.graphRef",
            evidence_refs=evidence_refs,
        )
        _required_optional_ref(
            statement.get("nodeRef"),
            label="statement.nodeRef",
            evidence_refs=evidence_refs,
        )
        source_order = statement.get("sourceOrder")
        if (
            isinstance(source_order, bool)
            or not isinstance(source_order, int)
            or source_order < 0
            or source_order in source_orders
        ):
            raise _invalid(
                "INTERPRETATION_STATEMENTS_INVALID",
                "Statement sourceOrder values must be unique non-negative integers.",
            )
        source_orders.add(source_order)
        raw_gap_refs = statement.get("gapRefs")
        if not isinstance(raw_gap_refs, list) or any(
            not isinstance(ref, str) or ref not in gap_by_id for ref in raw_gap_refs
        ):
            raise _invalid(
                "INTERPRETATION_STATEMENTS_INVALID",
                "Statement gapRefs must reference exact gaps.",
            )
        if len(raw_gap_refs) != len(set(raw_gap_refs)):
            raise _invalid(
                "INTERPRETATION_STATEMENTS_INVALID",
                "Statement gapRefs must not contain duplicates.",
            )
        if statement.get("kind") == "GAP":
            if len(raw_gap_refs) != 1:
                raise _invalid(
                    "INTERPRETATION_STATEMENTS_INVALID",
                    "Every GAP statement must reference exactly one gap.",
                )
            gap_statement_counts[raw_gap_refs[0]] += 1
        elif raw_gap_refs:
            raise _invalid(
                "INTERPRETATION_STATEMENTS_INVALID",
                "Only GAP statements may carry gapRefs in v1.",
            )
        statement_by_id[statement_id] = statement
    if source_orders != set(range(len(statements))):
        raise _invalid(
            "INTERPRETATION_STATEMENTS_INVALID",
            "Statement sourceOrder values must form one contiguous sequence.",
        )
    if any(count != 1 for count in gap_statement_counts.values()):
        raise _invalid(
            "INTERPRETATION_GAPS_INVALID",
            "Every explicit gap must have exactly one GAP statement.",
        )

    hints = interpretation.get("heuristicReviewHints")
    if not isinstance(hints, list):
        raise _invalid(
            "INTERPRETATION_HINTS_INVALID",
            "heuristicReviewHints must be an array.",
        )
    for hint in hints:
        if (
            not isinstance(hint, dict)
            or hint.get("basis") != "KEYWORD_AND_NAME_HEURISTIC"
            or hint.get("confidence") != "HEURISTIC"
            or hint.get("notEvidence") is not True
        ):
            raise _invalid(
                "INTERPRETATION_HINTS_INVALID",
                "Every heuristic hint must remain explicitly non-evidentiary.",
            )
        _required_optional_ref(
            hint.get("reviewRef"), label="hint.reviewRef", evidence_refs=evidence_refs
        )

    if not pseudocode.startswith(PSEUDOCODE_HEADER + "\n") or "\r" in pseudocode:
        raise _invalid(
            "INTERPRETATION_PSEUDOCODE_INVALID",
            "Pseudocode must use the exact v1 header and LF line endings.",
        )
    pseudocode_bytes = pseudocode.encode("utf-8")
    rows = trace.get("pseudocodeLines")
    encoded_lines = pseudocode.splitlines(keepends=True)
    if (
        not isinstance(rows, list)
        or len(rows) != len(encoded_lines)
        or any(not line.endswith("\n") for line in encoded_lines)
    ):
        raise _invalid(
            "INTERPRETATION_TRACE_INVALID",
            "Trace must contain one row for every pseudocode line.",
        )
    executable_statement_ids: list[str] = []
    byte_offset = 0
    for expected_line, (row, rendered_line) in enumerate(
        zip(rows, encoded_lines, strict=True), start=1
    ):
        if not isinstance(row, dict) or set(row) != _TRACE_LINE_KEYS:
            raise _invalid(
                "INTERPRETATION_TRACE_INVALID",
                "Trace rows must match the v1 line contract.",
            )
        content = rendered_line.removesuffix("\n").encode("utf-8")
        start = row.get("startByte")
        end = row.get("endByte")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or row.get("line") != expected_line
            or start != byte_offset
            or end != byte_offset + len(content)
            or pseudocode_bytes[start:end] != content
            or not isinstance(row.get("executable"), bool)
        ):
            raise _invalid(
                "INTERPRETATION_TRACE_INVALID",
                "Trace byte ranges are invalid.",
            )
        line_refs = _required_ref_list(
            row.get("evidenceRefs"),
            label="trace line evidenceRefs",
            evidence_refs=evidence_refs,
        )
        statement_id = row.get("statementId")
        if row.get("executable") is True:
            if not isinstance(statement_id, str) or statement_id not in statement_by_id:
                raise _invalid(
                    "INTERPRETATION_TRACE_MISSING_STATEMENT",
                    "An executable pseudocode line has no valid statement trace.",
                )
            if line_refs != statement_by_id[statement_id]["evidenceRefs"]:
                raise _invalid(
                    "INTERPRETATION_TRACE_INVALID",
                    "Executable line Evidence refs differ from its statement.",
                )
            executable_statement_ids.append(statement_id)
        elif statement_id != "":
            raise _invalid(
                "INTERPRETATION_TRACE_INVALID",
                "Non-executable pseudocode lines cannot claim statement identities.",
            )
        byte_offset += len(rendered_line.encode("utf-8"))
    if byte_offset != len(pseudocode_bytes) or sorted(executable_statement_ids) != sorted(
        statement_by_id
    ):
        raise _invalid(
            "INTERPRETATION_TRACE_MISSING_STATEMENT",
            "Every statement must map to exactly one executable pseudocode line.",
        )

    trace_statements = trace.get("statements")
    if not isinstance(trace_statements, list) or len(trace_statements) != len(statements):
        raise _invalid(
            "INTERPRETATION_TRACE_INVALID",
            "Trace must contain one statement projection per statement.",
        )
    for projected, statement in zip(trace_statements, statements, strict=True):
        if not isinstance(projected, dict) or set(projected) != _TRACE_STATEMENT_KEYS:
            raise _invalid(
                "INTERPRETATION_TRACE_INVALID",
                "Trace statement projections do not match the v1 contract.",
            )
        expected = {
            "statementId": statement["id"],
            "graphRef": statement["graphRef"],
            "nodeRef": statement["nodeRef"],
            "evidenceRefs": statement["evidenceRefs"],
            "gapRefs": statement["gapRefs"],
        }
        if projected != expected:
            raise _invalid(
                "INTERPRETATION_TRACE_INVALID",
                "Trace statement projections differ from Interpretation statements.",
            )

    expected_gaps = gaps_payload(interpretation, gap_items)
    expected_pseudocode, expected_trace = render_pseudocode_and_trace(
        interpretation,
        gap_items,
    )
    expected_markdown = render_markdown(interpretation, gap_items)
    if gaps != expected_gaps:
        raise _invalid(
            "INTERPRETATION_GAPS_INVALID",
            "Gap document differs from the deterministic v1 renderer.",
        )
    if pseudocode != expected_pseudocode or trace != expected_trace:
        raise _invalid(
            "INTERPRETATION_TRACE_INVALID",
            "Pseudocode or trace differs from the deterministic v1 renderer.",
        )
    if markdown != expected_markdown:
        raise _invalid(
            "INTERPRETATION_MARKDOWN_INVALID",
            "Markdown differs from the deterministic v1 renderer.",
        )


def _validate_derived_content(
    *,
    root: Path,
    state: ResolvedEvidenceState,
    interpretation: dict[str, Any],
    trace: dict[str, Any],
    gaps: dict[str, Any],
    pseudocode: str,
    markdown: str,
) -> None:
    source = load_interpretation_source(
        root,
        evidence_state=state,
    )
    expected_build = _build_from_source(source, budget=100_000)
    expected_interpretation = {
        **expected_build.interpretation,
        "generatedAt": interpretation["generatedAt"],
    }
    expected_gap_items = list(expected_build.gaps["items"])
    expected_gaps = gaps_payload(expected_interpretation, expected_gap_items)
    expected_pseudocode, expected_trace = render_pseudocode_and_trace(
        expected_interpretation,
        expected_gap_items,
    )
    expected_markdown = render_markdown(
        expected_interpretation,
        expected_gap_items,
    )
    if (
        interpretation != expected_interpretation
        or gaps != expected_gaps
        or trace != expected_trace
        or pseudocode != expected_pseudocode
        or markdown != expected_markdown
    ):
        raise _invalid(
            "INTERPRETATION_SEMANTIC_EVIDENCE_MISMATCH",
            "Interpretation artifacts differ from a deterministic rebuild of current Evidence.",
        )


def load_interpretation_revision(
    asset_dir: str | Path,
    revision_id: str,
    *,
    expected_manifest_sha256: str | None = None,
    evidence_state: ResolvedEvidenceState | None = None,
    pointer_path: Path | None = None,
    pointer_sha256: str = "",
) -> LoadedInterpretation:
    root = _lexical_absolute(asset_dir)
    _require_plain_path_chain(root, label="asset directory")
    if _REVISION_RE.fullmatch(str(revision_id)) is None:
        raise _invalid(
            "INTERPRETATION_REVISION_ID_INVALID",
            "Interpretation revision id must be 24 lowercase hex characters.",
        )
    state = evidence_state or resolve_asset_evidence_state(root)
    _require_authoritative_evidence(state)
    revision_dir = root / "interpretation" / "revisions" / str(revision_id)
    _require_plain_path_chain(revision_dir, label="interpretation revision")
    if not revision_dir.is_dir():
        raise FileNotFoundError("INTERPRETATION_REVISION_NOT_FOUND")
    _require_exact_revision_files(revision_dir)
    manifest_path = revision_dir / "manifest.json"
    manifest_raw = _read_bound_file_bytes(
        manifest_path,
        label="interpretation manifest",
        maximum_size=1024 * 1024,
    )
    manifest_sha = sha256_bytes(manifest_raw)
    if expected_manifest_sha256 is not None and manifest_sha != expected_manifest_sha256:
        raise _invalid(
            "INTERPRETATION_MANIFEST_HASH_MISMATCH",
            "Interpretation manifest does not match the pointer hash.",
        )
    manifest = _json_object(manifest_raw, label="interpretation manifest")
    if set(manifest) != _MANIFEST_KEYS or manifest.get("schema") != MANIFEST_SCHEMA:
        raise _invalid(
            "INTERPRETATION_MANIFEST_FIELDS_INVALID",
            "Interpretation manifest fields do not match the v1 contract.",
        )
    if manifest.get("revisionId") != revision_id:
        raise _invalid(
            "INTERPRETATION_IDENTITY_MISMATCH",
            "Interpretation manifest revision id is inconsistent.",
        )
    expected_revision_id = sha256_bytes(
        canonical_json_bytes(
            {
                "semanticDigest": manifest.get("semanticDigest"),
                "interpreterVersion": manifest.get("interpreterVersion"),
                "schemaVersion": manifest.get("schemaVersion"),
                "artifacts": manifest.get("artifacts"),
            },
            newline=False,
        )
    )[:24]
    if expected_revision_id != revision_id:
        raise _invalid(
            "INTERPRETATION_REVISION_DIGEST_MISMATCH",
            "Interpretation revision id does not match its immutable content binding.",
        )
    evidence_revision, evidence_manifest, _evidence_pointer = _evidence_binding(state)
    evidence_identity = evidence_manifest_payload(state)
    if (
        manifest.get("evidenceRevisionId") != evidence_revision
        or manifest.get("evidenceManifestSha256") != evidence_manifest
        or manifest.get("assetId") != evidence_identity.get("assetId")
        or manifest.get("objectPath") != evidence_identity.get("objectPath")
    ):
        raise _invalid(
            "INTERPRETATION_STALE_EVIDENCE",
            "The Interpretation identity does not match current Evidence.",
        )
    artifacts = _read_artifacts(revision_dir, manifest)
    interpretation = _json_object(
        artifacts["interpretationJson"], label="interpretation.json"
    )
    trace = _json_object(artifacts["trace"], label="trace.json")
    gaps = _json_object(artifacts["gaps"], label="gaps.json")
    markdown = _decode_text(
        artifacts["interpretationMarkdown"], label="interpretation.md"
    )
    pseudocode = _decode_text(artifacts["pseudocode"], label="pseudocode.txt")
    _validate_contract(
        manifest=manifest,
        interpretation=interpretation,
        trace=trace,
        gaps=gaps,
        pseudocode=pseudocode,
        markdown=markdown,
        evidence_refs=_evidence_refs(state),
    )
    _validate_derived_content(
        root=root,
        state=state,
        interpretation=interpretation,
        trace=trace,
        gaps=gaps,
        pseudocode=pseudocode,
        markdown=markdown,
    )
    _require_exact_revision_files(revision_dir)
    return LoadedInterpretation(
        asset_dir=root,
        revision_dir=revision_dir,
        pointer_path=pointer_path or (root / "interpretation" / "current.json"),
        revision_id=str(revision_id),
        manifest_sha256=manifest_sha,
        pointer_sha256=pointer_sha256,
        manifest=manifest,
        interpretation=interpretation,
        trace=trace,
        gaps=gaps,
        pseudocode=pseudocode,
        markdown=markdown,
    )


def load_current_interpretation(asset_dir: str | Path) -> LoadedInterpretation:
    root = _lexical_absolute(asset_dir)
    initial_evidence = resolve_asset_evidence_state(root)
    _require_authoritative_evidence(initial_evidence)
    pointer_path = root / "interpretation" / "current.json"
    try:
        pointer_raw = _read_bound_file_bytes(
            pointer_path,
            label="interpretation current pointer",
            maximum_size=64 * 1024,
        )
    except FileNotFoundError:
        raise FileNotFoundError("INTERPRETATION_CURRENT_POINTER_MISSING") from None
    pointer = _json_object(pointer_raw, label="interpretation current pointer")
    if set(pointer) != _POINTER_KEYS or pointer.get("schema") != CURRENT_SCHEMA:
        raise _invalid(
            "INTERPRETATION_POINTER_FIELDS_INVALID",
            "Interpretation current pointer fields do not match the v1 contract.",
        )
    revision_id = _required_text(pointer, "revisionId", label="pointer", maximum=24)
    if _REVISION_RE.fullmatch(revision_id) is None:
        raise _invalid(
            "INTERPRETATION_REVISION_ID_INVALID",
            "Interpretation pointer revision id is invalid.",
        )
    expected_manifest = f"revisions/{revision_id}/manifest.json"
    if pointer.get("manifest") != expected_manifest:
        raise _invalid(
            "INTERPRETATION_MANIFEST_PATH_INVALID",
            "Interpretation pointer manifest path is inconsistent.",
        )
    manifest_sha = _required_hash(pointer, "manifestSha256", label="pointer")
    _required_hash(pointer, "evidenceManifestSha256", label="pointer")
    pointer_evidence_revision = _required_text(
        pointer, "evidenceRevisionId", label="pointer", maximum=24
    )
    if _REVISION_RE.fullmatch(pointer_evidence_revision) is None:
        raise _invalid(
            "INTERPRETATION_IDENTITY_INVALID",
            "Interpretation pointer Evidence revision id is invalid.",
        )
    loaded = load_interpretation_revision(
        root,
        revision_id,
        expected_manifest_sha256=manifest_sha,
        evidence_state=initial_evidence,
        pointer_path=pointer_path,
        pointer_sha256=sha256_bytes(pointer_raw),
    )
    if (
        pointer.get("evidenceRevisionId") != loaded.manifest.get("evidenceRevisionId")
        or pointer.get("evidenceManifestSha256")
        != loaded.manifest.get("evidenceManifestSha256")
    ):
        raise _invalid(
            "INTERPRETATION_IDENTITY_MISMATCH",
            "Interpretation pointer and manifest Evidence bindings differ.",
        )
    pointer_after = _read_bound_file_bytes(
        pointer_path,
        label="interpretation current pointer",
        maximum_size=64 * 1024,
    )
    final_evidence = resolve_asset_evidence_state(root)
    if pointer_after != pointer_raw or _evidence_binding(final_evidence) != _evidence_binding(
        initial_evidence
    ):
        raise _invalid(
            "INTERPRETATION_CHANGED_DURING_READ",
            "Evidence or Interpretation current changed during the bound read.",
        )
    return loaded


__all__ = ["load_current_interpretation", "load_interpretation_revision"]
