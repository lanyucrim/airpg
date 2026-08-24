"""Assess or reuse one strict ordinary-item recipe at design time."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SERVER_SRC = ROOT / "apps" / "server" / "src"
AI_ITEM_DIR = (
    ROOT
    / "content"
    / "campaigns"
    / "gray-harbor"
    / "items-atlas"
    / "ai-items"
)
if str(SERVER_SRC) not in sys.path:
    sys.path.insert(0, str(SERVER_SRC))

from trpg_server.ai.platform.deepseek import DeepSeekSettings  # noqa: E402
from trpg_server.ai.platform.environment import load_backend_environment  # noqa: E402
from trpg_server.items.ai_items.deepseek_adapter import (  # noqa: E402
    DeepSeekDailyItemGenerationAdapter,
    DeepSeekRecipeAssessmentAdapter,
)
from trpg_server.items.ai_items.era import EraTechnologyProfile  # noqa: E402
from trpg_server.items.ai_items.generation import (  # noqa: E402
    DailyItemDefinitionCatalog,
    render_daily_item_definition_markdown,
)
from trpg_server.items.ai_items.recipes import (  # noqa: E402
    GeneratedRecipeCatalog,
    RecipeAssessmentRequest,
    render_generated_recipe_markdown,
    resolve_item_recipe,
)
from trpg_server.items.ai_items.references import (  # noqa: E402
    DailyItemReferenceTable,
    render_daily_item_reference_markdown,
)
from trpg_server.items.catalog import load_item_atlas  # noqa: E402
from trpg_server.items.recipes import RecipeIngredient  # noqa: E402


DEFAULT_RECIPES = AI_ITEM_DIR / "generated-recipes.json"
DEFAULT_DAILY = AI_ITEM_DIR / "daily-item-definitions.json"
DEFAULT_REFERENCES = AI_ITEM_DIR / "daily-item-references.json"
DEFAULT_ERA = AI_ITEM_DIR / "era-technology-profile.json"
DEFAULT_ATLAS = AI_ITEM_DIR.parent / "important-items.json"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read or assess one era-constrained ordinary-item recipe."
    )
    parser.add_argument("--process", required=True, help="Physical combination process.")
    parser.add_argument(
        "--ingredient",
        required=True,
        action="append",
        metavar="DEFINITION_ID=QUANTITY",
        help="Exact input definition and quantity; repeat for multiple inputs.",
    )
    parser.add_argument(
        "--allow-ai",
        action="store_true",
        help="Allow one assessment call and at most one output-definition call on cache miss.",
    )
    parser.add_argument("--recipes", type=Path, default=DEFAULT_RECIPES)
    parser.add_argument("--daily", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--references", type=Path, default=DEFAULT_REFERENCES)
    parser.add_argument("--era", type=Path, default=DEFAULT_ERA)
    parser.add_argument("--atlas", type=Path, default=DEFAULT_ATLAS)
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    era = EraTechnologyProfile.load(args.era)
    recipe_catalog = GeneratedRecipeCatalog.load(args.recipes, era)
    daily_catalog = DailyItemDefinitionCatalog.load(args.daily)
    references = DailyItemReferenceTable.load(args.references)
    known = load_item_atlas(args.atlas).definitions
    request = RecipeAssessmentRequest(
        args.process,
        tuple(_ingredient(value) for value in args.ingredient),
    )

    cached = resolve_item_recipe(
        recipe_catalog,
        daily_catalog,
        references,
        era,
        request,
        known_definitions=known,
    )
    if cached.status == "cache_hit":
        assert cached.entry is not None
        print(
            f"cache_hit: {cached.entry.recipe_key} -> "
            f"{cached.entry.output_definition_id} x{cached.entry.output_quantity}; no AI call"
        )
        return
    if cached.status == "rejected":
        raise SystemExit(f"recipe cannot be assessed: {cached.reason}")
    if not args.allow_ai:
        raise SystemExit(
            "cache miss: rerun with --allow-ai to assess once and generate the output only if needed"
        )

    load_backend_environment()
    settings = DeepSeekSettings.from_environment()
    resolution = resolve_item_recipe(
        recipe_catalog,
        daily_catalog,
        references,
        era,
        request,
        DeepSeekRecipeAssessmentAdapter(settings),
        DeepSeekDailyItemGenerationAdapter(settings),
        known_definitions=known,
    )
    if resolution.status != "model_accepted" or resolution.entry is None:
        raise SystemExit(f"recipe rejected: {resolution.reason or resolution.status}")

    _save_pair(
        args.recipes,
        resolution.recipe_catalog.save,
        render_generated_recipe_markdown(resolution.recipe_catalog),
    )
    _save_pair(
        args.daily,
        resolution.daily_catalog.save,
        render_daily_item_definition_markdown(resolution.daily_catalog),
    )
    _save_pair(
        args.references,
        resolution.reference_table.save,
        render_daily_item_reference_markdown(resolution.reference_table),
    )
    entry = resolution.entry
    print(
        f"model_accepted: {entry.recipe_key} -> "
        f"{entry.output_definition_id} x{entry.output_quantity}; "
        f"assessment_tokens={entry.model_audit.total_tokens or 'unknown'}"
    )


def _ingredient(value: str) -> RecipeIngredient:
    definition_id, separator, quantity = value.rpartition("=")
    if not separator or not definition_id:
        raise argparse.ArgumentTypeError(
            "ingredient must use DEFINITION_ID=QUANTITY"
        )
    try:
        parsed_quantity = int(quantity)
    except ValueError as error:
        raise argparse.ArgumentTypeError("ingredient quantity must be an integer") from error
    return RecipeIngredient(definition_id, parsed_quantity)


def _save_pair(path: Path, save, markdown: str) -> None:  # type: ignore[no-untyped-def]
    save(path)
    path.with_suffix(".md").write_text(markdown, encoding="utf-8")


if __name__ == "__main__":
    main()
