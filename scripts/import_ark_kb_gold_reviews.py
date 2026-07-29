from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
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


IMPORT_SCHEMA = "ark-kb-gold-review-import/v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Import review receipts into a validation bundle without "
            "writing production gold."
        )
    )
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--trusted-reviewers", type=Path)
    parser.add_argument("--output", type=Path, required=True)
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
    try:
        raw_pack = _read_json(_absolute(args.pack))
        if not isinstance(raw_pack, Mapping):
            raise GoldReviewError("review pack must be an object")
        pack = validate_review_pack(raw_pack)
        reviews = _load_reviews(_absolute(args.reviews))
        trusted = (
            load_trusted_reviewer_registry(
                _absolute(args.trusted_reviewers)
            )
            if args.trusted_reviewers is not None
            else None
        )
        validation = validate_review_set(
            pack,
            reviews,
            trusted_reviewers=trusted,
        )
        result = {
            "schema": IMPORT_SCHEMA,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "pack": pack,
            "reviews": reviews,
            "validation": validation,
            "productionGoldWritten": False,
        }
        _write_json_atomic(_absolute(args.output), result)
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
            {
                "schema": IMPORT_SCHEMA,
                "output": str(_absolute(args.output).resolve()),
                "validation": validation,
                "productionGoldWritten": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if validation["status"] == READY_TO_FREEZE else 2


if __name__ == "__main__":
    raise SystemExit(main())
