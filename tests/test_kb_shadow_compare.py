from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.fact_store import (  # noqa: E402
    FactValue,
    store_fact,
)
from blueprint_translator.kb_vnext.kb_api import (  # noqa: E402
    VNextKnowledgeService,
)
from blueprint_translator.kb_vnext.ontology import load_ontology  # noqa: E402
from blueprint_translator.kb_vnext.shadow_compare import (  # noqa: E402
    LegacyVNextComparator,
    file_sha256,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    CACHE_SCHEMA_SQL,
    CORE_SCHEMA_VERSION,
    FULL_CORE_SCHEMA_SQL,
)


def _fixture(
    root: Path,
    *,
    legacy_value: float = 2.5,
    legacy_status: str = "CONFIRMED",
) -> tuple[LegacyVNextComparator, Path]:
    ontology = load_ontology(PROJECT_ROOT / "ontology")
    vnext_root = root / "vnext"
    legacy_root = root / "legacy"
    (vnext_root / "manifests").mkdir(parents=True)
    legacy_root.mkdir()
    core = sqlite3.connect(vnext_root / "core.sqlite")
    core.execute("PRAGMA foreign_keys=ON")
    core.executescript(FULL_CORE_SCHEMA_SQL)
    core.executemany(
        "INSERT INTO metadata VALUES (?, ?)",
        [
            ("schema_version", CORE_SCHEMA_VERSION),
            ("ontology_version", ontology.version),
        ],
    )
    core.execute(
        """
        INSERT INTO source_revisions VALUES (
            1, 'capture', 'capture://fixture', 'sha', 'test', 'v1',
            '2026-07-27T00:00:00Z', 'FRESH'
        )
        """
    )
    core.execute(
        """
        INSERT INTO entities(
            entity_id, canonical_uri, entity_kind,
            display_name, internal_name, status, confidence
        ) VALUES (
            1, '/Game/Test/ItemA.ItemA', 'BLUEPRINT_ASSET',
            'Item A', 'ItemA', 'CONFIRMED', 'HIGH'
        )
        """
    )
    store_fact(
        core,
        ontology=ontology,
        subject_entity_id=1,
        fact_type="ITEM_PROPERTY",
        fact_name="Weight",
        scope_kind="DERIVED_STATIC",
        declared_on_entity_id=1,
        value=FactValue("NUMBER", value_number=2.5),
        status="CONFIRMED",
        confidence="HIGH",
        source_revision_id=1,
        evidence_uri="bp://fixture/item-a/weight",
        evidence_role="DIRECT_FIELD",
    )
    core.commit()
    core.close()
    cache = sqlite3.connect(vnext_root / "cache.sqlite")
    cache.executescript(CACHE_SCHEMA_SQL)
    cache.commit()
    cache.close()
    (vnext_root / "manifests" / "current.json").write_text(
        json.dumps(
            {
                "schema": "ark-kb-vnext-snapshot/v1",
                "buildId": "fixture",
                "source": {"uri": "discovery://fixture", "sha256": "sha"},
                "cutover": {
                    "mode": "shadow",
                    "defaultQuerySource": "legacy",
                },
            }
        ),
        encoding="utf-8",
    )
    legacy_path = legacy_root / "items.sqlite"
    legacy = sqlite3.connect(legacy_path)
    legacy.execute(
        """
        CREATE TABLE item_facts(
            object_path TEXT NOT NULL,
            fact_type TEXT NOT NULL,
            fact_name TEXT NOT NULL,
            value_number REAL,
            status TEXT NOT NULL,
            evidence_uri TEXT NOT NULL
        )
        """
    )
    legacy.execute(
        "INSERT INTO item_facts VALUES (?, 'ITEM_PROPERTY', 'Weight', ?, ?, 'legacy://fixture/item-a/weight')",
        ("/Game/Test/ItemA.ItemA", legacy_value, legacy_status),
    )
    legacy.commit()
    legacy.close()
    service = VNextKnowledgeService(vnext_root)
    return (
        LegacyVNextComparator(
            vnext=service,
            legacy_root=legacy_root,
        ),
        legacy_path,
    )


class KnowledgeShadowCompareTests(unittest.TestCase):
    def test_matching_semantic_rows_report_consistency_and_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            comparator, legacy_path = _fixture(Path(temp_dir))
            before = file_sha256(legacy_path)
            result = comparator.compare(
                {
                    "entity": "/Game/Test/ItemA.ItemA",
                    "factTypes": ["ITEM_PROPERTY"],
                    "factNames": ["Weight"],
                    "budgetTokens": 500,
                }
            )
            after = file_sha256(legacy_path)
            self.assertTrue(result["consistent"])
            self.assertEqual(result["differenceReasons"], ["MATCH"])
            self.assertEqual(result["preferredSource"], "vnext")
            self.assertEqual(
                result["evidenceCompleteness"],
                {"legacy": 1, "vnext": 1, "vnextComplete": True},
            )
            self.assertEqual(before, after)

    def test_value_mismatch_is_explicit_and_does_not_pick_a_winner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            comparator, _ = _fixture(
                Path(temp_dir), legacy_value=9.0
            )
            result = comparator.compare(
                {
                    "entity": "/Game/Test/ItemA.ItemA",
                    "factTypes": ["ITEM_PROPERTY"],
                    "factNames": ["Weight"],
                    "budgetTokens": 500,
                }
            )
            self.assertFalse(result["consistent"])
            self.assertEqual(result["preferredSource"], "vnext")
            self.assertTrue(
                any(
                    reason.startswith("VALUE_MISMATCH")
                    for reason in result["differenceReasons"]
                )
            )

    def test_status_mismatch_and_uncomparable_rows_are_not_silent_matches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            comparator, _ = _fixture(
                Path(temp_dir), legacy_status="STALE"
            )
            stale = comparator.compare(
                {
                    "entity": "/Game/Test/ItemA.ItemA",
                    "factTypes": ["ITEM_PROPERTY"],
                    "factNames": ["Weight"],
                    "budgetTokens": 500,
                }
            )
            self.assertFalse(stale["consistent"])
            self.assertIn(
                "STATUS_MISMATCH:ITEM_PROPERTY:Weight",
                stale["differenceReasons"],
            )
            no_match = comparator.compare(
                {
                    "entity": "/Game/Test/Missing.Missing",
                    "factTypes": ["ITEM_PROPERTY"],
                    "budgetTokens": 500,
                }
            )
            self.assertIsNone(no_match["consistent"])
            self.assertIn(
                "LEGACY_NO_MATCH", no_match["differenceReasons"]
            )

    def test_public_shadow_payload_redacts_local_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            comparator, legacy_path = _fixture(Path(temp_dir))
            legacy = sqlite3.connect(legacy_path)
            legacy.execute(
                """
                UPDATE item_facts
                SET evidence_uri='C:\\Users\\person\\secret.json'
                """
            )
            legacy.commit()
            legacy.close()
            result = comparator.compare(
                {
                    "entity": "/Game/Test/ItemA.ItemA",
                    "factTypes": ["ITEM_PROPERTY"],
                    "budgetTokens": 500,
                }
            )
            payload = json.dumps(result)
            self.assertNotIn("C:\\\\Users", payload)
            self.assertIn("LOCAL_PATH_REDACTED", payload)


if __name__ == "__main__":
    unittest.main()
