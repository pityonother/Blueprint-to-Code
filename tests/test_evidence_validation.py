import io
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_capture(root: Path, name: str = "ValidationFixture") -> Path:
    asset_dir = root / name
    graph_path = asset_dir / "graphs_from_uasset" / "EventGraph_7.json"
    graph_payload = {
        "metadata": {
            "asset_name": name,
            "graph_name": "EventGraph",
            "graph_type": "EventGraph",
            "uasset_export_index": 7,
            "uasset_read_status": "complete",
            "confidence": "high",
        },
        "nodes": [
            {
                "index": 1,
                "name": "K2Node_VariableGet_0",
                "variable": "ExactEnergy",
                "properties": [
                    {"name": "bPureGet", "type": "BoolProperty", "value": True}
                ],
                "pins": [
                    {
                        "id": "pin-energy-out",
                        "name": "Energy",
                        "direction": "EGPD_Output",
                        "category": "float",
                        "links": [
                            {
                                "target_node": "K2Node_CallFunction_0",
                                "target_package_index": 22,
                                "target_pin_id": "pin-amount-in",
                                "target_pin_id_candidates": [
                                    "pin-amount-in",
                                    "pin-fallback-in",
                                ],
                                "resolution_status": "resolved_pin",
                                "kind": "data",
                            }
                        ],
                    }
                ],
            },
            {
                "index": 2,
                "export_index": 22,
                "name": "K2Node_CallFunction_0",
                "function": "ExactConsumeEnergy",
                "pins": [
                    {
                        "id": "pin-amount-in",
                        "name": "Amount",
                        "direction": "EGPD_Input",
                        "category": "float",
                        "links": [],
                    },
                    {
                        "id": "pin-fallback-in",
                        "name": "FallbackAmount",
                        "direction": "EGPD_Input",
                        "category": "float",
                        "links": [],
                    },
                ],
            },
            {
                "index": 3,
                "name": "K2Node_Event_0",
                "event": "ExactOnEnergyChanged",
                "pins": [],
            },
        ],
        # This models the repeated legacy projections that v2 intentionally
        # does not persist.  It also makes the size-ratio assertion meaningful.
        "legacy_projection_padding": "x" * 500_000,
    }
    _write_json(graph_path, graph_payload)
    _write_json(
        asset_dir / "graphs_from_uasset_manifest.json",
        {
            "schema": "blueprint-translator.graphs-from-uasset-manifest.v1",
            "asset_name": name,
            "asset_path": f"/Game/Test/{name}.{name}",
            "files": [
                {
                    "graph": "EventGraph",
                    "graph_type": "EventGraph",
                    "export_index": 7,
                    "status": "complete",
                    "confidence": "high",
                    "path": "graphs_from_uasset/EventGraph_7.json",
                }
            ],
        },
    )
    _write_json(
        asset_dir / "uasset_class_defaults.json",
        {
            "variables": {
                "ExactMaxEnergy": {
                    "type": "FloatProperty",
                    "value": 100.0,
                }
            },
            "properties": [
                {
                    "name": "ExactRechargeRate",
                    "type": "FloatProperty",
                    "value": 2.5,
                }
            ],
        },
    )
    # A valid-looking but unmanifested graph must never affect validation.
    _write_json(
        asset_dir / "graphs_from_uasset" / "Stale_999.json",
        {"metadata": {"uasset_export_index": 999}, "nodes": [{"pins": []}]},
    )
    return asset_dir


def _migrate(asset_dir: Path) -> None:
    from blueprint_translator.evidence_writer import migrate_asset_capture

    migrate_asset_capture(asset_dir)


def _make_direct_capture(root: Path, name: str = "DirectValidationFixture") -> tuple[Path, Path]:
    from blueprint_translator.evidence_writer import write_evidence_artifacts_from_payload

    asset_dir = root / name
    uasset_path = root / "DevKit" / f"{name}.uasset"
    uasset_path.parent.mkdir(parents=True, exist_ok=True)
    uasset_path.write_bytes(b"current-devkit-binary\x00fixture")
    payload = {
        "asset_name": name,
        "asset_path": f"/Game/Test/{name}.{name}",
        "graphs": [
            {
                "graph": "EventGraph",
                "graph_type": "EventGraph",
                "export_index": 11,
                "status": "complete",
                "confidence": "high",
                "node_count": 1,
                "pin_count": 0,
                "link_count": 0,
                "coverage": {},
                "warnings": [],
                "payload": {
                    "metadata": {
                        "asset_name": name,
                        "graph_name": "EventGraph",
                        "graph_type": "EventGraph",
                        "uasset_export_index": 11,
                        "uasset_read_status": "complete",
                        "confidence": "high",
                    },
                    "nodes": [
                        {
                            "index": 1,
                            "name": "K2Node_CallFunction_0",
                            "function": "ExactDirectFunction",
                            "properties": [
                                {"name": "DirectProperty", "value": "kept"}
                            ],
                            "pins": [],
                        }
                    ],
                },
            }
        ],
        "class_defaults": {},
    }
    write_evidence_artifacts_from_payload(
        payload["asset_path"], uasset_path, payload, asset_dir
    )
    return asset_dir, uasset_path


def _make_link_disambiguation_capture(root: Path) -> Path:
    asset_dir = root / "LinkDisambiguationFixture"
    graph_path = asset_dir / "graphs_from_uasset" / "EventGraph_7.json"
    _write_json(
        graph_path,
        {
            "metadata": {
                "asset_name": asset_dir.name,
                "graph_name": "EventGraph",
                "graph_type": "EventGraph",
                "uasset_export_index": 7,
                "uasset_read_status": "complete",
                "confidence": "high",
            },
            "nodes": [
                {
                    "index": 1,
                    "package_index": 10,
                    "name": "SourceByNodePinId",
                    "pins": [
                        {
                            "id": "source-1",
                            "name": "Value",
                            "direction": "EGPD_Output",
                            "category": "float",
                            "links": [
                                {
                                    "target_node": "DuplicateTarget",
                                    "target_pin_id": "wanted-native-id",
                                    "target_pin_name": "TargetValue",
                                    "kind": "data",
                                }
                            ],
                        }
                    ],
                },
                {
                    "index": 2,
                    "package_index": 11,
                    "name": "SourceByPinName",
                    "pins": [
                        {
                            "id": "source-2",
                            "name": "Value",
                            "direction": "EGPD_Output",
                            "category": "float",
                            "links": [
                                {
                                    "target_package_index": 30,
                                    "target_node": "UniqueTarget",
                                    "target_pin_id": "duplicate-native-id",
                                    "target_pin_name": "RightPin",
                                    "kind": "data",
                                }
                            ],
                        }
                    ],
                },
                {
                    "index": 3,
                    "package_index": 20,
                    "name": "DuplicateTarget",
                    "pins": [
                        {
                            "id": "wrong-native-id",
                            "name": "TargetValue",
                            "direction": "EGPD_Input",
                            "category": "float",
                            "links": [],
                        }
                    ],
                },
                {
                    "index": 4,
                    "package_index": 21,
                    "name": "DuplicateTarget",
                    "pins": [
                        {
                            "id": "wanted-native-id",
                            "name": "TargetValue",
                            "direction": "EGPD_Input",
                            "category": "float",
                            "links": [],
                        }
                    ],
                },
                {
                    "index": 5,
                    "package_index": 30,
                    "name": "UniqueTarget",
                    "pins": [
                        {
                            "id": "duplicate-native-id",
                            "name": "WrongPin",
                            "direction": "EGPD_Input",
                            "category": "float",
                            "links": [],
                        },
                        {
                            "id": "duplicate-native-id",
                            "name": "RightPin",
                            "direction": "EGPD_Input",
                            "category": "float",
                            "links": [],
                        },
                    ],
                },
            ],
        },
    )
    _write_json(
        asset_dir / "graphs_from_uasset_manifest.json",
        {
            "schema": "blueprint-translator.graphs-from-uasset-manifest.v1",
            "asset_name": asset_dir.name,
            "asset_path": f"/Game/Test/{asset_dir.name}.{asset_dir.name}",
            "files": [
                {
                    "graph": "EventGraph",
                    "graph_type": "EventGraph",
                    "export_index": 7,
                    "status": "complete",
                    "confidence": "high",
                    "path": "graphs_from_uasset/EventGraph_7.json",
                }
            ],
        },
    )
    return asset_dir


class EvidenceValidationTests(unittest.TestCase):
    def test_index_only_validation_ignores_source_drift_but_checks_index_and_sqlite(self):
        from validate_evidence_store import validate_asset, validate_index_consistency

        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir, uasset_path = _make_direct_capture(Path(temp_dir))
            uasset_path.write_bytes(b"changed-after-capture")

            full_report = validate_asset(asset_dir)
            index_report = validate_index_consistency(asset_dir)

        self.assertFalse(full_report["ok"], full_report)
        self.assertTrue(index_report["ok"], index_report)
        self.assertTrue(index_report["checks"]["sqlite"]["ok"])
        self.assertTrue(index_report["checks"]["agentIndex"]["ok"])

    def test_agent_index_count_parser_supports_full_and_compact_cards(self):
        from validate_evidence_store import _agent_index_counts

        full = """\
- Graphs: 27
- Nodes: 620
- Pins: 1956
- Wires: 676
- Link observations: 1352 (confirmed=1352)
- Class defaults: 81
- Evidence gaps: 43; unresolved/heuristic link observations: 0
"""
        compact = """\
- Graphs=27; Nodes=620; Pins=1956; Wires=676; Link observations=1352
- Graph status: complete=27; Defaults=81; Gaps=43
"""
        expected = {
            "graphCount": 27,
            "nodeCount": 620,
            "pinCount": 1956,
            "wireCount": 676,
            "linkObservationCount": 1352,
            "defaultCount": 81,
            "gapCount": 43,
        }

        self.assertEqual(_agent_index_counts(full), expected)
        self.assertEqual(_agent_index_counts(compact), expected)

    def test_independent_oracle_reproduces_documented_node_and_pin_disambiguation(self):
        from validate_evidence_store import validate_asset

        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _make_link_disambiguation_capture(Path(temp_dir))
            _migrate(asset_dir)
            report = validate_asset(asset_dir)

        self.assertTrue(report["ok"], report)
        reconciliation = report["checks"]["legacyReconciliation"]
        self.assertEqual(reconciliation["expectedCounts"]["edge_observations"], 2)
        self.assertEqual(reconciliation["expectedCounts"]["edges"], 2)
        self.assertEqual(reconciliation["mismatches"], {})

    def test_validator_reconciles_manifest_sources_and_proves_exact_recall(self):
        from validate_evidence_store import validate_asset

        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _make_capture(Path(temp_dir))
            _migrate(asset_dir)

            report = validate_asset(
                asset_dir,
                benchmark=True,
                benchmark_iterations=2,
                max_search_p95_ms=10_000,
                max_two_hop_p95_ms=10_000,
            )

        self.assertTrue(report["ok"], report)
        reconciliation = report["checks"]["legacyReconciliation"]
        self.assertTrue(reconciliation["ok"])
        self.assertEqual(
            reconciliation["expectedCounts"],
            {
                "graphs": 1,
                "nodes": 3,
                "pins": 3,
                "edges": 1,
                "edge_observations": 1,
                "properties": 1,
                "class_defaults": 2,
                "references": 3,
                "edge_candidates": 2,
            },
        )
        self.assertEqual(reconciliation["mismatches"], {})
        self.assertEqual(report["source"]["manifestGraphCount"], 1)
        self.assertTrue(report["checks"]["sqlite"]["ok"])
        self.assertEqual(report["checks"]["sqlite"]["foreignKeyErrors"], [])
        self.assertTrue(report["checks"]["artifacts"]["ok"])
        self.assertLessEqual(report["checks"]["agentIndex"]["estimatedTokens"], 1500)
        self.assertLessEqual(report["checks"]["sizeRatio"]["ratio"], 0.5)
        self.assertEqual(report["checks"]["recall"]["requested"], 5)
        self.assertEqual(report["checks"]["recall"]["missing"], [])
        self.assertIn("searchP95Ms", report["checks"]["benchmark"])
        self.assertIn("twoHopP95Ms", report["checks"]["benchmark"])

    def test_validator_rejects_agent_index_gap_count_that_differs_from_query_contract(self):
        from validate_evidence_store import validate_asset

        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _make_capture(Path(temp_dir))
            defaults_path = asset_dir / "uasset_class_defaults.json"
            defaults = json.loads(defaults_path.read_text(encoding="utf-8"))
            defaults.setdefault("properties", []).append(
                {
                    "name": "UnparsedItems",
                    "type": "ArrayProperty",
                    "value": [],
                    "array_parse": {"parsed": False, "count": 0},
                }
            )
            _write_json(defaults_path, defaults)
            _migrate(asset_dir)

            valid = validate_asset(asset_dir)
            self.assertTrue(valid["ok"], valid)
            index_path = asset_dir / "output" / "agent_index.md"
            index_text = index_path.read_text(encoding="utf-8")
            tampered = re.sub(
                r"(?im)^(-\s*Evidence gaps:\s*)\d+(?=;|\s*$)",
                r"\g<1>0",
                index_text,
                count=1,
            )
            self.assertNotEqual(tampered, index_text)
            index_path.write_text(tampered, encoding="utf-8")

            invalid = validate_asset(asset_dir)

        self.assertFalse(invalid["ok"], invalid)
        self.assertFalse(invalid["checks"]["agentIndex"]["ok"])
        self.assertEqual(invalid["checks"]["agentIndex"]["indexCounts"]["gapCount"], 0)
        self.assertEqual(invalid["checks"]["agentIndex"]["queryCounts"]["gapCount"], 2)
        self.assertTrue(
            any("gapCount" in error for error in invalid["checks"]["agentIndex"]["errors"]),
            invalid["checks"]["agentIndex"],
        )

    def test_per_asset_size_is_report_only_and_aggregate_gate_uses_all_legacy_bytes(self):
        from validate_evidence_store import _aggregate_size_check, validate_asset

        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _make_capture(Path(temp_dir))
            _migrate(asset_dir)
            report = validate_asset(asset_dir, max_size_ratio=0.0)

        self.assertTrue(report["ok"], report)
        self.assertTrue(report["checks"]["sizeRatio"]["ok"])
        self.assertFalse(report["checks"]["sizeRatio"]["withinMaxRatio"])

        aggregate = _aggregate_size_check(
            [
                {
                    "source": {"mode": "legacy"},
                    "checks": {
                        "sizeRatio": {"legacyGraphJsonBytes": 100, "v2Bytes": 70}
                    },
                },
                {
                    "source": {"mode": "legacy"},
                    "checks": {
                        "sizeRatio": {"legacyGraphJsonBytes": 100, "v2Bytes": 10}
                    },
                },
                {
                    "source": {"mode": "legacy"},
                    "checks": {
                        "sizeRatio": {"legacyGraphJsonBytes": 0, "v2Bytes": 20}
                    },
                },
                {
                    "source": {"mode": "direct"},
                    "checks": {
                        "sizeRatio": {"legacyGraphJsonBytes": 0, "v2Bytes": 999}
                    },
                },
            ],
            0.5,
        )

        self.assertTrue(aggregate["ok"], aggregate)
        self.assertEqual(aggregate["legacyAssetCount"], 3)
        self.assertEqual(aggregate["directAssetCount"], 1)
        self.assertEqual(aggregate["zeroDenominatorAssetCount"], 1)
        self.assertEqual(aggregate["legacyGraphJsonBytes"], 200)
        self.assertEqual(aggregate["v2Bytes"], 100)
        self.assertEqual(aggregate["ratio"], 0.5)
        self.assertFalse(aggregate["target40Met"])

    def test_direct_asset_validates_current_parser_and_current_devkit_binary_hash(self):
        from blueprint_translator.evidence_writer import DIRECT_PAYLOAD_PARSER_VERSION
        from validate_evidence_store import discover_asset_dirs, validate_asset

        with tempfile.TemporaryDirectory() as temp_dir:
            capture_root = Path(temp_dir) / "captures"
            asset_dir, uasset_path = _make_direct_capture(capture_root)

            self.assertEqual(discover_asset_dirs(capture_root), [asset_dir.resolve()])
            current = validate_asset(asset_dir)
            uasset_path.write_bytes(b"changed-after-evidence-was-written")
            stale = validate_asset(asset_dir)

        self.assertTrue(current["ok"], current)
        self.assertEqual(current["source"]["mode"], "direct")
        self.assertEqual(current["identity"]["parserVersion"], DIRECT_PAYLOAD_PARSER_VERSION)
        self.assertTrue(current["checks"]["sourceManifest"]["ok"])
        self.assertFalse(stale["ok"])
        self.assertFalse(stale["checks"]["sourceManifest"]["ok"])
        self.assertTrue(
            any("sha256" in error for error in stale["checks"]["sourceManifest"]["errors"]),
            stale,
        )

    def test_parser_version_must_match_the_current_legacy_constant(self):
        from blueprint_translator.evidence_schema import LEGACY_CAPTURE_PARSER_VERSION
        from validate_evidence_store import validate_asset

        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _make_capture(Path(temp_dir))
            _migrate(asset_dir)
            database_path = asset_dir / "evidence" / "evidence.sqlite"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    "UPDATE asset_revisions SET parser_version = ?",
                    (LEGACY_CAPTURE_PARSER_VERSION + "-obsolete",),
                )
                connection.commit()
            report = validate_asset(asset_dir)

        self.assertFalse(report["ok"])
        self.assertFalse(report["checks"]["versions"]["ok"])
        self.assertTrue(any("parser_version" in item for item in report["checks"]["versions"]["errors"]))

    def test_benchmark_uses_a_real_two_hop_traversal_request(self):
        import validate_evidence_store as validator

        requests: list[dict[str, object]] = []

        class FakeService:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def query(self, request):
                requests.append(dict(request))
                return {"items": []}

        with patch.object(
            validator.EvidenceQueryService, "open", return_value=FakeService()
        ):
            result = validator._benchmark_check(
                Path("unused.sqlite"),
                iterations=2,
                search_name="ExactDirectFunction",
                node_ref="bp://asset@revision/g/1/n/1",
                max_search_p95_ms=10_000,
                max_two_hop_p95_ms=10_000,
            )

        self.assertTrue(result["ok"], result)
        neighborhood_requests = [
            request for request in requests if request.get("operation") == "neighborhood"
        ]
        self.assertTrue(neighborhood_requests)
        for request in neighborhood_requests:
            self.assertNotIn("maxHops", request)
            self.assertEqual(request["traversal"]["maxHops"], 2)

    def test_validator_reports_association_mismatch_and_stale_v2_file(self):
        from validate_evidence_store import validate_asset

        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _make_capture(Path(temp_dir))
            _migrate(asset_dir)
            database_path = asset_dir / "evidence" / "evidence.sqlite"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("DELETE FROM properties")
                connection.commit()
            stale_path = asset_dir / "evidence" / ".evidence.sqlite.abandoned.tmp"
            stale_path.write_bytes(b"stale")
            (asset_dir / "evidence" / "abandoned-stage").mkdir()

            report = validate_asset(asset_dir)

        self.assertFalse(report["ok"])
        self.assertIn("properties", report["checks"]["legacyReconciliation"]["mismatches"])
        self.assertIn(
            ".evidence.sqlite.abandoned.tmp",
            report["checks"]["artifacts"]["unexpectedEvidenceFiles"],
        )
        self.assertIn(
            "abandoned-stage/",
            report["checks"]["artifacts"]["unexpectedEvidenceEntries"],
        )
        self.assertTrue(any("legacyReconciliation" in item for item in report["hardFailures"]))
        self.assertTrue(any("artifacts" in item for item in report["hardFailures"]))

    def test_cli_all_discovers_assets_and_returns_nonzero_on_hard_failure(self):
        from validate_evidence_store import main

        with tempfile.TemporaryDirectory() as temp_dir:
            capture_root = Path(temp_dir) / "captures"
            asset_dir = _make_capture(capture_root)
            _migrate(asset_dir)
            direct_dir, _uasset_path = _make_direct_capture(capture_root)

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                success_code = main(
                    [
                        "--capture-root",
                        str(capture_root),
                        "--all",
                        "--expected-asset-count",
                        "2",
                    ]
                )
            success_payload = json.loads(stdout.getvalue())
            self.assertEqual(success_code, 0, stderr.getvalue())
            self.assertTrue(success_payload["ok"])
            self.assertEqual(success_payload["assetCount"], 2)
            self.assertEqual(
                {Path(item["assetDir"]).name for item in success_payload["reports"]},
                {asset_dir.name, direct_dir.name},
            )
            self.assertTrue(success_payload["checks"]["assetCount"]["ok"])

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                wrong_count_code = main(
                    [
                        "--capture-root",
                        str(capture_root),
                        "--all",
                        "--expected-asset-count",
                        "3",
                    ]
                )
            wrong_count_payload = json.loads(stdout.getvalue())
            self.assertNotEqual(wrong_count_code, 0)
            self.assertFalse(wrong_count_payload["checks"]["assetCount"]["ok"])
            self.assertEqual(wrong_count_payload["checks"]["assetCount"]["actual"], 2)

            (asset_dir / "output" / ".agent_index.md.leftover.tmp").write_text(
                "stale", encoding="utf-8"
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                failure_code = main(["--asset-dir", str(asset_dir)])
            failure_payload = json.loads(stdout.getvalue())

        self.assertNotEqual(failure_code, 0)
        self.assertFalse(failure_payload["ok"])

    def test_cli_single_asset_reports_but_does_not_enforce_full_aggregate_size_gate(self):
        from validate_evidence_store import main

        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _make_capture(Path(temp_dir) / "captures")
            _migrate(asset_dir)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = main(
                    [
                        "--asset-dir",
                        str(asset_dir),
                        "--max-size-ratio",
                        "0",
                    ]
                )
            payload = json.loads(stdout.getvalue())

        self.assertEqual(result, 0, payload)
        self.assertTrue(payload["ok"], payload)
        self.assertFalse(payload["checks"]["aggregateSize"]["enforced"])
        self.assertFalse(payload["checks"]["aggregateSize"]["withinMaxRatio"])
        self.assertTrue(payload["checks"]["aggregateSize"]["ok"])


if __name__ == "__main__":
    unittest.main()
