from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "arkdev_editor_bridge"
    / "editor_state.connected.json"
)
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.blueprint_service import BlueprintService  # noqa: E402
from arkdev_mcp.editor_binding import EditorBindingService  # noqa: E402
from arkdev_mcp.editor_bridge_file import FileEditorBridge  # noqa: E402
from arkdev_mcp.tasking.store import TaskStore  # noqa: E402
from arkdev_mcp.tasking.task_service import TaskService  # noqa: E402
from blueprint_translator.interpretation_publication import (  # noqa: E402
    publish_interpretation,
)
from interpretation_fixture import (  # noqa: E402
    interpretation_payload,
    publish_interpretation_fixture,
)


EXACT_GUID = "11111111111111111111111111111111"
DUPLICATE_GUID = "22222222222222222222222222222222"
MISSING_GUID = "33333333333333333333333333333333"
NO_HEURISTIC_GUID = "44444444444444444444444444444444"


class EditorBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.capture_root = self.root / "captures"
        self.now = datetime(2026, 8, 11, 0, 0, tzinfo=timezone.utc)

        payload = interpretation_payload()
        nodes = payload["graphs"][0]["payload"]["nodes"]
        nodes[0].update({"node_guid": EXACT_GUID, "x": 0, "y": 0})
        nodes[1].update({"node_guid": DUPLICATE_GUID, "x": 100, "y": 0})
        nodes[2].update({"node_guid": DUPLICATE_GUID, "x": 0, "y": 100})
        nodes[3].update({"x": 100, "y": 100})
        self.asset_dir, self.source_path, _ = publish_interpretation_fixture(
            self.capture_root,
            payload=payload,
        )
        publish_interpretation(self.asset_dir, budget=32_000)

        self.blueprint = BlueprintService(self.capture_root)
        self.store = TaskStore(self.root / ".blueprint-tasks")
        self.tasks = TaskService(self.blueprint, self.store)
        self.binding = EditorBindingService(self.blueprint, self.tasks)

        snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))
        snapshot["writtenAtUtc"] = self.now.isoformat().replace("+00:00", "Z")
        self.state_file = self.root / "editor_state.json"
        self.state_file.write_text(
            json.dumps(snapshot, ensure_ascii=False),
            encoding="utf-8",
        )
        self.bridge = FileEditorBridge(
            self.state_file,
            clock=lambda: self.now,
        )

    def live_state(self) -> dict[str, object]:
        return self.bridge.get_state(
            include_selection=True,
            include_graph_nodes=True,
            max_graph_nodes=1000,
        )

    def enrich(
        self,
        *,
        binding: EditorBindingService | None = None,
        state: dict[str, object] | None = None,
        task_id: str = "",
    ) -> dict[str, object]:
        return (binding or self.binding).enrich(
            state or self.live_state(),
            task_id=task_id,
        )

    def create_task(self, *, asset: str = "InterpretationFixture", graph: str = "EventGraph") -> dict[str, object]:
        authority = self.blueprint.get_task_authority(asset=asset)
        graph_ref = next(
            item["ref"]
            for item in authority["graphTargets"]
            if item["name"] == graph
        )
        return self.tasks.create(
            mode="BLUEPRINT_DESIGN",
            asset=asset,
            goal="Verify exact live editor binding",
            completion_criteria=["Live Editor state matches exact Evidence"],
            allowed_changes=["Read-only inspection"],
            forbidden_changes=["ARK DevKit mutation"],
            graph_ref=graph_ref,
            supporting_assets=[],
        )

    def test_exact_asset_graph_and_node_guid_bindings_are_explicit(self) -> None:
        state = self.enrich()

        self.assertEqual(state["activeAssetBinding"]["status"], "EXACT")
        self.assertEqual(state["activeGraphBinding"]["status"], "EXACT")
        self.assertTrue(state["activeGraphBinding"]["graphRef"].startswith("bp://"))
        by_guid = {node["nodeGuid"]: node for node in state["graphNodes"]}
        self.assertEqual(by_guid[EXACT_GUID]["bindingStatus"], "EXACT")
        self.assertTrue(by_guid[EXACT_GUID]["evidenceNodeRef"].startswith("bp://"))
        self.assertEqual(by_guid[DUPLICATE_GUID]["bindingStatus"], "AMBIGUOUS")
        self.assertEqual(by_guid[DUPLICATE_GUID]["evidenceNodeRef"], "")
        self.assertEqual(by_guid[MISSING_GUID]["bindingStatus"], "UNBOUND")
        self.assertEqual(by_guid[NO_HEURISTIC_GUID]["bindingStatus"], "UNBOUND")

    def test_name_class_and_position_never_promote_a_missing_guid(self) -> None:
        state = self.enrich()
        node = next(
            item for item in state["graphNodes"] if item["nodeGuid"] == NO_HEURISTIC_GUID
        )

        self.assertEqual(node["name"], "MacroCall")
        self.assertEqual((node["x"], node["y"]), (100, 100))
        self.assertEqual(node["bindingStatus"], "UNBOUND")
        self.assertEqual(node["evidenceNodeRef"], "")

    def test_duplicate_live_node_guids_are_ambiguous(self) -> None:
        state = self.live_state()
        duplicate = copy.deepcopy(state["graphNodes"][0])
        duplicate["name"] = "DuplicateLiveGuid"
        state["graphNodes"].append(duplicate)

        bound = self.enrich(state=state)
        matches = [
            node for node in bound["graphNodes"] if node["nodeGuid"] == EXACT_GUID
        ]

        self.assertEqual(len(matches), 2)
        self.assertTrue(
            all(node["bindingStatus"] == "AMBIGUOUS" for node in matches)
        )
        self.assertTrue(all(node["evidenceNodeRef"] == "" for node in matches))

    def test_asset_not_found_does_not_guess_from_filename(self) -> None:
        state = self.live_state()
        state["activeAsset"] = "/Game/Other/InterpretationFixture.InterpretationFixture"
        state["activeAssetDetails"]["objectPath"] = state["activeAsset"]

        bound = self.enrich(state=state)

        self.assertEqual(bound["activeAssetBinding"]["status"], "NOT_FOUND")
        self.assertEqual(bound["activeGraphBinding"]["status"], "NOT_FOUND")

    def test_duplicate_exact_object_paths_are_ambiguous(self) -> None:
        duplicate = copy.deepcopy(interpretation_payload())
        duplicate["asset_name"] = "InterpretationFixtureCopy"
        duplicate["asset_path"] = "/Game/Test/InterpretationFixture.InterpretationFixture"
        copy_dir, _source, _ = publish_interpretation_fixture(
            self.capture_root,
            name="InterpretationFixtureCopy",
            payload=duplicate,
        )
        publish_interpretation(copy_dir, budget=32_000)

        bound = self.enrich()

        self.assertEqual(bound["activeAssetBinding"]["status"], "AMBIGUOUS")
        self.assertEqual(bound["activeGraphBinding"]["status"], "NOT_FOUND")

    def test_duplicate_graph_names_are_ambiguous(self) -> None:
        root = self.root / "ambiguous"
        capture_root = root / "captures"
        payload = interpretation_payload()
        duplicate_graph = copy.deepcopy(payload["graphs"][1])
        duplicate_graph["graph"] = "EventGraph"
        duplicate_graph["export_index"] = 88
        duplicate_graph["payload"]["metadata"]["graph_name"] = "EventGraph"
        duplicate_graph["payload"]["metadata"]["uasset_export_index"] = 88
        payload["graphs"].append(duplicate_graph)
        asset_dir, _source, _ = publish_interpretation_fixture(
            capture_root,
            payload=payload,
        )
        publish_interpretation(asset_dir, budget=32_000)
        blueprint = BlueprintService(capture_root)
        tasks = TaskService(blueprint, TaskStore(root / ".blueprint-tasks"))

        bound = self.enrich(binding=EditorBindingService(blueprint, tasks))

        self.assertEqual(bound["activeAssetBinding"]["status"], "EXACT")
        self.assertEqual(bound["activeGraphBinding"]["status"], "AMBIGUOUS")
        self.assertTrue(
            all(node["bindingStatus"] == "UNBOUND" for node in bound["graphNodes"])
        )

    def test_stale_evidence_never_produces_an_exact_binding(self) -> None:
        self.source_path.write_bytes(b"changed-after-evidence-publication")

        bound = self.enrich()

        self.assertEqual(bound["activeAssetBinding"]["status"], "EVIDENCE_STALE")
        self.assertNotEqual(bound["activeGraphBinding"]["status"], "EXACT")
        self.assertTrue(
            all(node["bindingStatus"] == "UNBOUND" for node in bound["graphNodes"])
        )

    def test_exact_task_binding_is_read_only_and_never_mutation_ready(self) -> None:
        task = self.create_task()
        task_root = self.store.root / task["taskId"].removeprefix("task://")
        before = {
            path.relative_to(task_root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in task_root.rglob("*")
            if path.is_file()
        }

        bound = self.enrich(task_id=task["taskId"])

        after = {
            path.relative_to(task_root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in task_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(bound["taskBinding"]["status"], "MATCHED")
        self.assertTrue(bound["taskBinding"]["assetMatch"])
        self.assertTrue(bound["taskBinding"]["graphMatch"])
        self.assertTrue(bound["taskBinding"]["evidenceMatch"])
        self.assertFalse(bound["taskBinding"]["mutationReady"])
        self.assertEqual(before, after)

    def test_task_asset_and_graph_mismatch_statuses_are_distinct(self) -> None:
        other_dir, _source, _ = publish_interpretation_fixture(
            self.capture_root,
            name="OtherFixture",
        )
        publish_interpretation(other_dir, budget=32_000)
        asset_task = self.create_task(asset="OtherFixture")
        graph_task = self.create_task(graph="LocalHelper")

        asset_bound = self.enrich(task_id=asset_task["taskId"])
        graph_bound = self.enrich(task_id=graph_task["taskId"])

        self.assertEqual(asset_bound["taskBinding"]["status"], "ASSET_MISMATCH")
        self.assertEqual(graph_bound["taskBinding"]["status"], "GRAPH_MISMATCH")
        self.assertFalse(asset_bound["taskBinding"]["mutationReady"])
        self.assertFalse(graph_bound["taskBinding"]["mutationReady"])

    def test_task_not_found_and_blocked_are_fail_closed(self) -> None:
        missing = self.enrich(task_id="task://" + "f" * 32)
        self.assertEqual(missing["taskBinding"]["status"], "TASK_NOT_FOUND")

        task = self.create_task()
        context = self.store.load_context(task["taskId"])
        session = self.store.load_session(task["taskId"])
        session["phase"] = "BLOCKED"
        session["reasonCode"] = "TEST_BLOCK"
        self.store.save_task(task["taskId"], context, session)
        blocked = self.enrich(task_id=task["taskId"])
        self.assertEqual(blocked["taskBinding"]["status"], "TASK_BLOCKED")
        self.assertFalse(blocked["taskBinding"]["mutationReady"])

    def test_editor_idle_has_no_task_or_evidence_match(self) -> None:
        task = self.create_task()
        idle = json.loads(
            (
                ROOT
                / "tests"
                / "fixtures"
                / "arkdev_editor_bridge"
                / "editor_state.idle.json"
            ).read_text(encoding="utf-8")
        )
        idle["writtenAtUtc"] = self.now.isoformat().replace("+00:00", "Z")
        self.state_file.write_text(json.dumps(idle), encoding="utf-8")
        state = self.bridge.get_state(
            include_selection=True,
            include_graph_nodes=True,
            max_graph_nodes=200,
        )

        bound = self.enrich(state=state, task_id=task["taskId"])

        self.assertEqual(bound["taskBinding"]["status"], "EDITOR_IDLE")
        self.assertFalse(bound["taskBinding"]["assetMatch"])
        self.assertFalse(bound["taskBinding"]["graphMatch"])
        self.assertFalse(bound["taskBinding"]["evidenceMatch"])
        self.assertFalse(bound["taskBinding"]["mutationReady"])


if __name__ == "__main__":
    unittest.main()
