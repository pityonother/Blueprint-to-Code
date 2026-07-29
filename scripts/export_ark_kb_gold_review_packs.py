from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.gold_review import (  # noqa: E402
    GoldReviewError,
    build_query_review_pack,
)


TOOL_VERSION = "ark-kb-gold-review-export/v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export prediction-free ARK KB gold review packs."
    )
    parser.add_argument("--kind", choices=("query",), required=True)
    parser.add_argument(
        "--gold-set",
        type=Path,
        default=PROJECT_ROOT
        / "tests"
        / "fixtures"
        / "kb_query_gold_set.v1.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "review_work" / "ark_kb_gold" / "query",
    )
    parser.add_argument("--author-id", required=True)
    parser.add_argument("--author-key-fingerprint", required=True)
    parser.add_argument("--seed", required=True)
    parser.add_argument(
        "--created-at",
        default=None,
        help="ISO-8601 timestamp override for reproducible exports.",
    )
    return parser


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    created_at = args.created_at or datetime.now(timezone.utc).isoformat()
    try:
        pack = build_query_review_pack(
            gold_set_path=_absolute(args.gold_set),
            author_id=args.author_id,
            author_key_fingerprint=args.author_key_fingerprint,
            seed=args.seed,
            created_at=created_at,
            tool_version=TOOL_VERSION,
        )
        pack_path = _absolute(args.output) / str(pack["packId"])
        pack_path = pack_path / "review_pack.json"
        _write_json_atomic(pack_path, pack)
    except (GoldReviewError, OSError) as error:
        print(
            json.dumps(
                {"status": "INVALID", "error": str(error)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "schema": pack["schema"],
                "kind": pack["kind"],
                "packId": pack["packId"],
                "packSha256": pack["packSha256"],
                "candidateCases": len(pack["candidates"]),
                "packPath": str(pack_path.resolve()),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
