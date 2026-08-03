from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from blueprint_translator.interpretation.contracts import (  # noqa: E402
    InterpretationArtifactInvalid,
)
from blueprint_translator.interpretation.render import (  # noqa: E402
    render_pseudocode_and_trace,
)
from blueprint_translator.interpretation_publication import (  # noqa: E402
    load_current_interpretation,
    publish_interpretation,
)
from interpretation_fixture import publish_interpretation_fixture  # noqa: E402


class InterpretationRevisionRenderAdversarialTests(unittest.TestCase):
    def test_reader_rejects_unmanifested_revision_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, _source, _payload = publish_interpretation_fixture(
                Path(temporary)
            )
            published = publish_interpretation(asset_dir, budget=32_000)
            (published.revision_dir / "UNMANIFESTED.txt").write_text(
                "not part of the immutable revision",
                encoding="utf-8",
            )

            with self.assertRaises(InterpretationArtifactInvalid) as raised:
                load_current_interpretation(asset_dir)

        self.assertEqual(
            raised.exception.code,
            "INTERPRETATION_REVISION_FILES_INVALID",
        )

    def test_pseudocode_escapes_hostile_text_without_breaking_trace_offsets(self) -> None:
        graph_ref = f"bp://{'a' * 24}@{'b' * 24}/g/7"
        node_ref = f"{graph_ref}/n/1"
        statement_id = "statement://fixture/hostile"
        hostile = "<script>$(touch pwned); `cmd`\n下一行 & [link] # heading"
        interpretation = {
            "assetId": "a" * 24,
            "evidenceRevisionId": "b" * 24,
            "evidenceManifestSha256": "c" * 64,
            "semanticDigest": "d" * 64,
            "statements": [
                {
                    "id": statement_id,
                    "kind": "CALL",
                    "text": hostile,
                    "status": "CONFIRMED",
                    "evidenceRefs": [node_ref],
                    "gapRefs": [],
                    "graphRef": graph_ref,
                    "nodeRef": node_ref,
                    "sourceOrder": 0,
                }
            ],
            "controlFlow": {
                "graphs": [
                    {
                        "graphRef": graph_ref,
                        "name": hostile,
                        "nodes": [{"nodeRef": node_ref, "successors": []}],
                        "basicBlocks": [
                            {
                                "label": "L_0",
                                "nodeRefs": [node_ref],
                            }
                        ],
                    }
                ]
            },
        }

        pseudocode, trace = render_pseudocode_and_trace(interpretation, [])

        self.assertNotIn("<script>", pseudocode)
        self.assertNotIn("$(", pseudocode)
        self.assertNotIn("`cmd`", pseudocode)
        self.assertNotIn("下一行", pseudocode)
        self.assertIn(r"\u003cscript\u003e", pseudocode)
        self.assertIn(r"\u0024\u0028touch pwned\u0029", pseudocode)
        self.assertIn(r"\u4e0b\u4e00\u884c", pseudocode)
        executable = [row for row in trace["pseudocodeLines"] if row["executable"]]
        self.assertEqual(len(executable), 1)
        self.assertEqual(executable[0]["statementId"], statement_id)
        raw = pseudocode.encode("utf-8")
        for row in trace["pseudocodeLines"]:
            self.assertLessEqual(row["startByte"], row["endByte"])
            self.assertLessEqual(row["endByte"], len(raw))


if __name__ == "__main__":
    unittest.main()
