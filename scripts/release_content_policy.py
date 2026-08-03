"""Fail-closed policy for the exact Git-generated source archive."""

from __future__ import annotations

import getpass
import io
import os
import re
import stat
import subprocess
import tarfile
import zlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable
from urllib.parse import unquote


MAX_ARCHIVE_BYTES = 96 * 1024 * 1024
MAX_ENTRY_BYTES = 12 * 1024 * 1024
MAX_PNG_TEXT_BYTES = 1024 * 1024
MAX_PNG_TEXT_CHUNKS = 64
MAX_PNG_DECODED_BYTES = 64 * 1024 * 1024

_GENERATED_ROOTS = frozenset(
    {
        "analysis",
        "build",
        "coverage",
        "dist",
        "dist-ssr",
        "htmlcov",
        "knowledge_base",
        "node_modules",
        "output",
        "release",
    }
)
_GENERATED_PARTS = frozenset(
    {
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "analysis",
        "binaries",
        "build",
        "captures",
        "coverage",
        "dist",
        "dist-ssr",
        "htmlcov",
        "intermediate",
        "knowledge_base",
        "node_modules",
        "output",
        "release",
        "runtime",
        "saved",
    }
)
_GENERATED_NAMES = frozenset({".coverage", "coverage.xml", "junit.xml", "lcov.info"})
_ARCHIVE_SUFFIXES = (
    ".7z",
    ".rar",
    ".tar.gz",
    ".tgz",
    ".whl",
    ".zip",
)
_DATABASE_SUFFIXES = (
    ".db",
    ".sqlite",
    ".sqlite-shm",
    ".sqlite-wal",
    ".sqlite3",
)
_UNREAL_SUFFIXES = (".pak", ".uasset", ".ubulk", ".ucas", ".uexp", ".utoc")
_NATIVE_SUFFIXES = (
    ".a",
    ".bin",
    ".class",
    ".dll",
    ".dmp",
    ".dylib",
    ".exe",
    ".lib",
    ".msi",
    ".o",
    ".obj",
    ".pdb",
    ".pyc",
    ".pyd",
    ".pyo",
    ".so",
    ".wasm",
)
_SENSITIVE_SUFFIXES = (
    ".cer",
    ".crt",
    ".der",
    ".jks",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
    ".ppk",
)
_SENSITIVE_NAMES = frozenset(
    {
        ".env",
        ".npmrc",
        ".pypirc",
        "credentials",
        "id_dsa",
        "id_ed25519",
        "id_rsa",
    }
)
_ALLOWED_BINARY_PREFIXES = ("public/assets/", "src/assets/")
# The v0.3.0 source archive contains PNG assets only. Other media formats stay
# fail-closed until their complete container structure is validated here.
_ALLOWED_BINARY_SUFFIXES = (".png",)
_SOURCE_BUILD_PREFIX = "scripts/blueprint_translator/harvest/build/"
_SOURCE_CODE_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".mjs",
        ".ps1",
        ".py",
        ".sh",
        ".ts",
        ".tsx",
    }
)

_WINDOWS_USER_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?P<path>[A-Za-z]:[\\/]+"
    + r"Users[\\/]+[A-Za-z0-9._-]+(?:[\\/]+[^\s\"'<>]*)?)"
)
_WINDOWS_PROFILE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?P<path>[A-Za-z]:[\\/]+"
    + r"Documents and Settings[\\/]+[A-Za-z0-9._-]+"
    + r"(?:[\\/]+[^\s\"'<>]*)?)"
)
_POSIX_USER_RE = re.compile(
    r"(?i)(?<![:A-Za-z0-9.!])(?P<path>/(?:Users|home)/"
    r"[A-Za-z0-9._-]+(?:/[^\s\"'<>]*)?)"
)
_ROOT_HOME_RE = re.compile(
    r"(?i)(?<![:A-Za-z0-9.!])(?P<path>/"
    + "ro"
    + r"ot(?=$|[/\s\"'<>)\]}])(?:/[^\s\"'<>]*)?)"
)
_MOUNTED_WINDOWS_RE = re.compile(
    r"(?i)(?<![:A-Za-z0-9.!])(?P<path>/"
    + "mnt"
    + r"/[A-Za-z]/Users/"
    r"[A-Za-z0-9._-]+(?:/[^\s\"'<>]*)?)"
)
_WINDOWS_ABSOLUTE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])"
    r"(?P<path>[A-Za-z]:[\\/]+[^\r\n\"'<>`]*?)"
    r"(?=\s+[A-Za-z]:[\\/]|[\"'<>`]|$)"
)
_WORKSPACE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?P<path>[A-Za-z]:/"
    r"(?:[^/\s\"'<>]+/){0,5}"
    r"(?:_work|checkouts?|Desktop|Documents|repos?|source|src|workspaces?)/"
    r"[^\s\"'<>]+)"
)
_POSIX_WORKSPACE_RE = re.compile(
    r"(?i)(?<![:A-Za-z0-9.!])(?P<path>/(?:workspace|workspaces)/"
    r"[^\s\"'<>]+)"
)
_POSIX_SYSTEM_RE = re.compile(
    r"(?i)(?<![:A-Za-z0-9.!])(?P<path>/"
    r"(?:__w|app|data|etc|github|media|mnt|opt|private|run|srv|usr|var|volumes)/"
    r"[^\s\"'<>]+)"
)
_TEMP_RE = re.compile(
    r"(?i)(?<![:A-Za-z0-9.!])(?P<path>/(?:tmp|var/tmp)/[^\s\"'<>]+)"
)
_FILE_URI_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9+.-])(?P<path>"
    + "file:"
    + r"/+(?:[A-Za-z]:/)?[^/\s\"'<>)\]}][^\s\"'<>)\]}]*)"
)
_UNC_RE = re.compile(
    r"(?i)(?<![:A-Za-z0-9_\\])(?P<path>\\{2,}"
    r"[A-Za-z0-9$][A-Za-z0-9._$-]*\\+[A-Za-z0-9._$-]+"
    r"(?:\\+[^\s\"'<>]*)?)"
)
_SLASH_UNC_RE = re.compile(
    r"(?i)(?<![:A-Za-z0-9_/])(?P<path>/{2}"
    r"[A-Za-z0-9$][A-Za-z0-9._$-]*/+[A-Za-z0-9._$-]+"
    r"(?:/+[^\s\"'<>]*)?)"
)
_EXTENDED_UNC_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_\\])(?P<path>\\{2,}\?\\UNC\\+"
    r"[A-Za-z0-9$][A-Za-z0-9._$-]*\\+[A-Za-z0-9._$-]+"
    r"(?:\\+[^\s\"'<>]*)?)"
)
_GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")
_GITHUB_PAT_RE = re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")
_AWS_ACCESS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
_SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")
_STRIPE_LIVE_KEY_RE = re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b")
_BASIC_AUTH_HEADER_RE = re.compile(
    r"(?i)\bAuthorization\s*:\s*Basic\s+[A-Za-z0-9+/]{2,}={0,2}"
    r"(?![A-Za-z0-9+/=])"
)
_BEARER_AUTH_HEADER_RE = re.compile(
    r"(?i)\bAuthorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/-]+={0,2}"
)
_BASIC_AUTH_URL_RE = re.compile(
    r'(?i)https?://[^/@\s:"<>]+'
    + ":"
    + r'[^/@\s"<>]+@[^/\s"<>]+'
)
_CREDENTIAL_URL_RE = re.compile(
    r"(?i)(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqps?)://"
    r'[^/@\s:"<>]+:'
    r'[^/@\s"<>]+@[^/\s"<>]+'
)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")
_CERTIFICATE_RE = re.compile("-----BEGIN " + "CERTIFICATE-----")
_PUTTY_KEY_RE = re.compile(r"(?im)^PuTTY-User-Key-File-[0-9]+:")
_SECRET_QUOTED_ASSIGNMENT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])[\"']?(?:[A-Za-z0-9]+[_-])*"
    r"(?:api[_-]?key|access[_-]?token|client[_-]?secret|"
    r"secret[_-]?access[_-]?key|password|private[_-]?key)[\"']?"
    r"\s*[:=]\s*(?P<quote>[\"'])(?P<secret>[^'\"\r\n]+)(?P=quote)"
    r"(?=\s*(?:[,}\]]|#.*)?$)"
)
_SECRET_UNQUOTED_ASSIGNMENT_RE = re.compile(
    r"(?im)^\s*(?:export\s+)?[\"']?(?:[A-Z0-9]+[_-])*"
    r"(?:API[_-]?KEY|ACCESS[_-]?TOKEN|CLIENT[_-]?SECRET|"
    r"SECRET[_-]?ACCESS[_-]?KEY|PASSWORD|PRIVATE[_-]?KEY)[\"']?"
    r"\s*=\s*(?P<secret>[^\s#'\"}\]\r\n]+)\s*(?:#.*)?$"
)
_SECRET_YAML_ASSIGNMENT_RE = re.compile(
    r"(?im)^\s*[\"']?(?:[A-Z0-9]+[_-])*"
    r"(?:API[_-]?KEY|ACCESS[_-]?TOKEN|CLIENT[_-]?SECRET|"
    r"SECRET[_-]?ACCESS[_-]?KEY|PASSWORD|PRIVATE[_-]?KEY)[\"']?"
    r"\s*:\s*(?P<secret>[^#\r\n]+)\s*(?:#.*)?$"
)
_SECRET_QUERY_RE = re.compile(
    r"(?i)[?&](?:api[_-]?key|access[_-]?token|client[_-]?secret|password)="
    r"(?P<secret>[^&\s\"'<>]+)"
)
_SECRET_CLI_RE = re.compile(
    r"(?i)(?:^|\s)--(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)"
    r"(?:=|\s+)(?P<secret>[^\s\"']+)"
)
_GENERIC_TOKEN_QUOTED_ASSIGNMENT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])[\"']?"
    r"(?:(?:auth|bearer|refresh|session)[_-]?token|token|secret)[\"']?"
    r"\s*[:=]\s*(?P<quote>[\"'])(?P<secret>[^'\"\r\n]+)(?P=quote)"
    r"(?=\s*(?:[,}\]]|#.*)?$)"
)
_GENERIC_TOKEN_YAML_ASSIGNMENT_RE = re.compile(
    r"(?im)^\s*[\"']?"
    r"(?:(?:AUTH|BEARER|REFRESH|SESSION)[_-]?TOKEN|TOKEN|SECRET)[\"']?"
    r"\s*:\s*(?P<secret>[^#\r\n]+)\s*(?:#.*)?$"
)
_GENERIC_TOKEN_ENV_ASSIGNMENT_RE = re.compile(
    r"(?m)^\s*(?:export\s+)?"
    r"(?:(?:AUTH|BEARER|REFRESH|SESSION)[_-]?TOKEN|TOKEN|SECRET)"
    r"\s*=\s*(?P<secret>[^\s#'\"}\]\r\n]+)\s*(?:#.*)?$"
)
_VARIABLE_REFERENCE_RE = re.compile(
    r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})\Z"
)
_WINDOWS_DEVICE_NAMES = frozenset(
    {"aux", "con", "nul", "prn"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)
_PLACEHOLDER_WORDS = (
    "changeme",
    "dummy",
    "example",
    "fake",
    "fixture",
    "not-a-real",
    "test",
)
_PLACEHOLDER_EXACT_VALUES = frozenset({"ed25519privatekey"})


@dataclass(frozen=True)
class ReleaseArchiveEntry:
    relative_path: str
    content: bytes
    entry_type: str = "file"


@dataclass(frozen=True)
class ReleaseContentAllowRule:
    relative_path: str
    exact_match: str
    reason: str
    max_occurrences: int = 1


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
    allowed_binary_files: int
    findings: tuple[ReleaseContentFinding, ...]

    def count_categories(self, categories: set[str]) -> int:
        return sum(finding.category in categories for finding in self.findings)


def _synthetic_windows_path(user: str, *parts: str) -> str:
    return "C" + ":/" + "/".join(("Users", user, *parts))


def _synthetic_unc(server: str, share: str, *parts: str) -> str:
    return "\\\\" + "\\".join((server, share, *parts))


def _synthetic_posix_path(*parts: str) -> str:
    return "/" + "/".join(parts)


def _documented_windows_path(
    relative_path: str,
    exact_match: str,
    *,
    max_occurrences: int = 1,
) -> ReleaseContentAllowRule:
    # Public, user-independent examples already required by source or docs.
    # Rules stay exact, per-file, and occurrence-bounded; _validated_rules
    # separately forbids current user, home, and repository roots.
    return ReleaseContentAllowRule(
        relative_path,
        exact_match,
        "Exact public installation/configuration example; no user profile is named.",
        max_occurrences=max_occurrences,
    )


def _synthetic_windows_fixture(
    relative_path: str,
    exact_match: str,
    *,
    max_occurrences: int = 1,
) -> ReleaseContentAllowRule:
    return ReleaseContentAllowRule(
        relative_path,
        exact_match,
        "Exact synthetic test value exercising path validation or redaction.",
        max_occurrences=max_occurrences,
    )


DEFAULT_ALLOW_RULES: tuple[ReleaseContentAllowRule, ...] = (
    ReleaseContentAllowRule(
        "tests/test_evidence_migration.py",
        "file:" + "///" + "C" + ":/x",
        "Synthetic local-link fixture proving that public HTML is neutralized.",
        max_occurrences=2,
    ),
    ReleaseContentAllowRule(
        "tests/test_kb_query_planner.py",
        "file:" + "///" + "C" + ":/fixture/source",
        "Synthetic query fixture proving local URI rejection.",
    ),
    _documented_windows_path(
        "docs/GHIDRA_NATIVE_ANALYSIS_zh.md",
        "C" ":/Users/",
        max_occurrences=3,
    ),
    _synthetic_windows_fixture(
        "scripts/blueprint_translator/kb_review_subset.py",
        "C" ":/Users/",
    ),
    _synthetic_windows_fixture(
        "tests/test_evidence_migration.py",
        "C" ":/",
    ),
    _synthetic_windows_fixture(
        "tests/test_kb_api.py",
        "C" ":/",
    ),
    _synthetic_windows_fixture(
        "tests/test_kb_api.py",
        "C" ":/Users",
    ),
    ReleaseContentAllowRule(
        "tests/test_kb_blueprint_ingest.py",
        _synthetic_posix_path("workspace", "private", "value.txt"),
        "Exact synthetic POSIX fixture proving private defaults are rejected.",
    ),
    ReleaseContentAllowRule(
        "tests/test_kb_blueprint_ingest.py",
        _synthetic_posix_path("etc", "passwd"),
        "Exact synthetic POSIX fixture proving system paths are rejected.",
    ),
    ReleaseContentAllowRule(
        "tests/test_kb_blueprint_ingest.py",
        _synthetic_posix_path("usr", "local", "bin"),
        "Exact synthetic POSIX fixture proving embedded paths are rejected.",
    ),
    ReleaseContentAllowRule(
        "tests/test_kb_blueprint_ingest.py",
        _synthetic_posix_path("srv", "private", "value.txt"),
        "Exact synthetic POSIX fixture proving service paths are rejected.",
    ),
    ReleaseContentAllowRule(
        "tests/test_kb_blueprint_ingest.py",
        _synthetic_posix_path("data", "private", "value.txt"),
        "Exact synthetic POSIX fixture proving data paths are rejected.",
    ),
    ReleaseContentAllowRule(
        "tests/test_kb_blueprint_ingest.py",
        _synthetic_posix_path("run", "private", "value.txt"),
        "Exact synthetic POSIX fixture proving runtime paths are rejected.",
    ),
    ReleaseContentAllowRule(
        "tests/test_python_interpreter.py",
        _synthetic_posix_path("usr", "bin", "python3"),
        "Exact synthetic interpreter fallback used by the platform contract.",
    ),
    _synthetic_windows_fixture(
        "tests/test_kb_blueprint_ingest.py",
        "C" ":/Users",
    ),
    _synthetic_windows_fixture(
        "tests/test_kb_migration.py",
        "C" ":/Users/",
    ),
    _synthetic_windows_fixture(
        "tests/test_kb_quality_gates.py",
        "C" ":/Users/",
    ),
    _synthetic_windows_fixture(
        "tests/test_kb_roles.py",
        "C" ":/Users/",
    ),
    _synthetic_windows_fixture(
        "tests/test_kb_shadow_compare.py",
        "C" ":/Users",
    ),
    _synthetic_windows_fixture(
        "tests/test_kb_storage.py",
        "C" ":/",
    ),
    _synthetic_windows_fixture(
        "tests/test_knowledge_discovery_bundle.py",
        "C" ":/Users",
    ),
    _synthetic_windows_fixture(
        "tests/test_release_packaging.py",
        "C" ":/Users",
    ),
    _synthetic_windows_fixture(
        "tests/test_report_claims.py",
        "C" ":/",
        max_occurrences=2,
    ),
    _synthetic_windows_fixture(
        "tests/test_update_ark_kb_vnext.py",
        "C" ":/Users/",
        max_occurrences=4,
    ),
    _synthetic_windows_fixture(
        "tests/test_update_ark_kb_vnext.py",
        "C" ":/",
        max_occurrences=2,
    ),
    ReleaseContentAllowRule(
        "tests/test_http_api_contracts.py",
        _synthetic_windows_path("example", "native"),
        "Synthetic API fixture proving native directory path redaction.",
    ),
    ReleaseContentAllowRule(
        "tests/test_http_api_contracts.py",
        _synthetic_windows_path("example", "claims.json"),
        "Synthetic API fixture proving manifest path redaction.",
    ),
    ReleaseContentAllowRule(
        "scripts/devkit_plugins/install_blueprint_to_code_exporter.ps1",
        _synthetic_unc("Engine", "Plugins$"),
        "Exact PowerShell regex suffix, not a filesystem workspace.",
    ),
    ReleaseContentAllowRule(
        "schemas/kb_production_narrow_gate_report_v1.schema.json",
        _synthetic_unc("u0000-", "u0020", "u007f]"),
        "Exact JSON Schema character-range text, not a network path.",
    ),
    ReleaseContentAllowRule(
        "tests/test_job_process_tree.py",
        _synthetic_unc("build-server", "private-share", "native", "symbols.pdb"),
        "Synthetic process fixture proving UNC path redaction.",
    ),
    ReleaseContentAllowRule(
        "tests/test_kb_api.py",
        _synthetic_windows_path("person", "secret.uasset"),
        "Synthetic API fixture proving canonical URI redaction.",
    ),
    ReleaseContentAllowRule(
        "tests/test_kb_api.py",
        _synthetic_windows_path("person", "evidence.json"),
        "Synthetic API fixture proving evidence URI redaction.",
    ),
    ReleaseContentAllowRule(
        "tests/test_kb_blueprint_ingest.py",
        _synthetic_windows_path("secret", "Desktop", "value.txt"),
        "Synthetic Blueprint property proving local default rejection.",
    ),
    ReleaseContentAllowRule(
        "tests/test_kb_migration.py",
        _synthetic_windows_path("secret", "captures", "Buff_Known"),
        "Synthetic migration fixture proving known paths are sanitized.",
    ),
    ReleaseContentAllowRule(
        "tests/test_kb_migration.py",
        _synthetic_windows_path("secret", "captures", "Buff_Unknown"),
        "Synthetic migration fixture proving unknown paths are sanitized.",
    ),
    ReleaseContentAllowRule(
        "tests/test_kb_quality_gates.py",
        _synthetic_windows_path("learner", "private", "report.json"),
        "Synthetic quality fixture proving report paths are rejected.",
    ),
    ReleaseContentAllowRule(
        "tests/test_kb_shadow_compare.py",
        _synthetic_windows_path("person", "secret.json"),
        "Synthetic shadow fixture proving evidence paths are redacted.",
    ),
    ReleaseContentAllowRule(
        "tests/test_knowledge_discovery_bundle.py",
        _synthetic_windows_path("secret"),
        "Synthetic discovery prose proving embedded paths are detected.",
    ),
    ReleaseContentAllowRule(
        "tests/test_knowledge_discovery_bundle.py",
        _synthetic_windows_path("secret", "native.bin"),
        "Synthetic discovery fixture proving native paths are redacted.",
    ),
    ReleaseContentAllowRule(
        "tests/test_knowledge_discovery_bundle.py",
        _synthetic_windows_path("secret", "captures", "Buff_Test"),
        "Synthetic discovery fixture proving capture roots are redacted.",
    ),
    ReleaseContentAllowRule(
        "tests/test_native_identity.py",
        _synthetic_windows_path("fixture", "private", "workspace"),
        "Synthetic native fixture proving local roots are rejected.",
    ),
    ReleaseContentAllowRule(
        "tests/test_release_packaging.py",
        _synthetic_windows_path("someone", "secret.txt"),
        "Synthetic archive fixture proving absolute members are unsafe.",
    ),
    _documented_windows_path(
        "README.md",
        "G" ":/ARKDevkit/Projects/ShooterGame/Mods/Kaminan_server/Content",
    ),
    _documented_windows_path(
        "devkit_content_root.example.txt",
        "C" ":/Program Files/Epic Games/ARKDevkit/Projects/ShooterGame/Content",
    ),
    _documented_windows_path(
        "devkit_path_mappings.example.txt",
        "C" ":/Program Files/Epic Games/ARKDevkit/Projects/ShooterGame/Content",
    ),
    _documented_windows_path(
        "devkit_path_mappings.example.txt",
        "G" ":/ARKDevkit/Projects/ShooterGame/Mods/Kaminan_server/Content",
    ),
    _documented_windows_path(
        "docs/ARK_HARVEST_RANKING_SYSTEM_zh.md",
        "C" ":/Program Files/Epic Games/ARKDevkit",
    ),
    _documented_windows_path(
        "docs/BLUEPRINT_EVIDENCE_STORE_V2_SPEC_zh.md",
        "C" ":/Program Files/Epic Games/ARKDevkit",
    ),
    _documented_windows_path(
        "docs/DEVELOPER_HANDOFF_zh.md",
        "E" ":/AKD/ARKDevkit/Projects/ShooterGame/Content",
    ),
    _documented_windows_path(
        "docs/GHIDRA_NATIVE_ANALYSIS_zh.md",
        "D" ":/tools-projects",
    ),
    _documented_windows_path(
        "docs/GHIDRA_NATIVE_ANALYSIS_zh.md",
        "D" ":/tools-projects/ghidra_12.1.2_PUBLIC",
    ),
    _documented_windows_path(
        "docs/GHIDRA_NATIVE_ANALYSIS_zh.md",
        "D" ":/tools-projects/jdk-21.0.11+10",
    ),
    _documented_windows_path(
        "docs/GHIDRA_NATIVE_ANALYSIS_zh.md",
        "E" ":/ARKDevkit",
    ),
    _documented_windows_path(
        "docs/USER_GUIDE_zh.md",
        "E" ":/AKD/ARKDevkit",
    ),
    _documented_windows_path(
        "docs/USER_GUIDE_zh.md",
        "D" ":/Epic Games/ARKDevkit/Projects/ShooterGame/Content",
    ),
    _documented_windows_path(
        "docs/USER_GUIDE_zh.md",
        "G" ":/ARKDevkit/Projects/ShooterGame/Mods/Kaminan_server/Content",
        max_occurrences=2,
    ),
    _documented_windows_path(
        "docs/ark_kb_vnext/GPT_PRO_STAGE10_12_HANDOFF.md",
        "X" ":/REPLACE_WITH_AUTHORIZED_EVIDENCE_WORKSPACE",
    ),
    _documented_windows_path(
        "reports/tides_of_fortune_2026-07-25.md",
        "C" ":/Program Files/Epic Games/ARKDevkit/Projects/ShooterGame/Content/"
        "Packs/TidesOfFortune/Dinos/Parrot/Parrot_Character_BP.uasset",
    ),
    _documented_windows_path(
        "reports/tides_of_fortune_2026-07-25.md",
        "C" ":/Program Files/Epic Games/ARKDevkit/Projects/ShooterGame/Content/"
        "Packs/TidesOfFortune/Items/Tools/TreasureMapBottle/Gameplay/BaseClasses/"
        "PrimalItem_TreasureMap_Wild_Bottle_Base.uasset",
    ),
    _documented_windows_path(
        "reports/tides_of_fortune_2026-07-25.md",
        "C" ":/Program Files/Epic Games/ARKDevkit/Projects/ShooterGame/Content/"
        "Packs/TidesOfFortune/CoreBlueprints/Skills/DT_ShipSkills.uasset",
    ),
    _documented_windows_path(
        "reports/tides_of_fortune_2026-07-25.md",
        "C" ":/Program Files/Epic Games/ARKDevkit/Projects/ShooterGame/Content/"
        "Packs/TidesOfFortune/CoreBlueprints/Skills/ST_Ship.uasset",
    ),
    _documented_windows_path(
        "reports/tides_of_fortune_2026-07-25.md",
        "C" ":/Program Files/Epic Games/ARKDevkit/Projects/ShooterGame/Content/"
        "Packs/TidesOfFortune/CoreBlueprints/Milestones/DT_Milestones_ToF.uasset",
    ),
    _documented_windows_path(
        "scripts/blueprint_translator/devkit_paths.py",
        "C" ":/Program Files/Epic Games/ARKDevkit/Projects/ShooterGame/Content",
    ),
    _documented_windows_path(
        "scripts/blueprint_translator/devkit_paths.py",
        "D" ":/Epic Games/ARKDevkit/Projects/ShooterGame/Content",
    ),
    _documented_windows_path(
        "scripts/blueprint_translator/devkit_paths.py",
        "E" ":/Epic Games/ARKDevkit/Projects/ShooterGame/Content",
    ),
    _documented_windows_path(
        "scripts/blueprint_translator/devkit_paths.py",
        "G" ":/ARKDevkit/Projects/ShooterGame/Content",
    ),
    _documented_windows_path(
        "scripts/blueprint_translator/devkit_paths.py",
        "C" ":/ProgramData",
    ),
    _documented_windows_path(
        "scripts/blueprint_translator/evidence_writer.py",
        "C" ":/Program Files/Epic Games/ARKDevkit",
    ),
    _documented_windows_path(
        "scripts/build_ark_harvest_explorer.py",
        "C" ":/Program Files/Epic Games/ARKDevkit",
    ),
    _documented_windows_path(
        "scripts/build_ark_resource_node_catalog.py",
        "C" ":/Program Files/Epic Games/ARKDevkit",
    ),
    _documented_windows_path(
        "scripts/devkit_plugins/install_blueprint_to_code_exporter.ps1",
        "C" ":/Program Files/Epic Games/ARKDevKit/Engine/Plugins",
    ),
    _documented_windows_path(
        "scripts/diagnose_blueprint_tool.py",
        "G" ":/ARKDevkit/Projects/ShooterGame/Content",
    ),
    _documented_windows_path(
        "scripts/native_analysis/toolchain.json",
        "C" ":/Program Files/Epic Games/ARKDevkit",
    ),
    _documented_windows_path(
        "scripts/rank_ark_harvest.py",
        "C" ":/Program Files/Epic Games/ARKDevkit",
    ),
    _synthetic_windows_fixture(
        "tests/test_evidence_query.py",
        "C" ":/Fixture/{revision_marker}/TimingAsset.uasset",
    ),
    _documented_windows_path(
        "tests/test_harvest_build_jobs.py",
        "C" ":/Program Files/Epic Games/ARKDevkit/Engine/Binaries/Win64",
    ),
    _synthetic_windows_fixture(
        "tests/test_harvest_report_validation.py",
        "C" ":/ARKDevkit",
    ),
    _documented_windows_path(
        "tests/test_job_process_tree.py",
        "C" ":/Program Files/Epic Games/ARKDevkit/Engine/Binaries/Win64",
    ),
    _synthetic_windows_fixture(
        "tests/test_kb_semantic_adapters.py",
        "C" ":/Unsafe/Buff_Test",
    ),
    _synthetic_windows_fixture(
        "tests/test_native_identity.py",
        "C" ":/private/fixture.dll",
    ),
    _synthetic_windows_fixture(
        "tests/test_read_priority_assets.py",
        "C" ":/ARK/{read_priority_assets.asset_name_from_object_path(object_path)}.uasset",
    ),
    _synthetic_windows_fixture(
        "tests/test_read_priority_assets.py",
        "C" ":/ARK/Buff_Test.uasset",
    ),
    _synthetic_windows_fixture(
        "tests/test_release_packaging.py",
        "C" ":/node/npm.cmd",
        max_occurrences=2,
    ),
    _synthetic_windows_fixture(
        "tests/test_release_packaging.py",
        "E" ":/AKD/ARKDevkit/Projects/ShooterGame/Content",
        max_occurrences=2,
    ),
    _synthetic_windows_fixture(
        "tests/test_release_packaging.py",
        "E" ":/AKD/ARKDevkit",
    ),
    _synthetic_windows_fixture(
        "tests/test_release_packaging.py",
        "E" ":/AKD/../Projects/ShooterGame/Content",
    ),
    _synthetic_windows_fixture(
        "tests/test_release_packaging.py",
        "E" ":/AKD/ARKDevkit/Projects/ShooterGame/Content/n",
        max_occurrences=2,
    ),
    _synthetic_windows_fixture(
        "tests/test_release_packaging.py",
        "E" ":/AKD/ARKDevkit/Projects/ShooterGame/Content/nmalicious",
    ),
    _documented_windows_path(
        "tests/test_release_readiness.py",
        "C" ":/Program Files/Epic Games/ARKDevkit/Projects/ShooterGame/Content",
    ),
    _synthetic_windows_fixture(
        "tests/test_resource_node_catalog_builder.py",
        "C" ":/Content",
    ),
    _synthetic_windows_fixture(
        "tests/test_tool_server.py",
        "E" ":/AKD/ARKDevkit/Projects/ShooterGame/Content",
    ),
    _synthetic_windows_fixture(
        "tests/test_tool_server.py",
        "C" ":/capture/IndexedDefault",
    ),
    _synthetic_windows_fixture(
        "tests/test_tool_server.py",
        "C" ":/capture/IndexedDefault/evidence/evidence.sqlite",
    ),
    _synthetic_windows_fixture(
        "tests/test_tool_server.py",
        "C" ":/capture/IndexedDefault/evidence/manifest.json",
    ),
    _synthetic_windows_fixture(
        "tests/test_tool_server.py",
        "C" ":/capture/IndexedDefault/output/agent_index.md",
    ),
    _synthetic_windows_fixture(
        "tests/test_tool_server.py",
        "C" ":/DevKit/Test.uasset",
    ),
)


def _decode_repeated(value: str) -> str:
    decoded = value
    for _ in range(3):
        candidate = unquote(decoded)
        if candidate == decoded:
            return decoded
        decoded = candidate
    return decoded


def _canonical(value: str) -> str:
    return re.sub(r"/+", "/", _decode_repeated(value).replace("\\", "/")).casefold()


def _validated_rules(
    rules: Iterable[ReleaseContentAllowRule],
    *,
    repository_root: Path,
) -> tuple[ReleaseContentAllowRule, ...]:
    root = _canonical(str(repository_root.resolve()))
    blocked_users = {
        getpass.getuser().casefold(),
        Path.home().name.casefold(),
    }
    root_user = _WINDOWS_USER_RE.search(str(repository_root.resolve()))
    if root_user is not None:
        slashified = root_user.group("path").replace("\\", "/")
        parts = PurePosixPath(slashified).parts
        if len(parts) >= 3:
            blocked_users.add(parts[2].casefold())
    validated: list[ReleaseContentAllowRule] = []
    seen_rules: set[tuple[str, str]] = set()
    for rule in rules:
        relative = rule.relative_path.replace("\\", "/")
        pure = PurePosixPath(relative)
        if (
            not relative
            or pure.is_absolute()
            or ".." in pure.parts
            or any(marker in relative for marker in "*?[]")
            or len(rule.reason.strip()) < 12
            or rule.max_occurrences < 1
        ):
            raise ValueError("release allow rules must be exact and explained")
        identity = (relative, _canonical(rule.exact_match))
        if identity in seen_rules:
            raise ValueError("release allow rules must not be duplicated")
        seen_rules.add(identity)
        if root and root in _canonical(rule.exact_match):
            raise ValueError("the current repository root cannot be allowlisted")
        windows_match = _WINDOWS_USER_RE.search(rule.exact_match)
        profile_match = _WINDOWS_PROFILE_RE.search(rule.exact_match)
        posix_match = _POSIX_USER_RE.search(rule.exact_match)
        mounted_match = _MOUNTED_WINDOWS_RE.search(rule.exact_match)
        matched_users: list[str] = []
        if windows_match is not None:
            parts = PurePosixPath(
                windows_match.group("path").replace("\\", "/")
            ).parts
            if len(parts) >= 3:
                matched_users.append(parts[2])
        if profile_match is not None:
            parts = PurePosixPath(
                profile_match.group("path").replace("\\", "/")
            ).parts
            if len(parts) >= 3:
                matched_users.append(parts[2])
        if posix_match is not None:
            parts = PurePosixPath(posix_match.group("path")).parts
            if len(parts) >= 3:
                matched_users.append(parts[2])
        if mounted_match is not None:
            parts = PurePosixPath(mounted_match.group("path")).parts
            if len(parts) >= 5:
                matched_users.append(parts[4])
        if _ROOT_HOME_RE.search(rule.exact_match) is not None or any(
            user.casefold() in blocked_users for user in matched_users
        ):
            raise ValueError("a current local username cannot be allowlisted")
        validated.append(rule)
    return tuple(validated)


def _is_allowed(
    relative_path: str,
    exact_match: str,
    rules: tuple[ReleaseContentAllowRule, ...],
    usage: Counter[tuple[str, str]],
) -> bool:
    canonical = _canonical(exact_match)
    matching = next(
        (
            rule
            for rule in rules
            if rule.relative_path == relative_path
            and _canonical(rule.exact_match) == canonical
        ),
        None,
    )
    if matching is None:
        return False
    key = (relative_path, canonical)
    usage[key] += 1
    return usage[key] <= matching.max_occurrences


def _path_category(relative_path: str) -> str | None:
    pure = PurePosixPath(relative_path)
    lowered_parts = tuple(part.casefold() for part in pure.parts)
    root = lowered_parts[0] if lowered_parts else ""
    name = pure.name.casefold()
    if root == "runtime":
        return "runtime-artifact"
    if root == "captures":
        return "capture-artifact"
    temporary_part = any(
        part == ".tmp" or part.startswith(".tmp_") for part in lowered_parts
    )
    source_build_package = relative_path.casefold().startswith(_SOURCE_BUILD_PREFIX)
    generated_parts = set(lowered_parts) & _GENERATED_PARTS
    if source_build_package:
        generated_parts.discard("build")
    if (
        root in _GENERATED_ROOTS
        or generated_parts
        or name in _GENERATED_NAMES
        or temporary_part
    ):
        return "generated-artifact"
    if name.endswith(_ARCHIVE_SUFFIXES):
        return "archive-artifact"
    if name.endswith(_DATABASE_SUFFIXES):
        return "database-artifact"
    if name.endswith(_UNREAL_SUFFIXES):
        return "unreal-artifact"
    if name.endswith(_NATIVE_SUFFIXES):
        return "native-binary"
    private_env = name.startswith(".env.") and name != ".env.example"
    if (
        name in _SENSITIVE_NAMES
        or private_env
        or name.endswith(_SENSITIVE_SUFFIXES)
    ):
        return "sensitive-file"
    return None


def _is_allowed_binary(relative_path: str) -> bool:
    lowered = relative_path.casefold()
    return lowered.startswith(_ALLOWED_BINARY_PREFIXES) and lowered.endswith(
        _ALLOWED_BINARY_SUFFIXES
    )


def _local_path_matches(value: str) -> tuple[tuple[str, str], ...]:
    decoded = _decode_repeated(value)
    slashified = decoded.replace("\\", "/")
    matches: list[tuple[str, str]] = []
    covered_spans: list[tuple[int, int]] = []
    for detector, redacted in (
        (_WINDOWS_USER_RE, "<drive>:/Users/<redacted>/..."),
        (
            _WINDOWS_PROFILE_RE,
            "<drive>:/Documents and Settings/<redacted>/...",
        ),
        (_WORKSPACE_RE, "<absolute-workspace-path>"),
    ):
        for match in detector.finditer(slashified):
            span = match.span("path")
            if any(
                span[0] >= covered[0] and span[1] <= covered[1]
                for covered in covered_spans
            ):
                continue
            matches.append((match.group("path"), redacted))
            covered_spans.append(span)
    for detector, redacted in (
        (_FILE_URI_RE, "file://<redacted-local-path>"),
        (_POSIX_USER_RE, "/<user-home>/<redacted>/..."),
        (_ROOT_HOME_RE, "/<root-home>/<redacted>/..."),
        (_MOUNTED_WINDOWS_RE, "/mnt/<drive>/Users/<redacted>/..."),
        (_POSIX_WORKSPACE_RE, "<absolute-workspace-path>"),
        (_POSIX_SYSTEM_RE, "<absolute-posix-path>"),
        (_TEMP_RE, "/<temporary-directory>/<redacted>/..."),
    ):
        for match in detector.finditer(decoded):
            span = match.span("path")
            if any(
                span[0] >= covered[0] and span[1] <= covered[1]
                for covered in covered_spans
            ):
                continue
            matches.append((match.group("path"), redacted))
            covered_spans.append(span)
    for match in _WINDOWS_ABSOLUTE_RE.finditer(slashified):
        raw_absolute = match.group("path")
        absolute = raw_absolute.rstrip(" \t.,;:)]}")
        span = match.span("path")
        span = (span[0], span[1] - (len(raw_absolute) - len(absolute)))
        if any(
            span[0] >= covered[0] and span[1] <= covered[1]
            for covered in covered_spans
        ):
            continue
        matches.append((absolute, "<absolute-windows-path>"))
        covered_spans.append(span)
    for match in _SLASH_UNC_RE.finditer(decoded):
        matches.append(
            (
                match.group("path"),
                "//<redacted-server>/<redacted-share>/...",
            )
        )
    recorded_paths = {_canonical(exact) for exact, _redacted in matches}
    for detector in (_EXTENDED_UNC_RE, _UNC_RE):
        for match in detector.finditer(decoded):
            canonical = _canonical(match.group("path"))
            if canonical in recorded_paths:
                continue
            recorded_paths.add(canonical)
            matches.append(
                (
                    match.group("path"),
                    "//<redacted-server>/<redacted-share>/...",
                )
            )
    return tuple(matches)


def _secret_matches(value: str) -> tuple[str, ...]:
    matches: list[str] = []
    decoded = _decode_repeated(value)
    for detector, redacted in (
        (_GITHUB_TOKEN_RE, "<redacted-github-token>"),
        (_GITHUB_PAT_RE, "<redacted-github-token>"),
        (_AWS_ACCESS_KEY_RE, "<redacted-cloud-access-key>"),
        (_OPENAI_KEY_RE, "<redacted-api-key>"),
        (_SLACK_TOKEN_RE, "<redacted-api-key>"),
        (_STRIPE_LIVE_KEY_RE, "<redacted-api-key>"),
        (_BASIC_AUTH_HEADER_RE, "<redacted-basic-authorization>"),
        (_BEARER_AUTH_HEADER_RE, "<redacted-bearer-authorization>"),
        (_BASIC_AUTH_URL_RE, "<redacted-basic-auth-url>"),
        (_CREDENTIAL_URL_RE, "<redacted-credential-url>"),
        (_PRIVATE_KEY_RE, "<redacted-private-key-header>"),
        (_CERTIFICATE_RE, "<redacted-certificate-header>"),
        (_PUTTY_KEY_RE, "<redacted-private-key-header>"),
    ):
        matches.extend(redacted for _match in detector.finditer(decoded))
    return tuple(matches)


def _is_placeholder_secret(value: str) -> bool:
    stripped = value.strip()
    lowered = stripped.casefold()
    if lowered.rstrip(",") in _PLACEHOLDER_EXACT_VALUES:
        return True
    if _VARIABLE_REFERENCE_RE.fullmatch(stripped):
        return True
    if re.fullmatch(r"<[A-Z][A-Z0-9_]{1,63}>", stripped):
        return True
    return lowered in _PLACEHOLDER_WORDS


def _hard_coded_secret_matches(
    value: str,
    *,
    relative_path: str = "",
) -> tuple[str, ...]:
    matches: list[str] = []
    decoded = _decode_repeated(value)
    detectors = [
        _SECRET_QUOTED_ASSIGNMENT_RE,
        _SECRET_QUERY_RE,
        _SECRET_CLI_RE,
        _GENERIC_TOKEN_QUOTED_ASSIGNMENT_RE,
    ]
    suffix = PurePosixPath(relative_path).suffix.casefold()
    if not relative_path or suffix not in _SOURCE_CODE_SUFFIXES:
        detectors.extend(
            (
                _SECRET_UNQUOTED_ASSIGNMENT_RE,
                _SECRET_YAML_ASSIGNMENT_RE,
                _GENERIC_TOKEN_YAML_ASSIGNMENT_RE,
                _GENERIC_TOKEN_ENV_ASSIGNMENT_RE,
            )
        )
    for detector in detectors:
        for match in detector.finditer(decoded):
            secret = (match.group("secret") or "").strip()
            if secret and not _is_placeholder_secret(secret):
                matches.append("<redacted-secret-assignment>")
    return tuple(matches)


def _relative_secret_matches(
    relative_path: str,
) -> tuple[tuple[str, str], ...]:
    matches: list[tuple[str, str]] = []
    for part in PurePosixPath(relative_path).parts:
        matches.extend(("secret-signature", match) for match in _secret_matches(part))
        matches.extend(
            ("hard-coded-secret", match)
            for match in _hard_coded_secret_matches(part)
        )
    return tuple(matches)


def _safe_relative_path(relative_path: str) -> bool:
    pure = PurePosixPath(relative_path)
    unsafe_windows_part = any(
        part.endswith((" ", "."))
        or part.split(".", maxsplit=1)[0].casefold() in _WINDOWS_DEVICE_NAMES
        for part in pure.parts
    )
    return bool(
        relative_path
        and "\\" not in relative_path
        and ":" not in relative_path
        and not any(ord(character) < 32 for character in relative_path)
        and not pure.is_absolute()
        and all(part not in {"", ".", ".."} for part in pure.parts)
        and not unsafe_windows_part
    )


def _display_path(relative_path: str) -> str:
    if (
        _safe_relative_path(relative_path)
        and not _local_path_matches(relative_path)
        and not _relative_secret_matches(relative_path)
    ):
        return relative_path
    return "<redacted-relative-path>"


def _decode_text(content: bytes) -> str | None:
    if content.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        try:
            return content.decode("utf-32")
        except UnicodeDecodeError:
            return None
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return content.decode("utf-16")
        except UnicodeDecodeError:
            return None
    if b"\x00" in content:
        return None
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _bounded_zlib_bytes(payload: bytes, maximum: int) -> bytes | None:
    try:
        decompressor = zlib.decompressobj()
        decoded = decompressor.decompress(payload, maximum + 1)
        remaining = maximum + 1 - len(decoded)
        if remaining > 0:
            decoded += decompressor.flush(remaining)
    except (ValueError, zlib.error):
        return None
    if (
        len(decoded) > maximum
        or decompressor.unconsumed_tail
        or decompressor.unused_data
        or not decompressor.eof
    ):
        return None
    return decoded


def _bounded_zlib_text(payload: bytes) -> str | None:
    decoded = _bounded_zlib_bytes(payload, MAX_PNG_TEXT_BYTES)
    if decoded is None:
        return None
    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError:
        return decoded.decode("latin-1")


def _png_metadata_text(content: bytes) -> tuple[str, ...] | None:
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    offset = 8
    chunk_index = 0
    saw_idat = False
    saw_iend = False
    saw_plte = False
    idat_payload = bytearray()
    dimensions: tuple[int, int, int, int] | None = None
    metadata: list[str] = []
    metadata_bytes = 0
    while offset < len(content):
        if len(content) - offset < 12:
            return None
        length = int.from_bytes(content[offset : offset + 4], "big")
        chunk_type = content[offset + 4 : offset + 8]
        chunk_end = offset + 12 + length
        if length > MAX_ENTRY_BYTES or chunk_end > len(content):
            return None
        data = content[offset + 8 : offset + 8 + length]
        expected_crc = int.from_bytes(content[offset + 8 + length : chunk_end], "big")
        if zlib.crc32(chunk_type + data) & 0xFFFFFFFF != expected_crc:
            return None
        if not re.fullmatch(rb"[A-Za-z]{4}", chunk_type):
            return None
        if chunk_index == 0 and (chunk_type != b"IHDR" or length != 13):
            return None
        if chunk_type == b"IHDR":
            if chunk_index != 0 or length != 13:
                return None
            width = int.from_bytes(data[0:4], "big")
            height = int.from_bytes(data[4:8], "big")
            bit_depth = data[8]
            color_type = data[9]
            valid_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                3: {1, 2, 4, 8},
                4: {8, 16},
                6: {8, 16},
            }
            if (
                width == 0
                or height == 0
                or color_type not in valid_depths
                or bit_depth not in valid_depths[color_type]
                or data[10] != 0
                or data[11] != 0
                or data[12] != 0
            ):
                return None
            dimensions = (width, height, bit_depth, color_type)
        elif chunk_type == b"PLTE":
            if length == 0 or length % 3 or length > 768 or saw_idat:
                return None
            saw_plte = True
        elif chunk_type == b"IDAT":
            saw_idat = True
            idat_payload.extend(data)
        elif chunk_type == b"zTXt":
            separator = data.find(b"\x00")
            if separator < 1 or separator + 2 > len(data) or data[separator + 1] != 0:
                return None
            text = _bounded_zlib_text(data[separator + 2 :])
            if text is None:
                return None
            metadata.append(text)
        elif chunk_type == b"iTXt":
            separator = data.find(b"\x00")
            if separator < 1 or separator + 3 > len(data):
                return None
            compressed = data[separator + 1]
            method = data[separator + 2]
            remainder = data[separator + 3 :]
            language_end = remainder.find(b"\x00")
            if language_end < 0:
                return None
            remainder = remainder[language_end + 1 :]
            translated_end = remainder.find(b"\x00")
            if translated_end < 0 or method != 0 or compressed not in {0, 1}:
                return None
            payload = remainder[translated_end + 1 :]
            if compressed:
                text = _bounded_zlib_text(payload)
                if text is None:
                    return None
            else:
                try:
                    text = payload.decode("utf-8")
                except UnicodeDecodeError:
                    return None
            metadata.append(text)
        elif chunk_type == b"IEND":
            if length != 0 or chunk_end != len(content):
                return None
            saw_iend = True
        elif chunk_type[:1].isupper():
            return None
        if saw_iend and chunk_type != b"IEND":
            return None
        offset = chunk_end
        chunk_index += 1
        if len(metadata) > MAX_PNG_TEXT_CHUNKS:
            return None
        if metadata:
            metadata_bytes = sum(len(item.encode("utf-8")) for item in metadata)
            if metadata_bytes > MAX_PNG_TEXT_BYTES:
                return None
    if not saw_idat or not saw_iend or dimensions is None:
        return None
    width, height, bit_depth, color_type = dimensions
    if color_type == 3 and not saw_plte:
        return None
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    row_bytes = (width * channels * bit_depth + 7) // 8
    expected_bytes = height * (row_bytes + 1)
    if expected_bytes > MAX_PNG_DECODED_BYTES:
        return None
    decoded_pixels = _bounded_zlib_bytes(bytes(idat_payload), expected_bytes)
    if decoded_pixels is None or len(decoded_pixels) != expected_bytes:
        return None
    if any(decoded_pixels[offset] > 4 for offset in range(0, expected_bytes, row_bytes + 1)):
        return None
    return tuple(metadata)


def _validated_media_metadata(relative_path: str, content: bytes) -> tuple[str, ...] | None:
    if PurePosixPath(relative_path).suffix.casefold() == ".png":
        return _png_metadata_text(content)
    return None


def scan_release_entries(
    entries: Iterable[ReleaseArchiveEntry],
    *,
    repository_root: Path,
    allow_rules: Iterable[ReleaseContentAllowRule] = DEFAULT_ALLOW_RULES,
) -> ReleaseContentScanResult:
    """Scan the exact archive inventory and content without leaking matches."""

    rules = _validated_rules(allow_rules, repository_root=repository_root)
    findings: list[ReleaseContentFinding] = []
    scanned_files = 0
    scanned_text_files = 0
    allowed_binary_files = 0
    seen: set[str] = set()
    allow_usage: Counter[tuple[str, str]] = Counter()

    for entry in entries:
        scanned_files += 1
        relative = entry.relative_path.replace("\\", "/")
        display = _display_path(relative)
        identity = relative.casefold()
        if not _safe_relative_path(relative) or identity in seen:
            findings.append(
                ReleaseContentFinding(display, 0, "unsafe-archive-path", "<redacted-path>")
            )
            continue
        seen.add(identity)

        if entry.entry_type in {"symlink", "hardlink"}:
            findings.append(ReleaseContentFinding(display, 0, "link-entry", "<link>"))
            continue
        if entry.entry_type == "oversized":
            findings.append(
                ReleaseContentFinding(display, 0, "oversized-artifact", "<oversized-file>")
            )
            continue
        if entry.entry_type != "file":
            findings.append(
                ReleaseContentFinding(display, 0, "special-entry", "<special-entry>")
            )
            continue

        category = _path_category(relative)
        if category is not None:
            findings.append(ReleaseContentFinding(display, 0, category, "<forbidden-path>"))
            continue

        filename_paths = _local_path_matches(relative)
        for exact_match, redacted in filename_paths:
            if not _is_allowed(relative, exact_match, rules, allow_usage):
                findings.append(ReleaseContentFinding(display, 0, "absolute-path", redacted))
        for secret_category, redacted in _relative_secret_matches(relative):
            findings.append(
                ReleaseContentFinding(display, 0, secret_category, redacted)
            )

        media_source = _is_allowed_binary(relative)
        text = None if media_source else _decode_text(entry.content)
        if media_source or text is None:
            binary_text = entry.content.decode("latin-1")
            binary_text = re.sub(
                r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]+",
                "\n",
                binary_text,
            )
            for exact_match, redacted in _local_path_matches(binary_text):
                if not _is_allowed(relative, exact_match, rules, allow_usage):
                    findings.append(
                        ReleaseContentFinding(display, 0, "absolute-path", redacted)
                    )
            for redacted in _secret_matches(binary_text):
                findings.append(
                    ReleaseContentFinding(display, 0, "secret-signature", redacted)
                )
            for redacted in _hard_coded_secret_matches(
                binary_text,
                relative_path=relative,
            ):
                findings.append(
                    ReleaseContentFinding(display, 0, "hard-coded-secret", redacted)
                )
            media_metadata = (
                _validated_media_metadata(relative, entry.content)
                if media_source
                else None
            )
            if media_metadata is not None:
                for metadata_text in media_metadata:
                    for _exact_match, redacted in _local_path_matches(metadata_text):
                        findings.append(
                            ReleaseContentFinding(
                                display,
                                0,
                                "absolute-path",
                                redacted,
                            )
                        )
                    for redacted in _secret_matches(metadata_text):
                        findings.append(
                            ReleaseContentFinding(
                                display,
                                0,
                                "secret-signature",
                                redacted,
                            )
                        )
                    for redacted in _hard_coded_secret_matches(
                        metadata_text,
                        relative_path=relative,
                    ):
                        findings.append(
                            ReleaseContentFinding(
                                display,
                                0,
                                "hard-coded-secret",
                                redacted,
                            )
                        )
                allowed_binary_files += 1
            else:
                findings.append(
                    ReleaseContentFinding(display, 0, "binary-content", "<binary-content>")
                )
            continue
        scanned_text_files += 1

        for line_number, line in enumerate(text.splitlines(), start=1):
            for exact_match, redacted in _local_path_matches(line):
                if not _is_allowed(relative, exact_match, rules, allow_usage):
                    findings.append(
                        ReleaseContentFinding(display, line_number, "absolute-path", redacted)
                    )
            for redacted in _secret_matches(line):
                findings.append(
                    ReleaseContentFinding(display, line_number, "secret-signature", redacted)
                )
            for redacted in _hard_coded_secret_matches(
                line,
                relative_path=relative,
            ):
                findings.append(
                    ReleaseContentFinding(display, line_number, "hard-coded-secret", redacted)
                )

    unique = {
        (finding.relative_path, finding.line, finding.category, finding.redacted_match): finding
        for finding in findings
    }
    ordered = tuple(
        sorted(
            unique.values(),
            key=lambda item: (item.relative_path, item.line, item.category),
        )
    )
    return ReleaseContentScanResult(
        scanned_files=scanned_files,
        scanned_text_files=scanned_text_files,
        allowed_binary_files=allowed_binary_files,
        findings=ordered,
    )


def collect_git_archive_entries(
    repository_root: Path,
    git_ref: str,
) -> tuple[tuple[ReleaseArchiveEntry, ...], str]:
    """Read the Git archive for one resolved commit, honoring export-ignore."""

    root = repository_root.resolve()
    revision = subprocess.run(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{git_ref}^{{commit}}"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        raise ValueError("git ref did not resolve to one commit")
    archive = subprocess.run(
        ["git", "archive", "--format=tar", revision],
        cwd=root,
        check=True,
        capture_output=True,
        timeout=120,
    ).stdout
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise ValueError("source archive exceeds the fail-closed size limit")

    entries: list[ReleaseArchiveEntry] = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source:
        for member in source.getmembers():
            if member.isdir():
                continue
            relative = member.name
            if member.issym():
                entries.append(ReleaseArchiveEntry(relative, b"", "symlink"))
                continue
            if member.islnk():
                entries.append(ReleaseArchiveEntry(relative, b"", "hardlink"))
                continue
            if not member.isfile():
                entries.append(ReleaseArchiveEntry(relative, b"", "special"))
                continue
            if member.size > MAX_ENTRY_BYTES:
                entries.append(ReleaseArchiveEntry(relative, b"", "oversized"))
                continue
            extracted = source.extractfile(member)
            if extracted is None:
                raise ValueError("regular archive entry could not be read")
            content = extracted.read(MAX_ENTRY_BYTES + 1)
            if len(content) != member.size:
                raise ValueError("archive entry size changed during read")
            entries.append(ReleaseArchiveEntry(relative, content, "file"))
    return tuple(entries), revision.casefold()


def _worktree_entry_type(path: Path) -> str:
    try:
        status = path.lstat()
    except FileNotFoundError:
        return "missing"
    if stat.S_ISLNK(status.st_mode):
        return "symlink"
    current = path
    while True:
        try:
            current_status = current.lstat()
        except FileNotFoundError:
            return "missing"
        attributes = getattr(current_status, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if attributes & reparse_flag:
            return "symlink"
        if current == current.parent:
            break
        current = current.parent
    if stat.S_ISREG(status.st_mode):
        return "file"
    return "special"


def collect_tracked_worktree_entries(
    repository_root: Path,
) -> tuple[ReleaseArchiveEntry, ...]:
    """Collect the pre-commit candidate using worktree export-ignore rules."""

    root = repository_root.resolve()
    listed = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout
    names = [os.fsdecode(item) for item in listed.split(b"\0") if item]
    attributes = subprocess.run(
        ["git", "check-attr", "-z", "--stdin", "export-ignore"],
        cwd=root,
        check=True,
        input=b"\0".join(os.fsencode(name) for name in names) + b"\0",
        capture_output=True,
        timeout=30,
    ).stdout.split(b"\0")
    if attributes and attributes[-1] == b"":
        attributes.pop()
    if len(attributes) % 3:
        raise ValueError("git check-attr returned an incomplete response")
    exported: dict[str, bool] = {}
    for offset in range(0, len(attributes), 3):
        name = os.fsdecode(attributes[offset])
        attribute = attributes[offset + 1]
        value = attributes[offset + 2]
        if attribute != b"export-ignore":
            raise ValueError("git check-attr returned an unexpected attribute")
        exported[name] = value not in {b"set", b"true"}

    entries: list[ReleaseArchiveEntry] = []
    for name in names:
        if not exported.get(name, True):
            continue
        path = root / Path(name)
        entry_type = _worktree_entry_type(path)
        if entry_type != "file":
            entries.append(ReleaseArchiveEntry(name, b"", entry_type))
            continue
        size = path.stat().st_size
        if size > MAX_ENTRY_BYTES:
            entries.append(ReleaseArchiveEntry(name, b"", "oversized"))
            continue
        content = path.read_bytes()
        if len(content) != size:
            raise ValueError("worktree entry size changed during read")
        entries.append(ReleaseArchiveEntry(name, content, "file"))
    return tuple(entries)


def finding_counts(report: ReleaseContentScanResult) -> Counter[str]:
    return Counter(finding.category for finding in report.findings)
