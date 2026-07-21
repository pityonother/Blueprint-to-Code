import json
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import blueprint_tool_server as tool_server  # noqa: E402
from blueprint_tool_server import (  # noqa: E402
    cancel_harvest_build_for_request,
    encode_json_response,
    query_harvest_build_for_request,
    query_harvest_creature_specialties_for_request,
    query_harvest_creatures_for_request,
    query_harvest_node_for_request,
    query_harvest_nodes_for_request,
    query_harvest_ranking_for_request,
    resolve_harvest_image_path,
    start_harvest_build_for_request,
    static_content_type,
)
from blueprint_translator.harvest_build_jobs import (  # noqa: E402
    HarvestBuildAlreadyRunning,
    HarvestBuildArgumentError,
    HarvestBuildJobNotFound,
)


class _FakeRepository:
    def list_nodes(self, **kwargs):
        return {"schema": "page", "arguments": kwargs, "items": []}

    def get_node(self, node_id):
        return {"id": node_id}

    def rankings(self, node_id, node_resource_id, *, limit):
        return {
            "node": {"id": node_id},
            "resource": {"nodeResourceId": node_resource_id},
            "limit": limit,
        }

    def list_creatures(self, **kwargs):
        return {"schema": "creature-page", "arguments": kwargs, "items": []}

    def creature_specialties(self, species_key, *, offset, limit):
        return {
            "schema": "specialties",
            "species": {"speciesKey": species_key},
            "offset": offset,
            "limit": limit,
            "items": [],
        }


class _FakeBuildManager:
    def __init__(self, start_error: Exception | None = None):
        self.started = None
        self.requested = None
        self.cancelled = None
        self.start_error = start_error

    def start(self, options):
        if self.start_error is not None:
            raise self.start_error
        self.started = options
        return {"id": "job-1", "status": "QUEUED"}

    def get(self, job_id=None):
        self.requested = job_id
        return {"id": job_id or "job-1", "status": "RUNNING"}

    def cancel(self, job_id=None):
        self.cancelled = job_id
        return {"id": job_id or "job-1", "status": "CANCELLED"}


class HarvestHttpContractTests(unittest.TestCase):
    def request_build(
        self,
        body: dict[str, object],
        *,
        content_type: str = "application/json",
        host: str = "127.0.0.1:{port}",
        origin: str | None = None,
        start_error: Exception | None = None,
    ) -> tuple[int, dict[str, object], dict[str, str], _FakeBuildManager]:
        manager = _FakeBuildManager(start_error=start_error)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            tool_server.ControlCenterHandler,
        )
        port = int(server.server_address[1])
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            headers = {
                "Content-Type": content_type,
                "Host": host.format(port=port),
            }
            if origin is not None:
                headers["Origin"] = origin.format(port=port)
            with patch.object(tool_server, "HARVEST_BUILD_MANAGER", manager):
                connection = HTTPConnection("127.0.0.1", port, timeout=3)
                try:
                    connection.request(
                        "POST",
                        "/api/harvest/build",
                        body=json.dumps(body),
                        headers=headers,
                    )
                    response = connection.getresponse()
                    raw = response.read().decode("utf-8")
                    response_headers = {
                        key.casefold(): value for key, value in response.getheaders()
                    }
                    return (
                        response.status,
                        json.loads(raw),
                        response_headers,
                        manager,
                    )
                finally:
                    connection.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)

    def test_image_path_accepts_only_exact_sha256_jpeg_identity(self):
        digest = "a" * 64
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expected = root / f"{digest}.jpg"
            expected.write_bytes(b"jpeg")

            self.assertEqual(resolve_harvest_image_path(digest, root), expected.resolve())
            for unsafe in (
                "../secret",
                "a" * 63,
                "A" * 64,
                f"{digest}.png",
                f"{digest}/extra",
            ):
                with self.assertRaises(ValueError):
                    resolve_harvest_image_path(unsafe, root)

    def test_image_path_rejects_hash_named_symlink_outside_cache_root(self):
        digest = "b" * 64
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "images"
            root.mkdir()
            outside = base / "outside.jpg"
            outside.write_bytes(b"private")
            link = root / f"{digest}.jpg"
            try:
                link.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")

            with self.assertRaises(ValueError):
                resolve_harvest_image_path(digest, root)

    def test_query_helpers_bound_limits_and_forward_exact_node_resource_identity(self):
        with patch.object(tool_server, "HARVEST_REPOSITORY", _FakeRepository()):
            page = query_harvest_nodes_for_request(
                "q=metal&onlyMapFamily=TheIsland&"
                "resource=PrimalItemResource_Metal_C&limit=999&offset=2"
            )
            node = query_harvest_node_for_request("node-metal")
            ranking = query_harvest_ranking_for_request(
                "nodeId=node-metal&nodeResourceId=node-resource-metal&limit=99"
            )

        self.assertEqual(page["arguments"]["q"], "metal")
        self.assertEqual(page["arguments"]["only_map_family"], "TheIsland")
        self.assertEqual(
            page["arguments"]["resource"],
            "PrimalItemResource_Metal_C",
        )
        self.assertEqual(page["arguments"]["limit"], 16)
        self.assertEqual(page["arguments"]["offset"], 2)
        self.assertEqual(node["id"], "node-metal")
        self.assertEqual(ranking["resource"]["nodeResourceId"], "node-resource-metal")
        self.assertEqual(ranking["limit"], 10)

    def test_invalid_node_filter_is_returned_as_bounded_client_error(self):
        class _RejectingRepository(_FakeRepository):
            def list_nodes(self, **kwargs):
                raise ValueError("private validation detail")

        with patch.object(tool_server, "HARVEST_REPOSITORY", _RejectingRepository()):
            with self.assertRaises(tool_server.ApiProblem) as raised:
                query_harvest_nodes_for_request(
                    f"onlyMapFamily={'x' * 101}"
                )

        self.assertEqual(raised.exception.status, 400)
        self.assertEqual(
            raised.exception.payload,
            {
                "ok": False,
                "code": "INVALID_HARVEST_NODE_FILTER",
                "error": "Invalid resource-node filter.",
            },
        )

    def test_invalid_dataset_keeps_service_unavailable_error_semantics(self):
        class _InvalidDatasetRepository(_FakeRepository):
            def list_nodes(self, **kwargs):
                raise tool_server.HarvestDatasetInvalid("private dataset detail")

        with patch.object(tool_server, "HARVEST_REPOSITORY", _InvalidDatasetRepository()):
            with self.assertRaises(tool_server.ApiProblem) as raised:
                query_harvest_nodes_for_request("onlyMapFamily=TheIsland")

        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(raised.exception.payload["code"], "HARVEST_DATASET_INVALID")
        self.assertNotIn("private dataset detail", raised.exception.payload["error"])

    def test_creature_query_helpers_bound_pagination_and_keep_exact_species_identity(self):
        with patch.object(tool_server, "HARVEST_REPOSITORY", _FakeRepository()):
            page = query_harvest_creatures_for_request(
                "q=anky&offset=3&limit=999"
            )
            specialties = query_harvest_creature_specialties_for_request(
                "AnKy",
                "offset=2&limit=999",
            )

        self.assertEqual(page["arguments"], {"q": "anky", "offset": 3, "limit": 100})
        self.assertEqual(specialties["species"]["speciesKey"], "AnKy")
        self.assertEqual(specialties["offset"], 2)
        self.assertEqual(specialties["limit"], 100)

    def test_build_query_start_and_cancel_delegate_only_typed_options(self):
        manager = _FakeBuildManager()
        with patch.object(tool_server, "HARVEST_BUILD_MANAGER", manager):
            started = start_harvest_build_for_request(
                {"options": {}}
            )
            current = query_harvest_build_for_request("jobId=job-1")
            cancelled = cancel_harvest_build_for_request("job-1")

        self.assertEqual(manager.started, {})
        self.assertEqual(manager.requested, "job-1")
        self.assertEqual(manager.cancelled, "job-1")
        self.assertEqual(started["status"], "QUEUED")
        self.assertEqual(current["status"], "RUNNING")
        self.assertEqual(cancelled["status"], "CANCELLED")

    def test_public_build_post_accepts_only_exact_empty_options_and_adds_security_headers(self):
        status, payload, headers, manager = self.request_build(
            {"options": {}},
            origin="http://127.0.0.1:{port}",
        )

        self.assertEqual(status, 202)
        self.assertTrue(payload["ok"])
        self.assertEqual(manager.started, {})
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertEqual(headers["referrer-policy"], "no-referrer")
        self.assertIn("default-src 'self'", headers["content-security-policy"])
        self.assertEqual(headers["cache-control"], "no-store")

    def test_public_build_post_rejects_text_plain_before_starting_a_job(self):
        status, payload, _headers, manager = self.request_build(
            {"options": {}},
            content_type="text/plain",
        )

        self.assertEqual(status, 415)
        self.assertEqual(payload["code"], "HARVEST_BUILD_JSON_REQUIRED")
        self.assertIsNone(manager.started)

    def test_public_build_post_rejects_cross_origin_browser_request(self):
        status, payload, _headers, manager = self.request_build(
            {"options": {}},
            origin="https://attacker.example",
        )

        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "HARVEST_BUILD_ORIGIN_FORBIDDEN")
        self.assertIsNone(manager.started)

    def test_public_build_post_allows_local_tool_without_origin(self):
        status, payload, _headers, manager = self.request_build({"options": {}})

        self.assertEqual(status, 202)
        self.assertTrue(payload["ok"])
        self.assertEqual(manager.started, {})

    def test_public_build_post_rejects_non_local_host(self):
        status, payload, _headers, manager = self.request_build(
            {"options": {}},
            host="attacker.example:{port}",
        )

        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "HARVEST_BUILD_HOST_FORBIDDEN")
        self.assertIsNone(manager.started)

    def test_public_build_post_rejects_path_overrides_without_leaking_paths(self):
        for options in (
            {"catalog_output": "README.md"},
            {"scan_cache": ".git/config"},
        ):
            with self.subTest(options=options):
                status, payload, _headers, manager = self.request_build(
                    {"options": options},
                )

                self.assertEqual(status, 400)
                self.assertEqual(payload["code"], "HARVEST_BUILD_OPTIONS_FORBIDDEN")
                self.assertIsNone(manager.started)
                serialized = json.dumps(payload)
                self.assertNotIn("README.md", serialized)
                self.assertNotIn(".git", serialized)
                self.assertNotIn(str(ROOT), serialized)

    def test_public_build_post_hides_internal_details_from_unexpected_errors(self):
        private_path = ROOT / "private-build-detail.txt"
        status, payload, _headers, manager = self.request_build(
            {"options": {}},
            start_error=RuntimeError(str(private_path)),
        )

        self.assertEqual(status, 500)
        self.assertEqual(payload["code"], "HARVEST_BUILD_FAILED")
        self.assertIsNone(manager.started)
        self.assertNotIn(str(private_path), json.dumps(payload))

    def test_public_build_post_requires_the_exact_options_envelope(self):
        for body in ({}, {"options": {}, "extra": True}):
            with self.subTest(body=body):
                status, payload, _headers, manager = self.request_build(body)

                self.assertEqual(status, 400)
                self.assertEqual(payload["code"], "HARVEST_BUILD_REQUEST_INVALID")
                self.assertIsNone(manager.started)

    def test_build_query_without_any_job_returns_idle_instead_of_expected_404_noise(self):
        with patch.object(
            tool_server.HARVEST_BUILD_MANAGER,
            "get",
            side_effect=HarvestBuildJobNotFound(None),
        ):
            current = query_harvest_build_for_request("")

        self.assertIsNone(current)

    def test_build_errors_have_stable_http_status_and_code(self):
        cases = (
            (HarvestBuildArgumentError("bad"), 400, "invalid_harvest_build_arguments"),
            (HarvestBuildAlreadyRunning("active"), 409, "harvest_build_already_running"),
            (HarvestBuildJobNotFound("missing"), 404, "harvest_build_job_not_found"),
        )
        for error, status, code in cases:
            with self.subTest(code=code), patch.object(
                tool_server.HARVEST_BUILD_MANAGER,
                "get",
                side_effect=error,
            ):
                with self.assertRaises(tool_server.ApiProblem) as context:
                    query_harvest_build_for_request("")
            self.assertEqual(context.exception.status, status)
            self.assertEqual(context.exception.payload["code"], code)

    def test_json_responses_are_compact_utf8_for_low_token_transport(self):
        encoded = encode_json_response({"ok": True, "label": "资源", "items": [1, 2]})

        self.assertEqual(
            encoded,
            '{"ok":true,"label":"资源","items":[1,2]}'.encode("utf-8"),
        )

    def test_static_text_assets_declare_utf8(self):
        self.assertEqual(static_content_type("text/html"), "text/html; charset=utf-8")
        self.assertEqual(
            static_content_type("text/javascript"),
            "text/javascript; charset=utf-8",
        )
        self.assertEqual(static_content_type("text/css"), "text/css; charset=utf-8")
        self.assertEqual(static_content_type("image/jpeg"), "image/jpeg")

    def test_server_declares_harvest_read_only_routes(self):
        source = (ROOT / "scripts" / "blueprint_tool_server.py").read_text(encoding="utf-8")

        self.assertIn('parsed.path == "/api/harvest/nodes"', source)
        self.assertIn('parsed.path.startswith("/api/harvest/nodes/")', source)
        self.assertIn('parsed.path == "/api/harvest/rankings"', source)
        self.assertIn('parsed.path.startswith("/api/harvest/images/")', source)
        self.assertIn('parsed.path == "/api/harvest/creatures"', source)
        self.assertIn('parsed.path == "/api/harvest/build"', source)
        self.assertIn('self.path == "/api/harvest/build"', source)
        self.assertIn('self.path.endswith("/cancel")', source)
        self.assertIn("HARVEST_EVALUATION_CATALOG_PATH", source)
        self.assertIn("evaluation_catalog_path=HARVEST_EVALUATION_CATALOG_PATH", source)
        self.assertIn("HARVEST_SQLITE_CATALOG_PATH", source)
        self.assertIn("HARVEST_SQLITE_CATALOG_PATH.is_file()", source)


if __name__ == "__main__":
    unittest.main()
