from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.quality_gates import (  # noqa: E402
    QUALITY_GATE_SCHEMA,
    _class_closure_metrics,
    publish_gate_report,
)


class KnowledgeQualityGateTests(unittest.TestCase):
    def _manifest_root(self, root: Path) -> Path:
        manifests = root / "manifests"
        manifests.mkdir()
        manifest = {
            "schema": "ark-kb-vnext-snapshot/v1",
            "buildId": "fixture-build",
            "cutover": {
                "mode": "shadow",
                "defaultQuerySource": "legacy",
            },
        }
        for name in ("current.json", "fixture-build.json"):
            (manifests / name).write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
        return manifests

    def _report(self, *, eligible: bool) -> dict[str, object]:
        return {
            "schema": QUALITY_GATE_SCHEMA,
            "buildId": "fixture-build",
            "summary": {
                "total": 1,
                "passed": 1 if eligible else 0,
                "failed": 0 if eligible else 1,
                "cutoverEligible": eligible,
                "recommendation": (
                    "ready_for_default"
                    if eligible
                    else "keep_legacy_shadow"
                ),
            },
            "gates": [
                {
                    "id": "fixture.gate",
                    "passed": eligible,
                    "critical": True,
                }
            ],
            "benchmark": {"total": 120},
        }

    def test_failed_gate_report_keeps_legacy_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifests = self._manifest_root(root)
            cutover = publish_gate_report(
                snapshot_root=root,
                report=self._report(eligible=False),
            )
            current = json.loads(
                (manifests / "current.json").read_text(encoding="utf-8")
            )
        self.assertEqual(cutover["mode"], "shadow")
        self.assertEqual(cutover["defaultQuerySource"], "legacy")
        self.assertEqual(current["qualityGates"]["failed"], 1)

    def test_passing_gate_report_is_only_path_to_vnext_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._manifest_root(root)
            cutover = publish_gate_report(
                snapshot_root=root,
                report=self._report(eligible=True),
            )
        self.assertEqual(cutover["mode"], "ready")
        self.assertEqual(cutover["defaultQuerySource"], "vnext")

    def test_class_closure_gate_prefers_generated_then_asset_assignment(self):
        core = sqlite3.connect(":memory:")
        core.executescript(
            """
            CREATE TABLE knowledge_depth_policies(
                entity_id INTEGER PRIMARY KEY,
                depth_policy TEXT NOT NULL
            );
            CREATE TABLE asset_class_assignments(
                entity_id INTEGER NOT NULL,
                class_id INTEGER NOT NULL,
                assignment_kind TEXT NOT NULL
            );
            CREATE TABLE class_gaps(
                class_id INTEGER NOT NULL,
                gap_kind TEXT NOT NULL
            );

            INSERT INTO knowledge_depth_policies VALUES (1, 'DEEP');
            INSERT INTO knowledge_depth_policies VALUES (2, 'SEMANTIC');
            INSERT INTO knowledge_depth_policies VALUES (3, 'DEEP');
            INSERT INTO knowledge_depth_policies VALUES (4, 'DEEP');
            INSERT INTO knowledge_depth_policies VALUES (5, 'INDEX_ONLY');

            INSERT INTO asset_class_assignments VALUES
                (1, 101, 'GENERATED_CLASS'),
                (1, 201, 'ASSET_CLASS'),
                (2, 202, 'ASSET_CLASS'),
                (3, 203, 'ASSET_CLASS'),
                (5, 205, 'ASSET_CLASS');

            INSERT INTO class_gaps VALUES
                (201, 'NATIVE_ROOT_NOT_REACHED'),
                (203, 'NATIVE_ROOT_NOT_REACHED'),
                (205, 'NATIVE_ROOT_NOT_REACHED');
            """
        )

        metrics = _class_closure_metrics(core)

        self.assertEqual(metrics["classApplicableCount"], 3)
        self.assertEqual(metrics["classClosedCount"], 2)
        self.assertEqual(metrics["classNotApplicableCount"], 1)
        self.assertEqual(metrics["classOpenCount"], 1)
        self.assertAlmostEqual(metrics["closureRate"], 2 / 3)
        core.close()


if __name__ == "__main__":
    unittest.main()
