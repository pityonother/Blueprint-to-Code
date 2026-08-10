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
    def test_tool_allowlist_preserves_phase_one_and_adds_exact_phase_two_surface(self) -> None:
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

    def test_path_free_guard_rejects_windows_and_posix_paths_recursively(self) -> None:
        assert_path_free({"asset": "/Game/Test/Fixture.Fixture"})
        separator = chr(92)
        for private_path in (
            "C:" + separator + separator.join(("Users", "fixture", "evidence.sqlite")),
            "/" + "home/fixture/evidence.sqlite",
            "failure at /" + "tmp/fixture/evidence.sqlite",
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
        self.assertEqual(
            bridge.get_state(include_selection=True),
            {
                "schema": "blueprint-to-code.arkdev-editor-state/v1",
                "connected": False,
                "bridgeVersion": "",
                "devkitBuild": "",
                "activeAsset": None,
                "activeGraph": None,
                "selectedNodes": [],
                "dirty": None,
                "compileStatus": "UNKNOWN",
                "capabilities": [],
                "reasonCode": "EDITOR_BRIDGE_NOT_INSTALLED",
            },
        )

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


if __name__ == "__main__":
    unittest.main()
