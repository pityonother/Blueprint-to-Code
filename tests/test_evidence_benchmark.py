from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_blueprint_evidence  # noqa: E402
from blueprint_translator.evidence_writer import (  # noqa: E402
    write_evidence_artifacts_from_payload,
)


def _make_v3_fixture(root: Path) -> tuple[Path, Path]:
    asset_dir = root / "BenchmarkFixture"
    payload = {
        "asset_name": "BenchmarkFixture",
        "asset_path": "/Game/Test/BenchmarkFixture.BenchmarkFixture",
        "graphs": [
            {
                "graph": "EventGraph",
                "graph_type": "EventGraph",
                "export_index": 1,
                "status": "complete",
                "confidence": "high",
                "payload": {
                    "metadata": {
                        "asset_name": "BenchmarkFixture",
                        "graph_name": "EventGraph",
                        "graph_type": "EventGraph",
                        "uasset_export_index": 1,
                        "uasset_read_status": "complete",
                        "confidence": "high",
                    },
                    "nodes": [
                        {
                            "index": 1,
                            "name": "BeginPlay",
                            "label": "Receive Begin Play",
                            "node_type": "K2Node_Event",
                            "event": "ReceiveBeginPlay",
                            "source": "fixture",
                            "confidence": "high",
                            "pins": [],
                        }
                    ],
                },
            }
        ],
    }
    result = write_evidence_artifacts_from_payload(
        "/Game/Test/BenchmarkFixture.BenchmarkFixture",
        None,
        payload,
        asset_dir,
    )
    revision_dir = Path(str(result["revision_dir"]))
    for path in (
        asset_dir / "evidence" / "evidence.sqlite",
        asset_dir / "evidence" / "manifest.json",
        asset_dir / "output" / "agent_index.md",
    ):
        path.unlink(missing_ok=True)
    return asset_dir, revision_dir


class EvidenceBenchmarkTests(unittest.TestCase):
    def _run(self, asset_dir: Path) -> tuple[int, dict[str, object]]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = benchmark_blueprint_evidence.main(
                [
                    "--asset-dir",
                    str(asset_dir),
                    "--search-query",
                    "BeginPlay",
                    "--iterations",
                    "1",
                    "--max-search-p95-ms",
                    "10000",
                    "--max-two-hop-p95-ms",
                    "10000",
                ]
            )
        return status, json.loads(stdout.getvalue())

    def test_asset_dir_benchmark_uses_pruned_v3_and_fails_closed_on_tamper(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir, revision_dir = _make_v3_fixture(Path(temp_dir))
            immutable_index = (revision_dir / "agent_index.md").read_bytes()

            status, payload = self._run(asset_dir)
            self.assertEqual(status, 0, payload)
            self.assertTrue(payload["ok"])
            self.assertEqual(
                Path(str(payload["database"])).resolve(),
                (revision_dir / "evidence.sqlite").resolve(),
            )
            self.assertEqual((revision_dir / "agent_index.md").read_bytes(), immutable_index)

            (asset_dir / "evidence" / "current.json").write_text(
                "{}\n", encoding="utf-8"
            )
            status, payload = self._run(asset_dir)
            self.assertEqual(status, 1)
            self.assertFalse(payload["ok"])
            self.assertIn("EvidenceArtifactInvalid", str(payload["errors"][0]))


if __name__ == "__main__":
    unittest.main()
