import copy
import hashlib
import struct
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.native_identity import (  # noqa: E402
    NativeIdentityError,
    build_native_identity,
    create_native_evidence_manifest,
    create_native_project_manifest,
    project_name_for_hash,
    validate_native_evidence_manifest,
    validate_native_project_manifest,
)


MSF7_MAGIC = b"Microsoft C/C++ MSF 7.00\r\n\x1aDS\x00\x00\x00"


def _write_synthetic_pe(path: Path, guid: uuid.UUID, age: int) -> None:
    pe_offset = 0x80
    optional_size = 0xF0
    section_raw_offset = 0x200
    section_rva = 0x1000
    debug_raw_offset = 0x220
    debug_rva = section_rva + (debug_raw_offset - section_raw_offset)
    codeview_raw_offset = 0x240
    codeview_rva = section_rva + (codeview_raw_offset - section_raw_offset)
    codeview = b"RSDS" + guid.bytes_le + struct.pack("<I", age) + b"fixture.pdb\x00"

    payload = bytearray(0x600)
    payload[0:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, pe_offset)
    payload[pe_offset : pe_offset + 4] = b"PE\x00\x00"

    coff_offset = pe_offset + 4
    struct.pack_into(
        "<HHIIIHH",
        payload,
        coff_offset,
        0x8664,
        1,
        0x65A1B2C3,
        0,
        0,
        optional_size,
        0x2022,
    )

    optional_offset = coff_offset + 20
    struct.pack_into("<H", payload, optional_offset, 0x20B)
    struct.pack_into("<Q", payload, optional_offset + 24, 0x180000000)
    struct.pack_into("<I", payload, optional_offset + 108, 16)
    struct.pack_into(
        "<II",
        payload,
        optional_offset + 112 + (6 * 8),
        debug_rva,
        28,
    )

    section_offset = optional_offset + optional_size
    struct.pack_into(
        "<8sIIIIIIHHI",
        payload,
        section_offset,
        b".rdata\x00\x00",
        0x400,
        section_rva,
        0x400,
        section_raw_offset,
        0,
        0,
        0,
        0,
        0x40000040,
    )
    struct.pack_into(
        "<IIHHIIII",
        payload,
        debug_raw_offset,
        0,
        0x65A1B2C3,
        0,
        0,
        2,
        len(codeview),
        codeview_rva,
        codeview_raw_offset,
    )
    payload[codeview_raw_offset : codeview_raw_offset + len(codeview)] = codeview
    path.write_bytes(payload)


def _write_synthetic_pdb(path: Path, guid: uuid.UUID, age: int) -> None:
    block_size = 512
    num_blocks = 6
    pdb_stream = (
        struct.pack("<III", 20000404, 0x12345678, age)
        + guid.bytes_le
    )
    directory = (
        struct.pack("<I", 2)
        + struct.pack("<II", 0, len(pdb_stream))
        + struct.pack("<I", 5)
    )

    payload = bytearray(block_size * num_blocks)
    payload[: len(MSF7_MAGIC)] = MSF7_MAGIC
    struct.pack_into(
        "<IIIIII",
        payload,
        len(MSF7_MAGIC),
        block_size,
        1,
        num_blocks,
        len(directory),
        0,
        3,
    )
    struct.pack_into("<I", payload, block_size * 3, 4)
    payload[block_size * 4 : (block_size * 4) + len(directory)] = directory
    payload[block_size * 5 : (block_size * 5) + len(pdb_stream)] = pdb_stream
    path.write_bytes(payload)


def _formal_manifest(identity: dict) -> dict:
    binary = identity["binary"]
    pdb = identity["pdb"]
    recipe_sha = hashlib.sha256(b"fixture-recipe").hexdigest()
    return {
        "schema": "blueprint-to-code-native-evidence-set/v2",
        "evidenceSetId": f"native-set://{binary['sha256']}/{recipe_sha}",
        "generatedAtUtc": "2026-07-27T00:00:00Z",
        "provenance": {
            "binary": {
                "module": binary["module"],
                "sha256": binary["sha256"],
                "size": binary["size"],
                "machine": binary["machine"],
                "peTimestamp": binary["peTimestamp"],
                "imageBase": binary["imageBase"],
                "codeView": copy.deepcopy(binary["codeView"]),
            },
            "pdb": {
                "fileName": pdb["fileName"],
                "sha256": pdb["sha256"],
                "guid": pdb["guid"],
                "age": pdb["age"],
                "loaded": True,
                "matchesBinary": True,
            },
            "ghidra": {
                "version": "12.1.2",
                "releaseAssetSha256": "1" * 64,
                "languageId": "x86:LE:64:default",
                "compilerSpecId": "windows",
                "analysisOptionsSha256": "2" * 64,
            },
            "java": {
                "vendor": "Eclipse Adoptium",
                "version": "21.0.11+10-LTS",
            },
            "generator": {
                "repositoryCommit": "a" * 40,
                "repositoryDirty": False,
                "recipeId": "test-native-fixture/v1",
                "recipeSha256": recipe_sha,
                "scriptSha256": {
                    "runner": "3" * 64,
                    "exporter": "4" * 64,
                    "pdbConfigurator": "5" * 64,
                },
            },
        },
        "trust": {"status": "VERIFIED"},
        "targets": [],
        "gaps": [],
    }


class NativeIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.guid = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
        self.age = 7
        self.dll_path = self.root / "fixture.dll"
        self.pdb_path = self.root / "fixture.pdb"
        _write_synthetic_pe(self.dll_path, self.guid, self.age)
        _write_synthetic_pdb(self.pdb_path, self.guid, self.age)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def assert_error_code(self, expected: str, callback) -> None:
        with self.assertRaises(NativeIdentityError) as raised:
            callback()
        self.assertEqual(raised.exception.code, expected)

    def test_project_name_is_stable_and_uses_first_twelve_lowercase_hex(self):
        digest = "ABCDEF0123456789" * 4

        first = project_name_for_hash(digest)
        second = project_name_for_hash(digest.lower())

        self.assertEqual(first, "ShooterGameNative_abcdef012345")
        self.assertEqual(second, first)

    def test_matching_pe_codeview_and_pdb_stream_one_are_verified(self):
        identity = build_native_identity(self.dll_path, self.pdb_path)

        self.assertEqual(identity["binary"]["codeView"]["format"], "RSDS")
        self.assertEqual(identity["binary"]["codeView"]["guid"], str(self.guid))
        self.assertEqual(identity["binary"]["codeView"]["age"], self.age)
        self.assertEqual(identity["pdb"]["guid"], str(self.guid))
        self.assertEqual(identity["pdb"]["age"], self.age)
        self.assertTrue(identity["pdb"]["matchesBinary"])
        self.assertEqual(
            identity["project"]["name"],
            project_name_for_hash(identity["binary"]["sha256"]),
        )
        self.assertNotIn(str(self.root), str(identity))

    def test_guid_or_age_mismatch_is_rejected(self):
        mismatches = (
            (uuid.UUID("10112233-4455-6677-8899-aabbccddeeff"), self.age),
            (self.guid, self.age + 1),
        )
        for guid, age in mismatches:
            with self.subTest(guid=guid, age=age):
                _write_synthetic_pdb(self.pdb_path, guid, age)
                self.assert_error_code(
                    "NATIVE_PDB_IDENTITY_MISMATCH",
                    lambda: build_native_identity(self.dll_path, self.pdb_path),
                )

    def test_bad_pe_and_pdb_formats_raise_structured_errors(self):
        self.dll_path.write_bytes(b"not a PE")
        self.assert_error_code(
            "NATIVE_BINARY_FORMAT_INVALID",
            lambda: build_native_identity(self.dll_path, self.pdb_path),
        )

        _write_synthetic_pe(self.dll_path, self.guid, self.age)
        self.pdb_path.write_bytes(b"not an MSF7 PDB")
        self.assert_error_code(
            "NATIVE_PDB_FORMAT_INVALID",
            lambda: build_native_identity(self.dll_path, self.pdb_path),
        )

    def test_manifest_binary_hash_mismatch_is_rejected(self):
        identity = build_native_identity(self.dll_path, self.pdb_path)
        manifest = _formal_manifest(identity)
        manifest["provenance"]["binary"]["sha256"] = "f" * 64

        self.assert_error_code(
            "NATIVE_EVIDENCE_PROVENANCE_MISMATCH",
            lambda: validate_native_evidence_manifest(
                manifest, expected_identity=identity, formal=True
            ),
        )

    def test_manifest_pdb_hash_mismatch_is_rejected(self):
        identity = build_native_identity(self.dll_path, self.pdb_path)
        manifest = _formal_manifest(identity)
        manifest["provenance"]["pdb"]["sha256"] = "e" * 64

        self.assert_error_code(
            "NATIVE_PDB_HASH_MISMATCH",
            lambda: validate_native_evidence_manifest(
                manifest, expected_identity=identity, formal=True
            ),
        )

    def test_formal_manifest_rejects_dirty_generator(self):
        identity = build_native_identity(self.dll_path, self.pdb_path)
        manifest = _formal_manifest(identity)
        manifest["provenance"]["generator"]["repositoryDirty"] = True
        manifest["trust"]["status"] = "DIRTY_GENERATOR"

        self.assert_error_code(
            "NATIVE_EVIDENCE_PROVENANCE_MISMATCH",
            lambda: validate_native_evidence_manifest(
                manifest, expected_identity=identity, formal=True
            ),
        )

    def test_formal_manifest_rejects_unloaded_pdb(self):
        identity = build_native_identity(self.dll_path, self.pdb_path)
        manifest = _formal_manifest(identity)
        manifest["provenance"]["pdb"]["loaded"] = False

        self.assert_error_code(
            "NATIVE_PDB_NOT_LOADED",
            lambda: validate_native_evidence_manifest(
                manifest, expected_identity=identity, formal=True
            ),
        )

    def test_formal_manifest_rejects_absolute_path_leaks(self):
        identity = build_native_identity(self.dll_path, self.pdb_path)
        manifest = _formal_manifest(identity)
        manifest["provenance"]["generator"]["debugPath"] = (
            r"C:\Users\fixture\private\workspace"
        )

        self.assert_error_code(
            "NATIVE_EVIDENCE_PROVENANCE_MISMATCH",
            lambda: validate_native_evidence_manifest(
                manifest, expected_identity=identity, formal=True
            ),
        )

    def test_formal_manifest_enforces_exact_schema_and_recipe_binding(self):
        identity = build_native_identity(self.dll_path, self.pdb_path)
        cases = []

        wrong_recipe = _formal_manifest(identity)
        wrong_recipe["evidenceSetId"] = (
            f"native-set://{identity['binary']['sha256']}/{'f' * 64}"
        )
        cases.append(
            (
                "recipe binding",
                wrong_recipe,
                "NATIVE_EVIDENCE_PROVENANCE_MISMATCH",
            )
        )

        missing_timestamp = _formal_manifest(identity)
        missing_timestamp.pop("generatedAtUtc")
        cases.append(
            (
                "generated timestamp",
                missing_timestamp,
                "NATIVE_EXPORT_SCHEMA_INVALID",
            )
        )

        wrong_module = _formal_manifest(identity)
        wrong_module["provenance"]["binary"]["module"] = "other.dll"
        cases.append(
            (
                "binary module",
                wrong_module,
                "NATIVE_EVIDENCE_PROVENANCE_MISMATCH",
            )
        )

        extra_root_field = _formal_manifest(identity)
        extra_root_field["localDebugPath"] = "debug-only"
        cases.append(
            (
                "unexpected root field",
                extra_root_field,
                "NATIVE_EXPORT_SCHEMA_INVALID",
            )
        )

        for label, manifest, expected_code in cases:
            with self.subTest(label=label):
                self.assert_error_code(
                    expected_code,
                    lambda manifest=manifest: validate_native_evidence_manifest(
                        manifest,
                        expected_identity=identity,
                        formal=True,
                    ),
                )

    def test_project_manifest_rejects_cross_hash_project_reuse(self):
        identity = build_native_identity(self.dll_path, self.pdb_path)
        manifest = create_native_project_manifest(identity)
        validate_native_project_manifest(manifest, expected_identity=identity)
        manifest["binary"]["sha256"] = "d" * 64

        self.assert_error_code(
            "NATIVE_PROJECT_PROGRAM_HASH_MISMATCH",
            lambda: validate_native_project_manifest(
                manifest, expected_identity=identity
            ),
        )

    def test_legacy_export_is_wrapped_only_when_program_identity_matches(self):
        identity = build_native_identity(self.dll_path, self.pdb_path)
        raw_export = {
            "schema": "blueprint-to-code-native-targets/v1",
            "program": identity["binary"]["module"],
            "executablePath": r"C:\private\fixture.dll",
            "binarySha256": identity["binary"]["sha256"],
            "languageId": "x86:LE:64:default",
            "compilerSpecId": "windows",
            "pdbLoaded": True,
            "pdbGuid": identity["pdb"]["guid"],
            "pdbAge": format(identity["pdb"]["age"], "x"),
            "patterns": ["ComputeQuality"],
            "functions": [],
        }
        manifest = create_native_evidence_manifest(
            raw_export,
            identity=identity,
            ghidra={
                "version": "12.1.2",
                "releaseAssetSha256": "1" * 64,
                "analysisOptionsSha256": "2" * 64,
            },
            java={
                "vendor": "Eclipse Adoptium",
                "version": "21.0.11+10-LTS",
            },
            generator={
                "repositoryCommit": "a" * 40,
                "repositoryDirty": False,
                "recipeId": "legacy-hardcoded-native-targets/v1",
                "recipeSha256": "b" * 64,
                "scriptSha256": {
                    "runner": "3" * 64,
                    "exporter": "4" * 64,
                    "pdbConfigurator": "5" * 64,
                },
            },
            formal=True,
        )

        validate_native_evidence_manifest(
            manifest, expected_identity=identity, formal=True
        )
        self.assertNotIn("executablePath", str(manifest))

        raw_export["binarySha256"] = "c" * 64
        self.assert_error_code(
            "NATIVE_PROJECT_PROGRAM_HASH_MISMATCH",
            lambda: create_native_evidence_manifest(
                raw_export,
                identity=identity,
                ghidra=manifest["provenance"]["ghidra"],
                java=manifest["provenance"]["java"],
                generator=manifest["provenance"]["generator"],
                formal=True,
            ),
        )

        raw_export["binarySha256"] = identity["binary"]["sha256"]
        raw_export["pdbGuid"] = "10112233-4455-6677-8899-aabbccddeeff"
        self.assert_error_code(
            "NATIVE_PDB_IDENTITY_MISMATCH",
            lambda: create_native_evidence_manifest(
                raw_export,
                identity=identity,
                ghidra=manifest["provenance"]["ghidra"],
                java=manifest["provenance"]["java"],
                generator=manifest["provenance"]["generator"],
                formal=True,
            ),
        )


if __name__ == "__main__":
    unittest.main()
