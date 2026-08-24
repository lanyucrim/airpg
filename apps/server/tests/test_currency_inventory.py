from __future__ import annotations

import pytest

from trpg_server.core.projection import public_state, replay
from trpg_server.core.projection_handlers import projection_handlers
from trpg_server.core.state import Event
from trpg_server.items.models import ItemInstance


def _kron_stack_payload() -> dict[str, object]:
    return ItemInstance(
        item_id="protagonist_kron_stack",
        definition_id="currency_kron",
        name="克朗",
        description="王国克朗体系的最小整数面额。",
        category="currency",
        is_plot_item=False,
        quantity=37,
        stackable=True,
        unit_weight_grams=None,
        value_crown=1,
        condition="intact",
        durability=None,
        container_id="protagonist_inventory",
        location_id=None,
        properties={},
    ).to_payload()


def test_player_cash_is_a_currency_stack_in_the_owned_inventory() -> None:
    events = (
        Event(
            event_id="evt_campaign",
            event_type="campaign.created",
            actor_id="system",
            world_time=0,
            payload={"name": "测试战役", "playerCharacterId": "protagonist"},
        ),
        Event(
            event_id="evt_pack",
            event_type="container.created",
            actor_id="system",
            world_time=0,
            payload={
                "containerId": "protagonist_inventory",
                "kind": "inventory",
                "ownerCharacterId": "protagonist",
                "locationId": None,
            },
        ),
        Event(
            event_id="evt_kron_stack",
            event_type="item.created",
            actor_id="system",
            world_time=0,
            payload={"item": _kron_stack_payload()},
            schema_version=3,
        ),
    )

    state = replay("cmp_currency_inventory", events, state_version=len(events))
    player = public_state(state)["player"]

    assert "balancePence" not in player
    assert player["inventory"] == [
        {
            "itemId": "protagonist_kron_stack",
            "definitionId": "currency_kron",
            "name": "克朗",
            "description": "王国克朗体系的最小整数面额。",
            "category": "currency",
            "isPlotItem": False,
            "quantity": 37,
            "stackable": True,
            "unitWeightGrams": None,
            "valueCrown": 1,
            "condition": "intact",
            "durability": None,
            "containerId": "protagonist_inventory",
            "locationId": None,
            "properties": {},
            "totalWeightGrams": None,
            "totalValueCrown": 37,
        }
    ]


def test_numeric_currency_events_are_not_part_of_the_current_projection() -> None:
    assert "currency.balance_initialized" not in projection_handlers.event_types
    assert "money.transferred" not in projection_handlers.event_types


def test_campaign_rejects_the_removed_initial_numeric_balance() -> None:
    event = Event(
        event_id="evt_legacy_balance",
        event_type="campaign.created",
        actor_id="system",
        world_time=0,
        payload={
            "name": "旧余额战役",
            "playerCharacterId": "protagonist",
            "initialBalances": {"protagonist": 37},
        },
    )

    with pytest.raises(ValueError, match="physical currency item instances"):
        replay("cmp_legacy_balance", (event,), state_version=1)
