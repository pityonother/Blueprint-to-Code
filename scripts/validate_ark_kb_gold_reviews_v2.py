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

from blueprint_translator.kb_vnext.gold_review_v2 import (  # noqa: E402
    GoldReviewV2Error,
    parse_strict_json_bytes,
    validate_gold_review_set_v2,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate signed, artifact-bound ARK KB Gold review v2 "
            "receipts without writing production Gold."
        )
    )
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument(
        "--receipts",
        type=Path,
        help="One receipt JSON file or a directory of envelope JSON files.",
    )
    parser.add_argument("--registry-v2", type=Path)
    parser.add_argument(
        "--expected-registry-sha256",
        help=(
            "Out-of-band trusted registry version SHA-256. "
            "It is never inferred from the registry file."
        ),
    )
    parser.add_argument(
        "--expected-pack-author-key-fingerprint",
        help=(
            "Out-of-band trusted SHA-256 fingerprint for the public key "
            "that authored the exact review pack. It is never inferred "
            "from the pack."
        ),
    )
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help=(
            "Diagnose only the named pack case; repeat for multiple cases. "
            "A subset can never complete the full-pack production contract."
        ),
    )
    return parser


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _read_json(path: Path) -> object:
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise GoldReviewV2Error(f"cannot read JSON artifact: {path}") from error
    return parse_strict_json_bytes(
        payload,
        field=f"JSON artifact {path}",
    )


def _load_receipts(path: Path | None) -> list[Mapping[str, object]]:
    if path is None:
        return []
    absolute = _absolute(path)
    paths = sorted(absolute.glob("*.json")) if absolute.is_dir() else [absolute]
    receipts: list[Mapping[str, object]] = []
    for receipt_path in paths:
        raw = _read_json(receipt_path)
        values = raw if isinstance(raw, list) else [raw]
        if any(not isinstance(value, Mapping) for value in values):
            raise GoldReviewV2Error(
                f"receipt file is malformed: {receipt_path}"
            )
        receipts.extend(values)
    return receipts


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        raw_pack = _read_json(_absolute(args.pack))
        if not isinstance(raw_pack, Mapping):
            raise GoldReviewV2Error("review pack must be a JSON object")
        receipts = _load_receipts(args.receipts)
        contains_v1 = any(
            receipt.get("schema") == "ark-kb-gold-review/v1"
            for receipt in receipts
        )
        registry: Mapping[str, object] | None = None
        artifact_root: Path | None = None
        if receipts and not contains_v1:
            missing = [
                name
                for name, value in (
                    ("--registry-v2", args.registry_v2),
                    (
                        "--expected-registry-sha256",
                        args.expected_registry_sha256,
                    ),
                    (
                        "--expected-pack-author-key-fingerprint",
                        args.expected_pack_author_key_fingerprint,
                    ),
                    ("--artifact-root", args.artifact_root),
                )
                if value is None
            ]
            if missing:
                raise GoldReviewV2Error(
                    "signed v2 validation requires " + ", ".join(missing)
                )
            raw_registry = _read_json(_absolute(args.registry_v2))
            if not isinstance(raw_registry, Mapping):
                raise GoldReviewV2Error(
                    "reviewer registry v2 must be a JSON object"
                )
            registry = raw_registry
            artifact_root = _absolute(args.artifact_root)
        validation = validate_gold_review_set_v2(
            raw_pack,
            receipts,
            registry=registry,
            expected_registry_sha256=args.expected_registry_sha256,
            artifact_root=artifact_root,
            expected_pack_author_key_fingerprint=(
                args.expected_pack_author_key_fingerprint
            ),
            required_case_ids=args.case_ids,
        )
        result = validation.to_summary()
    except (GoldReviewV2Error, OSError) as error:
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
    return 0 if validation.production_gold_eligible else 2


if __name__ == "__main__":
    raise SystemExit(main())
