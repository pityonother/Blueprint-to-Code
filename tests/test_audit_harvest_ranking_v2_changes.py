from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_harvest_ranking_v2_changes as audit_module  # noqa: E402


def _node_catalog() -> dict[str, object]:
    return {
        "dataset": {"revision": "node-revision"},
        "nodes": [
            {
                "id": "node",
                "harvestComponent": {"packagePath": "/Game/Test/Component"},
                "resources": {
                    "items": [
                        {
                            "entryIndex": 0,
                            "resource": "PrimalItemResource_Test_C",
                            "nodeResourceId": "resource",
                        }
                    ]
                },
            }
        ],
    }


def _evaluation_catalog() -> dict[str, object]:
    return {
        "dataset": {"revision": "evaluation-revision"},
        "methodology": {
            "contractVersion": audit_module.HARVEST_RANKING_CONTRACT_VERSION
        },
    }


def _variant_audits() -> list[dict[str, object]]:
    return [
        {
            "speciesKey": "aaa-ancestry-root",
            "canonicalObjectPath": "/Game/Dinos/Root/Root_Character_BP",
            "selectionReasons": ["UNIQUE_ANCESTRY_ROOT_BASE_VARIANT"],
            "excludedVariantClasses": ["UNKNOWN_VARIANT"],
            "ambiguous": False,
            "ambiguityReasons": [],
        }
    ] + [
        {
            "speciesKey": f"ambiguous-{index:02d}",
            "canonicalObjectPath": None,
            "selectionReasons": [],
            "excludedVariantClasses": ["EVENT"],
            "ambiguous": True,
            "ambiguityReasons": [
                "CANONICAL_VARIANT_AMBIGUOUS",
                "NO_BASE_VARIANT_CANDIDATE",
            ],
        }
        for index in range(12)
    ] + [
        {
            "speciesKey": "unique-base",
            "canonicalObjectPath": "/Game/Dinos/Unique/Unique_Character_BP",
            "selectionReasons": ["UNIQUE_BASE_VARIANT"],
            "excludedVariantClasses": ["MISSION"],
            "ambiguous": False,
            "ambiguityReasons": [],
        }
    ]


class _FakeEngine:
    def __init__(self, _catalog: dict[str, object]) -> None:
        pass

    def _rank_node_resource_v1(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        return {"items": [{"speciesKey": "legacy"}]}

    def canonical_variant_audits(self) -> list[dict[str, object]]:
        return _variant_audits()

    def rank_node_resource(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        audits = _variant_audits()
        ambiguity_examples = [
            row for row in audits if row["ambiguous"] is True
        ][:10]
        return {
            "confirmedItems": [{"speciesKey": "confirmed"}],
            "conditionalItems": [{"speciesKey": "conditional"}],
            "items": [{"speciesKey": "confirmed"}],
            "variantSelectionAudits": audits[:10],
            "coverage": {
                "rowsWithEffectivenessField": 7,
                "rowsWithNonNeutralEffectiveness": 2,
                "rowsConditionalBecauseEffectiveness": 2,
                "canonicalVariantsAudited": 14,
                "canonicalVariantAmbiguousSpecies": 12,
                "canonicalVariantAmbiguityExamples": ambiguity_examples,
            },
        }


class HarvestRankingV2ChangeAuditTests(unittest.TestCase):
    def test_audit_exposes_contracts_bounded_variant_audits_and_tier_semantics(self):
        with patch.object(audit_module, "HarvestEvaluationEngine", _FakeEngine):
            result = audit_module.audit_changes(
                _node_catalog(),
                _evaluation_catalog(),
                sample_limit=1,
            )

        self.assertEqual(
            result["metricContracts"],
            {
                "staticCompleteNodeTargetYield": {
                    "scoreBasis": "STATIC_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE",
                    "unit": "target_resource_units/node",
                    "runtime": False,
                },
                "staticYieldPerAttackCycleSecond": {
                    "scoreBasis": (
                        "STATIC_TARGET_RESOURCE_UNITS_PER_ATTACK_CYCLE_SECOND"
                    ),
                    "unit": "target_resource_units/attack_cycle_second",
                    "runtime": False,
                },
                "observedYieldPerNode": {
                    "scoreBasis": "OBSERVED_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE",
                    "unit": "target_resource_units/node",
                    "runtime": True,
                },
                "observedYieldPerSecond": {
                    "scoreBasis": "OBSERVED_TARGET_RESOURCE_UNITS_PER_SECOND",
                    "unit": "target_resource_units/second",
                    "runtime": True,
                },
            },
        )
        self.assertEqual(
            result["effectivenessCoverage"],
            {
                "rowsWithEffectivenessField": 7,
                "rowsWithNonNeutralEffectiveness": 2,
                "rowsConditionalBecauseEffectiveness": 2,
            },
        )
        self.assertEqual(
            result["cycleTimingContract"]["firstHitTiming"],
            "FIRST_HIT_AT_END_OF_FIRST_ATTACK_CYCLE",
        )
        variant = result["canonicalVariantAudit"]
        self.assertEqual(variant["speciesAudited"], 14)
        self.assertEqual(variant["ambiguousSpecies"], 12)
        self.assertEqual(variant["scope"], "ALL_DISCOVERED_CREATURE_ASSETS")
        self.assertEqual(variant["rankingUsageScope"], "TAMED_RIDDEN")
        self.assertEqual(variant["auditExampleLimit"], 10)
        self.assertEqual(len(variant["audits"]), 14)
        self.assertEqual(len(variant["auditExamples"]), 10)
        self.assertEqual(len(variant["ambiguityExamples"]), 10)
        self.assertTrue(all(row["ambiguous"] for row in variant["ambiguityExamples"]))
        ancestry = next(
            row
            for row in variant["auditExamples"]
            if row["speciesKey"] == "aaa-ancestry-root"
        )
        self.assertEqual(
            ancestry["selectionReasons"],
            ["UNIQUE_ANCESTRY_ROOT_BASE_VARIANT"],
        )
        self.assertEqual(ancestry["excludedVariantClasses"], ["UNKNOWN_VARIANT"])
        self.assertEqual(
            result["resultTierSemantics"]["items"],
            "CONFIRMED_ITEMS_COMPATIBILITY_ALIAS_ONLY",
        )
        self.assertEqual(
            result["resultTierSemantics"]["relativeBaselines"],
            "INDEPENDENT_WITHIN_EACH_EVIDENCE_TIER",
        )

        markdown = audit_module.render_markdown(result)
        self.assertIn("Effectiveness rows with field: `7`", markdown)
        self.assertIn("compatibility alias of `confirmedItems` only", markdown)
        self.assertIn(
            "`observedYieldPerSecond`: "
            "`OBSERVED_TARGET_RESOURCE_UNITS_PER_SECOND`, "
            "`target_resource_units/second`, runtime=`true`",
            markdown,
        )

    def test_audit_rejects_a_compatibility_items_tier_leak(self):
        class _LeakyItemsEngine(_FakeEngine):
            def rank_node_resource(
                self, *_args: object, **_kwargs: object
            ) -> dict[str, object]:
                result = super().rank_node_resource(*_args, **_kwargs)
                result["items"] = list(result["conditionalItems"])
                return result

        with patch.object(audit_module, "HarvestEvaluationEngine", _LeakyItemsEngine):
            with self.assertRaisesRegex(
                ValueError,
                "items must remain a confirmedItems-only compatibility alias",
            ):
                audit_module.audit_changes(
                    _node_catalog(),
                    _evaluation_catalog(),
                )


if __name__ == "__main__":
    unittest.main()
