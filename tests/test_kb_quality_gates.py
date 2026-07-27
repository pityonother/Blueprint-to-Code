from __future__ import annotations

import json
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


if __name__ == "__main__":
    unittest.main()
