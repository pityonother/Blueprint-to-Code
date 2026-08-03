from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.evidence_repository import (  # noqa: E402
    open_asset_repository,
    open_bound_evidence_database,
    resolve_asset_evidence_state,
)
from blueprint_translator.evidence_query import EvidenceQueryService  # noqa: E402
from blueprint_translator.evidence_writer import (  # noqa: E402
    migrate_asset_capture,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _pin(
    pin_id: str,
    name: str,
    direction: str,
    *,
    links: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "id": pin_id,
        "persistent_guid": pin_id,
        "name": name,
        "direction": direction,
        "category": "exec",
        "subcategory": "",
        "default": "",
        "default_object": "",
        "links": links or [],
        "source": "fixture_pin_reader",
        "confidence": "high",
        "raw_offsets": {},
    }


def _node(
    package_index: int,
    name: str,
    node_type: str,
    *,
    function: str = "",
    event: str = "",
    pins: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    node_pins = pins or []
    return {
        "index": package_index,
        "package_index": package_index,
        "export_index": package_index - 1,
        "name": name,
        "label": function or event or name,
        "class_name": node_type,
        "node_type": node_type,
        "function": function,
        "event": event,
        "source": "uasset_binary",
        "confidence": "high",
        "properties": {},
        "pins": node_pins,
    }


def _write_legacy_capture(
    root: Path,
    *,
    revision_marker: str,
    target_function: str = "ApplyEffect",
) -> Path:
    asset_dir = root / "Fixture_BP"
    graph_path = asset_dir / "graphs_from_uasset" / "MainGraph_7.json"

    link = {
        "target_node": "ApplyEffectNode",
        "target_pin_id": "P_APPLY_EXEC",
        "target_pin": "execute",
        "source": "fixture_link_reader",
        "confidence": "high",
        "resolution_status": "resolved_pin",
        "kind": "exec",
    }
    begin_play = _node(
        101,
        "BeginPlay",
        "K2Node_Event",
        event="ReceiveBeginPlay",
        pins=[_pin("P_BEGIN_THEN", "then", "EGPD_Output", links=[link])],
    )
    apply_effect = _node(
        102,
        "ApplyEffectNode",
        "K2Node_CallFunction",
        function=target_function,
        pins=[_pin("P_APPLY_EXEC", "execute", "EGPD_Input")],
    )
    graph_payload = {
        "metadata": {
            "generated": revision_marker,
            "asset_name": "Fixture_BP",
            "graph_name": "MainGraph",
            "graph_type": "EventGraph",
            "uasset_export_index": 7,
            "uasset_read_status": "partial",
            "confidence": "medium",
            "node_count": 2,
            "pin_count": 2,
            "link_count": 1,
        },
        "nodes": [begin_play, apply_effect],
        "pins": [],
        "links": [],
        "diagnostics": {},
    }
    _write_json(graph_path, graph_payload)

    _write_json(
        asset_dir / "graphs_from_uasset_manifest.json",
        {
            "schema": "blueprint-translator.graphs-from-uasset-manifest.v1",
            "generated": revision_marker,
            "asset_name": "Fixture_BP",
            "asset_path": "/Game/Test/Fixture_BP.Fixture_BP",
            "source_graph_count": 1,
            "graph_file_count": 1,
            "files": [
                {
                    "graph": "MainGraph",
                    "graph_type": "EventGraph",
                    "export_index": 7,
                    "status": "partial",
                    "confidence": "medium",
                    "path": "graphs_from_uasset/MainGraph_7.json",
                }
            ],
        },
    )
    _write_json(
        asset_dir / "uasset_graph_nodes.json",
        {
            "schema": "blueprint-translator.uasset-graph-nodes.v1",
            "generated": revision_marker,
            "asset_path": "/Game/Test/Fixture_BP.Fixture_BP",
            "asset_name": "Fixture_BP",
            "graph_count": 1,
            "node_count": 2,
            "pin_count": 2,
            "link_count": 1,
            "graphs": [],
        },
    )
    _write_json(
        asset_dir / "uasset_class_defaults.json",
        {
            "schema": "blueprint-translator.uasset-class-defaults.v1",
            "generated": revision_marker,
            "loaded": True,
            "asset_name": "Fixture_BP",
            "variables": {
                "EffectCooldown": {
                    "value": 0.5,
                    "type": "FloatProperty",
                    "source": "uasset_cdo",
                    "confidence": "high",
                }
            },
            "properties": [],
        },
    )
    _write_json(
        asset_dir / "uasset_partial_graph_triage.json",
        {
            "schema": "blueprint-translator.uasset-partial-graph-triage.v1",
            "generated": revision_marker,
            "asset_path": "/Game/Test/Fixture_BP.Fixture_BP",
            "asset_name": "Fixture_BP",
            "partial_graph_count": 1,
            "reason_counts": {"missing_target_pin_id": 1},
            "reason_meanings": {
                "missing_target_pin_id": "The target pin was not recovered."
            },
            "graphs": [
                {
                    "graph": "MainGraph",
                    "graph_type": "EventGraph",
                    "status": "partial",
                    "confidence": "medium",
                    "primary_reason": "missing_target_pin_id",
                    "reasons": ["missing_target_pin_id"],
                    "next_action": "Capture the full graph from the DevKit clipboard.",
                    "warnings": [],
                }
            ],
        },
    )
    return asset_dir


def _asset_snapshot(asset_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(asset_dir).as_posix(): path.read_bytes()
        for path in sorted(asset_dir.rglob("*"))
        if path.is_file()
    }


def _find_node_ref(repository: object, query: str, name: str) -> str:
    result = repository.query(  # type: ignore[attr-defined]
        {
            "operation": "search",
            "query": query,
            "kinds": ["node"],
            "pageSize": 20,
            "budgetTokens": 1600,
        }
    )
    matches = [item for item in result["items"] if item.get("name") == name]
    if not matches:
        raise AssertionError(f"missing node {name!r} in {result['items']!r}")
    return str(matches[0]["ref"])


def _replace_with_lionfish_scale_gaps(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        asset_id, revision_id = connection.execute(
            "SELECT asset_id, revision_id FROM asset_revisions LIMIT 1"
        ).fetchone()
        scope_ref = f"bp://{asset_id}@{revision_id}"
        connection.execute("DELETE FROM diagnostics")
        connection.execute("DELETE FROM edge_observations")
        rows = []
        for index in range(26_461):
            if index < 26_450:
                status, reason = "NOT_RECOVERED", "lionfish_bulk_gap"
            elif index < 26_460:
                status, reason = "SOURCE_NOT_AVAILABLE", "native_body_not_available"
            else:
                status, reason = "AMBIGUOUS", "rare_ambiguous_link"
            rows.append(
                (
                    f"{scope_ref}/diagnostic/synthetic-{index:05d}",
                    revision_id,
                    "asset",
                    scope_ref,
                    status,
                    reason,
                    "warning",
                    f"Synthetic gap {index}",
                    f"Synthetic gap detail {index}",
                    "Inspect the missing source evidence.",
                    "[]",
                    "{}",
                )
            )
        connection.executemany(
            "INSERT INTO diagnostics("
            "diagnostic_ref, revision_id, scope_kind, scope_ref, status, reason_code, "
            "severity, title, detail, next_probe, evidence_json, raw_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()


class EvidenceRepositoryContractTests(unittest.TestCase):
    def test_repository_open_rejects_sidecars_created_after_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for suffix in ("-wal", "-shm", "-journal"):
                with self.subTest(sidecar=suffix):
                    asset_dir = _write_legacy_capture(
                        root / suffix.removeprefix("-"),
                        revision_marker=f"sidecar-race-{suffix}",
                    )
                    migrate_asset_capture(asset_dir)

                    from blueprint_translator import evidence_repository as repository_module

                    real_resolve = repository_module.resolve_asset_evidence_state

                    def resolve_then_add_sidecar(*args: object, **kwargs: object):
                        state = real_resolve(*args, **kwargs)
                        state.database_path.with_name(
                            state.database_path.name + suffix
                        ).write_bytes(b"injected-sidecar")
                        return state

                    with (
                        patch.object(
                            repository_module,
                            "resolve_asset_evidence_state",
                            side_effect=resolve_then_add_sidecar,
                        ),
                        self.assertRaisesRegex(ValueError, "sidecar is forbidden"),
                    ):
                        open_asset_repository(asset_dir)

    def test_stable_binding_rejects_a_hardlink_created_while_hashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.bin"
            linked = root / "linked.bin"
            source.write_bytes(b"immutable-evidence")

            from blueprint_translator import evidence_repository as repository_module

            real_read = repository_module.os.read
            linked_once = False

            def read_then_link(descriptor: int, size: int) -> bytes:
                nonlocal linked_once
                chunk = real_read(descriptor, size)
                if chunk and not linked_once:
                    try:
                        linked.hardlink_to(source)
                    except OSError as error:
                        self.skipTest(f"hardlink creation is unavailable: {error}")
                    linked_once = True
                return chunk

            with (
                patch.object(
                    repository_module.os,
                    "read",
                    side_effect=read_then_link,
                ),
                self.assertRaisesRegex(ValueError, "plain regular file"),
            ):
                repository_module._stable_file_binding(source, label="test artifact")

    def test_v2_resolver_rejects_database_swap_during_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset_dir = _write_legacy_capture(
                root / "generation-a",
                revision_marker="resolver-race-a",
            )
            replacement_dir = _write_legacy_capture(
                root / "generation-b",
                revision_marker="resolver-race-b",
            )
            migrate_asset_capture(asset_dir, publish_v3=False)
            migrate_asset_capture(replacement_dir, publish_v3=False)
            database_path = asset_dir / "evidence" / "evidence.sqlite"
            replacement_database = replacement_dir / "evidence" / "evidence.sqlite"

            from blueprint_translator import evidence_repository as repository_module

            real_projection = repository_module._database_projection

            def project_then_swap(
                path: Path,
                **kwargs: object,
            ) -> dict[str, object]:
                projection = real_projection(path, **kwargs)
                shutil.copyfile(replacement_database, database_path)
                return projection

            with (
                patch.object(
                    repository_module,
                    "_database_projection",
                    side_effect=project_then_swap,
                ),
                self.assertRaisesRegex(
                    ValueError,
                    "DATABASE_HASH_MISMATCH|changed while they were validated",
                ),
            ):
                resolve_asset_evidence_state(asset_dir)

    def test_bound_database_helper_is_query_only_and_rechecks_after_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset_dir = _write_legacy_capture(
                Path(tmp),
                revision_marker="bound-read",
            )
            migrate_asset_capture(asset_dir)
            state = resolve_asset_evidence_state(asset_dir)

            with (
                patch(
                    "blueprint_translator.evidence_repository._read_bound_file_bytes",
                    return_value=b"drift",
                ),
                self.assertRaisesRegex(ValueError, "size drifted"),
                open_bound_evidence_database(state) as connection,
            ):
                self.assertEqual(
                    connection.execute("PRAGMA query_only").fetchone()[0],
                    1,
                )

    def test_query_connection_deserializes_bound_bytes_despite_path_aba(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_asset = _write_legacy_capture(
                root / "generation-a",
                revision_marker="aba-a",
                target_function="BoundFunctionA",
            )
            second_asset = _write_legacy_capture(
                root / "generation-b",
                revision_marker="aba-b",
                target_function="ReplacementFunctionB",
            )
            migrate_asset_capture(first_asset)
            migrate_asset_capture(second_asset)
            first_state = resolve_asset_evidence_state(first_asset)
            replacement = resolve_asset_evidence_state(second_asset).database_path.read_bytes()
            original = first_state.database_path.read_bytes()
            real_connect = sqlite3.connect
            connect_targets: list[str] = []

            def swap_path_while_connecting(*args: object, **kwargs: object):
                connect_targets.append(str(args[0]))
                first_state.database_path.write_bytes(replacement)
                try:
                    return real_connect(*args, **kwargs)
                finally:
                    first_state.database_path.write_bytes(original)

            with patch(
                "blueprint_translator.bound_database.sqlite3.connect",
                side_effect=swap_path_while_connecting,
            ):
                with EvidenceQueryService.open(
                    first_state.database_path,
                    expected_sha256=first_state.database_sha256,
                    expected_size=first_state.database_bytes,
                ) as service:
                    functions = {
                        str(row[0])
                        for row in service._connection.execute(  # noqa: SLF001
                            "SELECT function_name FROM nodes WHERE function_name <> ''"
                        )
                    }

            self.assertEqual(connect_targets, [":memory:"])
            self.assertIn("BoundFunctionA", functions)
            self.assertNotIn("ReplacementFunctionB", functions)

    def test_gap_summary_reports_lionfish_scale_omissions_and_every_reason_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset_dir = _write_legacy_capture(root, revision_marker="lionfish-scale")
            database_path = asset_dir / "evidence" / "evidence.sqlite"
            migrate_asset_capture(asset_dir, publish_v3=False)
            _replace_with_lionfish_scale_gaps(database_path)
            manifest_path = asset_dir / "evidence" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["counts"]["edge_observations"] = 0
            manifest["counts"]["diagnostics"] = 26_461
            _write_json(manifest_path, manifest)

            with open_asset_repository(asset_dir) as repository:
                summary = repository.gap_summary(limit=200, example_limit=2)

        self.assertEqual(summary["total"], 26_461)
        self.assertEqual(summary["returned"], 200)
        self.assertEqual(summary["omitted"], 26_261)
        self.assertTrue(summary["truncated"])
        self.assertEqual(
            summary["by_status"],
            {
                "AMBIGUOUS": 1,
                "NOT_RECOVERED": 26_450,
                "SOURCE_NOT_AVAILABLE": 10,
            },
        )
        self.assertEqual(
            summary["by_reason"],
            {
                "lionfish_bulk_gap": 26_450,
                "native_body_not_available": 10,
                "rare_ambiguous_link": 1,
            },
        )
        groups = {
            (row["status"], row["reason_code"]): row
            for row in summary["groups"]
        }
        self.assertEqual(groups[("AMBIGUOUS", "rare_ambiguous_link")]["count"], 1)
        self.assertEqual(
            len(groups[("NOT_RECOVERED", "lionfish_bulk_gap")]["examples"]),
            2,
        )

    def test_v2_database_is_preferred_even_when_legacy_files_change_after_indexing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset_dir = _write_legacy_capture(root, revision_marker="revision-a")
            migrate_asset_capture(asset_dir, publish_v3=False)

            # The indexed revision says ApplyEffect. The legacy capture is now
            # deliberately newer and says LegacyOnlyNewName. A repository that
            # incorrectly rebuilds from legacy will return the wrong result.
            _write_legacy_capture(
                root,
                revision_marker="revision-b",
                target_function="LegacyOnlyNewName",
            )

            with open_asset_repository(asset_dir) as repository:
                overview = repository.query(
                    {"operation": "overview", "budgetTokens": 800}
                )
                indexed = repository.query(
                    {
                        "operation": "search",
                        "query": "ApplyEffect",
                        "kinds": ["node"],
                        "budgetTokens": 1200,
                    }
                )
                changed_legacy = repository.query(
                    {
                        "operation": "search",
                        "query": "LegacyOnlyNewName",
                        "kinds": ["node"],
                        "budgetTokens": 1200,
                    }
                )

        self.assertEqual(overview["summary"]["graphCount"], 1)
        self.assertIn("ApplyEffectNode", {item["name"] for item in indexed["items"]})
        self.assertEqual(changed_legacy["items"], [])

    def test_legacy_fallback_supports_all_queries_without_writing_the_asset_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset_dir = _write_legacy_capture(
                Path(tmp),
                revision_marker="legacy-only",
            )
            before = _asset_snapshot(asset_dir)

            repository = open_asset_repository(
                asset_dir,
                allow_legacy_fallback=True,
            )
            try:
                overview = repository.query(
                    {"operation": "overview", "budgetTokens": 800}
                )
                search = repository.query(
                    {
                        "operation": "search",
                        "query": "ApplyEffect",
                        "kinds": ["node"],
                        "budgetTokens": 1200,
                    }
                )
                node_ref = _find_node_ref(
                    repository,
                    query="ApplyEffect",
                    name="ApplyEffectNode",
                )
                entity = repository.query(
                    {
                        "operation": "entity",
                        "selector": {"ref": node_ref},
                        "budgetTokens": 1200,
                    }
                )
                neighborhood = repository.query(
                    {
                        "operation": "neighborhood",
                        "selector": {"ref": node_ref},
                        "traversal": {
                            "maxHops": 1,
                            "direction": "both",
                            "edgeKinds": ["exec"],
                        },
                        "budgetTokens": 1800,
                    }
                )
                trace = repository.query(
                    {
                        "operation": "trace",
                        "selector": {"ref": node_ref},
                        "traversal": {
                            "maxHops": 1,
                            "direction": "upstream",
                            "edgeKinds": ["exec"],
                        },
                        "budgetTokens": 1800,
                    }
                )
                gaps = repository.query(
                    {"operation": "gaps", "budgetTokens": 1200}
                )
                defaults = repository.default_summaries(include_values=False)
            finally:
                repository.close()

            after = _asset_snapshot(asset_dir)

        self.assertEqual(overview["asset"]["objectPath"], "/Game/Test/Fixture_BP.Fixture_BP")
        self.assertTrue(search["items"])
        self.assertEqual(entity["items"][0]["ref"], node_ref)
        self.assertTrue(neighborhood["items"])
        self.assertIn(
            "BeginPlay",
            {item["node"]["name"] for item in trace["items"]},
        )
        self.assertIn(
            "missing_target_pin_id",
            {item.get("reasonCode") for item in gaps["items"]},
        )
        self.assertEqual(defaults[0]["name"], "EffectCooldown")
        self.assertEqual(defaults[0]["valueStatus"], "CONFIRMED")
        self.assertTrue(defaults[0]["valueUsable"])
        self.assertNotIn("value", defaults[0])
        self.assertEqual(after, before)

    def test_reference_from_previous_revision_is_rejected_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset_dir = _write_legacy_capture(root, revision_marker="revision-a")
            migrate_asset_capture(asset_dir, publish_v3=False)

            with open_asset_repository(asset_dir) as old_repository:
                old_ref = _find_node_ref(
                    old_repository,
                    query="ApplyEffect",
                    name="ApplyEffectNode",
                )

            _write_legacy_capture(root, revision_marker="revision-b")
            migrate_asset_capture(asset_dir, publish_v3=False)

            with open_asset_repository(asset_dir) as new_repository:
                with self.assertRaisesRegex(ValueError, "STALE_REVISION"):
                    new_repository.query(
                        {
                            "operation": "entity",
                            "selector": {"ref": old_ref},
                            "budgetTokens": 1200,
                        }
                    )

    def test_asset_without_v2_or_legacy_evidence_fails_with_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_asset_dir = Path(tmp) / "Empty_BP"
            empty_asset_dir.mkdir()

            with self.assertRaisesRegex(
                (FileNotFoundError, ValueError),
                "(?i)(NO_EVIDENCE|no evidence|evidence.*not found)",
            ):
                open_asset_repository(empty_asset_dir)


if __name__ == "__main__":
    unittest.main()
