from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.adapters import (  # noqa: E402
    ADAPTER_SPECS,
    AdapterSchemaError,
    materialize_semantic_adapters,
)
from blueprint_translator.kb_vnext.fact_store import (  # noqa: E402
    FactValue,
    store_fact,
)
from blueprint_translator.kb_vnext.legacy import (  # noqa: E402
    import_legacy_lineage,
)
from blueprint_translator.kb_vnext.ontology import (  # noqa: E402
    load_ontology,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    FULL_CORE_SCHEMA_SQL,
)


GENERATED_AT = "2026-07-27T00:00:00+00:00"
BUSINESS_SCHEMA = "ark-devkit-knowledge.business-db.v1"
ASSET_INDEX_SCHEMA = "ark-devkit-knowledge.global-asset-index.v1"


def _core() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(FULL_CORE_SCHEMA_SQL)
    return connection


def _source_revision(
    connection: sqlite3.Connection,
    *,
    revision_id: int = 1,
    freshness: str = "FRESH",
    source_uri: str = "bp://fixture-asset@fixture-revision",
) -> None:
    connection.execute(
        """
        INSERT INTO source_revisions VALUES (
            ?, 'blueprint_evidence', ?, 'sha',
            'fixture', 'ark.blueprint.evidence.v2',
            ?, ?
        )
        """,
        (revision_id, source_uri, GENERATED_AT, freshness),
    )


def _entity_with_native_root(
    connection: sqlite3.Connection,
    *,
    entity_id: int,
    object_path: str,
    native_root: str,
) -> None:
    root_id = entity_id * 10
    generated_id = root_id + 1
    generated_path = f"{object_path}_C"
    connection.execute(
        """
        INSERT INTO entities(
            entity_id, canonical_uri, entity_kind, status, confidence
        ) VALUES (?, ?, 'BLUEPRINT_ASSET', 'CONFIRMED', 'HIGH')
        """,
        (entity_id, object_path),
    )
    connection.executemany(
        """
        INSERT INTO classes(
            class_id, class_path, class_name, module_or_package,
            class_kind, is_native, source_revision_id, status, confidence
        ) VALUES (?, ?, ?, ?, ?, ?, 1, 'IDENTIFIED', 'HIGH')
        """,
        (
            (
                root_id,
                native_root,
                native_root.rsplit(".", 1)[-1],
                "ShooterGame",
                "NATIVE_UCLASS",
                1,
            ),
            (
                generated_id,
                generated_path,
                generated_path.rsplit(".", 1)[-1],
                object_path.rsplit(".", 1)[0],
                "BLUEPRINT_GENERATED_CLASS",
                0,
            ),
        ),
    )
    connection.executemany(
        """
        INSERT INTO class_closure(
            ancestor_class_id, descendant_class_id, depth, path_status
        ) VALUES (?, ?, ?, ?)
        """,
        (
            (root_id, root_id, 0, "SELF"),
            (generated_id, generated_id, 0, "SELF"),
            (root_id, generated_id, 1, "CONFIRMED"),
        ),
    )
    connection.execute(
        """
        INSERT INTO asset_class_assignments(
            entity_id, class_id, assignment_kind, evidence_uri,
            status, confidence
        ) VALUES (?, ?, 'GENERATED_CLASS', ?, 'IDENTIFIED', 'HIGH')
        """,
        (entity_id, generated_id, f"class-edge://fixture/{entity_id}"),
    )
    connection.execute(
        """
        INSERT INTO class_edges(
            child_class_id, parent_class_id, edge_kind, evidence_id,
            source_revision_id, status, confidence
        ) VALUES (?, ?, 'native_parent', ?, 1, 'CONFIRMED', 'HIGH')
        """,
        (
            generated_id,
            root_id,
            f"class-edge://fixture/native-parent/{entity_id}",
        ),
    )
    connection.execute(
        "UPDATE entities SET class_id=? WHERE entity_id=?",
        (generated_id, entity_id),
    )


def _source_fact(
    connection: sqlite3.Connection,
    *,
    ontology: object,
    entity_id: int,
    name: str,
    value: FactValue,
    status: str = "CONFIRMED",
    confidence: str = "HIGH",
    revision_id: int = 1,
    evidence_uri: str | None = None,
    evidence_role: str = "DEFAULT_VALUE_ACTUAL",
) -> int:
    evidence_uri = evidence_uri or (
        "bp://fixture-asset@fixture-revision/default/"
        + quote(name, safe="")
    )
    return store_fact(
        connection,
        ontology=ontology,
        subject_entity_id=entity_id,
        fact_type="DECLARED_DEFAULT",
        fact_name=name,
        scope_kind="DECLARED",
        declared_on_entity_id=entity_id,
        value=value,
        status=status,
        confidence=confidence,
        source_revision_id=revision_id,
        evidence_uri=evidence_uri,
        evidence_role=evidence_role,
    )


def _business_database(
    root: Path,
    *,
    schema: str = BUSINESS_SCHEMA,
    object_path: str,
    value: float = 150.0,
    id_declaration: str = "id INTEGER PRIMARY KEY",
) -> Path:
    path = root / "buffs.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE buff_effects(
            {id_declaration},
            object_path TEXT NOT NULL,
            effect_key TEXT NOT NULL,
            effect_value TEXT NOT NULL,
            duration TEXT NOT NULL,
            interval TEXT NOT NULL,
            confidence TEXT NOT NULL,
            source_json TEXT NOT NULL
        );
        CREATE TABLE buff_stacks(
            {id_declaration},
            object_path TEXT NOT NULL,
            stack_key TEXT NOT NULL,
            stack_value TEXT NOT NULL,
            confidence TEXT NOT NULL,
            source_json TEXT NOT NULL
        );
        CREATE TABLE buff_stat_modifiers(
            {id_declaration},
            object_path TEXT NOT NULL,
            stat_name TEXT NOT NULL,
            operation TEXT NOT NULL,
            value TEXT NOT NULL,
            confidence TEXT NOT NULL,
            source_json TEXT NOT NULL
        );
        """.format(id_declaration=id_declaration)
    )
    connection.execute("INSERT INTO metadata VALUES ('schema', ?)", (schema,))
    source = {
        "confidence": "high",
        "key": "DeactivateAfterTime",
        "raw": {
            "confidence": "high",
            "source": "uasset_cdo",
            "type": "FloatProperty",
            "value": value,
        },
        "source": "uasset_class_defaults",
        "type": "FloatProperty",
    }
    connection.execute(
        """
        INSERT INTO buff_effects VALUES (
            1, ?, 'DeactivateAfterTime', ?, ?, '',
            'high', ?
        )
        """,
        (
            object_path,
            str(value),
            str(value),
            json.dumps(source, separators=(",", ":")),
        ),
    )
    connection.commit()
    connection.close()
    return path


def _asset_index_database(root: Path, *, object_path: str) -> Path:
    path = root / "asset_catalog.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE asset_files(
            object_path TEXT PRIMARY KEY,
            asset_type TEXT NOT NULL,
            domain TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO metadata VALUES ('schema', ?)",
        (ASSET_INDEX_SCHEMA,),
    )
    connection.execute(
        "INSERT INTO asset_files VALUES (?, 'primal_item_blueprint', 'item')",
        (object_path,),
    )
    connection.commit()
    connection.close()
    return path


class SemanticAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ontology = load_ontology(PROJECT_ROOT / "ontology")

    def test_all_domain_adapters_declare_versioned_sources_and_rules(self):
        self.assertEqual(
            {spec.adapter_id for spec in ADAPTER_SPECS},
            {
                "primal_game_data",
                "buffs",
                "primal_items",
                "status_components",
                "loot",
                "harvest",
                "missions",
            },
        )
        for spec in ADAPTER_SPECS:
            self.assertTrue(spec.adapter_version)
            self.assertTrue(spec.output_fact_types)
            self.assertTrue(spec.legacy_sources or spec.direct_rules)
            for source in spec.legacy_sources:
                self.assertTrue(source.database_name.endswith(".sqlite"))
                self.assertTrue(source.schema_version)
                self.assertTrue(source.table_name)
                self.assertIn(source.object_path_column, source.required_columns)
                self.assertTrue(source.primary_key_columns)
        pgd = next(
            spec
            for spec in ADAPTER_SPECS
            if spec.adapter_id == "primal_game_data"
        )
        self.assertTrue(
            all(
                source.reject_all_reason
                == "REGISTRATION_KIND_NOT_GOLD_VERIFIED"
                and not source.rules
                for source in pgd.legacy_sources
            )
        )

    def test_legacy_exact_match_promotes_with_fresh_evidence_and_lineage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_path = "/Game/Test/Buff_Test.Buff_Test"
            _business_database(root, object_path=object_path)
            core = _core()
            _source_revision(core)
            _entity_with_native_root(
                core,
                entity_id=1,
                object_path=object_path,
                native_root="/Script/ShooterGame.PrimalBuff",
            )
            source_fact_id = _source_fact(
                core,
                ontology=self.ontology,
                entity_id=1,
                name="DeactivateAfterTime",
                value=FactValue("NUMBER", value_number=150.0),
            )
            import_legacy_lineage(
                core=core,
                legacy_root=root,
                generated_at=GENERATED_AT,
            )

            result = materialize_semantic_adapters(
                core=core,
                legacy_root=root,
                ontology=self.ontology,
                generated_at=GENERATED_AT,
                adapter_ids=("buffs",),
            )

            self.assertEqual(result["promotedFacts"], 1)
            semantic = core.execute(
                """
                SELECT fact_id, fact_type, fact_name, value_kind, value_number
                FROM facts WHERE fact_type='STATUS_EFFECT'
                """
            ).fetchone()
            self.assertEqual(
                semantic[1:],
                ("STATUS_EFFECT", "DeactivateAfterTime", "NUMBER", 150.0),
            )
            decision = core.execute(
                """
                SELECT decision_status, reason_code, source_fact_id,
                       semantic_fact_id, legacy_lineage_id, evidence_uri
                FROM semantic_adapter_decisions
                """
            ).fetchone()
            self.assertEqual(decision[:4], ("PROMOTED", "VERIFIED", source_fact_id, semantic[0]))
            self.assertIsNotNone(decision[4])
            self.assertEqual(
                decision[5],
                (
                    "bp://fixture-asset@fixture-revision/default/"
                    "DeactivateAfterTime"
                ),
            )
            self.assertEqual(
                core.execute("SELECT status FROM legacy_lineage").fetchone()[0],
                "SEMANTICALLY_VERIFIED",
            )
            core.close()

    def test_stale_source_stays_legacy_unverified_and_makes_no_fact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_path = "/Game/Test/Buff_Stale.Buff_Stale"
            _business_database(root, object_path=object_path)
            core = _core()
            _source_revision(core)
            _entity_with_native_root(
                core,
                entity_id=1,
                object_path=object_path,
                native_root="/Script/ShooterGame.PrimalBuff",
            )
            _source_fact(
                core,
                ontology=self.ontology,
                entity_id=1,
                name="DeactivateAfterTime",
                value=FactValue("UNKNOWN"),
                status="STALE",
            )
            import_legacy_lineage(
                core=core,
                legacy_root=root,
                generated_at=GENERATED_AT,
            )

            result = materialize_semantic_adapters(
                core=core,
                legacy_root=root,
                ontology=self.ontology,
                generated_at=GENERATED_AT,
                adapter_ids=("buffs",),
            )

            self.assertEqual(result["promotedFacts"], 0)
            self.assertEqual(
                core.execute(
                    """
                    SELECT reason_code FROM semantic_adapter_decisions
                    """
                ).fetchone()[0],
                "SOURCE_STALE",
            )
            self.assertEqual(
                core.execute("SELECT status FROM legacy_lineage").fetchone()[0],
                "LEGACY_UNVERIFIED",
            )
            self.assertEqual(
                core.execute(
                    "SELECT COUNT(*) FROM facts WHERE fact_type='STATUS_EFFECT'"
                ).fetchone()[0],
                0,
            )
            core.close()

    def test_value_mismatch_and_nonfresh_evidence_are_rejected(self):
        cases = (
            ("VALUE_MISMATCH", 149.0, "FRESH"),
            ("EVIDENCE_NOT_FRESH", 150.0, "STALE"),
        )
        for expected_reason, fact_value, freshness in cases:
            with self.subTest(reason=expected_reason):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    object_path = "/Game/Test/Buff_Guard.Buff_Guard"
                    _business_database(root, object_path=object_path)
                    core = _core()
                    _source_revision(core)
                    value_revision_id = 1
                    evidence_uri = None
                    if freshness == "STALE":
                        value_revision_id = 2
                        _source_revision(
                            core,
                            revision_id=value_revision_id,
                            freshness="STALE",
                            source_uri="bp://fixture-stale@revision",
                        )
                        evidence_uri = (
                            "bp://fixture-stale@revision/default/"
                            "DeactivateAfterTime"
                        )
                    _entity_with_native_root(
                        core,
                        entity_id=1,
                        object_path=object_path,
                        native_root="/Script/ShooterGame.PrimalBuff",
                    )
                    _source_fact(
                        core,
                        ontology=self.ontology,
                        entity_id=1,
                        name="DeactivateAfterTime",
                        value=FactValue(
                            "NUMBER",
                            value_number=fact_value,
                        ),
                        revision_id=value_revision_id,
                        evidence_uri=evidence_uri,
                    )
                    import_legacy_lineage(
                        core=core,
                        legacy_root=root,
                        generated_at=GENERATED_AT,
                    )

                    result = materialize_semantic_adapters(
                        core=core,
                        legacy_root=root,
                        ontology=self.ontology,
                        generated_at=GENERATED_AT,
                        adapter_ids=("buffs",),
                    )

                    self.assertEqual(result["promotedFacts"], 0)
                    self.assertEqual(
                        core.execute(
                            """
                            SELECT reason_code
                            FROM semantic_adapter_decisions
                            """
                        ).fetchone()[0],
                        expected_reason,
                    )
                    core.close()

    def test_schema_mismatch_fails_closed_before_promotion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_path = "/Game/Test/Buff_Test.Buff_Test"
            _business_database(
                root,
                schema="ark-devkit-knowledge.business-db.v0",
                object_path=object_path,
            )
            core = _core()
            _source_revision(core)
            _entity_with_native_root(
                core,
                entity_id=1,
                object_path=object_path,
                native_root="/Script/ShooterGame.PrimalBuff",
            )
            _source_fact(
                core,
                ontology=self.ontology,
                entity_id=1,
                name="DeactivateAfterTime",
                value=FactValue("NUMBER", value_number=150.0),
            )
            import_legacy_lineage(
                core=core,
                legacy_root=root,
                generated_at=GENERATED_AT,
            )

            with self.assertRaisesRegex(
                AdapterSchemaError,
                "buffs.sqlite.*schema",
            ):
                materialize_semantic_adapters(
                    core=core,
                    legacy_root=root,
                    ontology=self.ontology,
                    generated_at=GENERATED_AT,
                    adapter_ids=("buffs",),
                )
            self.assertEqual(
                core.execute(
                    "SELECT COUNT(*) FROM facts WHERE fact_type='STATUS_EFFECT'"
                ).fetchone()[0],
                0,
            )
            core.close()

    def test_invalid_object_path_and_wrong_native_root_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invalid_path = "C:\\Unsafe\\Buff_Test"
            _business_database(root, object_path=invalid_path)
            core = _core()
            _source_revision(core)
            import_legacy_lineage(
                core=core,
                legacy_root=root,
                generated_at=GENERATED_AT,
            )
            materialize_semantic_adapters(
                core=core,
                legacy_root=root,
                ontology=self.ontology,
                generated_at=GENERATED_AT,
                adapter_ids=("buffs",),
            )
            self.assertEqual(
                core.execute(
                    "SELECT reason_code FROM semantic_adapter_decisions"
                ).fetchone()[0],
                "INVALID_OBJECT_PATH",
            )
            core.close()

            root = Path(temp_dir) / "wrong-root"
            root.mkdir()
            object_path = "/Game/Test/Buff_Test.Buff_Test"
            _business_database(root, object_path=object_path)
            core = _core()
            _source_revision(core)
            _entity_with_native_root(
                core,
                entity_id=1,
                object_path=object_path,
                native_root="/Script/ShooterGame.PrimalItem",
            )
            _source_fact(
                core,
                ontology=self.ontology,
                entity_id=1,
                name="DeactivateAfterTime",
                value=FactValue("NUMBER", value_number=150.0),
            )
            import_legacy_lineage(
                core=core,
                legacy_root=root,
                generated_at=GENERATED_AT,
            )
            materialize_semantic_adapters(
                core=core,
                legacy_root=root,
                ontology=self.ontology,
                generated_at=GENERATED_AT,
                adapter_ids=("buffs",),
            )
            self.assertEqual(
                core.execute(
                    "SELECT reason_code FROM semantic_adapter_decisions"
                ).fetchone()[0],
                "CLASS_ROOT_NOT_CONFIRMED",
            )
            core.close()

    def test_direct_typed_fact_preserves_catalog_lineage_when_available(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_path = "/Game/Test/PrimalItem_Amber.PrimalItem_Amber"
            _asset_index_database(root, object_path=object_path)
            core = _core()
            _source_revision(core)
            _entity_with_native_root(
                core,
                entity_id=1,
                object_path=object_path,
                native_root="/Script/ShooterGame.PrimalItem",
            )
            source_fact_id = _source_fact(
                core,
                ontology=self.ontology,
                entity_id=1,
                name="BaseItemWeight",
                value=FactValue("NUMBER", value_number=5.0),
            )
            import_legacy_lineage(
                core=core,
                legacy_root=root,
                generated_at=GENERATED_AT,
            )

            result = materialize_semantic_adapters(
                core=core,
                legacy_root=root,
                ontology=self.ontology,
                generated_at=GENERATED_AT,
                adapter_ids=("primal_items",),
            )

            self.assertEqual(result["promotedFacts"], 1)
            semantic = core.execute(
                """
                SELECT fact_id, value_number
                FROM facts
                WHERE fact_type='ITEM_PROPERTY'
                  AND fact_name='BaseItemWeight'
                """
            ).fetchone()
            self.assertEqual(semantic[1], 5.0)
            decision = core.execute(
                """
                SELECT source_mode, source_fact_id, semantic_fact_id,
                       legacy_lineage_id, decision_status
                FROM semantic_adapter_decisions
                """
            ).fetchone()
            self.assertEqual(
                decision,
                (
                    "CORE_TYPED_FACT",
                    source_fact_id,
                    semantic[0],
                    decision[3],
                    "PROMOTED",
                ),
            )
            self.assertIsNotNone(decision[3])
            self.assertEqual(
                core.execute("SELECT status FROM legacy_lineage").fetchone()[0],
                "LEGACY_UNVERIFIED",
            )
            core.close()

    def test_direct_blueprint_evidence_does_not_invent_legacy_lineage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_path = "/Game/Test/PrimalItem_Direct.PrimalItem_Direct"
            core = _core()
            _source_revision(core)
            _entity_with_native_root(
                core,
                entity_id=1,
                object_path=object_path,
                native_root="/Script/ShooterGame.PrimalItem",
            )
            _source_fact(
                core,
                ontology=self.ontology,
                entity_id=1,
                name="BaseItemWeight",
                value=FactValue("NUMBER", value_number=2.0),
            )

            result = materialize_semantic_adapters(
                core=core,
                legacy_root=root,
                ontology=self.ontology,
                generated_at=GENERATED_AT,
                adapter_ids=("primal_items",),
            )

            self.assertEqual(result["promotedFacts"], 1)
            decision = core.execute(
                """
                SELECT decision_status, source_mode, legacy_lineage_id
                FROM semantic_adapter_decisions
                """
            ).fetchone()
            self.assertEqual(
                decision,
                ("PROMOTED", "CORE_TYPED_FACT", None),
            )
            self.assertEqual(
                core.execute("SELECT COUNT(*) FROM legacy_lineage").fetchone()[0],
                0,
            )
            core.close()

    def test_json_rules_reject_scalar_and_unreviewed_shapes(self):
        invalid_values = (
            42,
            {"unexpected": "object"},
            [{}],
            [
                {
                    "ItemEntries": [
                        {
                            "ItemClass": 123,
                            "MinQuantity": "many",
                        }
                    ],
                    "ItemSetOverride": "",
                    "MinNumItems": 1.0,
                    "MaxNumItems": 1.0,
                    "SetWeight": 1.0,
                    "bItemsRandomWithoutReplacement": False,
                }
            ],
        )
        for value in invalid_values:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                object_path = "/Game/Test/Crate_Test.Crate_Test"
                core = _core()
                _source_revision(core)
                _entity_with_native_root(
                    core,
                    entity_id=1,
                    object_path=object_path,
                    native_root=(
                        "/Script/ShooterGame."
                        "PrimalStructureItemContainer_SupplyCrate"
                    ),
                )
                _source_fact(
                    core,
                    ontology=self.ontology,
                    entity_id=1,
                    name="ItemSets",
                    value=FactValue(
                        "JSON",
                        value_json=json.dumps(
                            value,
                            separators=(",", ":"),
                        ),
                    ),
                    confidence="MEDIUM",
                )

                result = materialize_semantic_adapters(
                    core=core,
                    legacy_root=root,
                    ontology=self.ontology,
                    generated_at=GENERATED_AT,
                    adapter_ids=("loot",),
                )

                self.assertEqual(result["promotedFacts"], 0)
                self.assertEqual(
                    core.execute(
                        """
                        SELECT reason_code
                        FROM semantic_adapter_decisions
                        """
                    ).fetchone()[0],
                    "INVALID_SEMANTIC_JSON_SHAPE",
                )
                self.assertEqual(
                    core.execute(
                        "SELECT COUNT(*) FROM facts WHERE fact_type='LOOT_ENTRY'"
                    ).fetchone()[0],
                    0,
                )
                core.close()

    def test_json_item_set_shape_with_typed_override_is_promoted_partial(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_path = "/Game/Test/Crate_Test.Crate_Test"
            core = _core()
            _source_revision(core)
            _entity_with_native_root(
                core,
                entity_id=1,
                object_path=object_path,
                native_root=(
                    "/Script/ShooterGame."
                    "PrimalStructureItemContainer_SupplyCrate"
                ),
            )
            _source_fact(
                core,
                ontology=self.ontology,
                entity_id=1,
                name="ItemSets",
                value=FactValue(
                    "JSON",
                    value_json=json.dumps(
                        [
                            {
                                "ItemEntries": [],
                                "ItemSetOverride": (
                                    "/Game/Test/LootSet_Test."
                                    "LootSet_Test_C"
                                ),
                                "MinNumItems": 1.0,
                                "MaxNumItems": 1.0,
                                "SetWeight": 1.0,
                                "bItemsRandomWithoutReplacement": False,
                            }
                        ],
                        separators=(",", ":"),
                    ),
                ),
                confidence="MEDIUM",
            )

            result = materialize_semantic_adapters(
                core=core,
                legacy_root=root,
                ontology=self.ontology,
                generated_at=GENERATED_AT,
                adapter_ids=("loot",),
            )

            self.assertEqual(result["promotedFacts"], 1)
            self.assertEqual(
                core.execute(
                    """
                    SELECT decision_status, reason_code
                    FROM semantic_adapter_decisions
                    """
                ).fetchone(),
                ("PROMOTED", "VERIFIED_PARTIAL"),
            )
            core.close()

    def test_item_json_rules_reject_wrong_top_level_and_nested_types(self):
        cases = (
            ("BaseCraftingResourceRequirements", 42),
            (
                "BaseCraftingResourceRequirements",
                [
                    {
                        "BaseResourceRequirement": "many",
                        "ResourceItemType": 123,
                        "bCraftingRequireExactResourceType": False,
                    }
                ],
            ),
            ("UseItemAddCharacterStatusValues", {"unexpected": True}),
        )
        for property_name, value in cases:
            with self.subTest(property=property_name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                core = _core()
                _source_revision(core)
                _entity_with_native_root(
                    core,
                    entity_id=1,
                    object_path="/Game/Test/Item_Test.Item_Test",
                    native_root="/Script/ShooterGame.PrimalItem",
                )
                _source_fact(
                    core,
                    ontology=self.ontology,
                    entity_id=1,
                    name=property_name,
                    value=FactValue(
                        "JSON",
                        value_json=json.dumps(
                            value,
                            separators=(",", ":"),
                        ),
                    ),
                )

                result = materialize_semantic_adapters(
                    core=core,
                    legacy_root=root,
                    ontology=self.ontology,
                    generated_at=GENERATED_AT,
                    adapter_ids=("primal_items",),
                )

                self.assertEqual(result["promotedFacts"], 0)
                self.assertEqual(
                    core.execute(
                        "SELECT reason_code FROM semantic_adapter_decisions"
                    ).fetchone()[0],
                    "INVALID_SEMANTIC_JSON_SHAPE",
                )
                core.close()

    def test_property_rules_reject_cross_family_value_kinds(self):
        cases = (
            (
                "primal_items",
                "/Script/ShooterGame.PrimalItem",
                "BaseItemWeight",
                FactValue("TEXT", value_text="heavy"),
            ),
            (
                "primal_items",
                "/Script/ShooterGame.PrimalItem",
                "DescriptiveNameBase",
                FactValue("NUMBER", value_number=123.0),
            ),
            (
                "primal_items",
                "/Script/ShooterGame.PrimalItem",
                "MaxItemQuantity",
                FactValue(
                    "ENTITY_REF",
                    value_text="/Game/Test/NotAQuantity.NotAQuantity",
                ),
            ),
            (
                "loot",
                (
                    "/Script/ShooterGame."
                    "PrimalStructureItemContainer_SupplyCrate"
                ),
                "bItemsRandomWithoutReplacement",
                FactValue("NUMBER", value_number=42.0),
            ),
            (
                "loot",
                (
                    "/Script/ShooterGame."
                    "PrimalStructureItemContainer_SupplyCrate"
                ),
                "MinItemSets",
                FactValue("BOOLEAN", value_integer=1),
            ),
        )
        for adapter_id, native_root, property_name, value in cases:
            with self.subTest(property=property_name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                core = _core()
                _source_revision(core)
                _entity_with_native_root(
                    core,
                    entity_id=1,
                    object_path="/Game/Test/Typed_Test.Typed_Test",
                    native_root=native_root,
                )
                _source_fact(
                    core,
                    ontology=self.ontology,
                    entity_id=1,
                    name=property_name,
                    value=value,
                )

                result = materialize_semantic_adapters(
                    core=core,
                    legacy_root=root,
                    ontology=self.ontology,
                    generated_at=GENERATED_AT,
                    adapter_ids=(adapter_id,),
                )

                self.assertEqual(result["promotedFacts"], 0)
                self.assertEqual(
                    core.execute(
                        "SELECT reason_code FROM semantic_adapter_decisions"
                    ).fetchone()[0],
                    "UNSUPPORTED_VALUE_TYPE",
                )
                core.close()

    def test_reviewed_item_json_shapes_are_promoted(self):
        cases = (
            (
                "BaseCraftingResourceRequirements",
                [
                    {
                        "BaseResourceRequirement": 5.0,
                        "ResourceItemType": (
                            "/Game/Test/Resource_Test.Resource_Test_C"
                        ),
                        "bCraftingRequireExactResourceType": False,
                    }
                ],
                "VERIFIED",
            ),
            (
                "CraftingRequiresInventoryComponent",
                ["/Game/Test/Inventory_Test.Inventory_Test_C"],
                "VERIFIED",
            ),
            (
                "UseItemAddCharacterStatusValues",
                [
                    {
                        "AddOverTimeSpeed": 5.0,
                        "BaseAmountToAdd": 100.0,
                        "StatusValueType": (
                            "EPrimalCharacterStatusValue::Food"
                        ),
                        "bAddOverTime": True,
                    }
                ],
                "VERIFIED_PARTIAL",
            ),
        )
        for property_name, value, reason in cases:
            with self.subTest(property=property_name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                core = _core()
                _source_revision(core)
                _entity_with_native_root(
                    core,
                    entity_id=1,
                    object_path="/Game/Test/Item_Test.Item_Test",
                    native_root="/Script/ShooterGame.PrimalItem",
                )
                _source_fact(
                    core,
                    ontology=self.ontology,
                    entity_id=1,
                    name=property_name,
                    value=FactValue(
                        "JSON",
                        value_json=json.dumps(
                            value,
                            separators=(",", ":"),
                        ),
                    ),
                )

                result = materialize_semantic_adapters(
                    core=core,
                    legacy_root=root,
                    ontology=self.ontology,
                    generated_at=GENERATED_AT,
                    adapter_ids=("primal_items",),
                )

                self.assertEqual(result["promotedFacts"], 1)
                self.assertEqual(
                    core.execute(
                        "SELECT reason_code FROM semantic_adapter_decisions"
                    ).fetchone()[0],
                    reason,
                )
                core.close()

    def test_harvest_json_rule_rejects_untyped_resource_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            core = _core()
            _source_revision(core)
            _entity_with_native_root(
                core,
                entity_id=1,
                object_path="/Game/Test/Harvest_Test.Harvest_Test",
                native_root=(
                    "/Script/ShooterGame.PrimalHarvestingComponent"
                ),
            )
            _source_fact(
                core,
                ontology=self.ontology,
                entity_id=1,
                name="HarvestResourceEntries",
                value=FactValue(
                    "JSON",
                    value_json=json.dumps(
                        [
                            {
                                "ResourceItemClass": 7,
                                "BaseResourceAmount": "many",
                            }
                        ],
                        separators=(",", ":"),
                    ),
                ),
                confidence="MEDIUM",
            )

            result = materialize_semantic_adapters(
                core=core,
                legacy_root=root,
                ontology=self.ontology,
                generated_at=GENERATED_AT,
                adapter_ids=("harvest",),
            )

            self.assertEqual(result["promotedFacts"], 0)
            self.assertEqual(
                core.execute(
                    "SELECT reason_code FROM semantic_adapter_decisions"
                ).fetchone()[0],
                "INVALID_SEMANTIC_JSON_SHAPE",
            )
            core.close()

    def test_status_array_like_defaults_are_not_promoted_as_scalars(self):
        ambiguous_properties = (
            "MaxStatusValues",
            "TamingMaxStatAdditions",
            "TamingMaxStatMultipliers",
        )
        for property_name in ambiguous_properties:
            with self.subTest(property=property_name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                core = _core()
                _source_revision(core)
                _entity_with_native_root(
                    core,
                    entity_id=1,
                    object_path="/Game/Test/Status_Test.Status_Test",
                    native_root=(
                        "/Script/ShooterGame.PrimalDinoStatusComponent"
                    ),
                )
                _source_fact(
                    core,
                    ontology=self.ontology,
                    entity_id=1,
                    name=property_name,
                    value=FactValue("NUMBER", value_number=0.0),
                )

                result = materialize_semantic_adapters(
                    core=core,
                    legacy_root=root,
                    ontology=self.ontology,
                    generated_at=GENERATED_AT,
                    adapter_ids=("status_components",),
                )

                self.assertEqual(result["promotedFacts"], 0)
                self.assertEqual(
                    core.execute(
                        """
                        SELECT COUNT(*) FROM facts
                        WHERE fact_type='STATUS_VALUE'
                        """
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    core.execute(
                        "SELECT COUNT(*) FROM semantic_adapter_decisions"
                    ).fetchone()[0],
                    0,
                )
                core.close()

    def test_rerun_revokes_and_reactivates_adapter_owned_fact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_path = "/Game/Test/Buff_Test.Buff_Test"
            _business_database(root, object_path=object_path)
            core = _core()
            _source_revision(core)
            _entity_with_native_root(
                core,
                entity_id=1,
                object_path=object_path,
                native_root="/Script/ShooterGame.PrimalBuff",
            )
            source_fact_id = _source_fact(
                core,
                ontology=self.ontology,
                entity_id=1,
                name="DeactivateAfterTime",
                value=FactValue("NUMBER", value_number=150.0),
            )
            import_legacy_lineage(
                core=core,
                legacy_root=root,
                generated_at=GENERATED_AT,
            )
            first = materialize_semantic_adapters(
                core=core,
                legacy_root=root,
                ontology=self.ontology,
                generated_at=GENERATED_AT,
                adapter_ids=("buffs",),
            )
            self.assertEqual(first["promotedFacts"], 1)

            core.execute(
                "UPDATE facts SET current=0 WHERE fact_id=?",
                (source_fact_id,),
            )
            second = materialize_semantic_adapters(
                core=core,
                legacy_root=root,
                ontology=self.ontology,
                generated_at=GENERATED_AT,
                adapter_ids=("buffs",),
            )

            self.assertEqual(second["promotedFacts"], 0)
            self.assertEqual(
                core.execute(
                    """
                    SELECT COUNT(*) FROM facts
                    WHERE fact_type='STATUS_EFFECT' AND current=1
                    """
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                core.execute("SELECT status FROM legacy_lineage").fetchone()[0],
                "LEGACY_UNVERIFIED",
            )

            core.execute(
                "UPDATE facts SET current=1 WHERE fact_id=?",
                (source_fact_id,),
            )
            third = materialize_semantic_adapters(
                core=core,
                legacy_root=root,
                ontology=self.ontology,
                generated_at=GENERATED_AT,
                adapter_ids=("buffs",),
            )

            self.assertEqual(third["promotedFacts"], 1)
            self.assertEqual(
                core.execute(
                    """
                    SELECT COUNT(*) FROM facts
                    WHERE fact_type='STATUS_EFFECT' AND current=1
                    """
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                core.execute("SELECT status FROM legacy_lineage").fetchone()[0],
                "SEMANTICALLY_VERIFIED",
            )
            core.close()

    def test_legacy_source_requires_imported_lineage_and_declared_primary_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_path = "/Game/Test/Buff_Test.Buff_Test"
            _business_database(root, object_path=object_path)
            core = _core()
            _source_revision(core)
            _entity_with_native_root(
                core,
                entity_id=1,
                object_path=object_path,
                native_root="/Script/ShooterGame.PrimalBuff",
            )
            _source_fact(
                core,
                ontology=self.ontology,
                entity_id=1,
                name="DeactivateAfterTime",
                value=FactValue("NUMBER", value_number=150.0),
            )

            result = materialize_semantic_adapters(
                core=core,
                legacy_root=root,
                ontology=self.ontology,
                generated_at=GENERATED_AT,
                adapter_ids=("buffs",),
            )

            self.assertEqual(result["promotedFacts"], 0)
            self.assertEqual(
                core.execute(
                    "SELECT reason_code FROM semantic_adapter_decisions"
                ).fetchone()[0],
                "LEGACY_LINEAGE_MISSING",
            )
            core.close()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _business_database(
                root,
                object_path="/Game/Test/Buff_Test.Buff_Test",
                id_declaration="id INTEGER",
            )
            core = _core()
            with self.assertRaises(AdapterSchemaError):
                materialize_semantic_adapters(
                    core=core,
                    legacy_root=root,
                    ontology=self.ontology,
                    generated_at=GENERATED_AT,
                    adapter_ids=("buffs",),
                )
            core.close()

    def test_native_root_and_default_value_evidence_are_fresh_and_canonical(self):
        cases = (
            "non_native_root",
            "stale_root",
            "wrong_value_evidence",
            "stale_ontology",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                object_path = "/Game/Test/PrimalItem_Test.PrimalItem_Test"
                core = _core()
                _source_revision(core)
                if case == "stale_root":
                    _source_revision(
                        core,
                        revision_id=2,
                        freshness="STALE",
                        source_uri="class-edge://fixture-stale",
                    )
                _entity_with_native_root(
                    core,
                    entity_id=1,
                    object_path=object_path,
                    native_root="/Script/ShooterGame.PrimalItem",
                )
                if case == "non_native_root":
                    core.execute(
                        """
                        UPDATE classes
                        SET is_native=0,
                            class_kind='BLUEPRINT_GENERATED_CLASS'
                        WHERE class_path='/Script/ShooterGame.PrimalItem'
                        """
                    )
                elif case == "stale_root":
                    core.execute(
                        """
                        UPDATE classes SET source_revision_id=2
                        WHERE class_path='/Script/ShooterGame.PrimalItem'
                        """
                    )
                    core.execute(
                        "UPDATE class_edges SET source_revision_id=2"
                    )
                evidence_uri = None
                evidence_role = "DEFAULT_VALUE_ACTUAL"
                if case == "wrong_value_evidence":
                    evidence_uri = "class-edge://not-a-default-value"
                    evidence_role = "CLASS_PARENT"
                source_fact_id = _source_fact(
                    core,
                    ontology=self.ontology,
                    entity_id=1,
                    name="BaseItemWeight",
                    value=FactValue("NUMBER", value_number=2.0),
                    evidence_uri=evidence_uri,
                    evidence_role=evidence_role,
                )
                if case == "stale_ontology":
                    core.execute(
                        """
                        UPDATE facts
                        SET ontology_version='ark-fact-types/v1'
                        WHERE fact_id=?
                        """,
                        (source_fact_id,),
                    )

                result = materialize_semantic_adapters(
                    core=core,
                    legacy_root=root,
                    ontology=self.ontology,
                    generated_at=GENERATED_AT,
                    adapter_ids=("primal_items",),
                )

                self.assertEqual(result["promotedFacts"], 0)
                expected_reason = {
                    "wrong_value_evidence": "EVIDENCE_NOT_CANONICAL",
                    "stale_ontology": "SOURCE_ONTOLOGY_MISMATCH",
                }.get(case, "CLASS_ROOT_NOT_CONFIRMED")
                self.assertEqual(
                    core.execute(
                        "SELECT reason_code FROM semantic_adapter_decisions"
                    ).fetchone()[0],
                    expected_reason,
                )
                core.close()


if __name__ == "__main__":
    unittest.main()
