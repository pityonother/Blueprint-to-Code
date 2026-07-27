"""Versioned ARK ontology loading and evidence-prioritized domain inference."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


REQUIRED_DOMAINS = {
    "global_registration",
    "class_inheritance",
    "creature_definition",
    "taming",
    "breeding_growth_imprinting_genetics",
    "status_component",
    "damage_resistance",
    "buff",
    "inventory",
    "item_use",
    "crafting_engram",
    "loot_quality_reward",
    "harvest",
    "ai_combat_riding",
    "weapon_projectile",
    "structure",
    "mission_world_event",
    "map_world",
    "pcg_world_partition",
    "spawn_ecology",
    "ui_runtime",
    "network_replication",
    "save_persistence_transfer",
    "native_runtime",
    "runtime_validation",
    "evidence_boundary",
}


@dataclass(frozen=True)
class DomainDefinition:
    domain_id: str
    label: str
    class_categories: tuple[str, ...]
    registration_types: tuple[str, ...]
    component_categories: tuple[str, ...]
    function_tokens: tuple[str, ...]
    property_tokens: tuple[str, ...]
    path_tokens: tuple[str, ...]


@dataclass(frozen=True)
class OntologyBundle:
    version: str
    domains: dict[str, DomainDefinition]
    roles: tuple[str, ...]
    depth_policies: tuple[str, ...]
    edge_types: tuple[str, ...]
    fact_types: tuple[str, ...]
    scope_kinds: tuple[str, ...]


@dataclass(frozen=True)
class DomainMembership:
    domain_id: str
    membership_kind: str
    confidence: str
    status: str
    evidence_id: str


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _string_list(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{field} must be a non-empty-string array")
    return tuple(value)


def _unique(values: Sequence[str], *, field: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} contains duplicate IDs")
    return tuple(values)


def load_ontology(root: Path) -> OntologyBundle:
    root = root.resolve()
    domains_data = _load_json(root / "ark_domains.v1.json")
    roles_data = _load_json(root / "ark_roles.v1.json")
    edges_data = _load_json(root / "ark_edge_types.v1.json")
    facts_data = _load_json(root / "ark_fact_types.v1.json")

    raw_domains = domains_data.get("domains")
    if not isinstance(raw_domains, list):
        raise ValueError("domains must be an array")
    domains: dict[str, DomainDefinition] = {}
    for raw in raw_domains:
        if not isinstance(raw, dict):
            raise ValueError("each domain must be an object")
        domain_id = str(raw.get("id") or "")
        if not domain_id or domain_id in domains:
            raise ValueError(f"invalid or duplicate domain id: {domain_id}")
        domains[domain_id] = DomainDefinition(
            domain_id=domain_id,
            label=str(raw.get("label") or domain_id),
            class_categories=_string_list(
                raw.get("classCategories"), field=f"{domain_id}.classCategories"
            ),
            registration_types=_string_list(
                raw.get("registrationTypes"),
                field=f"{domain_id}.registrationTypes",
            ),
            component_categories=_string_list(
                raw.get("componentCategories"),
                field=f"{domain_id}.componentCategories",
            ),
            function_tokens=_string_list(
                raw.get("functionTokens"), field=f"{domain_id}.functionTokens"
            ),
            property_tokens=_string_list(
                raw.get("propertyTokens"), field=f"{domain_id}.propertyTokens"
            ),
            path_tokens=_string_list(
                raw.get("pathTokens"), field=f"{domain_id}.pathTokens"
            ),
        )
    missing = REQUIRED_DOMAINS - set(domains)
    if missing:
        raise ValueError(f"domain ontology is missing: {sorted(missing)}")

    roles = _unique(
        _string_list(roles_data.get("roles"), field="roles"),
        field="roles",
    )
    depth = _unique(
        _string_list(roles_data.get("depthPolicies"), field="depthPolicies"),
        field="depthPolicies",
    )
    raw_edges = edges_data.get("edgeTypes")
    if not isinstance(raw_edges, list):
        raise ValueError("edgeTypes must be an array")
    edge_types = _unique(
        tuple(
            str(item.get("id") or "")
            for item in raw_edges
            if isinstance(item, dict)
        ),
        field="edgeTypes",
    )
    raw_facts = facts_data.get("factTypes")
    if not isinstance(raw_facts, list):
        raise ValueError("factTypes must be an array")
    fact_types = _unique(
        tuple(
            str(item.get("id") or "")
            for item in raw_facts
            if isinstance(item, dict)
        ),
        field="factTypes",
    )
    scope_kinds = _unique(
        _string_list(facts_data.get("scopeKinds"), field="scopeKinds"),
        field="scopeKinds",
    )
    versions = (
        str(domains_data.get("version") or ""),
        str(roles_data.get("version") or ""),
        str(edges_data.get("version") or ""),
        str(facts_data.get("version") or ""),
    )
    return OntologyBundle(
        version="|".join(versions),
        domains=domains,
        roles=roles,
        depth_policies=depth,
        edge_types=edge_types,
        fact_types=fact_types,
        scope_kinds=scope_kinds,
    )

def _strings(context: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = context.get(key)
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(str(item) for item in value if str(item))


def _evidence_id(
    entity_uri: str,
    domain_id: str,
    membership_kind: str,
    matched_value: str,
) -> str:
    raw = "\0".join(
        (entity_uri, domain_id, membership_kind, matched_value)
    )
    return "ontology-evidence://" + hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def _token_matches(values: Sequence[str], tokens: Sequence[str]) -> str | None:
    lowered = tuple(value.casefold() for value in values)
    for token in tokens:
        needle = token.casefold()
        if any(needle in value for value in lowered):
            return token
    return None


def infer_domain_memberships(
    ontology: OntologyBundle,
    context: Mapping[str, object],
) -> tuple[DomainMembership, ...]:
    """Infer multi-domain memberships while retaining evidence priority."""

    entity_uri = str(context.get("entity_uri") or "UNKNOWN")
    class_categories = set(_strings(context, "class_categories"))
    registration_types = set(_strings(context, "registration_types"))
    component_categories = set(_strings(context, "component_categories"))
    function_names = _strings(context, "function_names")
    property_names = _strings(context, "property_names")
    confirmed_reference_domains = set(
        _strings(context, "confirmed_reference_domains")
    )
    path_values = (
        str(context.get("object_path") or ""),
        str(context.get("asset_name") or ""),
    )
    recovered_semantics = bool(context.get("semantic_evidence_recovered"))
    memberships: dict[
        tuple[str, str, str], DomainMembership
    ] = {}

    def add(
        domain_id: str,
        kind: str,
        matched_value: str,
        *,
        confidence: str,
        status: str,
    ) -> None:
        evidence = _evidence_id(
            entity_uri, domain_id, kind, matched_value
        )
        membership = DomainMembership(
            domain_id=domain_id,
            membership_kind=kind,
            confidence=confidence,
            status=status,
            evidence_id=evidence,
        )
        memberships[(domain_id, kind, evidence)] = membership

    for domain_id, domain in ontology.domains.items():
        if match := class_categories.intersection(domain.class_categories):
            add(
                domain_id,
                "CLASS_ANCESTRY",
                sorted(match)[0],
                confidence="HIGH",
                status="CONFIRMED",
            )
        if match := registration_types.intersection(
            domain.registration_types
        ):
            add(
                domain_id,
                "TYPED_REGISTRATION",
                sorted(match)[0],
                confidence="HIGH",
                status="CONFIRMED",
            )
        if match := component_categories.intersection(
            domain.component_categories
        ):
            add(
                domain_id,
                "COMPONENT_TYPE",
                sorted(match)[0],
                confidence="HIGH",
                status="CONFIRMED",
            )
        if recovered_semantics:
            function_match = _token_matches(
                function_names, domain.function_tokens
            )
            if function_match:
                add(
                    domain_id,
                    "FUNCTION_SEMANTIC",
                    function_match,
                    confidence="MEDIUM",
                    status="CANDIDATE",
                )
            property_match = _token_matches(
                property_names, domain.property_tokens
            )
            if property_match:
                add(
                    domain_id,
                    "PROPERTY_SEMANTIC",
                    property_match,
                    confidence="MEDIUM",
                    status="CANDIDATE",
                )
        if domain_id in confirmed_reference_domains:
            add(
                domain_id,
                "CONFIRMED_REFERENCE_NEIGHBORHOOD",
                domain_id,
                confidence="HIGH",
                status="CONFIRMED",
            )
        path_match = _token_matches(path_values, domain.path_tokens)
        if path_match:
            add(
                domain_id,
                "NAME_OR_FOLDER_CANDIDATE",
                path_match,
                confidence="LOW",
                status="CANDIDATE",
            )
    return tuple(
        sorted(
            memberships.values(),
            key=lambda item: (
                item.domain_id,
                item.membership_kind,
                item.evidence_id,
            ),
        )
    )
