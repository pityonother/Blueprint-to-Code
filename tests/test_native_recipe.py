import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.native_identity import NativeIdentityError  # noqa: E402
from blueprint_translator.native_recipe import (  # noqa: E402
    create_native_recipe_evidence_manifest,
    load_native_recipe,
    requires_registered_binary_hashes,
    validate_native_recipe,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def _target(target_id: str, selector: dict) -> dict:
    return {
        "id": target_id,
        "selector": selector,
        "expectedMatches": 1,
        "exports": {
            "decompile": True,
            "callersDepth": 1,
            "calleesDepth": 1,
            "constants": True,
            "fieldAccesses": True,
            "branches": True,
            "vtable": True,
        },
    }


def _recipe() -> dict:
    return {
        "schema": "blueprint-to-code-native-analysis-recipe/v1",
        "recipeId": "test-native-fixture/v1",
        "description": "Synthetic fixture recipe.",
        "binaryModule": "fixture.dll",
        "requirements": {
            "pdbRequired": True,
            "formalProvenanceRequired": True,
        },
        "targets": [
            _target(
                "quality-qualified",
                {"qualifiedName": "Fixture::ComputeQuality"},
            ),
            _target(
                "quality-signature",
                {
                    "qualifiedName": "Fixture::ComputeQuality",
                    "signature": "int Fixture::ComputeQuality(int,int)",
                },
            ),
            _target("quality-rva", {"rva": "0x1000"}),
            _target(
                "quality-simple",
                {"simpleName": "QualityLeaf", "allowSimpleName": True},
            ),
        ],
        "fieldQueries": [
            {
                "id": "quality-scale-field",
                "structureName": "Fixture::QualityInputs",
                "fieldName": "multiplier",
                "expectedMatches": 1,
            }
        ],
        "vtableQueries": [
            {
                "id": "quality-adjust-slot",
                "className": "Fixture::QualityModel",
                "slotOffset": "0x8",
                "expectedMatches": 1,
            }
        ],
        "budgets": {
            "maxFunctions": 32,
            "maxCallEdges": 128,
            "maxFieldAccesses": 128,
            "maxConstants": 256,
            "maxVtableMatches": 16,
            "maxDecompiledCharactersPerFunction": 40000,
            "maxTotalDecompiledCharacters": 200000,
        },
    }


def _identity() -> dict:
    return {
        "schema": "blueprint-to-code-native-build-identity/v1",
        "binary": {
            "module": "fixture.dll",
            "sha256": SHA_A,
            "size": 4096,
            "machine": "x86_64",
            "machineCode": "0x8664",
            "peTimestamp": 1,
            "imageBase": "0x180000000",
            "codeView": {
                "format": "RSDS",
                "guid": "11111111-2222-3333-4444-555555555555",
                "age": 1,
                "pdbFileName": "fixture.pdb",
            },
        },
        "pdb": {
            "fileName": "fixture.pdb",
            "sha256": SHA_B,
            "size": 8192,
            "version": 20000404,
            "signature": "0x12345678",
            "guid": "11111111-2222-3333-4444-555555555555",
            "age": 1,
            "matchesBinary": True,
        },
        "project": {
            "name": f"ShooterGameNative_{SHA_A[:12]}",
            "hashPrefix": SHA_A[:12],
            "workspaceSlug": SHA_A[:12],
        },
    }


def _raw_export(recipe_sha: str) -> dict:
    evidence_id = f"native://{SHA_A}/fixture.dll/0x1000"
    return {
        "schema": "blueprint-to-code-native-recipe-export/v1",
        "program": "fixture.dll",
        "binarySha256": SHA_A,
        "languageId": "x86:LE:64:default",
        "compilerSpecId": "windows",
        "pdbLoaded": True,
        "pdbGuid": "11111111-2222-3333-4444-555555555555",
        "pdbAge": "1",
        "recipeId": "test-native-fixture/v1",
        "recipeSha256": recipe_sha,
        "targetResults": [
            {
                "targetId": "quality-qualified",
                "selector": {"qualifiedName": "Fixture::ComputeQuality"},
                "expectedMatches": 1,
                "matchCount": 1,
                "resolvedEvidenceIds": [evidence_id],
                "candidates": [
                    {
                        "evidenceId": evidence_id,
                        "qualifiedName": "Fixture::ComputeQuality",
                        "accepted": True,
                        "rejectionReason": "",
                    }
                ],
                "status": "CONFIRMED",
            }
        ],
        "functions": [
            {
                "evidenceId": evidence_id,
                "name": "ComputeQuality",
                "qualifiedName": "Fixture::ComputeQuality",
                "owner": "Fixture",
                "rva": "0x1000",
                "signature": "int Fixture::ComputeQuality(int,int)",
                "symbolSource": "IMPORTED",
                "status": "CONFIRMED",
                "confidence": "HIGH",
                "decompile": {
                    "completed": True,
                    "textSha256": "c" * 64,
                    "lineCount": 1,
                    "characterCount": 15,
                    "text": "return value;",
                },
                "decompiledC": "return value;",
                "parameters": [],
                "returns": [],
                "callSites": [],
                "calledFunctions": [],
                "incomingCallers": [],
                "calls": [],
                "fieldAccesses": [],
                "numericConstants": [],
                "stringConstants": [],
                "constants": [],
                "branches": [],
                "vtableSlots": [],
                "gaps": [],
            }
        ],
        "fieldQueryResults": [],
        "vtableQueryResults": [],
        "gaps": [],
    }


def _document(recipe: dict) -> dict:
    source = json.dumps(recipe, sort_keys=True).encode("utf-8")
    return {
        "schema": "blueprint-to-code-native-analysis-recipe-document/v1",
        "sha256": hashlib.sha256(source).hexdigest(),
        "recipe": recipe,
    }


def _wrap(raw: dict, document: dict) -> dict:
    return create_native_recipe_evidence_manifest(
        raw,
        recipe_document=document,
        identity=_identity(),
        ghidra={
            "version": "12.1.2",
            "releaseAssetSha256": "d" * 64,
            "analysisOptionsSha256": "e" * 64,
        },
        java={"vendor": "Eclipse Adoptium", "version": "21"},
        generator={
            "repositoryCommit": "f" * 40,
            "repositoryDirty": False,
            "recipeId": document["recipe"]["recipeId"],
            "recipeSha256": document["sha256"],
            "scriptSha256": {
                "runner": "1" * 64,
                "exporter": "2" * 64,
                "pdbConfigurator": "3" * 64,
            },
        },
        formal=True,
    )


class NativeRecipeTests(unittest.TestCase):
    def assert_error_code(self, expected: str, callback) -> None:
        with self.assertRaises(NativeIdentityError) as raised:
            callback()
        self.assertEqual(raised.exception.code, expected)

    def test_selector_modes_are_explicit_and_formal_regex_is_forbidden(self):
        validate_native_recipe(_recipe(), formal=True)

        regex_recipe = _recipe()
        regex_recipe["targets"] = [
            _target("discovery-only", {"regex": "^Fixture::Quality.*"})
        ]
        self.assert_error_code(
            "NATIVE_RECIPE_SELECTOR_FORBIDDEN",
            lambda: validate_native_recipe(regex_recipe, formal=True),
        )
        validate_native_recipe(regex_recipe, formal=False)

    def test_expected_matches_and_simple_name_opt_in_are_mandatory(self):
        missing_count = _recipe()
        missing_count["targets"][0].pop("expectedMatches")
        self.assert_error_code(
            "NATIVE_RECIPE_SCHEMA_INVALID",
            lambda: validate_native_recipe(missing_count, formal=True),
        )

        unsafe_simple = _recipe()
        unsafe_simple["targets"] = [
            _target("unsafe-simple", {"simpleName": "ComputeQuality"})
        ]
        self.assert_error_code(
            "NATIVE_RECIPE_SELECTOR_FORBIDDEN",
            lambda: validate_native_recipe(unsafe_simple, formal=True),
        )

    def test_recipe_hash_is_raw_file_sha256_and_document_is_path_free(self):
        payload = _recipe()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "recipe.json"
            source = json.dumps(payload, indent=2) + "\n"
            path.write_text(source, encoding="utf-8")
            expected_hash = hashlib.sha256(path.read_bytes()).hexdigest()

            document = load_native_recipe(path, formal=True)

        self.assertEqual(document["sha256"], expected_hash)
        self.assertEqual(document["recipe"], payload)
        self.assertNotIn(str(path.parent), str(document))

    def test_public_fixture_does_not_require_ark_hash_registration(self):
        fixture = load_native_recipe(
            ROOT
            / "scripts"
            / "native_analysis"
            / "recipes"
            / "test-native-fixture.v1.json",
            formal=True,
        )["recipe"]
        loot = load_native_recipe(
            ROOT
            / "scripts"
            / "native_analysis"
            / "recipes"
            / "ark-loot-quality.v1.json",
            formal=True,
        )["recipe"]
        registered_module = "ShooterGameEditor-ShooterGame.dll"

        self.assertFalse(
            requires_registered_binary_hashes(
                fixture,
                registered_module=registered_module,
            )
        )
        self.assertTrue(
            requires_registered_binary_hashes(
                loot,
                registered_module=registered_module,
            )
        )

    def test_runner_explicit_inputs_skip_devkit_and_temp_is_cleaned(self):
        runner = (
            ROOT / "scripts" / "native_analysis" / "Run-NativeRecipe.ps1"
        ).read_text(encoding="utf-8")
        common = (
            ROOT
            / "scripts"
            / "native_analysis"
            / "NativeAnalysis.Common.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "if ([bool]$DllPath -xor [bool]$PdbPath)",
            runner,
        )
        self.assertIn(
            "elseif (-not $DllPath -and -not $PdbPath)",
            runner,
        )
        self.assertIn("finally {", runner)
        self.assertIn("Remove-NativeRunDirectory", runner)
        self.assertIn("function Remove-NativeRunDirectory", common)
        self.assertIn("StartsWith(", common)
        self.assertIn(
            "Remove-Item -LiteralPath $resolvedRunRoot -Recurse -Force",
            common,
        )
        self.assertIn("trap {", runner)
        self.assertIn(
            "blueprint-to-code-native-run-diagnostic/v1",
            runner,
        )
        self.assertIn("ok = $false", runner)
        self.assertIn("native-diagnostic-", runner)

    def test_committed_recipes_validate_and_fixture_has_exact_targets(self):
        recipe_root = ROOT / "scripts" / "native_analysis" / "recipes"
        expectations = {
            "ark-loot-quality.v1.json": ("ark-loot-quality/v1", 14),
            "ark-harvest-native.v1.json": ("ark-harvest-native/v1", 4),
            "test-native-fixture.v1.json": ("test-native-fixture/v1", 8),
        }
        for file_name, (recipe_id, minimum_targets) in expectations.items():
            with self.subTest(file=file_name):
                document = load_native_recipe(
                    recipe_root / file_name,
                    formal=True,
                )
                self.assertEqual(document["recipe"]["recipeId"], recipe_id)
                self.assertGreaterEqual(
                    len(document["recipe"]["targets"]),
                    minimum_targets,
                )
                if file_name == "ark-loot-quality.v1.json":
                    selectors = {
                        target["id"]: target["selector"]
                        for target in document["recipe"]["targets"]
                    }
                    self.assertEqual(
                        selectors["victory-core-weighted-random-index"],
                        {"rva": "0xB65B60"},
                    )
                    self.assertEqual(
                        selectors["primal-item-add-new-item"],
                        {"rva": "0x1414E60"},
                    )
                    self.assertEqual(
                        selectors["item-stat-info-get-modifier"],
                        {"rva": "0x143D7B0"},
                    )
                    self.assertEqual(
                        selectors["primal-item-init-new-item"],
                        {"rva": "0x1447DB0"},
                    )
                    self.assertEqual(
                        selectors["primal-item-initialize-item"],
                        {"rva": "0x1448C40"},
                    )

    def test_export_count_mismatch_fails_even_when_exporter_claims_success(self):
        recipe = _recipe()
        recipe["targets"] = [recipe["targets"][0]]
        recipe["fieldQueries"] = []
        recipe["vtableQueries"] = []
        source = json.dumps(recipe, sort_keys=True).encode("utf-8")
        document = {
            "schema": "blueprint-to-code-native-analysis-recipe-document/v1",
            "sha256": hashlib.sha256(source).hexdigest(),
            "recipe": recipe,
        }
        raw = _raw_export(document["sha256"])
        raw["targetResults"][0]["matchCount"] = 2

        self.assert_error_code(
            "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
            lambda: create_native_recipe_evidence_manifest(
                raw,
                recipe_document=document,
                identity=_identity(),
                ghidra={
                    "version": "12.1.2",
                    "releaseAssetSha256": "d" * 64,
                    "analysisOptionsSha256": "e" * 64,
                },
                java={"vendor": "Eclipse Adoptium", "version": "21"},
                generator={
                    "repositoryCommit": "f" * 40,
                    "repositoryDirty": False,
                    "recipeId": recipe["recipeId"],
                    "recipeSha256": document["sha256"],
                    "scriptSha256": {
                        "runner": "1" * 64,
                        "exporter": "2" * 64,
                        "pdbConfigurator": "3" * 64,
                    },
                },
                formal=True,
            ),
        )

        mismatched_candidate = _raw_export(document["sha256"])
        mismatched_candidate["targetResults"][0]["candidates"][0][
            "evidenceId"
        ] = f"native://{SHA_A}/fixture.dll/0x2000"
        self.assert_error_code(
            "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
            lambda: _wrap(mismatched_candidate, document),
        )

    def test_query_results_reject_duplicate_extra_and_contract_drift(self):
        recipe = _recipe()
        recipe["targets"] = [recipe["targets"][0]]
        document = _document(recipe)
        raw = _raw_export(document["sha256"])
        evidence_id = raw["functions"][0]["evidenceId"]
        raw["fieldQueryResults"] = [
            {
                "queryId": "quality-scale-field",
                "structureName": "Fixture::QualityInputs",
                "fieldName": "multiplier",
                "functionTargetIds": [],
                "expectedMatches": 1,
                "matchCount": 1,
                "resolvedEvidenceIds": [evidence_id],
                "candidates": [
                    {
                        "evidenceId": evidence_id,
                        "accepted": True,
                        "rejectionReason": "",
                    }
                ],
            }
        ]
        raw["vtableQueryResults"] = [
            {
                "queryId": "quality-adjust-slot",
                "className": "Fixture::QualityModel",
                "slotOffset": "0x8",
                "expectedMatches": 1,
                "matchCount": 1,
                "resolvedEvidenceIds": [evidence_id],
                "candidates": [
                    {
                        "evidenceId": evidence_id,
                        "accepted": True,
                        "rejectionReason": "",
                    }
                ],
            }
        ]
        _wrap(raw, document)

        duplicate = copy.deepcopy(raw)
        duplicate["fieldQueryResults"].append(
            copy.deepcopy(duplicate["fieldQueryResults"][0])
        )
        self.assert_error_code(
            "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
            lambda: _wrap(duplicate, document),
        )

        unexpected = copy.deepcopy(raw)
        extra = copy.deepcopy(unexpected["vtableQueryResults"][0])
        extra["queryId"] = "unexpected-vtable"
        unexpected["vtableQueryResults"].append(extra)
        self.assert_error_code(
            "NATIVE_RECIPE_TARGET_COUNT_MISMATCH",
            lambda: _wrap(unexpected, document),
        )

        drifted = copy.deepcopy(raw)
        drifted["fieldQueryResults"][0]["fieldName"] = "wrong_field"
        self.assert_error_code(
            "NATIVE_EVIDENCE_PROVENANCE_MISMATCH",
            lambda: _wrap(drifted, document),
        )

    def test_recipe_export_wraps_v2_targets_and_candidate_diagnostics(self):
        recipe = _recipe()
        recipe["targets"] = [recipe["targets"][0]]
        recipe["fieldQueries"] = []
        recipe["vtableQueries"] = []
        source = json.dumps(recipe, sort_keys=True).encode("utf-8")
        document = {
            "schema": "blueprint-to-code-native-analysis-recipe-document/v1",
            "sha256": hashlib.sha256(source).hexdigest(),
            "recipe": recipe,
        }
        manifest = create_native_recipe_evidence_manifest(
            _raw_export(document["sha256"]),
            recipe_document=document,
            identity=_identity(),
            ghidra={
                "version": "12.1.2",
                "releaseAssetSha256": "d" * 64,
                "analysisOptionsSha256": "e" * 64,
            },
            java={"vendor": "Eclipse Adoptium", "version": "21"},
            generator={
                "repositoryCommit": "f" * 40,
                "repositoryDirty": False,
                "recipeId": recipe["recipeId"],
                "recipeSha256": document["sha256"],
                "scriptSha256": {
                    "runner": "1" * 64,
                    "exporter": "2" * 64,
                    "pdbConfigurator": "3" * 64,
                },
            },
            formal=True,
        )

        self.assertEqual(
            manifest["schema"],
            "blueprint-to-code-native-evidence-set/v2",
        )
        self.assertEqual(len(manifest["targets"]), 1)
        self.assertEqual(len(manifest["recipeTargets"]), 1)
        self.assertEqual(
            manifest["recipeTargets"][0]["resolvedEvidenceIds"],
            [manifest["targets"][0]["evidenceId"]],
        )
        self.assertEqual(
            manifest["recipeTargets"][0]["candidates"][0]["accepted"],
            True,
        )


if __name__ == "__main__":
    unittest.main()
