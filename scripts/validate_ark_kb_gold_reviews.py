from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.gold_review import (  # noqa: E402
    READY_TO_FREEZE,
    GoldReviewError,
    load_trusted_reviewer_registry,
    validate_review_pack,
    validate_review_set,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate ARK KB gold review packs and receipts."
    )
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--trusted-reviewers", type=Path)
    parser.add_argument("--pack-only", action="store_true")
    return parser


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_reviews(path: Path) -> list[Mapping[str, object]]:
    paths = sorted(path.glob("*.json")) if path.is_dir() else [path]
    reviews: list[Mapping[str, object]] = []
    for review_path in paths:
        raw = _read_json(review_path)
        values = raw if isinstance(raw, list) else [raw]
        if any(not isinstance(value, Mapping) for value in values):
            raise GoldReviewError(
                f"review file is malformed: {review_path}"
            )
        reviews.extend(values)
    return reviews


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        raw_pack = _read_json(_absolute(args.pack))
        if not isinstance(raw_pack, Mapping):
            raise GoldReviewError("review pack must be an object")
        pack = validate_review_pack(raw_pack)
        if args.pack_only:
            result = {
                "schema": pack["schema"],
                "packId": pack["packId"],
                "packSha256": pack["packSha256"],
                "candidateCases": len(pack["candidates"]),
                "status": "VALID_REVIEW_PACK",
            }
            exit_code = 0
        else:
            if args.reviews is None:
                raise GoldReviewError(
                    "--reviews is required unless --pack-only is used"
                )
            reviews = _load_reviews(_absolute(args.reviews))
            trusted = (
                load_trusted_reviewer_registry(
                    _absolute(args.trusted_reviewers)
                )
                if args.trusted_reviewers is not None
                else None
            )
            result = validate_review_set(
                pack,
                reviews,
                trusted_reviewers=trusted,
            )
            exit_code = 0 if result["status"] == READY_TO_FREEZE else 2
    except (
        GoldReviewError,
        OSError,
        json.JSONDecodeError,
    ) as error:
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
            result,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
