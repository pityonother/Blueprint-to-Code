from __future__ import annotations

import contextlib
import io
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


class KnowledgeClassHierarchySeedBuilderTests(unittest.TestCase):
    def _builder(self):
        from devkit_exporters import build_kb_class_hierarchy_seed

        return build_kb_class_hierarchy_seed

    def _database(self, root: Path, *, unresolved_component: bool = False) -> Path:
        database_path = root / "kb_discovery.sqlite"
        connection = sqlite3.connect(database_path)
        connection.executescript(
            """
            CREATE TABLE assets (
                asset_class_path TEXT,
                generated_class_path TEXT,
                parent_class_path TEXT,
                native_parent_class_path TEXT
            );
            CREATE TABLE class_edges (
                child_class_path TEXT,
                parent_class_path TEXT
            );
            CREATE TABLE interfaces (
                interface_class_path TEXT
            );
            CREATE TABLE components (
                component_class_path TEXT
            );
            CREATE TABLE blueprint_functions (
                declaring_class_path TEXT
            );
            CREATE TABLE default_property_surface (
                declaring_class_path TEXT
            );
            """
        )
        connection.executemany(
            "INSERT INTO assets VALUES (?, ?, ?, ?)",
            [
                (
                    "/Script/Engine.Blueprint",
                    "/Game/Test/BP_A.BP_A_C",
                    "/Script/Engine.Actor",
                    "/Script/Engine.Actor",
                ),
                (
                    "/Script/Engine.SceneComponent",
                    "/Game/Test/BP_Component.BP_Component_C",
                    "/Script/Engine.SceneComponent",
                    "/Script/Engine.SceneComponent",
                ),
            ],
        )
        connection.execute(
            "INSERT INTO class_edges VALUES (?, ?)",
            ("/Game/Test/BP_A.BP_A_C", "/Script/Engine.Actor"),
        )
        connection.executemany(
            "INSERT INTO interfaces VALUES (?)",
            [
                ("/Game/Test/BPI_Test.BPI_Test_C",),
                ("/Game/Test/BP_A.BP_A:GetInterfaceFunction",),
            ],
        )
        components = [
            ("SceneComponent",),
            ("/Game/Test/BP_Component.BP_Component_C",),
        ]
        if unresolved_component:
            components.append(("MysteryComponent",))
        connection.executemany("INSERT INTO components VALUES (?)", components)
        connection.execute(
            "INSERT INTO blueprint_functions VALUES (?)",
            ("/Game/Test/BP_FunctionOwner.BP_FunctionOwner_C",),
        )
        connection.execute(
            "INSERT INTO default_property_surface VALUES (?)",
            ("/Game/Test/BP_DefaultOwner.BP_DefaultOwner_C",),
        )
        connection.commit()
        connection.close()
        return database_path

    def test_builds_deterministic_seed_from_discovery_class_surfaces(self):
        builder = self._builder()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database_path = self._database(root)
            output_path = root / "class_hierarchy_seed.json"

            first = builder.build_seed(database_path, output_path)
            first_bytes = output_path.read_bytes()
            second = builder.build_seed(database_path, output_path)

            self.assertEqual(first, second)
            self.assertEqual(output_path.read_bytes(), first_bytes)
            self.assertEqual(
                json.loads(first_bytes.decode("utf-8")),
                first,
            )
            self.assertEqual(first["schema"], builder.SEED_SCHEMA)
            self.assertEqual(first["class_count"], len(first["class_paths"]))
            self.assertIn("/Game/Test/BP_A.BP_A_C", first["class_paths"])
            self.assertIn(
                "/Game/Test/BPI_Test.BPI_Test_C",
                first["class_paths"],
            )
            self.assertIn(
                "/Game/Test/BP_Component.BP_Component_C",
                first["class_paths"],
            )
            self.assertIn(
                "/Game/Test/BP_FunctionOwner.BP_FunctionOwner_C",
                first["class_paths"],
            )
            self.assertIn(
                "/Game/Test/BP_DefaultOwner.BP_DefaultOwner_C",
                first["class_paths"],
            )
            self.assertIn("/Script/Engine.SceneComponent", first["class_paths"])
            self.assertNotIn(
                "/Game/Test/BP_A.BP_A",
                first["class_paths"],
            )
            self.assertEqual(
                first["component_resolution"]["resolved_short_names"],
                {"SceneComponent": "/Script/Engine.SceneComponent"},
            )
            self.assertEqual(
                first["ignored_non_class_value_counts"][
                    "interfaces.interface_class_path"
                ],
                1,
            )

    def test_unresolved_component_short_name_fails_closed_without_output(self):
        builder = self._builder()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database_path = self._database(
                root,
                unresolved_component=True,
            )
            output_path = root / "class_hierarchy_seed.json"

            with self.assertRaisesRegex(
                RuntimeError,
                "MysteryComponent",
            ):
                builder.build_seed(database_path, output_path)

            self.assertFalse(output_path.exists())

    def test_cli_prints_command_that_passes_seed_path_to_exporter(self):
        builder = self._builder()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database_path = self._database(
                root,
                unresolved_component=True,
            )
            output_path = root / "class_hierarchy_seed.json"
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = builder.main(
                    [
                        "--discovery-db",
                        str(database_path),
                        "--output",
                        str(output_path),
                        "--short-class",
                        "MysteryComponent=/Script/Test.MysteryComponent",
                    ]
                )

            text = stdout.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.is_file())
            self.assertIn("BTC_KB_CLASS_HIERARCHY_SEED_FILE", text)
            self.assertIn("export_kb_class_hierarchy_snapshot.py", text)
            self.assertIn(str(output_path.resolve()), text)


if __name__ == "__main__":
    unittest.main()
