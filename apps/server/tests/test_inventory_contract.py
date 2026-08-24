from __future__ import annotations

from trpg_server.core.state import Projection
from trpg_server.items.inventory import (
    can_operate,
    item_public_summary,
    validate_container_capacity,
)
from trpg_server.items.models import ItemContainer, ItemInstance


def _item(
    *,
    item_id: str,
    container_id: str | None,
    location_id: str | None = None,
    category: str = "food",
    quantity: int = 1,
    unit_weight_grams: int | None = 80,
    consumable: bool = False,
) -> ItemInstance:
    properties: dict[str, object] = {"volumeCm3": 25}
    if consumable:
        properties["consumable"] = {
            "schemaVersion": 1,
            "quantityPerUse": 1,
            "method": "eat",
            "targetKinds": ["character"],
            "riskClass": "low",
            "effectCandidates": [
                {
                    "domain": "characters",
                    "effectKind": "nourishment",
                    "summary": "作为普通食物缓解饥饿",
                    "magnitude": "minor",
                    "durationMinutes": None,
                    "requiresDomainResolution": True,
                }
            ],
        }
    return ItemInstance(
        item_id=item_id,
        definition_id=f"definition_{item_id}",
        name="测试物品",
        description="用于物品契约测试的可观察物品。",
        category=category,
        is_plot_item=False,
        quantity=quantity,
        stackable=quantity > 1,
        unit_weight_grams=unit_weight_grams,
        value_crown=None,
        condition="intact",
        durability=None,
        container_id=container_id,
        location_id=location_id,
        properties=properties,
    )


def test_operations_read_authoritative_placement_not_definition_metadata() -> None:
    state = Projection(campaign_id="cmp_inventory_contract")
    state.containers["player_pack"] = ItemContainer(
        container_id="player_pack",
        kind="inventory",
        owner_character_id="player",
    )
    held_food = _item(
        item_id="held_food", container_id="player_pack", consumable=True
    )
    location_food = _item(
        item_id="location_food",
        container_id=None,
        location_id="market",
    )

    assert can_operate(state, held_food, "player", "consume").code == "allowed"
    assert can_operate(state, held_food, "other", "consume").code == "item_not_owned"
    food_without_profile = _item(
        item_id="unprofiled_food", container_id="player_pack"
    )
    assert (
        can_operate(state, food_without_profile, "player", "consume").code
        == "not_consumable"
    )
    assert (
        can_operate(
            state,
            location_food,
            "player",
            "take",
            target_location_id="market",
        ).code
        == "allowed"
    )
    assert (
        can_operate(
            state,
            location_food,
            "player",
            "take",
            target_location_id="harbor",
        ).code
        == "item_not_accessible"
    )


def test_capacity_uses_15_field_weight_and_properties() -> None:
    state = Projection(campaign_id="cmp_inventory_capacity")
    state.containers["pack"] = ItemContainer(
        container_id="pack",
        kind="inventory",
        owner_character_id="player",
        capacity_weight=150,
        capacity_volume=100,
    )
    state.items["existing"] = _item(item_id="existing", container_id="pack")
    incoming = _item(
        item_id="incoming",
        container_id=None,
        location_id="market_stall",
    )

    assert validate_container_capacity(state, "pack", incoming).code == (
        "capacity_weight_exceeded"
    )

    state.containers["pack"].capacity_weight = 200
    assert validate_container_capacity(state, "pack", incoming).code == "allowed"


def test_capacity_does_not_treat_unknown_measurements_as_zero() -> None:
    state = Projection(campaign_id="cmp_inventory_unknown_capacity")
    state.containers["weight_limited"] = ItemContainer(
        container_id="weight_limited",
        kind="inventory",
        owner_character_id="player",
        capacity_weight=500,
    )
    unknown_weight = _item(
        item_id="unknown_weight",
        container_id=None,
        location_id="market",
        unit_weight_grams=None,
    )

    assert validate_container_capacity(
        state,
        "weight_limited",
        unknown_weight,
    ).code == "unknown_weight"

    state.containers["volume_limited"] = ItemContainer(
        container_id="volume_limited",
        kind="inventory",
        owner_character_id="player",
        capacity_volume=500,
    )
    unknown_volume = _item(
        item_id="unknown_volume",
        container_id=None,
        location_id="market",
    )
    unknown_volume.properties = {}

    assert validate_container_capacity(
        state,
        "volume_limited",
        unknown_volume,
    ).code == "unknown_volume"


def test_public_summary_is_the_15_field_record_plus_derived_totals() -> None:
    item = _item(item_id="summary", container_id="pack", quantity=2)

    summary = item_public_summary(item)

    assert summary["totalWeightGrams"] == 160
    assert summary["totalValueCrown"] is None
    assert "rights" not in summary
    assert "criticality" not in summary
