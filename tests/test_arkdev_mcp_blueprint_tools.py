from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.blueprint_service import BlueprintService  # noqa: E402
from arkdev_mcp.contracts import McpExecutionError  # noqa: E402
from blueprint_translator.interpretation_publication import (  # noqa: E402
    publish_interpretation,
)
from interpretation_fixture import (  # noqa: E402
    interpretation_payload,
    large_interpretation_payload,
    publish_interpretation_fixture,
)


def _assert_path_free(case: unittest.TestCase, value: object, forbidden: Path) -> None:
    encoded = json.dumps(value, ensure_ascii=False)
    case.assertNotIn(str(forbidden), encoded)
    case.assertNotIn(str(forbidden).replace("\\", "/"), encoded)
    case.assertNotRegex(encoded, r"(?i)(?<![A-Za-z0-9_])[a-z]:[\\/]")


class BlueprintServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.capture_root = Path(self._temporary.name) / "captures"
        self.asset_dir, self.source_path, _payload = publish_interpretation_fixture(
            self.capture_root
        )
        publish_interpretation(self.asset_dir, budget=32_000)
        self.service = BlueprintService(self.capture_root)

    def context(self, **overrides: object) -> dict[str, object]:
        arguments: dict[str, object] = {
            "asset": "InterpretationFixture",
            "goal": "ReceiveBeginPlay",
            "graph_ref": "",
            "seed_refs": (),
            "max_hops": 1,
            "max_nodes": 40,
            "max_pins": 160,
            "max_edges": 160,
            "budget_tokens": 2400,
            "continuation": "",
        }
        arguments.update(overrides)
        return self.service.get_context(**arguments)

    def test_asset_list_reuses_public_health_and_opaque_pagination(self) -> None:
        second_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root,
            name="SecondFixture",
        )
        publish_interpretation(second_dir, budget=32_000)

        first = self.service.list_assets(query="Fixture", limit=1, cursor="")
        self.assertEqual(
            first["schema"], "blueprint-to-code.mcp-blueprint-assets/v1"
        )
        self.assertEqual(len(first["items"]), 1)
        self.assertIsInstance(first["page"]["nextCursor"], str)
        second = self.service.list_assets(
            query="Fixture",
            limit=1,
            cursor=first["page"]["nextCursor"],
        )
        self.assertNotEqual(first["items"], second["items"])
        _assert_path_free(self, first, self.capture_root)
        _assert_path_free(self, second, self.capture_root)

    def test_context_is_fresh_path_free_bounded_and_deterministic(self) -> None:
        first = self.context()
        second = self.context()

        self.assertEqual(first, second)
        self.assertEqual(
            first["schema"], "blueprint-to-code.mcp-blueprint-context/v1"
        )
        self.assertEqual(first["freshness"], "FRESH")
        self.assertEqual(first["goal"], "ReceiveBeginPlay")
        self.assertEqual(len(first["querySignature"]), 64)
        self.assertGreaterEqual(len(first["graphTargets"]), 1)
        self.assertLessEqual(len(first["nodes"]), 40)
        self.assertLessEqual(len(first["pins"]), 160)
        self.assertLessEqual(len(first["edges"]), 160)
        _assert_path_free(self, first, self.capture_root)

    def test_context_uses_only_exact_node_or_pin_evidence_refs_as_seeds(self) -> None:
        baseline = self.context(max_hops=0)
        graph_ref = baseline["graphTargets"][0]["ref"]
        node_ref = baseline["nodes"][0]["ref"]
        pin_ref = baseline["pins"][0]["ref"]
        facts = [
            {
                "id": "statement://fixture",
                "kind": "CALL",
                "text": "Call fixture",
                "status": "HEURISTIC",
                "graphRef": graph_ref,
                "nodeRef": node_ref,
                "evidenceRefs": [
                    node_ref,
                    f"{node_ref}/reference/function/not-a-seed",
                    pin_ref,
                ],
                "gapRefs": [],
            }
        ]

        with patch.object(BlueprintService, "_facts", return_value=facts):
            result = self.context(
                goal="fixture",
                graph_ref=graph_ref,
                max_hops=0,
            )

        self.assertGreater(len(result["nodes"]), 0)
        self.assertEqual(result["facts"], facts)

    def test_current_interpreter_1_1_dinodefense_fixture_is_readable_across_services(
        self,
    ) -> None:
        name = "DinoDefenseFixture"
        object_path = f"/DinoDefense/Camera/{name}.{name}"
        payload = interpretation_payload(name)
        payload["asset_path"] = object_path
        asset_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root,
            name=name,
            payload=payload,
        )
        publish_interpretation(asset_dir, budget=32_000)

        health = self.service.health(asset=name)
        authority = self.service.get_task_authority(asset=name)
        context = self.service.get_context(
            asset=name,
            goal="ReceiveBeginPlay",
        )

        self.assertTrue((asset_dir / "evidence" / "current.json").is_file())
        self.assertTrue((asset_dir / "interpretation" / "current.json").is_file())
        self.assertEqual(health["health"]["status"], "READY")
        self.assertEqual(health["health"]["asset"]["objectPath"], object_path)
        self.assertEqual(
            health["health"]["evidence"]["freshnessStatus"],
            "FRESH",
        )
        self.assertIs(
            health["health"]["evidence"]["releaseAuthority"],
            True,
        )
        self.assertEqual(
            health["health"]["interpretation"]["interpreterVersion"],
            "blueprint-interpreter/1.1.0",
        )
        self.assertEqual(authority["objectPath"], object_path)
        self.assertEqual(authority["freshness"], "FRESH")
        self.assertEqual(context["identity"]["asset"]["objectPath"], object_path)
        self.assertIs(
            context["identity"]["evidence"]["releaseAuthority"],
            True,
        )
        self.assertEqual(
            context["identity"]["interpretation"]["interpreterVersion"],
            "blueprint-interpreter/1.1.0",
        )
        self.assertEqual(context["freshness"], "FRESH")

    def test_context_requires_explicit_graph_selection_when_goal_has_no_match(
        self,
    ) -> None:
        with self.assertRaises(McpExecutionError) as raised:
            self.context(goal="definitely-no-such-graph-or-node")

        self.assertEqual(raised.exception.code, "GRAPH_SELECTION_REQUIRED")
        candidates = raised.exception.details["candidates"]
        self.assertGreater(len(candidates), 0)
        self.assertLessEqual(len(candidates), 5)
        _assert_path_free(self, raised.exception.as_payload(), self.capture_root)

    def test_context_fails_closed_when_evidence_source_is_stale(self) -> None:
        self.source_path.write_bytes(self.source_path.read_bytes() + b"-changed")

        with self.assertRaises(McpExecutionError) as raised:
            self.context()

        self.assertEqual(raised.exception.code, "EVIDENCE_STALE")
        _assert_path_free(self, raised.exception.as_payload(), self.capture_root)

    def test_context_continuation_is_opaque_bounded_and_query_bound(self) -> None:
        name = "LargeInterpretationFixture"
        payload = large_interpretation_payload(name, node_count=30)
        large_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root,
            name=name,
            payload=payload,
        )
        publish_interpretation(large_dir, budget=32_000)

        arguments = {
            "asset": name,
            "goal": "LargePureNode",
            "graph_ref": "",
            "seed_refs": (),
            "max_hops": 0,
            "max_nodes": 1,
            "max_pins": 1,
            "max_edges": 1,
            "budget_tokens": 2400,
            "continuation": "",
        }
        first = self.service.get_context(**arguments)
        self.assertTrue(first["truncated"])
        self.assertEqual(len(first["nodes"]), 1)
        self.assertEqual(len(first["pins"]), 1)
        self.assertIsInstance(first["continuation"], str)
        self.assertNotIn("revision", first["continuation"])

        arguments["continuation"] = first["continuation"]
        second = self.service.get_context(**arguments)
        self.assertEqual(len(second["nodes"]), 1)
        self.assertNotEqual(first["nodes"], second["nodes"])

        arguments["goal"] = "different query"
        with self.assertRaises(McpExecutionError) as raised:
            self.service.get_context(**arguments)
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

        arguments["goal"] = "LargePureNode"
        token = first["continuation"]
        decoded = json.loads(
            base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode(
                "utf-8"
            )
        )
        decoded["r"] = "0" * 24
        arguments["continuation"] = base64.urlsafe_b64encode(
            json.dumps(decoded, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")
        with self.assertRaises(McpExecutionError) as raised:
            self.service.get_context(**arguments)
        self.assertEqual(raised.exception.code, "EVIDENCE_REVISION_MISMATCH")

        decoded["r"] = first["identity"]["evidence"]["revisionId"]
        decoded["o"]["nodes"] = 10_000
        arguments["continuation"] = base64.urlsafe_b64encode(
            json.dumps(decoded, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")
        with self.assertRaises(McpExecutionError) as raised:
            self.service.get_context(**arguments)
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

    def test_exact_node_is_revision_bound_and_returns_direct_context(self) -> None:
        context = self.context(max_hops=0)
        node_ref = context["nodes"][0]["ref"]

        result = self.service.get_node(
            asset="InterpretationFixture",
            node_ref=node_ref,
            include_neighborhood=True,
            max_hops=1,
        )
        self.assertEqual(
            result["schema"], "blueprint-to-code.mcp-blueprint-node/v1"
        )
        self.assertEqual(result["node"]["ref"], node_ref)
        self.assertTrue(all(item["ref"].startswith("bp://") for item in result["pins"]))
        _assert_path_free(self, result, self.capture_root)

        wrong_revision = node_ref.replace(
            node_ref.split("@", 1)[1].split("/", 1)[0],
            "0" * 24,
            1,
        )
        with self.assertRaises(McpExecutionError) as raised:
            self.service.get_node(
                asset="InterpretationFixture",
                node_ref=wrong_revision,
                include_neighborhood=False,
                max_hops=0,
            )
        self.assertEqual(raised.exception.code, "EVIDENCE_REVISION_MISMATCH")

    def test_exact_node_does_not_fall_back_to_fuzzy_search(self) -> None:
        context = self.context(max_hops=0)
        node_ref = context["nodes"][0]["ref"]
        missing_ref = node_ref.rsplit("/", 1)[0] + "/999999"

        with self.assertRaises(McpExecutionError) as raised:
            self.service.get_node(
                asset="InterpretationFixture",
                node_ref=missing_ref,
                include_neighborhood=False,
                max_hops=0,
            )
        self.assertEqual(raised.exception.code, "NODE_NOT_FOUND")

    def test_context_graph_ref_must_belong_to_current_asset_revision(self) -> None:
        context = self.context(max_hops=0)
        graph_ref = context["graphTargets"][0]["ref"]
        wrong_revision = graph_ref.replace(
            graph_ref.split("@", 1)[1].split("/", 1)[0],
            "f" * 24,
            1,
        )

        with self.assertRaises(McpExecutionError) as raised:
            self.context(graph_ref=wrong_revision)
        self.assertEqual(raised.exception.code, "EVIDENCE_REVISION_MISMATCH")

    def test_fixture_helper_can_publish_a_distinct_goal_for_future_regressions(
        self,
    ) -> None:
        payload = interpretation_payload("OtherFixture")
        self.assertEqual(payload["asset_name"], "OtherFixture")


if __name__ == "__main__":
    unittest.main()
