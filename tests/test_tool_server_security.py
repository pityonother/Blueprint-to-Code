from __future__ import annotations

import json
import sys
import threading
import unittest
from contextlib import ExitStack
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import blueprint_tool_server as tool_server  # noqa: E402


POST_ROUTES = (
    "/api/harvest/build",
    "/api/harvest/build/job-1/cancel",
    "/api/analyze",
    "/api/capture-graph",
    "/api/compare-asset",
    "/api/jobs/job-1/cancel",
    "/api/open",
    "/api/open-captures",
    "/api/knowledge-base/build",
    "/api/knowledge-base/read-priority",
    "/api/knowledge-base/open",
    "/api/devkit-request",
    "/api/uasset-candidates",
    "/api/uasset-graphs",
    "/api/evidence-queries",
    "/api/notes-append",
)


class ToolServerSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = tool_server.create_control_center_server("127.0.0.1", 0)
        self.port = int(self.server.server_address[1])
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=3)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object], dict[str, str]]:
        connection = HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            raw = response.read().decode("utf-8")
            response_headers = {
                key.casefold(): value for key, value in response.getheaders()
            }
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"raw": raw}
            return response.status, payload, response_headers
        finally:
            connection.close()

    def session_token(self) -> str:
        status, payload, headers = self.request(
            "GET",
            "/api/session",
            headers={"Host": f"127.0.0.1:{self.port}"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["cache-control"], "no-store")
        token = payload.get("sessionToken")
        self.assertIsInstance(token, str)
        self.assertGreaterEqual(len(token), 32)
        return str(token)

    def post_headers(
        self,
        token: str,
        *,
        origin: str | None = None,
        content_type: str = "application/json",
        non_browser: bool = False,
    ) -> dict[str, str]:
        headers = {
            "Host": f"127.0.0.1:{self.port}",
            "Content-Type": content_type,
            "X-Blueprint-Session": token,
        }
        if origin is not None:
            headers["Origin"] = origin
        if non_browser:
            headers["X-Blueprint-Client"] = "non-browser"
        return headers

    def block_route_side_effects(self) -> ExitStack:
        stack = ExitStack()
        for name in (
            "start_harvest_build_for_request",
            "cancel_harvest_build_for_request",
            "start_report_generation_job",
            "capture_graph_from_request",
            "start_asset_compare_job",
            "cancel_job",
            "open_path",
            "start_knowledge_base_job",
            "start_priority_read_job",
            "write_devkit_request",
            "mine_uasset_graph_candidates_for_request",
            "read_uasset_graphs_for_request",
            "query_asset_evidence",
            "append_notes_for_functions",
        ):
            stack.enter_context(
                patch.object(
                    tool_server,
                    name,
                    side_effect=AssertionError(
                        f"{name} ran before request security validation"
                    ),
                )
            )
        return stack

    def test_all_post_routes_reject_missing_session_before_side_effects(self) -> None:
        with self.block_route_side_effects():
            for route in POST_ROUTES:
                with self.subTest(route=route):
                    status, payload, headers = self.request(
                        "POST",
                        route,
                        body="{}",
                        headers={
                            "Host": f"127.0.0.1:{self.port}",
                            "Origin": f"http://127.0.0.1:{self.port}",
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(status, 403)
                    self.assertEqual(payload["code"], "SESSION_TOKEN_REQUIRED")
                    self.assertEqual(headers["cache-control"], "no-store")

    def test_cross_origin_post_is_rejected_with_a_valid_session(self) -> None:
        token = self.session_token()
        status, payload, _headers = self.request(
            "POST",
            "/api/open-captures",
            body="{}",
            headers=self.post_headers(
                token,
                origin="https://attacker.example",
            ),
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "ORIGIN_FORBIDDEN")

    def test_https_origin_is_not_same_origin_as_the_http_server(self) -> None:
        token = self.session_token()
        with patch.object(tool_server, "open_path") as open_path_mock:
            status, payload, _headers = self.request(
                "POST",
                "/api/open-captures",
                body="{}",
                headers=self.post_headers(
                    token,
                    origin=f"https://127.0.0.1:{self.port}",
                ),
            )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "ORIGIN_FORBIDDEN")
        open_path_mock.assert_not_called()

    def test_browser_post_requires_origin_even_with_a_valid_session(self) -> None:
        token = self.session_token()
        status, payload, _headers = self.request(
            "POST",
            "/api/open-captures",
            body="{}",
            headers=self.post_headers(token),
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "ORIGIN_REQUIRED")

    def test_explicit_non_browser_client_can_use_a_valid_session_without_origin(
        self,
    ) -> None:
        token = self.session_token()
        with patch.object(tool_server, "open_path"):
            status, payload, _headers = self.request(
                "POST",
                "/api/open-captures",
                body="{}",
                headers=self.post_headers(token, non_browser=True),
            )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_wrong_content_type_is_rejected_before_body_parsing(self) -> None:
        token = self.session_token()
        status, payload, _headers = self.request(
            "POST",
            "/api/open-captures",
            body="{}",
            headers=self.post_headers(
                token,
                origin=f"http://127.0.0.1:{self.port}",
                content_type="text/plain",
            ),
        )
        self.assertEqual(status, 415)
        self.assertEqual(payload["code"], "JSON_CONTENT_TYPE_REQUIRED")

    def test_body_larger_than_one_mib_is_rejected(self) -> None:
        token = self.session_token()
        connection = HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.putrequest(
                "POST",
                "/api/open-captures",
                skip_host=True,
            )
            for key, value in self.post_headers(
                token,
                origin=f"http://127.0.0.1:{self.port}",
            ).items():
                connection.putheader(key, value)
            connection.putheader("Content-Length", str(1024 * 1024 + 1))
            connection.endheaders()
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()
        self.assertEqual(response.status, 413)
        self.assertEqual(payload["code"], "REQUEST_BODY_TOO_LARGE")

    def test_json_body_must_be_an_object(self) -> None:
        token = self.session_token()
        status, payload, _headers = self.request(
            "POST",
            "/api/open-captures",
            body="[]",
            headers=self.post_headers(
                token,
                origin=f"http://127.0.0.1:{self.port}",
            ),
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "REQUEST_BODY_OBJECT_REQUIRED")

    def test_session_token_is_not_exposed_by_state(self) -> None:
        token = self.session_token()
        with patch.object(tool_server, "api_state", return_value={"assets": []}):
            status, payload, _headers = self.request(
                "GET",
                "/api/state",
                headers={"Host": f"127.0.0.1:{self.port}"},
            )
        self.assertEqual(status, 200)
        self.assertNotIn(token, json.dumps(payload))

    def test_remote_bind_requires_allow_remote_and_nonempty_auth_token(self) -> None:
        with self.assertRaises(ValueError):
            tool_server.create_control_center_server("0.0.0.0", 0)
        with self.assertRaises(ValueError):
            tool_server.create_control_center_server(
                "0.0.0.0",
                0,
                allow_remote=True,
            )

        server = tool_server.create_control_center_server(
            "0.0.0.0",
            0,
            allow_remote=True,
            auth_token="remote-test-secret",
        )
        try:
            self.assertNotIn("remote-test-secret", repr(server.security_policy))
        finally:
            server.server_close()

    def test_remote_session_and_post_both_require_bearer_auth(self) -> None:
        auth_token = "remote-test-secret"
        server = tool_server.create_control_center_server(
            "0.0.0.0",
            0,
            allow_remote=True,
            auth_token=auth_token,
        )
        port = int(server.server_address[1])
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()

        def remote_request(
            method: str,
            path: str,
            *,
            body: str | None = None,
            headers: dict[str, str] | None = None,
        ) -> tuple[int, dict[str, object]]:
            connection = HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                return response.status, json.loads(
                    response.read().decode("utf-8")
                )
            finally:
                connection.close()

        try:
            status, payload = remote_request(
                "GET",
                "/api/session",
                headers={"Host": f"127.0.0.1:{port}"},
            )
            self.assertEqual(status, 403)
            self.assertEqual(payload["code"], "REMOTE_AUTH_REQUIRED")

            status, payload = remote_request(
                "GET",
                "/api/session",
                headers={
                    "Host": f"127.0.0.1:{port}",
                    "Authorization": f"Bearer {auth_token}",
                },
            )
            self.assertEqual(status, 200)
            session_token = str(payload["sessionToken"])

            post_headers = {
                "Host": f"127.0.0.1:{port}",
                "Origin": f"http://127.0.0.1:{port}",
                "Content-Type": "application/json",
                "X-Blueprint-Session": session_token,
            }
            status, payload = remote_request(
                "POST",
                "/api/open-captures",
                body="{}",
                headers=post_headers,
            )
            self.assertEqual(status, 403)
            self.assertEqual(payload["code"], "REMOTE_AUTH_REQUIRED")

            with patch.object(tool_server, "open_path"):
                status, payload = remote_request(
                    "POST",
                    "/api/open-captures",
                    body="{}",
                    headers={
                        **post_headers,
                        "Authorization": f"Bearer {auth_token}",
                    },
                )
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)

    def test_static_path_traversal_remains_rejected(self) -> None:
        status, _payload, _headers = self.request(
            "GET",
            "/%2e%2e/package.json",
            headers={"Host": f"127.0.0.1:{self.port}"},
        )
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
