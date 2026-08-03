from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = (
    ROOT
    / "devkit_plugins"
    / "BlueprintToCodeExporter"
    / "Source"
    / "BlueprintToCodeExporter"
    / "Private"
    / "BlueprintToCodeExporterModule.cpp"
)


class DevKitExporterProjectRootContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SOURCE_PATH.read_text(encoding="utf-8")
        cls.resolve_body = cls.source.split(
            "FString FBlueprintToCodeExporterModule::ResolveProjectRoot() const",
            maxsplit=1,
        )[1].split(
            "FString FBlueprintToCodeExporterModule::MakeCaptureDirectoryName",
            maxsplit=1,
        )[0]

    def test_resolution_order_is_environment_then_plugin_search_then_documents(self):
        ordered_markers = (
            'GetEnvironmentVariable(TEXT("BLUEPRINT_TO_CODE_ROOT"))',
            'FindPlugin(TEXT("BlueprintToCodeExporter"))',
            "Plugin->GetBaseDir()",
            'TEXT("scripts")',
            'TEXT("bp_clipboard_to_prompt.py")',
            "FPlatformProcess::UserDir()",
            'TEXT("Documents")',
            'TEXT("Blueprint to Code")',
        )

        positions = [self.resolve_body.index(marker) for marker in ordered_markers]
        self.assertEqual(positions, sorted(positions))

    def test_resolution_uses_cross_platform_unreal_path_primitives(self):
        self.assertIn('#include "HAL/PlatformProcess.h"', self.source)
        for marker in (
            "FPaths::Combine(",
            "FPaths::ConvertRelativePathToFull(",
            "FPaths::NormalizeDirectoryName(",
            "FPaths::GetPath(Candidate)",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.resolve_body)

        windows_forward_root = 'TEXT("' + "/".join(("C:", "Users")) + "/"
        windows_backslash_root = 'TEXT("' + "\\\\".join(("C:", "Users")) + "\\\\"
        self.assertNotIn(windows_forward_root, self.resolve_body)
        self.assertNotIn(windows_backslash_root, self.resolve_body)

    def test_no_machine_specific_default_root_remains(self):
        machine_specific_fragments = (
            "DefaultProjectRoot",
            "/".join(("C:", "Users", "ac")),
            "\\\\".join(("C:", "Users", "ac")),
            "project " + "gaming/Blueprint to Code",
        )
        for forbidden in machine_specific_fragments:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.source)

    def test_plugin_search_requires_the_translator_entrypoint(self):
        self.assertIn(
            (
                "FPaths::FileExists(FPaths::Combine(Candidate, TEXT(\"scripts\"), "
                "TEXT(\"bp_clipboard_to_prompt.py\")))"
            ),
            self.resolve_body,
        )
        self.assertLess(
            self.resolve_body.index("FPaths::FileExists("),
            self.resolve_body.index("FPaths::GetPath(Candidate)"),
        )


if __name__ == "__main__":
    unittest.main()
