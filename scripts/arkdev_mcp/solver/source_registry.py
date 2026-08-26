"""ARK-first v1 acquisition sources and their verified capability boundaries."""

from __future__ import annotations

from .contracts import SOURCE_REGISTRY_VERSION


def _source(
    kind: str,
    provided_evidence_kinds: tuple[str, ...],
    *,
    automatable: bool,
    requires_user_action: bool,
    verified_ark_builds: tuple[str, ...] = (),
    limitations: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "kind": kind,
        "providedEvidenceKinds": list(provided_evidence_kinds),
        "automatable": automatable,
        "requiresUserAction": requires_user_action,
        "readOnly": True,
        "mutation": False,
        "verifiedArkBuilds": list(verified_ark_builds),
        "limitations": list(limitations),
    }


SOURCE_REGISTRY: dict[str, object] = {
    "version": SOURCE_REGISTRY_VERSION,
    "sources": [
        _source(
            "CURRENT_V4_EVIDENCE",
            (
                "ASSET_IDENTITY",
                "GRAPH_STRUCTURE",
                "NODE_IDENTITY",
                "PIN_SIGNATURES",
                "DEFAULTS",
                "REFERENCE_CLOSURE",
            ),
            automatable=True,
            requires_user_action=False,
            limitations=(
                "Each requirement is READY only after current authority and freshness checks pass.",
                "Pin identity is not implied by a v4 Evidence record unless separately proven.",
            ),
        ),
        _source(
            "BINARY_V4_REBUILD",
            (
                "ASSET_IDENTITY",
                "GRAPH_STRUCTURE",
                "NODE_IDENTITY",
                "PIN_SIGNATURES",
                "DEFAULTS",
                "REFERENCE_CLOSURE",
            ),
            automatable=True,
            requires_user_action=False,
            limitations=(
                "Automation is available only when the source asset is locally available.",
                "Recovered Pin links may remain heuristic and native Pin identity may be unavailable.",
            ),
        ),
        _source(
            "WC_REFLECTION",
            ("NODE_IDENTITY",),
            automatable=False,
            requires_user_action=True,
            verified_ark_builds=("5.5.4-0+UE5",),
            limitations=(
                "Verified only for bounded read-only existing-node identity on one explicit asset and Graph.",
                "Pin identity is unavailable and Link identity is not tested.",
                "It does not prove mutation, compile, save, rollback, or runtime correctness.",
            ),
        ),
        _source(
            "CLIPBOARD_GRAPH_CAPTURE",
            ("GRAPH_STRUCTURE", "NODE_IDENTITY", "PIN_SIGNATURES", "DEFAULTS"),
            automatable=False,
            requires_user_action=True,
            limitations=(
                "Requires one explicit user capture action per Graph.",
                "Only fields present in the exported text are evidence.",
            ),
        ),
        _source(
            "DEFAULTS_EXPORT",
            ("DEFAULTS",),
            automatable=False,
            requires_user_action=True,
            limitations=("Does not recover Graph structure or execution flow.",),
        ),
        _source(
            "LOCALIZATION_IMPORT",
            ("LOCALIZATION",),
            automatable=False,
            requires_user_action=True,
            limitations=("Requires an explicit bounded localization descriptor.",),
        ),
        _source(
            "DATASET_IMPORT",
            ("ENTITY_DATASET", "DATASET_SCHEMA"),
            automatable=False,
            requires_user_action=True,
            limitations=("Requires an explicit bounded dataset descriptor.",),
        ),
        _source(
            "USER_SUPPLIED_ASSET",
            ("ASSET_IDENTITY",),
            automatable=False,
            requires_user_action=True,
            limitations=(
                "The supplied descriptor is not authoritative Graph evidence by itself.",
            ),
        ),
        _source(
            "USER_SUPPLIED_MOD_ASSET",
            ("THIRD_PARTY_MOD_ASSET",),
            automatable=False,
            requires_user_action=True,
            limitations=(
                "The Mod asset remains unverified until a separate evidence workflow runs.",
            ),
        ),
        _source(
            "NATIVE_SOURCE_REFERENCE",
            ("NATIVE_SOURCE",),
            automatable=False,
            requires_user_action=True,
            limitations=(
                "Requires an authoritative native source reference supplied out of band.",
            ),
        ),
    ],
}
SOURCE_DEFINITIONS = {str(item["kind"]): item for item in SOURCE_REGISTRY["sources"]}


def preferred_sources_for(evidence_kind: str) -> list[str]:
    return [
        str(source["kind"])
        for source in SOURCE_REGISTRY["sources"]
        if evidence_kind in source["providedEvidenceKinds"]
    ]


__all__ = ["SOURCE_DEFINITIONS", "SOURCE_REGISTRY", "preferred_sources_for"]
