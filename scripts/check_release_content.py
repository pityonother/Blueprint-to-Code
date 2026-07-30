"""Check the exact public source inventory for local filesystem paths."""

from __future__ import annotations

import argparse
from pathlib import Path

from release_content_policy import (
    collect_git_ref_entries,
    collect_tracked_worktree_entries,
    scan_release_entries,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan tracked release content for local path leaks."
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--tracked-worktree", action="store_true")
    modes.add_argument("--git-ref")
    return parser


def _print_report(report) -> None:
    print(f"scannedFiles={report.scanned_files}")
    print(f"scannedTextFiles={report.scanned_text_files}")
    print(f"skippedBinaryFiles={report.skipped_binary_files}")
    for reason, count in report.skipped_binary_reasons:
        print(f"skippedBinaryReason.{reason}={count}")
    print(f"findings={len(report.findings)}")
    for finding in report.findings:
        print(f"relativePath={finding.relative_path}")
        print(f"line={finding.line}")
        print(f"category={finding.category}")
        print(f"redactedMatch={finding.redacted_match}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        entries = (
            collect_tracked_worktree_entries(root)
            if args.tracked_worktree
            else collect_git_ref_entries(root, args.git_ref)
        )
        report = scan_release_entries(entries, repository_root=root)
    except Exception:
        print("status=FAIL")
        print("relativePath=<scanner>")
        print("line=0")
        print("category=policy-error")
        print("redactedMatch=<internal-error>")
        return 2
    print("status=PASS" if not report.findings else "status=FAIL")
    _print_report(report)
    return 0 if not report.findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
