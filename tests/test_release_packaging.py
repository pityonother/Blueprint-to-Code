import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class ReleasePackagingTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "bundled Windows Python executable")
    def test_packager_bootstraps_scripts_directory_for_embedded_python(self):
        python = ROOT / "runtime" / "python" / "python.exe"
        script = ROOT / "scripts" / "package_full_env.py"
        probe = """
import importlib.util
import sys
from pathlib import Path

script = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("package_full_env_probe", script)
if spec is None or spec.loader is None:
    raise RuntimeError("unable to load packager")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
import blueprint_translator.evidence_repository
"""

        process = subprocess.run(
            [str(python), "-I", "-c", probe, str(script)],
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        self.assertEqual(process.returncode, 0, process.stderr or process.stdout)

    def test_archive_paths_reject_escape_absolute_and_drive_paths(self):
        from package_full_env import is_safe_archive_path

        self.assertTrue(is_safe_archive_path("BlueprintToCode/scripts/tool.py"))
        for unsafe in (
            "../secret.txt",
            "BlueprintToCode/../../secret.txt",
            "/absolute/file.txt",
            r"C:\Users\someone\secret.txt",
        ):
            with self.subTest(path=unsafe):
                self.assertFalse(is_safe_archive_path(unsafe))

    def test_release_allowlist_excludes_local_generated_and_internal_files(self):
        from package_full_env import should_include_tracked

        included = (
            ".gitignore",
            "README.md",
            "scripts/query_blueprint_evidence.py",
            "runtime/python/python.exe",
            "docs/BLUEPRINT_EVIDENCE_STORE_V2_SPEC_zh.md",
        )
        excluded = (
            "release/old.zip",
            "runtime/downloads/python.zip",
            "analysis/harvest_rankings/report.json",
            "captures/Asset/evidence/evidence.sqlite",
            "devkit_content_root.txt",
            "devkit_path_mappings.txt",
            "docs/GPT_PRO_PROJECT_REPORT_zh.md",
            "docs/SESSION_HANDOFF_zh.md",
            "docs/NEXT_CHAT_HANDOFF_zh.md",
        )
        self.assertTrue(all(should_include_tracked(path) for path in included))
        self.assertTrue(all(not should_include_tracked(path) for path in excluded))

    def test_dotfile_name_is_preserved_when_packaged(self):
        from package_full_env import _normalized_relative

        self.assertEqual(_normalized_relative(".gitignore"), ".gitignore")
        self.assertEqual(_normalized_relative("./README.md"), "README.md")

    def test_windows_npm_command_is_resolved_explicitly(self):
        from package_full_env import resolve_npm_executable

        with patch("package_full_env.shutil.which", side_effect=lambda name: "C:/node/npm.cmd" if name == "npm.cmd" else None):
            self.assertEqual(resolve_npm_executable(), "C:/node/npm.cmd")

    def test_repository_url_strips_http_userinfo(self):
        from package_full_env import sanitize_repository_url

        self.assertEqual(
            sanitize_repository_url("https://secret-token@github.com/example/project.git"),
            "https://github.com/example/project.git",
        )
        self.assertEqual(
            sanitize_repository_url("git@github.com:example/project.git"),
            "git@github.com:example/project.git",
        )

    def test_partner_content_root_can_be_written_as_explicit_package_config(self):
        from package_full_env import build_devkit_content_root_config

        self.assertEqual(
            build_devkit_content_root_config(
                r"E:\AKD\ARKDevkit\Projects\ShooterGame\Content"
            ),
            b"E:\\AKD\\ARKDevkit\\Projects\\ShooterGame\\Content\n",
        )
        for invalid in (
            r"E:\AKD\ARKDevkit",
            r"E:\AKD\..\Projects\ShooterGame\Content",
            "relative/Projects/ShooterGame/Content",
            "E:\\AKD\\ARKDevkit\\Projects\\ShooterGame\\Content\nmalicious",
        ):
            with self.subTest(path=invalid), self.assertRaises(ValueError):
                build_devkit_content_root_config(invalid)

    def test_verified_archive_embeds_partner_content_root_and_checksum(self):
        import json
        import zipfile

        from package_full_env import (
            ARCHIVE_ROOT,
            _add_entry,
            _sha256_bytes,
            _verify_archive,
            build_devkit_content_root_config,
            build_package_manifest,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            entries: dict[str, Path | bytes] = {}
            for relative in (
                "START_HERE.bat",
                "DIAGNOSE.bat",
                "runtime/python/python.exe",
                "dist/index.html",
                "scripts/blueprint_tool_server.py",
            ):
                _add_entry(entries, relative, f"fixture:{relative}\n".encode("utf-8"))
            config = build_devkit_content_root_config(
                r"E:\AKD\ARKDevkit\Projects\ShooterGame\Content"
            )
            _add_entry(entries, "devkit_content_root.txt", config)
            manifest = build_package_manifest(
                repository_url="https://github.com/example/Blueprint-to-Code.git",
                commit="a" * 40,
                branch="codex/fix-partner-devkit-root",
                generated_at_utc="2026-07-21T00:00:00+00:00",
                file_count=len(entries) + 2,
                sample_asset="Fixture",
                devkit_content_root_configured=True,
            )
            manifest_bytes = (
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            _add_entry(entries, "PACKAGE_MANIFEST.json", manifest_bytes)

            hashes = {
                name: _sha256_bytes(source)
                for name, source in entries.items()
                if isinstance(source, bytes)
            }
            sums = "".join(
                f"{digest}  {name.removeprefix(f'{ARCHIVE_ROOT}/')}\n"
                for name, digest in sorted(hashes.items())
            ).encode("utf-8")
            _add_entry(entries, "SHA256SUMS.txt", sums)
            archive_path = Path(temp_dir) / "partner.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for name, source in sorted(entries.items()):
                    self.assertIsInstance(source, bytes)
                    archive.writestr(name, source)

            _verify_archive(archive_path, hashes, sums)
            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()
                packaged_manifest = json.loads(
                    archive.read(f"{ARCHIVE_ROOT}/PACKAGE_MANIFEST.json")
                )
                packaged_sums = archive.read(f"{ARCHIVE_ROOT}/SHA256SUMS.txt")
                packaged_config = archive.read(
                    f"{ARCHIVE_ROOT}/devkit_content_root.txt"
                )

        self.assertEqual(
            packaged_config,
            b"E:\\AKD\\ARKDevkit\\Projects\\ShooterGame\\Content\n",
        )
        self.assertIn(f"{ARCHIVE_ROOT}/devkit_content_root.txt", names)
        self.assertTrue(packaged_manifest["devkitContentRootConfigured"])
        self.assertEqual(packaged_manifest["fileCount"], len(names))
        self.assertIn(b"devkit_content_root.txt", packaged_sums)

    def test_harvest_report_discovery_requires_complete_nonempty_triplets(self):
        from package_full_env import discover_harvest_reports

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "harvest_rankings"
            root.mkdir()
            for suffix in (".ai.json", ".full.json", ".md"):
                (root / f"harvest_ranking_metal{suffix}").write_text("{}", encoding="utf-8")
            reports = discover_harvest_reports(root)
            self.assertEqual([item[0] for item in reports], ["harvest_ranking_metal"])

            (root / "harvest_ranking_metal.md").unlink()
            with self.assertRaises(FileNotFoundError):
                discover_harvest_reports(root)

    def test_package_manifest_records_commit_without_local_source_path(self):
        from package_full_env import build_package_manifest

        manifest = build_package_manifest(
            repository_url="https://github.com/example/Blueprint-to-Code.git",
            commit="a" * 40,
            branch="main",
            generated_at_utc="2026-07-20T00:00:00+00:00",
            file_count=123,
            sample_asset="Buff_StriderHackingParent",
        )

        self.assertFalse(manifest["dirty"])
        self.assertEqual(manifest["commit"], "a" * 40)
        self.assertNotIn("source", manifest)
        self.assertNotIn("C:\\Users", str(manifest))

    def test_v3_sample_selection_packages_only_current_and_pointed_revision(self):
        from package_full_env import discover_sample_evidence_files

        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "PointedFixture"
            evidence_dir = asset_dir / "evidence"
            revision_dir = evidence_dir / "revisions" / "revision-current"
            orphan_dir = evidence_dir / "revisions" / "revision-orphan"
            staging_dir = evidence_dir / ".staging-unpublished"
            revision_dir.mkdir(parents=True)
            orphan_dir.mkdir(parents=True)
            staging_dir.mkdir()

            pointer = evidence_dir / "current.json"
            database = revision_dir / "evidence.sqlite"
            manifest = revision_dir / "manifest.json"
            agent_index = revision_dir / "agent_index.md"
            database_raw = b"current-database"
            index_raw = b"# current index\n"
            manifest_raw = (
                json.dumps(
                    {
                        "revisionId": "revision-current",
                        "artifacts": {
                            "agentIndex": {
                                "bytes": len(index_raw),
                                "sha256": hashlib.sha256(index_raw).hexdigest(),
                            }
                        },
                    },
                    sort_keys=True,
                )
                + "\n"
            ).encode()
            manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
            pointer_raw = (
                json.dumps(
                    {
                        "revisionId": "revision-current",
                        "manifestSha256": manifest_sha,
                    },
                    sort_keys=True,
                )
                + "\n"
            ).encode()
            for path, content in (
                (pointer, pointer_raw),
                (database, database_raw),
                (manifest, manifest_raw),
                (agent_index, index_raw),
                (orphan_dir / "evidence.sqlite", b"orphan"),
                (staging_dir / "evidence.sqlite", b"staging"),
                (revision_dir / "evidence.sqlite-wal", b"wal"),
                (revision_dir / "evidence.sqlite-shm", b"shm"),
            ):
                path.write_bytes(content)
            (evidence_dir / "evidence.sqlite").write_bytes(b"v2 compatibility")
            (evidence_dir / "manifest.json").write_text("{}\n", encoding="utf-8")
            (asset_dir / "output").mkdir()
            (asset_dir / "output" / "agent_index.md").write_text(
                "# v2 compatibility\n", encoding="utf-8"
            )
            state = SimpleNamespace(
                source_kind="INDEXED_V3_CURRENT",
                pointer_path=pointer,
                database_path=database,
                manifest_path=manifest,
                agent_index_path=agent_index,
                database_bytes=len(database_raw),
                database_sha256=hashlib.sha256(database_raw).hexdigest(),
                manifest_sha256=manifest_sha,
                pointer_sha256=hashlib.sha256(pointer_raw).hexdigest(),
            )

            with patch(
                "blueprint_translator.evidence_repository.resolve_asset_evidence_state",
                return_value=state,
            ):
                resolved, selected = discover_sample_evidence_files(asset_dir)

            self.assertIs(resolved, state)
            self.assertEqual(
                [relative.as_posix() for relative, _source in selected],
                [
                    "evidence/current.json",
                    "evidence/revisions/revision-current/evidence.sqlite",
                    "evidence/revisions/revision-current/manifest.json",
                    "evidence/revisions/revision-current/agent_index.md",
                ],
            )
            selected_payloads = {source for _relative, source in selected}
            self.assertNotIn(b"orphan", selected_payloads)
            self.assertNotIn(b"staging", selected_payloads)
            self.assertNotIn(b"wal", selected_payloads)
            self.assertNotIn(b"shm", selected_payloads)
            self.assertNotIn(b"v2 compatibility", selected_payloads)
            self.assertTrue(
                all(str(asset_dir.resolve()) not in relative.as_posix() for relative, _ in selected)
            )

    def test_v3_sample_selection_supports_pruned_v2_compatibility_files(self):
        from package_full_env import discover_sample_evidence_files

        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "PrunedFixture"
            revision_dir = asset_dir / "evidence" / "revisions" / "revision-pruned"
            revision_dir.mkdir(parents=True)
            pointer = asset_dir / "evidence" / "current.json"
            database = revision_dir / "evidence.sqlite"
            manifest = revision_dir / "manifest.json"
            agent_index = revision_dir / "agent_index.md"
            database_raw = b"database"
            index_raw = b"# index\n"
            manifest_raw = (
                json.dumps(
                    {
                        "revisionId": "revision-pruned",
                        "artifacts": {
                            "agentIndex": {
                                "bytes": len(index_raw),
                                "sha256": hashlib.sha256(index_raw).hexdigest(),
                            }
                        },
                    },
                    sort_keys=True,
                )
                + "\n"
            ).encode()
            manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
            pointer_raw = (
                json.dumps(
                    {
                        "revisionId": "revision-pruned",
                        "manifestSha256": manifest_sha,
                    },
                    sort_keys=True,
                )
                + "\n"
            ).encode()
            pointer.write_bytes(pointer_raw)
            database.write_bytes(database_raw)
            manifest.write_bytes(manifest_raw)
            agent_index.write_bytes(index_raw)
            state = SimpleNamespace(
                source_kind="INDEXED_V3_CURRENT",
                pointer_path=pointer,
                database_path=database,
                manifest_path=manifest,
                agent_index_path=agent_index,
                database_bytes=len(database_raw),
                database_sha256=hashlib.sha256(database_raw).hexdigest(),
                manifest_sha256=manifest_sha,
                pointer_sha256=hashlib.sha256(pointer_raw).hexdigest(),
            )

            with patch(
                "blueprint_translator.evidence_repository.resolve_asset_evidence_state",
                return_value=state,
            ):
                _resolved, selected = discover_sample_evidence_files(asset_dir)

            self.assertEqual(len(selected), 4)
            self.assertFalse((asset_dir / "evidence" / "evidence.sqlite").exists())
            self.assertFalse((asset_dir / "evidence" / "manifest.json").exists())
            self.assertFalse((asset_dir / "output" / "agent_index.md").exists())

    def test_tampered_v3_current_fails_closed_even_when_v2_compatibility_exists(self):
        from package_full_env import discover_sample_evidence_files

        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "TamperedFixture"
            evidence_dir = asset_dir / "evidence"
            output_dir = asset_dir / "output"
            evidence_dir.mkdir(parents=True)
            output_dir.mkdir()
            (evidence_dir / "current.json").write_text("{tampered", encoding="utf-8")
            (evidence_dir / "evidence.sqlite").write_bytes(b"v2 compatibility")
            (evidence_dir / "manifest.json").write_text("{}\n", encoding="utf-8")
            (output_dir / "agent_index.md").write_text("# v2\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "JSON_INVALID"):
                discover_sample_evidence_files(asset_dir)

    def test_v2_sample_selection_keeps_legacy_package_layout(self):
        from package_full_env import discover_sample_evidence_files

        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "V2Fixture"
            evidence_dir = asset_dir / "evidence"
            output_dir = asset_dir / "output"
            evidence_dir.mkdir(parents=True)
            output_dir.mkdir()
            database = evidence_dir / "evidence.sqlite"
            manifest = evidence_dir / "manifest.json"
            agent_index = output_dir / "agent_index.md"
            database.write_bytes(b"v2 database")
            manifest.write_text("{}\n", encoding="utf-8")
            agent_index.write_text("# v2 index\n", encoding="utf-8")
            state = SimpleNamespace(
                source_kind="INDEXED_V2_COMPATIBILITY",
                pointer_path=None,
                database_path=database,
                manifest_path=manifest,
                agent_index_path=agent_index,
                database_bytes=database.stat().st_size,
                database_sha256=hashlib.sha256(database.read_bytes()).hexdigest(),
            )

            with patch(
                "blueprint_translator.evidence_repository.resolve_asset_evidence_state",
                return_value=state,
            ):
                _resolved, selected = discover_sample_evidence_files(asset_dir)

            self.assertEqual(
                [relative.as_posix() for relative, _source in selected],
                [
                    "evidence/evidence.sqlite",
                    "evidence/manifest.json",
                    "output/agent_index.md",
                ],
            )


if __name__ == "__main__":
    unittest.main()
