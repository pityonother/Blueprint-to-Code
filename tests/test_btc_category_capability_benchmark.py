from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_btc_category_capabilities as benchmark  # noqa: E402
from blueprint_translator.evidence_writer import (  # noqa: E402
    write_evidence_artifacts_from_payload,
)


def _pin(
    native_id: str,
    name: str,
    direction: str,
    *,
    links: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "id": native_id,
        "persistent_guid": native_id,
        "name": name,
        "direction": direction,
        "category": "exec",
        "subcategory": "",
        "default": "",
        "default_object": "",
        "links": links or [],
        "source": "fixture_pin_reader",
        "confidence": "high",
    }


def _publish_fixture(
    root: Path,
    *,
    asset_name: str,
    object_path: str,
    with_content: bool,
    exact_link: bool = False,
    heuristic_link: bool = False,
) -> Path:
    asset_dir = root / asset_name
    source_path = asset_dir / "source" / f"{asset_name}.uasset"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(("fixture-package:" + asset_name).encode("utf-8"))
    graphs: list[dict[str, object]] = []
    if with_content:
        links = []
        if exact_link or heuristic_link:
            links.append(
                {
                    "target_node": "Target",
                    "target_pin_id": "TARGET-PIN",
                    "target_pin": "execute",
                    "kind": "exec",
                    "resolution_status": (
                        "resolved_pin" if exact_link else "resolved_pin_heuristic"
                    ),
                }
            )
        graphs = [
            {
                "graph": "EventGraph",
                "graph_type": "EventGraph",
                "export_index": 7,
                "status": "complete",
                "confidence": "high",
                "payload": {
                    "metadata": {
                        "asset_name": asset_name,
                        "graph_name": "EventGraph",
                        "graph_type": "EventGraph",
                        "uasset_export_index": 7,
                        "uasset_read_status": "complete",
                    },
                    "nodes": [
                        {
                            "index": 1,
                            "name": "Source",
                            "event": "ReceiveBeginPlay",
                            "pins": [
                                _pin(
                                    "SOURCE-PIN",
                                    "then",
                                    "EGPD_Output",
                                    links=links,
                                )
                            ],
                        },
                        {
                            "index": 2,
                            "name": "Target",
                            "function": "ApplyBuff",
                            "pins": [
                                _pin(
                                    "TARGET-PIN",
                                    "execute",
                                    "EGPD_Input",
                                )
                            ],
                        },
                    ],
                },
            }
        ]
    write_evidence_artifacts_from_payload(
        object_path,
        source_path,
        {
            "asset_name": asset_name,
            "asset_path": object_path,
            "graphs": graphs,
            "class_defaults": {
                "variables": (
                    {
                        "BuffToApply": {
                            "type": "Class",
                            "value": "/Game/Test/Buff_Test.Buff_Test_C",
                            "confidence": "high",
                            "source": "fixture",
                        }
                    }
                    if with_content
                    else {}
                )
            },
        },
        asset_dir,
    )
    return asset_dir


def _sample(
    index: int,
    *,
    code: str,
    target_kind: str = "CLASS_DEFINITION",
    action: str = "READ_BLUEPRINT_CLASS_DEFINITION",
    target_path: str | None = None,
) -> dict[str, object]:
    object_path = target_path or f"/Game/Test/Asset{index}.Asset{index}"
    return {
        "sampleIndex": index,
        "clusterId": f"class-{index:02d}",
        "captureIsolationKey": f"class-{index:02d}-capture",
        "exactClassPath": object_path + "_C",
        "memberObjectCount": index,
        "propagationScope": "CLASS_LEVEL_ONLY",
        "readStatus": "PLANNED_NOT_READ",
        "recommendedAction": action,
        "targetKind": target_kind,
        "targetPath": object_path,
        "coverageCategoryGroups": [
            {"code": code, "labelZh": code, "status": "CANDIDATE"}
        ],
    }


class BtcCategoryCapabilityBenchmarkTests(unittest.TestCase):
    def test_verified_native_evidence_is_formally_queryable_and_revision_bound(self):
        target_path = "/Script/ShooterGame.PrimalCameraProbeActor"
        evidence_ref = (
            "native://binary-sha/ShooterGameEditor-ShooterGame.dll/0x7B93E0"
        )
        evidence_set_id = "native-set://binary-sha/recipe-sha"
        payload = {
            "schema": "blueprint-to-code-native-evidence-set/v2",
            "evidenceSetId": evidence_set_id,
            "trust": {"status": "VERIFIED"},
            "provenance": {
                "pdb": {"loaded": True, "matchesBinary": True},
            },
            "targets": [
                {
                    "qualifiedName": "APrimalCameraProbeActor::PlayFromHere",
                    "evidenceId": evidence_ref,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence_path = Path(temp_dir) / "native-evidence.json"
            evidence_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            profiles = benchmark.profile_native_evidence(
                evidence_path,
                [target_path],
            )
            payload["provenance"]["pdb"]["matchesBinary"] = False
            evidence_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            mismatched_profiles = benchmark.profile_native_evidence(
                evidence_path,
                [target_path],
            )

        native_profile = profiles[target_path]
        self.assertTrue(native_profile["formallyQueryable"])
        self.assertEqual(native_profile["evidenceRevisionId"], evidence_set_id)
        self.assertFalse(mismatched_profiles[target_path]["formallyQueryable"])

        report = benchmark.build_capability_report(
            sample_plan={
                "sampleCount": 1,
                "samples": [
                    _sample(
                        3,
                        code="NATIVE_CAMERA",
                        target_kind="NATIVE_CLASS",
                        action="ROUTE_NATIVE_CLASS_EVIDENCE",
                        target_path=target_path,
                    )
                ],
            },
            question_contracts={
                "NATIVE_CAMERA:CANDIDATE": {
                    "questionZh": "镜头如何切换？",
                    "requiredSignalGroups": [],
                    "requiresExactFlow": False,
                }
            },
            blueprint_profiles={},
            native_profiles=profiles,
            reviewed_assessments={
                "3": {
                    "status": "CLOSED_EXACT",
                    "claims": [
                        {
                            "textZh": "PlayFromHere 切换镜头。",
                            "evidenceRefs": [evidence_ref],
                        }
                    ],
                    "blockingGaps": [],
                }
            },
        )

        row = report["samples"][0]
        self.assertTrue(row["p0"]["ready"])
        self.assertEqual(row["p0"]["evidenceRevisionId"], evidence_set_id)
        self.assertEqual(row["route"]["effectiveReader"], "NATIVE_EVIDENCE")
        self.assertEqual(row["result"]["benchmarkClosureStatus"], "CLOSED_EXACT")

    def test_data_asset_route_requires_confirmed_fields_and_formal_queryability(self):
        target_path = "/Script/ShooterGame.ModDataAsset"
        plan = {
            "sampleCount": 1,
            "samples": [
                _sample(
                    27,
                    code="DATA_CONFIGURATION",
                    target_kind="NATIVE_CLASS",
                    action="ROUTE_NATIVE_CLASS_EVIDENCE",
                    target_path=target_path,
                )
            ],
        }
        contracts = {
            "DATA_CONFIGURATION:CANDIDATE": {
                "questionZh": "模组配置字段是什么？",
                "requiredSignalGroups": [],
                "requiresExactFlow": False,
            }
        }

        empty = benchmark.build_capability_report(
            sample_plan=plan,
            question_contracts=contracts,
            blueprint_profiles={},
            data_asset_profiles={
                target_path: {
                    "captureIntegrityStatus": "PASS",
                    "businessFactCount": 0,
                    "formallyQueryable": True,
                    "evidenceRevisionId": "aggregate-empty",
                }
            },
        )
        recovered = benchmark.build_capability_report(
            sample_plan=plan,
            question_contracts=contracts,
            blueprint_profiles={},
            data_asset_profiles={
                target_path: {
                    "captureIntegrityStatus": "PASS",
                    "businessFactCount": 22,
                    "formallyQueryable": True,
                    "evidenceRevisionId": "aggregate-fields",
                    "objectSampleCount": 2,
                    "readyObjectCount": 2,
                }
            },
        )

        self.assertFalse(empty["samples"][0]["p0"]["ready"])
        self.assertEqual(
            empty["samples"][0]["result"]["benchmarkClosureStatus"],
            "IDENTITY_ONLY",
        )
        row = recovered["samples"][0]
        self.assertTrue(row["p0"]["ready"])
        self.assertEqual(row["p0"]["evidenceRevisionId"], "aggregate-fields")
        self.assertEqual(row["route"]["evidenceOrigin"], "CANONICAL_CURRENT")
        self.assertEqual(row["result"]["benchmarkClosureStatus"], "PARTIAL")

    def test_data_asset_discovery_requires_every_planned_object_to_be_canonical(self):
        source_class = "/Script/ShooterGame.VRBattleGroupDataAsset"
        first_path = "/Game/Test/VRGroupA.VRGroupA"
        second_path = "/Game/Test/VRGroupB.VRGroupB"

        def profile(object_path: str, revision: str, fields: int) -> dict[str, object]:
            return {
                "asset": {
                    "assetId": object_path.rsplit(".", 1)[-1],
                    "name": object_path.rsplit(".", 1)[-1],
                    "objectPath": object_path,
                    "revisionId": revision,
                },
                "authority": {"captureIntegrityStatus": "PASS"},
                "content": {
                    "assetFieldCount": fields,
                    "businessFactCount": fields,
                },
                "gaps": {"blockingStatusCount": 0},
                "_searchRecords": [
                    {
                        "kind": "asset_field",
                        "ref": f"bp://asset@{revision}/asset/field/Units.count",
                        "text": "Units.count",
                    }
                ],
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "batch_plan.json").write_text(
                json.dumps(
                    {
                        "samples": [
                            {
                                "sampleIndex": 1,
                                "sourceClass": source_class,
                                "targetPath": first_path,
                            },
                            {
                                "sampleIndex": 2,
                                "sourceClass": source_class,
                                "targetPath": second_path,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            incomplete = benchmark.discover_data_asset_profiles(
                [root],
                canonical_ready_profiles={
                    first_path: profile(first_path, "revision-a", 2),
                },
            )
            complete = benchmark.discover_data_asset_profiles(
                [root],
                canonical_ready_profiles={
                    first_path: profile(first_path, "revision-a", 2),
                    second_path: profile(second_path, "revision-b", 3),
                },
            )

        self.assertFalse(incomplete[source_class]["formallyQueryable"])
        self.assertEqual(incomplete[source_class]["businessFactCount"], 2)
        self.assertTrue(complete[source_class]["formallyQueryable"])
        self.assertEqual(complete[source_class]["businessFactCount"], 5)
        self.assertEqual(complete[source_class]["readyObjectCount"], 2)
        self.assertEqual(
            complete[source_class]["revisionIds"],
            ["revision-a", "revision-b"],
        )

    def test_newer_data_asset_plan_supersedes_obsolete_plan_for_same_class(self):
        source_class = "/Script/ShooterGame.ModDataAsset"
        obsolete_path = "/ASBExportGun/Old.Old"
        current_path = "/DinoDefense/Current.Current"

        def profile(object_path, revision, field_count):
            return {
                "asset": {"objectPath": object_path, "revisionId": revision},
                "authority": {"captureIntegrityStatus": "PASS"},
                "content": {"assetFieldCount": field_count},
                "gaps": {"blockingStatusCount": 0},
                "_searchRecords": [],
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_root = root / "old"
            current_root = root / "current"
            old_root.mkdir()
            current_root.mkdir()
            (old_root / "batch_plan.json").write_text(
                json.dumps(
                    {
                        "samples": [
                            {
                                "sourceClass": source_class,
                                "targetPath": obsolete_path,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (current_root / "batch_plan.json").write_text(
                json.dumps(
                    {
                        "samples": [
                            {
                                "sourceClass": source_class,
                                "targetPath": current_path,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            discovered = benchmark.discover_data_asset_profiles(
                [old_root, current_root],
                canonical_ready_profiles={
                    current_path: profile(current_path, "revision-current", 4)
                },
            )

        self.assertTrue(discovered[source_class]["formallyQueryable"])
        self.assertEqual(discovered[source_class]["plannedObjectCount"], 1)
        self.assertEqual(
            [item["objectPath"] for item in discovered[source_class]["objects"]],
            [current_path],
        )

    def test_plan_validation_rejects_duplicate_sample_indices(self):
        plan = {"sampleCount": 2, "samples": [_sample(1, code="A"), _sample(1, code="B")]}

        with self.assertRaisesRegex(ValueError, "duplicate sampleIndex"):
            benchmark.validate_sample_plan(plan)

    def test_profile_counts_exact_pin_and_link_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _publish_fixture(
                Path(temp_dir),
                asset_name="FlowAsset",
                object_path="/Game/Test/FlowAsset.FlowAsset",
                with_content=True,
                exact_link=True,
            )

            profile = benchmark.profile_blueprint_evidence(asset_dir)

        self.assertEqual(profile["content"]["graphCount"], 1)
        self.assertEqual(profile["content"]["nodeCount"], 2)
        self.assertEqual(profile["content"]["defaultCount"], 1)
        self.assertEqual(profile["identity"]["nativePinIdExactCount"], 2)
        self.assertEqual(profile["identity"]["persistentGuidExactCount"], 2)
        self.assertEqual(profile["identity"]["exactLinkCount"], 1)
        self.assertEqual(profile["identity"]["heuristicLinkCount"], 0)
        self.assertEqual(
            profile["authority"]["evidenceDecision"]["evidenceAvailability"],
            "FORMAL_QUERY",
        )

    def test_silent_empty_evidence_is_identity_only_even_when_authoritative(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _publish_fixture(
                Path(temp_dir),
                asset_name="EmptyData",
                object_path="/Game/Test/EmptyData.EmptyData",
                with_content=False,
            )
            profile = benchmark.profile_blueprint_evidence(asset_dir)

            result = benchmark.classify_blueprint_sample(
                profile,
                question_contract={
                    "questionZh": "这个配置里有哪些业务字段？",
                    "requiredSignalGroups": [
                        {"id": "business-field", "terms": ["Requirement", "Reward"]}
                    ],
                    "requiresExactFlow": False,
                },
            )

        self.assertTrue(profile["content"]["silentEmpty"])
        self.assertEqual(profile["authority"]["captureIntegrityStatus"], "PASS")
        self.assertEqual(
            profile["authority"]["evidenceDecision"]["evidenceAvailability"],
            "IDENTITY_ONLY",
        )
        self.assertEqual(result["contentRecoveryStatus"], "IDENTITY_ONLY")
        self.assertEqual(result["benchmarkClosureStatus"], "IDENTITY_ONLY")
        self.assertIn("SILENT_EMPTY", result["blockerCodes"])

    def test_content_is_not_promoted_to_closed_without_question_assessment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _publish_fixture(
                Path(temp_dir),
                asset_name="BuffAsset",
                object_path="/Game/Test/BuffAsset.BuffAsset",
                with_content=True,
                exact_link=True,
            )
            profile = benchmark.profile_blueprint_evidence(asset_dir)

            result = benchmark.classify_blueprint_sample(
                profile,
                question_contract={
                    "questionZh": "这个 Buff 何时施加，施加给谁？",
                    "requiredSignalGroups": [
                        {"id": "effect", "terms": ["BuffToApply", "ApplyBuff"]}
                    ],
                    "requiresExactFlow": True,
                },
            )

        self.assertEqual(result["contentRecoveryStatus"], "CONTENT_RECOVERED")
        self.assertEqual(result["benchmarkClosureStatus"], "PARTIAL")
        self.assertIn("QUESTION_ASSESSMENT_NOT_REVIEWED", result["blockerCodes"])

    def test_reviewed_claims_must_resolve_before_exact_closure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _publish_fixture(
                Path(temp_dir),
                asset_name="BuffAsset",
                object_path="/Game/Test/BuffAsset.BuffAsset",
                with_content=True,
                exact_link=True,
            )
            profile = benchmark.profile_blueprint_evidence(asset_dir)
            valid_ref = next(
                ref
                for ref in profile["_availableEvidenceRefs"]
                if "/default/" in ref
            )

            result = benchmark.classify_blueprint_sample(
                profile,
                question_contract={
                    "questionZh": "这个 Buff 何时施加，施加给谁？",
                    "requiredSignalGroups": [
                        {"id": "effect", "terms": ["BuffToApply", "ApplyBuff"]}
                    ],
                    "requiresExactFlow": True,
                },
                reviewed_assessment={
                    "status": "CLOSED_EXACT",
                    "claims": [
                        {
                            "textZh": "存在可精确定位的 Buff 事实。",
                            "evidenceRefs": [valid_ref],
                        }
                    ],
                    "blockingGaps": [],
                },
            )
            bad = benchmark.classify_blueprint_sample(
                profile,
                question_contract={
                    "questionZh": "这个 Buff 何时施加，施加给谁？",
                    "requiredSignalGroups": [],
                    "requiresExactFlow": False,
                },
                reviewed_assessment={
                    "status": "CLOSED_EXACT",
                    "claims": [
                        {
                            "textZh": "伪造引用不得闭环。",
                            "evidenceRefs": ["bp://missing@revision/default/Nope"],
                        }
                    ],
                    "blockingGaps": [],
                },
            )

        self.assertEqual(result["benchmarkClosureStatus"], "CLOSED_EXACT")
        self.assertEqual(bad["benchmarkClosureStatus"], "FAILED")
        self.assertIn("CITATION_BINDING_FAILED", bad["blockerCodes"])

    def test_every_closed_claim_requires_its_own_citation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _publish_fixture(
                Path(temp_dir),
                asset_name="PerClaimCitationAsset",
                object_path="/Game/Test/PerClaimCitationAsset.PerClaimCitationAsset",
                with_content=True,
            )
            profile = benchmark.profile_blueprint_evidence(asset_dir)
            valid_ref = next(
                ref
                for ref in profile["_availableEvidenceRefs"]
                if "/default/" in ref
            )

            result = benchmark.classify_blueprint_sample(
                profile,
                question_contract={
                    "questionZh": "这两条结论是否都有自己的证据？",
                    "requiredSignalGroups": [],
                    "requiresExactFlow": False,
                },
                reviewed_assessment={
                    "status": "CLOSED_EXACT",
                    "claims": [
                        {
                            "textZh": "第一条结论有精确证据。",
                            "evidenceRefs": [valid_ref],
                        },
                        {
                            "textZh": "第二条结论没有证据。",
                            "evidenceRefs": [],
                        },
                    ],
                    "blockingGaps": [],
                },
            )

        self.assertEqual(result["benchmarkClosureStatus"], "PARTIAL")
        self.assertIn("CLOSED_CLAIM_WITHOUT_CITATIONS", result["blockerCodes"])

    def test_closed_exact_rejects_heuristic_edge_and_diagnostic_citations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _publish_fixture(
                Path(temp_dir),
                asset_name="HeuristicCitationAsset",
                object_path=(
                    "/Game/Test/HeuristicCitationAsset.HeuristicCitationAsset"
                ),
                with_content=True,
                heuristic_link=True,
            )
            profile = benchmark.profile_blueprint_evidence(asset_dir)
            heuristic_edge_ref = next(
                ref
                for ref in profile["_availableEvidenceRefs"]
                if "/observation/" in ref
            )
            diagnostic_ref = next(
                ref
                for ref in profile["_availableEvidenceRefs"]
                if "/diagnostic/" in ref
            )

            for citation in (heuristic_edge_ref, diagnostic_ref):
                with self.subTest(citation=citation):
                    result = benchmark.classify_blueprint_sample(
                        profile,
                        question_contract={
                            "questionZh": "完整执行链是什么？",
                            "requiredSignalGroups": [],
                            "requiresExactFlow": True,
                        },
                        reviewed_assessment={
                            "questionOverride": {
                                "questionZh": "是否存在一条精确可证的执行连接？",
                                "requiredSignalGroups": [],
                                "requiresExactFlow": False,
                            },
                            "status": "CLOSED_EXACT",
                            "claims": [
                                {
                                    "textZh": "存在一条精确连接。",
                                    "evidenceRefs": [citation],
                                }
                            ],
                            "blockingGaps": [],
                        },
                    )

                    self.assertNotEqual(
                        result["benchmarkClosureStatus"],
                        "CLOSED_EXACT",
                    )
                    self.assertTrue(
                        any(
                            "CITATION" in str(code)
                            for code in result["blockerCodes"]
                        ),
                        result,
                    )

    def test_review_can_narrow_the_fixed_category_question_without_hiding_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _publish_fixture(
                Path(temp_dir),
                asset_name="ConfigAsset",
                object_path="/Game/Test/ConfigAsset.ConfigAsset",
                with_content=True,
                exact_link=False,
            )
            profile = benchmark.profile_blueprint_evidence(asset_dir)
            valid_ref = profile["content"]["sampleEvidenceRefs"][0]

            result = benchmark.classify_blueprint_sample(
                profile,
                question_contract={
                    "questionZh": "这个资产的完整执行流程是什么？",
                    "requiredSignalGroups": [
                        {"id": "missing-flow", "terms": ["NeverRecovered"]}
                    ],
                    "requiresExactFlow": True,
                },
                reviewed_assessment={
                    "questionOverride": {
                        "questionZh": "这个资产的一个已恢复默认值是什么？",
                        "requiredSignalGroups": [],
                        "requiresExactFlow": False,
                    },
                    "status": "CLOSED_EXACT",
                    "claims": [
                        {
                            "textZh": "默认值有精确证据。",
                            "evidenceRefs": [valid_ref],
                        }
                    ],
                    "blockingGaps": [],
                },
            )

        self.assertEqual(result["benchmarkClosureStatus"], "CLOSED_EXACT")
        self.assertEqual(
            result["question"]["questionZh"],
            "这个资产的一个已恢复默认值是什么？",
        )
        self.assertEqual(result["question"]["categoryQuestionZh"], "这个资产的完整执行流程是什么？")
        self.assertTrue(result["question"]["wasNarrowedForSample"])
        self.assertNotIn("EXACT_EXECUTION_FLOW_NOT_RECOVERED", result["blockerCodes"])
        self.assertNotIn("REQUIRED_BUSINESS_SIGNAL_NOT_RECOVERED", result["blockerCodes"])

    def test_blocking_gap_cannot_be_labeled_closed_heuristic(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _publish_fixture(
                Path(temp_dir),
                asset_name="HeuristicAsset",
                object_path="/Game/Test/HeuristicAsset.HeuristicAsset",
                with_content=True,
                exact_link=False,
            )
            profile = benchmark.profile_blueprint_evidence(asset_dir)
            valid_ref = profile["content"]["sampleEvidenceRefs"][0]

            result = benchmark.classify_blueprint_sample(
                profile,
                question_contract={
                    "questionZh": "这个行为流程是什么？",
                    "requiredSignalGroups": [],
                    "requiresExactFlow": True,
                },
                reviewed_assessment={
                    "status": "CLOSED_HEURISTIC",
                    "claims": [
                        {
                            "textZh": "只能确认一个局部事实。",
                            "evidenceRefs": [valid_ref],
                        }
                    ],
                    "blockingGaps": ["最终效果没有恢复。"],
                },
            )

        self.assertEqual(result["benchmarkClosureStatus"], "PARTIAL")
        self.assertIn("QUESTION_HAS_BLOCKING_GAPS", result["blockerCodes"])

    def test_report_keeps_ready_separate_from_benchmark_closure(self):
        plan = {
            "schema": "ark.kb.asset-unknown-stratified-sample-plan.v1",
            "selectionAlgorithm": "category-stratified-role-family-aware/v1",
            "sampleCount": 3,
            "coverage": {
                "categoryGroupsCovered": 3,
                "categoryGroupsTotal": 3,
                "uncoveredCategoryGroups": [],
            },
            "samples": [
                _sample(1, code="BUFF"),
                _sample(
                    2,
                    code="DATA",
                    target_kind="NATIVE_CLASS",
                    action="ROUTE_NATIVE_CLASS_EVIDENCE",
                    target_path="/Script/Test.DataAsset",
                ),
                _sample(
                    3,
                    code="KNOWN",
                    action="SKIP_EXISTING_CONFIRMED_CLASSIFICATION",
                ),
            ],
        }
        contracts = {
            "BUFF:CANDIDATE": {
                "questionZh": "Buff 做什么？",
                "requiredSignalGroups": [],
                "requiresExactFlow": True,
            },
            "DATA:CANDIDATE": {
                "questionZh": "配置字段是什么？",
                "requiredSignalGroups": [],
                "requiresExactFlow": False,
            },
            "KNOWN:CANDIDATE": {
                "questionZh": "既有分类能否由 BTC 重现？",
                "requiredSignalGroups": [],
                "requiresExactFlow": False,
            },
        }

        report = benchmark.build_capability_report(
            sample_plan=plan,
            question_contracts=contracts,
            blueprint_profiles={},
            canonical_ready_paths=set(),
            native_profiles={
                "/Script/Test.DataAsset": {
                    "trustStatus": "VERIFIED",
                    "symbolCount": 0,
                    "evidenceRefs": [],
                }
            },
            input_bindings={
                "samplePlan": {"sha256": "plan-sha"},
                "questionContracts": {"sha256": "contract-sha"},
            },
        )

        self.assertEqual(report["counts"]["samples"], 3)
        self.assertEqual(report["counts"]["ready"], 0)
        self.assertEqual(report["counts"]["closedExact"], 0)
        statuses = [item["result"]["benchmarkClosureStatus"] for item in report["samples"]]
        self.assertEqual(statuses, ["NOT_RUN", "UNSUPPORTED", "NOT_RUN"])
        self.assertEqual(
            [item["axes"]["answerClosure"] for item in report["samples"]],
            ["NOT_REVIEWED", "PARTIAL", "NOT_REVIEWED"],
        )
        self.assertEqual(
            [item["statusZh"] for item in report["samples"]],
            ["尚未测试", "当前工具无法读取", "尚未测试"],
        )
        self.assertEqual(
            report["counts"]["answerClosure"],
            {"NOT_REVIEWED": 2, "PARTIAL": 1},
        )
        self.assertEqual(report["samples"][1]["evidence"]["trustStatus"], "VERIFIED")
        self.assertEqual(
            report["classificationLineage"]["selectionAlgorithm"],
            "category-stratified-role-family-aware/v1",
        )
        self.assertEqual(
            report["classificationLineage"]["categoryGroupsCovered"], 3
        )
        self.assertEqual(
            report["inputBindings"]["samplePlan"]["sha256"], "plan-sha"
        )

    def test_ready_row_uses_the_same_canonical_current_profile_revision(self):
        target_path = "/Game/Test/SharedAsset.SharedAsset"
        batch_profile = {
            "asset": {
                "assetId": "shared-asset",
                "name": "SharedAsset",
                "objectPath": target_path,
                "revisionId": "batch-revision",
            },
            "authority": {"captureIntegrityStatus": "PASS"},
            "content": {"silentEmpty": False, "businessFactCount": 1},
            "identity": {"exactLinkCount": 0, "heuristicLinkCount": 0},
            "_searchRecords": [],
            "_availableEvidenceRefs": [],
        }
        canonical_current_profile = {
            **batch_profile,
            "asset": {
                **batch_profile["asset"],
                "revisionId": "canonical-current-revision",
            },
        }

        report = benchmark.build_capability_report(
            sample_plan={
                "sampleCount": 1,
                "samples": [
                    _sample(
                        1,
                        code="CONFIG",
                        action="SKIP_EXISTING_CONFIRMED_CLASSIFICATION",
                        target_path=target_path,
                    )
                ],
            },
            question_contracts={
                "CONFIG:CANDIDATE": {
                    "questionZh": "这个配置里有什么？",
                    "requiredSignalGroups": [],
                    "requiresExactFlow": False,
                }
            },
            blueprint_profiles={target_path: batch_profile},
            canonical_ready_profiles={target_path: canonical_current_profile},
        )

        row = report["samples"][0]
        self.assertTrue(row["p0"]["ready"])
        self.assertEqual(
            row["evidence"]["asset"]["revisionId"],
            "canonical-current-revision",
        )
        self.assertEqual(
            row["p0"]["evidenceRevisionId"],
            row["evidence"]["asset"]["revisionId"],
        )
        self.assertEqual(row["result"]["benchmarkClosureStatus"], "PARTIAL")

    def test_public_profile_omits_local_asset_directory_and_fails_closed(self):
        local_path = "C:" + r"\Users\learner\private\capture"

        public = benchmark._public_profile(
            {
                "assetDir": local_path,
                "asset": {
                    "objectPath": "/Game/Test/SharedAsset.SharedAsset",
                    "revisionId": "revision-1",
                },
            }
        )

        self.assertNotIn("assetDir", public)
        self.assertNotIn(local_path, json.dumps(public))
        with self.assertRaisesRegex(ValueError, "machine-local path"):
            benchmark._public_profile(
                {
                    "asset": {
                        "objectPath": "/Game/Test/SharedAsset.SharedAsset",
                        "revisionId": "revision-1",
                    },
                    "unexpected": {"diagnostic": local_path},
                }
            )

    def test_public_profile_allows_bound_cosmo_mod_object_path(self):
        object_path = (
            "/CosmoCarCosmetic/ModDataAsset_CosmoCarCosmetic."
            "ModDataAsset_CosmoCarCosmetic"
        )

        public = benchmark._public_profile(
            {
                "sourceClassObjectPath": "/Script/ShooterGame.ModDataAsset",
                "objects": [{"objectPath": object_path}],
            }
        )

        self.assertEqual(public["objects"][0]["objectPath"], object_path)

    def test_review_distinguishes_native_identity_from_partial_implementation(self):
        ref = "native://binary/ShooterGame.dll/0x1234"
        result = benchmark._native_result(
            {
                "trustStatus": "VERIFIED",
                "symbolCount": 1,
                "evidenceRefs": [
                    {"evidenceRef": ref, "qualifiedName": "ASplineActor::~ASplineActor"}
                ],
            },
            question_contract={
                "questionZh": "这个原生类怎样实现玩法逻辑？",
                "requiredSignalGroups": [],
                "requiresExactFlow": True,
            },
            reviewed_assessment={
                "questionOverride": {
                    "questionZh": "当前证据是否只确认类身份？",
                    "requiredSignalGroups": [],
                    "requiresExactFlow": False,
                },
                "status": "IDENTITY_ONLY",
                "claims": [
                    {"textZh": "只找到析构函数身份。", "evidenceRefs": [ref]}
                ],
                "blockingGaps": ["没有业务函数或调用链。"],
            },
        )

        self.assertEqual(result["benchmarkClosureStatus"], "IDENTITY_ONLY")
        self.assertEqual(result["contentRecoveryStatus"], "IDENTITY_ONLY")
        self.assertEqual(result["question"]["questionZh"], "当前证据是否只确认类身份？")
        self.assertEqual(result["claims"][0]["evidenceRefs"], [ref])

    def test_batch_alias_maps_registry_path_but_keeps_identity_boundary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            alias_target = "/Game/Mods/PCG/TestAsset.TestAsset"
            registry_target = "/PCG/TestAsset.TestAsset"
            profile = {
                "asset": {"objectPath": alias_target},
                "authority": {"captureIntegrityStatus": "PASS"},
                "content": {"silentEmpty": True},
            }
            (root / "batch_plan.json").write_text(
                json.dumps(
                    {
                        "samples": [
                            {
                                "sampleIndex": 37,
                                "sourceRegistryObjectPath": registry_target,
                                "targetPath": alias_target,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            mapped = benchmark.apply_batch_aliases(
                {alias_target: profile},
                [root],
            )

        self.assertIn(registry_target, mapped)
        self.assertEqual(
            mapped[registry_target]["identityBoundary"]["status"],
            "ALIASED_NOT_AUTHORITY_BOUND",
        )
        self.assertEqual(
            mapped[registry_target]["identityBoundary"]["resolvedObjectPath"],
            alias_target,
        )
        result = benchmark.classify_blueprint_sample(
            mapped[registry_target],
            question_contract={
                "questionZh": "PCG 点数据包含什么？",
                "requiredSignalGroups": [],
                "requiresExactFlow": False,
            },
        )
        self.assertEqual(result["benchmarkClosureStatus"], "IDENTITY_ONLY")
        self.assertIn("ALIASED_IDENTITY_NOT_AUTHORITY_BOUND", result["blockerCodes"])

    def test_chinese_report_exposes_category_question_claim_and_citation(self):
        report = {
            "counts": {
                "samples": 1,
                "categoryStatusStrata": 1,
                "categoryCodes": 1,
                "closedExact": 1,
                "closedHeuristic": 0,
                "partial": 0,
                "identityOnly": 0,
                "unsupported": 0,
                "failed": 0,
                "notRun": 0,
                "ready": 0,
            },
            "classificationLineage": {
                "samplePlanSchema": (
                    "ark.kb.asset-unknown-stratified-sample-plan.v1"
                ),
                "selectionAlgorithm": (
                    "category-stratified-role-family-aware/v1"
                ),
                "sampleCount": 1,
                "categoryGroupsCovered": 1,
                "categoryGroupsTotal": 1,
            },
            "inputBindings": {
                "samplePlan": {"sha256": "plan-sha"},
            },
            "samples": [
                {
                    "sampleIndex": 1,
                    "targetPath": "/Game/Test/Config.Config",
                    "plannedStratum": {
                        "code": "DATA_CONFIGURATION",
                        "labelZh": "数据与配置资产",
                        "status": "CANDIDATE",
                    },
                    "p0": {"ready": False},
                    "result": {
                        "contentRecoveryStatus": "CONTENT_RECOVERED",
                        "benchmarkClosureStatus": "CLOSED_EXACT",
                        "question": {"questionZh": "默认倍率是多少？"},
                        "claims": [
                            {
                                "textZh": "默认倍率为 1。",
                                "evidenceRefs": [
                                    "bp://asset@revision/default/Multiplier"
                                ],
                            }
                        ],
                        "blockingGaps": [],
                        "blockerCodes": [],
                    },
                }
            ],
        }

        markdown = benchmark.render_report_zh(report)

        self.assertIn("## 按类别汇总", markdown)
        self.assertIn("数据与配置资产", markdown)
        self.assertIn("## 逐样本玩家问题与证据", markdown)
        self.assertIn("默认倍率是多少？", markdown)
        self.assertIn("默认倍率为 1。", markdown)
        self.assertIn("bp://asset@revision/default/Multiplier", markdown)
        self.assertIn("## 分类样本来源", markdown)
        self.assertIn("category-stratified-role-family-aware/v1", markdown)
        self.assertIn("窄问题精确闭环", markdown)
        self.assertIn("可正式查询", markdown)
        for internal_status in (
            "READY",
            "FRESH",
            "releaseAuthority",
            "validator PASS",
        ):
            with self.subTest(internal_status=internal_status):
                self.assertNotIn(internal_status, markdown)

    def test_non_closed_sample_without_prose_gap_does_not_claim_no_blocker(self):
        report = {
            "counts": {"samples": 1},
            "samples": [
                {
                    "sampleIndex": 37,
                    "targetPath": "/PCG/Test/PCGPointList.PCGPointList",
                    "plannedStratum": {
                        "code": "PROCEDURAL_ECOLOGY",
                        "labelZh": "PCG 与程序化生态",
                    },
                    "p0": {"ready": False},
                    "result": {
                        "contentRecoveryStatus": "IDENTITY_ONLY",
                        "benchmarkClosureStatus": "IDENTITY_ONLY",
                        "question": {"questionZh": "点列表里有哪些业务配置？"},
                        "claims": [],
                        "blockingGaps": [],
                        "blockerCodes": [
                            "ALIASED_IDENTITY_NOT_AUTHORITY_BOUND",
                            "SILENT_EMPTY",
                        ],
                    },
                }
            ],
        }

        markdown = benchmark.render_report_zh(report)

        sample_section = markdown.split("### #37", 1)[1]
        self.assertNotIn("本次所选窄问题无阻断", sample_section)
        self.assertIn("ALIASED_IDENTITY_NOT_AUTHORITY_BOUND", sample_section)
        self.assertIn("SILENT_EMPTY", sample_section)


if __name__ == "__main__":
    unittest.main()
