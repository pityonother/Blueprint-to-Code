from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import package_full_env  # noqa: E402
import release_content_policy  # noqa: E402
from release_content_policy import (  # noqa: E402
    ReleaseContentAllowRule,
    ReleaseContentEntry,
    ReleaseContentPolicyError,
    collect_git_ref_entries,
    scan_release_entries,
    validate_allow_rules,
)


class ReleaseContentPolicyTests(unittest.TestCase):
    @staticmethod
    def _windows_user_path(*parts: str, slash: str = "/") -> str:
        return slash.join(("C:", "Users", "ac", *parts))

    @staticmethod
    def _synthetic_windows_path(user: str, *parts: str) -> str:
        return "\\".join(("C:", "Users", user, *parts))

    @staticmethod
    def _scan(
        content: str | bytes,
        *,
        relative_path: str = "src/example.cpp",
        allow_rules: tuple[ReleaseContentAllowRule, ...] = (),
    ):
        encoded = content.encode("utf-8") if isinstance(content, str) else content
        return scan_release_entries(
            (ReleaseContentEntry(relative_path, encoded),),
            repository_root=ROOT,
            allow_rules=allow_rules,
        )

    def test_production_cpp_has_no_specific_user_or_repository_path(self):
        relative = (
            "devkit_plugins/BlueprintToCodeExporter/Source/"
            "BlueprintToCodeExporter/Private/BlueprintToCodeExporterModule.cpp"
        )
        content = (ROOT / relative).read_bytes()

        report = scan_release_entries(
            (ReleaseContentEntry(relative, content),),
            repository_root=ROOT,
        )

        self.assertEqual(report.findings, ())

    def test_original_blocking_repository_path_is_detected(self):
        path = self._windows_user_path(
            "Documents", "project gaming", "Blueprint to Code"
        )

        report = self._scan(path)

        self.assertTrue(report.findings)
        self.assertIn(
            "current-repository-root",
            {finding.category for finding in report.findings},
        )

    def test_windows_user_paths_with_both_separator_styles_are_detected(self):
        for slash in ("/", "\\"):
            with self.subTest(slash=slash):
                report = self._scan(
                    self._windows_user_path("private", "secret.txt", slash=slash)
                )
                self.assertIn(
                    "windows-user-directory",
                    {finding.category for finding in report.findings},
                )

    def test_macos_and_linux_user_paths_are_detected(self):
        cases = (
            ("/" + "/".join(("Users", "alice", "private.txt")), "macos-user-directory"),
            ("/" + "/".join(("home", "alice", "private.txt")), "linux-user-directory"),
            ("/" + "/".join(("root", "private.txt")), "linux-root-directory"),
        )
        for path, category in cases:
            with self.subTest(category=category):
                report = self._scan(path)
                self.assertIn(
                    category,
                    {finding.category for finding in report.findings},
                )

    def test_file_uri_and_url_encoded_local_paths_are_detected(self):
        file_uri = "file:" + "///" + self._windows_user_path("private.txt")
        encoded = "%43%3A%2F%55sers%2F" + "%61c%2Fprivate.txt"

        uri_report = self._scan(file_uri)
        encoded_report = self._scan(encoded)

        self.assertIn(
            "local-file-uri",
            {finding.category for finding in uri_report.findings},
        )
        self.assertIn(
            "windows-user-directory",
            {finding.category for finding in encoded_report.findings},
        )

    def test_leaking_tracked_filename_is_detected(self):
        relative = (
            "fixtures/"
            + self._windows_user_path("private.txt").replace("\\", "/")
        )

        report = self._scan(b"safe content\n", relative_path=relative)

        self.assertTrue(any(finding.line == 0 for finding in report.findings))

    def test_binary_files_are_skipped_without_skipping_their_names(self):
        report = self._scan(b"\x00\xff\x10binary", relative_path="assets/blob.bin")

        self.assertEqual(report.scanned_files, 1)
        self.assertEqual(report.scanned_text_files, 0)
        self.assertEqual(report.skipped_binary_files, 1)
        self.assertEqual(report.findings, ())

    def test_utf8_bom_text_is_scanned(self):
        content = b"\xef\xbb\xbf" + self._windows_user_path(
            "private.txt"
        ).encode("utf-8")

        report = self._scan(content)

        self.assertTrue(report.findings)

    def test_non_utf8_text_is_scanned_instead_of_disabling_policy(self):
        content = b"\xffprefix " + self._windows_user_path(
            "private.txt"
        ).encode("ascii")

        report = self._scan(content)

        self.assertEqual(report.scanned_text_files, 1)
        self.assertTrue(report.findings)

    def test_generic_program_files_install_template_is_not_flagged(self):
        template = (
            "C:\\Program Files\\Epic Games\\ARKDevkit\\Projects\\"
            "ShooterGame\\Content"
        )

        report = self._scan(template, relative_path="config.example.txt")

        self.assertEqual(report.findings, ())

    def test_synthetic_test_fixture_requires_an_exact_explained_rule(self):
        relative = "tests/test_fixture.py"
        fixture = self._synthetic_windows_path("someone", "secret.txt")
        rule = ReleaseContentAllowRule(
            relative_path=relative,
            category="windows-user-directory",
            exact_match=fixture,
            reason="Synthetic path used to prove unsafe archive-path rejection.",
        )

        report = self._scan(
            fixture,
            relative_path=relative,
            allow_rules=(rule,),
        )

        self.assertEqual(report.findings, ())

    def test_actual_username_cannot_be_allowlisted(self):
        private_path = self._windows_user_path("private.txt")
        rule = ReleaseContentAllowRule(
            relative_path="tests/test_fixture.py",
            category="windows-user-directory",
            exact_match=private_path,
            reason="This must be rejected.",
        )

        with self.assertRaises(ValueError):
            validate_allow_rules((rule,), repository_root=ROOT)

    def test_allowlist_cannot_skip_a_production_directory_or_use_wildcards(self):
        for relative_path in ("scripts/*", "src/.*", "devkit_plugins/"):
            with self.subTest(relative_path=relative_path):
                rule = ReleaseContentAllowRule(
                    relative_path=relative_path,
                    category="windows-user-directory",
                    exact_match=self._synthetic_windows_path(
                        "someone",
                        "fixture.txt",
                    ),
                    reason="Overbroad rule under test.",
                )
                with self.assertRaises(ValueError):
                    validate_allow_rules((rule,), repository_root=ROOT)

    def test_allowlist_path_and_match_are_both_exact(self):
        fixture = self._synthetic_windows_path("someone", "secret.txt")
        rule = ReleaseContentAllowRule(
            relative_path="tests/allowed.py",
            category="windows-user-directory",
            exact_match=fixture,
            reason="One exact synthetic security fixture.",
        )

        wrong_file = self._scan(
            fixture,
            relative_path="tests/not_allowed.py",
            allow_rules=(rule,),
        )
        longer_match = self._scan(
            fixture.replace("secret.txt", "other.txt"),
            relative_path="tests/allowed.py",
            allow_rules=(rule,),
        )

        self.assertTrue(wrong_file.findings)
        self.assertTrue(longer_match.findings)

    def test_package_writer_refuses_leak_before_creating_archive(self):
        private_path = self._windows_user_path("private.txt")
        entries = {
            "BlueprintToCode/scripts/leak.txt": private_path.encode("utf-8"),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "blocked.zip"

            with self.assertRaises(ReleaseContentPolicyError):
                package_full_env._write_archive(
                    ROOT,
                    archive_path,
                    entries,
                )

            self.assertFalse(archive_path.exists())

    def test_package_writer_uses_the_shared_release_policy(self):
        self.assertIs(
            package_full_env.require_release_entries_safe,
            release_content_policy.require_release_entries_safe,
        )

    def test_git_ref_inventory_matches_the_complete_tracked_tree(self):
        entries = collect_git_ref_entries(ROOT, "HEAD")
        expected = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
        ).stdout.splitlines()

        self.assertEqual(
            {entry.relative_path for entry in entries},
            set(expected),
        )
        self.assertEqual(len(entries), len(expected))


if __name__ == "__main__":
    unittest.main()
