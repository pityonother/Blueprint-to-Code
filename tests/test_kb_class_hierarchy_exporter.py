from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))


class _FakeClass:
    def __init__(
        self,
        path: str,
        *,
        super_class: "_FakeClass | None" = None,
        interfaces: tuple["_FakeClass", ...] = (),
    ) -> None:
        self._path = path
        self._super_class = super_class
        self._interfaces = interfaces

    def get_path_name(self) -> str:
        return self._path

    def get_super_class(self) -> "_FakeClass | None":
        return self._super_class

    def get_interfaces(self) -> tuple["_FakeClass", ...]:
        return self._interfaces


class KnowledgeClassHierarchyExporterTests(unittest.TestCase):
    def _exporter(self):
        from devkit_exporters import export_kb_class_hierarchy_snapshot

        return export_kb_class_hierarchy_snapshot

    def test_normalizes_wrapped_unreal_class_references(self):
        exporter = self._exporter()
        self.assertEqual(
            exporter._normalize_class_path(
                "BlueprintGeneratedClass'/Game/Test/BP_Test.BP_Test_C'"
            ),
            "/Game/Test/BP_Test.BP_Test_C",
        )
        self.assertEqual(
            exporter._normalize_class_path("Class'/Script/Engine.ActorComponent'"),
            "/Script/Engine.ActorComponent",
        )
        self.assertEqual(exporter._normalize_class_path("Function_42"), "")

    def test_collects_asset_generated_parent_native_and_interface_classes(self):
        exporter = self._exporter()
        paths = exporter._class_paths_from_asset_record(
            {
                "asset_class_path": "/Script/Engine.Blueprint",
                "tags": {
                    "GeneratedClass": (
                        "BlueprintGeneratedClass'/Game/Test/BP_Test.BP_Test_C'"
                    ),
                    "ParentClass": "Class'/Script/Engine.Actor'",
                    "NativeParentClass": ("Class'/Script/ShooterGame.PrimalCharacter'"),
                    "ImplementedInterfaces": (
                        "(Interface=Class'/Game/Test/BPI_Test.BPI_Test_C')"
                    ),
                    "ComponentClass": ("Class'/Script/Engine.ActorComponent'"),
                },
            }
        )
        self.assertEqual(
            paths,
            {
                "/Game/Test/BPI_Test.BPI_Test_C",
                "/Game/Test/BP_Test.BP_Test_C",
                "/Script/Engine.Actor",
                "/Script/Engine.ActorComponent",
                "/Script/Engine.Blueprint",
                "/Script/ShooterGame.PrimalCharacter",
            },
        )

    def test_interface_tag_parser_excludes_wrapper_graph_and_graph_owner(self):
        exporter = self._exporter()
        tag = (
            '(Interface="/Script/Engine.BlueprintGeneratedClass'
            "'/Game/Test/BPI_Test.BPI_Test_C'\", "
            'Graphs=("/Script/Engine.EdGraph'
            "'/Game/Test/BP_Owner.BP_Owner:EventGraph'\"))"
        )

        paths, complete = exporter._implemented_interface_paths(tag)

        self.assertTrue(complete)
        self.assertEqual(paths, {"/Game/Test/BPI_Test.BPI_Test_C"})
        record_paths = exporter._class_paths_from_asset_record(
            {
                "asset_class_path": "/Script/Engine.Blueprint",
                "tags": {"ImplementedInterfaces": tag},
            }
        )
        self.assertIn("/Game/Test/BPI_Test.BPI_Test_C", record_paths)
        self.assertNotIn(
            "/Script/Engine.BlueprintGeneratedClass",
            record_paths,
        )
        self.assertNotIn("/Script/Engine.EdGraph", record_paths)
        self.assertNotIn("/Game/Test/BP_Owner.BP_Owner", record_paths)

    def test_malformed_interface_tag_is_not_confirmed_complete(self):
        exporter = self._exporter()
        paths, complete = exporter._implemented_interface_paths(
            "(Interface=corrupt,Graphs=())"
        )
        self.assertEqual(paths, set())
        self.assertFalse(complete)

    def test_undocumented_runtime_methods_remain_explicitly_partial(self):
        exporter = self._exporter()
        interface = _FakeClass("/Game/Test/BPI_Test.BPI_Test_C")
        actor = _FakeClass("/Script/Engine.Actor")
        child = _FakeClass(
            "/Game/Test/BP_Test.BP_Test_C",
            super_class=actor,
            interfaces=(interface,),
        )

        row = exporter._reflect_class_row(
            "/Game/Test/BP_Test.BP_Test_C",
            lambda _path: child,
        )

        self.assertEqual(row["class_path"], "/Game/Test/BP_Test.BP_Test_C")
        self.assertEqual(row["super_class_path"], "/Script/Engine.Actor")
        self.assertEqual(row["interfaces"], ["/Game/Test/BPI_Test.BPI_Test_C"])
        self.assertFalse(row["is_native"])
        self.assertEqual(
            row["parent_status"],
            "RECOVERED_UNVERIFIED_RUNTIME_API",
        )
        self.assertEqual(
            row["interfaces_status"],
            "RECOVERED_UNVERIFIED_RUNTIME_API",
        )
        self.assertEqual(row["status"], "PARTIAL")
        self.assertEqual(row["confidence"], "MEDIUM")

    def test_registry_parent_and_interface_evidence_can_confirm_row(self):
        exporter = self._exporter()

        row = exporter._reflect_class_row(
            "/Game/Test/BP_Test.BP_Test_C",
            lambda _path: None,
            parent_hint="/Script/Engine.Actor",
            parent_hint_source="asset_registry_class_ancestry",
            interface_hints=("/Game/Test/BPI_Test.BPI_Test_C",),
            interfaces_complete=True,
        )

        self.assertEqual(row["super_class_path"], "/Script/Engine.Actor")
        self.assertEqual(row["interfaces"], ["/Game/Test/BPI_Test.BPI_Test_C"])
        self.assertEqual(row["parent_status"], "CONFIRMED")
        self.assertEqual(row["interfaces_status"], "CONFIRMED")
        self.assertEqual(row["status"], "CONFIRMED")
        self.assertEqual(row["confidence"], "HIGH")

    def test_complete_registry_evidence_skips_live_class_loader(self):
        exporter = self._exporter()
        loader_calls = []

        def loader(class_path):
            loader_calls.append(class_path)
            raise AssertionError("fully confirmed rows must not load a class")

        row = exporter._reflect_class_row(
            "/Game/Test/BP_Test.BP_Test_C",
            loader,
            parent_hint="/Script/Engine.Actor",
            parent_hint_source="asset_registry_class_ancestry",
            interface_hints=("/Game/Test/BPI_Test.BPI_Test_C",),
            interfaces_complete=True,
        )

        self.assertEqual(loader_calls, [])
        self.assertEqual(row["parent_status"], "CONFIRMED")
        self.assertEqual(row["interfaces_status"], "CONFIRMED")
        self.assertEqual(row["status"], "CONFIRMED")
        self.assertEqual(row["confidence"], "HIGH")

    def test_partial_registry_evidence_queries_only_the_missing_side(self):
        exporter = self._exporter()
        actor = _FakeClass("/Script/Engine.Actor")
        interface = _FakeClass("/Game/Test/BPI_Runtime.BPI_Runtime_C")

        class TrackingClass:
            def __init__(self):
                self.calls = []

            def get_super_class(self):
                self.calls.append("get_super_class")
                return actor

            def get_interfaces(self):
                self.calls.append("get_interfaces")
                return (interface,)

        missing_interfaces = TrackingClass()
        row = exporter._reflect_class_row(
            "/Game/Test/BP_ParentKnown.BP_ParentKnown_C",
            lambda _path: missing_interfaces,
            parent_hint="/Script/Engine.Actor",
            parent_hint_source="asset_registry_class_ancestry",
        )
        self.assertEqual(missing_interfaces.calls, ["get_interfaces"])
        self.assertEqual(row["parent_status"], "CONFIRMED")
        self.assertEqual(
            row["interfaces_status"],
            "RECOVERED_UNVERIFIED_RUNTIME_API",
        )

        missing_parent = TrackingClass()
        row = exporter._reflect_class_row(
            "/Game/Test/BP_InterfacesKnown.BP_InterfacesKnown_C",
            lambda _path: missing_parent,
            interface_hints=("/Game/Test/BPI_Tag.BPI_Tag_C",),
            interfaces_complete=True,
        )
        self.assertEqual(missing_parent.calls, ["get_super_class"])
        self.assertEqual(
            row["parent_status"],
            "RECOVERED_UNVERIFIED_RUNTIME_API",
        )
        self.assertEqual(row["interfaces_status"], "CONFIRMED")
        self.assertEqual(
            row["interfaces"],
            ["/Game/Test/BPI_Tag.BPI_Tag_C"],
        )

    def test_missing_interface_api_never_confirms_empty_interfaces(self):
        exporter = self._exporter()
        actor = _FakeClass("/Script/Engine.Actor")

        class ParentOnlyClass:
            def get_path_name(self):
                return "/Game/Test/BP_ParentOnly.BP_ParentOnly_C"

            def get_super_class(self):
                return actor

        row = exporter._reflect_class_row(
            "/Game/Test/BP_ParentOnly.BP_ParentOnly_C",
            lambda _path: ParentOnlyClass(),
        )

        self.assertEqual(row["super_class_path"], "/Script/Engine.Actor")
        self.assertEqual(row["interfaces"], [])
        self.assertEqual(row["interfaces_status"], "NOT_RECOVERED")
        self.assertEqual(row["status"], "PARTIAL")

    def test_confirmed_interface_tag_is_not_polluted_by_runtime_fallback(self):
        exporter = self._exporter()
        tagged = _FakeClass("/Game/Test/BPI_Tagged.BPI_Tagged_C")
        runtime_only = _FakeClass("/Game/Test/BPI_RuntimeOnly.BPI_RuntimeOnly_C")
        actor = _FakeClass("/Script/Engine.Actor")
        child = _FakeClass(
            "/Game/Test/BP_Test.BP_Test_C",
            super_class=actor,
            interfaces=(runtime_only,),
        )

        row = exporter._reflect_class_row(
            "/Game/Test/BP_Test.BP_Test_C",
            lambda _path: child,
            parent_hint="/Script/Engine.Actor",
            interface_hints=(tagged.get_path_name(),),
            interfaces_complete=True,
        )

        self.assertEqual(row["interfaces"], [tagged.get_path_name()])
        self.assertEqual(row["interfaces_status"], "CONFIRMED")
        self.assertNotIn("get_interfaces", row["source"])

    def test_expands_registry_seeds_with_reported_native_ancestors(self):
        exporter = self._exporter()
        paths = exporter._expand_ancestor_class_paths(
            {"/Game/Test/BP_Test.BP_Test_C"},
            lambda _path: (
                "/Script/Engine.Actor",
                "/Script/CoreUObject.Object",
            ),
        )
        self.assertEqual(
            paths,
            {
                "/Game/Test/BP_Test.BP_Test_C",
                "/Script/CoreUObject.Object",
                "/Script/Engine.Actor",
            },
        )
        self.assertEqual(
            exporter._expand_ancestor_class_paths(
                {"/Script/Engine.Actor"},
                lambda _path: "/Script/CoreUObject.Object",
            ),
            {
                "/Script/CoreUObject.Object",
                "/Script/Engine.Actor",
            },
        )

    def test_ancestor_query_uses_top_level_asset_path_and_decodes_result(self):
        exporter = self._exporter()

        class FakeTopLevelAssetPath:
            def __init__(self, package_name, asset_name):
                self.package_name = package_name
                self.asset_name = asset_name

        class FakeUnreal:
            TopLevelAssetPath = FakeTopLevelAssetPath

        seen = []

        def ancestors(argument):
            seen.append(argument)
            return [
                FakeTopLevelAssetPath("/Script/Engine", "Actor"),
                FakeTopLevelAssetPath("/Script/CoreUObject", "Object"),
            ]

        with mock.patch.object(exporter, "unreal", FakeUnreal):
            values = exporter._ancestor_paths(
                "/Game/Test/BP_Test.BP_Test_C",
                ancestors,
            )

        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], FakeTopLevelAssetPath)
        self.assertEqual(seen[0].package_name, "/Game/Test/BP_Test")
        self.assertEqual(seen[0].asset_name, "BP_Test_C")
        self.assertEqual(
            values,
            {
                "/Script/CoreUObject.Object",
                "/Script/Engine.Actor",
            },
        )

    def test_registry_parent_inference_requires_complete_ancestor_candidates(self):
        exporter = self._exporter()
        child = "/Game/Test/BP_Child.BP_Child_C"
        parent = "/Script/Engine.Actor"
        grandparent = "/Script/CoreUObject.Object"

        complete_map = {
            child: (parent, grandparent),
            parent: (grandparent,),
            grandparent: (),
        }
        parents, ambiguous, complete = exporter._registry_parent_map(
            complete_map,
            lambda value: complete_map[value],
        )
        self.assertEqual(parents[child], parent)
        self.assertFalse(ambiguous)
        self.assertTrue(complete)

        def incomplete_ancestors(value):
            if value == parent:
                raise RuntimeError("simulated missing ancestor record")
            return complete_map[value]

        parents, ambiguous, complete = exporter._registry_parent_map(
            complete_map,
            incomplete_ancestors,
        )
        self.assertNotIn(child, parents)
        self.assertIn(child, ambiguous)
        self.assertFalse(complete)

        parents, _ambiguous, complete = exporter._registry_parent_map(
            {child},
            lambda _value: None,
        )
        self.assertEqual(parents, {})
        self.assertFalse(complete)

    def test_explicit_seed_inventory_covers_component_and_interface_classes(self):
        exporter = self._exporter()
        with tempfile.TemporaryDirectory() as temporary:
            seed_path = Path(temporary) / "class-seeds.json"
            payload = json.dumps(
                {
                    "component_class": ("/Script/ShooterGame.PrimalInventoryComponent"),
                    "interface_class": ("/Game/Test/BPI_Inventory.BPI_Inventory_C"),
                }
            )
            seed_path.write_text(payload, encoding="utf-8")

            with mock.patch.dict(
                exporter.os.environ,
                {"BTC_KB_CLASS_HIERARCHY_SEED_FILE": str(seed_path)},
            ):
                paths, sha256 = exporter._seed_inventory()

        self.assertEqual(
            paths,
            {
                "/Game/Test/BPI_Inventory.BPI_Inventory_C",
                "/Script/ShooterGame.PrimalInventoryComponent",
            },
        )
        self.assertEqual(
            sha256,
            exporter.hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        )

    def test_registry_none_or_empty_collection_fails_closed(self):
        exporter = self._exporter()

        class FakeRegistry:
            def __init__(self, assets):
                self.assets = assets

            def get_all_assets(self, _on_disk=True):
                return self.assets

        class FakeHelpers:
            registry = None

            @classmethod
            def get_asset_registry(cls):
                return cls.registry

        class FakeUnreal:
            AssetRegistryHelpers = FakeHelpers

        for assets in (None, []):
            with self.subTest(assets=assets):
                FakeHelpers.registry = FakeRegistry(assets)
                with mock.patch.object(exporter, "unreal", FakeUnreal):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "no asset collection|empty asset collection",
                    ):
                        exporter._registry_class_inventory()

    def test_inventory_signature_changes_when_relationship_tags_reparent(self):
        exporter = self._exporter()
        shared = {
            "schema": "ark.kb.class-hierarchy-inventory.v1",
            "runtime": {
                "engine_version": "5.5.4",
                "game_name": "ShooterGame",
                "devkit_build_id": "fixture",
            },
            "seed_sha256": "0" * 64,
            "seed_class_paths": [
                "/Game/Test/BP_A.BP_A_C",
                "/Game/Test/BP_B.BP_B_C",
                "/Script/Engine.Actor",
                "/Script/Engine.Pawn",
            ],
            "resolved_parent_hints": [],
            "ambiguous_ancestry": [],
            "ancestry_complete": False,
        }

        def record_hash(object_path, generated, parent):
            return exporter._inventory_record_sha256(
                {
                    "object_path": object_path,
                    "package_name": object_path.split(".", 1)[0],
                    "asset_name": object_path.rsplit(".", 1)[-1],
                    "asset_class_path": "/Script/Engine.Blueprint",
                    "tags": {
                        "GeneratedClass": generated,
                        "ParentClass": parent,
                    },
                }
            )

        first = dict(shared)
        first["registry_record_hashes"] = sorted(
            [
                record_hash(
                    "/Game/Test/BP_A.BP_A",
                    "/Game/Test/BP_A.BP_A_C",
                    "/Script/Engine.Actor",
                ),
                record_hash(
                    "/Game/Test/BP_B.BP_B",
                    "/Game/Test/BP_B.BP_B_C",
                    "/Script/Engine.Pawn",
                ),
            ]
        )
        second = dict(shared)
        second["registry_record_hashes"] = sorted(
            [
                record_hash(
                    "/Game/Test/BP_A.BP_A",
                    "/Game/Test/BP_A.BP_A_C",
                    "/Script/Engine.Pawn",
                ),
                record_hash(
                    "/Game/Test/BP_B.BP_B",
                    "/Game/Test/BP_B.BP_B_C",
                    "/Script/Engine.Actor",
                ),
            ]
        )

        self.assertNotEqual(
            exporter._inventory_signature(first),
            exporter._inventory_signature(second),
        )

    def test_publishes_one_verified_immutable_generation(self):
        exporter = self._exporter()
        rows = [
            {
                "schema": exporter.CLASS_ROW_SCHEMA,
                "class_path": "/Script/CoreUObject.Object",
                "super_class_path": "",
                "is_native": True,
                "interfaces": [],
                "source": "devkit_uclass_reflection",
                "status": "CONFIRMED_ROOT",
                "confidence": "HIGH",
            }
        ]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            manifest = exporter._publish_rows(
                str(output),
                rows,
                producer_source_sha256="a" * 64,
                inventory_signature="b" * 64,
            )
            manifest_path = output / exporter.MANIFEST_OUTPUT_NAME
            on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
            generation = (
                output / exporter.GENERATIONS_DIRECTORY_NAME / manifest["generation_id"]
            )
            rows_path = generation / exporter.CLASS_OUTPUT_NAME
            checkpoint_path = generation / exporter.CHECKPOINT_OUTPUT_NAME

            self.assertEqual(on_disk, manifest)
            self.assertTrue(rows_path.is_file())
            self.assertTrue(checkpoint_path.is_file())
            self.assertRegex(manifest["files"]["classes"]["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(manifest["files"]["classes"]["record_count"], 1)
            self.assertEqual(
                manifest["outputs"]["classes"],
                (
                    f"{exporter.GENERATIONS_DIRECTORY_NAME}/"
                    f"{manifest['generation_id']}/"
                    f"{exporter.CLASS_OUTPUT_NAME}"
                ),
            )
            self.assertEqual(
                exporter._published_manifest_if_current(
                    str(output),
                    "a" * 64,
                    "b" * 64,
                ),
                manifest,
            )
            with mock.patch.object(
                exporter,
                "_publish_root_manifest",
            ) as publish:
                self.assertIsNone(
                    exporter._recover_prepared_generation(
                        str(output),
                        "a" * 64,
                        "b" * 64,
                        current_manifest=manifest,
                    )
                )
                publish.assert_not_called()
            rows_path.write_text("{}\n", encoding="utf-8")
            self.assertIsNone(
                exporter._published_manifest_if_current(
                    str(output),
                    "a" * 64,
                    "b" * 64,
                )
            )

    def test_resume_requires_matching_source_inventory_and_committed_bytes(self):
        exporter = self._exporter()
        with tempfile.TemporaryDirectory() as temporary:
            rows_path = Path(temporary) / exporter.CLASS_OUTPUT_NAME
            committed = b"".join(
                (
                    exporter._canonical_json(
                        {
                            "schema": exporter.CLASS_ROW_SCHEMA,
                            "class_path": (f"/Script/Test.Class{index}"),
                        }
                    )
                    + "\n"
                ).encode("utf-8")
                for index in range(3)
            )
            rows_path.write_bytes(committed + b"uncommitted")
            checkpoint = exporter._new_checkpoint(
                "a" * 64,
                "b" * 64,
                10,
            )
            checkpoint["cursor"] = 3
            checkpoint["row_count"] = 3
            checkpoint["row_bytes"] = len(committed)
            checkpoint["row_chain_sha256"] = exporter._committed_rows_integrity(
                str(rows_path),
                len(committed),
            )["row_chain_sha256"]

            self.assertTrue(
                exporter._checkpoint_is_resumable(
                    checkpoint,
                    "a" * 64,
                    "b" * 64,
                    10,
                    str(rows_path),
                )
            )
            self.assertFalse(
                exporter._checkpoint_is_resumable(
                    checkpoint,
                    "c" * 64,
                    "b" * 64,
                    10,
                    str(rows_path),
                )
            )
            checkpoint["row_bytes"] = len(committed)
            checkpoint["generation_id"] = "../escape"
            self.assertFalse(
                exporter._checkpoint_is_resumable(
                    checkpoint,
                    "a" * 64,
                    "b" * 64,
                    10,
                    str(rows_path),
                )
            )
            checkpoint["generation_id"] = "d" * 32
            checkpoint["row_bytes"] = rows_path.stat().st_size + 1
            self.assertFalse(
                exporter._checkpoint_is_resumable(
                    checkpoint,
                    "a" * 64,
                    "b" * 64,
                    10,
                    str(rows_path),
                )
            )

    def test_resume_rejects_cursor_row_count_or_prefix_line_mismatch(self):
        exporter = self._exporter()
        with tempfile.TemporaryDirectory() as temporary:
            rows_path = Path(temporary) / exporter.CLASS_OUTPUT_NAME
            rows_path.write_bytes(b"{}\n{}\n")
            checkpoint = exporter._new_checkpoint(
                "a" * 64,
                "b" * 64,
                2,
            )
            checkpoint["cursor"] = 2
            checkpoint["row_count"] = 1
            checkpoint["row_bytes"] = rows_path.stat().st_size

            self.assertFalse(
                exporter._checkpoint_is_resumable(
                    checkpoint,
                    "a" * 64,
                    "b" * 64,
                    2,
                    str(rows_path),
                )
            )
            checkpoint["row_count"] = 2
            checkpoint["row_bytes"] = len(b"{}\n")
            self.assertFalse(
                exporter._checkpoint_is_resumable(
                    checkpoint,
                    "a" * 64,
                    "b" * 64,
                    2,
                    str(rows_path),
                )
            )

    def test_checkpoint_chain_update_does_not_rescan_full_jsonl(self):
        exporter = self._exporter()
        checkpoint = exporter._new_checkpoint(
            "a" * 64,
            "b" * 64,
            1,
        )
        row = {
            "schema": exporter.CLASS_ROW_SCHEMA,
            "class_path": "/Script/Engine.Actor",
        }
        row_bytes = len((exporter._canonical_json(row) + "\n").encode("utf-8"))

        with mock.patch.object(
            exporter,
            "_file_integrity",
            side_effect=AssertionError("unexpected full scan"),
        ):
            exporter._checkpoint_rows_committed(
                checkpoint,
                row_bytes,
                [row],
            )

        self.assertEqual(checkpoint["row_bytes"], row_bytes)
        self.assertNotEqual(
            checkpoint["row_chain_sha256"],
            exporter.hashlib.sha256(b"").hexdigest(),
        )

    def test_finalize_rejects_incomplete_checkpoint_even_when_lines_match(self):
        exporter = self._exporter()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            staging = output / exporter.STAGING_DIRECTORY_NAME
            staging.mkdir()
            (staging / exporter.CLASS_OUTPUT_NAME).write_bytes(b"")
            checkpoint = exporter._new_checkpoint(
                "a" * 64,
                "b" * 64,
                10,
            )
            checkpoint["cursor"] = 10
            exporter._write_json_atomic(
                str(staging / exporter.CHECKPOINT_OUTPUT_NAME),
                checkpoint,
            )

            with self.assertRaisesRegex(RuntimeError, "not complete"):
                exporter._finalize_staging(
                    str(output),
                    str(staging),
                    checkpoint,
                )

            self.assertFalse((output / exporter.GENERATIONS_DIRECTORY_NAME).exists())

    def test_prepared_generation_recovers_after_manifest_replace_failure(self):
        exporter = self._exporter()
        rows = [
            {
                "schema": exporter.CLASS_ROW_SCHEMA,
                "class_path": "/Script/CoreUObject.Object",
                "super_class_path": "",
                "is_native": True,
                "interfaces": [],
                "source": "devkit_uclass_reflection",
                "status": "CONFIRMED_ROOT",
                "confidence": "HIGH",
            }
        ]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            original_replace = exporter.os.replace

            def fail_root_manifest_replace(source, destination):
                if Path(destination) == output / exporter.MANIFEST_OUTPUT_NAME:
                    raise PermissionError("simulated Windows sharing violation")
                return original_replace(source, destination)

            with mock.patch.object(
                exporter.os,
                "replace",
                side_effect=fail_root_manifest_replace,
            ):
                with self.assertRaises(PermissionError):
                    exporter._publish_rows(
                        str(output),
                        rows,
                        producer_source_sha256="a" * 64,
                        inventory_signature="b" * 64,
                    )

            generations = list((output / exporter.GENERATIONS_DIRECTORY_NAME).iterdir())
            self.assertEqual(len(generations), 1)
            self.assertTrue((generations[0] / exporter.MANIFEST_STAGING_NAME).is_file())

            recovered = exporter._recover_prepared_generation(
                str(output),
                "a" * 64,
                "b" * 64,
            )

            self.assertIsNotNone(recovered)
            self.assertEqual(
                json.loads(
                    (output / exporter.MANIFEST_OUTPUT_NAME).read_text(encoding="utf-8")
                ),
                recovered,
            )

    def test_first_interruption_retries_the_same_class_without_quarantine(self):
        exporter = self._exporter()
        class_path = "/Game/Test/BP_Interrupted.BP_Interrupted_C"
        source_path = Path(exporter.__file__).resolve()
        source_sha256 = exporter._sha256_file(str(source_path))
        inventory_signature = "b" * 64
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            staging = output / exporter.STAGING_DIRECTORY_NAME
            staging.mkdir()
            rows_path = staging / exporter.CLASS_OUTPUT_NAME
            rows_path.write_bytes(b"")
            checkpoint = exporter._new_checkpoint(
                source_sha256,
                inventory_signature,
                1,
            )
            checkpoint["active_cursor"] = 0
            checkpoint["active_class_path"] = class_path
            checkpoint["active_attempt"] = 1
            exporter._write_json_atomic(
                str(staging / exporter.CHECKPOINT_OUTPUT_NAME),
                checkpoint,
            )
            inventory = {
                "class_paths": [class_path],
                "parent_hints": {class_path: "/Script/Engine.Actor"},
                "parent_hint_sources": {class_path: "asset_registry_class_ancestry"},
                "interface_hints": {class_path: ["/Game/Test/BPI_Test.BPI_Test_C"]},
                "interface_complete": {class_path},
                "inventory_signature": inventory_signature,
            }

            with (
                mock.patch.object(
                    exporter,
                    "_resolve_source_path",
                    return_value=str(source_path),
                ),
                mock.patch.object(
                    exporter,
                    "_registry_class_inventory",
                    return_value=inventory,
                ),
                mock.patch.object(
                    exporter,
                    "_output_dir",
                    return_value=str(output),
                ),
            ):
                manifest = exporter.export_class_hierarchy_snapshot()

            generation = (
                output / exporter.GENERATIONS_DIRECTORY_NAME / manifest["generation_id"]
            )
            row = json.loads(
                (generation / exporter.CLASS_OUTPUT_NAME)
                .read_text(encoding="utf-8")
                .strip()
            )
            self.assertEqual(row["class_path"], class_path)
            self.assertEqual(row["super_class_path"], "/Script/Engine.Actor")
            self.assertEqual(
                row["interfaces"],
                ["/Game/Test/BPI_Test.BPI_Test_C"],
            )
            self.assertEqual(row["status"], "CONFIRMED")
            self.assertNotIn("reason_code", row)
            self.assertEqual(manifest["files"]["classes"]["record_count"], 1)

    def test_second_interruption_quarantines_with_hints_and_does_not_leak(self):
        exporter = self._exporter()
        interrupted_path = "/Game/Test/BP_A_Interrupted.BP_A_Interrupted_C"
        next_path = "/Game/Test/BP_B_Next.BP_B_Next_C"
        source_path = Path(exporter.__file__).resolve()
        source_sha256 = exporter._sha256_file(str(source_path))
        inventory_signature = "c" * 64
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            staging = output / exporter.STAGING_DIRECTORY_NAME
            staging.mkdir()
            rows_path = staging / exporter.CLASS_OUTPUT_NAME
            rows_path.write_bytes(b"")
            checkpoint = exporter._new_checkpoint(
                source_sha256,
                inventory_signature,
                2,
            )
            checkpoint["active_cursor"] = 0
            checkpoint["active_class_path"] = interrupted_path
            checkpoint["active_attempt"] = 2
            exporter._write_json_atomic(
                str(staging / exporter.CHECKPOINT_OUTPUT_NAME),
                checkpoint,
            )
            inventory = {
                "class_paths": [interrupted_path, next_path],
                "parent_hints": {
                    interrupted_path: "/Script/Engine.Actor",
                    next_path: "/Script/Engine.Actor",
                },
                "parent_hint_sources": {
                    interrupted_path: "asset_registry_class_ancestry",
                    next_path: "asset_registry_class_ancestry",
                },
                "interface_hints": {
                    interrupted_path: ["/Game/Test/BPI_Test.BPI_Test_C"],
                    next_path: ["/Game/Test/BPI_Test.BPI_Test_C"],
                },
                "interface_complete": {interrupted_path, next_path},
                "inventory_signature": inventory_signature,
            }

            with (
                mock.patch.object(
                    exporter,
                    "_resolve_source_path",
                    return_value=str(source_path),
                ),
                mock.patch.object(
                    exporter,
                    "_registry_class_inventory",
                    return_value=inventory,
                ),
                mock.patch.object(
                    exporter,
                    "_output_dir",
                    return_value=str(output),
                ),
            ):
                manifest = exporter.export_class_hierarchy_snapshot()
                current = exporter.export_class_hierarchy_snapshot()

            generation = (
                output / exporter.GENERATIONS_DIRECTORY_NAME / manifest["generation_id"]
            )
            rows = [
                json.loads(line)
                for line in (generation / exporter.CLASS_OUTPUT_NAME)
                .read_text(encoding="utf-8")
                .splitlines()
            ]

            self.assertEqual(current["generation_id"], manifest["generation_id"])
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["class_path"], interrupted_path)
            self.assertEqual(
                rows[0]["status"],
                "QUARANTINED_AFTER_REPEATED_INTERRUPTION",
            )
            self.assertEqual(
                rows[0]["reason_code"],
                "REPEATED_INTERRUPTION_SAME_CLASS_GENERATION",
            )
            self.assertEqual(rows[0]["interruption_attempts"], 2)
            self.assertEqual(
                rows[0]["super_class_path"],
                "/Script/Engine.Actor",
            )
            self.assertEqual(
                rows[0]["interfaces"],
                ["/Game/Test/BPI_Test.BPI_Test_C"],
            )
            self.assertEqual(rows[0]["parent_status"], "CONFIRMED")
            self.assertEqual(rows[0]["interfaces_status"], "CONFIRMED")
            self.assertEqual(rows[1]["class_path"], next_path)
            self.assertEqual(rows[1]["status"], "CONFIRMED")
            self.assertNotIn("reason_code", rows[1])

    def test_malformed_publication_pointer_fails_closed(self):
        exporter = self._exporter()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / exporter.MANIFEST_OUTPUT_NAME).write_text(
                json.dumps(
                    {
                        "schema": exporter.SNAPSHOT_SCHEMA,
                        "status": "COMPLETE",
                        "producer": [],
                        "inventory_signature": "b" * 64,
                    }
                ),
                encoding="utf-8",
            )
            self.assertIsNone(
                exporter._published_manifest_if_current(
                    str(output),
                    "a" * 64,
                    "b" * 64,
                )
            )

    def test_manifest_verifier_cross_checks_generation_and_checkpoint(self):
        exporter = self._exporter()
        rows = [
            {
                "schema": exporter.CLASS_ROW_SCHEMA,
                "class_path": "/Script/CoreUObject.Object",
                "super_class_path": "",
                "is_native": True,
                "interfaces": [],
                "source": "core_uobject_root",
                "status": "CONFIRMED_ROOT",
                "confidence": "HIGH",
            }
        ]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            manifest = exporter._publish_rows(
                str(output),
                rows,
                producer_source_sha256="a" * 64,
                inventory_signature="b" * 64,
            )
            generation = (
                output / exporter.GENERATIONS_DIRECTORY_NAME / manifest["generation_id"]
            )
            checkpoint_path = generation / exporter.CHECKPOINT_OUTPUT_NAME
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint["generation_id"] = "f" * 32
            exporter._write_json_atomic(
                str(checkpoint_path),
                checkpoint,
            )
            checkpoint_integrity = exporter._file_integrity(str(checkpoint_path))
            manifest["files"]["checkpoint"] = {
                "sha256": checkpoint_integrity["sha256"],
                "bytes": checkpoint_integrity["bytes"],
            }
            exporter._write_json_atomic(
                str(generation / exporter.MANIFEST_STAGING_NAME),
                manifest,
            )
            exporter._write_json_atomic(
                str(output / exporter.MANIFEST_OUTPUT_NAME),
                manifest,
            )

            self.assertIsNone(
                exporter._published_manifest_if_current(
                    str(output),
                    "a" * 64,
                    "b" * 64,
                )
            )


if __name__ == "__main__":
    unittest.main()
