from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.runtime_loot_chain import (  # noqa: E402
    compose_runtime_loot_chain,
)
from query_runtime_loot_chain import _query_all  # noqa: E402


EVENT_NAME = "Ice Queen is Killed"
ITEM_CLASS = (
    "/Game/Test/Items/PrimalItemArmor_TestSaddle."
    "PrimalItemArmor_TestSaddle_C"
)


def _asset(object_path: str, name: str) -> dict[str, object]:
    return {
        "name": name,
        "objectPath": object_path,
        "revisionId": f"revision-{name}",
        "sourceKind": "INDEXED_V3_CURRENT",
        "freshnessStatus": "FRESH",
        "releaseAuthority": True,
    }


class RuntimeLootChainCompositionTests(unittest.TestCase):
    def test_cli_query_collector_follows_lossless_cursors(self):
        class PagedRepository:
            def __init__(self):
                self.requests: list[dict[str, object]] = []

            def query(self, request):
                self.requests.append(dict(request))
                if request.get("cursor") == "next-page":
                    return {
                        "items": [{"ref": "second"}],
                        "coverage": {
                            "requested": 2,
                            "availableNotReturned": 1,
                        },
                        "page": {"nextCursor": None},
                    }
                return {
                    "items": [{"ref": "first"}],
                    "coverage": {
                        "requested": 2,
                        "availableNotReturned": 1,
                    },
                    "page": {"nextCursor": "next-page"},
                    "match": {"status": "MATCHED"},
                }

        repository = PagedRepository()

        result = _query_all(repository, {"operation": "runtime-signals"})

        self.assertEqual([item["ref"] for item in result["items"]], ["first", "second"])
        self.assertEqual(result["page"]["nextCursor"], None)
        self.assertEqual(repository.requests[0]["pageSize"], 100)
        self.assertEqual(repository.requests[0]["budgetTokens"], 8000)
        self.assertEqual(repository.requests[1]["cursor"], "next-page")

    def test_cli_query_collector_rejects_silent_omissions(self):
        class TruncatedRepository:
            @staticmethod
            def query(_request):
                return {
                    "items": [],
                    "coverage": {
                        "requested": 1,
                        "availableNotReturned": 1,
                    },
                    "page": {"nextCursor": None},
                }

        with self.assertRaisesRegex(
            ValueError,
            "LOSSLESS_CONTINUATION_UNAVAILABLE",
        ):
            _query_all(TruncatedRepository(), {"operation": "runtime-routes"})

    def test_complete_chain_requires_spawned_class_to_own_matching_reward_default(self):
        emitter = {
            "asset": _asset("/Game/Test/IceQueen.IceQueen", "IceQueen"),
            "result": {
                "items": [
                    {
                        "kind": "runtimeSignal",
                        "signalKind": "global_level_event_emit",
                        "status": "CONFIRMED",
                        "eventName": EVENT_NAME,
                        "nodeRef": "bp://emit/node",
                        "valuePinRef": "bp://emit/pin",
                        "evidenceRefs": ["bp://emit/node", "bp://emit/pin"],
                    }
                ]
            },
        }
        receiver = {
            "asset": _asset("/Game/Test/TestMap.TestMap", "TestMap"),
            "result": {
                "match": {
                    "status": "MATCHED",
                    "receiverNodes": 1,
                    "confirmedRoutes": 1,
                },
                "items": [
                    {
                        "kind": "runtimeRoute",
                        "status": "CONFIRMED",
                        "eventName": EVENT_NAME,
                        "actorClass": "/Game/Test/TestCrate.TestCrate_C",
                        "receiverNodeRef": "bp://map/receiver",
                        "spawnNodeRef": "bp://map/spawn",
                        "evidenceRefs": [
                            "bp://map/receiver",
                            "bp://map/edge",
                            "bp://map/spawn",
                            "bp://map/class-pin",
                        ],
                    }
                ],
            },
        }
        reward = {
            "asset": _asset("/Game/Test/TestCrate.TestCrate", "TestCrate"),
            "result": {
                "match": {"status": "MATCHED", "matchingEntries": 1},
                "items": [
                    {
                        "kind": "lootRewardEntry",
                        "status": "CONFIRMED",
                        "itemClass": ITEM_CLASS,
                        "rewardType": "ITEM_OR_PHYSICAL_BLUEPRINT",
                        "evidenceRefs": ["bp://crate/default"],
                    }
                ],
            },
        }

        result = compose_runtime_loot_chain(
            event_name=EVENT_NAME,
            item_query="PrimalItemArmor_TestSaddle",
            emitter_probe=emitter,
            receiver_probes=[receiver],
            reward_probes=[reward],
        )

        self.assertEqual(result["schema"], "blueprint-to-code.runtime-loot-chain/v1")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["gaps"], [])
        steps = {step["id"]: step for step in result["steps"]}
        self.assertEqual(steps["event_emit"]["status"], "CONFIRMED")
        self.assertEqual(steps["event_receiver"]["status"], "CONFIRMED")
        self.assertEqual(steps["spawn_actor"]["status"], "CONFIRMED")
        self.assertEqual(steps["reward_membership"]["status"], "CONFIRMED")
        self.assertEqual(
            steps["spawn_to_reward_source"]["status"], "CONFIRMED"
        )
        self.assertEqual(
            steps["spawn_to_reward_source"]["bindingAuthority"],
            "EXACT_GENERATED_CLASS_TO_ASSET_OBJECT_PATH",
        )
        self.assertEqual(len(result["evidenceRefs"]), len(set(result["evidenceRefs"])))

    def test_partial_chain_names_missing_receiver_and_does_not_hide_reward_membership(self):
        emitter = {
            "asset": _asset("/Game/Test/IceQueen.IceQueen", "IceQueen"),
            "result": {
                "items": [
                    {
                        "kind": "runtimeSignal",
                        "signalKind": "global_level_event_emit",
                        "status": "CONFIRMED",
                        "eventName": EVENT_NAME,
                        "evidenceRefs": ["bp://emit/node", "bp://emit/pin"],
                    }
                ]
            },
        }
        receiver = {
            "asset": _asset("/Game/Mods/Ragnarok/Ragnarok_WP.Ragnarok_WP", "Ragnarok_WP"),
            "result": {
                "match": {
                    "status": "GLOBAL_EVENT_RECEIVER_NOT_INDEXED",
                    "receiverNodes": 0,
                    "confirmedRoutes": 0,
                },
                "items": [],
            },
        }
        reward = {
            "asset": _asset("/Game/Test/IndependentPool.IndependentPool", "IndependentPool"),
            "result": {
                "match": {"status": "MATCHED", "matchingEntries": 1},
                "items": [
                    {
                        "kind": "lootRewardEntry",
                        "status": "CONFIRMED",
                        "itemClass": ITEM_CLASS,
                        "rewardType": "PHYSICAL_ITEM_ONLY",
                        "evidenceRefs": ["bp://pool/default"],
                    }
                ],
            },
        }

        result = compose_runtime_loot_chain(
            event_name=EVENT_NAME,
            item_query="PrimalItemArmor_TestSaddle",
            emitter_probe=emitter,
            receiver_probes=[receiver],
            reward_probes=[reward],
            expected_receiver_object_paths=[
                "/Game/Mods/Ragnarok/Ragnarok_WP.Ragnarok_WP",
            ],
            expected_reward_object_paths=[
                "/Game/Mods/Astraeos/Assets/CoreBlueprints/HordeCrates/"
                "SupplyCrate_Base_Horde_Easy_Astraeos."
                "SupplyCrate_Base_Horde_Easy_Astraeos",
            ],
        )

        self.assertEqual(result["status"], "PARTIAL")
        reason_codes = {gap["reasonCode"] for gap in result["gaps"]}
        self.assertIn("GLOBAL_EVENT_RECEIVER_NOT_INDEXED", reason_codes)
        self.assertIn("REWARD_SOURCE_NOT_AVAILABLE", reason_codes)
        self.assertIn("SPAWN_ROUTE_NOT_RECOVERED", reason_codes)
        self.assertNotIn("ITEM_NOT_DROPPED", reason_codes)
        steps = {step["id"]: step for step in result["steps"]}
        self.assertEqual(steps["reward_membership"]["status"], "CONFIRMED")
        self.assertEqual(
            steps["spawn_to_reward_source"]["status"], "NOT_RECOVERED"
        )
        self.assertEqual(
            steps["spawn_to_reward_source"]["reasonCode"],
            "SPAWN_ROUTE_NOT_RECOVERED",
        )

    def test_reward_pool_membership_without_spawn_class_binding_stays_partial(self):
        result = compose_runtime_loot_chain(
            event_name=EVENT_NAME,
            item_query="PrimalItemArmor_TestSaddle",
            emitter_probe={
                "asset": _asset("/Game/Test/IceQueen.IceQueen", "IceQueen"),
                "result": {
                    "items": [
                        {
                            "signalKind": "global_level_event_emit",
                            "status": "CONFIRMED",
                            "eventName": EVENT_NAME,
                            "evidenceRefs": ["bp://emit"],
                        }
                    ]
                },
            },
            receiver_probes=[
                {
                    "asset": _asset("/Game/Test/TestMap.TestMap", "TestMap"),
                    "result": {
                        "match": {"receiverNodes": 1, "confirmedRoutes": 1},
                        "items": [
                            {
                                "kind": "runtimeRoute",
                                "status": "CONFIRMED",
                                "eventName": EVENT_NAME,
                                "actorClass": "/Game/Test/TestCrate.TestCrate_C",
                                "evidenceRefs": ["bp://route"],
                            }
                        ],
                    },
                }
            ],
            reward_probes=[
                {
                    "asset": _asset(
                        "/Game/Test/SeparateLootPool.SeparateLootPool",
                        "SeparateLootPool",
                    ),
                    "result": {
                        "match": {"status": "MATCHED", "matchingEntries": 1},
                        "items": [
                            {
                                "kind": "lootRewardEntry",
                                "status": "CONFIRMED",
                                "evidenceRefs": ["bp://pool"],
                            }
                        ],
                    },
                }
            ],
        )

        self.assertEqual(result["status"], "PARTIAL")
        binding = next(
            step for step in result["steps"] if step["id"] == "spawn_to_reward_source"
        )
        self.assertEqual(
            binding["reasonCode"], "SPAWNED_CLASS_TO_REWARD_SOURCE_NOT_BOUND"
        )
        self.assertNotIn("overallDropChance", str(result))


if __name__ == "__main__":
    unittest.main()
