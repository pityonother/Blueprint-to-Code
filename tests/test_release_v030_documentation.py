from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseV030DocumentationTests(unittest.TestCase):
    def test_release_notes_are_discoverable_and_define_the_release_boundary(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        release_path = ROOT / "docs" / "releases" / "v0.3.0.md"

        self.assertTrue(release_path.is_file())
        self.assertIn(
            "[v0.3.0 Release notes](docs/releases/v0.3.0.md)",
            readme,
        )

        release = release_path.read_text(encoding="utf-8")
        for marker in (
            "Engineering Preview",
            "SOURCE_ARCHIVE_ONLY",
            "不包含 ARK 数据",
            "不包含 DevKit",
            "不包含 DLL/PDB",
            "不包含 Capture/Evidence DB",
            "不包含真实 runtime observations",
            "mode=shadow",
            "defaultQuerySource=legacy",
            "cutoverEligible=false",
            "已知限制",
            "不代表真实 ARK runtime 行为已经验证",
            "ACTIVE_FORMAL=0",
            "historical errors=3",
            "historical warnings=3",
            "DIAGNOSTIC=6",
            "[report registry](../../reports/report_registry.json)",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, release)

    def test_changelog_has_a_complete_v030_blueprint_harvest_engineering_entry(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

        self.assertIn("## [0.3.0] - 2026-08-03", changelog)
        versioned = changelog.split("## [0.3.0] - 2026-08-03", maxsplit=1)[1]
        versioned = versioned.split("## [0.2.0]", maxsplit=1)[0]
        for marker in (
            "Evidence Publication v3",
            "manifest-bound validated readers",
            "Interpretation Contract v1",
            "statement trace",
            "heuristic behavior hints",
            "legacy/experimental pseudocode",
            "dominance audit",
            "confirmed/conditional",
            "canonical variant",
            "metric-specific units",
            "runtime profile isolation",
            "relative-first specialties",
            "Effectiveness gap",
            "source archive policy",
            "release-content scanner",
            "path/credential/generated-artifact scan",
            "shadow/legacy",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, versioned)


if __name__ == "__main__":
    unittest.main()
