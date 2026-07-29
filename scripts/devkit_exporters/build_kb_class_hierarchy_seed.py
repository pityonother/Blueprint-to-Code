"""Build the DevKit class-hierarchy seed from the Discovery database.

This script runs in normal local Python, not inside Unreal.  It reads the
published Discovery SQLite database in read-only mode and writes a deterministic
JSON inventory containing every canonical class path observed on the class,
interface, component, function-owner, and default-owner surfaces.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path


SEED_SCHEMA = "ark.kb.class-hierarchy-seed.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DISCOVERY_DB = (
    PROJECT_ROOT / "knowledge_base" / "discovery_bundle" / "kb_discovery.sqlite"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "knowledge_base"
    / "devkit_class_hierarchy"
    / "class_hierarchy_seed.json"
)

CLASS_SOURCES = (
    ("assets", "asset_class_path"),
    ("assets", "generated_class_path"),
    ("assets", "parent_class_path"),
    ("assets", "native_parent_class_path"),
    ("class_edges", "child_class_path"),
    ("class_edges", "parent_class_path"),
    ("interfaces", "interface_class_path"),
    ("blueprint_functions", "declaring_class_path"),
    ("default_property_surface", "declaring_class_path"),
)
COMPONENT_SOURCE = ("components", "component_class_path")

BUILTIN_SHORT_CLASS_PATHS = {
    "BoxComponent": "/Script/Engine.BoxComponent",
}

_CLASS_PATH_TEXT = r"/[A-Za-z0-9_]+(?:/[A-Za-z0-9_.-]+)*\.[A-Za-z0-9_.-]+"
_CLASS_PATH_RE = re.compile(r"^" + _CLASS_PATH_TEXT + r"$")
_WRAPPED_CLASS_PATH_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]*|" + _CLASS_PATH_TEXT + r")"
    r"'(?P<target>" + _CLASS_PATH_TEXT + r")'$"
)
_SHORT_CLASS_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _canonical_class_path(value: object) -> str:
    text = str(value or "").replace("\\", "/").replace("\x00", "").strip()
    if not text or text.upper() == "UNKNOWN":
        return ""
    if _CLASS_PATH_RE.fullmatch(text):
        return text
    wrapped = _WRAPPED_CLASS_PATH_RE.fullmatch(text)
    return wrapped.group("target") if wrapped is not None else ""


def _quoted_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _available_columns(connection: sqlite3.Connection) -> dict[str, set[str]]:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    return {
        table: {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info({})".format(_quoted_identifier(table))
            )
        }
        for table in tables
    }


def _distinct_values(
    connection: sqlite3.Connection,
    available: Mapping[str, set[str]],
    table: str,
    column: str,
) -> list[str]:
    if table not in available or column not in available[table]:
        return []
    table_sql = _quoted_identifier(table)
    column_sql = _quoted_identifier(column)
    return [
        str(row[0])
        for row in connection.execute(
            "SELECT DISTINCT {column} FROM {table} "
            "WHERE {column} IS NOT NULL AND trim({column}) NOT IN ('', 'UNKNOWN')".format(
                column=column_sql,
                table=table_sql,
            )
        )
    ]


def _parse_short_class_overrides(
    values: Sequence[str],
) -> dict[str, str]:
    overrides = {}
    for value in values:
        name, separator, target = str(value).partition("=")
        name = name.strip()
        target = _canonical_class_path(target)
        if not separator or not _SHORT_CLASS_NAME_RE.fullmatch(name) or not target:
            raise ValueError("--short-class must use NAME=/Script/Module.Class")
        overrides[name] = target
    return overrides


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".{}.tmp.{}".format(path.name, os.getpid()))
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    try:
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.is_file():
            temporary.unlink()


def _component_paths(
    values: Sequence[str],
    known_paths: set[str],
    overrides: Mapping[str, str],
) -> tuple[set[str], dict[str, object]]:
    leaf_index: defaultdict[str, set[str]] = defaultdict(set)
    for class_path in known_paths:
        leaf_index[class_path.rsplit(".", 1)[-1]].add(class_path)

    direct_paths = set()
    resolved_short_names = {}
    override_names = set()
    unresolved = []
    ambiguous = {}
    for value in sorted(set(values), key=str.casefold):
        direct = _canonical_class_path(value)
        if direct:
            direct_paths.add(direct)
            continue
        short_name = str(value or "").strip()
        if not _SHORT_CLASS_NAME_RE.fullmatch(short_name):
            unresolved.append(short_name or "<EMPTY>")
            continue
        if short_name in overrides:
            resolved_short_names[short_name] = overrides[short_name]
            override_names.add(short_name)
            continue
        candidates = sorted(leaf_index.get(short_name, ()))
        if len(candidates) == 1:
            resolved_short_names[short_name] = candidates[0]
        elif len(candidates) > 1:
            ambiguous[short_name] = candidates
        else:
            unresolved.append(short_name)

    if unresolved or ambiguous:
        details = []
        if unresolved:
            details.append("unresolved=" + ",".join(sorted(set(unresolved))))
        if ambiguous:
            details.append(
                "ambiguous="
                + ";".join(
                    "{}:{}".format(name, "|".join(paths))
                    for name, paths in sorted(ambiguous.items())
                )
            )
        raise RuntimeError(
            "Component class seed is incomplete ({}). "
            "Provide --short-class NAME=/Script/Module.Class.".format(" ".join(details))
        )

    resolved_paths = set(resolved_short_names.values())
    return direct_paths | resolved_paths, {
        "direct_path_count": len(direct_paths),
        "resolved_short_names": dict(sorted(resolved_short_names.items())),
        "override_short_names": sorted(override_names),
    }


def build_seed(
    discovery_db: Path | str,
    output_path: Path | str,
    short_class_overrides: Mapping[str, str] | None = None,
) -> dict[str, object]:
    database_path = Path(discovery_db).resolve()
    destination = Path(output_path).resolve()
    if not database_path.is_file():
        raise RuntimeError("Discovery database does not exist")

    overrides = dict(BUILTIN_SHORT_CLASS_PATHS)
    for name, value in (short_class_overrides or {}).items():
        if not _SHORT_CLASS_NAME_RE.fullmatch(str(name)):
            raise ValueError("Short class override name is invalid")
        canonical = _canonical_class_path(value)
        if not canonical:
            raise ValueError("Short class override path is invalid")
        overrides[str(name)] = canonical

    connection = sqlite3.connect(
        database_path.as_uri() + "?mode=ro",
        uri=True,
    )
    try:
        connection.execute("PRAGMA query_only = ON")
        available = _available_columns(connection)
        class_paths = set()
        source_counts = {}
        ignored_counts = {}
        observed_source_count = 0
        for table, column in CLASS_SOURCES:
            values = _distinct_values(
                connection,
                available,
                table,
                column,
            )
            if table in available and column in available[table]:
                observed_source_count += 1
            accepted = {
                path
                for path in (_canonical_class_path(value) for value in values)
                if path
            }
            source_name = "{}.{}".format(table, column)
            class_paths.update(accepted)
            source_counts[source_name] = {
                "accepted_distinct": len(accepted),
                "observed_distinct": len(values),
            }
            ignored = len(values) - len(accepted)
            if ignored:
                ignored_counts[source_name] = ignored

        if observed_source_count == 0:
            raise RuntimeError("Discovery database has no supported class-path columns")

        component_table, component_column = COMPONENT_SOURCE
        component_values = _distinct_values(
            connection,
            available,
            component_table,
            component_column,
        )
    finally:
        connection.close()

    component_paths, component_resolution = _component_paths(
        component_values,
        class_paths,
        overrides,
    )
    class_paths.update(component_paths)
    component_source_name = "{}.{}".format(*COMPONENT_SOURCE)
    source_counts[component_source_name] = {
        "accepted_distinct": len(component_paths),
        "observed_distinct": len(set(component_values)),
    }

    if not class_paths:
        raise RuntimeError("Discovery database produced no canonical class paths")

    payload = {
        "schema": SEED_SCHEMA,
        "source_kind": "kb_discovery_sqlite",
        "class_count": len(class_paths),
        "class_paths": sorted(class_paths),
        "source_counts": dict(sorted(source_counts.items())),
        "ignored_non_class_value_counts": dict(sorted(ignored_counts.items())),
        "component_resolution": component_resolution,
    }
    _write_json_atomic(destination, payload)
    return payload


def _devkit_console_command(
    project_root: Path,
    seed_path: Path,
) -> str:
    exporter_path = (
        project_root
        / "scripts"
        / "devkit_exporters"
        / "export_kb_class_hierarchy_snapshot.py"
    )
    return (
        'import os; os.environ["BTC_KB_CLASS_HIERARCHY_SEED_FILE"] = r"{}"; '.format(
            seed_path.resolve()
        )
        + 'BLUEPRINT_TO_CODE_PROJECT_ROOT = r"{}"; '.format(project_root.resolve())
        + 'exec(open(r"{}", encoding="utf-8").read())'.format(exporter_path.resolve())
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a deterministic ARK class-hierarchy seed from kb_discovery.sqlite."
        )
    )
    parser.add_argument(
        "--discovery-db",
        type=Path,
        default=DEFAULT_DISCOVERY_DB,
        help="Published Discovery SQLite database.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Seed JSON consumed by the DevKit exporter.",
    )
    parser.add_argument(
        "--short-class",
        action="append",
        default=[],
        metavar="NAME=/Script/Module.Class",
        help=("Resolve an ambiguous or short component class name. May be repeated."),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    overrides = _parse_short_class_overrides(arguments.short_class)
    payload = build_seed(
        arguments.discovery_db,
        arguments.output,
        overrides,
    )
    print(
        "Wrote {} canonical class paths to {}".format(
            payload["class_count"],
            arguments.output.resolve(),
        )
    )
    print("Run this in the ARK DevKit Python Console:")
    print(
        _devkit_console_command(
            PROJECT_ROOT,
            arguments.output,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
