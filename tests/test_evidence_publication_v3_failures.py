from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import blueprint_translator.evidence_publication as evidence_publication_module  # noqa: E402
import blueprint_translator.evidence_revision as evidence_revision_module  # noqa: E402
from blueprint_translator.evidence_publication import (  # noqa: E402
    migrate_v2_evidence_to_v3,
    publish_prepared_evidence_revision,
    publish_v2_evidence_revision,
)
from blueprint_translator.evidence_repository import open_asset_repository  # noqa: E402
from blueprint_translator.evidence_revision import (  # noqa: E402
    EvidenceArtifactInvalid,
    load_current_evidence_revision,
    load_evidence_revision,
)
from blueprint_translator.evidence_writer import (  # noqa: E402
    write_evidence_artifacts_from_payload,
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _direct_payload(name: str) -> dict[str, object]:
    return {
        "asset_name": name,
        "asset_path": f"/Game/Test/{name}.{name}",
        "graphs": [
            {
                "graph": "EventGraph",
                "graph_type": "EventGraph",
                "export_index": 7,
                "status": "complete",
                "confidence": "high",
                "node_count": 1,
                "pin_count": 1,
                "link_count": 0,
                "coverage": {"nodePinCoverage": 1.0},
                "warnings": [],
                "payload": {
                    "metadata": {
                        "asset_name": name,
                        "graph_name": "EventGraph",
                        "graph_type": "EventGraph",
                        "uasset_export_index": 7,
                        "uasset_read_status": "complete",
                        "confidence": "high",
                    },
                    "nodes": [
                        {
                            "index": 1,
                            "package_index": 21,
                            "name": "K2Node_Event_0",
                            "label": "Begin Play",
                            "class_name": "K2Node_Event",
                            "node_type": "K2Node_Event",
                            "event": "ReceiveBeginPlay",
                            "source": "fixture_binary_reader",
                            "confidence": "high",
                            "properties": {},
                            "pins": [
                                {
                                    "id": "pin-then",
                                    "persistent_guid": "pin-then",
                                    "name": "then",
                                    "direction": "EGPD_Output",
                                    "category": "exec",
                                    "subcategory": "",
                                    "default": "",
                                    "default_object": "",
                                    "links": [],
                                    "source": "fixture_pin_reader",
                                    "confidence": "high",
                                }
                            ],
                        }
                    ],
                },
            }
        ],
        "class_defaults": {
            "variables": {
                "ExactFixtureValue": {
                    "value": 2.5,
                    "type": "FloatProperty",
                    "source": "fixture_cdo_reader",
                    "confidence": "high",
                }
            }
        },
    }


def _make_v2_capture(
    parent: Path,
    name: str,
    *,
    source_bytes: bytes = b"fixture-package-binary\x00v1",
) -> tuple[Path, Path]:
    asset_dir = parent / name
    source_path = asset_dir / "source" / f"{name}.uasset"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(source_bytes)
    payload = _direct_payload(name)
    write_evidence_artifacts_from_payload(
        str(payload["asset_path"]),
        source_path,
        payload,
        asset_dir,
        publish_v3=False,
    )
    return asset_dir, source_path


def _revision_paths(asset_dir: Path, revision_id: str) -> dict[str, Path]:
    revision_dir = asset_dir / "evidence" / "revisions" / revision_id
    return {
        "pointer": asset_dir / "evidence" / "current.json",
        "revision": revision_dir,
        "manifest": revision_dir / "manifest.json",
        "database": revision_dir / "evidence.sqlite",
        "index": revision_dir / "agent_index.md",
    }


def _rebind_manifest_and_pointer(
    paths: dict[str, Path],
    mutate_manifest: Callable[[dict[str, object]], None],
) -> None:
    manifest = json.loads(paths["manifest"].read_bytes())
    mutate_manifest(manifest)
    manifest_raw = _json_bytes(manifest)
    paths["manifest"].write_bytes(manifest_raw)
    pointer = json.loads(paths["pointer"].read_bytes())
    pointer["manifestSha256"] = _sha256(manifest_raw)
    paths["pointer"].write_bytes(_json_bytes(pointer))


def _rebind_database_artifact(paths: dict[str, Path]) -> None:
    def mutate(manifest: dict[str, object]) -> None:
        artifacts = manifest["artifacts"]
        assert isinstance(artifacts, dict)
        database = artifacts["database"]
        assert isinstance(database, dict)
        raw = paths["database"].read_bytes()
        database["bytes"] = len(raw)
        database["sha256"] = _sha256(raw)

    _rebind_manifest_and_pointer(paths, mutate)


def _v2_artifact_bytes(asset_dir: Path) -> dict[str, bytes]:
    return {
        "evidence/evidence.sqlite": (asset_dir / "evidence" / "evidence.sqlite").read_bytes(),
        "evidence/manifest.json": (asset_dir / "evidence" / "manifest.json").read_bytes(),
        "output/agent_index.md": (asset_dir / "output" / "agent_index.md").read_bytes(),
    }


class EvidenceRevisionIdentityFailureTests(unittest.TestCase):
    def test_reader_rejects_manifest_revision_asset_and_object_identity_mismatches(self):
        mutations: dict[str, tuple[Callable[[dict[str, object]], None], str]] = {
            "revision": (
                lambda manifest: manifest.__setitem__("revisionId", "0" * 24),
                "REVISION_MISMATCH",
            ),
            "asset": (
                lambda manifest: manifest.__setitem__("assetId", "0" * 24),
                "SQLITE_IDENTITY_MISMATCH",
            ),
            "object": (
                lambda manifest: manifest.__setitem__(
                    "objectPath", "/Game/Test/Other.Other"
                ),
                "SQLITE_IDENTITY_MISMATCH",
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, (mutate, expected_code) in mutations.items():
                with self.subTest(identity=label):
                    asset_dir, _source = _make_v2_capture(
                        root,
                        f"Wrong{label.title()}Identity",
                    )
                    published = publish_v2_evidence_revision(asset_dir=asset_dir)
                    paths = _revision_paths(asset_dir, published.revision_id)
                    _rebind_manifest_and_pointer(paths, mutate)

                    with self.assertRaises(EvidenceArtifactInvalid) as caught:
                        load_current_evidence_revision(asset_dir)
                    self.assertEqual(caught.exception.code, expected_code)

    def test_publisher_rejects_asset_identity_mismatch_before_creating_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, _source = _make_v2_capture(
                Path(temporary),
                "PublisherWrongAsset",
            )
            database = asset_dir / "evidence" / "evidence.sqlite"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "UPDATE asset_revisions SET asset_id = ?",
                    ("0" * 24,),
                )
                connection.commit()
            finally:
                connection.close()
            index_bytes = (asset_dir / "output" / "agent_index.md").read_bytes()

            with self.assertRaises(EvidenceArtifactInvalid):
                publish_prepared_evidence_revision(
                    asset_dir=asset_dir,
                    database_path=database,
                    agent_index_bytes=index_bytes,
                )

            self.assertFalse(
                (asset_dir / "evidence" / "current.json").exists(),
                "a rejected database must never become current authority",
            )

    def test_publisher_rejects_revision_not_derived_from_sources_before_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, _source = _make_v2_capture(
                Path(temporary),
                "PublisherWrongRevision",
            )
            database = asset_dir / "evidence" / "evidence.sqlite"
            wrong_revision = "f" * 24
            connection = sqlite3.connect(database)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                table_names = [
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    )
                ]
                for table_name in table_names:
                    columns = {
                        str(row[1])
                        for row in connection.execute(
                            f'PRAGMA table_info("{table_name}")'
                        )
                    }
                    if table_name != "asset_revisions" and "revision_id" in columns:
                        connection.execute(
                            f'UPDATE "{table_name}" SET revision_id = ?',
                            (wrong_revision,),
                        )
                connection.execute(
                    "UPDATE asset_revisions SET revision_id = ?",
                    (wrong_revision,),
                )
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                connection.commit()
            finally:
                connection.close()
            index_bytes = (asset_dir / "output" / "agent_index.md").read_bytes()

            with self.assertRaises(EvidenceArtifactInvalid):
                publish_prepared_evidence_revision(
                    asset_dir=asset_dir,
                    database_path=database,
                    agent_index_bytes=index_bytes,
                )

            self.assertFalse(
                (asset_dir / "evidence" / "current.json").exists(),
                "a non-derived revision id must be rejected before pointer publication",
            )


class EvidenceDatabaseFailureTests(unittest.TestCase):
    def test_reader_rejects_a_hardlinked_revision_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, _source = _make_v2_capture(Path(temporary), "HardlinkAlias")
            published = publish_v2_evidence_revision(asset_dir=asset_dir)
            paths = _revision_paths(asset_dir, published.revision_id)
            alias = asset_dir / "database-hardlink-alias.sqlite"
            try:
                os.link(paths["database"], alias)
            except OSError as exc:
                self.skipTest(f"hard links are unavailable: {exc}")

            with self.assertRaises(EvidenceArtifactInvalid) as caught:
                load_current_evidence_revision(asset_dir)
            self.assertEqual(caught.exception.code, "HARDLINK_REJECTED")

    def test_publisher_rejects_invalid_or_unbounded_index_before_rename(self):
        invalid_indexes = {
            "invalid_utf8": b"\xff\xfe",
            "over_budget": ("evidence-token " * 2000).encode("utf-8"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, index_bytes in invalid_indexes.items():
                with self.subTest(index=label):
                    asset_dir, _source = _make_v2_capture(root, f"BadIndex{label}")
                    database = asset_dir / "evidence" / "evidence.sqlite"
                    with self.assertRaises(ValueError):
                        publish_prepared_evidence_revision(
                            asset_dir=asset_dir,
                            database_path=database,
                            agent_index_bytes=index_bytes,
                            expected_pointer_sha256=None,
                        )
                    self.assertFalse((asset_dir / "evidence" / "current.json").exists())
                    revisions = asset_dir / "evidence" / "revisions"
                    self.assertEqual(list(revisions.iterdir()), [])

    def test_reader_rejects_a_truncated_database_even_if_public_hashes_are_rebound(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, _source = _make_v2_capture(Path(temporary), "TruncatedDatabase")
            published = publish_v2_evidence_revision(asset_dir=asset_dir)
            paths = _revision_paths(asset_dir, published.revision_id)
            paths["database"].write_bytes(paths["database"].read_bytes()[:128])
            _rebind_database_artifact(paths)

            with self.assertRaises(EvidenceArtifactInvalid) as caught:
                load_current_evidence_revision(asset_dir)
            self.assertTrue(caught.exception.code.startswith("SQLITE_"))

    def test_reader_reports_a_real_foreign_key_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, _source = _make_v2_capture(Path(temporary), "ForeignKeyFailure")
            published = publish_v2_evidence_revision(asset_dir=asset_dir)
            paths = _revision_paths(asset_dir, published.revision_id)
            connection = sqlite3.connect(paths["database"])
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    "INSERT INTO properties "
                    "(property_ref, revision_id, owner_kind, owner_ref, name) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        "property:orphan",
                        "0" * 24,
                        "asset",
                        "asset:missing",
                        "OrphanValue",
                    ),
                )
                violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                self.assertTrue(violations, "the fixture must contain a real FK violation")
                connection.commit()
            finally:
                connection.close()
            _rebind_database_artifact(paths)

            with self.assertRaises(EvidenceArtifactInvalid) as caught:
                load_current_evidence_revision(asset_dir)
            self.assertEqual(caught.exception.code, "SQLITE_FOREIGN_KEY_FAILED")


class EvidenceMigrationSafetyTests(unittest.TestCase):
    def test_migration_preserves_v2_by_default_and_only_prunes_when_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preserved_dir, _source = _make_v2_capture(root, "PreservedV2")
            preserved_before = _v2_artifact_bytes(preserved_dir)

            preserved = migrate_v2_evidence_to_v3(preserved_dir)

            self.assertFalse(preserved.pruned_v2)
            self.assertEqual(_v2_artifact_bytes(preserved_dir), preserved_before)
            self.assertEqual(
                load_current_evidence_revision(preserved_dir).revision_id,
                preserved.revision_id,
            )

            pruned_dir, _source = _make_v2_capture(root, "ExplicitlyPrunedV2")
            pruned = migrate_v2_evidence_to_v3(pruned_dir, prune_v2=True)

            self.assertTrue(pruned.pruned_v2)
            for relative in (
                "evidence/evidence.sqlite",
                "evidence/manifest.json",
                "output/agent_index.md",
            ):
                self.assertFalse((pruned_dir / relative).exists())
            self.assertEqual(
                load_current_evidence_revision(pruned_dir).revision_id,
                pruned.revision_id,
            )

    def test_locked_prune_failure_keeps_v3_authority_and_all_v2_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, _source = _make_v2_capture(Path(temporary), "LockedPrune")
            published = migrate_v2_evidence_to_v3(asset_dir)
            before = _v2_artifact_bytes(asset_dir)
            locked_target = asset_dir / "evidence" / "manifest.json"
            original_replace = os.replace

            def locked_replace(source: os.PathLike[str], destination: os.PathLike[str]) -> None:
                if Path(source) == locked_target:
                    raise PermissionError("simulated compatibility manifest lock")
                original_replace(source, destination)

            with mock.patch.object(
                evidence_publication_module.os,
                "replace",
                new=locked_replace,
            ):
                with self.assertRaises(PermissionError):
                    migrate_v2_evidence_to_v3(asset_dir, prune_v2=True)

            validated = load_current_evidence_revision(asset_dir)
            self.assertEqual(validated.revision_id, published.revision_id)
            self.assertTrue(validated.release_authority)
            self.assertEqual(
                _v2_artifact_bytes(asset_dir),
                before,
                "a failed explicit prune must not partially delete compatibility artifacts",
            )


class EvidencePathFailureTests(unittest.TestCase):
    def test_publication_lock_rejects_a_hardlink_without_writing_its_external_alias(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir = root / "HardlinkedLock"
            asset_dir.mkdir()
            external = root / "external-lock"
            external.write_bytes(b"")
            try:
                os.link(external, asset_dir / ".publication.lock")
            except OSError as error:
                self.skipTest(f"hardlink creation is unavailable: {error}")

            with self.assertRaisesRegex(ValueError, r"(?i)(hard|one plain)"):
                with evidence_publication_module.evidence_publication_lock(asset_dir):
                    self.fail("hardlinked publication lock must not be acquired")

            self.assertEqual(external.read_bytes(), b"")

    def test_source_size_mismatch_is_rejected_before_any_hash_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "large-source.uasset"
            source.write_bytes(b"size-mismatch")
            with (
                mock.patch.object(
                    evidence_revision_module.os,
                    "read",
                    side_effect=AssertionError("source bytes must not be read"),
                ),
                self.assertRaisesRegex(
                    EvidenceArtifactInvalid,
                    "FILE_SIZE_MISMATCH",
                ),
            ):
                evidence_revision_module._hash_bound_file(
                    source,
                    "evidence source",
                    expected_size=1,
                )

    def test_current_pointer_rejects_unknown_and_local_path_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            additions = (
                ("unexpected", "value", "POINTER_FIELDS_INVALID"),
                (
                    "localDiagnosticPath",
                    "\\".join(("C" + ":", "Users", "victim", "secret.uasset")),
                    "LOCAL_PATH_DISCLOSURE",
                ),
            )
            for field, value, code in additions:
                with self.subTest(field=field):
                    asset_dir, _source = _make_v2_capture(
                        root,
                        f"PointerExtra{field}",
                    )
                    published = publish_v2_evidence_revision(asset_dir=asset_dir)
                    pointer = _revision_paths(asset_dir, published.revision_id)["pointer"]
                    payload = json.loads(pointer.read_text(encoding="utf-8"))
                    payload[field] = value
                    pointer.write_bytes(_json_bytes(payload))

                    with self.assertRaisesRegex(EvidenceArtifactInvalid, code):
                        load_current_evidence_revision(asset_dir)

    @unittest.skipUnless(os.name == "nt", "Windows junction contract")
    def test_v2_prune_rejects_an_output_junction_without_external_delete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir, _source = _make_v2_capture(root, "PruneOutputJunction")
            original_output = root / "original-output"
            (asset_dir / "output").rename(original_output)
            external_output = root / "external-output"
            external_output.mkdir()
            external_index = external_output / "agent_index.md"
            external_index.write_bytes(b"external-sentinel")
            completed = subprocess.run(
                [
                    "cmd",
                    "/c",
                    "mklink",
                    "/J",
                    str(asset_dir / "output"),
                    str(external_output),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            if completed.returncode != 0:
                original_output.rename(asset_dir / "output")
                self.skipTest("Windows junction creation is unavailable")
            try:
                with self.assertRaisesRegex(
                    ValueError,
                    r"(?i)(symlink|junction|reparse)",
                ):
                    evidence_publication_module._prune_v2_exact(asset_dir)
            finally:
                os.rmdir(asset_dir / "output")

            self.assertEqual(external_index.read_bytes(), b"external-sentinel")
            self.assertTrue((asset_dir / "evidence" / "evidence.sqlite").is_file())
            self.assertTrue((asset_dir / "evidence" / "manifest.json").is_file())

    def test_publisher_rejects_a_symlinked_asset_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir, _source = _make_v2_capture(root / "real", "LinkedAsset")
            alias = root / "asset-alias"
            try:
                os.symlink(asset_dir, alias, target_is_directory=True)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"directory symlink creation is unavailable: {error}")

            with self.assertRaises((ValueError, OSError)):
                publish_v2_evidence_revision(asset_dir=alias)

    def test_publisher_rejects_symlinked_database_and_agent_index_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for artifact in ("database", "index"):
                with self.subTest(artifact=artifact):
                    asset_dir, _source = _make_v2_capture(
                        root,
                        f"Linked{artifact.title()}Source",
                    )
                    database = asset_dir / "evidence" / "evidence.sqlite"
                    index = asset_dir / "output" / "agent_index.md"
                    target = database if artifact == "database" else index
                    backing = asset_dir / f"{target.name}.backing"
                    shutil.copyfile(target, backing)
                    target.unlink()
                    try:
                        os.symlink(backing, target)
                    except (NotImplementedError, OSError) as error:
                        shutil.copyfile(backing, target)
                        self.skipTest(f"file symlink creation is unavailable: {error}")

                    with self.assertRaises((ValueError, OSError)):
                        publish_prepared_evidence_revision(
                            asset_dir=asset_dir,
                            database_path=database,
                            agent_index_path=index,
                        )

    @unittest.skipUnless(os.name == "nt", "Windows junction contract")
    def test_publisher_rejects_a_windows_junction_asset_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir, _source = _make_v2_capture(root / "real", "JunctionAsset")
            alias = root / "asset-junction"
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(alias), str(asset_dir)],
                capture_output=True,
                check=False,
                text=True,
            )
            if completed.returncode != 0:
                self.skipTest("Windows junction creation is unavailable")
            try:
                with self.assertRaises((ValueError, OSError)):
                    publish_v2_evidence_revision(asset_dir=alias)
            finally:
                os.rmdir(alias)

    @unittest.skipUnless(os.name == "nt", "Windows junction contract")
    def test_v2_migration_rejects_nested_evidence_and_output_junctions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for linked_name in ("evidence", "output"):
                with self.subTest(linked_name=linked_name):
                    asset_dir, _source = _make_v2_capture(
                        root,
                        f"Nested{linked_name.title()}Junction",
                    )
                    linked_dir = asset_dir / linked_name
                    backing_dir = root / f"backing-{linked_name}"
                    linked_dir.rename(backing_dir)
                    completed = subprocess.run(
                        [
                            "cmd",
                            "/c",
                            "mklink",
                            "/J",
                            str(linked_dir),
                            str(backing_dir),
                        ],
                        capture_output=True,
                        check=False,
                        text=True,
                    )
                    if completed.returncode != 0:
                        backing_dir.rename(linked_dir)
                        self.skipTest("Windows junction creation is unavailable")
                    try:
                        with self.assertRaisesRegex(
                            ValueError,
                            r"(?i)(symlink|junction|reparse)",
                        ):
                            publish_v2_evidence_revision(asset_dir=asset_dir)
                    finally:
                        os.rmdir(linked_dir)

    def test_v2_migration_publishes_one_locked_snapshot_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir, _source = _make_v2_capture(
                root / "generation-a",
                "SnapshotRace",
                source_bytes=b"fixture-package-binary\x00generation-a",
            )
            replacement_dir, _replacement_source = _make_v2_capture(
                root / "generation-b",
                "SnapshotRace",
                source_bytes=b"fixture-package-binary\x00generation-b",
            )
            manifest_a = json.loads(
                (asset_dir / "evidence" / "manifest.json").read_text(encoding="utf-8")
            )
            manifest_b = json.loads(
                (replacement_dir / "evidence" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotEqual(manifest_a["revision_id"], manifest_b["revision_id"])
            real_publish_prepared = (
                evidence_publication_module.publish_prepared_evidence_revision
            )

            def replace_mutable_v2_then_publish(**kwargs: object):
                for source, destination in (
                    (
                        replacement_dir / "evidence" / "evidence.sqlite",
                        asset_dir / "evidence" / "evidence.sqlite",
                    ),
                    (
                        replacement_dir / "evidence" / "manifest.json",
                        asset_dir / "evidence" / "manifest.json",
                    ),
                    (
                        replacement_dir / "output" / "agent_index.md",
                        asset_dir / "output" / "agent_index.md",
                    ),
                ):
                    shutil.copyfile(source, destination)
                return real_publish_prepared(**kwargs)

            with mock.patch.object(
                evidence_publication_module,
                "publish_prepared_evidence_revision",
                side_effect=replace_mutable_v2_then_publish,
            ):
                published = publish_v2_evidence_revision(asset_dir=asset_dir)

            validated = load_current_evidence_revision(asset_dir)
            self.assertEqual(published.revision_id, manifest_a["revision_id"])
            self.assertEqual(validated.revision_id, manifest_a["revision_id"])

    def test_invalid_current_pointer_never_falls_back_to_valid_v2(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, _source = _make_v2_capture(Path(temporary), "BrokenPointer")
            published = publish_v2_evidence_revision(asset_dir=asset_dir)
            pointer = _revision_paths(asset_dir, published.revision_id)["pointer"]
            pointer.write_bytes(b"{\"schema\":")

            with self.assertRaises((ValueError, OSError)):
                open_asset_repository(asset_dir)

    def test_symlinked_and_broken_symlink_current_never_fall_back_to_v2(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for broken in (False, True):
                with self.subTest(broken=broken):
                    asset_dir, _source = _make_v2_capture(
                        root,
                        "BrokenCurrentLink" if broken else "LinkedCurrent",
                    )
                    published = publish_v2_evidence_revision(asset_dir=asset_dir)
                    pointer = _revision_paths(asset_dir, published.revision_id)["pointer"]
                    backing = asset_dir / "external-current.json"
                    if not broken:
                        backing.write_bytes(pointer.read_bytes())
                    pointer.unlink()
                    try:
                        os.symlink(backing, pointer)
                    except (NotImplementedError, OSError) as error:
                        self.skipTest(f"file symlink creation is unavailable: {error}")

                    with self.assertRaises((ValueError, OSError, FileNotFoundError)):
                        open_asset_repository(asset_dir)


class EvidenceReaderContinuityTests(unittest.TestCase):
    def test_old_reader_remains_usable_while_current_moves_to_a_new_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir, _source = _make_v2_capture(
                root / "generation-one",
                "ReaderContinuity",
                source_bytes=b"fixture-package-binary\x00generation-one",
            )
            first = publish_v2_evidence_revision(asset_dir=asset_dir)
            next_dir, _next_source = _make_v2_capture(
                root / "generation-two",
                "ReaderContinuity",
                source_bytes=b"fixture-package-binary\x00generation-two",
            )

            with open_asset_repository(asset_dir) as old_reader:
                old_identity_before = old_reader.identity()
                second = publish_prepared_evidence_revision(
                    asset_dir=asset_dir,
                    database_path=next_dir / "evidence" / "evidence.sqlite",
                    agent_index_path=next_dir / "output" / "agent_index.md",
                    expected_pointer_sha256=first.pointer_sha256,
                )

                self.assertNotEqual(second.revision_id, first.revision_id)
                self.assertEqual(old_reader.identity(), old_identity_before)
                self.assertEqual(old_reader.revision_id, first.revision_id)
                self.assertTrue(old_reader.graph_summaries())
                self.assertEqual(
                    load_current_evidence_revision(asset_dir).revision_id,
                    second.revision_id,
                )
                self.assertEqual(
                    load_evidence_revision(
                        asset_dir,
                        first.revision_id,
                        allow_stale=True,
                    ).revision_id,
                    first.revision_id,
                )


if __name__ == "__main__":
    unittest.main()
