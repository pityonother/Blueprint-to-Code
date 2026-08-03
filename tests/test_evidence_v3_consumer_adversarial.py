from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import blueprint_tool_server  # noqa: E402
import build_ark_knowledge_base as knowledge_builder  # noqa: E402
import build_asset_context_pack  # noqa: E402
import build_hybrid_context_pack as build_hybrid_context_pack_cli  # noqa: E402
import link_blueprint_native_evidence  # noqa: E402
import query_blueprint_evidence  # noqa: E402
import rebuild_evidence_indexes  # noqa: E402
from blueprint_translator.asset import run_asset_translate  # noqa: E402
from blueprint_translator.evidence_repository import (  # noqa: E402
    open_asset_repository,
)
from blueprint_translator import report_query  # noqa: E402
from blueprint_translator.evidence_revision import (  # noqa: E402
    load_current_evidence_revision,
)
from blueprint_translator.evidence_writer import (  # noqa: E402
    migrate_asset_capture,
    refresh_agent_index,
    write_evidence_artifacts_from_payload,
)
from validate_evidence_store import validate_asset  # noqa: E402


def _payload(name: str, marker: str) -> dict[str, object]:
    return {
        "asset_name": name,
        "asset_path": f"/Game/Test/{name}.{name}",
        "graphs": [
            {
                "graph": "EventGraph",
                "graph_type": "EventGraph",
                "export_index": 1,
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
                        "uasset_export_index": 1,
                        "uasset_read_status": "complete",
                        "confidence": "high",
                    },
                    "nodes": [
                        {
                            "index": 1,
                            "name": marker,
                            "function": "FixtureCall",
                            "pins": [],
                        }
                    ],
                },
            }
        ],
        "class_defaults": {"variables": {}},
    }


def _publish(
    asset_dir: Path,
    marker: str,
    *,
    publish_v3: bool = True,
) -> dict[str, object]:
    source = asset_dir / "source" / f"{asset_dir.name}.uasset"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(f"fixture:{marker}".encode())
    payload = _payload(asset_dir.name, marker)
    return write_evidence_artifacts_from_payload(
        str(payload["asset_path"]),
        source,
        payload,
        asset_dir,
        publish_v3=publish_v3,
    )


@contextmanager
def _directory_alias(alias: Path, target: Path):
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(target)],
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            raise unittest.SkipTest("Windows junction creation is unavailable")
    else:
        try:
            alias.symlink_to(target, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            raise unittest.SkipTest(f"directory symlink creation is unavailable: {exc}") from exc
    try:
        yield alias
    finally:
        if os.path.lexists(alias):
            os.rmdir(alias) if os.name == "nt" else alias.unlink()


class EvidenceV3ConsumerAdversarialTests(unittest.TestCase):
    def test_report_query_uses_bound_index_after_repository_open_path_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir = Path(temporary) / "BoundReportIndexFixture"
            published = _publish(asset_dir, "generation-a")
            agent_index_path = Path(str(published["agent_index_path"]))
            original_text = agent_index_path.read_text(encoding="utf-8")
            original_open = report_query.open_asset_repository

            @contextmanager
            def open_then_replace_index(*args, **kwargs):
                with original_open(*args, **kwargs) as repository:
                    agent_index_path.write_text(
                        "# TAMPERED AFTER REPOSITORY OPEN\n",
                        encoding="utf-8",
                    )
                    yield repository

            with mock.patch.object(
                report_query,
                "open_asset_repository",
                open_then_replace_index,
            ):
                result = blueprint_tool_server.query_report_for_request(
                    asset_dir,
                    "agent_index",
                    mode="full",
                    budget=8000,
                )

            self.assertTrue(result["releaseAuthority"], result)
            self.assertIn(original_text.splitlines()[0], result["content"])
            self.assertNotIn("TAMPERED AFTER REPOSITORY OPEN", result["content"])

    def test_knowledge_summary_uses_bound_index_after_repository_open_path_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir = root / "BoundKnowledgeIndexFixture"
            published = _publish(asset_dir, "generation-a")
            agent_index_path = Path(str(published["agent_index_path"]))
            original_text = agent_index_path.read_text(encoding="utf-8")
            original_open = knowledge_builder.open_asset_repository

            @contextmanager
            def open_then_replace_index(*args, **kwargs):
                with original_open(*args, **kwargs) as repository:
                    agent_index_path.write_text(
                        "# TAMPERED KNOWLEDGE SNIPPET\n",
                        encoding="utf-8",
                    )
                    yield repository

            with mock.patch.object(
                knowledge_builder,
                "open_asset_repository",
                open_then_replace_index,
            ):
                asset = knowledge_builder.summarize_asset_from_repository(
                    asset_dir,
                    root,
                )

            snippet = asset["report_snippets"]["behavior_summary_head"]
            self.assertTrue(asset["sources"]["release_authority"], asset["sources"])
            self.assertIn(original_text.splitlines()[0], snippet)
            self.assertNotIn("TAMPERED KNOWLEDGE SNIPPET", snippet)

    def test_refresh_and_publication_keep_flat_compatibility_on_one_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir = Path(temporary) / "RefreshPublicationRace"
            _publish(asset_dir, "generation-a")
            entered_refresh_publish = threading.Event()
            release_refresh_publish = threading.Event()
            publisher_completed = threading.Event()
            failures: list[BaseException] = []

            from blueprint_translator import evidence_writer as writer_module

            real_publish_staged = writer_module._publish_staged

            def gate_refresh_publish(*args: object, **kwargs: object) -> None:
                if threading.current_thread().name == "refresh-index":
                    entered_refresh_publish.set()
                    if not release_refresh_publish.wait(timeout=10):
                        raise TimeoutError("test did not release refresh publication")
                real_publish_staged(*args, **kwargs)

            def run_refresh() -> None:
                try:
                    refresh_agent_index(asset_dir)
                except BaseException as exc:  # pragma: no cover - asserted below
                    failures.append(exc)

            def run_publication() -> None:
                try:
                    _publish(asset_dir, "generation-b")
                except BaseException as exc:  # pragma: no cover - asserted below
                    failures.append(exc)
                finally:
                    publisher_completed.set()

            with mock.patch.object(
                writer_module,
                "_publish_staged",
                side_effect=gate_refresh_publish,
            ):
                refresh_thread = threading.Thread(
                    target=run_refresh,
                    name="refresh-index",
                )
                refresh_thread.start()
                self.assertTrue(entered_refresh_publish.wait(timeout=10))
                publisher_thread = threading.Thread(
                    target=run_publication,
                    name="publish-next-generation",
                )
                publisher_thread.start()
                time.sleep(0.15)
                self.assertFalse(
                    publisher_completed.is_set(),
                    "publication bypassed the refresh publication lock",
                )
                release_refresh_publish.set()
                refresh_thread.join(timeout=15)
                publisher_thread.join(timeout=15)

            self.assertFalse(refresh_thread.is_alive())
            self.assertFalse(publisher_thread.is_alive())
            self.assertEqual(failures, [])
            current = load_current_evidence_revision(asset_dir, allow_stale=True)
            flat_index = (asset_dir / "output" / "agent_index.md").read_text(
                encoding="utf-8"
            )
            self.assertIn(current.revision_id, flat_index)

            pointer = asset_dir / "evidence" / "current.json"
            held_pointer = asset_dir / "evidence" / "current.held"
            pointer.replace(held_pointer)
            try:
                with open_asset_repository(asset_dir) as repository:
                    self.assertEqual(
                        repository.source_kind,
                        "INDEXED_V2_COMPATIBILITY",
                    )
                    self.assertEqual(repository.revision_id, current.revision_id)
            finally:
                held_pointer.replace(pointer)

    def test_refresh_agent_index_rejects_an_output_junction_without_external_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir = root / "RefreshChildJunction"
            _publish(asset_dir, "generation-a")
            original_output = root / "original-output"
            (asset_dir / "output").rename(original_output)
            external_output = root / "external-output"
            external_output.mkdir()
            sentinel = external_output / "agent_index.md"
            sentinel.write_bytes(b"external-sentinel")
            before = sentinel.read_bytes()

            with _directory_alias(asset_dir / "output", external_output):
                with self.assertRaisesRegex(
                    (ValueError, OSError),
                    r"(?i)(symlink|junction|reparse)",
                ):
                    refresh_agent_index(asset_dir)

            self.assertEqual(sentinel.read_bytes(), before)
            self.assertEqual(
                sorted(path.name for path in external_output.iterdir()),
                ["agent_index.md"],
            )

    def test_flat_writer_rejects_child_junctions_without_external_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for child in ("evidence", "output"):
                with self.subTest(child=child):
                    asset_dir = root / f"Flat{child.title()}Junction"
                    asset_dir.mkdir()
                    external = root / f"external-{child}"
                    external.mkdir()
                    sentinel = external / "sentinel.bin"
                    sentinel.write_bytes(b"external-sentinel")
                    before = sentinel.read_bytes()

                    with _directory_alias(asset_dir / child, external):
                        with self.assertRaisesRegex(
                            (ValueError, OSError),
                            r"(?i)(symlink|junction|reparse)",
                        ):
                            _publish(
                                asset_dir,
                                "generation-a",
                                publish_v3=False,
                            )

                    self.assertEqual(sentinel.read_bytes(), before)
                    self.assertEqual(
                        sorted(path.name for path in external.iterdir()),
                        ["sentinel.bin"],
                    )

    def test_writers_do_not_erase_asset_root_link_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir = root / "real" / "LinkedWriterFixture"
            _publish(asset_dir, "generation-a")
            pointer = asset_dir / "evidence" / "current.json"
            pointer_before = pointer.read_bytes()
            alias = root / "asset-alias"
            source = asset_dir / "source" / f"{asset_dir.name}.uasset"
            payload = _payload(asset_dir.name, "generation-b")
            with _directory_alias(alias, asset_dir):
                with self.assertRaises((ValueError, OSError)):
                    write_evidence_artifacts_from_payload(
                        str(payload["asset_path"]),
                        source,
                        payload,
                        alias,
                    )
                with self.assertRaises((ValueError, OSError)):
                    refresh_agent_index(alias)
                with self.assertRaises((ValueError, OSError)):
                    migrate_asset_capture(alias)
            self.assertEqual(pointer.read_bytes(), pointer_before)

    def test_validator_does_not_erase_asset_root_link_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir = root / "real" / "LinkedValidatorFixture"
            _publish(asset_dir, "generation-a")
            alias = root / "asset-alias"
            with _directory_alias(alias, asset_dir):
                with self.assertRaises((ValueError, OSError)):
                    open_asset_repository(alias)
                with self.assertRaises((ValueError, OSError)):
                    load_current_evidence_revision(alias)
                try:
                    report = validate_asset(alias)
                except (ValueError, OSError):
                    return
                self.assertFalse(report["ok"], report)

    def test_public_readers_reject_a_linked_ancestor_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real_parent = root / "real-parent"
            asset_dir = real_parent / "AncestorLinkedFixture"
            _publish(asset_dir, "generation-a")
            linked_parent = root / "linked-parent"
            with _directory_alias(linked_parent, real_parent):
                lexical_asset_dir = linked_parent / asset_dir.name
                with self.assertRaisesRegex(
                    (ValueError, OSError),
                    r"(?i)(symlink|junction|reparse)",
                ):
                    open_asset_repository(lexical_asset_dir)
                with self.assertRaisesRegex(
                    (ValueError, OSError),
                    r"(?i)(symlink|junction|reparse)",
                ):
                    load_current_evidence_revision(lexical_asset_dir)

    def test_context_pack_does_not_erase_asset_root_link_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir = root / "real" / "LinkedContextFixture"
            _publish(asset_dir, "generation-a")
            alias = root / "asset-alias"
            with _directory_alias(alias, asset_dir):
                with self.assertRaises((ValueError, OSError)):
                    open_asset_repository(alias)
                with self.assertRaises((ValueError, OSError)):
                    build_asset_context_pack.build_pack(alias, "fixture", 1200)

    def test_index_rebuild_does_not_erase_asset_root_link_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir = root / "real" / "LinkedIndexFixture"
            _publish(asset_dir, "generation-a")
            alias = root / "asset-alias"
            with _directory_alias(alias, asset_dir):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    status = rebuild_evidence_indexes.main(
                        ["--asset-dir", str(alias)]
                    )

            response = json.loads(stdout.getvalue())
            self.assertEqual(status, 2, response)
            self.assertEqual(response["failed"], 1)
            self.assertRegex(
                response["failures"][0]["error"],
                r"(?i)(symlink|junction|reparse)",
            )

    def test_native_link_cli_does_not_erase_asset_root_link_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir = root / "real" / "LinkedNativeFixture"
            _publish(asset_dir, "generation-a")
            alias = root / "asset-alias"
            with _directory_alias(alias, asset_dir), mock.patch.object(
                link_blueprint_native_evidence,
                "open_native_evidence_repository",
            ) as open_native:
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    status = link_blueprint_native_evidence.main(
                        [
                            "--asset-dir",
                            str(alias),
                            "--native-evidence-dir",
                            str(root / "native"),
                            "--output-dir",
                            str(root / "hybrid"),
                        ]
                    )

            response = json.loads(stdout.getvalue())
            self.assertEqual(status, 1, response)
            self.assertRegex(
                response["error"],
                r"(?i)(symlink|junction|reparse)",
            )
            open_native.assert_not_called()

    def test_hybrid_context_cli_does_not_erase_asset_root_link_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_dir = root / "real" / "LinkedHybridFixture"
            _publish(asset_dir, "generation-a")
            alias = root / "asset-alias"
            with _directory_alias(alias, asset_dir), mock.patch.object(
                build_hybrid_context_pack_cli,
                "open_hybrid_evidence_repository",
            ) as open_hybrid, mock.patch.object(
                build_hybrid_context_pack_cli,
                "open_native_evidence_repository",
            ) as open_native:
                with self.assertRaisesRegex(
                    (ValueError, OSError),
                    r"(?i)(symlink|junction|reparse)",
                ):
                    build_hybrid_context_pack_cli.main(
                        [
                            "--asset-dir",
                            str(alias),
                            "--hybrid-dir",
                            str(root / "hybrid"),
                            "--native-evidence-dir",
                            str(root / "native"),
                            "--question",
                            "fixture",
                        ]
                    )

            open_hybrid.assert_not_called()
            open_native.assert_not_called()

    def test_direct_orphan_revision_agent_index_is_not_a_report_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir = Path(temporary) / "OrphanReportFixture"
            first = _publish(asset_dir, "generation-a")
            second = _publish(asset_dir, "generation-b")
            first_revision = Path(str(first["revision_dir"])).name
            second_revision = Path(str(second["revision_dir"])).name
            self.assertNotEqual(first_revision, second_revision)
            target = (
                Path("evidence")
                / "revisions"
                / first_revision
                / "agent_index.md"
            ).as_posix()
            traversal = (
                Path("evidence")
                / "revisions"
                / first_revision
                / "ignored"
                / ".."
                / "agent_index.md"
            ).as_posix()
            absolute = str(
                asset_dir
                / "evidence"
                / "revisions"
                / first_revision
                / "agent_index.md"
            )

            for requested in (target, traversal, absolute):
                with self.subTest(requested=requested), self.assertRaises(
                    (ValueError, OSError)
                ):
                    blueprint_tool_server.query_report_for_request(
                        asset_dir,
                        requested,
                        mode="outline",
                        budget=1000,
                    )

    def test_direct_revision_agent_index_fails_closed_when_current_is_damaged(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir = Path(temporary) / "DamagedCurrentReportFixture"
            published = _publish(asset_dir, "generation-a")
            revision = Path(str(published["revision_dir"])).name
            (asset_dir / "evidence" / "current.json").write_text(
                "{}\n",
                encoding="utf-8",
            )
            target = (
                Path("evidence") / "revisions" / revision / "agent_index.md"
            ).as_posix()

            with self.assertRaises((ValueError, OSError)):
                blueprint_tool_server.query_report_for_request(
                    asset_dir,
                    target,
                    mode="outline",
                    budget=1000,
                )

    def test_asset_command_recognizes_pruned_v3_as_indexed_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir = Path(temporary) / "PrunedV3AssetFixture"
            published = _publish(asset_dir, "generation-a")
            for path in (
                asset_dir / "evidence" / "evidence.sqlite",
                asset_dir / "evidence" / "manifest.json",
                asset_dir / "output" / "agent_index.md",
            ):
                path.unlink(missing_ok=True)
            with open_asset_repository(asset_dir) as repository:
                self.assertEqual(repository.source_kind, "INDEXED_V3_CURRENT")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = run_asset_translate(SimpleNamespace(asset_dir=str(asset_dir)))
            self.assertEqual(status, 0, stderr.getvalue())
            self.assertIn("Indexed evidence is ready", stdout.getvalue())
            self.assertIn(str(published["database_path"]), stdout.getvalue())
            self.assertIn(str(published["agent_index_path"]), stdout.getvalue())

    def test_v2_cli_propagates_repository_authority_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            asset_dir = Path(temporary) / "V2MetadataFixture"
            _publish(asset_dir, "generation-a", publish_v3=False)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = query_blueprint_evidence.main(
                    [
                        "--asset-dir",
                        str(asset_dir),
                        "overview",
                        "--budget",
                        "1000",
                    ]
                )
            self.assertEqual(status, 0, stderr.getvalue())
            response = json.loads(stdout.getvalue())
            self.assertEqual(response["sourceKind"], "INDEXED_V2_COMPATIBILITY")
            self.assertEqual(response["freshnessStatus"], "FRESH")
            self.assertFalse(response["releaseAuthority"])
            self.assertTrue(response["migrationRequired"])
            self.assertIsNone(response["manifestSha256"])
            self.assertIsNone(response["pointerSha256"])


if __name__ == "__main__":
    unittest.main()
