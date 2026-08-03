from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.evidence_repository import (  # noqa: E402
    resolve_asset_evidence_state,
)
from blueprint_translator.evidence_writer import (  # noqa: E402
    write_evidence_artifacts_from_payload,
)
from package_full_env import (  # noqa: E402
    ARCHIVE_ROOT,
    _add_entry,
    _sha256_bytes,
    _sha256_file,
    _verify_archive,
    build_package_manifest,
    discover_sample_evidence_files,
)


def _payload(name: str, marker: str) -> dict[str, object]:
    return {
        "asset_name": name,
        "asset_path": f"/Game/Test/{name}.{name}",
        "graphs": [
            {
                "graph": "EventGraph",
                "graph_type": "EventGraph",
                "export_index": 1,
                "status": "complete",
                "confidence": "high",
                "node_count": 1,
                "pin_count": 0,
                "link_count": 0,
                "coverage": {},
                "warnings": [],
                "payload": {
                    "metadata": {
                        "asset_name": name,
                        "graph_name": "EventGraph",
                        "graph_type": "EventGraph",
                        "uasset_export_index": 1,
                        "uasset_read_status": "complete",
                        "confidence": "high",
                    },
                    "nodes": [
                        {
                            "index": 1,
                            "name": marker,
                            "function": "FixtureCall",
                            "pins": [],
                        }
                    ],
                },
            }
        ],
        "class_defaults": {"variables": {}},
    }


def _publish(
    asset_dir: Path,
    marker: str,
    *,
    publish_v3: bool = True,
) -> dict[str, object]:
    source = asset_dir / "source" / f"{asset_dir.name}.uasset"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(f"fixture:{marker}".encode())
    payload = _payload(asset_dir.name, marker)
    return write_evidence_artifacts_from_payload(
        str(payload["asset_path"]),
        source,
        payload,
        asset_dir,
        publish_v3=publish_v3,
    )


@contextmanager
def _directory_alias(alias: Path, target: Path):
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(target)],
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            raise unittest.SkipTest("Windows junction creation is unavailable")
    else:
        try:
            alias.symlink_to(target, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            raise unittest.SkipTest(f"directory symlink creation is unavailable: {exc}") from exc
    try:
        yield alias
    finally:
        if os.path.lexists(alias):
            os.rmdir(alias) if os.name == "nt" else alias.unlink()


class ReleasePackagingV3AdversarialTests(unittest.TestCase):
    def test_v2_manifest_with_local_diagnostic_path_is_not_packaged(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir = Path(temporary) / "V2LocalPathFixture"
            _publish(asset_dir, "generation-a", publish_v3=False)
            manifest_path = asset_dir / "evidence" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["localDiagnosticPath"] = "\\".join(
                ("C" + ":", "Users", "victim", "capture.log")
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "LOCAL_PATH_DISCLOSURE"):
                resolve_asset_evidence_state(asset_dir)
            with self.assertRaisesRegex(ValueError, "LOCAL_PATH_DISCLOSURE"):
                discover_sample_evidence_files(asset_dir)

    def test_v2_evidence_directory_junction_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            external = root / "external" / "V2JunctionFixture"
            _publish(external, "generation-a", publish_v3=False)
            asset_dir = root / "asset"
            output_dir = asset_dir / "output"
            output_dir.mkdir(parents=True)
            (output_dir / "agent_index.md").write_bytes(
                (external / "output" / "agent_index.md").read_bytes()
            )
            with _directory_alias(
                asset_dir / "evidence",
                external / "evidence",
            ):
                with self.assertRaises((ValueError, OSError)):
                    resolve_asset_evidence_state(asset_dir)
                with self.assertRaises((ValueError, OSError)):
                    discover_sample_evidence_files(asset_dir)

    def test_sample_asset_root_link_is_rejected_before_resolution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir = root / "real" / "LinkedPackageFixture"
            _publish(asset_dir, "generation-a")
            alias = root / "asset-alias"
            with _directory_alias(alias, asset_dir):
                with self.assertRaises((ValueError, OSError)):
                    resolve_asset_evidence_state(alias)
                with self.assertRaises((ValueError, OSError)):
                    discover_sample_evidence_files(alias)

    def test_materialized_sample_cannot_mix_pointer_after_concurrent_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir = root / "ConcurrentPackageFixture"
            first = _publish(asset_dir, "generation-a")
            first_revision = Path(str(first["revision_dir"])).name
            _state, selected = discover_sample_evidence_files(asset_dir)

            second = _publish(asset_dir, "generation-b")
            second_revision = Path(str(second["revision_dir"])).name
            self.assertNotEqual(first_revision, second_revision)

            entries: dict[str, Path | bytes] = {}
            for relative in (
                "START_HERE.bat",
                "DIAGNOSE.bat",
                "runtime/python/python.exe",
                "dist/index.html",
                "scripts/blueprint_tool_server.py",
            ):
                _add_entry(entries, relative, f"fixture:{relative}\n".encode())
            for sample_relative, source in selected:
                _add_entry(
                    entries,
                    (Path("captures") / asset_dir.name / sample_relative).as_posix(),
                    source,
                )
            manifest = build_package_manifest(
                repository_url="https://github.com/example/Blueprint-to-Code.git",
                commit="a" * 40,
                branch="main",
                generated_at_utc="2026-08-03T00:00:00+00:00",
                file_count=len(entries) + 2,
                sample_asset=asset_dir.name,
                sample_revision=first_revision,
            )
            _add_entry(
                entries,
                "PACKAGE_MANIFEST.json",
                (
                    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n"
                ).encode(),
            )
            hashes = {
                name: _sha256_bytes(source)
                if isinstance(source, bytes)
                else _sha256_file(source)
                for name, source in entries.items()
            }
            sums = "".join(
                f"{digest}  {name.removeprefix(f'{ARCHIVE_ROOT}/')}\n"
                for name, digest in sorted(hashes.items())
            ).encode()
            _add_entry(entries, "SHA256SUMS.txt", sums)

            archive_path = root / "concurrent-sample.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for name, source in sorted(entries.items()):
                    if isinstance(source, bytes):
                        archive.writestr(name, source)
                    else:
                        archive.write(source, name)

            # The existing byte-level package verifier accepts this archive;
            # the semantic assertion below proves whether it is one generation.
            _verify_archive(archive_path, hashes, sums)
            capture_prefix = f"{ARCHIVE_ROOT}/captures/{asset_dir.name}/evidence"
            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
                pointer = json.loads(archive.read(f"{capture_prefix}/current.json"))
            packaged_revision = str(pointer["revisionId"])
            self.assertEqual(packaged_revision, first_revision)
            self.assertIn(
                f"{capture_prefix}/revisions/{packaged_revision}/manifest.json",
                names,
            )


if __name__ == "__main__":
    unittest.main()
