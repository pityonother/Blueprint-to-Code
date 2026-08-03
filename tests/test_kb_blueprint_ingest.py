from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
import zlib
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.asset_ledger import (  # noqa: E402
    metadata_fingerprint,
)
from blueprint_translator.evidence_publication import (  # noqa: E402
    migrate_v2_evidence_to_v3,
)
from blueprint_translator.evidence_repository import (  # noqa: E402
    resolve_asset_evidence_state,
)
from blueprint_translator.evidence_schema import (  # noqa: E402
    ensure_evidence_schema,
    make_asset_id,
    make_default_ref,
    make_revision_id,
)
from blueprint_translator.kb_vnext.blueprint_ingest import (  # noqa: E402
    materialize_blueprint_defaults,
)
from blueprint_translator.kb_vnext.fact_store import (  # noqa: E402
    materialize_declared_defaults,
)
from blueprint_translator.kb_vnext.ontology import load_ontology  # noqa: E402
from blueprint_translator.kb_vnext.source_manifest import (  # noqa: E402
    SourceRevision,
    live_capture_evidence_fingerprint,
    source_id,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    FULL_CORE_SCHEMA_SQL,
)


DIRECT_PARSER = "uasset-graph-reader-evidence-v3"
LEGACY_PARSER = "legacy-capture-evidence-v3"
EVIDENCE_SCHEMA = "ark.blueprint.evidence.v2"
OBJECT_PATH = "/Game/Test/BP_Test.BP_Test"
ASSET_ID = make_asset_id(OBJECT_PATH)


def _source_identity(
    package_path: Path,
    parser_version: str = DIRECT_PARSER,
    source_mode: str = "direct",
) -> tuple[list[tuple[str, str, int, str]], str, str, str]:
    package_bytes = b"synthetic-uasset-package"
    package_path.write_bytes(package_bytes)
    rows = [
        (
            f"binary/{package_path.name}",
            hashlib.sha256(package_bytes).hexdigest(),
            len(package_bytes),
            "package_binary",
        ),
    ]
    if source_mode in {"direct", "both"}:
        rows.append(
            (
                "@memory/normalized_graph_facts",
                "a" * 64,
                123,
                "in_memory_capture",
            )
        )
    if source_mode in {"legacy", "both"}:
        rows.append(
            (
                "graphs_from_uasset_manifest.json",
                "c" * 64,
                123,
                "graph_manifest",
            )
        )
    hashes = {path: digest for path, digest, _size, _kind in rows}
    compact = json.dumps(
        sorted(hashes.items()),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    fingerprint = hashlib.sha256(compact).hexdigest()
    revision = make_revision_id(
        hashes,
        parser_version=parser_version,
        schema_version=EVIDENCE_SCHEMA,
    )
    stat = package_path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
    package_fingerprint = metadata_fingerprint(
        uasset_size=stat.st_size,
        uasset_modified=modified,
    )
    return rows, fingerprint, revision, package_fingerprint


def _default_rows(revision: str) -> list[tuple[object, ...]]:
    def row(
        name: str,
        type_name: str,
        value: object,
        extra: object,
        confidence: str = "high",
    ) -> tuple[object, ...]:
        return (
            make_default_ref(ASSET_ID, revision, name),
            revision,
            name,
            type_name,
            json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            "json",
            None,
            confidence,
            "fixture",
            json.dumps(extra, ensure_ascii=False, separators=(",", ":")),
        )

    rows = [
        row("Enabled", "BoolProperty", True, {}),
        row("Disabled", "BoolProperty", False, {}),
        row("Count", "IntProperty", 7, {}),
        row("Rate", "FloatProperty", 1.5, {}),
        row("Label", "StrProperty", "hello", {}),
        row("Socket", "NameProperty", "GripPoint", {}),
        row("Caption", "TextProperty", "Ready", {}),
        row(
            "Target",
            "ObjectProperty",
            -1,
            {
                "package_index": -1,
                "object": "Target_C",
                "object_path": "/Game/Test/Target.Target_C",
            },
        ),
        row(
            "SoftTarget",
            "SoftObjectProperty",
            "/Game/Test/SoftTarget.SoftTarget_C",
            {
                "object": "/Game/Test/SoftTarget.SoftTarget_C",
                "object_path": "/Game/Test/SoftTarget.SoftTarget_C",
            },
        ),
        row(
            "EmptyItems",
            "ArrayProperty",
            [],
            {
                "array_parse": {
                    "parsed": True,
                    "count": 0,
                    "element_kind": "IntProperty",
                    "elements": [],
                }
            },
        ),
        row(
            "SmallItems",
            "ArrayProperty",
            [2, 1],
            {
                "array_parse": {
                    "parsed": True,
                    "count": 2,
                    "element_kind": "IntProperty",
                    "elements": [
                        {"index": 0, "value": 2},
                        {"index": 1, "value": 1},
                    ],
                }
            },
        ),
        row(
            "SmallStruct",
            "StructProperty",
            {"Z": 2, "A": 1},
            {
                "struct_parse": {
                    "parsed": True,
                    "properties": [
                        {"name": "Z", "type": "IntProperty", "value": 2},
                        {"name": "A", "type": "IntProperty", "value": 1},
                    ],
                }
            },
        ),
        row(
            "EmptyMap",
            "MapProperty",
            {},
            {
                "map_parse": {
                    "parsed": True,
                    "count": 0,
                    "key_kind": "NameProperty",
                    "value_kind": "IntProperty",
                    "entries": [],
                }
            },
        ),
        row(
            "SmallMap",
            "MapProperty",
            [
                {"key": "B", "value": 2},
                {"key": "A", "value": 1},
            ],
            {
                "map_parse": {
                    "parsed": True,
                    "count": 2,
                    "key_kind": "NameProperty",
                    "value_kind": "IntProperty",
                    "entries": [
                        {"index": 0, "key": "B", "value": 2},
                        {"index": 1, "key": "A", "value": 1},
                    ],
                }
            },
        ),
        row(
            "ObjectMap",
            "MapProperty",
            [{"key": "Target", "value": -1}],
            {
                "map_parse": {
                    "parsed": True,
                    "count": 1,
                    "key_kind": "NameProperty",
                    "value_kind": "ObjectProperty",
                    "entries": [
                        {
                            "index": 0,
                            "key": "Target",
                            "value": -1,
                            "value_metadata": {
                                "type": "ObjectProperty",
                                "package_index": -1,
                                "object_path": "/Game/Test/Target.Target_C",
                            },
                        }
                    ],
                }
            },
        ),
        row(
            "ScrambledObjectMap",
            "MapProperty",
            {"A": -1, "B": -2},
            {
                "map_parse": {
                    "parsed": True,
                    "count": 2,
                    "key_kind": "NameProperty",
                    "value_kind": "ObjectProperty",
                    "entries": [
                        {
                            "index": 0,
                            "key": "B",
                            "value": -2,
                            "value_metadata": {
                                "type": "ObjectProperty",
                                "package_index": -2,
                                "object_path": "/Game/Test/B.B_C",
                            },
                        },
                        {
                            "index": 1,
                            "key": "A",
                            "value": -1,
                            "value_metadata": {
                                "type": "ObjectProperty",
                                "package_index": -1,
                                "object_path": "/Game/Test/A.A_C",
                            },
                        },
                    ],
                }
            },
        ),
        row(
            "ObjectItems",
            "ArrayProperty",
            [-1],
            {
                "array_parse": {
                    "parsed": True,
                    "count": 1,
                    "element_kind": "ObjectProperty",
                    "elements": [
                        {
                            "index": 0,
                            "value": -1,
                            "object": "Target_C",
                            "object_path": "/Game/Test/Target.Target_C",
                        }
                    ],
                }
            },
        ),
        row(
            "LargeItems",
            "ArrayProperty",
            list(range(65)),
            {
                "array_parse": {
                    "parsed": True,
                    "count": 65,
                    "element_kind": "IntProperty",
                    "elements": [],
                }
            },
        ),
        row(
            "LargeMap",
            "MapProperty",
            [{"key": f"K{index:02d}", "value": index} for index in range(65)],
            {
                "map_parse": {
                    "parsed": True,
                    "count": 65,
                    "key_kind": "NameProperty",
                    "value_kind": "IntProperty",
                    "entries": [],
                }
            },
        ),
        row(
            "NestedPartial",
            "ArrayProperty",
            [{"Broken": {"parsed": False, "raw_size": 12}}],
            {
                "array_parse": {
                    "parsed": True,
                    "count": 1,
                    "element_kind": "StructProperty",
                    "elements": [
                        {
                            "index": 0,
                            "properties": [
                                {
                                    "name": "Broken",
                                    "type": "StructProperty",
                                    "value": {
                                        "parsed": False,
                                        "raw_size": 12,
                                    },
                                    "struct_parse": {
                                        "parsed": False,
                                        "raw_size": 12,
                                    },
                                }
                            ],
                        }
                    ],
                }
            },
        ),
        row(
            "LeakedPath",
            "StrProperty",
            r"C:\Users\secret\Desktop\value.txt",
            {},
        ),
        row("RootPath", "StrProperty", "/root/secret/value.txt", {}),
        row("EtcPath", "StrProperty", "/etc/passwd", {}),
        row("EmbeddedPath", "StrProperty", "prefix=/usr/local/bin", {}),
        row("NotANumber", "FloatProperty", float("nan"), {}),
        row("InfiniteNumber", "FloatProperty", float("inf"), {}),
        row("HugeInteger", "IntProperty", 2**63, {}),
        row(
            "PartialMap",
            "MapProperty",
            {"parsed": False, "raw_size": 24},
            {},
        ),
        row(
            "UnresolvedObject",
            "ObjectProperty",
            1,
            {
                "package_index": 1,
                "object": "Component0",
                "object_path": "Component0",
            },
        ),
        row(
            "ExplicitObjectGap",
            "ObjectProperty",
            None,
            {
                "package_index": -1769473,
                "object": "",
                "value_status": "NOT_RECOVERED",
                "error": "PackageIndex is outside package maps",
            },
        ),
        row(
            "ParserErrorText",
            "StrProperty",
            "plausible but untrusted",
            {"error": "Invalid FString length."},
        ),
        row(
            "ExplicitlyUnusableText",
            "StrProperty",
            "plausible but untrusted",
            {"value_usable": False},
        ),
        row(
            "UnportableIndexes",
            "ArrayProperty",
            [-1],
            {
                "array_parse": {
                    "parsed": True,
                    "count": 1,
                    "element_kind": "FPackageIndex",
                    "elements": [{"index": 0, "value": -1}],
                }
            },
        ),
        row(
            "MismatchedArrayCount",
            "ArrayProperty",
            [1],
            {
                "array_parse": {
                    "parsed": True,
                    "count": 9,
                    "element_kind": "IntProperty",
                    "elements": [],
                }
            },
        ),
        row("SrvPath", "StrProperty", "/srv/private/value.txt", {}),
        row("RunPath", "StrProperty", "/run/private/value.txt", {}),
        row("DataPath", "StrProperty", "/data/private/value.txt", {}),
        row(
            "CustomRootPath",
            "StrProperty",
            "/custom/private/value.txt",
            {},
        ),
        row(
            "WorkspacePath",
            "StrProperty",
            "/workspace/private/value.txt",
            {},
        ),
        row(
            "TraversingObject",
            "ObjectProperty",
            -1,
            {
                "package_index": -1,
                "object_path": "/Game/../srv/secret",
            },
        ),
    ]
    compressed = zlib.compress(b'"compressed"')
    rows.append(
        (
            make_default_ref(ASSET_ID, revision, "CompressedLabel"),
            revision,
            "CompressedLabel",
            "StrProperty",
            "null",
            "zlib-json-utf8",
            compressed,
            "high",
            "fixture",
            "{}",
        )
    )
    return rows


def _write_capture(
    capture_root: Path,
    *,
    legacy_marker: bool = False,
    source_mode: str = "direct",
) -> tuple[str, Path, str]:
    asset_root = capture_root / "BP_Test"
    evidence_root = asset_root / "evidence"
    evidence_root.mkdir(parents=True)
    package_path = asset_root / "BP_Test.uasset"
    parser_version = LEGACY_PARSER if source_mode == "legacy" else DIRECT_PARSER
    source_rows, fingerprint, revision, package_fingerprint = _source_identity(
        package_path,
        parser_version=parser_version,
        source_mode=source_mode,
    )
    if legacy_marker:
        (asset_root / "graphs_from_uasset_manifest.json").write_text(
            '{"files":[]}',
            encoding="utf-8",
        )
    database_path = evidence_root / "evidence.sqlite"
    connection = sqlite3.connect(database_path)
    ensure_evidence_schema(connection)
    connection.execute(
        "INSERT INTO asset_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            revision,
            ASSET_ID,
            "BP_Test",
            OBJECT_PATH,
            fingerprint,
            parser_version,
            EVIDENCE_SCHEMA,
            "2026-07-27T00:00:00+00:00",
            str(package_path),
        ),
    )
    connection.executemany(
        "INSERT INTO source_manifest VALUES (?, ?, ?, ?, ?)",
        [(revision, *source) for source in source_rows],
    )
    connection.executemany(
        "INSERT INTO class_defaults VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        _default_rows(revision),
    )
    connection.commit()
    connection.close()
    (evidence_root / "manifest.json").write_text(
        json.dumps(
            {
                "asset_id": ASSET_ID,
                "asset_name": "BP_Test",
                "object_path": OBJECT_PATH,
                "revision_id": revision,
                "source_fingerprint": fingerprint,
                "parser_version": parser_version,
                "schema": EVIDENCE_SCHEMA,
                "database": "evidence.sqlite",
                "agent_index": "../output/agent_index.md",
                "counts": {
                    "graphs": 0,
                    "nodes": 0,
                    "pins": 0,
                    "edges": 0,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    output_root = asset_root / "output"
    output_root.mkdir()
    (output_root / "agent_index.md").write_text(
        f"# BP_Test\n\nRevision: `{revision}`\n",
        encoding="utf-8",
    )
    return revision, database_path, package_fingerprint


def _insert_default_rows(
    database_path: Path,
    revision: str,
    rows: list[tuple[str, str, object, object]],
) -> None:
    connection = sqlite3.connect(database_path)
    connection.executemany(
        """
        INSERT INTO class_defaults VALUES (
            ?, ?, ?, ?, ?, 'json', NULL, 'high', 'fixture', ?
        )
        """,
        [
            (
                make_default_ref(ASSET_ID, revision, name),
                revision,
                name,
                type_name,
                json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                json.dumps(
                    extra,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            for name, type_name, value, extra in rows
        ],
    )
    connection.commit()
    connection.close()


def _discovery(
    revision: str,
    *,
    package_path: Path,
    freshness: str = "FRESH",
    package_fingerprint: str = "package-sha",
) -> sqlite3.Connection:
    stat = package_path.stat()
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE assets(
            object_path TEXT PRIMARY KEY,
            asset_name TEXT NOT NULL,
            capture_exists INTEGER NOT NULL,
            evidence_revision TEXT NOT NULL,
            evidence_freshness TEXT NOT NULL,
            source_fingerprint TEXT NOT NULL,
            file_size_total INTEGER NOT NULL,
            source_modified TEXT NOT NULL,
            has_uasset INTEGER NOT NULL,
            has_uexp INTEGER NOT NULL,
            has_ubulk INTEGER NOT NULL
        );
        CREATE TABLE default_property_surface(
            surface_id TEXT PRIMARY KEY,
            asset_object_path TEXT NOT NULL,
            property_name TEXT NOT NULL,
            property_type TEXT NOT NULL,
            has_value INTEGER NOT NULL,
            value_status TEXT NOT NULL,
            value_fingerprint TEXT NOT NULL,
            source_evidence_id TEXT NOT NULL,
            confidence TEXT NOT NULL
        );
        """
    )
    connection.execute(
        """
        INSERT INTO assets VALUES (
            ?, ?, 1, ?, ?, ?, ?, ?, 1, 0, 0
        )
        """,
        (
            OBJECT_PATH,
            "BP_Test",
            revision,
            freshness,
            package_fingerprint,
            stat.st_size,
            datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO default_property_surface VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)",
        (
            "surface-count",
            OBJECT_PATH,
            "Count",
            "IntProperty",
            "CONFIRMED_FINGERPRINT_ONLY",
            "fallback-fingerprint",
            f"bp://{ASSET_ID}@{revision}/default/Count",
            "HIGH",
        ),
    )
    return connection


def _capture_source_revision(
    capture_root: Path,
    revision: str,
    *,
    fingerprint: str | None = None,
) -> SourceRevision:
    state = resolve_asset_evidence_state(capture_root / "BP_Test")
    source_uri = "capture://BP_Test"
    return SourceRevision(
        source_id=source_id("BLUEPRINT_EVIDENCE", source_uri),
        source_kind="BLUEPRINT_EVIDENCE",
        source_uri=source_uri,
        fingerprint=fingerprint or live_capture_evidence_fingerprint(state),
        size_bytes=state.database_bytes,
        entity_uri=OBJECT_PATH,
        revision_label=revision,
    )


def _core() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(FULL_CORE_SCHEMA_SQL)
    connection.execute(
        """
        INSERT INTO source_revisions VALUES (
            1, 'discovery', 'discovery://fixture', 'fixture-sha',
            'fixture', 'v1', '2026-07-27T00:00:00+00:00', 'FRESH'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO entities(
            entity_id, canonical_uri, entity_kind, status, confidence
        ) VALUES (1, ?, 'BLUEPRINT_ASSET', 'CONFIRMED', 'HIGH')
        """,
        (OBJECT_PATH,),
    )
    return connection


class BlueprintIngestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ontology = load_ontology(PROJECT_ROOT / "ontology")

    def test_ingests_typed_values_with_revision_evidence_and_safe_fallbacks(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            revision, _database_path, package_fingerprint = _write_capture(
                root / "captures"
            )
            discovery = _discovery(
                revision,
                package_path=_database_path.parents[1] / "BP_Test.uasset",
                package_fingerprint=package_fingerprint,
            )
            core = _core()

            result = materialize_blueprint_defaults(
                discovery,
                core,
                capture_root=root / "captures",
                ontology=self.ontology,
            )

            rows = {
                row[0]: row[1:]
                for row in core.execute(
                    """
                    SELECT fact_name, value_kind, value_text, value_number,
                           value_integer, value_json, status
                    FROM facts
                    ORDER BY fact_name
                    """
                )
            }
            self.assertEqual(
                rows["Enabled"],
                ("BOOLEAN", None, None, 1, None, "CONFIRMED"),
            )
            self.assertEqual(
                rows["Disabled"],
                ("BOOLEAN", None, None, 0, None, "CONFIRMED"),
            )
            self.assertEqual(
                rows["Count"],
                ("INTEGER", None, None, 7, None, "CONFIRMED"),
            )
            self.assertEqual(
                rows["Rate"],
                ("NUMBER", None, 1.5, None, None, "CONFIRMED"),
            )
            self.assertEqual(
                rows["Label"],
                ("TEXT", "hello", None, None, None, "CONFIRMED"),
            )
            self.assertEqual(
                rows["CompressedLabel"],
                (
                    "TEXT",
                    "compressed",
                    None,
                    None,
                    None,
                    "CONFIRMED",
                ),
            )
            self.assertEqual(rows["Socket"][0:2], ("TEXT", "GripPoint"))
            self.assertEqual(rows["Caption"][0:2], ("TEXT", "Ready"))
            self.assertEqual(
                rows["Target"][0:2],
                ("ENTITY_REF", "/Game/Test/Target.Target_C"),
            )
            self.assertEqual(
                rows["SoftTarget"][0:2],
                ("ENTITY_REF", "/Game/Test/SoftTarget.SoftTarget_C"),
            )
            self.assertEqual(
                rows["EmptyItems"],
                (
                    "CONFIRMED_EMPTY",
                    None,
                    None,
                    None,
                    None,
                    "CONFIRMED_EMPTY",
                ),
            )
            self.assertEqual(rows["SmallItems"][4], "[2,1]")
            self.assertEqual(rows["SmallStruct"][4], '{"A":1,"Z":2}')
            self.assertEqual(
                rows["EmptyMap"],
                (
                    "CONFIRMED_EMPTY",
                    None,
                    None,
                    None,
                    None,
                    "CONFIRMED_EMPTY",
                ),
            )
            self.assertEqual(
                rows["SmallMap"][4],
                '[{"key":"A","value":1},{"key":"B","value":2}]',
            )
            self.assertEqual(
                rows["ObjectMap"][4],
                ('[{"key":"Target","value":"/Game/Test/Target.Target_C"}]'),
            )
            self.assertEqual(
                rows["ScrambledObjectMap"][4],
                (
                    '[{"key":"A","value":"/Game/Test/A.A_C"},'
                    '{"key":"B","value":"/Game/Test/B.B_C"}]'
                ),
            )
            large_map_summary = json.loads(str(rows["LargeMap"][4]))
            self.assertEqual(rows["LargeMap"][0], "FINGERPRINT")
            self.assertEqual(large_map_summary["count"], 65)
            self.assertEqual(
                rows["ObjectItems"][4],
                '["/Game/Test/Target.Target_C"]',
            )
            large_summary = json.loads(str(rows["LargeItems"][4]))
            self.assertEqual(rows["LargeItems"][0], "FINGERPRINT")
            self.assertEqual(
                rows["LargeItems"][-1],
                "CONFIRMED_FINGERPRINT_ONLY",
            )
            self.assertEqual(large_summary["count"], 65)
            self.assertTrue(str(large_summary["detail_uri"]).startswith("bp://"))
            self.assertEqual(
                rows["NestedPartial"],
                ("UNKNOWN", None, None, None, None, "NOT_RECOVERED"),
            )
            self.assertEqual(
                rows["LeakedPath"],
                ("UNKNOWN", None, None, None, None, "NOT_RECOVERED"),
            )
            for name in (
                "RootPath",
                "EtcPath",
                "EmbeddedPath",
                "NotANumber",
                "InfiniteNumber",
                "HugeInteger",
                "ExplicitObjectGap",
                "ParserErrorText",
                "ExplicitlyUnusableText",
                "UnportableIndexes",
                "MismatchedArrayCount",
                "SrvPath",
                "RunPath",
                "DataPath",
                "CustomRootPath",
                "WorkspacePath",
                "TraversingObject",
            ):
                self.assertEqual(
                    rows[name],
                    (
                        "UNKNOWN",
                        None,
                        None,
                        None,
                        None,
                        "NOT_RECOVERED",
                    ),
                )
            self.assertEqual(
                rows["UnresolvedObject"],
                ("UNKNOWN", None, None, None, None, "NOT_RECOVERED"),
            )
            self.assertEqual(
                rows["PartialMap"],
                ("UNKNOWN", None, None, None, None, "NOT_RECOVERED"),
            )
            self.assertEqual(result.counts["partialFacts"], 2)
            self.assertEqual(result.counts["summaryFacts"], 2)
            self.assertEqual(result.counts["freshnessGapAssets"], 0)
            self.assertEqual(result.counts["packageVerifiedAssets"], 1)
            self.assertEqual(
                core.execute(
                    """
                    SELECT evidence_role
                    FROM fact_evidence AS evidence
                    JOIN facts AS fact ON fact.fact_id=evidence.fact_id
                    WHERE fact.fact_name='NestedPartial'
                    """
                ).fetchone()[0],
                "DEFAULT_VALUE_PARTIAL",
            )

            revision_row = core.execute(
                """
                SELECT source_kind, source_uri, source_fingerprint,
                       producer_version, schema_version, freshness_status
                FROM source_revisions
                WHERE source_kind='blueprint_evidence'
                """
            ).fetchone()
            self.assertEqual(revision_row[0], "blueprint_evidence")
            self.assertEqual(
                revision_row[1],
                f"bp://{ASSET_ID}@{revision}",
            )
            self.assertEqual(revision_row[-1], "FRESH")
            evidence_uris = [
                str(row[0])
                for row in core.execute("SELECT evidence_uri FROM fact_evidence")
            ]
            self.assertTrue(evidence_uris)
            self.assertTrue(all(uri.startswith("bp://") for uri in evidence_uris))
            persisted = "\n".join(
                str(value or "")
                for row in core.execute(
                    """
                    SELECT source_uri, source_fingerprint, producer_version,
                           schema_version, generated_at
                    FROM source_revisions
                    UNION ALL
                    SELECT value_text, value_json, fact_name, status, confidence
                    FROM facts
                    """
                )
                for value in row
            )
            self.assertNotIn(r"C:\Users", persisted)
            core.close()
            discovery.close()

    def test_ingests_pruned_v3_current_without_v2_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            revision, database_path, package_fingerprint = _write_capture(
                root / "captures"
            )
            asset_dir = database_path.parents[1]
            migrate_v2_evidence_to_v3(asset_dir, prune_v2=True)
            discovery = _discovery(
                revision,
                package_path=asset_dir / "BP_Test.uasset",
                package_fingerprint=package_fingerprint,
            )
            core = _core()

            result = materialize_blueprint_defaults(
                discovery,
                core,
                capture_root=root / "captures",
                ontology=self.ontology,
            )

            self.assertEqual(result.counts["freshAssets"], 1)
            self.assertGreater(result.counts["declaredFacts"], 0)
            self.assertFalse(
                (asset_dir / "evidence" / "evidence.sqlite").exists()
            )
            core.close()
            discovery.close()

    def test_explicit_manifest_subset_ingests_new_capture_not_yet_marked_in_discovery(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            capture_root = root / "captures"
            revision, database_path, package_fingerprint = _write_capture(
                capture_root
            )
            discovery = _discovery(
                revision,
                package_path=database_path.parents[1] / "BP_Test.uasset",
                package_fingerprint=package_fingerprint,
            )
            discovery.execute(
                """
                UPDATE assets
                SET capture_exists=0,
                    evidence_revision='',
                    evidence_freshness='NOT_AVAILABLE'
                """
            )
            core = _core()
            source_revision = _capture_source_revision(
                capture_root,
                revision,
            )

            result = materialize_blueprint_defaults(
                discovery,
                core,
                capture_root=capture_root,
                ontology=self.ontology,
                source_revisions=(source_revision,),
            )

            self.assertEqual(result.counts["freshAssets"], 1)
            self.assertEqual(
                core.execute(
                    """
                    SELECT value_integer, status
                    FROM facts
                    WHERE fact_name='Count'
                    """
                ).fetchall(),
                [(7, "CONFIRMED")],
            )
            core.close()
            discovery.close()

    def test_explicit_empty_subset_never_falls_back_to_full_capture_scan(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            capture_root = root / "captures"
            revision, database_path, package_fingerprint = _write_capture(
                capture_root
            )
            discovery = _discovery(
                revision,
                package_path=database_path.parents[1] / "BP_Test.uasset",
                package_fingerprint=package_fingerprint,
            )
            core = _core()

            result = materialize_blueprint_defaults(
                discovery,
                core,
                capture_root=capture_root,
                ontology=self.ontology,
                source_revisions=(),
            )

            self.assertEqual(result.counts["freshAssets"], 0)
            self.assertEqual(
                core.execute("SELECT COUNT(*) FROM facts").fetchone()[0],
                0,
            )
            core.close()
            discovery.close()

    def test_explicit_subset_rejects_changed_evidence_aggregate_before_commit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            capture_root = root / "captures"
            revision, database_path, package_fingerprint = _write_capture(
                capture_root
            )
            discovery = _discovery(
                revision,
                package_path=database_path.parents[1] / "BP_Test.uasset",
                package_fingerprint=package_fingerprint,
            )
            core = _core()
            source_revision = _capture_source_revision(
                capture_root,
                revision,
                fingerprint="f" * 64,
            )

            with self.assertRaisesRegex(
                ValueError,
                "explicit Blueprint subset",
            ):
                materialize_blueprint_defaults(
                    discovery,
                    core,
                    capture_root=capture_root,
                    ontology=self.ontology,
                    source_revisions=(source_revision,),
                )

            core.rollback()
            self.assertEqual(
                core.execute("SELECT COUNT(*) FROM facts").fetchone()[0],
                0,
            )
            core.close()
            discovery.close()

    def test_capture_mode_uses_revision_bound_manifest_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            revision, _database_path, package_fingerprint = _write_capture(
                root / "captures",
                legacy_marker=True,
            )
            discovery = _discovery(
                revision,
                package_path=_database_path.parents[1] / "BP_Test.uasset",
                package_fingerprint=package_fingerprint,
            )
            core = _core()
            result = materialize_blueprint_defaults(
                discovery,
                core,
                capture_root=root / "captures",
                ontology=self.ontology,
            )
            self.assertEqual(result.counts["freshAssets"], 1)
            self.assertEqual(result.counts["rejectedAssets"], 0)
            self.assertGreater(
                core.execute("SELECT COUNT(*) FROM facts").fetchone()[0],
                0,
            )
            core.close()
            discovery.close()

    def test_requires_exactly_one_revision_bound_capture_mode(self) -> None:
        for source_mode, expected_fresh, expected_rejected in (
            ("legacy", 1, 0),
            ("both", 0, 1),
            ("none", 0, 1),
        ):
            with self.subTest(source_mode=source_mode):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    (
                        revision,
                        database_path,
                        package_fingerprint,
                    ) = _write_capture(
                        root / "captures",
                        source_mode=source_mode,
                    )
                    discovery = _discovery(
                        revision,
                        package_path=(database_path.parents[1] / "BP_Test.uasset"),
                        package_fingerprint=package_fingerprint,
                    )
                    core = _core()
                    result = materialize_blueprint_defaults(
                        discovery,
                        core,
                        capture_root=root / "captures",
                        ontology=self.ontology,
                    )
                    self.assertEqual(
                        result.counts["freshAssets"],
                        expected_fresh,
                    )
                    self.assertEqual(
                        result.counts["rejectedAssets"],
                        expected_rejected,
                    )
                    core.close()
                    discovery.close()

    def test_rejects_revision_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _revision, database_path, package_fingerprint = _write_capture(
                root / "captures"
            )
            discovery = _discovery(
                "wrong-revision",
                package_path=database_path.parents[1] / "BP_Test.uasset",
                package_fingerprint=package_fingerprint,
            )
            core = _core()
            result = materialize_blueprint_defaults(
                discovery,
                core,
                capture_root=root / "captures",
                ontology=self.ontology,
            )
            self.assertEqual(result.counts["rejectedAssets"], 1)
            self.assertEqual(
                core.execute("SELECT COUNT(*) FROM facts").fetchone()[0], 0
            )
            core.close()
            discovery.close()

    def test_rejects_whitespace_only_default_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            revision, database_path, package_fingerprint = _write_capture(
                root / "captures"
            )
            evidence = sqlite3.connect(database_path)
            evidence.execute(
                """
                UPDATE class_defaults
                SET name=?, default_ref=?
                WHERE name='Count'
                """,
                (
                    " ",
                    make_default_ref(ASSET_ID, revision, " "),
                ),
            )
            evidence.commit()
            evidence.close()
            discovery = _discovery(
                revision,
                package_path=database_path.parents[1] / "BP_Test.uasset",
                package_fingerprint=package_fingerprint,
            )
            core = _core()

            result = materialize_blueprint_defaults(
                discovery,
                core,
                capture_root=root / "captures",
                ontology=self.ontology,
            )

            self.assertEqual(result.counts["rejectedAssets"], 1)
            self.assertEqual(
                core.execute("SELECT COUNT(*) FROM facts").fetchone()[0],
                0,
            )
            core.close()
            discovery.close()

    def test_rejects_asset_id_mismatch_and_malformed_manifest_size(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            revision, database_path, package_fingerprint = _write_capture(
                root / "captures"
            )
            wrong_asset_id = "0" * 24
            evidence = sqlite3.connect(database_path)
            evidence.execute(
                "UPDATE asset_revisions SET asset_id=?",
                (wrong_asset_id,),
            )
            evidence.execute(
                """
                UPDATE class_defaults
                SET default_ref=REPLACE(default_ref, ?, ?)
                """,
                (ASSET_ID, wrong_asset_id),
            )
            evidence.commit()
            evidence.close()
            manifest_path = database_path.with_name("manifest.json")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["asset_id"] = wrong_asset_id
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True),
                encoding="utf-8",
            )
            discovery = _discovery(
                revision,
                package_path=database_path.parents[1] / "BP_Test.uasset",
                package_fingerprint=package_fingerprint,
            )
            core = _core()
            result = materialize_blueprint_defaults(
                discovery,
                core,
                capture_root=root / "captures",
                ontology=self.ontology,
            )
            self.assertEqual(result.counts["rejectedAssets"], 1)
            core.close()
            discovery.close()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            revision, database_path, package_fingerprint = _write_capture(
                root / "captures"
            )
            evidence = sqlite3.connect(database_path)
            evidence.execute(
                """
                UPDATE source_manifest
                SET size_bytes='not-an-integer'
                WHERE source_kind='in_memory_capture'
                """
            )
            evidence.commit()
            evidence.close()
            discovery = _discovery(
                revision,
                package_path=database_path.parents[1] / "BP_Test.uasset",
                package_fingerprint=package_fingerprint,
            )
            core = _core()
            result = materialize_blueprint_defaults(
                discovery,
                core,
                capture_root=root / "captures",
                ontology=self.ontology,
            )
            self.assertEqual(result.counts["rejectedAssets"], 1)
            core.close()
            discovery.close()

    def test_fresh_rejected_asset_is_untrusted_and_cannot_keep_fallback(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            revision, database_path, package_fingerprint = _write_capture(
                root / "captures"
            )
            evidence = sqlite3.connect(database_path)
            evidence.execute(
                """
                UPDATE class_defaults
                SET default_ref=?
                WHERE name='Count'
                """,
                (f"bp://{ASSET_ID}@{revision}/default/DifferentProperty",),
            )
            evidence.commit()
            evidence.close()
            discovery = _discovery(
                revision,
                package_path=database_path.parents[1] / "BP_Test.uasset",
                package_fingerprint=package_fingerprint,
            )
            core = _core()

            result = materialize_blueprint_defaults(
                discovery,
                core,
                capture_root=root / "captures",
                ontology=self.ontology,
            )
            with self.subTest("tracks rejected FRESH identity"):
                self.assertEqual(
                    result.untrusted_assets,
                    frozenset({OBJECT_PATH}),
                )

            materialize_declared_defaults(
                discovery,
                core,
                ontology=self.ontology,
                source_revision_id=1,
                covered_properties=result.covered_properties,
                freshness_gap_assets=result.freshness_gap_assets,
                untrusted_assets=result.untrusted_assets,
            )

            with self.subTest("downgrades rejected FRESH fallback"):
                self.assertEqual(
                    core.execute(
                        """
                        SELECT value_kind, value_text, status
                        FROM facts
                        WHERE fact_name='Count'
                        """
                    ).fetchall(),
                    [("UNKNOWN", None, "NOT_RECOVERED")],
                )
            core.close()
            discovery.close()

    def test_struct_metadata_fails_closed_without_breaking_builtins(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            revision, database_path, package_fingerprint = _write_capture(
                root / "captures"
            )
            _insert_default_rows(
                database_path,
                revision,
                [
                    (
                        "StructMissingField",
                        "StructProperty",
                        {"A": 1, "B": 2},
                        {
                            "struct_parse": {
                                "parsed": True,
                                "struct_name": "CustomStruct",
                                "properties": [
                                    {
                                        "name": "A",
                                        "type": "IntProperty",
                                        "value": 1,
                                    }
                                ],
                            }
                        },
                    ),
                    (
                        "StructDuplicateField",
                        "StructProperty",
                        {"A": 1},
                        {
                            "struct_parse": {
                                "parsed": True,
                                "struct_name": "CustomStruct",
                                "properties": [
                                    {
                                        "name": "A",
                                        "type": "IntProperty",
                                        "value": 1,
                                    },
                                    {
                                        "name": "A",
                                        "type": "IntProperty",
                                        "value": 1,
                                    },
                                ],
                            }
                        },
                    ),
                    (
                        "StructPackageIndex",
                        "StructProperty",
                        {"Ref": -1},
                        {
                            "struct_parse": {
                                "parsed": True,
                                "struct_name": "CustomStruct",
                                "properties": [
                                    {
                                        "name": "Ref",
                                        "type": "FPackageIndex",
                                        "value": -1,
                                    }
                                ],
                            }
                        },
                    ),
                    (
                        "StructParserError",
                        "StructProperty",
                        {"A": 1},
                        {
                            "struct_parse": {
                                "parsed": True,
                                "struct_name": "CustomStruct",
                                "properties": [
                                    {
                                        "name": "A",
                                        "type": "IntProperty",
                                        "value": 1,
                                        "error": "field parse failed",
                                    }
                                ],
                            }
                        },
                    ),
                    (
                        "StructMetadataMismatch",
                        "StructProperty",
                        {"A": 1},
                        {
                            "struct_parse": {
                                "parsed": True,
                                "struct_name": "CustomStruct",
                                "properties": [
                                    {
                                        "name": "A",
                                        "type": "IntProperty",
                                        "value": 2,
                                    }
                                ],
                            }
                        },
                    ),
                    (
                        "StructPortableError",
                        "StructProperty",
                        {"x": 1.25, "y": 2.5},
                        {
                            "struct_parse": {
                                "parsed": True,
                                "struct_name": "Vector2D",
                                "raw_size": 16,
                                "fields": ["x", "y"],
                                "error": "portable parse failed",
                            }
                        },
                    ),
                    (
                        "StructPortableBadSize",
                        "StructProperty",
                        {"x": 1.25, "y": 2.5},
                        {
                            "struct_parse": {
                                "parsed": True,
                                "struct_name": "Vector2D",
                                "raw_size": 999,
                                "fields": ["x", "y"],
                            }
                        },
                    ),
                    (
                        "TrustedVector2D",
                        "StructProperty",
                        {"x": 1.25, "y": 2.5},
                        {
                            "struct_parse": {
                                "parsed": True,
                                "struct_name": "Vector2D",
                                "raw_size": 16,
                                "fields": ["x", "y"],
                            }
                        },
                    ),
                ],
            )
            discovery = _discovery(
                revision,
                package_path=database_path.parents[1] / "BP_Test.uasset",
                package_fingerprint=package_fingerprint,
            )
            core = _core()

            materialize_blueprint_defaults(
                discovery,
                core,
                capture_root=root / "captures",
                ontology=self.ontology,
            )
            rows = {
                str(row[0]): (str(row[1]), row[2], str(row[3]))
                for row in core.execute(
                    """
                    SELECT fact_name, value_kind, value_json, status
                    FROM facts
                    WHERE fact_name LIKE 'Struct%'
                       OR fact_name='TrustedVector2D'
                    """
                )
            }

            for name in (
                "StructMissingField",
                "StructDuplicateField",
                "StructPackageIndex",
                "StructParserError",
                "StructMetadataMismatch",
                "StructPortableError",
                "StructPortableBadSize",
            ):
                with self.subTest(name=name):
                    self.assertEqual(
                        rows[name],
                        ("UNKNOWN", None, "NOT_RECOVERED"),
                    )
            self.assertEqual(
                rows["TrustedVector2D"],
                (
                    "JSON",
                    '{"x":1.25,"y":2.5}',
                    "CONFIRMED",
                ),
            )
            core.close()
            discovery.close()

    def test_manifest_and_container_integer_fields_are_strict(
        self,
    ) -> None:
        manifest_mutations = (
            (
                "package source kind mismatch",
                """
                UPDATE source_manifest
                SET source_kind='capture_sidecar'
                WHERE path LIKE 'binary/%'
                """,
                (),
            ),
            (
                "negative manifest size",
                """
                UPDATE source_manifest
                SET size_bytes=?
                WHERE source_kind='in_memory_capture'
                """,
                (-1,),
            ),
            (
                "floating manifest size",
                """
                UPDATE source_manifest
                SET size_bytes=?
                WHERE source_kind='in_memory_capture'
                """,
                (1.5,),
            ),
        )
        for label, statement, parameters in manifest_mutations:
            with self.subTest(manifest=label):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    (
                        revision,
                        database_path,
                        package_fingerprint,
                    ) = _write_capture(root / "captures")
                    evidence = sqlite3.connect(database_path)
                    evidence.execute(statement, parameters)
                    evidence.commit()
                    evidence.close()
                    discovery = _discovery(
                        revision,
                        package_path=(database_path.parents[1] / "BP_Test.uasset"),
                        package_fingerprint=package_fingerprint,
                    )
                    core = _core()

                    result = materialize_blueprint_defaults(
                        discovery,
                        core,
                        capture_root=root / "captures",
                        ontology=self.ontology,
                    )

                    self.assertEqual(result.counts["rejectedAssets"], 1)
                    self.assertEqual(
                        core.execute("SELECT COUNT(*) FROM facts").fetchone()[0],
                        0,
                    )
                    core.close()
                    discovery.close()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            revision, database_path, package_fingerprint = _write_capture(
                root / "captures"
            )
            invalid_rows: list[tuple[str, str, object, object]] = []
            for suffix, raw_integer in (
                ("Bool", True),
                ("Float", 1.0),
                ("String", "1"),
            ):
                invalid_rows.extend(
                    [
                        (
                            f"StrictArray{suffix}Count",
                            "ArrayProperty",
                            [1],
                            {
                                "array_parse": {
                                    "parsed": True,
                                    "count": raw_integer,
                                    "element_kind": "IntProperty",
                                    "elements": [
                                        {
                                            "index": 0,
                                            "value": 1,
                                        }
                                    ],
                                }
                            },
                        ),
                        (
                            f"StrictMap{suffix}Count",
                            "MapProperty",
                            [{"key": "A", "value": 1}],
                            {
                                "map_parse": {
                                    "parsed": True,
                                    "count": raw_integer,
                                    "key_kind": "NameProperty",
                                    "value_kind": "IntProperty",
                                    "entries": [
                                        {
                                            "index": 0,
                                            "key": "A",
                                            "value": 1,
                                        }
                                    ],
                                }
                            },
                        ),
                    ]
                )
            for suffix, raw_integer in (
                ("Bool", False),
                ("Float", 0.0),
                ("String", "0"),
            ):
                invalid_rows.extend(
                    [
                        (
                            f"StrictArray{suffix}Index",
                            "ArrayProperty",
                            [1],
                            {
                                "array_parse": {
                                    "parsed": True,
                                    "count": 1,
                                    "element_kind": "IntProperty",
                                    "elements": [
                                        {
                                            "index": raw_integer,
                                            "value": 1,
                                        }
                                    ],
                                }
                            },
                        ),
                        (
                            f"StrictMap{suffix}Index",
                            "MapProperty",
                            [{"key": "A", "value": 1}],
                            {
                                "map_parse": {
                                    "parsed": True,
                                    "count": 1,
                                    "key_kind": "NameProperty",
                                    "value_kind": "IntProperty",
                                    "entries": [
                                        {
                                            "index": raw_integer,
                                            "key": "A",
                                            "value": 1,
                                        }
                                    ],
                                }
                            },
                        ),
                    ]
                )
            _insert_default_rows(
                database_path,
                revision,
                invalid_rows,
            )
            discovery = _discovery(
                revision,
                package_path=database_path.parents[1] / "BP_Test.uasset",
                package_fingerprint=package_fingerprint,
            )
            core = _core()

            materialize_blueprint_defaults(
                discovery,
                core,
                capture_root=root / "captures",
                ontology=self.ontology,
            )
            rows = {
                str(row[0]): (str(row[1]), str(row[2]))
                for row in core.execute(
                    """
                    SELECT fact_name, value_kind, status
                    FROM facts
                    WHERE fact_name LIKE 'StrictArray%'
                       OR fact_name LIKE 'StrictMap%'
                    """
                )
            }

            self.assertEqual(set(rows), {row[0] for row in invalid_rows})
            for name in sorted(rows):
                with self.subTest(container=name):
                    self.assertEqual(
                        rows[name],
                        ("UNKNOWN", "NOT_RECOVERED"),
                    )
            core.close()
            discovery.close()

    def test_normalizes_only_the_exact_legacy_package_name_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            revision, database_path, package_fingerprint = _write_capture(
                root / "captures"
            )
            legacy_object_path = "/Game/Test/BP_Test"
            legacy_asset_id = make_asset_id(legacy_object_path)
            evidence = sqlite3.connect(database_path)
            evidence.execute(
                """
                UPDATE asset_revisions
                SET object_path=?, asset_name='BP_Test', asset_id=?
                """,
                (legacy_object_path, legacy_asset_id),
            )
            evidence.execute(
                """
                UPDATE class_defaults
                SET default_ref=REPLACE(default_ref, ?, ?)
                """,
                (ASSET_ID, legacy_asset_id),
            )
            evidence.commit()
            evidence.close()
            manifest_path = database_path.with_name("manifest.json")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["object_path"] = legacy_object_path
            manifest["asset_id"] = legacy_asset_id
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True),
                encoding="utf-8",
            )
            discovery = _discovery(
                revision,
                package_path=database_path.parents[1] / "BP_Test.uasset",
                package_fingerprint=package_fingerprint,
            )
            discovery.execute(
                """
                UPDATE assets
                SET object_path=?, asset_name=?
                """,
                (legacy_object_path, legacy_object_path),
            )
            core = _core()
            core.execute(
                "UPDATE entities SET canonical_uri=?",
                (legacy_object_path,),
            )

            result = materialize_blueprint_defaults(
                discovery,
                core,
                capture_root=root / "captures",
                ontology=self.ontology,
            )

            self.assertEqual(result.counts["freshAssets"], 1)
            self.assertEqual(result.counts["rejectedAssets"], 0)
            core.close()
            discovery.close()

    def test_actual_value_suppresses_discovery_fingerprint_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            revision, _database_path, package_fingerprint = _write_capture(
                root / "captures"
            )
            discovery = _discovery(
                revision,
                package_path=_database_path.parents[1] / "BP_Test.uasset",
                package_fingerprint=package_fingerprint,
            )
            core = _core()
            result = materialize_blueprint_defaults(
                discovery,
                core,
                capture_root=root / "captures",
                ontology=self.ontology,
            )
            materialize_declared_defaults(
                discovery,
                core,
                ontology=self.ontology,
                source_revision_id=1,
                covered_properties=result.covered_properties,
                freshness_gap_assets=result.freshness_gap_assets,
            )
            rows = list(
                core.execute(
                    """
                    SELECT value_kind, value_integer, status
                    FROM facts
                    WHERE fact_name='Count'
                    """
                )
            )
            self.assertEqual(rows, [("INTEGER", 7, "CONFIRMED")])
            core.close()
            discovery.close()

    def test_stale_evidence_fallback_cannot_remain_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            revision, database_path, package_fingerprint = _write_capture(
                root / "captures"
            )
            discovery = _discovery(
                revision,
                package_path=database_path.parents[1] / "BP_Test.uasset",
                freshness="STALE",
                package_fingerprint=package_fingerprint,
            )
            core = _core()
            result = materialize_blueprint_defaults(
                discovery,
                core,
                capture_root=root / "captures",
                ontology=self.ontology,
            )
            materialize_declared_defaults(
                discovery,
                core,
                ontology=self.ontology,
                source_revision_id=1,
                covered_properties=result.covered_properties,
            )

            self.assertEqual(result.counts["staleAssets"], 1)
            self.assertEqual(
                core.execute(
                    """
                    SELECT value_kind, value_text, status
                    FROM facts WHERE fact_name='Count'
                    """
                ).fetchall(),
                [("UNKNOWN", None, "STALE")],
            )
            core.close()
            discovery.close()

    def test_rejects_noncanonical_default_ref_and_changed_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            revision, database_path, package_fingerprint = _write_capture(
                root / "captures"
            )
            connection = sqlite3.connect(database_path)
            connection.execute(
                """
                UPDATE class_defaults
                SET default_ref=?
                WHERE name='Count'
                """,
                (f"bp://{ASSET_ID}@{revision}/default/DifferentProperty",),
            )
            connection.commit()
            connection.close()
            discovery = _discovery(
                revision,
                package_path=database_path.parents[1] / "BP_Test.uasset",
                package_fingerprint=package_fingerprint,
            )
            core = _core()
            result = materialize_blueprint_defaults(
                discovery,
                core,
                capture_root=root / "captures",
                ontology=self.ontology,
            )
            self.assertEqual(result.counts["rejectedAssets"], 1)
            self.assertEqual(
                core.execute("SELECT COUNT(*) FROM facts").fetchone()[0], 0
            )
            core.close()
            discovery.close()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            revision, _database_path, package_fingerprint = _write_capture(
                root / "captures"
            )
            (root / "captures" / "BP_Test" / "BP_Test.uasset").write_bytes(
                b"changed-after-capture"
            )
            discovery = _discovery(
                revision,
                package_path=(root / "captures" / "BP_Test" / "BP_Test.uasset"),
                package_fingerprint=package_fingerprint,
            )
            core = _core()
            result = materialize_blueprint_defaults(
                discovery,
                core,
                capture_root=root / "captures",
                ontology=self.ontology,
            )
            self.assertEqual(result.counts["freshnessGapAssets"], 1)
            self.assertEqual(result.counts["rejectedAssets"], 0)
            materialize_declared_defaults(
                discovery,
                core,
                ontology=self.ontology,
                source_revision_id=1,
                covered_properties=result.covered_properties,
                freshness_gap_assets=result.freshness_gap_assets,
            )
            self.assertEqual(
                core.execute(
                    """
                    SELECT value_kind, value_text, status
                    FROM facts WHERE fact_name='Count'
                    """
                ).fetchall(),
                [
                    (
                        "UNKNOWN",
                        None,
                        "STALE",
                    )
                ],
            )
            core.close()
            discovery.close()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            revision, database_path, package_fingerprint = _write_capture(
                root / "captures"
            )
            discovery = _discovery(
                revision,
                package_path=database_path.parents[1] / "BP_Test.uasset",
                package_fingerprint=package_fingerprint,
            )
            discovery.execute(
                """
                UPDATE assets
                SET source_modified='2000-01-01T00:00:00+00:00'
                """
            )
            core = _core()
            result = materialize_blueprint_defaults(
                discovery,
                core,
                capture_root=root / "captures",
                ontology=self.ontology,
            )
            self.assertEqual(result.counts["freshnessGapAssets"], 0)
            self.assertEqual(result.counts["freshAssets"], 1)
            self.assertGreater(
                core.execute("SELECT COUNT(*) FROM facts").fetchone()[0], 0
            )
            core.close()
            discovery.close()


if __name__ == "__main__":
    unittest.main()
