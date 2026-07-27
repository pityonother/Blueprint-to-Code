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

from _python_interpreter import (
    compatible_python_interpreters,
    preferred_python,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURE = ROOT / "tests" / "fixtures" / "native_evidence" / "native_evidence_v2.json"
REAL_LOOT_EVIDENCE = (
    ROOT
    / "native_evidence"
    / "stores"
    / "b0e67e1e7625"
    / (
        "ark-loot-quality-v1-b81a1f3c01fd-20260727-032404-"
        "d386cfd2851c49f292673cf60d1d13f8"
    )
    / "evidence.full.json"
)
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
    render_native_index,
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

    def test_raw_ghidra_from_address_is_normalized_into_callsite_rva(self):
        payload = json.loads(self.source.read_text(encoding="utf-8"))
        caller = payload["targets"][0]
        callee = payload["targets"][1]
        caller_id = caller["evidenceId"]
        callee_id = callee["evidenceId"]
        caller["calls"] = [
            {
                "callerEvidenceId": caller_id,
                "calleeEvidenceId": callee_id,
                "targetEvidenceId": callee_id,
                "callsiteRva": "",
                "kind": "DIRECT_OR_RECOVERED_CALL",
                "status": "CONFIRMED",
                "confidence": "HIGH",
            }
        ]
        callee["callSites"] = [
            {
                "fromAddress": "180004321",
                "referenceType": "UNCONDITIONAL_CALL",
                "callerEvidenceId": caller_id,
            }
        ]
        payload["provenance"]["binary"]["imageBase"] = "0x180000000"
        self.source.write_text(json.dumps(payload), encoding="utf-8")

        write_native_evidence_artifacts(self.source, self.evidence_dir)

        with closing(
            sqlite3.connect(self.evidence_dir / "evidence.sqlite")
        ) as connection:
            row = connection.execute(
                """
                SELECT callsite_rva, payload_json
                FROM native_call_sites
                WHERE caller_evidence_id=? AND callee_evidence_id=?
                """,
                (caller_id, callee_id),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "0x4321")
        self.assertEqual(
            json.loads(row[1])["rawCallSite"]["referenceType"],
            "UNCONDITIONAL_CALL",
        )

    def test_real_formal_recipe_evidence_preserves_exec_callsite_rva(self):
        shutil.copyfile(REAL_LOOT_EVIDENCE, self.source)

        write_native_evidence_artifacts(self.source, self.evidence_dir)

        with closing(
            sqlite3.connect(self.evidence_dir / "evidence.sqlite")
        ) as connection:
            row = connection.execute(
                """
                SELECT caller.qualified_name, callee.qualified_name,
                       site.callsite_rva
                FROM native_call_sites AS site
                JOIN native_functions AS caller
                  ON caller.evidence_id=site.caller_evidence_id
                JOIN native_functions AS callee
                  ON callee.evidence_id=site.callee_evidence_id
                WHERE caller.qualified_name=
                          'UPrimalInventoryComponent::execAddItemObjectEx'
                  AND callee.qualified_name=
                          'UPrimalInventoryComponent::AddItemObjectEx'
                """
            ).fetchone()

        self.assertEqual(
            row,
            (
                "UPrimalInventoryComponent::execAddItemObjectEx",
                "UPrimalInventoryComponent::AddItemObjectEx",
                "0x43114E",
            ),
        )

    def test_index_reports_gaps_when_budget_omits_every_gap_detail(self):
        payload = json.loads(self.source.read_text(encoding="utf-8"))
        payload["gaps"] = [
            {
                "gapId": f"native-gap://fixture/budget-{index:03d}",
                "status": "NOT_RECOVERED",
                "reasonCode": "DECOMPILE_BUDGET_EXCEEDED",
                "detail": "bounded decompile detail " + ("x" * 180),
            }
            for index in range(40)
        ]
        payload_without_gaps = {**payload, "gaps": []}
        shell = render_native_index(
            payload_without_gaps,
            source_sha256="f" * 64,
            max_tokens=10_000,
        )

        index = render_native_index(
            payload,
            source_sha256="f" * 64,
            max_tokens=estimate_tokens(shell) + 30,
        )

        self.assertIn(
            "40 gaps were recorded; details were omitted by the index token budget.",
            index,
        )
        self.assertNotIn("No gaps were recorded.", index)

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
        for interpreter in compatible_python_interpreters(ROOT):
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
        interpreter = preferred_python(ROOT)
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
