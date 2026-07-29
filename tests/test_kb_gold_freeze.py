from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.benchmark import (  # noqa: E402
    validate_benchmark_gold_payload,
)
import blueprint_translator.kb_vnext.gold_freeze as gold_freeze_module  # noqa: E402
from blueprint_translator.kb_vnext.gold_freeze import (  # noqa: E402
    BLOCKED_BY_SIGNED_FREEZE_APPROVAL,
    FREEZE_PROPOSAL_SCHEMA,
    SIGNED_V2_GOLD_PROVENANCE_CONSUMER_REQUIRED,
    build_deterministic_query_diff,
    validate_and_propose_gold_freeze,
    validate_freeze_bindings,
)
from blueprint_translator.kb_vnext.gold_review import (  # noqa: E402
    build_review_pack,
    query_candidate_from_gold_case,
)
from blueprint_translator.kb_vnext.gold_review_v2 import (  # noqa: E402
    BLOCKED_BY_INDEPENDENT_REVIEW,
    GoldReviewV2Error,
    SIGNED_V2_RECEIPTS_REQUIRED,
)
from blueprint_translator.kb_vnext.signed_receipts import (  # noqa: E402
    TEST_ONLY,
)
import freeze_ark_kb_gold_reviews as freeze_cli_module  # noqa: E402


FREEZE_CLI = PROJECT_ROOT / "scripts" / "freeze_ark_kb_gold_reviews.py"
CASE_ID = "automated-freeze-contract-case-001"
TEST_AUTHOR_FINGERPRINT = hashlib.sha256(
    b"automated-freeze-contract-author-test-only"
).hexdigest()


def _case(
    case_id: str = CASE_ID,
    *,
    value: int = 5,
    review_status: str = "FIXTURE_EXACT",
) -> dict[str, object]:
    return {
        "id": case_id,
        "question": "What is the independently reviewed value?",
        "category": "FACT",
        "primaryDomain": "item_use",
        "entity": "/Game/Test/Asset.Asset",
        "requirements": {
            "answerMode": "FACT",
            "factTypes": ["ITEM_PROPERTY"],
            "factNames": ["BaseItemWeight"],
            "edgeTypes": [],
            "requiresNative": False,
            "requiresRuntime": False,
            "requiresMapEvidence": False,
            "evidenceLimit": 50,
            "budgetTokens": 2000,
        },
        "expected": {
            "route": "DB_SEMANTIC_COMPLETE",
            "identityUri": "/Game/Test/Asset.Asset",
            "facts": [
                {
                    "factType": "ITEM_PROPERTY",
                    "factName": "BaseItemWeight",
                    "valueKind": "NUMBER",
                    "value": value,
                    "status": "CONFIRMED",
                    "evidenceUri": "bp://test/default/BaseItemWeight",
                }
            ],
            "relationships": [],
            "gapCodes": [],
            "mustContainEvidence": True,
            "semanticExpectation": "EXACT",
        },
        "reviewStatus": review_status,
        "protocolBoundaryOnly": False,
        "negativeCase": "",
        "performancePath": "EXACT_CANONICAL_URI",
    }


def _gold_bytes(cases: list[dict[str, object]]) -> bytes:
    payload = {
        "schema": "ark-kb-query-gold-set/v1",
        "version": "automated-freeze-contract-v1",
        "selectionMode": "MANUAL_FIXED",
        "generatedFromCore": False,
        "authoredFrom": ["AUTOMATED_TEST_ONLY_CONTRACT"],
        "semanticCoverageLimitations": [],
        "cases": cases,
    }
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _pack(source_bytes: bytes, cases: list[dict[str, object]]) -> dict[str, object]:
    return build_review_pack(
        kind="query",
        author_id="automated-freeze-contract-author",
        author_key_fingerprint=TEST_AUTHOR_FINGERPRINT,
        seed="automated-freeze-contract-seed",
        selection_rule="MANUAL_FIXED_ALL_CASES",
        source_manifest_sha256=hashlib.sha256(source_bytes).hexdigest(),
        candidates=[query_candidate_from_gold_case(case) for case in cases],
        created_at="2026-07-29T12:00:00Z",
        tool_version="automated-freeze-contract/v1",
    )


def _artifact_bytes(
    case_id: str,
    *,
    expected: dict[str, object],
) -> bytes:
    artifact = {
        "schema": "ark-kb-gold-review-artifact/v2",
        "caseId": case_id,
        "verdict": "CONFIRMED",
        "answer": {"queryExpected": expected},
    }
    return (
        json.dumps(
            artifact,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _artifact_map_for_gold(
    payload: dict[str, object],
) -> dict[str, bytes]:
    cases = payload["cases"]
    assert isinstance(cases, list)
    return {
        str(case["id"]): _artifact_bytes(
            str(case["id"]),
            expected=deepcopy(case["expected"]),
        )
        for case in cases
    }


def _apply_expected_only_patch(
    payload: dict[str, object],
    patch: list[dict[str, object]],
) -> dict[str, object]:
    proposed = deepcopy(payload)
    cases = proposed["cases"]
    assert isinstance(cases, list)
    for operation in patch:
        path = str(operation["path"]).split("/")
        assert path[:2] == ["", "cases"]
        assert path[3] == "expected"
        case = cases[int(path[2])]
        assert isinstance(case, dict)
        case["expected"] = deepcopy(operation["value"])
    return proposed


class GoldFreezeProposalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tracked_target_path = (
            PROJECT_ROOT / "tests" / "fixtures" / "kb_query_gold_set.v1.json"
        )
        cls.tracked_target_bytes = cls.tracked_target_path.read_bytes()
        cls.tracked_target_sha256 = hashlib.sha256(
            cls.tracked_target_bytes
        ).hexdigest()
        raw_target = json.loads(cls.tracked_target_bytes)
        cls.tracked_target = raw_target
        cls.tracked_pack = _pack(
            cls.tracked_target_bytes,
            raw_target["cases"],
        )

    def setUp(self) -> None:
        self.cases = [_case()]
        self.target_bytes = _gold_bytes(self.cases)
        self.target_sha256 = hashlib.sha256(self.target_bytes).hexdigest()
        self.pack = _pack(self.target_bytes, self.cases)

    def test_schema_is_strict_proposal_and_provenance_contract(self) -> None:
        schema = json.loads(
            (
                PROJECT_ROOT
                / "schemas"
                / "kb_gold_freeze_proposal_v1.schema.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            schema["properties"]["schema"]["const"],
            FREEZE_PROPOSAL_SCHEMA,
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            schema["properties"]["productionGoldWritten"]["const"],
            False,
        )
        self.assertEqual(schema["properties"]["applyAllowed"]["const"], False)
        self.assertEqual(
            schema["properties"]["applyBlockers"]["prefixItems"][0]["const"],
            SIGNED_V2_GOLD_PROVENANCE_CONSUMER_REQUIRED,
        )
        self.assertEqual(
            schema["properties"]["expectedGateDelta"]["$ref"],
            "#/$defs/expectedGateDelta",
        )
        provenance = schema["$defs"]["provenance"]
        self.assertFalse(provenance["additionalProperties"])
        self.assertFalse(
            schema["$defs"]["queryExpected"]["additionalProperties"]
        )
        self.assertTrue(
            {
                "packSha256",
                "sourceManifestSha256",
                "sourceManifestRawSha256",
                "targetRawSha256",
                "expectedTargetRawSha256",
                "registryVersionSha256",
                "reviewReceiptSetSha256",
                "signedV2ProvenanceCaseCount",
                "caseArtifactBindings",
            }.issubset(provenance["required"])
        )
        self.assertEqual(
            schema["$defs"]["expectedGateDelta"]["properties"][
                "signedV2ReviewedCasesDelta"
            ]["const"],
            0,
        )
        self.assertIn(
            "reviewStatusPreserved",
            schema["$defs"]["caseChange"]["required"],
        )
        self.assertNotIn(
            "newReviewStatus",
            schema["$defs"]["caseChange"]["properties"],
        )
        self.assertEqual(
            schema["$defs"]["patchOperation"]["properties"]["path"]["pattern"],
            "^/cases/[0-9]+/expected$",
        )
        for definition in (
            "fact",
            "relationship",
            "identityEvidence",
            "sourceRevision",
        ):
            self.assertFalse(schema["$defs"][definition]["additionalProperties"])

    def test_real_json_schema_rejects_nested_query_expected_attacks(
        self,
    ) -> None:
        schema = json.loads(
            (
                PROJECT_ROOT
                / "schemas"
                / "kb_gold_freeze_proposal_v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        query_schema = {
            "$schema": schema["$schema"],
            "$defs": schema["$defs"],
            "$ref": "#/$defs/queryExpected",
        }
        validator = Draft202012Validator(query_schema)
        canonical = deepcopy(self.cases[0]["expected"])
        validator.validate(canonical)

        valid_revision = {
            "sourceKind": "BLUEPRINT_EVIDENCE",
            "sourceUri": "evidence://test/source",
            "sourceFingerprint": "source-fingerprint",
            "producerVersion": "test-only/v1",
            "schemaVersion": "test-only/v1",
            "freshness": "FRESH",
        }
        valid_identity_evidence = {
            "evidenceUri": "evidence://test/identity",
            "evidenceRole": "IDENTITY",
            "freshness": "FRESH",
            "sourceRevision": valid_revision,
        }
        valid_relationship = {
            "edgeType": "TEST_EDGE",
            "targetUri": "/Game/Test/Target.Target",
            "status": "CONFIRMED",
            "evidenceUri": "evidence://test/edge",
        }
        attacks: list[dict[str, object]] = []
        for replacement in (
            {"facts": [{}]},
            {"relationships": [{"evil": True}]},
            {"identityEvidence": {}},
        ):
            attack = deepcopy(canonical)
            attack.update(replacement)
            attacks.append(attack)

        boolean_as_text = deepcopy(canonical)
        boolean_as_text["facts"][0]["valueKind"] = "BOOLEAN"
        boolean_as_text["facts"][0]["value"] = "false"
        attacks.append(boolean_as_text)

        relationship_extra = deepcopy(canonical)
        relationship_extra["relationships"] = [
            {**valid_relationship, "evil": True}
        ]
        attacks.append(relationship_extra)

        revision_extra = deepcopy(canonical)
        revision_extra["identityEvidence"] = {
            **valid_identity_evidence,
            "sourceRevision": {**valid_revision, "evil": True},
        }
        attacks.append(revision_extra)

        for index, attack in enumerate(attacks, start=1):
            with self.subTest(attack=index):
                with self.assertRaises(ValidationError):
                    validator.validate(attack)

    def test_no_receipts_is_stably_blocked_without_answer_leakage(self) -> None:
        result = validate_and_propose_gold_freeze(
            self.pack,
            [],
            registry=None,
            expected_registry_sha256=None,
            expected_pack_author_key_fingerprint=None,
            artifact_root=None,
            source_manifest_bytes=self.target_bytes,
            expected_source_manifest_sha256=self.target_sha256,
            target_bytes=self.target_bytes,
            expected_target_sha256=self.target_sha256,
            target_relative_path="tests/fixtures/test-only-gold.json",
            trust_context=TEST_ONLY,
        )

        encoded = json.dumps(result, ensure_ascii=False).casefold()
        self.assertEqual(result["status"], BLOCKED_BY_INDEPENDENT_REVIEW)
        self.assertFalse(result["proposalReady"])
        self.assertFalse(result["applyAllowed"])
        self.assertFalse(result["productionGoldWritten"])
        self.assertNotIn("caseChanges", result)
        self.assertNotIn("independently reviewed value", encoded)
        self.assertNotIn('"expected"', encoded)
        self.assertNotIn("planner", encoded)

    def test_caller_cannot_supply_forged_validation_authority(self) -> None:
        self.assertFalse(
            hasattr(gold_freeze_module, "propose_gold_freeze")
        )
        self.assertNotIn(
            "propose_gold_freeze",
            gold_freeze_module.__all__,
        )
        with self.assertRaisesRegex(
            TypeError,
            "unexpected keyword argument 'validation'",
        ):
            validate_and_propose_gold_freeze(
                self.pack,
                [],
                validation=object(),
                registry=None,
                expected_registry_sha256=None,
                expected_pack_author_key_fingerprint=None,
                artifact_root=None,
                source_manifest_bytes=self.target_bytes,
                expected_source_manifest_sha256=self.target_sha256,
                target_bytes=self.target_bytes,
                expected_target_sha256=self.target_sha256,
                target_relative_path="tests/fixtures/test-only-gold.json",
                trust_context=TEST_ONLY,
            )

    def test_v1_and_automation_identity_never_become_ready(self) -> None:
        automation_pack = build_review_pack(
            kind="query",
            author_id="automated-freeze-contract-author",
            author_key_fingerprint=(
                "automation:automated-freeze-contract-author"
            ),
            seed="automated-freeze-contract-seed",
            selection_rule="MANUAL_FIXED_ALL_CASES",
            source_manifest_sha256=self.target_sha256,
            candidates=[
                query_candidate_from_gold_case(case) for case in self.cases
            ],
            created_at="2026-07-29T12:00:00Z",
            tool_version="automated-freeze-contract/v1",
        )
        legacy = validate_and_propose_gold_freeze(
            automation_pack,
            [{"schema": "ark-kb-gold-review/v1"}],
            registry=None,
            expected_registry_sha256=None,
            expected_pack_author_key_fingerprint=None,
            artifact_root=None,
            source_manifest_bytes=self.target_bytes,
            expected_source_manifest_sha256=self.target_sha256,
            target_bytes=self.target_bytes,
            expected_target_sha256=self.target_sha256,
            target_relative_path="tests/fixtures/test-only-gold.json",
            trust_context=TEST_ONLY,
        )

        self.assertEqual(legacy["status"], SIGNED_V2_RECEIPTS_REQUIRED)
        self.assertFalse(legacy["proposalReady"])
        self.assertNotIn("caseChanges", legacy)

    def test_raw_source_and_target_hashes_are_explicitly_bound(self) -> None:
        target = validate_freeze_bindings(
            self.pack,
            source_manifest_bytes=self.target_bytes,
            expected_source_manifest_sha256=self.target_sha256,
            target_bytes=self.target_bytes,
            expected_target_sha256=self.target_sha256,
        )
        self.assertEqual(target["schema"], "ark-kb-query-gold-set/v1")

        for field, override, error in (
            (
                "source",
                {"expected_source_manifest_sha256": "0" * 64},
                "source manifest",
            ),
            (
                "target",
                {"expected_target_sha256": "0" * 64},
                "target",
            ),
            (
                "changed-target",
                {"target_bytes": self.target_bytes + b" "},
                "target",
            ),
            (
                "changed-target-with-updated-operator-hash",
                {
                    "target_bytes": self.target_bytes + b" ",
                    "expected_target_sha256": hashlib.sha256(
                        self.target_bytes + b" "
                    ).hexdigest(),
                },
                "changed since review pack export",
            ),
        ):
            arguments: dict[str, object] = {
                "source_manifest_bytes": self.target_bytes,
                "expected_source_manifest_sha256": self.target_sha256,
                "target_bytes": self.target_bytes,
                "expected_target_sha256": self.target_sha256,
            }
            arguments.update(override)
            with self.subTest(field=field):
                with self.assertRaisesRegex(GoldReviewV2Error, error):
                    validate_freeze_bindings(
                        self.pack,
                        **arguments,
                    )

    def test_deterministic_diff_uses_only_immutable_artifact_bytes(
        self,
    ) -> None:
        artifacts = _artifact_map_for_gold(self.tracked_target)
        unchanged = build_deterministic_query_diff(
            self.tracked_target_bytes,
            artifacts,
        )
        self.assertEqual(unchanged["caseChanges"], [])
        self.assertEqual(unchanged["jsonPatch"], [])
        self.assertEqual(
            unchanged["expectedGateDelta"]["signedV2ProvenanceCaseCount"],
            len(self.tracked_target["cases"]),
        )
        changed_case = next(
            case
            for case in self.tracked_target["cases"]
            if case["reviewStatus"] == "FIXTURE_EXACT"
            and case["expected"]["facts"]
            and case["expected"]["facts"][0]["valueKind"] == "NUMBER"
            and isinstance(case["expected"]["facts"][0]["value"], (int, float))
        )
        changed_expected = deepcopy(changed_case["expected"])
        changed_expected["facts"][0]["value"] += 1
        artifacts[changed_case["id"]] = _artifact_bytes(
            changed_case["id"],
            expected=changed_expected,
        )

        first = build_deterministic_query_diff(
            self.tracked_target_bytes,
            artifacts,
        )
        second = build_deterministic_query_diff(
            self.tracked_target_bytes,
            dict(reversed(list(artifacts.items()))),
        )
        proposed = _apply_expected_only_patch(
            self.tracked_target,
            first["jsonPatch"],
        )
        validated = validate_benchmark_gold_payload(proposed)

        self.assertEqual(first, second)
        self.assertEqual(len(first["caseChanges"]), 1)
        self.assertEqual(first["caseChanges"][0]["caseId"], changed_case["id"])
        self.assertTrue(first["caseChanges"][0]["reviewStatusPreserved"])
        self.assertEqual(
            first["caseChanges"][0]["reviewStatus"],
            changed_case["reviewStatus"],
        )
        self.assertEqual(len(first["jsonPatch"]), 1)
        self.assertTrue(
            all(
                operation["path"].endswith("/expected")
                for operation in first["jsonPatch"]
            )
        )
        self.assertEqual(
            [case.review_status for case in validated],
            [case["reviewStatus"] for case in self.tracked_target["cases"]],
        )
        self.assertEqual(
            first["expectedGateDelta"]["signedV2ReviewedCasesDelta"],
            0,
        )
        self.assertEqual(
            first["expectedGateDelta"]["signedV2ProvenanceCaseCount"],
            len(self.tracked_target["cases"]),
        )
        self.assertEqual(
            first["expectedGateDelta"]["fixtureExactCasesDelta"],
            0,
        )
        self.assertEqual(
            first["expectedGateDelta"]["qualityGateEvaluation"],
            "PENDING_FULL_SNAPSHOT_REBUILD",
        )
        self.assertFalse(
            first["expectedGateDelta"]["cutoverEligibleClaimed"]
        )

    def test_diff_rejects_untyped_review_answer(self) -> None:
        artifact = {
            "schema": "ark-kb-gold-review-artifact/v2",
            "caseId": CASE_ID,
            "verdict": "CONFIRMED",
            "answer": {"facts": []},
        }
        artifact_bytes = (
            json.dumps(artifact, sort_keys=True) + "\n"
        ).encode()

        with self.assertRaisesRegex(
            GoldReviewV2Error,
            "queryExpected",
        ):
            build_deterministic_query_diff(
                self.target_bytes,
                {CASE_ID: artifact_bytes},
            )

    def test_diff_rejects_malformed_query_expected_fields(self) -> None:
        valid_expected = dict(self.cases[0]["expected"])
        for field, value in (
            ("identityUri", 42),
            ("facts", ["not-an-object"]),
            ("relationships", [None]),
            ("gapCodes", ["DUPLICATE", "DUPLICATE"]),
            ("identityEvidence", []),
        ):
            malformed = dict(valid_expected)
            malformed[field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    GoldReviewV2Error,
                    "queryExpected",
                ):
                    build_deterministic_query_diff(
                        self.target_bytes,
                        {
                            CASE_ID: _artifact_bytes(
                                CASE_ID,
                                expected=malformed,
                            )
                        },
                    )

    def test_diff_rejects_noncanonical_gold_semantic_attacks(self) -> None:
        exact_case = next(
            case
            for case in self.tracked_target["cases"]
            if case["expected"]["semanticExpectation"] == "EXACT"
            and case["expected"]["facts"]
        )
        gap_case = next(
            case
            for case in self.tracked_target["cases"]
            if case["expected"]["semanticExpectation"] == "GAP_ONLY"
        )
        empty_exact = deepcopy(exact_case["expected"])
        empty_exact["facts"] = []
        empty_exact["relationships"] = []
        wrong_route = deepcopy(exact_case["expected"])
        wrong_route["route"] = "DB_PARTIAL"
        malformed_fact = deepcopy(exact_case["expected"])
        malformed_fact["facts"] = [{}]
        unknown_gap = deepcopy(gap_case["expected"])
        unknown_gap["gapCodes"] = ["AUTOMATED_UNKNOWN_GAP_ATTACK"]
        attacks = (
            (
                "empty-exact",
                exact_case["id"],
                empty_exact,
                "exact semantic case has no expected answer",
            ),
            (
                "wrong-route",
                exact_case["id"],
                wrong_route,
                "exact semantic route must be complete",
            ),
            (
                "malformed-fact",
                exact_case["id"],
                malformed_fact,
                "expected fact 1 missing fields",
            ),
            (
                "unknown-gap",
                gap_case["id"],
                unknown_gap,
                "unknown gapCodes",
            ),
        )

        for attack, case_id, expected, error in attacks:
            artifacts = _artifact_map_for_gold(self.tracked_target)
            artifacts[case_id] = _artifact_bytes(
                case_id,
                expected=expected,
            )
            with self.subTest(attack=attack):
                with self.assertRaisesRegex(GoldReviewV2Error, error):
                    build_deterministic_query_diff(
                        self.tracked_target_bytes,
                        artifacts,
                    )

    def test_cli_no_receipts_writes_no_proposal_or_gold(self) -> None:
        proposal_root = (
            PROJECT_ROOT
            / "review_work"
            / "ark_kb_gold"
            / "freeze_proposals"
        )
        proposal_root.mkdir(parents=True, exist_ok=True)
        with (
            tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary,
            tempfile.TemporaryDirectory(dir=proposal_root) as proposal_dir,
        ):
            root = Path(temporary)
            pack_path = root / "review-pack.json"
            output_path = Path(proposal_dir) / "proposal.json"
            pack_path.write_text(
                json.dumps(self.tracked_pack, ensure_ascii=False),
                encoding="utf-8",
            )
            before = hashlib.sha256(
                self.tracked_target_path.read_bytes()
            ).hexdigest()

            completed = subprocess.run(
                [
                    sys.executable,
                    str(FREEZE_CLI),
                    "--pack",
                    str(pack_path),
                    "--source-manifest",
                    str(self.tracked_target_path),
                    "--expected-source-manifest-sha256",
                    self.tracked_target_sha256,
                    "--gold-target",
                    str(self.tracked_target_path),
                    "--expected-gold-target-sha256",
                    self.tracked_target_sha256,
                    "--output",
                    str(output_path),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            after = hashlib.sha256(
                self.tracked_target_path.read_bytes()
            ).hexdigest()
            output_exists = output_path.exists()

        self.assertEqual(completed.returncode, 2, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], BLOCKED_BY_INDEPENDENT_REVIEW)
        self.assertFalse(result["proposalReady"])
        self.assertFalse(result["applyAllowed"])
        self.assertFalse(result["productionGoldWritten"])
        self.assertEqual(before, after)
        self.assertFalse(output_exists)
        self.assertNotIn("independently reviewed value", completed.stdout)

    def test_cli_apply_is_always_blocked_and_target_is_unchanged(self) -> None:
        proposal_root = (
            PROJECT_ROOT
            / "review_work"
            / "ark_kb_gold"
            / "freeze_proposals"
        )
        proposal_root.mkdir(parents=True, exist_ok=True)
        with (
            tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary,
            tempfile.TemporaryDirectory(dir=proposal_root) as proposal_dir,
        ):
            root = Path(temporary)
            pack_path = root / "review-pack.json"
            output_path = Path(proposal_dir) / "proposal.json"
            pack_path.write_text(
                json.dumps(self.pack, ensure_ascii=False),
                encoding="utf-8",
            )
            before = self.tracked_target_path.read_bytes()

            completed = subprocess.run(
                [
                    sys.executable,
                    str(FREEZE_CLI),
                    "--pack",
                    str(pack_path),
                    "--source-manifest",
                    str(self.tracked_target_path),
                    "--expected-source-manifest-sha256",
                    self.tracked_target_sha256,
                    "--gold-target",
                    str(self.tracked_target_path),
                    "--expected-gold-target-sha256",
                    self.tracked_target_sha256,
                    "--output",
                    str(output_path),
                    "--apply",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            after = self.tracked_target_path.read_bytes()
            output_exists = output_path.exists()
            bare_apply = subprocess.run(
                [sys.executable, str(FREEZE_CLI), "--apply"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 3, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(
            result["status"],
            BLOCKED_BY_SIGNED_FREEZE_APPROVAL,
        )
        self.assertFalse(result["proposalReady"])
        self.assertFalse(result["applyAllowed"])
        self.assertFalse(result["productionGoldWritten"])
        self.assertEqual(before, after)
        self.assertFalse(output_exists)
        self.assertEqual(bare_apply.returncode, 3, bare_apply.stderr)
        self.assertEqual(
            json.loads(bare_apply.stdout)["status"],
            BLOCKED_BY_SIGNED_FREEZE_APPROVAL,
        )

    def test_apply_blocker_precedes_argparse_and_business_file_access(
        self,
    ) -> None:
        output = io.StringIO()
        with (
            patch.object(
                freeze_cli_module,
                "_parser",
                side_effect=AssertionError("argparse must not run"),
            ),
            patch.object(
                Path,
                "resolve",
                side_effect=AssertionError("filesystem resolve must not run"),
            ),
            patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("business file read must not run"),
            ),
            redirect_stdout(output),
        ):
            status = freeze_cli_module.main(["--apply"])

        self.assertEqual(status, 3)
        self.assertEqual(
            json.loads(output.getvalue())["status"],
            BLOCKED_BY_SIGNED_FREEZE_APPROVAL,
        )

    def test_cli_rejects_unallowlisted_or_colliding_paths(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            root = Path(temporary)
            pack_path = root / "review-pack.json"
            arbitrary_target = root / "gold.json"
            pack_path.write_text(
                json.dumps(self.pack, ensure_ascii=False),
                encoding="utf-8",
            )
            arbitrary_target.write_bytes(self.target_bytes)

            unallowlisted = subprocess.run(
                [
                    sys.executable,
                    str(FREEZE_CLI),
                    "--pack",
                    str(pack_path),
                    "--source-manifest",
                    str(arbitrary_target),
                    "--expected-source-manifest-sha256",
                    self.target_sha256,
                    "--gold-target",
                    str(arbitrary_target),
                    "--expected-gold-target-sha256",
                    self.target_sha256,
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            before = self.tracked_target_path.read_bytes()
            collision = subprocess.run(
                [
                    sys.executable,
                    str(FREEZE_CLI),
                    "--pack",
                    str(pack_path),
                    "--source-manifest",
                    str(self.tracked_target_path),
                    "--expected-source-manifest-sha256",
                    self.tracked_target_sha256,
                    "--gold-target",
                    str(self.tracked_target_path),
                    "--expected-gold-target-sha256",
                    self.tracked_target_sha256,
                    "--output",
                    str(self.tracked_target_path),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            after = self.tracked_target_path.read_bytes()

        self.assertEqual(unallowlisted.returncode, 1)
        self.assertIn(
            "allowlisted query Gold target",
            json.loads(unallowlisted.stderr)["error"],
        )
        self.assertEqual(collision.returncode, 1)
        self.assertIn(
            "proposal output",
            json.loads(collision.stderr)["error"],
        )
        self.assertEqual(before, after)

    def test_cli_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            pack_path = Path(temporary) / "duplicate-pack.json"
            pack_path.write_text(
                '{"schema":"first","schema":"second"}',
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(FREEZE_CLI),
                    "--pack",
                    str(pack_path),
                    "--source-manifest",
                    str(self.tracked_target_path),
                    "--expected-source-manifest-sha256",
                    self.tracked_target_sha256,
                    "--gold-target",
                    str(self.tracked_target_path),
                    "--expected-gold-target-sha256",
                    self.tracked_target_sha256,
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 1)
        result = json.loads(completed.stderr)
        self.assertEqual(result["status"], "INVALID")
        self.assertIn("duplicate JSON key", result["error"])

    def test_cli_never_overwrites_an_existing_proposal(self) -> None:
        proposal_root = (
            PROJECT_ROOT
            / "review_work"
            / "ark_kb_gold"
            / "freeze_proposals"
        )
        proposal_root.mkdir(parents=True, exist_ok=True)
        with (
            tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary,
            tempfile.TemporaryDirectory(dir=proposal_root) as proposal_dir,
        ):
            pack_path = Path(temporary) / "review-pack.json"
            output_path = Path(proposal_dir) / "proposal.json"
            pack_path.write_text(
                json.dumps(self.pack, ensure_ascii=False),
                encoding="utf-8",
            )
            output_path.write_bytes(b"do-not-overwrite")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(FREEZE_CLI),
                    "--pack",
                    str(pack_path),
                    "--source-manifest",
                    str(self.tracked_target_path),
                    "--expected-source-manifest-sha256",
                    self.tracked_target_sha256,
                    "--gold-target",
                    str(self.tracked_target_path),
                    "--expected-gold-target-sha256",
                    self.tracked_target_sha256,
                    "--output",
                    str(output_path),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            after = output_path.read_bytes()

        self.assertEqual(completed.returncode, 1)
        self.assertIn(
            "must not already exist",
            json.loads(completed.stderr)["error"],
        )
        self.assertEqual(after, b"do-not-overwrite")

    def test_cli_rejects_source_or_target_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as external:
            root = Path(external)
            pack_path = root / "review-pack.json"
            target_path = root / "gold.json"
            pack_path.write_text(
                json.dumps(self.pack, ensure_ascii=False),
                encoding="utf-8",
            )
            target_path.write_bytes(self.target_bytes)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(FREEZE_CLI),
                    "--pack",
                    str(pack_path),
                    "--source-manifest",
                    str(target_path),
                    "--expected-source-manifest-sha256",
                    self.target_sha256,
                    "--gold-target",
                    str(target_path),
                    "--expected-gold-target-sha256",
                    self.target_sha256,
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 1)
        result = json.loads(completed.stderr)
        self.assertEqual(result["status"], "INVALID")
        self.assertIn("outside repository root", result["error"])


if __name__ == "__main__":
    unittest.main()
