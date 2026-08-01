from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARVEST_ROOT = ROOT / "scripts" / "blueprint_translator" / "harvest"


def _module_name(path: Path) -> str:
    relative = path.relative_to(ROOT / "scripts").with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _local_imports(path: Path) -> set[str]:
    module_name = _module_name(path)
    package = module_name if path.name == "__init__.py" else module_name.rsplit(".", 1)[0]
    imports: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(
                alias.name
                for alias in node.names
                if alias.name.startswith("blueprint_translator.harvest")
            )
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                prefix = base[: len(base) - (node.level - 1)]
                target = ".".join(prefix + ((node.module or "").split(".") if node.module else []))
            else:
                target = node.module or ""
            if target.startswith("blueprint_translator.harvest"):
                imports.add(target)
    return imports


def test_harvest_module_graph_has_no_cycles() -> None:
    paths = list(HARVEST_ROOT.rglob("*.py"))
    modules = {_module_name(path): path for path in paths}
    graph = {
        module: {
            candidate
            for imported in _local_imports(path)
            for candidate in modules
            if candidate == imported or candidate.startswith(imported + ".")
        }
        for module, path in modules.items()
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str, trail: tuple[str, ...]) -> None:
        if module in visiting:
            raise AssertionError("Harvest import cycle: " + " -> ".join((*trail, module)))
        if module in visited:
            return
        visiting.add(module)
        for dependency in sorted(graph[module]):
            visit(dependency, (*trail, module))
        visiting.remove(module)
        visited.add(module)

    for module in sorted(graph):
        visit(module, ())


def test_lower_layers_do_not_import_repository_build_or_server() -> None:
    forbidden_by_layer = {
        "facts": (".repository", ".build", "blueprint_tool_server"),
        "model": (".repository", ".build", "blueprint_tool_server"),
        "evaluation": (".repository", ".build", "blueprint_tool_server"),
    }
    for layer, forbidden in forbidden_by_layer.items():
        for path in (HARVEST_ROOT / layer).rglob("*.py"):
            imports = _local_imports(path)
            assert not any(
                marker in imported
                for imported in imports
                for marker in forbidden
            ), f"{path} crosses the {layer} boundary: {sorted(imports)}"


def test_formula_and_compatibility_ownership_is_explicit() -> None:
    formula_definitions: list[Path] = []
    for path in HARVEST_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "estimate_complete_node_yield"
            for node in ast.walk(tree)
        ):
            formula_definitions.append(path)
    assert formula_definitions == [HARVEST_ROOT / "model" / "complete_node.py"]

    repository_source = (HARVEST_ROOT / "repository" / "service.py").read_text(
        encoding="utf-8"
    )
    assert "def _component_coefficients_by_source" not in repository_source
    assert "estimate_complete_node_yield(" not in repository_source

    for compatibility_module in (
        ROOT / "scripts" / "blueprint_translator" / "harvest_ranking.py",
        ROOT / "scripts" / "blueprint_translator" / "harvest_evaluation_catalog.py",
        ROOT / "scripts" / "blueprint_translator" / "harvest_node_repository.py",
    ):
        tree = ast.parse(
            compatibility_module.read_text(encoding="utf-8"),
            filename=str(compatibility_module),
        )
        assert not any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            for node in tree.body
        ), f"Compatibility module owns behavior: {compatibility_module}"


def test_builder_and_frontend_facades_remain_thin() -> None:
    builder = (ROOT / "scripts" / "build_ark_harvest_evaluation_catalog.py").read_text(
        encoding="utf-8"
    )
    assert "blueprint_tool_server" not in builder
    assert "def discover_creature_candidates" not in builder
    assert "def trace_primal_dino_ancestry" not in builder
    assert "def build_creature_record" not in builder
    assert "def build_catalog" not in builder

    explorer = (ROOT / "src" / "harvest" / "explorer.ts").read_text(
        encoding="utf-8"
    )
    assert "class HarvestExplorer" not in explorer
    assert "<section" not in explorer
    assert "innerHTML" not in explorer

    ranking_view = (ROOT / "src" / "harvest" / "views" / "ranking.ts").read_text(
        encoding="utf-8"
    )
    for authoritative_rows in (
        "ranking.items.sort(",
        "ranking.confirmedItems.sort(",
        "ranking.conditionalItems.sort(",
        "rows.sort(",
    ):
        assert authoritative_rows not in ranking_view
    assert "from '../../shared/html'" in ranking_view
