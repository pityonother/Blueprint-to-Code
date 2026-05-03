import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.graph_queue import (
    classify_graph_queue_item,
    graph_queue_summary,
    graph_queue_text_for_mode,
)


class GraphQueueTests(unittest.TestCase):
    def test_classifies_core_and_behavior_graphs_as_recommended(self):
        self.assertEqual(classify_graph_queue_item("EventGraph", "EventGraph")[0], "recommended")
        self.assertEqual(classify_graph_queue_item("BPPreventRiding", "Unknown")[0], "recommended")
        self.assertEqual(classify_graph_queue_item("Server Request Jump", "Unknown")[0], "recommended")
        self.assertEqual(classify_graph_queue_item("Start Teleport", "Unknown")[0], "recommended")

    def test_classifies_getters_and_collapsed_graphs_lower(self):
        self.assertEqual(classify_graph_queue_item("Can Sleep", "Unknown")[0], "optional")
        self.assertEqual(classify_graph_queue_item("Get Default Dino", "Unknown")[0], "deferred")
        self.assertEqual(classify_graph_queue_item("collapsed multicast started roar", "Unknown")[0], "deferred")

    def test_filters_queue_text_by_mode(self):
        text = "\n".join(
            [
                "EventGraph | EventGraph",
                "BPPreventRiding | Unknown",
                "Can Sleep | Unknown",
                "Get Default Dino | Unknown",
                "collapsed multicast started roar | Unknown",
            ]
        )

        summary = graph_queue_summary(text)
        compact = graph_queue_text_for_mode(text, "compact")
        recommended = graph_queue_text_for_mode(text, "recommended")
        focused = graph_queue_text_for_mode(text, "focused")

        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["recommended"], 2)
        self.assertEqual(summary["compact"], 2)
        self.assertEqual(summary["optional"], 1)
        self.assertEqual(summary["deferred"], 2)
        self.assertEqual(compact, recommended)
        self.assertIn("BPPreventRiding", recommended)
        self.assertNotIn("Can Sleep", recommended)
        self.assertIn("Can Sleep", focused)
        self.assertNotIn("collapsed multicast started roar", focused)


if __name__ == "__main__":
    unittest.main()
