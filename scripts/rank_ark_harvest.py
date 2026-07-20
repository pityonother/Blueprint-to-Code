#!/usr/bin/env python3
"""Build compact, evidence-aware ARK creature/resource harvesting rankings."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.harvest_ranking import (
    evaluate_attack_resource,
    extract_creature_attacks,
    extract_harvest_component,
    extract_resource_damage_overrides,
    rank_harvest_rows,
)
from blueprint_translator.harvest_report_validation import (
    COMPACT_SCHEMA,
    build_canonical_ai_view,
    build_ranking_revision_fields,
)
from blueprint_translator.uasset_graphs import (
    normalize_blueprint_object_path,
    object_path_to_uasset_path,
    object_ref_name,
    parse_uasset_package,
    read_uasset_class_defaults,
)


SCHEMA = "ark-harvest-ranking/v1"
DEFAULT_DEVKIT_ROOT = Path(r"C:\Program Files\Epic Games\ARKDevkit")
DEFAULT_CREATURES = [
    {
        "name": "Magmasaur",
        "objectPath": "/Game/Genesis/Dinos/Cherufe/Cherufe_Character_BP.Cherufe_Character_BP",
    },
    {
        "name": "Ankylosaurus",
        "objectPath": "/Game/PrimalEarth/Dinos/Ankylo/Ankylo_Character_BP.Ankylo_Character_BP",
    },
    {
        "name": "Doedicurus",
        "objectPath": "/Game/PrimalEarth/Dinos/Doedicurus/Doed_Character_BP.Doed_Character_BP",
    },
    {
        "name": "Therizinosaurus",
        "objectPath": "/Game/PrimalEarth/Dinos/Therizinosaurus/Therizino_Character_BP.Therizino_Character_BP",
    },
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Decode creature attacks, damage-type overrides, and harvest components into a compact "
            "comparison report. Scores are evidence-bounded engine indices, not observed resource yield."
        )
    )
    parser.add_argument("--devkit-root", type=Path, default=DEFAULT_DEVKIT_ROOT)
    resource_group = parser.add_mutually_exclusive_group()
    resource_group.add_argument(
        "--resource",
        action="append",
        default=[],
        help="Target resource class/name; repeatable. Default: PrimalItemResource_Metal_C.",
    )
    resource_group.add_argument(
        "--all-resources",
        action="store_true",
        help="Rank every resource class recovered from the component catalog.",
    )
    parser.add_argument(
        "--creature",
        action="append",
        default=[],
        help="Creature as Label=/Game/.../Character_BP.Character_BP; repeatable.",
    )
    parser.add_argument(
        "--creature-file",
        type=Path,
        help="JSON array of {name, objectPath}; replaces the representative preset.",
    )
    parser.add_argument(
        "--component",
        action="append",
        default=[],
        help="Limit to a HarvestComponent filename/class (without .uasset); repeatable.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("analysis") / "harvest_rankings")
    parser.add_argument("--max-components", type=int, default=0, help="Optional safety limit; 0 means all.")
    parser.add_argument(
        "--discover-all-components",
        action="store_true",
        help=(
            "Add every *HarvestComponent*.uasset under Content to the standard "
            "PrimalEarth component directory."
        ),
    )
    parser.add_argument(
        "--extra-component",
        action="append",
        default=[],
        help="Exact component .uasset or /Game object path; repeatable.",
    )
    parser.add_argument(
        "--extra-component-file",
        action="append",
        type=Path,
        default=[],
        help="UTF-8 file containing exact component paths; repeatable.",
    )
    return parser.parse_args(argv)


def normalize_resource(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    if not text.startswith("PrimalItemResource_"):
        text = f"PrimalItemResource_{text}"
    if not text.endswith("_C"):
        text += "_C"
    return text


def resource_slug(resource: str) -> str:
    value = resource.removeprefix("PrimalItemResource_").removesuffix("_C")
    return "".join(char.lower() if char.isalnum() else "_" for char in value).strip("_") or "resource"


def resource_report_slug(resources: Iterable[str], *, selection_mode: str) -> str:
    if selection_mode == "ALL_DISCOVERED":
        return "all_resources"
    return "_".join(resource_slug(resource) for resource in resources)


def uasset_object_path(path: Path, content_root: Path) -> str:
    relative = path.resolve().relative_to(content_root.resolve()).with_suffix("").as_posix()
    return f"/Game/{relative}.{path.stem}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class AssetReader:
    def __init__(self) -> None:
        self._packages: dict[Path, dict[str, Any]] = {}
        self._defaults: dict[Path, dict[str, Any]] = {}

    def package(self, path: Path) -> dict[str, Any]:
        resolved = path.resolve()
        if resolved not in self._packages:
            self._packages[resolved] = parse_uasset_package(resolved)
        return self._packages[resolved]

    def defaults(self, path: Path) -> dict[str, Any]:
        resolved = path.resolve()
        if resolved not in self._defaults:
            self._defaults[resolved] = read_uasset_class_defaults(self.package(resolved), resolved.stem)
        return self._defaults[resolved]

    def generated_class_parent(self, path: Path) -> str:
        package = self.package(path)
        exports = package.get("exports")
        imports = package.get("imports")
        if not isinstance(exports, list) or not isinstance(imports, list):
            return ""
        expected = f"{path.stem}_C"
        generated = next(
            (
                row
                for row in exports
                if isinstance(row, dict)
                and str(row.get("object_name") or "") == expected
                and str(row.get("class_name") or "") == "BlueprintGeneratedClass"
            ),
            None,
        )
        if not isinstance(generated, dict):
            return ""
        super_index = int(generated.get("super_index") or 0)
        if super_index >= 0:
            return object_ref_name(super_index, imports, exports)
        import_index = -super_index - 1
        if not 0 <= import_index < len(imports):
            return ""
        imported = imports[import_index]
        if not isinstance(imported, dict):
            return ""
        name = str(imported.get("object_name") or "")
        package_path = ""
        outer_index = imported.get("outer_index")
        seen: set[int] = set()
        while isinstance(outer_index, int) and outer_index < 0:
            outer_import_index = -outer_index - 1
            if outer_import_index in seen or not 0 <= outer_import_index < len(imports):
                break
            seen.add(outer_import_index)
            outer = imports[outer_import_index]
            if not isinstance(outer, dict):
                break
            outer_name = str(outer.get("object_name") or "")
            if str(outer.get("class_name") or "") == "Package" or outer_name.startswith("/Game/"):
                package_path = outer_name.split(".", 1)[0]
                break
            outer_index = outer.get("outer_index")
        return f"{package_path}.{name}" if package_path and name else name

    def effective_defaults(
        self,
        path: Path,
        class_index: dict[str, Path],
        *,
        stack: tuple[Path, ...] = (),
    ) -> tuple[list[dict[str, Any]], list[Path]]:
        resolved = path.resolve()
        if resolved in stack:
            return [], [resolved]
        merged: dict[str, dict[str, Any]] = {}
        source_chain: list[Path] = []
        parent_name = self.generated_class_parent(resolved)
        parent_path = class_index.get(parent_name) or class_index.get(parent_name.casefold())
        if parent_path and parent_path.resolve() != resolved:
            parent_rows, parent_chain = self.effective_defaults(
                parent_path,
                class_index,
                stack=(*stack, resolved),
            )
            source_chain.extend(parent_chain)
            for row in parent_rows:
                name = str(row.get("name") or "")
                if name:
                    merged[name] = row
        payload = self.defaults(resolved)
        properties = payload.get("properties")
        for row in properties if isinstance(properties, list) else []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "")
            if name:
                merged[name] = row
        source_chain.append(resolved)
        return list(merged.values()), source_chain


def build_class_index(
    paths: Iterable[Path],
    *,
    content_root: Path | None = None,
) -> dict[str, Path]:
    index: dict[str, Path] = {}
    resolved_paths = sorted({item.resolve() for item in paths if item.is_file()})
    by_stem: dict[str, list[Path]] = defaultdict(list)
    for path in resolved_paths:
        by_stem[path.stem.casefold()].append(path)
        if content_root is not None:
            try:
                relative = path.relative_to(content_root.resolve()).with_suffix("").as_posix()
            except ValueError:
                relative = ""
            if relative:
                package_path = f"/Game/{relative}"
                for key in (
                    package_path,
                    f"{package_path}.{path.stem}",
                    f"{package_path}.{path.stem}_C",
                ):
                    index[key] = path
                    index[key.casefold()] = path
    for group in by_stem.values():
        if len(group) != 1:
            continue
        path = group[0]
        for key in (path.stem, f"{path.stem}_C"):
            index[key] = path
            index[key.casefold()] = path
    return index


def load_creature_specs(args: argparse.Namespace) -> list[dict[str, str]]:
    if args.creature_file:
        payload = json.loads(args.creature_file.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("--creature-file must contain a JSON array")
        return [
            {"name": str(row.get("name") or ""), "objectPath": str(row.get("objectPath") or "")}
            for row in payload
            if isinstance(row, dict)
        ]
    if not args.creature:
        return [dict(row) for row in DEFAULT_CREATURES]
    specs: list[dict[str, str]] = []
    for raw in args.creature:
        label, separator, object_path = str(raw).partition("=")
        if not separator:
            object_path = label
            label = Path(object_path.rsplit(".", 1)[0]).name
        specs.append({"name": label.strip(), "objectPath": object_path.strip()})
    return specs


def resolve_creatures(
    specs: list[dict[str, str]],
    content_root: Path,
    reader: AssetReader,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    creatures: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for spec in specs:
        normalized = normalize_blueprint_object_path(spec.get("objectPath", ""))
        path, attempted = object_path_to_uasset_path(normalized, extra_roots=[content_root])
        if not path:
            failures.append(
                {
                    "name": spec.get("name"),
                    "objectPath": normalized,
                    "reasonCode": "CREATURE_ASSET_NOT_FOUND",
                    "attempted": [str(item) for item in attempted[:10]],
                }
            )
            continue
        payload = reader.defaults(path)
        properties = payload.get("properties")
        property_rows = properties if isinstance(properties, list) else []
        attacks = extract_creature_attacks(property_rows)
        attack_infos = next(
            (
                row
                for row in property_rows
                if isinstance(row, dict) and str(row.get("name") or "") == "AttackInfos"
            ),
            None,
        )
        attack_parse = attack_infos.get("array_parse") if isinstance(attack_infos, dict) else None
        if not isinstance(attack_parse, dict) or attack_parse.get("parsed") is not True:
            detail = (
                str(attack_parse.get("error") or "AttackInfos array was not decoded")
                if isinstance(attack_parse, dict)
                else "AttackInfos property was not recovered"
            )
            failures.append(
                {
                    "name": spec.get("name") or path.stem,
                    "objectPath": normalized,
                    "path": str(path.resolve()),
                    "reasonCode": "ATTACK_INFOS_NOT_RECOVERED",
                    "detail": detail,
                }
            )
        creatures.append(
            {
                "name": spec.get("name") or path.stem,
                "objectPath": normalized,
                "path": path.resolve(),
                "attacks": attacks,
                "warnings": payload.get("warnings") or [],
            }
        )
    return creatures, failures


def discover_components(
    *,
    content_root: Path,
    reader: AssetReader,
    selected_names: set[str],
    max_components: int,
    target_resources: set[str] | None = None,
    discover_all_content: bool = False,
    extra_component_paths: Iterable[Path] = (),
) -> tuple[
    list[dict[str, Any]],
    dict[str, list[str]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    harvest_root = content_root / "PrimalEarth" / "CoreBlueprints" / "HarvestComponents"
    all_files_set = {path.resolve() for path in harvest_root.rglob("*.uasset")}
    if discover_all_content:
        all_files_set.update(
            path.resolve() for path in content_root.rglob("*HarvestComponent*.uasset")
        )
    all_files_set.update(
        path.resolve() for path in extra_component_paths if Path(path).is_file()
    )
    all_files = sorted(all_files_set)
    class_index = build_class_index(all_files, content_root=content_root)
    files = list(all_files)
    if selected_names:
        files = [path for path in files if path.stem in selected_names or f"{path.stem}_C" in selected_names]
    if max_components > 0:
        files = files[:max_components]
    components: list[dict[str, Any]] = []
    resource_catalog: dict[str, list[str]] = defaultdict(list)
    failures: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    requested_resources = None if target_resources is None else set(target_resources)
    for path in files:
        object_path = uasset_object_path(path, content_root)
        try:
            properties, source_chain = reader.effective_defaults(path, class_index)
            fact = extract_harvest_component(
                properties,
                component=path.stem,
                object_path=object_path,
            )
            fact["path"] = path.resolve()
            fact["sourceChain"] = [item.resolve() for item in source_chain]
            recovered_resources = {
                str(entry.get("resource") or "")
                for entry in fact.get("resourceEntries", [])
                if isinstance(entry, dict) and str(entry.get("resource") or "")
            }
            matched_resources = sorted(
                recovered_resources
                if requested_resources is None
                else recovered_resources & requested_resources
            )
            semantic_gaps = sorted({str(gap) for gap in fact.get("gaps") or [] if str(gap)})
            semantic_gap = bool(semantic_gaps)
            matched = bool(matched_resources)
            if semantic_gap and matched:
                discovery_status = "MATCHED_WITH_SEMANTIC_GAP"
            elif semantic_gap:
                discovery_status = "SEMANTIC_GAP"
            elif matched:
                discovery_status = "MATCHED"
            else:
                discovery_status = "DECODED_NO_TARGET_RESOURCE"
            fact["matchedResources"] = matched_resources
            fact["discoveryStatus"] = discovery_status
            components.append(fact)
            for entry in fact.get("resourceEntries", []):
                resource = str(entry.get("resource") or "") if isinstance(entry, dict) else ""
                if resource and fact["objectPath"] not in resource_catalog[resource]:
                    resource_catalog[resource].append(fact["objectPath"])
            manifest.append(
                {
                    "component": path.stem,
                    "componentObjectPath": object_path,
                    "path": str(path.resolve()),
                    "attempted": True,
                    "decoded": True,
                    "semanticGap": semantic_gap,
                    "matched": matched,
                    "matchedResources": matched_resources,
                    "gaps": semantic_gaps,
                    "discoveryStatus": discovery_status,
                    "sourceChain": [str(item.resolve()) for item in source_chain],
                }
            )
            if semantic_gap:
                failures.append(
                    {
                        "component": path.stem,
                        "path": str(path.resolve()),
                        "reasonCode": "COMPONENT_SEMANTIC_GAP",
                        "gaps": semantic_gaps,
                    }
                )
        except Exception as exc:  # keep discovery bounded and report each failed asset explicitly
            failure = {
                "component": path.stem,
                "path": str(path.resolve()),
                "reasonCode": "COMPONENT_DECODE_FAILED",
                "detail": str(exc),
            }
            failures.append(failure)
            manifest.append(
                {
                    "component": path.stem,
                    "componentObjectPath": object_path,
                    "path": str(path.resolve()),
                    "attempted": True,
                    "decoded": False,
                    "semanticGap": False,
                    "matched": False,
                    "matchedResources": [],
                    "gaps": ["COMPONENT_DECODE_FAILED"],
                    "discoveryStatus": "DECODE_FAILED",
                    "sourceChain": [str(path.resolve())],
                }
            )
    return components, dict(sorted(resource_catalog.items())), failures, manifest


def load_extra_component_paths(args: argparse.Namespace, content_root: Path) -> list[Path]:
    values = [str(value) for value in args.extra_component]
    for manifest_path in args.extra_component_file:
        values.extend(
            line.strip()
            for line in manifest_path.read_text(
                encoding="utf-8-sig", errors="replace"
            ).splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    paths: list[Path] = []
    for value in values:
        text = value.strip().strip("\"'").replace("\\", "/")
        if text.startswith("/Game/"):
            package = text.split(".", 1)[0].removeprefix("/Game/")
            path = content_root / Path(package + ".uasset")
        else:
            path = Path(text)
            if not path.is_absolute():
                path = content_root / path
        paths.append(path.resolve())
    return sorted(set(paths))


def discover_damage_type_assets(
    content_root: Path,
    *,
    prefer_rg: bool = True,
) -> tuple[list[Path], str]:
    """Discover DLC damage types in one native walk; avoid Path.rglob over all Content."""

    root = Path(content_root).resolve()
    rg = shutil.which("rg") if prefer_rg else None
    if rg:
        completed = subprocess.run(
            [rg, "--files", "-g", "*DmgType*.uasset", str(root)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode in {0, 1}:
            return (
                sorted(
                    {
                        Path(line.strip()).resolve()
                        for line in completed.stdout.splitlines()
                        if line.strip()
                    }
                ),
                "RIPGREP",
            )

    found: list[Path] = []
    for directory, _subdirectories, filenames in os.walk(root):
        base = Path(directory)
        for filename in filenames:
            if "DmgType" in filename and filename.endswith(".uasset"):
                found.append((base / filename).resolve())
    return sorted(set(found)), "OS_WALK"


def build_damage_context(
    *,
    creatures: list[dict[str, Any]],
    resources: list[str],
    content_root: Path,
    reader: AssetReader,
) -> tuple[
    dict[str, str],
    dict[tuple[str, str], str],
    list[dict[str, Any]],
    set[Path],
    dict[str, list[str]],
]:
    damage_root = content_root / "PrimalEarth" / "CoreBlueprints" / "DamageTypes"
    damage_files = set(damage_root.glob("*.uasset"))
    discovered_damage_files, _discovery_backend = discover_damage_type_assets(content_root)
    damage_files.update(discovered_damage_files)
    exact_paths: dict[str, Path] = {}
    for creature in creatures:
        for attack in creature.get("attacks", []):
            if not isinstance(attack, dict):
                continue
            damage_type = str(attack.get("damageType") or "")
            object_path = str(attack.get("damageTypeObjectPath") or "")
            if not damage_type or not object_path.startswith("/Game/"):
                continue
            package = object_path.split(".", 1)[0].removeprefix("/Game/")
            candidate = (content_root / Path(package + ".uasset")).resolve()
            if candidate.is_file():
                exact_paths[damage_type] = candidate
                damage_files.add(candidate)
    damage_files = sorted(path.resolve() for path in damage_files if path.is_file())
    damage_index = build_class_index(damage_files, content_root=content_root)
    parent_map: dict[str, str] = {}
    overrides: dict[tuple[str, str], str] = {}
    facts: list[dict[str, Any]] = []
    used_paths: set[Path] = set()
    gaps_by_damage_type: dict[str, list[str]] = {}
    pending = {
        str(attack.get("damageType") or "")
        for creature in creatures
        for attack in creature.get("attacks", [])
        if isinstance(attack, dict) and isinstance(attack.get("damageType"), str)
    }
    visited: set[str] = set()
    while pending:
        damage_type = pending.pop()
        if not damage_type or damage_type in visited:
            continue
        visited.add(damage_type)
        path = exact_paths.get(damage_type) or damage_index.get(damage_type)
        if not path:
            gaps_by_damage_type[damage_type] = ["DAMAGE_TYPE_ASSET_NOT_FOUND"]
            facts.append(
                {
                    "damageType": damage_type,
                    "path": None,
                    "parent": None,
                    "overrides": [],
                    "gaps": ["DAMAGE_TYPE_ASSET_NOT_FOUND"],
                }
            )
            continue
        used_paths.add(path.resolve())
        try:
            parent = reader.generated_class_parent(path)
            if parent:
                parent_map[damage_type] = parent
                if parent.endswith("_C") and parent not in visited:
                    pending.add(parent)
            properties, source_chain = reader.effective_defaults(path, damage_index)
        except Exception as exc:
            gaps_by_damage_type[damage_type] = ["DAMAGE_TYPE_DECODE_FAILED"]
            facts.append(
                {
                    "damageType": damage_type,
                    "path": str(path.resolve()),
                    "parent": None,
                    "overrides": [],
                    "gaps": ["DAMAGE_TYPE_DECODE_FAILED"],
                    "detail": str(exc),
                    "sourceChain": [str(path.resolve())],
                }
            )
            continue
        override_fact = extract_resource_damage_overrides(properties, damage_type)
        gaps_by_damage_type[damage_type] = sorted(
            {str(gap) for gap in override_fact["gaps"] if str(gap)}
        )
        for key, replacement in override_fact["overrides"].items():
            if key[1] in resources:
                overrides[key] = replacement
                if replacement.endswith("_C") and replacement not in visited:
                    pending.add(replacement)
        facts.append(
            {
                "damageType": damage_type,
                "path": str(path.resolve()),
                "parent": parent or None,
                "overrides": [
                    {"resource": resource, "replacementDamageType": replacement}
                    for (source, resource), replacement in sorted(override_fact["overrides"].items())
                    if source == damage_type
                ],
                "gaps": override_fact["gaps"],
                "sourceChain": [str(item.resolve()) for item in source_chain],
            }
        )
        used_paths.update(item.resolve() for item in source_chain)
    return (
        parent_map,
        overrides,
        sorted(facts, key=lambda row: row["damageType"]),
        used_paths,
        gaps_by_damage_type,
    )


def best_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[
            (
                str(row.get("resource") or ""),
                str(row.get("componentObjectPath") or row.get("component") or ""),
                str(row.get("creatureObjectPath") or row.get("creature") or ""),
            )
        ].append(row)
    selected: list[dict[str, Any]] = []
    for group in groups.values():
        if not group:
            continue
        ranked = [row for row in group if row.get("rankingStatus") == "RANKED"]
        unknown = [row for row in group if row.get("rankingStatus") == "UNRANKED"]
        pool = ranked or unknown or group
        selected.append(rank_harvest_rows(pool)[0])
    return rank_harvest_rows(selected)


def compact_row(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "resource",
        "component",
        "componentObjectPath",
        "creature",
        "creatureObjectPath",
        "attackIndex",
        "attackName",
        "rankingStatus",
        "reasonCode",
        "sourceDamageType",
        "effectiveDamageType",
        "damageOverrideApplied",
        "damageTypeMatch",
        "baseDamage",
        "attackInterval",
        "damageMultiplier",
        "harvestQuantityMultiplier",
        "resourceWeight",
        "resourceWeightShare",
        "overrideQuantityMin",
        "overrideQuantityMax",
        "harvestPressurePerSecond",
        "engineComparisonIndex",
        "maxHarvestHealth",
        "harvestHealthGiveResourceInterval",
        "observedYieldPerSecond",
        "missingFacts",
        "missingFactsByScope",
        "warnings",
        "warningsByScope",
        "scoreBasis",
    )
    return {key: row.get(key) for key in keys if key in row}


def build_resource_candidates(
    resources: Iterable[str],
    selected_best_rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep a canonical candidate set for every requested resource."""

    rows = [dict(row) for row in selected_best_rows if isinstance(row, dict)]
    result: list[dict[str, Any]] = []
    for resource in resources:
        candidates = rank_harvest_rows(
            row for row in rows if str(row.get("resource") or "") == str(resource)
        )
        statuses = {str(row.get("rankingStatus") or "") for row in candidates}
        if "RANKED" in statuses:
            status = "RANKED_CANDIDATES_AVAILABLE"
        elif "UNRANKED" in statuses:
            status = "ONLY_UNRANKED_CANDIDATES"
        elif "INCOMPATIBLE" in statuses:
            status = "ONLY_INCOMPATIBLE_CANDIDATES"
        else:
            status = "NO_ROWS"
        result.append(
            {
                "resource": str(resource),
                "discoveryStatus": status,
                "rankedDiscoveryStatus": (
                    "RANKED_ROWS_AVAILABLE"
                    if "RANKED" in statuses
                    else "NO_RANKED_ROW"
                ),
                "bestRows": [compact_row(row) for row in candidates],
            }
        )
    return result


def summarize_component_gaps(manifest: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for record in manifest:
        if not isinstance(record, dict):
            continue
        for gap in record.get("gaps") or []:
            code = str(gap or "")
            if not code:
                continue
            summary = grouped.setdefault(code, {"gap": code, "count": 0, "examples": []})
            summary["count"] += 1
            component = str(record.get("componentObjectPath") or record.get("component") or "")
            if component and component not in summary["examples"] and len(summary["examples"]) < 5:
                summary["examples"].append(component)
    return [grouped[key] for key in sorted(grouped)]


def scan_manifest_hash(manifest: Iterable[dict[str, Any]]) -> str:
    canonical = json.dumps(
        [record for record in manifest if isinstance(record, dict)],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_ai_view(payload: dict[str, Any], *, detail_location: str) -> dict[str, Any]:
    return build_canonical_ai_view(payload, detail_location=detail_location)


def _render_resource_view(view: dict[str, Any]) -> list[str]:
    resource = str(view.get("resource") or "-")
    focus_component = str(view.get("focusComponent") or "")
    counts = view.get("candidateCounts") if isinstance(view.get("candidateCounts"), dict) else {}
    discovery_coverage = (
        view.get("rankedDiscoveryCoverage")
        if isinstance(view.get("rankedDiscoveryCoverage"), dict)
        else {}
    )
    lines = [
        f"## 资源：{resource}",
        "",
        f"- 候选状态：`{view.get('discoveryStatus', 'NO_ROWS')}`；"
        f"可排行状态：`{view.get('rankedDiscoveryStatus', 'NO_RANKED_ROW')}`",
        f"- 最佳候选：{counts.get('total', 0)}；可排行 {counts.get('ranked', 0)}；"
        f"未知 {counts.get('unranked', 0)}；已确认不兼容 {counts.get('incompatible', 0)}",
        f"- 排行发现：返回 {discovery_coverage.get('returned', 0)} / "
        f"总计 {discovery_coverage.get('total', 0)}；"
        f"省略 {discovery_coverage.get('omitted', 0)}",
        "",
        f"### 具体蓝图：{focus_component or 'NO_FOCUS_COMPONENT'}",
        "",
    ]
    focus_rows = view.get("focusRows") if isinstance(view.get("focusRows"), list) else []
    if focus_rows:
        lines.extend(
            [
                "| Resource | Component | Creature / Attack | Status | Damage | Interval | Dmg× | Qty× | Weight share | Index |",
                "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in focus_rows:
            lines.append(
                "| {resource} | {component} | {creature} / {attack} | {status} | {damage} | {interval} | "
                "{damage_multiplier} | {quantity_multiplier} | {share} | {score} |".format(
                    resource=row.get("resource", "-"),
                    component=row.get("component", "-"),
                    creature=row.get("creature", "-"),
                    attack=row.get("attackName", "-"),
                    status=row.get("rankingStatus", "-"),
                    damage=_number(row.get("baseDamage")),
                    interval=_number(row.get("attackInterval")),
                    damage_multiplier=_number(row.get("damageMultiplier")),
                    quantity_multiplier=_number(row.get("harvestQuantityMultiplier")),
                    share=_number(row.get("resourceWeightShare")),
                    score=_number(row.get("engineComparisonIndex")),
                )
            )
    else:
        lines.append("- 没有可作为焦点的已恢复候选；详情见状态与缺口汇总。")

    lines.extend(
        [
            "",
            "### 其他节点的有界排行发现",
            "",
            "同系组件只有在所有比较字段一致时才折叠；名称与对象路径均保留。",
            "",
            "| Component aliases | Creature / Attack | Status | Weight share | Index |",
            "| --- | --- | --- | ---: | ---: |",
        ]
    )
    for row in view.get("rankedDiscoveries") or []:
        aliases = row.get("componentAliases") or [str(row.get("component") or "-")]
        if focus_component and focus_component in aliases:
            continue
        lines.append(
            f"| {', '.join(str(item) for item in aliases)} | "
            f"{row.get('creature', '-')} / {row.get('attackName', '-')} | "
            f"{row.get('rankingStatus', '-')} | {_number(row.get('resourceWeightShare'))} | "
            f"{_number(row.get('engineComparisonIndex'))} |"
        )
    if not any(
        not focus_component
        or focus_component not in (row.get("componentAliases") or [])
        for row in view.get("rankedDiscoveries") or []
    ):
        lines.append("| - | - | - | - | - |")
    return lines


def render_markdown(payload: dict[str, Any], *, detail_location: str) -> str:
    ai_view = build_ai_view(payload, detail_location=detail_location)
    lines = [
        "# ARK 资源采集排行（本地 DevKit 证据）",
        "",
        f"- 生成时间：{payload['generatedAt']}",
        f"- 资源：{', '.join(payload['resources'])}",
        f"- 本地 DevKit：`{payload['devkitRoot']}`",
        f"- 组件扫描：{payload['coverage']['componentsScanned']}；成功解码：{payload['coverage']['componentsDecoded']}；"
        f"语义缺口：{payload['coverage']['componentsSemanticGap']}；命中资源组件：{payload['coverage']['componentsMatched']}",
        f"- 生物：{payload['coverage']['creaturesLoaded']}；攻击：{payload['coverage']['attacksDecoded']}",
        "",
        "> `engineComparisonIndex` 是用于同节点横向比较的推断索引，不是每击产量或资源/秒。",
        "> `observedYieldPerSecond` 保持为 null，直到补齐运行时公式、服务器倍率和受控实测。",
    ]
    resource_views = ai_view.get("resourceViews") or []
    if resource_views:
        for resource_view in resource_views:
            lines.extend(["", *_render_resource_view(resource_view)])
    else:
        lines.extend(
            [
                "",
                "## 全资源有界目录",
                "",
                "详细行不内联；按资源点和资源 ID 从本地 API/完整报告按需查询。",
                "",
                "| Resource | Status | Ranked | Unranked | Incompatible |",
                "| --- | --- | ---: | ---: | ---: |",
            ]
        )
        for item in (ai_view.get("resourceIndex") or {}).get("items") or []:
            counts = item.get("candidateCounts") or {}
            lines.append(
                f"| {item.get('resource', '-')} | {item.get('discoveryStatus', '-')} | "
                f"{counts.get('ranked', 0)} | {counts.get('unranked', 0)} | "
                f"{counts.get('incompatible', 0)} |"
            )
    lines.extend(
        [
            "",
            "## 口径",
            "",
            "```text",
            "harvestPressurePerSecond = baseDamage / attackInterval",
            "                           * DamageMultiplier",
            "                           * HarvestQuantityMultiplier",
            "resourceWeightShare       = target effective weight / sum(positive effective weights)",
            "engineComparisonIndex     = harvestPressurePerSecond * resourceWeightShare",
            "```",
            "",
            "这个索引只在同一 HarvestComponent、同一资源、相同运行时条件下用于排序。",
            "资源权重是选择权重；攻击范围、实际命中节点数、近战属性、节点剩余生命、服务器倍率和动画实际周期仍需单列。",
            "",
            "## 扫描完整性与组件缺口",
            "",
        ]
    )
    scan_manifest = ai_view.get("scanManifest") or {}
    failure_summary = ai_view.get("failureSummary") or {}
    semantic_gaps = failure_summary.get("componentSemanticGaps") or {}
    lines.extend(
        [
            f"- 扫描清单：{scan_manifest.get('count', 0)} 个组件；"
            f"指纹 `{scan_manifest.get('sha256', '-')}`。",
            f"- 存在语义缺口的组件：{semantic_gaps.get('count', 0)}。"
            "这些组件未被静默当成“不匹配”。",
        ]
    )
    for item in semantic_gaps.get("byGap") or []:
        examples = "; ".join(str(value) for value in item.get("examples") or [])
        lines.append(
            f"- `{item.get('gap', 'UNKNOWN')}` × {item.get('count', 0)}；"
            f"例：{examples or '-'}"
        )
    lines.extend(["", "## 明确缺失与不可比较项", ""])
    unknowns = ai_view["unknownSummary"]
    if unknowns:
        for item in unknowns:
            examples = "; ".join(
                f"{example.get('creature', '-')}/{example.get('attackName', '-')}/{example.get('component', '-')}"
                for example in item.get("examples") or []
            )
            lines.append(
                f"- `{item.get('reasonCode', 'UNKNOWN')}` × {item.get('count', 0)}；"
                f"缺失 `{', '.join(item.get('missingFacts') or []) or '-'}`；例：{examples or '-'}"
            )
    else:
        lines.append("- 当前最佳行没有解析缺口；运行时产量公式仍按设计保持未知。")
    lines.extend(
        [
            "",
            "## 与网上榜单的关系",
            "",
            "- Dododex / Wiki 星级只作为候选与交叉检查，不进入系数计算。",
            "- ASA 与 ASE、补丁、地图、服务器倍率必须另存；本报告只绑定当前本地 DevKit 文件指纹。",
            "- 外部榜单若与 `EntryWeight` 或 DamageType 兼容性冲突，本地资产证据优先，冲突保留为复核项。",
            "",
            "## AI 按需读取建议",
            "",
            "1. 先读同名 `.ai.json`；它只含最佳行、缺口、口径和源指纹。",
            "2. 需要全部攻击或全部不兼容原因时，再读 `.full.json`。",
            "3. 需要重新核验时运行 `runtime\\python\\python.exe scripts\\rank_ark_harvest.py`。",
            "",
        ]
    )
    return "\n".join(lines)


def _number(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    if not math.isfinite(float(value)):
        return "-"
    return f"{float(value):.4f}".rstrip("0").rstrip(".")


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    devkit_root = args.devkit_root.resolve()
    content_root = devkit_root / "Projects" / "ShooterGame" / "Content"
    if not content_root.is_dir():
        raise FileNotFoundError(f"ARK DevKit content root not found: {content_root}")
    selection_mode = "ALL_DISCOVERED" if args.all_resources else "EXPLICIT"
    resources = [normalize_resource(value) for value in (args.resource or ["Metal"])]
    resources = list(dict.fromkeys(resources))
    reader = AssetReader()
    creature_specs = load_creature_specs(args)
    creatures, creature_failures = resolve_creatures(creature_specs, content_root, reader)
    selected_components = {value.removesuffix("_C").removesuffix(".uasset") for value in args.component}
    extra_component_paths = load_extra_component_paths(args, content_root)
    components, resource_catalog, component_failures, component_scan_manifest = discover_components(
        content_root=content_root,
        reader=reader,
        selected_names=selected_components,
        max_components=max(0, int(args.max_components)),
        target_resources=None if args.all_resources else set(resources),
        discover_all_content=bool(args.discover_all_components),
        extra_component_paths=extra_component_paths,
    )
    if args.all_resources:
        resources = sorted(resource_catalog)
        if not resources:
            raise ValueError("No resource classes were recovered for --all-resources")
    matched_components = [
        component
        for component in components
        if any(resource in resources for resource in component.get("matchedResources") or [])
    ]
    parent_map, overrides, damage_facts, damage_paths, damage_type_gaps = build_damage_context(
        creatures=creatures,
        resources=resources,
        content_root=content_root,
        reader=reader,
    )
    rows: list[dict[str, Any]] = []
    for resource in resources:
        for component in matched_components:
            if not any(
                isinstance(entry, dict) and str(entry.get("resource") or "") == resource
                for entry in component.get("resourceEntries", [])
            ):
                continue
            for creature in creatures:
                for attack in creature["attacks"]:
                    rows.append(
                        evaluate_attack_resource(
                            creature=creature["name"],
                            creature_object_path=creature["objectPath"],
                            attack=attack,
                            component=component,
                            resource=resource,
                            damage_type_parents=parent_map,
                            resource_damage_overrides=overrides,
                            damage_type_gaps=damage_type_gaps,
                        )
                    )
    rows = rank_harvest_rows(rows)
    best = best_rows(rows)
    resource_candidates = build_resource_candidates(resources, best)
    used_paths: set[Path] = {creature["path"] for creature in creatures}
    used_paths.update(damage_paths)
    for record in component_scan_manifest:
        path = Path(str(record.get("path") or ""))
        if path.is_file():
            used_paths.add(path.resolve())
        for source_path in record.get("sourceChain") or []:
            source = Path(str(source_path))
            if source.is_file():
                used_paths.add(source.resolve())
    sources = [
        {
            "path": str(path),
            "sizeBytes": path.stat().st_size,
            "mtimeUtc": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            "sha256": sha256_file(path),
        }
        for path in sorted(used_paths)
        if path.is_file()
    ]
    fingerprinted_paths = {str(Path(str(row["path"])).resolve()) for row in sources}
    attempted_component_paths = {
        str(Path(str(record.get("path") or "")).resolve())
        for record in component_scan_manifest
        if record.get("path")
    }
    fingerprinted_component_paths = attempted_component_paths & fingerprinted_paths
    unknowns = [
        compact_row(row)
        for row in best
        if row.get("rankingStatus") != "RANKED"
    ]
    component_gap_summary = summarize_component_gaps(component_scan_manifest)
    manifest_hash = scan_manifest_hash(component_scan_manifest)
    payload = {
        "schema": SCHEMA,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "devkitRoot": str(devkit_root),
        "contentRoot": str(content_root),
        "resources": resources,
        "resourceSelectionMode": selection_mode,
        "methodology": {
            "scoreBasis": "INFERRED_ENGINE_COEFFICIENT_INDEX_NOT_RESOURCE_YIELD",
            "formulaVersion": "harvest-engine-comparison-index/v1",
            "usageScope": "UNFILTERED_ENGINE_ATTACKS",
            "observedYieldPerSecond": None,
            "formula": (
                "baseDamage / attackInterval * DamageMultiplier * HarvestQuantityMultiplier "
                "* normalizedResourceWeight"
            ),
            "notIncluded": [
                "runtime melee stat scaling",
                "server harvest multipliers",
                "node remaining-health clamp",
                "actual animation wall-clock timing",
                "nodes hit per swing",
                "controlled observed yield",
            ],
        },
        "coverage": {
            "creaturesRequested": len(creature_specs),
            "creaturesLoaded": len(creatures),
            "attacksDecoded": sum(len(creature["attacks"]) for creature in creatures),
            "componentsScanned": len(component_scan_manifest),
            "componentsAttempted": len(component_scan_manifest),
            "componentsDecoded": sum(
                record.get("decoded") is True for record in component_scan_manifest
            ),
            "componentsSemanticGap": sum(
                record.get("semanticGap") is True for record in component_scan_manifest
            ),
            "componentsMatched": len(matched_components),
            "componentCatalogEntries": len(components),
            "componentSourceFingerprints": {
                "attemptedPaths": len(attempted_component_paths),
                "fingerprintedPaths": len(fingerprinted_component_paths),
                "complete": fingerprinted_component_paths == attempted_component_paths,
            },
            "resourceClassesDiscovered": len(resource_catalog),
            "rows": len(rows),
            "rankedRows": sum(row.get("rankingStatus") == "RANKED" for row in rows),
            "incompatibleRows": sum(row.get("rankingStatus") == "INCOMPATIBLE" for row in rows),
            "unrankedRows": sum(row.get("rankingStatus") == "UNRANKED" for row in rows),
        },
        "creatures": [
            {
                "name": creature["name"],
                "objectPath": creature["objectPath"],
                "path": str(creature["path"]),
                "attacks": creature["attacks"],
                "warnings": creature["warnings"],
            }
            for creature in creatures
        ],
        "components": [
            {
                **{key: value for key, value in component.items() if key not in {"path", "sourceChain"}},
                "path": str(component["path"]),
                "sourceChain": [str(item) for item in component["sourceChain"]],
            }
            for component in matched_components
        ],
        "componentCatalog": [
            {
                **{
                    key: value
                    for key, value in component.items()
                    if key not in {"path", "sourceChain"}
                },
                "path": str(component["path"]),
                "sourceChain": [str(item) for item in component["sourceChain"]],
            }
            for component in components
        ],
        "damageTypes": damage_facts,
        "rows": rows,
        "bestRows": [compact_row(row) for row in best],
        "resourceCandidates": resource_candidates,
        "unknowns": unknowns,
        "resourceCatalog": resource_catalog,
        "componentGapSummary": component_gap_summary,
        "scanManifestHash": manifest_hash,
        "componentScanManifest": component_scan_manifest,
        "failures": {
            "creatures": creature_failures,
            "components": component_failures,
        },
        "sources": sources,
    }
    payload.update(build_ranking_revision_fields(payload))
    return payload


def write_outputs(payload: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = resource_report_slug(
        payload["resources"],
        selection_mode=str(payload.get("resourceSelectionMode") or "EXPLICIT"),
    )
    full_path = output_dir / f"harvest_ranking_{slug}.full.json"
    ai_path = output_dir / f"harvest_ranking_{slug}.ai.json"
    query_path = output_dir / f"harvest_ranking_{slug}.query.json"
    markdown_path = output_dir / f"harvest_ranking_{slug}.md"
    catalog_path = output_dir / "resource_catalog.json"
    detail_location = full_path.name
    markdown = render_markdown(payload, detail_location=detail_location)
    ai_view = build_ai_view(payload, detail_location=detail_location)
    ai_payload = {
        "schema": payload["schema"],
        "compactSchema": COMPACT_SCHEMA,
        "generatedAt": payload["generatedAt"],
        "resources": payload["resources"],
        "methodology": payload["methodology"],
        "coverage": payload["coverage"],
        **ai_view,
    }
    ai_payload["tokenEstimate"] = {"method": "ceil(characters/4)"}
    for _iteration in range(3):
        ai_text = json.dumps(ai_payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        ai_payload["tokenEstimate"] = {
            "method": "ceil(characters/4)",
            "estimatedTokens": math.ceil(len(ai_text) / 4),
            "characters": len(ai_text),
        }
    full_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ai_path.write_text(json.dumps(ai_payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    query_path.write_text(
        json.dumps(
            {
                "schema": payload["schema"],
                "querySchema": "ark-harvest-ranking-query/v1",
                "generatedAt": payload["generatedAt"],
                "datasetRevision": payload.get("datasetRevision"),
                "scanManifestHash": payload.get("scanManifestHash"),
                "methodology": payload["methodology"],
                "coverage": payload["coverage"],
                "bestRows": payload["bestRows"],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(markdown + "\n", encoding="utf-8")
    catalog_path.write_text(
        json.dumps(
            {
                "schema": "ark-harvest-resource-catalog/v1",
                "generatedAt": payload["generatedAt"],
                "resources": payload["resourceCatalog"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "full": str(full_path.resolve()),
        "ai": str(ai_path.resolve()),
        "query": str(query_path.resolve()),
        "markdown": str(markdown_path.resolve()),
        "catalog": str(catalog_path.resolve()),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_payload(args)
    outputs = write_outputs(payload, args.output_dir.resolve())
    print(
        json.dumps(
            {
                "schema": payload["schema"],
                "coverage": payload["coverage"],
                "outputs": outputs,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
