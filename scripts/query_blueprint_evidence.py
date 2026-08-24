"""Command-line adapter for the bounded Blueprint evidence query service."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.evidence_repository import (  # noqa: E402
    open_resolved_asset_repository,
    resolve_asset_evidence_state,
)


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def _add_budget(parser: argparse.ArgumentParser, default: int = 1000) -> None:
    parser.add_argument("--budget", type=int, default=default, help="Whole-response token budget (min 500, max 8000).")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query normalized Blueprint evidence without loading full reports.")
    parser.add_argument("--asset-dir", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="operation", required=True)

    overview = subparsers.add_parser("overview")
    _add_budget(overview, 1000)

    search = subparsers.add_parser("search")
    search.add_argument("--query", required=True)
    search.add_argument(
        "--kind",
        action="append",
        dest="kinds",
        choices=[
            "graph",
            "node",
            "pin",
            "default",
            "asset_field",
            "diagnostic",
            "edge_observation",
        ],
    )
    search.add_argument("--page-size", type=int)
    search.add_argument("--cursor")
    _add_budget(search, 800)

    entity = subparsers.add_parser("entity")
    entity.add_argument("--id", required=True)
    entity.add_argument("--candidate-offset", type=int, default=0)
    entity.add_argument("--candidate-limit", type=int, default=25)
    entity.add_argument("--value-offset", type=int, default=0)
    entity.add_argument("--value-chars", type=int, default=600)
    entity.add_argument("--property-offset", type=int)
    entity.add_argument("--property-limit", type=int)
    entity.add_argument("--observation-offset", type=int)
    entity.add_argument("--observation-limit", type=int)
    _add_budget(entity, 1200)

    for operation in ("neighborhood", "trace"):
        command = subparsers.add_parser(operation)
        command.add_argument("--id", required=True)
        command.add_argument("--hops", type=int, default=1)
        command.add_argument(
            "--direction",
            choices=["both", "upstream", "downstream"],
            default="both" if operation == "neighborhood" else "downstream",
        )
        command.add_argument("--edge-kind", action="append", dest="edge_kinds", choices=["exec", "data"])
        command.add_argument("--page-size", type=int)
        command.add_argument("--cursor")
        command.add_argument("--pin-offset", type=int, default=0)
        command.add_argument("--pin-limit", type=int, default=8)
        command.add_argument("--edge-offset", type=int, default=0)
        command.add_argument("--edge-limit", type=int, default=8)
        _add_budget(command, 1500)

    gaps = subparsers.add_parser("gaps")
    gaps.add_argument(
        "--scope",
        help="Evidence bp:// ref, or graph:<exact graph name>; ambiguous names require an explicit ref.",
    )
    gaps.add_argument("--page-size", type=int)
    gaps.add_argument("--cursor")
    _add_budget(gaps, 1000)

    runtime_signals = subparsers.add_parser("runtime-signals")
    runtime_signals.add_argument("--page-size", type=int)
    runtime_signals.add_argument("--cursor")
    _add_budget(runtime_signals, 1200)

    loot_rewards = subparsers.add_parser("loot-rewards")
    loot_rewards.add_argument("--item-query", required=True)
    loot_rewards.add_argument("--page-size", type=int)
    loot_rewards.add_argument("--cursor")
    _add_budget(loot_rewards, 1600)
    return parser.parse_args(argv)


def request_from_args(args: argparse.Namespace) -> dict[str, object]:
    request: dict[str, object] = {
        "operation": args.operation,
        "budgetTokens": args.budget,
    }
    if args.operation == "search":
        request["query"] = args.query
        if args.kinds:
            request["kinds"] = args.kinds
        if args.page_size is not None:
            request["pageSize"] = args.page_size
        if args.cursor:
            request["cursor"] = args.cursor
    elif args.operation == "entity":
        request["selector"] = {"ref": args.id}
        request["candidateOffset"] = args.candidate_offset
        request["candidateLimit"] = args.candidate_limit
        request["valueOffset"] = args.value_offset
        request["valueChars"] = args.value_chars
        if args.property_offset is not None:
            request["propertyOffset"] = args.property_offset
        if args.property_limit is not None:
            request["propertyLimit"] = args.property_limit
        if args.observation_offset is not None:
            request["observationOffset"] = args.observation_offset
        if args.observation_limit is not None:
            request["observationLimit"] = args.observation_limit
    elif args.operation in {"neighborhood", "trace"}:
        request["selector"] = {"ref": args.id}
        request["traversal"] = {
            "maxHops": args.hops,
            "direction": args.direction,
            "edgeKinds": args.edge_kinds or ["exec", "data"],
        }
        request["pinOffset"] = args.pin_offset
        request["pinLimit"] = args.pin_limit
        request["edgeOffset"] = args.edge_offset
        request["edgeLimit"] = args.edge_limit
        if args.page_size is not None:
            request["pageSize"] = args.page_size
        if args.cursor:
            request["cursor"] = args.cursor
    elif args.operation == "gaps" and args.scope and not str(args.scope).startswith("graph:"):
        request["selector"] = {"ref": args.scope}
    if args.operation == "gaps":
        if args.page_size is not None:
            request["pageSize"] = args.page_size
        if args.cursor:
            request["cursor"] = args.cursor
    if args.operation == "runtime-signals":
        if args.page_size is not None:
            request["pageSize"] = args.page_size
        if args.cursor:
            request["cursor"] = args.cursor
    if args.operation == "loot-rewards":
        request["itemQuery"] = args.item_query
        if args.page_size is not None:
            request["pageSize"] = args.page_size
        if args.cursor:
            request["cursor"] = args.cursor
    return request


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_stdio()
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        state = resolve_asset_evidence_state(args.asset_dir, allow_stale=True)
        purpose = (
            "formal_query"
            if state.source_kind == "INDEXED_V3_CURRENT"
            else "draft_query"
        )
        with open_resolved_asset_repository(
            state,
            purpose=purpose,
        ) as repository:
            request = request_from_args(args)
            if args.operation == "gaps" and str(args.scope or "").startswith("graph:"):
                graph_name = str(args.scope)[len("graph:") :].strip()
                if not graph_name:
                    raise ValueError("graph: scope requires an exact graph name")
                search = repository.query(
                    {
                        "operation": "search",
                        "query": graph_name,
                        "kinds": ["graph"],
                        "pageSize": 100,
                        "budgetTokens": 4000,
                    }
                )
                matches = [
                    item
                    for item in search.get("items", [])
                    if isinstance(item, dict) and item.get("name") == graph_name
                ]
                if len(matches) != 1:
                    raise ValueError(
                        f"graph scope {graph_name!r} resolved to {len(matches)} refs; use an explicit bp:// ref"
                    )
                request["selector"] = {"ref": str(matches[0]["ref"])}
            response = repository.query(request)
            response.update(
                {
                    "sourceKind": repository.source_kind,
                    "freshnessStatus": repository.freshness_status,
                    "releaseAuthority": repository.release_authority,
                    "migrationRequired": repository.migration_required,
                    "manifestSha256": repository.manifest_sha256,
                    "pointerSha256": repository.pointer_sha256,
                    "evidenceDecision": {
                        "allowed": repository.evidence_decision.allowed,
                        "purpose": repository.evidence_decision.purpose,
                        "reasonCode": repository.evidence_decision.reason_code,
                        "reasonCodes": list(repository.evidence_decision.reason_codes),
                        "bindingDigest": repository.evidence_decision.binding_digest,
                        "evidenceAvailability": (
                            repository.evidence_decision.evidence_availability
                        ),
                        "nonUpgradeableGaps": list(
                            repository.evidence_decision.non_upgradeable_gaps
                        ),
                    },
                    "statusZh": repository.evidence_decision.public_status_zh,
                }
            )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
