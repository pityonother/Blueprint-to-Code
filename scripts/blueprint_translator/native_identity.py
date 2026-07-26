"""Fail-closed native binary, PDB, project, and evidence provenance helpers.

The parsers intentionally cover only the identity-bearing parts of the public
PE/COFF and MSF 7.00 formats.  They do not inspect symbols or proprietary
program contents.
"""

from __future__ import annotations

import hashlib
import math
import mmap
import re
import struct
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping


MSF7_MAGIC = b"Microsoft C/C++ MSF 7.00\r\n\x1aDS\x00\x00\x00"
VALID_MSF_BLOCK_SIZES = frozenset({512, 1024, 2048, 4096})
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
PROJECT_PREFIX_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

MACHINE_NAMES = {
    0x014C: "x86",
    0x8664: "x86_64",
    0xAA64: "arm64",
    0xA641: "arm64ec",
}


class NativeIdentityError(ValueError):
    """A native-analysis contract error with a stable machine-readable code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def to_diagnostic(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            },
        }


def _fail(
    code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> None:
    raise NativeIdentityError(code, message, details=details)


def _require_range(
    data: bytes,
    offset: int,
    size: int,
    *,
    code: str,
    label: str,
) -> None:
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        _fail(code, f"{label} is outside the file bounds.")


def _u16(data: bytes, offset: int, *, code: str, label: str) -> int:
    _require_range(data, offset, 2, code=code, label=label)
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int, *, code: str, label: str) -> int:
    _require_range(data, offset, 4, code=code, label=label)
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data: bytes, offset: int, *, code: str, label: str) -> int:
    _require_range(data, offset, 8, code=code, label=label)
    return struct.unpack_from("<Q", data, offset)[0]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _basename(value: str) -> str:
    return re.split(r"[\\/]", value)[-1]


def project_name_for_hash(
    binary_sha256: str,
    *,
    prefix: str = "ShooterGameNative",
    hash_length: int = 12,
) -> str:
    """Return the deterministic Ghidra project name for one binary hash."""

    normalized = str(binary_sha256).strip().lower()
    if not SHA256_PATTERN.fullmatch(normalized):
        _fail(
            "NATIVE_BINARY_HASH_UNREGISTERED",
            "Binary SHA-256 must be exactly 64 hexadecimal characters.",
        )
    if not PROJECT_PREFIX_PATTERN.fullmatch(prefix):
        _fail(
            "NATIVE_PROJECT_PROGRAM_HASH_MISMATCH",
            "Native project prefix contains unsupported characters.",
        )
    if hash_length < 8 or hash_length > 64:
        _fail(
            "NATIVE_PROJECT_PROGRAM_HASH_MISMATCH",
            "Native project hash length must be between 8 and 64.",
        )
    return f"{prefix}_{normalized[:hash_length]}"


def parse_pe_codeview(data: bytes) -> dict[str, Any]:
    """Parse the first RSDS CodeView identity from a PE image."""

    code = "NATIVE_BINARY_FORMAT_INVALID"
    if len(data) < 0x40 or data[:2] != b"MZ":
        _fail(code, "Input is not a DOS/PE image.")

    pe_offset = _u32(data, 0x3C, code=code, label="PE header offset")
    _require_range(data, pe_offset, 24, code=code, label="PE and COFF headers")
    if data[pe_offset : pe_offset + 4] != b"PE\x00\x00":
        _fail(code, "PE signature is missing.")

    coff_offset = pe_offset + 4
    machine = _u16(data, coff_offset, code=code, label="COFF machine")
    section_count = _u16(
        data, coff_offset + 2, code=code, label="COFF section count"
    )
    timestamp = _u32(
        data, coff_offset + 4, code=code, label="COFF timestamp"
    )
    optional_size = _u16(
        data,
        coff_offset + 16,
        code=code,
        label="COFF optional-header size",
    )
    if section_count < 1 or section_count > 96:
        _fail(code, "PE section count is invalid.")

    optional_offset = coff_offset + 20
    _require_range(
        data,
        optional_offset,
        optional_size,
        code=code,
        label="PE optional header",
    )
    magic = _u16(data, optional_offset, code=code, label="optional-header magic")
    if magic == 0x20B:
        data_directory_count_offset = 108
        data_directory_offset = 112
        image_base = _u64(
            data,
            optional_offset + 24,
            code=code,
            label="PE32+ image base",
        )
    elif magic == 0x10B:
        data_directory_count_offset = 92
        data_directory_offset = 96
        image_base = _u32(
            data,
            optional_offset + 28,
            code=code,
            label="PE32 image base",
        )
    else:
        _fail(code, f"Unsupported PE optional-header magic 0x{magic:04X}.")

    if optional_size < data_directory_offset + (7 * 8):
        _fail(code, "PE optional header does not contain the debug directory.")
    directory_count = _u32(
        data,
        optional_offset + data_directory_count_offset,
        code=code,
        label="data-directory count",
    )
    if directory_count <= 6:
        _fail(code, "PE image has no debug data-directory entry.")

    debug_directory_entry = optional_offset + data_directory_offset + (6 * 8)
    debug_rva = _u32(
        data,
        debug_directory_entry,
        code=code,
        label="debug-directory RVA",
    )
    debug_size = _u32(
        data,
        debug_directory_entry + 4,
        code=code,
        label="debug-directory size",
    )
    if debug_rva == 0 or debug_size < 28:
        _fail(code, "PE image has no usable debug directory.")

    section_offset = optional_offset + optional_size
    _require_range(
        data,
        section_offset,
        section_count * 40,
        code=code,
        label="PE section table",
    )
    sections: list[tuple[int, int, int, int]] = []
    for index in range(section_count):
        offset = section_offset + (index * 40)
        virtual_size = _u32(
            data, offset + 8, code=code, label="section virtual size"
        )
        virtual_address = _u32(
            data, offset + 12, code=code, label="section virtual address"
        )
        raw_size = _u32(data, offset + 16, code=code, label="section raw size")
        raw_offset = _u32(
            data, offset + 20, code=code, label="section raw offset"
        )
        sections.append((virtual_address, virtual_size, raw_offset, raw_size))

    def rva_to_file_offset(rva: int, label: str) -> int:
        for virtual_address, virtual_size, raw_offset, raw_size in sections:
            span = max(virtual_size, raw_size)
            if virtual_address <= rva < virtual_address + span:
                relative = rva - virtual_address
                if relative >= raw_size:
                    _fail(code, f"{label} points into an uninitialized section range.")
                file_offset = raw_offset + relative
                _require_range(
                    data, file_offset, 1, code=code, label=f"{label} file offset"
                )
                return file_offset
        _fail(code, f"{label} RVA does not map to a PE section.")
        raise AssertionError("unreachable")

    debug_offset = rva_to_file_offset(debug_rva, "debug directory")
    _require_range(
        data,
        debug_offset,
        debug_size,
        code=code,
        label="debug-directory records",
    )

    saw_codeview = False
    for offset in range(debug_offset, debug_offset + debug_size - 27, 28):
        (
            _characteristics,
            _entry_timestamp,
            _major_version,
            _minor_version,
            debug_type,
            size_of_data,
            address_of_raw_data,
            pointer_to_raw_data,
        ) = struct.unpack_from("<IIHHIIII", data, offset)
        if debug_type != 2:
            continue
        saw_codeview = True
        if size_of_data < 24:
            continue
        if pointer_to_raw_data:
            codeview_offset = pointer_to_raw_data
        elif address_of_raw_data:
            codeview_offset = rva_to_file_offset(
                address_of_raw_data, "CodeView record"
            )
        else:
            continue
        _require_range(
            data,
            codeview_offset,
            size_of_data,
            code=code,
            label="CodeView record",
        )
        record = data[codeview_offset : codeview_offset + size_of_data]
        if record[:4] != b"RSDS":
            continue
        guid = str(uuid.UUID(bytes_le=record[4:20]))
        age = struct.unpack_from("<I", record, 20)[0]
        raw_name = record[24:].split(b"\x00", 1)[0]
        try:
            pdb_name = raw_name.decode("utf-8")
        except UnicodeDecodeError:
            pdb_name = raw_name.decode("mbcs", errors="replace")
        return {
            "format": "RSDS",
            "guid": guid,
            "age": age,
            "pdbFileName": _basename(pdb_name),
            "machine": MACHINE_NAMES.get(machine, f"0x{machine:04x}"),
            "machineCode": f"0x{machine:04X}",
            "peTimestamp": timestamp,
            "imageBase": f"0x{image_base:X}",
        }

    if saw_codeview:
        _fail(code, "PE CodeView debug data does not contain an RSDS record.")
    _fail(code, "PE debug directory does not contain a CodeView record.")
    raise AssertionError("unreachable")


def _read_msf_blocks(
    data: bytes,
    *,
    block_size: int,
    num_blocks: int,
    blocks: list[int],
    byte_count: int,
    code: str,
    label: str,
) -> bytes:
    chunks: list[bytes] = []
    for block in blocks:
        if block < 0 or block >= num_blocks:
            _fail(code, f"{label} references an invalid MSF block.")
        offset = block * block_size
        _require_range(data, offset, block_size, code=code, label=label)
        chunks.append(data[offset : offset + block_size])
    return b"".join(chunks)[:byte_count]


def parse_pdb_stream_identity(data: bytes) -> dict[str, Any]:
    """Parse GUID/Age from stream 1 of a Microsoft MSF 7.00 PDB."""

    code = "NATIVE_PDB_FORMAT_INVALID"
    if len(data) < len(MSF7_MAGIC) + 24 or data[: len(MSF7_MAGIC)] != MSF7_MAGIC:
        _fail(code, "Input is not an MSF 7.00 PDB.")

    (
        block_size,
        free_block_map_block,
        num_blocks,
        directory_byte_count,
        _unknown,
        block_map_address,
    ) = struct.unpack_from("<IIIIII", data, len(MSF7_MAGIC))
    if block_size not in VALID_MSF_BLOCK_SIZES:
        _fail(code, f"Unsupported MSF block size {block_size}.")
    if free_block_map_block not in (1, 2):
        _fail(code, "MSF free-block-map block must be 1 or 2.")
    if num_blocks < 1 or num_blocks * block_size != len(data):
        _fail(code, "MSF block count does not match the file size.")
    if directory_byte_count < 4:
        _fail(code, "MSF stream directory is missing.")

    directory_block_count = math.ceil(directory_byte_count / block_size)
    block_map_bytes = directory_block_count * 4
    if block_map_bytes > block_size:
        _fail(code, "MSF directory block map exceeds the supported single block.")
    if block_map_address >= num_blocks:
        _fail(code, "MSF block-map address is invalid.")
    block_map_offset = block_map_address * block_size
    _require_range(
        data,
        block_map_offset,
        block_map_bytes,
        code=code,
        label="MSF directory block map",
    )
    directory_blocks = list(
        struct.unpack_from(
            f"<{directory_block_count}I", data, block_map_offset
        )
    )
    directory = _read_msf_blocks(
        data,
        block_size=block_size,
        num_blocks=num_blocks,
        blocks=directory_blocks,
        byte_count=directory_byte_count,
        code=code,
        label="MSF stream directory",
    )

    stream_count = _u32(
        directory, 0, code=code, label="MSF stream-directory count"
    )
    if stream_count < 2 or stream_count > 1_000_000:
        _fail(code, "PDB stream 1 is unavailable.")
    sizes_offset = 4
    _require_range(
        directory,
        sizes_offset,
        stream_count * 4,
        code=code,
        label="MSF stream sizes",
    )
    stream_sizes = list(
        struct.unpack_from(f"<{stream_count}I", directory, sizes_offset)
    )
    block_lists_offset = sizes_offset + (stream_count * 4)
    stream_blocks: list[list[int]] = []
    cursor = block_lists_offset
    for stream_size in stream_sizes:
        if stream_size == 0xFFFFFFFF:
            stream_blocks.append([])
            continue
        block_count = math.ceil(stream_size / block_size) if stream_size else 0
        _require_range(
            directory,
            cursor,
            block_count * 4,
            code=code,
            label="MSF stream block list",
        )
        blocks = (
            list(struct.unpack_from(f"<{block_count}I", directory, cursor))
            if block_count
            else []
        )
        cursor += block_count * 4
        stream_blocks.append(blocks)

    pdb_stream_size = stream_sizes[1]
    if pdb_stream_size == 0xFFFFFFFF or pdb_stream_size < 28:
        _fail(code, "PDB stream 1 is missing its identity header.")
    pdb_stream = _read_msf_blocks(
        data,
        block_size=block_size,
        num_blocks=num_blocks,
        blocks=stream_blocks[1],
        byte_count=pdb_stream_size,
        code=code,
        label="PDB stream 1",
    )
    _require_range(
        pdb_stream, 0, 28, code=code, label="PDB stream 1 identity header"
    )
    version, signature, age = struct.unpack_from("<III", pdb_stream, 0)
    guid = str(uuid.UUID(bytes_le=pdb_stream[12:28]))
    return {
        "version": version,
        "signature": signature,
        "guid": guid,
        "age": age,
    }


def build_native_identity(
    binary_path: str | Path,
    pdb_path: str | Path,
    *,
    project_prefix: str = "ShooterGameNative",
    project_hash_length: int = 12,
) -> dict[str, Any]:
    """Hash and strongly bind one PE image to one MSF 7.00 PDB."""

    binary = Path(binary_path)
    pdb = Path(pdb_path)
    if not binary.is_file():
        _fail("NATIVE_TOOL_MISSING", "Native binary file does not exist.")
    if not pdb.is_file():
        _fail("NATIVE_TOOL_MISSING", "Native PDB file does not exist.")

    binary_size = binary.stat().st_size
    pdb_size = pdb.stat().st_size
    if binary_size == 0:
        _fail("NATIVE_BINARY_FORMAT_INVALID", "Native binary file is empty.")
    if pdb_size == 0:
        _fail("NATIVE_PDB_FORMAT_INVALID", "Native PDB file is empty.")
    with binary.open("rb") as binary_file:
        with mmap.mmap(binary_file.fileno(), 0, access=mmap.ACCESS_READ) as view:
            codeview = parse_pe_codeview(view)
    with pdb.open("rb") as pdb_file:
        with mmap.mmap(pdb_file.fileno(), 0, access=mmap.ACCESS_READ) as view:
            pdb_identity = parse_pdb_stream_identity(view)
    matches = (
        codeview["guid"].lower() == pdb_identity["guid"].lower()
        and codeview["age"] == pdb_identity["age"]
    )
    if not matches:
        _fail(
            "NATIVE_PDB_IDENTITY_MISMATCH",
            "PDB GUID/Age does not match the PE CodeView RSDS identity.",
            details={
                "peGuid": codeview["guid"],
                "peAge": codeview["age"],
                "pdbGuid": pdb_identity["guid"],
                "pdbAge": pdb_identity["age"],
            },
        )

    binary_sha = _sha256_file(binary)
    pdb_sha = _sha256_file(pdb)
    project_name = project_name_for_hash(
        binary_sha,
        prefix=project_prefix,
        hash_length=project_hash_length,
    )
    hash_prefix = binary_sha[:project_hash_length]
    return {
        "schema": "blueprint-to-code-native-build-identity/v1",
        "binary": {
            "module": binary.name,
            "sha256": binary_sha,
            "size": binary_size,
            "machine": codeview["machine"],
            "machineCode": codeview["machineCode"],
            "peTimestamp": codeview["peTimestamp"],
            "imageBase": codeview["imageBase"],
            "codeView": {
                "format": codeview["format"],
                "guid": codeview["guid"],
                "age": codeview["age"],
                "pdbFileName": codeview["pdbFileName"],
            },
        },
        "pdb": {
            "fileName": pdb.name,
            "sha256": pdb_sha,
            "size": pdb_size,
            "version": pdb_identity["version"],
            "signature": f"0x{pdb_identity['signature']:08X}",
            "guid": pdb_identity["guid"],
            "age": pdb_identity["age"],
            "matchesBinary": True,
        },
        "project": {
            "name": project_name,
            "hashPrefix": hash_prefix,
            "workspaceSlug": hash_prefix,
        },
    }


def create_native_project_manifest(
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Create the path-free manifest that authorizes reuse of one project."""

    source = _get_mapping(identity, "native build identity")
    binary = _get_mapping(source.get("binary"), "native build identity binary")
    pdb = _get_mapping(source.get("pdb"), "native build identity PDB")
    project = _get_mapping(
        source.get("project"), "native build identity project"
    )
    return {
        "schema": "blueprint-to-code-native-project/v1",
        "project": {
            "name": project.get("name"),
            "hashPrefix": project.get("hashPrefix"),
            "workspaceSlug": project.get("workspaceSlug"),
        },
        "binary": {
            "module": binary.get("module"),
            "sha256": binary.get("sha256"),
            "size": binary.get("size"),
            "codeView": deepcopy(binary.get("codeView")),
        },
        "pdb": {
            "fileName": pdb.get("fileName"),
            "sha256": pdb.get("sha256"),
            "guid": pdb.get("guid"),
            "age": pdb.get("age"),
            "matchesBinary": pdb.get("matchesBinary"),
        },
    }


def validate_native_project_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_identity: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Refuse to process a project created for another binary or PDB."""

    root = _get_mapping(manifest, "native project manifest")
    if root.get("schema") != "blueprint-to-code-native-project/v1":
        _fail(
            "NATIVE_EXPORT_SCHEMA_INVALID",
            "Native project manifest schema must be v1.",
        )
    if _contains_absolute_path(root):
        _fail(
            "NATIVE_EVIDENCE_PROVENANCE_MISMATCH",
            "Native project manifest contains a local absolute path.",
        )

    expected = _get_mapping(expected_identity, "expected native identity")
    expected_binary = _get_mapping(
        expected.get("binary"), "expected native binary"
    )
    expected_pdb = _get_mapping(expected.get("pdb"), "expected native PDB")
    expected_project = _get_mapping(
        expected.get("project"), "expected native project"
    )
    binary = _get_mapping(root.get("binary"), "project manifest binary")
    pdb = _get_mapping(root.get("pdb"), "project manifest PDB")
    project = _get_mapping(root.get("project"), "project manifest project")

    if (
        str(binary.get("sha256", "")).lower()
        != str(expected_binary.get("sha256", "")).lower()
        or binary.get("module") != expected_binary.get("module")
        or project.get("name") != expected_project.get("name")
        or project.get("hashPrefix") != expected_project.get("hashPrefix")
        or project.get("workspaceSlug") != expected_project.get("workspaceSlug")
    ):
        _fail(
            "NATIVE_PROJECT_PROGRAM_HASH_MISMATCH",
            "Ghidra project manifest is bound to a different binary.",
        )
    if (
        str(pdb.get("sha256", "")).lower()
        != str(expected_pdb.get("sha256", "")).lower()
    ):
        _fail(
            "NATIVE_PDB_HASH_MISMATCH",
            "Ghidra project manifest is bound to a different PDB file.",
        )
    if (
        str(pdb.get("guid", "")).lower()
        != str(expected_pdb.get("guid", "")).lower()
        or pdb.get("age") != expected_pdb.get("age")
        or pdb.get("matchesBinary") is not True
    ):
        _fail(
            "NATIVE_PDB_IDENTITY_MISMATCH",
            "Ghidra project manifest PDB identity does not match the binary.",
        )
    return manifest


def _get_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(
            "NATIVE_EXPORT_SCHEMA_INVALID",
            f"{label} must be a JSON object.",
        )
    return value


def _require_only_keys(
    value: Mapping[str, Any],
    allowed: set[str],
    label: str,
) -> None:
    unexpected = sorted(str(key) for key in value.keys() if key not in allowed)
    if unexpected:
        _fail(
            "NATIVE_EXPORT_SCHEMA_INVALID",
            f"{label} contains unsupported fields: {', '.join(unexpected)}.",
        )


def _contains_absolute_path(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_absolute_path(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_absolute_path(item) for item in value)
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate or "://" in candidate:
        return candidate.lower().startswith("file://")
    return (
        PureWindowsPath(candidate).is_absolute()
        or PurePosixPath(candidate).is_absolute()
        or candidate.startswith("\\\\")
    )


def _require_sha256(value: Any, label: str) -> str:
    normalized = str(value or "").lower()
    if not SHA256_PATTERN.fullmatch(normalized):
        _fail(
            "NATIVE_EXPORT_SCHEMA_INVALID",
            f"{label} must be a lowercase SHA-256 value.",
        )
    return normalized


def validate_native_evidence_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_identity: Mapping[str, Any] | None = None,
    formal: bool = True,
) -> Mapping[str, Any]:
    """Validate v2 provenance, optionally against freshly computed inputs."""

    root = _get_mapping(manifest, "manifest")
    if root.get("schema") != "blueprint-to-code-native-evidence-set/v2":
        _fail(
            "NATIVE_EXPORT_SCHEMA_INVALID",
            "Native evidence manifest schema must be v2.",
        )
    _require_only_keys(
        root,
        {
            "schema",
            "evidenceSetId",
            "generatedAtUtc",
            "provenance",
            "trust",
            "selection",
            "targets",
            "recipeTargets",
            "gaps",
        },
        "manifest",
    )
    generated_at = root.get("generatedAtUtc")
    if not isinstance(generated_at, str) or not generated_at.strip():
        _fail(
            "NATIVE_EXPORT_SCHEMA_INVALID",
            "generatedAtUtc must be a timezone-aware ISO-8601 timestamp.",
        )
    try:
        parsed_generated_at = datetime.fromisoformat(
            generated_at.replace("Z", "+00:00")
            if generated_at.endswith("Z")
            else generated_at
        )
    except ValueError:
        parsed_generated_at = None
    if parsed_generated_at is None or parsed_generated_at.tzinfo is None:
        _fail(
            "NATIVE_EXPORT_SCHEMA_INVALID",
            "generatedAtUtc must be a timezone-aware ISO-8601 timestamp.",
        )
    provenance = _get_mapping(root.get("provenance"), "provenance")
    if _contains_absolute_path(provenance):
        _fail(
            "NATIVE_EVIDENCE_PROVENANCE_MISMATCH",
            "Native evidence provenance contains a local absolute path.",
        )
    _require_only_keys(
        provenance,
        {"binary", "pdb", "ghidra", "java", "generator"},
        "provenance",
    )
    binary = _get_mapping(provenance.get("binary"), "provenance.binary")
    pdb = _get_mapping(provenance.get("pdb"), "provenance.pdb")
    ghidra = _get_mapping(provenance.get("ghidra"), "provenance.ghidra")
    java = _get_mapping(provenance.get("java"), "provenance.java")
    generator = _get_mapping(
        provenance.get("generator"), "provenance.generator"
    )
    trust = _get_mapping(root.get("trust"), "trust")
    _require_only_keys(
        binary,
        {
            "module",
            "sha256",
            "size",
            "machine",
            "machineCode",
            "peTimestamp",
            "imageBase",
            "codeView",
        },
        "provenance.binary",
    )
    _require_only_keys(
        pdb,
        {
            "fileName",
            "sha256",
            "size",
            "version",
            "signature",
            "guid",
            "age",
            "loaded",
            "matchesBinary",
        },
        "provenance.pdb",
    )
    _require_only_keys(
        ghidra,
        {
            "version",
            "releaseAssetSha256",
            "languageId",
            "compilerSpecId",
            "analysisOptionsSha256",
        },
        "provenance.ghidra",
    )
    _require_only_keys(java, {"vendor", "version"}, "provenance.java")
    _require_only_keys(
        generator,
        {
            "repositoryCommit",
            "repositoryDirty",
            "recipeId",
            "recipeSha256",
            "scriptSha256",
        },
        "provenance.generator",
    )
    _require_only_keys(trust, {"status"}, "trust")

    binary_sha = _require_sha256(binary.get("sha256"), "binary.sha256")
    pdb_sha = _require_sha256(pdb.get("sha256"), "pdb.sha256")
    _require_sha256(
        ghidra.get("releaseAssetSha256"), "ghidra.releaseAssetSha256"
    )
    _require_sha256(
        ghidra.get("analysisOptionsSha256"), "ghidra.analysisOptionsSha256"
    )
    recipe_sha = _require_sha256(
        generator.get("recipeSha256"), "generator.recipeSha256"
    )
    script_hashes = _get_mapping(
        generator.get("scriptSha256"), "generator.scriptSha256"
    )
    for required_script in ("runner", "exporter", "pdbConfigurator"):
        _require_sha256(
            script_hashes.get(required_script),
            f"generator.scriptSha256.{required_script}",
        )
    if not re.fullmatch(
        r"[0-9a-f]{40,64}",
        str(generator.get("repositoryCommit", "")).lower(),
    ):
        _fail(
            "NATIVE_EXPORT_SCHEMA_INVALID",
            "generator.repositoryCommit is not a Git object ID.",
        )
    for value, label in (
        (ghidra.get("version"), "ghidra.version"),
        (ghidra.get("languageId"), "ghidra.languageId"),
        (ghidra.get("compilerSpecId"), "ghidra.compilerSpecId"),
        (java.get("vendor"), "java.vendor"),
        (java.get("version"), "java.version"),
        (generator.get("recipeId"), "generator.recipeId"),
    ):
        if not isinstance(value, str) or not value.strip():
            _fail(
                "NATIVE_EXPORT_SCHEMA_INVALID",
                f"{label} must be a non-empty string.",
            )
    codeview = _get_mapping(binary.get("codeView"), "binary.codeView")
    _require_only_keys(
        codeview,
        {"format", "guid", "age", "pdbFileName"},
        "provenance.binary.codeView",
    )
    if codeview.get("format") != "RSDS":
        _fail(
            "NATIVE_EXPORT_SCHEMA_INVALID",
            "Formal native evidence requires an RSDS CodeView identity.",
        )
    for guid, label in (
        (codeview.get("guid"), "binary.codeView.guid"),
        (pdb.get("guid"), "pdb.guid"),
    ):
        if not GUID_PATTERN.fullmatch(str(guid or "").lower()):
            _fail(
                "NATIVE_EXPORT_SCHEMA_INVALID",
                f"{label} must be a canonical GUID.",
            )
    if not isinstance(codeview.get("age"), int) or codeview.get("age") < 0:
        _fail(
            "NATIVE_EXPORT_SCHEMA_INVALID",
            "binary.codeView.age must be a non-negative integer.",
        )
    if not isinstance(pdb.get("age"), int) or pdb.get("age") < 0:
        _fail(
            "NATIVE_EXPORT_SCHEMA_INVALID",
            "pdb.age must be a non-negative integer.",
        )
    if (
        str(codeview.get("guid", "")).lower()
        != str(pdb.get("guid", "")).lower()
        or codeview.get("age") != pdb.get("age")
        or pdb.get("matchesBinary") is not True
    ):
        _fail(
            "NATIVE_PDB_IDENTITY_MISMATCH",
            "Manifest PDB identity does not match its PE CodeView identity.",
        )

    if expected_identity is not None:
        expected = _get_mapping(expected_identity, "expected identity")
        expected_binary = _get_mapping(
            expected.get("binary"), "expected identity binary"
        )
        expected_pdb = _get_mapping(
            expected.get("pdb"), "expected identity PDB"
        )
        if (
            binary_sha != str(expected_binary.get("sha256", "")).lower()
            or binary.get("module") != expected_binary.get("module")
            or binary.get("size") != expected_binary.get("size")
            or codeview != expected_binary.get("codeView")
        ):
            _fail(
                "NATIVE_EVIDENCE_PROVENANCE_MISMATCH",
                "Evidence binary identity differs from the current input.",
            )
        if (
            pdb_sha != str(expected_pdb.get("sha256", "")).lower()
            or pdb.get("fileName") != expected_pdb.get("fileName")
            or (
                "size" in pdb
                and pdb.get("size") != expected_pdb.get("size")
            )
        ):
            _fail(
                "NATIVE_PDB_HASH_MISMATCH",
                "Evidence PDB file identity differs from the current input.",
            )
        if (
            str(pdb.get("guid", "")).lower()
            != str(expected_pdb.get("guid", "")).lower()
            or pdb.get("age") != expected_pdb.get("age")
        ):
            _fail(
                "NATIVE_PDB_IDENTITY_MISMATCH",
                "Evidence PDB GUID/Age differs from the current input.",
            )

    if formal:
        if pdb.get("loaded") is not True:
            _fail(
                "NATIVE_PDB_NOT_LOADED",
                "Formal native evidence requires Ghidra to load the PDB.",
            )
        if generator.get("repositoryDirty") is not False:
            _fail(
                "NATIVE_EVIDENCE_PROVENANCE_MISMATCH",
                "Formal native evidence rejects a dirty generator repository.",
            )
        if trust.get("status") != "VERIFIED":
            _fail(
                "NATIVE_EVIDENCE_PROVENANCE_MISMATCH",
                "Formal native evidence trust status must be VERIFIED.",
            )

    evidence_set_id = str(root.get("evidenceSetId", ""))
    if evidence_set_id != f"native-set://{binary_sha}/{recipe_sha}":
        _fail(
            "NATIVE_EVIDENCE_PROVENANCE_MISMATCH",
            "Evidence-set ID is not bound to its binary and recipe SHA-256.",
        )
    if not isinstance(root.get("targets"), list) or not isinstance(
        root.get("gaps"), list
    ):
        _fail(
            "NATIVE_EXPORT_SCHEMA_INVALID",
            "Native evidence targets and gaps must be JSON arrays.",
        )
    if "recipeTargets" in root and not isinstance(
        root.get("recipeTargets"), list
    ):
        _fail(
            "NATIVE_EXPORT_SCHEMA_INVALID",
            "Native evidence recipeTargets must be a JSON array.",
        )
    return manifest


def create_native_evidence_manifest(
    raw_export: Mapping[str, Any],
    *,
    identity: Mapping[str, Any],
    ghidra: Mapping[str, Any],
    java: Mapping[str, Any],
    generator: Mapping[str, Any],
    formal: bool,
) -> dict[str, Any]:
    """Wrap a Ghidra v1 export in a verified, path-free provenance envelope."""

    raw = _get_mapping(raw_export, "raw Ghidra export")
    if raw.get("schema") != "blueprint-to-code-native-targets/v1":
        _fail(
            "NATIVE_EXPORT_SCHEMA_INVALID",
            "Legacy Ghidra export schema is not native-targets/v1.",
        )
    source_identity = _get_mapping(identity, "native build identity")
    binary = _get_mapping(source_identity.get("binary"), "native binary")
    pdb = _get_mapping(source_identity.get("pdb"), "native PDB")
    if (
        str(raw.get("binarySha256", "")).lower()
        != str(binary.get("sha256", "")).lower()
        or raw.get("program") != binary.get("module")
    ):
        _fail(
            "NATIVE_PROJECT_PROGRAM_HASH_MISMATCH",
            "Ghidra exported a program other than the requested binary.",
        )
    raw_pdb_guid = str(raw.get("pdbGuid") or "").strip().strip("{}").lower()
    raw_pdb_age_text = str(raw.get("pdbAge") or "").strip()
    try:
        raw_pdb_age = int(raw_pdb_age_text, 16)
    except ValueError:
        raw_pdb_age = None
    if formal and (not raw_pdb_guid or raw_pdb_age is None):
        _fail(
            "NATIVE_PDB_IDENTITY_MISMATCH",
            "Ghidra did not export a verifiable PDB GUID/Age.",
        )
    if raw_pdb_guid and (
        raw_pdb_guid != str(pdb.get("guid", "")).lower()
        or raw_pdb_age != pdb.get("age")
    ):
        _fail(
            "NATIVE_PDB_IDENTITY_MISMATCH",
            "Ghidra program PDB GUID/Age differs from the current PDB input.",
        )
    functions = raw.get("functions")
    if not isinstance(functions, list):
        _fail(
            "NATIVE_EXPORT_SCHEMA_INVALID",
            "Legacy Ghidra export functions must be a JSON array.",
        )

    ghidra_input = dict(_get_mapping(ghidra, "Ghidra provenance"))
    java_input = dict(_get_mapping(java, "Java provenance"))
    generator_input = dict(_get_mapping(generator, "generator provenance"))
    recipe_sha = _require_sha256(
        generator_input.get("recipeSha256"), "generator.recipeSha256"
    )
    binary_sha = _require_sha256(binary.get("sha256"), "binary.sha256")
    repository_dirty = generator_input.get("repositoryDirty") is not False
    trust_status = (
        "DIRTY_GENERATOR"
        if repository_dirty
        else ("VERIFIED" if formal else "EXPERIMENTAL")
    )
    gaps = []
    for function in functions:
        if isinstance(function, Mapping) and function.get(
            "decompileCompleted"
        ) is False:
            gaps.append(
                {
                    "kind": "DECOMPILE_FAILED",
                    "evidenceId": function.get("evidenceId"),
                    "reason": function.get("decompileError")
                    or "Ghidra decompile did not complete.",
                }
            )

    manifest = {
        "schema": "blueprint-to-code-native-evidence-set/v2",
        "evidenceSetId": f"native-set://{binary_sha}/{recipe_sha}",
        "generatedAtUtc": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "provenance": {
            "binary": {
                "module": binary.get("module"),
                "sha256": binary_sha,
                "size": binary.get("size"),
                "machine": binary.get("machine"),
                "machineCode": binary.get("machineCode"),
                "peTimestamp": binary.get("peTimestamp"),
                "imageBase": binary.get("imageBase"),
                "codeView": deepcopy(binary.get("codeView")),
            },
            "pdb": {
                "fileName": pdb.get("fileName"),
                "sha256": pdb.get("sha256"),
                "size": pdb.get("size"),
                "version": pdb.get("version"),
                "signature": pdb.get("signature"),
                "guid": pdb.get("guid"),
                "age": pdb.get("age"),
                "loaded": raw.get("pdbLoaded") is True,
                "matchesBinary": pdb.get("matchesBinary") is True,
            },
            "ghidra": {
                **ghidra_input,
                "languageId": raw.get("languageId"),
                "compilerSpecId": raw.get("compilerSpecId"),
            },
            "java": java_input,
            "generator": generator_input,
        },
        "trust": {"status": trust_status},
        "selection": {
            "patterns": deepcopy(raw.get("patterns") or []),
            "matchCount": len(functions),
        },
        "targets": deepcopy(functions),
        "gaps": gaps,
    }
    validate_native_evidence_manifest(
        manifest,
        expected_identity=source_identity,
        formal=formal,
    )
    return manifest
