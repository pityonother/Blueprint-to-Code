from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocumentationConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_readme_keeps_runnable_build_test_and_launch_commands(self):
        for command in (
            "npm ci",
            "npm run build",
            r".\scripts\launch_blueprint_tool.ps1 -NoBuild",
            'runtime\\python\\python.exe -m unittest discover -s tests -p "test_*.py"',
        ):
            with self.subTest(command=command):
                self.assertIn(command, self.readme)

    def test_readme_names_version_and_evidence_contracts(self):
        self.assertIn("[`VERSION`](VERSION)", self.readme)
        for identifier in ("bp://", "native://", "claim://", "runtime://"):
            with self.subTest(identifier=identifier):
                self.assertIn(identifier, self.readme)
        self.assertIn("Claim Manifest", self.readme)

    def test_readme_keeps_indexed_as_the_validated_default(self):
        self.assertRegex(
            self.readme,
            re.compile(r"validated default is `indexed`", re.IGNORECASE),
        )
        self.assertIn(
            "The command above uses `indexed` by default.",
            self.readme,
        )
        self.assertNotRegex(
            self.readme,
            re.compile(r"(?:default|默认)[^\n]{0,32}`dual`", re.IGNORECASE),
        )

    def test_unified_evidence_docs_exist_and_publish_runnable_commands(self):
        required_docs = {
            "NATIVE_EVIDENCE_STORE_V1_SPEC_zh.md": (
                "scripts\\import_native_evidence.py",
                "scripts\\query_native_evidence.py",
                "scripts\\build_native_context_pack.py",
            ),
            "HYBRID_EVIDENCE_LINKING_zh.md": (
                "scripts\\link_blueprint_native_evidence.py",
                "scripts\\build_hybrid_context_pack.py",
            ),
            "REPORT_CLAIM_MANIFEST_zh.md": (
                "scripts\\validate_report_claims.py",
                "--formal",
            ),
            "GHIDRA_NATIVE_ANALYSIS_zh.md": (
                "Run-NativeRecipe.ps1",
                "-Recipe",
            ),
        }
        for file_name, commands in required_docs.items():
            with self.subTest(document=file_name):
                path = ROOT / "docs" / file_name
                self.assertTrue(path.is_file(), file_name)
                text = path.read_text(encoding="utf-8")
                for command in commands:
                    self.assertIn(command, text)
                self.assertIn(f"(docs/{file_name})", self.readme)

    def test_native_docs_do_not_recommend_cross_hash_project_reuse(self):
        ghidra = (ROOT / "docs" / "GHIDRA_NATIVE_ANALYSIS_zh.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("ShooterGameNative_<binary-sha12>", ghidra)
        self.assertIn("-AllowHashMismatch -Experimental", ghidra)
        self.assertNotIn("ShooterGameNative_B0E67E1E", ghidra)

    def test_author_retained_rights_policy_is_explicit(self):
        policy_path = ROOT / "docs" / "LICENSE_POLICY.md"

        self.assertTrue(policy_path.is_file())
        self.assertIn(
            "[授权与分发策略](docs/LICENSE_POLICY.md)",
            self.readme,
        )
        self.assertNotIn("LICENSE_DECISION_REQUIRED", self.readme)
        self.assertFalse((ROOT / "LICENSE").exists())

        policy = policy_path.read_text(encoding="utf-8")
        self.assertIn("不授予开源许可证", policy)
        self.assertIn("版权由项目作者保留", policy)


if __name__ == "__main__":
    unittest.main()
