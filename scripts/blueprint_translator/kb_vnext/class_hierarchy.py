"""Unified Blueprint/native class identity and incremental closure for KB vNext."""

from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence


UNKNOWN = "UNKNOWN"
CLASS_SCHEMA_VERSION = "ark-kb-classes/v1"

ANCESTRY_ROOTS: dict[str, tuple[str, ...]] = {
    "DATA_ASSET": ("/Script/Engine.DataAsset",),
    "PRIMARY_DATA_ASSET": ("/Script/Engine.PrimaryDataAsset",),
    "ACTOR_COMPONENT": ("/Script/Engine.ActorComponent",),
    "DAMAGE_TYPE": (
        "/Script/Engine.DamageType",
        "/Script/ShooterGame.DamageType",
    ),
    "INVENTORY": ("/Script/ShooterGame.PrimalInventoryComponent",),
    "STATUS_COMPONENT": (
        "/Script/ShooterGame.PrimalCharacterStatusComponent",
    ),
    "BUFF": ("/Script/ShooterGame.PrimalBuff",),
}

BUILTIN_CLASS_EDGES = (
    (
        "/Script/Engine.PrimaryDataAsset",
        "/Script/Engine.DataAsset",
        "native_parent",
    ),
    (
        "/Script/Engine.DataAsset",
        "/Script/CoreUObject.Object",
        "native_parent",
    ),
    (
        "/Script/Engine.ActorComponent",
        "/Script/CoreUObject.Object",
        "native_parent",
    ),
    (
        "/Script/Engine.DamageType",
        "/Script/CoreUObject.Object",
        "native_parent",
    ),
)

CLASS_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS classes (
    class_id INTEGER PRIMARY KEY,
    class_path TEXT UNIQUE NOT NULL,
    class_name TEXT NOT NULL,
    module_or_package TEXT NOT NULL,
    class_kind TEXT NOT NULL,
    is_native INTEGER NOT NULL,
    source_revision_id INTEGER,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS class_edges (
    child_class_id INTEGER NOT NULL,
    parent_class_id INTEGER NOT NULL,
    edge_kind TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    source_revision_id INTEGER,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    PRIMARY KEY(
        child_class_id, parent_class_id, edge_kind, evidence_id
    ),
    FOREIGN KEY(child_class_id) REFERENCES classes(class_id),
    FOREIGN KEY(parent_class_id) REFERENCES classes(class_id)
);

CREATE TABLE IF NOT EXISTS class_closure (
    ancestor_class_id INTEGER NOT NULL,
    descendant_class_id INTEGER NOT NULL,
    depth INTEGER NOT NULL,
    path_status TEXT NOT NULL,
    PRIMARY KEY(ancestor_class_id, descendant_class_id),
    FOREIGN KEY(ancestor_class_id) REFERENCES classes(class_id),
    FOREIGN KEY(descendant_class_id) REFERENCES classes(class_id)
);

CREATE TABLE IF NOT EXISTS class_gaps (
    class_id INTEGER NOT NULL,
    gap_kind TEXT NOT NULL,
    detail TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY(class_id, gap_kind, detail),
    FOREIGN KEY(class_id) REFERENCES classes(class_id)
);

CREATE TABLE IF NOT EXISTS asset_class_assignments (
    entity_uri TEXT NOT NULL,
    class_id INTEGER NOT NULL,
    assignment_kind TEXT NOT NULL,
    evidence_uri TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    PRIMARY KEY(entity_uri, assignment_kind),
    FOREIGN KEY(class_id) REFERENCES classes(class_id)
);

CREATE TABLE IF NOT EXISTS class_ancestry_categories (
    class_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    ancestor_class_id INTEGER NOT NULL,
    depth INTEGER NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    PRIMARY KEY(class_id, category, ancestor_class_id),
    FOREIGN KEY(class_id) REFERENCES classes(class_id),
    FOREIGN KEY(ancestor_class_id) REFERENCES classes(class_id)
);

CREATE INDEX IF NOT EXISTS idx_class_edges_parent
    ON class_edges(parent_class_id, child_class_id);
CREATE INDEX IF NOT EXISTS idx_class_closure_descendant
    ON class_closure(descendant_class_id, depth);
CREATE INDEX IF NOT EXISTS idx_class_gaps_kind
    ON class_gaps(gap_kind, status);
CREATE INDEX IF NOT EXISTS idx_asset_class_assignment_class
    ON asset_class_assignments(class_id, assignment_kind);
CREATE INDEX IF NOT EXISTS idx_class_category
    ON class_ancestry_categories(category, class_id);
"""


def _known_path(value: object) -> str | None:
    text = "" if value is None else str(value).strip()
    if not text or text.upper() == UNKNOWN:
        return None
    return text


def _class_name(class_path: str) -> str:
    return class_path.rsplit("/", 1)[-1].rsplit(".", 1)[-1]


def _module_or_package(class_path: str) -> str:
    if class_path.startswith("/Script/"):
        return class_path[len("/Script/") :].split(".", 1)[0]
    return class_path.rsplit(".", 1)[0]


def _class_kind(class_path: str, generated_paths: set[str]) -> str:
    if class_path.startswith("/Script/"):
        return "NATIVE_UCLASS"
    if class_path in generated_paths or class_path.endswith("_C"):
        return "BLUEPRINT_GENERATED_CLASS"
    if class_path.startswith("/Game/") or class_path.startswith("/Mods/"):
        return "CONTENT_CLASS"
    return "MOUNTED_CLASS"


def _edge_evidence_id(
    child_path: str,
    parent_path: str,
    edge_kind: str,
    source_kind: str,
) -> str:
    payload = "\0".join((child_path, parent_path, edge_kind, source_kind))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"class-edge://{digest}"


def create_class_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(CLASS_TABLES_SQL)


def _source_rows(
    discovery: sqlite3.Connection,
    sql: str,
    parameters: Sequence[object] = (),
) -> list[dict[str, object]]:
    discovery.row_factory = sqlite3.Row
    return [dict(row) for row in discovery.execute(sql, parameters)]


def _all_class_paths(
    discovery: sqlite3.Connection,
    edge_rows: Sequence[Mapping[str, object]],
) -> tuple[set[str], set[str]]:
    paths: set[str] = set()
    generated = {
        str(row[0])
        for row in discovery.execute(
            """
            SELECT DISTINCT generated_class_path
            FROM assets
            WHERE generated_class_path NOT IN ('', 'UNKNOWN')
            """
        )
    }
    paths.update(generated)
    for row in discovery.execute(
        """
        SELECT asset_class_path AS class_path FROM assets
        WHERE asset_class_path NOT IN ('', 'UNKNOWN')
        UNION
        SELECT parent_class_path FROM assets
        WHERE parent_class_path NOT IN ('', 'UNKNOWN')
        UNION
        SELECT native_parent_class_path FROM assets
        WHERE native_parent_class_path NOT IN ('', 'UNKNOWN')
        """
    ):
        if value := _known_path(row[0]):
            paths.add(value)
    for row in edge_rows:
        if value := _known_path(row.get("child_class_path")):
            paths.add(value)
        if value := _known_path(row.get("parent_class_path")):
            paths.add(value)
    for roots in ANCESTRY_ROOTS.values():
        paths.update(roots)
    for child, parent, _edge_kind in BUILTIN_CLASS_EDGES:
        paths.add(child)
        paths.add(parent)
    return paths, generated


def _class_id_map(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT class_path, class_id FROM classes"
        )
    }


def materialize_discovery_classes(
    discovery: sqlite3.Connection,
    target: sqlite3.Connection,
    *,
    source_revision_id: int | None = None,
) -> dict[str, int]:
    """Build normalized class identity and closure from Discovery evidence."""

    create_class_tables(target)
    edge_rows = _source_rows(
        discovery,
        """
        SELECT
            child_class_path, parent_class_path, edge_kind,
            source_kind, confidence
        FROM class_edges
        ORDER BY child_class_path, parent_class_path, edge_kind
        """,
    )
    paths, generated_paths = _all_class_paths(discovery, edge_rows)

    target.execute("DELETE FROM class_ancestry_categories")
    target.execute("DELETE FROM class_closure")
    target.execute("DELETE FROM class_gaps")
    target.execute("DELETE FROM asset_class_assignments")
    target.execute("DELETE FROM class_edges")
    target.execute("DELETE FROM classes")
    target.executemany(
        """
        INSERT INTO classes(
            class_path, class_name, module_or_package, class_kind,
            is_native, source_revision_id, status, confidence
        ) VALUES (?, ?, ?, ?, ?, ?, 'IDENTIFIED', 'HIGH')
        """,
        [
            (
                path,
                _class_name(path),
                _module_or_package(path),
                _class_kind(path, generated_paths),
                int(path.startswith("/Script/")),
                source_revision_id,
            )
            for path in sorted(paths)
        ],
    )
    class_ids = _class_id_map(target)

    normalized_edges: dict[tuple[int, int, str, str], tuple[object, ...]] = {}
    for row in edge_rows:
        child_path = _known_path(row.get("child_class_path"))
        parent_path = _known_path(row.get("parent_class_path"))
        if not child_path or not parent_path:
            continue
        edge_kind = str(row.get("edge_kind") or "parent")
        source_kind = str(row.get("source_kind") or "discovery")
        confidence = str(row.get("confidence") or "UNKNOWN").upper()
        status = (
            "CONFIRMED"
            if confidence in {"HIGH", "CONFIRMED"}
            else "AMBIGUOUS"
        )
        evidence_id = _edge_evidence_id(
            child_path, parent_path, edge_kind, source_kind
        )
        key = (
            class_ids[child_path],
            class_ids[parent_path],
            edge_kind,
            evidence_id,
        )
        normalized_edges[key] = (
            *key,
            source_revision_id,
            status,
            confidence,
        )
    for child_path, parent_path, edge_kind in BUILTIN_CLASS_EDGES:
        source_kind = "builtin_unreal_class_ontology/v1"
        evidence_id = _edge_evidence_id(
            child_path, parent_path, edge_kind, source_kind
        )
        key = (
            class_ids[child_path],
            class_ids[parent_path],
            edge_kind,
            evidence_id,
        )
        normalized_edges[key] = (
            *key,
            source_revision_id,
            "CONFIRMED",
            "HIGH",
        )
    target.executemany(
        """
        INSERT INTO class_edges(
            child_class_id, parent_class_id, edge_kind, evidence_id,
            source_revision_id, status, confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        normalized_edges.values(),
    )

    assignment_count = 0
    asset_cursor = discovery.execute(
        """
        SELECT
            object_path, asset_class_path, generated_class_path,
            identity_status, identity_confidence
        FROM assets
        ORDER BY object_path
        """
    )
    while batch := asset_cursor.fetchmany(10_000):
        assignments: list[tuple[object, ...]] = []
        gap_rows: list[tuple[int, str, str, str]] = []
        for source_row in batch:
            row = dict(source_row)
            entity_uri = str(row["object_path"])
            confidence = str(
                row.get("identity_confidence") or "UNKNOWN"
            ).upper()
            status = str(row.get("identity_status") or "UNKNOWN").upper()
            if generated := _known_path(row.get("generated_class_path")):
                assignments.append(
                    (
                        entity_uri,
                        class_ids[generated],
                        "GENERATED_CLASS",
                        f"discovery://asset/{entity_uri}#generated-class",
                        status,
                        confidence,
                    )
                )
            elif (
                _known_path(row.get("asset_class_path"))
                == "/Script/Engine.Blueprint"
            ):
                gap_rows.append(
                    (
                        class_ids["/Script/Engine.Blueprint"],
                        "GENERATED_CLASS_NOT_RECOVERED",
                        entity_uri,
                        "NOT_RECOVERED",
                    )
                )
            if asset_class := _known_path(row.get("asset_class_path")):
                assignments.append(
                    (
                        entity_uri,
                        class_ids[asset_class],
                        "ASSET_CLASS",
                        f"discovery://asset/{entity_uri}#asset-class",
                        status,
                        confidence,
                    )
                )
        target.executemany(
            """
            INSERT INTO asset_class_assignments(
                entity_uri, class_id, assignment_kind, evidence_uri,
                status, confidence
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            assignments,
        )
        if gap_rows:
            target.executemany(
                "INSERT OR IGNORE INTO class_gaps VALUES (?, ?, ?, ?)",
                gap_rows,
            )
        assignment_count += len(assignments)

    closure = rebuild_class_closure(target)
    target.commit()
    return {
        "classes": len(class_ids),
        "classEdges": len(normalized_edges),
        "assignments": assignment_count,
        **closure,
    }


def _graph(
    connection: sqlite3.Connection,
) -> tuple[
    dict[int, list[tuple[int, str, str]]],
    dict[int, set[int]],
]:
    parent_map: dict[int, dict[int, tuple[str, str]]] = defaultdict(dict)
    children: dict[int, set[int]] = defaultdict(set)
    for child, parent, status, confidence in connection.execute(
        """
        SELECT child_class_id, parent_class_id, status, confidence
        FROM class_edges
        """
    ):
        child_id = int(child)
        parent_id = int(parent)
        status_text = str(status)
        confidence_text = str(confidence)
        existing = parent_map[child_id].get(parent_id)
        if existing is None or (
            existing[0] not in {"CONFIRMED", "VERIFIED", "RESOLVED"}
            and status_text in {"CONFIRMED", "VERIFIED", "RESOLVED"}
        ):
            parent_map[child_id][parent_id] = (
                status_text,
                confidence_text,
            )
        children[parent_id].add(child_id)
    parents = {
        child: [
            (parent, status, confidence)
            for parent, (status, confidence) in sorted(parent_rows.items())
        ]
        for child, parent_rows in parent_map.items()
    }
    return parents, children


def _affected_descendants(
    children: Mapping[int, set[int]],
    changed_class_ids: Iterable[int],
) -> set[int]:
    affected = {int(value) for value in changed_class_ids}
    queue = deque(affected)
    while queue:
        parent = queue.popleft()
        for child in children.get(parent, set()):
            if child not in affected:
                affected.add(child)
                queue.append(child)
    return affected


def _closure_for_descendant(
    descendant: int,
    parents: Mapping[int, Sequence[tuple[int, str, str]]],
) -> tuple[dict[int, tuple[int, str]], set[int], bool]:
    results: dict[int, tuple[int, str]] = {
        descendant: (0, "SELF")
    }
    cycle_nodes: set[int] = set()
    queue: deque[tuple[int, int, tuple[int, ...], str]] = deque(
        [(descendant, 0, (descendant,), "CONFIRMED")]
    )
    while queue:
        current, depth, path, path_status = queue.popleft()
        for parent, edge_status, confidence in parents.get(current, ()):
            if parent in path:
                cycle_nodes.update(path[path.index(parent) :])
                cycle_nodes.add(parent)
                continue
            next_status = path_status
            if (
                edge_status not in {"CONFIRMED", "VERIFIED", "RESOLVED"}
                or confidence not in {"HIGH", "CONFIRMED"}
            ):
                next_status = "AMBIGUOUS"
            next_depth = depth + 1
            existing = results.get(parent)
            if existing is None or next_depth < existing[0]:
                results[parent] = (next_depth, next_status)
            elif next_depth == existing[0] and next_status != existing[1]:
                results[parent] = (next_depth, "AMBIGUOUS")
            queue.append(
                (parent, next_depth, (*path, parent), next_status)
            )
    ambiguous_parent = len(parents.get(descendant, ())) > 1
    return results, cycle_nodes, ambiguous_parent


def _rebuild_ancestry_categories(
    connection: sqlite3.Connection,
    class_ids: set[int],
) -> int:
    if not class_ids:
        return 0
    placeholders = ",".join("?" for _ in class_ids)
    connection.execute(
        f"DELETE FROM class_ancestry_categories WHERE class_id IN ({placeholders})",
        tuple(class_ids),
    )
    path_to_id = _class_id_map(connection)
    rows: list[tuple[object, ...]] = []
    for category, roots in ANCESTRY_ROOTS.items():
        for root in roots:
            root_id = path_to_id.get(root)
            if root_id is None:
                continue
            for class_id, depth, path_status in connection.execute(
                f"""
                SELECT descendant_class_id, depth, path_status
                FROM class_closure
                WHERE ancestor_class_id=?
                  AND descendant_class_id IN ({placeholders})
                """,
                (root_id, *class_ids),
            ):
                rows.append(
                    (
                        int(class_id),
                        category,
                        root_id,
                        int(depth),
                        str(path_status),
                        (
                            "HIGH"
                            if str(path_status) in {"SELF", "CONFIRMED"}
                            else "UNKNOWN"
                        ),
                    )
                )
    if rows:
        connection.executemany(
            """
            INSERT INTO class_ancestry_categories(
                class_id, category, ancestor_class_id, depth,
                status, confidence
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def rebuild_class_closure(
    connection: sqlite3.Connection,
    *,
    changed_class_ids: Iterable[int] | None = None,
) -> dict[str, int]:
    """Rebuild all closure rows or only changed classes and their descendants."""

    parents, children = _graph(connection)
    all_class_ids = {
        int(row[0]) for row in connection.execute("SELECT class_id FROM classes")
    }
    native_class_ids = {
        int(row[0])
        for row in connection.execute(
            "SELECT class_id FROM classes WHERE is_native=1"
        )
    }
    affected = (
        all_class_ids
        if changed_class_ids is None
        else _affected_descendants(children, changed_class_ids)
    )
    if not affected:
        return {
            "closureRows": 0,
            "affectedClasses": 0,
            "cycleClasses": 0,
            "openChains": 0,
            "ancestryCategories": 0,
        }
    placeholders = ",".join("?" for _ in affected)
    values = tuple(sorted(affected))
    connection.execute(
        f"DELETE FROM class_closure WHERE descendant_class_id IN ({placeholders})",
        values,
    )
    connection.execute(
        f"""
        DELETE FROM class_gaps
        WHERE class_id IN ({placeholders})
          AND gap_kind IN (
              'INHERITANCE_CYCLE', 'MULTIPLE_PARENT_CANDIDATES',
              'NATIVE_ROOT_NOT_REACHED'
          )
        """,
        values,
    )

    closure_rows: list[tuple[int, int, int, str]] = []
    gaps: set[tuple[int, str, str, str]] = set()
    cycle_classes: set[int] = set()
    open_chains = 0
    for descendant in values:
        closure, cycle_nodes, ambiguous_parent = _closure_for_descendant(
            descendant, parents
        )
        if cycle_nodes:
            cycle_classes.update(cycle_nodes)
            gaps.add(
                (
                    descendant,
                    "INHERITANCE_CYCLE",
                    ",".join(str(value) for value in sorted(cycle_nodes)),
                    "AMBIGUOUS",
                )
            )
        if ambiguous_parent:
            gaps.add(
                (
                    descendant,
                    "MULTIPLE_PARENT_CANDIDATES",
                    ",".join(
                        str(parent)
                        for parent, _status, _confidence in parents[descendant]
                    ),
                    "AMBIGUOUS",
                )
            )
        if not set(closure).intersection(native_class_ids):
            open_chains += 1
            gaps.add(
                (
                    descendant,
                    "NATIVE_ROOT_NOT_REACHED",
                    "No confirmed /Script ancestor is reachable.",
                    "NOT_RECOVERED",
                )
            )
        for ancestor, (depth, path_status) in closure.items():
            status = "AMBIGUOUS" if cycle_nodes else path_status
            closure_rows.append((ancestor, descendant, depth, status))

    connection.executemany(
        """
        INSERT INTO class_closure(
            ancestor_class_id, descendant_class_id, depth, path_status
        ) VALUES (?, ?, ?, ?)
        """,
        closure_rows,
    )
    if gaps:
        connection.executemany(
            "INSERT OR IGNORE INTO class_gaps VALUES (?, ?, ?, ?)",
            sorted(gaps),
        )
    category_count = _rebuild_ancestry_categories(connection, affected)
    connection.commit()
    return {
        "closureRows": len(closure_rows),
        "affectedClasses": len(affected),
        "cycleClasses": len(cycle_classes),
        "openChains": open_chains,
        "ancestryCategories": category_count,
    }


def inheritance_path_to_native_root(
    connection: sqlite3.Connection,
    class_path: str,
) -> dict[str, object]:
    """Return one shortest confirmed native-root path or an explicit gap."""

    class_row = connection.execute(
        "SELECT class_id FROM classes WHERE class_path=?",
        (class_path,),
    ).fetchone()
    if class_row is None:
        return {
            "status": "NO_ENTITY_MATCH",
            "classPath": class_path,
            "path": [],
            "gaps": ["CLASS_NOT_INDEXED"],
        }
    start = int(class_row[0])
    parents, _children = _graph(connection)
    queue: deque[tuple[int, tuple[int, ...]]] = deque([(start, (start,))])
    visited = {start}
    while queue:
        current, path = queue.popleft()
        native = connection.execute(
            "SELECT is_native FROM classes WHERE class_id=?",
            (current,),
        ).fetchone()
        if native and int(native[0]) == 1:
            placeholders = ",".join("?" for _ in path)
            names = {
                int(row[0]): str(row[1])
                for row in connection.execute(
                    f"""
                    SELECT class_id, class_path
                    FROM classes
                    WHERE class_id IN ({placeholders})
                    """,
                    path,
                )
            }
            return {
                "status": "CONFIRMED",
                "classPath": class_path,
                "path": [names[value] for value in path],
                "gaps": [],
            }
        for parent, edge_status, confidence in parents.get(current, ()):
            if (
                edge_status not in {"CONFIRMED", "VERIFIED", "RESOLVED"}
                or confidence not in {"HIGH", "CONFIRMED"}
                or parent in visited
            ):
                continue
            visited.add(parent)
            queue.append((parent, (*path, parent)))
    gaps = [
        str(row[0])
        for row in connection.execute(
            "SELECT gap_kind FROM class_gaps WHERE class_id=? ORDER BY gap_kind",
            (start,),
        )
    ]
    return {
        "status": "PARENT_CHAIN_OPEN",
        "classPath": class_path,
        "path": [],
        "gaps": gaps or ["NATIVE_ROOT_NOT_REACHED"],
    }
