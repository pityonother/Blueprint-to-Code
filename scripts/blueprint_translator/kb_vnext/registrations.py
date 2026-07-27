"""Typed ARK system-registration extraction with fail-closed confidence."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Mapping, Sequence


REGISTRATION_EXTRACTOR_VERSION = "ark-kb-registrations/v1"


@dataclass(frozen=True)
class RegistrationRule:
    registration_type: str
    exact_properties: tuple[str, ...]
    property_tokens: tuple[str, ...]
    target_categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class RegistrationClassification:
    registration_type: str
    status: str
    confidence: str
    match_method: str


REGISTRATION_RULES = (
    RegistrationRule(
        "primal_game_data_registration",
        ("PrimalGameDataOverride", "PrimalGameDataClass"),
        ("primalgamedata",),
    ),
    RegistrationRule(
        "game_mode_registration",
        ("DefaultGameMode", "GameModeClass"),
        ("gamemode",),
    ),
    RegistrationRule(
        "game_state_registration",
        ("GameStateClass", "DefaultGameState"),
        ("gamestate",),
    ),
    RegistrationRule(
        "player_controller_registration",
        ("PlayerControllerClass", "DefaultPlayerController"),
        ("playercontroller",),
    ),
    RegistrationRule(
        "world_settings_registration",
        ("WorldSettingsClass", "DefaultWorldSettings"),
        ("worldsettings",),
    ),
    RegistrationRule(
        "map_world_registration",
        ("MapClass", "WorldClass", "PersistentLevelClass"),
        ("mapclass", "worldclass"),
        ("MAP", "WORLD"),
    ),
    RegistrationRule(
        "spawn_registration",
        (
            "NPCZoneManager",
            "NPCSpawnEntriesContainer",
            "SpawnerClass",
        ),
        ("npczone", "spawnentries", "spawner"),
        ("SPAWNER", "NPC_ZONE"),
    ),
    RegistrationRule(
        "mission_world_event_registration",
        ("MissionType", "TaskClass", "WorldEventClass"),
        ("mission", "taskclass", "worldevent"),
        ("MISSION", "WORLD_EVENT"),
    ),
    RegistrationRule(
        "engram_registration",
        (
            "AdditionalEngramBlueprintClasses",
            "RemapEngrams",
            "EngramClass",
        ),
        ("engram",),
        ("ENGRAM",),
    ),
    RegistrationRule(
        "creature_registration",
        ("AdditionalDinoEntries", "NPCClass", "DinoClass"),
        ("dinoentries", "npcclass", "dinoclass"),
        ("CREATURE",),
    ),
    RegistrationRule(
        "item_registration",
        (
            "AdditionalItemBlueprintClasses",
            "RemapItems",
            "ItemClass",
            "GhostItemSkinPerEquipment",
            "GhostItemSkinStructure",
        ),
        ("itemclass", "remapitems"),
        ("ITEM",),
    ),
    RegistrationRule(
        "buff_registration",
        (
            "AdditionalBuffClasses",
            "BuffClass",
            "BuffClassString",
            "CarriedNotifyBuff",
            "ExplorerNoteXPBuff",
            "SpecialExplorerNoteXPBuff",
            "HostilePlayerBuff",
            "ThreateningPlayerBuff",
        ),
        ("buffclass", "buffs"),
        ("BUFF",),
    ),
    RegistrationRule(
        "structure_registration",
        (
            "AdditionalStructuresToPlace",
            "AdditionalStructureEngrams",
            "StructureClass",
        ),
        ("structure",),
        ("STRUCTURE",),
    ),
    RegistrationRule(
        "loot_reward_registration",
        (
            "SupplyCrateClass",
            "ItemSets",
            "RewardClass",
            "LootItemSetClass",
        ),
        ("supplycrate", "itemsets", "rewardclass", "loot"),
        ("SUPPLY_CRATE", "LOOT_ITEM_SET"),
    ),
    RegistrationRule(
        "harvest_component_registration",
        ("HarvestResourceComponent", "HarvestComponentClass"),
        ("harvestcomponent", "harvestresource"),
        ("HARVEST_COMPONENT",),
    ),
    RegistrationRule(
        "damage_type_registration",
        ("DamageType", "DamageTypeClass", "CheatDestroyFoliageDamageType"),
        ("damagetype",),
        ("DAMAGE_TYPE",),
    ),
    RegistrationRule(
        "status_component_registration",
        ("StatusComponentClass", "MyCharacterStatusComponent"),
        ("statuscomponent",),
        ("STATUS_COMPONENT",),
    ),
    RegistrationRule(
        "inventory_component_registration",
        ("InventoryComponentClass", "MyInventoryComponent"),
        ("inventorycomponent",),
        ("INVENTORY",),
    ),
    RegistrationRule(
        "biome_pcg_registration",
        ("BiomeClass", "PCGGraph", "PCGComponentClass"),
        ("biome", "pcg"),
        ("BIOME", "PCG"),
    ),
    RegistrationRule(
        "world_partition_registration",
        ("WorldPartitionRuntimeCellClass", "WorldPartitionDataLayer"),
        ("worldpartition",),
        ("WORLD_PARTITION",),
    ),
    RegistrationRule(
        "save_transfer_registration",
        ("SaveGameClass", "TransferList", "UploadClass"),
        ("savegame", "transfer", "upload"),
    ),
    RegistrationRule(
        "boss_arena_encounter_registration",
        ("BossClass", "ArenaClass", "EncounterClass"),
        ("bossclass", "arenaclass", "encounter"),
        ("BOSS", "ARENA", "ENCOUNTER"),
    ),
)

REGISTRATION_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS typed_registrations (
    registration_id TEXT PRIMARY KEY,
    owner_uri TEXT NOT NULL,
    target_uri TEXT NOT NULL,
    registration_type TEXT NOT NULL,
    source_property TEXT NOT NULL,
    evidence_uri TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    confidence TEXT NOT NULL,
    status TEXT NOT NULL,
    source_revision_id INTEGER,
    extractor_version TEXT NOT NULL,
    match_method TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_typed_registration_owner
    ON typed_registrations(owner_uri, registration_type);
CREATE INDEX IF NOT EXISTS idx_typed_registration_target
    ON typed_registrations(target_uri, registration_type);
CREATE INDEX IF NOT EXISTS idx_typed_registration_status
    ON typed_registrations(status, confidence);
"""


def create_registration_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(REGISTRATION_TABLES_SQL)


def classify_registration_property(
    source_property: str,
    *,
    target_categories: Sequence[str] = (),
) -> tuple[RegistrationClassification, ...]:
    """Classify source-property semantics; names never create confirmation."""

    property_key = source_property.casefold()
    categories = {value.upper() for value in target_categories}
    results: list[RegistrationClassification] = []
    for rule in REGISTRATION_RULES:
        if property_key in {
            value.casefold() for value in rule.exact_properties
        }:
            results.append(
                RegistrationClassification(
                    registration_type=rule.registration_type,
                    status="CONFIRMED",
                    confidence="HIGH",
                    match_method="exact_source_property",
                )
            )
            continue
        token_match = any(
            token.casefold() in property_key
            for token in rule.property_tokens
        )
        if not token_match:
            continue
        if categories.intersection(rule.target_categories):
            results.append(
                RegistrationClassification(
                    registration_type=rule.registration_type,
                    status="CONFIRMED",
                    confidence="HIGH",
                    match_method="property_semantic_and_class_ancestry",
                )
            )
        else:
            results.append(
                RegistrationClassification(
                    registration_type=rule.registration_type,
                    status="CANDIDATE",
                    confidence="LOW",
                    match_method="property_token_candidate",
                )
            )
    return tuple(results)


def _registration_id(
    owner_uri: str,
    target_uri: str,
    registration_type: str,
    source_property: str,
    evidence_uri: str,
) -> str:
    raw = "\0".join(
        (
            owner_uri,
            target_uri,
            registration_type,
            source_property,
            evidence_uri,
        )
    )
    return "registration-vnext://" + hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def _legacy_status(confidence: str, evidence_uri: str) -> str:
    if (
        confidence.upper() in {"HIGH", "CONFIRMED"}
        and evidence_uri
        and not evidence_uri.startswith("existing-kb://")
    ):
        return "CONFIRMED"
    return "LEGACY_UNVERIFIED"


def _candidate_where_clause() -> tuple[str, tuple[object, ...]]:
    exact = sorted(
        {
            value.casefold()
            for rule in REGISTRATION_RULES
            for value in rule.exact_properties
        }
    )
    tokens = sorted(
        {
            value.casefold()
            for rule in REGISTRATION_RULES
            for value in rule.property_tokens
        }
    )
    conditions = [
        f"lower(source_property) IN ({','.join('?' for _ in exact)})"
    ]
    parameters: list[object] = list(exact)
    for token in tokens:
        conditions.append("lower(source_property) LIKE ?")
        parameters.append(f"%{token}%")
    return "(" + " OR ".join(conditions) + ")", tuple(parameters)


def materialize_typed_registrations(
    discovery: sqlite3.Connection,
    target: sqlite3.Connection,
    *,
    source_revision_id: int | None = None,
    target_categories_by_uri: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, int]:
    """Import legacy rows and extract typed candidates from reference evidence."""

    create_registration_tables(target)
    target.execute("DELETE FROM typed_registrations")
    discovery.row_factory = sqlite3.Row
    category_map = target_categories_by_uri or {}
    rows: dict[str, tuple[object, ...]] = {}

    for source in discovery.execute(
        """
        SELECT
            owner_object_path, target_object_path, registration_type,
            source_property, source_evidence_id, confidence, source_kind
        FROM system_registrations
        ORDER BY registration_id
        """
    ):
        owner_uri = str(source["owner_object_path"])
        target_uri = str(source["target_object_path"])
        registration_type = str(source["registration_type"])
        source_property = str(source["source_property"])
        evidence_uri = str(source["source_evidence_id"])
        confidence = str(source["confidence"] or "UNKNOWN").upper()
        registration_id = _registration_id(
            owner_uri,
            target_uri,
            registration_type,
            source_property,
            evidence_uri,
        )
        rows[registration_id] = (
            registration_id,
            owner_uri,
            target_uri,
            registration_type,
            source_property,
            evidence_uri,
            "DECLARED",
            confidence,
            _legacy_status(confidence, evidence_uri),
            source_revision_id,
            REGISTRATION_EXTRACTOR_VERSION,
            "legacy_typed_registration",
        )

    where_sql, parameters = _candidate_where_clause()
    reference_cursor = discovery.execute(
        f"""
        SELECT
            source_object_path, target_object_path, source_property,
            source_evidence_id, confidence, source_kind, edge_kind
        FROM asset_references
        WHERE source_property <> ''
          AND source_property <> 'AssetRegistryDependency'
          AND target_object_path <> ''
          AND {where_sql}
        ORDER BY source_object_path, target_object_path, source_property
        """,
        parameters,
    )
    confirmed = 0
    candidates = 0
    for source in reference_cursor:
        owner_uri = str(source["source_object_path"])
        target_uri = str(source["target_object_path"])
        source_property = str(source["source_property"])
        source_kind = str(source["source_kind"] or "")
        evidence_uri = str(source["source_evidence_id"] or "")
        if not evidence_uri:
            evidence_uri = (
                "discovery-reference://"
                + hashlib.sha256(
                    "\0".join(
                        (owner_uri, target_uri, source_property)
                    ).encode("utf-8")
                ).hexdigest()
            )
        classifications = classify_registration_property(
            source_property,
            target_categories=category_map.get(target_uri, ()),
        )
        for classification in classifications:
            if source_kind == "existing_knowledge_database":
                output_status = "LEGACY_UNVERIFIED"
                output_confidence = str(
                    source["confidence"] or "UNKNOWN"
                ).upper()
                match_method = "legacy_reference_candidate"
            else:
                output_status = classification.status
                output_confidence = classification.confidence
                match_method = classification.match_method
            registration_id = _registration_id(
                owner_uri,
                target_uri,
                classification.registration_type,
                source_property,
                evidence_uri,
            )
            rows[registration_id] = (
                registration_id,
                owner_uri,
                target_uri,
                classification.registration_type,
                source_property,
                evidence_uri,
                "DECLARED",
                output_confidence,
                output_status,
                source_revision_id,
                REGISTRATION_EXTRACTOR_VERSION,
                match_method,
            )
            confirmed += int(output_status == "CONFIRMED")
            candidates += int(output_status == "CANDIDATE")

    target.executemany(
        """
        INSERT INTO typed_registrations VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        rows.values(),
    )
    target.commit()
    status_counts = {
        str(status): int(count)
        for status, count in target.execute(
            """
            SELECT status, COUNT(*)
            FROM typed_registrations
            GROUP BY status
            """
        )
    }
    return {
        "registrations": len(rows),
        "extractedConfirmed": confirmed,
        "extractedCandidates": candidates,
        **{
            f"status_{status.casefold()}": count
            for status, count in status_counts.items()
        },
    }
