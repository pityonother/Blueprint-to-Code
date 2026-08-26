from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.contracts import (  # noqa: E402
    ERROR_CODES,
    ERROR_SCHEMA,
    TOOL_NAMES,
    McpExecutionError,
    assert_path_free,
)
from arkdev_mcp.editor_bridge import (  # noqa: E402
    MUTATION_CAPABILITIES,
    DisconnectedEditorBridge,
    EditorCapability,
    FixtureEditorBridge,
)


class ArkdevMcpContractTests(unittest.TestCase):
    def test_tool_allowlist_preserves_phase_one_and_adds_exact_phase_two_surface(
        self,
    ) -> None:
        self.assertEqual(
            TOOL_NAMES,
            (
                "arkdev_status",
                "arkdev_editor_state",
                "blueprint_list_assets",
                "blueprint_get_context",
                "blueprint_get_node",
                "blueprint_task_create",
                "blueprint_task_resume",
                "blueprint_task_research",
                "blueprint_patch_plan_draft",
                "blueprint_patch_plan_validate",
                "blueprint_patch_plan_confirm",
                "blueprint_solver_create",
                "blueprint_solver_resume",
                "blueprint_solver_preflight",
                "blueprint_solver_update",
                "blueprint_solver_materialize_task",
            ),
        )

    def test_error_contract_has_the_stable_phase_one_codes(self) -> None:
        self.assertEqual(ERROR_SCHEMA, "blueprint-to-code.arkdev-mcp-error/v1")
        self.assertEqual(
            ERROR_CODES,
            frozenset(
                {
                    "INVALID_ARGUMENT",
                    "ASSET_NOT_FOUND",
                    "EVIDENCE_NOT_FOUND",
                    "EVIDENCE_STALE",
                    "EVIDENCE_NOT_AUTHORITATIVE",
                    "EVIDENCE_REVISION_MISMATCH",
                    "GRAPH_SELECTION_REQUIRED",
                    "NODE_NOT_FOUND",
                    "RESULT_BUDGET_EXCEEDED",
                    "EDITOR_BRIDGE_NOT_INSTALLED",
                    "EDITOR_BRIDGE_UNAVAILABLE",
                    "TASK_NOT_FOUND",
                    "TASK_PHASE_INVALID",
                    "TASK_BLOCKED",
                    "TASK_SLICE_LIMIT_REACHED",
                    "EVIDENCE_REVISION_CHANGED",
                    "PATCH_PLAN_NOT_FOUND",
                    "PATCH_PLAN_INVALID",
                    "PATCH_PLAN_LIMIT_EXCEEDED",
                    "PATCH_PLAN_NOT_CONFIRMABLE",
                    "PATCH_PLAN_DIGEST_MISMATCH",
                    "PLAN_CONFIRMATION_REQUIRED",
                    "SOLVER_NOT_FOUND",
                    "REQUIREMENT_PROPOSAL_INVALID",
                    "REQUEST_TEXT_UNASSIGNED",
                    "SOLVER_PHASE_INVALID",
                    "SOLVER_UPDATE_INVALID",
                    "TARGET_SELECTION_REQUIRED",
                    "TARGET_CANDIDATE_NOT_FOUND",
                    "EVIDENCE_ACQUISITION_REQUIRED",
                    "TASK_NOT_APPLICABLE",
                    "SOLVER_LIMIT_EXCEEDED",
                    "INTERNAL_CONTRACT_ERROR",
                }
            ),
        )
        payload = McpExecutionError(
            "EVIDENCE_STALE",
            "Current Blueprint evidence is stale.",
            retryable=False,
            details={"asset": "Fixture"},
        ).as_payload()
        self.assertEqual(
            payload,
            {
                "schema": ERROR_SCHEMA,
                "code": "EVIDENCE_STALE",
                "message": "Current Blueprint evidence is stale.",
                "retryable": False,
                "details": {"asset": "Fixture"},
            },
        )

    def test_path_free_guard_accepts_only_trusted_unreal_object_path_fields(self) -> None:
        trusted_paths = (
            "/ASBExportGun/Weapons/Fixture.Fixture",
            "/DinoDefense/Camera/Fixture.Fixture",
            "/Engine/EngineMaterials/Fixture.Fixture",
            (
                "/Game/__ExternalActors__/Maps/Genesis/Genesis_WP/0/AA/"
                "PACKAGEHASH.PrimalCameraProbeActor_13"
            ),
            (
                "/Game/__ExternalActors__/Genesis/Mission_WP/0/AA/"
                "PACKAGEHASH.PrimalRecastNavMesh-Large"
            ),
            "/Game/Test/Fixture.Fixture",
            "/Game/Test/Fixture.Fixture_C",
            "/Game/Maps/Genesis/Genesis_WP.CameraComponent",
            "/PCG/Test/Fixture.Fixture",
            "/Plugin/Test/Fixture.Fixture",
            "/Plugins/Test/Fixture.Fixture",
            "/Script/Engine.Actor",
        )

        for object_path in trusted_paths:
            with self.subTest(object_path=object_path):
                assert_path_free({"assetObjectPath": object_path})
        assert_path_free({"supportingObjectPaths": list(trusted_paths)})
        assert_path_free({"activeAsset": "/Game/Test/Fixture.Fixture"})
        assert_path_free(
            {
                "activeGraph": "/Game/Test/Fixture.Fixture:EventGraph",
                "pathName": "/Game/Test/Fixture.Fixture:EventGraph",
            }
        )
        assert_path_free(
            {
                "evidenceRef": "bp://asset@revision/g/1",
                "documentation": "https://example.com/public/path",
            }
        )

    def test_path_free_guard_rejects_machine_paths_and_disguised_paths_recursively(
        self,
    ) -> None:
        separator = chr(92)
        posix_private = "/" + "/".join(("home", "ac", "private"))
        users_private = "/" + "/".join(("Users", "ac", "private"))
        volumes_asset = "/" + "/".join(("Volumes", "Secret", "Project", "Asset.Asset"))
        workspace_asset = "/" + "/".join(("workspace", "repo", "Secret.Secret"))
        local_file_uri = "file:" + "//localhost" + users_private + "/evidence.sqlite"
        percent = chr(37)
        encoded_slash = percent + "2F"
        encoded_colon = percent + "3A"
        encoded_backslash = percent + "5C"
        for private_value in (
            {"objectPath": posix_private + "/evidence.sqlite"},
            {"objectPath": users_private + "/private.private"},
            {"objectPath": volumes_asset},
            {"objectPath": workspace_asset},
            {"objectPath": "/C/Users/ac/Secret.Secret"},
            {"objectPath": "/Game/private/evidence.sqlite"},
            {"activeGraph": users_private + ":EventGraph"},
            {"pathName": users_private + ":EventGraph"},
            {"objectPath": "file://" + users_private + "/evidence.sqlite"},
            {"objectPath": "file:" + users_private + "/evidence.sqlite"},
            {"objectPath": local_file_uri},
            {"detail": "/Game/private/private.private"},
            {"detail": "file://" + users_private + "/evidence.sqlite"},
            {"detail": "file:relative/private.txt"},
            {"detail": "file:secret.txt"},
            {
                "detail": "file:"
                + encoded_slash * 3
                + encoded_slash.join(("Users", "ac", "private", "evidence.sqlite"))
            },
            {
                "detail": "file"
                + encoded_colon
                + encoded_slash * 3
                + "C"
                + encoded_colon
                + encoded_slash
                + encoded_slash.join(("Users", "ac", "private"))
            },
            {
                "detail": encoded_slash
                + encoded_slash.join(("Users", "ac", "private", "evidence.sqlite"))
            },
            {
                "detail": percent
                + "252F"
                + (percent + "252F").join(("home", "ac", "private"))
            },
            {
                "detail": encoded_backslash
                + encoded_backslash.join(
                    ("Users", "ac", "private", "evidence.sqlite")
                )
            },
            {"detail": separator + separator.join(("Users", "ac", "private"))},
            {"detail": "C:Users" + separator + "ac" + separator + "private"},
            {"detail": "source:" + posix_private},
            {
                "nested": [
                    "C:"
                    + separator
                    + separator.join(("Users", "fixture", "evidence.sqlite"))
                ]
            },
            {"nested": ["failure at /" + "tmp/fixture/evidence.sqlite"]},
            {
                "nested": [
                    "share "
                    + separator * 2
                    + separator.join(("server", "fixture", "evidence.sqlite"))
                ]
            },
        ):
            with self.subTest(private_value=private_value):
                with self.assertRaises(McpExecutionError) as raised:
                    assert_path_free(private_value)
                self.assertEqual(raised.exception.code, "INTERNAL_CONTRACT_ERROR")
                self.assertNotIn("private", json.dumps(raised.exception.as_payload()))

        for private_path in (
            "C:" + separator + separator.join(("Users", "fixture", "evidence.sqlite")),
            "/" + "home/fixture/evidence.sqlite",
            "failure at /" + "tmp/fixture/evidence.sqlite",
            "failure at /" + "tmp=secret",
            "路径/" + "home/fixture/evidence.sqlite",
            "路径/" + "custom/root/private.db",
            "路径/" + "custom=secret",
            "路径/" + "秘密/private.db",
            "路径/" + ".cache/private.db",
            "路径/秘密=token/private.db",
            "路径/秘密=token>1/private.db",
            "路径/秘密=K如果>1/private.db",
            "路径/秘密=K如果>1.private",
            "路径/秘密=K如果>1",
            "工作目录/秘密文件=K如果>1",
            "生物体重/死神体重=K如果>1",
            "/生物体重/死神体重=K如果>1",
            "生物体重/死神体重=K如果>1/etc/passwd",
            "生物体重/死神体重=K如果>1\n/etc/passwd",
            "/" + "var/tmp/fixture/evidence.sqlite",
            "/" + "root/fixture/evidence.sqlite",
            "/" + "mnt/c/workspace/evidence.sqlite",
            "/" + "workspace/repository/evidence.sqlite",
            "open file" + "://fixture/evidence.sqlite",
            "share "
            + separator * 2
            + separator.join(("server", "fixture", "evidence.sqlite")),
        ):
            with self.subTest(private_path=private_path):
                with self.assertRaises(McpExecutionError) as raised:
                    assert_path_free({"nested": [private_path]})
                self.assertEqual(raised.exception.code, "INTERNAL_CONTRACT_ERROR")
                self.assertNotIn(
                    private_path,
                    json.dumps(raised.exception.as_payload(), ensure_ascii=False),
                )
        with self.assertRaises(McpExecutionError):
            assert_path_free({"path": Path("private/evidence.sqlite")})
        with self.assertRaises(McpExecutionError):
            assert_path_free({Path("private-key"): "value"})

    def test_phase_two_json_schemas_are_valid_and_use_exact_contract_ids(self) -> None:
        expected = {
            "blueprint_task_context.v1.schema.json": "blueprint-to-code.task-context/v1",
            "blueprint_task_session.v1.schema.json": "blueprint-to-code.task-session/v1",
            "blueprint_graph_slice.v1.schema.json": "blueprint-to-code.graph-slice/v1",
            "blueprint_patch_plan.v1.schema.json": "blueprint-to-code.blueprint-patch-plan/v1",
        }
        for name, schema_id in expected.items():
            with self.subTest(schema=name):
                payload = json.loads(
                    (ROOT / "schemas" / name).read_text(encoding="utf-8")
                )
                Draft202012Validator.check_schema(payload)
                self.assertEqual(payload["$id"], schema_id)


class ArkdevEditorBridgeContractTests(unittest.TestCase):
    def test_disconnected_bridge_reports_protocol_only_state(self) -> None:
        bridge = DisconnectedEditorBridge()
        self.assertEqual(bridge.health()["status"], "DISCONNECTED")
        self.assertEqual(bridge.get_capabilities(), ())
        state = bridge.get_state(include_selection=True)
        self.assertEqual(state["schema"], "blueprint-to-code.arkdev-editor-state/v1")
        self.assertFalse(state["connected"])
        self.assertEqual(state["reasonCode"], "EDITOR_BRIDGE_NOT_INSTALLED")
        self.assertEqual(state["graphStatus"], "DISCONNECTED")
        self.assertEqual(state["graphNodes"], [])
        self.assertEqual(state["taskBinding"], {})

    def test_fixture_bridge_never_advertises_mutation_capabilities(self) -> None:
        bridge = FixtureEditorBridge(
            active_asset="/Game/Test/Fixture.Fixture",
            active_graph="EventGraph",
            selected_nodes=("bp://asset@revision/g/7/n/1",),
            capabilities=(
                EditorCapability.READ_ACTIVE_ASSET,
                EditorCapability.READ_ACTIVE_GRAPH,
                EditorCapability.READ_SELECTION,
            ),
        )
        advertised = set(bridge.get_capabilities())
        self.assertTrue(bridge.health()["connected"])
        self.assertFalse(advertised & MUTATION_CAPABILITIES)
        self.assertEqual(
            bridge.get_state(include_selection=True)["graphStatus"],
            "FOCUSED_GRAPH",
        )


if __name__ == "__main__":
    unittest.main()
