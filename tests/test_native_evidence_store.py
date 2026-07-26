from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURE = ROOT / "tests" / "fixtures" / "native_evidence" / "native_evidence_v2.json"
sys.path.insert(0, str(SCRIPTS))

from blueprint_translator.context_pack import estimate_tokens  # noqa: E402
from blueprint_translator.native_evidence_repository import (  # noqa: E402
    NativeEvidenceArtifactInvalid,
    open_native_evidence_repository,
)
from blueprint_translator.native_identity import (  # noqa: E402
    validate_native_evidence_manifest,
)
from blueprint_translator.native_evidence_store import (  # noqa: E402
    NATIVE_TABLES,
    write_native_evidence_artifacts,
)
from import_native_evidence import build_parser as build_import_parser  # noqa: E402


class NativeEvidenceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.source = self.root / "source.json"
        shutil.copyfile(FIXTURE, self.source)
        self.evidence_dir = self.root / "evidence"

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_json_authority_builds_hash_bound_integrity_checked_companion(self):
        validate_native_evidence_manifest(
            json.loads(self.source.read_text(encoding="utf-8")),
            formal=True,
        )
        result = write_native_evidence_artifacts(self.source, self.evidence_dir)

        self.assertEqual(
            result["evidence_set_id"],
            "native-set://"
            + "a" * 64
            + "/"
            + "c" * 64,
        )
        self.assertEqual(len(result["source_sha256"]), 64)
        self.assertTrue((self.evidence_dir / "evidence.full.json").is_file())
        self.assertTrue((self.evidence_dir / "evidence.manifest.json").is_file())
        self.assertTrue((self.evidence_dir / "evidence.sqlite").is_file())
        index = (self.evidence_dir / "output" / "native_index.md").read_text(encoding="utf-8")
        self.assertLessEqual(estimate_tokens(index), 1500)
        self.assertNotIn("FULL_DECOMPILE_SHOULD_NOT_APPEAR_IN_INDEX", index)

        manifest = json.loads(
            (self.evidence_dir / "evidence.manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["source"]["sha256"], result["source_sha256"])
        self.assertEqual(len(manifest["sqlite"]["sha256"]), 64)

        with closing(sqlite3.connect(self.evidence_dir / "evidence.sqlite")) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertTrue(set(NATIVE_TABLES).issubset(tables))

        with open_native_evidence_repository(self.evidence_dir) as repository:
            self.assertEqual(repository.evidence_set_id, result["evidence_set_id"])
            self.assertEqual(len(repository.list_functions()), 4)

    def test_source_or_sqlite_tamper_fails_closed(self):
        write_native_evidence_artifacts(self.source, self.evidence_dir)
        authoritative = self.evidence_dir / "evidence.full.json"
        payload = json.loads(authoritative.read_text(encoding="utf-8"))
        payload["generatedAtUtc"] = "tampered"
        authoritative.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(NativeEvidenceArtifactInvalid, "source.*hash"):
            open_native_evidence_repository(self.evidence_dir)

        shutil.rmtree(self.evidence_dir)
        write_native_evidence_artifacts(self.source, self.evidence_dir)
        database = self.evidence_dir / "evidence.sqlite"
        with database.open("ab") as handle:
            handle.write(b"tamper")
        with self.assertRaisesRegex(NativeEvidenceArtifactInvalid, "SQLite.*hash"):
            open_native_evidence_repository(self.evidence_dir)

    def test_invalid_native_evidence_id_is_rejected_before_publication(self):
        payload = json.loads(self.source.read_text(encoding="utf-8"))
        payload["targets"][0]["evidenceId"] = "native://wrong/fixture.dll/0x1000"
        self.source.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "evidenceId"):
            write_native_evidence_artifacts(self.source, self.evidence_dir)
        self.assertFalse((self.evidence_dir / "evidence.sqlite").exists())

    def test_public_cli_help_runs_with_standard_and_bundled_python(self):
        commands = (
            "import_native_evidence.py",
            "query_native_evidence.py",
            "build_native_context_pack.py",
            "link_blueprint_native_evidence.py",
            "build_hybrid_context_pack.py",
        )
        interpreters = [Path(sys.executable)]
        bundled = ROOT / "runtime" / "python" / "python.exe"
        if bundled.is_file() and bundled.resolve() != interpreters[0].resolve():
            interpreters.append(bundled)

        for interpreter in interpreters:
            for command in commands:
                with self.subTest(interpreter=str(interpreter), command=command):
                    completed = subprocess.run(
                        [
                            str(interpreter),
                            str(SCRIPTS / command),
                            "--help",
                        ],
                        cwd=ROOT,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=15,
                        check=False,
                    )
                    self.assertEqual(
                        completed.returncode,
                        0,
                        msg=completed.stdout + completed.stderr,
                    )

    def test_formal_import_rejects_unverified_evidence_and_experimental_is_labeled(self):
        payload = json.loads(self.source.read_text(encoding="utf-8"))
        payload["trust"]["status"] = "EXPERIMENTAL"
        self.source.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "VERIFIED|formal"):
            write_native_evidence_artifacts(self.source, self.evidence_dir)
        self.assertFalse((self.evidence_dir / "evidence.manifest.json").exists())

        write_native_evidence_artifacts(
            self.source,
            self.evidence_dir,
            formal=False,
        )
        manifest = json.loads(
            (self.evidence_dir / "evidence.manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["trust"]["status"], "EXPERIMENTAL")
        self.assertFalse(manifest["trust"]["formalValidation"])
        index = (self.evidence_dir / "output" / "native_index.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Provenance: `EXPERIMENTAL`", index)
        with open_native_evidence_repository(self.evidence_dir) as repository:
            overview = repository.query(
                {"operation": "overview", "budgetTokens": 700}
            )
        self.assertEqual(overview["items"][0]["status"], "EXPERIMENTAL")

        args = build_import_parser().parse_args(
            [
                "--source",
                str(self.source),
                "--evidence-dir",
                str(self.evidence_dir),
                "--formal",
            ]
        )
        self.assertTrue(args.formal)
        default_args = build_import_parser().parse_args(
            [
                "--source",
                str(self.source),
                "--evidence-dir",
                str(self.evidence_dir),
            ]
        )
        self.assertTrue(default_args.formal)

    def test_bundled_cli_end_to_end_import_query_and_context(self):
        bundled = ROOT / "runtime" / "python" / "python.exe"
        interpreter = bundled if bundled.is_file() else Path(sys.executable)
        evidence_dir = self.root / "cli-evidence"
        context_dir = self.root / "cli-context"

        def run_cli(script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
            completed = subprocess.run(
                [
                    str(interpreter),
                    str(SCRIPTS / script),
                    *arguments,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                msg=completed.stdout + completed.stderr,
            )
            return completed

        imported = run_cli(
            "import_native_evidence.py",
            "--source",
            str(self.source),
            "--evidence-dir",
            str(evidence_dir),
            "--formal",
        )
        import_payload = json.loads(imported.stdout)
        self.assertTrue(import_payload["ok"])

        queried = run_cli(
            "query_native_evidence.py",
            "--evidence-dir",
            str(evidence_dir),
            "overview",
            "--budget",
            "700",
        )
        query_payload = json.loads(queried.stdout)
        self.assertEqual(
            query_payload["schema"],
            "blueprint-to-code-native-query/v1",
        )
        self.assertEqual(query_payload["items"][0]["status"], "VERIFIED")

        run_cli(
            "build_native_context_pack.py",
            "--evidence-dir",
            str(evidence_dir),
            "--question",
            "How does ComputeQuality apply QualityScale?",
            "--budget",
            "1600",
            "--output-dir",
            str(context_dir),
        )
        context_payload = json.loads(
            (context_dir / "native_context_pack.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertLessEqual(context_payload["estimatedTokens"], 1600)
        self.assertTrue(context_payload["functions"])
        self.assertTrue((context_dir / "native_context_pack.md").is_file())


if __name__ == "__main__":
    unittest.main()
