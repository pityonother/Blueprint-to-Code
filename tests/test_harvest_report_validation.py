import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.harvest_report_validation import (  # noqa: E402
    build_ranking_revision_fields,
    build_canonical_ai_view,
    validate_harvest_report,
)
from rank_ark_harvest import (  # noqa: E402
    best_rows,
    build_resource_candidates,
    compact_row,
    write_outputs,
)


class HarvestReportValidationTests(unittest.TestCase):
    def setUp(self):
        self.full_path = Path("reports/harvest_ranking_metal.full.json")
        self.full = {
            "schema": "ark-harvest-ranking/v1",
            "generatedAt": "2026-07-20T00:00:00+00:00",
            "resources": ["Metal_C"],
            "resourceSelectionMode": "EXPLICIT",
            "methodology": {
                "scoreBasis": "INFERRED_ENGINE_COEFFICIENT_INDEX_NOT_RESOURCE_YIELD",
                "formulaVersion": "harvest-engine-comparison-index/v1",
                "usageScope": "UNFILTERED_ENGINE_ATTACKS",
                "observedYieldPerSecond": None,
                "formula": (
                    "baseDamage / attackInterval * DamageMultiplier * "
                    "HarvestQuantityMultiplier * normalizedResourceWeight"
                ),
                "notIncluded": [
                    "runtime melee stat scaling",
                    "server harvest multipliers",
                    "node remaining-health clamp",
                    "actual animation wall-clock timing",
                    "nodes hit per swing",
                    "controlled observed yield",
                ],
            },
            "coverage": {
                "creaturesRequested": 0,
                "creaturesLoaded": 0,
                "attacksDecoded": 0,
                "componentsScanned": 1,
                "componentsAttempted": 1,
                "componentsDecoded": 1,
                "componentsSemanticGap": 0,
                "componentsMatched": 1,
                "componentCatalogEntries": 1,
                "componentSourceFingerprints": {
                    "attemptedPaths": 1,
                    "fingerprintedPaths": 1,
                    "complete": True,
                },
                "resourceClassesDiscovered": 0,
                "rows": 3,
                "rankedRows": 1,
                "incompatibleRows": 1,
                "unrankedRows": 1,
            },
            "rows": [
                {
                    "resource": "Metal_C",
                    "component": "MetalHarvestComponent",
                    "creature": "Magmasaur",
                    "attackName": "Bite",
                    "rankingStatus": "RANKED",
                    "engineComparisonIndex": 293.5,
                    "observedYieldPerSecond": None,
                },
                {
                    "resource": "Metal_C",
                    "component": "MetalHarvestComponent_Rich",
                    "creature": "Ankylosaurus",
                    "attackName": "Tail",
                    "rankingStatus": "UNRANKED",
                    "reasonCode": "REQUIRED_ATTACK_FACT_NOT_RECOVERED",
                    "missingFacts": ["AttackInterval"],
                    "engineComparisonIndex": None,
                    "observedYieldPerSecond": None,
                },
                {
                    "resource": "Metal_C",
                    "component": "MetalHarvestComponent",
                    "creature": "Doedicurus",
                    "attackName": "Tail",
                    "rankingStatus": "INCOMPATIBLE",
                    "reasonCode": "ZERO_RESOURCE_WEIGHT",
                    "engineComparisonIndex": None,
                    "observedYieldPerSecond": None,
                },
            ],
            "bestRows": [
                {
                    "resource": "Metal_C",
                    "component": "MetalHarvestComponent",
                    "creature": "Magmasaur",
                    "attackName": "Bite",
                    "rankingStatus": "RANKED",
                    "engineComparisonIndex": 293.5,
                    "observedYieldPerSecond": None,
                },
                {
                    "resource": "Metal_C",
                    "component": "MetalHarvestComponent_Rich",
                    "creature": "Ankylosaurus",
                    "attackName": "Tail",
                    "rankingStatus": "UNRANKED",
                    "reasonCode": "REQUIRED_ATTACK_FACT_NOT_RECOVERED",
                    "missingFacts": ["AttackInterval"],
                    "engineComparisonIndex": None,
                    "observedYieldPerSecond": None,
                },
                {
                    "resource": "Metal_C",
                    "component": "MetalHarvestComponent",
                    "creature": "Doedicurus",
                    "attackName": "Tail",
                    "rankingStatus": "INCOMPATIBLE",
                    "reasonCode": "ZERO_RESOURCE_WEIGHT",
                    "engineComparisonIndex": None,
                    "observedYieldPerSecond": None,
                },
            ],
            "components": [
                {
                    "component": "MetalHarvestComponent",
                    "objectPath": "/Game/Harvest/MetalHarvestComponent",
                    "maxHarvestHealth": 620.0,
                    "harvestHealthGiveResourceInterval": 40.0,
                    "matchedResources": ["Metal_C"],
                    "discoveryStatus": "MATCHED",
                    "gaps": [],
                }
            ],
            "failures": {"creatures": [], "components": []},
            "resourceCatalog": [],
            "sources": [
                {"path": "A.uasset", "sha256": "a" * 64},
                {"path": "B.uasset", "sha256": "b" * 64},
            ],
        }
        self.full["componentCatalog"] = [dict(row) for row in self.full["components"]]
        for index, row in enumerate(self.full["rows"]):
            row.setdefault("componentObjectPath", "/Game/Harvest/MetalHarvestComponent")
            row.setdefault("creatureObjectPath", f"/Game/Dinos/{row['creature']}")
            row.setdefault("attackIndex", index)
        self.full["bestRows"] = [
            compact_row(row)
            for row in best_rows(self.full["rows"])
        ]
        self.full["resourceCandidates"] = [
            {
                "resource": "Metal_C",
                "discoveryStatus": "RANKED_CANDIDATES_AVAILABLE",
                "rankedDiscoveryStatus": "RANKED_ROWS_AVAILABLE",
                "bestRows": [dict(row) for row in self.full["bestRows"]],
            }
        ]
        self.full["componentScanManifest"] = [
            {
                "component": "MetalHarvestComponent",
                "componentObjectPath": "/Game/Harvest/MetalHarvestComponent",
                "path": "A.uasset",
                "attempted": True,
                "decoded": True,
                "semanticGap": False,
                "matched": True,
                "gaps": [],
            }
        ]
        self.full["componentGapSummary"] = []
        manifest_text = json.dumps(
            self.full["componentScanManifest"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.full["scanManifestHash"] = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
        self.full.update(build_ranking_revision_fields(self.full))
        self.ai = {
            "schema": "ark-harvest-ranking/v1",
            "compactSchema": "ark-harvest-compact/v2",
            "generatedAt": self.full["generatedAt"],
            "resources": ["Metal_C"],
            "methodology": self.full["methodology"],
            "coverage": self.full["coverage"],
            **build_canonical_ai_view(
                self.full,
                detail_location=self.full_path.name,
            ),
            "tokenEstimate": {"method": "ceil(characters/4)"},
        }

    def validate(self, **kwargs):
        return validate_harvest_report(
            self.full,
            self.ai,
            full_path=self.full_path,
            **kwargs,
        )

    def rebuild_ai(self):
        self.full.update(build_ranking_revision_fields(self.full))
        self.ai["coverage"] = self.full["coverage"]
        self.ai.update(
            build_canonical_ai_view(
                self.full,
                detail_location=self.full_path.name,
            )
        )
        self.ai["tokenEstimate"] = {
            "method": "ceil(characters/4)",
            "characters": 1000,
            "estimatedTokens": 250,
        }

    def test_valid_subset_passes_and_reports_reduction(self):
        source_lines = "\n".join(
            f"{source['path']}|{source['sha256']}" for source in self.full["sources"]
        )
        self.ai["sourceSet"]["sha256"] = hashlib.sha256(source_lines.encode("utf-8")).hexdigest()
        self.ai["tokenEstimate"] = {
            "method": "ceil(characters/4)",
            "characters": 1000,
            "estimatedTokens": 250,
        }

        result = self.validate(full_characters=10000, ai_characters=1000)

        self.assertTrue(result["valid"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["compression"]["characterReductionPct"], 90.0)

    def test_changed_focus_coefficient_is_rejected(self):
        self.ai["resourceViews"][0]["focusRows"][0]["engineComparisonIndex"] = 999.0

        result = self.validate()

        self.assertFalse(result["valid"])
        self.assertTrue(any("resourceViews" in error for error in result["errors"]))

    def test_missing_focus_row_is_rejected(self):
        focus = self.ai["resourceViews"][0]["focusRows"]
        self.ai["resourceViews"][0]["focusRows"] = focus[:1]

        result = self.validate()

        self.assertFalse(result["valid"])
        self.assertTrue(any("resourceViews" in error for error in result["errors"]))

    def test_unknown_summary_must_cover_non_best_full_rows(self):
        self.ai["unknownSummary"] = self.ai["unknownSummary"][:1]

        result = self.validate()

        self.assertFalse(result["valid"])
        self.assertTrue(any("unknownSummary" in error for error in result["errors"]))

    def test_methodology_component_index_and_failures_are_contract_checked(self):
        self.ai["methodology"] = {"scoreBasis": "OBSERVED_RESOURCE_YIELD"}
        self.ai["componentIndex"]["items"][0]["gaps"] = ["invented"]
        self.full["failures"] = {
            "creatures": [
                {
                    "name": "MissingCreature",
                    "objectPath": "/Game/MissingCreature",
                    "reasonCode": "CREATURE_ASSET_NOT_FOUND",
                }
            ],
            "components": [],
        }

        result = self.validate()

        self.assertFalse(result["valid"])
        self.assertTrue(any("methodology" in error for error in result["errors"]))
        self.assertTrue(any("componentIndex" in error for error in result["errors"]))
        self.assertTrue(any("failureSummary" in error for error in result["errors"]))

    def test_focus_component_cannot_be_switched_to_hide_rows(self):
        self.ai["resourceViews"][0]["focusComponent"] = "NonexistentComponent"
        self.ai["resourceViews"][0]["focusRows"] = []

        result = self.validate()

        self.assertFalse(result["valid"])
        self.assertTrue(any("resourceViews" in error for error in result["errors"]))

    def test_ranked_discoveries_cannot_omit_a_ranked_result(self):
        self.ai["resourceViews"][0]["rankedDiscoveries"] = []

        result = self.validate()

        self.assertFalse(result["valid"])
        self.assertTrue(any("resourceViews" in error for error in result["errors"]))

    def test_token_estimate_is_checked_against_actual_compact_length(self):
        self.ai["tokenEstimate"] = {
            "method": "ceil(characters/4)",
            "characters": 999,
            "estimatedTokens": 1,
        }

        result = self.validate(ai_characters=1000)

        self.assertFalse(result["valid"])
        self.assertTrue(any("tokenEstimate" in error for error in result["errors"]))

    def test_compact_report_must_be_smaller_than_full_report(self):
        self.ai["tokenEstimate"] = {
            "method": "ceil(characters/4)",
            "characters": 1000,
            "estimatedTokens": 250,
        }

        result = self.validate(
            full_characters=100,
            ai_characters=1000,
        )

        self.assertFalse(result["valid"])
        self.assertTrue(any("smaller than full" in error for error in result["errors"]))

    def test_compact_report_has_a_hard_token_budget(self):
        self.ai["tokenEstimate"] = {
            "method": "ceil(characters/4)",
            "characters": 48001,
            "estimatedTokens": 12001,
        }

        result = self.validate(
            full_characters=100000,
            ai_characters=48001,
        )

        self.assertFalse(result["valid"])
        self.assertTrue(any("token budget" in error for error in result["errors"]))

    def test_canonical_view_has_a_bounded_view_for_every_resource(self):
        wood_row = {
            "resource": "Wood_C",
            "component": "WoodHarvestComponent",
            "componentObjectPath": "/Game/Harvest/WoodHarvestComponent",
            "creature": "Therizinosaurus",
            "creatureObjectPath": "/Game/Dinos/Theri",
            "attackIndex": 2,
            "attackName": "Bite",
            "rankingStatus": "RANKED",
            "engineComparisonIndex": 52.0,
            "observedYieldPerSecond": None,
        }
        self.full["resources"] = ["Metal_C", "Wood_C"]
        self.full["rows"].append(wood_row)

        view = build_canonical_ai_view(
            self.full,
            detail_location=self.full_path.name,
        )

        self.assertEqual(
            [row["resource"] for row in view["resourceViews"]],
            ["Metal_C", "Wood_C"],
        )
        wood = view["resourceViews"][1]
        self.assertEqual(wood["discoveryStatus"], "RANKED_CANDIDATES_AVAILABLE")
        self.assertEqual(len(wood["rankedDiscoveries"]), 1)
        self.assertEqual(wood["rankedDiscoveryCoverage"]["omitted"], 0)

    def test_all_resources_compact_view_is_an_index_not_ninety_two_detail_reports(self):
        self.full["resourceSelectionMode"] = "ALL_DISCOVERED"
        self.full["resources"] = [f"Resource_{index}_C" for index in range(92)]
        self.full["resources"][0] = "Metal_C"

        view = build_canonical_ai_view(
            self.full,
            detail_location="harvest_ranking_all_resources.full.json",
        )
        serialized = json.dumps(view, ensure_ascii=False, separators=(",", ":"))

        self.assertEqual(view["viewMode"], "RESOURCE_INDEX")
        self.assertEqual(view["resourceViews"], [])
        self.assertEqual(view["resourceIndex"]["total"], 92)
        self.assertEqual(len(view["resourceIndex"]["items"]), 92)
        self.assertEqual(view["componentIndex"]["returned"], 0)
        self.assertLess(len(serialized), 48_000)

    def test_dataset_revision_changes_with_creatures_damage_types_or_usage_scope(self):
        payload = {
            "schema": "ark-harvest-ranking/v1",
            "resources": ["Metal_C"],
            "resourceSelectionMode": "EXPLICIT",
            "methodology": dict(self.full["methodology"]),
            "scanManifestHash": "a" * 64,
            "creatures": [{"objectPath": "/Game/Dinos/Ankylo", "attacks": [{"damage": 10}]}],
            "failures": {"creatures": []},
            "damageTypes": [{"damageType": "MineStone_C", "multiplier": 1.0}],
            "sources": [{"path": "A.uasset", "sha256": "b" * 64}],
        }
        original = build_ranking_revision_fields(payload)

        creature_changed = json.loads(json.dumps(payload))
        creature_changed["creatures"][0]["attacks"][0]["damage"] = 11
        damage_changed = json.loads(json.dumps(payload))
        damage_changed["damageTypes"][0]["multiplier"] = 2.0
        scope_changed = json.loads(json.dumps(payload))
        scope_changed["methodology"]["usageScope"] = "TAMED_PLAYER"

        self.assertNotEqual(
            original["datasetRevision"],
            build_ranking_revision_fields(creature_changed)["datasetRevision"],
        )
        self.assertNotEqual(
            original["datasetRevision"],
            build_ranking_revision_fields(damage_changed)["datasetRevision"],
        )
        self.assertNotEqual(
            original["datasetRevision"],
            build_ranking_revision_fields(scope_changed)["datasetRevision"],
        )

    def test_unexpected_compact_fields_are_rejected(self):
        self.ai["inventedConclusion"] = "Magmasaur always wins"

        result = self.validate()

        self.assertFalse(result["valid"])
        self.assertTrue(any("unexpected fields" in error for error in result["errors"]))

    def test_unexpected_nested_token_estimate_fields_are_rejected(self):
        self.ai["tokenEstimate"] = {
            "method": "ceil(characters/4)",
            "characters": 1000,
            "estimatedTokens": 250,
            "inventedConclusion": "Magmasaur always wins",
        }

        result = self.validate(
            full_characters=10000,
            ai_characters=1000,
        )

        self.assertFalse(result["valid"])
        self.assertTrue(any("tokenEstimate fields" in error for error in result["errors"]))

    def test_token_estimate_requires_canonical_non_boolean_integer_types(self):
        self.ai["tokenEstimate"] = {
            "method": "ceil(characters/4)",
            "characters": True,
            "estimatedTokens": True,
        }

        result = self.validate()

        self.assertFalse(result["valid"])
        self.assertTrue(any("tokenEstimate characters type" in error for error in result["errors"]))
        self.assertTrue(
            any("tokenEstimate estimatedTokens type" in error for error in result["errors"])
        )

    def test_unexpected_nested_methodology_fields_are_rejected(self):
        self.full["methodology"]["inventedConclusion"] = "Magmasaur always wins"
        self.ai["methodology"] = self.full["methodology"]
        self.ai["tokenEstimate"] = {
            "method": "ceil(characters/4)",
            "characters": 1000,
            "estimatedTokens": 250,
        }

        result = self.validate(full_characters=10000, ai_characters=1000)

        self.assertFalse(result["valid"])
        self.assertTrue(any("methodology fields" in error for error in result["errors"]))

    def test_unexpected_nested_coverage_fields_are_rejected(self):
        self.full["coverage"]["inventedConclusion"] = "Magmasaur always wins"
        self.ai["coverage"] = self.full["coverage"]
        self.ai["tokenEstimate"] = {
            "method": "ceil(characters/4)",
            "characters": 1000,
            "estimatedTokens": 250,
        }

        result = self.validate(full_characters=10000, ai_characters=1000)

        self.assertFalse(result["valid"])
        self.assertTrue(any("coverage fields" in error for error in result["errors"]))

    def test_full_scan_manifest_fingerprint_is_recomputed(self):
        self.full["componentScanManifest"][0]["decoded"] = False

        result = self.validate()

        self.assertFalse(result["valid"])
        self.assertTrue(any("scanManifestHash" in error for error in result["errors"]))

    def test_full_best_rows_and_resource_candidates_are_recomputed_from_rows(self):
        self.full["bestRows"] = []
        self.full["resourceCandidates"] = [
            {
                "resource": "Metal_C",
                "discoveryStatus": "NO_ROWS",
                "rankedDiscoveryStatus": "NO_RANKED_ROW",
                "bestRows": [],
            }
        ]
        self.ai.update(
            build_canonical_ai_view(
                self.full,
                detail_location=self.full_path.name,
            )
        )
        self.ai["tokenEstimate"] = {
            "method": "ceil(characters/4)",
            "characters": 1000,
            "estimatedTokens": 250,
        }

        result = self.validate(
            full_characters=10000,
            ai_characters=1000,
        )

        self.assertFalse(result["valid"])
        self.assertTrue(any("full bestRows" in error for error in result["errors"]))
        self.assertTrue(any("full resourceCandidates" in error for error in result["errors"]))

    def test_resource_candidates_recompute_preserves_full_row_sort_fallbacks(self):
        self.full["rows"].append(
            {
                "resource": "Metal_C",
                "component": "MetalHarvestComponent",
                "componentObjectPath": "/Game/Harvest/MetalHarvestComponent",
                "creature": "Therizinosaurus",
                "creatureObjectPath": "/Game/Dinos/Therizinosaurus",
                "attackIndex": 3,
                "attackName": "ClawAttack",
                "rankingStatus": "INCOMPATIBLE",
                "reasonCode": "DAMAGE_TYPE_NOT_ACCEPTED",
                "potentialAttackRate": 130.0,
                "engineComparisonIndex": None,
                "observedYieldPerSecond": None,
            }
        )
        self.full["coverage"]["rows"] = 4
        self.full["coverage"]["incompatibleRows"] = 2
        selected = best_rows(self.full["rows"])
        self.full["bestRows"] = [compact_row(row) for row in selected]
        self.full["resourceCandidates"] = build_resource_candidates(
            self.full["resources"],
            selected,
        )
        self.ai.update(
            build_canonical_ai_view(
                self.full,
                detail_location=self.full_path.name,
            )
        )
        self.ai["coverage"] = self.full["coverage"]
        self.ai["tokenEstimate"] = {
            "method": "ceil(characters/4)",
            "characters": 1000,
            "estimatedTokens": 250,
        }

        result = self.validate(full_characters=10000, ai_characters=1000)

        self.assertTrue(result["valid"], result["errors"])

    def test_component_source_fingerprint_coverage_is_recomputed(self):
        self.full["sources"] = [self.full["sources"][1]]
        self.ai.update(
            build_canonical_ai_view(
                self.full,
                detail_location=self.full_path.name,
            )
        )
        self.ai["coverage"] = self.full["coverage"]
        self.ai["tokenEstimate"] = {
            "method": "ceil(characters/4)",
            "characters": 1000,
            "estimatedTokens": 250,
        }

        result = self.validate(
            full_characters=10000,
            ai_characters=1000,
        )

        self.assertFalse(result["valid"])
        self.assertTrue(
            any("componentSourceFingerprints" in error for error in result["errors"])
        )

    def test_component_source_fingerprint_coverage_must_be_complete(self):
        self.full["sources"] = [self.full["sources"][1]]
        self.full["coverage"]["componentSourceFingerprints"] = {
            "attemptedPaths": 1,
            "fingerprintedPaths": 0,
            "complete": False,
        }
        self.ai["coverage"] = self.full["coverage"]
        self.ai.update(
            build_canonical_ai_view(
                self.full,
                detail_location=self.full_path.name,
            )
        )
        self.ai["tokenEstimate"] = {
            "method": "ceil(characters/4)",
            "characters": 1000,
            "estimatedTokens": 250,
        }

        result = self.validate(
            full_characters=10000,
            ai_characters=1000,
        )

        self.assertFalse(result["valid"])
        self.assertTrue(
            any("componentSourceFingerprints must be complete" in error for error in result["errors"])
        )

    def test_component_scan_manifest_cannot_be_empty_when_rows_exist(self):
        self.full["componentScanManifest"] = []
        self.full["scanManifestHash"] = hashlib.sha256(b"[]").hexdigest()
        self.full["componentGapSummary"] = []
        self.full["coverage"].update(
            {
                "componentsScanned": 0,
                "componentsAttempted": 0,
                "componentsDecoded": 0,
                "componentsSemanticGap": 0,
                "componentsMatched": 0,
                "componentSourceFingerprints": {
                    "attemptedPaths": 0,
                    "fingerprintedPaths": 0,
                    "complete": True,
                },
            }
        )
        self.rebuild_ai()

        result = self.validate(full_characters=10000, ai_characters=1000)

        self.assertFalse(result["valid"])
        self.assertTrue(any("componentScanManifest cannot be empty" in error for error in result["errors"]))

    def test_component_scan_manifest_rejects_duplicate_path_and_object_path(self):
        self.full["componentScanManifest"] = [
            dict(self.full["componentScanManifest"][0]),
            dict(self.full["componentScanManifest"][0]),
        ]
        manifest_text = json.dumps(
            self.full["componentScanManifest"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.full["scanManifestHash"] = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
        self.full["coverage"].update(
            {
                "componentsScanned": 2,
                "componentsAttempted": 2,
                "componentsDecoded": 2,
                "componentsSemanticGap": 0,
                "componentsMatched": 2,
            }
        )
        self.rebuild_ai()

        result = self.validate(full_characters=10000, ai_characters=1000)

        self.assertFalse(result["valid"])
        self.assertTrue(any("duplicate component path" in error for error in result["errors"]))
        self.assertTrue(any("duplicate componentObjectPath" in error for error in result["errors"]))

    def test_components_and_rows_must_belong_to_scan_manifest(self):
        self.full["componentScanManifest"][0]["componentObjectPath"] = "/Game/Harvest/Other"
        manifest_text = json.dumps(
            self.full["componentScanManifest"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.full["scanManifestHash"] = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
        self.rebuild_ai()

        result = self.validate(full_characters=10000, ai_characters=1000)

        self.assertFalse(result["valid"])
        self.assertTrue(any("full components missing from componentScanManifest" in error for error in result["errors"]))
        self.assertTrue(any("full rows missing from componentScanManifest" in error for error in result["errors"]))

    def test_source_set_rejects_duplicate_paths_and_invalid_sha256(self):
        self.full["sources"].append(dict(self.full["sources"][0]))
        self.full["sources"][1]["sha256"] = "not-a-sha256"
        self.rebuild_ai()

        result = self.validate(full_characters=10000, ai_characters=1000)

        self.assertFalse(result["valid"])
        self.assertTrue(any("duplicate source path" in error for error in result["errors"]))
        self.assertTrue(any("invalid source SHA-256" in error for error in result["errors"]))

    def test_every_referenced_asset_path_requires_a_fingerprinted_source(self):
        self.full["creatures"] = [
            {
                "name": "MissingCreatureSource",
                "path": "Creature.uasset",
                "sourceChain": [],
            }
        ]
        self.full["damageTypes"] = [
            {
                "damageType": "MissingDamageSource_C",
                "path": "DamageType.uasset",
                "sourceChain": ["DamageTypeParent.uasset"],
                "gaps": [],
            }
        ]
        self.rebuild_ai()

        result = self.validate(full_characters=10000, ai_characters=1000)

        self.assertFalse(result["valid"])
        self.assertTrue(any("referenced asset paths missing from sources" in error for error in result["errors"]))

    def test_writer_uses_exact_full_filename_as_shared_detail_location(self):
        payload = json.loads(json.dumps(self.full))
        payload["devkitRoot"] = "C:/ARKDevkit"
        payload["resourceCatalog"] = []
        payload["coverage"]["creaturesLoaded"] = 0
        payload["coverage"]["attacksDecoded"] = 0

        with tempfile.TemporaryDirectory() as temporary_directory:
            outputs = write_outputs(payload, Path(temporary_directory))
            ai_payload = json.loads(Path(outputs["ai"]).read_text(encoding="utf-8"))
            query_payload = json.loads(
                Path(outputs["query"]).read_text(encoding="utf-8")
            )

        self.assertEqual(
            ai_payload["detailLocation"],
            Path(outputs["full"]).name,
        )
        self.assertNotIn("detailLocation", ai_payload["scanManifest"])
        self.assertNotIn("detailLocation", ai_payload["sourceSet"])
        self.assertEqual(
            set(query_payload),
            {
                "schema",
                "querySchema",
                "generatedAt",
                "datasetRevision",
                "scanManifestHash",
                "methodology",
                "coverage",
                "bestRows",
            },
        )
        self.assertNotIn("rows", query_payload)
        self.assertNotIn("componentCatalog", query_payload)

    def test_validator_rejects_detail_location_not_matching_passed_full_file(self):
        self.ai["detailLocation"] = "wrong.full.json"
        self.ai["tokenEstimate"] = {
            "method": "ceil(characters/4)",
            "characters": 1000,
            "estimatedTokens": 250,
        }

        result = validate_harvest_report(
            self.full,
            self.ai,
            full_path=Path("reports/harvest_ranking_metal.full.json"),
            full_characters=10000,
            ai_characters=1000,
        )

        self.assertFalse(result["valid"])
        self.assertTrue(any("detailLocation mismatch" in error for error in result["errors"]))

    def test_full_report_cannot_invent_observed_yield(self):
        self.full["rows"][0]["observedYieldPerSecond"] = 999.0

        result = self.validate()

        self.assertFalse(result["valid"])
        self.assertTrue(any("observedYieldPerSecond" in error for error in result["errors"]))

    def test_unknown_summary_missing_facts_and_examples_are_contract_checked(self):
        self.ai["unknownSummary"][1]["missingFacts"] = []
        self.ai["unknownSummary"][1]["examples"][0]["creature"] = "InventedCreature"

        result = self.validate()

        self.assertFalse(result["valid"])
        self.assertTrue(any("unknownSummary" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
