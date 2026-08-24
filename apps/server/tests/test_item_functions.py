from __future__ import annotations

import pytest

from trpg_server.core.state import Projection
from trpg_server.items.functions import (
    ItemFunctionError,
    parse_consumable_profile,
    parse_equipment_profile,
    validate_generated_function_profiles,
)
from trpg_server.items.inventory import can_operate
from trpg_server.items.models import ItemContainer, ItemInstance


def _effect(*, magnitude: str = "minor") -> dict[str, object]:
    return {
        "domain": "locations",
        "effectKind": "illumination",
        "summary": "燃烧时提供有限照明",
        "magnitude": magnitude,
        "durationMinutes": 60,
        "requiresDomainResolution": True,
    }


def _consumable(*, risk: str = "moderate", magnitude: str = "minor") -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "quantityPerUse": 1,
        "method": "burn",
        "targetKinds": ["location"],
        "riskClass": risk,
        "effectCandidates": [_effect(magnitude=magnitude)],
    }


def _item(*, category: str, properties: dict[str, object]) -> ItemInstance:
    return ItemInstance(
        item_id="item_test",
        definition_id="definition_test",
        name="测试物品",
        description="用于验证物品功能契约。",
        category=category,
        is_plot_item=False,
        quantity=1,
        stackable=True,
        unit_weight_grams=50,
        value_crown=2,
        condition="intact",
        durability=None,
        container_id="player_pack",
        location_id=None,
        properties=properties,
    )


def test_equipment_profiles_cover_one_hand_two_hands_and_worn_slots() -> None:
    one_hand = parse_equipment_profile(
        {"mode": "held", "slotIds": ["left_hand", "right_hand"], "handCount": 1}
    )
    two_hands = parse_equipment_profile(
        {"mode": "held", "slotIds": ["left_hand", "right_hand"], "handCount": 2}
    )
    worn = parse_equipment_profile(
        {"mode": "worn", "slotIds": ["left_foot", "right_foot"], "handCount": 0}
    )

    assert one_hand.hand_count == 1
    assert two_hands.hand_count == 2
    assert worn.slot_ids == ("left_foot", "right_foot")


def test_invalid_equipment_slot_and_hand_count_are_rejected() -> None:
    with pytest.raises(ItemFunctionError, match="unknown body slot"):
        parse_equipment_profile(
            {"mode": "worn", "slotIds": ["waist"], "handCount": 0}
        )
    with pytest.raises(ItemFunctionError, match="must be 0"):
        parse_equipment_profile(
            {"mode": "worn", "slotIds": ["torso"], "handCount": 1}
        )


def test_non_food_consumable_is_allowed_but_category_alone_is_not() -> None:
    state = Projection(campaign_id="cmp", player_character_id="player")
    state.containers["player_pack"] = ItemContainer(
        container_id="player_pack",
        kind="inventory",
        owner_character_id="player",
    )
    candle = _item(category="household", properties={"consumable": _consumable()})
    food_without_profile = _item(category="food", properties={})

    assert can_operate(state, candle, "player", "consume").allowed is True
    rejected = can_operate(state, food_without_profile, "player", "consume")
    assert rejected.allowed is False
    assert rejected.code == "not_consumable"


def test_consumable_effect_can_never_bypass_domain_resolution() -> None:
    value = _consumable()
    value["effectCandidates"][0]["requiresDomainResolution"] = False  # type: ignore[index]

    with pytest.raises(ItemFunctionError, match="require domain resolution"):
        parse_consumable_profile(value)


@pytest.mark.parametrize(
    "profile",
    [
        _consumable(risk="high"),
        _consumable(risk="restricted"),
        _consumable(magnitude="major"),
    ],
)
def test_ai_generated_unsafe_consumable_profiles_are_rejected(
    profile: dict[str, object],
) -> None:
    with pytest.raises(ItemFunctionError):
        validate_generated_function_profiles(equipment=None, consumable=profile)

