"""Mine likely Blueprint graph names from Unreal .uasset files.

This is deliberately conservative: it does not parse Blueprint bytecode or
serialized graph objects. It only extracts safe ASCII/UTF-16 strings and ranks
names that look like Blueprint graph pages. ARK DevKit Python validation remains
the authority for whether a candidate is a real graph.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import struct
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from .config import NODE_SEMANTICS
from .core import build_blueprint_payload_from_nodes, parse_blueprint_text
from .models import NodeInfo, PinInfo


GRAPH_CANDIDATE_SCHEMA = "blueprint-translator.graph-candidates.uasset.v1"
UASSET_STRUCTURE_SCHEMA = "blueprint-translator.uasset-structure.v1"
UASSET_GRAPH_READ_SCHEMA = "blueprint-translator.uasset-graph-read.v1"
UASSET_PIN_LINK_SCHEMA = "blueprint-translator.uasset-pin-links.v1"
UASSET_CLIPBOARD_COMPARE_SCHEMA = "blueprint-translator.uasset-vs-clipboard-compare.v1"
UASSET_PARTIAL_TRIAGE_SCHEMA = "blueprint-translator.uasset-partial-graph-triage.v1"
UASSET_QUALITY_GATES_SCHEMA = "blueprint-translator.uasset-quality-gates.v1"
UASSET_CLASS_DEFAULTS_SCHEMA = "blueprint-translator.uasset-class-defaults.v1"
DEFAULT_MAX_CANDIDATES = 1600

DEFAULT_CONTENT_ROOTS = (
    r"C:\Program Files\Epic Games\ARKDevkit\Projects\ShooterGame\Content",
    r"D:\Epic Games\ARKDevkit\Projects\ShooterGame\Content",
    r"E:\Epic Games\ARKDevkit\Projects\ShooterGame\Content",
)

PREFIX_RE = re.compile(
    r"^(BP|BPTimer|Blueprint|OnRep|On[A-Z]|Receive|Server|Client|Net|Can|Get|Is|Has|Should|"
    r"Set|Start|Stop|Clear|Enable|Disable|Execute|Try|Update|Override|Prevent|Adjust|Do|"
    r"Check|Apply|Remove|Add|Play|Tick|MultiUse|Use|Allow|Force|Notify|Handle|Init)",
)
VALID_NAME_RE = re.compile(r"^[\w][\w '\-()]*$", re.UNICODE)
TYPE_SUFFIX_RE = re.compile(
    r"(Property|Component|Class|Struct|Enum|Interface|Delegate|Signature|Pin|Node|Schema|"
    r"Factory|Template|Asset|Data|Settings|Montage|Material|Texture|Particle|Sound|Font)$",
    re.IGNORECASE,
)

TOP_UOBJECT_PROPERTY_NAMES = {
    "Schema",
    "Nodes",
    "FunctionReference",
    "VariableReference",
    "EventReference",
    "DelegateReference",
    "MacroGraphReference",
    "CustomFunctionName",
    "NodePosX",
    "NodePosY",
    "NodeComment",
    "CommentText",
    "AdvancedPinDisplay",
    "EnabledState",
    "bIsNodeEnabled",
    "bCommentBubblePinned",
    "None",
}

NESTED_REFERENCE_PROPERTY_NAMES = {
    "MemberName",
    "MemberGuid",
    "MemberParent",
    "SelfContextInfo",
    "ReferenceObject",
}

UOBJECT_PROPERTY_TYPE_NAMES = {
    "ArrayProperty",
    "BoolProperty",
    "ByteProperty",
    "DoubleProperty",
    "EnumProperty",
    "FloatProperty",
    "IntProperty",
    "Int64Property",
    "MapProperty",
    "NameProperty",
    "ObjectProperty",
    "SoftObjectProperty",
    "StrProperty",
    "StructProperty",
    "TextProperty",
}

PIN_CATEGORY_NAMES = {
    "exec",
    "bool",
    "byte",
    "class",
    "delegate",
    "double",
    "float",
    "int",
    "int64",
    "interface",
    "name",
    "object",
    "real",
    "rotator",
    "softclass",
    "softobject",
    "string",
    "struct",
    "text",
    "vector",
    "wildcard",
}

PIN_OUTPUT_NAMES = {
    "then",
    "returnvalue",
    "output",
    "true",
    "false",
    "completed",
    "loop body",
    "array element",
    "is valid",
    "is not valid",
    "cast succeeded",
    "cast failed",
}

NODE_CLASS_PREFIXES = ("K2Node_", "EdGraphNode_")

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

UASSET_GRAPH_STATUS_MEANINGS = {
    "complete": "Nodes, pins, and links were recovered with enough structure for normal reports.",
    "partial": "Nodes and at least some pins were recovered, but wiring or pin coverage is incomplete.",
    "heuristic": "Nodes were recovered and the remaining graph data is inferred by byte-pattern scanning.",
    "failed": "The graph export could not be converted into a report payload.",
    "needs_clipboard": "The graph should be manually copied because binary recovery did not reach useful coverage.",
}

FAILURE_CATEGORY_MEANINGS = {
    "need_pin_layout_rule": "The graph has nodes but pin custom-data layout is not fully decoded.",
    "need_node_reader": "The graph contains node classes that need a dedicated semantic reader.",
    "need_cross_graph_resolve": "The graph has links that point outside the locally resolved node set.",
    "need_manual_clipboard": "The binary reader could not recover enough content; use manual Ctrl+A/C for this page.",
}

PARTIAL_TRIAGE_MEANINGS = {
    "missing_target_pin_id": "Links resolve to nodes but still need exact target PinId decoding.",
    "pin_count_mismatch": "A node reported more serialized pins than were recovered.",
    "custom_pin_layout_variant": "This graph uses a custom pin layout variant that needs another binary rule.",
    "single_node_graph": "The graph is tiny or isolated; low link counts may be normal.",
    "external_or_macro_link": "Links point to macro/collapsed/external boundaries rather than local nodes.",
    "collapsed_graph_boundary": "The graph is a collapsed graph/tunnel boundary and should be checked with its parent graph.",
    "unknown_node_custom_data": "At least one node class has custom data that is not yet interpreted semantically.",
    "manual_only": "Binary recovery is too weak; manual clipboard capture is the best next step.",
}

SEMANTIC_NODE_CLASSES = {
    "K2Node_CallFunction",
    "K2Node_CallArrayFunction",
    "K2Node_CallParentFunction",
    "K2Node_VariableGet",
    "K2Node_VariableSet",
    "K2Node_Event",
    "K2Node_CustomEvent",
    "K2Node_FunctionEntry",
    "K2Node_FunctionResult",
    "K2Node_IfThenElse",
    "K2Node_ExecutionSequence",
    "K2Node_DynamicCast",
    "K2Node_MacroInstance",
    "K2Node_AddDelegate",
    "K2Node_ComponentBoundEvent",
    "K2Node_Knot",
    "K2Node_Timeline",
    "K2Node_CommutativeAssociativeBinaryOperator",
    "K2Node_Self",
    "K2Node_Tunnel",
    "K2Node_EnumEquality",
    "K2Node_MakeStruct",
    "K2Node_GetArrayItem",
    "K2Node_MakeArray",
    "K2Node_Composite",
    "K2Node_SwitchEnum",
    "K2Node_BreakStruct",
    "K2Node_Select",
    "K2Node_ConvertAsset",
    "K2Node_AsyncAction",
    "K2Node_SetFieldsInStruct",
}
SUPPORTED_SEMANTIC_NODE_CLASSES = set(NODE_SEMANTICS) | SEMANTIC_NODE_CLASSES | {"EdGraphNode_Comment"}
CORE_RECOVERABLE_PROPERTY_NAMES = {
    "FunctionReference",
    "VariableReference",
    "EventReference",
    "DelegateReference",
    "MacroGraphReference",
    "CustomFunctionName",
    "NodeGuid",
    "GraphGuid",
    "NodePosX",
    "NodePosY",
    "NodeComment",
    "CommentText",
}

EXCLUDE_CONTAINS = (
    "/",
    "\\",
    "::",
    "__",
    "K2Node",
    "EdGraph",
    "BlueprintGeneratedClass",
    "Default__",
    "ObjectRedirector",
    "AnimBlueprint",
)

SHORT_ACTION_WORDS = {
    "ai",
    "actor",
    "attack",
    "camo",
    "cloak",
    "combo",
    "dino",
    "hud",
    "pawn",
    "roar",
    "taming",
    "wake",
}

KNOWN_PAGE_PHRASES = {
    "ai stalking behavior": "AI Stalking Behavior",
    "big spacebar leap": "Big Spacebar Leap",
    "natural armor": "Natural Armor",
    "net exec": "Net Exec",
    "pack buff": "Pack Buff",
    "pack logic": "Pack Logic",
    "sleep during day": "Sleep During Day",
    "teleport attack": "Teleport Attack",
}


def safe_filename(value: str, fallback: str = "Blueprint") -> str:
    text = str(value or fallback).strip()
    text = re.sub(r"[^\w.\- ]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text)
    return text.strip("._ ") or fallback


def normalize_blueprint_object_path(raw_text: str) -> str:
    text = str(raw_text or "").strip().replace("\\", "/").strip("\"'")
    quoted = re.search(r"['\"](?P<path>/Game/[^'\"]+)['\"]", text)
    if quoted:
        text = quoted.group("path").strip()
    path_match = re.search(r"(?P<path>/Game/[^\s,'\"]+)", text)
    if path_match:
        text = path_match.group("path").strip()
    text = text.strip("\"'")
    if not text.startswith("/Game/"):
        return ""
    if "." in text and text.endswith("_C"):
        package, obj = text.rsplit(".", 1)
        text = package + "." + obj[:-2]
    if "." not in text:
        object_name = text.rsplit("/", 1)[-1]
        if object_name:
            text = text + "." + object_name
    return text


def asset_name_from_object_path(asset_path: str) -> str:
    normalized = normalize_blueprint_object_path(asset_path)
    if not normalized:
        return ""
    object_name = normalized.rsplit(".", 1)[-1]
    return object_name[:-2] if object_name.endswith("_C") else object_name


def package_path_from_object_path(asset_path: str) -> str:
    normalized = normalize_blueprint_object_path(asset_path)
    if not normalized:
        return ""
    return normalized.split(".", 1)[0]


def content_roots(extra_roots: Iterable[str | os.PathLike[str]] | None = None) -> list[Path]:
    roots: list[Path] = []
    for env_name in ("ARK_DEVKIT_CONTENT_ROOT", "BLUEPRINT_TO_CODE_DEVKIT_CONTENT_ROOT"):
        value = os.environ.get(env_name)
        if value:
            roots.append(Path(value))
    for env_name in ("ARK_DEVKIT_ROOT", "BLUEPRINT_TO_CODE_DEVKIT_ROOT"):
        value = os.environ.get(env_name)
        if value:
            roots.append(Path(value) / "Projects" / "ShooterGame" / "Content")
    if extra_roots:
        roots.extend(Path(value) for value in extra_roots if str(value).strip())
    roots.extend(Path(value) for value in DEFAULT_CONTENT_ROOTS)

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def object_path_to_uasset_path(
    asset_path: str,
    extra_roots: Iterable[str | os.PathLike[str]] | None = None,
) -> tuple[Path | None, list[str]]:
    package_path = package_path_from_object_path(asset_path)
    if not package_path:
        return None, []
    relative = package_path.removeprefix("/Game/") + ".uasset"
    relative_path = Path(*relative.split("/"))
    attempted: list[str] = []
    for root in content_roots(extra_roots):
        candidate = root / relative_path
        attempted.append(str(candidate))
        if candidate.is_file():
            return candidate, attempted
    return None, attempted


def _extract_ascii_strings(data: bytes) -> Iterable[str]:
    for match in re.finditer(rb"[\x20-\x7e]{3,}", data):
        value = match.group(0).decode("ascii", errors="ignore").strip("\x00")
        if value:
            yield value


def _extract_utf16le_strings(data: bytes) -> Iterable[str]:
    for match in re.finditer(rb"(?:[\x20-\x7e]\x00){3,}", data):
        try:
            value = match.group(0).decode("utf-16le", errors="ignore").strip("\x00")
        except UnicodeDecodeError:
            continue
        if value:
            yield value


def _read_i32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def _read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _read_i64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<q", data, offset)[0]


def _read_f32(data: bytes, offset: int) -> float:
    return struct.unpack_from("<f", data, offset)[0]


def _read_f64(data: bytes, offset: int) -> float:
    return struct.unpack_from("<d", data, offset)[0]


def _read_fstring(data: bytes, offset: int) -> tuple[str, int]:
    length = _read_i32(data, offset)
    offset += 4
    if length == 0:
        return "", offset
    if length > 0:
        if length > 1_000_000 or offset + length > len(data):
            raise ValueError("Invalid FString length.")
        raw = data[offset : offset + max(0, length - 1)]
        offset += length
        return raw.decode("ascii", errors="replace"), offset
    char_count = -length
    byte_count = char_count * 2
    if char_count > 1_000_000 or offset + byte_count > len(data):
        raise ValueError("Invalid UTF-16 FString length.")
    raw = data[offset : offset + max(0, byte_count - 2)]
    offset += byte_count
    return raw.decode("utf-16le", errors="replace"), offset


def _read_fname(names: list[str], data: bytes, offset: int) -> tuple[str, int, int]:
    index = _read_i32(data, offset)
    number = _read_i32(data, offset + 4)
    name = names[index] if 0 <= index < len(names) else ""
    return name, index, number


def parse_uasset_summary(data: bytes) -> tuple[dict[str, object], list[str]]:
    warnings: list[str] = []
    if len(data) < 256:
        raise ValueError("File is too small to be a uasset.")
    tag = _read_u32(data, 0)
    if tag != 0x9E2A83C1:
        raise ValueError("Unsupported uasset magic tag.")
    legacy_file_version = _read_i32(data, 4)
    legacy_ue3_version = _read_i32(data, 8)
    file_version_ue4 = _read_i32(data, 12)
    offset = 16
    file_version_ue5 = 0
    if legacy_file_version <= -8:
        file_version_ue5 = _read_i32(data, offset)
        offset += 4
    file_version_licensee = _read_i32(data, offset)
    offset += 4
    custom_version_count = _read_i32(data, offset)
    offset += 4
    if custom_version_count < 0 or custom_version_count > 10000:
        raise ValueError("Invalid custom version count.")
    offset += custom_version_count * 20
    total_header_size = _read_i32(data, offset)
    offset += 4
    package_name, offset = _read_fstring(data, offset)
    package_flags = _read_u32(data, offset)
    offset += 4
    name_count = _read_i32(data, offset)
    name_offset = _read_i32(data, offset + 4)
    offset += 8
    soft_object_paths_count = 0
    soft_object_paths_offset = 0
    localization_id = ""
    gatherable_text_data_count = 0
    gatherable_text_data_offset = 0
    export_count = 0
    export_offset = 0
    import_count = 0
    import_offset = 0
    depends_offset = 0
    try:
        soft_object_paths_count = _read_i32(data, offset)
        soft_object_paths_offset = _read_i32(data, offset + 4)
        offset += 8
        maybe_length = _read_i32(data, offset)
        if 0 < maybe_length < 512 and offset + 4 + maybe_length <= len(data):
            localization_id, offset = _read_fstring(data, offset)
        gatherable_text_data_count = _read_i32(data, offset)
        gatherable_text_data_offset = _read_i32(data, offset + 4)
        export_count = _read_i32(data, offset + 8)
        export_offset = _read_i32(data, offset + 12)
        import_count = _read_i32(data, offset + 16)
        import_offset = _read_i32(data, offset + 20)
        depends_offset = _read_i32(data, offset + 24)
    except Exception as exc:
        warnings.append(f"Could not parse full package summary: {exc}")
    return (
        {
            "legacy_file_version": legacy_file_version,
            "legacy_ue3_version": legacy_ue3_version,
            "file_version_ue4": file_version_ue4,
            "file_version_ue5": file_version_ue5,
            "file_version_licensee": file_version_licensee,
            "custom_version_count": custom_version_count,
            "total_header_size": total_header_size,
            "package_name": package_name,
            "package_flags": package_flags,
            "name_count": name_count,
            "name_offset": name_offset,
            "soft_object_paths_count": soft_object_paths_count,
            "soft_object_paths_offset": soft_object_paths_offset,
            "localization_id": localization_id,
            "gatherable_text_data_count": gatherable_text_data_count,
            "gatherable_text_data_offset": gatherable_text_data_offset,
            "export_count": export_count,
            "export_offset": export_offset,
            "import_count": import_count,
            "import_offset": import_offset,
            "depends_offset": depends_offset,
        },
        warnings,
    )


def parse_uasset_name_map(data: bytes, summary: dict[str, object]) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    count = int(summary.get("name_count") or 0)
    offset = int(summary.get("name_offset") or 0)
    names: list[str] = []
    if count <= 0 or offset <= 0 or offset >= len(data):
        return names, ["Package summary does not include a usable NameMap."]
    pos = offset
    try:
        for _index in range(count):
            name, pos = _read_fstring(data, pos)
            # UE4/UE5 serialized NameMap entries include a 32-bit non-case hash
            # after the string. This is enough for the ARK DevKit assets tested
            # locally; if it fails we report structure parsing as experimental.
            pos += 4
            names.append(name)
    except Exception as exc:
        warnings.append(f"Could not parse complete NameMap: {exc}")
    return names, warnings


def _name_from_package_index(package_index: int, imports: list[dict[str, object]], exports: list[dict[str, object]]) -> str:
    if package_index < 0:
        index = -package_index - 1
        if 0 <= index < len(imports):
            return str(imports[index].get("object_name") or "")
        return ""
    if package_index > 0:
        index = package_index - 1
        if 0 <= index < len(exports):
            return str(exports[index].get("object_name") or "")
    return ""


def export_display_name(object_name: str, object_number: int | object) -> str:
    try:
        number = int(object_number)
    except Exception:
        number = 0
    if number > 0:
        return f"{object_name}_{number - 1}"
    return object_name


def package_index_to_export_index(package_index: int) -> int | None:
    return package_index - 1 if package_index > 0 else None


def parse_uasset_imports(data: bytes, summary: dict[str, object], names: list[str]) -> tuple[list[dict[str, object]], list[str]]:
    warnings: list[str] = []
    count = int(summary.get("import_count") or 0)
    offset = int(summary.get("import_offset") or 0)
    export_offset = int(summary.get("export_offset") or 0)
    imports: list[dict[str, object]] = []
    if count <= 0 or offset <= 0 or export_offset <= offset:
        return imports, ["Package summary does not include a usable ImportMap."]
    size = (export_offset - offset) // count
    if size < 28:
        return imports, [f"Unsupported ImportMap entry size: {size}."]
    try:
        for index in range(count):
            base = offset + index * size
            class_package, _class_package_index, _class_package_number = _read_fname(names, data, base)
            class_name, _class_name_index, _class_name_number = _read_fname(names, data, base + 8)
            outer_index = _read_i32(data, base + 16)
            object_name, _object_name_index, _object_name_number = _read_fname(names, data, base + 20)
            package_name = ""
            if size >= 36:
                package_name, _package_name_index, _package_name_number = _read_fname(names, data, base + 28)
            imports.append(
                {
                    "index": index,
                    "class_package": class_package,
                    "class_name": class_name,
                    "outer_index": outer_index,
                    "object_name": object_name,
                    "package_name": package_name,
                }
            )
    except Exception as exc:
        warnings.append(f"Could not parse complete ImportMap: {exc}")
    return imports, warnings


def parse_uasset_exports(
    data: bytes,
    summary: dict[str, object],
    names: list[str],
    imports: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[str]]:
    warnings: list[str] = []
    count = int(summary.get("export_count") or 0)
    offset = int(summary.get("export_offset") or 0)
    depends_offset = int(summary.get("depends_offset") or 0)
    exports: list[dict[str, object]] = []
    if count <= 0 or offset <= 0 or depends_offset <= offset:
        return exports, ["Package summary does not include a usable ExportMap."]
    size = (depends_offset - offset) // count
    if size < 40:
        return exports, [f"Unsupported ExportMap entry size: {size}."]
    try:
        for index in range(count):
            base = offset + index * size
            class_index = _read_i32(data, base)
            super_index = _read_i32(data, base + 4)
            template_index = _read_i32(data, base + 8)
            outer_index = _read_i32(data, base + 12)
            object_name, _name_index, object_number = _read_fname(names, data, base + 16)
            object_flags = _read_u32(data, base + 24)
            serial_size = 0
            serial_offset = 0
            if size >= 44:
                serial_size = _read_i64(data, base + 28)
                serial_offset = _read_i64(data, base + 36)
            elif size >= 36:
                serial_size = _read_i32(data, base + 28)
                serial_offset = _read_i32(data, base + 32)
            class_name = _name_from_package_index(class_index, imports, exports)
            outer_name = _name_from_package_index(outer_index, imports, exports)
            exports.append(
                {
                    "index": index,
                    "package_index": index + 1,
                    "class_index": class_index,
                    "class_name": class_name,
                    "super_index": super_index,
                    "template_index": template_index,
                    "outer_index": outer_index,
                    "outer_name": outer_name,
                    "object_name": object_name,
                    "object_number": object_number,
                    "display_name": export_display_name(object_name, object_number),
                    "object_flags": object_flags,
                    "serial_size": serial_size,
                    "serial_offset": serial_offset,
                    "export_map_entry_size": size,
                }
            )
    except Exception as exc:
        warnings.append(f"Could not parse complete ExportMap: {exc}")
    return exports, warnings


def companion_uexp_path(uasset_path: Path) -> Path:
    return uasset_path.with_suffix(".uexp")


def locate_export_data(
    export: dict[str, object],
    *,
    uasset_size: int,
    uexp_size: int = 0,
    total_header_size: int = 0,
) -> dict[str, object]:
    serial_size = int(export.get("serial_size") or 0)
    serial_offset = int(export.get("serial_offset") or 0)
    if serial_size <= 0:
        return {
            "file": "",
            "offset": serial_offset,
            "size": serial_size,
            "available": False,
            "reason": "empty_serial_data",
        }
    if 0 <= serial_offset and serial_offset + serial_size <= uasset_size:
        return {
            "file": "uasset",
            "offset": serial_offset,
            "size": serial_size,
            "available": True,
            "reason": "absolute_uasset_offset",
        }
    if uexp_size > 0:
        uexp_relative = serial_offset - uasset_size
        if 0 <= uexp_relative and uexp_relative + serial_size <= uexp_size:
            return {
                "file": "uexp",
                "offset": uexp_relative,
                "size": serial_size,
                "available": True,
                "reason": "serial_offset_after_uasset",
            }
        header_relative = serial_offset - total_header_size
        if 0 <= header_relative and header_relative + serial_size <= uexp_size:
            return {
                "file": "uexp",
                "offset": header_relative,
                "size": serial_size,
                "available": True,
                "reason": "serial_offset_after_header",
            }
        if 0 <= serial_offset and serial_offset + serial_size <= uexp_size:
            return {
                "file": "uexp",
                "offset": serial_offset,
                "size": serial_size,
                "available": True,
                "reason": "absolute_uexp_offset",
            }
    return {
        "file": "uexp" if uexp_size else "missing",
        "offset": serial_offset,
        "size": serial_size,
        "available": False,
        "reason": "serialized_data_outside_loaded_files",
    }


def attach_export_data_locations(
    exports: list[dict[str, object]],
    *,
    uasset_size: int,
    uexp_size: int = 0,
    total_header_size: int = 0,
) -> None:
    for export in exports:
        export["serial_location"] = locate_export_data(
            export,
            uasset_size=uasset_size,
            uexp_size=uexp_size,
            total_header_size=total_header_size,
        )


def _structure_graph_kind(name: str, has_function_export: bool) -> str:
    lowered = normalize_candidate_text(name).lower()
    if lowered == "eventgraph":
        return "EventGraph"
    if "construction" in lowered or lowered == "userconstructionscript":
        return "ConstructionScript"
    if has_function_export:
        return "FunctionGraph"
    if lowered.startswith("collapsed ") or lowered.startswith("collapsegraph"):
        return "CollapsedGraph"
    return "StandaloneEdGraph"


def _structure_type_hint(graph_kind: str) -> str:
    if graph_kind == "FunctionGraph":
        return "Function"
    if graph_kind in {"EventGraph", "ConstructionScript", "Macro"}:
        return graph_kind
    return "Unknown"


def parse_uasset_structure(uasset_path: Path) -> dict[str, object]:
    data = uasset_path.read_bytes()
    uexp_path = companion_uexp_path(uasset_path)
    uexp_size = uexp_path.stat().st_size if uexp_path.is_file() else 0
    warnings: list[str] = []
    try:
        summary, summary_warnings = parse_uasset_summary(data)
    except Exception as exc:
        return {
            "schema": UASSET_STRUCTURE_SCHEMA,
            "method": "experimental_name_import_export_map",
            "uasset_path": str(uasset_path),
            "loaded": False,
            "warnings": [str(exc)],
        }
    warnings.extend(summary_warnings)
    names, name_warnings = parse_uasset_name_map(data, summary)
    warnings.extend(name_warnings)
    imports, import_warnings = parse_uasset_imports(data, summary, names)
    warnings.extend(import_warnings)
    exports, export_warnings = parse_uasset_exports(data, summary, names, imports)
    warnings.extend(export_warnings)
    attach_export_data_locations(
        exports,
        uasset_size=len(data),
        uexp_size=uexp_size,
        total_header_size=int(summary.get("total_header_size") or 0),
    )

    class_counts: dict[str, int] = {}
    graph_exports: list[dict[str, object]] = []
    function_exports: list[dict[str, object]] = []
    for item in exports:
        class_name = str(item.get("class_name") or "")
        if class_name:
            class_counts[class_name] = class_counts.get(class_name, 0) + 1
        object_name = str(item.get("object_name") or "")
        if class_name == "EdGraph" and object_name:
            graph_exports.append(
                {
                    "name": object_name,
                    "class": class_name,
                    "outer": item.get("outer_name", ""),
                    "export_index": item.get("index", 0),
                    "serial_size": item.get("serial_size", 0),
                    "serial_offset": item.get("serial_offset", 0),
                    "serial_location": item.get("serial_location", {}),
                    "source": "ExportMap class EdGraph",
                }
            )
        elif class_name == "Function" and object_name:
            function_exports.append(
                {
                    "name": object_name,
                    "class": class_name,
                    "outer": item.get("outer_name", ""),
                    "export_index": item.get("index", 0),
                    "serial_size": item.get("serial_size", 0),
                    "serial_offset": item.get("serial_offset", 0),
                    "serial_location": item.get("serial_location", {}),
                    "source": "ExportMap class Function",
                }
            )

    top_classes = [
        {"class": name, "count": count}
        for name, count in sorted(class_counts.items(), key=lambda item: (-item[1], item[0]))[:80]
    ]
    function_name_keys = {normalize_candidate_text(str(item.get("name") or "")).lower() for item in function_exports}
    graph_kind_counts: dict[str, int] = {}
    for item in graph_exports:
        name = str(item.get("name") or "")
        has_function_export = normalize_candidate_text(name).lower() in function_name_keys
        graph_kind = _structure_graph_kind(name, has_function_export)
        item["matching_function_export"] = has_function_export
        item["graph_kind"] = graph_kind
        item["type_hint"] = _structure_type_hint(graph_kind)
        graph_kind_counts[graph_kind] = graph_kind_counts.get(graph_kind, 0) + 1

    return {
        "schema": UASSET_STRUCTURE_SCHEMA,
        "method": "experimental_name_import_export_map",
        "uasset_path": str(uasset_path),
        "uexp_path": str(uexp_path) if uexp_path.is_file() else "",
        "uasset_size": len(data),
        "uexp_size": uexp_size,
        "loaded": bool(names and exports),
        "summary": summary,
        "name_count": len(names),
        "import_count": len(imports),
        "export_count": len(exports),
        "top_export_classes": top_classes,
        "graph_exports_count": len(graph_exports),
        "function_exports_count": len(function_exports),
        "function_graph_exports_count": graph_kind_counts.get("FunctionGraph", 0),
        "builtin_graph_exports_count": graph_kind_counts.get("EventGraph", 0)
        + graph_kind_counts.get("ConstructionScript", 0),
        "collapsed_graph_exports_count": graph_kind_counts.get("CollapsedGraph", 0),
        "standalone_graph_exports_count": graph_kind_counts.get("StandaloneEdGraph", 0),
        "graph_kind_counts": [
            {"kind": name, "count": count}
            for name, count in sorted(graph_kind_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "graph_exports": graph_exports,
        "function_exports": function_exports,
        "warnings": warnings,
    }


def parse_uasset_package(uasset_path: Path) -> dict[str, object]:
    data = uasset_path.read_bytes()
    uexp_path = companion_uexp_path(uasset_path)
    uexp_data = uexp_path.read_bytes() if uexp_path.is_file() else b""
    warnings: list[str] = []
    summary, summary_warnings = parse_uasset_summary(data)
    warnings.extend(summary_warnings)
    names, name_warnings = parse_uasset_name_map(data, summary)
    warnings.extend(name_warnings)
    soft_object_paths, soft_object_warnings = parse_uasset_soft_object_paths(data, summary, names)
    warnings.extend(soft_object_warnings)
    imports, import_warnings = parse_uasset_imports(data, summary, names)
    warnings.extend(import_warnings)
    exports, export_warnings = parse_uasset_exports(data, summary, names, imports)
    warnings.extend(export_warnings)
    attach_export_data_locations(
        exports,
        uasset_size=len(data),
        uexp_size=len(uexp_data),
        total_header_size=int(summary.get("total_header_size") or 0),
    )
    return {
        "uasset_path": uasset_path,
        "uexp_path": uexp_path if uexp_data else None,
        "uasset_data": data,
        "uexp_data": uexp_data,
        "summary": summary,
        "names": names,
        "soft_object_paths": soft_object_paths,
        "imports": imports,
        "exports": exports,
        "warnings": warnings,
    }


def parse_uasset_soft_object_paths(data: bytes, summary: dict[str, object], names: list[str]) -> tuple[list[dict[str, object]], list[str]]:
    count = int(summary.get("soft_object_paths_count") or 0)
    offset = int(summary.get("soft_object_paths_offset") or 0)
    if count <= 0 or offset <= 0:
        return [], []
    warnings: list[str] = []
    paths: list[dict[str, object]] = []
    pos = offset
    for index in range(count):
        start = pos
        if pos + 16 > len(data):
            warnings.append(f"Soft object path {index}: offset out of range")
            break
        package_info = _fname_at(data, pos, names)
        asset_info = _fname_at(data, pos + 8, names)
        if not package_info or not asset_info:
            warnings.append(f"Soft object path {index}: invalid top-level asset path")
            break
        pos += 16
        try:
            sub_path, pos = _read_fstring(data, pos)
        except Exception as exc:
            warnings.append(f"Soft object path {index}: {exc}")
            break
        package_name = package_info[0]
        asset_name = asset_info[0]
        object_path = f"{package_name}.{asset_name}" if package_name and asset_name else package_name or asset_name
        if sub_path:
            object_path = f"{object_path}:{sub_path}" if object_path else sub_path
        paths.append(
            {
                "index": index,
                "package": package_name,
                "asset": asset_name,
                "sub_path": sub_path,
                "object_path": object_path,
                "offset": start,
            }
        )
    return paths, warnings


def export_data_bytes(package: dict[str, object], export: dict[str, object]) -> bytes:
    location = export.get("serial_location", {})
    if not isinstance(location, dict) or not location.get("available"):
        return b""
    offset = int(location.get("offset") or 0)
    size = int(location.get("size") or 0)
    if location.get("file") == "uexp":
        data = package.get("uexp_data", b"")
    else:
        data = package.get("uasset_data", b"")
    if not isinstance(data, (bytes, bytearray)) or size <= 0:
        return b""
    return bytes(data[offset : offset + size])


def _fname_at(data: bytes, offset: int, names: list[str]) -> tuple[str, int, int] | None:
    if offset < 0 or offset + 8 > len(data):
        return None
    index = _read_i32(data, offset)
    number = _read_i32(data, offset + 4)
    if 0 <= index < len(names):
        return names[index], index, number
    return None


def fname_positions(data: bytes, names: list[str], targets: Iterable[str]) -> list[tuple[int, str]]:
    target_set = set(targets)
    wanted = {name: index for index, name in enumerate(names) if name in target_set}
    positions: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for name, index in wanted.items():
        needle = struct.pack("<i", index)
        start = 0
        while True:
            pos = data.find(needle, start)
            if pos < 0:
                break
            if pos + 8 <= len(data) and _read_i32(data, pos + 4) == 0:
                marker = (pos, name)
                if marker not in seen:
                    positions.append(marker)
                    seen.add(marker)
            start = pos + 1
    positions.sort(key=lambda item: item[0])
    return positions


def _next_position_after(positions: list[int], value: int) -> int | None:
    for pos in positions:
        if pos > value:
            return pos
    return None


def parse_top_property_blocks(data: bytes, names: list[str]) -> list[dict[str, object]]:
    starts = fname_positions(data, names, TOP_UOBJECT_PROPERTY_NAMES - {"None"})
    none_positions = [pos for pos, _name in fname_positions(data, names, {"None"})]
    start_positions = [pos for pos, _name in starts]
    blocks: list[dict[str, object]] = []
    for pos, property_name in starts:
        type_name = ""
        type_info = _fname_at(data, pos + 8, names)
        if type_info:
            type_name = type_info[0]
        next_start = _next_position_after(start_positions, pos)
        next_none = _next_position_after(none_positions, pos)
        if next_start is not None:
            end = next_start
        elif next_none is not None:
            end = next_none
        else:
            end = len(data)
        if end <= pos:
            continue
        blocks.append(
            {
                "name": property_name,
                "type": type_name,
                "offset": pos,
                "end": end,
                "raw_size": end - pos,
            }
        )
    return blocks


def _read_i32_candidate(data: bytes, positions: Iterable[int], *, limit: int = 1_000_000) -> int | None:
    for pos in positions:
        if 0 <= pos <= len(data) - 4:
            value = _read_i32(data, pos)
            if -limit <= value <= limit:
                return value
    return None


def _read_fname_candidate(data: bytes, names: list[str], positions: Iterable[int]) -> str:
    for pos in positions:
        item = _fname_at(data, pos, names)
        if item and item[0]:
            return item[0]
    return ""


def _read_bool_candidate(data: bytes, positions: Iterable[int]) -> bool | None:
    for pos in positions:
        if 0 <= pos < len(data) and data[pos] in {0, 1}:
            return bool(data[pos])
    return None


def _read_float_candidate(data: bytes, positions: Iterable[int]) -> float | None:
    for pos in positions:
        if 0 <= pos <= len(data) - 4:
            value = _read_f32(data, pos)
            if -1.0e12 < value < 1.0e12:
                return value
    return None


def _read_double_candidate(data: bytes, positions: Iterable[int]) -> float | None:
    for pos in positions:
        if 0 <= pos <= len(data) - 8:
            value = _read_f64(data, pos)
            if -1.0e18 < value < 1.0e18:
                return value
    return None


def _valid_package_index(value: int, imports: list[dict[str, object]], exports: list[dict[str, object]]) -> bool:
    if value > 0:
        return value <= len(exports)
    if value < 0:
        return -value <= len(imports)
    return True


def object_ref_name(value: int, imports: list[dict[str, object]], exports: list[dict[str, object]]) -> str:
    if value > 0:
        index = value - 1
        if 0 <= index < len(exports):
            item = exports[index]
            return str(item.get("display_name") or item.get("object_name") or "")
    if value < 0:
        index = -value - 1
        if 0 <= index < len(imports):
            return str(imports[index].get("object_name") or "")
    return ""


def extract_object_ref_array(
    block: bytes,
    imports: list[dict[str, object]],
    exports: list[dict[str, object]],
) -> tuple[list[int], int]:
    best_refs: list[int] = []
    best_offset = -1
    best_score = -1
    for pos in range(16, max(16, len(block) - 4)):
        count = _read_i32(block, pos)
        if count <= 0 or count > 100_000:
            continue
        refs_start = pos + 4
        refs_end = refs_start + count * 4
        if refs_end > len(block):
            continue
        refs = [_read_i32(block, refs_start + index * 4) for index in range(count)]
        valid = sum(1 for value in refs if value and _valid_package_index(value, imports, exports))
        score = valid * 10 - abs((len(block) - refs_start) - count * 4)
        if valid >= max(1, count // 2) and score > best_score:
            best_refs = refs
            best_offset = pos
            best_score = score
    return best_refs, best_offset


def extract_member_reference_name(data: bytes, names: list[str]) -> str:
    for pos, _name in fname_positions(data, names, {"MemberName"}):
        value = _read_fname_candidate(data, names, [pos + 25, pos + 24, pos + 26, pos + 29])
        if value and value not in UOBJECT_PROPERTY_TYPE_NAMES:
            return value
    return ""


def guid_to_text(raw: bytes) -> str:
    if len(raw) != 16:
        return ""
    a, b, c, d = struct.unpack("<IIII", raw)
    return f"{a:08X}{b:08X}{c:08X}{d:08X}"


def guid_text_to_raw(value: str) -> bytes:
    text = str(value or "").strip().replace("-", "")
    if not re.fullmatch(r"[0-9A-Fa-f]{32}", text):
        return b""
    parts = [int(text[index : index + 8], 16) for index in range(0, 32, 8)]
    return struct.pack("<IIII", *parts)


def guid_candidates_from_region(region: bytes, *, limit: int = 256) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    upper = max(0, min(len(region), limit) - 15)
    for pos in range(0, upper):
        raw = region[pos : pos + 16]
        if len(raw) != 16 or raw == b"\x00" * 16:
            continue
        if len(set(raw)) < 5:
            continue
        text = guid_to_text(raw)
        if text not in seen:
            candidates.append(text)
            seen.add(text)
    return candidates


def confidence_floor(*levels: str) -> str:
    values = [level for level in levels if level]
    if not values:
        return "low"
    return min(values, key=lambda level: CONFIDENCE_RANK.get(level, 0))


def confidence_from_warnings(warnings: Iterable[str], *, base: str = "high") -> str:
    warning_count = len([warning for warning in warnings if str(warning).strip()])
    if warning_count == 0:
        return base
    if warning_count <= 2 and CONFIDENCE_RANK.get(base, 0) >= CONFIDENCE_RANK["medium"]:
        return "medium"
    return "low"


def raw_offsets(start: int, end: int) -> dict[str, int]:
    return {"start": int(start), "end": int(end)}


def property_parse_source(type_name: str) -> str:
    if type_name in {
        "IntProperty",
        "Int64Property",
        "BoolProperty",
        "FloatProperty",
        "DoubleProperty",
        "NameProperty",
        "ByteProperty",
        "EnumProperty",
        "ObjectProperty",
        "SoftObjectProperty",
        "ArrayProperty",
        "StructProperty",
        "StrProperty",
        "TextProperty",
    }:
        return "uasset_property_tag"
    if type_name == "MapProperty":
        return "uasset_property_tag_raw_map"
    return "uasset_property_tag_unknown"


def property_parse_confidence(type_name: str, parsed: dict[str, object]) -> str:
    if parsed.get("error"):
        return "low"
    if type_name in {
        "IntProperty",
        "Int64Property",
        "BoolProperty",
        "FloatProperty",
        "DoubleProperty",
        "NameProperty",
        "ByteProperty",
        "EnumProperty",
        "ObjectProperty",
        "SoftObjectProperty",
        "StrProperty",
        "TextProperty",
    }:
        return "high" if parsed.get("value") not in {None, ""} else "medium"
    if type_name == "ArrayProperty":
        value = parsed.get("value")
        return "medium" if isinstance(value, list) and value else "low"
    if type_name == "StructProperty":
        return "medium" if parsed.get("member_name") or parsed.get("guid") else "low"
    return "low"


def extract_guid_value(data: bytes, names: list[str], property_name: str) -> str:
    positions = fname_positions(data, names, {property_name})
    if not positions:
        return ""
    pos = positions[0][0]
    for value_pos in range(pos + 24, min(len(data) - 15, pos + 96)):
        raw = data[value_pos : value_pos + 16]
        if raw and raw != b"\x00" * 16:
            return guid_to_text(raw)
    return ""


def parse_property_block_value(
    export_data: bytes,
    block: dict[str, object],
    names: list[str],
    imports: list[dict[str, object]],
    exports: list[dict[str, object]],
) -> dict[str, object]:
    pos = int(block.get("offset") or 0)
    end = int(block.get("end") or len(export_data))
    chunk = export_data[pos:end]
    name = str(block.get("name") or "")
    type_name = str(block.get("type") or "")
    value_positions = [pos + 25, pos + 24, pos + 26, pos + 29, max(pos, end - 4)]
    item: dict[str, object] = {
        "name": name,
        "type": type_name,
        "offset": pos,
        "end": end,
        "raw_offsets": raw_offsets(pos, end),
        "raw_size": len(chunk),
        "source": property_parse_source(type_name),
    }
    try:
        if type_name in {"IntProperty", "Int64Property"}:
            value = _read_i32_candidate(export_data, value_positions, limit=50_000_000)
            item["value"] = value
        elif type_name == "BoolProperty":
            item["value"] = _read_bool_candidate(export_data, value_positions)
        elif type_name == "FloatProperty":
            item["value"] = _read_float_candidate(export_data, value_positions)
        elif type_name == "DoubleProperty":
            item["value"] = _read_double_candidate(export_data, value_positions)
        elif type_name in {"NameProperty", "ByteProperty", "EnumProperty"}:
            item["value"] = _read_fname_candidate(export_data, names, value_positions)
            item["fname"] = item["value"]
        elif type_name in {"ObjectProperty", "SoftObjectProperty"}:
            ref = _read_i32_candidate(export_data, value_positions, limit=50_000_000)
            item["value"] = ref
            item["package_index"] = ref
            item["object"] = object_ref_name(int(ref or 0), imports, exports)
        elif type_name == "ArrayProperty":
            refs, array_offset = extract_object_ref_array(chunk, imports, exports)
            item["value"] = refs
            item["array_offset"] = array_offset
            item["element_kind"] = "FPackageIndex"
            item["objects"] = [object_ref_name(value, imports, exports) for value in refs[:500]]
        elif type_name == "StructProperty":
            member_name = extract_member_reference_name(chunk, names)
            if member_name:
                item["member_name"] = member_name
            guid = extract_guid_value(chunk, names, "MemberGuid")
            if guid:
                item["guid"] = guid
        elif type_name in {"StrProperty", "TextProperty"}:
            text = ""
            for value_pos in value_positions:
                try:
                    text, _next = _read_fstring(export_data, value_pos)
                    if text:
                        break
                except Exception:
                    continue
            item["value"] = text
        elif type_name == "MapProperty":
            item["value"] = {"raw_size": len(chunk), "parsed": False}
        else:
            item["value"] = {"raw_size": len(chunk), "parsed": False}
    except Exception as exc:
        item["error"] = str(exc)
    item["confidence"] = property_parse_confidence(type_name, item)
    return item


def parse_export_properties(
    export_data: bytes,
    names: list[str],
    imports: list[dict[str, object]],
    exports: list[dict[str, object]],
) -> tuple[dict[str, dict[str, object]], list[str]]:
    warnings: list[str] = []
    properties: dict[str, dict[str, object]] = {}
    blocks = parse_top_property_blocks(export_data, names)
    for block in blocks:
        name = str(block.get("name") or "")
        if not name:
            continue
        parsed = parse_property_block_value(export_data, block, names, imports, exports)
        if parsed.get("error"):
            warnings.append(f"{name}: {parsed.get('error')}")
        properties[name] = parsed
    return properties, warnings


def cdo_export_for_package(exports: list[dict[str, object]], asset_name: str) -> dict[str, object] | None:
    expected = f"Default__{asset_name}_C"
    for export in exports:
        object_name = str(export.get("object_name") or export.get("display_name") or "")
        if object_name == expected:
            return export
    for export in exports:
        object_name = str(export.get("object_name") or export.get("display_name") or "")
        if object_name.startswith("Default__") and asset_name in object_name:
            return export
    for export in exports:
        object_name = str(export.get("object_name") or export.get("display_name") or "")
        if object_name.startswith("Default__"):
            return export
    return None


def cdo_property_tag_blocks(export_data: bytes, names: list[str]) -> list[dict[str, object]]:
    starts: list[dict[str, object]] = []
    seen: set[int] = set()
    for pos in range(0, max(0, len(export_data) - 16)):
        name_info = _fname_at(export_data, pos, names)
        type_info = _fname_at(export_data, pos + 8, names) if name_info else None
        if not name_info or not type_info:
            continue
        property_name = name_info[0]
        type_name = type_info[0]
        if type_name not in UOBJECT_PROPERTY_TYPE_NAMES:
            continue
        if (
            not property_name
            or property_name == "None"
            or property_name in UOBJECT_PROPERTY_TYPE_NAMES
            or "/" in property_name
            or "\\" in property_name
        ):
            continue
        if pos in seen:
            continue
        seen.add(pos)
        starts.append({"name": property_name, "type": type_name, "offset": pos})
    starts.sort(key=lambda item: int(item.get("offset") or 0))
    for index, block in enumerate(starts):
        end = int(starts[index + 1].get("offset") or len(export_data)) if index + 1 < len(starts) else len(export_data)
        block["end"] = end
        block["raw_size"] = max(0, end - int(block.get("offset") or 0))
    return starts


def cdo_declared_value_size(export_data: bytes, pos: int, end: int, type_name: str) -> int:
    if type_name == "StructProperty":
        candidates = (pos + 44, pos + 20, pos + 16)
    else:
        candidates = (pos + 20, pos + 16, pos + 44)
    max_size = max(0, end - pos)
    for offset in candidates:
        if 0 <= offset <= len(export_data) - 4:
            value = _read_i32(export_data, offset)
            if 0 < value <= max_size and value < 10_000_000:
                return value
    return 0


def cdo_value_offset(export_data: bytes, block: dict[str, object], type_name: str) -> tuple[int, int]:
    pos = int(block.get("offset") or 0)
    end = int(block.get("end") or len(export_data))
    declared_size = cdo_declared_value_size(export_data, pos, end, type_name)
    if declared_size and end - declared_size > pos:
        return end - declared_size, declared_size
    fallback = pos + 41
    if fallback < end:
        return fallback, max(0, end - fallback)
    return end, 0


def _soft_object_path_for_index(paths: list[dict[str, object]], index: int) -> dict[str, object] | None:
    if 0 <= index < len(paths):
        item = paths[index]
        return item if isinstance(item, dict) else None
    return None


def parse_cdo_property_value(
    export_data: bytes,
    block: dict[str, object],
    names: list[str],
    imports: list[dict[str, object]],
    exports: list[dict[str, object]],
    soft_object_paths: list[dict[str, object]],
) -> dict[str, object]:
    name = str(block.get("name") or "")
    type_name = str(block.get("type") or "")
    pos = int(block.get("offset") or 0)
    end = int(block.get("end") or len(export_data))
    value_offset, declared_size = cdo_value_offset(export_data, block, type_name)
    item: dict[str, object] = {
        "name": name,
        "type": type_name,
        "offset": pos,
        "end": end,
        "value_offset": value_offset,
        "declared_size": declared_size,
        "raw_size": max(0, end - pos),
        "source": "uasset_cdo_property_tag",
    }
    try:
        if type_name == "DoubleProperty" and value_offset + 8 <= len(export_data):
            item["value"] = _read_f64(export_data, value_offset)
        elif type_name == "FloatProperty" and value_offset + 4 <= len(export_data):
            item["value"] = _read_f32(export_data, value_offset)
        elif type_name == "IntProperty" and value_offset + 4 <= len(export_data):
            item["value"] = _read_i32(export_data, value_offset)
        elif type_name == "Int64Property" and value_offset + 8 <= len(export_data):
            item["value"] = _read_i64(export_data, value_offset)
        elif type_name == "BoolProperty" and value_offset < len(export_data):
            item["value"] = bool(export_data[value_offset])
        elif type_name in {"NameProperty", "ByteProperty", "EnumProperty"}:
            value = _fname_at(export_data, value_offset, names)
            item["value"] = value[0] if value else ""
        elif type_name == "ObjectProperty" and value_offset + 4 <= len(export_data):
            ref = _read_i32(export_data, value_offset)
            item["value"] = ref
            item["package_index"] = ref
            item["object"] = object_ref_name(ref, imports, exports)
        elif type_name == "SoftObjectProperty" and value_offset + 4 <= len(export_data):
            path_index = _read_i32(export_data, value_offset)
            soft_path = _soft_object_path_for_index(soft_object_paths, path_index)
            item["value"] = str(soft_path.get("object_path") or "") if soft_path else path_index
            item["soft_object_path_index"] = path_index
            if soft_path:
                item["soft_object_path"] = soft_path
        elif type_name == "StructProperty":
            struct_info = _fname_at(export_data, pos + 20, names)
            struct_name = struct_info[0] if struct_info else ""
            item["struct"] = struct_name
            if struct_name in {"Vector2D", "Vector2d"} and value_offset + 16 <= len(export_data):
                item["value"] = {
                    "x": _read_f64(export_data, value_offset),
                    "y": _read_f64(export_data, value_offset + 8),
                }
            elif struct_name in {"Vector", "Rotator", "Color"} and value_offset + 24 <= len(export_data):
                item["value"] = {
                    "x": _read_f64(export_data, value_offset),
                    "y": _read_f64(export_data, value_offset + 8),
                    "z": _read_f64(export_data, value_offset + 16),
                }
            elif declared_size:
                item["value"] = {"struct": struct_name, "raw_size": declared_size, "parsed": False}
            else:
                item["value"] = {"struct": struct_name, "raw_size": max(0, end - value_offset), "parsed": False}
        elif type_name in {"StrProperty", "TextProperty"}:
            text, _next = _read_fstring(export_data, value_offset)
            item["value"] = text
        elif type_name in {"ArrayProperty", "MapProperty"}:
            item["value"] = {"raw_size": max(0, end - value_offset), "parsed": False}
        else:
            item["value"] = {"raw_size": max(0, end - value_offset), "parsed": False}
    except Exception as exc:
        item["error"] = str(exc)
    value = item.get("value")
    if item.get("error"):
        item["confidence"] = "low"
    elif type_name in {"ArrayProperty", "MapProperty"} or (isinstance(value, dict) and value.get("parsed") is False):
        item["confidence"] = "low"
    elif value is None or value == "":
        item["confidence"] = "medium"
    else:
        item["confidence"] = "high"
    return item


def read_uasset_class_defaults(package: dict[str, object], asset_name: str) -> dict[str, object]:
    names = package.get("names", [])
    imports = package.get("imports", [])
    exports = package.get("exports", [])
    soft_object_paths = package.get("soft_object_paths", [])
    if not isinstance(names, list) or not isinstance(imports, list) or not isinstance(exports, list):
        return {
            "schema": UASSET_CLASS_DEFAULTS_SCHEMA,
            "loaded": False,
            "warnings": ["Package maps were not available."],
            "variables": {},
            "classDefaults": {},
            "properties": [],
        }
    cdo_export = cdo_export_for_package(exports, asset_name)
    if not cdo_export:
        return {
            "schema": UASSET_CLASS_DEFAULTS_SCHEMA,
            "loaded": False,
            "warnings": ["No Default__ class default object export was found."],
            "variables": {},
            "classDefaults": {},
            "properties": [],
        }
    export_data = export_data_bytes(package, cdo_export)
    if not export_data:
        return {
            "schema": UASSET_CLASS_DEFAULTS_SCHEMA,
            "loaded": False,
            "default_object": cdo_export.get("object_name", ""),
            "warnings": ["Default object serialized data is not available."],
            "variables": {},
            "classDefaults": {},
            "properties": [],
        }
    soft_paths = [item for item in soft_object_paths if isinstance(item, dict)]
    properties: list[dict[str, object]] = []
    variables: dict[str, object] = {}
    warnings: list[str] = []
    for block in cdo_property_tag_blocks(export_data, names):
        prop = parse_cdo_property_value(export_data, block, names, imports, exports, soft_paths)
        properties.append(prop)
        if prop.get("error"):
            warnings.append(f"{prop.get('name')}: {prop.get('error')}")
        value = prop.get("value")
        if value is None or (isinstance(value, dict) and value.get("parsed") is False):
            continue
        variables[str(prop.get("name") or "")] = {
            "value": value,
            "type": prop.get("type", ""),
            "source": "uasset_cdo",
            "confidence": prop.get("confidence", ""),
        }
    return {
        "schema": UASSET_CLASS_DEFAULTS_SCHEMA,
        "loaded": True,
        "asset_name": asset_name,
        "default_object": cdo_export.get("object_name") or cdo_export.get("display_name") or "",
        "export_index": cdo_export.get("index"),
        "property_count": len(properties),
        "variable_count": len(variables),
        "variables": variables,
        "classDefaults": {},
        "properties": properties,
        "warnings": warnings,
    }


def render_uasset_class_defaults_report(payload: dict[str, object]) -> str:
    lines = [
        "# UAsset Class Defaults Report",
        "",
        f"- Asset: {payload.get('asset_name') or '-'}",
        f"- Loaded: {'yes' if payload.get('loaded') else 'no'}",
        f"- Default object: {payload.get('default_object') or '-'}",
        f"- Parsed properties: {payload.get('property_count', 0)}",
        f"- Usable variables: {payload.get('variable_count', 0)}",
        "",
        "## Variables",
        "",
        "| Name | Type | Value | Confidence |",
        "| --- | --- | --- | --- |",
    ]
    variables = payload.get("variables", {})
    if isinstance(variables, dict) and variables:
        for name, value in sorted(variables.items()):
            if isinstance(value, dict):
                display_value = value.get("value", "")
                type_name = value.get("type", "")
                confidence = value.get("confidence", "")
            else:
                display_value = value
                type_name = ""
                confidence = ""
            lines.append(f"| {name} | {type_name} | {json.dumps(display_value, ensure_ascii=False)} | {confidence} |")
    else:
        lines.append("| none |  |  |  |")
    warnings = payload.get("warnings", [])
    if isinstance(warnings, list) and warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings[:200])
    lines.append("")
    return "\n".join(lines)


def property_scalar(properties: dict[str, dict[str, object]], key: str) -> object:
    item = properties.get(key, {})
    if isinstance(item, dict):
        return item.get("value")
    return None


def property_member_name(properties: dict[str, dict[str, object]], key: str) -> str:
    item = properties.get(key, {})
    if isinstance(item, dict):
        return str(item.get("member_name") or item.get("value") or "")
    return ""


def property_object_refs(properties: dict[str, dict[str, object]], key: str) -> list[int]:
    item = properties.get(key, {})
    value = item.get("value") if isinstance(item, dict) else []
    if isinstance(value, list):
        return [int(ref) for ref in value if isinstance(ref, int)]
    return []


def node_semantic_kind(class_name: str) -> str:
    mapping = {
        "K2Node_CallFunction": "call_function",
        "K2Node_CallArrayFunction": "array_function",
        "K2Node_CallParentFunction": "parent_function_call",
        "K2Node_VariableGet": "variable_get",
        "K2Node_VariableSet": "variable_set",
        "K2Node_Event": "event",
        "K2Node_CustomEvent": "custom_event",
        "K2Node_FunctionEntry": "function_entry",
        "K2Node_FunctionResult": "function_result",
        "K2Node_IfThenElse": "branch",
        "K2Node_ExecutionSequence": "sequence",
        "K2Node_DynamicCast": "dynamic_cast",
        "K2Node_MacroInstance": "macro",
        "K2Node_AddDelegate": "delegate_bind",
        "K2Node_ComponentBoundEvent": "component_event",
        "K2Node_Knot": "reroute",
        "K2Node_Timeline": "timeline",
        "K2Node_CommutativeAssociativeBinaryOperator": "operator",
        "K2Node_Self": "self",
        "K2Node_Tunnel": "tunnel",
        "K2Node_EnumEquality": "enum_equality",
        "K2Node_MakeStruct": "make_struct",
        "K2Node_GetArrayItem": "get_array_item",
        "K2Node_MakeArray": "make_array",
        "K2Node_Composite": "collapsed_graph",
        "K2Node_SwitchEnum": "switch_enum",
        "K2Node_BreakStruct": "break_struct",
        "K2Node_Select": "select",
        "K2Node_ConvertAsset": "asset_conversion",
        "K2Node_AsyncAction": "async_action",
        "K2Node_SetFieldsInStruct": "set_struct_fields",
    }
    return mapping.get(class_name, "node")


def build_node_semantic(
    *,
    class_name: str,
    properties: dict[str, dict[str, object]],
    pins: list[PinInfo],
) -> dict[str, object]:
    kind = node_semantic_kind(class_name)
    semantic: dict[str, object] = {
        "kind": kind,
        "source": "uasset_node_semantic_reader" if class_name in SUPPORTED_SEMANTIC_NODE_CLASSES else "generic_uasset_node",
        "confidence": "medium" if class_name in SUPPORTED_SEMANTIC_NODE_CLASSES else "low",
        "inputs": [
            {
                "name": pin.name,
                "category": pin.category,
                "default": pin.default,
                "default_object": pin.default_object,
                "confidence": pin.confidence,
            }
            for pin in pins
            if pin.direction != "EGPD_Output"
        ],
        "outputs": [
            {
                "name": pin.name,
                "category": pin.category,
                "link_count": len(pin.links),
                "confidence": pin.confidence,
            }
            for pin in pins
            if pin.direction == "EGPD_Output"
        ],
        "exec_in": [pin.name for pin in pins if pin.category == "exec" and pin.direction != "EGPD_Output"],
        "exec_out": [pin.name for pin in pins if pin.category == "exec" and pin.direction == "EGPD_Output"],
    }
    function = property_member_name(properties, "FunctionReference")
    variable = property_member_name(properties, "VariableReference")
    event = property_member_name(properties, "EventReference") or str(property_scalar(properties, "CustomFunctionName") or "")
    delegate = property_member_name(properties, "DelegateReference")
    macro = property_member_name(properties, "MacroGraphReference")
    if function:
        semantic["function"] = function
    if variable:
        semantic["variable"] = variable
    if event:
        semantic["event"] = event
    if delegate:
        semantic["delegate"] = delegate
    if macro:
        semantic["macro"] = macro
    if class_name == "K2Node_IfThenElse":
        semantic["condition_pin"] = "Condition"
    elif class_name == "K2Node_ExecutionSequence":
        semantic["sequence_outputs"] = [pin.name for pin in pins if pin.direction == "EGPD_Output" and pin.category == "exec"]
    elif class_name == "K2Node_DynamicCast":
        semantic["cast_outputs"] = [pin.name for pin in pins if pin.direction == "EGPD_Output"]
    elif class_name in {"K2Node_FunctionEntry", "K2Node_FunctionResult"}:
        semantic["signature_pins"] = [pin.name for pin in pins if pin.category != "exec"]
    elif class_name == "K2Node_CommutativeAssociativeBinaryOperator":
        semantic["operator_inputs"] = [pin.name for pin in pins if pin.direction != "EGPD_Output"]
    elif class_name in {"K2Node_MakeStruct", "K2Node_BreakStruct", "K2Node_SetFieldsInStruct"}:
        semantic["struct_fields"] = [pin.name for pin in pins if pin.category != "exec"]
    elif class_name in {"K2Node_MakeArray", "K2Node_GetArrayItem", "K2Node_CallArrayFunction"}:
        semantic["array_pins"] = [pin.name for pin in pins if pin.category != "exec"]
    elif class_name in {"K2Node_SwitchEnum", "K2Node_Select"}:
        semantic["selection_pins"] = [pin.name for pin in pins]
    return semantic


def node_confidence(
    *,
    class_name: str,
    properties: dict[str, dict[str, object]],
    pins: list[PinInfo],
    warnings: Iterable[str],
) -> str:
    levels = [
        str(item.get("confidence") or "")
        for key, item in properties.items()
        if key in CORE_RECOVERABLE_PROPERTY_NAMES and isinstance(item, dict)
    ]
    if pins:
        pin_levels = [pin.confidence for pin in pins if pin.confidence]
        if pin_levels:
            levels.append(confidence_floor(*pin_levels))
    if class_name in SUPPORTED_SEMANTIC_NODE_CLASSES:
        levels.append("high" if class_name in NODE_SEMANTICS else "medium")
    else:
        levels.append("medium")
    warning_count = len([warning for warning in warnings if str(warning).strip()])
    if warning_count == 0:
        levels.append("high")
    elif warning_count <= 4:
        levels.append("medium")
    else:
        levels.append("low")
    return confidence_floor(*levels)


def find_property_terminator(export_data: bytes, names: list[str], properties: dict[str, dict[str, object]]) -> int:
    max_property_pos = max((int(item.get("offset") or 0) for item in properties.values()), default=0)
    none_positions = [pos for pos, _name in fname_positions(export_data, names, {"None"})]
    for pos in none_positions:
        if pos > max_property_pos:
            return pos
    return -1


def _is_pin_name_candidate(name: str) -> bool:
    if not name or name in UOBJECT_PROPERTY_TYPE_NAMES or name in TOP_UOBJECT_PROPERTY_NAMES:
        return False
    lowered = name.lower()
    if lowered in PIN_CATEGORY_NAMES or lowered in {"none", "axis", "local pc"}:
        return False
    if name.startswith(("/Game", "/Script")):
        return False
    if len(name) > 80:
        return False
    return any(character.isalpha() for character in name)


def _all_fname_candidates(data: bytes, names: list[str], start: int, end: int) -> list[tuple[int, str]]:
    results: list[tuple[int, str]] = []
    upper = min(end, len(data) - 8)
    for pos in range(max(0, start), max(0, upper)):
        item = _fname_at(data, pos, names)
        if item and item[2] == 0:
            results.append((pos, item[0]))
    return results


def infer_pin_direction(pin_name: str, category: str, node_type: str) -> str:
    lowered = pin_name.lower()
    if lowered in PIN_OUTPUT_NAMES:
        return "EGPD_Output"
    if lowered in {"execute", "exec"}:
        return "EGPD_Input"
    if node_type == "K2Node_VariableGet" and lowered not in {"self"}:
        return "EGPD_Output"
    if node_type == "K2Node_FunctionEntry" and lowered == "then":
        return "EGPD_Output"
    if node_type == "K2Node_Event" and category == "exec":
        return "EGPD_Output"
    return "EGPD_Input"


def extract_pin_guid_from_region(region: bytes) -> str:
    candidates = guid_candidates_from_region(region, limit=128)
    return candidates[0] if candidates else ""


def infer_pin_subcategory(region: bytes, names: list[str], category_pos: int, region_start: int) -> str:
    local_category_pos = max(0, category_pos - region_start)
    candidates = _all_fname_candidates(region, names, local_category_pos + 8, min(len(region), local_category_pos + 120))
    for _pos, name in candidates:
        lowered = name.lower()
        if lowered in PIN_CATEGORY_NAMES or lowered in {"none"}:
            continue
        if _is_pin_name_candidate(name):
            return name
    return ""


def infer_pin_container_type(region: bytes, names: list[str]) -> str:
    for _pos, name in _all_fname_candidates(region, names, 0, min(len(region), 220)):
        if name in {"Array", "Set", "Map"}:
            return name
    return "None"


def extract_pin_default_value(region: bytes) -> str:
    for pos in range(0, max(0, min(len(region) - 4, 160))):
        try:
            value, _next = _read_fstring(region, pos)
        except Exception:
            continue
        if value and len(value) <= 500 and not any(ord(ch) < 9 for ch in value):
            return value
    return ""


def extract_default_object_name(
    region: bytes,
    imports: list[dict[str, object]],
    exports: list[dict[str, object]],
    graph_refset: set[int],
) -> str:
    for pos in range(0, max(0, len(region) - 4)):
        value = _read_i32(region, pos)
        if value in graph_refset or value == 0:
            continue
        if _valid_package_index(value, imports, exports):
            name = object_ref_name(value, imports, exports)
            if name:
                return name
    return ""


def classify_pin_link(pin: PinInfo, link: dict[str, object], graph_refset: set[int]) -> dict[str, object]:
    target_package_index = int(link.get("target_package_index") or 0)
    if target_package_index and target_package_index not in graph_refset:
        status = "cross_graph_or_external"
    elif link.get("target_node"):
        status = "resolved_node"
    else:
        status = "unresolved"
    return {
        "status": status,
        "kind": "exec" if pin.category == "exec" else "data",
        "confidence": str(link.get("confidence") or pin.confidence or "low"),
    }


def parse_custom_pins(
    export_data: bytes,
    names: list[str],
    properties: dict[str, dict[str, object]],
    *,
    node_export: dict[str, object],
    graph_refset: set[int],
    imports: list[dict[str, object]],
    exports: list[dict[str, object]],
) -> tuple[list[PinInfo], list[str]]:
    warnings: list[str] = []
    terminator = find_property_terminator(export_data, names, properties)
    if terminator < 0 or terminator + 16 > len(export_data):
        return [], ["Could not locate custom pin data after property tags."]
    pin_count = -1
    count_offset = -1
    for offset in (12, 8, 9, 10, 11, 13, 14, 15, 16):
        if terminator + offset + 4 <= len(export_data):
            value = _read_i32(export_data, terminator + offset)
            if 0 <= value <= 2000:
                pin_count = value
                count_offset = terminator + offset
                break
    if pin_count < 0 or pin_count > 2000:
        return [], [f"Unusable custom pin count near property terminator: {pin_count}."]
    if pin_count == 0:
        return [], []

    search_start = count_offset + 4
    candidates = _all_fname_candidates(export_data, names, search_start, len(export_data))
    category_positions = [(pos, name.lower()) for pos, name in candidates if name.lower() in PIN_CATEGORY_NAMES]
    used_name_positions: set[int] = set()
    pin_markers: list[tuple[int, str, str, int]] = []
    for category_pos, category in category_positions:
        previous = [
            (pos, name)
            for pos, name in candidates
            if pos < category_pos and pos not in used_name_positions and category_pos - pos <= 96 and _is_pin_name_candidate(name)
        ]
        if not previous:
            continue
        pin_name_pos, pin_name = previous[-1]
        used_name_positions.add(pin_name_pos)
        pin_markers.append((pin_name_pos, pin_name, category, category_pos))
        if len(pin_markers) >= pin_count:
            break
    pin_markers.sort(key=lambda item: item[0])
    if not pin_markers:
        return [], [f"Pin count is {pin_count}, but no pin names could be recovered."]

    pins: list[PinInfo] = []
    self_package_index = int(node_export.get("package_index") or 0)
    node_type = str(node_export.get("class_name") or "")
    for index, (name_pos, pin_name, category, category_pos) in enumerate(pin_markers):
        region_end = pin_markers[index + 1][0] if index + 1 < len(pin_markers) else len(export_data)
        region = export_data[name_pos:region_end]
        links: list[dict[str, object]] = []
        for pos in range(name_pos, max(name_pos, region_end - 4)):
            value = _read_i32(export_data, pos)
            if value in graph_refset and value != self_package_index:
                target_index = value - 1
                if 0 <= target_index < len(exports):
                    target = exports[target_index]
                    target_name = str(target.get("display_name") or target.get("object_name") or "")
                    candidate = {
                        "target_node": target_name,
                        "target_pin_id": "",
                        "target_package_index": value,
                        "source_offset": pos,
                        "target_pin_id_candidates": guid_candidates_from_region(export_data[max(name_pos, pos - 32) : min(region_end, pos + 128)], limit=160),
                        "source": "uasset_pin_package_index_scan",
                        "confidence": "medium",
                        "resolution_status": "resolved_node" if target_name else "unresolved",
                    }
                    if target_name and candidate not in links:
                        links.append(candidate)
        linked_raw = " ".join(f"{item['target_node']} {item['target_pin_id']}".strip() for item in links)
        pin_guid = extract_pin_guid_from_region(region[: max(0, min(len(region), 96))])
        default_value = extract_pin_default_value(region)
        default_object = extract_default_object_name(region, imports, exports, graph_refset)
        direction = infer_pin_direction(pin_name, category, node_type)
        pin_confidence = "medium"
        pin_warnings: list[str] = []
        if not pin_guid:
            pin_warnings.append("PinId/PersistentGuid was not structurally decoded; a stable synthetic id is used.")
        pin_id = pin_guid or f"{node_export.get('display_name') or node_export.get('object_name')}_pin_{index + 1}"
        subcategory = infer_pin_subcategory(region, names, category_pos, name_pos)
        container_type = infer_pin_container_type(region, names)
        pin_type = {
            "PinCategory": category,
            "PinSubCategory": subcategory,
            "PinSubCategoryObject": default_object,
            "ContainerType": container_type,
            "bIsReference": False,
            "bIsConst": False,
            "source": "uasset_pin_type_scan",
            "confidence": "medium" if category else "low",
        }
        pin_resolution = {
            "status": "resolved_node" if links else "no_links_recovered",
            "link_count": len(links),
        }
        pin = PinInfo(
            id=pin_id,
            name=pin_name,
            direction=direction,
            category=category,
            pin_type=pin_type,
            default=default_value,
            default_object=default_object,
            persistent_guid=pin_guid,
            linked_to_raw=linked_raw,
            links=links,  # type: ignore[arg-type]
            source="uasset_custom_pin_scan",
            confidence=pin_confidence,
            warnings=pin_warnings,
            raw_offsets=raw_offsets(name_pos, region_end),
            resolution=pin_resolution,
        )
        for link in links:
            link.update(classify_pin_link(pin, link, graph_refset))
        pins.append(
            pin
        )
    if len(pins) < pin_count:
        warnings.append(f"Recovered {len(pins)} of {pin_count} custom pins.")
    return pins, warnings


def is_blueprint_node_export(export: dict[str, object]) -> bool:
    class_name = str(export.get("class_name") or "")
    return class_name.startswith(NODE_CLASS_PREFIXES)


def node_info_from_export(
    *,
    node_export: dict[str, object],
    properties: dict[str, dict[str, object]],
    pins: list[PinInfo],
    index: int,
    warnings: Iterable[str] = (),
) -> NodeInfo:
    class_name = str(node_export.get("class_name") or "")
    function = property_member_name(properties, "FunctionReference")
    variable = property_member_name(properties, "VariableReference")
    event = property_member_name(properties, "EventReference") or str(property_scalar(properties, "CustomFunctionName") or "")
    delegate = property_member_name(properties, "DelegateReference")
    macro = property_member_name(properties, "MacroGraphReference")
    comment = str(property_scalar(properties, "NodeComment") or property_scalar(properties, "CommentText") or "")
    x_value = property_scalar(properties, "NodePosX")
    y_value = property_scalar(properties, "NodePosY")
    node_guid = ""
    guid_item = properties.get("NodeGuid", {})
    if isinstance(guid_item, dict):
        node_guid = str(guid_item.get("guid") or "")
    warning_list = [str(warning) for warning in warnings if str(warning)]
    serial_location = node_export.get("serial_location", {})
    offset_start = 0
    offset_end = 0
    if isinstance(serial_location, dict):
        offset_start = int(serial_location.get("offset") or 0)
        offset_end = offset_start + int(serial_location.get("size") or 0)
    semantic = build_node_semantic(class_name=class_name, properties=properties, pins=pins)
    confidence = node_confidence(class_name=class_name, properties=properties, pins=pins, warnings=warning_list)
    return NodeInfo(
        index=index,
        class_name=class_name,
        node_type=class_name,
        name=str(node_export.get("display_name") or node_export.get("object_name") or f"Export_{node_export.get('index')}"),
        export_path=class_name,
        node_guid=node_guid,
        function=function,
        variable=variable,
        event=event,
        delegate=delegate,
        macro=macro,
        comment=comment,
        node_pos_x=int(x_value) if isinstance(x_value, int) else None,
        node_pos_y=int(y_value) if isinstance(y_value, int) else None,
        properties={
            key: {
                sub_key: sub_value
                for sub_key, sub_value in value.items()
                if sub_key not in {"raw_bytes"}
            }
            for key, value in properties.items()
        },
        semantic=semantic,
        pins=pins,
        source="uasset_binary",
        confidence=confidence,
        warnings=warning_list,
        raw_offsets=raw_offsets(offset_start, offset_end),
    )


def graph_type_from_export(graph_export: dict[str, object]) -> str:
    hint = str(graph_export.get("type_hint") or graph_export.get("graph_kind") or "")
    if hint == "FunctionGraph":
        return "Function"
    if hint:
        return hint
    name = str(graph_export.get("object_name") or "")
    return candidate_type_hint(name)


def classify_graph_failure(graph: dict[str, object]) -> list[str]:
    categories: list[str] = []
    node_count = int(graph.get("node_count") or 0)
    pin_count = int(graph.get("pin_count") or 0)
    link_count = int(graph.get("link_count") or 0)
    warnings = [str(item).lower() for item in graph.get("warnings", []) if isinstance(item, str)]
    nodes = [item for item in graph.get("nodes", []) if isinstance(item, dict)]
    if node_count == 0:
        categories.append("need_manual_clipboard")
    if node_count and pin_count == 0:
        categories.append("need_pin_layout_rule")
    if pin_count and link_count == 0:
        categories.append("need_cross_graph_resolve")
    if any("pin" in warning or "custom" in warning for warning in warnings):
        categories.append("need_pin_layout_rule")
    if any("unsupported package index" in warning for warning in warnings):
        categories.append("need_cross_graph_resolve")
    if any(str(node.get("class") or "") not in SUPPORTED_SEMANTIC_NODE_CLASSES for node in nodes[:200]):
        categories.append("need_node_reader")
    return list(dict.fromkeys(categories)) or ["need_manual_clipboard"]


def classify_graph_status(nodes: list[NodeInfo], warnings: Iterable[str]) -> str:
    if not nodes:
        return "failed"
    pin_count = sum(len(node.pins) for node in nodes)
    link_count = sum(node.link_count for node in nodes)
    if pin_count == 0:
        return "needs_clipboard"
    coverage = graph_coverage(nodes)
    if float(coverage.get("node_pin_coverage") or 0) < 0.75:
        return "partial"
    if link_count == 0:
        return "heuristic"
    return "complete"


def graph_confidence(nodes: list[NodeInfo], warnings: Iterable[str]) -> str:
    if not nodes:
        return "low"
    levels = [node.confidence for node in nodes]
    warning_count = len([warning for warning in warnings if str(warning).strip()])
    if warning_count == 0:
        levels.append("high")
    elif warning_count <= max(4, len(nodes) // 4):
        levels.append("medium")
    else:
        levels.append("low")
    return confidence_floor(*levels)


def graph_coverage(nodes: list[NodeInfo]) -> dict[str, object]:
    node_count = len(nodes)
    pin_nodes = sum(1 for node in nodes if node.pins)
    linked_nodes = sum(1 for node in nodes if node.link_count)
    pins = [pin for node in nodes for pin in node.pins]
    exec_pins = [pin for pin in pins if pin.category == "exec"]
    data_pins = [pin for pin in pins if pin.category != "exec"]
    links = [link for node in nodes for pin in node.pins for link in pin.links]
    exec_links = [link for node in nodes for pin in node.pins if pin.category == "exec" for link in pin.links]
    data_links = [link for node in nodes for pin in node.pins if pin.category != "exec" for link in pin.links]
    return {
        "nodes_with_pins": pin_nodes,
        "nodes_with_links": linked_nodes,
        "node_pin_coverage": round(pin_nodes / node_count, 4) if node_count else 0,
        "node_link_coverage": round(linked_nodes / node_count, 4) if node_count else 0,
        "pin_count": len(pins),
        "exec_pin_count": len(exec_pins),
        "data_pin_count": len(data_pins),
        "link_count": len(links),
        "exec_link_count": len(exec_links),
        "data_link_count": len(data_links),
    }


def synthetic_pin_type(category: str) -> dict[str, object]:
    return {
        "PinCategory": category,
        "PinSubCategory": "",
        "PinSubCategoryObject": "",
        "ContainerType": "None",
        "bIsReference": False,
        "bIsConst": False,
        "source": "uasset_reverse_link_synthesis",
        "confidence": "medium",
    }


def synthetic_boundary_pin_name(category: str, source_pin: PinInfo, used_names: set[str]) -> str:
    if category == "exec":
        base = "then"
    else:
        base = source_pin.name or category or "value"
    name = base
    suffix = 2
    while name in used_names:
        name = f"{base}_{suffix}"
        suffix += 1
    used_names.add(name)
    return name


def synthesize_boundary_pins_from_incoming_links(nodes: list[NodeInfo]) -> list[str]:
    by_name = {node.name: node for node in nodes if node.name}
    incoming: dict[str, list[tuple[NodeInfo, PinInfo, dict[str, object]]]] = defaultdict(list)
    for source_node in nodes:
        for source_pin in source_node.pins:
            for link in source_pin.links:
                target = by_name.get(str(link.get("target_node") or ""))
                if not target or target is source_node or target.pins:
                    continue
                incoming[target.name].append((source_node, source_pin, link))

    warnings: list[str] = []
    for target_name, refs in sorted(incoming.items()):
        target = by_name.get(target_name)
        if not target or target.pins:
            continue
        used_names: set[str] = set()
        synthesized: list[PinInfo] = []
        seen: set[tuple[str, str, str]] = set()
        for source_node, source_pin, _link in refs[:16]:
            category = source_pin.category or ("exec" if source_pin.name in {"execute", "then"} else "wildcard")
            marker = (category, source_node.name, source_pin.name)
            if marker in seen:
                continue
            seen.add(marker)
            pin_name = synthetic_boundary_pin_name(category, source_pin, used_names)
            pin_id = f"{target.name}_synth_pin_{len(synthesized) + 1}"
            reverse_link = {
                "target_node": source_node.name,
                "target_pin_id": source_pin.id,
                "target_pin": source_pin.name,
                "target_package_index": 0,
                "source_offset": 0,
                "source": "uasset_reverse_link_synthesis",
                "confidence": "medium",
                "resolution_status": "resolved_pin" if source_pin.id else "resolved_pin_heuristic",
                "status": "resolved_node",
                "kind": "exec" if category == "exec" else "data",
                "target_node_guid": source_node.node_guid,
            }
            synthesized.append(
                PinInfo(
                    id=pin_id,
                    name=pin_name,
                    direction="EGPD_Output",
                    category=category,
                    pin_type=synthetic_pin_type(category),
                    linked_to_raw=source_node.name,
                    links=[reverse_link],  # type: ignore[list-item]
                    source="uasset_reverse_link_synthesis",
                    confidence="medium",
                    warnings=[
                        "Pin was synthesized from an incoming LinkedTo reference because this boundary node did not expose decoded pins."
                    ],
                    resolution={"status": "synthesized_from_incoming_link", "link_count": 1},
                )
            )
        if synthesized:
            target.pins.extend(synthesized)
            target.semantic = build_node_semantic(
                class_name=target.class_name,
                properties=target.properties if isinstance(target.properties, dict) else {},
                pins=target.pins,
            )
            target.confidence = confidence_floor(target.confidence, "medium")
            message = f"{target.name}: Synthesized {len(synthesized)} boundary pins from incoming LinkedTo references."
            target.warnings.append(message)
            warnings.append(message)
    return warnings


def is_complete_empty_graph(
    nodes: list[NodeInfo],
    node_refs: list[int],
    graph_warnings: list[str],
    *,
    graph_name: str,
    graph_type: str,
) -> bool:
    if not graph_warnings and not nodes and not node_refs:
        return True
    if len(nodes) != 1:
        return False
    node = nodes[0]
    if node.pins or node.link_count:
        return False
    if node.class_name != "K2Node_FunctionEntry":
        return False
    return graph_type == "ConstructionScript" or graph_name == "UserConstructionScript" or node.function == "UserConstructionScript"


def target_pin_candidates(target: NodeInfo, source_pin: PinInfo) -> list[PinInfo]:
    wants_exec = source_pin.category == "exec"
    input_pins = [pin for pin in target.pins if pin.direction != "EGPD_Output"]
    output_pins = [pin for pin in target.pins if pin.direction == "EGPD_Output"]
    candidates = input_pins or target.pins
    if wants_exec:
        exec_inputs = [pin for pin in candidates if pin.category == "exec"]
        if exec_inputs:
            return exec_inputs
    else:
        same_category = [pin for pin in candidates if pin.category == source_pin.category and pin.category != "exec"]
        if same_category:
            return same_category
        data_inputs = [pin for pin in candidates if pin.category != "exec"]
        if data_inputs:
            return data_inputs
    if "Knot" in target.node_type:
        opposite = [pin for pin in output_pins if pin.category == source_pin.category]
        if opposite:
            return opposite
    return candidates


def resolve_graph_link_target_pins(nodes: list[NodeInfo]) -> dict[str, int]:
    by_name = {node.name: node for node in nodes if node.name}
    exact = 0
    heuristic = 0
    unresolved = 0
    for source_node in nodes:
        for source_pin in source_node.pins:
            for link in source_pin.links:
                target = by_name.get(str(link.get("target_node") or ""))
                if not target:
                    link["resolution_status"] = "cross_graph_or_missing_node" if link.get("target_node") else "unresolved"
                    unresolved += 1
                    continue
                if link.get("target_pin_id"):
                    link["resolution_status"] = "resolved_pin"
                    exact += 1
                    continue
                target_pin_by_id = {pin.id: pin for pin in target.pins if pin.id}
                target_pin: PinInfo | None = None
                for candidate_id in link.get("target_pin_id_candidates", []) or []:
                    candidate = target_pin_by_id.get(str(candidate_id))
                    if candidate:
                        target_pin = candidate
                        break
                if target_pin:
                    link["target_pin_id"] = target_pin.id
                    link["target_pin"] = target_pin.name
                    link["target_node_guid"] = target.node_guid
                    link["resolution_status"] = "resolved_pin"
                    link["confidence"] = "high"
                    exact += 1
                    continue
                candidates = target_pin_candidates(target, source_pin)
                if candidates:
                    target_pin = candidates[0]
                    link["target_pin_id"] = target_pin.id
                    link["target_pin"] = target_pin.name
                    link["target_node_guid"] = target.node_guid
                    link["resolution_status"] = "resolved_pin_heuristic"
                    link["confidence"] = confidence_floor(str(link.get("confidence") or "medium"), "medium")
                    heuristic += 1
                else:
                    link["resolution_status"] = "node_resolved_pin_unknown"
                    unresolved += 1
    return {
        "resolved_pin": exact,
        "resolved_pin_heuristic": heuristic,
        "unresolved": unresolved,
    }


def build_pin_link_payload(read_payload: dict[str, object]) -> dict[str, object]:
    graph_summaries: list[dict[str, object]] = []
    resolution_counts: Counter[str] = Counter()
    kind_counts: Counter[str] = Counter()
    for graph in read_payload.get("graphs", []):
        if not isinstance(graph, dict):
            continue
        graph_payload = graph.get("payload", {})
        if not isinstance(graph_payload, dict):
            graph_payload = {}
        node_names = {
            str(node.get("name") or "")
            for node in graph_payload.get("nodes", [])
            if isinstance(node, dict) and str(node.get("name") or "")
        }
        links: list[dict[str, object]] = []
        unresolved: list[dict[str, object]] = []
        for link in graph_payload.get("links", []):
            if not isinstance(link, dict):
                continue
            kind = "exec" if str(link.get("source_pin_category") or "") == "exec" else "data"
            target = str(link.get("target_node") or "")
            link_status = str(link.get("resolution_status") or "")
            if link_status in {"resolved_pin", "resolved_pin_heuristic"}:
                status = link_status
            elif target and target in node_names and str(link.get("target_pin_id") or ""):
                status = "resolved_pin"
            elif target and target in node_names:
                status = "node_resolved_pin_unknown"
            elif target:
                status = "cross_graph_or_missing_node"
            else:
                status = "unresolved"
            row = {
                "source_node": link.get("source_node", ""),
                "source_pin": link.get("source_pin", ""),
                "source_pin_id": link.get("source_pin_id", ""),
                "target_node": target,
                "target_pin_id": link.get("target_pin_id", ""),
                "target_package_index": link.get("target_package_index", ""),
                "kind": kind,
                "status": status,
                "confidence": link.get("link_confidence") or link.get("confidence") or "medium",
                "source": link.get("link_source") or link.get("source") or "uasset",
            }
            links.append(row)
            resolution_counts[status] += 1
            kind_counts[kind] += 1
            if status != "resolved_pin":
                unresolved.append(row)
        graph_summaries.append(
            {
                "graph": graph.get("graph", ""),
                "graph_type": graph.get("graph_type", ""),
                "status": graph.get("status", ""),
                "confidence": graph.get("confidence", ""),
                "link_count": len(links),
                "unresolved_count": len(unresolved),
                "links": links,
                "unresolved": unresolved[:300],
            }
        )
    return {
        "schema": UASSET_PIN_LINK_SCHEMA,
        "generated": read_payload.get("generated", ""),
        "asset_path": read_payload.get("asset_path", ""),
        "asset_name": read_payload.get("asset_name", ""),
        "summary": {
            "link_count": sum(int(graph.get("link_count") or 0) for graph in graph_summaries),
            "resolution_counts": dict(sorted(resolution_counts.items())),
            "kind_counts": dict(sorted(kind_counts.items())),
        },
        "graphs": graph_summaries,
    }


def parse_graph_export_payload(
    package: dict[str, object],
    graph_export: dict[str, object],
    *,
    asset_path: str,
    asset_name: str,
    node_cache: dict[int, dict[str, object]],
) -> dict[str, object]:
    names = package.get("names", [])
    imports = package.get("imports", [])
    exports = package.get("exports", [])
    if not isinstance(names, list) or not isinstance(imports, list) or not isinstance(exports, list):
        raise ValueError("Invalid package model.")
    graph_data = export_data_bytes(package, graph_export)
    graph_properties, graph_warnings = parse_export_properties(graph_data, names, imports, exports)
    node_refs = property_object_refs(graph_properties, "Nodes")
    if not node_refs:
        graph_package_index = int(graph_export.get("package_index") or 0)
        node_refs = [
            int(item.get("package_index") or 0)
            for item in exports
            if int(item.get("outer_index") or 0) == graph_package_index and is_blueprint_node_export(item)
        ]
    graph_refset = set(node_refs)
    nodes: list[NodeInfo] = []
    parsed_node_records: list[dict[str, object]] = []
    node_warnings: list[str] = []
    for node_ref in node_refs:
        node_index = package_index_to_export_index(node_ref)
        if node_index is None or not (0 <= node_index < len(exports)):
            node_warnings.append(f"Nodes array referenced unsupported package index {node_ref}.")
            continue
        node_export = exports[node_index]
        if not isinstance(node_export, dict) or not is_blueprint_node_export(node_export):
            continue
        cached = node_cache.get(node_index)
        if cached is None:
            node_data = export_data_bytes(package, node_export)
            properties, property_warnings = parse_export_properties(node_data, names, imports, exports)
            pins, pin_warnings = parse_custom_pins(
                node_data,
                names,
                properties,
                node_export=node_export,
                graph_refset=graph_refset,
                imports=imports,
                exports=exports,
            )
            cached = {
                "export": node_export,
                "properties": properties,
                "property_warnings": property_warnings,
                "pins": pins,
                "pin_warnings": pin_warnings,
            }
            node_cache[node_index] = cached
        node_info = node_info_from_export(
            node_export=node_export,
            properties=cached.get("properties", {}) if isinstance(cached.get("properties"), dict) else {},
            pins=list(cached.get("pins", [])) if isinstance(cached.get("pins", []), list) else [],
            index=len(nodes) + 1,
            warnings=list(cached.get("property_warnings", [])) + list(cached.get("pin_warnings", [])),
        )
        nodes.append(node_info)
        warnings_for_node = list(cached.get("property_warnings", [])) + list(cached.get("pin_warnings", []))
        if warnings_for_node:
            node_warnings.extend(f"{node_info.name}: {warning}" for warning in warnings_for_node[:3])
        parsed_node_records.append(
            {
                "export_index": node_index,
                "package_index": node_ref,
                "class": node_export.get("class_name", ""),
                "name": node_info.name,
                "x": node_info.node_pos_x,
                "y": node_info.node_pos_y,
                "function": node_info.function,
                "variable": node_info.variable,
                "event": node_info.event,
                "delegate": node_info.delegate,
                "macro": node_info.macro,
                "pin_count": len(node_info.pins),
                "link_count": node_info.link_count,
                "confidence": node_info.confidence,
                "source": node_info.source,
                "semantic": node_info.semantic,
                "raw_offsets": node_info.raw_offsets,
                "warnings": node_info.warnings,
                "properties": list((cached.get("properties", {}) if isinstance(cached.get("properties"), dict) else {}).keys()),
            }
        )
    synthesis_warnings = synthesize_boundary_pins_from_incoming_links(nodes)
    node_by_name = {node.name: node for node in nodes}
    for record in parsed_node_records:
        node = node_by_name.get(str(record.get("name") or ""))
        if not node:
            continue
        record["pin_count"] = len(node.pins)
        record["link_count"] = node.link_count
        record["confidence"] = node.confidence
        record["semantic"] = node.semantic
        record["warnings"] = node.warnings
    link_resolution_counts = resolve_graph_link_target_pins(nodes)
    graph_name = str(graph_export.get("object_name") or graph_export.get("display_name") or f"Graph_{graph_export.get('index')}")
    graph_type = graph_type_from_export(graph_export)
    empty_graph_complete = is_complete_empty_graph(
        nodes,
        node_refs,
        graph_warnings,
        graph_name=graph_name,
        graph_type=graph_type,
    )
    payload = build_blueprint_payload_from_nodes(
        nodes=nodes,
        raw_text="",
        cleaned_text="",
        text="",
        source=f"uasset:{graph_export.get('index')}",
        asset_name=asset_name,
        graph_name=graph_name,
        keywords=[],
        include_raw=False,
        context={"graph_type": graph_type, "source_kind": "uasset_binary"},
    )
    payload["metadata"]["source_kind"] = "uasset_binary"
    payload["metadata"]["uasset_export_index"] = graph_export.get("index")
    payload["metadata"]["uasset_node_refs"] = node_refs
    payload["metadata"]["graph_type"] = graph_type
    pin_count = sum(len(node.pins) for node in nodes)
    link_count = sum(node.link_count for node in nodes)
    all_warnings = graph_warnings + node_warnings[:120] + synthesis_warnings
    status = "complete" if empty_graph_complete else classify_graph_status(nodes, all_warnings)
    confidence = graph_confidence(nodes, all_warnings)
    if empty_graph_complete:
        confidence = "medium" if not nodes else confidence_floor(confidence, "medium")
    payload["metadata"]["uasset_read_status"] = status
    payload["metadata"]["confidence"] = confidence
    payload["metadata"]["coverage"] = graph_coverage(nodes)
    payload["metadata"]["link_resolution_counts"] = link_resolution_counts
    graph_record = {
        "graph": graph_name,
        "graph_type": graph_type,
        "export_index": graph_export.get("index"),
        "status": status,
        "confidence": confidence,
        "node_refs": node_refs,
        "node_count": len(nodes),
        "pin_count": pin_count,
        "link_count": link_count,
        "coverage": graph_coverage(nodes),
        "link_resolution_counts": link_resolution_counts,
        "nodes": parsed_node_records,
        "properties": graph_properties,
        "warnings": all_warnings,
        "payload": payload,
    }
    graph_record["failure_categories"] = [] if status == "complete" else classify_graph_failure(graph_record)
    return {
        **graph_record,
    }


def read_uasset_graph_content(
    asset_path: str,
    uasset_path: Path,
    *,
    max_graphs: int = 0,
) -> dict[str, object]:
    normalized = normalize_blueprint_object_path(asset_path)
    asset_name = asset_name_from_object_path(normalized) or uasset_path.stem
    generated = _dt.datetime.now().isoformat(timespec="seconds")
    try:
        package = parse_uasset_package(uasset_path)
    except Exception as exc:
        return {
            "schema": UASSET_GRAPH_READ_SCHEMA,
            "generated": generated,
            "asset_path": normalized,
            "asset_name": asset_name,
            "uasset_path": str(uasset_path),
            "loaded": False,
            "warnings": [str(exc)],
            "graphs": [],
        }

    names = package["names"]
    imports = package["imports"]
    exports = package["exports"]
    summary = package["summary"]
    class_defaults = read_uasset_class_defaults(package, asset_name)
    structure = parse_uasset_structure(uasset_path)
    graph_exports_by_index: dict[int, dict[str, object]] = {}
    for graph in structure.get("graph_exports", []):
        if isinstance(graph, dict):
            graph_exports_by_index[int(graph.get("export_index") or -1)] = graph
    graph_exports = [
        export
        for export in exports
        if isinstance(export, dict) and str(export.get("class_name") or "") == "EdGraph" and str(export.get("object_name") or "")
    ]
    if max_graphs > 0:
        graph_exports = graph_exports[:max_graphs]
    for export in graph_exports:
        hint = graph_exports_by_index.get(int(export.get("index") or -1), {})
        if hint:
            export.update({key: value for key, value in hint.items() if key in {"graph_kind", "type_hint", "matching_function_export"}})

    node_cache: dict[int, dict[str, object]] = {}
    graphs: list[dict[str, object]] = []
    warnings: list[str] = [str(value) for value in package.get("warnings", [])]
    for graph_export in graph_exports:
        try:
            graphs.append(
                parse_graph_export_payload(
                    package,
                    graph_export,
                    asset_path=normalized,
                    asset_name=asset_name,
                    node_cache=node_cache,
                )
            )
        except Exception as exc:
            warnings.append(f"{graph_export.get('object_name')}: {exc}")
            graphs.append(
                {
                    "graph": str(graph_export.get("object_name") or f"Graph_{graph_export.get('index')}"),
                    "graph_type": graph_type_from_export(graph_export),
                    "export_index": graph_export.get("index"),
                    "status": "failed",
                    "confidence": "low",
                    "failure_categories": ["need_manual_clipboard"],
                    "node_refs": [],
                    "node_count": 0,
                    "pin_count": 0,
                    "link_count": 0,
                    "coverage": {},
                    "nodes": [],
                    "properties": {},
                    "warnings": [str(exc)],
                    "payload": {},
                }
            )

    status_counts: dict[str, int] = {}
    class_counts: dict[str, int] = {}
    confidence_counts: dict[str, int] = {}
    failure_category_counts: dict[str, int] = {}
    for graph in graphs:
        status = str(graph.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        confidence = str(graph.get("confidence") or "low")
        confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1
        for category in graph.get("failure_categories", []):
            failure_category_counts[str(category)] = failure_category_counts.get(str(category), 0) + 1
        for node in graph.get("nodes", []):
            if isinstance(node, dict):
                class_name = str(node.get("class") or "")
                if class_name:
                    class_counts[class_name] = class_counts.get(class_name, 0) + 1

    parsed_properties = []
    unknown_properties = []
    for cached in node_cache.values():
        node_export = cached.get("export", {})
        if not isinstance(node_export, dict):
            continue
        properties = cached.get("properties", {})
        if isinstance(properties, dict):
            for prop_name, prop in properties.items():
                if not isinstance(prop, dict):
                    continue
                value = prop.get("value")
                unresolved_raw = isinstance(value, dict) and value.get("parsed") is False
                if unresolved_raw or prop.get("confidence") == "low" or prop.get("error"):
                    unknown_properties.append(
                        {
                            "export_index": node_export.get("index"),
                            "node": node_export.get("display_name") or node_export.get("object_name"),
                            "class": node_export.get("class_name"),
                            "property": prop_name,
                            "type": prop.get("type", ""),
                            "confidence": prop.get("confidence", ""),
                            "raw_offsets": prop.get("raw_offsets", {}),
                            "error": prop.get("error", ""),
                        }
                    )
        parsed_properties.append(
            {
                "export_index": node_export.get("index"),
                "name": node_export.get("display_name") or node_export.get("object_name"),
                "class": node_export.get("class_name"),
                "property_count": len(properties) if isinstance(properties, dict) else 0,
                "properties": properties if isinstance(properties, dict) else {},
                "warnings": list(cached.get("property_warnings", [])) + list(cached.get("pin_warnings", [])),
            }
        )

    result = {
        "schema": UASSET_GRAPH_READ_SCHEMA,
        "generated": generated,
        "asset_path": normalized,
        "asset_name": asset_name,
        "uasset_path": str(uasset_path),
        "uexp_path": str(package.get("uexp_path") or ""),
        "loaded": True,
        "package": {
            "schema": "blueprint-translator.uasset-package.v1",
            "summary": summary,
            "name_count": len(names) if isinstance(names, list) else 0,
            "soft_object_paths_count": len(package.get("soft_object_paths", [])) if isinstance(package.get("soft_object_paths", []), list) else 0,
            "import_count": len(imports) if isinstance(imports, list) else 0,
            "export_count": len(exports) if isinstance(exports, list) else 0,
            "uasset_path": str(uasset_path),
            "uexp_path": str(package.get("uexp_path") or ""),
            "warnings": package.get("warnings", []),
        },
        "exports": exports,
        "structure": structure,
        "class_defaults": class_defaults,
        "graph_count": len(graphs),
        "node_count": sum(int(graph.get("node_count") or 0) for graph in graphs),
        "pin_count": sum(int(graph.get("pin_count") or 0) for graph in graphs),
        "link_count": sum(int(graph.get("link_count") or 0) for graph in graphs),
        "status_counts": status_counts,
        "confidence_counts": confidence_counts,
        "failure_category_counts": failure_category_counts,
        "node_class_counts": [
            {"class": name, "count": count}
            for name, count in sorted(class_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "properties": parsed_properties,
        "unknown_properties": unknown_properties,
        "graphs": graphs,
        "warnings": warnings,
    }
    result["pin_links"] = build_pin_link_payload(result)
    return result


def extract_uasset_strings(uasset_path: Path) -> list[dict[str, object]]:
    data = uasset_path.read_bytes()
    found: dict[str, dict[str, object]] = {}
    for source, values in (
        ("ascii", _extract_ascii_strings(data)),
        ("utf16le", _extract_utf16le_strings(data)),
    ):
        for value in values:
            text = normalize_candidate_text(value)
            if not text:
                continue
            key = text.lower()
            item = found.setdefault(key, {"text": text, "sources": set(), "hits": 0})
            item["sources"].add(source)  # type: ignore[union-attr]
            item["hits"] = int(item.get("hits", 0)) + 1
    results: list[dict[str, object]] = []
    for item in found.values():
        sources = sorted(str(value) for value in item.get("sources", set()))
        results.append({"text": item["text"], "sources": sources, "hits": item.get("hits", 0)})
    return results


def normalize_candidate_text(value: str) -> str:
    text = str(value or "").replace("\x00", "").strip()
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\r\n\"'")


def candidate_type_hint(name: str) -> str:
    lowered = name.lower()
    if lowered == "eventgraph":
        return "EventGraph"
    if "construction" in lowered or lowered == "userconstructionscript":
        return "ConstructionScript"
    if "macro" in lowered:
        return "Macro"
    return "Unknown"


def score_graph_candidate(name: str) -> tuple[int, list[str]]:
    text = normalize_candidate_text(name)
    if len(text) < 2 or len(text) > 90:
        return 0, ["length"]
    if any(marker in text for marker in EXCLUDE_CONTAINS):
        return 0, ["excluded_marker"]
    if not VALID_NAME_RE.match(text):
        return 0, ["invalid_chars"]
    if re.fullmatch(r"[\d\s._-]+", text):
        return 0, ["numeric"]
    letters = sum(1 for character in text if character.isalpha())
    visible = sum(1 for character in text if not character.isspace())
    if letters < 2 or (visible and letters / visible < 0.45):
        return 0, ["too_few_letters"]
    words = [part for part in re.split(r"[\s_\-()]+", text) if part]
    if any(part.isdigit() for part in words):
        return 0, ["numeric_token"]
    if len(words) > 1 and any(len(part) == 1 and not part.isalpha() for part in words):
        return 0, ["garbled_token"]
    if TYPE_SUFFIX_RE.search(text) and " " not in text and not PREFIX_RE.search(text):
        return 0, ["type_suffix"]
    if text.endswith(("_C", "_GEN_VARIABLE")):
        return 0, ["generated_name"]

    lowered = text.lower()
    score = 0
    reasons: list[str] = []

    if lowered in {"eventgraph", "userconstructionscript", "constructionscript"}:
        score += 100
        reasons.append("builtin_graph")
    if lowered.startswith("collapsed "):
        score += 75
        reasons.append("collapsed_graph_name")
    if PREFIX_RE.search(text):
        score += 65
        reasons.append("blueprint_function_prefix")
    if " " in text and 2 <= len(words) <= 9:
        score += 48
        reasons.append("human_readable_page_name")
    if len(words) == 1 and re.match(r"^[A-Z][A-Za-z0-9]{2,40}$", text):
        score += 38
        reasons.append("single_title_word")
    if lowered in SHORT_ACTION_WORDS:
        score += 42
        reasons.append("short_action_word")
    if any(word.lower() in SHORT_ACTION_WORDS for word in words):
        score += 16
        reasons.append("action_word")
    if any(marker in lowered for marker in ("graph", "event", "tick", "timer", "multiuse", "rep", "riding", "damage", "sleep", "buff", "attack")):
        score += 18
        reasons.append("graph_context_word")

    if text[0].islower() and not lowered.startswith(("collapsed ", "client ", "server ", "check ", "clear ", "disable ", "enable ")):
        score -= 12
        reasons.append("lowercase_penalty")
    if any(marker in lowered for marker in ("template", "socket", "material", "texture", "montage", "particle", "camera shake")):
        score -= 20
        reasons.append("asset_reference_penalty")

    return max(score, 0), reasons


def title_variant(text: str) -> str:
    special = {"ai": "AI", "bp": "BP", "hud": "HUD", "net": "Net"}
    words = []
    for word in re.split(r"(\s+|-)", text.strip()):
        lowered = word.lower()
        if lowered in special:
            words.append(special[lowered])
        elif word.strip() and word not in {" ", "-"}:
            words.append(word[:1].upper() + word[1:])
        else:
            words.append(word)
    return "".join(words).strip()


def derived_candidate_names(strings: list[dict[str, object]]) -> list[dict[str, object]]:
    derived: dict[str, dict[str, object]] = {}
    for item in strings:
        text = str(item.get("text") or "")
        lowered = text.lower()
        for phrase, display in KNOWN_PAGE_PHRASES.items():
            if phrase in lowered:
                derived.setdefault(
                    display.lower(),
                    {"text": display, "sources": ["derived"], "hits": 1},
                )
        for word in SHORT_ACTION_WORDS:
            if re.search(r"\b{}\b".format(re.escape(word)), lowered):
                display = word.upper() if word in {"ai", "hud"} else title_variant(word)
                derived.setdefault(
                    display.lower(),
                    {"text": display, "sources": ["derived"], "hits": 1},
                )
        if text and text == text.lower() and " " in text and 2 <= len(text.split()) <= 5:
            display = title_variant(text)
            derived.setdefault(
                display.lower(),
                {"text": display, "sources": ["derived_title"], "hits": 1},
            )
    return list(derived.values())


def structural_candidate_names(structure: dict[str, object]) -> list[dict[str, object]]:
    candidates: dict[str, dict[str, object]] = {}
    for key, default_score, default_reason in (
        ("graph_exports", 130, "export_class_edgraph"),
        ("function_exports", 86, "export_class_function"),
    ):
        values = structure.get(key, [])
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            name = normalize_candidate_text(str(item.get("name") or ""))
            if not name:
                continue
            graph_kind = str(item.get("graph_kind") or "")
            type_hint = str(item.get("type_hint") or "")
            score = default_score
            reason = default_reason
            if graph_kind == "FunctionGraph":
                score = 190
                reason = "export_class_edgraph_with_function_export"
            elif graph_kind in {"EventGraph", "ConstructionScript"}:
                score = 195
                reason = "export_class_edgraph_builtin"
            elif graph_kind == "CollapsedGraph":
                score = 100
                reason = "export_class_edgraph_collapsed"
            elif graph_kind == "StandaloneEdGraph":
                score = 175
                reason = "export_class_edgraph_standalone"
            if key == "function_exports":
                type_hint = "Function"
            normalized = name.lower()
            current = candidates.get(normalized)
            if current is None or int(current.get("structure_score", 0)) < score:
                candidates[normalized] = {
                    "text": name,
                    "sources": [reason],
                    "hits": 1,
                    "structure_class": str(item.get("class") or ""),
                    "structure_graph_kind": graph_kind,
                    "type_hint": type_hint,
                    "structure_score": score,
                    "structure_reason": reason,
                }
    return list(candidates.values())


def mine_graph_candidates_from_uasset(
    asset_path: str,
    uasset_path: Path,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> dict[str, object]:
    structure = parse_uasset_structure(uasset_path)
    strings = extract_uasset_strings(uasset_path)
    strings.extend(derived_candidate_names(strings))
    strings.extend(structural_candidate_names(structure))
    candidate_by_name: dict[str, dict[str, object]] = {}
    rejected = 0
    for item in strings:
        name = str(item.get("text") or "")
        score, reasons = score_graph_candidate(name)
        structure_score = int(item.get("structure_score", 0) or 0)
        if structure_score:
            score = max(score, structure_score)
            structure_reason = str(item.get("structure_reason") or "structured_export")
            reasons = [structure_reason, *[reason for reason in reasons if reason != structure_reason]]
        if score < 35:
            rejected += 1
            continue
        candidate = {
            "name": name,
            "type_hint": str(item.get("type_hint") or "")
            or ("Function" if item.get("structure_class") == "Function" else candidate_type_hint(name)),
            "score": score,
            "reasons": reasons,
            "sources": item.get("sources", []),
            "hits": item.get("hits", 0),
            "structure_class": item.get("structure_class", ""),
            "structure_graph_kind": item.get("structure_graph_kind", ""),
        }
        key = name.lower()
        existing = candidate_by_name.get(key)
        if existing is None or int(existing.get("score", 0)) < score:
            candidate_by_name[key] = candidate

    candidates = list(candidate_by_name.values())
    candidates.sort(key=lambda item: (-int(item.get("score", 0)), str(item.get("name", "")).lower()))
    if max_candidates > 0:
        candidates = candidates[:max_candidates]

    return {
        "schema": GRAPH_CANDIDATE_SCHEMA,
        "generated": _dt.datetime.now().isoformat(timespec="seconds"),
        "asset_path": normalize_blueprint_object_path(asset_path),
        "asset_name": asset_name_from_object_path(asset_path),
        "uasset_path": str(uasset_path),
        "method": "safe_ascii_utf16_string_scan_plus_experimental_export_map",
        "structure": {
            "loaded": bool(structure.get("loaded")),
            "method": structure.get("method", ""),
            "name_count": structure.get("name_count", 0),
            "import_count": structure.get("import_count", 0),
            "export_count": structure.get("export_count", 0),
            "graph_exports_count": structure.get("graph_exports_count", 0),
            "function_exports_count": structure.get("function_exports_count", 0),
            "function_graph_exports_count": structure.get("function_graph_exports_count", 0),
            "collapsed_graph_exports_count": structure.get("collapsed_graph_exports_count", 0),
            "standalone_graph_exports_count": structure.get("standalone_graph_exports_count", 0),
            "warnings": structure.get("warnings", []),
        },
        "candidate_count": len(candidates),
        "raw_string_count": len(strings),
        "rejected_string_count": rejected,
        "candidates": candidates,
        "uasset_structure": structure,
    }


def mine_graph_candidates(
    asset_path: str,
    extra_roots: Iterable[str | os.PathLike[str]] | None = None,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> tuple[dict[str, object], list[str]]:
    uasset_path, attempted = object_path_to_uasset_path(asset_path, extra_roots)
    if uasset_path is None:
        normalized = normalize_blueprint_object_path(asset_path)
        return (
            {
                "schema": GRAPH_CANDIDATE_SCHEMA,
                "generated": _dt.datetime.now().isoformat(timespec="seconds"),
                "asset_path": normalized,
                "asset_name": asset_name_from_object_path(normalized),
                "uasset_path": "",
                "method": "safe_ascii_utf16_string_scan_plus_experimental_export_map",
                "structure": {"loaded": False, "warnings": ["Could not resolve local .uasset path."]},
                "candidate_count": 0,
                "raw_string_count": 0,
                "rejected_string_count": 0,
                "candidates": [],
                "warnings": ["Could not resolve local .uasset path."],
            },
            attempted,
        )
    return mine_graph_candidates_from_uasset(asset_path, uasset_path, max_candidates), attempted


def render_candidate_text(payload: dict[str, object]) -> str:
    lines: list[str] = []
    for item in payload.get("candidates", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        type_hint = str(item.get("type_hint") or "Unknown").strip() or "Unknown"
        lines.append(f"{name} | {type_hint}")
    return "\n".join(lines) + ("\n" if lines else "")


def write_graph_candidate_files(asset_path: str, capture_root: Path, payload: dict[str, object]) -> dict[str, str]:
    asset_name = str(payload.get("asset_name") or asset_name_from_object_path(asset_path) or "Blueprint")
    asset_dir = capture_root / safe_filename(asset_name)
    asset_dir.mkdir(parents=True, exist_ok=True)
    json_path = asset_dir / "graph_candidates_uasset.json"
    text_path = asset_dir / "graph_candidates_uasset.txt"
    report_path = asset_dir / "graph_candidates_uasset_report.md"
    structure_json_path = asset_dir / "uasset_structure.json"
    structure_report_path = asset_dir / "uasset_structure_report.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text_path.write_text(render_candidate_text(payload), encoding="utf-8")
    report_path.write_text(render_candidate_report(payload), encoding="utf-8")
    structure = payload.get("uasset_structure", {})
    if isinstance(structure, dict):
        structure_json_path.write_text(json.dumps(structure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        structure_report_path.write_text(render_structure_report(structure), encoding="utf-8")
    return {
        "asset_dir": str(asset_dir),
        "json": str(json_path),
        "text": str(text_path),
        "report": str(report_path),
        "structure_json": str(structure_json_path),
        "structure_report": str(structure_report_path),
    }


def graph_payload_filename(graph_name: str, export_index: object) -> str:
    suffix = str(export_index) if str(export_index).strip() else "graph"
    return f"{safe_filename(graph_name, 'Graph')}_{suffix}.json"


def reduced_graph_nodes_payload(payload: dict[str, object]) -> dict[str, object]:
    return {
        "schema": "blueprint-translator.uasset-graph-nodes.v1",
        "generated": payload.get("generated", ""),
        "asset_path": payload.get("asset_path", ""),
        "asset_name": payload.get("asset_name", ""),
        "uasset_path": payload.get("uasset_path", ""),
        "graph_count": payload.get("graph_count", 0),
        "node_count": payload.get("node_count", 0),
        "pin_count": payload.get("pin_count", 0),
        "link_count": payload.get("link_count", 0),
        "status_counts": payload.get("status_counts", {}),
        "confidence_counts": payload.get("confidence_counts", {}),
        "failure_category_counts": payload.get("failure_category_counts", {}),
        "node_class_counts": payload.get("node_class_counts", []),
        "graphs": [
            {
                "graph": graph.get("graph", ""),
                "graph_type": graph.get("graph_type", ""),
                "export_index": graph.get("export_index", 0),
                "status": graph.get("status", ""),
                "confidence": graph.get("confidence", ""),
                "failure_categories": graph.get("failure_categories", []),
                "coverage": graph.get("coverage", {}),
                "node_count": graph.get("node_count", 0),
                "pin_count": graph.get("pin_count", 0),
                "link_count": graph.get("link_count", 0),
                "nodes": graph.get("nodes", []),
                "warnings": graph.get("warnings", []),
            }
            for graph in payload.get("graphs", [])
            if isinstance(graph, dict)
        ],
    }


def render_property_parse_report(payload: dict[str, object]) -> str:
    properties = [item for item in payload.get("properties", []) if isinstance(item, dict)]
    unknown_properties = [item for item in payload.get("unknown_properties", []) if isinstance(item, dict)]
    warning_count = sum(len(item.get("warnings", [])) for item in properties if isinstance(item.get("warnings", []), list))
    lines = [
        "# UAsset Property Parse Report",
        "",
        f"- Asset: {payload.get('asset_name') or '-'}",
        f"- UAsset path: {payload.get('uasset_path') or '-'}",
        f"- Parsed node exports: {len(properties)}",
        f"- Unknown/raw property fields: {len(unknown_properties)}",
        f"- Property/pin warnings: {warning_count}",
        "",
        "## Recoverable Fields",
        "",
        "| Field | Recovered Exports |",
        "| --- | ---: |",
    ]
    field_counts: dict[str, int] = {}
    for item in properties:
        props = item.get("properties", {})
        if not isinstance(props, dict):
            continue
        for key in props:
            field_counts[key] = field_counts.get(key, 0) + 1
    for key, count in sorted(field_counts.items(), key=lambda item: (-item[1], item[0]))[:80]:
        lines.append(f"| {key} | {count} |")
    lines.extend(["", "## Warnings", ""])
    written = 0
    for item in properties:
        warnings = item.get("warnings", [])
        if not isinstance(warnings, list) or not warnings:
            continue
        lines.append(f"### {item.get('name', '-')} ({item.get('class', '-')})")
        lines.append("")
        for warning in warnings[:8]:
            lines.append(f"- {warning}")
            written += 1
        lines.append("")
        if written >= 120:
            lines.append("- Additional warnings omitted.")
            break
    if written == 0:
        lines.append("- No property parse warnings.")
    lines.extend(["", "## Unknown Or Raw Properties", ""])
    if unknown_properties:
        lines.append("| Node | Class | Property | Type | Confidence | Offset |")
        lines.append("| --- | --- | --- | --- | --- | ---: |")
        for item in unknown_properties[:160]:
            offsets = item.get("raw_offsets", {}) if isinstance(item.get("raw_offsets", {}), dict) else {}
            lines.append(
                "| {} | {} | {} | {} | {} | {} |".format(
                    item.get("node", ""),
                    item.get("class", ""),
                    item.get("property", ""),
                    item.get("type", ""),
                    item.get("confidence", ""),
                    offsets.get("start", ""),
                )
            )
    else:
        lines.append("- No unknown/raw property fields were recorded.")
    lines.append("")
    return "\n".join(lines)


def render_pin_link_report(payload: dict[str, object]) -> str:
    pin_links = payload.get("pin_links", {})
    if not isinstance(pin_links, dict) or not pin_links:
        pin_links = build_pin_link_payload(payload)
    summary = pin_links.get("summary", {}) if isinstance(pin_links.get("summary", {}), dict) else {}
    lines = [
        "# UAsset Pin Link Resolution Report",
        "",
        f"- Asset: {payload.get('asset_name') or '-'}",
        f"- Total links: {summary.get('link_count', 0)}",
        "",
        "## Link Kinds",
        "",
        "| Kind | Count |",
        "| --- | ---: |",
    ]
    kind_counts = summary.get("kind_counts", {})
    if isinstance(kind_counts, dict) and kind_counts:
        for kind, count in sorted(kind_counts.items()):
            lines.append(f"| {kind} | {count} |")
    else:
        lines.append("| none | 0 |")
    lines.extend(["", "## Resolution", "", "| Status | Count | Meaning |", "| --- | ---: | --- |"])
    meanings = {
        "resolved_pin": "target node and target pin were resolved",
        "resolved_pin_heuristic": "target node was resolved and target pin was inferred from direction/category fallback",
        "node_resolved_pin_unknown": "target node was resolved, but the target pin id still needs a structural rule",
        "cross_graph_or_missing_node": "target node is not in this graph payload; it may be cross-graph, macro, or missed by parsing",
        "unresolved": "no target node could be recovered",
    }
    resolution_counts = summary.get("resolution_counts", {})
    if isinstance(resolution_counts, dict) and resolution_counts:
        for status, count in sorted(resolution_counts.items()):
            lines.append(f"| {status} | {count} | {meanings.get(str(status), '')} |")
    else:
        lines.append("| none | 0 | - |")
    lines.extend(["", "## Graphs Needing Link Rules", "", "| Graph | Status | Confidence | Links | Unresolved |", "| --- | --- | --- | ---: | ---: |"])
    for graph in pin_links.get("graphs", []):
        if not isinstance(graph, dict):
            continue
        unresolved_count = int(graph.get("unresolved_count") or 0)
        if unresolved_count <= 0:
            continue
        lines.append(
            "| {} | {} | {} | {} | {} |".format(
                graph.get("graph", ""),
                graph.get("status", ""),
                graph.get("confidence", ""),
                graph.get("link_count", 0),
                unresolved_count,
            )
        )
    lines.append("")
    return "\n".join(lines)


def primary_partial_reason(graph: dict[str, object], pin_graph: dict[str, object] | None = None) -> tuple[str, list[str]]:
    reasons: list[str] = []
    graph_name = str(graph.get("graph") or "")
    graph_type = str(graph.get("graph_type") or "")
    node_count = int(graph.get("node_count") or 0)
    pin_count = int(graph.get("pin_count") or 0)
    link_count = int(graph.get("link_count") or 0)
    warnings = [str(warning).lower() for warning in graph.get("warnings", []) if isinstance(warning, str)]
    failure_categories = [str(item) for item in graph.get("failure_categories", []) if str(item)]
    unresolved = pin_graph.get("unresolved", []) if isinstance(pin_graph, dict) else []
    unresolved_statuses = Counter(str(item.get("status") or "") for item in unresolved if isinstance(item, dict))
    lowered_name = graph_name.lower()
    if "need_manual_clipboard" in failure_categories or (node_count and pin_count == 0):
        reasons.append("manual_only")
    if node_count <= 4 and link_count <= 2:
        reasons.append("single_node_graph")
    if "collapsed" in lowered_name or graph_type in {"CollapsedGraph", "Composite"}:
        reasons.append("collapsed_graph_boundary")
    if any("recovered" in warning and "custom pins" in warning for warning in warnings):
        reasons.append("pin_count_mismatch")
    if any("could not locate custom pin" in warning or "pin count" in warning for warning in warnings):
        reasons.append("custom_pin_layout_variant")
    if unresolved_statuses.get("node_resolved_pin_unknown"):
        reasons.append("missing_target_pin_id")
    if unresolved_statuses.get("cross_graph_or_missing_node") or "need_cross_graph_resolve" in failure_categories:
        reasons.append("external_or_macro_link")
    if "need_node_reader" in failure_categories:
        reasons.append("unknown_node_custom_data")
    if not reasons and str(graph.get("status") or "") != "complete":
        reasons.append("custom_pin_layout_variant" if pin_count else "manual_only")
    reasons = list(dict.fromkeys(reasons))
    return (reasons[0] if reasons else "manual_only"), reasons


def build_partial_graph_triage(payload: dict[str, object]) -> dict[str, object]:
    pin_links = payload.get("pin_links", {})
    if not isinstance(pin_links, dict) or not pin_links:
        pin_links = build_pin_link_payload(payload)
    pin_graphs = {
        str(graph.get("graph") or ""): graph
        for graph in pin_links.get("graphs", [])
        if isinstance(graph, dict)
    }
    rows: list[dict[str, object]] = []
    reason_counts: Counter[str] = Counter()
    for graph in payload.get("graphs", []):
        if not isinstance(graph, dict):
            continue
        if str(graph.get("status") or "") == "complete":
            continue
        pin_graph = pin_graphs.get(str(graph.get("graph") or ""))
        primary, reasons = primary_partial_reason(graph, pin_graph)
        reason_counts[primary] += 1
        rows.append(
            {
                "graph": graph.get("graph", ""),
                "graph_type": graph.get("graph_type", ""),
                "status": graph.get("status", ""),
                "confidence": graph.get("confidence", ""),
                "primary_reason": primary,
                "reasons": reasons,
                "node_count": graph.get("node_count", 0),
                "pin_count": graph.get("pin_count", 0),
                "link_count": graph.get("link_count", 0),
                "coverage": graph.get("coverage", {}),
                "next_action": partial_triage_next_action(primary),
                "warnings": graph.get("warnings", [])[:12],
            }
        )
    return {
        "schema": UASSET_PARTIAL_TRIAGE_SCHEMA,
        "generated": payload.get("generated", ""),
        "asset_path": payload.get("asset_path", ""),
        "asset_name": payload.get("asset_name", ""),
        "partial_graph_count": len(rows),
        "reason_counts": dict(sorted(reason_counts.items())),
        "reason_meanings": PARTIAL_TRIAGE_MEANINGS,
        "graphs": rows,
    }


def partial_triage_next_action(reason: str) -> str:
    actions = {
        "missing_target_pin_id": "Improve FEdGraphPinReference/LinkedTo target PinId decoding for this layout.",
        "pin_count_mismatch": "Add a custom pin layout rule for nodes that recover fewer pins than the serialized count.",
        "custom_pin_layout_variant": "Inspect this node export's custom data around the property terminator and add a layout variant.",
        "single_node_graph": "Treat as low priority unless the graph is behavior-critical or the node should have links.",
        "external_or_macro_link": "Resolve macro/collapsed/external package indexes or manually capture the boundary graph.",
        "collapsed_graph_boundary": "Compare with the parent collapsed graph and tunnel nodes before requiring manual capture.",
        "unknown_node_custom_data": "Add a dedicated node semantic reader if this class appears frequently.",
        "manual_only": "Manual clipboard capture is the fastest reliable path for this graph.",
    }
    return actions.get(reason, "Review the graph report and add the narrowest missing binary rule.")


def render_partial_graph_triage_report(payload: dict[str, object]) -> str:
    triage = build_partial_graph_triage(payload)
    lines = [
        "# UAsset Partial Graph Triage",
        "",
        f"- Asset: {triage.get('asset_name') or '-'}",
        f"- Partial graphs: {triage.get('partial_graph_count', 0)}",
        "",
        "## Reason Counts",
        "",
        "| Reason | Count | Meaning |",
        "| --- | ---: | --- |",
    ]
    counts = triage.get("reason_counts", {})
    if isinstance(counts, dict) and counts:
        for reason, count in sorted(counts.items()):
            lines.append(f"| {reason} | {count} | {PARTIAL_TRIAGE_MEANINGS.get(str(reason), '')} |")
    else:
        lines.append("| none | 0 | - |")
    lines.extend(["", "## Graphs", "", "| Graph | Type | Status | Confidence | Primary Reason | Nodes | Pins | Links | Next Action |", "| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- |"])
    for row in triage.get("graphs", [])[:500]:
        if not isinstance(row, dict):
            continue
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                row.get("graph", ""),
                row.get("graph_type", ""),
                row.get("status", ""),
                row.get("confidence", ""),
                row.get("primary_reason", ""),
                row.get("node_count", 0),
                row.get("pin_count", 0),
                row.get("link_count", 0),
                row.get("next_action", ""),
            )
        )
    lines.append("")
    return "\n".join(lines)


def build_quality_gate_payload(payload: dict[str, object]) -> dict[str, object]:
    pin_links = payload.get("pin_links", {})
    if not isinstance(pin_links, dict) or not pin_links:
        pin_links = build_pin_link_payload(payload)
    summary = pin_links.get("summary", {}) if isinstance(pin_links.get("summary", {}), dict) else {}
    resolution_counts = summary.get("resolution_counts", {}) if isinstance(summary.get("resolution_counts", {}), dict) else {}
    status_counts = payload.get("status_counts", {}) if isinstance(payload.get("status_counts", {}), dict) else {}
    unknown_properties = payload.get("unknown_properties", [])
    metrics = {
        "graphs": int(payload.get("graph_count") or 0),
        "nodes": int(payload.get("node_count") or 0),
        "pins": int(payload.get("pin_count") or 0),
        "links": int(payload.get("link_count") or 0),
        "complete_graphs": int(status_counts.get("complete") or 0),
        "partial_graphs": int(status_counts.get("partial") or 0) + int(status_counts.get("heuristic") or 0) + int(status_counts.get("needs_clipboard") or 0) + int(status_counts.get("failed") or 0),
        "node_resolved_pin_unknown": int(resolution_counts.get("node_resolved_pin_unknown") or 0),
        "need_manual_clipboard": int(payload.get("failure_category_counts", {}).get("need_manual_clipboard") or 0) if isinstance(payload.get("failure_category_counts", {}), dict) else 0,
        "unknown_raw_properties": len(unknown_properties) if isinstance(unknown_properties, list) else 0,
    }
    gates = [
        {"metric": "graphs", "operator": ">=", "target": 307, "actual": metrics["graphs"]},
        {"metric": "nodes", "operator": ">=", "target": 11117, "actual": metrics["nodes"]},
        {"metric": "pins", "operator": ">=", "target": 38640, "actual": metrics["pins"]},
        {"metric": "links", "operator": ">=", "target": 26940, "actual": metrics["links"]},
        {"metric": "complete_graphs", "operator": ">=", "target": 250, "actual": metrics["complete_graphs"]},
        {"metric": "partial_graphs", "operator": "<=", "target": 57, "actual": metrics["partial_graphs"]},
        {"metric": "node_resolved_pin_unknown", "operator": "<=", "target": 13135, "actual": metrics["node_resolved_pin_unknown"]},
        {"metric": "need_manual_clipboard", "operator": "<=", "target": 3, "actual": metrics["need_manual_clipboard"]},
        {"metric": "unknown_raw_properties", "operator": "<=", "target": 330, "actual": metrics["unknown_raw_properties"]},
    ]
    for gate in gates:
        actual = int(gate["actual"])
        target = int(gate["target"])
        gate["passed"] = actual >= target if gate["operator"] == ">=" else actual <= target
    return {
        "schema": UASSET_QUALITY_GATES_SCHEMA,
        "generated": payload.get("generated", ""),
        "asset_path": payload.get("asset_path", ""),
        "asset_name": payload.get("asset_name", ""),
        "metrics": metrics,
        "gates": gates,
        "passed": all(bool(gate.get("passed")) for gate in gates),
    }


def render_quality_gate_report(payload: dict[str, object]) -> str:
    gate_payload = build_quality_gate_payload(payload)
    lines = [
        "# UAsset Quality Gates",
        "",
        f"- Asset: {gate_payload.get('asset_name') or '-'}",
        f"- Overall: {'PASS' if gate_payload.get('passed') else 'NEEDS WORK'}",
        "",
        "| Metric | Target | Actual | Result |",
        "| --- | ---: | ---: | --- |",
    ]
    for gate in gate_payload.get("gates", []):
        if not isinstance(gate, dict):
            continue
        lines.append(
            "| {} | {} {} | {} | {} |".format(
                gate.get("metric", ""),
                gate.get("operator", ""),
                gate.get("target", ""),
                gate.get("actual", ""),
                "PASS" if gate.get("passed") else "FAIL",
            )
        )
    lines.append("")
    return "\n".join(lines)


def render_graph_read_report(payload: dict[str, object]) -> str:
    lines = [
        "# UAsset Graph Read Report",
        "",
        f"- Asset: {payload.get('asset_name') or '-'}",
        f"- Asset path: {payload.get('asset_path') or '-'}",
        f"- UAsset path: {payload.get('uasset_path') or '-'}",
        f"- UEXP path: {payload.get('uexp_path') or 'none'}",
        f"- Graphs: {payload.get('graph_count', 0)}",
        f"- Nodes: {payload.get('node_count', 0)}",
        f"- Pins recovered: {payload.get('pin_count', 0)}",
        f"- Links recovered: {payload.get('link_count', 0)}",
        "",
        "## Read Status",
        "",
        "| Status | Count | Meaning |",
        "| --- | ---: | --- |",
    ]
    meanings = {
        key: value for key, value in UASSET_GRAPH_STATUS_MEANINGS.items()
    }
    status_counts = payload.get("status_counts", {})
    if isinstance(status_counts, dict):
        for status, count in sorted(status_counts.items(), key=lambda item: str(item[0])):
            lines.append(f"| {status} | {count} | {meanings.get(str(status), '')} |")
    lines.extend(["", "## Confidence", "", "| Confidence | Graphs |", "| --- | ---: |"])
    confidence_counts = payload.get("confidence_counts", {})
    if isinstance(confidence_counts, dict) and confidence_counts:
        for confidence, count in sorted(confidence_counts.items(), key=lambda item: str(item[0])):
            lines.append(f"| {confidence} | {count} |")
    else:
        lines.append("| none | 0 |")
    lines.extend(["", "## Failure Categories", "", "| Category | Count | Meaning |", "| --- | ---: | --- |"])
    failure_counts = payload.get("failure_category_counts", {})
    if isinstance(failure_counts, dict) and failure_counts:
        for category, count in sorted(failure_counts.items(), key=lambda item: str(item[0])):
            lines.append(f"| {category} | {count} | {FAILURE_CATEGORY_MEANINGS.get(str(category), '')} |")
    else:
        lines.append("| none | 0 | - |")
    lines.extend(["", "## Node Classes", "", "| Class | Count |", "| --- | ---: |"])
    for item in payload.get("node_class_counts", [])[:80]:
        if isinstance(item, dict):
            lines.append(f"| {item.get('class', '')} | {item.get('count', 0)} |")
    lines.extend(["", "## Graphs", "", "| Graph | Type | Status | Confidence | Nodes | Pins | Links | Node Pin Coverage | Warnings |", "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |"])
    for graph in payload.get("graphs", [])[:500]:
        if not isinstance(graph, dict):
            continue
        warnings = graph.get("warnings", [])
        warning_count = len(warnings) if isinstance(warnings, list) else 0
        coverage = graph.get("coverage", {}) if isinstance(graph.get("coverage", {}), dict) else {}
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                graph.get("graph", ""),
                graph.get("graph_type", ""),
                graph.get("status", ""),
                graph.get("confidence", ""),
                graph.get("node_count", 0),
                graph.get("pin_count", 0),
                graph.get("link_count", 0),
                coverage.get("node_pin_coverage", 0),
                warning_count,
            )
        )
    warnings = payload.get("warnings", [])
    if isinstance(warnings, list) and warnings:
        lines.extend(["", "## Package Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings[:120])
    lines.extend(
        [
            "",
            "## Manual Supplement Queue",
            "",
            "Graphs marked `nodes_only`, `partial`, or `failed` are written to `uasset_failed_graph_queue.txt` so the control center can focus manual Ctrl+A/C only where the binary reader still lacks coverage.",
            "",
        ]
    )
    return "\n".join(lines)


def compare_name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def counter_overlap(left: Counter[str], right: Counter[str]) -> int:
    return sum(min(left[key], right[key]) for key in set(left) | set(right))


def payload_counter(payload: dict[str, object], field: str) -> Counter[str]:
    return Counter(str(node.get(field) or "") for node in payload.get("nodes", []) if isinstance(node, dict) and str(node.get(field) or ""))


def compare_clipboard_and_uasset_payloads(clipboard_payload: dict[str, object], uasset_payload: dict[str, object]) -> dict[str, object]:
    clip_nodes = [node for node in clipboard_payload.get("nodes", []) if isinstance(node, dict)]
    uasset_nodes = [node for node in uasset_payload.get("nodes", []) if isinstance(node, dict)]
    clip_classes = payload_counter(clipboard_payload, "node_type")
    uasset_classes = payload_counter(uasset_payload, "node_type")
    clip_functions = payload_counter(clipboard_payload, "function")
    uasset_functions = payload_counter(uasset_payload, "function")
    clip_variables = payload_counter(clipboard_payload, "variable")
    uasset_variables = payload_counter(uasset_payload, "variable")
    clip_events = payload_counter(clipboard_payload, "event")
    uasset_events = payload_counter(uasset_payload, "event")
    clip_pins = int(clipboard_payload.get("metadata", {}).get("pin_count") or len(clipboard_payload.get("pins", [])))
    uasset_pins = int(uasset_payload.get("metadata", {}).get("pin_count") or len(uasset_payload.get("pins", [])))
    clip_links = int(clipboard_payload.get("metadata", {}).get("link_count") or len(clipboard_payload.get("links", [])))
    uasset_links = int(uasset_payload.get("metadata", {}).get("link_count") or len(uasset_payload.get("links", [])))

    node_overlap = counter_overlap(clip_classes, uasset_classes)
    function_overlap = counter_overlap(clip_functions, uasset_functions)
    variable_overlap = counter_overlap(clip_variables, uasset_variables)
    event_overlap = counter_overlap(clip_events, uasset_events)
    node_match_ratio = round(node_overlap / max(1, len(clip_nodes)), 4)
    function_hit_ratio = 1.0 if not clip_functions else round(function_overlap / max(1, sum(clip_functions.values())), 4)
    variable_hit_ratio = 1.0 if not clip_variables else round(variable_overlap / max(1, sum(clip_variables.values())), 4)
    event_hit_ratio = 1.0 if not clip_events else round(event_overlap / max(1, sum(clip_events.values())), 4)
    pin_ratio = round(uasset_pins / max(1, clip_pins), 4)
    link_ratio = round(uasset_links / max(1, clip_links), 4)
    if node_match_ratio >= 0.9 and function_hit_ratio >= 0.85 and variable_hit_ratio >= 0.85:
        confidence = "high"
    elif node_match_ratio >= 0.7:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "clipboard_node_count": len(clip_nodes),
        "uasset_node_count": len(uasset_nodes),
        "clipboard_pin_count": clip_pins,
        "uasset_pin_count": uasset_pins,
        "clipboard_link_count": clip_links,
        "uasset_link_count": uasset_links,
        "node_class_overlap": node_overlap,
        "node_match_ratio": node_match_ratio,
        "function_hit_ratio": function_hit_ratio,
        "variable_hit_ratio": variable_hit_ratio,
        "event_hit_ratio": event_hit_ratio,
        "pin_recovery_ratio": pin_ratio,
        "link_recovery_ratio": link_ratio,
        "confidence": confidence,
        "node_type_delta": {key: uasset_classes.get(key, 0) - clip_classes.get(key, 0) for key in sorted(set(clip_classes) | set(uasset_classes)) if uasset_classes.get(key, 0) != clip_classes.get(key, 0)},
        "function_delta": {key: uasset_functions.get(key, 0) - clip_functions.get(key, 0) for key in sorted(set(clip_functions) | set(uasset_functions)) if uasset_functions.get(key, 0) != clip_functions.get(key, 0)},
        "variable_delta": {key: uasset_variables.get(key, 0) - clip_variables.get(key, 0) for key in sorted(set(clip_variables) | set(uasset_variables)) if uasset_variables.get(key, 0) != clip_variables.get(key, 0)},
    }


def compare_uasset_with_clipboard(asset_dir: Path, *, keywords: list[str] | None = None) -> dict[str, object]:
    keywords = keywords or []
    graphs_dir = asset_dir / "graphs"
    uasset_dir = asset_dir / "graphs_from_uasset"
    clipboard_files = sorted(graphs_dir.glob("*.txt")) if graphs_dir.is_dir() else []
    uasset_files = sorted(uasset_dir.glob("*.json")) if uasset_dir.is_dir() else []
    uasset_by_key: dict[str, Path] = {}
    for path in uasset_files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        metadata = payload.get("metadata", {}) if isinstance(payload, dict) and isinstance(payload.get("metadata", {}), dict) else {}
        graph_name = str(metadata.get("graph_name") or path.stem.rsplit("_", 1)[0])
        uasset_by_key.setdefault(compare_name_key(graph_name), path)
    graph_rows: list[dict[str, object]] = []
    missing_uasset: list[str] = []
    matched_uasset: set[str] = set()
    for clip_path in clipboard_files:
        key = compare_name_key(clip_path.stem)
        uasset_path = uasset_by_key.get(key)
        if not uasset_path:
            missing_uasset.append(clip_path.stem)
            continue
        raw = clip_path.read_text(encoding="utf-8-sig", errors="replace")
        _cleaned, _nodes, clip_payload = parse_blueprint_text(
            text=raw,
            source=str(clip_path),
            asset_name=asset_dir.name,
            graph_name=clip_path.stem,
            keywords=keywords,
        )
        uasset_payload = json.loads(uasset_path.read_text(encoding="utf-8-sig"))
        metrics = compare_clipboard_and_uasset_payloads(clip_payload, uasset_payload)
        graph_rows.append(
            {
                "graph": clip_path.stem,
                "clipboard": str(clip_path),
                "uasset": str(uasset_path),
                **metrics,
            }
        )
        matched_uasset.add(str(uasset_path))
    unmatched_uasset = [str(path) for path in uasset_files if str(path) not in matched_uasset]
    avg_node_ratio = round(sum(float(row.get("node_match_ratio") or 0) for row in graph_rows) / max(1, len(graph_rows)), 4)
    avg_pin_ratio = round(sum(float(row.get("pin_recovery_ratio") or 0) for row in graph_rows) / max(1, len(graph_rows)), 4)
    avg_link_ratio = round(sum(float(row.get("link_recovery_ratio") or 0) for row in graph_rows) / max(1, len(graph_rows)), 4)
    confidence_counts = Counter(str(row.get("confidence") or "low") for row in graph_rows)
    return {
        "schema": UASSET_CLIPBOARD_COMPARE_SCHEMA,
        "generated": _dt.datetime.now().isoformat(timespec="seconds"),
        "asset_dir": str(asset_dir),
        "clipboard_graph_count": len(clipboard_files),
        "uasset_graph_count": len(uasset_files),
        "matched_graph_count": len(graph_rows),
        "missing_uasset_graphs": missing_uasset,
        "unmatched_uasset_graphs": unmatched_uasset[:500],
        "averages": {
            "node_match_ratio": avg_node_ratio,
            "pin_recovery_ratio": avg_pin_ratio,
            "link_recovery_ratio": avg_link_ratio,
        },
        "confidence_counts": dict(confidence_counts),
        "graphs": graph_rows,
    }


def render_uasset_clipboard_compare_report(payload: dict[str, object]) -> str:
    averages = payload.get("averages", {}) if isinstance(payload.get("averages", {}), dict) else {}
    lines = [
        "# UAsset Vs Clipboard Compare",
        "",
        f"- Asset dir: {payload.get('asset_dir') or '-'}",
        f"- Clipboard graphs: {payload.get('clipboard_graph_count', 0)}",
        f"- UAsset graphs: {payload.get('uasset_graph_count', 0)}",
        f"- Matched graphs: {payload.get('matched_graph_count', 0)}",
        f"- Average node match: {averages.get('node_match_ratio', 0)}",
        f"- Average pin recovery: {averages.get('pin_recovery_ratio', 0)}",
        f"- Average link recovery: {averages.get('link_recovery_ratio', 0)}",
        "",
        "## Confidence Counts",
        "",
        "| Confidence | Graphs |",
        "| --- | ---: |",
    ]
    confidence_counts = payload.get("confidence_counts", {})
    if isinstance(confidence_counts, dict) and confidence_counts:
        for confidence, count in sorted(confidence_counts.items()):
            lines.append(f"| {confidence} | {count} |")
    else:
        lines.append("| none | 0 |")
    lines.extend(["", "## Graph Matrix", "", "| Graph | Confidence | Nodes | Pins | Links | Functions | Variables |", "| --- | --- | ---: | ---: | ---: | ---: | ---: |"])
    for row in payload.get("graphs", [])[:500]:
        if not isinstance(row, dict):
            continue
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} |".format(
                row.get("graph", ""),
                row.get("confidence", ""),
                row.get("node_match_ratio", 0),
                row.get("pin_recovery_ratio", 0),
                row.get("link_recovery_ratio", 0),
                row.get("function_hit_ratio", 0),
                row.get("variable_hit_ratio", 0),
            )
        )
    missing = payload.get("missing_uasset_graphs", [])
    if isinstance(missing, list) and missing:
        lines.extend(["", "## Clipboard Graphs Without UAsset Match", ""])
        lines.extend(f"- {item}" for item in missing[:120])
    lines.append("")
    return "\n".join(lines)


def write_uasset_clipboard_compare_files(asset_dir: Path, payload: dict[str, object]) -> dict[str, str]:
    json_path = asset_dir / "uasset_compare_matrix.json"
    report_path = asset_dir / "uasset_vs_clipboard_compare.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_uasset_clipboard_compare_report(payload), encoding="utf-8")
    return {"compare_json": str(json_path), "compare_report": str(report_path)}


def write_uasset_graph_read_files(asset_path: str, capture_root: Path, payload: dict[str, object]) -> dict[str, str]:
    asset_name = str(payload.get("asset_name") or asset_name_from_object_path(asset_path) or "Blueprint")
    asset_dir = capture_root / safe_filename(asset_name)
    asset_dir.mkdir(parents=True, exist_ok=True)
    graphs_dir = asset_dir / "graphs_from_uasset"
    graphs_dir.mkdir(parents=True, exist_ok=True)

    package_path = asset_dir / "uasset_package.json"
    exports_path = asset_dir / "uasset_exports.json"
    structure_json_path = asset_dir / "uasset_structure.json"
    structure_report_path = asset_dir / "uasset_structure_report.md"
    class_defaults_path = asset_dir / "uasset_class_defaults.json"
    class_defaults_report_path = asset_dir / "uasset_class_defaults_report.md"
    properties_path = asset_dir / "uasset_properties.json"
    unknown_properties_path = asset_dir / "uasset_unknown_properties.json"
    property_report_path = asset_dir / "uasset_property_parse_report.md"
    pin_links_path = asset_dir / "uasset_pin_links.json"
    pin_link_report_path = asset_dir / "uasset_link_resolution_report.md"
    partial_triage_path = asset_dir / "uasset_partial_graph_triage.json"
    partial_triage_report_path = asset_dir / "uasset_partial_graph_triage.md"
    quality_gates_path = asset_dir / "uasset_quality_gates.json"
    quality_gates_report_path = asset_dir / "uasset_quality_gates.md"
    graph_nodes_path = asset_dir / "uasset_graph_nodes.json"
    graph_report_path = asset_dir / "uasset_graph_read_report.md"
    failed_queue_path = asset_dir / "uasset_failed_graph_queue.txt"
    failed_queue_json_path = asset_dir / "uasset_failed_graph_queue.json"

    package_path.write_text(json.dumps(payload.get("package", {}), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    exports_path.write_text(json.dumps(payload.get("exports", []), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    structure = payload.get("structure", {})
    if isinstance(structure, dict):
        structure_json_path.write_text(json.dumps(structure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        structure_report_path.write_text(render_structure_report(structure), encoding="utf-8")
    class_defaults = payload.get("class_defaults", {})
    if isinstance(class_defaults, dict):
        class_defaults_path.write_text(json.dumps(class_defaults, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        class_defaults_report_path.write_text(render_uasset_class_defaults_report(class_defaults), encoding="utf-8")
    properties_path.write_text(
        json.dumps(
            {
                "schema": "blueprint-translator.uasset-properties.v1",
                "generated": payload.get("generated", ""),
                "asset_name": payload.get("asset_name", ""),
                "properties": payload.get("properties", []),
                "unknown_properties": payload.get("unknown_properties", []),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    unknown_properties_path.write_text(
        json.dumps(
            {
                "schema": "blueprint-translator.uasset-unknown-properties.v1",
                "generated": payload.get("generated", ""),
                "asset_name": payload.get("asset_name", ""),
                "unknown_properties": payload.get("unknown_properties", []),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    property_report_path.write_text(render_property_parse_report(payload), encoding="utf-8")
    pin_links_payload = payload.get("pin_links", {})
    pin_links_path.write_text(json.dumps(pin_links_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pin_link_report_path.write_text(render_pin_link_report(payload), encoding="utf-8")
    partial_triage_payload = build_partial_graph_triage(payload)
    partial_triage_path.write_text(json.dumps(partial_triage_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    partial_triage_report_path.write_text(render_partial_graph_triage_report(payload), encoding="utf-8")
    quality_gates_payload = build_quality_gate_payload(payload)
    quality_gates_path.write_text(json.dumps(quality_gates_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    quality_gates_report_path.write_text(render_quality_gate_report(payload), encoding="utf-8")
    graph_nodes_path.write_text(json.dumps(reduced_graph_nodes_payload(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    graph_report_path.write_text(render_graph_read_report(payload), encoding="utf-8")

    failed_lines: list[str] = []
    failed_records: list[dict[str, object]] = []
    graph_files = 0
    for graph in payload.get("graphs", []):
        if not isinstance(graph, dict):
            continue
        graph_name = str(graph.get("graph") or f"Graph_{graph.get('export_index')}")
        graph_type = str(graph.get("graph_type") or "Unknown")
        graph_payload = graph.get("payload", {})
        if isinstance(graph_payload, dict) and graph_payload:
            graph_file = graphs_dir / graph_payload_filename(graph_name, graph.get("export_index", ""))
            graph_file.write_text(json.dumps(graph_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            graph_files += 1
        if str(graph.get("status") or "") != "complete":
            categories = list(graph.get("failure_categories", [])) if isinstance(graph.get("failure_categories", []), list) else []
            category = str(categories[0]) if categories else "need_manual_clipboard"
            failed_lines.append(f"{graph_name} | {graph_type}")
            failed_records.append(
                {
                    "graph": graph_name,
                    "graph_type": graph_type,
                    "status": graph.get("status", ""),
                    "confidence": graph.get("confidence", ""),
                    "failure_categories": categories,
                    "primary_category": category,
                    "node_count": graph.get("node_count", 0),
                    "pin_count": graph.get("pin_count", 0),
                    "link_count": graph.get("link_count", 0),
                    "warnings": graph.get("warnings", []),
                }
            )
    failed_queue_path.write_text("\n".join(failed_lines) + ("\n" if failed_lines else ""), encoding="utf-8")
    failed_queue_json_path.write_text(
        json.dumps(
            {
                "schema": "blueprint-translator.uasset-failed-graph-queue.v1",
                "generated": payload.get("generated", ""),
                "asset_name": payload.get("asset_name", ""),
                "category_meanings": FAILURE_CATEGORY_MEANINGS,
                "graphs": failed_records,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    compare_paths: dict[str, str] = {}
    if (asset_dir / "graphs").is_dir():
        compare_payload = compare_uasset_with_clipboard(asset_dir)
        compare_paths = write_uasset_clipboard_compare_files(asset_dir, compare_payload)

    result_paths = {
        "asset_dir": str(asset_dir),
        "graphs_dir": str(graphs_dir),
        "graph_files": str(graph_files),
        "package_json": str(package_path),
        "exports_json": str(exports_path),
        "structure_json": str(structure_json_path),
        "structure_report": str(structure_report_path),
        "class_defaults_json": str(class_defaults_path),
        "class_defaults_report": str(class_defaults_report_path),
        "properties_json": str(properties_path),
        "unknown_properties_json": str(unknown_properties_path),
        "property_report": str(property_report_path),
        "pin_links_json": str(pin_links_path),
        "pin_link_report": str(pin_link_report_path),
        "partial_triage_json": str(partial_triage_path),
        "partial_triage_report": str(partial_triage_report_path),
        "quality_gates_json": str(quality_gates_path),
        "quality_gates_report": str(quality_gates_report_path),
        "graph_nodes_json": str(graph_nodes_path),
        "graph_report": str(graph_report_path),
        "failed_queue": str(failed_queue_path),
        "failed_queue_json": str(failed_queue_json_path),
    }
    result_paths.update(compare_paths)
    return result_paths


def render_candidate_report(payload: dict[str, object]) -> str:
    structure = payload.get("structure", {})
    structure_lines: list[str] = []
    if isinstance(structure, dict):
        structure_lines = [
            "- Structure parser: {}".format("loaded" if structure.get("loaded") else "not loaded"),
            "- ExportMap objects: {}".format(structure.get("export_count", 0)),
            "- EdGraph exports: {}".format(structure.get("graph_exports_count", 0)),
            "- Function exports: {}".format(structure.get("function_exports_count", 0)),
            "- Function-backed EdGraphs: {}".format(structure.get("function_graph_exports_count", 0)),
            "- Collapsed EdGraphs: {}".format(structure.get("collapsed_graph_exports_count", 0)),
        ]
    lines = [
        "# UAsset Graph Candidate Report",
        "",
        "- Asset: {}".format(payload.get("asset_name") or "-"),
        "- Asset path: {}".format(payload.get("asset_path") or "-"),
        "- UAsset path: {}".format(payload.get("uasset_path") or "-"),
        "- Method: {}".format(payload.get("method") or "-"),
        "- Raw strings: {}".format(payload.get("raw_string_count", 0)),
        "- Candidates: {}".format(payload.get("candidate_count", 0)),
        *structure_lines,
        "",
        "## Top Candidates",
        "",
        "| Name | Type Hint | Score | Reasons |",
        "| --- | --- | ---: | --- |",
    ]
    for item in payload.get("candidates", [])[:300]:
        if not isinstance(item, dict):
            continue
        lines.append(
            "| {} | {} | {} | {} |".format(
                item.get("name", ""),
                item.get("type_hint", ""),
                item.get("score", ""),
                ", ".join(str(value) for value in item.get("reasons", []) if str(value)),
            )
        )
    if int(payload.get("candidate_count", 0) or 0) > 300:
        lines.append("")
        lines.append("- Additional candidates omitted from this report.")
    lines.append("")
    return "\n".join(lines)


def render_structure_report(structure: dict[str, object]) -> str:
    lines = [
        "# UAsset Structure Report",
        "",
        "- UAsset path: {}".format(structure.get("uasset_path") or "-"),
        "- Method: {}".format(structure.get("method") or "-"),
        "- Loaded: {}".format("yes" if structure.get("loaded") else "no"),
        "- NameMap entries: {}".format(structure.get("name_count", 0)),
        "- ImportMap entries: {}".format(structure.get("import_count", 0)),
        "- ExportMap entries: {}".format(structure.get("export_count", 0)),
        "- EdGraph exports: {}".format(structure.get("graph_exports_count", 0)),
        "- Function exports: {}".format(structure.get("function_exports_count", 0)),
        "- Function-backed EdGraphs: {}".format(structure.get("function_graph_exports_count", 0)),
        "- Built-in EdGraphs: {}".format(structure.get("builtin_graph_exports_count", 0)),
        "- Collapsed EdGraphs: {}".format(structure.get("collapsed_graph_exports_count", 0)),
        "- Standalone EdGraphs: {}".format(structure.get("standalone_graph_exports_count", 0)),
        "",
        "This report is experimental. It reads the package NameMap/ImportMap/ExportMap,",
        "not Blueprint bytecode and not the editor's currently open tab state.",
        "",
        "## EdGraph Kinds",
        "",
        "| Kind | Count |",
        "| --- | ---: |",
    ]
    for item in structure.get("graph_kind_counts", []):
        if isinstance(item, dict):
            lines.append("| {} | {} |".format(item.get("kind", ""), item.get("count", "")))
    lines.extend(
        [
            "",
        "## Top Export Classes",
        "",
        "| Class | Count |",
        "| --- | ---: |",
        ]
    )
    for item in structure.get("top_export_classes", []):
        if isinstance(item, dict):
            lines.append("| {} | {} |".format(item.get("class", ""), item.get("count", "")))
    lines.extend(["", "## EdGraph Exports", "", "| Name | Kind | Outer |", "| --- | --- | --- |"])
    for item in structure.get("graph_exports", [])[:400]:
        if isinstance(item, dict):
            lines.append(
                "| {} | {} | {} |".format(
                    item.get("name", ""),
                    item.get("graph_kind", ""),
                    item.get("outer", ""),
                )
            )
    function_count = int(structure.get("function_exports_count", 0) or 0)
    if function_count:
        lines.extend(["", "## Function Exports", "", "| Name | Outer |", "| --- | --- |"])
        for item in structure.get("function_exports", [])[:400]:
            if isinstance(item, dict):
                lines.append("| {} | {} |".format(item.get("name", ""), item.get("outer", "")))
    warnings = structure.get("warnings", [])
    if isinstance(warnings, list) and warnings:
        lines.extend(["", "## Warnings", ""])
        for warning in warnings:
            lines.append("- {}".format(warning))
    lines.append("")
    return "\n".join(lines)
