from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import blueprint_translator.evidence_publication as publication_module  # noqa: E402
from blueprint_translator.evidence_publication import (  # noqa: E402
    migrate_v2_evidence_to_v3,
    publish_prepared_evidence_revision,
)
from blueprint_translator.evidence_revision import (  # noqa: E402
    load_current_evidence_revision,
)
from blueprint_translator.evidence_writer import (  # noqa: E402
    write_evidence_artifacts_from_payload,
)


def _make_v2_capture(parent: Path, name: str) -> Path:
    asset_dir = parent / name
    source_path = asset_dir / "source" / f"{name}.uasset"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"adversarial-publication-fixture\x00v1")
    payload: dict[str, object] = {
        "asset_name": name,
        "asset_path": f"/Game/Test/{name}.{name}",
        "graphs": [],
        "class_defaults": {},
    }
    write_evidence_artifacts_from_payload(
        str(payload["asset_path"]),
        source_path,
        payload,
        asset_dir,
        publish_v3=False,
    )
    return asset_dir


def _rewrite_all_revision_ids(database_path: Path, revision_id: str) -> None:
    connection = sqlite3.connect(database_path)
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
            if "revision_id" in columns:
                connection.execute(
                    f'UPDATE "{table_name}" SET revision_id = ?',
                    (revision_id,),
                )
        connection.commit()
    finally:
        connection.close()


def _v2_bytes(asset_dir: Path) -> dict[str, bytes]:
    return {
        relative: (asset_dir / relative).read_bytes()
        for relative in (
            "evidence/evidence.sqlite",
            "evidence/manifest.json",
            "output/agent_index.md",
        )
    }


class EvidencePublicationV3AdversarialTests(unittest.TestCase):
    def test_invalid_revision_id_is_rejected_before_rename_without_path_escape_or_orphan(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir = _make_v2_capture(Path(temporary), "RevisionPathEscape")
            database_path = asset_dir / "evidence" / "evidence.sqlite"
            agent_index_path = asset_dir / "output" / "agent_index.md"
            _rewrite_all_revision_ids(database_path, "../escaped")

            with self.assertRaises(ValueError):
                publish_prepared_evidence_revision(
                    asset_dir=asset_dir,
                    database_path=database_path,
                    agent_index_path=agent_index_path,
                )

            self.assertFalse((asset_dir / "evidence" / "current.json").exists())
            self.assertFalse(
                (asset_dir / "evidence" / "escaped").exists(),
                "an invalid database revision must be rejected before staging is renamed",
            )
            revisions_root = asset_dir / "evidence" / "revisions"
            self.assertEqual(
                list(revisions_root.iterdir()) if revisions_root.exists() else [],
                [],
                "invalid identity must not leave an orphan revision",
            )

    def test_v2_manifest_identity_counts_and_paths_are_fully_validated_before_migration(self):
        mutations: dict[str, Callable[[dict[str, object]], None]] = {
            "source_fingerprint": lambda manifest: manifest.__setitem__(
                "source_fingerprint", "0" * 64
            ),
            "counts": lambda manifest: manifest.__setitem__(
                "counts",
                {
                    "graphs": 999_999,
                    "nodes": 0,
                    "pins": 0,
                    "edges": 0,
                    "edge_observations": 0,
                },
            ),
            "database_path": lambda manifest: manifest.__setitem__(
                "database", "../unbound.sqlite"
            ),
            "agent_index_path": lambda manifest: manifest.__setitem__(
                "agent_index", "../other.md"
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, mutate in mutations.items():
                with self.subTest(tamper=label):
                    asset_dir = _make_v2_capture(root, f"V2Tamper{label}")
                    manifest_path = asset_dir / "evidence" / "manifest.json"
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    mutate(manifest)
                    manifest_path.write_text(
                        json.dumps(
                            manifest,
                            ensure_ascii=False,
                            indent=2,
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )

                    with self.assertRaises(ValueError):
                        migrate_v2_evidence_to_v3(asset_dir)

                    self.assertFalse(
                        (asset_dir / "evidence" / "current.json").exists()
                    )
                    revisions_root = asset_dir / "evidence" / "revisions"
                    self.assertEqual(
                        list(revisions_root.iterdir()) if revisions_root.exists() else [],
                        [],
                    )

    def test_post_pointer_compatibility_validation_failure_returns_committed_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir = _make_v2_capture(Path(temporary), "CompatibilityFailure")
            database_path = asset_dir / "evidence" / "evidence.sqlite"
            compatibility_index = asset_dir / "output" / "agent_index.md"
            index_bytes = compatibility_index.read_bytes()
            compatibility_index.unlink()
            compatibility_index.mkdir()

            result = publish_prepared_evidence_revision(
                asset_dir=asset_dir,
                database_path=database_path,
                agent_index_bytes=index_bytes,
            )

            self.assertEqual(
                result.compatibility_copy_status,
                "FAILED_PRESERVED_CURRENT",
            )
            self.assertTrue(result.compatibility_error)
            current = load_current_evidence_revision(asset_dir)
            self.assertEqual(current.revision_id, result.revision_id)
            self.assertEqual(current.manifest_sha256, result.manifest_sha256)

    def test_prune_rename_failure_rolls_back_all_canonical_v2_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir = _make_v2_capture(Path(temporary), "PruneRenameRollback")
            published = migrate_v2_evidence_to_v3(asset_dir)
            before = _v2_bytes(asset_dir)
            locked_target = asset_dir / "evidence" / "manifest.json"
            original_replace = os.replace

            def fail_second_prune_rename(
                source: os.PathLike[str],
                destination: os.PathLike[str],
            ) -> None:
                if Path(source) == locked_target:
                    raise PermissionError("simulated Windows rename lock")
                original_replace(source, destination)

            with mock.patch.object(
                publication_module.os,
                "replace",
                new=fail_second_prune_rename,
            ):
                with self.assertRaises(PermissionError):
                    migrate_v2_evidence_to_v3(asset_dir, prune_v2=True)

            self.assertEqual(_v2_bytes(asset_dir), before)
            self.assertFalse(
                any(".v2-prune-" in path.name for path in asset_dir.rglob("*"))
            )
            self.assertEqual(
                load_current_evidence_revision(asset_dir).revision_id,
                published.revision_id,
            )

    def test_prune_cleanup_lock_is_nonfatal_and_reports_pending_quarantine(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir = _make_v2_capture(Path(temporary), "PruneCleanupPending")
            migrate_v2_evidence_to_v3(asset_dir)
            original_unlink = Path.unlink

            def fail_manifest_quarantine_cleanup(
                path: Path,
                *args: object,
                **kwargs: object,
            ) -> None:
                if path.name.startswith(".manifest.json.v2-prune-"):
                    raise PermissionError("simulated Windows quarantine unlink lock")
                original_unlink(path, *args, **kwargs)

            with mock.patch.object(
                Path,
                "unlink",
                new=fail_manifest_quarantine_cleanup,
            ):
                result = migrate_v2_evidence_to_v3(asset_dir, prune_v2=True)

            self.assertTrue(result.pruned_v2)
            self.assertEqual(result.prune_cleanup_status, "PENDING")
            self.assertTrue(result.prune_cleanup_error)
            self.assertTrue(result.prune_cleanup_leftovers)
            self.assertTrue(
                any(
                    "manifest.json.v2-prune-" in str(path)
                    for path in result.prune_cleanup_leftovers
                )
            )
            for relative in (
                "evidence/evidence.sqlite",
                "evidence/manifest.json",
                "output/agent_index.md",
            ):
                self.assertFalse((asset_dir / relative).exists())
            self.assertTrue(load_current_evidence_revision(asset_dir).release_authority)


if __name__ == "__main__":
    unittest.main()
