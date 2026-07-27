from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.fact_store import (  # noqa: E402
    FactValue,
    store_fact,
)
from blueprint_translator.kb_vnext.kb_api import (  # noqa: E402
    KnowledgeApiError,
    VNextKnowledgeService,
)
from blueprint_translator.kb_vnext.kb_context import (  # noqa: E402
    build_bounded_context_pack,
)
from blueprint_translator.kb_vnext.ontology import load_ontology  # noqa: E402
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    CACHE_SCHEMA_SQL,
    FULL_CORE_SCHEMA_SQL,
)
import blueprint_tool_server as tool_server  # noqa: E402


def _snapshot(root: Path) -> VNextKnowledgeService:
    ontology = load_ontology(PROJECT_ROOT / "ontology")
    root.mkdir(parents=True)
    (root / "manifests").mkdir()
    core = sqlite3.connect(root / "core.sqlite")
    core.execute("PRAGMA foreign_keys=ON")
    core.executescript(FULL_CORE_SCHEMA_SQL)
    core.executemany(
        "INSERT INTO metadata VALUES (?, ?)",
        [
            ("schema_version", "ark-kb-core/v1"),
            ("ontology_version", ontology.version),
        ],
    )
    core.execute(
        """
        INSERT INTO source_revisions VALUES (
            1, 'capture', 'capture://fixture', 'sha', 'test', 'v1',
            '2026-07-27T00:00:00Z', 'FRESH'
        )
        """
    )
    core.executemany(
        """
        INSERT INTO entities(
            entity_id, canonical_uri, entity_kind,
            display_name, internal_name, status, confidence
        ) VALUES (?, ?, 'BLUEPRINT_ASSET', ?, ?, 'CONFIRMED', 'HIGH')
        """,
        [
            (1, "/Game/Test/ItemA.ItemA", "Item A", "ItemA"),
            (2, "/Game/Test/ItemB.ItemB", "Item B", "ItemB"),
            (3, "/Game/Test/Other.Other", "Other", "Other"),
        ],
    )
    fact_id = store_fact(
        core,
        ontology=ontology,
        subject_entity_id=1,
        fact_type="ITEM_PROPERTY",
        fact_name="Weight",
        scope_kind="DERIVED_STATIC",
        declared_on_entity_id=1,
        value=FactValue("NUMBER", value_number=2.5),
        status="CONFIRMED",
        confidence="HIGH",
        source_revision_id=1,
        evidence_uri="bp://fixture/item-a/weight",
        evidence_role="DIRECT_FIELD",
    )
    core.execute(
        """
        INSERT INTO knowledge_roles VALUES (
            1, 'entity_definition', 'HIGH', 'CONFIRMED',
            '["class ancestry"]', 'v1'
        )
        """
    )
    core.execute(
        """
        INSERT INTO domain_memberships VALUES (
            1, 'item_use', 'CLASS_ANCESTRY', 'HIGH', 'CONFIRMED',
            'ontology://fixture/item-use', 'v1'
        )
        """
    )
    core.execute(
        """
        INSERT INTO effective_facts VALUES (
            1, 'EFFECTIVE_DEFAULT', 'Weight', ?, NULL,
            '{"classes":[],"overrideDepth":0}', 'RESOLVED', 'hash'
        )
        """,
        (fact_id,),
    )
    core.execute(
        """
        INSERT INTO coverage VALUES (
            1, 'asset_identity', 'CONFIRMED',
            1, 0, 0, 0, 0, 0, ''
        )
        """
    )
    core.commit()
    core.close()
    cache = sqlite3.connect(root / "cache.sqlite")
    cache.executescript(CACHE_SCHEMA_SQL)
    cache.commit()
    cache.close()
    (root / "manifests" / "current.json").write_text(
        json.dumps(
            {
                "schema": "ark-kb-vnext-snapshot/v1",
                "buildId": "fixture-build",
                "source": {
                    "uri": "discovery://fixture",
                    "sha256": "sha",
                },
                "cutover": {
                    "mode": "shadow",
                    "defaultQuerySource": "legacy",
                },
            }
        ),
        encoding="utf-8",
    )
    return VNextKnowledgeService(root)


class KnowledgeApiTests(unittest.TestCase):
    def test_http_routes_inherit_session_origin_and_host_security(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _snapshot(Path(temp_dir) / "vnext")
            server = tool_server.create_control_center_server(
                "127.0.0.1", 0
            )
            port = int(server.server_address[1])
            worker = threading.Thread(
                target=server.serve_forever, daemon=True
            )
            worker.start()
            try:
                with patch.object(
                    tool_server, "KB_VNEXT_SERVICE", service
                ):
                    session = HTTPConnection("127.0.0.1", port, timeout=3)
                    session.request(
                        "GET",
                        "/api/session",
                        headers={"Host": f"127.0.0.1:{port}"},
                    )
                    response = session.getresponse()
                    token = json.loads(
                        response.read().decode("utf-8")
                    )["sessionToken"]
                    session.close()

                    body = json.dumps(
                        {
                            "entity": "ItemA",
                            "factTypes": ["ITEM_PROPERTY"],
                            "budgetTokens": 500,
                        }
                    )
                    request = HTTPConnection(
                        "127.0.0.1", port, timeout=3
                    )
                    request.request(
                        "POST",
                        "/api/kb/query",
                        body=body,
                        headers={
                            "Host": f"127.0.0.1:{port}",
                            "Origin": f"http://127.0.0.1:{port}",
                            "Content-Type": "application/json",
                            "X-Blueprint-Session": str(token),
                        },
                    )
                    response = request.getresponse()
                    payload = json.loads(
                        response.read().decode("utf-8")
                    )
                    request.close()
                    self.assertEqual(response.status, 200)
                    self.assertTrue(payload["ok"])
                    self.assertEqual(
                        payload["route"], "DB_ONLY_COMPLETE"
                    )

                    rejected = HTTPConnection(
                        "127.0.0.1", port, timeout=3
                    )
                    rejected.request(
                        "POST",
                        "/api/kb/query",
                        body=body,
                        headers={
                            "Host": f"127.0.0.1:{port}",
                            "Origin": "https://attacker.example",
                            "Content-Type": "application/json",
                            "X-Blueprint-Session": str(token),
                        },
                    )
                    rejected_response = rejected.getresponse()
                    rejected_payload = json.loads(
                        rejected_response.read().decode("utf-8")
                    )
                    rejected.close()
                    self.assertEqual(rejected_response.status, 403)
                    self.assertEqual(
                        rejected_payload["code"], "ORIGIN_FORBIDDEN"
                    )
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)

    def test_health_and_entity_pages_do_not_expose_local_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _snapshot(Path(temp_dir) / "vnext")
            health = service.health()
            self.assertTrue(health["available"])
            self.assertEqual(health["cutover"]["mode"], "shadow")
            search = service.search_entities(
                query="Item", limit=1, cursor=0
            )
            self.assertEqual(search["returned"], 1)
            self.assertEqual(search["omitted"], 1)
            self.assertIn("cursor=1", search["nextQuery"])
            entity_id = search["items"][0]["entityId"]
            entity = service.entity(int(entity_id))
            facts = service.entity_collection(
                int(entity_id), kind="facts"
            )
            payload = json.dumps(
                {
                    "health": health,
                    "entity": entity,
                    "facts": facts,
                }
            )
            self.assertNotIn("C:\\", payload)
            self.assertNotIn(str(Path(temp_dir)), payload)
            self.assertTrue(facts["evidence"])

    def test_query_is_bounded_and_cached_as_disposable_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            result = service.query(
                {
                    "entity": "ItemA",
                    "factTypes": ["ITEM_PROPERTY"],
                    "budgetTokens": 500,
                    "evidenceLimit": 10,
                }
            )
            self.assertEqual(result["route"], "DB_ONLY_COMPLETE")
            self.assertLessEqual(
                result["contextPack"]["estimatedTokens"], 500
            )
            self.assertEqual(result["gap"], [])
            cache = sqlite3.connect(root / "cache.sqlite")
            try:
                self.assertEqual(
                    cache.execute(
                        "SELECT COUNT(*) FROM query_snapshots"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    cache.execute(
                        "SELECT COUNT(*) FROM context_packs"
                    ).fetchone()[0],
                    1,
                )
            finally:
                cache.close()

    def test_query_validation_rejects_unbounded_or_unknown_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _snapshot(Path(temp_dir) / "vnext")
            with self.assertRaisesRegex(
                KnowledgeApiError, "Unsupported query fields"
            ):
                service.query(
                    {"entity": "ItemA", "command": ["python", "tool.py"]}
                )
            with self.assertRaisesRegex(
                KnowledgeApiError, "budgetTokens"
            ):
                service.query(
                    {"entity": "ItemA", "budgetTokens": 20_000}
                )
            with self.assertRaisesRegex(
                KnowledgeApiError, "factTypes"
            ):
                service.query(
                    {
                        "entity": "ItemA",
                        "factTypes": ["x"] * 21,
                    }
                )

    def test_context_pack_redacts_absolute_local_paths(self):
        pack = build_bounded_context_pack(
            {
                "route": "EVIDENCE_REQUIRED",
                "freshness": "UNKNOWN",
                "entity": {
                    "canonicalUri": r"C:\Users\person\secret.uasset"
                },
                "facts": [],
                "relationships": [],
                "evidence": [
                    {
                        "evidenceUri": r"C:\Users\person\evidence.json",
                        "freshness": "FRESH",
                    }
                ],
                "missingRequirements": [
                    {"code": "MISSING_FACT", "requirement": "FORMULA"}
                ],
                "recommendedProbes": [],
            },
            budget_tokens=500,
        )
        self.assertNotIn(r"C:\Users", pack["content"])
        self.assertIn("[LOCAL_PATH_REDACTED]", pack["content"])

    def test_unbuilt_snapshot_returns_health_gap_and_query_503(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = VNextKnowledgeService(Path(temp_dir) / "vnext")
            health = service.health()
            self.assertFalse(health["available"])
            self.assertEqual(
                health["gap"][0]["code"], "KB_VNEXT_NOT_BUILT"
            )
            with self.assertRaises(KnowledgeApiError) as raised:
                service.query({"entity": "ItemA"})
            self.assertEqual(
                raised.exception.status.value, 503
            )


if __name__ == "__main__":
    unittest.main()
