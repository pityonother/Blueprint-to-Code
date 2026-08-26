from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures" / "arkdev_editor_bridge"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.contracts import assert_path_free  # noqa: E402
from arkdev_mcp.editor_bridge_file import (  # noqa: E402
    MAX_SNAPSHOT_BYTES,
    FileEditorBridge,
)


class FileEditorBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.state_file = self.root / "private" / "editor_state.json"
        self.now = datetime(2026, 8, 11, 0, 0, tzinfo=timezone.utc)

    def payload(self, name: str = "editor_state.connected.json") -> dict[str, object]:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def write(
        self,
        payload: dict[str, object],
        *,
        written_at: datetime | None = None,
    ) -> bytes:
        candidate = copy.deepcopy(payload)
        moment = written_at or self.now
        candidate["writtenAtUtc"] = moment.isoformat().replace("+00:00", "Z")
        raw = json.dumps(
            candidate,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_bytes(raw)
        return raw

    def bridge(self, **overrides: object) -> FileEditorBridge:
        return FileEditorBridge(
            self.state_file,
            clock=lambda: self.now,
            **overrides,
        )

    def test_missing_file_is_a_normal_disconnected_state(self) -> None:
        bridge = self.bridge()

        self.assertEqual(
            bridge.health(),
            {
                "connected": False,
                "status": "DISCONNECTED",
                "stateStatus": "STATE_NOT_FOUND",
                "reasonCode": "EDITOR_BRIDGE_STATE_NOT_FOUND",
            },
        )
        state = bridge.get_state(
            include_selection=True,
            include_graph_nodes=True,
            max_graph_nodes=200,
        )
        self.assertFalse(state["connected"])
        self.assertEqual(state["reasonCode"], "EDITOR_BRIDGE_STATE_NOT_FOUND")
        self.assertEqual(state["graphNodes"], [])

    def test_fresh_file_is_connected_and_bounds_graph_nodes_per_call(self) -> None:
        self.write(self.payload())
        bridge = self.bridge()

        state = bridge.get_state(
            include_selection=True,
            include_graph_nodes=True,
            max_graph_nodes=2,
        )

        self.assertTrue(state["connected"])
        self.assertEqual(state["schema"], "blueprint-to-code.arkdev-editor-state/v1")
        self.assertEqual(state["activeAsset"], "/Game/Test/InterpretationFixture.InterpretationFixture")
        self.assertEqual(state["activeGraph"], "/Game/Test/InterpretationFixture.InterpretationFixture:EventGraph")
        self.assertEqual(state["graphStatus"], "FOCUSED_GRAPH")
        self.assertTrue(state["dirty"])
        self.assertEqual(state["compileStatus"], "DIRTY")
        self.assertEqual(len(state["graphNodes"]), 2)
        self.assertEqual(
            state["graphNodeSummary"],
            {
                "returned": 2,
                "total": 4,
                "omitted": 2,
                "snapshotTruncated": False,
            },
        )
        self.assertEqual(bridge.health()["stateStatus"], "CONNECTED")
        self.assertEqual(
            bridge.get_capabilities(),
            (
                "READ_ACTIVE_ASSET",
                "READ_ACTIVE_GRAPH",
                "READ_COMPILE_STATE",
                "READ_DIRTY_STATE",
                "READ_GRAPH_POSITIONS",
            ),
        )

    def test_lightweight_state_omits_nodes_and_not_requested_selection(self) -> None:
        self.write(self.payload())

        state = self.bridge().get_state(
            include_selection=False,
            include_graph_nodes=False,
            max_graph_nodes=200,
        )

        self.assertEqual(state["selectionStatus"], "NOT_REQUESTED")
        self.assertEqual(state["graphStatus"], "FOCUSED_GRAPH")
        self.assertEqual(state["selectedNodes"], [])
        self.assertEqual(state["graphNodes"], [])
        self.assertEqual(state["graphNodeSummary"]["total"], 4)
        self.assertEqual(state["graphNodeSummary"]["omitted"], 4)

    def test_stale_file_fails_closed_without_protocol_error(self) -> None:
        self.write(self.payload(), written_at=self.now - timedelta(seconds=7))

        state = self.bridge().get_state(
            include_selection=True,
            include_graph_nodes=True,
            max_graph_nodes=200,
        )

        self.assertFalse(state["connected"])
        self.assertEqual(state["reasonCode"], "EDITOR_BRIDGE_STATE_STALE")
        self.assertEqual(self.bridge().health()["stateStatus"], "STATE_STALE")

    def test_invalid_json_and_wrong_schema_fail_closed(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text("{not-json", encoding="utf-8")
        invalid = self.bridge().get_state(
            include_selection=True,
            include_graph_nodes=False,
            max_graph_nodes=200,
        )
        self.assertEqual(invalid["reasonCode"], "EDITOR_BRIDGE_STATE_INVALID")

        wrong = self.payload()
        wrong["schema"] = "wrong/v1"
        self.write(wrong)
        state = self.bridge().get_state(
            include_selection=True,
            include_graph_nodes=False,
            max_graph_nodes=200,
        )
        self.assertEqual(state["reasonCode"], "EDITOR_BRIDGE_STATE_INVALID")

        inconsistent_graph = self.payload()
        inconsistent_graph["graphStatus"] = "NO_FOCUSED_GRAPH"
        self.write(inconsistent_graph)
        state = self.bridge().get_state(
            include_selection=True,
            include_graph_nodes=False,
            max_graph_nodes=200,
        )
        self.assertEqual(state["reasonCode"], "EDITOR_BRIDGE_STATE_INVALID")

        invalid_node = self.payload()
        invalid_node["focusedGraph"]["nodes"][0]["x"] = "not-an-integer"
        self.write(invalid_node)
        self.assertEqual(self.bridge().health()["stateStatus"], "STATE_INVALID")

    def test_zero_node_guid_is_rejected_as_invalid_snapshot(self) -> None:
        payload = self.payload()
        payload["focusedGraph"]["nodes"][0]["nodeGuid"] = "0" * 32
        self.write(payload)

        state = self.bridge().get_state(
            include_selection=True,
            include_graph_nodes=True,
            max_graph_nodes=200,
        )

        self.assertFalse(state["connected"])
        self.assertEqual(state["reasonCode"], "EDITOR_BRIDGE_STATE_INVALID")
        self.assertEqual(state["graphNodes"], [])

    def test_oversized_file_is_rejected_before_json_decode(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_bytes(b"{" + b" " * MAX_SNAPSHOT_BYTES)

        state = self.bridge().get_state(
            include_selection=True,
            include_graph_nodes=False,
            max_graph_nodes=200,
        )

        self.assertEqual(state["reasonCode"], "EDITOR_BRIDGE_STATE_OVERSIZED")
        self.assertEqual(self.bridge().health()["stateStatus"], "STATE_OVERSIZED")

    def test_mutation_or_unknown_capability_is_rejected(self) -> None:
        for capability in ("CREATE_NODE", "READ_PRIVATE_EDITOR_MEMORY"):
            with self.subTest(capability=capability):
                payload = self.payload()
                payload["capabilities"] = [*payload["capabilities"], capability]
                self.write(payload)

                state = self.bridge().get_state(
                    include_selection=True,
                    include_graph_nodes=False,
                    max_graph_nodes=200,
                )

                self.assertEqual(state["reasonCode"], "EDITOR_BRIDGE_STATE_INVALID")

    def test_future_timestamp_beyond_clock_skew_is_invalid(self) -> None:
        self.write(self.payload(), written_at=self.now + timedelta(seconds=3))

        state = self.bridge().get_state(
            include_selection=True,
            include_graph_nodes=False,
            max_graph_nodes=200,
        )

        self.assertEqual(state["reasonCode"], "EDITOR_BRIDGE_STATE_INVALID")

    def test_semantic_digest_ignores_heartbeat_fields_but_raw_sha_does_not(self) -> None:
        first = self.payload()
        first["sequence"] = 10
        self.write(first, written_at=self.now)
        first_state = self.bridge().get_state(
            include_selection=True,
            include_graph_nodes=False,
            max_graph_nodes=200,
        )

        second = self.payload()
        second["sequence"] = 11
        self.write(second, written_at=self.now + timedelta(seconds=1))
        second_state = FileEditorBridge(
            self.state_file,
            clock=lambda: self.now + timedelta(seconds=1),
        ).get_state(
            include_selection=True,
            include_graph_nodes=False,
            max_graph_nodes=200,
        )

        self.assertNotEqual(
            first_state["snapshot"]["rawSha256"],
            second_state["snapshot"]["rawSha256"],
        )
        self.assertEqual(
            first_state["snapshot"]["semanticDigest"],
            second_state["snapshot"]["semanticDigest"],
        )

    def test_public_state_never_exposes_the_private_state_file_path(self) -> None:
        self.write(self.payload())

        state = self.bridge().get_state(
            include_selection=True,
            include_graph_nodes=True,
            max_graph_nodes=200,
        )

        encoded = json.dumps(state, ensure_ascii=False)
        self.assertNotIn(str(self.root), encoded)
        assert_path_free(state)

    def test_plugin_reported_disconnect_and_age_bounds_are_stable(self) -> None:
        payload = self.payload("editor_state.idle.json")
        payload["connected"] = False
        payload["reasonCode"] = "PLUGIN_SHUTDOWN"
        self.write(payload)

        state = self.bridge().get_state(
            include_selection=True,
            include_graph_nodes=False,
            max_graph_nodes=200,
        )
        self.assertEqual(
            state["reasonCode"], "EDITOR_BRIDGE_PLUGIN_DISCONNECTED"
        )
        self.assertEqual(state["graphStatus"], "NO_ACTIVE_BLUEPRINT")
        for maximum_age in (0.99, 30.01):
            with self.subTest(maximum_age=maximum_age):
                with self.assertRaises(ValueError):
                    FileEditorBridge(self.state_file, max_age_sec=maximum_age)

    def test_validator_prints_only_a_path_free_summary(self) -> None:
        self.write(self.payload(), written_at=datetime.now(timezone.utc))

        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "validate_arkdev_editor_bridge_snapshot.py"),
                "--state-file",
                str(self.state_file),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )

        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("EDITOR_BRIDGE_STATE_FRESH=true", process.stdout)
        self.assertIn("EDITOR_BRIDGE_CONNECTED=true", process.stdout)
        self.assertNotIn(str(self.root), process.stdout)


if __name__ == "__main__":
    unittest.main()
