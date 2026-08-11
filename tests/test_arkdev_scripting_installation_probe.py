from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_scripting_probe.contracts import assert_path_free  # noqa: E402
from arkdev_scripting_probe.installation_probe import (  # noqa: E402
    render_installation_summary,
    scan_installation,
)


class InstallationProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.devkit = Path(self._temporary.name) / "DevKit"
        self.output = Path(self._temporary.name) / "result.json"

    def write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def write_file(self, relative: str, content: bytes = b"") -> Path:
        path = self.devkit / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def descriptor(
        self,
        name: str,
        *,
        enabled_by_default: bool = False,
    ) -> None:
        self.write_json(
            self.devkit / "Engine" / "Plugins" / name / f"{name}.uplugin",
            {
                "FileVersion": 3,
                "VersionName": "1.0",
                "EnabledByDefault": enabled_by_default,
                "Modules": [{"Name": name, "Type": "Editor"}],
            },
        )

    def complete_install(self) -> None:
        self.descriptor("PythonScriptPlugin")
        self.descriptor("EditorScriptingUtilities")
        self.write_file("Engine/Binaries/Win64/ShooterGameEditor.exe")
        self.write_file("Engine/Binaries/ThirdParty/Python3/Win64/python.exe")
        self.write_file("Engine/Binaries/ThirdParty/Python3/Win64/python311.dll")
        self.write_file(
            "Engine/Binaries/Win64/ShooterGameEditor-PythonScriptPlugin.dll"
        )
        self.write_file(
            "Engine/Binaries/Win64/ShooterGameEditor-EditorScriptingUtilities.dll"
        )
        self.write_file("Engine/Binaries/Win64/ShooterGameEditor-Blutility.dll")
        self.write_file("Engine/Content/Python/init_unreal.py")
        self.write_json(
            self.devkit / "Engine" / "Build" / "Build.version",
            {
                "MajorVersion": 5,
                "MinorVersion": 5,
                "PatchVersion": 4,
                "Changelist": 0,
                "CompatibleChangelist": 37670630,
            },
        )
        self.write_json(
            self.devkit / "Projects" / "ShooterGame" / "ShooterGame.uproject",
            {
                "FileVersion": 3,
                "Plugins": [
                    {"Name": "PythonScriptPlugin", "Enabled": True},
                    {"Name": "EditorScriptingUtilities", "Enabled": True},
                ],
            },
        )

    def test_complete_install_is_detected_without_exposing_paths(self) -> None:
        self.complete_install()

        result = scan_installation(self.devkit, output_path=self.output)

        self.assertTrue(result["devkitFound"])
        self.assertEqual(result["devkitBuild"], "5.5.4-0+UE5")
        self.assertTrue(result["pythonScriptPluginPresent"])
        self.assertTrue(result["editorScriptingUtilitiesPresent"])
        self.assertTrue(result["editorUtilityPresent"])
        self.assertTrue(result["pythonEmbeddedRuntimePresent"])
        self.assertTrue(result["projectDescriptorFound"])
        self.assertEqual(result["pythonPluginEnabled"], "TRUE")
        self.assertEqual(result["nextManualAction"], "RUN_IN_EDITOR_PROBE")
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8")), result)
        self.assertNotIn(str(self.devkit), json.dumps(result))
        assert_path_free(result)

        rendered = render_installation_summary(result)
        self.assertIn("PYTHON_SCRIPT_PLUGIN_PRESENT=true", rendered)
        self.assertIn("PYTHON_PLUGIN_ENABLED=TRUE", rendered)
        self.assertNotIn(str(self.devkit), rendered)

    def test_explicit_project_disable_wins_over_descriptor_default(self) -> None:
        self.descriptor("PythonScriptPlugin", enabled_by_default=True)
        self.write_file("Engine/Binaries/Win64/ShooterGameEditor.exe")
        self.write_file("Engine/Binaries/ThirdParty/Python3/Win64/python.exe")
        self.write_file("Engine/Binaries/ThirdParty/Python3/Win64/python311.dll")
        self.write_file(
            "Engine/Binaries/Win64/ShooterGameEditor-PythonScriptPlugin.dll"
        )
        self.write_json(
            self.devkit / "Project" / "Project.uproject",
            {
                "Plugins": [
                    {"Name": "PythonScriptPlugin", "Enabled": False}
                ]
            },
        )

        result = scan_installation(self.devkit)

        self.assertEqual(result["pythonPluginEnabled"], "FALSE")
        self.assertEqual(result["nextManualAction"], "ENABLE_PYTHON_PLUGIN")

    def test_descriptor_without_project_entry_is_unknown(self) -> None:
        self.descriptor("PythonScriptPlugin", enabled_by_default=False)

        result = scan_installation(self.devkit)

        self.assertEqual(result["pythonPluginEnabled"], "UNKNOWN")

    def test_missing_root_is_a_bounded_unavailable_result(self) -> None:
        result = scan_installation(self.devkit)

        self.assertFalse(result["devkitFound"])
        self.assertEqual(result["nextManualAction"], "STOP_DEVKIT_NOT_FOUND")
        assert_path_free(result)


if __name__ == "__main__":
    unittest.main()
