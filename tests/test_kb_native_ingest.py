from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "native_evidence"
    / "native_evidence_v2.json"
)
REAL_NATIVE_ROOT = ROOT / "native_evidence"
GOLD_SET = ROOT / "ontology" / "native_gold_set.v1.json"
sys.path.insert(0, str(SCRIPTS))

from blueprint_translator.kb_vnext.native_ingest import (  # noqa: E402
    NativeEvidenceCorpusInvalid,
    load_native_evidence_corpus,
    native_evidence_input_sha256,
)
from blueprint_translator.native_evidence_store import (  # noqa: E402
    write_native_evidence_artifacts,
)


_BINARY_SHA256 = "a" * 64


def _write_store(
    native_root: Path,
    *,
    directory_name: str,
    recipe_id: str,
    recipe_sha256: str,
    generated_at: str = "2026-07-27T00:00:00Z",
    mutate: object | None = None,
) -> Path:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["generatedAtUtc"] = generated_at
    payload["provenance"]["generator"]["recipeId"] = recipe_id
    payload["provenance"]["generator"]["recipeSha256"] = recipe_sha256
    payload["evidenceSetId"] = (
        f"native-set://{_BINARY_SHA256}/{recipe_sha256}"
    )
    if callable(mutate):
        mutate(payload)
    source = native_root / f"{directory_name}.source.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    store = (
        native_root
        / "stores"
        / _BINARY_SHA256[:12]
        / directory_name
    )
    write_native_evidence_artifacts(source, store, formal=True)
    return store


class NativeEvidenceCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_missing_root_is_an_explicit_deterministic_empty_corpus(self) -> None:
        missing_a = self.root / "missing-a"
        missing_b = self.root / "missing-b"

        corpus_a = load_native_evidence_corpus(missing_a)
        corpus_b = load_native_evidence_corpus(missing_b)

        self.assertFalse(corpus_a.available)
        self.assertEqual(corpus_a.evidence_sets, ())
        self.assertEqual(corpus_a.functions, ())
        self.assertEqual(corpus_a.blueprint_links, ())
        self.assertRegex(corpus_a.input_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(corpus_a.input_sha256, corpus_b.input_sha256)
        self.assertEqual(
            native_evidence_input_sha256(missing_a),
            corpus_a.input_sha256,
        )

    def test_test_recipe_and_unrelated_files_do_not_change_the_digest(self) -> None:
        production = _write_store(
            self.root,
            directory_name="production",
            recipe_id="ark-production/v1",
            recipe_sha256="c" * 64,
        )
        ignored = _write_store(
            self.root,
            directory_name="directory-name-does-not-say-test",
            recipe_id="test-fixture/v1",
            recipe_sha256="d" * 64,
        )
        before = native_evidence_input_sha256(self.root)

        (self.root / "unrelated.bin").write_bytes(b"unrelated")
        (production / "notes.txt").write_text(
            "not part of the canonical artifact contract",
            encoding="utf-8",
        )
        ignored_source = ignored / "evidence.full.json"
        ignored_payload = json.loads(
            ignored_source.read_text(encoding="utf-8")
        )
        ignored_payload["generatedAtUtc"] = "2099-01-01T00:00:00Z"
        ignored_source.write_text(
            json.dumps(ignored_payload, sort_keys=True),
            encoding="utf-8",
        )

        after = native_evidence_input_sha256(self.root)
        corpus = load_native_evidence_corpus(self.root)

        self.assertEqual(after, before)
        self.assertEqual(
            [row.recipe_id for row in corpus.evidence_sets],
            ["ark-production/v1"],
        )

    def test_digest_is_path_free_and_changes_with_production_metadata(self) -> None:
        first_root = self.root / "first"
        second_root = self.root / "second"
        changed_root = self.root / "changed"
        common = {
            "directory_name": "production",
            "recipe_id": "ark-production/v1",
            "recipe_sha256": "c" * 64,
        }
        _write_store(first_root, **common)
        _write_store(second_root, **common)
        _write_store(
            changed_root,
            **common,
            generated_at="2026-07-28T00:00:00Z",
        )

        first = native_evidence_input_sha256(first_root)
        second = native_evidence_input_sha256(second_root)
        changed = native_evidence_input_sha256(changed_root)

        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)

    def test_corrupt_non_test_store_fails_closed(self) -> None:
        store = _write_store(
            self.root,
            directory_name="production",
            recipe_id="ark-production/v1",
            recipe_sha256="c" * 64,
        )
        with (store / "evidence.sqlite").open("ab") as stream:
            stream.write(b"tamper")

        with self.assertRaisesRegex(
            NativeEvidenceCorpusInvalid,
            "production|SQLite|artifact",
        ):
            load_native_evidence_corpus(self.root)

    def test_overlapping_functions_deduplicate_and_conflicts_fail_closed(
        self,
    ) -> None:
        _write_store(
            self.root,
            directory_name="recipe-a",
            recipe_id="ark-recipe-a/v1",
            recipe_sha256="c" * 64,
        )
        _write_store(
            self.root,
            directory_name="recipe-b",
            recipe_id="ark-recipe-b/v1",
            recipe_sha256="d" * 64,
        )

        corpus = load_native_evidence_corpus(self.root)

        self.assertEqual(len(corpus.evidence_sets), 2)
        self.assertEqual(len(corpus.functions), 4)
        self.assertTrue(
            all(len(function.origins) == 2 for function in corpus.functions)
        )

        conflicting_root = self.root / "conflicting"
        _write_store(
            conflicting_root,
            directory_name="recipe-a",
            recipe_id="ark-recipe-a/v1",
            recipe_sha256="c" * 64,
        )

        def change_signature(payload: dict[str, object]) -> None:
            targets = payload["targets"]
            assert isinstance(targets, list)
            targets[0]["signature"] = "int conflicting_signature(void)"

        _write_store(
            conflicting_root,
            directory_name="recipe-b",
            recipe_id="ark-recipe-b/v1",
            recipe_sha256="d" * 64,
            mutate=change_signature,
        )

        with self.assertRaisesRegex(
            NativeEvidenceCorpusInvalid,
            "conflict",
        ):
            load_native_evidence_corpus(conflicting_root)

    def test_evidence_set_identity_cannot_collide_across_recipes(self) -> None:
        _write_store(
            self.root,
            directory_name="recipe-a",
            recipe_id="ark-recipe-a/v1",
            recipe_sha256="c" * 64,
        )
        _write_store(
            self.root,
            directory_name="recipe-b",
            recipe_id="ark-recipe-b/v1",
            recipe_sha256="c" * 64,
        )

        with self.assertRaisesRegex(
            NativeEvidenceCorpusInvalid,
            "evidence.?set.*(collision|duplicate|unique)",
        ):
            load_native_evidence_corpus(self.root)

    def test_authoritative_exact_identities_must_be_canonical(self) -> None:
        cases = {
            "padded_recipe": {
                "recipe_id": "  ark-padded/v1  ",
                "mutate": None,
            },
            "padded_symbol": {
                "recipe_id": "ark-padded-symbol/v1",
                "mutate": lambda target: target.__setitem__(
                    "qualifiedName",
                    f"  {target['qualifiedName']}  ",
                ),
            },
            "padded_signature": {
                "recipe_id": "ark-padded-signature/v1",
                "mutate": lambda target: target.__setitem__(
                    "signature",
                    f"  {target['signature']}  ",
                ),
            },
            "noncanonical_declared_rva": {
                "recipe_id": "ark-rva-alias/v1",
                "mutate": lambda target: target.__setitem__(
                    "rva",
                    "0x01000",
                ),
            },
        }
        for label, case in cases.items():
            with self.subTest(identity=label):
                native_root = self.root / label

                def mutate_identity(
                    payload: dict[str, object],
                    *,
                    mutate=case["mutate"],
                ) -> None:
                    if mutate is None:
                        return
                    targets = payload["targets"]
                    assert isinstance(targets, list)
                    mutate(targets[0])

                _write_store(
                    native_root,
                    directory_name="noncanonical",
                    recipe_id=str(case["recipe_id"]),
                    recipe_sha256="c" * 64,
                    mutate=mutate_identity,
                )

                with self.assertRaisesRegex(
                    NativeEvidenceCorpusInvalid,
                    "canonical",
                ):
                    load_native_evidence_corpus(native_root)

    def test_raw_confidence_label_alone_cannot_promote_function_or_child(
        self,
    ) -> None:
        def downgrade_direct_evidence(payload: dict[str, object]) -> None:
            targets = payload["targets"]
            assert isinstance(targets, list)
            target = targets[0]
            target["status"] = "CANDIDATE"
            target["confidence"] = "PDB-SYMBOL-PLUS-DECOMPILER"
            field = target["fieldAccesses"][0]
            field["status"] = "CANDIDATE"
            field["confidence"] = "LOW"

        _write_store(
            self.root,
            directory_name="candidate",
            recipe_id="ark-candidate/v1",
            recipe_sha256="c" * 64,
            mutate=downgrade_direct_evidence,
        )

        corpus = load_native_evidence_corpus(self.root)
        function = corpus.match_gold_target(
            "ark-candidate/v1",
            "FixtureMath::ComputeQuality(int)",
            "0x1000",
        )

        self.assertIsNotNone(function)
        assert function is not None
        self.assertEqual(function.normalized_status, "CANDIDATE")
        self.assertEqual(function.normalized_confidence, "LOW")
        self.assertEqual(
            function.origins[0].raw_confidence,
            "PDB-SYMBOL-PLUS-DECOMPILER",
        )
        self.assertEqual(function.field_accesses[0].raw_status, "CANDIDATE")
        self.assertEqual(
            function.field_accesses[0].normalized_status,
            "CANDIDATE",
        )
        with self.assertRaises(FrozenInstanceError):
            function.normalized_status = "CONFIRMED"  # type: ignore[misc]

    def test_confirmed_status_with_low_raw_confidence_cannot_promote(self) -> None:
        def lower_function_confidence(payload: dict[str, object]) -> None:
            targets = payload["targets"]
            assert isinstance(targets, list)
            targets[0]["status"] = "CONFIRMED"
            targets[0]["confidence"] = "LOW"

        _write_store(
            self.root,
            directory_name="low-confidence",
            recipe_id="ark-low-confidence/v1",
            recipe_sha256="c" * 64,
            mutate=lower_function_confidence,
        )

        corpus = load_native_evidence_corpus(self.root)
        function = corpus.match_gold_target(
            "ark-low-confidence/v1",
            "FixtureMath::ComputeQuality(int)",
            "0x1000",
        )

        self.assertIsNotNone(function)
        assert function is not None
        self.assertEqual(function.origins[0].raw_status, "CONFIRMED")
        self.assertEqual(function.origins[0].raw_confidence, "LOW")
        self.assertEqual(function.normalized_status, "CANDIDATE")
        self.assertEqual(function.normalized_confidence, "LOW")

    def test_noncanonical_native_function_uri_aliases_fail_closed(self) -> None:
        aliases = {
            "uppercase_binary": (
                f"native://{'A' * 64}/fixture.dll/0x1000"
            ),
            "leading_zero_rva": (
                f"native://{_BINARY_SHA256}/fixture.dll/0x001000"
            ),
            "signed_rva": (
                f"native://{_BINARY_SHA256}/fixture.dll/0x+1000"
            ),
            "encoded_module_separator": (
                f"native://{_BINARY_SHA256}/fixture%2Edll/0x1000"
            ),
        }
        for label, alias in aliases.items():
            with self.subTest(alias=label):
                native_root = self.root / label

                def alias_function_uri(
                    payload: dict[str, object],
                    *,
                    value: str = alias,
                ) -> None:
                    targets = payload["targets"]
                    assert isinstance(targets, list)
                    targets[0]["evidenceId"] = value

                _write_store(
                    native_root,
                    directory_name="aliased",
                    recipe_id=f"ark-{label}/v1",
                    recipe_sha256="c" * 64,
                    mutate=alias_function_uri,
                )

                with self.assertRaisesRegex(
                    NativeEvidenceCorpusInvalid,
                    "canonical|identity|invalid",
                ):
                    load_native_evidence_corpus(native_root)

    def test_latest_store_per_recipe_is_selected_deterministically(self) -> None:
        _write_store(
            self.root,
            directory_name="older",
            recipe_id="ark-production/v1",
            recipe_sha256="c" * 64,
            generated_at="2026-07-26T00:00:00Z",
        )
        _write_store(
            self.root,
            directory_name="newer",
            recipe_id="ark-production/v1",
            recipe_sha256="d" * 64,
            generated_at="2026-07-27T00:00:00Z",
        )

        corpus = load_native_evidence_corpus(self.root)

        self.assertEqual(len(corpus.evidence_sets), 1)
        self.assertEqual(corpus.evidence_sets[0].recipe_sha256, "d" * 64)
        self.assertTrue(
            all(
                origin.recipe_sha256 == "d" * 64
                for function in corpus.functions
                for origin in function.origins
            )
        )

    @unittest.skipUnless(
        len(
            list(
                REAL_NATIVE_ROOT.glob(
                    "stores/*/ark-*/evidence.manifest.json"
                )
            )
        )
        >= 2,
        "real production Native Evidence stores are not present",
    )
    def test_real_production_corpus_has_expected_exact_coverage(self) -> None:
        corpus = load_native_evidence_corpus(REAL_NATIVE_ROOT)
        gold = json.loads(GOLD_SET.read_text(encoding="utf-8"))

        self.assertTrue(corpus.available)
        self.assertEqual(len(corpus.evidence_sets), 2)
        self.assertEqual(len(corpus.functions), 204)
        self.assertEqual(
            sum(len(function.origins) - 1 for function in corpus.functions),
            16,
        )
        self.assertEqual(
            sum(
                len(function.field_accesses)
                for function in corpus.functions
            ),
            0,
        )
        self.assertTrue(
            all(
                function.normalized_status == "CONFIRMED"
                and function.normalized_confidence == "HIGH"
                for function in corpus.functions
            )
        )
        matches = [
            corpus.match_gold_target(
                target["recipeId"],
                target["qualifiedSymbol"],
                target["rva"],
            )
            for target in gold["targets"]
        ]
        self.assertEqual(len(matches), 20)
        self.assertTrue(all(match is not None for match in matches))


if __name__ == "__main__":
    unittest.main()
