from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.evidence_repository import open_asset_repository  # noqa: E402
from blueprint_translator.evidence_writer import (  # noqa: E402
    write_evidence_artifacts_from_payload,
)


POINTER_SCHEMA = "blueprint-to-code.evidence-current/v1"
MANIFEST_SCHEMA = "blueprint-to-code.evidence-revision-manifest/v3"


def _publication_api() -> ModuleType:
    return importlib.import_module("blueprint_translator.evidence_publication")


def _revision_api() -> ModuleType:
    return importlib.import_module("blueprint_translator.evidence_revision")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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


def _make_v2_capture(root: Path, name: str) -> tuple[Path, Path]:
    asset_dir = root / name
    source_path = asset_dir / "source" / f"{name}.uasset"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"fixture-package-binary\x00v1")
    payload = _direct_payload(name)
    write_evidence_artifacts_from_payload(
        str(payload["asset_path"]),
        source_path,
        payload,
        asset_dir,
        publish_v3=False,
    )
    return asset_dir, source_path


def _make_legacy_capture(root: Path, name: str) -> Path:
    asset_dir = root / name
    graph_path = asset_dir / "graphs_from_uasset" / "EventGraph_7.json"
    _write_json(
        graph_path,
        {
            "metadata": {
                "asset_name": name,
                "graph_name": "EventGraph",
                "graph_type": "EventGraph",
                "uasset_export_index": 7,
                "uasset_read_status": "complete",
                "confidence": "high",
            },
            "nodes": [],
            "pins": [],
            "links": [],
        },
    )
    _write_json(
        asset_dir / "graphs_from_uasset_manifest.json",
        {
            "schema": "blueprint-translator.graphs-from-uasset-manifest.v1",
            "asset_name": name,
            "asset_path": f"/Game/Test/{name}.{name}",
            "files": [
                {
                    "graph": "EventGraph",
                    "graph_type": "EventGraph",
                    "export_index": 7,
                    "status": "complete",
                    "confidence": "high",
                    "path": "graphs_from_uasset/EventGraph_7.json",
                }
            ],
        },
    )
    return asset_dir


def _published_paths(asset_dir: Path, revision_id: str) -> dict[str, Path]:
    revision_dir = asset_dir / "evidence" / "revisions" / revision_id
    return {
        "pointer": asset_dir / "evidence" / "current.json",
        "revision": revision_dir,
        "database": revision_dir / "evidence.sqlite",
        "manifest": revision_dir / "manifest.json",
        "index": revision_dir / "agent_index.md",
    }


def _repo_metadata(repository: object, key: str) -> Any:
    if hasattr(repository, key):
        return getattr(repository, key)
    metadata = getattr(repository, "metadata", None)
    if isinstance(metadata, dict) and key in metadata:
        return metadata[key]
    raise AssertionError(f"repository metadata is missing {key!r}")


def _snapshot_files(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class EvidencePublicationV3ContractTests(unittest.TestCase):
    def test_v2_publication_binds_layout_hashes_and_reader_metadata_without_local_paths(self):
        publication = _publication_api()
        revision_reader = _revision_api()
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, source_path = _make_v2_capture(Path(temporary), "LayoutFixture")

            published = publication.publish_v2_evidence_revision(asset_dir=asset_dir)
            paths = _published_paths(asset_dir, published.revision_id)

            self.assertEqual(
                {path.name for path in paths["revision"].iterdir()},
                {"evidence.sqlite", "manifest.json", "agent_index.md"},
            )
            self.assertEqual(Path(published.revision_dir), paths["revision"])
            pointer_bytes = paths["pointer"].read_bytes()
            manifest_bytes = paths["manifest"].read_bytes()
            pointer = json.loads(pointer_bytes)
            manifest = json.loads(manifest_bytes)

            self.assertEqual(pointer["schema"], POINTER_SCHEMA)
            self.assertEqual(pointer["revisionId"], published.revision_id)
            self.assertEqual(
                pointer["manifest"],
                f"revisions/{published.revision_id}/manifest.json",
            )
            self.assertEqual(pointer["manifestSha256"], _sha256(manifest_bytes))
            self.assertEqual(pointer["mode"], "indexed")
            self.assertEqual(published.pointer_sha256, _sha256(pointer_bytes))
            self.assertEqual(published.manifest_sha256, _sha256(manifest_bytes))

            self.assertEqual(manifest["schema"], MANIFEST_SCHEMA)
            self.assertEqual(manifest["revisionId"], published.revision_id)
            self.assertEqual(manifest["objectPath"], "/Game/Test/LayoutFixture.LayoutFixture")
            self.assertRegex(manifest["semanticDigest"], r"^[0-9a-f]{64}$")
            self.assertIn("generatedAt", manifest)
            self.assertIn("sourceManifest", manifest)
            self.assertIn("counts", manifest)
            self.assertIn("graphCoverage", manifest)
            self.assertIn("linkRecoveryCounts", manifest)
            self.assertNotIn("manifestSha256", manifest)
            self.assertEqual(manifest["artifacts"]["database"]["path"], "evidence.sqlite")
            self.assertEqual(manifest["artifacts"]["agentIndex"]["path"], "agent_index.md")
            for artifact_name, path_key in (("database", "database"), ("agentIndex", "index")):
                artifact = manifest["artifacts"][artifact_name]
                artifact_bytes = paths[path_key].read_bytes()
                self.assertEqual(artifact["bytes"], len(artifact_bytes))
                self.assertEqual(artifact["sha256"], _sha256(artifact_bytes))

            public_bytes = pointer_bytes + manifest_bytes
            self.assertNotIn(str(asset_dir.resolve()).encode(), public_bytes)
            self.assertNotIn(str(source_path.resolve()).encode(), public_bytes)
            self.assertNotIn(str(source_path.resolve()).replace("\\", "/").encode(), public_bytes)

            loaded = revision_reader.load_current_evidence_revision(asset_dir)
            self.assertEqual(loaded.revision_id, published.revision_id)
            self.assertEqual(loaded.manifest_sha256, published.manifest_sha256)
            self.assertEqual(loaded.pointer_sha256, published.pointer_sha256)
            self.assertEqual(loaded.freshness_status, "FRESH")
            self.assertTrue(loaded.release_authority)

            with open_asset_repository(asset_dir) as repository:
                self.assertEqual(repository.revision_id, published.revision_id)
                self.assertEqual(_repo_metadata(repository, "freshness_status"), "FRESH")
                self.assertTrue(_repo_metadata(repository, "release_authority"))
                self.assertEqual(
                    _repo_metadata(repository, "manifest_sha256"),
                    published.manifest_sha256,
                )
                self.assertEqual(
                    _repo_metadata(repository, "pointer_sha256"),
                    published.pointer_sha256,
                )

            self.assertTrue((asset_dir / "evidence" / "evidence.sqlite").is_file())
            self.assertTrue((asset_dir / "evidence" / "manifest.json").is_file())
            self.assertTrue((asset_dir / "output" / "agent_index.md").is_file())

    def test_deterministic_rerun_reuses_the_revision_without_overwriting_any_bytes(self):
        publication = _publication_api()
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, _source_path = _make_v2_capture(Path(temporary), "RerunFixture")
            database_path = asset_dir / "evidence" / "evidence.sqlite"
            index_bytes = (asset_dir / "output" / "agent_index.md").read_bytes()

            first = publication.publish_prepared_evidence_revision(
                asset_dir=asset_dir,
                database_path=database_path,
                agent_index_bytes=index_bytes,
            )
            paths = _published_paths(asset_dir, first.revision_id)
            first_revision = _snapshot_files(paths["revision"])
            first_pointer = (paths["pointer"].read_bytes(), paths["pointer"].stat().st_mtime_ns)
            time.sleep(0.01)

            second = publication.publish_prepared_evidence_revision(
                asset_dir=asset_dir,
                database_path=database_path,
                agent_index_bytes=index_bytes,
                expected_pointer_sha256=first.pointer_sha256,
            )

            self.assertEqual(second.revision_id, first.revision_id)
            self.assertEqual(second.manifest_sha256, first.manifest_sha256)
            self.assertEqual(second.pointer_sha256, first.pointer_sha256)
            self.assertTrue(second.reused_existing)
            self.assertEqual(_snapshot_files(paths["revision"]), first_revision)
            self.assertEqual(
                (paths["pointer"].read_bytes(), paths["pointer"].stat().st_mtime_ns),
                first_pointer,
            )

    def test_database_index_manifest_and_pointer_tampering_all_fail_closed_without_v2_fallback(self):
        publication = _publication_api()
        revision_reader = _revision_api()
        mutations: dict[str, Callable[[dict[str, Path]], None]] = {
            "database": lambda paths: paths["database"].write_bytes(
                paths["database"].read_bytes() + b"tamper"
            ),
            "index": lambda paths: paths["index"].write_bytes(
                paths["index"].read_bytes() + b"\nTampered\n"
            ),
            "manifest": lambda paths: paths["manifest"].write_bytes(
                paths["manifest"].read_bytes() + b" "
            ),
            "pointer": self._tamper_pointer_manifest_hash,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, mutate in mutations.items():
                with self.subTest(artifact=label):
                    asset_dir, _source_path = _make_v2_capture(root, f"Tamper{label.title()}")
                    published = publication.publish_v2_evidence_revision(asset_dir=asset_dir)
                    paths = _published_paths(asset_dir, published.revision_id)
                    mutate(paths)

                    with self.assertRaises((ValueError, OSError)):
                        revision_reader.load_current_evidence_revision(asset_dir)
                    with self.assertRaises((ValueError, OSError)):
                        open_asset_repository(asset_dir)

    @staticmethod
    def _tamper_pointer_manifest_hash(paths: dict[str, Path]) -> None:
        pointer = json.loads(paths["pointer"].read_text(encoding="utf-8"))
        pointer["manifestSha256"] = "0" * 64
        _write_json(paths["pointer"], pointer)

    def test_wal_and_shm_sidecars_are_rejected_even_when_bound_artifacts_are_unchanged(self):
        publication = _publication_api()
        revision_reader = _revision_api()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for suffix in ("-wal", "-shm"):
                with self.subTest(sidecar=suffix):
                    asset_dir, _source_path = _make_v2_capture(
                        root,
                        "Sidecar" + suffix.removeprefix("-").upper(),
                    )
                    published = publication.publish_v2_evidence_revision(asset_dir=asset_dir)
                    paths = _published_paths(asset_dir, published.revision_id)
                    paths["database"].with_name(paths["database"].name + suffix).write_bytes(
                        b"unexpected-sidecar"
                    )

                    with self.assertRaises((ValueError, OSError)):
                        revision_reader.load_current_evidence_revision(asset_dir)

    def test_changed_source_is_stale_and_requires_explicit_allow_stale(self):
        publication = _publication_api()
        revision_reader = _revision_api()
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, source_path = _make_v2_capture(Path(temporary), "StaleFixture")
            published = publication.publish_v2_evidence_revision(asset_dir=asset_dir)
            source_path.write_bytes(b"fixture-package-binary\x00v2")

            with self.assertRaises(ValueError):
                revision_reader.load_current_evidence_revision(asset_dir)
            with self.assertRaises(ValueError):
                open_asset_repository(asset_dir)

            loaded = revision_reader.load_current_evidence_revision(
                asset_dir,
                allow_stale=True,
            )
            self.assertEqual(loaded.revision_id, published.revision_id)
            self.assertEqual(loaded.freshness_status, "STALE")
            self.assertTrue(loaded.release_authority)
            with open_asset_repository(asset_dir, allow_stale=True) as repository:
                self.assertEqual(_repo_metadata(repository, "freshness_status"), "STALE")
                self.assertTrue(_repo_metadata(repository, "release_authority"))

    def test_missing_local_source_is_explicit_source_unavailable(self):
        publication = _publication_api()
        revision_reader = _revision_api()
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, source_path = _make_v2_capture(
                Path(temporary),
                "UnavailableFixture",
            )
            published = publication.publish_v2_evidence_revision(asset_dir=asset_dir)
            source_path.unlink()

            loaded = revision_reader.load_current_evidence_revision(asset_dir)
            self.assertEqual(loaded.revision_id, published.revision_id)
            self.assertEqual(loaded.freshness_status, "SOURCE_UNAVAILABLE")
            self.assertTrue(loaded.release_authority)
            with open_asset_repository(asset_dir) as repository:
                self.assertEqual(
                    _repo_metadata(repository, "freshness_status"),
                    "SOURCE_UNAVAILABLE",
                )

    def test_v2_compatibility_is_readable_but_never_release_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, _source_path = _make_v2_capture(
                Path(temporary),
                "V2CompatibilityFixture",
            )

            with open_asset_repository(asset_dir) as repository:
                self.assertEqual(
                    _repo_metadata(repository, "source_kind"),
                    "INDEXED_V2_COMPATIBILITY",
                )
                self.assertFalse(_repo_metadata(repository, "release_authority"))
                self.assertTrue(_repo_metadata(repository, "migration_required"))
                self.assertEqual(_repo_metadata(repository, "freshness_status"), "FRESH")

    def test_legacy_projection_requires_opt_in_and_is_never_release_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir = _make_legacy_capture(Path(temporary), "LegacyFixture")
            before = _snapshot_files(asset_dir)

            try:
                unexpected_repository = open_asset_repository(asset_dir)
            except FileNotFoundError:
                pass
            else:
                unexpected_repository.close()
                self.fail("legacy fallback must require explicit opt-in")

            with open_asset_repository(
                asset_dir,
                allow_legacy_fallback=True,
            ) as repository:
                self.assertEqual(
                    _repo_metadata(repository, "source_kind"),
                    "LEGACY_TEMPORARY_PROJECTION",
                )
                self.assertFalse(_repo_metadata(repository, "release_authority"))
            self.assertEqual(_snapshot_files(asset_dir), before)

    def test_explicit_pointer_compare_and_swap_rejects_a_stale_expected_pointer(self):
        publication = _publication_api()
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, _source_path = _make_v2_capture(Path(temporary), "CASFixture")
            database_path = asset_dir / "evidence" / "evidence.sqlite"
            index_path = asset_dir / "output" / "agent_index.md"

            first = publication.publish_prepared_evidence_revision(
                asset_dir=asset_dir,
                database_path=database_path,
                agent_index_path=index_path,
                expected_pointer_sha256=None,
            )
            pointer_before = (asset_dir / "evidence" / "current.json").read_bytes()

            with self.assertRaises(publication.EvidencePointerConflict):
                publication.publish_prepared_evidence_revision(
                    asset_dir=asset_dir,
                    database_path=database_path,
                    agent_index_path=index_path,
                    expected_pointer_sha256=None,
                )

            self.assertEqual(
                (asset_dir / "evidence" / "current.json").read_bytes(),
                pointer_before,
            )
            self.assertFalse(first.reused_existing)

    def test_failure_after_revision_rename_leaves_an_orphan_that_can_be_reused(self):
        publication = _publication_api()
        revision_reader = _revision_api()
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, _source_path = _make_v2_capture(Path(temporary), "OrphanFixture")
            database_path = asset_dir / "evidence" / "evidence.sqlite"
            index_path = asset_dir / "output" / "agent_index.md"
            compatibility_index_before = index_path.read_bytes()

            def fail_after_rename(phase: str) -> None:
                if phase == "after_revision_rename":
                    raise RuntimeError("injected failure after immutable rename")

            with self.assertRaisesRegex(RuntimeError, "immutable rename"):
                publication.publish_prepared_evidence_revision(
                    asset_dir=asset_dir,
                    database_path=database_path,
                    agent_index_path=index_path,
                    expected_pointer_sha256=None,
                    fault_injector=fail_after_rename,
                )

            revisions_root = asset_dir / "evidence" / "revisions"
            orphan_dirs = [path for path in revisions_root.iterdir() if path.is_dir()]
            self.assertEqual(len(orphan_dirs), 1)
            self.assertFalse((asset_dir / "evidence" / "current.json").exists())
            self.assertEqual(index_path.read_bytes(), compatibility_index_before)
            with self.assertRaises(FileNotFoundError):
                revision_reader.load_current_evidence_revision(asset_dir)
            orphan = revision_reader.load_evidence_revision(
                asset_dir,
                orphan_dirs[0].name,
            )
            self.assertFalse(orphan.release_authority)

            published = publication.publish_prepared_evidence_revision(
                asset_dir=asset_dir,
                database_path=database_path,
                agent_index_path=index_path,
                expected_pointer_sha256=None,
            )
            self.assertTrue(published.reused_existing)
            self.assertEqual(Path(published.revision_dir), orphan_dirs[0])
            self.assertTrue((asset_dir / "evidence" / "current.json").is_file())
            self.assertTrue(
                revision_reader.load_current_evidence_revision(
                    asset_dir
                ).release_authority
            )

    def test_publisher_reports_freshness_revalidated_after_pointer_commit(self):
        publication = _publication_api()
        revision_reader = _revision_api()
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, source_path = _make_v2_capture(
                Path(temporary),
                "PostPointerFreshness",
            )
            database_path = asset_dir / "evidence" / "evidence.sqlite"
            index_path = asset_dir / "output" / "agent_index.md"

            def change_source_after_pointer(phase: str) -> None:
                if phase == "after_pointer_replace":
                    source_path.write_bytes(b"changed-after-pointer")

            published = publication.publish_prepared_evidence_revision(
                asset_dir=asset_dir,
                database_path=database_path,
                agent_index_path=index_path,
                expected_pointer_sha256=None,
                fault_injector=change_source_after_pointer,
            )

            self.assertEqual(published.freshness_status, "STALE")
            self.assertTrue(published.release_authority)
            current = revision_reader.load_current_evidence_revision(
                asset_dir,
                allow_stale=True,
            )
            self.assertEqual(current.freshness_status, "STALE")

    def test_publisher_drops_authority_when_current_advances_before_compatibility(self):
        publication = _publication_api()
        revision_reader = _revision_api()
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, source_path = _make_v2_capture(
                Path(temporary),
                "PostPointerAuthority",
            )
            database_path = asset_dir / "evidence" / "evidence.sqlite"
            index_path = asset_dir / "output" / "agent_index.md"
            first = publication.publish_prepared_evidence_revision(
                asset_dir=asset_dir,
                database_path=database_path,
                agent_index_path=index_path,
                expected_pointer_sha256=None,
            )
            first_pointer_raw = (asset_dir / "evidence" / "current.json").read_bytes()

            source_path.write_bytes(b"fixture-package-binary\x00v2")
            payload = _direct_payload(asset_dir.name)
            write_evidence_artifacts_from_payload(
                str(payload["asset_path"]),
                source_path,
                payload,
                asset_dir,
                publish_v3=False,
            )
            real_load_current = revision_reader.load_current_evidence_revision

            def load_then_advance(*args: object, **kwargs: object):
                validated = real_load_current(*args, **kwargs)
                (asset_dir / "evidence" / "current.json").write_bytes(
                    first_pointer_raw
                )
                return validated

            with patch.object(
                revision_reader,
                "load_current_evidence_revision",
                side_effect=load_then_advance,
            ):
                second = publication.publish_prepared_evidence_revision(
                    asset_dir=asset_dir,
                    database_path=database_path,
                    agent_index_path=index_path,
                    expected_pointer_sha256=first.pointer_sha256,
                )

            self.assertNotEqual(second.revision_id, first.revision_id)
            self.assertEqual(
                second.compatibility_copy_status,
                "SKIPPED_CURRENT_ADVANCED",
            )
            self.assertFalse(second.release_authority)
            current = real_load_current(asset_dir, allow_stale=True)
            self.assertEqual(current.revision_id, first.revision_id)

    def test_same_revision_identity_with_different_artifact_bytes_is_a_collision(self):
        publication = _publication_api()
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, _source_path = _make_v2_capture(Path(temporary), "CollisionFixture")
            database_path = asset_dir / "evidence" / "evidence.sqlite"
            original_index = (asset_dir / "output" / "agent_index.md").read_bytes()

            def fail_after_rename(phase: str) -> None:
                if phase == "after_revision_rename":
                    raise RuntimeError("leave collision candidate orphan")

            with self.assertRaises(RuntimeError):
                publication.publish_prepared_evidence_revision(
                    asset_dir=asset_dir,
                    database_path=database_path,
                    agent_index_bytes=original_index,
                    expected_pointer_sha256=None,
                    fault_injector=fail_after_rename,
                )
            orphan_root = next(
                path
                for path in (asset_dir / "evidence" / "revisions").iterdir()
                if path.is_dir()
            )
            orphan_before = _snapshot_files(orphan_root)

            with self.assertRaises(publication.EvidenceRevisionCollision):
                publication.publish_prepared_evidence_revision(
                    asset_dir=asset_dir,
                    database_path=database_path,
                    agent_index_bytes=original_index + b"\nDifferent artifact bytes\n",
                    expected_pointer_sha256=None,
                )

            self.assertEqual(_snapshot_files(orphan_root), orphan_before)
            self.assertFalse((asset_dir / "evidence" / "current.json").exists())

    def test_symlinked_database_is_rejected_even_when_target_bytes_match(self):
        publication = _publication_api()
        revision_reader = _revision_api()
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir, _source_path = _make_v2_capture(Path(temporary), "SymlinkFixture")
            published = publication.publish_v2_evidence_revision(asset_dir=asset_dir)
            paths = _published_paths(asset_dir, published.revision_id)
            backing = asset_dir / "matching-database-copy.sqlite"
            shutil.copyfile(paths["database"], backing)
            paths["database"].unlink()
            try:
                os.symlink(backing, paths["database"])
            except (NotImplementedError, OSError) as error:
                shutil.copyfile(backing, paths["database"])
                self.skipTest(f"symlink creation is unavailable: {error}")

            with self.assertRaises((ValueError, OSError)):
                revision_reader.load_current_evidence_revision(asset_dir)


if __name__ == "__main__":
    unittest.main()
