#!/usr/bin/env python3
"""Compare legacy combined ranking behavior with Ranking Contract v2."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.harvest_evaluation_catalog import (  # noqa: E402
    AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
    HARVEST_RANKING_CONTRACT_VERSION,
    METRIC_CONTRACTS,
    METRIC_STATIC_TOTAL,
    POLICY_INCLUDE_CONDITIONAL,
    TAMED_RIDDEN,
    VARIANT_CANONICAL,
    HarvestEvaluationEngine,
)
from blueprint_translator.resource_nodes import canonical_package_path  # noqa: E402


DEFAULT_NODE_CATALOG = (
    PROJECT_ROOT / "analysis" / "harvest_nodes" / "resource_node_catalog.json"
)
DEFAULT_EVALUATION_CATALOG = (
    PROJECT_ROOT
    / "analysis"
    / "harvest_rankings"
    / "harvest_evaluation_catalog.json"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "analysis"
    / "harvest_rankings"
    / "audits"
    / "ranking-contract-v2-changed-cases.json"
)

_VARIANT_AUDIT_EXAMPLE_LIMIT = 10
_EFFECTIVENESS_COVERAGE_FIELDS = (
    "rowsWithEffectivenessField",
    "rowsWithNonNeutralEffectiveness",
    "rowsConditionalBecauseEffectiveness",
)


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _species(row: object) -> str:
    return str(row.get("speciesKey") or "").casefold() if isinstance(row, dict) else ""


def _top_profile(row: object) -> tuple[str, int | None, str, str]:
    if not isinstance(row, dict):
        return "", None, "", ""
    attack_index = row.get("attackIndex")
    return (
        str(row.get("creatureObjectPath") or ""),
        (
            int(attack_index)
            if isinstance(attack_index, int) and not isinstance(attack_index, bool)
            else None
        ),
        str(row.get("attackName") or ""),
        str(row.get("rankingTier") or ""),
    )


def _profile_rows(counter: Counter[tuple[str, int | None, str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "creatureObjectPath": key[0],
            "attackIndex": key[1],
            "attackName": key[2],
            "rankingTier": key[3],
            "topOccurrences": occurrences,
        }
        for key, occurrences in sorted(
            counter.items(),
            key=lambda item: (
                -item[1],
                item[0][0],
                item[0][1] if item[0][1] is not None else -1,
                item[0][2],
                item[0][3],
            ),
        )
    ]


def _coverage_count(coverage: dict[str, Any], field: str) -> int:
    value = coverage.get(field, 0)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise ValueError(f"coverage.{field} must be a non-negative integer")
    return value


def audit_changes(
    node_catalog: dict[str, Any],
    evaluation_catalog: dict[str, Any],
    *,
    sample_limit: int = 100,
) -> dict[str, Any]:
    if evaluation_catalog.get("methodology", {}).get(
        "contractVersion"
    ) != HARVEST_RANKING_CONTRACT_VERSION:
        raise ValueError("Evaluation catalog is not Ranking Contract v2.")
    engine = HarvestEvaluationEngine(evaluation_catalog)
    complete_variant_audits = engine.canonical_variant_audits()
    canonical_audits: dict[str, dict[str, Any]] = {}
    for raw_audit in complete_variant_audits:
        if not isinstance(raw_audit, dict):
            raise ValueError("Complete canonical variant audit entries must be objects.")
        species_key = str(raw_audit.get("speciesKey") or "")
        if not species_key:
            raise ValueError(
                "Complete canonical variant audit speciesKey must be non-empty."
            )
        if species_key in canonical_audits:
            raise ValueError(
                f"Duplicate complete canonical variant audit: {species_key}"
            )
        canonical_audits[species_key] = dict(raw_audit)
    canonical_ambiguity_examples = {
        species_key: audit
        for species_key, audit in canonical_audits.items()
        if audit.get("ambiguous") is True
    }
    representatives: OrderedDict[
        tuple[str, str, int | None], tuple[str, str]
    ] = OrderedDict()
    occurrences = Counter()
    for node in node_catalog.get("nodes", []):
        if not isinstance(node, dict):
            continue
        component_ref = node.get("harvestComponent")
        component = canonical_package_path(
            component_ref.get("packagePath")
            if isinstance(component_ref, dict)
            else ""
        )
        for resource in node.get("resources", {}).get("items", []):
            if not isinstance(resource, dict):
                continue
            raw_entry_index = resource.get("entryIndex")
            entry_index = (
                int(raw_entry_index)
                if isinstance(raw_entry_index, int)
                and not isinstance(raw_entry_index, bool)
                else None
            )
            key = (
                component.casefold(),
                str(resource.get("resource") or "").casefold(),
                entry_index,
            )
            representatives.setdefault(
                key,
                (
                    str(node.get("id") or ""),
                    str(resource.get("nodeResourceId") or ""),
                ),
            )
            occurrences[key] += 1

    counts = Counter()
    for field in (
        "dreadV2ConfirmedTopUnique",
        "dreadV2ConfirmedTopOccurrences",
        "dreadV2ConfirmedRankedUnique",
        "dreadV2ConfirmedRankedOccurrences",
    ):
        counts[field] = 0
    dread_legacy_top_profiles: Counter[tuple[str, int | None, str, str]] = Counter()
    dread_confirmed_top_profiles: Counter[tuple[str, int | None, str, str]] = Counter()
    dread_conditional_top_profiles: Counter[tuple[str, int | None, str, str]] = Counter()
    effectiveness_coverage: Counter[str] = Counter()
    canonical_species_audited: int | None = len(canonical_audits)
    canonical_ambiguous_species: int | None = len(canonical_ambiguity_examples)
    samples: list[dict[str, Any]] = []
    dread_key = "dreadnoughtus"
    for key, (node_id, node_resource_id) in representatives.items():
        legacy = engine._rank_node_resource_v1(  # noqa: SLF001 - explicit audit baseline
            node_catalog,
            node_id=node_id,
            node_resource_id=node_resource_id,
            limit=10,
        )
        current = engine.rank_node_resource(
            node_catalog,
            node_id=node_id,
            node_resource_id=node_resource_id,
            limit=10,
            evidence_policy=POLICY_INCLUDE_CONDITIONAL,
            variant_policy=VARIANT_CANONICAL,
            metric=METRIC_STATIC_TOTAL,
            availability_policy=AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
        )
        occurrence_count = occurrences[key]
        legacy_items = legacy.get("items") or []
        confirmed_items = current.get("confirmedItems") or []
        conditional_items = current.get("conditionalItems") or []
        compatibility_items = current.get("items") or []
        if compatibility_items != confirmed_items:
            raise ValueError(
                "items must remain a confirmedItems-only compatibility alias"
            )
        coverage = current.get("coverage")
        if not isinstance(coverage, dict):
            raise ValueError("Ranking result coverage must be an object.")
        for field in _EFFECTIVENESS_COVERAGE_FIELDS:
            effectiveness_coverage[field] += _coverage_count(coverage, field)
        for field in (
            "canonicalVariantsAudited",
            "canonicalVariantAmbiguousSpecies",
        ):
            if field not in coverage:
                raise ValueError(f"coverage.{field} is required")
        reported_species_audited = _coverage_count(
            coverage, "canonicalVariantsAudited"
        )
        reported_ambiguous_species = _coverage_count(
            coverage, "canonicalVariantAmbiguousSpecies"
        )
        if canonical_species_audited is None:
            canonical_species_audited = reported_species_audited
            canonical_ambiguous_species = reported_ambiguous_species
        elif (
            canonical_species_audited != reported_species_audited
            or canonical_ambiguous_species != reported_ambiguous_species
        ):
            raise ValueError(
                "Canonical variant coverage changed across node/resource queries"
            )
        raw_variant_audits = current.get("variantSelectionAudits") or []
        if not isinstance(raw_variant_audits, list):
            raise ValueError("variantSelectionAudits must be an array.")
        for raw_audit in raw_variant_audits:
            if not isinstance(raw_audit, dict):
                raise ValueError("variantSelectionAudits entries must be objects.")
            species_key = str(raw_audit.get("speciesKey") or "")
            if not species_key:
                raise ValueError("variantSelectionAudits speciesKey must be non-empty.")
            audit = dict(raw_audit)
            previous = canonical_audits.setdefault(species_key, audit)
            if previous != audit:
                raise ValueError(
                    "Canonical variant audit changed across node/resource queries: "
                    f"{species_key}"
                )
        raw_ambiguity_examples = coverage.get(
            "canonicalVariantAmbiguityExamples"
        ) or []
        if not isinstance(raw_ambiguity_examples, list):
            raise ValueError(
                "coverage.canonicalVariantAmbiguityExamples must be an array"
            )
        for raw_audit in raw_ambiguity_examples:
            if not isinstance(raw_audit, dict):
                raise ValueError(
                    "coverage.canonicalVariantAmbiguityExamples entries must be objects"
                )
            species_key = str(raw_audit.get("speciesKey") or "")
            if not species_key or raw_audit.get("ambiguous") is not True:
                raise ValueError(
                    "Canonical ambiguity examples must identify an ambiguous species"
                )
            audit = dict(raw_audit)
            previous = canonical_ambiguity_examples.setdefault(species_key, audit)
            if previous != audit:
                raise ValueError(
                    "Canonical ambiguity example changed across queries: "
                    f"{species_key}"
                )
        if reported_species_audited < len(raw_variant_audits):
            raise ValueError(
                "coverage.canonicalVariantsAudited is smaller than returned audits"
            )
        if reported_ambiguous_species < len(raw_ambiguity_examples):
            raise ValueError(
                "coverage.canonicalVariantAmbiguousSpecies is smaller than examples"
            )
        legacy_top = _species(legacy_items[0] if legacy_items else None)
        confirmed_top = _species(confirmed_items[0] if confirmed_items else None)
        conditional_top = _species(
            conditional_items[0] if conditional_items else None
        )
        counts["uniquePairs"] += 1
        counts["occurrences"] += occurrence_count
        if legacy_top == dread_key:
            counts["dreadLegacyTopUnique"] += 1
            counts["dreadLegacyTopOccurrences"] += occurrence_count
            dread_legacy_top_profiles[_top_profile(legacy_items[0])] += occurrence_count
        if confirmed_top == dread_key:
            counts["dreadV2ConfirmedTopUnique"] += 1
            counts["dreadV2ConfirmedTopOccurrences"] += occurrence_count
            dread_confirmed_top_profiles[
                _top_profile(confirmed_items[0])
            ] += occurrence_count
        if conditional_top == dread_key:
            counts["dreadV2ConditionalTopUnique"] += 1
            counts["dreadV2ConditionalTopOccurrences"] += occurrence_count
            dread_conditional_top_profiles[
                _top_profile(conditional_items[0])
            ] += occurrence_count
        legacy_dread = next(
            (row for row in legacy_items if _species(row) == dread_key), None
        )
        current_dread_confirmed = next(
            (row for row in confirmed_items if _species(row) == dread_key), None
        )
        current_dread_conditional = next(
            (row for row in conditional_items if _species(row) == dread_key), None
        )
        if legacy_dread is not None:
            counts["dreadLegacyRankedUnique"] += 1
            counts["dreadLegacyRankedOccurrences"] += occurrence_count
        if current_dread_confirmed is not None:
            counts["dreadV2ConfirmedRankedUnique"] += 1
            counts["dreadV2ConfirmedRankedOccurrences"] += occurrence_count
        if current_dread_conditional is not None:
            counts["dreadV2ConditionalRankedUnique"] += 1
            counts["dreadV2ConditionalRankedOccurrences"] += occurrence_count
        changed = legacy_top != confirmed_top
        if changed:
            counts["confirmedTopChangedUnique"] += 1
            counts["confirmedTopChangedOccurrences"] += occurrence_count
            if len(samples) < max(0, sample_limit):
                samples.append(
                    {
                        "component": key[0],
                        "resource": key[1],
                        "entryIndex": key[2],
                        "representativeNodeId": node_id,
                        "representativeNodeResourceId": node_resource_id,
                        "occurrences": occurrence_count,
                        "legacyTop": legacy_top or None,
                        "v2ConfirmedTop": confirmed_top or None,
                        "v2ConditionalTop": conditional_top or None,
                        "dreadLegacyTier": (
                            legacy_dread.get("rankingTier")
                            if isinstance(legacy_dread, dict)
                            else None
                        ),
                        "dreadV2Tier": (
                            "CONFIRMED"
                            if current_dread_confirmed is not None
                            else "CONDITIONAL"
                            if current_dread_conditional is not None
                            else None
                        ),
                    }
                )
    ordered_variant_audits = [
        canonical_audits[species_key] for species_key in sorted(canonical_audits)
    ]
    ordered_ambiguity_examples = [
        canonical_ambiguity_examples[species_key]
        for species_key in sorted(canonical_ambiguity_examples)
    ]
    return {
        "schema": "blueprint-to-code.harvest-ranking-v2-changed-cases/v2",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "comparison": {
            "before": "legacy combined evidence + best discovered variant",
            "after": "confirmed and conditional split + canonical variant",
            "metric": METRIC_STATIC_TOTAL,
            "availabilityPolicy": AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
        },
        "metricContracts": {
            metric: dict(contract) for metric, contract in METRIC_CONTRACTS.items()
        },
        "cycleTimingContract": {
            "firstHitTiming": "FIRST_HIT_AT_END_OF_FIRST_ATTACK_CYCLE",
            "elapsedSeconds": (
                "estimatedHitsToDepleteNode * effectiveAttackInterval"
            ),
        },
        "resultTierSemantics": {
            "confirmedItems": "PRIMARY_CONFIRMED_RANKING",
            "conditionalItems": "SEPARATE_CONDITIONAL_RANKING_NOT_PROMOTED",
            "items": "CONFIRMED_ITEMS_COMPATIBILITY_ALIAS_ONLY",
            "relativeBaselines": "INDEPENDENT_WITHIN_EACH_EVIDENCE_TIER",
        },
        "dataset": {
            "node": dict(node_catalog.get("dataset") or {}),
            "evaluation": dict(evaluation_catalog.get("dataset") or {}),
        },
        "counts": dict(sorted(counts.items())),
        "effectivenessCoverage": {
            field: effectiveness_coverage[field]
            for field in _EFFECTIVENESS_COVERAGE_FIELDS
        },
        "canonicalVariantAudit": {
            "scope": "ALL_DISCOVERED_CREATURE_ASSETS",
            "rankingUsageScope": str(
                evaluation_catalog.get("methodology", {}).get("usageScope")
                or TAMED_RIDDEN
            ),
            "creatureAssetsAudited": len(
                [
                    row
                    for row in evaluation_catalog.get("creatures", [])
                    if isinstance(row, dict)
                ]
            ),
            "speciesAudited": canonical_species_audited or 0,
            "ambiguousSpecies": canonical_ambiguous_species or 0,
            "auditExampleLimit": _VARIANT_AUDIT_EXAMPLE_LIMIT,
            "audits": ordered_variant_audits,
            "auditExamples": ordered_variant_audits[
                :_VARIANT_AUDIT_EXAMPLE_LIMIT
            ],
            "ambiguityExamples": ordered_ambiguity_examples[
                :_VARIANT_AUDIT_EXAMPLE_LIMIT
            ],
        },
        "dreadTopProfiles": {
            "legacy": _profile_rows(dread_legacy_top_profiles),
            "v2Confirmed": _profile_rows(dread_confirmed_top_profiles),
            "v2Conditional": _profile_rows(dread_conditional_top_profiles),
        },
        "changedSamples": samples,
        "boundaries": {
            "orderingWasChanged": True,
            "staticYieldFormulaWasChanged": False,
            "runtimeGoldCreated": False,
            "liveKnowledgePointerChanged": False,
        },
    }


def render_markdown(result: dict[str, Any]) -> str:
    counts = result.get("counts") or {}
    effectiveness = result.get("effectivenessCoverage") or {}
    variant_audit = result.get("canonicalVariantAudit") or {}
    metric_contracts = result.get("metricContracts") or {}
    metric_lines = [
        (
            f"- `{metric}`: `{contract.get('scoreBasis')}`, "
            f"`{contract.get('unit')}`, "
            f"runtime=`{str(contract.get('runtime')).lower()}`"
        )
        for metric, contract in metric_contracts.items()
        if isinstance(contract, dict)
    ]
    return "\n".join(
        [
            "# Harvest Ranking Contract v2 changed cases",
            "",
            "## Metric contracts",
            "",
            *metric_lines,
            "",
            "## Audit counts",
            "",
            f"- Unique pairs: `{counts.get('uniquePairs', 0)}`",
            f"- Node/resource occurrences: `{counts.get('occurrences', 0)}`",
            f"- Confirmed top changed occurrences: `{counts.get('confirmedTopChangedOccurrences', 0)}`",
            f"- Dreadnoughtus legacy top occurrences: `{counts.get('dreadLegacyTopOccurrences', 0)}`",
            f"- Dreadnoughtus v2 confirmed top occurrences: `{counts.get('dreadV2ConfirmedTopOccurrences', 0)}`",
            f"- Dreadnoughtus v2 conditional top occurrences: `{counts.get('dreadV2ConditionalTopOccurrences', 0)}`",
            (
                "- Canonical creature assets audited before ranking scope: "
                f"`{variant_audit.get('creatureAssetsAudited', 0)}`"
            ),
            (
                "- Canonical species audited in "
                f"`{variant_audit.get('scope', 'ALL_DISCOVERED_CREATURE_ASSETS')}`: "
                f"`{variant_audit.get('speciesAudited', 0)}`"
            ),
            (
                "- Ranking usage scope applied after canonical audit: "
                f"`{variant_audit.get('rankingUsageScope', TAMED_RIDDEN)}`"
            ),
            f"- Canonical ambiguous species: `{variant_audit.get('ambiguousSpecies', 0)}`",
            f"- Effectiveness rows with field: `{effectiveness.get('rowsWithEffectivenessField', 0)}`",
            f"- Effectiveness non-neutral rows: `{effectiveness.get('rowsWithNonNeutralEffectiveness', 0)}`",
            f"- Rows conditional because Effectiveness is not modeled: `{effectiveness.get('rowsConditionalBecauseEffectiveness', 0)}`",
            "",
            "> `items` is a compatibility alias of `confirmedItems` only. "
            "`conditionalItems` has an independent rank and relative baseline.",
            "",
            "> v2 splits evidence tiers and requires either one BASE candidate or "
            "one unambiguous ancestry-root BASE candidate. "
            "It does not change the static complete-node formula or create runtime gold.",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node-catalog", type=Path, default=DEFAULT_NODE_CATALOG)
    parser.add_argument(
        "--evaluation-catalog", type=Path, default=DEFAULT_EVALUATION_CATALOG
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--sample-limit", type=int, default=100)
    args = parser.parse_args(argv)
    result = audit_changes(
        _load(args.node_catalog),
        _load(args.evaluation_catalog),
        sample_limit=args.sample_limit,
    )
    _write(
        args.output,
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    markdown_path = args.markdown_output or args.output.with_suffix(".md")
    _write(markdown_path, render_markdown(result))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
