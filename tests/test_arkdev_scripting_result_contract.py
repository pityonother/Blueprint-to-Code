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

from arkdev_scripting_probe.contracts import (  # noqa: E402
    PROBE_SCHEMA,
    SNAPSHOT_SCHEMA,
    assert_path_free,
    attach_semantic_digest,
)
from arkdev_scripting_probe.in_editor.arkdev_official_scripting_probe import (  # noqa: E402
    build_probe_result,
)
from arkdev_scripting_probe.result_reader import (  # noqa: E402
    validate_probe_result,
    validator_summary,
)


class _AssetEditorSubsystem:
    mutation_calls: list[str] = []

    def get_all_edited_assets(self) -> list[object]:
        return [object()]

    def open_editor_for_assets(self, _assets: list[object]) -> bool:
        self.mutation_calls.append("open_editor_for_assets")
        raise AssertionError("mutation API was called")


class _BlueprintEditorLibrary:
    mutation_calls: list[str] = []

    @classmethod
    def get_blueprint_asset(cls, value: object) -> object:
        return value

    @classmethod
    def find_graph(cls, _blueprint: object, _name: str) -> None:
        return None

    @classmethod
    def compile_blueprint(cls, _blueprint: object) -> None:
        cls.mutation_calls.append("compile_blueprint")
        raise AssertionError("mutation API was called")


class _EditorAssetLibrary:
    mutation_calls: list[str] = []

    @classmethod
    def load_asset(cls, _object_path: str) -> None:
        return None

    @classmethod
    def save_asset(cls, _object_path: str) -> bool:
        cls.mutation_calls.append("save_asset")
        raise AssertionError("mutation API was called")


class _EditorUtilitySubsystem:
    def release_instance_of_asset(self, _asset: object) -> None:
        raise AssertionError("mutation API was called")


class _SystemLibrary:
    @classmethod
    def get_engine_version(cls) -> str:
        return "5.5.4-0+UE5"


class _FakeUnreal:
    AssetEditorSubsystem = _AssetEditorSubsystem
    BlueprintEditorLibrary = _BlueprintEditorLibrary
    EditorAssetLibrary = _EditorAssetLibrary
    EditorUtilitySubsystem = _EditorUtilitySubsystem
    EditorUtilityBlueprint = type("EditorUtilityBlueprint", (), {})
    EditorUtilityWidgetBlueprint = type("EditorUtilityWidgetBlueprint", (), {})
    EditorPythonScripting = type("EditorPythonScripting", (), {})
    SystemLibrary = _SystemLibrary

    @staticmethod
    def get_editor_subsystem(_class: type[object]) -> _AssetEditorSubsystem:
        return _AssetEditorSubsystem()


class ProbeResultContractTests(unittest.TestCase):
    def setUp(self) -> None:
        _AssetEditorSubsystem.mutation_calls.clear()
        _BlueprintEditorLibrary.mutation_calls.clear()
        _EditorAssetLibrary.mutation_calls.clear()

    def test_runtime_introspection_records_mutation_without_calling_it(self) -> None:
        result = build_probe_result(
            _FakeUnreal,
            request=None,
            generated_at="2026-08-11T00:00:00Z",
            python_version="3.11.8",
        )

        self.assertEqual(result["schema"], PROBE_SCHEMA)
        self.assertTrue(result["python"]["available"])
        self.assertTrue(result["python"]["unrealImport"])
        self.assertEqual(result["classes"]["AssetEditorSubsystem"], "AVAILABLE")
        self.assertEqual(result["classes"]["EditorUtilityLibrary"], "MISSING")
        self.assertEqual(
            result["activeEditor"]["openEditedAssets"], "AVAILABLE"
        )
        self.assertEqual(result["activeEditor"]["activeAsset"], "MISSING")
        self.assertEqual(result["activeEditor"]["focusedGraph"], "MISSING")
        observed = {
            (item["owner"], item["method"], item["status"])
            for item in result["mutationApisObserved"]
        }
        self.assertIn(
            (
                "BlueprintEditorLibrary",
                "compile_blueprint",
                "PRESENT_BUT_NOT_USED",
            ),
            observed,
        )
        self.assertIn(
            (
                "EditorAssetLibrary",
                "save_asset",
                "PRESENT_BUT_NOT_USED",
            ),
            observed,
        )
        self.assertEqual(_AssetEditorSubsystem.mutation_calls, [])
        self.assertEqual(_BlueprintEditorLibrary.mutation_calls, [])
        self.assertEqual(_EditorAssetLibrary.mutation_calls, [])
        validate_probe_result(result)
        assert_path_free(result)

    def test_missing_unreal_is_unavailable_not_a_false_pass(self) -> None:
        result = build_probe_result(
            None,
            request=None,
            generated_at="2026-08-11T00:00:00Z",
            python_version="3.11.8",
        )

        self.assertTrue(result["python"]["available"])
        self.assertFalse(result["python"]["unrealImport"])
        self.assertEqual(result["classes"]["AssetEditorSubsystem"], "NOT_TESTED")
        self.assertIn("UNREAL_IMPORT_FAILED", result["gaps"])

    def test_semantic_digest_ignores_generation_time(self) -> None:
        first = build_probe_result(
            _FakeUnreal,
            request=None,
            generated_at="2026-08-11T00:00:00Z",
            python_version="3.11.8",
        )
        second = build_probe_result(
            _FakeUnreal,
            request=None,
            generated_at="2026-08-11T00:01:00Z",
            python_version="3.11.8",
        )

        self.assertNotEqual(first["generatedAt"], second["generatedAt"])
        self.assertEqual(first["semanticDigest"], second["semanticDigest"])

    def test_validator_summary_is_path_free_and_conservative(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            probe = build_probe_result(
                _FakeUnreal,
                request=None,
                generated_at="2026-08-11T00:00:00Z",
                python_version="3.11.8",
            )
            path = root / "live-probe.json"
            path.write_text(json.dumps(probe), encoding="utf-8")

            summary = validator_summary(path, root / "missing-snapshot.json")

        self.assertEqual(summary["PYTHON_RUNTIME"], "PASS")
        self.assertEqual(summary["UNREAL_IMPORT"], "PASS")
        self.assertEqual(summary["EXPLICIT_GRAPH_SNAPSHOT"], "NOT_TESTED")
        self.assertEqual(summary["ACTIVE_ASSET_READ"], "UNAVAILABLE")
        assert_path_free(summary)

    def test_validator_rejects_snapshot_from_a_different_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            probe = build_probe_result(
                _FakeUnreal,
                request=None,
                generated_at="2026-08-11T00:00:00Z",
                python_version="3.11.8",
            )
            capabilities = {
                "explicitAssetLoad": "AVAILABLE",
                "blueprintObject": "AVAILABLE",
                "explicitGraphFind": "AVAILABLE",
                "graphNodeEnumeration": "AVAILABLE",
                "nodeGuidRead": "AVAILABLE",
                "nodePositionRead": "AVAILABLE",
            }
            probe["explicitTarget"] = {
                "status": "AVAILABLE",
                "objectPath": "/Game/Test/BP_Current.BP_Current",
                "graphName": "EventGraph",
                "capabilities": capabilities,
                "gaps": [],
            }
            attach_semantic_digest(probe)
            snapshot = {
                "schema": SNAPSHOT_SCHEMA,
                "objectPath": "/Game/Test/BP_Stale.BP_Stale",
                "graphName": "EventGraph",
                "graphPath": (
                    "/Game/Test/BP_Stale.BP_Stale:EventGraph"
                ),
                "graphClass": "EdGraph",
                "nodeCount": 0,
                "nodesReturned": 0,
                "nodesOmitted": 0,
                "nodes": [],
                "capabilities": capabilities,
                "gaps": [],
            }
            attach_semantic_digest(snapshot)
            probe_path = root / "live-probe.json"
            snapshot_path = root / "explicit-graph-snapshot.json"
            probe_path.write_text(json.dumps(probe), encoding="utf-8")
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

            summary = validator_summary(probe_path, snapshot_path)

        self.assertEqual(summary["EXPLICIT_GRAPH_SNAPSHOT"], "ERROR")


if __name__ == "__main__":
    unittest.main()
