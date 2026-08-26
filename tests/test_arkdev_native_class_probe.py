from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_scripting_probe.contracts import assert_path_free  # noqa: E402
from arkdev_scripting_probe.in_editor.arkdev_native_class_probe import (  # noqa: E402
    collect_native_class_result,
)
from arkdev_scripting_probe.native_class import (  # noqa: E402
    NATIVE_CLASS_RESULT_SCHEMA,
    NativeClassProbeError,
    is_native_class_path,
    run_native_class_probe,
    validate_native_class_result,
)


class Character:
    """Minimal fake of the inherited Unreal Character wrapper."""

    is_crouched = False

    def crouch(self) -> None:
        raise AssertionError("read-only probe must not call crouch")

    def un_crouch(self) -> None:
        raise AssertionError("read-only probe must not call un_crouch")

    def on_start_crouch(self) -> None:
        raise AssertionError("read-only probe must not call on_start_crouch")

    def on_end_crouch(self) -> None:
        raise AssertionError("read-only probe must not call on_end_crouch")


class PrimalCharacter(Character):
    pass


class ShooterCharacter(PrimalCharacter):
    def get_editor_property(self, name: str) -> object:
        if name in {"is_crouched", "bIsCrouched"}:
            return False
        raise AttributeError(name)

    def set_editor_property(self, _name: str, _value: object) -> None:
        raise AssertionError("read-only probe must not mutate the CDO")


class _SystemLibrary:
    @staticmethod
    def get_engine_version() -> str:
        return "5.5.4-0+UE5"


class _FakeUnreal:
    SystemLibrary = _SystemLibrary

    @staticmethod
    def load_class(_outer: object, path: str) -> type[ShooterCharacter] | None:
        if path == "/Script/ShooterGame.ShooterCharacter":
            return ShooterCharacter
        return None

    @staticmethod
    def get_default_object(
        class_handle: type[ShooterCharacter],
    ) -> ShooterCharacter:
        return class_handle()


def _fixture_result() -> dict[str, object]:
    return collect_native_class_result(
        _FakeUnreal,
        "/Script/ShooterGame.ShooterCharacter",
        generated_at="2026-08-26T00:00:00Z",
    )


class NativeClassProbeTests(unittest.TestCase):
    def test_shooter_character_crouch_property_is_read_from_cdo_without_mutation(self) -> None:
        result = _fixture_result()

        self.assertEqual(result["schema"], NATIVE_CLASS_RESULT_SCHEMA)
        self.assertEqual(result["sourceKind"], "native_class_reflection")
        self.assertTrue(result["classLoaded"])
        self.assertTrue(result["classDefaultObjectRead"])
        self.assertFalse(result["runtimeStateAvailable"])
        self.assertEqual(result["runtimeStateReason"], "CLASS_DEFAULT_OBJECT_ONLY")
        self.assertTrue(result["readOnly"])
        self.assertTrue(result["runtimeStateExplanation"].isascii())
        self.assertEqual(
            result["inheritance"][:3],
            ["ShooterCharacter", "PrimalCharacter", "Character"],
        )

        properties = {
            item["requestedName"]: item for item in result["properties"]
        }
        for name in ("is_crouched", "bIsCrouched"):
            self.assertTrue(properties[name]["readable"])
            self.assertIs(properties[name]["value"], False)
            self.assertEqual(properties[name]["valueType"], "bool")
            self.assertEqual(properties[name]["ownerClass"], "Character")
            self.assertEqual(properties[name]["scope"], "CLASS_DEFAULT_OBJECT")

        functions = {item["name"]: item for item in result["functions"]}
        self.assertTrue(functions["crouch"]["available"])
        self.assertTrue(functions["un_crouch"]["available"])
        self.assertEqual(functions["crouch"]["ownerClass"], "Character")
        assert_path_free(result)

    def test_invalid_or_asset_paths_are_not_accepted_as_native_classes(self) -> None:
        self.assertTrue(is_native_class_path("/Script/ShooterGame.ShooterCharacter"))
        self.assertFalse(is_native_class_path("/Game/Test/BP_Test.BP_Test"))
        self.assertFalse(is_native_class_path("/Script/ShooterGame"))
        self.assertFalse(is_native_class_path("/Script/ShooterGame.Bad/Path"))
        self.assertFalse(is_native_class_path("/Script/M." + "C" * 300))

    def test_validator_rejects_a_result_that_claims_live_runtime_state(self) -> None:
        result = _fixture_result()
        result["runtimeStateAvailable"] = True

        with self.assertRaises(NativeClassProbeError) as raised:
            validate_native_class_result(
                result,
                expected_class_path="/Script/ShooterGame.ShooterCharacter",
            )

        self.assertEqual(raised.exception.code, "invalid_native_class_result")

    def test_host_runner_uses_commandlet_and_returns_only_validated_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ARKDevkit"
            executable = root / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
            project = root / "Projects" / "ShooterGame" / "ShooterGame.uproject"
            probe_script = Path(temporary) / "arkdev_native_class_probe.py"
            executable.parent.mkdir(parents=True)
            project.parent.mkdir(parents=True)
            executable.write_bytes(b"")
            project.write_text("{}", encoding="utf-8")
            probe_script.write_text("# fixture", encoding="utf-8")
            observed: dict[str, object] = {}

            def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
                observed["command"] = command
                environment = kwargs["env"]
                assert isinstance(environment, dict)
                result_path = Path(
                    environment["BLUEPRINT_TO_CODE_NATIVE_CLASS_RESULT"]
                )
                result_path.write_text(
                    json.dumps(_fixture_result()),
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            result = run_native_class_probe(
                "/Script/ShooterGame.ShooterCharacter",
                devkit_root=root,
                probe_script=probe_script,
                process_runner=fake_run,
            )

        command = observed["command"]
        self.assertIsInstance(command, list)
        self.assertEqual(Path(command[0]), executable)
        self.assertEqual(Path(command[1]), project)
        self.assertIn("-run=pythonscript", command)
        self.assertTrue(any(item.startswith("-script=") for item in command))
        self.assertEqual(result["assetPath"], "/Script/ShooterGame.ShooterCharacter")
        self.assertNotIn(str(root), json.dumps(result))
        assert_path_free(result)

    def test_host_runner_rejects_invalid_path_before_starting_devkit(self) -> None:
        called = False

        def fake_run(_command: list[str], **_kwargs: object) -> SimpleNamespace:
            nonlocal called
            called = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with self.assertRaises(NativeClassProbeError) as raised:
            run_native_class_probe(
                "/Game/Test/BP_Test.BP_Test",
                devkit_root=Path("C:/missing"),
                probe_script=Path("C:/missing/probe.py"),
                process_runner=fake_run,
            )

        self.assertEqual(raised.exception.code, "invalid_native_class_path")
        self.assertFalse(called)

    def test_probe_does_not_depend_on_local_environment_secrets(self) -> None:
        old = os.environ.get("BLUEPRINT_TO_CODE_NATIVE_CLASS_REQUEST")
        try:
            os.environ["BLUEPRINT_TO_CODE_NATIVE_CLASS_REQUEST"] = "ignored"
            result = _fixture_result()
        finally:
            if old is None:
                os.environ.pop("BLUEPRINT_TO_CODE_NATIVE_CLASS_REQUEST", None)
            else:
                os.environ["BLUEPRINT_TO_CODE_NATIVE_CLASS_REQUEST"] = old

        self.assertEqual(result["assetPath"], "/Script/ShooterGame.ShooterCharacter")


if __name__ == "__main__":
    unittest.main()
