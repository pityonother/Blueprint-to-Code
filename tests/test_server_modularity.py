from __future__ import annotations

import json
import sys
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from blueprint_server.responses import (  # noqa: E402
    error_payload,
    prepare_json_response,
    static_content_type,
)
from blueprint_server.routes_state import (  # noqa: E402
    StateRoute,
    state_route_payload,
)
import blueprint_tool_server as tool_server  # noqa: E402


class ServerModularityTests(unittest.TestCase):
    def test_json_response_is_prepared_without_handler_state(self) -> None:
        response = prepare_json_response(
            {"ok": True, "label": "资源"},
            HTTPStatus.ACCEPTED,
            close_connection=True,
        )

        self.assertEqual(response.status, HTTPStatus.ACCEPTED)
        self.assertEqual(
            response.headers,
            (
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(response.body))),
                ("Cache-Control", "no-store"),
                ("Connection", "close"),
            ),
        )
        self.assertEqual(
            json.loads(response.body.decode("utf-8")),
            {"ok": True, "label": "资源"},
        )
        self.assertNotIn(b"\n", response.body)

    def test_response_helpers_preserve_public_error_and_mime_contracts(self) -> None:
        self.assertEqual(
            error_payload("No such report."),
            {"ok": False, "error": "No such report."},
        )
        self.assertEqual(
            static_content_type("text/html"),
            "text/html; charset=utf-8",
        )
        self.assertEqual(static_content_type("image/jpeg"), "image/jpeg")
        self.assertEqual(
            static_content_type(None),
            "application/octet-stream",
        )

    def test_state_route_builds_dynamic_state_and_only_matches_state_url(self) -> None:
        asset_snapshots = iter(
            [
                [{"name": "first"}],
                [{"name": "second"}],
            ]
        )
        route = StateRoute(
            version="1.2.3",
            project_root=Path("project"),
            capture_root=Path("captures"),
            devkit_request_path=Path("captures/request.json"),
            list_assets=lambda: next(asset_snapshots),
            knowledge_base_summary=lambda: {"exists": True},
            read_devkit_request=lambda: "/Game/Test/Asset.Asset",
            devkit_python_command=lambda: "python-command",
            devkit_output_log_command=lambda: "output-command",
        )

        first = state_route_payload("/api/state", route.state)
        second = state_route_payload("/api/state", route.state)

        self.assertEqual(first["ok"], True)
        self.assertEqual(first["version"], "1.2.3")
        self.assertEqual(first["assets"], [{"name": "first"}])
        self.assertEqual(second["assets"], [{"name": "second"}])
        self.assertIsNone(state_route_payload("/api/other", route.state))

    def test_legacy_server_entry_delegates_response_and_state_route_work(self) -> None:
        source = (SCRIPTS / "blueprint_tool_server.py").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "from blueprint_server.responses import",
            source,
        )
        self.assertIn(
            "from blueprint_server.routes_state import",
            source,
        )
        self.assertNotIn("def encode_json_response(", source)
        self.assertNotIn("def static_content_type(", source)
        self.assertIn(
            "state_route_payload(parsed.path, api_state)",
            source,
        )

    def test_legacy_state_entry_resolves_patchable_dependencies_per_call(self) -> None:
        with (
            patch.object(
                tool_server,
                "list_assets",
                return_value=[{"name": "patched"}],
            ),
            patch.object(
                tool_server,
                "knowledge_base_summary",
                return_value={"exists": False},
            ),
            patch.object(
                tool_server,
                "read_devkit_request",
                return_value="/Game/Patched.Asset",
            ),
        ):
            state = tool_server.api_state()

        self.assertEqual(state["assets"], [{"name": "patched"}])
        self.assertEqual(state["knowledgeBase"], {"exists": False})
        self.assertEqual(state["devkitAssetPath"], "/Game/Patched.Asset")


if __name__ == "__main__":
    unittest.main()
