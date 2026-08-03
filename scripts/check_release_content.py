"""Validate the exact GitHub-style source archive for a release candidate."""

from __future__ import annotations

import argparse
from pathlib import Path

from release_content_policy import (
    collect_git_archive_entries,
    collect_tracked_worktree_entries,
    finding_counts,
    scan_release_entries,
)


_ABSOLUTE_CATEGORIES = {"absolute-path"}
_SECRET_CATEGORIES = {"hard-coded-secret", "secret-signature", "sensitive-file"}
_GENERATED_CATEGORIES = {
    "archive-artifact",
    "capture-artifact",
    "database-artifact",
    "generated-artifact",
    "runtime-artifact",
    "unreal-artifact",
}
_BINARY_CATEGORIES = {"binary-content", "native-binary"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan the exact Git source archive for release-forbidden content."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--git-ref")
    mode.add_argument("--tracked-worktree", action="store_true")
    return parser


def _print_report(report, *, commit: str) -> None:
    counts = finding_counts(report)
    print(f"resolvedCommit={commit}")
    print(f"scannedFiles={report.scanned_files}")
    print(f"scannedTextFiles={report.scanned_text_files}")
    print(f"allowedBinarySourceFiles={report.allowed_binary_files}")
    print(
        "absolutePathFindings="
        + str(report.count_categories(_ABSOLUTE_CATEGORIES))
    )
    print("secretFindings=" + str(report.count_categories(_SECRET_CATEGORIES)))
    print(
        "generatedArtifactFindings="
        + str(report.count_categories(_GENERATED_CATEGORIES))
    )
    print("binaryFindings=" + str(report.count_categories(_BINARY_CATEGORIES)))
    print(f"sourceArchiveFindings={len(report.findings)}")
    for category, count in sorted(counts.items()):
        print(f"findingCategory.{category}={count}")
    for finding in report.findings:
        print(f"relativePath={finding.relative_path}")
        print(f"line={finding.line}")
        print(f"category={finding.category}")
        print(f"redactedMatch={finding.redacted_match}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        if args.tracked_worktree:
            entries = collect_tracked_worktree_entries(root)
            commit = "WORKTREE"
        else:
            entries, commit = collect_git_archive_entries(root, args.git_ref)
        report = scan_release_entries(entries, repository_root=root)
    except Exception:
        print("status=FAIL")
        print("relativePath=<scanner>")
        print("line=0")
        print("category=policy-error")
        print("redactedMatch=<internal-error>")
        return 2

    print("status=PASS" if not report.findings else "status=FAIL")
    _print_report(report, commit=commit)
    return 0 if not report.findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
