"""Acceptance tests against the checked-in ARK Blueprint evidence captures.

CI clones that do not carry the large capture artifacts skip these tests.  When
the captures are present, the tests deliberately use only the public query
service and follow every continuation instead of consulting SQLite directly.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from benchmark_blueprint_evidence import benchmark_database
from blueprint_translator.evidence_query import EvidenceQueryService


CHAINS = (
    (
        "Buff_GigantoraptorPassProtection",
        "IsPrimalDino",
        "BPAdjustDamage_Ex",
    ),
    (
        "Buff_GigantoraptorBonded",
        "CachedCharsKilled",
        "GetBondedChanges",
    ),
    (
        "Buff_StriderHackingParent",
        "NextTimeOut",
        "UpdateBuffTimer",
    ),
    (
        "Archelon_Character_BP_ASA",
        "MapRangeClamped",
        "GetAlgaePercentage",
    ),
)


def _database(asset_name: str) -> Path:
    return ROOT / "captures" / asset_name / "evidence" / "evidence.sqlite"


class RealEvidenceChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        missing = [asset for asset, _signal, _graph in CHAINS if not _database(asset).is_file()]
        if missing:
            raise unittest.SkipTest(
                "real Blueprint evidence captures are unavailable: " + ", ".join(missing)
            )

    def _assert_budget(self, response: Mapping[str, object], requested: int) -> None:
        budget = response.get("budget")
        self.assertIsInstance(budget, Mapping)
        assert isinstance(budget, Mapping)
        self.assertLessEqual(int(budget.get("estimatedUsed") or 0), requested)
        self.assertLessEqual(int(budget.get("effective") or 0), requested)

    def _collect_search(
        self,
        service: EvidenceQueryService,
        query: str,
        kind: str,
        *,
        budget: int = 1200,
    ) -> list[dict[str, Any]]:
        request: dict[str, object] = {
            "operation": "search",
            "query": query,
            "kinds": [kind],
            "pageSize": 100,
            "budgetTokens": budget,
        }
        items: list[dict[str, Any]] = []
        seen_cursors: set[str] = set()
        expected_total: int | None = None
        for _ in range(100):
            response = service.query(request)
            self._assert_budget(response, int(request["budgetTokens"]))
            coverage = response["coverage"]
            self.assertIsInstance(coverage, Mapping)
            assert isinstance(coverage, Mapping)
            expected_total = int(coverage["requested"])
            page_items = [item for item in response["items"] if isinstance(item, dict)]
            items.extend(page_items)
            page = response.get("page")
            cursor = str(page.get("nextCursor") or "") if isinstance(page, Mapping) else ""
            if not cursor:
                break
            self.assertTrue(page_items, "search continuation must make progress")
            self.assertNotIn(cursor, seen_cursors)
            seen_cursors.add(cursor)
            request["cursor"] = cursor
        else:
            self.fail(f"search pagination did not terminate for {query!r}")
        refs = [str(item["ref"]) for item in items]
        self.assertEqual(len(refs), len(set(refs)))
        self.assertEqual(len(items), expected_total)
        return items

    def _collect_properties(
        self,
        service: EvidenceQueryService,
        node_ref: str,
        signal_name: str,
    ) -> list[dict[str, Any]]:
        request: dict[str, object] = {
            "operation": "entity",
            "selector": {"ref": node_ref},
            "propertyOffset": 0,
            "propertyLimit": 5,
            "observationOffset": 0,
            "observationLimit": 0,
            "budgetTokens": 1200,
        }
        properties: list[dict[str, Any]] = []
        expected_total: int | None = None
        for _ in range(50):
            response = service.query(request)
            self._assert_budget(response, int(request["budgetTokens"]))
            next_property_queries = [
                query
                for query in response.get("nextQueries", [])
                if isinstance(query, dict) and "propertyOffset" in query
            ]
            if not response["items"]:
                self.assertTrue(next_property_queries)
                self.assertEqual(
                    int(next_property_queries[0]["propertyOffset"]),
                    int(request.get("propertyOffset") or 0),
                )
                request = dict(next_property_queries[0])
                continue

            item = response["items"][0]
            signals = item.get("signals") if isinstance(item, Mapping) else None
            self.assertIsInstance(signals, Mapping)
            assert isinstance(signals, Mapping)
            self.assertIn(signal_name, {str(value) for value in signals.values()})
            page_properties = [
                prop for prop in item.get("properties", []) if isinstance(prop, dict)
            ]
            properties.extend(page_properties)
            property_coverage = item["propertyCoverage"]
            self.assertIsInstance(property_coverage, Mapping)
            assert isinstance(property_coverage, Mapping)
            expected_total = int(property_coverage["available"])
            if not next_property_queries:
                break
            returned = int(property_coverage["returned"])
            expected_offset = int(property_coverage["offset"]) + returned
            self.assertEqual(
                int(next_property_queries[0]["propertyOffset"]), expected_offset
            )
            if returned == 0:
                self.assertGreater(
                    int(next_property_queries[0]["budgetTokens"]),
                    int(request["budgetTokens"]),
                )
            request = dict(next_property_queries[0])
        else:
            self.fail("property pagination did not terminate")

        refs = [str(item["ref"]) for item in properties]
        self.assertGreater(len(refs), 0)
        self.assertEqual(len(refs), len(set(refs)))
        self.assertEqual(len(refs), expected_total)
        return properties

    def _assert_property_value(
        self, service: EvidenceQueryService, property_ref: str
    ) -> None:
        offset = 0
        value_pages: list[str] = []
        for _ in range(50):
            request = {
                "operation": "entity",
                "selector": {"ref": property_ref},
                "valueOffset": offset,
                "valueChars": 600,
                "budgetTokens": 1200,
            }
            response = service.query(request)
            self._assert_budget(response, 1200)
            self.assertEqual(len(response["items"]), 1)
            item = response["items"][0]
            if "value" in item:
                return
            self.assertIn("valueJsonPage", item)
            coverage = item["valueCoverage"]
            self.assertEqual(int(coverage["offset"]), offset)
            page = str(item["valueJsonPage"])
            self.assertEqual(len(page), int(coverage["returnedChars"]))
            value_pages.append(page)
            next_values = [
                query
                for query in response.get("nextQueries", [])
                if isinstance(query, dict) and "valueOffset" in query
            ]
            if not next_values:
                self.assertEqual(
                    sum(len(value) for value in value_pages),
                    int(coverage["availableChars"]),
                )
                return
            self.assertGreater(len(page), 0)
            offset += len(page)
            self.assertEqual(int(next_values[0]["valueOffset"]), offset)
        self.fail("property-value pagination did not terminate")

    def _collect_bundle_pages(
        self,
        service: EvidenceQueryService,
        first_bundle: Mapping[str, object],
    ) -> None:
        node = first_bundle["node"]
        self.assertIsInstance(node, Mapping)
        assert isinstance(node, Mapping)
        node_ref = str(node["ref"])
        bundle: Mapping[str, object] = first_bundle
        pin_refs: list[str] = []
        edge_refs: list[str] = []
        expected_pins = expected_edges = 0
        for _ in range(100):
            pin_refs.extend(
                str(pin["ref"])
                for pin in bundle.get("pins", [])
                if isinstance(pin, Mapping)
            )
            edge_refs.extend(
                str(edge["ref"])
                for edge in bundle.get("edges", [])
                if isinstance(edge, Mapping)
            )
            coverage = bundle["bundleCoverage"]
            self.assertIsInstance(coverage, Mapping)
            assert isinstance(coverage, Mapping)
            expected_pins = int(coverage["pins"]["available"])
            expected_edges = int(coverage["edges"]["available"])
            continuation = coverage.get("nextQuery")
            if continuation is None:
                break
            self.assertIsInstance(continuation, Mapping)
            assert isinstance(continuation, Mapping)
            self.assertEqual(continuation["traversal"]["maxHops"], 0)
            response = service.query(continuation)
            self._assert_budget(response, int(continuation["budgetTokens"]))
            self.assertEqual(len(response["items"]), 1)
            bundle = response["items"][0]
            self.assertEqual(str(bundle["node"]["ref"]), node_ref)
        else:
            self.fail("node-bundle continuation did not terminate")
        self.assertEqual(len(pin_refs), expected_pins)
        self.assertEqual(len(edge_refs), expected_edges)
        self.assertEqual(len(pin_refs), len(set(pin_refs)))
        self.assertEqual(len(edge_refs), len(set(edge_refs)))

    def _assert_neighborhood_lossless(
        self, service: EvidenceQueryService, node_ref: str
    ) -> None:
        request: dict[str, object] = {
            "operation": "neighborhood",
            "selector": {"ref": node_ref},
            "traversal": {
                "maxHops": 1,
                "direction": "both",
                "edgeKinds": ["exec", "data"],
            },
            "pageSize": 100,
            "pinLimit": 8,
            "edgeLimit": 8,
            "budgetTokens": 1500,
        }
        node_refs: list[str] = []
        expected_nodes: int | None = None
        seen_cursors: set[str] = set()
        for _ in range(100):
            response = service.query(request)
            self._assert_budget(response, 1500)
            coverage = response["coverage"]
            expected_nodes = int(coverage["requested"])
            for bundle in response["items"]:
                node_refs.append(str(bundle["node"]["ref"]))
                self._collect_bundle_pages(service, bundle)
            page = response.get("page")
            cursor = str(page.get("nextCursor") or "") if isinstance(page, Mapping) else ""
            if not cursor:
                break
            self.assertTrue(response["items"], "traversal continuation must make progress")
            self.assertNotIn(cursor, seen_cursors)
            seen_cursors.add(cursor)
            request["cursor"] = cursor
        else:
            self.fail("neighborhood node pagination did not terminate")
        self.assertIn(node_ref, node_refs)
        self.assertEqual(len(node_refs), expected_nodes)
        self.assertEqual(len(node_refs), len(set(node_refs)))

    def _assert_gaps_lossless(
        self, service: EvidenceQueryService, graph_ref: str
    ) -> None:
        request: dict[str, object] = {
            "operation": "gaps",
            "selector": {"ref": graph_ref},
            "pageSize": 100,
            "budgetTokens": 1200,
        }
        gaps: list[dict[str, Any]] = []
        expected_total: int | None = None
        seen_cursors: set[str] = set()
        for _ in range(100):
            response = service.query(request)
            self._assert_budget(response, 1200)
            expected_total = int(response["coverage"]["requested"])
            gaps.extend(item for item in response["items"] if isinstance(item, dict))
            page = response.get("page")
            cursor = str(page.get("nextCursor") or "") if isinstance(page, Mapping) else ""
            if not cursor:
                break
            self.assertTrue(response["items"], "gap continuation must make progress")
            self.assertNotIn(cursor, seen_cursors)
            seen_cursors.add(cursor)
            request["cursor"] = cursor
        else:
            self.fail("gap pagination did not terminate")
        refs = [str(item["ref"]) for item in gaps]
        self.assertEqual(len(refs), expected_total)
        self.assertEqual(len(refs), len(set(refs)))
        missing = [
            item
            for item in gaps
            if item.get("status") in {"SOURCE_NOT_AVAILABLE", "NOT_RECOVERED"}
        ]
        self.assertTrue(missing, gaps)
        self.assertTrue(all(str(item.get("nextProbe") or "").strip() for item in missing))

    def test_four_fixed_blueprint_evidence_chains_are_budgeted_and_lossless(self):
        for asset_name, signal_name, graph_name in CHAINS:
            with self.subTest(asset=asset_name, signal=signal_name, graph=graph_name):
                with EvidenceQueryService.open(_database(asset_name)) as service:
                    graphs = self._collect_search(service, graph_name, "graph")
                    exact_graphs = [item for item in graphs if item.get("name") == graph_name]
                    self.assertEqual(len(exact_graphs), 1)
                    graph_ref = str(exact_graphs[0]["ref"])

                    nodes = self._collect_search(service, signal_name, "node")
                    graph_nodes = [
                        item for item in nodes if str(item.get("graphRef") or "") == graph_ref
                    ]
                    self.assertTrue(graph_nodes)
                    node_ref = str(graph_nodes[0]["ref"])

                    properties = self._collect_properties(
                        service, node_ref, signal_name
                    )
                    for prop in properties:
                        self._assert_property_value(service, str(prop["ref"]))

                    self._assert_neighborhood_lossless(service, node_ref)
                    self._assert_gaps_lossless(service, graph_ref)


class LargestRealEvidenceBenchmarkTests(unittest.TestCase):
    def test_lionfish_search_and_real_two_hop_p95(self):
        database_path = _database("LionfishLion_Character_BP")
        if not database_path.is_file():
            self.skipTest("Lionfish real evidence capture is unavailable")
        result = benchmark_database(database_path, iterations=25)
        self.assertEqual(result["twoHop"]["request"]["traversal"]["maxHops"], 2)
        self.assertTrue(result["ok"], result)


if __name__ == "__main__":
    unittest.main()
