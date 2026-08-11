from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "devkit_plugins" / "BlueprintToCodeExporter"
SOURCE = PLUGIN / "Source" / "BlueprintToCodeExporter"


class DevkitEditorBridgeSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = (
            SOURCE / "Private" / "BlueprintToCodeExporterModule.cpp"
        ).read_text(encoding="utf-8")
        cls.bridge = (
            SOURCE / "Private" / "BlueprintToCodeEditorBridge.cpp"
        ).read_text(encoding="utf-8")
        cls.header = (
            SOURCE / "Public" / "BlueprintToCodeExporterModule.h"
        ).read_text(encoding="utf-8")
        cls.build = (SOURCE / "BlueprintToCodeExporter.Build.cs").read_text(
            encoding="utf-8"
        )
        cls.descriptor = json.loads(
            (PLUGIN / "BlueprintToCodeExporter.uplugin").read_text(encoding="utf-8")
        )
        cls.all_source = "\n".join((cls.module, cls.bridge, cls.header, cls.build))

    def test_plugin_version_and_existing_exporter_are_preserved(self) -> None:
        self.assertEqual(self.descriptor["Version"], 2)
        self.assertEqual(self.descriptor["VersionName"], "0.2.0")
        self.assertIn("Export Selected Blueprint Graph Queue", self.module)
        self.assertIn("graph_queue.txt", self.module)
        self.assertIn("graph_pages_cpp.json", self.module)
        self.assertIn("cpp_export_report.md", self.module)

    def test_bridge_uses_only_required_public_editor_headers_and_modules(self) -> None:
        for header in (
            '"Subsystems/AssetEditorSubsystem.h"',
            '"Kismet2/KismetEditorUtilities.h"',
            '"BlueprintEditorModule.h"',
            '"EdGraph/EdGraph.h"',
            '"EdGraph/EdGraphNode.h"',
            '"Engine/Blueprint.h"',
        ):
            with self.subTest(header=header):
                self.assertIn(header, self.bridge)
        for module in ("Kismet", "UnrealEd", "Json", "Projects", "ToolMenus"):
            with self.subTest(module=module):
                self.assertIn(f'"{module}"', self.build)
        for forbidden in (
            "Networking",
            "Sockets",
            "HTTP",
            "PythonScriptPlugin",
        ):
            self.assertNotIn(f'"{forbidden}"', self.build)

    def test_snapshot_is_bounded_heartbeat_driven_and_atomically_replaced(self) -> None:
        self.assertIn("blueprint-to-code.arkdev-editor-snapshot/v1", self.bridge)
        self.assertIn("arkdev-editor-snapshot/v1", self.bridge)
        self.assertIn("blueprint-to-code-exporter/editor-bridge-v1", self.bridge)
        self.assertRegex(self.bridge, r"MaxSnapshotNodes\s*=\s*2000")
        self.assertRegex(self.bridge, r"EditorBridgeTickSeconds\s*=\s*0\.25")
        self.assertRegex(self.bridge, r"EditorBridgeHeartbeatSeconds\s*=\s*2\.0")
        self.assertIn(".arkdev-bridge", self.bridge)
        self.assertIn("editor_state.json", self.bridge)
        self.assertIn(".editor_state.json.", self.bridge)
        self.assertIn(".tmp", self.bridge)
        self.assertIn("SaveStringToFile", self.bridge)
        self.assertIn('"Windows/WindowsHWrapper.h"', self.bridge)
        self.assertIn("MoveFileExW", self.bridge)
        self.assertIn("MOVEFILE_REPLACE_EXISTING", self.bridge)
        self.assertIn("MOVEFILE_WRITE_THROUGH", self.bridge)
        self.assertNotIn("MOVEFILE_COPY_ALLOWED", self.bridge)
        self.assertNotRegex(self.bridge, r"IFileManager::Get\(\)\.Move\(")
        self.assertIn("LastBridgeSemanticState", self.bridge)
        self.assertIn("LastBridgeWriteSeconds", self.bridge)
        self.assertIn("Delete(*EditorBridgeStatePath", self.bridge)

    def test_public_api_calls_do_not_focus_or_open_editor_state(self) -> None:
        self.assertIn("GetAllEditedAssets()", self.bridge)
        self.assertIn("FindEditorForAsset(Blueprint, false)", self.bridge)
        self.assertIn("GetLastActivationTime()", self.bridge)
        self.assertIn("ACTIVE_BLUEPRINT_AMBIGUOUS", self.bridge)
        self.assertIn("Left.LastActivationTime > Right.LastActivationTime", self.bridge)
        self.assertIn("Left.ObjectPath < Right.ObjectPath", self.bridge)
        self.assertIn("GetIBlueprintEditorForObject(Blueprint, false)", self.bridge)
        self.assertIn("GetFocusedGraph()", self.bridge)
        self.assertNotIn("OpenEditorForAsset", self.bridge)
        self.assertNotIn("OpenGraphAndBringToFront", self.bridge)
        self.assertIn("GetOutermost()->IsDirty()", self.bridge)
        self.assertIn("NodeGuid.ToString()", self.bridge)
        self.assertIn("Left.Node->NodePosY < Right.Node->NodePosY", self.bridge)
        self.assertIn("Left.Node->NodePosX < Right.Node->NodePosX", self.bridge)
        for status in (
            "NO_ACTIVE_BLUEPRINT",
            "BLUEPRINT_EDITOR_INTERFACE_UNAVAILABLE",
            "NO_FOCUSED_GRAPH",
            "FOCUSED_GRAPH",
        ):
            with self.subTest(status=status):
                self.assertIn(status, self.bridge)
        for compile_status in (
            "BS_Dirty",
            "BS_Error",
            "BS_UpToDate",
            "BS_UpToDateWithWarnings",
        ):
            with self.subTest(compile_status=compile_status):
                self.assertIn(compile_status, self.bridge)

    def test_source_contains_no_blueprint_mutation_or_private_transport_calls(self) -> None:
        forbidden_calls = (
            "ImportNodesFromText",
            "TryCreateConnection",
            "BreakPinLinks",
            "Modify",
            "MarkPackageDirty",
            "CompileBlueprint",
            "SavePackage",
            "SaveAsset",
            "PasteNodesHere",
            "ReconstructNodes",
            "RefreshEditors",
        )
        for call in forbidden_calls:
            with self.subTest(call=call):
                self.assertNotRegex(self.all_source, rf"\b{call}\s*\(")
        for forbidden in (
            "NamedPipe",
            "CreateNamedPipe",
            "FSocket",
            "ISocketSubsystem",
            "FHttpModule",
            "CreateProc",
            "ExecProcess",
            "ScreenShot",
            "ReadPixels",
            "ctypes",
            "GetDllExport",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.all_source)

    def test_selection_is_explicitly_capability_gated_for_this_build(self) -> None:
        self.assertIn("UNSUPPORTED_BY_DEVKIT_BUILD", self.bridge)
        self.assertNotIn("READ_SELECTION", self.bridge)
        for required in (
            "READ_ACTIVE_ASSET",
            "READ_ACTIVE_GRAPH",
            "READ_GRAPH_POSITIONS",
            "READ_DIRTY_STATE",
            "READ_COMPILE_STATE",
        ):
            self.assertIn(required, self.bridge)


if __name__ == "__main__":
    unittest.main()
