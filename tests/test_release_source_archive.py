from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from release_content_policy import (  # noqa: E402
    ReleaseArchiveEntry,
    ReleaseContentAllowRule,
    collect_git_archive_entries,
    collect_tracked_worktree_entries,
    scan_release_entries,
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _png(
    *extra_chunks: tuple[bytes, bytes],
    idat_payload: bytes | None = None,
) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = (
        zlib.compress(b"\x00\x00\x00\x00\x00")
        if idat_payload is None
        else idat_payload
    )
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", header),
            *(chunk(kind, payload) for kind, payload in extra_chunks),
            chunk(b"IDAT", pixels),
            chunk(b"IEND", b""),
        )
    )


class ReleaseSourceArchiveTests(unittest.TestCase):
    def _repository(self, root: Path, files: dict[str, bytes | str]) -> Path:
        repo = root / "repo"
        repo.mkdir()
        _git(repo, "init", "--initial-branch=main")
        _git(repo, "config", "user.email", "release-fixture@example.invalid")
        _git(repo, "config", "user.name", "Release Fixture")
        for relative, content in files.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        _git(repo, "add", "--all")
        _git(repo, "commit", "-m", "fixture")
        return repo

    def test_source_archive_policy_preserves_sources_docs_tests_and_media(self) -> None:
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        required = (
            "/runtime/** export-ignore",
            "/captures/** export-ignore",
            "/analysis/** export-ignore",
            "/knowledge_base/** export-ignore",
            "/dist/** export-ignore",
            "/release/** export-ignore",
            "*.zip export-ignore",
            "*.sqlite export-ignore",
            "*.dll export-ignore",
            "*.pdb export-ignore",
        )
        for rule in required:
            with self.subTest(rule=rule):
                self.assertIn(rule, attributes)
        for protected in ("/scripts/**", "/src/**", "/docs/**", "/tests/**"):
            with self.subTest(protected=protected):
                self.assertNotIn(f"{protected} export-ignore", attributes)

    def test_git_archive_respects_source_only_export_ignore_inventory(self) -> None:
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            repo = self._repository(
                Path(temporary),
                {
                    ".gitattributes": attributes,
                    "README.md": "source release\n",
                    "scripts/tool.py": "print('safe')\n",
                    "src/main.ts": "export const safe = true\n",
                    "docs/guide.md": "guide\n",
                    "tests/test_tool.py": "def test_safe(): pass\n",
                    "public/assets/bg/sky.png": _png(),
                    "scripts/blueprint_translator/harvest/build/catalog_builder.py": (
                        "SOURCE_PACKAGE = True\n"
                    ),
                    "runtime/python/python.exe": b"MZ\x00runtime",
                    "captures/Asset/evidence.sqlite": b"SQLite format 3\x00",
                    "analysis/generated/report.json": "{}\n",
                    "knowledge_base/discovery_bundle.zip": b"PK\x03\x04\x00",
                    "docs/generated.zip": b"PK\x03\x04\x00",
                    "dist/index.js": "generated\n",
                    "release/full-env.zip": b"PK\x03\x04\x00",
                    "native/fixture.dll": b"MZ\x00native",
                    "native/fixture.pdb": b"Microsoft C/C++\x00",
                },
            )

            entries, commit = collect_git_archive_entries(repo, "HEAD")
            paths = {entry.relative_path for entry in entries}
            report = scan_release_entries(entries, repository_root=repo)

        self.assertRegex(commit, r"^[0-9a-f]{40}$")
        for required_path in (
            "README.md",
            "scripts/tool.py",
            "src/main.ts",
            "docs/guide.md",
            "tests/test_tool.py",
            "public/assets/bg/sky.png",
            "scripts/blueprint_translator/harvest/build/catalog_builder.py",
        ):
            with self.subTest(required_path=required_path):
                self.assertIn(required_path, paths)
        for forbidden_path in (
            "runtime/python/python.exe",
            "captures/Asset/evidence.sqlite",
            "analysis/generated/report.json",
            "knowledge_base/discovery_bundle.zip",
            "docs/generated.zip",
            "dist/index.js",
            "release/full-env.zip",
            "native/fixture.dll",
            "native/fixture.pdb",
        ):
            with self.subTest(forbidden_path=forbidden_path):
                self.assertNotIn(forbidden_path, paths)
        self.assertEqual(report.findings, ())

    def test_scan_rejects_generated_database_archive_native_and_link_entries(self) -> None:
        entries = (
            ReleaseArchiveEntry("runtime/python/python.exe", b"MZ", "file"),
            ReleaseArchiveEntry("captures/Asset/report.md", b"capture", "file"),
            ReleaseArchiveEntry("analysis/result.json", b"{}", "file"),
            ReleaseArchiveEntry("cache/evidence.sqlite", b"SQLite", "file"),
            ReleaseArchiveEntry("bundle/package.zip", b"PK", "file"),
            ReleaseArchiveEntry("native/plugin.dll", b"MZ", "file"),
            ReleaseArchiveEntry("docs/alias", b"", "symlink"),
        )

        report = scan_release_entries(entries, repository_root=ROOT)

        self.assertEqual(
            {finding.category for finding in report.findings},
            {
                "archive-artifact",
                "capture-artifact",
                "database-artifact",
                "generated-artifact",
                "link-entry",
                "native-binary",
                "runtime-artifact",
            },
        )

    def test_scan_rejects_local_paths_secret_signatures_and_binary_content(self) -> None:
        windows_path = "C:" + "/" + "/".join(("Users", "private-user", "work", "file.txt"))
        github_token = "gh" + "p_" + ("A" * 36)
        aws_signature = "AK" + "IA" + ("B" * 16)
        key_header = "-----BEGIN " + "PRIVATE KEY-----"
        assignment_fixture = "SERVICE_PASSWORD" + "=live-value-123456"
        lowercase_assignment = "password" + "=live-value-123456"
        deceptive_placeholder = "SERVICE_PASSWORD" + "=testActualCredentialLong123"
        punctuation_assignments = (
            "password" + "=live!value123456",
            "password" + "=live$Value123456",
            "password" + "=live,value123456",
        )
        entries = (
            ReleaseArchiveEntry("docs/path.md", windows_path.encode(), "file"),
            ReleaseArchiveEntry("docs/token.md", github_token.encode(), "file"),
            ReleaseArchiveEntry("docs/aws.md", aws_signature.encode(), "file"),
            ReleaseArchiveEntry("docs/key.md", key_header.encode(), "file"),
            ReleaseArchiveEntry(
                "docs/config.env",
                assignment_fixture.encode(),
                "file",
            ),
            ReleaseArchiveEntry(
                "docs/lowercase.env",
                lowercase_assignment.encode(),
                "file",
            ),
            ReleaseArchiveEntry(
                "docs/deceptive.env",
                deceptive_placeholder.encode(),
                "file",
            ),
            *(
                ReleaseArchiveEntry(
                    f"docs/punctuation-{index}.env",
                    assignment.encode(),
                    "file",
                )
                for index, assignment in enumerate(punctuation_assignments)
            ),
            ReleaseArchiveEntry("scripts/blob.py", b"source\x00binary", "file"),
            ReleaseArchiveEntry(
                "public/assets/bg/source.png",
                _png(),
                "file",
            ),
        )

        report = scan_release_entries(entries, repository_root=ROOT)

        categories = [finding.category for finding in report.findings]
        self.assertEqual(categories.count("absolute-path"), 1)
        self.assertEqual(categories.count("secret-signature"), 3)
        self.assertEqual(categories.count("hard-coded-secret"), 6)
        self.assertEqual(categories.count("binary-content"), 1)
        self.assertFalse(any(finding.relative_path.endswith("source.png") for finding in report.findings))
        self.assertTrue(all(finding.redacted_match for finding in report.findings))

    def test_scan_rejects_encoded_or_binary_media_secrets_and_redacts_secret_names(self) -> None:
        token = "gh" + "p_" + ("Q" * 36)
        encoded_token = "%67%68%70_" + ("R" * 36)
        binary_assignment = "password" + "=live-value-123456"
        entries = (
            ReleaseArchiveEntry(
                "public/assets/bg/metadata.png",
                _png((b"tEXt", b"comment\x00" + token.encode())),
            ),
            ReleaseArchiveEntry(
                "public/assets/bg/assignment.png",
                _png((b"tEXt", b"comment\x00" + binary_assignment.encode())),
            ),
            ReleaseArchiveEntry("docs/encoded.txt", encoded_token.encode()),
            ReleaseArchiveEntry("docs/" + token + ".txt", b"safe"),
            ReleaseArchiveEntry(
                "docs/" + binary_assignment + ".txt",
                b"safe",
            ),
        )

        report = scan_release_entries(entries, repository_root=ROOT)

        categories = [finding.category for finding in report.findings]
        self.assertEqual(categories.count("hard-coded-secret"), 2)
        self.assertEqual(categories.count("secret-signature"), 3)
        self.assertTrue(
            any(finding.relative_path == "<redacted-relative-path>" for finding in report.findings)
        )
        self.assertNotIn(token, repr(report))
        self.assertNotIn(binary_assignment, repr(report))

    def test_nested_generated_directories_and_temporary_roots_are_rejected(self) -> None:
        entries = tuple(
            ReleaseArchiveEntry(path, b"generated")
            for path in (
                ".tmp/cache.txt",
                ".tmp_build/cache.txt",
                "tests/native_fixture/build/result.txt",
                "packages/ui/node_modules/pkg/index.js",
                "packages/ui/dist/index.js",
                "packages/ui/coverage/out.json",
                "packages/ui/output/bundle.js",
                "packages/app/release/notes.txt",
                "docs/captures/raw.json",
                "pkg/runtime/manifest.json",
                "pkg/analysis/result.json",
                "pkg/knowledge_base/current.json",
                "scripts/blueprint_translator/harvest/build/node_modules/pkg/index.js",
            )
        )

        report = scan_release_entries(entries, repository_root=ROOT)

        self.assertEqual(len(report.findings), len(entries))
        self.assertEqual(
            {finding.category for finding in report.findings},
            {"generated-artifact"},
        )

    def test_binary_media_allowance_requires_a_matching_magic_signature(self) -> None:
        entries = (
            ReleaseArchiveEntry("public/assets/bg/valid.png", _png()),
            ReleaseArchiveEntry("public/assets/bg/renamed.png", b"MZ\x00executable"),
            ReleaseArchiveEntry("public/assets/bg/text.png", b"not-an-image"),
            ReleaseArchiveEntry(
                "public/assets/bg/polyglot.png",
                b"\x89PNG\r\n\x1a\nMZ\x00executable",
            ),
            ReleaseArchiveEntry(
                "public/assets/bg/appended.png",
                _png() + b"MZ\x00executable",
            ),
            ReleaseArchiveEntry(
                "public/assets/bg/invalid-idat.png",
                _png(idat_payload=b"MZ-not-zlib-PE\x00\x00"),
            ),
        )

        report = scan_release_entries(entries, repository_root=ROOT)

        self.assertEqual(
            [(finding.relative_path, finding.category) for finding in report.findings],
            [
                ("public/assets/bg/appended.png", "binary-content"),
                ("public/assets/bg/invalid-idat.png", "binary-content"),
                ("public/assets/bg/polyglot.png", "binary-content"),
                ("public/assets/bg/renamed.png", "binary-content"),
                ("public/assets/bg/text.png", "binary-content"),
            ],
        )

    def test_png_compressed_text_metadata_is_scanned(self) -> None:
        token = "gh" + "p_" + ("M" * 36)
        compressed = zlib.compress(token.encode())
        png = _png((b"zTXt", b"Comment\x00\x00" + compressed))

        report = scan_release_entries(
            (ReleaseArchiveEntry("public/assets/bg/compressed.png", png),),
            repository_root=ROOT,
        )

        self.assertEqual(
            [(finding.relative_path, finding.category) for finding in report.findings],
            [("public/assets/bg/compressed.png", "secret-signature")],
        )
        self.assertNotIn(token, repr(report))

    def test_utf32_secret_and_common_credentials_are_rejected(self) -> None:
        github_token = "gh" + "p_" + ("U" * 36)
        slack_token = "xo" + "xb-" + ("1" * 24) + "-" + ("a" * 24)
        stripe_key = "sk" + "_live_" + ("s" * 24)
        basic_header = "Authorization" + ": Basic " + ("Y" * 28) + "="
        bearer_header = "Authorization" + ": Bearer eyJ" + ("Z" * 24)
        basic_url = "https:" + "//alice:live-password-123@example.invalid/api"
        database_url = "postgresql:" + "//alice:password123@db.invalid/app"
        entries = (
            ReleaseArchiveEntry("docs/utf32.txt", github_token.encode("utf-32")),
            ReleaseArchiveEntry("docs/slack.txt", slack_token.encode()),
            ReleaseArchiveEntry("docs/stripe.txt", stripe_key.encode()),
            ReleaseArchiveEntry("docs/basic-header.txt", basic_header.encode()),
            ReleaseArchiveEntry("docs/bearer-header.txt", bearer_header.encode()),
            ReleaseArchiveEntry("docs/basic-url.txt", basic_url.encode()),
            ReleaseArchiveEntry("docs/database-url.txt", database_url.encode()),
        )

        report = scan_release_entries(entries, repository_root=ROOT)

        self.assertEqual(
            [finding.category for finding in report.findings],
            ["secret-signature"] * len(entries),
        )

    def test_json_yaml_cli_query_and_hash_secret_assignments_are_rejected(self) -> None:
        yaml_secret = "password" + ": RealSecret123!"
        json_secret = '{"' + "client_secret" + '":"RealSecret123!"}'
        hash_secret = "PASSWORD" + "=$2b$12$abcdefghijklmnopqrstuvwxyz0123456789"
        query_secret = (
            "https://example.invalid/?"
            + "access_"
            + "token="
            + "RealSecret123!"
        )
        cli_secret = "tool --" + "password RealSecret123!"
        yaml_spaces = "password" + ": correct horse battery staple"
        angle_secret = "PASSWORD" + "=<live-password-123>"
        prefix_secret = "PASSWORD" + "=test-real-production-password-123"
        weak_value = "password" + "=abc123"
        weak_json = '{"' + "password" + '":"s3cret"}'
        weak_cli = "tool --" + "password letmein"
        auth_token = "AUTH_" + "TOKEN=opaque123"
        refresh_json = '{"' + "refresh_token" + '":"opaque123"}'
        token_yaml = "token" + ": opaque123"
        secret_yaml = "secret" + ": opaque123"
        variable_examples = ("PASSWORD=$SAFE_VALUE", "PASSWORD=${SAFE_VALUE}")
        entries = (
            ReleaseArchiveEntry("docs/yaml.txt", yaml_secret.encode()),
            ReleaseArchiveEntry("docs/json.txt", json_secret.encode()),
            ReleaseArchiveEntry("docs/hash.txt", hash_secret.encode()),
            ReleaseArchiveEntry("docs/query.txt", query_secret.encode()),
            ReleaseArchiveEntry("docs/cli.txt", cli_secret.encode()),
            ReleaseArchiveEntry("docs/yaml-spaces.txt", yaml_spaces.encode()),
            ReleaseArchiveEntry("docs/angle.txt", angle_secret.encode()),
            ReleaseArchiveEntry("docs/prefix.txt", prefix_secret.encode()),
            ReleaseArchiveEntry("docs/weak-password.txt", weak_value.encode()),
            ReleaseArchiveEntry("docs/weak-json.txt", weak_json.encode()),
            ReleaseArchiveEntry("docs/weak-cli.txt", weak_cli.encode()),
            ReleaseArchiveEntry("docs/auth-token.txt", auth_token.encode()),
            ReleaseArchiveEntry("docs/refresh-token.txt", refresh_json.encode()),
            ReleaseArchiveEntry("docs/token-yaml.txt", token_yaml.encode()),
            ReleaseArchiveEntry("docs/secret-yaml.txt", secret_yaml.encode()),
            ReleaseArchiveEntry(
                "docs/variables.txt",
                ("\n".join(variable_examples) + "\n").encode(),
            ),
        )

        report = scan_release_entries(entries, repository_root=ROOT)

        self.assertEqual(
            [finding.relative_path for finding in report.findings],
            [
                "docs/angle.txt",
                "docs/auth-token.txt",
                "docs/cli.txt",
                "docs/hash.txt",
                "docs/json.txt",
                "docs/prefix.txt",
                "docs/query.txt",
                "docs/refresh-token.txt",
                "docs/secret-yaml.txt",
                "docs/token-yaml.txt",
                "docs/weak-cli.txt",
                "docs/weak-json.txt",
                "docs/weak-password.txt",
                "docs/yaml-spaces.txt",
                "docs/yaml.txt",
            ],
        )
        self.assertTrue(
            all(finding.category == "hard-coded-secret" for finding in report.findings)
        )

    def test_certificates_private_env_and_generated_report_names_are_rejected(self) -> None:
        certificate = "-----BEGIN " + "CERTIFICATE-----"
        putty = "PuTTY-" + "User-Key-File-3: ssh-rsa"
        entries = tuple(
            ReleaseArchiveEntry(path, content.encode())
            for path, content in (
                ("keys/release.crt", certificate),
                ("keys/release.ppk", putty),
                ("config/.env.production", "SAFE=true"),
                ("coverage.xml", "<coverage />"),
                ("reports/junit.xml", "<testsuite />"),
                ("reports/lcov.info", "TN:\n"),
            )
        )

        report = scan_release_entries(entries, repository_root=ROOT)

        self.assertEqual(len(report.findings), len(entries))
        self.assertEqual(
            {finding.category for finding in report.findings},
            {"generated-artifact", "sensitive-file"},
        )

    def test_windows_equivalent_and_reserved_archive_paths_are_rejected(self) -> None:
        entries = tuple(
            ReleaseArchiveEntry(path, b"unsafe")
            for path in (
                "docs/tool.exe.",
                "docs/bundle.zip.",
                "docs/CON.txt",
                "docs/aux",
            )
        )

        report = scan_release_entries(entries, repository_root=ROOT)

        self.assertEqual(len(report.findings), 1)
        self.assertEqual(
            {finding.category for finding in report.findings},
            {"unsafe-archive-path"},
        )

    def test_posix_file_uri_unc_and_drive_root_paths_are_rejected(self) -> None:
        slash_unc = "/" * 2 + "/".join(("private-server", "secret-share", "file.txt"))
        backslash = chr(92)
        extended_unc = (
            backslash * 2
            + "?"
            + backslash
            + backslash.join(("UNC", "private-server", "secret-share", "file.txt"))
        )
        entries = tuple(
            ReleaseArchiveEntry(f"docs/path-{index}.txt", path.encode())
            for index, path in enumerate(
                (
                    "/" + "/".join(("workspace", "alice", "private", "config.json")),
                    "file:" + "///" + "/".join(("opt", "private", "project", "file.txt")),
                    slash_unc,
                    extended_unc,
                    "C" + ":/",
                    "D" + ":/Users",
                )
            )
        )

        report = scan_release_entries(entries, repository_root=ROOT)

        self.assertEqual(
            [finding.category for finding in report.findings],
            ["absolute-path"] * len(entries),
        )

    def test_posix_system_absolute_paths_and_single_slash_file_uri_are_rejected(self) -> None:
        roots = (
            ("opt", "private", "project", "file.txt"),
            ("etc", "secret.conf"),
            ("srv", "company", "app", "config.yml"),
            ("var", "lib", "private", "data.db"),
            ("usr", "local", "private", "tool"),
            ("private", "var", "tmp", "file"),
            ("data", "company", "evidence.json"),
            ("app", "private", "config"),
            ("mnt", "d", "private", "project", "file"),
            ("media", "alice", "drive", "private"),
            ("Volumes", "Private", "project"),
            ("run", "user", "1000", "private"),
            ("github", "workspace", "private"),
            ("__w", "project", "source", "file"),
        )
        values = ["/" + "/".join(parts) for parts in roots]
        values.append("file:" + "/" + "/".join(("opt", "private", "file")))
        entries = tuple(
            ReleaseArchiveEntry(f"docs/system-{index}.txt", value.encode())
            for index, value in enumerate(values)
        )

        report = scan_release_entries(entries, repository_root=ROOT)

        self.assertEqual(
            [finding.category for finding in report.findings],
            ["absolute-path"] * len(entries),
        )

    def test_current_local_username_cannot_be_allowlisted(self) -> None:
        local_user = Path.home().name
        private_path = "C" + ":/" + "/".join(
            ("Users", local_user, "private", "file.txt")
        )
        rule = ReleaseContentAllowRule(
            "tests/fixture.py",
            private_path,
            "This deliberately invalid rule names the current local user.",
        )

        with self.assertRaises(ValueError):
            scan_release_entries(
                (ReleaseArchiveEntry("tests/fixture.py", private_path.encode()),),
                repository_root=ROOT,
                allow_rules=(rule,),
            )

    def test_current_posix_username_and_root_home_cannot_be_allowlisted(self) -> None:
        local_user = Path.home().name
        posix_path = "/" + "/".join(("home", local_user, "private", "file.txt"))
        root_path = "/" + "/".join(("root", "private", "file.txt"))
        for private_path in (posix_path, root_path):
            with self.subTest(private_path=private_path):
                rule = ReleaseContentAllowRule(
                    "tests/fixture.py",
                    private_path,
                    "This deliberately invalid rule names a local private home.",
                )
                with self.assertRaises(ValueError):
                    scan_release_entries(
                        (ReleaseArchiveEntry("tests/fixture.py", private_path.encode()),),
                        repository_root=ROOT,
                        allow_rules=(rule,),
                    )

    def test_allow_rule_occurrence_budget_cannot_hide_duplicate_paths(self) -> None:
        private_path = "C" + ":/" + "/".join(
            ("Users", "fixture-user", "private.txt")
        )
        rule = ReleaseContentAllowRule(
            "tests/fixture.py",
            private_path,
            "One synthetic path occurrence is permitted for this focused fixture.",
        )
        content = (private_path + " " + private_path + "\n").encode()

        report = scan_release_entries(
            (ReleaseArchiveEntry("tests/fixture.py", content),),
            repository_root=ROOT,
            allow_rules=(rule,),
        )

        self.assertEqual(
            [(finding.relative_path, finding.line, finding.category) for finding in report.findings],
            [("tests/fixture.py", 1, "absolute-path")],
        )

    def test_workspace_temporary_and_arbitrary_drive_paths_are_rejected(self) -> None:
        workspace = "D" + ":/" + "/".join(
            ("agent", "_work", "project", "source.py")
        )
        temporary = "/" + "/".join(("tmp", "build", "result.json"))
        install = "C" + ":/Program Files/Epic Games/ARKDevkit/Projects/ShooterGame/Content"
        private = "D" + ":/private/company/project/file.txt"
        entries = (
            ReleaseArchiveEntry("docs/workspace.md", workspace.encode()),
            ReleaseArchiveEntry("docs/temporary.md", temporary.encode()),
            ReleaseArchiveEntry("docs/install.md", install.encode()),
            ReleaseArchiveEntry("docs/private.md", private.encode()),
        )

        report = scan_release_entries(entries, repository_root=ROOT)

        self.assertEqual(
            [finding.relative_path for finding in report.findings],
            [
                "docs/install.md",
                "docs/private.md",
                "docs/temporary.md",
                "docs/workspace.md",
            ],
        )

    def test_schema_unicode_escape_allowance_does_not_allow_a_matching_unc_server(self) -> None:
        slash = chr(92)
        schema_range = slash * 2 + "u0000-" + slash * 2 + "u0020" + slash * 2 + "u007f]"
        unc_path = slash * 2 + "u1234" + slash + "private" + slash + "file.txt"
        entries = (
            ReleaseArchiveEntry(
                "schemas/kb_production_narrow_gate_report_v1.schema.json",
                schema_range.encode(),
            ),
            ReleaseArchiveEntry("docs/unc.md", unc_path.encode()),
        )

        report = scan_release_entries(entries, repository_root=ROOT)

        self.assertEqual(
            [(finding.relative_path, finding.category) for finding in report.findings],
            [("docs/unc.md", "absolute-path")],
        )

    def test_git_ref_inventory_is_immutable_when_worktree_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = self._repository(
                Path(temporary),
                {
                    ".gitattributes": "* text=auto\n",
                    "docs/note.md": "committed safe content\n",
                },
            )
            secret = "sk" + "-" + ("Z" * 32)
            (repo / "docs" / "note.md").write_text(secret + "\n", encoding="utf-8")

            entries, _commit = collect_git_archive_entries(repo, "HEAD")
            report = scan_release_entries(entries, repository_root=repo)

        self.assertEqual(report.findings, ())

    def test_worktree_mode_sees_uncommitted_content_and_honors_export_ignore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = self._repository(
                Path(temporary),
                {
                    ".gitattributes": "/runtime/** export-ignore\n",
                    "docs/note.md": "committed safe content\n",
                    "runtime/python.exe": b"MZ\x00runtime",
                },
            )
            secret = "sk" + "-" + ("Z" * 32)
            (repo / "docs" / "note.md").write_text(secret + "\n", encoding="utf-8")
            (repo / "scripts").mkdir()
            (repo / "scripts" / "new.py").write_text("print('new')\n", encoding="utf-8")

            entries = collect_tracked_worktree_entries(repo)
            report = scan_release_entries(entries, repository_root=repo)

        paths = {entry.relative_path for entry in entries}
        self.assertIn("scripts/new.py", paths)
        self.assertNotIn("runtime/python.exe", paths)
        self.assertEqual(
            [finding.category for finding in report.findings],
            ["secret-signature"],
        )

    @unittest.skipUnless(os.name == "nt", "Windows CLI contract")
    def test_cli_invalid_ref_fails_closed_without_exposing_internal_paths(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "check_release_content.py"),
                "--git-ref",
                "missing-release-candidate",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("status=FAIL", completed.stdout)
        self.assertIn("category=policy-error", completed.stdout)
        self.assertNotIn(str(ROOT), completed.stdout)
        self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
