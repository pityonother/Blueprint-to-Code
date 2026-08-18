from __future__ import annotations

import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.evidence_publication import (  # noqa: E402
    publish_prepared_evidence_revision,
)
from blueprint_translator.evidence_writer import (  # noqa: E402
    write_evidence_artifacts_from_payload,
)
from blueprint_translator.package_source_snapshot import (  # noqa: E402
    EvidenceSourceChangedDuringCapture,
    snapshot_package_source,
)


def _payload(object_path: str, uasset_path: Path) -> dict[str, object]:
    name = object_path.rsplit("/", 1)[-1].split(".", 1)[0]
    return {
        "asset_name": name,
        "asset_path": object_path,
        "uasset_path": str(uasset_path),
        "uexp_path": "",
        "loaded": True,
        "package": {"uasset_path": str(uasset_path), "uexp_path": ""},
        "structure": {"uasset_path": str(uasset_path), "graph_exports_count": 1},
        "graph_count": 1,
        "node_count": 1,
        "pin_count": 0,
        "link_count": 0,
        "status_counts": {"complete": 1},
        "class_defaults": {"variables": {}},
        "graphs": [
            {
                "graph": "EventGraph",
                "graph_type": "EventGraph",
                "export_index": 7,
                "status": "complete",
                "confidence": "high",
                "node_count": 1,
                "pin_count": 0,
                "link_count": 0,
                "coverage": {},
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
                            "pins": [],
                        }
                    ],
                },
            }
        ],
    }


class PackageSourceSnapshotTests(unittest.TestCase):
    def test_snapshot_detects_content_or_companion_set_changes(self):
        for suffix in (".uasset", ".uexp", ".ubulk"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "Fixture.uasset"
                source.write_bytes(b"uasset-a")
                source.with_suffix(".uexp").write_bytes(b"uexp-a")
                source.with_suffix(".ubulk").write_bytes(b"ubulk-a")
                with snapshot_package_source(source) as snapshot:
                    source.with_suffix(suffix).write_bytes(b"changed-generation")
                    with self.assertRaisesRegex(
                        EvidenceSourceChangedDuringCapture,
                        "EVIDENCE_SOURCE_CHANGED_DURING_CAPTURE",
                    ):
                        snapshot.assert_original_unchanged()

    def test_snapshot_allows_mtime_only_change_when_bytes_and_identity_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "Fixture.uasset"
            source.write_bytes(b"stable-bytes")
            with snapshot_package_source(source) as snapshot:
                original = source.stat()
                os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns + 1_000_000))
                snapshot.assert_original_unchanged()

    def test_cli_refuses_a_source_changed_after_snapshot_parse_without_publishing(self):
        from blueprint_translator import asset as asset_module

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Fixture.uasset"
            source.write_bytes(b"generation-a")
            capture_root = root / "captures"
            args = Namespace(
                asset_binary="/Game/Test/Fixture.Fixture",
                uasset_max_graphs=0,
                prune_legacy=False,
                artifact_mode="indexed",
                content_root=[],
                capture_root=str(capture_root),
                asset_binary_no_report=True,
            )

            def parse_then_change(_asset_path: str, snapshot_path: Path, **_kwargs: object):
                self.assertNotEqual(snapshot_path, source)
                self.assertEqual(snapshot_path.read_bytes(), b"generation-a")
                source.write_bytes(b"generation-b")
                return _payload("/Game/Test/Fixture.Fixture", snapshot_path)

            with (
                mock.patch.object(
                    asset_module,
                    "object_path_to_uasset_path",
                    return_value=(source, []),
                ),
                mock.patch.object(
                    asset_module,
                    "read_uasset_graph_content",
                    side_effect=parse_then_change,
                ),
                self.assertRaisesRegex(
                    EvidenceSourceChangedDuringCapture,
                    "EVIDENCE_SOURCE_CHANGED_DURING_CAPTURE",
                ),
            ):
                asset_module.run_asset_binary_translate(args)

            self.assertFalse((capture_root / "Fixture" / "evidence" / "current.json").exists())


class FreshPublicationAndIdentityTests(unittest.TestCase):
    def test_require_fresh_rejects_source_unavailable_before_current_pointer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "Fixture.uasset"
            source.parent.mkdir()
            source.write_bytes(b"generation-a")
            asset_dir = root / "capture" / "Fixture"
            payload = _payload("/Game/Test/Fixture.Fixture", source)
            prepared = write_evidence_artifacts_from_payload(
                str(payload["asset_path"]),
                source,
                payload,
                asset_dir,
                publish_v3=False,
            )
            source.unlink()

            with self.assertRaisesRegex(ValueError, "EVIDENCE_SOURCE_NOT_FRESH"):
                publish_prepared_evidence_revision(
                    asset_dir=asset_dir,
                    database_path=prepared["database_path"],
                    agent_index_path=prepared["agent_index_path"],
                    require_fresh=True,
                )

            self.assertFalse((asset_dir / "evidence" / "current.json").exists())

    def test_same_short_name_cannot_replace_a_different_object_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir = root / "captures" / "Shared"
            source_a = root / "source-a" / "Shared.uasset"
            source_b = root / "source-b" / "Shared.uasset"
            source_a.parent.mkdir()
            source_b.parent.mkdir()
            source_a.write_bytes(b"asset-a")
            source_b.write_bytes(b"asset-b")
            payload_a = _payload("/Game/AreaA/Shared.Shared", source_a)
            payload_b = _payload("/Game/AreaB/Shared.Shared", source_b)
            write_evidence_artifacts_from_payload(
                str(payload_a["asset_path"]), source_a, payload_a, asset_dir
            )
            pointer = (asset_dir / "evidence" / "current.json").read_bytes()
            revisions = sorted(
                path.name
                for path in (asset_dir / "evidence" / "revisions").iterdir()
                if path.is_dir()
            )

            with self.assertRaisesRegex(
                ValueError,
                "ASSET_DIRECTORY_IDENTITY_MISMATCH",
            ):
                write_evidence_artifacts_from_payload(
                    str(payload_b["asset_path"]), source_b, payload_b, asset_dir
                )

            self.assertEqual((asset_dir / "evidence" / "current.json").read_bytes(), pointer)
            self.assertEqual(
                sorted(
                    path.name
                    for path in (asset_dir / "evidence" / "revisions").iterdir()
                    if path.is_dir()
                ),
                revisions,
            )


if __name__ == "__main__":
    unittest.main()
