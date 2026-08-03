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


def test_repository_facade_only_composes_focused_services() -> None:
    repository_root = HARVEST_ROOT / "repository"
    expected_owners = {
        "dataset_loader.py": {"_load_catalog", "_load_sqlite_catalog", "list_nodes"},
        "revision_binding.py": {"_load_evaluation", "_evaluation_revisions"},
        "runtime_overlay.py": {"_load_runtime_observations"},
        "forward_service.py": {"_lazy_rankings", "rankings"},
        "specialty_service.py": {"_v2_tier_baselines", "creature_specialties"},
        "creature_service.py": {"list_creatures"},
    }
    for filename, required_methods in expected_owners.items():
        tree = ast.parse(
            (repository_root / filename).read_text(encoding="utf-8"),
            filename=filename,
        )
        owned_methods = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert required_methods <= owned_methods

    facade_path = repository_root / "service.py"
    facade_tree = ast.parse(
        facade_path.read_text(encoding="utf-8"), filename=str(facade_path)
    )
    facade = next(
        node
        for node in facade_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "HarvestNodeRepository"
    )
    assert {
        node.name
        for node in facade.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    } == {"__init__"}


def test_evaluation_engine_only_orchestrates_focused_services() -> None:
    evaluation_root = HARVEST_ROOT / "evaluation"
    expected_owners = {
        "runtime.py": {
            "_runtime_profile_context",
            "_eligible_runtime_observation",
        },
        "variant_selection.py": {
            "_canonical_variant_audit",
            "project_species_variants",
        },
        "species_evaluation.py": {"evaluate_species_catalog"},
        "tier_projection.py": {"project_tiers"},
        "result_projection.py": {"rank_node_resource_v2"},
        "legacy.py": {"rank_node_resource_v1"},
    }
    for filename, required_functions in expected_owners.items():
        tree = ast.parse(
            (evaluation_root / filename).read_text(encoding="utf-8"),
            filename=filename,
        )
        owned_functions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert required_functions <= owned_functions

    engine_path = evaluation_root / "engine.py"
    engine_tree = ast.parse(
        engine_path.read_text(encoding="utf-8"), filename=str(engine_path)
    )
    engine = next(
        node
        for node in engine_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "HarvestEvaluationEngine"
    )
    assert {
        node.name
        for node in engine.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    } == {
        "__init__",
        "canonical_variant_audits",
        "_rank_node_resource_v1",
        "rank_node_resource",
    }


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
