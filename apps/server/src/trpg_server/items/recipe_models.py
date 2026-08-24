"""Pure recipe value objects shared by item rules and AI candidate validation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any


class RecipeError(ValueError):
    pass


@dataclass(frozen=True, slots=True, order=True)
class RecipeIngredient:
    definition_id: str
    quantity: int

    def __post_init__(self) -> None:
        if type(self.definition_id) is not str or not self.definition_id:
            raise RecipeError("recipe ingredient definition_id cannot be empty")
        if type(self.quantity) is not int or self.quantity < 1:
            raise RecipeError("recipe ingredient quantity must be positive")

    def to_mapping(self) -> dict[str, Any]:
        return {"definitionId": self.definition_id, "quantity": self.quantity}


@dataclass(frozen=True, slots=True)
class RecipeBlueprint:
    recipe_key: str
    ingredients: tuple[RecipeIngredient, ...]
    output_definition_id: str
    output_quantity: int
    process_summary: str

    def __post_init__(self) -> None:
        if type(self.recipe_key) is not str or not self.recipe_key:
            raise RecipeError("recipe_key cannot be empty")
        normalized = normalize_ingredients(self.ingredients)
        if normalized != self.ingredients:
            raise RecipeError("blueprint ingredients must be normalized")
        if type(self.output_definition_id) is not str or not self.output_definition_id:
            raise RecipeError("output_definition_id cannot be empty")
        if type(self.output_quantity) is not int or self.output_quantity < 1:
            raise RecipeError("output_quantity must be positive")
        if type(self.process_summary) is not str or not self.process_summary.strip():
            raise RecipeError("process_summary cannot be empty")


def normalize_ingredients(
    values: tuple[RecipeIngredient, ...] | list[RecipeIngredient],
) -> tuple[RecipeIngredient, ...]:
    totals: Counter[str] = Counter()
    for value in values:
        if not isinstance(value, RecipeIngredient):
            raise RecipeError("ingredients must contain RecipeIngredient values")
        totals[value.definition_id] += value.quantity
    if not totals:
        raise RecipeError("recipe requires at least one ingredient")
    if len(totals) > 12:
        raise RecipeError("recipe cannot use more than 12 ingredient definitions")
    return tuple(
        RecipeIngredient(definition_id, quantity)
        for definition_id, quantity in sorted(totals.items())
    )


__all__ = [
    "RecipeBlueprint",
    "RecipeError",
    "RecipeIngredient",
    "normalize_ingredients",
]
