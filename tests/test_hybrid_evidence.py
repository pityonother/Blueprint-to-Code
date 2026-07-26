from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURE = ROOT / "tests" / "fixtures" / "native_evidence" / "native_evidence_v2.json"
sys.path.insert(0, str(SCRIPTS))

from blueprint_translator.hybrid_evidence import (  # noqa: E402
    HybridEvidenceArtifactInvalid,
    build_hybrid_evidence_payload,
    mark_stale_edges,
    open_hybrid_evidence_repository,
    write_hybrid_evidence_artifacts,
)
from blueprint_translator.native_evidence_repository import (  # noqa: E402
    open_native_evidence_repository,
)
from blueprint_translator.native_evidence_store import write_native_evidence_artifacts  # noqa: E402
from blueprint_translator.context_pack import estimate_tokens  # noqa: E402
from build_hybrid_context_pack import (  # noqa: E402
    build_parser as build_hybrid_parser,
    build_hybrid_context_pack,
    render_hybrid_context_pack,
)
from link_blueprint_native_evidence import main as link_main  # noqa: E402


class HybridEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        source = self.root / "source.json"
        shutil.copyfile(FIXTURE, source)
        self.native_dir = self.root / "native"
        write_native_evidence_artifacts(source, self.native_dir)
        self.native = open_native_evidence_repository(self.native_dir)
        self.calls = [
            {
                "evidenceId": "bp://111111111111111111111111@222222222222222222222222/g/1/n/10",
                "memberName": "ComputeQuality",
                "owner": "FixtureMath",
                "signatureHints": ["float"],
            },
            {
                "evidenceId": "bp://111111111111111111111111@222222222222222222222222/g/1/n/11",
                "memberName": "ComputeQuality",
                "owner": "FixtureMath",
                "signatureHints": [],
            },
            {
                "evidenceId": "bp://111111111111111111111111@222222222222222222222222/g/1/n/12",
                "memberName": "MissingNativeFunction",
                "owner": "FixtureMath",
                "signatureHints": [],
            },
            {
                "evidenceId": "bp://111111111111111111111111@222222222222222222222222/g/1/n/13",
                "memberName": "ApplyClamp",
                "owner": "FixtureMath",
                "signatureHints": ["int"],
                "kind": "REFERENCES_NATIVE",
            },
            {
                "evidenceId": "bp://111111111111111111111111@222222222222222222222222/g/1/n/14",
                "memberName": "ComputeQuality",
                "owner": "FixtureMath",
                "signatureHints": ["float"],
                "kind": "MACRO",
            },
            {
                "evidenceId": "bp://111111111111111111111111@222222222222222222222222/g/1/n/15",
                "memberName": "ComputeQuality",
                "owner": "FixtureMath",
                "signatureHints": ["float"],
                "implementation": "BLUEPRINT_IMPLEMENTED",
            },
        ]

    def tearDown(self) -> None:
        self.native.close()
        self._temporary.cleanup()

    def test_owner_member_and_signature_resolution_is_fail_closed(self):
        payload = build_hybrid_evidence_payload(
            blueprint_calls=self.calls,
            native_functions=self.native.list_functions(),
            blueprint_revision_id="222222222222222222222222",
            blueprint_source_fingerprint="e" * 64,
            native_evidence_set_id=self.native.evidence_set_id,
            native_source_fingerprint=self.native.source_sha256,
        )
        by_source = {edge["sourceId"]: edge for edge in payload["edges"]}

        exact = by_source[self.calls[0]["evidenceId"]]
        self.assertEqual(exact["status"], "CONFIRMED")
        self.assertEqual(exact["relation"], "CALLS_NATIVE")
        self.assertEqual(exact["resolution"]["candidateCount"], 1)
        self.assertIn("0x1100", exact["targetId"])

        overload = by_source[self.calls[1]["evidenceId"]]
        self.assertEqual(overload["status"], "AMBIGUOUS")
        self.assertEqual(overload["resolution"]["candidateCount"], 2)
        self.assertEqual(overload["targetId"], "")

        missing = by_source[self.calls[2]["evidenceId"]]
        self.assertEqual(missing["status"], "SOURCE_NOT_AVAILABLE")
        self.assertEqual(missing["resolution"]["candidateCount"], 0)

        reference = by_source[self.calls[3]["evidenceId"]]
        self.assertEqual(reference["status"], "CONFIRMED")
        self.assertEqual(reference["relation"], "REFERENCES_NATIVE")

        macro = by_source[self.calls[4]["evidenceId"]]
        self.assertEqual(macro["status"], "NOT_RECOVERED")
        self.assertIn("BLUEPRINT_MACRO_NOT_NATIVE", macro["gaps"])
        blueprint_implemented = by_source[self.calls[5]["evidenceId"]]
        self.assertEqual(blueprint_implemented["status"], "NOT_RECOVERED")
        self.assertIn(
            "BLUEPRINT_IMPLEMENTATION_NOT_NATIVE",
            blueprint_implemented["gaps"],
        )

    def test_hybrid_json_is_authoritative_and_stale_dependencies_are_explicit(self):
        payload = build_hybrid_evidence_payload(
            blueprint_calls=self.calls[:1],
            native_functions=self.native.list_functions(),
            blueprint_revision_id="222222222222222222222222",
            blueprint_source_fingerprint="e" * 64,
            native_evidence_set_id=self.native.evidence_set_id,
            native_source_fingerprint=self.native.source_sha256,
        )
        hybrid_dir = self.root / "hybrid"
        result = write_hybrid_evidence_artifacts(payload, hybrid_dir)
        self.assertEqual(len(result["source_sha256"]), 64)

        with open_hybrid_evidence_repository(hybrid_dir) as repository:
            edges = repository.list_edges()
            self.assertEqual(edges[0]["status"], "CONFIRMED")
            inverse = repository.list_edges(relation="CALLED_BY_BLUEPRINT")
            self.assertEqual(len(inverse), 1)
            self.assertEqual(inverse[0]["sourceId"], edges[0]["targetId"])
            self.assertEqual(inverse[0]["targetId"], edges[0]["sourceId"])

        stale = mark_stale_edges(
            payload["edges"],
            current_blueprint_revision_id="different-revision",
            current_blueprint_source_fingerprint="e" * 64,
            current_native_source_fingerprint=self.native.source_sha256,
        )
        self.assertEqual(stale[0]["status"], "STALE")
        self.assertIn("STALE_BLUEPRINT_REVISION", stale[0]["gaps"])

        source = hybrid_dir / "hybrid_edges.json"
        tampered = json.loads(source.read_text(encoding="utf-8"))
        tampered["edges"][0]["status"] = "AMBIGUOUS"
        source.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaisesRegex(HybridEvidenceArtifactInvalid, "source.*hash"):
            open_hybrid_evidence_repository(hybrid_dir)

    def test_link_cli_and_context_pack_keep_source_types_and_gaps_separate(self):
        calls_path = self.root / "calls.json"
        calls_path.write_text(
            json.dumps(
                {
                    "schema": "blueprint-to-code-blueprint-native-calls/v1",
                    "blueprintRevisionId": "222222222222222222222222",
                    "blueprintSourceFingerprint": "e" * 64,
                    "calls": self.calls,
                }
            ),
            encoding="utf-8",
        )
        hybrid_dir = self.root / "linked"
        output = StringIO()
        with redirect_stdout(output):
            exit_code = link_main(
                [
                    "--calls-json",
                    str(calls_path),
                    "--native-evidence-dir",
                    str(self.native_dir),
                    "--output-dir",
                    str(hybrid_dir),
                ]
            )
        self.assertEqual(exit_code, 0, msg=output.getvalue())
        self.assertTrue(json.loads(output.getvalue())["ok"])
        with open_hybrid_evidence_repository(hybrid_dir) as hybrid:
            pack = build_hybrid_context_pack(
                hybrid,
                self.native,
                question="How does ComputeQuality reach native quality logic?",
                budget=2200,
                current_blueprint_revision_id="222222222222222222222222",
                current_blueprint_source_fingerprint="e" * 64,
            )
        rendered = render_hybrid_context_pack(pack)
        self.assertLessEqual(estimate_tokens(rendered), 2200)
        self.assertTrue(pack["blueprintConfirmedFacts"])
        self.assertTrue(pack["nativeConfirmedFacts"])
        self.assertTrue(pack["resolvedCrossSourceEdges"])
        self.assertTrue(pack["assumptions"])
        self.assertTrue(pack["runtimeOnlyGaps"])
        self.assertEqual(pack["staleProvenanceWarnings"], [])
        self.assertNotIn("FULL_DECOMPILE_SHOULD_NOT_APPEAR_IN_INDEX", rendered)

        args = build_hybrid_parser().parse_args(
            [
                "--asset-dir",
                str(self.root / "asset"),
                "--native-evidence-dir",
                str(self.native_dir),
                "--question",
                "quality",
            ]
        )
        self.assertEqual(args.hybrid_dir, Path("analysis") / "evidence_graph")


if __name__ == "__main__":
    unittest.main()
