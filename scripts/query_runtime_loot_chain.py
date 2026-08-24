"""Compose bound per-asset evidence into a cross-asset runtime loot chain."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.evidence_repository import (  # noqa: E402
    EvidenceRepository,
    open_resolved_asset_repository,
    resolve_asset_evidence_state,
)
from blueprint_translator.runtime_loot_chain import (  # noqa: E402
    RUNTIME_LOOT_CHAIN_SCHEMA,
    compose_runtime_loot_chain,
)


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Join an event emitter, map receivers, spawned class, and reward defaults "
            "without upgrading missing route evidence."
        )
    )
    parser.add_argument("--emitter-asset-dir", type=Path, required=True)
    parser.add_argument("--receiver-asset-dir", type=Path, action="append", default=[])
    parser.add_argument("--reward-asset-dir", type=Path, action="append", default=[])
    parser.add_argument(
        "--expected-receiver-object-path",
        action="append",
        default=[],
        help="Expected map/receiver object path; missing paths become explicit source gaps.",
    )
    parser.add_argument(
        "--expected-reward-object-path",
        action="append",
        default=[],
        help="Expected crate/pool object path; missing paths become explicit source gaps.",
    )
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--item-query", default="")
    args = parser.parse_args(argv)
    if args.reward_asset_dir and not str(args.item_query).strip():
        parser.error("--item-query is required when --reward-asset-dir is used")
    return args


def _query_all(
    repository: EvidenceRepository,
    request: dict[str, object],
) -> dict[str, object]:
    base_request = {**request, "pageSize": 100, "budgetTokens": 8000}
    cursor: str | None = None
    seen_cursors: set[str] = set()
    combined: list[object] = []
    first: dict[str, object] | None = None
    for _page in range(1000):
        page_request = dict(base_request)
        if cursor is not None:
            page_request["cursor"] = cursor
        response = repository.query(page_request)
        if first is None:
            first = dict(response)
        page_items = response.get("items")
        if isinstance(page_items, list):
            combined.extend(page_items)
        page = response.get("page")
        next_cursor = (
            str(page.get("nextCursor"))
            if isinstance(page, dict) and page.get("nextCursor")
            else None
        )
        coverage = response.get("coverage")
        available_not_returned = (
            int(coverage.get("availableNotReturned") or 0)
            if isinstance(coverage, dict)
            else 0
        )
        requested_total = (
            int(coverage.get("requested") or 0)
            if isinstance(coverage, dict)
            else 0
        )
        if not requested_total and available_not_returned:
            requested_total = len(combined) + available_not_returned
        if (
            next_cursor is None
            and available_not_returned
            and len(combined) < requested_total
        ):
            raise ValueError("LOSSLESS_CONTINUATION_UNAVAILABLE")
        if next_cursor is None:
            break
        if next_cursor in seen_cursors:
            raise ValueError("PAGINATION_CURSOR_CYCLE")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    else:
        raise ValueError("PAGINATION_LIMIT_EXCEEDED")
    if first is None:
        raise ValueError("EMPTY_QUERY_RESPONSE")
    first["items"] = combined
    first["page"] = {"nextCursor": None}
    return first


def _asset_identity(repository: EvidenceRepository) -> dict[str, object]:
    identity = repository.identity()
    return {
        "name": str(identity["asset_name"]),
        "objectPath": str(identity["object_path"]),
        "revisionId": str(identity["revision_id"]),
        "sourceKind": repository.source_kind,
        "freshnessStatus": repository.freshness_status,
        "releaseAuthority": repository.release_authority,
    }


def _open_formal_repository(
    stack: ExitStack,
    asset_dir: Path,
) -> EvidenceRepository:
    state = resolve_asset_evidence_state(asset_dir, allow_stale=True)
    repository = open_resolved_asset_repository(state, purpose="formal_query")
    return stack.enter_context(repository)


def _probe(
    repository: EvidenceRepository,
    request: dict[str, object],
) -> dict[str, object]:
    return {
        "asset": _asset_identity(repository),
        "result": _query_all(repository, request),
    }


def build_chain(args: argparse.Namespace) -> dict[str, object]:
    with ExitStack() as stack:
        emitter_repository = _open_formal_repository(
            stack,
            args.emitter_asset_dir,
        )
        receiver_repositories = [
            _open_formal_repository(stack, asset_dir)
            for asset_dir in args.receiver_asset_dir
        ]
        reward_repositories = [
            _open_formal_repository(stack, asset_dir)
            for asset_dir in args.reward_asset_dir
        ]
        emitter_probe = _probe(
            emitter_repository,
            {"operation": "runtime-signals"},
        )
        receiver_probes = [
            _probe(
                repository,
                {
                    "operation": "runtime-routes",
                    "eventName": str(args.event_name),
                },
            )
            for repository in receiver_repositories
        ]
        reward_probes = [
            _probe(
                repository,
                {
                    "operation": "loot-rewards",
                    "itemQuery": str(args.item_query),
                },
            )
            for repository in reward_repositories
        ]
        return compose_runtime_loot_chain(
            event_name=str(args.event_name).strip(),
            item_query=str(args.item_query).strip(),
            emitter_probe=emitter_probe,
            receiver_probes=receiver_probes,
            reward_probes=reward_probes,
            expected_receiver_object_paths=[
                str(value) for value in args.expected_receiver_object_path
            ],
            expected_reward_object_paths=[
                str(value) for value in args.expected_reward_object_path
            ],
        )


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_stdio()
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        result = build_chain(args)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        error: dict[str, Any] = {
            "schema": RUNTIME_LOOT_CHAIN_SCHEMA,
            "status": "ERROR",
            "reasonCode": "CHAIN_QUERY_FAILED",
            "errorType": type(exc).__name__,
        }
        print(json.dumps(error, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] in {"COMPLETE", "PARTIAL"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
