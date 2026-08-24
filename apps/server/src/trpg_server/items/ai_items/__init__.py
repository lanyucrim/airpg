"""AI-assisted item catalog tooling, isolated from runtime item rules."""

from .generation import (
    DailyItemDefinitionCatalog,
    DailyItemDefinitionEntry,
    DailyItemGenerationRequest,
    resolve_daily_item_definition,
)
from .references import (
    DailyItemReference,
    DailyItemReferenceRequest,
    DailyItemReferenceTable,
    resolve_daily_item_reference,
    with_reference_measurements,
)
from .era import EraTechnologyProfile
from .durability import (
    InitialDurabilityRequest,
    InitialDurabilityResolution,
    resolve_initial_durability,
)
from .recipes import (
    GeneratedRecipeCatalog,
    RecipeAssessmentRequest,
    resolve_item_recipe,
)
from .furniture import (
    FurnitureAdapterResult,
    FurnitureCandidate,
    FurnitureGenerationError,
    FurnitureStructureRequest,
    resolve_furniture_candidates,
)

__all__ = [
    "DailyItemDefinitionCatalog",
    "DailyItemDefinitionEntry",
    "DailyItemGenerationRequest",
    "DailyItemReference",
    "DailyItemReferenceRequest",
    "DailyItemReferenceTable",
    "EraTechnologyProfile",
    "GeneratedRecipeCatalog",
    "InitialDurabilityRequest",
    "InitialDurabilityResolution",
    "RecipeAssessmentRequest",
    "resolve_daily_item_reference",
    "resolve_daily_item_definition",
    "resolve_initial_durability",
    "resolve_item_recipe",
    "with_reference_measurements",
    "FurnitureAdapterResult",
    "FurnitureCandidate",
    "FurnitureGenerationError",
    "FurnitureStructureRequest",
    "resolve_furniture_candidates",
]
