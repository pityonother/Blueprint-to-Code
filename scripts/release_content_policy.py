"""Fail closed when public release content contains local filesystem paths."""

from __future__ import annotations

import getpass
import io
import re
import subprocess
import tarfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable
from urllib.parse import unquote


_KNOWN_CATEGORIES = frozenset(
    {
        "absolute-workspace-path",
        "current-repository-root",
        "documents-and-settings-directory",
        "linux-root-directory",
        "linux-user-directory",
        "local-file-uri",
        "macos-user-directory",
        "unc-workspace-path",
        "windows-user-directory",
    }
)
_ROOT_SEGMENT = "ro" + "ot"
_WINDOWS_USER_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?P<path>[A-Za-z]:/+Users/+"
    r"(?P<user>[A-Za-z0-9._-]+)(?:/+[^\s\"'<>]*)?)"
)
_DOCUMENTS_AND_SETTINGS_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?P<path>[A-Za-z]:/+Documents and Settings/+"
    r"(?P<user>[A-Za-z0-9._-]+)(?:/+[^\s\"'<>]*)?)"
)
_MACOS_USER_RE = re.compile(
    r"(?i)(?<![:A-Za-z0-9])(?P<path>/Users/(?P<user>[A-Za-z0-9._-]+)"
    r"(?:/[^\s\"'<>]*)?)"
)
_LINUX_USER_RE = re.compile(
    r"(?i)(?<![:A-Za-z0-9])(?P<path>/home/(?P<user>[A-Za-z0-9._-]+)"
    r"(?:/[^\s\"'<>]*)?)"
)
_LINUX_ROOT_RE = re.compile(
    rf"(?i)(?<![:A-Za-z0-9])(?P<path>/{_ROOT_SEGMENT}"
    r"(?=$|[\s\"'<>\)\]\}]|/)(?:/[^\s\"'<>]*)?)"
)
_FILE_URI_RE = re.compile(
    rf"(?i)(?P<path>file:/+(?:[A-Za-z]:/|Users/|home/|{_ROOT_SEGMENT}"
    r"(?:/|$)|/)[^\s\"'<>\)\]\}]*)"
)
_UNC_BACKSLASH_RE = re.compile(
    r"(?i)(?<![:A-Za-z0-9_\\])(?P<path>\\{2,}"
    r"(?P<server>[A-Za-z0-9$][A-Za-z0-9._$-]*)\\+"
    r"(?P<share>[A-Za-z0-9._$-]+)(?:\\+[^\s\"'<>]*)?)"
)
_ABSOLUTE_WORKSPACE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?P<path>[A-Za-z]:/"
    r"(?:[^/\s\"'<>]+/){0,4}"
    r"(?:_work|checkouts?|Desktop|Documents|repos?|source|src|workspaces?)/"
    r"[^\s\"'<>]+)"
)
_WILDCARD_MARKERS = frozenset("*?[]")


@dataclass(frozen=True)
class ReleaseContentEntry:
    """One archive-relative path and its exact release content."""

    relative_path: str
    content: bytes | Path


@dataclass(frozen=True)
class ReleaseContentAllowRule:
    """One exact, explained exception for a non-private fixture."""

    relative_path: str
    category: str
    exact_match: str
    reason: str


@dataclass(frozen=True)
class ReleaseContentFinding:
    relative_path: str
    line: int
    category: str
    redacted_match: str


@dataclass(frozen=True)
class ReleaseContentScanResult:
    scanned_files: int
    scanned_text_files: int
    skipped_binary_files: int
    skipped_binary_reasons: tuple[tuple[str, int], ...]
    findings: tuple[ReleaseContentFinding, ...]


class ReleaseContentPolicyError(ValueError):
    """Raised before packaging when release content violates the policy."""

    def __init__(self, report: ReleaseContentScanResult) -> None:
        super().__init__(
            f"release content policy found {len(report.findings)} local path leak(s)"
        )
        self.report = report


@dataclass(frozen=True)
class _DetectedPath:
    category: str
    exact_match: str
    redacted_match: str
    user: str = ""


def _fixture_user_path(user: str, *parts: str) -> str:
    return "C:/" + "/".join(("Users", user, *parts))


def _fixture_unc_path(server: str, share: str, *parts: str) -> str:
    return "\\\\" + "\\".join((server, share, *parts))


DEFAULT_ALLOW_RULES: tuple[ReleaseContentAllowRule, ...] = (
    ReleaseContentAllowRule(
        relative_path="tests/test_evidence_migration.py",
        category="local-file-uri",
        exact_match="file:" + "///C:/x",
        reason="Synthetic HTML-link fixture proving local links are neutralized.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_kb_query_planner.py",
        category="local-file-uri",
        exact_match="file:" + "///C:/fixture/source",
        reason="Synthetic query-planner fixture proving local URI rejection.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_http_api_contracts.py",
        category="windows-user-directory",
        exact_match=_fixture_user_path("example", "native"),
        reason="Synthetic HTTP response fixture proving path redaction.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_http_api_contracts.py",
        category="windows-user-directory",
        exact_match=_fixture_user_path("example", "claims.json"),
        reason="Synthetic HTTP response fixture proving manifest path redaction.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_job_process_tree.py",
        category="unc-workspace-path",
        exact_match=_fixture_unc_path(
            "build-server",
            "private-share",
            "native",
            "symbols.pdb",
        ),
        reason="Synthetic UNC fixture proving process cleanup remains local.",
    ),
    ReleaseContentAllowRule(
        relative_path=(
            "scripts/devkit_plugins/install_blueprint_to_code_exporter.ps1"
        ),
        category="unc-workspace-path",
        exact_match=_fixture_unc_path("Engine", "Plugins$"),
        reason="Exact PowerShell regex suffix, not a filesystem workspace.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_kb_api.py",
        category="windows-user-directory",
        exact_match=_fixture_user_path("person", "secret.uasset"),
        reason="Synthetic API fixture proving canonical paths are redacted.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_kb_api.py",
        category="windows-user-directory",
        exact_match=_fixture_user_path("person", "evidence.json"),
        reason="Synthetic API fixture proving evidence paths are redacted.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_kb_blueprint_ingest.py",
        category="windows-user-directory",
        exact_match=_fixture_user_path(
            "secret",
            "Desktop",
            "value.txt",
        ),
        reason="Synthetic Blueprint fixture proving local properties are rejected.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_kb_blueprint_ingest.py",
        category="absolute-workspace-path",
        exact_match=_fixture_user_path(
            "secret",
            "Desktop",
            "value.txt",
        ),
        reason="Same exact synthetic Desktop fixture, classified as a workspace.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_kb_migration.py",
        category="windows-user-directory",
        exact_match=_fixture_user_path(
            "secret",
            "captures",
            "Buff_Known",
        ),
        reason="Synthetic migration fixture proving known paths are sanitized.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_kb_migration.py",
        category="windows-user-directory",
        exact_match=_fixture_user_path(
            "secret",
            "captures",
            "Buff_Unknown",
        ),
        reason="Synthetic migration fixture proving unknown paths are sanitized.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_kb_quality_gates.py",
        category="windows-user-directory",
        exact_match=_fixture_user_path(
            "learner",
            "private",
            "report.json",
        ),
        reason="Synthetic quality-gate fixture proving report paths are rejected.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_kb_shadow_compare.py",
        category="windows-user-directory",
        exact_match=_fixture_user_path("person", "secret.json"),
        reason="Synthetic shadow fixture proving evidence paths are redacted.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_knowledge_discovery_bundle.py",
        category="windows-user-directory",
        exact_match=_fixture_user_path(
            "secret",
            "ARKDevkit",
            "Buff_Test.uasset",
        ),
        reason="Synthetic bundle fixture proving game assets are excluded.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_knowledge_discovery_bundle.py",
        category="windows-user-directory",
        exact_match=_fixture_user_path(
            "secret",
            "captures",
            "Buff_Test",
            "uasset_package.json",
        ),
        reason="Synthetic bundle fixture proving capture paths are redacted.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_knowledge_discovery_bundle.py",
        category="windows-user-directory",
        exact_match=_fixture_user_path("secret"),
        reason="Synthetic prose fixture proving embedded paths are detected.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_knowledge_discovery_bundle.py",
        category="windows-user-directory",
        exact_match=_fixture_user_path("secret", "native.bin"),
        reason="Synthetic bundle fixture proving native paths are redacted.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_knowledge_discovery_bundle.py",
        category="windows-user-directory",
        exact_match=_fixture_user_path(
            "secret",
            "captures",
            "Buff_Test",
        ),
        reason="Synthetic discovery fixture proving capture roots are redacted.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_native_identity.py",
        category="windows-user-directory",
        exact_match=_fixture_user_path(
            "fixture",
            "private",
            "workspace",
        ),
        reason="Synthetic native identity fixture proving local roots are rejected.",
    ),
    ReleaseContentAllowRule(
        relative_path="tests/test_release_packaging.py",
        category="windows-user-directory",
        exact_match=_fixture_user_path("someone", "secret.txt"),
        reason="Synthetic archive fixture proving absolute paths are unsafe.",
    ),
)


def _decode_repeated(value: str) -> str:
    decoded = value
    for _ in range(3):
        candidate = unquote(decoded)
        if candidate == decoded:
            break
        decoded = candidate
    return decoded


def _slashified(value: str) -> str:
    return _decode_repeated(value).replace("\\", "/")


def _canonical(value: str) -> str:
    return re.sub(r"/+", "/", _slashified(value)).casefold()


def _safe_rule_path(value: str) -> str:
    normalized = str(value or "").replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _blocked_usernames(repository_root: Path) -> frozenset[str]:
    candidates = {"ac", getpass.getuser(), Path.home().name}
    root_text = repository_root.resolve().as_posix()
    match = _WINDOWS_USER_RE.search(root_text)
    if match is not None:
        candidates.add(match.group("user"))
    return frozenset(
        candidate.casefold() for candidate in candidates if candidate
    )


def validate_allow_rules(
    rules: Iterable[ReleaseContentAllowRule],
    *,
    repository_root: Path,
) -> tuple[ReleaseContentAllowRule, ...]:
    """Reject wildcard, directory-wide, private-user, and root-path exceptions."""

    root = repository_root.resolve()
    canonical_root = _canonical(str(root))
    blocked_users = _blocked_usernames(root)
    validated: list[ReleaseContentAllowRule] = []
    for rule in rules:
        relative = _safe_rule_path(rule.relative_path)
        pure = PurePosixPath(relative)
        if (
            not relative
            or relative.endswith("/")
            or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or any(marker in relative for marker in _WILDCARD_MARKERS)
        ):
            raise ValueError("release allowlist paths must name one exact file")
        if rule.category not in _KNOWN_CATEGORIES:
            raise ValueError("release allowlist category is invalid")
        if (
            not rule.exact_match
            or rule.exact_match in {".*", "*"}
            or len(rule.reason.strip()) < 12
        ):
            raise ValueError(
                "release allowlist rules require an exact match and explanation"
            )
        canonical_match = _canonical(rule.exact_match)
        if canonical_root and canonical_root in canonical_match:
            raise ValueError("the current repository root cannot be allowlisted")
        for detector in (
            _WINDOWS_USER_RE,
            _DOCUMENTS_AND_SETTINGS_RE,
            _MACOS_USER_RE,
            _LINUX_USER_RE,
        ):
            match = detector.search(canonical_match)
            if (
                match is not None
                and match.group("user").casefold() in blocked_users
            ):
                raise ValueError("a real local username cannot be allowlisted")
        validated.append(
            ReleaseContentAllowRule(
                relative_path=relative,
                category=rule.category,
                exact_match=rule.exact_match,
                reason=rule.reason.strip(),
            )
        )
    return tuple(validated)


def _detected_paths(
    value: str,
    *,
    repository_root: Path,
) -> tuple[_DetectedPath, ...]:
    decoded = _decode_repeated(value)
    slashified = decoded.replace("\\", "/")
    canonical = re.sub(r"/+", "/", slashified)
    findings: list[_DetectedPath] = []
    root = re.sub(r"/+", "/", repository_root.resolve().as_posix())
    root_index = canonical.casefold().find(root.casefold())
    if root_index >= 0:
        findings.append(
            _DetectedPath(
                "current-repository-root",
                canonical[root_index : root_index + len(root)],
                "<repository-root>",
            )
        )

    detectors = (
        (
            "local-file-uri",
            _FILE_URI_RE,
            "file://<redacted-local-path>",
        ),
        (
            "windows-user-directory",
            _WINDOWS_USER_RE,
            "C:/Users/<redacted>/...",
        ),
        (
            "documents-and-settings-directory",
            _DOCUMENTS_AND_SETTINGS_RE,
            "C:/Documents and Settings/<redacted>/...",
        ),
        (
            "macos-user-directory",
            _MACOS_USER_RE,
            "/Users/<redacted>/...",
        ),
        (
            "linux-user-directory",
            _LINUX_USER_RE,
            "/home/<redacted>/...",
        ),
        (
            "linux-root-directory",
            _LINUX_ROOT_RE,
            "/" + _ROOT_SEGMENT + "/<redacted>/...",
        ),
        (
            "absolute-workspace-path",
            _ABSOLUTE_WORKSPACE_RE,
            "<absolute-workspace-path>",
        ),
    )
    for category, detector, redacted in detectors:
        for match in detector.finditer(canonical):
            findings.append(
                _DetectedPath(
                    category,
                    match.group("path"),
                    redacted,
                    (
                        match.groupdict().get("user")
                        if "user" in match.groupdict()
                        else ""
                    )
                    or "",
                )
            )
    for match in _UNC_BACKSLASH_RE.finditer(decoded):
        if re.fullmatch(
            r"u[0-9a-f]{4}-?",
            match.group("server"),
            flags=re.IGNORECASE,
        ):
            continue
        findings.append(
            _DetectedPath(
                "unc-workspace-path",
                re.sub(r"/+", "/", match.group("path").replace("\\", "/")),
                "//<redacted-server>/<redacted-share>/...",
            )
        )
    unique: dict[tuple[str, str], _DetectedPath] = {}
    for finding in findings:
        unique[(finding.category, finding.exact_match.casefold())] = finding
    return tuple(unique.values())


def _is_allowed(
    *,
    relative_path: str,
    detected: _DetectedPath,
    rules: tuple[ReleaseContentAllowRule, ...],
) -> bool:
    canonical_match = _canonical(detected.exact_match)
    return any(
        rule.relative_path == relative_path
        and rule.category == detected.category
        and _canonical(rule.exact_match) == canonical_match
        for rule in rules
    )


def _binary_reason(content: bytes) -> str | None:
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return None
    if b"\x00" in content:
        return "nul-byte"
    if not content:
        return None
    controls = sum(
        byte < 32 and byte not in {9, 10, 12, 13}
        for byte in content
    )
    if controls > max(4, len(content) // 50):
        return "control-byte-density"
    return None


def _entry_bytes(entry: ReleaseContentEntry) -> tuple[bytes | None, str | None]:
    if isinstance(entry.content, bytes):
        content = entry.content
    elif isinstance(entry.content, Path):
        with entry.content.open("rb") as stream:
            probe = stream.read(64 * 1024)
            reason = _binary_reason(probe)
            if reason is not None:
                return None, reason
            content = probe + stream.read()
    else:
        raise TypeError("release entry content must be bytes or Path")
    reason = _binary_reason(content[: 64 * 1024])
    return (None, reason) if reason is not None else (content, None)


def _decode_text(content: bytes) -> str:
    if content.startswith(b"\xef\xbb\xbf"):
        return content.decode("utf-8-sig")
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("latin-1")


def _redacted_relative_path(
    relative_path: str,
    *,
    repository_root: Path,
) -> str:
    return (
        "<redacted-path>"
        if _detected_paths(relative_path, repository_root=repository_root)
        else relative_path
    )


def scan_release_entries(
    entries: Iterable[ReleaseContentEntry],
    *,
    repository_root: Path,
    allow_rules: Iterable[ReleaseContentAllowRule] = DEFAULT_ALLOW_RULES,
) -> ReleaseContentScanResult:
    """Scan archive names and all recognizable text without exposing matches."""

    root = repository_root.resolve()
    rules = validate_allow_rules(allow_rules, repository_root=root)
    scanned_files = 0
    scanned_text_files = 0
    skipped_reasons: Counter[str] = Counter()
    findings: list[ReleaseContentFinding] = []
    for entry in entries:
        scanned_files += 1
        relative = _safe_rule_path(entry.relative_path)
        if not relative:
            raise ValueError("release entries require a relative path")
        redacted_relative = _redacted_relative_path(
            relative,
            repository_root=root,
        )
        for detected in _detected_paths(relative, repository_root=root):
            if not _is_allowed(
                relative_path=relative,
                detected=detected,
                rules=rules,
            ):
                findings.append(
                    ReleaseContentFinding(
                        redacted_relative,
                        0,
                        detected.category,
                        detected.redacted_match,
                    )
                )

        content, binary_reason = _entry_bytes(entry)
        if binary_reason is not None:
            skipped_reasons[binary_reason] += 1
            continue
        assert content is not None
        scanned_text_files += 1
        text = _decode_text(content)
        for line_number, line in enumerate(text.splitlines(), start=1):
            for detected in _detected_paths(line, repository_root=root):
                if _is_allowed(
                    relative_path=relative,
                    detected=detected,
                    rules=rules,
                ):
                    continue
                findings.append(
                    ReleaseContentFinding(
                        redacted_relative,
                        line_number,
                        detected.category,
                        detected.redacted_match,
                    )
                )
    unique_findings = tuple(
        dict.fromkeys(
            sorted(
                findings,
                key=lambda item: (
                    item.relative_path,
                    item.line,
                    item.category,
                    item.redacted_match,
                ),
            )
        )
    )
    return ReleaseContentScanResult(
        scanned_files=scanned_files,
        scanned_text_files=scanned_text_files,
        skipped_binary_files=sum(skipped_reasons.values()),
        skipped_binary_reasons=tuple(sorted(skipped_reasons.items())),
        findings=unique_findings,
    )


def require_release_entries_safe(
    entries: Iterable[ReleaseContentEntry],
    *,
    repository_root: Path,
    allow_rules: Iterable[ReleaseContentAllowRule] = DEFAULT_ALLOW_RULES,
) -> ReleaseContentScanResult:
    report = scan_release_entries(
        entries,
        repository_root=repository_root,
        allow_rules=allow_rules,
    )
    if report.findings:
        raise ReleaseContentPolicyError(report)
    return report


def _run_git(root: Path, *args: str) -> bytes:
    process = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError("git could not produce the requested release inventory")
    return process.stdout


def collect_tracked_worktree_entries(
    repository_root: Path,
) -> tuple[ReleaseContentEntry, ...]:
    root = repository_root.resolve()
    raw_paths = _run_git(root, "ls-files", "-z")
    rows = [
        row.decode("utf-8", errors="surrogateescape")
        for row in raw_paths.split(b"\0")
        if row
    ]
    entries: list[ReleaseContentEntry] = []
    for relative in rows:
        path = root / Path(relative)
        if not path.is_file():
            raise RuntimeError("a tracked release file is missing from the worktree")
        entries.append(ReleaseContentEntry(relative, path))
    return tuple(entries)


def collect_git_ref_entries(
    repository_root: Path,
    git_ref: str,
) -> tuple[ReleaseContentEntry, ...]:
    root = repository_root.resolve()
    ref = str(git_ref or "").strip()
    if not ref or ref.startswith("-"):
        raise ValueError("git ref is invalid")
    commit = _run_git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    archive = _run_git(
        root,
        "archive",
        "--format=tar",
        commit.decode("ascii").strip(),
    )
    entries: list[ReleaseContentEntry] = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
        for member in stream.getmembers():
            if member.isfile():
                extracted = stream.extractfile(member)
                if extracted is None:
                    raise RuntimeError("git archive contains an unreadable file")
                entries.append(
                    ReleaseContentEntry(member.name, extracted.read())
                )
            elif member.issym() or member.islnk():
                entries.append(
                    ReleaseContentEntry(
                        member.name,
                        member.linkname.encode("utf-8"),
                    )
                )
    return tuple(entries)
