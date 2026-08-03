from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SCHEMAS = ROOT / "schemas" / "http_api"
NATIVE_FIXTURE = (
    ROOT / "tests" / "fixtures" / "native_evidence" / "native_evidence_v2.json"
)
CLAIM_FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "report_claims"
CLAIM_MANIFEST = (
    CLAIM_FIXTURE_ROOT
    / "reports"
    / "manifests"
    / "fixture.claims.json"
)
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from blueprint_translator.hybrid_evidence import (  # noqa: E402
    build_hybrid_evidence_payload,
    open_hybrid_evidence_repository,
    write_hybrid_evidence_artifacts,
)
from blueprint_translator.native_evidence_repository import (  # noqa: E402
    open_native_evidence_repository,
)
from blueprint_translator.native_evidence_store import (  # noqa: E402
    write_native_evidence_artifacts,
)
from build_hybrid_context_pack import build_hybrid_context_pack  # noqa: E402
from validate_report_claims import validate_claim_manifests  # noqa: E402


SCHEMA_FILES = {
    "blueprint_api_error": "blueprint_api_error_v1.schema.json",
    "blueprint_asset_list_response": "blueprint_asset_list_response_v1.schema.json",
    "blueprint_evidence_health_response": "blueprint_evidence_health_response_v1.schema.json",
    "blueprint_gaps_response": "blueprint_gaps_response_v1.schema.json",
    "blueprint_interpretation_response": "blueprint_interpretation_response_v1.schema.json",
    "blueprint_statement_response": "blueprint_statement_response_v1.schema.json",
    "blueprint_trace_response": "blueprint_trace_response_v1.schema.json",
    "native_request": "native_query_request_v1.schema.json",
    "native_response": "native_query_response_v1.schema.json",
    "hybrid_request": "hybrid_context_request_v1.schema.json",
    "hybrid_response": "hybrid_context_response_v1.schema.json",
    "claim_request": "claim_validation_request_v1.schema.json",
    "claim_response": "claim_validation_response_v1.schema.json",
}


def _load_schema(name: str) -> dict[str, Any]:
    payload = json.loads(
        (SCHEMAS / SCHEMA_FILES[name]).read_text(encoding="utf-8")
    )
    if not isinstance(payload, dict):
        raise AssertionError(f"{name} schema must be an object")
    return payload


def _type_matches(expected: str, value: object) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise AssertionError(f"unsupported test schema type: {expected}")


def assert_matches_schema(
    case: unittest.TestCase,
    schema: dict[str, Any],
    value: object,
    path: str = "$",
) -> None:
    raw_type = schema.get("type")
    if isinstance(raw_type, str):
        case.assertTrue(
            _type_matches(raw_type, value),
            f"{path} must be {raw_type}, got {type(value).__name__}",
        )
    elif isinstance(raw_type, list):
        case.assertTrue(
            any(_type_matches(str(item), value) for item in raw_type),
            f"{path} does not match any declared type",
        )
    if "const" in schema:
        case.assertEqual(value, schema["const"], f"{path} const mismatch")
    if "enum" in schema:
        case.assertIn(value, schema["enum"], f"{path} enum mismatch")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        case.assertIsInstance(properties, dict, f"{path}.properties")
        for required in schema.get("required", []):
            case.assertIn(required, value, f"{path}.{required} is required")
        if schema.get("additionalProperties") is False:
            case.assertEqual(
                set(value) - set(properties),
                set(),
                f"{path} has undeclared fields",
            )
        for key, item in value.items():
            child = properties.get(key)
            if isinstance(child, dict):
                assert_matches_schema(case, child, item, f"{path}.{key}")
    elif isinstance(value, list):
        if "minItems" in schema:
            case.assertGreaterEqual(len(value), int(schema["minItems"]))
        if "maxItems" in schema:
            case.assertLessEqual(len(value), int(schema["maxItems"]))
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                assert_matches_schema(case, item_schema, item, f"{path}[{index}]")
    elif isinstance(value, str):
        if "minLength" in schema:
            case.assertGreaterEqual(len(value), int(schema["minLength"]))
        if "maxLength" in schema:
            case.assertLessEqual(len(value), int(schema["maxLength"]))
        if "pattern" in schema:
            case.assertRegex(value, str(schema["pattern"]), f"{path} pattern")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema:
            case.assertGreaterEqual(value, schema["minimum"])
        if "maximum" in schema:
            case.assertLessEqual(value, schema["maximum"])


class HttpApiContractTests(unittest.TestCase):
    def test_contract_directory_has_only_registered_versioned_json_schemas(self):
        self.assertEqual(
            {path.name for path in SCHEMAS.glob("*.schema.json")},
            set(SCHEMA_FILES.values()),
        )
        for name in SCHEMA_FILES:
            with self.subTest(schema=name):
                schema = _load_schema(name)
                self.assertEqual(
                    schema["$schema"],
                    "https://json-schema.org/draft/2020-12/schema",
                )
                self.assertRegex(str(schema["$id"]), r"/v1$")
                self.assertEqual(schema["type"], "object")
                self.assertIsInstance(schema.get("required"), list)

    def test_native_query_request_and_real_response_match_contract(self):
        request = {"operation": "overview", "budgetTokens": 700}
        assert_matches_schema(self, _load_schema("native_request"), request)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.json"
            shutil.copyfile(NATIVE_FIXTURE, source)
            evidence_dir = root / "evidence"
            write_native_evidence_artifacts(source, evidence_dir)
            with open_native_evidence_repository(evidence_dir) as repository:
                response = repository.query(request)
        assert_matches_schema(self, _load_schema("native_response"), response)

    def test_hybrid_context_request_and_real_response_match_contract(self):
        request = {
            "question": "How does ComputeQuality reach native code?",
            "budgetTokens": 2200,
        }
        assert_matches_schema(self, _load_schema("hybrid_request"), request)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.json"
            shutil.copyfile(NATIVE_FIXTURE, source)
            native_dir = root / "native"
            write_native_evidence_artifacts(source, native_dir)
            with open_native_evidence_repository(native_dir) as native:
                calls = [
                    {
                        "evidenceId": (
                            "bp://111111111111111111111111@"
                            "222222222222222222222222/g/1/n/10"
                        ),
                        "memberName": "ComputeQuality",
                        "owner": "FixtureMath",
                        "signatureHints": ["float"],
                    }
                ]
                payload = build_hybrid_evidence_payload(
                    blueprint_calls=calls,
                    native_functions=native.list_functions(),
                    blueprint_revision_id="2" * 24,
                    blueprint_source_fingerprint="e" * 64,
                    native_evidence_set_id=native.evidence_set_id,
                    native_source_fingerprint=native.source_sha256,
                )
                hybrid_dir = root / "hybrid"
                write_hybrid_evidence_artifacts(payload, hybrid_dir)
                with open_hybrid_evidence_repository(hybrid_dir) as hybrid:
                    response = build_hybrid_context_pack(
                        hybrid,
                        native,
                        question=request["question"],
                        budget=request["budgetTokens"],
                    )
        self.assertEqual(
            response["nativeTrust"],
            {"status": "VERIFIED", "formalValidation": True},
        )
        assert_matches_schema(self, _load_schema("hybrid_response"), response)

    def test_claim_validation_request_and_real_responses_match_contract(self):
        relative_manifest = "reports/manifests/fixture.claims.json"
        for formal in (False, True):
            with self.subTest(formal=formal):
                request = {
                    "manifestPaths": [relative_manifest],
                    "formal": formal,
                }
                assert_matches_schema(
                    self,
                    _load_schema("claim_request"),
                    request,
                )
                response = validate_claim_manifests(
                    CLAIM_FIXTURE_ROOT,
                    [CLAIM_MANIFEST],
                    formal=formal,
                )
                self.assertTrue(response["ok"], response["issues"])
                assert_matches_schema(
                    self,
                    _load_schema("claim_response"),
                    response,
                )

    def test_request_contracts_reject_commands_and_absolute_local_paths(self):
        invalid_cases = (
            (
                "native_request",
                {"operation": "overview", "command": ["python", "tool.py"]},
            ),
            (
                "hybrid_request",
                {
                    "question": "fixture",
                    "nativeEvidenceDir": r"C:\Users\example\native",
                },
            ),
            (
                "claim_request",
                {
                    "manifestPaths": [r"C:\Users\example\claims.json"],
                    "formal": True,
                },
            ),
        )
        for schema_name, payload in invalid_cases:
            with self.subTest(schema=schema_name), self.assertRaises(
                AssertionError
            ):
                assert_matches_schema(self, _load_schema(schema_name), payload)


if __name__ == "__main__":
    unittest.main()
