"""Versioned ARK ontology loading and evidence-prioritized domain inference."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .registrations import effective_registration_provenance


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
    fact_value_kinds: dict[str, tuple[str, ...]]
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
    edges_data = _load_json(root / "ark_edge_types.v2.json")
    facts_data = _load_json(root / "ark_fact_types.v2.json")

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
    fact_value_kinds: dict[str, tuple[str, ...]] = {}
    for item in raw_facts:
        if not isinstance(item, dict):
            raise ValueError("each fact type must be an object")
        fact_type_id = str(item.get("id") or "")
        if not fact_type_id or fact_type_id in fact_value_kinds:
            raise ValueError(
                f"invalid or duplicate fact type id: {fact_type_id}"
            )
        fact_value_kinds[fact_type_id] = _unique(
            _string_list(
                item.get("valueKinds"),
                field=f"{fact_type_id}.valueKinds",
            ),
            field=f"{fact_type_id}.valueKinds",
        )
    fact_types = tuple(fact_value_kinds)
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
        fact_value_kinds=fact_value_kinds,
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
    registration_status = str(
        context.get("registration_status") or "UNKNOWN"
    ).upper()
    registration_confidence = str(
        context.get("registration_confidence") or "UNKNOWN"
    ).upper()
    registration_evidence_uri = str(
        context.get("registration_evidence_uri") or ""
    )
    registration_status, registration_confidence = (
        effective_registration_provenance(
            registration_status,
            registration_confidence,
            registration_evidence_uri,
        )
    )
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
        evidence_value: str | None = None,
    ) -> None:
        evidence = _evidence_id(
            entity_uri,
            domain_id,
            kind,
            evidence_value or matched_value,
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
            registration_type = sorted(match)[0]
            add(
                domain_id,
                "TYPED_REGISTRATION",
                registration_type,
                confidence=registration_confidence,
                status=registration_status,
                evidence_value=(
                    "\0".join(
                        (
                            registration_type,
                            registration_evidence_uri,
                        )
                    )
                    if registration_evidence_uri
                    else registration_type
                ),
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


def materialize_domain_entity_memberships(
    connection: sqlite3.Connection,
    *,
    ontology: OntologyBundle,
    entity_id: int,
) -> dict[str, int]:
    """Replace only ontology-owned memberships for one entity.

    The incremental domain owner is deliberately narrow: class ancestry and
    typed-registration inference.  Rows from manual or future producers are
    outside this function's ownership and therefore remain byte-for-byte
    untouched.
    """

    if isinstance(entity_id, bool) or not isinstance(entity_id, int):
        raise ValueError("domain entity_id must be a positive integer")
    if entity_id < 1:
        raise ValueError("domain entity_id must be a positive integer")
    entity = connection.execute(
        "SELECT canonical_uri FROM entities WHERE entity_id=?",
        (entity_id,),
    ).fetchone()
    if entity is None:
        raise ValueError("domain entity_id does not exist")
    revision_rows = connection.execute(
        """
        SELECT revision_id
        FROM source_revisions
        WHERE source_kind='ontology'
          AND source_uri=?
          AND freshness_status='FRESH'
        ORDER BY revision_id
        """,
        (f"ontology://{ontology.version}",),
    ).fetchall()
    if len(revision_rows) != 1:
        raise ValueError(
            "domain rebuild requires exactly one fresh ontology revision"
        )
    source_revision_id = int(revision_rows[0][0])
    entity_uri = str(entity[0])
    owned_rows: dict[tuple[object, ...], tuple[object, ...]] = {}

    category_to_domains: dict[str, tuple[str, ...]] = {}
    for domain_id, definition in ontology.domains.items():
        for category in definition.class_categories:
            category_to_domains[category] = tuple(
                sorted(
                    {
                        *category_to_domains.get(category, ()),
                        domain_id,
                    }
                )
            )
    for category, ancestor_class_id in connection.execute(
        """
        SELECT DISTINCT c.category, c.ancestor_class_id
        FROM asset_class_assignments AS a
        JOIN class_ancestry_categories AS c ON c.class_id=a.class_id
        WHERE a.entity_id=?
          AND UPPER(a.status) IN (
              'SELF', 'EXTRACTED', 'IDENTIFIED',
              'CONFIRMED', 'VERIFIED', 'RESOLVED'
          )
          AND UPPER(a.confidence) IN ('HIGH', 'CONFIRMED')
          AND UPPER(c.status) IN (
              'SELF', 'EXTRACTED', 'IDENTIFIED',
              'CONFIRMED', 'VERIFIED', 'RESOLVED'
          )
          AND UPPER(c.confidence) IN ('HIGH', 'CONFIRMED')
          AND a.source_revision_id IN (
              SELECT revision_id FROM source_revisions
              WHERE freshness_status='FRESH'
          )
        ORDER BY c.category, c.ancestor_class_id
        """,
        (entity_id,),
    ):
        for domain_id in category_to_domains.get(str(category), ()):
            row = (
                entity_id,
                domain_id,
                "CLASS_ANCESTRY",
                "HIGH",
                "CONFIRMED",
                f"class-category://{int(ancestor_class_id)}/{category}",
                ontology.version,
                source_revision_id,
            )
            owned_rows[row] = row

    registrations = connection.execute(
        """
        SELECT
            r.owner_uri, r.target_uri, r.registration_type,
            r.evidence_uri, r.confidence, r.status
        FROM typed_registrations AS r
        WHERE r.source_revision_id IN (
            SELECT revision_id FROM source_revisions
            WHERE freshness_status='FRESH'
        )
        ORDER BY r.registration_id
        """
    ).fetchall()
    class_paths = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT c.class_path
            FROM classes AS c
            JOIN asset_class_assignments AS a ON a.class_id=c.class_id
            WHERE a.entity_id=? AND a.assignment_kind='GENERATED_CLASS'
            """,
            (entity_id,),
        )
    }
    matching_uris = {entity_uri, *class_paths}
    for (
        owner_uri,
        target_uri,
        registration_type,
        evidence_uri,
        confidence,
        status,
    ) in registrations:
        contexts: list[dict[str, object]] = []
        if str(owner_uri) in matching_uris:
            contexts.append(
                {
                    "entity_uri": str(owner_uri),
                    "registration_types": ["global_asset_reference"],
                    "registration_status": str(status),
                    "registration_confidence": str(confidence),
                    "registration_evidence_uri": str(evidence_uri),
                }
            )
        if str(target_uri) in matching_uris:
            contexts.append(
                {
                    "entity_uri": str(target_uri),
                    "registration_types": [str(registration_type)],
                    "registration_status": str(status),
                    "registration_confidence": str(confidence),
                    "registration_evidence_uri": str(evidence_uri),
                }
            )
        for context in contexts:
            for membership in infer_domain_memberships(ontology, context):
                if membership.membership_kind != "TYPED_REGISTRATION":
                    continue
                row = (
                    entity_id,
                    membership.domain_id,
                    membership.membership_kind,
                    membership.confidence,
                    membership.status,
                    membership.evidence_id,
                    ontology.version,
                    source_revision_id,
                )
                owned_rows[row] = row

    connection.execute(
        """
        DELETE FROM domain_memberships
        WHERE entity_id=?
          AND membership_kind IN ('CLASS_ANCESTRY', 'TYPED_REGISTRATION')
        """,
        (entity_id,),
    )
    if owned_rows:
        connection.executemany(
            "INSERT INTO domain_memberships VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            sorted(owned_rows.values()),
        )
    return {
        "entityId": entity_id,
        "ownedMemberships": len(owned_rows),
        "sourceRevisionId": source_revision_id,
    }
