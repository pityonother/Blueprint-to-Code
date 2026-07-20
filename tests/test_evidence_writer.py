import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.evidence_writer import (  # noqa: E402
    write_evidence_store_from_capture,
    write_evidence_store_from_payload,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _make_pin(
    native_pin_id: str,
    name: str,
    direction: str,
    *,
    default: object = "",
    links: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "id": native_pin_id,
        "persistent_guid": native_pin_id,
        "name": name,
        "direction": direction,
        "category": "exec" if name in {"then", "execute"} else "real",
        "subcategory": "",
        "default": default,
        "default_object": "",
        "links": links or [],
        "source": "fixture_pin_reader",
        "confidence": "high",
        "raw_offsets": {"start": 10, "end": 20},
    }


def _make_legacy_capture(root: Path) -> tuple[Path, Path, Path]:
    asset_dir = root / "Fixture_BP"
    graphs_dir = asset_dir / "graphs_from_uasset"
    graphs_dir.mkdir(parents=True)

    uasset_path = asset_dir / "Fixture_BP.uasset"
    uexp_path = asset_dir / "Fixture_BP.uexp"
    ubulk_path = asset_dir / "Fixture_BP.ubulk"
    uasset_path.write_bytes(b"fixture-uasset-v1")
    uexp_path.write_bytes(b"fixture-uexp-v1")
    ubulk_path.write_bytes(b"fixture-ubulk-v1")

    _write_json(
        asset_dir / "uasset_package.json",
        {
            "schema": "blueprint-translator.uasset-package.v1",
            "summary": {"package_name": "/Game/Test/Fixture_BP"},
            "uasset_path": str(uasset_path),
            "uexp_path": str(uexp_path),
            "warnings": [],
        },
    )

    graph_7_path = graphs_dir / "SharedGraph_7.json"
    graph_8_path = graphs_dir / "SharedGraph_8.json"
    manifest_path = asset_dir / "graphs_from_uasset_manifest.json"
    _write_json(
        manifest_path,
        {
            "schema": "blueprint-translator.graphs-from-uasset-manifest.v1",
            "asset_name": "Fixture_BP",
            "asset_path": "/Game/Test/Fixture_BP.Fixture_BP",
            "source_graph_count": 2,
            "graph_file_count": 2,
            "files": [
                {
                    "graph": "SharedGraph",
                    "graph_type": "Function",
                    "export_index": 7,
                    "status": "complete",
                    "confidence": "high",
                    "path": "graphs_from_uasset/SharedGraph_7.json",
                },
                {
                    "graph": "SharedGraph",
                    "graph_type": "Function",
                    "export_index": 8,
                    "status": "complete",
                    "confidence": "medium",
                    "path": "graphs_from_uasset/SharedGraph_8.json",
                },
            ],
        },
    )

    source_to_target = {
        "target_node": "TargetNode",
        "target_pin_id": "PIN-TARGET",
        "target_pin": "execute",
        "source": "fixture_link_reader",
        "confidence": "high",
        "resolution_status": "resolved_pin",
        "kind": "exec",
    }
    target_to_source = {
        "target_node": "SourceNode",
        "target_pin_id": "PIN-COLLISION",
        "target_pin": "then",
        "source": "fixture_link_reader",
        "confidence": "high",
        "resolution_status": "resolved_pin",
        "kind": "exec",
    }
    source_pin = _make_pin(
        "PIN-COLLISION",
        "then",
        "EGPD_Output",
        default="0.35",
        links=[source_to_target],
    )
    target_pin = _make_pin(
        "PIN-TARGET",
        "execute",
        "EGPD_Input",
        links=[target_to_source],
    )
    source_node = {
        "index": 1,
        "name": "SourceNode",
        "label": "Do Work",
        "class_name": "K2Node_CallFunction",
        "node_type": "K2Node_CallFunction",
        "function": "DoWork",
        "x": 100,
        "y": 200,
        "source": "uasset_binary",
        "confidence": "high",
        "properties": {
            "NodePosX": {
                "name": "NodePosX",
                "type": "IntProperty",
                "value": 100,
                "source": "uasset_property_tag",
                "confidence": "high",
                "raw_offsets": {"start": 1, "end": 9},
            },
            "FunctionReference": {
                "name": "FunctionReference",
                "type": "StructProperty",
                "member_name": "DoWork",
                "source": "uasset_property_tag",
                "confidence": "medium",
                "raw_offsets": {"start": 9, "end": 30},
            },
        },
        "pins": [source_pin],
    }
    target_node = {
        "index": 2,
        "name": "TargetNode",
        "label": "Target",
        "class_name": "K2Node_FunctionResult",
        "node_type": "K2Node_FunctionResult",
        "x": 400,
        "y": 200,
        "source": "uasset_binary",
        "confidence": "high",
        "properties": {},
        "pins": [target_pin],
    }
    graph_7_payload = {
        "metadata": {
            "asset_name": "Fixture_BP",
            "graph_name": "SharedGraph",
            "graph_type": "Function",
            "uasset_export_index": 7,
            "uasset_node_refs": [101, 102],
            "uasset_read_status": "complete",
            "confidence": "high",
            "node_count": 2,
            "pin_count": 2,
            "link_count": 2,
        },
        "nodes": [source_node, target_node],
        # These are deliberate legacy duplicates. The normalized store must not
        # insert them a second time.
        "pins": [
            {**copy.deepcopy(source_pin), "node_index": 1, "node_name": "SourceNode"},
            {**copy.deepcopy(target_pin), "node_index": 2, "node_name": "TargetNode"},
        ],
        "links": [
            {
                "source_node_index": 1,
                "source_node": "SourceNode",
                "source_pin_id": "PIN-COLLISION",
                "source_pin": "then",
                "source_pin_direction": "EGPD_Output",
                "target_node": "TargetNode",
                "target_pin_id": "PIN-TARGET",
                "target_pin": "execute",
                "link_source": "fixture_link_reader",
                "link_confidence": "high",
                "resolution_status": "resolved_pin",
            },
            {
                "source_node_index": 2,
                "source_node": "TargetNode",
                "source_pin_id": "PIN-TARGET",
                "source_pin": "execute",
                "source_pin_direction": "EGPD_Input",
                "target_node": "SourceNode",
                "target_pin_id": "PIN-COLLISION",
                "target_pin": "then",
                "link_source": "fixture_link_reader",
                "link_confidence": "high",
                "resolution_status": "resolved_pin",
            },
        ],
        "function_calls": [copy.deepcopy(source_node)],
        "events": [],
        "variable_gets": [],
        "variable_sets": [],
    }
    _write_json(graph_7_path, graph_7_payload)

    colliding_pin = _make_pin(
        "PIN-COLLISION",
        "Value",
        "EGPD_Output",
        default=42.5,
    )
    colliding_node = {
        "index": 1,
        "name": "SourceNode",
        "label": "Same names, different graph",
        "class_name": "K2Node_VariableGet",
        "node_type": "K2Node_VariableGet",
        "variable": "MaxSpeed",
        "source": "uasset_binary",
        "confidence": "medium",
        "properties": {},
        "pins": [colliding_pin],
    }
    _write_json(
        graph_8_path,
        {
            "metadata": {
                "asset_name": "Fixture_BP",
                "graph_name": "SharedGraph",
                "graph_type": "Function",
                "uasset_export_index": 8,
                "uasset_node_refs": [201],
                "uasset_read_status": "complete",
                "confidence": "medium",
                "node_count": 1,
                "pin_count": 1,
                "link_count": 0,
            },
            "nodes": [colliding_node],
            "pins": [{**copy.deepcopy(colliding_pin), "node_index": 1, "node_name": "SourceNode"}],
            "links": [],
            "variable_gets": [copy.deepcopy(colliding_node)],
        },
    )

    _write_json(
        asset_dir / "uasset_class_defaults.json",
        {
            "schema": "blueprint-translator.uasset-class-defaults.v1",
            "loaded": True,
            "asset_name": "Fixture_BP",
            "default_object": "Default__Fixture_BP_C",
            "variables": {
                "MaxSpeed": {
                    "value": 42.5,
                    "type": "FloatProperty",
                    "source": "uasset_cdo",
                    "confidence": "high",
                },
                "bEnabled": {
                    "value": True,
                    "type": "BoolProperty",
                    "source": "uasset_cdo",
                    "confidence": "high",
                },
            },
            # MaxSpeed appears in both legacy projections but is one canonical
            # default in v2. The property form carries the richer source data.
            "properties": [
                {
                    "name": "MaxSpeed",
                    "type": "FloatProperty",
                    "value": 42.5,
                    "source": "uasset_cdo_property_tag",
                    "confidence": "high",
                    "offset": 40,
                    "end": 52,
                },
                {
                    "name": "bEnabled",
                    "type": "BoolProperty",
                    "value": True,
                    "source": "uasset_cdo_property_tag",
                    "confidence": "high",
                    "offset": 52,
                    "end": 60,
                },
            ],
        },
    )

    return asset_dir, manifest_path, graph_7_path


@contextmanager
def _open_rows(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


class EvidenceWriterTests(unittest.TestCase):
    def test_writer_materializes_only_compact_bounded_search_entities(self):
        huge_marker = "RAW_DEFAULT_MARKER_" + ("X" * 20000)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset_dir, _manifest_path, _graph_7_path = _make_legacy_capture(root)
            defaults_path = asset_dir / "uasset_class_defaults.json"
            defaults = json.loads(defaults_path.read_text(encoding="utf-8"))
            defaults["properties"].append(
                {
                    "name": "HugeDefault",
                    "type": "StringProperty",
                    "value": huge_marker,
                    "source": "fixture",
                    "confidence": "high",
                }
            )
            _write_json(defaults_path, defaults)
            database_path = root / "evidence.sqlite"

            write_evidence_store_from_capture(asset_dir, database_path)

            with _open_rows(database_path) as connection:
                canonical_counts = {
                    "graph": connection.execute("SELECT COUNT(*) FROM graphs").fetchone()[0],
                    "node": connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
                    "pin": connection.execute("SELECT COUNT(*) FROM pins").fetchone()[0],
                    "default": connection.execute("SELECT COUNT(*) FROM class_defaults").fetchone()[0],
                }
                indexed_counts = {
                    str(row["kind"]): int(row["row_count"])
                    for row in connection.execute(
                        "SELECT kind, COUNT(*) AS row_count FROM search_entities GROUP BY kind"
                    )
                }
                materialized = {
                    str(row["kind"]): (int(row["row_count"]), int(row["is_complete"]))
                    for row in connection.execute(
                        "SELECT kind, row_count, is_complete FROM search_materialization"
                    )
                }
                projection = connection.execute(
                    "SELECT kind, name, summary, search_text FROM search_entities"
                ).fetchall()

        self.assertEqual(indexed_counts, canonical_counts)
        self.assertEqual(
            materialized,
            {kind: (count, 1) for kind, count in canonical_counts.items()},
        )
        self.assertEqual({str(row["kind"]) for row in projection}, set(canonical_counts))
        self.assertTrue(all(len(str(row["summary"])) <= 160 for row in projection))
        self.assertTrue(all(len(str(row["search_text"])) <= 384 for row in projection))
        copied_projection = "\n".join(
            f"{row['name']} {row['summary']} {row['search_text']}" for row in projection
        )
        self.assertNotIn("RAW_DEFAULT_MARKER_", copied_projection)

    def test_ambiguous_same_named_target_nodes_do_not_create_a_canonical_edge(self):
        graph_payload = {
            "metadata": {
                "asset_name": "AmbiguousTargetFixture",
                "graph_name": "EventGraph",
                "graph_type": "EventGraph",
                "uasset_export_index": 7,
                "uasset_read_status": "complete",
            },
            "nodes": [
                {
                    "index": 1,
                    "name": "Source",
                    "pins": [
                        _make_pin(
                            "SOURCE-PIN",
                            "then",
                            "EGPD_Output",
                            links=[
                                {
                                    "target_node": "DuplicateTarget",
                                    "target_pin": "execute",
                                    "kind": "exec",
                                }
                            ],
                        )
                    ],
                },
                {
                    "index": 2,
                    "name": "DuplicateTarget",
                    "pins": [_make_pin("TARGET-A", "execute", "EGPD_Input")],
                },
                {
                    "index": 3,
                    "name": "DuplicateTarget",
                    "pins": [_make_pin("TARGET-B", "execute", "EGPD_Input")],
                },
            ],
        }
        payload = {
            "asset_name": "AmbiguousTargetFixture",
            "asset_path": "/Game/Test/AmbiguousTargetFixture.AmbiguousTargetFixture",
            "graphs": [
                {
                    "graph": "EventGraph",
                    "graph_type": "EventGraph",
                    "export_index": 7,
                    "status": "complete",
                    "confidence": "high",
                    "payload": graph_payload,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            database_path = Path(tmp) / "evidence.sqlite"
            write_evidence_store_from_payload(
                str(payload["asset_path"]),
                None,
                payload,
                database_path,
            )
            with _open_rows(database_path) as connection:
                edge_count = connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
                observation = connection.execute(
                    "SELECT target_node_ref, target_pin_ref, resolution_status FROM edge_observations"
                ).fetchone()

        self.assertEqual(edge_count, 0)
        self.assertIsNone(observation["target_node_ref"])
        self.assertIsNone(observation["target_pin_ref"])
        self.assertEqual(observation["resolution_status"], "ambiguous")

    def test_ambiguous_same_named_target_pins_do_not_create_a_canonical_edge(self):
        graph_payload = {
            "metadata": {
                "asset_name": "AmbiguousPinFixture",
                "graph_name": "EventGraph",
                "graph_type": "EventGraph",
                "uasset_export_index": 7,
                "uasset_read_status": "complete",
            },
            "nodes": [
                {
                    "index": 1,
                    "name": "Source",
                    "pins": [
                        _make_pin(
                            "SOURCE-PIN",
                            "then",
                            "EGPD_Output",
                            links=[
                                {
                                    "target_node": "UniqueTarget",
                                    "target_pin": "execute",
                                    "kind": "exec",
                                }
                            ],
                        )
                    ],
                },
                {
                    "index": 2,
                    "name": "UniqueTarget",
                    "pins": [
                        _make_pin("TARGET-A", "execute", "EGPD_Input"),
                        _make_pin("TARGET-B", "execute", "EGPD_Input"),
                    ],
                },
            ],
        }
        payload = {
            "asset_name": "AmbiguousPinFixture",
            "asset_path": "/Game/Test/AmbiguousPinFixture.AmbiguousPinFixture",
            "graphs": [
                {
                    "graph": "EventGraph",
                    "graph_type": "EventGraph",
                    "export_index": 7,
                    "status": "complete",
                    "confidence": "high",
                    "payload": graph_payload,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            database_path = Path(tmp) / "evidence.sqlite"
            write_evidence_store_from_payload(
                str(payload["asset_path"]),
                None,
                payload,
                database_path,
            )
            with _open_rows(database_path) as connection:
                edge_count = connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
                observation = connection.execute(
                    "SELECT target_node_ref, target_pin_ref, resolution_status FROM edge_observations"
                ).fetchone()

        self.assertEqual(edge_count, 0)
        self.assertIsNotNone(observation["target_node_ref"])
        self.assertIsNone(observation["target_pin_ref"])
        self.assertEqual(observation["resolution_status"], "ambiguous")

    def test_unique_native_pin_id_disambiguates_same_named_target_nodes(self):
        graph_payload = {
            "metadata": {
                "asset_name": "NodeDisambiguationFixture",
                "graph_name": "EventGraph",
                "graph_type": "EventGraph",
                "uasset_export_index": 7,
                "uasset_read_status": "complete",
            },
            "nodes": [
                {
                    "index": 1,
                    "name": "Source",
                    "pins": [
                        _make_pin(
                            "SOURCE-PIN",
                            "then",
                            "EGPD_Output",
                            links=[
                                {
                                    "target_node": "DuplicateTarget",
                                    "target_pin_id": "TARGET-B",
                                    "target_pin": "execute",
                                    "kind": "exec",
                                }
                            ],
                        )
                    ],
                },
                {
                    "index": 2,
                    "name": "DuplicateTarget",
                    "pins": [_make_pin("TARGET-A", "execute", "EGPD_Input")],
                },
                {
                    "index": 3,
                    "name": "DuplicateTarget",
                    "pins": [_make_pin("TARGET-B", "execute", "EGPD_Input")],
                },
            ],
        }
        payload = {
            "asset_name": "NodeDisambiguationFixture",
            "asset_path": "/Game/Test/NodeDisambiguationFixture.NodeDisambiguationFixture",
            "graphs": [
                {
                    "graph": "EventGraph",
                    "graph_type": "EventGraph",
                    "export_index": 7,
                    "status": "complete",
                    "confidence": "high",
                    "payload": graph_payload,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            database_path = Path(tmp) / "evidence.sqlite"
            write_evidence_store_from_payload(
                str(payload["asset_path"]),
                None,
                payload,
                database_path,
            )
            with _open_rows(database_path) as connection:
                edge = connection.execute(
                    "SELECT target.native_pin_id AS target_native_pin_id, edges.resolution_status "
                    "FROM edges JOIN pins AS target ON target.pin_ref = edges.target_pin_ref"
                ).fetchone()

        self.assertIsNotNone(edge)
        self.assertEqual(edge["target_native_pin_id"], "TARGET-B")
        self.assertEqual(edge["resolution_status"], "resolved_pin")

    def test_unique_pin_name_disambiguates_colliding_native_ids_within_one_node(self):
        graph_payload = {
            "metadata": {
                "asset_name": "PinDisambiguationFixture",
                "graph_name": "EventGraph",
                "graph_type": "EventGraph",
                "uasset_export_index": 7,
                "uasset_read_status": "complete",
            },
            "nodes": [
                {
                    "index": 1,
                    "name": "Source",
                    "pins": [
                        _make_pin(
                            "SOURCE-PIN",
                            "then",
                            "EGPD_Output",
                            links=[
                                {
                                    "target_node": "UniqueTarget",
                                    "target_pin_id": "COLLIDING-ID",
                                    "target_pin": "execute B",
                                    "kind": "exec",
                                }
                            ],
                        )
                    ],
                },
                {
                    "index": 2,
                    "name": "UniqueTarget",
                    "pins": [
                        _make_pin("COLLIDING-ID", "execute A", "EGPD_Input"),
                        _make_pin("COLLIDING-ID", "execute B", "EGPD_Input"),
                    ],
                },
            ],
        }
        payload = {
            "asset_name": "PinDisambiguationFixture",
            "asset_path": "/Game/Test/PinDisambiguationFixture.PinDisambiguationFixture",
            "graphs": [
                {
                    "graph": "EventGraph",
                    "graph_type": "EventGraph",
                    "export_index": 7,
                    "status": "complete",
                    "confidence": "high",
                    "payload": graph_payload,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            database_path = Path(tmp) / "evidence.sqlite"
            write_evidence_store_from_payload(
                str(payload["asset_path"]),
                None,
                payload,
                database_path,
            )
            with _open_rows(database_path) as connection:
                edge = connection.execute(
                    "SELECT target.name AS target_name, edges.resolution_status "
                    "FROM edges JOIN pins AS target ON target.pin_ref = edges.target_pin_ref"
                ).fetchone()

        self.assertIsNotNone(edge)
        self.assertEqual(edge["target_name"], "execute B")
        self.assertEqual(edge["resolution_status"], "resolved_pin")

    def test_direct_diagnostic_uses_graph_export_index_when_names_collide(self):
        def graph(export_index: int, status: str) -> dict[str, object]:
            return {
                "graph": "SharedGraph",
                "graph_type": "Function",
                "export_index": export_index,
                "status": status,
                "confidence": "high",
                "failure_categories": [] if status == "complete" else ["missing_target_pin_id"],
                "payload": {
                    "metadata": {
                        "asset_name": "DuplicateGraphFixture",
                        "graph_name": "SharedGraph",
                        "graph_type": "Function",
                        "uasset_export_index": export_index,
                        "uasset_read_status": status,
                    },
                    "nodes": [],
                },
            }

        payload = {
            "asset_name": "DuplicateGraphFixture",
            "asset_path": "/Game/Test/DuplicateGraphFixture.DuplicateGraphFixture",
            "graphs": [graph(7, "complete"), graph(8, "partial")],
        }

        with tempfile.TemporaryDirectory() as tmp:
            database_path = Path(tmp) / "evidence.sqlite"
            write_evidence_store_from_payload(
                str(payload["asset_path"]),
                None,
                payload,
                database_path,
            )
            with _open_rows(database_path) as connection:
                diagnostic = connection.execute(
                    "SELECT scope_kind, scope_ref FROM diagnostics WHERE reason_code = ?",
                    ("missing_target_pin_id",),
                ).fetchone()

        self.assertEqual(diagnostic["scope_kind"], "graph")
        self.assertTrue(diagnostic["scope_ref"].endswith("/g/8"), diagnostic["scope_ref"])

    def test_name_only_diagnostic_does_not_silently_choose_a_same_named_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset_dir, _manifest_path, _graph_path = _make_legacy_capture(root)
            _write_json(
                asset_dir / "uasset_partial_graph_triage.json",
                {
                    "reason_meanings": {"duplicate_graph_scope": "Graph identity is ambiguous."},
                    "graphs": [
                        {
                            "graph": "SharedGraph",
                            "graph_type": "Function",
                            "reasons": ["duplicate_graph_scope"],
                        }
                    ],
                },
            )
            database_path = root / "evidence.sqlite"
            result = write_evidence_store_from_capture(asset_dir, database_path)
            with _open_rows(database_path) as connection:
                diagnostic = connection.execute(
                    "SELECT scope_kind, scope_ref FROM diagnostics WHERE reason_code = ?",
                    ("duplicate_graph_scope",),
                ).fetchone()

        self.assertEqual(diagnostic["scope_kind"], "asset")
        self.assertEqual(
            diagnostic["scope_ref"],
            f"bp://{result['asset_id']}@{result['revision_id']}",
        )

    def test_writer_revision_is_deterministic_and_changes_when_referenced_evidence_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset_dir, _manifest_path, graph_7_path = _make_legacy_capture(root)

            first = write_evidence_store_from_capture(asset_dir, root / "first.sqlite")
            second = write_evidence_store_from_capture(asset_dir, root / "second.sqlite")
            graph_payload = json.loads(graph_7_path.read_text(encoding="utf-8"))
            graph_payload["nodes"][0]["pins"][0]["default"] = "0.50"
            _write_json(graph_7_path, graph_payload)
            changed = write_evidence_store_from_capture(asset_dir, root / "changed.sqlite")

        self.assertEqual(first["asset_id"], second["asset_id"])
        self.assertEqual(first["revision_id"], second["revision_id"])
        self.assertEqual(first["source_fingerprint"], second["source_fingerprint"])
        self.assertNotEqual(first["revision_id"], changed["revision_id"])
        self.assertNotEqual(first["source_fingerprint"], changed["source_fingerprint"])

    def test_writer_normalizes_duplicate_node_and_pin_projections_and_composite_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset_dir, _manifest_path, _graph_7_path = _make_legacy_capture(root)
            database_path = root / "evidence.sqlite"

            result = write_evidence_store_from_capture(asset_dir, database_path)

            with _open_rows(database_path) as connection:
                counts = {
                    table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("graphs", "nodes", "pins")
                }
                same_named_graphs = connection.execute(
                    "SELECT graph_ref, export_index FROM graphs WHERE name = ? ORDER BY export_index",
                    ("SharedGraph",),
                ).fetchall()
                colliding_pins = connection.execute(
                    "SELECT pin_ref, node_ref, ordinal FROM pins WHERE native_pin_id = ? ORDER BY pin_ref",
                    ("PIN-COLLISION",),
                ).fetchall()

        self.assertEqual(result["database_path"], str(database_path))
        self.assertEqual(counts, {"graphs": 2, "nodes": 3, "pins": 3})
        self.assertEqual(len(same_named_graphs), 2)
        self.assertEqual([row["export_index"] for row in same_named_graphs], [7, 8])
        self.assertNotEqual(same_named_graphs[0]["graph_ref"], same_named_graphs[1]["graph_ref"])
        self.assertEqual(len(colliding_pins), 2)
        self.assertNotEqual(colliding_pins[0]["pin_ref"], colliding_pins[1]["pin_ref"])
        self.assertNotEqual(colliding_pins[0]["node_ref"], colliding_pins[1]["node_ref"])

    def test_writer_keeps_symmetric_link_observations_but_stores_one_canonical_edge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset_dir, _manifest_path, _graph_7_path = _make_legacy_capture(root)
            database_path = root / "evidence.sqlite"

            write_evidence_store_from_capture(asset_dir, database_path)

            with _open_rows(database_path) as connection:
                observations = connection.execute(
                    "SELECT source_pin_ref, target_pin_ref, resolution_status "
                    "FROM edge_observations ORDER BY observation_id"
                ).fetchall()
                edges = connection.execute(
                    "SELECT edges.source_pin_ref, edges.target_pin_ref, "
                    "source.direction AS source_direction, target.direction AS target_direction "
                    "FROM edges "
                    "JOIN pins AS source ON source.pin_ref = edges.source_pin_ref "
                    "JOIN pins AS target ON target.pin_ref = edges.target_pin_ref"
                ).fetchall()

        self.assertEqual(len(observations), 2)
        self.assertEqual(len(edges), 1)
        self.assertEqual({row["resolution_status"] for row in observations}, {"resolved_pin"})
        edge = edges[0]
        self.assertNotEqual(edge["source_pin_ref"], edge["target_pin_ref"])
        self.assertEqual(edge["source_direction"], "EGPD_Output")
        self.assertEqual(edge["target_direction"], "EGPD_Input")
        self.assertIn("/p/", edge["source_pin_ref"])
        self.assertIn("/p/", edge["target_pin_ref"])

    def test_writer_database_has_foreign_keys_integrity_properties_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset_dir, _manifest_path, _graph_7_path = _make_legacy_capture(root)
            database_path = root / "evidence.sqlite"

            result = write_evidence_store_from_capture(asset_dir, database_path)

            with _open_rows(database_path) as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
                pin_foreign_keys = connection.execute("PRAGMA foreign_key_list(pins)").fetchall()
                edge_foreign_keys = connection.execute("PRAGMA foreign_key_list(edges)").fetchall()
                node_position = connection.execute(
                    "SELECT owner_ref, type_name, value_json FROM properties WHERE name = ?",
                    ("NodePosX",),
                ).fetchone()
                defaults = connection.execute(
                    "SELECT name, type_name, value_json FROM class_defaults ORDER BY name"
                ).fetchall()
                revision = connection.execute(
                    "SELECT asset_id, revision_id, source_fingerprint FROM asset_revisions"
                ).fetchone()

        self.assertEqual(integrity, "ok")
        self.assertEqual(foreign_key_errors, [])
        self.assertGreaterEqual(len(pin_foreign_keys), 1)
        self.assertGreaterEqual(len(edge_foreign_keys), 2)
        self.assertTrue(node_position["owner_ref"].startswith("bp://"))
        self.assertEqual(node_position["type_name"], "IntProperty")
        self.assertEqual(json.loads(node_position["value_json"]), 100)
        self.assertEqual([row["name"] for row in defaults], ["MaxSpeed", "bEnabled"])
        self.assertEqual(json.loads(defaults[0]["value_json"]), 42.5)
        self.assertIs(json.loads(defaults[1]["value_json"]), True)
        self.assertEqual(revision["asset_id"], result["asset_id"])
        self.assertEqual(revision["revision_id"], result["revision_id"])
        self.assertEqual(revision["source_fingerprint"], result["source_fingerprint"])


if __name__ == "__main__":
    unittest.main()
