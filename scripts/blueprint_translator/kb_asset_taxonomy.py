"""Deterministic, Registry-first taxonomy and representative sampling for ARK assets.

The classifier intentionally avoids loading asset contents.  It uses exact Asset
Registry classes and tags, mounted-package provenance, package shape, and an
optional verified class hierarchy.  Names and folders are never promoted to a
semantic gameplay role.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from .kb_asset_taxonomy_rules import (
    BLUEPRINT_TYPE_RULES,
    EXACT_TECHNICAL_RULES,
    SEMANTIC_CANDIDATE_TOKEN_RULES,
    SEMANTIC_ROOTS,
    TECHNICAL_ANCESTRY_RULES,
    _ruleset_digest,
    _ruleset_summary,
)
from .kb_asset_taxonomy_sampling import (
    DEFAULT_TARGET_CANDIDATE_LIMIT,
    QUOTA_CANDIDATE_MINIMUM,
    QUOTA_CANDIDATE_MULTIPLIER,
    QUOTA_TARGET_CANDIDATE_PREFIXES,
    _candidate_rank,
    _distribution,
    _keep_smallest,
    _sample_targets,
    _select_samples,
    _update_distributions,
)


TAXONOMY_ROW_SCHEMA = "ark.kb.asset-taxonomy-package.v1"
TAXONOMY_MANIFEST_SCHEMA = "ark.kb.asset-taxonomy-manifest.v1"
UNKNOWN_ROW_SCHEMA = "ark.kb.asset-taxonomy-unknown.v1"


class TaxonomyBuildError(RuntimeError):
    """Fail-closed input, classification, or publication error."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_line(value: object) -> bytes:
    return (_canonical_json(value) + "\n").encode("utf-8")


def _sha256_file(path: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    byte_count = 0
    line_count = 0
    last_byte = b""
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            byte_count += len(chunk)
            line_count += chunk.count(b"\n")
            last_byte = chunk[-1:]
    if byte_count and last_byte != b"\n":
        line_count += 1
    return digest.hexdigest(), byte_count, line_count


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(_canonical_line(value))


def _require_mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TaxonomyBuildError(code)
    return value


def _require_nonempty_string(value: object, code: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise TaxonomyBuildError(code)
    return text


def _manifest_integer(mapping: Mapping[str, object], key: str, code: str) -> int:
    if key not in mapping or mapping[key] is None or isinstance(mapping[key], bool):
        raise TaxonomyBuildError(code)
    try:
        value = int(mapping[key])
    except (TypeError, ValueError):
        raise TaxonomyBuildError(code) from None
    if value < 0:
        raise TaxonomyBuildError(code)
    return value


def _load_json(path: Path, code: str) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TaxonomyBuildError(f"{code}: {type(exc).__name__}") from None
    return _require_mapping(value, code)


def _resolve_manifest_member(
    manifest_path: Path,
    relative_path: object,
    code: str,
) -> Path:
    text = _require_nonempty_string(relative_path, code).replace("/", os.sep)
    candidate = (manifest_path.parent / text).resolve()
    root = manifest_path.parent.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise TaxonomyBuildError(code) from None
    if not candidate.is_file():
        raise TaxonomyBuildError(code)
    return candidate


def _validate_registry_manifest(
    assets_path: Path,
    manifest_path: Path,
) -> dict[str, object]:
    manifest = _load_json(manifest_path, "SOURCE_MANIFEST_INVALID")
    if manifest.get("status") != "COMPLETE":
        raise TaxonomyBuildError("SOURCE_MANIFEST_NOT_COMPLETE")
    if manifest.get("schema") not in {
        "ark.kb.registry-snapshot.v1",
        "ark.kb.registry-snapshot.v2",
    }:
        raise TaxonomyBuildError("SOURCE_MANIFEST_SCHEMA_UNSUPPORTED")
    files = _require_mapping(manifest.get("files"), "SOURCE_MANIFEST_FILES_INVALID")
    assets_meta = _require_mapping(
        files.get("assets"), "SOURCE_MANIFEST_ASSETS_INVALID"
    )
    manifest_asset_path = _resolve_manifest_member(
        manifest_path,
        assets_meta.get("path")
        or _require_mapping(
            manifest.get("outputs"), "SOURCE_MANIFEST_OUTPUTS_INVALID"
        ).get("assets"),
        "SOURCE_MANIFEST_ASSET_PATH_INVALID",
    )
    if manifest_asset_path != assets_path.resolve():
        raise TaxonomyBuildError("SOURCE_MANIFEST_ASSET_PATH_MISMATCH")
    return {
        "schema": str(manifest.get("schema")),
        "generationId": str(manifest.get("generation_id") or "UNKNOWN"),
        "expectedSha256": _require_nonempty_string(
            assets_meta.get("sha256"), "SOURCE_ASSETS_SHA256_MISSING"
        ),
        "expectedBytes": _manifest_integer(
            assets_meta, "bytes", "SOURCE_ASSETS_BYTES_INVALID"
        ),
        "expectedRecords": _manifest_integer(
            assets_meta,
            "record_count",
            "SOURCE_ASSETS_RECORD_COUNT_INVALID",
        ),
        "dependenciesEnabled": manifest.get("dependencies_enabled") is True,
    }


_REFERENCE_RE = re.compile(r"'(?P<target>/[^']+)'$")


def _reference_target(value: object) -> str:
    text = "" if value is None else str(value).strip()
    match = _REFERENCE_RE.search(text)
    if match:
        return match.group("target")
    return text if text.startswith("/") else ""


def _mount_point(package_name: str) -> str:
    parts = package_name.strip("/").split("/", 1)
    return f"/{parts[0]}" if parts and parts[0] else "/UNKNOWN"


def _source_scope(mount_point: str) -> str:
    if mount_point == "/Game":
        return "ARK_PROJECT_CONTENT"
    if mount_point == "/Engine":
        return "UNREAL_ENGINE_CONTENT"
    if mount_point == "/UNKNOWN":
        return "UNKNOWN_SOURCE"
    return "MOUNTED_PLUGIN_CONTENT"


def _storage_area(package_name: str, mount_point: str) -> str:
    parts = package_name.strip("/").split("/")
    if len(parts) > 1:
        return f"{mount_point}/{parts[1]}"
    return mount_point


class _HierarchyIndex:
    def __init__(self, parents: Mapping[str, tuple[str, str, str]]) -> None:
        self.parents = dict(parents)
        self._cache: dict[str, dict[str, tuple[str, int, str]]] = {}

    def ancestors(self, class_path: str) -> dict[str, tuple[str, int, str]]:
        if class_path in self._cache:
            return self._cache[class_path]
        result: dict[str, tuple[str, int, str]] = {
            class_path: ("CONFIRMED", 0, "class_identity")
        }
        seen = {class_path}
        current = class_path
        chain_status = "CONFIRMED"
        depth = 0
        while current in self.parents:
            parent, edge_status, source = self.parents[current]
            if not parent or parent in seen:
                break
            depth += 1
            if edge_status != "CONFIRMED":
                chain_status = "CANDIDATE"
            result[parent] = (chain_status, depth, source)
            seen.add(parent)
            current = parent
        self._cache[class_path] = result
        return result


def _load_hierarchy(
    manifest_path: Path,
    expected_registry_sha256: str | None,
) -> tuple[_HierarchyIndex, dict[str, object]]:
    manifest = _load_json(manifest_path, "CLASS_HIERARCHY_MANIFEST_INVALID")
    if manifest.get("status") != "COMPLETE":
        raise TaxonomyBuildError("CLASS_HIERARCHY_NOT_COMPLETE")
    if manifest.get("schema") not in {
        "ark.kb.class-hierarchy-snapshot.v1",
        "ark.kb.class-hierarchy-snapshot.v2",
    }:
        raise TaxonomyBuildError("CLASS_HIERARCHY_SCHEMA_UNSUPPORTED")
    files = _require_mapping(manifest.get("files"), "CLASS_HIERARCHY_FILES_INVALID")
    classes_meta = _require_mapping(
        files.get("classes"), "CLASS_HIERARCHY_CLASSES_INVALID"
    )
    relative = classes_meta.get("path")
    if not relative:
        outputs = _require_mapping(
            manifest.get("outputs"), "CLASS_HIERARCHY_OUTPUTS_INVALID"
        )
        relative = outputs.get("classes")
    classes_path = _resolve_manifest_member(
        manifest_path, relative, "CLASS_HIERARCHY_PATH_INVALID"
    )
    actual_sha, actual_bytes, actual_lines = _sha256_file(classes_path)
    if actual_sha != classes_meta.get("sha256"):
        raise TaxonomyBuildError("CLASS_HIERARCHY_SHA256_MISMATCH")
    if actual_bytes != _manifest_integer(
        classes_meta, "bytes", "CLASS_HIERARCHY_BYTES_INVALID"
    ):
        raise TaxonomyBuildError("CLASS_HIERARCHY_BYTES_MISMATCH")
    expected_records = _manifest_integer(
        classes_meta, "record_count", "CLASS_HIERARCHY_RECORD_COUNT_INVALID"
    )
    if actual_lines != expected_records:
        raise TaxonomyBuildError("CLASS_HIERARCHY_RECORD_COUNT_MISMATCH")
    producer = manifest.get("producer") or {}
    if not isinstance(producer, Mapping):
        producer = {}
    runtime_identity = producer.get("runtime_identity") or {}
    if not isinstance(runtime_identity, Mapping):
        runtime_identity = {}
    devkit_build_id = str(runtime_identity.get("devkit_build_id") or "UNSPECIFIED")
    expected_build_id = (
        f"registry-{expected_registry_sha256}" if expected_registry_sha256 else ""
    )
    same_registry = bool(expected_build_id and devkit_build_id == expected_build_id)
    parents: dict[str, tuple[str, str, str]] = {}
    with classes_path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = _require_mapping(json.loads(line), "CLASS_HIERARCHY_ROW_INVALID")
            except json.JSONDecodeError:
                raise TaxonomyBuildError(
                    f"CLASS_HIERARCHY_ROW_INVALID:{line_number}"
                ) from None
            if row.get("schema") != "ark.kb.class-hierarchy-row.v1":
                raise TaxonomyBuildError(
                    f"CLASS_HIERARCHY_ROW_SCHEMA_INVALID:{line_number}"
                )
            child = _require_nonempty_string(
                row.get("class_path"), "CLASS_HIERARCHY_CLASS_PATH_MISSING"
            )
            parent = str(row.get("super_class_path") or "").strip()
            parent_status = str(row.get("parent_status") or "NOT_RECOVERED")
            if not parent:
                continue
            edge_status = (
                "CONFIRMED"
                if same_registry and parent_status in {"CONFIRMED", "CONFIRMED_ROOT"}
                else "CANDIDATE"
            )
            edge = (parent, edge_status, str(row.get("source") or "UNKNOWN"))
            existing = parents.get(child)
            if existing is not None and existing != edge:
                raise TaxonomyBuildError("CLASS_HIERARCHY_PARENT_CONFLICT")
            parents[child] = edge
    return _HierarchyIndex(parents), {
        "status": (
            "VERIFIED_SAME_REGISTRY_GENERATION"
            if same_registry
            else "VERIFIED_MANIFEST_UNBOUND"
        ),
        "registryBinding": "MATCH" if same_registry else "UNBOUND_OR_MISMATCH",
        "devkitBuildId": devkit_build_id,
        "schema": str(manifest.get("schema")),
        "generationId": str(manifest.get("generation_id") or "UNKNOWN"),
        "classesSha256": actual_sha,
        "recordCount": actual_lines,
    }


def _classify_technical(
    asset_class_path: str,
    tags: Mapping[str, object],
    hierarchy: _HierarchyIndex | None,
) -> tuple[str, str, str, list[dict[str, str]]]:
    exact = EXACT_TECHNICAL_RULES.get(asset_class_path)
    if exact is not None:
        family, kind, rule_id = exact
        evidence = [
            {
                "sourceKind": "unreal_asset_registry",
                "sourceField": "asset_class_path",
                "observedValue": asset_class_path,
                "ruleId": rule_id,
            }
        ]
        if asset_class_path == "/Script/Engine.Blueprint":
            blueprint_type = str(tags.get("BlueprintType") or "")
            override = BLUEPRINT_TYPE_RULES.get(blueprint_type)
            if override is not None:
                family, kind, tag_rule = override
                evidence.append(
                    {
                        "sourceKind": "unreal_asset_registry",
                        "sourceField": "tags.BlueprintType",
                        "observedValue": blueprint_type,
                        "ruleId": tag_rule,
                    }
                )
        return family, kind, "HIGH", evidence
    if not asset_class_path:
        return "UNKNOWN", "UNKNOWN_CLASS", "UNKNOWN", []
    if hierarchy is not None:
        ancestors = hierarchy.ancestors(asset_class_path)
        for root, family, kind in TECHNICAL_ANCESTRY_RULES:
            match = ancestors.get(root)
            if match is None:
                continue
            status, depth, source = match
            return (
                family,
                kind,
                "HIGH" if status == "CONFIRMED" else "MEDIUM",
                [
                    {
                        "sourceKind": "class_hierarchy",
                        "sourceField": "super_class_path",
                        "observedValue": root,
                        "ruleId": f"ancestry.{kind.lower()}.v1",
                        "pathStatus": status,
                        "depth": str(depth),
                        "edgeSource": source,
                    }
                ],
            )
    return (
        "UNKNOWN",
        "UNMAPPED_EXACT_CLASS",
        "UNKNOWN",
        [
            {
                "sourceKind": "unreal_asset_registry",
                "sourceField": "asset_class_path",
                "observedValue": asset_class_path,
                "ruleId": "unmapped.exact-class.v1",
            }
        ],
    )


def _semantic_anchors(
    asset_class_path: str,
    tags: Mapping[str, object],
    hierarchy: _HierarchyIndex | None,
) -> list[dict[str, object]]:
    class_candidates: list[tuple[str, str]] = []
    for tag in ("GeneratedClass", "ParentClass", "NativeParentClass"):
        target = _reference_target(tags.get(tag))
        if target:
            class_candidates.append((target, f"tags.{tag}"))
    if asset_class_path:
        class_candidates.append((asset_class_path, "asset_class_path"))
    best: dict[str, dict[str, object]] = {}
    for class_path, source_field in class_candidates:
        direct = {class_path: ("CONFIRMED", 0, "registry_identity_or_tag")}
        ancestry = hierarchy.ancestors(class_path) if hierarchy is not None else direct
        for role, root in SEMANTIC_ROOTS:
            match = ancestry.get(root)
            if match is None:
                continue
            status, depth, source = match
            candidate = {
                "role": role,
                "rootClassPath": root,
                "status": status,
                "depth": depth,
                "sourceField": source_field,
                "edgeSource": source,
            }
            existing = best.get(role)
            if (
                existing is None
                or (
                    (candidate["status"] == "CONFIRMED")
                    > (existing["status"] == "CONFIRMED")
                )
                or (
                    candidate["status"] == existing["status"]
                    and int(candidate["depth"]) < int(existing["depth"])
                )
            ):
                best[role] = candidate
    return [best[key] for key in sorted(best)]


def _semantic_candidates(
    package_name: str,
    asset_name: str,
) -> list[dict[str, object]]:
    """Return recall-only hints; never promote a path or name to confirmation."""

    observed_tokens = {
        token.casefold()
        for token in re.split(r"[^A-Za-z0-9]+", package_name + "/" + asset_name)
        if token
    }
    candidates = []
    for role, tokens in SEMANTIC_CANDIDATE_TOKEN_RULES:
        matched = sorted(observed_tokens & set(tokens))
        if not matched:
            continue
        candidates.append(
            {
                "role": role,
                "status": "CANDIDATE",
                "confidence": "LOW",
                "evidenceKind": "exact_path_or_name_token",
                "observedTokens": matched,
            }
        )
    return candidates


def _acquisition_routes(
    family: str,
    kind: str,
    tags: Mapping[str, object],
) -> list[str]:
    routes: set[str] = set()
    if kind in {
        "BLUEPRINT",
        "FUNCTION_LIBRARY",
        "MACRO_LIBRARY",
        "BLUEPRINT_INTERFACE",
        "WIDGET_BLUEPRINT",
        "ANIMATION_BLUEPRINT",
    }:
        routes.update({"BLUEPRINT_GRAPH", "COMPONENTS", "DEFAULT_PROPERTIES"})
    if kind in {"DATA_TABLE", "CURVE_TABLE", "STRING_TABLE"}:
        routes.add("TABULAR_ROWS")
    if kind in {"DATA_ASSET", "PRIMARY_DATA_ASSET", "DATA_ASSET_SUBCLASS"}:
        routes.add("DEFAULT_PROPERTIES")
    if family == "WORLD":
        routes.add("WORLD_STRUCTURE")
    if family in {"VISUAL", "AUDIO", "ANIMATION", "VFX", "UI"}:
        routes.add("ASSET_PROPERTIES")
    if any(
        tags.get(key) for key in ("GeneratedClass", "ParentClass", "NativeParentClass")
    ):
        routes.add("CLASS_ANCESTRY")
    return sorted(routes)


def _available_capabilities(
    confidence: str,
    tags: Mapping[str, object],
    anchors: Iterable[Mapping[str, object]],
) -> list[str]:
    capabilities = {
        "REGISTRY_METADATA",
        "REGISTRY_IDENTITY",
        "EXACT_ASSET_CLASS",
        "SOURCE_PROVENANCE",
        "PACKAGE_LAYOUT",
    }
    if confidence != "UNKNOWN":
        capabilities.add("TECHNICAL_TAXONOMY")
    if any(
        tags.get(key) for key in ("GeneratedClass", "ParentClass", "NativeParentClass")
    ):
        capabilities.add("DIRECT_CLASS_TAGS")
    if any(item.get("status") == "CONFIRMED" for item in anchors):
        capabilities.add("CONFIRMED_SEMANTIC_CLASS_ANCHOR")
    return sorted(capabilities)


def _capability_status(
    *,
    is_blueprint: bool,
    has_class_route: bool,
    layout_kind: str,
) -> dict[str, str]:
    return {
        "identity": "CURRENT",
        "technicalType": "CURRENT",
        "classAncestry": "PARTIAL" if has_class_route else "NOT_CAPTURED",
        "dependenciesAndReferences": "NOT_REQUESTED",
        "blueprintGraph": "NOT_CAPTURED" if is_blueprint else "NOT_APPLICABLE",
        "defaults": "NOT_CAPTURED",
        "components": "NOT_CAPTURED" if is_blueprint else "NOT_APPLICABLE",
        "mapPlacement": (
            "PACKAGE_IDENTITY_ONLY"
            if layout_kind.startswith("WORLD_PARTITION_EXTERNAL_")
            else "NOT_CAPTURED"
        ),
        "worldStructure": (
            "WORLD_PARTITION_PACKAGE_IDENTITY_ONLY"
            if layout_kind == "WORLD_PARTITION_EXTERNAL_ACTOR"
            else "WORLD_PARTITION_FOLDER_ORGANIZATION_ONLY"
            if layout_kind == "WORLD_PARTITION_EXTERNAL_OBJECT"
            else "NOT_CAPTURED"
        ),
        "nativeImplementation": "NOT_CAPTURED",
        "runtimeValidation": "NOT_RUN",
    }


def _layout_kind(package_name: str, families: set[str]) -> str:
    parts = package_name.strip("/").split("/")
    if len(parts) > 1 and parts[1] == "__ExternalActors__":
        return "WORLD_PARTITION_EXTERNAL_ACTOR"
    if len(parts) > 1 and parts[1] == "__ExternalObjects__":
        return "WORLD_PARTITION_EXTERNAL_OBJECT"
    if "WORLD" in families:
        return "WORLD_PACKAGE"
    return "STANDARD_PACKAGE"


def _placement_metadata(
    package_name: str,
    layout_kind: str,
    asset_class_paths: Iterable[str],
) -> dict[str, object]:
    classes = sorted(set(asset_class_paths))
    if layout_kind not in {
        "WORLD_PARTITION_EXTERNAL_ACTOR",
        "WORLD_PARTITION_EXTERNAL_OBJECT",
    }:
        return {
            "placementLayer": "DEFINITION_OR_SUPPORT_PACKAGE",
            "ownerWorldPath": "",
            "ownerWorldPathStatus": "NOT_APPLICABLE",
            "actorClassPaths": [],
            "samplingLayer": "DEFINITION_OR_SUPPORT",
            "sampleClusterKey": package_name,
        }
    parts = package_name.strip("/").split("/")
    owner_parts = parts[2:-3] if len(parts) >= 6 else []
    owner_world = f"/{parts[0]}/" + "/".join(owner_parts) if owner_parts else ""
    sampling_layer = layout_kind
    primary_class = classes[0] if classes else "UNKNOWN"
    return {
        "placementLayer": (
            "EXTERNAL_ACTOR_PACKAGE"
            if layout_kind == "WORLD_PARTITION_EXTERNAL_ACTOR"
            else "EXTERNAL_OBJECT_PACKAGE"
        ),
        "ownerWorldPath": owner_world,
        "ownerWorldPathStatus": (
            "PATH_CONVENTION_CANDIDATE" if owner_world else "NOT_RECOVERED"
        ),
        "actorClassPaths": classes,
        "samplingLayer": sampling_layer,
        "sampleClusterKey": f"{owner_world or 'UNKNOWN'}|{primary_class}",
    }


def _classification_confidence(values: Iterable[str]) -> str:
    observed = set(values)
    if "UNKNOWN" in observed:
        return "UNKNOWN"
    if "MEDIUM" in observed:
        return "MEDIUM"
    return "HIGH"


def _normalize_registry_row(
    row: Mapping[str, object], line_number: int
) -> dict[str, object]:
    if row.get("schema") != "ark.kb.registry-asset.v1":
        raise TaxonomyBuildError(f"REGISTRY_ROW_SCHEMA_INVALID:{line_number}")
    tags = row.get("tags") or {}
    if not isinstance(tags, Mapping):
        raise TaxonomyBuildError(f"REGISTRY_ROW_TAGS_INVALID:{line_number}")
    normalized_tags = {str(key): value for key, value in tags.items()}
    return {
        "object_path": _require_nonempty_string(
            row.get("object_path"), f"REGISTRY_OBJECT_PATH_MISSING:{line_number}"
        ),
        "package_name": _require_nonempty_string(
            row.get("package_name"), f"REGISTRY_PACKAGE_NAME_MISSING:{line_number}"
        ),
        "package_path": str(row.get("package_path") or ""),
        "asset_name": _require_nonempty_string(
            row.get("asset_name"), f"REGISTRY_ASSET_NAME_MISSING:{line_number}"
        ),
        "asset_class_path": str(row.get("asset_class_path") or "").strip(),
        "tags": normalized_tags,
    }


def _create_spool(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=FILE")
    connection.execute(
        """
        CREATE TABLE assets (
            object_path TEXT PRIMARY KEY,
            package_name TEXT NOT NULL,
            package_path TEXT NOT NULL,
            asset_name TEXT NOT NULL,
            asset_class_path TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            row_digest TEXT NOT NULL
        ) WITHOUT ROWID
        """
    )
    connection.execute(
        "CREATE INDEX idx_assets_package ON assets(package_name, object_path)"
    )
    return connection


def _load_registry_spool(
    assets_path: Path,
    connection: sqlite3.Connection,
) -> dict[str, object]:
    raw_digest = hashlib.sha256()
    raw_bytes = 0
    raw_records = 0
    duplicate_records = 0
    with assets_path.open("rb") as handle, connection:
        for line_number, raw_line in enumerate(handle, 1):
            raw_digest.update(raw_line)
            raw_bytes += len(raw_line)
            if not raw_line.strip():
                raise TaxonomyBuildError(f"REGISTRY_ROW_EMPTY:{line_number}")
            try:
                text = raw_line.decode("utf-8-sig" if line_number == 1 else "utf-8")
                parsed = _require_mapping(
                    json.loads(text), f"REGISTRY_ROW_INVALID:{line_number}"
                )
            except (UnicodeError, json.JSONDecodeError):
                raise TaxonomyBuildError(
                    f"REGISTRY_ROW_INVALID:{line_number}"
                ) from None
            row = _normalize_registry_row(parsed, line_number)
            canonical = _canonical_json(parsed)
            row_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO assets(
                    object_path, package_name, package_path, asset_name,
                    asset_class_path, tags_json, row_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["object_path"],
                    row["package_name"],
                    row["package_path"],
                    row["asset_name"],
                    row["asset_class_path"],
                    _canonical_json(row["tags"]),
                    row_hash,
                ),
            )
            if cursor.rowcount == 0:
                existing = connection.execute(
                    "SELECT row_digest FROM assets WHERE object_path = ?",
                    (row["object_path"],),
                ).fetchone()
                if existing is None or existing[0] != row_hash:
                    raise TaxonomyBuildError("DUPLICATE_OBJECT_IDENTITY_CONFLICT")
                duplicate_records += 1
            raw_records += 1
    unique_objects = int(
        connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    )
    inventory_digest = hashlib.sha256()
    for object_path, row_hash in connection.execute(
        "SELECT object_path, row_digest FROM assets ORDER BY object_path"
    ):
        inventory_digest.update(object_path.encode("utf-8"))
        inventory_digest.update(b"\0")
        inventory_digest.update(row_hash.encode("ascii"))
        inventory_digest.update(b"\n")
    return {
        "rawSha256": raw_digest.hexdigest(),
        "rawBytes": raw_bytes,
        "rawRecords": raw_records,
        "uniqueObjects": unique_objects,
        "duplicateRecords": duplicate_records,
        "assetInventorySha256": inventory_digest.hexdigest(),
    }


def _object_taxonomy(
    row: tuple[str, str, str, str, str, str],
    hierarchy: _HierarchyIndex | None,
) -> dict[str, object]:
    object_path, package_name, _package_path, asset_name, asset_class, tags_json = row
    tags = _require_mapping(json.loads(tags_json), "SPOOL_TAGS_INVALID")
    family, kind, confidence, evidence = _classify_technical(
        asset_class, tags, hierarchy
    )
    observed_anchors = _semantic_anchors(asset_class, tags, hierarchy)
    anchors = [item for item in observed_anchors if item.get("status") == "CONFIRMED"]
    candidates = _semantic_candidates(package_name, asset_name)
    candidates.extend(
        {
            "role": item["role"],
            "status": "CANDIDATE",
            "confidence": "MEDIUM",
            "evidenceKind": "unbound_or_unverified_class_ancestry",
            "rootClassPath": item["rootClassPath"],
            "depth": item["depth"],
            "edgeSource": item["edgeSource"],
        }
        for item in observed_anchors
        if item.get("status") != "CONFIRMED"
    )
    references = sorted(
        {
            target
            for key in ("GeneratedClass", "ParentClass", "NativeParentClass")
            if (target := _reference_target(tags.get(key)))
        }
    )
    return {
        "objectPath": object_path,
        "assetName": asset_name,
        "assetClassPath": asset_class or "UNKNOWN",
        "technicalFamily": family,
        "technicalKind": kind,
        "classificationConfidence": confidence,
        "classReferences": references,
        "confirmedSemanticAnchors": anchors,
        "semanticCandidates": candidates,
        "availableCapabilities": _available_capabilities(confidence, tags, anchors),
        "acquisitionRoutes": _acquisition_routes(family, kind, tags),
        "classificationEvidence": evidence,
    }


def _package_taxonomy(
    package_name: str,
    rows: list[tuple[str, str, str, str, str, str]],
    hierarchy: _HierarchyIndex | None,
) -> dict[str, object]:
    objects = [_object_taxonomy(row, hierarchy) for row in rows]
    leaf = package_name.rsplit("/", 1)[-1]
    matching = [item for item in objects if item["assetName"] == leaf]
    primary = min(matching or objects, key=lambda item: str(item["objectPath"]))
    categories = sorted(
        {(str(item["technicalFamily"]), str(item["technicalKind"])) for item in objects}
    )
    if len(objects) == 1:
        shape = "SINGLE_OBJECT"
    elif len(categories) == 1:
        shape = "MULTI_OBJECT_SAME_CATEGORY"
    else:
        shape = "MULTI_CATEGORY"
    families = {family for family, _kind in categories}
    mount_point = _mount_point(package_name)
    layout_kind = _layout_kind(package_name, families)
    placement = _placement_metadata(
        package_name,
        layout_kind,
        (str(item["assetClassPath"]) for item in objects),
    )
    semantic_by_key: dict[tuple[str, str], dict[str, object]] = {}
    semantic_candidates_by_role: dict[str, dict[str, object]] = {}
    for item in objects:
        for anchor in item["confirmedSemanticAnchors"]:
            key = (str(anchor["role"]), str(anchor["rootClassPath"]))
            existing = semantic_by_key.get(key)
            if existing is None or (
                (anchor["status"] == "CONFIRMED") > (existing["status"] == "CONFIRMED")
            ):
                semantic_by_key[key] = dict(anchor)
        for candidate in item["semanticCandidates"]:
            role = str(candidate["role"])
            existing_candidate = semantic_candidates_by_role.get(role)
            if existing_candidate is None or (
                candidate.get("confidence") == "MEDIUM"
                and existing_candidate.get("confidence") == "LOW"
            ):
                semantic_candidates_by_role[role] = dict(candidate)
    acquisition_routes = sorted(
        {route for item in objects for route in item["acquisitionRoutes"]}
    )
    available_capabilities = sorted(
        {capability for item in objects for capability in item["availableCapabilities"]}
    )
    if layout_kind in {
        "WORLD_PARTITION_EXTERNAL_ACTOR",
        "WORLD_PARTITION_EXTERNAL_OBJECT",
    }:
        acquisition_routes = sorted({*acquisition_routes, "WORLD_STRUCTURE"})
    storage_area = _storage_area(package_name, mount_point)
    owner_world_path = str(placement["ownerWorldPath"])
    origin_area = _storage_area(owner_world_path or package_name, mount_point)
    origin_area_status = (
        str(placement["ownerWorldPathStatus"])
        if layout_kind.startswith("WORLD_PARTITION_EXTERNAL_")
        else "PACKAGE_PATH"
    )
    fingerprint_payload = {
        "packageName": package_name,
        "objects": objects,
        "mountPoint": mount_point,
        "layoutKind": layout_kind,
    }
    profile_fingerprint = hashlib.sha256(
        _canonical_json(fingerprint_payload).encode("utf-8")
    ).hexdigest()
    return {
        "schema": TAXONOMY_ROW_SCHEMA,
        "packageName": package_name,
        "mountPoint": mount_point,
        "sourceScope": _source_scope(mount_point),
        "storageArea": storage_area,
        "originArea": origin_area,
        "originAreaStatus": origin_area_status,
        "layoutKind": layout_kind,
        **placement,
        "objectCount": len(objects),
        "packageShape": shape,
        "isMixedPackage": len(categories) > 1,
        "primaryObjectPath": primary["objectPath"],
        "primaryTechnicalFamily": primary["technicalFamily"],
        "primaryTechnicalKind": primary["technicalKind"],
        "classificationConfidence": _classification_confidence(
            str(item["classificationConfidence"]) for item in objects
        ),
        "technicalCategories": [
            {"family": family, "kind": kind} for family, kind in categories
        ],
        "confirmedSemanticAnchors": [
            semantic_by_key[key] for key in sorted(semantic_by_key)
        ],
        "semanticCandidates": [
            semantic_candidates_by_role[key]
            for key in sorted(semantic_candidates_by_role)
        ],
        "semanticStatus": (
            "CONFIRMED"
            if semantic_by_key
            else "CANDIDATE"
            if semantic_candidates_by_role
            else "UNKNOWN"
        ),
        "availableCapabilities": available_capabilities,
        "acquisitionRoutes": acquisition_routes,
        "capabilityStatus": _capability_status(
            is_blueprint=any(
                str(item["technicalKind"])
                in {
                    "BLUEPRINT",
                    "FUNCTION_LIBRARY",
                    "MACRO_LIBRARY",
                    "BLUEPRINT_INTERFACE",
                    "WIDGET_BLUEPRINT",
                    "ANIMATION_BLUEPRINT",
                }
                for item in objects
            ),
            has_class_route="CLASS_ANCESTRY" in acquisition_routes,
            layout_kind=layout_kind,
        ),
        "profileFingerprint": profile_fingerprint,
        "objects": objects,
    }


def _package_rows(
    connection: sqlite3.Connection,
) -> Iterable[tuple[str, list[tuple[str, str, str, str, str, str]]]]:
    cursor = connection.execute(
        """
        SELECT object_path, package_name, package_path, asset_name,
               asset_class_path, tags_json
        FROM assets
        ORDER BY package_name, object_path
        """
    )
    current_name = ""
    current: list[tuple[str, str, str, str, str, str]] = []
    for row in cursor:
        package_name = str(row[1])
        if current and package_name != current_name:
            yield current_name, current
            current = []
        current_name = package_name
        current.append(row)
    if current:
        yield current_name, current


def _build_report(manifest: Mapping[str, object], sample: Mapping[str, object]) -> str:
    distributions = _require_mapping(
        manifest.get("distributions"), "REPORT_DISTRIBUTIONS_INVALID"
    )

    def table(title: str, key: str, limit: int = 30) -> list[str]:
        values = _require_mapping(distributions.get(key, {}), "REPORT_COUNTER_INVALID")
        ordered = sorted(values.items(), key=lambda item: (-int(item[1]), item[0]))
        lines = [f"## {title}", "", "| 类别 | 包数量 |", "|---|---:|"]
        lines.extend(f"| `{name}` | {count} |" for name, count in ordered[:limit])
        if len(ordered) > limit:
            lines.append(f"| 其余 {len(ordered) - limit} 类 | 见 manifest |")
        lines.append("")
        return lines

    counts = _require_mapping(manifest.get("counts"), "REPORT_COUNTS_INVALID")
    coverage = _require_mapping(sample.get("coverage"), "REPORT_SAMPLE_INVALID")
    constraints = _require_mapping(
        sample.get("samplingConstraints"), "REPORT_CONSTRAINTS_INVALID"
    )
    policy = _require_mapping(coverage.get("policy"), "REPORT_POLICY_INVALID")
    target_populations = _require_mapping(
        coverage.get("targetPopulations"), "REPORT_TARGET_POPULATIONS_INVALID"
    )
    uncovered_targets = coverage.get("uncoveredTargets")
    if not isinstance(uncovered_targets, list):
        raise TaxonomyBuildError("REPORT_UNCOVERED_TARGETS_INVALID")
    top_unknown_packages = sum(
        int(population)
        for target, population in target_populations.items()
        if str(target).startswith("unknownClassPath=")
    )
    unknown_class_count = int(policy["topUnknownClassPaths"]) + int(
        policy["detailedUnknownClassPathsNotIndividuallyOptimized"]
    )
    long_tail_unknown_packages = max(
        0, int(counts["technicallyUnknownPackages"]) - top_unknown_packages
    )
    lines = [
        "# ARK DevKit 资产多维分类与抽样报告",
        "",
        "本报告基于 Asset Registry 元数据和可选类继承证据，不加载全部资产内容。",
        "目录/名称只用于来源和存储布局，不用于确认玩法语义。",
        "",
        "## 总览",
        "",
        f"- Registry 行：{counts['registryRows']}",
        f"- 唯一资产对象：{counts['uniqueAssetObjects']}",
        f"- 技术类型已覆盖对象：{counts['technicallyKnownAssetObjects']}",
        f"- 技术类型 UNKNOWN 对象：{counts['technicallyUnknownAssetObjects']}",
        f"- 分类包：{counts['packages']}",
        f"- 技术分类未知包：{counts['technicallyUnknownPackages']}",
        f"- 语义锚点未解析包：{counts['semanticallyUnresolvedPackages']}",
        f"- 抽样：{sample['actualSampleSize']} / {sample['requestedSampleSize']}",
        f"- 抽样状态：{sample['status']}",
        f"- 已配置抽样目标覆盖：{coverage['coveredTargets']} / {coverage['totalTargets']}",
        f"- 硬配额缺口：{len(constraints['unmetQuotaTargets'])}",
        "",
        "## 证据边界",
        "",
        "- Registry 元数据：已遍历并自校验。",
        f"- 类继承：{manifest['evidenceCoverage']['classHierarchy']}。",
        "- 依赖拓扑：本轮未请求，不能据此评价引用中心性。",
        "- 资产内容深读：本轮未运行；样本清单用于下一阶段深查。",
        "- 这是面向类别/缺口覆盖的目的抽样，不是统计随机样本，不能用样本比例估计全库成功率。",
        "- 配额修复采用有界启发式；COMPLETE 证明本轮配额已全部达到，DEGRADED 不证明不存在其他可行组合。",
        "- originArea 是从包路径或 World Partition owner 路径约定得到；候选状态不等于内容归属证明。",
        "- WORLD_STRUCTURE 对 ExternalObject 仅表示 world/folder 组织信息，不表示已获得场景坐标。",
        "",
    ]
    lines.extend(
        [
            "## 样本覆盖与 UNKNOWN 结构",
            "",
            f"- 已配置目标：{coverage['coveredTargets']} / {coverage['totalTargets']}；状态 `{sample['status']}`。",
            f"- 未覆盖已配置目标：{len(uncovered_targets)}。",
            f"- originArea：高频前 {policy['topOriginAreas']} 类逐项覆盖；其余 {policy['detailedOriginAreasNotIndividuallyOptimized']} 类仅以 {policy['originAreaTailHashBuckets']} 个稳定 hash bucket 分散抽样，不代表逐 originArea 全覆盖。",
            f"- UNKNOWN：{counts['technicallyUnknownPackages']} 个包、{unknown_class_count} 个不同 class path。",
            f"- 高频前 {policy['topUnknownClassPaths']} 个 UNKNOWN class：约 {top_unknown_packages} 个包；其余长尾约 {long_tail_unknown_packages} 个包。",
            f"- 长尾仅用 {policy['unknownClassHashBuckets']} 个稳定 hash bucket 保证分散抽样，不代表逐 class 全覆盖。",
            "",
        ]
    )
    if uncovered_targets:
        lines.extend(
            [
                "### 未覆盖目标",
                "",
                *(f"- `{target}`" for target in uncovered_targets),
                "",
            ]
        )
    lines.extend(table("技术族", "technicalFamily"))
    lines.extend(table("来源挂载点", "mountPoint"))
    lines.extend(table("物理存储区", "storageArea"))
    lines.extend(table("来源区域候选", "originArea"))
    lines.extend(table("存储布局", "layoutKind"))
    lines.extend(table("已确认玩法/职责语义锚点", "confirmedSemanticRole"))
    lines.extend(table("低置信语义召回候选", "candidateSemanticRole"))
    lines.extend(table("当前可用能力", "availableCapability"))
    lines.extend(table("下一步采集路线", "acquisitionRoute"))
    lines.extend(
        [
            "## 下一步",
            "",
            "先人工确认 `sample_review_zh.md` 的样本分布，再只对入选包运行 BTC 深查。",
            "抽样未覆盖目标保留在 sample manifest，不会被静默丢弃。",
            "",
        ]
    )
    return "\n".join(lines)


def _markdown_cell(value: object) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _build_sample_review(sample: Mapping[str, object]) -> str:
    """Render a compact human-review surface for the deterministic sample."""

    rows = sample.get("samples")
    if not isinstance(rows, list):
        raise TaxonomyBuildError("SAMPLE_REVIEW_ROWS_INVALID")
    lines = [
        "# BTC 深查样本人工复核表",
        "",
        "本表只用于确认下一阶段要深查的代表性资产；当前尚未加载这些资产内容。",
        "`待采集路线` 只是一条待采集路线，不表示图、默认值、依赖或运行时结论已经存在。",
        "路径/名称产生的语义候选均为 LOW，只用于召回，不能替代类证据。",
        "",
        "| 序号 | 包路径 | 技术分类 | 来源与布局 | 语义状态 | 已确认语义 | LOW 候选 | UNKNOWN class | 当前可用能力 | 能力状态 | 待采集路线 | 入选原因 |",
        "|---:|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in rows:
        item_map = _require_mapping(item, "SAMPLE_REVIEW_ITEM_INVALID")
        dimensions = _require_mapping(
            item_map.get("dimensions"), "SAMPLE_REVIEW_DIMENSIONS_INVALID"
        )

        def joined(key: str) -> str:
            values = dimensions.get(key, [])
            if not isinstance(values, list):
                raise TaxonomyBuildError(f"SAMPLE_REVIEW_FIELD_INVALID:{key}")
            return ", ".join(str(value) for value in values) or "—"

        reasons = item_map.get("selectionReasons", [])
        if not isinstance(reasons, list):
            raise TaxonomyBuildError("SAMPLE_REVIEW_REASONS_INVALID")
        capability_status = _require_mapping(
            dimensions.get("capabilityStatus"),
            "SAMPLE_REVIEW_CAPABILITY_STATUS_INVALID",
        )
        cells = [
            item_map.get("selectionOrder", ""),
            item_map.get("packageName", ""),
            f"{dimensions.get('technicalFamily', '')} / {dimensions.get('technicalKind', '')}",
            (
                f"{dimensions.get('sourceScope', '')} / "
                f"{dimensions.get('mountPoint', '')} / "
                f"storage={dimensions.get('storageArea', '')} / "
                f"origin={dimensions.get('originArea', '')} "
                f"({dimensions.get('originAreaStatus', '')}) / "
                f"{dimensions.get('layoutKind', '')}"
            ),
            dimensions.get("semanticStatus", ""),
            joined("confirmedSemanticRoles"),
            joined("candidateSemanticRoles"),
            joined("unknownAssetClassPaths"),
            joined("availableCapabilities"),
            ", ".join(
                f"{key}={capability_status[key]}" for key in sorted(capability_status)
            ),
            joined("acquisitionRoutes"),
            ", ".join(str(reason) for reason in reasons) or "—",
        ]
        lines.append("| " + " | ".join(_markdown_cell(cell) for cell in cells) + " |")
    lines.append("")
    return "\n".join(lines)


def _file_descriptor(path: Path) -> dict[str, object]:
    sha, byte_count, lines = _sha256_file(path)
    return {
        "path": path.name,
        "sha256": sha,
        "bytes": byte_count,
        "lines": lines,
    }


def build_taxonomy(
    assets_path: str | Path,
    output_dir: str | Path,
    *,
    source_manifest: str | Path | None = None,
    class_hierarchy_manifest: str | Path | None = None,
    seed: str = "ark-taxonomy-v1",
    sample_size: int = 160,
) -> dict[str, object]:
    """Build an immutable taxonomy directory and deterministic sample manifest."""

    source = Path(assets_path).resolve()
    output = Path(output_dir).resolve()
    if not source.is_file():
        raise TaxonomyBuildError("SOURCE_ASSETS_MISSING")
    if output.exists():
        raise TaxonomyBuildError("OUTPUT_ALREADY_EXISTS")
    if sample_size < 0:
        raise TaxonomyBuildError("SAMPLE_SIZE_INVALID")
    if not seed:
        raise TaxonomyBuildError("SAMPLE_SEED_EMPTY")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent)
    )
    connection: sqlite3.Connection | None = None
    try:
        registry_binding = (
            _validate_registry_manifest(source, Path(source_manifest).resolve())
            if source_manifest is not None
            else None
        )
        spool_path = staging / ".taxonomy-spool.sqlite"
        connection = _create_spool(spool_path)
        source_stats = _load_registry_spool(source, connection)
        if registry_binding is not None:
            if source_stats["rawSha256"] != registry_binding["expectedSha256"]:
                raise TaxonomyBuildError("SOURCE_ASSETS_SHA256_MISMATCH")
            if source_stats["rawBytes"] != registry_binding["expectedBytes"]:
                raise TaxonomyBuildError("SOURCE_ASSETS_BYTES_MISMATCH")
            if source_stats["rawRecords"] != registry_binding["expectedRecords"]:
                raise TaxonomyBuildError("SOURCE_ASSETS_RECORD_COUNT_MISMATCH")
            source_binding: dict[str, object] = {
                "status": "VERIFIED_MANIFEST",
                "schema": registry_binding["schema"],
                "generationId": registry_binding["generationId"],
                "assetsSha256": source_stats["rawSha256"],
                "assetInventorySha256": source_stats["assetInventorySha256"],
                "dependenciesEnabled": registry_binding["dependenciesEnabled"],
            }
        else:
            source_binding = {
                "status": "SELF_HASHED_UNBOUND",
                "assetInventorySha256": source_stats["assetInventorySha256"],
                "dependenciesEnabled": False,
            }
        if class_hierarchy_manifest is not None:
            hierarchy, hierarchy_binding = _load_hierarchy(
                Path(class_hierarchy_manifest).resolve(),
                str(source_stats["rawSha256"]),
            )
        else:
            hierarchy = None
            hierarchy_binding = {"status": "NOT_PROVIDED"}

        taxonomy_path = staging / "package_taxonomy.jsonl"
        unknown_path = staging / "unknown_packages.jsonl"
        distributions: dict[str, Counter[str]] = defaultdict(Counter)
        target_counts: Counter[str] = Counter()
        target_heaps: dict[str, list[tuple[int, str]]] = defaultdict(list)
        global_heap: list[tuple[int, str]] = []
        package_count = 0
        unknown_count = 0
        semantic_unresolved = 0
        candidate_limit = DEFAULT_TARGET_CANDIDATE_LIMIT
        global_limit = max(
            sample_size * QUOTA_CANDIDATE_MULTIPLIER,
            QUOTA_CANDIDATE_MINIMUM,
        )
        with (
            taxonomy_path.open("wb") as taxonomy_handle,
            unknown_path.open("wb") as unknown_handle,
        ):
            for package_name, rows in _package_rows(connection):
                package = _package_taxonomy(package_name, rows, hierarchy)
                taxonomy_handle.write(_canonical_line(package))
                package_count += 1
                _update_distributions(distributions, package)
                if package["classificationConfidence"] == "UNKNOWN":
                    unknown_count += 1
                    unknown_handle.write(
                        _canonical_line(
                            {
                                "schema": UNKNOWN_ROW_SCHEMA,
                                "packageName": package_name,
                                "assetClassPaths": sorted(
                                    {
                                        item["assetClassPath"]
                                        for item in package["objects"]
                                    }
                                ),
                                "reasonCode": "UNMAPPED_OR_MISSING_ASSET_CLASS",
                            }
                        )
                    )
                if package["semanticStatus"] != "CONFIRMED":
                    semantic_unresolved += 1
                rank = _candidate_rank(seed, package)
                _keep_smallest(global_heap, rank, package_name, global_limit)
                for target in _sample_targets(package):
                    target_counts[target] += 1
                    target_candidate_limit = (
                        global_limit
                        if target.startswith(QUOTA_TARGET_CANDIDATE_PREFIXES)
                        else candidate_limit
                    )
                    _keep_smallest(
                        target_heaps[target],
                        rank,
                        package_name,
                        target_candidate_limit,
                    )

        taxonomy_sha, taxonomy_bytes, taxonomy_lines = _sha256_file(taxonomy_path)
        taxonomy_fingerprint = hashlib.sha256(
            _canonical_json(
                {
                    "rulesetSha256": _ruleset_digest(),
                    "assetInventorySha256": source_stats["assetInventorySha256"],
                    "classHierarchy": hierarchy_binding,
                    "packageTaxonomySha256": taxonomy_sha,
                }
            ).encode("utf-8")
        ).hexdigest()
        sample = _select_samples(
            taxonomy_path,
            seed=seed,
            sample_size=sample_size,
            package_count=package_count,
            target_counts=target_counts,
            target_heaps=target_heaps,
            global_heap=global_heap,
            taxonomy_fingerprint=taxonomy_fingerprint,
        )
        sample_path = staging / "sample_manifest.json"
        _write_json(sample_path, sample)
        sample_review_path = staging / "sample_review_zh.md"
        sample_review_path.write_text(_build_sample_review(sample), encoding="utf-8")

        manifest: dict[str, object] = {
            "schema": TAXONOMY_MANIFEST_SCHEMA,
            "status": "COMPLETE",
            "publicationEligible": (
                source_binding["status"] == "VERIFIED_MANIFEST"
                and sample["status"] == "COMPLETE"
                and hierarchy_binding["status"]
                in {"NOT_PROVIDED", "VERIFIED_SAME_REGISTRY_GENERATION"}
            ),
            "sampleReadiness": sample["status"],
            "authorityScope": (
                "REGISTRY_TECHNICAL_WITH_CLASS_ANCESTRY"
                if hierarchy_binding["status"] == "VERIFIED_SAME_REGISTRY_GENERATION"
                else "REGISTRY_TECHNICAL_WITH_UNBOUND_HIERARCHY_CANDIDATES"
                if hierarchy is not None
                else "REGISTRY_TECHNICAL_WITH_TAGGED_PARENTS"
            ),
            "ruleset": _ruleset_summary(),
            "taxonomyFingerprint": taxonomy_fingerprint,
            "sourceBinding": source_binding,
            "classHierarchyBinding": hierarchy_binding,
            "evidenceCoverage": {
                "assetRegistryMetadata": "COMPLETE",
                "classHierarchy": hierarchy_binding["status"],
                "dependencyTopology": (
                    "AVAILABLE_IN_SOURCE_NOT_INGESTED"
                    if source_binding["dependenciesEnabled"]
                    else "NOT_REQUESTED"
                ),
                "assetContentDeepRead": "NOT_RUN",
                "runtimeBehavior": "NOT_RUN",
            },
            "counts": {
                "registryRows": source_stats["rawRecords"],
                "uniqueAssetObjects": source_stats["uniqueObjects"],
                "duplicateRegistryRows": source_stats["duplicateRecords"],
                "packages": package_count,
                "technicallyKnownAssetObjects": int(
                    distributions["assetObjectClassificationConfidence"]["HIGH"]
                    + distributions["assetObjectClassificationConfidence"]["MEDIUM"]
                ),
                "technicallyUnknownAssetObjects": int(
                    distributions["assetObjectClassificationConfidence"]["UNKNOWN"]
                ),
                "technicallyUnknownPackages": unknown_count,
                "semanticallyUnresolvedPackages": semantic_unresolved,
            },
            "distributions": {
                key: _distribution(distributions[key]) for key in sorted(distributions)
            },
            "files": {
                "packageTaxonomy": {
                    "path": taxonomy_path.name,
                    "sha256": taxonomy_sha,
                    "bytes": taxonomy_bytes,
                    "lines": taxonomy_lines,
                    "rowSchema": TAXONOMY_ROW_SCHEMA,
                },
                "unknownPackages": _file_descriptor(unknown_path),
                "sampleManifest": _file_descriptor(sample_path),
                "sampleReview": _file_descriptor(sample_review_path),
            },
            "warnings": [
                "Query routes are eligible deep-read routes, not completed queries.",
                "Semantic roles require an exact observed class root or verified ancestry; unresolved packages remain explicit.",
                "World Partition origin areas are path-convention candidates; ExternalObject WORLD_STRUCTURE is folder organization only.",
                *(
                    [
                        "Sample hard quotas have shortfalls; review sample_manifest.json before deep reads."
                    ]
                    if sample["status"] == "DEGRADED"
                    else []
                ),
                *(
                    [
                        "Sample hard quotas are complete, but configured coverage targets remain; review sample_manifest.json before deep reads."
                    ]
                    if sample["status"] == "COVERAGE_PARTIAL"
                    else []
                ),
                *(
                    [
                        "Class hierarchy is not bound to this Registry generation; ancestry-derived classifications remain candidates and publication is blocked."
                    ]
                    if hierarchy_binding["status"] == "VERIFIED_MANIFEST_UNBOUND"
                    else []
                ),
            ],
        }
        report_path = staging / "taxonomy_report_zh.md"
        report_path.write_text(_build_report(manifest, sample), encoding="utf-8")
        manifest["files"]["report"] = _file_descriptor(report_path)
        manifest_path = staging / "taxonomy_manifest.json"
        _write_json(manifest_path, manifest)

        connection.close()
        connection = None
        spool_path.unlink()
        os.replace(staging, output)
        return {
            "status": "COMPLETE",
            "outputDirectory": str(output),
            "taxonomyFingerprint": taxonomy_fingerprint,
            "packageCount": package_count,
            "sampleCount": sample["actualSampleSize"],
            "sampleReadiness": sample["status"],
            "publicationEligible": manifest["publicationEligible"],
        }
    except TaxonomyBuildError:
        raise
    except Exception as exc:
        raise TaxonomyBuildError(f"TAXONOMY_BUILD_FAILED:{type(exc).__name__}") from exc
    finally:
        if connection is not None:
            connection.close()
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
