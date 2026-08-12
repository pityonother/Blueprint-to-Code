from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from devkit_exporters.arkdev_wc_properties import (  # noqa: E402
    wc_get_all_property_names,
    wc_get_all_property_values,
)


EXPORTER_PATH = (
    SCRIPTS / "devkit_exporters" / "export_current_blueprint_defaults.py"
)


class _Properties:
    def __init__(self, names: list[str]) -> None:
        self.names = names

    def wc_get_all_property_names(self) -> list[str]:
        return list(self.names)

    def wc_get_all_property_values(self) -> list[tuple[str, int]]:
        return [(name, int(name.removeprefix("Property"))) for name in self.names]


def _load_exporter_definitions() -> dict[str, object]:
    source = EXPORTER_PATH.read_text(encoding="utf-8")
    definitions = source.rsplit("\ntry:\n    EXPORT_RESULT =", maxsplit=1)[0]
    namespace: dict[str, object] = {
        "__file__": str(EXPORTER_PATH),
        "__name__": "test_devkit_exporter_definitions",
    }
    exec(compile(definitions, str(EXPORTER_PATH), "exec"), namespace)
    return namespace


class ArkDevWildcardPropertyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.names = [f"Property{index:04d}" for index in range(800)]
        self.forward = _Properties(self.names)
        self.reverse = _Properties(list(reversed(self.names)))

    def test_helpers_are_deterministic_above_512_and_honor_caller_limit(self) -> None:
        forward_names = wc_get_all_property_names(self.forward, limit=700)
        reverse_names = wc_get_all_property_names(self.reverse, limit=700)
        forward_values = wc_get_all_property_values(self.forward, limit=700)
        reverse_values = wc_get_all_property_values(self.reverse, limit=700)

        self.assertEqual(forward_names, reverse_names)
        self.assertEqual(len(forward_names), 700)
        self.assertEqual(forward_values, reverse_values)
        self.assertEqual(len(forward_values), 700)
        self.assertEqual(
            len(wc_get_all_property_values(self.forward, limit=1200)),
            800,
        )

    def test_defaults_exporter_preserves_1200_bound_and_reports_700_truncation(
        self,
    ) -> None:
        exporter = _load_exporter_definitions()
        collect_wc_defaults = exporter["collect_wc_defaults"]
        export_state = exporter["ExportState"]
        self.assertTrue(callable(collect_wc_defaults))
        self.assertTrue(callable(export_state))

        exporter["STATE"] = export_state()
        class_defaults = collect_wc_defaults(
            self.forward,
            "wc_class_default",
            exporter["MAX_CLASS_DEFAULT_PROPERTIES"],
        )
        self.assertEqual(len(class_defaults), 800)

        exporter["STATE"] = export_state()
        forward_components = collect_wc_defaults(
            self.forward,
            "component_default",
            exporter["MAX_COMPONENT_PROPERTIES"],
        )
        forward_skips = list(exporter["STATE"].skipped)

        exporter["STATE"] = export_state()
        reverse_components = collect_wc_defaults(
            self.reverse,
            "component_default",
            exporter["MAX_COMPONENT_PROPERTIES"],
        )
        reverse_skips = list(exporter["STATE"].skipped)

        self.assertEqual(forward_components, reverse_components)
        self.assertEqual(len(forward_components), 700)
        self.assertEqual(
            [item["reason"] for item in forward_skips],
            [item["reason"] for item in reverse_skips],
        )
        self.assertTrue(
            any(
                item["where"] == "component_default"
                and "property limit reached" in item["reason"]
                for item in forward_skips
            ),
            forward_skips,
        )


if __name__ == "__main__":
    unittest.main()
