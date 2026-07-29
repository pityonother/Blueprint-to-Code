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
    build_registration_review_pack,
    registration_review_source_from_sqlite,
    validate_registration_review_source,
)


TOOL_VERSION = "ark-kb-gold-review-export/v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export prediction-free ARK KB gold review packs."
    )
    parser.add_argument(
        "--kind",
        choices=("query", "registration"),
        required=True,
    )
    parser.add_argument(
        "--gold-set",
        type=Path,
        default=PROJECT_ROOT
        / "tests"
        / "fixtures"
        / "kb_query_gold_set.v1.json",
    )
    parser.add_argument("--discovery-db", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path)
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


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GoldReviewError(
            f"cannot read review source manifest: {path}"
        ) from error


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    created_at = args.created_at or datetime.now(timezone.utc).isoformat()
    source_manifest: dict[str, object] | None = None
    try:
        if args.kind == "query":
            if args.discovery_db is not None or args.source_manifest is not None:
                raise GoldReviewError(
                    "query export does not accept a Discovery source"
                )
            pack = build_query_review_pack(
                gold_set_path=_absolute(args.gold_set),
                author_id=args.author_id,
                author_key_fingerprint=args.author_key_fingerprint,
                seed=args.seed,
                created_at=created_at,
                tool_version=TOOL_VERSION,
            )
        else:
            if (args.discovery_db is None) == (
                args.source_manifest is None
            ):
                raise GoldReviewError(
                    "registration export requires exactly one of "
                    "--discovery-db or --source-manifest"
                )
            if args.discovery_db is not None:
                source_manifest = (
                    registration_review_source_from_sqlite(
                        _absolute(args.discovery_db)
                    )
                )
            else:
                raw_source = _read_json(
                    _absolute(args.source_manifest)
                )
                if not isinstance(raw_source, dict):
                    raise GoldReviewError(
                        "registration review source must be an object"
                    )
                source_manifest = validate_registration_review_source(
                    raw_source
                )
            pack = build_registration_review_pack(
                source_manifest=source_manifest,
                author_id=args.author_id,
                author_key_fingerprint=args.author_key_fingerprint,
                seed=args.seed,
                created_at=created_at,
                tool_version=TOOL_VERSION,
                limit=args.limit or 120,
            )
        output_root = (
            _absolute(args.output)
            if args.output is not None
            else PROJECT_ROOT
            / "review_work"
            / "ark_kb_gold"
            / args.kind
        )
        pack_path = output_root / str(pack["packId"])
        pack_path = pack_path / "review_pack.json"
        _write_json_atomic(pack_path, pack)
        source_manifest_path: Path | None = None
        if source_manifest is not None:
            source_manifest_path = pack_path.with_name(
                "source_manifest.json"
            )
            _write_json_atomic(source_manifest_path, source_manifest)
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
    result = {
        "schema": pack["schema"],
        "kind": pack["kind"],
        "packId": pack["packId"],
        "packSha256": pack["packSha256"],
        "candidateCases": len(pack["candidates"]),
        "packPath": str(pack_path.resolve()),
    }
    if source_manifest_path is not None:
        result["sourceManifestPath"] = str(
            source_manifest_path.resolve()
        )
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
