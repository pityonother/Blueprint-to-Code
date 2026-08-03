from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
from unittest.mock import patch

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from blueprint_server.request import ApiProblem  # noqa: E402
from blueprint_server.routes_blueprint import (  # noqa: E402
    BlueprintRouteResult,
    blueprint_get_payload,
)
import blueprint_tool_server as tool_server  # noqa: E402
from blueprint_translator.interpretation_publication import (  # noqa: E402
    publish_interpretation,
)
from interpretation_fixture import (  # noqa: E402
    interpretation_payload,
    publish_interpretation_fixture,
)


ASSET_ID = "a" * 24
EVIDENCE_REVISION = "b" * 24
INTERPRETATION_REVISION = "c" * 24
EVIDENCE_MANIFEST_SHA = "d" * 64
INTERPRETATION_MANIFEST_SHA = "e" * 64
INTERPRETATION_POINTER_SHA = "f" * 64
GRAPH_REF = f"bp://{ASSET_ID}@{EVIDENCE_REVISION}/g/7"
FIRST_STATEMENT = "statement://fixture/event/0"
SECOND_STATEMENT = "statement://fixture/call/1"


def _schema_validator(filename: str) -> Draft202012Validator:
    schema = json.loads(
        (ROOT / "schemas" / "http_api" / filename).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _state(*, revision_id: str = INTERPRETATION_REVISION) -> SimpleNamespace:
    statements = [
        {
            "id": FIRST_STATEMENT,
            "kind": "EVENT",
            "text": "Event BeginPlay",
            "status": "CONFIRMED",
            "evidenceRefs": [f"{GRAPH_REF}/n/1"],
            "gapRefs": [],
            "graphRef": GRAPH_REF,
            "sourceOrder": 0,
        },
        {
            "id": SECOND_STATEMENT,
            "kind": "CALL",
            "text": "Call <unsafe> & continue",
            "status": "CONFIRMED",
            "evidenceRefs": [f"{GRAPH_REF}/n/2"],
            "gapRefs": [],
            "graphRef": GRAPH_REF,
            "sourceOrder": 1,
        },
        {
            "id": "statement://fixture/gap/2",
            "kind": "GAP",
            "text": "External body is unavailable",
            "status": "SOURCE_NOT_AVAILABLE",
            "evidenceRefs": [],
            "gapRefs": ["gap://fixture/external/0"],
            "graphRef": GRAPH_REF,
            "sourceOrder": 2,
        },
    ]
    interpretation = {
        "schema": "blueprint-to-code.blueprint-interpretation/v1",
        "assetId": ASSET_ID,
        "objectPath": "/Game/Test/Fixture.Fixture",
        "evidenceRevisionId": EVIDENCE_REVISION,
        "evidenceManifestSha256": EVIDENCE_MANIFEST_SHA,
        "interpreterVersion": "fixture-interpreter",
        "schemaVersion": "blueprint-to-code.blueprint-interpretation/v1",
        "semanticDigest": "1" * 64,
        "generatedAt": "2026-08-03T00:00:00Z",
        "assetSummary": {"graphCount": 1, "confirmedStatementCount": 2},
        "heuristicReviewHints": [],
        "statements": statements,
    }
    return SimpleNamespace(
        manifest_sha256=INTERPRETATION_MANIFEST_SHA,
        pointer_sha256=INTERPRETATION_POINTER_SHA,
        manifest={
            "revisionId": revision_id,
            "evidenceRevisionId": EVIDENCE_REVISION,
            "evidenceManifestSha256": EVIDENCE_MANIFEST_SHA,
        },
        interpretation=interpretation,
        trace=[
            {
                "statementId": FIRST_STATEMENT,
                "evidenceRefs": [f"{GRAPH_REF}/n/1"],
                "pseudocodeLine": 2,
            },
            {
                "statementId": SECOND_STATEMENT,
                "evidenceRefs": [f"{GRAPH_REF}/n/2"],
                "pseudocodeLine": 3,
            },
        ],
        gaps=[
            {
                "id": "gap://fixture/external/0",
                "code": "EXTERNAL_CALLABLE_BODY_NOT_AVAILABLE",
                "status": "SOURCE_NOT_AVAILABLE",
                "graphRef": GRAPH_REF,
                "evidenceRefs": [f"{GRAPH_REF}/n/2"],
            }
        ],
        pseudocode="EVIDENCE-DERIVED PSEUDOCODE\n",
        markdown="# Fixture\n",
        revision_id=revision_id,
    )


def _assert_path_free(case: unittest.TestCase, value: object, forbidden: str) -> None:
    encoded = json.dumps(value, ensure_ascii=False)
    case.assertNotIn(forbidden, encoded)
    case.assertNotRegex(encoded, r"(?i)(?<![A-Za-z0-9_])[a-z]:[\\/]")


class BlueprintInterpretationHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.capture_root = Path(self._temporary.name) / "captures"
        self.asset_dir = self.capture_root / "Fixture"
        self.asset_dir.mkdir(parents=True)

    def route(
        self,
        path: str,
        query: str = "",
        *,
        state: SimpleNamespace | None = None,
    ):
        current = state or _state()
        return blueprint_get_payload(
            path,
            query,
            capture_root=self.capture_root,
            load_current=lambda _asset_dir: current,
            inspect_health=lambda _asset_dir: {
                "status": "READY",
                "assetId": ASSET_ID,
                "evidenceRevisionId": EVIDENCE_REVISION,
                "evidenceManifestSha256": EVIDENCE_MANIFEST_SHA,
                "interpretationRevisionId": current.revision_id,
                "interpretationManifestSha256": current.manifest_sha256,
            },
        )

    def test_asset_list_is_path_free_and_uses_an_opaque_cursor(self) -> None:
        for name in ("Alpha", "Beta", "Gamma"):
            (self.capture_root / name).mkdir()

        first = self.route("/api/blueprint/assets", "limit=2")
        self.assertEqual(first.status, 200)
        self.assertEqual([item["asset"] for item in first.payload["items"]], ["Alpha", "Beta"])
        cursor = first.payload["page"]["nextCursor"]
        self.assertIsInstance(cursor, str)
        self.assertNotIn("Beta", cursor)

        second = self.route("/api/blueprint/assets", f"limit=2&cursor={cursor}")
        self.assertEqual([item["asset"] for item in second.payload["items"]], ["Fixture", "Gamma"])
        _assert_path_free(self, first.payload, str(self.capture_root))
        _assert_path_free(self, second.payload, str(self.capture_root))

    def test_interpretation_filters_and_paginates_statements(self) -> None:
        first = self.route(
            "/api/blueprint/assets/Fixture/interpretation",
            "status=CONFIRMED&limit=1",
        )
        self.assertEqual(first.status, 200)
        self.assertEqual(first.payload["schema"], "blueprint-to-code.blueprint-interpretation-response/v1")
        self.assertEqual([item["id"] for item in first.payload["items"]], [FIRST_STATEMENT])
        self.assertEqual(first.payload["identity"]["evidence"]["revisionId"], EVIDENCE_REVISION)
        cursor = first.payload["page"]["nextCursor"]

        second = self.route(
            "/api/blueprint/assets/Fixture/interpretation",
            f"status=CONFIRMED&limit=1&cursor={cursor}",
        )
        self.assertEqual([item["id"] for item in second.payload["items"]], [SECOND_STATEMENT])
        self.assertIsNone(second.payload["page"]["nextCursor"])
        _assert_path_free(self, first.payload, str(self.capture_root))

    def test_interpretation_summary_and_hint_preview_are_bounded(self) -> None:
        state = _state()
        state.interpretation["assetSummary"].update(
            {
                "graphInventory": [{"large": "value"}] * 10_000,
                "graphStatusCounts": {f"STATUS_{index}": index for index in range(40)},
            }
        )
        state.interpretation["heuristicReviewHints"] = [
            {
                "id": f"hint://fixture/{index}",
                "topic": "review",
                "text": f"Review hint {index}",
                "basis": "KEYWORD_AND_NAME_HEURISTIC",
                "confidence": "HEURISTIC",
                "notEvidence": True,
                "reviewRef": GRAPH_REF,
            }
            for index in range(25)
        ]

        result = self.route(
            "/api/blueprint/assets/Fixture/interpretation",
            "limit=1",
            state=state,
        )

        self.assertEqual(len(result.payload["items"]), 1)
        self.assertEqual(len(result.payload["heuristicReviewHints"]), 20)
        self.assertEqual(result.payload["summary"]["heuristicReviewHintCount"], 25)
        self.assertTrue(result.payload["summary"]["heuristicReviewHintsTruncated"])
        self.assertEqual(len(result.payload["summary"]["graphStatusCounts"]), 32)
        self.assertNotIn("graphInventory", result.payload["summary"])
        _schema_validator(
            "blueprint_interpretation_response_v1.schema.json"
        ).validate(result.payload)

    def test_cursor_is_bound_to_the_current_interpretation_revision(self) -> None:
        first = self.route(
            "/api/blueprint/assets/Fixture/interpretation",
            "status=CONFIRMED&limit=1",
        )
        cursor = first.payload["page"]["nextCursor"]
        changed = _state(revision_id="9" * 24)

        with self.assertRaises(ApiProblem) as raised:
            self.route(
                "/api/blueprint/assets/Fixture/interpretation",
                f"status=CONFIRMED&limit=1&cursor={cursor}",
                state=changed,
            )

        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(raised.exception.payload["code"], "BLUEPRINT_CURSOR_STALE")

    def test_statement_id_is_url_encoded_and_returns_only_its_trace(self) -> None:
        encoded = quote(FIRST_STATEMENT, safe="")
        result = self.route(
            f"/api/blueprint/assets/Fixture/statements/{encoded}",
            "limit=10",
        )

        self.assertEqual(result.payload["statement"]["id"], FIRST_STATEMENT)
        self.assertEqual(len(result.payload["items"]), 1)
        self.assertEqual(result.payload["items"][0]["statementId"], FIRST_STATEMENT)

    def test_gap_route_filters_by_exact_code(self) -> None:
        result = self.route(
            "/api/blueprint/assets/Fixture/gaps",
            "code=EXTERNAL_CALLABLE_BODY_NOT_AVAILABLE&limit=10",
        )
        self.assertEqual(len(result.payload["items"]), 1)
        self.assertEqual(
            result.payload["items"][0]["code"],
            "EXTERNAL_CALLABLE_BODY_NOT_AVAILABLE",
        )

    def test_gap_filters_are_bounded_and_use_contract_statuses(self) -> None:
        for query in (
            f"code={'A' * 129}",
            "code=contains-hyphen",
            f"status={'A' * 129}",
            "status=CONFIRMED",
        ):
            with self.subTest(query=query), self.assertRaises(ApiProblem) as raised:
                self.route("/api/blueprint/assets/Fixture/gaps", query)
            self.assertEqual(raised.exception.status, HTTPStatus.BAD_REQUEST)
            self.assertEqual(
                raised.exception.payload["code"],
                "BLUEPRINT_QUERY_INVALID",
            )

    def test_stale_interpretation_error_is_stable_and_path_free(self) -> None:
        class StaleInterpretation(ValueError):
            code = "INTERPRETATION_STALE_EVIDENCE"

        with self.assertRaises(ApiProblem) as raised:
            blueprint_get_payload(
                "/api/blueprint/assets/Fixture/interpretation",
                "",
                capture_root=self.capture_root,
                load_current=lambda _asset_dir: (_ for _ in ()).throw(
                    StaleInterpretation(
                        "changed under " + "C:" + r"\Users\fixture\capture"
                    )
                ),
                inspect_health=lambda _asset_dir: {},
            )

        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(
            raised.exception.payload["code"],
            "BLUEPRINT_INTERPRETATION_STALE",
        )
        _assert_path_free(self, raised.exception.payload, str(self.capture_root))

    def test_invalid_asset_and_unknown_blueprint_route_have_stable_errors(self) -> None:
        for path, expected_code in (
            ("/api/blueprint/assets/%2e%2e/interpretation", "BLUEPRINT_ASSET_ID_INVALID"),
            ("/api/blueprint/unknown", "API_ENDPOINT_NOT_FOUND"),
        ):
            with self.subTest(path=path), self.assertRaises(ApiProblem) as raised:
                self.route(path)
            self.assertEqual(raised.exception.payload["code"], expected_code)
            _assert_path_free(self, raised.exception.payload, str(self.capture_root))
            _schema_validator("blueprint_api_error_v1.schema.json").validate(
                raised.exception.payload
            )


class BlueprintInterpretationPublishedArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.capture_root = Path(self._temporary.name) / "captures"
        self.asset_dir, _source_path, _payload = publish_interpretation_fixture(
            self.capture_root
        )
        self.published = publish_interpretation(self.asset_dir, budget=32_000)

    def route(self, endpoint: str, query: str = "") -> BlueprintRouteResult:
        result = blueprint_get_payload(
            f"/api/blueprint/assets/InterpretationFixture/{endpoint}",
            query,
            capture_root=self.capture_root,
        )
        self.assertIsNotNone(result)
        return result

    def test_real_publication_routes_are_recursively_path_free(self) -> None:
        for endpoint in ("evidence/health", "interpretation", "trace", "gaps"):
            with self.subTest(endpoint=endpoint):
                result = self.route(endpoint, "limit=5" if endpoint != "evidence/health" else "")
                self.assertEqual(result.status, HTTPStatus.OK)
                _assert_path_free(self, result.payload, str(self.capture_root))
                encoded = json.dumps(result.payload, ensure_ascii=False)
                self.assertNotIn(str(self.asset_dir), encoded)
                self.assertNotIn(str(self.asset_dir).replace("\\", "/"), encoded)

    def test_real_route_payloads_match_their_strict_public_schemas(self) -> None:
        asset_list = blueprint_get_payload(
            "/api/blueprint/assets",
            "limit=5",
            capture_root=self.capture_root,
        )
        self.assertIsNotNone(asset_list)
        interpretation = self.route("interpretation", "limit=5")
        statement_id = str(interpreted["id"]) if (
            interpreted := interpretation.payload["items"][0]
        ) else ""
        statement = self.route(
            f"statements/{quote(statement_id, safe='')}",
            "limit=5",
        )
        responses = (
            (
                "blueprint_asset_list_response_v1.schema.json",
                asset_list.payload,
            ),
            (
                "blueprint_evidence_health_response_v1.schema.json",
                self.route("evidence/health").payload,
            ),
            (
                "blueprint_interpretation_response_v1.schema.json",
                interpretation.payload,
            ),
            (
                "blueprint_statement_response_v1.schema.json",
                statement.payload,
            ),
            (
                "blueprint_trace_response_v1.schema.json",
                self.route("trace", "limit=5").payload,
            ),
            (
                "blueprint_gaps_response_v1.schema.json",
                self.route("gaps", "limit=5").payload,
            ),
        )
        for filename, payload in responses:
            with self.subTest(schema=filename):
                _schema_validator(filename).validate(payload)

        malformed = json.loads(json.dumps(interpretation.payload))
        malformed["items"][0]["unexpected"] = True
        with self.assertRaises(ValidationError):
            _schema_validator(
                "blueprint_interpretation_response_v1.schema.json"
            ).validate(malformed)

    def test_real_cursor_is_bound_to_published_revision_and_query(self) -> None:
        first = self.route("interpretation", "limit=1")
        cursor = first.payload["page"]["nextCursor"]
        self.assertIsInstance(cursor, str)

        second = self.route("interpretation", f"limit=1&cursor={cursor}")
        self.assertEqual(second.status, HTTPStatus.OK)
        self.assertNotEqual(first.payload["items"], second.payload["items"])

        with self.assertRaises(ApiProblem) as raised:
            self.route("interpretation", f"kind=EVENT&limit=1&cursor={cursor}")
        self.assertEqual(
            raised.exception.payload["code"],
            "BLUEPRINT_CURSOR_QUERY_MISMATCH",
        )

    def test_real_stale_source_is_reported_as_path_free_409(self) -> None:
        source_path = self.asset_dir / "source" / "InterpretationFixture.uasset"
        source_path.write_bytes(source_path.read_bytes() + b"-changed")

        with self.assertRaises(ApiProblem) as raised:
            self.route("interpretation")

        self.assertEqual(raised.exception.status, HTTPStatus.CONFLICT)
        self.assertEqual(
            raised.exception.payload["code"],
            "BLUEPRINT_EVIDENCE_STALE",
        )
        _assert_path_free(self, raised.exception.payload, str(self.capture_root))

    def test_unconfirmed_class_default_gap_round_trips_through_http_schemas(
        self,
    ) -> None:
        name = "HeuristicDefaultHttpFixture"
        payload = interpretation_payload(name)
        payload["class_defaults"]["variables"]["HeuristicDefault"] = {
            "value": 7,
            "type": "IntProperty",
            "confidence": "heuristic",
            "source": "pattern_scan",
        }
        asset_dir, _source_path, _payload = publish_interpretation_fixture(
            self.capture_root,
            name=name,
            payload=payload,
        )
        publish_interpretation(asset_dir, budget=32_000)

        interpretation = blueprint_get_payload(
            f"/api/blueprint/assets/{name}/interpretation",
            "kind=GAP&limit=100",
            capture_root=self.capture_root,
        )
        gaps = blueprint_get_payload(
            f"/api/blueprint/assets/{name}/gaps",
            "code=DEFAULT_NOT_RECOVERED&limit=100",
            capture_root=self.capture_root,
        )
        self.assertIsNotNone(interpretation)
        self.assertIsNotNone(gaps)
        matching_gap = next(
            item
            for item in gaps.payload["items"]
            if item["source"] == "CLASS_DEFAULT_PROVENANCE"
        )
        matching_statement = next(
            item
            for item in interpretation.payload["items"]
            if matching_gap["id"] in item["gapRefs"]
        )

        self.assertEqual(matching_gap["graphRef"], "")
        self.assertEqual(matching_statement["graphRef"], "")
        self.assertTrue(
            any("/default/" in ref for ref in matching_gap["evidenceRefs"])
        )
        _schema_validator(
            "blueprint_interpretation_response_v1.schema.json"
        ).validate(interpretation.payload)
        _schema_validator("blueprint_gaps_response_v1.schema.json").validate(
            gaps.payload
        )

        statement = blueprint_get_payload(
            f"/api/blueprint/assets/{name}/statements/"
            f"{quote(matching_statement['id'], safe='')}",
            "limit=100",
            capture_root=self.capture_root,
        )
        self.assertIsNotNone(statement)
        _schema_validator("blueprint_statement_response_v1.schema.json").validate(
            statement.payload
        )


class BlueprintInterpretationServerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = tool_server.create_control_center_server("127.0.0.1", 0)
        self.port = int(self.server.server_address[1])
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=3)

    def request(self, host: str) -> tuple[int, dict[str, object]]:
        connection = HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(
                "GET",
                "/api/blueprint/assets",
                headers={"Host": host},
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    def test_handler_dispatches_blueprint_get_result(self) -> None:
        result = BlueprintRouteResult(
            HTTPStatus.OK,
            {
                "ok": True,
                "schema": "blueprint-to-code.blueprint-asset-list-response/v1",
                "items": [],
                "page": {"limit": 25, "returned": 0, "total": 0, "nextCursor": None},
            },
        )
        with patch.object(tool_server, "blueprint_get_payload", return_value=result):
            status, payload = self.request(f"127.0.0.1:{self.port}")

        self.assertEqual(status, 200)
        self.assertEqual(payload, result.payload)

    def test_handler_rejects_untrusted_host_before_blueprint_dispatch(self) -> None:
        with patch.object(tool_server, "blueprint_get_payload") as dispatch:
            status, payload = self.request(f"attacker.example:{self.port}")

        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "HOST_FORBIDDEN")
        dispatch.assert_not_called()

    def test_handler_maps_unknown_blueprint_failure_to_path_free_schema_error(self) -> None:
        private_path = "C:" + r"\Users\fixture\private\evidence.sqlite"
        with patch.object(
            tool_server,
            "blueprint_get_payload",
            side_effect=OSError(f"failed to read {private_path}"),
        ):
            status, payload = self.request(f"127.0.0.1:{self.port}")

        self.assertEqual(status, 500)
        self.assertEqual(payload["code"], "BLUEPRINT_INTERNAL_ERROR")
        self.assertNotIn(private_path, json.dumps(payload))
        _schema_validator("blueprint_api_error_v1.schema.json").validate(payload)


if __name__ == "__main__":
    unittest.main()
