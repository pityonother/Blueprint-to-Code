#!/usr/bin/env python
"""Build a bounded, question-driven Native Evidence context pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.context_pack import estimate_tokens  # noqa: E402
from blueprint_translator.native_evidence_repository import (  # noqa: E402
    NativeEvidenceRepository,
    open_native_evidence_repository,
)


NATIVE_CONTEXT_SCHEMA = "blueprint-to-code-native-context-pack/v1"
MIN_CONTEXT_BUDGET = 500
MAX_CONTEXT_BUDGET = 8000
DEFAULT_CONTEXT_BUDGET = 1600
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_:]{2,}|[\u3400-\u9fff]{2,}")
_COMMENT = re.compile(r"/\*.*?\*/|//[^\r\n]*", re.DOTALL)
_STOP_WORDS = {
    "and",
    "are",
    "does",
    "from",
    "how",
    "into",
    "the",
    "this",
    "use",
    "uses",
    "what",
    "with",
}


def _unique(rows: Iterable[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = str(row.get(key) or "")
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _terms(question: str) -> list[str]:
    tokens = []
    seen: set[str] = set()
    for token in _WORD.findall(question):
        folded = token.casefold()
        if folded in _STOP_WORDS or folded in seen:
            continue
        seen.add(folded)
        tokens.append(token)
    return tokens[:8]


def _query_items(
    repository: NativeEvidenceRepository,
    request: dict[str, object],
) -> list[dict[str, Any]]:
    response = repository.query(request)
    rows = response.get("items")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _query_gaps(
    repository: NativeEvidenceRepository,
    request: dict[str, object],
) -> list[dict[str, Any]]:
    response = repository.query(request)
    rows = response.get("gaps")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _clean_decompile(value: object, limit: int = 360) -> str:
    text = _COMMENT.sub(" ", str(value or ""))
    return " ".join(text.split())[:limit]


def build_native_context_pack(
    repository: NativeEvidenceRepository,
    *,
    question: str,
    budget: int = DEFAULT_CONTEXT_BUDGET,
) -> dict[str, Any]:
    if budget < MIN_CONTEXT_BUDGET:
        raise ValueError(f"context budget must be at least {MIN_CONTEXT_BUDGET}")
    effective_budget = min(int(budget), MAX_CONTEXT_BUDGET)
    bounded_question = " ".join(str(question or "").split())[:1000]
    terms = _terms(bounded_question)

    search_hits: list[dict[str, Any]] = []
    for term in terms:
        try:
            search_hits.extend(
                _query_items(
                    repository,
                    {
                        "operation": "search",
                        "query": term,
                        "pageSize": 6,
                        "budgetTokens": 900,
                    },
                )
            )
        except ValueError:
            continue
    functions = _unique(search_hits, "evidenceId")[:5]

    callers: list[dict[str, Any]] = []
    callees: list[dict[str, Any]] = []
    field_accesses: list[dict[str, Any]] = []
    constants: list[dict[str, Any]] = []
    snippets: list[dict[str, Any]] = []
    for function in functions[:3]:
        evidence_id = str(function.get("evidenceId") or "")
        callers.extend(
            _query_items(
                repository,
                {
                    "operation": "callers",
                    "id": evidence_id,
                    "depth": 1,
                    "pageSize": 4,
                    "budgetTokens": 900,
                },
            )
        )
        callees.extend(
            _query_items(
                repository,
                {
                    "operation": "callees",
                    "id": evidence_id,
                    "depth": 1,
                    "pageSize": 4,
                    "budgetTokens": 900,
                },
            )
        )
        field_accesses.extend(
            _query_items(
                repository,
                {
                    "operation": "field-accesses",
                    "id": evidence_id,
                    "pageSize": 6,
                    "budgetTokens": 900,
                },
            )
        )
        constants.extend(
            _query_items(
                repository,
                {
                    "operation": "constants",
                    "id": evidence_id,
                    "pageSize": 6,
                    "budgetTokens": 900,
                },
            )
        )
        detail = _query_items(
            repository,
            {
                "operation": "function",
                "id": evidence_id,
                "includeDecompile": True,
                "snippetChars": 600,
                "budgetTokens": 1200,
            },
        )
        if detail:
            cleaned = _clean_decompile(detail[0].get("decompileSnippet"))
            if cleaned:
                snippets.append(
                    {
                        "evidenceId": evidence_id,
                        "qualifiedName": function.get("qualifiedName", ""),
                        "text": cleaned,
                    }
                )

    for term in terms:
        field_accesses.extend(
            _query_items(
                repository,
                {
                    "operation": "field-accesses",
                    "query": term,
                    "pageSize": 4,
                    "budgetTokens": 900,
                },
            )
        )
        constants.extend(
            _query_items(
                repository,
                {
                    "operation": "constants",
                    "query": term,
                    "pageSize": 4,
                    "budgetTokens": 900,
                },
            )
        )
    gaps = _query_gaps(
        repository,
        {
            "operation": "gaps",
            "pageSize": 6,
            "budgetTokens": 900,
        },
    )
    pack: dict[str, Any] = {
        "schema": NATIVE_CONTEXT_SCHEMA,
        "question": bounded_question,
        "queryTerms": terms,
        "evidenceSetId": repository.evidence_set_id,
        "sourceFingerprint": repository.source_sha256,
        "requestedBudget": int(budget),
        "effectiveBudget": effective_budget,
        "estimatedTokens": 0,
        "functions": functions,
        "callers": _unique(callers, "evidenceId")[:6],
        "callees": _unique(callees, "evidenceId")[:6],
        "fieldAccesses": _unique(field_accesses, "fieldAccessId")[:8],
        "constants": _unique(constants, "constantId")[:8],
        "decompileSnippets": snippets[:3],
        "gaps": _unique(gaps, "gapId")[:6],
        "nextQueries": [
            {
                "operation": "search",
                "query": terms[0] if terms else "<function>",
                "budgetTokens": 900,
            },
            {
                "operation": "gaps",
                "budgetTokens": 800,
            },
        ],
    }

    def update_estimate() -> int:
        estimate = 0
        for _attempt in range(5):
            pack["estimatedTokens"] = estimate
            updated = estimate_tokens(render_native_context_pack(pack))
            if updated == estimate:
                break
            estimate = updated
        pack["estimatedTokens"] = estimate_tokens(render_native_context_pack(pack))
        return int(pack["estimatedTokens"])

    removable = (
        "decompileSnippets",
        "gaps",
        "constants",
        "fieldAccesses",
        "callers",
        "callees",
        "functions",
    )
    while update_estimate() > effective_budget:
        target = next(
            (
                name
                for name in removable
                if isinstance(pack.get(name), list) and len(pack[name]) > (1 if name == "functions" else 0)
            ),
            None,
        )
        if target is None:
            raise ValueError(
                "native context pack shell cannot fit the requested budget"
            )
        pack[target].pop()
    update_estimate()
    return pack


def render_native_context_pack(pack: dict[str, Any]) -> str:
    def bullets(rows: object, render) -> list[str]:
        values = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        return [f"- {render(row)}" for row in values] or ["- None returned within this pack."]

    lines = [
        "# Native Evidence Context Pack",
        "",
        f"- Question: {pack.get('question', '')}",
        f"- Evidence set: `{pack.get('evidenceSetId', '')}`",
        f"- Source fingerprint: `{pack.get('sourceFingerprint', '')}`",
        f"- Budget: {pack.get('estimatedTokens', 0)} / {pack.get('effectiveBudget', 0)} estimated tokens",
        "",
        "## Relevant functions",
        "",
        *bullets(
            pack.get("functions"),
            lambda row: (
                f"`{row.get('qualifiedName', row.get('name', ''))}` — "
                f"`{row.get('evidenceId', '')}` [{row.get('status', '')}]"
            ),
        ),
        "",
        "## Callers",
        "",
        *bullets(
            pack.get("callers"),
            lambda row: f"`{row.get('qualifiedName', '')}` — `{row.get('evidenceId', '')}`",
        ),
        "",
        "## Callees",
        "",
        *bullets(
            pack.get("callees"),
            lambda row: f"`{row.get('qualifiedName', '')}` — `{row.get('evidenceId', '')}`",
        ),
        "",
        "## Field accesses",
        "",
        *bullets(
            pack.get("fieldAccesses"),
            lambda row: (
                f"`{row.get('ownerType', '')}::{row.get('fieldName', '')}` "
                f"{row.get('access', '')} at {row.get('offset', '')} "
                f"from `{row.get('functionEvidenceId', '')}`"
            ),
        ),
        "",
        "## Constants",
        "",
        *bullets(
            pack.get("constants"),
            lambda row: (
                f"`{row.get('value')}` ({row.get('context', '')}) "
                f"from `{row.get('functionEvidenceId', '')}`"
            ),
        ),
        "",
        "## Bounded decompiler snippets",
        "",
        *bullets(
            pack.get("decompileSnippets"),
            lambda row: f"`{row.get('qualifiedName', '')}`: `{row.get('text', '')}`",
        ),
        "",
        "## Gaps",
        "",
        *bullets(
            pack.get("gaps"),
            lambda row: (
                f"`{row.get('reasonCode', '')}` [{row.get('status', '')}]: "
                f"{row.get('detail', '')}"
            ),
        ),
        "",
    ]
    return "\n".join(lines)


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
        description="Build one bounded Native Evidence context pack."
    )
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--budget", type=int, default=DEFAULT_CONTEXT_BUDGET)
    parser.add_argument("--output-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with open_native_evidence_repository(args.evidence_dir) as repository:
        pack = build_native_context_pack(
            repository,
            question=args.question,
            budget=args.budget,
        )
    digest = hashlib.sha256(
        json.dumps(
            {
                "question": pack["question"],
                "budget": pack["effectiveBudget"],
                "source": pack["sourceFingerprint"],
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    output_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else args.evidence_dir.resolve() / "output" / "context_queries" / digest
    )
    json_path = output_dir / "native_context_pack.json"
    markdown_path = output_dir / "native_context_pack.md"
    _atomic_write(
        json_path,
        json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _atomic_write(markdown_path, render_native_context_pack(pack))
    print(f"Wrote native context pack: {markdown_path}")
    print(
        f"Estimated tokens: {pack['estimatedTokens']} / "
        f"{pack['effectiveBudget']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
