import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.uasset_graphs import write_uasset_graph_read_files  # noqa: E402


ASSET_PATH = "/Game/Test/ModeFixture.ModeFixture"


def _graph_payload() -> dict[str, object]:
    return {
        "metadata": {
            "asset_name": "ModeFixture",
            "graph_name": "EventGraph",
            "graph_type": "EventGraph",
            "uasset_export_index": 7,
            "uasset_node_refs": [21],
            "uasset_read_status": "complete",
            "confidence": "high",
            "node_count": 1,
            "pin_count": 1,
            "link_count": 0,
        },
        "nodes": [
            {
                "index": 1,
                "export_index": 20,
                "package_index": 21,
                "name": "K2Node_Event_0",
                "label": "Begin Play",
                "class_name": "K2Node_Event",
                "node_type": "K2Node_Event",
                "event": "ReceiveBeginPlay",
                "source": "uasset_binary",
                "confidence": "high",
                "properties": {},
                "pins": [
                    {
                        "id": "pin-then",
                        "persistent_guid": "pin-then",
                        "name": "then",
                        "direction": "EGPD_Output",
                        "category": "exec",
                        "subcategory": "",
                        "default": "",
                        "default_object": "",
                        "links": [],
                        "source": "fixture_pin_reader",
                        "confidence": "high",
                    }
                ],
            }
        ],
        "pins": [],
        "links": [],
        "events": [],
        "function_calls": [],
        "variable_gets": [],
        "variable_sets": [],
    }


def _reader_payload(uasset_path: Path) -> dict[str, object]:
    graph_payload = _graph_payload()
    return {
        "schema": "blueprint-translator.uasset-graph-read.v1",
        "generated": "2026-07-11T10:00:00",
        "asset_path": ASSET_PATH,
        "asset_name": "ModeFixture",
        "uasset_path": str(uasset_path),
        "uexp_path": "",
        "loaded": True,
        "package": {
            "schema": "blueprint-translator.uasset-package.v1",
            "summary": {"package_name": "/Game/Test/ModeFixture"},
            "uasset_path": str(uasset_path),
            "uexp_path": "",
            "warnings": [],
        },
        "exports": [],
        "structure": {},
        "class_defaults": {
            "schema": "blueprint-translator.uasset-class-defaults.v1",
            "loaded": True,
            "asset_name": "ModeFixture",
            "variables": {},
            "properties": [],
        },
        "graph_count": 1,
        "node_count": 1,
        "pin_count": 1,
        "link_count": 0,
        "status_counts": {"complete": 1},
        "confidence_counts": {"high": 1},
        "failure_category_counts": {},
        "node_class_counts": [{"class": "K2Node_Event", "count": 1}],
        "properties": [],
        "unknown_properties": [],
        "pin_links": {
            "schema": "blueprint-translator.uasset-pin-links.v1",
            "summary": {"link_count": 0},
            "graphs": [],
        },
        "graphs": [
            {
                "graph": "EventGraph",
                "graph_type": "EventGraph",
                "export_index": 7,
                "status": "complete",
                "confidence": "high",
                "failure_categories": [],
                "node_refs": [21],
                "node_count": 1,
                "pin_count": 1,
                "link_count": 0,
                "coverage": {},
                "nodes": [],
                "properties": {},
                "warnings": [],
                "payload": graph_payload,
            }
        ],
        "warnings": [],
    }


def _make_source(root: Path) -> tuple[Path, dict[str, object]]:
    uasset_path = root / "source" / "ModeFixture.uasset"
    uasset_path.parent.mkdir(parents=True, exist_ok=True)
    uasset_path.write_bytes(b"mode-fixture-uasset")
    return uasset_path, _reader_payload(uasset_path)


def _v2_paths(asset_dir: Path) -> tuple[Path, Path, Path]:
    return (
        asset_dir / "evidence" / "evidence.sqlite",
        asset_dir / "evidence" / "manifest.json",
        asset_dir / "output" / "agent_index.md",
    )


def _legacy_paths(asset_dir: Path) -> tuple[Path, Path, Path]:
    return (
        asset_dir / "uasset_package.json",
        asset_dir / "graphs_from_uasset_manifest.json",
        asset_dir / "graphs_from_uasset" / "EventGraph_7.json",
    )


class ArtifactModeNormalizationTests(unittest.TestCase):
    def test_normalizer_supports_three_modes_and_defaults_to_indexed_after_cutover(self):
        from blueprint_translator.artifact_modes import normalize_artifact_mode

        self.assertEqual(normalize_artifact_mode(), "indexed")
        self.assertEqual(normalize_artifact_mode(None), "indexed")
        for mode in ("legacy", "dual", "indexed"):
            with self.subTest(mode=mode):
                self.assertEqual(normalize_artifact_mode(mode), mode)

    def test_migration_cli_defaults_to_indexed(self):
        from migrate_capture_evidence import parse_args

        args = parse_args(["--asset-dir", "capture"])

        self.assertEqual(args.artifact_mode, "indexed")

    def test_normalizer_rejects_an_unknown_mode(self):
        from blueprint_translator.artifact_modes import normalize_artifact_mode

        with self.assertRaisesRegex(ValueError, "artifact.mode|legacy|dual|indexed"):
            normalize_artifact_mode("delete-legacy")


class DirectEvidenceArtifactWriterTests(unittest.TestCase):
    def test_direct_revision_changes_when_only_persisted_graph_coverage_or_warnings_change(self):
        from blueprint_translator.artifact_modes import write_evidence_artifacts_from_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            uasset_path, payload = _make_source(root)

            baseline = write_evidence_artifacts_from_payload(
                ASSET_PATH,
                uasset_path,
                copy.deepcopy(payload),
                root / "baseline",
            )

            coverage_payload = copy.deepcopy(payload)
            coverage_payload["graphs"][0]["coverage"] = {"pinsRecovered": 1, "pinsExpected": 2}
            coverage_changed = write_evidence_artifacts_from_payload(
                ASSET_PATH,
                uasset_path,
                coverage_payload,
                root / "coverage-changed",
            )

            warnings_payload = copy.deepcopy(payload)
            warnings_payload["graphs"][0]["warnings"] = ["persisted graph warning"]
            warnings_changed = write_evidence_artifacts_from_payload(
                ASSET_PATH,
                uasset_path,
                warnings_payload,
                root / "warnings-changed",
            )

        self.assertNotEqual(baseline["revision_id"], coverage_changed["revision_id"])
        self.assertNotEqual(baseline["source_fingerprint"], coverage_changed["source_fingerprint"])
        self.assertNotEqual(baseline["revision_id"], warnings_changed["revision_id"])
        self.assertNotEqual(baseline["source_fingerprint"], warnings_changed["source_fingerprint"])

    def test_direct_writer_builds_v2_from_memory_without_legacy_graph_json(self):
        from blueprint_translator.artifact_modes import write_evidence_artifacts_from_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            uasset_path, payload = _make_source(root)
            asset_dir = root / "capture" / "ModeFixture"

            result = write_evidence_artifacts_from_payload(
                ASSET_PATH,
                uasset_path,
                copy.deepcopy(payload),
                asset_dir,
            )

            database_path, manifest_path, agent_index_path = _v2_paths(asset_dir)
            connection = sqlite3.connect(database_path)
            try:
                counts = {
                    table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("graphs", "nodes", "pins")
                }
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                connection.close()

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            agent_index = agent_index_path.read_text(encoding="utf-8")

            self.assertEqual(Path(str(result["database_path"])), database_path)
            self.assertEqual(Path(str(result["manifest_path"])), manifest_path)
            self.assertEqual(Path(str(result["agent_index_path"])), agent_index_path)
            self.assertEqual(counts, {"graphs": 1, "nodes": 1, "pins": 1})
            self.assertEqual(integrity, "ok")
            self.assertEqual(manifest["revision_id"], result["revision_id"])
            self.assertIn("EventGraph", agent_index)
            self.assertFalse((asset_dir / "graphs_from_uasset").exists())
            self.assertFalse((asset_dir / "graphs_from_uasset_manifest.json").exists())

    def test_direct_writer_neither_reads_nor_removes_preexisting_legacy_files(self):
        from blueprint_translator.artifact_modes import write_evidence_artifacts_from_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            uasset_path, payload = _make_source(root)
            asset_dir = root / "capture" / "ModeFixture"
            legacy_graph = asset_dir / "graphs_from_uasset" / "keep-me.json"
            legacy_manifest = asset_dir / "graphs_from_uasset_manifest.json"
            legacy_graph.parent.mkdir(parents=True, exist_ok=True)
            legacy_graph.write_bytes(b"legacy-graph-sentinel")
            legacy_manifest.write_bytes(b"not-json-and-must-not-be-read-or-rewritten")

            write_evidence_artifacts_from_payload(
                ASSET_PATH,
                uasset_path,
                copy.deepcopy(payload),
                asset_dir,
            )

            self.assertEqual(legacy_graph.read_bytes(), b"legacy-graph-sentinel")
            self.assertEqual(
                legacy_manifest.read_bytes(),
                b"not-json-and-must-not-be-read-or-rewritten",
            )
            for path in _v2_paths(asset_dir):
                self.assertTrue(path.is_file(), path)


class UAssetGraphWriterModeTests(unittest.TestCase):
    def test_omitted_mode_uses_indexed_and_preserves_preexisting_legacy_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _uasset_path, payload = _make_source(root)
            capture_root = root / "capture"
            asset_dir = capture_root / "ModeFixture"
            legacy_package = asset_dir / "uasset_package.json"
            legacy_graph = asset_dir / "graphs_from_uasset" / "keep-me.json"
            legacy_package.parent.mkdir(parents=True, exist_ok=True)
            legacy_graph.parent.mkdir(parents=True, exist_ok=True)
            legacy_package.write_bytes(b"legacy-package-sentinel")
            legacy_graph.write_bytes(b"legacy-graph-sentinel")

            result = write_uasset_graph_read_files(
                ASSET_PATH,
                capture_root,
                copy.deepcopy(payload),
            )

            self.assertEqual(result["artifact_mode"], "indexed")
            self.assertEqual(legacy_package.read_bytes(), b"legacy-package-sentinel")
            self.assertEqual(legacy_graph.read_bytes(), b"legacy-graph-sentinel")
            for path in _v2_paths(asset_dir):
                self.assertTrue(path.is_file(), path)

    def test_prune_refuses_without_indexed_evidence_and_only_removes_known_legacy_paths(self):
        from blueprint_translator.asset import prune_legacy_uasset_outputs
        from blueprint_translator.artifact_modes import write_evidence_artifacts_from_payload

        with tempfile.TemporaryDirectory() as tmp:
            asset_dir = Path(tmp) / "capture" / "ModeFixture"
            legacy_package = asset_dir / "uasset_package.json"
            legacy_graph = asset_dir / "graphs_from_uasset" / "old.json"
            unrelated = asset_dir / "notes.md"
            legacy_graph.parent.mkdir(parents=True, exist_ok=True)
            legacy_package.write_bytes(b"legacy-package")
            legacy_graph.write_bytes(b"legacy-graph")
            unrelated.write_bytes(b"keep-unrelated")

            with self.assertRaisesRegex(ValueError, "before indexed evidence exists"):
                prune_legacy_uasset_outputs(asset_dir)

            self.assertEqual(legacy_package.read_bytes(), b"legacy-package")
            self.assertEqual(legacy_graph.read_bytes(), b"legacy-graph")

            uasset_path, payload = _make_source(Path(tmp))
            write_evidence_artifacts_from_payload(
                ASSET_PATH,
                uasset_path,
                copy.deepcopy(payload),
                asset_dir,
            )
            database_path = asset_dir / "evidence" / "evidence.sqlite"
            removed = prune_legacy_uasset_outputs(asset_dir)

            self.assertIn("uasset_package.json", removed)
            self.assertIn("graphs_from_uasset/", removed)
            self.assertFalse(legacy_package.exists())
            self.assertFalse(legacy_graph.parent.exists())
            self.assertTrue(database_path.is_file())
            self.assertEqual(unrelated.read_bytes(), b"keep-unrelated")

    def test_prune_rejects_debug_graph_limit_before_resolving_or_writing(self):
        from blueprint_translator import asset as asset_module

        args = Namespace(
            asset_binary=ASSET_PATH,
            uasset_max_graphs=1,
            prune_legacy=True,
            artifact_mode="indexed",
        )
        with mock.patch.object(asset_module, "object_path_to_uasset_path") as resolver:
            result = asset_module.run_asset_binary_translate(args)

        self.assertEqual(result, 2)
        resolver.assert_not_called()

    def test_prune_preserves_legacy_when_any_evidence_guard_is_invalid(self):
        from blueprint_translator.asset import prune_legacy_uasset_outputs
        from blueprint_translator.artifact_modes import write_evidence_artifacts_from_payload

        cases = {
            "invalid_manifest": "valid evidence manifest",
            "index_revision_mismatch": "different revision",
            "foreign_key_violation": "integrity validation failed",
            "manifest_count_mismatch": "counts do not match",
        }
        for case, expected_error in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                uasset_path, payload = _make_source(root)
                asset_dir = root / "capture" / "ModeFixture"
                legacy_package = asset_dir / "uasset_package.json"
                legacy_graph = asset_dir / "graphs_from_uasset" / "old.json"
                legacy_graph.parent.mkdir(parents=True, exist_ok=True)
                legacy_package.write_bytes(b"legacy-package-sentinel")
                legacy_graph.write_bytes(b"legacy-graph-sentinel")
                write_evidence_artifacts_from_payload(
                    ASSET_PATH,
                    uasset_path,
                    copy.deepcopy(payload),
                    asset_dir,
                )

                database_path, manifest_path, agent_index_path = _v2_paths(asset_dir)
                if case == "invalid_manifest":
                    manifest_path.write_text("{", encoding="utf-8")
                elif case == "index_revision_mismatch":
                    agent_index_path.write_text("# stale index\n", encoding="utf-8")
                elif case == "foreign_key_violation":
                    connection = sqlite3.connect(database_path)
                    try:
                        connection.execute("PRAGMA foreign_keys = OFF")
                        connection.execute(
                            "UPDATE pins SET node_ref = 'bp://missing/node'"
                        )
                        connection.commit()
                    finally:
                        connection.close()
                elif case == "manifest_count_mismatch":
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest["counts"]["graphs"] += 1
                    manifest_path.write_text(
                        json.dumps(manifest, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

                with self.assertRaisesRegex(ValueError, expected_error):
                    prune_legacy_uasset_outputs(asset_dir)

                self.assertEqual(legacy_package.read_bytes(), b"legacy-package-sentinel")
                self.assertEqual(legacy_graph.read_bytes(), b"legacy-graph-sentinel")

    def test_main_writer_has_legacy_dual_and_indexed_output_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            uasset_path, payload = _make_source(root)
            expectations = {
                "legacy": (True, False),
                "dual": (True, True),
                "indexed": (False, True),
            }

            for mode, (expect_legacy, expect_v2) in expectations.items():
                with self.subTest(mode=mode):
                    capture_root = root / f"capture-{mode}"
                    write_uasset_graph_read_files(
                        ASSET_PATH,
                        capture_root,
                        copy.deepcopy(payload),
                        artifact_mode=mode,
                    )
                    asset_dir = capture_root / "ModeFixture"

                    for path in _legacy_paths(asset_dir):
                        self.assertEqual(path.is_file(), expect_legacy, path)
                    for path in _v2_paths(asset_dir):
                        self.assertEqual(path.is_file(), expect_v2, path)

    def test_indexed_mode_does_not_implicitly_prune_or_rewrite_legacy_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _uasset_path, payload = _make_source(root)
            capture_root = root / "capture"
            asset_dir = capture_root / "ModeFixture"
            legacy_package = asset_dir / "uasset_package.json"
            legacy_graph = asset_dir / "graphs_from_uasset" / "old-large-graph.json"
            legacy_package.parent.mkdir(parents=True, exist_ok=True)
            legacy_graph.parent.mkdir(parents=True, exist_ok=True)
            legacy_package.write_bytes(b"legacy-package-sentinel")
            legacy_graph.write_bytes(b"legacy-graph-sentinel")

            write_uasset_graph_read_files(
                ASSET_PATH,
                capture_root,
                copy.deepcopy(payload),
                artifact_mode="indexed",
            )

            self.assertEqual(legacy_package.read_bytes(), b"legacy-package-sentinel")
            self.assertEqual(legacy_graph.read_bytes(), b"legacy-graph-sentinel")
            for path in _v2_paths(asset_dir):
                self.assertTrue(path.is_file(), path)


if __name__ == "__main__":
    unittest.main()
