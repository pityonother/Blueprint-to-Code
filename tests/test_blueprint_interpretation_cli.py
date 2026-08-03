from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import interpret_blueprint_evidence as cli  # noqa: E402
from interpretation_fixture import publish_interpretation_fixture  # noqa: E402


ASSET_ID = "a" * 24
EVIDENCE_REVISION = "b" * 24
INTERPRETATION_REVISION = "c" * 24
EVIDENCE_MANIFEST_SHA = "d" * 64
INTERPRETATION_MANIFEST_SHA = "e" * 64
GRAPH_REF = f"bp://{ASSET_ID}@{EVIDENCE_REVISION}/g/7"


def _state() -> SimpleNamespace:
    return SimpleNamespace(
        revision_id=INTERPRETATION_REVISION,
        manifest_sha256=INTERPRETATION_MANIFEST_SHA,
        pointer_sha256="f" * 64,
        manifest={
            "evidenceRevisionId": EVIDENCE_REVISION,
            "evidenceManifestSha256": EVIDENCE_MANIFEST_SHA,
        },
        interpretation={
            "schema": "blueprint-to-code.blueprint-interpretation/v1",
            "assetId": ASSET_ID,
            "objectPath": "/Game/Test/Fixture.Fixture",
            "evidenceRevisionId": EVIDENCE_REVISION,
            "evidenceManifestSha256": EVIDENCE_MANIFEST_SHA,
            "interpreterVersion": "fixture-interpreter",
            "schemaVersion": "blueprint-to-code.blueprint-interpretation/v1",
            "semanticDigest": "1" * 64,
            "generatedAt": "2026-08-03T00:00:00Z",
            "statements": [
                {
                    "id": "statement://fixture/event/0",
                    "kind": "EVENT",
                    "text": "BeginPlay",
                    "status": "CONFIRMED",
                    "evidenceRefs": [f"{GRAPH_REF}/n/1"],
                    "gapRefs": [],
                    "graphRef": GRAPH_REF,
                    "sourceOrder": 0,
                },
                {
                    "id": "statement://fixture/other/1",
                    "kind": "CALL",
                    "text": "Other graph",
                    "status": "CONFIRMED",
                    "evidenceRefs": [],
                    "gapRefs": [],
                    "graphRef": f"bp://{ASSET_ID}@{EVIDENCE_REVISION}/g/8",
                    "sourceOrder": 1,
                },
            ],
        },
        trace=[],
        gaps=[],
        markdown="# Fixture\n",
        pseudocode="EVIDENCE-DERIVED PSEUDOCODE\n",
    )


class BlueprintInterpretationCliTests(unittest.TestCase):
    def test_all_format_publishes_with_safe_defaults_and_prints_path_free_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "Fixture"
            asset_dir.mkdir()
            state = _state()
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch.object(cli, "_publish_interpretation", return_value=state) as publish,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = cli.main(
                    [
                        "--asset-dir",
                        str(asset_dir),
                        "--format",
                        "all",
                        "--budget",
                        "24000",
                        "--allow-stale=false",
                        "--allow-legacy-fallback=false",
                    ]
                )

        self.assertEqual(exit_code, 0, stderr.getvalue())
        publish.assert_called_once_with(
            asset_dir,
            budget=24000,
            fail_on_gap=False,
            allow_stale=False,
            allow_legacy_fallback=False,
            expected_semantic_digest=None,
        )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["schema"], "blueprint-to-code.interpretation-cli-result/v1")
        self.assertEqual(payload["revisionId"], INTERPRETATION_REVISION)
        self.assertNotIn(temp_dir, stdout.getvalue())
        self.assertEqual(
            payload["artifacts"],
            [
                "interpretation.json",
                "interpretation.md",
                "trace.json",
                "gaps.json",
                "pseudocode.txt",
                "manifest.json",
            ],
        )

    def test_json_format_applies_an_exact_graph_projection_without_changing_publication(self) -> None:
        state = _state()
        other_graph_ref = f"bp://{ASSET_ID}@{EVIDENCE_REVISION}/g/8"
        state.interpretation.update(
            {
                "assetSummary": {
                    "graphCount": 2,
                    "nodeCount": 5,
                    "pinCount": 8,
                    "edgeCount": 4,
                    "diagnosticGapCount": 1,
                    "graphInventory": [
                        {
                            "graphRef": GRAPH_REF,
                            "status": "RECOVERED",
                            "nodeCount": 2,
                            "pinCount": 3,
                        },
                        {
                            "graphRef": other_graph_ref,
                            "status": "PARTIAL",
                            "nodeCount": 3,
                            "pinCount": 5,
                        },
                    ],
                    "graphStatusCounts": {"PARTIAL": 1, "RECOVERED": 1},
                    "entries": [
                        {"nodeRef": f"{GRAPH_REF}/n/1"},
                        {"nodeRef": f"{other_graph_ref}/n/1"},
                    ],
                    "variableReads": [],
                    "variableWrites": [],
                    "confirmedLocalCalls": [],
                    "externalOrMissingCallableBodies": [
                        {"graphRef": other_graph_ref, "nodeRef": f"{other_graph_ref}/n/2"}
                    ],
                    "delegateBindings": [],
                    "macros": [],
                    "classDefaults": [],
                },
                "controlFlow": {
                    "graphs": [
                        {
                            "graphRef": GRAPH_REF,
                            "nodes": [
                                {
                                    "nodeRef": f"{GRAPH_REF}/n/1",
                                    "successors": [{"edgeRef": f"{GRAPH_REF}/e/exec"}],
                                }
                            ],
                        },
                        {"graphRef": other_graph_ref, "nodes": []},
                    ]
                },
                "dataFlow": {
                    "graphs": [
                        {
                            "graphRef": GRAPH_REF,
                            "edges": [{"edgeRef": f"{GRAPH_REF}/e/data"}],
                        },
                        {"graphRef": other_graph_ref, "edges": []},
                    ],
                    "sharedExpressions": [
                        {"sourceNodeRef": f"{GRAPH_REF}/n/2"},
                        {"sourceNodeRef": f"{other_graph_ref}/n/2"},
                    ],
                    "componentRefs": [
                        {"graphRef": GRAPH_REF, "referenceRef": f"{GRAPH_REF}/r/1"},
                        {
                            "graphRef": other_graph_ref,
                            "referenceRef": f"{other_graph_ref}/r/1",
                        },
                    ],
                    "classDefaultRefs": [],
                },
                "heuristicReviewHints": [
                    {"id": "hint://selected", "reviewRef": f"{GRAPH_REF}/n/2"},
                    {"id": "hint://other", "reviewRef": f"{other_graph_ref}/n/2"},
                ],
            }
        )
        stdout = io.StringIO()
        with (
            patch.object(cli, "_publish_interpretation", return_value=state),
            patch.object(cli, "_build_interpretation_preview", return_value=state),
            redirect_stdout(stdout),
        ):
            exit_code = cli.main(
                [
                    "--asset-dir",
                    "Fixture",
                    "--format",
                    "json",
                    "--graph",
                    GRAPH_REF,
                ]
            )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(len(payload["statements"]), 1)
        self.assertEqual(payload["statements"][0]["graphRef"], GRAPH_REF)
        self.assertEqual(payload["selection"]["graphRefs"], [GRAPH_REF])
        self.assertEqual(
            [row["id"] for row in payload["heuristicReviewHints"]],
            ["hint://selected"],
        )
        self.assertEqual(
            [row["graphRef"] for row in payload["controlFlow"]["graphs"]],
            [GRAPH_REF],
        )
        self.assertEqual(
            [row["graphRef"] for row in payload["dataFlow"]["graphs"]],
            [GRAPH_REF],
        )
        self.assertEqual(len(payload["dataFlow"]["sharedExpressions"]), 1)
        self.assertEqual(len(payload["dataFlow"]["componentRefs"]), 1)
        self.assertEqual(
            [row["graphRef"] for row in payload["assetSummary"]["graphInventory"]],
            [GRAPH_REF],
        )
        self.assertEqual(payload["assetSummary"]["graphCount"], 1)
        self.assertEqual(payload["assetSummary"]["nodeCount"], 2)
        self.assertEqual(payload["assetSummary"]["pinCount"], 3)
        self.assertEqual(payload["assetSummary"]["edgeCount"], 2)
        self.assertEqual(payload["assetSummary"]["graphStatusCounts"], {"RECOVERED": 1})
        self.assertEqual(len(payload["assetSummary"]["entries"]), 1)
        self.assertEqual(payload["assetSummary"]["externalOrMissingCallableBodies"], [])

    def test_invalid_graph_or_format_never_creates_interpretation_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir, _source, _payload = publish_interpretation_fixture(
                Path(temp_dir)
            )
            pointer = asset_dir / "interpretation" / "current.json"
            for arguments, expected_code in (
                (
                    [
                        "--asset-dir",
                        str(asset_dir),
                        "--format",
                        "json",
                        "--graph",
                        f"bp://{'a' * 24}@{'b' * 24}/g/999",
                    ],
                    "GRAPH_NOT_FOUND",
                ),
                (
                    [
                        "--asset-dir",
                        str(asset_dir),
                        "--format",
                        "markdown",
                        "--graph",
                        GRAPH_REF,
                    ],
                    "GRAPH_FORMAT_UNSUPPORTED",
                ),
            ):
                stderr = io.StringIO()
                with self.subTest(code=expected_code), redirect_stderr(stderr):
                    exit_code = cli.main(arguments)
                self.assertEqual(exit_code, 2)
                self.assertEqual(json.loads(stderr.getvalue())["code"], expected_code)
                self.assertFalse(pointer.exists())

    def test_publication_error_is_structured_and_does_not_expose_local_path(self) -> None:
        class FixtureFailure(ValueError):
            code = "STALE_SOURCE"

        separator = chr(92)
        fixture_path = separator.join(("C:", "Users", "fixture", "capture"))
        stderr = io.StringIO()
        with (
            patch.object(
                cli,
                "_publish_interpretation",
                side_effect=FixtureFailure(f"source changed under {fixture_path}"),
            ),
            redirect_stderr(stderr),
        ):
            exit_code = cli.main(["--asset-dir", "Fixture", "--format", "all"])

        self.assertEqual(exit_code, 4)
        payload = json.loads(stderr.getvalue())
        self.assertEqual(payload["code"], "EVIDENCE_STALE")
        self.assertNotIn(separator.join(("C:", "Users")), stderr.getvalue())

    def test_fail_on_gap_error_has_a_distinct_gate_exit_code(self) -> None:
        class GapFailure(ValueError):
            code = "INTERPRETATION_GAPS_PRESENT"

        stderr = io.StringIO()
        with (
            patch.object(cli, "_publish_interpretation", side_effect=GapFailure("gaps")),
            redirect_stderr(stderr),
        ):
            exit_code = cli.main(
                ["--asset-dir", "Fixture", "--fail-on-gap", "--format", "all"]
            )

        self.assertEqual(exit_code, 3)
        self.assertEqual(
            json.loads(stderr.getvalue())["code"],
            "INTERPRETATION_GAPS_PRESENT",
        )

    def test_preview_change_has_a_distinct_fail_closed_exit_code(self) -> None:
        class PreviewChanged(ValueError):
            code = "INTERPRETATION_PREVIEW_CHANGED"

        stderr = io.StringIO()
        with (
            patch.object(
                cli,
                "_publish_interpretation",
                side_effect=PreviewChanged("concurrent input change"),
            ),
            redirect_stderr(stderr),
        ):
            exit_code = cli.main(["--asset-dir", "Fixture", "--format", "all"])

        self.assertEqual(exit_code, 4)
        self.assertEqual(
            json.loads(stderr.getvalue())["code"],
            "INTERPRETATION_INPUT_CHANGED",
        )


if __name__ == "__main__":
    unittest.main()
