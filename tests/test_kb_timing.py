from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.profiling import (  # noqa: E402
    SegmentTiming,
)


class SegmentTimingTests(unittest.TestCase):
    def test_reports_auditable_percentiles_and_inclusive_query_counts(self):
        ticks = iter((0.000, 0.010, 0.020, 0.050)).__next__
        timing = SegmentTiming(clock=ticks)

        with timing.measure("plannerTotal"):
            timing.record_query("SELECT entity_id FROM entities")
            with timing.measure("identityLookup"):
                timing.record_query("SELECT canonical_uri FROM entities")

        report = timing.report()

        self.assertEqual(report["schema"], "ark-kb-segment-timing/v1")
        self.assertEqual(report["clock"], "time.perf_counter")
        self.assertEqual(report["queryCountMode"], "inclusive_sqlite_trace")
        self.assertEqual(
            report["segments"]["identityLookup"],
            {
                "samples": 1,
                "queryCount": 1,
                "p50": 10.0,
                "p95": 10.0,
                "p99": 10.0,
                "maximum": 10.0,
            },
        )
        self.assertEqual(
            report["segments"]["plannerTotal"]["queryCount"],
            2,
        )
        self.assertEqual(
            report["segments"]["plannerTotal"]["p50"],
            50.0,
        )

    def test_unknown_segments_are_rejected(self):
        timing = SegmentTiming()

        with self.assertRaisesRegex(ValueError, "Unknown timing segment"):
            with timing.measure("madeUpSegment"):
                pass


if __name__ == "__main__":
    unittest.main()
