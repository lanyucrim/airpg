from __future__ import annotations

import ast
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "trpg_server"

RETIRED_TOP_LEVEL_MODULES = {
    "bootstrap.py", "commerce.py", "consequences.py", "deepseek.py", "director.py",
    "domain.py", "engine.py", "environment.py", "evaluation.py", "intent.py",
    "inventory.py", "investigation.py", "main.py", "memory.py", "memory_evaluation.py",
    "movement.py", "narration.py", "npc_decision.py", "npc_evaluation.py", "ports.py",
    "postgres_schema.py", "postgres_store.py", "projection.py", "routine.py", "scenario.py",
    "schemas.py", "service.py", "store.py", "turn_pipeline.py", "v4_compiler.py",
    "world_simulation.py",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_retired_top_level_modules_do_not_return() -> None:
    existing = {path.name for path in PACKAGE_ROOT.glob("*.py")}
    assert not existing.intersection(RETIRED_TOP_LEVEL_MODULES)


def test_ai_platform_does_not_depend_on_authoritative_writers() -> None:
    forbidden = {
        "trpg_server.core.service",
        "trpg_server.core.store",
        "trpg_server.core.postgres_store",
        "trpg_server.core.projection",
    }
    for path in (PACKAGE_ROOT / "ai" / "platform").glob("*.py"):
        assert not _imports(path).intersection(forbidden), path.name


def test_player_ai_does_not_construct_authoritative_events() -> None:
    for path in (PACKAGE_ROOT / "ai" / "player").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constructors = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "Event" not in constructors, path.name


def test_character_and_location_domains_do_not_import_item_implementation() -> None:
    for domain in ("characters", "locations"):
        for path in (PACKAGE_ROOT / domain).glob("*.py"):
            item_imports = {
                name for name in _imports(path)
                if name.startswith("trpg_server.items")
            }
            assert not item_imports, f"{domain}/{path.name}: {sorted(item_imports)}"


def test_map_domain_does_not_depend_on_ai_or_authoritative_writers() -> None:
    forbidden = {
        "trpg_server.ai",
        "trpg_server.core.service",
        "trpg_server.core.store",
        "trpg_server.core.postgres_store",
    }
    for path in (PACKAGE_ROOT / "map").glob("*.py"):
        imports = _imports(path)
        violations = {
            name
            for name in imports
            if any(name == value or name.startswith(f"{value}.") for value in forbidden)
        }
        assert not violations, f"map/{path.name}: {sorted(violations)}"


def test_ai_item_tooling_stays_inside_items_and_has_no_authoritative_writers() -> None:
    ai_items = PACKAGE_ROOT / "items" / "ai_items"
    assert (ai_items / "generation.py").is_file()
    assert (ai_items / "deepseek_adapter.py").is_file()
    assert (ai_items / "era.py").is_file()
    assert (ai_items / "recipes.py").is_file()
    assert not (PACKAGE_ROOT / "ai" / "platform" / "item_reference_adapter.py").exists()
    forbidden_prefixes = (
        "trpg_server.core.service",
        "trpg_server.core.store",
        "trpg_server.core.projection",
        "trpg_server.core.state",
        "trpg_server.items.commands",
        "trpg_server.items.events",
        "trpg_server.items.recipes",
    )
    for path in ai_items.glob("*.py"):
        violations = {
            name
            for name in _imports(path)
            if any(
                name == prefix or name.startswith(f"{prefix}.")
                for prefix in forbidden_prefixes
            )
        }
        assert not violations, f"items/ai_items/{path.name}: {sorted(violations)}"
