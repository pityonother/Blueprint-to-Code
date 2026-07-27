from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.ontology import (  # noqa: E402
    REQUIRED_DOMAINS,
    infer_domain_memberships,
    load_ontology,
)
from blueprint_translator.kb_vnext.roles import (  # noqa: E402
    DEPTH_POLICIES,
    KNOWLEDGE_ROLES,
)


class KnowledgeOntologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ontology = load_ontology(PROJECT_ROOT / "ontology")

    def test_versioned_ontology_has_required_domains_and_shared_roles(self):
        self.assertEqual(
            set(self.ontology.domains),
            REQUIRED_DOMAINS,
        )
        self.assertEqual(set(self.ontology.roles), set(KNOWLEDGE_ROLES))
        self.assertEqual(
            set(self.ontology.depth_policies), set(DEPTH_POLICIES)
        )
        self.assertIn("BLUEPRINT_CALLS_NATIVE", self.ontology.edge_types)
        self.assertIn("EFFECTIVE_DEFAULT", self.ontology.fact_types)
        self.assertIn("RUNTIME_OBSERVED", self.ontology.scope_kinds)

    def test_class_and_registration_evidence_are_confirmed_and_multi_domain(self):
        memberships = infer_domain_memberships(
            self.ontology,
            {
                "entity_uri": "bp://fixture",
                "class_categories": ["BUFF"],
                "registration_types": ["buff_registration"],
                "component_categories": ["STATUS_COMPONENT"],
                "function_names": [],
                "property_names": [],
                "confirmed_reference_domains": [],
                "object_path": "/Game/Test/Neutral.Neutral",
                "asset_name": "Neutral",
                "semantic_evidence_recovered": True,
            },
        )
        confirmed = {
            (item.domain_id, item.membership_kind)
            for item in memberships
            if item.status == "CONFIRMED"
        }
        self.assertIn(("buff", "CLASS_ANCESTRY"), confirmed)
        self.assertIn(("buff", "TYPED_REGISTRATION"), confirmed)
        self.assertIn(("status_component", "COMPONENT_TYPE"), confirmed)

    def test_function_and_property_semantics_remain_candidates(self):
        memberships = infer_domain_memberships(
            self.ontology,
            {
                "entity_uri": "bp://fixture",
                "class_categories": [],
                "registration_types": [],
                "component_categories": [],
                "function_names": ["GenerateCrateItems"],
                "property_names": ["ItemSets"],
                "confirmed_reference_domains": [],
                "object_path": "/Game/Test/Neutral.Neutral",
                "asset_name": "Neutral",
                "semantic_evidence_recovered": True,
            },
        )
        loot = [
            item
            for item in memberships
            if item.domain_id == "loot_quality_reward"
        ]
        self.assertTrue(loot)
        self.assertTrue(all(item.status == "CANDIDATE" for item in loot))
        self.assertTrue(all(item.confidence == "MEDIUM" for item in loot))

    def test_name_or_folder_never_becomes_confirmed_domain_truth(self):
        memberships = infer_domain_memberships(
            self.ontology,
            {
                "entity_uri": "bp://fixture",
                "class_categories": [],
                "registration_types": [],
                "component_categories": [],
                "function_names": [],
                "property_names": [],
                "confirmed_reference_domains": [],
                "object_path": "/Game/CoreBlueprints/Buffs/Buff_NameOnly.Buff_NameOnly",
                "asset_name": "Buff_NameOnly",
                "semantic_evidence_recovered": False,
            },
        )
        buff_rows = [
            item for item in memberships if item.domain_id == "buff"
        ]
        self.assertTrue(buff_rows)
        self.assertTrue(
            all(
                item.membership_kind == "NAME_OR_FOLDER_CANDIDATE"
                and item.status == "CANDIDATE"
                and item.confidence == "LOW"
                for item in buff_rows
            )
        )

    def test_unrecovered_semantics_do_not_emit_function_candidates(self):
        memberships = infer_domain_memberships(
            self.ontology,
            {
                "entity_uri": "bp://fixture",
                "function_names": ["AddBuff"],
                "property_names": ["BuffClass"],
                "semantic_evidence_recovered": False,
                "object_path": "/Game/Test/Neutral.Neutral",
                "asset_name": "Neutral",
            },
        )
        self.assertFalse(
            any(
                item.membership_kind
                in {"FUNCTION_SEMANTIC", "PROPERTY_SEMANTIC"}
                for item in memberships
            )
        )


if __name__ == "__main__":
    unittest.main()
