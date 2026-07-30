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

    def test_v030_release_contract_is_complete_and_linked(self):
        release_path = ROOT / "docs" / "releases" / "v0.3.0.md"

        self.assertTrue(release_path.is_file())
        self.assertIn(
            "[v0.3.0 Release notes](docs/releases/v0.3.0.md)",
            self.readme,
        )
        self.assertIn("Current software version: `0.3.0`", self.readme)

        release = release_path.read_text(encoding="utf-8")
        for marker in (
            "# Blueprint to Code v0.3.0 — Engineering Foundation Milestone",
            "Engineering Foundation Milestone",
            "20260730T172442-19e56659d331",
            "60/75",
            "READY / FRESH",
            "shadow",
            "legacy",
            "cutoverEligible=false",
            "Snapshot Blueprint Evidence: 234",
            "Live Blueprint Evidence: 235",
            "SUCCEEDED=4",
            "BLOCKED_GAP=8",
            "FAILED=0",
            "ROLE_ENTITY × 1",
            "DOMAIN_ENTITY × 1",
            "PROJECTION × 6",
            "published=false",
            "SOURCE_ARCHIVE_ONLY",
            "版权由项目作者保留",
            "没有默认开源许可证",
            "../LICENSE_POLICY.md",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, release)

    def test_changelog_has_empty_unreleased_and_complete_v030_section(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        unreleased, versioned = changelog.split(
            "## [0.3.0] - 2026-07-31",
            maxsplit=1,
        )
        future = unreleased.split("## [Unreleased]", maxsplit=1)[1]

        self.assertEqual(future.strip(), "")
        for marker in (
            "Immutable-v2",
            "atomic root `current.json`",
            "fail-closed burn-in/cutover contracts",
            "reparse-safe whole-tree staging",
            "staging quarantine",
            "base-bound v3",
            "Production `QUERY_SNAPSHOT` cache invalidation",
            "Scarecrow",
            "retention and validation rules",
            "Role, Domain, and Projection",
            "production narrow",
            "incremental publisher",
            "Gold thresholds",
            "burn-in",
            "cutover",
            "not production-ready",
            "no publication occurred",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, versioned)

    def test_license_policy_defines_github_release_disclosures(self):
        policy = (ROOT / "docs" / "LICENSE_POLICY.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("## GitHub Release 披露要求", policy)
        for marker in (
            "作者保留权利",
            "无默认开源授权",
            "第三方资产",
            "Evidence",
            "KB cutover",
            "Release asset",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, policy)

    def test_readme_links_current_discovery_progress_review(self):
        review_path = (
            ROOT
            / "docs"
            / "GPT_PRO_PROGRESS_REVIEW_2026-07-27_zh.md"
        )

        self.assertTrue(review_path.is_file())
        self.assertIn(
            (
                "[ARK Knowledge Discovery：GPT Pro 视察说明]"
                "(docs/GPT_PRO_PROGRESS_REVIEW_2026-07-27_zh.md)"
            ),
            self.readme,
        )
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(
            "!docs/GPT_PRO_PROGRESS_REVIEW_2026-07-27_zh.md",
            gitignore,
        )

        review = review_path.read_text(encoding="utf-8")
        for marker in (
            "# ARK Knowledge Discovery 当前完成情况（供 GPT Pro 视察）",
            "## Codex 已完成的工程工作",
            "## 已发现的知识",
            "它不是交接文档，也不是要求 GPT Pro 接管实现",
            "`knowledge_base/discovery_bundle.zip`",
            "仓库完整测试：638 项通过，0 失败",
            "请给出审查结论与下一阶段方向即可；不需要接管或重写",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, review)

    def test_discovery_review_documents_git_lfs_retrieval_contract(self):
        review = (
            ROOT
            / "docs"
            / "GPT_PRO_PROGRESS_REVIEW_2026-07-27_zh.md"
        ).read_text(encoding="utf-8")
        lfs_path = "`knowledge_base/discovery_bundle.zip`"
        lfs_pull = (
            'git lfs pull --include="knowledge_base/discovery_bundle.zip"'
        )

        for document in (self.readme, review):
            with self.subTest(document=document[:30]):
                self.assertIn("Git LFS", document)
                self.assertIn(lfs_path, document)
                self.assertIn(lfs_pull, document)

        self.assertIn("git clone --branch codex/fix-partner-devkit-root", review)
        self.assertIn(
            "git pull --ff-only origin codex/fix-partner-devkit-root",
            review,
        )
        self.assertIn("不是项目交接包", review)
        self.assertIn("不要求 GPT Pro 接管或重写实现", review)

        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn(
            (
                "knowledge_base/discovery_bundle.zip "
                "filter=lfs diff=lfs merge=lfs -text"
            ),
            attributes,
        )

        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("knowledge_base/*", gitignore)
        self.assertIn("!knowledge_base/discovery_bundle.zip", gitignore)


if __name__ == "__main__":
    unittest.main()
