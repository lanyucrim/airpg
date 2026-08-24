"""Pure item-domain validation for a previously approved recipe."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping

from trpg_server.core.state import Event, Projection
from trpg_server.items.commands import (
    build_item_consumed_event,
    build_item_created_event,
)
from trpg_server.items.inventory import can_operate, validate_quantity
from trpg_server.items.models import ItemDefinition, ItemInstance
from trpg_server.items.recipe_models import (
    RecipeBlueprint,
    RecipeError,
    RecipeIngredient,
    normalize_ingredients,
)


@dataclass(frozen=True, slots=True)
class RecipeConversionInput:
    item_id: str
    quantity: int

    def __post_init__(self) -> None:
        if type(self.item_id) is not str or not self.item_id:
            raise RecipeError("conversion input item_id cannot be empty")
        if type(self.quantity) is not int or self.quantity < 1:
            raise RecipeError("conversion input quantity must be positive")


@dataclass(frozen=True, slots=True)
class RecipeConversionPlan:
    recipe_key: str
    consumed_item_ids: tuple[str, ...]
    output_item_id: str
    events: tuple[Event, ...]


def build_recipe_conversion_plan(
    state: Projection,
    *,
    actor_id: str,
    world_time: int,
    blueprint: RecipeBlueprint,
    output_definition: Mapping[str, Any],
    inputs: tuple[RecipeConversionInput, ...],
    output_item_id: str,
    destination_container_id: str,
) -> RecipeConversionPlan:
    """Return validated candidate events without mutating or submitting state."""

    if not inputs:
        raise RecipeError("conversion requires concrete input instances")
    if len({value.item_id for value in inputs}) != len(inputs):
        raise RecipeError("conversion input item ids must be unique")
    if not output_item_id or output_item_id in state.items:
        raise RecipeError("output item id is empty or already exists")
    container = state.containers.get(destination_container_id)
    if container is None or container.owner_character_id != actor_id:
        raise RecipeError("output container is not owned by the actor")

    required = Counter(
        {value.definition_id: value.quantity for value in blueprint.ingredients}
    )
    selected: Counter[str] = Counter()
    consume_events: list[Event] = []
    for selected_input in inputs:
        item = state.items.get(selected_input.item_id)
        check = can_operate(state, item, actor_id, "combine")
        if not check.allowed or item is None:
            raise RecipeError(f"input {selected_input.item_id} is unavailable: {check.code}")
        quantity_check = validate_quantity(item, selected_input.quantity)
        if not quantity_check.allowed:
            raise RecipeError(
                f"input {selected_input.item_id} quantity is invalid: {quantity_check.code}"
            )
        selected[item.definition_id] += selected_input.quantity
        consume_events.append(
            build_item_consumed_event(
                actor_id=actor_id,
                world_time=world_time,
                item_id=item.item_id,
                quantity=selected_input.quantity,
            )
        )
    if selected != required:
        raise RecipeError("concrete inputs do not exactly match the approved recipe")

    try:
        definition = ItemDefinition.from_payload(output_definition)
    except ValueError as error:
        raise RecipeError(f"output definition is invalid: {error}") from error
    if definition.definition_id != blueprint.output_definition_id:
        raise RecipeError("output definition differs from the approved recipe")
    if definition.is_plot_item or definition.category == "currency":
        raise RecipeError("recipe output cannot be a plot item or currency")
    if not definition.stackable and blueprint.output_quantity != 1:
        raise RecipeError("non-stackable recipe output quantity must be one")
    output = ItemInstance(
        item_id=output_item_id,
        definition_id=definition.definition_id,
        name=definition.name,
        description=definition.description,
        category=definition.category,
        is_plot_item=False,
        quantity=blueprint.output_quantity,
        stackable=definition.stackable,
        unit_weight_grams=definition.unit_weight_grams,
        value_crown=definition.value_crown,
        condition="intact",
        durability=None,
        container_id=destination_container_id,
        location_id=None,
        properties=dict(definition.properties),
    )
    created = build_item_created_event(
        actor_id=actor_id,
        world_time=world_time,
        item=output,
    )
    return RecipeConversionPlan(
        recipe_key=blueprint.recipe_key,
        consumed_item_ids=tuple(value.item_id for value in inputs),
        output_item_id=output_item_id,
        events=tuple([*consume_events, created]),
    )


__all__ = [
    "RecipeBlueprint",
    "RecipeConversionInput",
    "RecipeConversionPlan",
    "RecipeError",
    "RecipeIngredient",
    "build_recipe_conversion_plan",
    "normalize_ingredients",
]
