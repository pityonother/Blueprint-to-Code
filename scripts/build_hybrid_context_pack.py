#!/usr/bin/env python
"""Build a bounded context that keeps Blueprint, Native, and gaps separate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.context_pack import estimate_tokens  # noqa: E402
from blueprint_translator.hybrid_evidence import (  # noqa: E402
    HybridEvidenceRepository,
    extract_blueprint_calls,
    mark_stale_edges,
    open_hybrid_evidence_repository,
)
from blueprint_translator.native_evidence_repository import (  # noqa: E402
    NativeEvidenceRepository,
    open_native_evidence_repository,
)


HYBRID_CONTEXT_SCHEMA = "blueprint-to-code-hybrid-context-pack/v1"
MIN_CONTEXT_BUDGET = 500
MAX_CONTEXT_BUDGET = 8000
DEFAULT_CONTEXT_BUDGET = 2200
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_:]{2,}|[\u3400-\u9fff]{2,}")
_STOP = {"and", "does", "from", "how", "into", "native", "the", "this", "what"}


def _terms(question: str) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in _WORD.findall(question):
        folded = value.casefold()
        if folded in _STOP or folded in seen:
            continue
        seen.add(folded)
        result.append(value)
    return result[:8]


def _matches_question(edge: dict[str, Any], terms: list[str]) -> bool:
    if not terms:
        return True
    resolution = edge.get("resolution")
    resolution = resolution if isinstance(resolution, dict) else {}
    haystack = " ".join(
        (
            str(edge.get("sourceId") or ""),
            str(edge.get("targetId") or ""),
            str(resolution.get("blueprintMemberName") or ""),
            str(resolution.get("blueprintOwner") or ""),
            str(resolution.get("nativeQualifiedName") or ""),
            str(resolution.get("candidates") or ""),
        )
    ).casefold()
    return any(term.casefold() in haystack for term in terms)


def _native_function(
    repository: NativeEvidenceRepository,
    evidence_id: str,
) -> dict[str, Any] | None:
    response = repository.query(
        {
            "operation": "function",
            "id": evidence_id,
            "budgetTokens": 1000,
        }
    )
    items = response.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return dict(items[0])
    return None


def _native_gaps(repository: NativeEvidenceRepository) -> list[dict[str, Any]]:
    response = repository.query(
        {
            "operation": "gaps",
            "pageSize": 12,
            "budgetTokens": 1200,
        }
    )
    rows = response.get("gaps")
    return [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _is_runtime_only_gap(gap: dict[str, Any]) -> bool:
    reason = str(gap.get("reasonCode") or gap.get("kind") or "").upper()
    status = str(gap.get("status") or "").upper()
    return reason.startswith("RUNTIME_") or status == "UNSUPPORTED_DYNAMIC_BRANCH"


def build_hybrid_context_pack(
    hybrid: HybridEvidenceRepository,
    native: NativeEvidenceRepository,
    *,
    question: str,
    budget: int = DEFAULT_CONTEXT_BUDGET,
    current_blueprint_revision_id: str | None = None,
    current_blueprint_source_fingerprint: str | None = None,
) -> dict[str, Any]:
    if budget < MIN_CONTEXT_BUDGET:
        raise ValueError(f"context budget must be at least {MIN_CONTEXT_BUDGET}")
    effective_budget = min(int(budget), MAX_CONTEXT_BUDGET)
    bounded_question = " ".join(str(question or "").split())[:1000]
    terms = _terms(bounded_question)
    dependencies = hybrid.manifest.get("dependencies")
    dependencies = dependencies if isinstance(dependencies, dict) else {}
    current_revision = (
        current_blueprint_revision_id
        if current_blueprint_revision_id is not None
        else str(dependencies.get("blueprintRevisionId") or "")
    )
    current_blueprint_source = (
        current_blueprint_source_fingerprint
        if current_blueprint_source_fingerprint is not None
        else str(dependencies.get("blueprintSourceFingerprint") or "")
    )
    native_trust = {
        "status": native.trust_status,
        "formalValidation": native.formal_validation,
    }
    native_is_formal = (
        native.trust_status == "VERIFIED"
        and native.formal_validation
    )
    edges = mark_stale_edges(
        hybrid.list_edges(),
        current_blueprint_revision_id=current_revision,
        current_blueprint_source_fingerprint=current_blueprint_source,
        current_native_source_fingerprint=native.source_sha256,
        current_native_evidence_set_id=native.evidence_set_id,
    )
    selected = [edge for edge in edges if _matches_question(edge, terms)]
    if not selected:
        selected = edges[:12]
    selected = selected[:16]

    blueprint_facts: list[dict[str, Any]] = []
    native_facts: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []
    assumptions: list[dict[str, Any]] = []
    blueprint_gaps: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = (
        []
        if native_is_formal
        else [
            {
                "code": "PROVENANCE_UNVERIFIED",
                "status": native.trust_status,
                "formalValidation": native.formal_validation,
                "detail": (
                    "Native evidence is not formal VERIFIED provenance; "
                    "native facts and resolved cross-source edges were withheld."
                ),
            }
        ]
    )
    seen_native: set[str] = set()
    for edge in selected:
        resolution = edge.get("resolution")
        resolution = resolution if isinstance(resolution, dict) else {}
        blueprint_fact = {
            "evidenceId": edge.get("sourceId", ""),
            "memberName": resolution.get("blueprintMemberName", ""),
            "owner": resolution.get("blueprintOwner", ""),
            "status": (
                "CONFIRMED"
                if resolution.get("blueprintMemberName")
                else "NOT_RECOVERED"
            ),
        }
        if blueprint_fact["status"] == "CONFIRMED":
            blueprint_facts.append(blueprint_fact)
        if edge.get("status") == "CONFIRMED" and native_is_formal:
            resolved.append(edge)
            target_id = str(edge.get("targetId") or "")
            if target_id and target_id not in seen_native:
                seen_native.add(target_id)
                function = _native_function(native, target_id)
                if function is not None:
                    native_facts.append(function)
        elif edge.get("status") == "CONFIRMED":
            assumptions.append(
                {
                    "edgeId": edge.get("edgeId", ""),
                    "sourceId": edge.get("sourceId", ""),
                    "targetId": edge.get("targetId", ""),
                    "status": "PROVENANCE_UNVERIFIED",
                    "evidenceStatus": "CONFIRMED",
                    "candidateCount": resolution.get("candidateCount", 0),
                    "candidates": resolution.get("candidates", []),
                    "gaps": ["NATIVE_PROVENANCE_UNVERIFIED"],
                }
            )
        elif edge.get("status") == "STALE":
            warnings.append(
                {
                    "edgeId": edge.get("edgeId", ""),
                    "sourceId": edge.get("sourceId", ""),
                    "status": "STALE",
                    "gaps": edge.get("gaps", []),
                }
            )
        else:
            if edge.get("status") == "NOT_RECOVERED":
                blueprint_gaps.append(
                    {
                        "kind": "blueprint-gap",
                        "edgeId": edge.get("edgeId", ""),
                        "sourceId": edge.get("sourceId", ""),
                        "status": "NOT_RECOVERED",
                        "reasonCodes": edge.get("gaps", []),
                    }
                )
            assumptions.append(
                {
                    "edgeId": edge.get("edgeId", ""),
                    "sourceId": edge.get("sourceId", ""),
                    "status": edge.get("status", ""),
                    "candidateCount": resolution.get("candidateCount", 0),
                    "candidates": resolution.get("candidates", []),
                    "gaps": edge.get("gaps", []),
                }
            )

    all_native_gaps = _native_gaps(native)
    runtime_gaps = [
        gap for gap in all_native_gaps if _is_runtime_only_gap(gap)
    ]
    native_gaps = (
        []
        if native_is_formal
        else [
            {
                "kind": "native-provenance-gap",
                "status": "PROVENANCE_UNVERIFIED",
                "reasonCode": "NATIVE_PROVENANCE_UNVERIFIED",
                "detail": (
                    "Native evidence trust is "
                    f"{native.trust_status} with formalValidation="
                    f"{native.formal_validation}."
                ),
            }
        ]
    )
    native_gaps.extend(
        gap for gap in all_native_gaps if not _is_runtime_only_gap(gap)
    )
    for edge in selected:
        if edge.get("status") == "SOURCE_NOT_AVAILABLE":
            native_gaps.append(
                {
                    "kind": "hybrid-gap",
                    "edgeId": edge.get("edgeId", ""),
                    "status": "SOURCE_NOT_AVAILABLE",
                    "reasonCode": "NATIVE_LINK_UNRESOLVED",
                    "detail": ", ".join(str(item) for item in edge.get("gaps", [])),
                }
            )
    pack: dict[str, Any] = {
        "schema": HYBRID_CONTEXT_SCHEMA,
        "question": bounded_question,
        "queryTerms": terms,
        "requestedBudget": int(budget),
        "effectiveBudget": effective_budget,
        "estimatedTokens": 0,
        "blueprintRevisionId": current_revision,
        "blueprintSourceFingerprint": current_blueprint_source,
        "nativeEvidenceSetId": native.evidence_set_id,
        "nativeSourceFingerprint": native.source_sha256,
        "nativeTrust": native_trust,
        "hybridSourceFingerprint": hybrid.source_sha256,
        "blueprintConfirmedFacts": blueprint_facts,
        "nativeConfirmedFacts": native_facts,
        "resolvedCrossSourceEdges": resolved,
        "assumptions": assumptions,
        "blueprintGaps": blueprint_gaps[:12],
        "nativeGaps": native_gaps[:12],
        "runtimeOnlyGaps": runtime_gaps[:12],
        "staleProvenanceWarnings": warnings,
    }

    def update_estimate() -> int:
        estimate = 0
        for _attempt in range(5):
            pack["estimatedTokens"] = estimate
            updated = estimate_tokens(render_hybrid_context_pack(pack))
            if estimate == updated:
                break
            estimate = updated
        pack["estimatedTokens"] = estimate_tokens(render_hybrid_context_pack(pack))
        return int(pack["estimatedTokens"])

    removable = (
        "runtimeOnlyGaps",
        "nativeGaps",
        "blueprintGaps",
        "assumptions",
        "staleProvenanceWarnings",
        "nativeConfirmedFacts",
        "blueprintConfirmedFacts",
        "resolvedCrossSourceEdges",
    )
    while update_estimate() > effective_budget:
        target = next(
            (
                key
                for key in removable
                if isinstance(pack.get(key), list)
                and len(pack[key])
                > (
                    1
                    if key
                    in {
                        "blueprintConfirmedFacts",
                        "nativeConfirmedFacts",
                        "resolvedCrossSourceEdges",
                    }
                    or (
                        not native_is_formal
                        and key
                        in {
                            "assumptions",
                            "nativeGaps",
                            "staleProvenanceWarnings",
                        }
                    )
                    else 0
                )
            ),
            None,
        )
        if target is None:
            raise ValueError(
                "hybrid context pack shell cannot fit the requested budget"
            )
        pack[target].pop()
    update_estimate()
    return pack


def render_hybrid_context_pack(pack: dict[str, Any]) -> str:
    def rows(key: str) -> list[dict[str, Any]]:
        value = pack.get(key)
        return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []

    def section(title: str, values: list[str]) -> list[str]:
        return [f"## {title}", "", *(f"- {value}" for value in values)] if values else [
            f"## {title}",
            "",
            "- None returned within this pack.",
        ]

    blueprint = [
        (
            f"`{row.get('evidenceId', '')}` calls member "
            f"`{row.get('owner', '')}::{row.get('memberName', '')}` "
            f"[{row.get('status', '')}]"
        )
        for row in rows("blueprintConfirmedFacts")
    ]
    native = [
        (
            f"`{row.get('qualifiedName', '')}` — `{row.get('evidenceId', '')}` "
            f"[{row.get('status', '')} / {row.get('confidence', '')}]"
        )
        for row in rows("nativeConfirmedFacts")
    ]
    resolved = [
        (
            f"`{row.get('sourceId', '')}` --{row.get('relation', '')}--> "
            f"`{row.get('targetId', '')}` [{row.get('status', '')}]"
        )
        for row in rows("resolvedCrossSourceEdges")
    ]
    assumptions = [
        (
            f"`{row.get('sourceId', '')}` [{row.get('status', '')}] has "
            f"{row.get('candidateCount', 0)} candidate(s); "
            f"evidenceStatus={row.get('evidenceStatus', '')}; "
            f"gaps={row.get('gaps', [])}"
        )
        for row in rows("assumptions")
    ]
    blueprint_gaps = [
        (
            f"`{row.get('sourceId', '')}` [{row.get('status', '')}]: "
            f"{row.get('reasonCodes', [])}"
        )
        for row in rows("blueprintGaps")
    ]
    native_gaps = [
        (
            f"`{row.get('reasonCode', '')}` [{row.get('status', '')}]: "
            f"{row.get('detail', '')}"
        )
        for row in rows("nativeGaps")
    ]
    runtime_gaps = [
        (
            f"`{row.get('reasonCode', '')}` [{row.get('status', '')}]: "
            f"{row.get('detail', '')}"
        )
        for row in rows("runtimeOnlyGaps")
    ]
    warnings = [
        (
            f"`{row.get('code') or row.get('edgeId', '')}` "
            f"[{row.get('status', '')}]: "
            f"{row.get('detail') or row.get('gaps', [])}"
        )
        for row in rows("staleProvenanceWarnings")
    ]
    return "\n".join(
        [
            "# Hybrid Evidence Context Pack",
            "",
            f"- Question: {pack.get('question', '')}",
            f"- Blueprint revision: `{pack.get('blueprintRevisionId', '')}`",
            f"- Native evidence set: `{pack.get('nativeEvidenceSetId', '')}`",
            (
                "- Native trust: "
                f"`{(pack.get('nativeTrust') or {}).get('status', '')}` "
                f"(formalValidation="
                f"`{(pack.get('nativeTrust') or {}).get('formalValidation', False)}`)"
            ),
            f"- Budget: {pack.get('estimatedTokens', 0)} / {pack.get('effectiveBudget', 0)} estimated tokens",
            "",
            *section("Blueprint confirmed facts", blueprint),
            "",
            *section("Native confirmed facts", native),
            "",
            *section("Resolved cross-source edges", resolved),
            "",
            *section("Assumptions", assumptions),
            "",
            *section("Blueprint gaps", blueprint_gaps),
            "",
            *section("Native gaps", native_gaps),
            "",
            *section("Runtime-only gaps", runtime_gaps),
            "",
            *section("Stale/provenance warnings", warnings),
            "",
        ]
    )


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(raw, path)
    except Exception:
        Path(raw).unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a bounded Blueprint/Native hybrid context pack."
    )
    parser.add_argument(
        "--hybrid-dir",
        type=Path,
        default=Path("analysis") / "evidence_graph",
    )
    parser.add_argument("--native-evidence-dir", type=Path, required=True)
    parser.add_argument("--asset-dir", type=Path)
    parser.add_argument("--question", required=True)
    parser.add_argument("--budget", type=int, default=DEFAULT_CONTEXT_BUDGET)
    parser.add_argument("--output-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    current_revision: str | None = None
    current_source: str | None = None
    if args.asset_dir is not None:
        asset_dir = Path(
            os.path.abspath(os.path.expanduser(os.fspath(args.asset_dir)))
        )
        _calls, identity = extract_blueprint_calls(asset_dir)
        current_revision = identity["revisionId"]
        current_source = identity["sourceFingerprint"]
    with open_hybrid_evidence_repository(args.hybrid_dir) as hybrid, open_native_evidence_repository(
        args.native_evidence_dir
    ) as native:
        pack = build_hybrid_context_pack(
            hybrid,
            native,
            question=args.question,
            budget=args.budget,
            current_blueprint_revision_id=current_revision,
            current_blueprint_source_fingerprint=current_source,
        )
    digest = hashlib.sha256(
        json.dumps(
            {
                "question": pack["question"],
                "budget": pack["effectiveBudget"],
                "blueprint": pack["blueprintSourceFingerprint"],
                "native": pack["nativeSourceFingerprint"],
                "hybrid": pack["hybridSourceFingerprint"],
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    output = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else args.hybrid_dir.resolve() / "output" / "context_queries" / digest
    )
    json_path = output / "hybrid_context_pack.json"
    markdown_path = output / "hybrid_context_pack.md"
    _atomic_write(
        json_path,
        json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _atomic_write(markdown_path, render_hybrid_context_pack(pack))
    print(f"Wrote hybrid context pack: {markdown_path}")
    print(
        f"Estimated tokens: {pack['estimatedTokens']} / "
        f"{pack['effectiveBudget']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
