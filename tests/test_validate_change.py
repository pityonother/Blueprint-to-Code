from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_change  # noqa: E402


CONFIG = ROOT / "scripts" / "validation_profiles.json"
RECEIPT_SCHEMA = ROOT / "schemas" / "change_validation_receipt_v1.schema.json"


class ValidateChangeTests(unittest.TestCase):
    def test_auto_classification_uses_highest_risk_and_keeps_all_capabilities(self):
        config = validate_change.load_config(CONFIG)

        classification = validate_change.classify_changed_files(
            [
                "scripts/query_blueprint_evidence.py",
                "src/App.tsx",
                "scripts/blueprint_translator/evidence_publication.py",
            ],
            config,
        )

        self.assertEqual(classification.risk, "L3")
        self.assertEqual(classification.selected_profile, "publication")
        self.assertEqual(
            set(classification.affected_profiles),
            {"frontend", "publication", "query"},
        )
        self.assertEqual(
            set(classification.capabilities),
            {"frontend", "full-python", "query"},
        )

    def test_unknown_files_fail_closed_to_l3_full_python(self):
        config = validate_change.load_config(CONFIG)

        classification = validate_change.classify_changed_files(
            ["new-system/opaque.contract"],
            config,
        )

        self.assertEqual(classification.risk, "L3")
        self.assertEqual(classification.selected_profile, "unknown")
        self.assertIn("full-python", classification.capabilities)
        self.assertIn("UNKNOWN_FILE_CLASS", classification.reasons)

    def test_high_risk_classes_force_one_full_suite_and_docs_do_not(self):
        config = validate_change.load_config(CONFIG)
        cases = (
            ("scripts/blueprint_translator/evidence_policy.py", "parser"),
            (
                "scripts/blueprint_translator/evidence_publication.py",
                "publication",
            ),
            ("new-system/opaque.contract", "unknown"),
        )
        for path, expected_profile in cases:
            with self.subTest(path=path):
                classification = validate_change.classify_changed_files(
                    [path],
                    config,
                )
                commands = validate_change.build_command_plan(
                    classification,
                    changed_files=[path],
                    base_sha="a" * 40,
                    python_command="python",
                )
                self.assertEqual(classification.selected_profile, expected_profile)
                self.assertEqual(
                    [item.identifier for item in commands].count(
                        "python-full-suite"
                    ),
                    1,
                )

        docs = validate_change.classify_changed_files(["docs/guide.md"], config)
        docs_commands = validate_change.build_command_plan(
            docs,
            changed_files=["docs/guide.md"],
            base_sha="a" * 40,
            python_command="python",
        )
        self.assertEqual(docs.risk, "L0")
        self.assertNotIn(
            "python-full-suite",
            [item.identifier for item in docs_commands],
        )

    def test_explicit_profile_cannot_downgrade_detected_risk(self):
        config = validate_change.load_config(CONFIG)
        detected = validate_change.classify_changed_files(
            ["scripts/blueprint_translator/evidence_publication.py"],
            config,
        )

        with self.assertRaisesRegex(ValueError, "cannot downgrade"):
            validate_change.apply_requested_profile(detected, "query", config)

    def test_command_plan_runs_each_test_node_once(self):
        config = validate_change.load_config(CONFIG)
        detected = validate_change.classify_changed_files(
            [
                "scripts/blueprint_translator/evidence_publication.py",
                "scripts/query_blueprint_evidence.py",
                "src/App.tsx",
            ],
            config,
        )

        commands = validate_change.build_command_plan(
            detected,
            changed_files=[
                "scripts/blueprint_translator/evidence_publication.py",
                "scripts/query_blueprint_evidence.py",
                "src/App.tsx",
            ],
            base_sha="a" * 40,
            python_command="python",
        )

        ids = [command.identifier for command in commands]
        self.assertEqual(len(ids), len(set(ids)))
        pytest_commands = [
            command for command in commands if "-m" in command.argv and "pytest" in command.argv
        ]
        self.assertEqual(len(pytest_commands), 1)
        self.assertEqual(pytest_commands[0].identifier, "python-full-suite")
        self.assertIn("frontend-build", ids)

    def test_content_identity_cache_requires_all_four_bound_roles(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            cache = root / "cache"
            inputs: dict[str, Path] = {}
            for role in ("source", "database", "manifest", "pointer"):
                path = root / f"{role}.bin"
                path.write_bytes((role + "-bytes").encode("ascii"))
                inputs[role] = path

            first = validate_change.content_identity_cache(inputs, cache)
            second = validate_change.content_identity_cache(inputs, cache)
            inputs["source"].write_bytes(b"changed-source")
            third = validate_change.content_identity_cache(inputs, cache)

            self.assertFalse(first["hit"])
            self.assertTrue(second["hit"])
            self.assertFalse(third["hit"])
            self.assertNotEqual(first["key"], third["key"])
            self.assertEqual(first["usedToSkipCommands"], [])
            self.assertNotIn(str(root), json.dumps(first))

            with self.assertRaisesRegex(ValueError, "exactly"):
                validate_change.content_identity_cache(
                    {"source": inputs["source"]},
                    cache,
                )

    def test_dry_run_writes_atomic_machine_receipt(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            receipt = Path(temporary_directory) / "receipt.json"

            exit_code = validate_change.main(
                [
                    "--base",
                    "HEAD~1",
                    "--profile",
                    "auto",
                    "--receipt",
                    str(receipt),
                    "--config",
                    str(CONFIG),
                    "--dry-run",
                ]
            )

            self.assertEqual(exit_code, 0)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            jsonschema.validate(
                payload,
                json.loads(RECEIPT_SCHEMA.read_text(encoding="utf-8")),
            )
            self.assertEqual(
                payload["schema"],
                "blueprint-to-code.change-validation-receipt.v1",
            )
            self.assertEqual(payload["status"], "PLANNED")
            self.assertTrue(payload["changedFiles"])
            self.assertEqual(
                len(payload["commands"]),
                len({item["id"] for item in payload["commands"]}),
            )
            self.assertFalse(payload["fullRun"] is None)
            self.assertFalse((receipt.parent / ".receipt.json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
