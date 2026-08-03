import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.harvest_catalog_sqlite import (  # noqa: E402
    convert_resource_node_catalog,
)
from blueprint_translator.harvest_dominance_audit import (  # noqa: E402
    HARVEST_RANKING_POLICY_VERSION,
    HarvestDominanceAuditInvalid,
    audit_harvest_rankings,
    render_harvest_dominance_markdown,
)
from blueprint_translator.harvest_evaluation_catalog import (  # noqa: E402
    HarvestEvaluationEngine,
)
from blueprint_translator.harvest_ranking import (  # noqa: E402
    YIELD_MODEL_VERSION,
    evaluate_attack_resource,
)
from audit_ark_harvest_rankings import main as audit_main  # noqa: E402


EVALUATION_REVISION = "e" * 64
COMPONENT_REVISION = "c" * 64
EXTRACTOR_VERSION = "ark-creature-attack-catalog/v3"


def _attack(
    index: int,
    name: str,
    damage_type: str,
    *,
    conditional: bool = False,
) -> dict[str, object]:
    return {
        "attackIndex": index,
        "attackName": name,
        "damageType": damage_type,
        "baseDamage": 20.0,
        "attackInterval": 1.0,
        "riderAttackInterval": 1.0,
        "preventWithRider": False,
        "useBlueprintCanRiderAttack": conditional,
        "useBlueprintAdjustOutputDamage": False,
        "gaps": [],
    }


def _creature(
    name: str,
    species_key: str,
    object_path: str,
    attacks: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "name": name,
        "speciesKey": species_key,
        "dinoNameTag": name,
        "objectPath": object_path,
        "tameability": {"status": "ALLOWED", "reasonCodes": []},
        "rideability": {"status": "ALLOWED", "reasonCodes": []},
        "attacks": attacks,
    }


def _component(
    name: str,
    resource: str,
    multipliers: dict[str, float],
    *,
    effectiveness_quantity_multiplier: float = 1.0,
) -> dict[str, object]:
    return {
        "component": name,
        "objectPath": f"/Game/Components/{name}.{name}",
        "maxHarvestHealth": 75.0,
        "harvestHealthGiveResourceInterval": 20.0,
        "clampResourceHarvestDamage": True,
        "isSingleUnitHarvest": False,
        "resourceEntries": [
            {
                "entryIndex": 0,
                "resource": resource,
                "entryWeight": 1.0,
                "effectivenessQuantityMultiplier": effectiveness_quantity_multiplier,
                "overrideQuantityMin": 1.0,
                "overrideQuantityMax": 1.0,
                "overrideQuantityRandomPower": 1.0,
                "weightOverrides": {},
                "minQuantityOverrides": {},
                "maxQuantityOverrides": {},
                "gaps": [],
                "rankingGaps": [],
            }
        ],
        "damageEntries": [
            {
                "entryIndex": index,
                "damageTypeParent": damage_type,
                "damageMultiplier": 1.0,
                "harvestQuantityMultiplier": multiplier,
                "damageHarvestAdditionalEffectiveness": 0.0,
                "gaps": [],
            }
            for index, (damage_type, multiplier) in enumerate(multipliers.items())
        ],
        "gaps": [],
        "rankingGaps": [],
    }


def _node(
    node_id: str,
    component: str,
    resource: str,
    *,
    family: str = "TheIsland",
) -> dict[str, object]:
    return {
        "id": node_id,
        "name": node_id,
        "objectPath": f"/Game/Nodes/{node_id}.{node_id}",
        "harvestComponent": {
            "packagePath": f"/Game/Components/{component}",
        },
        "resources": {
            "status": "CONFIRMED",
            "count": 1,
            "items": [
                {
                    "entryIndex": 0,
                    "resource": resource,
                    "nodeResourceId": f"resource-{node_id}",
                }
            ],
        },
        "mapReferences": {
            "status": "REFERENCE_SCAN_COMPLETE",
            "items": [
                {
                    "mapFamily": family,
                    "objectPath": f"/Game/Maps/{family}/{node_id}",
                    "evidenceStatus": "CONFIRMED",
                }
            ],
        },
        "mapUsage": {
            "status": "PARTIAL",
            "claimsCompleteMapUsage": False,
            "families": [family],
        },
    }


def _fixture_payloads() -> tuple[dict[str, object], dict[str, object]]:
    resource = "PrimalItemResource_Test_C"
    dread_base = _creature(
        "Dreadnoughtus",
        "dreadnoughtus",
        "/Game/ASA/Dinos/Dreadnoughtus/Dreadnoughtus_Character_BP",
        [
            _attack(0, "Confirmed", "DmgDreadConfirmed_C"),
            _attack(1, "Conditional", "DmgDreadConditional_C", conditional=True),
            _attack(2, "Tie", "DmgDreadTie_C"),
        ],
    )
    dread_special = _creature(
        "Dreadnoughtus Mission Variant",
        "dreadnoughtus",
        "/Game/Mission/Dinos/Dreadnoughtus_Character_BP_Special",
        [_attack(0, "Mission Slam", "DmgDreadSpecial_C")],
    )
    anky = _creature(
        "Ankylosaurus",
        "anky",
        "/Game/PrimalEarth/Dinos/Anky/Anky_Character_BP",
        [
            _attack(0, "Confirmed", "DmgAnkyConfirmed_C"),
            _attack(1, "Conditional comparison", "DmgAnkyConditional_C"),
            _attack(2, "Special comparison", "DmgAnkySpecial_C"),
            _attack(3, "Tie", "DmgAnkyTie_C"),
        ],
    )
    components = [
        _component(
            "ConditionalComponent",
            resource,
            {"DmgDreadConditional_C": 3.0, "DmgAnkyConditional_C": 1.0},
            effectiveness_quantity_multiplier=2.0,
        ),
        _component(
            "ConfirmedComponent",
            resource,
            {"DmgDreadConfirmed_C": 2.0, "DmgAnkyConfirmed_C": 1.0},
        ),
        _component(
            "SpecialComponent",
            resource,
            {"DmgDreadSpecial_C": 3.0, "DmgAnkySpecial_C": 1.0},
        ),
        _component(
            "TieComponent",
            resource,
            {"DmgDreadTie_C": 1.0, "DmgAnkyTie_C": 1.0},
        ),
    ]
    evaluation = {
        "schema": "ark-harvest-evaluation-catalog/v2",
        "dataset": {
            "revision": EVALUATION_REVISION,
            "componentDatasetRevision": COMPONENT_REVISION,
            "extractorVersion": EXTRACTOR_VERSION,
            "generatedAt": "2026-01-02T00:00:00+00:00",
        },
        "methodology": {
            "formulaVersion": YIELD_MODEL_VERSION,
            "policyVersion": HARVEST_RANKING_POLICY_VERSION,
            "usageScope": "TAMED_RIDDEN",
            "rideabilityRequirement": "B_ALLOW_RIDING_TRUE",
            "metric": "estimatedYieldPerNode",
            "scoreBasis": "ESTIMATED_RESOURCE_UNITS_PER_COMPLETE_NODE",
        },
        "coverage": {"claimsAllCreatures": False},
        "claimBlockers": ["FIXTURE_PARTIAL_SCOPE"],
        "components": components,
        "damageTypeParents": {},
        "resourceDamageOverrides": [],
        "damageTypeGaps": {},
        "creatures": [dread_base, dread_special, anky],
    }
    nodes = [
        _node("conditional-a", "ConditionalComponent", resource),
        _node("conditional-b", "ConditionalComponent", resource),
        _node("confirmed", "ConfirmedComponent", resource),
        _node("special", "SpecialComponent", resource),
        _node("tie", "TieComponent", resource),
    ]
    node_catalog = {
        "schema": "ark-resource-node-catalog/v1",
        "dataset": {
            "revision": "n" * 64,
            "generatedAt": "2026-01-03T00:00:00+00:00",
            "evaluationDatasetRevision": EVALUATION_REVISION,
            "componentDatasetRevision": COMPONENT_REVISION,
        },
        "coverage": {"nodesDecoded": len(nodes)},
        "nodes": nodes,
    }
    return node_catalog, evaluation


def _write_fixture(root: Path) -> tuple[Path, Path, Path]:
    node_catalog, evaluation = _fixture_payloads()
    node_path = root / "nodes.json"
    evaluation_path = root / "evaluation.json"
    sqlite_path = root / "harvest.sqlite"
    node_path.write_text(
        json.dumps(node_catalog, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    evaluation_path.write_text(
        json.dumps(evaluation, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    convert_resource_node_catalog(node_path, sqlite_path)
    return node_path, evaluation_path, sqlite_path


class HarvestDominanceAuditTests(unittest.TestCase):
    def test_score_breakdown_exposes_inputs_and_omitted_factors_without_changing_score(self):
        component = _component(
            "BreakdownComponent",
            "PrimalItemResource_Test_C",
            {"DmgTest_C": 2.0},
            effectiveness_quantity_multiplier=2.0,
        )
        row = evaluate_attack_resource(
            creature="Test creature",
            creature_object_path="/Game/Dinos/Test",
            attack=_attack(0, "Test", "DmgTest_C", conditional=True),
            component=component,
            resource="PrimalItemResource_Test_C",
            resource_entry_index=0,
            damage_type_parents={},
            resource_damage_overrides={},
        )

        self.assertEqual(row["estimatedYieldPerNode"], 14.0)
        self.assertEqual(row["effectivenessQuantityMultiplier"], 2.0)
        self.assertEqual(
            row["scoreBreakdown"],
            {
                "metric": "estimatedYieldPerNode",
                "grantCalls": 14,
                "resourceWeightShare": 1.0,
                "expectedQuantityPerSelection": 1.0,
                "estimatedHits": 4,
                "effectiveDamagePerHit": 20.0,
                "contributions": [
                    {"factor": "grantCalls", "value": 14},
                    {"factor": "resourceWeightShare", "value": 1.0},
                    {"factor": "expectedQuantityPerSelection", "value": 1.0},
                ],
                "omittedFactors": [
                    "RUNTIME_BLUEPRINT_RIDER_ELIGIBILITY",
                    "RUNTIME_OUTPUT_DAMAGE_HOOK",
                    "BUFFS",
                    "GENES",
                    "MISSIONS",
                    "SERVER_OR_MOD_HOOKS",
                    "MOVEMENT",
                    "STAMINA",
                    "WEIGHT_AND_CARRY_REDUCTION",
                    "AOE_AND_NODE_DENSITY",
                    "MAP_AVAILABILITY",
                    "AUTO_HARVEST",
                    "COOLDOWN_OR_CHARGE",
                    "EFFECTIVENESS_QUANTITY_MULTIPLIER_NOT_MODELED",
                ],
                "evidenceTier": "CONDITIONAL",
            },
        )

    def test_score_breakdown_tier_follows_final_creature_evidence_tier(self):
        node_catalog, evaluation = _fixture_payloads()
        evaluation["creatures"][0]["tameability"] = {
            "status": "UNKNOWN",
            "reasonCodes": ["TAMEABILITY_NOT_RECOVERED"],
        }
        engine = HarvestEvaluationEngine(evaluation)

        result = engine.rank_node_resource(
            node_catalog,
            node_id="confirmed",
            node_resource_id="resource-confirmed",
            limit=10,
        )
        dread = next(
            row for row in result["items"] if row["speciesKey"] == "dreadnoughtus"
        )

        self.assertEqual(dread["rankingTier"], "CONDITIONAL")
        self.assertEqual(dread["scoreBreakdown"]["evidenceTier"], "CONDITIONAL")

    def test_audit_counts_occurrences_unique_keys_ties_conditional_and_special_variant(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            node_path, evaluation_path, sqlite_path = _write_fixture(Path(temp_dir))

            report = audit_harvest_rankings(
                node_catalog_path=node_path,
                evaluation_catalog_path=evaluation_path,
                sqlite_catalog_path=sqlite_path,
                species_query="dreadnoughtus",
                code_commit="a" * 40,
            )

        self.assertEqual(report["schema"], "blueprint-to-code.harvest-dominance-audit/v1")
        self.assertEqual(report["processing"]["evaluationStrategy"], "STREAM_UNIQUE_KEYS")
        self.assertEqual(report["processing"]["rankingsComputed"], 4)
        self.assertEqual(report["global"]["occurrencesTotal"], 5)
        self.assertEqual(report["global"]["uniqueEvaluationKeysTotal"], 4)
        self.assertEqual(report["global"]["rankableOccurrences"], 5)
        self.assertEqual(report["global"]["rankableUniqueEvaluationKeys"], 4)
        self.assertEqual(report["targetSpecies"]["topOccurrences"], 5)
        self.assertEqual(report["targetSpecies"]["topUniqueEvaluationKeys"], 4)
        self.assertEqual(report["targetSpecies"]["confirmedTopOccurrences"], 3)
        self.assertEqual(report["targetSpecies"]["conditionalTopOccurrences"], 2)
        self.assertEqual(report["targetSpecies"]["exclusiveTopOccurrences"], 4)
        self.assertEqual(report["global"]["tieOccurrences"], 1)
        self.assertEqual(report["targetSpecies"]["variantCount"], 2)
        self.assertEqual(len(report["cases"]), 5)
        self.assertTrue(
            any(
                case["winner"]["creatureObjectPath"].endswith("_Special")
                and "SPECIAL_VARIANT_MAX_POOLING" in case["rootCauses"]
                for case in report["cases"]
            )
        )
        self.assertTrue(
            any(
                case["winner"]["rankingTier"] == "CONDITIONAL"
                and "CONDITIONAL_ATTACK_WON" in case["rootCauses"]
                and case["effectivenessQuantityMultiplier"] == 2.0
                for case in report["cases"]
            )
        )
        tie = next(case for case in report["cases"] if case["node"]["id"] == "tie")
        self.assertFalse(tie["exclusiveTop"])
        self.assertIn("TIE_PRESENT", tie["rootCauses"])
        self.assertGreaterEqual(len(tie["comparisonRows"]), 2)
        self.assertEqual(
            report["verificationBoundary"]["proves"],
            "production implementation == independent implementation",
        )
        self.assertEqual(
            report["verificationBoundary"]["doesNotProve"],
            "static model == real game",
        )

    def test_audit_is_deterministic(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            node_path, evaluation_path, sqlite_path = _write_fixture(Path(temp_dir))
            kwargs = {
                "node_catalog_path": node_path,
                "evaluation_catalog_path": evaluation_path,
                "sqlite_catalog_path": sqlite_path,
                "species_query": "dreadnoughtus",
                "code_commit": "a" * 40,
            }
            first = audit_harvest_rankings(**kwargs)
            second = audit_harvest_rankings(**kwargs)

        self.assertEqual(first, second)
        self.assertEqual(
            render_harvest_dominance_markdown(first),
            render_harvest_dominance_markdown(second),
        )
        self.assertIn("static model == real game", render_harvest_dominance_markdown(first))

    def test_cli_writes_json_and_markdown_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            node_path, evaluation_path, sqlite_path = _write_fixture(root)
            json_out = root / "audit.json"
            markdown_out = root / "audit.md"

            exit_code = audit_main(
                [
                    "--node-catalog",
                    str(node_path),
                    "--evaluation-catalog",
                    str(evaluation_path),
                    "--sqlite-catalog",
                    str(sqlite_path),
                    "--species",
                    "dreadnoughtus",
                    "--json-out",
                    str(json_out),
                    "--markdown-out",
                    str(markdown_out),
                ]
            )

            payload = json.loads(json_out.read_text(encoding="utf-8"))
            markdown = markdown_out.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["targetSpecies"]["topOccurrences"], 5)
        self.assertIn("## Verification boundary", markdown)

    def test_audit_rejects_stale_model_extractor_and_policy(self):
        for label, mutate in (
            (
                "model",
                lambda payload: payload["methodology"].__setitem__(
                    "formulaVersion", "stale-model"
                ),
            ),
            (
                "extractor",
                lambda payload: payload["dataset"].__setitem__(
                    "extractorVersion", "stale-extractor"
                ),
            ),
            (
                "policy",
                lambda payload: payload["methodology"].__setitem__(
                    "policyVersion", "stale-policy"
                ),
            ),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                node_path, evaluation_path, sqlite_path = _write_fixture(root)
                evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
                mutate(evaluation)
                evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")

                with self.assertRaisesRegex(HarvestDominanceAuditInvalid, label.upper()):
                    audit_harvest_rankings(
                        node_catalog_path=node_path,
                        evaluation_catalog_path=evaluation_path,
                        sqlite_catalog_path=sqlite_path,
                        species_query="dreadnoughtus",
                        code_commit="a" * 40,
                    )

    def test_audit_fails_closed_when_canonical_catalog_changes_after_sqlite_build(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            node_path, evaluation_path, sqlite_path = _write_fixture(root)
            node_catalog = json.loads(node_path.read_text(encoding="utf-8"))
            node_catalog["nodes"][0]["name"] = "tampered"
            node_path.write_text(json.dumps(node_catalog), encoding="utf-8")

            with self.assertRaisesRegex(
                HarvestDominanceAuditInvalid,
                "SQLite harvest catalog does not match the canonical resource-node JSON",
            ):
                audit_harvest_rankings(
                    node_catalog_path=node_path,
                    evaluation_catalog_path=evaluation_path,
                    sqlite_catalog_path=sqlite_path,
                    species_query="dreadnoughtus",
                    code_commit="a" * 40,
                )

    def test_audit_rejects_cross_catalog_revision_drift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            node_path, evaluation_path, _sqlite_path = _write_fixture(root)
            node_catalog = json.loads(node_path.read_text(encoding="utf-8"))
            node_catalog["dataset"]["evaluationDatasetRevision"] = "f" * 64
            node_path.write_text(json.dumps(node_catalog), encoding="utf-8")
            sqlite_path = root / "harvest-rebuilt.sqlite"
            convert_resource_node_catalog(node_path, sqlite_path)

            with self.assertRaisesRegex(
                HarvestDominanceAuditInvalid,
                "evaluation revisions do not match",
            ):
                audit_harvest_rankings(
                    node_catalog_path=node_path,
                    evaluation_catalog_path=evaluation_path,
                    sqlite_catalog_path=sqlite_path,
                    species_query="dreadnoughtus",
                    code_commit="a" * 40,
                )


if __name__ == "__main__":
    unittest.main()
