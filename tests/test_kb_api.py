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
    CORE_SCHEMA_VERSION,
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
            ("schema_version", CORE_SCHEMA_VERSION),
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


def _remove_effective_candidate_capability(root: Path) -> None:
    core = sqlite3.connect(root / "core.sqlite")
    try:
        core.execute("DROP TABLE effective_fact_candidates")
        core.execute(
            """
            UPDATE metadata
            SET value='ark-kb-core/v1'
            WHERE key='schema_version'
            """
        )
        core.commit()
    finally:
        core.close()


def _remove_semantic_derivation_capability(root: Path) -> None:
    core = sqlite3.connect(root / "core.sqlite")
    try:
        core.execute("DROP TABLE semantic_adapter_decisions")
        core.execute("DROP TABLE semantic_adapter_runs")
        core.execute(
            """
            UPDATE metadata
            SET value='ark-kb-core/v2'
            WHERE key='schema_version'
            """
        )
        core.commit()
    finally:
        core.close()


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

    def test_v1_shadow_snapshot_reports_migration_gap_without_sql_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            _remove_effective_candidate_capability(root)

            health = service.health()
            collection = service.entity_collection(
                1, kind="effective-defaults"
            )
            result = service.query(
                {
                    "entity": "/Game/Test/ItemA.ItemA",
                    "factTypes": ["EFFECTIVE_DEFAULT"],
                    "factNames": ["Weight"],
                    "budgetTokens": 500,
                }
            )

        self.assertFalse(health["available"])
        self.assertEqual(health["status"], "MIGRATION_REQUIRED")
        self.assertEqual(health["schemaVersion"], "ark-kb-core/v1")
        self.assertFalse(
            health["capabilities"]["effectiveCandidateExplanations"]
        )
        self.assertEqual(
            health["gap"][0]["code"],
            "KB_VNEXT_SCHEMA_MIGRATION_REQUIRED",
        )
        self.assertEqual(collection["returned"], 1)
        self.assertEqual(
            collection["items"][0]["candidateExplanationStatus"],
            "SCHEMA_MIGRATION_REQUIRED",
        )
        self.assertEqual(collection["items"][0]["candidates"], [])
        self.assertIn(
            "KB_VNEXT_SCHEMA_MIGRATION_REQUIRED",
            {item["code"] for item in collection["gap"]},
        )
        self.assertEqual(result["route"], "EVIDENCE_REQUIRED")
        self.assertIn(
            "SCHEMA_MIGRATION_REQUIRED",
            {item["code"] for item in result["missingRequirements"]},
        )
        self.assertEqual(
            result["facts"][0]["candidateExplanationStatus"],
            "SCHEMA_MIGRATION_REQUIRED",
        )
        self.assertIn(
            "rebuild_core_v3_snapshot",
            {
                item["operation"]
                for item in result["recommendedProbes"]
            },
        )

    def test_v2_core_without_semantic_derivations_requires_migration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            _remove_semantic_derivation_capability(root)

            health = service.health()

        self.assertFalse(health["available"])
        self.assertEqual(health["status"], "MIGRATION_REQUIRED")
        self.assertEqual(health["schemaVersion"], "ark-kb-core/v2")
        self.assertTrue(
            health["capabilities"]["effectiveCandidateExplanations"]
        )
        self.assertFalse(
            health["capabilities"]["semanticAdapterDerivations"]
        )
        self.assertEqual(
            health["gap"][0]["code"],
            "KB_VNEXT_SCHEMA_MIGRATION_REQUIRED",
        )

    def test_effective_defaults_expose_unresolved_rows_without_stringifying_none(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            core = sqlite3.connect(root / "core.sqlite")
            try:
                ontology = load_ontology(PROJECT_ROOT / "ontology")
                selected_fact_id = int(
                    core.execute(
                        """
                        SELECT fact_id FROM effective_facts
                        WHERE entity_id=1
                          AND fact_type='EFFECTIVE_DEFAULT'
                          AND fact_name='Weight'
                        """
                    ).fetchone()[0]
                )
                rejected_fact_id = store_fact(
                    core,
                    ontology=ontology,
                    subject_entity_id=2,
                    fact_type="DECLARED_DEFAULT",
                    fact_name="MissingWeight",
                    scope_kind="DECLARED",
                    declared_on_entity_id=2,
                    value=FactValue("UNKNOWN"),
                    status="NOT_RECOVERED",
                    confidence="LOW",
                    source_revision_id=1,
                    evidence_uri="bp://fixture/item-b/missing-weight",
                    evidence_role="DEFAULT_VALUE",
                )
                core.execute(
                    """
                    INSERT INTO effective_facts(
                        entity_id, fact_type, fact_name, fact_id,
                        inherited_from_entity_id, resolution_chain_json,
                        resolution_status, source_revision_set_hash
                    ) VALUES (
                        1, 'EFFECTIVE_DEFAULT', 'MissingWeight', NULL, NULL,
                        '{"schema":"ark-kb-effective-path/v1","classes":[],"edges":[]}',
                        'NOT_RECOVERED', 'hash'
                    )
                    """
                )
                core.execute(
                    """
                    INSERT INTO effective_fact_candidates VALUES (
                        1, 'EFFECTIVE_DEFAULT', 'Weight', ?, 1, 0,
                        'CONFIRMED', 1, ''
                    )
                    """,
                    (selected_fact_id,),
                )
                core.execute(
                    """
                    INSERT INTO effective_fact_candidates VALUES (
                        1, 'EFFECTIVE_DEFAULT', 'MissingWeight', ?, 2, 1,
                        'PARENT_CHAIN_OPEN', 0, 'PARENT_CHAIN_OPEN'
                    )
                    """,
                    (rejected_fact_id,),
                )
                core.commit()
            finally:
                core.close()

            result = service.entity_collection(
                1, kind="effective-defaults"
            )

        self.assertEqual(result["returned"], 2)
        unresolved = next(
            item
            for item in result["items"]
            if item["factName"] == "MissingWeight"
        )
        self.assertIsNone(unresolved["factId"])
        self.assertIsNone(unresolved["valueKind"])
        self.assertIsNone(unresolved["status"])
        self.assertIsNone(unresolved["confidence"])
        self.assertEqual(
            unresolved["resolutionStatus"], "NOT_RECOVERED"
        )
        self.assertEqual(unresolved["candidateTotal"], 1)
        self.assertEqual(unresolved["candidateReturned"], 1)
        self.assertEqual(unresolved["candidateOmitted"], 0)
        rejected = unresolved["candidates"][0]
        self.assertEqual(rejected["candidateFactId"], rejected_fact_id)
        self.assertEqual(rejected["declaredOnEntityId"], 2)
        self.assertEqual(
            rejected["declaredOnUri"], "/Game/Test/ItemB.ItemB"
        )
        self.assertFalse(rejected["selected"])
        self.assertEqual(
            rejected["rejectionReason"], "PARENT_CHAIN_OPEN"
        )
        resolved = next(
            item for item in result["items"] if item["factName"] == "Weight"
        )
        self.assertEqual(resolved["candidateTotal"], 1)
        self.assertTrue(resolved["candidates"][0]["selected"])
        self.assertNotIn('"None"', json.dumps(result))
        self.assertTrue(result["gap"])
        self.assertEqual(result["freshness"], "UNKNOWN")
        self.assertEqual(
            {item["factId"] for item in result["evidence"]},
            {
                item["factId"]
                for item in result["items"]
                if item["factId"] is not None
            },
        )

    def test_effective_defaults_require_fresh_evidence_for_each_current_fact(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            core = sqlite3.connect(root / "core.sqlite")
            try:
                fact_id = int(
                    core.execute(
                        """
                        SELECT fact_id FROM effective_facts
                        WHERE entity_id=1 AND fact_name='Weight'
                        """
                    ).fetchone()[0]
                )
                core.execute(
                    """
                    INSERT INTO source_revisions VALUES (
                        2, 'capture', 'capture://historical', 'old-sha',
                        'test', 'v0', '2026-07-26T00:00:00Z', 'STALE'
                    )
                    """
                )
                core.execute(
                    """
                    INSERT INTO fact_evidence VALUES (
                        ?, 2, 'bp://fixture/item-a/weight-old',
                        'HISTORICAL_VALUE'
                    )
                    """,
                    (fact_id,),
                )
                core.commit()

                mixed = service.entity_collection(
                    1, kind="effective-defaults"
                )
                self.assertEqual(mixed["freshness"], "FRESH")
                self.assertEqual(mixed["gap"], [])
                self.assertEqual(mixed["items"][0]["candidates"], [])
                self.assertEqual(
                    mixed["items"][0]["candidateTotal"], 0
                )
                self.assertEqual(
                    mixed["items"][0]["candidateOmitted"], 0
                )

                core.execute(
                    """
                    UPDATE source_revisions
                    SET freshness_status='STALE'
                    WHERE revision_id=1
                    """
                )
                core.commit()
                stale = service.entity_collection(
                    1, kind="effective-defaults"
                )
                self.assertEqual(stale["freshness"], "STALE")
                self.assertIn(
                    "STALE_SOURCE",
                    {item["code"] for item in stale["gap"]},
                )
            finally:
                core.close()

    def test_effective_defaults_reject_missing_current_fact_projection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            core = sqlite3.connect(root / "core.sqlite")
            try:
                core.execute(
                    """
                    UPDATE facts SET current=0
                    WHERE fact_id=(
                        SELECT fact_id FROM effective_facts
                        WHERE entity_id=1 AND fact_name='Weight'
                    )
                    """
                )
                core.commit()
            finally:
                core.close()

            result = service.entity_collection(
                1, kind="effective-defaults"
            )

        self.assertEqual(result["freshness"], "UNKNOWN")
        self.assertTrue(result["gap"])
        self.assertIsNone(result["items"][0]["valueKind"])
        self.assertIsNone(result["items"][0]["status"])

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

    def test_context_pack_renders_unresolved_effective_fact_without_none(self):
        pack = build_bounded_context_pack(
            {
                "route": "EVIDENCE_REQUIRED",
                "freshness": "UNKNOWN",
                "entity": {"canonicalUri": "ark://class/ItemA"},
                "facts": [
                    {
                        "factType": "EFFECTIVE_DEFAULT",
                        "factName": "MissingWeight",
                        "status": None,
                        "valueKind": None,
                        "resolutionStatus": "PARENT_CHAIN_OPEN",
                    }
                ],
                "relationships": [],
                "evidence": [],
                "missingRequirements": [],
                "recommendedProbes": [],
            },
            budget_tokens=500,
        )

        self.assertIn(
            "[PARENT_CHAIN_OPEN/UNKNOWN]",
            pack["content"],
        )
        self.assertNotIn("None/None", pack["content"])

    def test_context_pack_compacts_selected_and_rejected_candidates(self):
        candidates = [
            {
                "candidateFactId": 101,
                "declaredOnEntityId": 1,
                "declaredOnUri": "/Game/Test/ItemA.ItemA",
                "inheritanceDepth": 0,
                "pathStatus": "CONFIRMED",
                "selected": True,
                "rejectionReason": "",
                "valueKind": "INTEGER",
                "valueInteger": 7,
                "status": "CONFIRMED",
            },
            *[
                {
                    "candidateFactId": candidate_id,
                    "declaredOnEntityId": 2,
                    "declaredOnUri": "/Game/Test/ItemB.ItemB",
                    "inheritanceDepth": candidate_id - 100,
                    "pathStatus": "CONFIRMED",
                    "selected": False,
                    "rejectionReason": "SHADOWED_BY_NEARER_USABLE",
                    "valueKind": "INTEGER",
                    "valueInteger": candidate_id,
                    "status": "CONFIRMED",
                }
                for candidate_id in range(102, 107)
            ],
        ]
        pack = build_bounded_context_pack(
            {
                "route": "EVIDENCE_REQUIRED",
                "freshness": "UNKNOWN",
                "entity": {"canonicalUri": "ark://class/ItemA"},
                "facts": [
                    {
                        "factType": "EFFECTIVE_DEFAULT",
                        "factName": "Rate",
                        "factId": 101,
                        "resolutionStatus": "RESOLVED",
                        "valueKind": "INTEGER",
                        "valueInteger": 7,
                        "status": "CONFIRMED",
                        "candidates": candidates,
                        "candidateTotal": 10,
                        "candidateReturned": 6,
                        "candidateOmitted": 4,
                    },
                    {
                        "factType": "EFFECTIVE_DEFAULT",
                        "factName": "UnresolvedRate",
                        "factId": None,
                        "resolutionStatus": "PARENT_CHAIN_OPEN",
                        "valueKind": None,
                        "status": None,
                        "candidates": [
                            {
                                "candidateFactId": 201,
                                "declaredOnEntityId": 2,
                                "declaredOnUri": (
                                    "/Game/Test/ItemB.ItemB"
                                ),
                                "inheritanceDepth": 1,
                                "pathStatus": "PARENT_CHAIN_OPEN",
                                "selected": False,
                                "rejectionReason": "PARENT_CHAIN_OPEN",
                                "valueKind": "UNKNOWN",
                                "status": "NOT_RECOVERED",
                            }
                        ],
                        "candidateTotal": 1,
                        "candidateReturned": 1,
                        "candidateOmitted": 0,
                    },
                ],
                "relationships": [],
                "evidence": [],
                "missingRequirements": [],
                "recommendedProbes": [],
            },
            budget_tokens=700,
        )

        content = pack["content"]
        self.assertIn("## Effective candidates", content)
        self.assertIn("selected candidate #101", content)
        self.assertIn("rejected candidate #102", content)
        self.assertIn("SHADOWED_BY_NEARER_USABLE", content)
        self.assertIn("7 candidates omitted", content)
        self.assertIn(
            "UnresolvedRate unresolved=PARENT_CHAIN_OPEN",
            content,
        )
        self.assertIn("rejected candidate #201", content)
        self.assertLessEqual(content.count("candidate #"), 4)
        self.assertLessEqual(pack["estimatedTokens"], 700)

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
