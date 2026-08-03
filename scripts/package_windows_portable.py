"""Build the public, data-minimal Blueprint to Code Windows portable ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from package_full_env import (  # noqa: E402
    ARCHIVE_ROOT,
    SEMVER_PATTERN,
    _add_entry,
    _normalized_relative,
    _run,
    _sha256_bytes,
    _sha256_file,
    _verify_archive,
    is_safe_archive_path,
    read_project_version,
    resolve_npm_executable,
    sanitize_repository_url,
    should_include_tracked,
)
from release_content_policy import (  # noqa: E402
    ReleaseArchiveEntry,
    _path_category,
    scan_release_entries,
)


PACKAGE_TYPE = "windows-portable-user-release"
PACKAGE_SCHEMA = "blueprint-to-code.windows-portable-package.v1"
RUNTIME_SOURCE_FILE = "runtime/PYTHON_RUNTIME_SOURCE.txt"
PORTABLE_REQUIRED_FILES = frozenset(
    {
        "DIAGNOSE.bat",
        "PACKAGE_MANIFEST.json",
        "QUICK_START_zh.txt",
        "SHA256SUMS.txt",
        "START_HERE.bat",
        "VERSION",
        "dist/index.html",
        "docs/USER_GUIDE_zh.md",
        "runtime/PYTHON_RUNTIME_SOURCE.txt",
        "runtime/python/LICENSE.txt",
        "runtime/python/python.exe",
        "scripts/blueprint_tool_server.py",
    }
)
_ROOT_FILES = {
    "CHANGELOG.md",
    "DIAGNOSE.bat",
    "QUICK_START_zh.txt",
    "README.md",
    "START_HERE.bat",
    "VERSION",
    "devkit_content_root.example.txt",
    "devkit_path_mappings.example.txt",
    "index.html",
    "package-lock.json",
    "package.json",
    "tsconfig.json",
    "vite.config.ts",
}
_DOC_FILES = {
    "docs/LICENSE_POLICY.md",
    "docs/USER_GUIDE_zh.md",
    "docs/releases/v0.3.1.md",
}
_SOURCE_PREFIXES = (
    "devkit_plugins/",
    "ontology/",
    "public/",
    "runtime/python/",
    "schemas/",
    "scripts/",
    "src/",
)
_PLACEHOLDER_FILES = {
    "analysis/README.txt",
    "captures/README.txt",
    "logs/README.txt",
}
_FORBIDDEN_DATA_PREFIXES = (
    "analysis/",
    "captures/",
    "knowledge_base/",
    "native_evidence/",
)
_NEVER_ALLOWED_SUFFIXES = (
    ".dmp",
    ".pdb",
    ".uasset",
    ".ubulk",
    ".ucas",
    ".uexp",
    ".utoc",
)
_RUNTIME_ONLY_SUFFIXES = (
    ".cat",
    ".dll",
    ".exe",
    ".pyd",
    ".zip",
)


def should_include_portable_path(path: str) -> bool:
    """Return whether a tracked source path belongs in the public portable ZIP."""

    normalized = _normalized_relative(path)
    if not should_include_tracked(normalized):
        return False
    if normalized.startswith("runtime/python/") or normalized == RUNTIME_SOURCE_FILE:
        return True
    if _path_category(normalized) is not None:
        return False
    return (
        normalized in _ROOT_FILES
        or normalized in _DOC_FILES
        or any(normalized.startswith(prefix) for prefix in _SOURCE_PREFIXES)
    )


def portable_asset_name(version: str) -> str:
    if not SEMVER_PATTERN.fullmatch(str(version or "")):
        raise ValueError(f"invalid portable package version: {version!r}")
    return f"BlueprintToCode-v{version}-windows-x64-portable.zip"


def _parse_runtime_source(root: Path) -> dict[str, str]:
    source_path = root.resolve() / RUNTIME_SOURCE_FILE
    fields: dict[str, str] = {}
    for line in source_path.read_text(encoding="utf-8-sig").splitlines():
        key, separator, value = line.partition(":")
        if separator and value.strip():
            fields[key.strip()] = value.strip()
    required = ("Bundled runtime", "Source", "SHA-256")
    missing = [key for key in required if not fields.get(key)]
    if missing:
        raise ValueError(f"runtime source metadata is missing: {missing}")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", fields["SHA-256"]):
        raise ValueError("runtime source SHA-256 is invalid")
    parsed = urlsplit(fields["Source"])
    if parsed.scheme != "https" or parsed.hostname != "www.python.org":
        raise ValueError("runtime source must be an HTTPS python.org URL")
    return fields


def _normalized_license_bytes(content: bytes) -> bytes:
    return content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def verify_python_runtime(root: Path) -> dict[str, object]:
    """Verify the bundled runtime against the pinned upstream archive."""

    project_root = root.resolve()
    fields = _parse_runtime_source(project_root)
    source_url = fields["Source"]
    archive_name = PurePosixPath(urlsplit(source_url).path).name
    archive_path = project_root / "runtime" / "downloads" / archive_name
    expected_source_sha = fields["SHA-256"].casefold()
    if _sha256_file(archive_path) != expected_source_sha:
        raise ValueError("bundled Python source archive SHA-256 does not match metadata")

    runtime_root = project_root / "runtime" / "python"
    local_files = {
        path.relative_to(runtime_root).as_posix(): path
        for path in runtime_root.rglob("*")
        if path.is_file()
    }
    with zipfile.ZipFile(archive_path) as archive:
        names = sorted(name for name in archive.namelist() if not name.endswith("/"))
        if names != sorted(local_files):
            missing = sorted(set(names) - set(local_files))
            extra = sorted(set(local_files) - set(names))
            raise ValueError(
                f"bundled Python runtime inventory differs from upstream: "
                f"missing={missing}, extra={extra}"
            )
        inventory = hashlib.sha256()
        for name in names:
            upstream = archive.read(name)
            local = local_files[name].read_bytes()
            if name == "LICENSE.txt":
                upstream_compare = _normalized_license_bytes(upstream)
                local_compare = _normalized_license_bytes(local)
            else:
                upstream_compare = upstream
                local_compare = local
            if upstream_compare != local_compare:
                raise ValueError(f"bundled Python runtime file differs from upstream: {name}")
            inventory.update(name.encode("utf-8"))
            inventory.update(b"\0")
            inventory.update(hashlib.sha256(upstream_compare).digest())

    match = re.search(r"Python\s+(\d+\.\d+\.\d+)", fields["Bundled runtime"])
    if match is None:
        raise ValueError("unable to read bundled Python version")
    return {
        "version": match.group(1),
        "architecture": "x64",
        "source": source_url,
        "sourceSha256": expected_source_sha,
        "inventorySha256": inventory.hexdigest(),
        "fileCount": len(local_files),
        "license": "runtime/python/LICENSE.txt",
    }


def build_portable_manifest(
    *,
    repository_url: str,
    commit: str,
    branch: str,
    generated_at_utc: str,
    file_count: int,
    runtime: dict[str, object],
    version: str,
) -> dict[str, object]:
    return {
        "schema": PACKAGE_SCHEMA,
        "version": version,
        "packageType": PACKAGE_TYPE,
        "platform": "windows",
        "architecture": "x64",
        "installation": "unzip-and-run",
        "userReady": True,
        "repository": repository_url,
        "branch": branch,
        "commit": commit,
        "generatedAtUtc": generated_at_utc,
        "dirty": False,
        "fileCount": int(file_count),
        "startup": "START_HERE.bat",
        "diagnostics": "DIAGNOSE.bat",
        "quickStart": "QUICK_START_zh.txt",
        "localService": "http://127.0.0.1:8765/",
        "prerequisites": {
            "systemPython": False,
            "nodeJs": False,
            "arkDevKitForNewAssets": True,
        },
        "excludedData": [
            "analysis",
            "captures",
            "knowledge_base",
            "native_evidence",
        ],
        "bundledRuntime": dict(runtime),
        "devkitContentRootConfigured": False,
    }


def _tracked_portable_paths(root: Path) -> list[Path]:
    rows = _run(root, "git", "ls-files").splitlines()
    paths: list[Path] = []
    for row in rows:
        normalized = _normalized_relative(row)
        if not should_include_portable_path(normalized):
            continue
        path = root / Path(normalized)
        if not path.is_file():
            raise FileNotFoundError(path)
        paths.append(path)
    return sorted(paths, key=lambda path: path.as_posix().casefold())


def _validate_portable_relative_path(relative: str) -> None:
    normalized = _normalized_relative(relative)
    if not is_safe_archive_path(normalized):
        raise ValueError(f"unsafe portable package path: {relative}")
    lowered = normalized.casefold()
    if lowered in {path.casefold() for path in _PLACEHOLDER_FILES}:
        return
    if any(lowered.startswith(prefix) for prefix in _FORBIDDEN_DATA_PREFIXES):
        raise ValueError(f"portable package contains excluded data: {relative}")
    if lowered.endswith(_NEVER_ALLOWED_SUFFIXES):
        raise ValueError(f"portable package contains prohibited artifact: {relative}")
    if lowered.endswith(_RUNTIME_ONLY_SUFFIXES) and not lowered.startswith(
        "runtime/python/"
    ):
        raise ValueError(f"portable package contains an unapproved binary: {relative}")


def _source_bytes(source: Path | bytes) -> bytes:
    return source if isinstance(source, bytes) else source.read_bytes()


def _portable_scan_path(relative: str) -> str:
    if relative.startswith("dist/"):
        dist_relative = relative.removeprefix("dist/")
        if PurePosixPath(dist_relative).suffix.casefold() == ".png":
            return f"public/assets/portable-dist/{dist_relative}"
        return f"portable_dist/{dist_relative}"
    if relative == RUNTIME_SOURCE_FILE:
        return "portable_runtime/PYTHON_RUNTIME_SOURCE.txt"
    if relative in _PLACEHOLDER_FILES or relative in {
        "PACKAGE_MANIFEST.json",
        "SHA256SUMS.txt",
    }:
        return f"portable_generated/{relative.replace('/', '_')}"
    return relative


def validate_portable_entries(root: Path, entries: dict[str, Path | bytes]) -> None:
    """Apply the source scanner to every non-runtime portable entry."""

    project_root = root.resolve()
    runtime_names = {
        f"runtime/python/{path.relative_to(project_root / 'runtime' / 'python').as_posix()}"
        for path in (project_root / "runtime" / "python").rglob("*")
        if path.is_file()
    }
    if any(
        name.removeprefix(f"{ARCHIVE_ROOT}/").startswith("runtime/python/")
        for name in entries
    ):
        verify_python_runtime(project_root)
    scan_entries: list[ReleaseArchiveEntry] = []
    for archive_name, source in entries.items():
        relative = archive_name.removeprefix(f"{ARCHIVE_ROOT}/")
        _validate_portable_relative_path(relative)
        if relative.startswith("runtime/python/"):
            if relative not in runtime_names:
                raise ValueError(f"unverified portable runtime entry: {relative}")
            continue
        scan_entries.append(
            ReleaseArchiveEntry(
                _portable_scan_path(relative),
                _source_bytes(source),
                "file",
            )
        )
    report = scan_release_entries(scan_entries, repository_root=project_root)
    if report.findings:
        categories = ",".join(sorted({item.category for item in report.findings}))
        raise ValueError(f"portable content policy findings: {categories}")


def validate_required_portable_names(names: set[str] | list[str]) -> None:
    normalized = {name.replace("\\", "/") for name in names}
    missing = sorted(
        f"{ARCHIVE_ROOT}/{relative}"
        for relative in PORTABLE_REQUIRED_FILES
        if f"{ARCHIVE_ROOT}/{relative}" not in normalized
    )
    if missing:
        raise ValueError(f"portable archive is missing required files: {missing}")


def _verify_public_archive(
    path: Path,
    expected_hashes: dict[str, str],
    expected_sums: bytes,
) -> None:
    _verify_archive(path, expected_hashes, expected_sums)
    with zipfile.ZipFile(path) as archive:
        names = [name.replace("\\", "/") for name in archive.namelist()]
        if len(names) != len(set(names)):
            raise ValueError("portable archive contains duplicate paths")
        validate_required_portable_names(names)
        expected_names = set(expected_hashes) | {f"{ARCHIVE_ROOT}/SHA256SUMS.txt"}
        if set(names) != expected_names:
            raise ValueError("portable archive entry set differs from its checksums")
        for name in names:
            relative = name.removeprefix(f"{ARCHIVE_ROOT}/")
            _validate_portable_relative_path(relative)
        manifest = json.loads(archive.read(f"{ARCHIVE_ROOT}/PACKAGE_MANIFEST.json"))
        if manifest.get("packageType") != PACKAGE_TYPE:
            raise ValueError("portable archive package type is invalid")
        if not manifest.get("userReady"):
            raise ValueError("portable archive is not marked user-ready")
        if manifest.get("platform") != "windows" or manifest.get("architecture") != "x64":
            raise ValueError("portable archive platform contract is invalid")
        if f"{ARCHIVE_ROOT}/devkit_content_root.txt" in names:
            raise ValueError("portable archive contains a builder-specific DevKit path")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the public Blueprint to Code Windows x64 portable ZIP."
    )
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    root = Path(__file__).resolve().parents[1]
    temporary_output: Path | None = None
    temporary_checksum: Path | None = None
    try:
        dirty = _run(root, "git", "status", "--porcelain", "--untracked-files=all")
        if dirty:
            raise ValueError("refusing to package a dirty working tree")
        runtime = verify_python_runtime(root)
        _run(root, resolve_npm_executable(), "run", "build")
        dirty_after_build = _run(
            root, "git", "status", "--porcelain", "--untracked-files=all"
        )
        if dirty_after_build:
            raise ValueError("build changed tracked or untracked release sources")
        dist_dir = root / "dist"
        if not (dist_dir / "index.html").is_file():
            raise FileNotFoundError(dist_dir / "index.html")

        commit = _run(root, "git", "rev-parse", "HEAD")
        branch = _run(root, "git", "branch", "--show-current") or "detached"
        repository_url = sanitize_repository_url(
            _run(root, "git", "remote", "get-url", "origin")
        )
        version = read_project_version(root)
        generated_at = datetime.now(timezone.utc)
        entries: dict[str, Path | bytes] = {}
        for source in _tracked_portable_paths(root):
            _add_entry(entries, source.relative_to(root).as_posix(), source)
        for source in sorted(dist_dir.rglob("*")):
            if source.is_file():
                _add_entry(entries, source.relative_to(root).as_posix(), source)
        for relative, content in (
            (
                "captures/README.txt",
                b"User-generated Blueprint evidence is stored in this directory.\n",
            ),
            (
                "analysis/README.txt",
                b"User-generated analysis outputs are stored in this directory.\n",
            ),
            ("logs/README.txt", b"Runtime logs are written in this directory.\n"),
        ):
            _add_entry(entries, relative, content)

        manifest = build_portable_manifest(
            repository_url=repository_url,
            commit=commit,
            branch=branch,
            generated_at_utc=generated_at.isoformat(),
            file_count=len(entries) + 2,
            runtime=runtime,
            version=version,
        )
        manifest_bytes = (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        _add_entry(entries, "PACKAGE_MANIFEST.json", manifest_bytes)
        validate_portable_entries(root, entries)

        hashes = {
            name: _sha256_bytes(source)
            if isinstance(source, bytes)
            else _sha256_file(source)
            for name, source in entries.items()
        }
        sums = "".join(
            f"{digest}  {name.removeprefix(f'{ARCHIVE_ROOT}/')}\n"
            for name, digest in sorted(hashes.items())
        ).encode("utf-8")
        _add_entry(entries, "SHA256SUMS.txt", sums)

        output_dir = (
            args.output_dir.expanduser().resolve()
            if args.output_dir is not None
            else Path.home() / "Desktop"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / portable_asset_name(version)
        checksum_path = output_path.with_suffix(output_path.suffix + ".sha256")
        if output_path.exists() or checksum_path.exists():
            raise FileExistsError(
                f"refusing to overwrite an existing release asset: {output_path}"
            )
        temporary_output = output_path.with_suffix(".tmp.zip")
        temporary_checksum = checksum_path.with_suffix(".tmp.sha256")
        with zipfile.ZipFile(
            temporary_output,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as archive:
            for name, source in sorted(entries.items()):
                if isinstance(source, bytes):
                    archive.writestr(name, source)
                else:
                    archive.write(source, name)
        _verify_public_archive(temporary_output, hashes, sums)
        archive_sha256 = _sha256_file(temporary_output)
        temporary_checksum.write_text(
            f"{archive_sha256}  {output_path.name}\n", encoding="utf-8", newline="\n"
        )
        temporary_output.replace(output_path)
        temporary_output = None
        temporary_checksum.replace(checksum_path)
        temporary_checksum = None
        payload: dict[str, Any] = {
            "schema": "blueprint-to-code.windows-portable-result.v1",
            "path": str(output_path),
            "checksumPath": str(checksum_path),
            "sha256": archive_sha256,
            "sizeBytes": output_path.stat().st_size,
            "entryCount": len(entries),
            "commit": commit,
            "branch": branch,
            "dirty": False,
            "archiveVerified": True,
            "runtimeVerified": True,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        for path in (temporary_output, temporary_checksum):
            if path is not None and path.exists():
                path.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
