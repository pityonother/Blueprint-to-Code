from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
ALLOWED_PROPOSAL_ROOT = (
    PROJECT_ROOT / "review_work" / "ark_kb_gold" / "freeze_proposals"
)
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.gold_freeze import (  # noqa: E402
    QUERY_GOLD_TARGET_RELATIVE_PATH,
    signed_freeze_approval_blocker,
    validate_and_propose_gold_freeze,
)
from blueprint_translator.kb_vnext.gold_review_v2 import (  # noqa: E402
    GoldReviewV2Error,
    parse_strict_json_bytes,
)

ALLOWED_GOLD_TARGET = (
    PROJECT_ROOT / QUERY_GOLD_TARGET_RELATIVE_PATH
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a signed-v2 Gold freeze proposal without writing "
            "production Gold."
        )
    )
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--registry-v2", type=Path)
    parser.add_argument("--expected-registry-sha256")
    parser.add_argument("--expected-pack-author-key-fingerprint")
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument(
        "--expected-source-manifest-sha256",
        required=True,
    )
    parser.add_argument("--gold-target", type=Path, required=True)
    parser.add_argument(
        "--expected-gold-target-sha256",
        required=True,
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Reserved for a later signed approval contract; Stage13C "
            "always blocks this option without writing Gold."
        ),
    )
    return parser


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _read_json(path: Path, *, field: str) -> object:
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise GoldReviewV2Error(f"cannot read {field}: {path}") from error
    return parse_strict_json_bytes(payload, field=field)


def _load_receipts(path: Path | None) -> list[Mapping[str, object]]:
    if path is None:
        return []
    absolute = _contained_path(path, field="Gold freeze receipts")
    paths = sorted(absolute.glob("*.json")) if absolute.is_dir() else [absolute]
    receipts: list[Mapping[str, object]] = []
    for receipt_path in paths:
        receipt_path = _contained_input(
            receipt_path,
            field="Gold freeze receipt",
        )
        raw = _read_json(
            receipt_path,
            field=f"Gold freeze receipt {receipt_path}",
        )
        values = raw if isinstance(raw, list) else [raw]
        if any(not isinstance(value, Mapping) for value in values):
            raise GoldReviewV2Error(
                f"Gold freeze receipt is malformed: {receipt_path}"
            )
        receipts.extend(values)
    return receipts


def _contained_path(path: Path, *, field: str) -> Path:
    try:
        resolved = _absolute(path).resolve(strict=True)
        resolved.relative_to(PROJECT_ROOT.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise GoldReviewV2Error(
            f"{field} is outside repository root or unreadable"
        ) from error
    return resolved


def _contained_input(path: Path, *, field: str) -> Path:
    resolved = _contained_path(path, field=field)
    if not resolved.is_file():
        raise GoldReviewV2Error(f"{field} must be a file")
    return resolved


def _allowlisted_gold_input(path: Path, *, field: str) -> Path:
    resolved = _contained_input(path, field=field)
    try:
        allowed_target = ALLOWED_GOLD_TARGET.resolve(strict=True)
    except OSError as error:
        raise GoldReviewV2Error(
            "allowlisted query Gold target is unreadable"
        ) from error
    if resolved != allowed_target:
        raise GoldReviewV2Error(
            f"{field} must be the allowlisted query Gold target: "
            "tests/fixtures/kb_query_gold_set.v1.json"
        )
    return resolved


def _contained_output(path: Path, *, forbidden: set[Path]) -> Path:
    try:
        parent = _absolute(path).parent.resolve(strict=True)
        proposal_root = ALLOWED_PROPOSAL_ROOT.resolve(strict=True)
        parent.relative_to(proposal_root)
    except (OSError, ValueError) as error:
        raise GoldReviewV2Error(
            "proposal output must be under the existing ignored "
            "review_work/ark_kb_gold/freeze_proposals directory"
        ) from error
    candidate = parent / path.name
    if candidate in forbidden:
        raise GoldReviewV2Error(
            "proposal output cannot be a source or Gold target"
        )
    if candidate.exists() or candidate.is_symlink():
        raise GoldReviewV2Error(
            "proposal output must not already exist"
        )
    return candidate


def _write_proposal_atomic(path: Path, value: Mapping[str, object]) -> None:
    payload = (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        if path.exists() or path.is_symlink():
            raise GoldReviewV2Error(
                "proposal output must not already exist"
            )
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        temporary.unlink()
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--apply" in arguments:
        result = signed_freeze_approval_blocker()
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 3
    args = _parser().parse_args(arguments)
    try:
        pack_path = _contained_input(
            args.pack,
            field="Gold freeze review pack",
        )
        raw_pack = _read_json(
            pack_path,
            field="Gold freeze review pack",
        )
        if not isinstance(raw_pack, Mapping):
            raise GoldReviewV2Error("Gold freeze pack must be an object")
        source_path = _allowlisted_gold_input(
            args.source_manifest,
            field="source manifest",
        )
        target_path = _allowlisted_gold_input(
            args.gold_target,
            field="Gold target",
        )
        if source_path != target_path:
            raise GoldReviewV2Error(
                "source manifest and Gold target must be the same "
                "allowlisted raw file"
            )
        output_path = (
            _contained_output(
                args.output,
                forbidden={source_path, target_path},
            )
            if args.output is not None
            else None
        )
        byte_snapshots: dict[Path, bytes] = {}

        def read_once(path: Path) -> bytes:
            if path not in byte_snapshots:
                byte_snapshots[path] = path.read_bytes()
            return byte_snapshots[path]

        source_bytes = read_once(source_path)
        target_bytes = read_once(target_path)

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
                    "signed v2 freeze validation requires "
                    + ", ".join(missing)
                )
            raw_registry = _read_json(
                _contained_input(
                    args.registry_v2,
                    field="Gold freeze reviewer registry",
                ),
                field="Gold freeze reviewer registry",
            )
            if not isinstance(raw_registry, Mapping):
                raise GoldReviewV2Error(
                    "Gold freeze reviewer registry must be an object"
                )
            registry = raw_registry
            artifact_root = _contained_path(
                args.artifact_root,
                field="Gold freeze artifact root",
            )
            if not artifact_root.is_dir():
                raise GoldReviewV2Error(
                    "Gold freeze artifact root must be a directory"
                )
        result = validate_and_propose_gold_freeze(
            raw_pack,
            receipts,
            registry=registry,
            expected_registry_sha256=args.expected_registry_sha256,
            expected_pack_author_key_fingerprint=(
                args.expected_pack_author_key_fingerprint
            ),
            artifact_root=artifact_root,
            source_manifest_bytes=source_bytes,
            expected_source_manifest_sha256=(
                args.expected_source_manifest_sha256
            ),
            target_bytes=target_bytes,
            expected_target_sha256=args.expected_gold_target_sha256,
            target_relative_path=target_path.relative_to(
                PROJECT_ROOT.resolve(strict=True)
            ).as_posix(),
        )
        if result["proposalReady"] and output_path is not None:
            _write_proposal_atomic(output_path, result)
    except (GoldReviewV2Error, OSError, ValueError) as error:
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
    return 0 if result["proposalReady"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
