from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.evidence_values import (  # noqa: E402
    canonical_default_value,
    default_parse_gap,
    default_value_is_comparable,
    default_value_is_usable,
    downstream_default_metadata,
    project_default_value,
)


class DefaultValueProjectionTests(unittest.TestCase):
    def test_unparsed_array_is_not_reported_as_a_confirmed_empty_value(self):
        projection = project_default_value(
            "ArrayProperty",
            [],
            {
                "array_parse": {
                    "parsed": False,
                    "element_kind": "unknown",
                    "raw_size": 96,
                }
            },
        )

        self.assertEqual(projection["valueStatus"], "NOT_RECOVERED")
        self.assertFalse(projection["valueUsable"])
        self.assertEqual(projection["parse"]["kind"], "array")
        self.assertFalse(projection["parse"]["parsed"])

    def test_decoded_empty_array_is_confirmed(self):
        projection = project_default_value(
            "ArrayProperty",
            [],
            {
                "array_parse": {
                    "parsed": True,
                    "count": 0,
                    "element_kind": "ObjectProperty",
                    "raw_size": 4,
                }
            },
        )

        self.assertEqual(projection["valueStatus"], "CONFIRMED")
        self.assertTrue(projection["valueUsable"])
        self.assertEqual(projection["parse"]["count"], 0)

    def test_object_property_exposes_the_resolved_object_name(self):
        projection = project_default_value(
            "ObjectProperty",
            -17,
            {"package_index": -17, "object": "PrimalItemResource_Metal_C"},
        )

        self.assertEqual(projection["valueStatus"], "CONFIRMED")
        self.assertTrue(projection["valueUsable"])
        self.assertEqual(projection["resolvedObjectName"], "PrimalItemResource_Metal_C")

    def test_object_array_projection_preserves_slots_duplicates_and_order(self):
        projection = project_default_value(
            "ArrayProperty",
            [-1, -2, -3, -4],
            {
                "array_parse": {
                    "parsed": True,
                    "count": 4,
                    "element_kind": "ObjectProperty",
                    "raw_size": 20,
                },
                "objects": ["MineStone_C", "", "MineStone_C", "Crystal_C"],
            },
            resolved_object_limit=3,
        )

        self.assertEqual(projection["resolvedObjectNames"], ["MineStone_C", None, "MineStone_C"])
        self.assertEqual(projection["resolvedObjectCoverage"], {"available": 4, "returned": 3})

    def test_parse_error_overrides_otherwise_successful_container_metadata(self):
        projection = project_default_value(
            "ArrayProperty",
            [],
            {
                "error": "truncated payload",
                "array_parse": {"parsed": True, "count": 0, "raw_size": 4},
            },
        )

        self.assertEqual(projection["valueStatus"], "NOT_RECOVERED")
        self.assertFalse(projection["valueUsable"])
        self.assertEqual(projection["parse"], {"kind": "error", "parsed": False})

    def test_struct_array_exposes_bounded_object_fields_with_element_positions(self):
        projection = project_default_value(
            "ArrayProperty",
            [{"ResourceItemType": -7}, {"ResourceItemType": -8}],
            {
                "array_parse": {
                    "parsed": True,
                    "count": 2,
                    "element_kind": "StructProperty",
                    "elements": [
                        {
                            "index": 0,
                            "properties": [
                                {"name": "ResourceItemType", "object": "PrimalItemResource_Stone_C"}
                            ],
                        },
                        {
                            "index": 1,
                            "properties": [
                                {"name": "ResourceItemType", "object": "PrimalItemResource_Metal_C"},
                                {"name": "DamageType", "object": "DmgType_Melee_DmgStone_C"},
                            ],
                        },
                    ],
                }
            },
            resolved_object_limit=2,
        )

        self.assertEqual(
            projection["resolvedObjectFields"],
            [
                {
                    "elementIndex": 0,
                    "propertyIndex": 0,
                    "propertyName": "ResourceItemType",
                    "name": "PrimalItemResource_Stone_C",
                },
                {
                    "elementIndex": 1,
                    "propertyIndex": 0,
                    "propertyName": "ResourceItemType",
                    "name": "PrimalItemResource_Metal_C",
                },
            ],
        )
        self.assertEqual(projection["resolvedObjectFieldCoverage"], {"available": 3, "returned": 2})

    def test_unparsed_array_creates_an_explicit_gap(self):
        projection = project_default_value(
            "ArrayProperty",
            [],
            {"array_parse": {"parsed": False, "raw_size": 96}},
        )

        gap = default_parse_gap(
            "bp://asset@revision/default/HarvestResourceEntries",
            "HarvestResourceEntries",
            "ArrayProperty",
            projection,
        )

        self.assertIsNotNone(gap)
        assert gap is not None
        self.assertEqual(gap["status"], "NOT_RECOVERED")
        self.assertEqual(gap["reasonCode"], "array_property_not_decoded")
        self.assertEqual(gap["scopeRef"], "bp://asset@revision/default/HarvestResourceEntries")

    def test_confirmed_scalar_has_no_parse_gap(self):
        projection = project_default_value("FloatProperty", 2.0, {})

        self.assertEqual(projection, {"valueStatus": "CONFIRMED", "valueUsable": True})
        self.assertIsNone(
            default_parse_gap(
                "bp://asset@revision/default/DamageMultiplier",
                "DamageMultiplier",
                "FloatProperty",
                projection,
            )
        )

    def test_downstream_metadata_keeps_missing_state_and_resolved_fields(self):
        row = {
            "valueStatus": "NOT_RECOVERED",
            "valueUsable": False,
            "parse": {"kind": "array", "parsed": False},
            "resolvedObjectNames": ["MineStone_C", None, "MineStone_C"],
        }

        metadata = downstream_default_metadata(row)

        self.assertFalse(default_value_is_usable(metadata))
        self.assertEqual(metadata["value_status"], "NOT_RECOVERED")
        self.assertEqual(metadata["resolved_object_names"], ["MineStone_C", None, "MineStone_C"])

    def test_legacy_parse_metadata_also_blocks_unusable_placeholder(self):
        row = {
            "type": "ArrayProperty",
            "value": [],
            "array_parse": {"parsed": False, "raw_size": 96},
        }

        self.assertFalse(default_value_is_usable(row))
        self.assertEqual(
            downstream_default_metadata(row),
            {"value_status": "NOT_RECOVERED", "value_usable": False},
        )

    def test_truncated_resolved_object_projection_is_not_cross_revision_comparable(self):
        row = {
            "value_usable": True,
            "resolved_object_names": ["Metal_C"],
            "resolved_object_coverage": {"available": 3, "returned": 1},
        }

        self.assertFalse(default_value_is_comparable(row))

    def test_canonical_struct_array_value_replaces_revision_local_package_indexes(self):
        row = {
            "value": [{"ResourceItemType": -17, "EntryWeight": 0.63}],
            "value_usable": True,
            "resolved_object_fields": [
                {
                    "elementIndex": 0,
                    "propertyIndex": 0,
                    "propertyName": "ResourceItemType",
                    "name": "PrimalItemResource_Metal_C",
                }
            ],
        }

        self.assertEqual(
            canonical_default_value(row),
            [{"ResourceItemType": "PrimalItemResource_Metal_C", "EntryWeight": 0.63}],
        )

    def test_canonical_struct_array_recurses_into_nested_object_arrays(self):
        value = [
            {
                "ResourceItem": -15,
                "DamageTypeEntryValuesOverrides": [-8, -9, 0],
                "DamageTypeEntryWeightOverrides": [0.8, 0.4, 1.0],
            }
        ]
        projection = project_default_value(
            "ArrayProperty",
            value,
            {
                "array_parse": {
                    "parsed": True,
                    "count": 1,
                    "element_kind": "StructProperty",
                    "elements": [
                        {
                            "index": 0,
                            "properties": [
                                {
                                    "name": "ResourceItem",
                                    "type": "ObjectProperty",
                                    "value": -15,
                                    "object": "PrimalItemResource_Metal_C",
                                },
                                {
                                    "name": "DamageTypeEntryValuesOverrides",
                                    "type": "ArrayProperty",
                                    "value": [-8, -9, 0],
                                    "objects": [
                                        "DmgType_Melee_MetalHatchet_C",
                                        "DmgType_Melee_MetalPick_C",
                                        "",
                                    ],
                                    "array_parse": {
                                        "parsed": True,
                                        "count": 3,
                                        "element_kind": "ObjectProperty",
                                        "elements": [
                                            {
                                                "index": 0,
                                                "value": -8,
                                                "object": "DmgType_Melee_MetalHatchet_C",
                                            },
                                            {
                                                "index": 1,
                                                "value": -9,
                                                "object": "DmgType_Melee_MetalPick_C",
                                            },
                                            {"index": 2, "value": 0, "object": ""},
                                        ],
                                    },
                                },
                            ],
                        }
                    ],
                }
            },
        )
        row = {"value": value, **projection}

        self.assertTrue(default_value_is_comparable(row))
        self.assertEqual(
            canonical_default_value(row),
            [
                {
                    "ResourceItem": "PrimalItemResource_Metal_C",
                    "DamageTypeEntryValuesOverrides": [
                        "DmgType_Melee_MetalHatchet_C",
                        "DmgType_Melee_MetalPick_C",
                        0,
                    ],
                    "DamageTypeEntryWeightOverrides": [0.8, 0.4, 1.0],
                }
            ],
        )

    def test_unparsed_nested_reference_array_is_not_cross_revision_comparable(self):
        projection = project_default_value(
            "ArrayProperty",
            [{"DamageTypeEntryValuesOverrides": []}],
            {
                "array_parse": {
                    "parsed": True,
                    "count": 1,
                    "element_kind": "StructProperty",
                    "elements": [
                        {
                            "index": 0,
                            "properties": [
                                {
                                    "name": "DamageTypeEntryValuesOverrides",
                                    "type": "ArrayProperty",
                                    "value": [],
                                    "inner_type": "ObjectProperty",
                                    "array_parse": {
                                        "parsed": False,
                                        "element_kind": "ObjectProperty",
                                        "raw_size": 28,
                                    },
                                }
                            ],
                        }
                    ],
                }
            },
        )

        self.assertTrue(projection["valueUsable"])
        self.assertFalse(
            default_value_is_comparable(
                {"value": [{"DamageTypeEntryValuesOverrides": []}], **projection}
            )
        )

    def test_truncated_nested_reference_projection_is_not_cross_revision_comparable(self):
        references = [-(index + 1) for index in range(25)]
        names = [f"DmgType_{index}_C" for index in range(25)]
        projection = project_default_value(
            "ArrayProperty",
            [{"DamageTypeEntryValuesOverrides": references}],
            {
                "array_parse": {
                    "parsed": True,
                    "count": 1,
                    "element_kind": "StructProperty",
                    "elements": [
                        {
                            "index": 0,
                            "properties": [
                                {
                                    "name": "DamageTypeEntryValuesOverrides",
                                    "type": "ArrayProperty",
                                    "value": references,
                                    "objects": names,
                                    "array_parse": {
                                        "parsed": True,
                                        "count": len(references),
                                        "element_kind": "ObjectProperty",
                                        "elements": [
                                            {"index": index, "value": ref, "object": names[index]}
                                            for index, ref in enumerate(references)
                                        ],
                                    },
                                }
                            ],
                        }
                    ],
                }
            },
            resolved_object_limit=24,
        )

        self.assertFalse(
            default_value_is_comparable(
                {"value": [{"DamageTypeEntryValuesOverrides": references}], **projection}
            )
        )


if __name__ == "__main__":
    unittest.main()
