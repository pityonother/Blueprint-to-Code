from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.evidence_repository import (  # noqa: E402
    resolve_asset_evidence_state,
)
from blueprint_translator.evidence_revision import EvidenceArtifactInvalid  # noqa: E402
from blueprint_translator.evidence_writer import (  # noqa: E402
    write_evidence_artifacts_from_payload,
)
from blueprint_translator.interpretation_publication import (  # noqa: E402
    InterpretationArtifactInvalid,
    InterpretationPublicationError,
    load_current_interpretation,
    publish_interpretation,
)
from blueprint_translator.interpretation.contracts import (  # noqa: E402
    artifact_descriptor,
    canonical_json_bytes,
    semantic_digest,
    sha256_bytes,
)
from blueprint_translator.interpretation.render import render_markdown  # noqa: E402
from blueprint_translator.interpretation import publication as publication_module  # noqa: E402
from interpretation_fixture import (  # noqa: E402
    interpretation_payload,
    publish_interpretation_fixture,
)


class InterpretationPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.asset_dir, self.source_path, _payload = publish_interpretation_fixture(
            Path(self._temporary.name)
        )

    def install_rewritten_current(
        self,
        published: object,
        *,
        mutate: Callable[[dict[str, Any]], None],
    ) -> None:
        old_dir = published.revision_dir
        manifest = json.loads((old_dir / "manifest.json").read_text(encoding="utf-8"))
        documents = {
            "interpretation": json.loads(
                (old_dir / "interpretation.json").read_text(encoding="utf-8")
            ),
            "trace": json.loads((old_dir / "trace.json").read_text(encoding="utf-8")),
            "gaps": json.loads((old_dir / "gaps.json").read_text(encoding="utf-8")),
            "markdown": (old_dir / "interpretation.md").read_bytes(),
            "pseudocode": (old_dir / "pseudocode.txt").read_bytes(),
        }
        mutate(documents)
        interpretation = documents["interpretation"]
        trace = documents["trace"]
        gaps = documents["gaps"]
        projection = {
            key: value
            for key, value in interpretation.items()
            if key not in {"semanticDigest", "generatedAt"}
        }
        projection["gaps"] = gaps["items"]
        digest = semantic_digest(projection)
        interpretation["semanticDigest"] = digest
        trace["semanticDigest"] = digest
        gaps["semanticDigest"] = digest
        manifest["semanticDigest"] = digest
        rendered_markdown = render_markdown(
            interpretation,
            gaps["items"],
        )
        if not rendered_markdown.endswith("\n"):
            rendered_markdown += "\n"
        documents["markdown"] = rendered_markdown.encode("utf-8")
        raws = {
            "interpretationJson": canonical_json_bytes(interpretation),
            "interpretationMarkdown": documents["markdown"],
            "trace": canonical_json_bytes(trace),
            "gaps": canonical_json_bytes(gaps),
            "pseudocode": documents["pseudocode"],
        }
        names = {
            "interpretationJson": "interpretation.json",
            "interpretationMarkdown": "interpretation.md",
            "trace": "trace.json",
            "gaps": "gaps.json",
            "pseudocode": "pseudocode.txt",
        }
        manifest["artifacts"] = {
            key: artifact_descriptor(names[key], raw) for key, raw in raws.items()
        }
        revision_id = sha256_bytes(
            canonical_json_bytes(
                {
                    "semanticDigest": digest,
                    "interpreterVersion": manifest["interpreterVersion"],
                    "schemaVersion": manifest["schemaVersion"],
                    "artifacts": manifest["artifacts"],
                },
                newline=False,
            )
        )[:24]
        manifest["revisionId"] = revision_id
        manifest_raw = canonical_json_bytes(manifest)
        rewritten_dir = old_dir.parent / revision_id
        rewritten_dir.mkdir()
        for key, filename in names.items():
            (rewritten_dir / filename).write_bytes(raws[key])
        (rewritten_dir / "manifest.json").write_bytes(manifest_raw)
        pointer_path = self.asset_dir / "interpretation" / "current.json"
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        pointer["revisionId"] = revision_id
        pointer["manifest"] = f"revisions/{revision_id}/manifest.json"
        pointer["manifestSha256"] = sha256_bytes(manifest_raw)
        pointer_path.write_bytes(canonical_json_bytes(pointer))

    def test_stale_evidence_identity_fails_closed(self) -> None:
        published = publish_interpretation(self.asset_dir, budget=32_000)
        payload = interpretation_payload("InterpretationFixture")
        payload["class_defaults"]["variables"]["DefaultThreshold"]["value"] = 9.25
        self.source_path.write_bytes(b"fixture-package:changed-generation")
        write_evidence_artifacts_from_payload(
            str(payload["asset_path"]),
            self.source_path,
            payload,
            self.asset_dir,
            publish_v3=True,
        )

        with self.assertRaises(InterpretationArtifactInvalid) as caught:
            load_current_interpretation(self.asset_dir)
        self.assertEqual(caught.exception.code, "INTERPRETATION_STALE_EVIDENCE")
        pointer = json.loads(
            (self.asset_dir / "interpretation" / "current.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(pointer["revisionId"], published.revision_id)

    def test_interrupted_publication_leaves_reusable_orphan(self) -> None:
        def fail_after_rename(checkpoint: str) -> None:
            if checkpoint == "after_revision_rename":
                raise RuntimeError("fixture interruption")

        with self.assertRaisesRegex(RuntimeError, "fixture interruption"):
            publish_interpretation(
                self.asset_dir,
                budget=32_000,
                fault_injector=fail_after_rename,
            )
        self.assertFalse((self.asset_dir / "interpretation" / "current.json").exists())
        orphaned = list((self.asset_dir / "interpretation" / "revisions").iterdir())
        self.assertEqual(len(orphaned), 1)

        published = publish_interpretation(self.asset_dir, budget=32_000)
        self.assertTrue(published.reused)
        self.assertEqual(published.revision_id, orphaned[0].name)
        self.assertTrue((self.asset_dir / "interpretation" / "current.json").is_file())

    def test_evidence_change_during_generation_never_advances_pointer(self) -> None:
        initial = resolve_asset_evidence_state(self.asset_dir)
        changed = replace(
            initial,
            pointer_sha256="f" * 64,
            manifest_sha256="e" * 64,
        )
        with mock.patch(
            "blueprint_translator.interpretation.publication.resolve_asset_evidence_state",
            side_effect=[initial, changed],
        ):
            with self.assertRaises(InterpretationPublicationError) as caught:
                publish_interpretation(self.asset_dir, budget=32_000)
        self.assertEqual(caught.exception.code, "EVIDENCE_REVISION_CHANGED")
        self.assertFalse((self.asset_dir / "interpretation" / "current.json").exists())

    def test_tampered_interpretation_artifact_is_rejected(self) -> None:
        published = publish_interpretation(self.asset_dir, budget=32_000)
        artifact = (
            self.asset_dir
            / "interpretation"
            / "revisions"
            / published.revision_id
            / "interpretation.json"
        )
        artifact.write_bytes(artifact.read_bytes() + b" ")
        with self.assertRaises(InterpretationArtifactInvalid) as caught:
            load_current_interpretation(self.asset_dir)
        self.assertEqual(caught.exception.code, "INTERPRETATION_ARTIFACT_SIZE_MISMATCH")

    def test_fabricated_confirmed_summary_is_rejected_after_full_rehash(self) -> None:
        published = publish_interpretation(self.asset_dir, budget=32_000)

        def mutate(documents: dict[str, Any]) -> None:
            interpretation = documents["interpretation"]
            interpretation["assetSummary"]["confirmedLocalCalls"].append(
                {
                    "referenceRef": "FABRICATED",
                    "targetRef": "FABRICATED",
                    "name": "FABRICATED",
                    "evidenceRefs": [],
                }
            )

        self.install_rewritten_current(published, mutate=mutate)
        with self.assertRaises(InterpretationArtifactInvalid) as caught:
            load_current_interpretation(self.asset_dir)
        self.assertEqual(
            caught.exception.code,
            "INTERPRETATION_SEMANTIC_EVIDENCE_MISMATCH",
        )

    def test_fabricated_pseudocode_is_rejected_after_full_rehash(self) -> None:
        published = publish_interpretation(self.asset_dir, budget=32_000)

        def mutate(documents: dict[str, Any]) -> None:
            trace = documents["trace"]
            lines = documents["pseudocode"].decode("utf-8").splitlines(
                keepends=True
            )
            target_index = next(
                index
                for index, row in enumerate(trace["pseudocodeLines"])
                if row["executable"]
            )
            lines[target_index] = lines[target_index].replace(
                ";  // statement://",
                " FABRICATED;  // statement://",
                1,
            )
            documents["pseudocode"] = "".join(lines).encode("utf-8")
            offset = 0
            for line_number, (row, line) in enumerate(
                zip(trace["pseudocodeLines"], lines, strict=True),
                start=1,
            ):
                content = line.removesuffix("\n").encode("utf-8")
                row["line"] = line_number
                row["startByte"] = offset
                row["endByte"] = offset + len(content)
                offset += len(line.encode("utf-8"))

        self.install_rewritten_current(published, mutate=mutate)
        with self.assertRaises(InterpretationArtifactInvalid) as caught:
            load_current_interpretation(self.asset_dir)
        self.assertEqual(caught.exception.code, "INTERPRETATION_TRACE_INVALID")

    def test_fail_on_gap_does_not_publish(self) -> None:
        with self.assertRaises(InterpretationPublicationError) as caught:
            publish_interpretation(
                self.asset_dir,
                budget=32_000,
                fail_on_gap=True,
            )
        self.assertEqual(caught.exception.code, "INTERPRETATION_GAPS_PRESENT")
        self.assertFalse((self.asset_dir / "interpretation" / "current.json").exists())

    def test_post_pointer_failure_restores_the_previous_current(self) -> None:
        first = publish_interpretation(self.asset_dir, budget=32_000)
        pointer_path = self.asset_dir / "interpretation" / "current.json"
        pointer_before = pointer_path.read_bytes()

        def fail_after_pointer(checkpoint: str) -> None:
            if checkpoint == "after_pointer_cas":
                raise RuntimeError("fixture post-pointer interruption")

        with self.assertRaises(InterpretationPublicationError) as caught:
            publish_interpretation(
                self.asset_dir,
                budget=32_000,
                generated_at="2026-08-03T12:34:56Z",
                fault_injector=fail_after_pointer,
            )
        self.assertEqual(
            caught.exception.code,
            "INTERPRETATION_PUBLICATION_ROLLED_BACK",
        )
        self.assertEqual(pointer_path.read_bytes(), pointer_before)
        self.assertEqual(load_current_interpretation(self.asset_dir).revision_id, first.revision_id)

    def test_source_change_after_build_never_advances_current(self) -> None:
        original_build = publication_module.build_interpretation

        def build_then_change(*args: object, **kwargs: object):
            built = original_build(*args, **kwargs)
            self.source_path.write_bytes(self.source_path.read_bytes() + b"-changed")
            return built

        with mock.patch.object(
            publication_module,
            "build_interpretation",
            side_effect=build_then_change,
        ):
            with self.assertRaises(EvidenceArtifactInvalid) as caught:
                publish_interpretation(self.asset_dir, budget=32_000)
        self.assertEqual(caught.exception.code, "STALE_SOURCE")
        self.assertFalse((self.asset_dir / "interpretation" / "current.json").exists())

    def test_hardlinked_artifact_is_rejected(self) -> None:
        published = publish_interpretation(self.asset_dir, budget=32_000)
        artifact = published.revision_dir / "trace.json"
        alias = Path(self._temporary.name) / "trace-hardlink.json"
        try:
            os.link(artifact, alias)
        except OSError as exc:
            self.skipTest(f"hard links are unavailable: {exc}")
        self.addCleanup(alias.unlink, missing_ok=True)
        with self.assertRaises((OSError, ValueError)):
            load_current_interpretation(self.asset_dir)

    def test_interpretation_directory_swap_cannot_write_outside_asset(self) -> None:
        external = Path(self._temporary.name) / "external"
        (external / "revisions").mkdir(parents=True)
        interpretation = self.asset_dir / "interpretation"
        parked = self.asset_dir / "interpretation-parked"
        original_read = publication_module._read_optional_pointer
        swapped = False

        def read_then_swap(path: Path) -> bytes | None:
            nonlocal swapped
            result = original_read(path)
            if not swapped:
                os.replace(interpretation, parked)
                try:
                    os.symlink(external, interpretation, target_is_directory=True)
                except OSError:
                    os.replace(parked, interpretation)
                    raise
                swapped = True
            return result

        try:
            with mock.patch.object(
                publication_module,
                "_read_optional_pointer",
                side_effect=read_then_swap,
            ):
                with self.assertRaises(InterpretationPublicationError) as caught:
                    publish_interpretation(self.asset_dir, budget=32_000)
            self.assertEqual(caught.exception.code, "INTERPRETATION_DIRECTORY_CHANGED")
            self.assertEqual(list((external / "revisions").iterdir()), [])
            self.assertEqual(
                [path.name for path in external.iterdir()],
                ["revisions"],
            )
        except OSError as exc:
            self.skipTest(f"directory symlinks are unavailable: {exc}")
        finally:
            if interpretation.is_symlink():
                interpretation.unlink()
            if parked.exists() and not interpretation.exists():
                os.replace(parked, interpretation)

    def test_asset_identity_cannot_be_rebound_away_from_current_evidence(self) -> None:
        published = publish_interpretation(self.asset_dir, budget=32_000)
        old_dir = published.revision_dir
        manifest = json.loads((old_dir / "manifest.json").read_text(encoding="utf-8"))
        interpretation = json.loads(
            (old_dir / "interpretation.json").read_text(encoding="utf-8")
        )
        trace = json.loads((old_dir / "trace.json").read_text(encoding="utf-8"))
        gaps = json.loads((old_dir / "gaps.json").read_text(encoding="utf-8"))
        rebound_asset_id = "a" * 24
        interpretation["assetId"] = rebound_asset_id
        trace["assetId"] = rebound_asset_id
        gaps["assetId"] = rebound_asset_id
        projection = {
            key: value
            for key, value in interpretation.items()
            if key not in {"semanticDigest", "generatedAt"}
        }
        projection["gaps"] = gaps["items"]
        digest = semantic_digest(projection)
        interpretation["semanticDigest"] = digest
        trace["semanticDigest"] = digest
        gaps["semanticDigest"] = digest
        manifest["assetId"] = rebound_asset_id
        manifest["semanticDigest"] = digest

        raws = {
            "interpretationJson": canonical_json_bytes(interpretation),
            "interpretationMarkdown": (old_dir / "interpretation.md").read_bytes(),
            "trace": canonical_json_bytes(trace),
            "gaps": canonical_json_bytes(gaps),
            "pseudocode": (old_dir / "pseudocode.txt").read_bytes(),
        }
        names = {
            "interpretationJson": "interpretation.json",
            "interpretationMarkdown": "interpretation.md",
            "trace": "trace.json",
            "gaps": "gaps.json",
            "pseudocode": "pseudocode.txt",
        }
        manifest["artifacts"] = {
            key: artifact_descriptor(names[key], raw) for key, raw in raws.items()
        }
        revision_id = sha256_bytes(
            canonical_json_bytes(
                {
                    "semanticDigest": digest,
                    "interpreterVersion": manifest["interpreterVersion"],
                    "schemaVersion": manifest["schemaVersion"],
                    "artifacts": manifest["artifacts"],
                },
                newline=False,
            )
        )[:24]
        manifest["revisionId"] = revision_id
        manifest_raw = canonical_json_bytes(manifest)
        rebound_dir = old_dir.parent / revision_id
        rebound_dir.mkdir()
        for key, filename in names.items():
            (rebound_dir / filename).write_bytes(raws[key])
        (rebound_dir / "manifest.json").write_bytes(manifest_raw)

        pointer_path = self.asset_dir / "interpretation" / "current.json"
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        pointer["revisionId"] = revision_id
        pointer["manifest"] = f"revisions/{revision_id}/manifest.json"
        pointer["manifestSha256"] = sha256_bytes(manifest_raw)
        pointer_path.write_bytes(canonical_json_bytes(pointer))

        with self.assertRaises(InterpretationArtifactInvalid) as caught:
            load_current_interpretation(self.asset_dir)
        self.assertEqual(caught.exception.code, "INTERPRETATION_STALE_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
