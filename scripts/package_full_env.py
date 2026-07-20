"""Build a portable, source-pinned Blueprint to Code Windows release archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit


ARCHIVE_ROOT = "BlueprintToCode"
_BLOCKED_PREFIXES = (
    ".claude/",
    ".git/",
    ".playwright-cli/",
    "analysis/",
    "captures/",
    "knowledge_base/",
    "node_modules/",
    "output/",
    "release/",
    "runtime/downloads/",
)
_BLOCKED_EXACT = {
    "devkit_content_root.txt",
    "devkit_path_mappings.txt",
}
_INTERNAL_DOC_PREFIXES = (
    "docs/GPT_PRO_",
    "docs/SESSION_HANDOFF_",
    "docs/NEXT_CHAT_HANDOFF_",
)


def _normalized_relative(path: str) -> str:
    normalized = str(path or "").replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def is_safe_archive_path(path: str) -> bool:
    normalized = str(path or "").replace("\\", "/")
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return False
    pure = PurePosixPath(normalized)
    return all(part not in {"", ".", ".."} for part in pure.parts)


def should_include_tracked(path: str) -> bool:
    normalized = _normalized_relative(path)
    if not normalized or normalized in _BLOCKED_EXACT:
        return False
    if any(normalized.startswith(prefix) for prefix in _BLOCKED_PREFIXES):
        return False
    if any(normalized.startswith(prefix) for prefix in _INTERNAL_DOC_PREFIXES):
        return False
    lowered = normalized.casefold()
    if lowered.endswith((".uasset", ".uexp", ".ubulk", ".env", ".local")):
        return False
    return True


def resolve_npm_executable() -> str:
    """Return the real npm launcher, including ``npm.cmd`` on Windows."""

    executable = shutil.which("npm.cmd") or shutil.which("npm")
    if executable is None:
        raise FileNotFoundError("npm/npm.cmd was not found on PATH")
    return executable


def sanitize_repository_url(url: str) -> str:
    """Remove HTTP(S) userinfo before recording a Git remote in a release."""

    value = str(url or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"} or parsed.hostname is None:
        return value
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))


def discover_harvest_reports(report_root: Path) -> list[tuple[str, Path, Path, Path]]:
    """Return validated report triplet paths without reading report content."""

    root = report_root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    if "smoke" in root.name.casefold():
        raise ValueError(f"refusing smoke report directory: {root}")
    results: list[tuple[str, Path, Path, Path]] = []
    for ai_path in sorted(root.glob("harvest_ranking_*.ai.json")):
        stem = ai_path.name[: -len(".ai.json")]
        full_path = root / f"{stem}.full.json"
        markdown_path = root / f"{stem}.md"
        for path in (ai_path, full_path, markdown_path):
            if not path.is_file() or path.stat().st_size <= 0:
                raise FileNotFoundError(path)
        results.append((stem, ai_path, full_path, markdown_path))
    if not results:
        raise ValueError(f"no harvest_ranking_*.ai.json reports found in {root}")
    return results


def build_package_manifest(
    *,
    repository_url: str,
    commit: str,
    branch: str,
    generated_at_utc: str,
    file_count: int,
    sample_asset: str,
    sample_revision: str = "",
    harvest_reports: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema": "blueprint-to-code.full-env-package.v2",
        "packageType": "full-environment-internal-beta",
        "repository": repository_url,
        "branch": branch,
        "commit": commit,
        "generatedAtUtc": generated_at_utc,
        "dirty": False,
        "fileCount": int(file_count),
        "sampleAsset": sample_asset,
        "sampleRevision": sample_revision,
        "harvestReports": list(harvest_reports or []),
        "sourceVerification": {
            "sampleEvidence": "validate_evidence_store.py:full",
            "harvestReports": "verify_ark_harvest_report.py",
        },
        "startup": "START_HERE.bat",
        "diagnostics": "DIAGNOSE.bat",
        "evidenceQuery": "scripts/query_blueprint_evidence.py",
    }


def _run(root: Path, *command: str) -> str:
    process = subprocess.run(
        list(command),
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip()
        raise RuntimeError(f"command failed ({' '.join(command)}): {detail}")
    return process.stdout.strip()


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _add_entry(entries: dict[str, Path | bytes], relative: str, source: Path | bytes) -> None:
    archive_name = f"{ARCHIVE_ROOT}/{_normalized_relative(relative)}"
    if not is_safe_archive_path(archive_name):
        raise ValueError(f"unsafe archive path: {archive_name}")
    if archive_name in entries:
        raise ValueError(f"duplicate archive path: {archive_name}")
    entries[archive_name] = source


def _tracked_paths(root: Path) -> list[Path]:
    rows = _run(root, "git", "ls-files").splitlines()
    paths: list[Path] = []
    for row in rows:
        normalized = _normalized_relative(row)
        if not should_include_tracked(normalized):
            continue
        path = root / Path(normalized)
        if path.is_file():
            paths.append(path)
    return sorted(paths, key=lambda path: path.as_posix().casefold())


def _verify_archive(
    path: Path,
    expected_hashes: dict[str, str],
    expected_sums: bytes,
) -> None:
    with zipfile.ZipFile(path) as archive:
        names = [name.replace("\\", "/") for name in archive.namelist()]
        unsafe = [name for name in names if not is_safe_archive_path(name)]
        if unsafe:
            raise ValueError(f"archive contains unsafe paths: {unsafe[:5]}")
        required = {
            f"{ARCHIVE_ROOT}/PACKAGE_MANIFEST.json",
            f"{ARCHIVE_ROOT}/SHA256SUMS.txt",
            f"{ARCHIVE_ROOT}/START_HERE.bat",
            f"{ARCHIVE_ROOT}/DIAGNOSE.bat",
            f"{ARCHIVE_ROOT}/runtime/python/python.exe",
            f"{ARCHIVE_ROOT}/dist/index.html",
            f"{ARCHIVE_ROOT}/scripts/blueprint_tool_server.py",
        }
        missing = sorted(required - set(names))
        if missing:
            raise ValueError(f"archive is missing required files: {missing}")
        forbidden = [
            name
            for name in names
            if name.casefold().endswith((".uasset", ".uexp", ".ubulk"))
            or "/node_modules/" in name.casefold()
            or "/runtime/downloads/" in name.casefold()
        ]
        if forbidden:
            raise ValueError(f"archive contains forbidden files: {forbidden[:5]}")
        sums_name = f"{ARCHIVE_ROOT}/SHA256SUMS.txt"
        actual_sums = archive.read(sums_name)
        if actual_sums != expected_sums:
            raise ValueError("SHA256SUMS.txt differs from the generated checksum manifest")
        parsed_hashes: dict[str, str] = {}
        for raw_line in actual_sums.decode("utf-8").splitlines():
            digest, separator, relative = raw_line.partition("  ")
            archive_name = f"{ARCHIVE_ROOT}/{_normalized_relative(relative)}"
            if (
                not separator
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not is_safe_archive_path(archive_name)
                or archive_name in parsed_hashes
            ):
                raise ValueError(f"invalid checksum entry: {raw_line!r}")
            parsed_hashes[archive_name] = digest
        if parsed_hashes != expected_hashes:
            raise ValueError("SHA256SUMS.txt file set or expected digests do not match")
        for name, expected in parsed_hashes.items():
            actual = _sha256_bytes(archive.read(name))
            if actual != expected:
                raise ValueError(f"archive checksum mismatch: {name}")
        manifest = json.loads(archive.read(f"{ARCHIVE_ROOT}/PACKAGE_MANIFEST.json"))
        if int(manifest.get("fileCount") or -1) != len(names):
            raise ValueError("PACKAGE_MANIFEST.json fileCount does not match ZIP entries")


def _resolve_input(root: Path, value: Path) -> Path:
    path = value.expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def _validate_sample_evidence(root: Path, sample_root: Path) -> dict[str, object]:
    python = root / "runtime" / "python" / "python.exe"
    raw = _run(
        root,
        str(python),
        "scripts/validate_evidence_store.py",
        "--asset-dir",
        str(sample_root),
    )
    payload = json.loads(raw)
    if not payload.get("ok") or payload.get("passed") != 1:
        raise ValueError(f"sample evidence validation failed: {sample_root}")
    report = payload["reports"][0]
    identity = report.get("identity") or {}
    agent_index = (report.get("checks") or {}).get("agentIndex") or {}
    return {
        "asset": sample_root.name,
        "revisionId": str(identity.get("revisionId") or ""),
        "counts": dict(agent_index.get("queryCounts") or {}),
    }


def _validate_harvest_reports(
    root: Path,
    reports: list[tuple[str, Path, Path, Path]],
) -> list[str]:
    python = root / "runtime" / "python" / "python.exe"
    verified: list[str] = []
    for stem, ai_path, full_path, _markdown_path in reports:
        raw = _run(
            root,
            str(python),
            "scripts/verify_ark_harvest_report.py",
            "--full",
            str(full_path),
            "--ai",
            str(ai_path),
        )
        payload = json.loads(raw)
        if not payload.get("valid"):
            raise ValueError(f"harvest report validation failed: {stem}")
        verified.append(stem)
    return verified


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a clean full-environment release ZIP.")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--sample-asset-dir", type=Path, required=True)
    parser.add_argument("--harvest-report-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    root = Path(__file__).resolve().parents[1]
    temporary_output: Path | None = None
    try:
        dirty = _run(root, "git", "status", "--porcelain", "--untracked-files=all")
        if dirty:
            raise ValueError("refusing to package a dirty working tree")
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
        short_commit = _run(root, "git", "rev-parse", "--short=10", "HEAD")
        branch = _run(root, "git", "branch", "--show-current") or "detached"
        repository_url = sanitize_repository_url(
            _run(root, "git", "remote", "get-url", "origin")
        )
        generated_at = datetime.now(timezone.utc)
        entries: dict[str, Path | bytes] = {}

        sample_root = _resolve_input(root, args.sample_asset_dir)
        report_root = _resolve_input(root, args.harvest_report_dir)
        sample_validation = _validate_sample_evidence(root, sample_root)
        report_triplets = discover_harvest_reports(report_root)
        verified_reports = _validate_harvest_reports(root, report_triplets)

        for path in _tracked_paths(root):
            _add_entry(entries, path.relative_to(root).as_posix(), path)
        for path in sorted(dist_dir.rglob("*")):
            if path.is_file():
                _add_entry(entries, path.relative_to(root).as_posix(), path)

        sample_asset = sample_root.name
        sample_files = (
            sample_root / "evidence" / "evidence.sqlite",
            sample_root / "evidence" / "manifest.json",
            sample_root / "output" / "agent_index.md",
        )
        for path in sample_files:
            if not path.is_file():
                raise FileNotFoundError(path)
            relative = Path("captures") / sample_asset / path.relative_to(sample_root)
            _add_entry(entries, relative.as_posix(), path)

        report_files = {
            path
            for _stem, ai_path, full_path, markdown_path in report_triplets
            for path in (ai_path, full_path, markdown_path)
        }
        catalog = report_root / "resource_catalog.json"
        if catalog.is_file():
            report_files.add(catalog)
        for path in sorted(report_files):
            relative = Path("analysis") / "harvest_rankings" / path.relative_to(report_root)
            _add_entry(entries, relative.as_posix(), path)

        _add_entry(
            entries,
            "captures/README.txt",
            (
                "Generated Blueprint captures belong here. The bundled sample contains only derived "
                "evidence, not ARK .uasset/.uexp files.\n"
            ).encode("utf-8"),
        )
        _add_entry(entries, "logs/README.txt", b"Runtime logs are written in this directory.\n")

        manifest = build_package_manifest(
            repository_url=repository_url,
            commit=commit,
            branch=branch,
            generated_at_utc=generated_at.isoformat(),
            file_count=len(entries) + 2,
            sample_asset=sample_asset,
            sample_revision=str(sample_validation["revisionId"]),
            harvest_reports=verified_reports,
        )
        manifest_bytes = (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        _add_entry(entries, "PACKAGE_MANIFEST.json", manifest_bytes)

        hashes: dict[str, str] = {}
        for name, source in entries.items():
            hashes[name] = _sha256_bytes(source) if isinstance(source, bytes) else _sha256_file(source)
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
        stamp = generated_at.astimezone().strftime("%Y%m%d-%H%M%S")
        output_path = output_dir / f"BlueprintToCode_full_env_{stamp}_{short_commit}.zip"
        temporary_output = output_path.with_suffix(".tmp.zip")
        if temporary_output.exists():
            temporary_output.unlink()
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

        _verify_archive(temporary_output, hashes, sums)
        temporary_output.replace(output_path)
        temporary_output = None
        payload: dict[str, Any] = {
            "schema": "blueprint-to-code.package-result.v1",
            "path": str(output_path),
            "sha256": _sha256_file(output_path),
            "sizeBytes": output_path.stat().st_size,
            "entryCount": len(entries),
            "commit": commit,
            "branch": branch,
            "dirty": False,
            "sampleAsset": sample_asset,
            "archiveVerified": True,
            "sampleEvidenceVerified": True,
            "harvestReportsVerified": verified_reports,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if temporary_output is not None and temporary_output.exists():
            temporary_output.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
