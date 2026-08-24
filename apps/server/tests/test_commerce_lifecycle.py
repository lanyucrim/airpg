import pytest

from trpg_server.core.projection import apply_event
from trpg_server.core.state import ParsedCommand, Projection
from trpg_server.items.commerce import build_purchase_event, resolve_purchase
from trpg_server.items.models import ItemContainer, ItemInstance


def _purchasable_item(*, plot_item: bool = False) -> ItemInstance:
    return ItemInstance(
        item_id="purchased_bread",
        definition_id="bread",
        name="测试面包",
        description="一条经确认交易交付的面包。",
        category="food",
        is_plot_item=plot_item,
        quantity=1,
        stackable=True,
        unit_weight_grams=300,
        value_crown=8,
        condition="intact",
        durability=None,
        container_id="player_pack",
        location_id=None,
        properties={},
    )


def test_confirmed_purchase_builds_and_replays_a_complete_schema_three_item() -> None:
    state = Projection(campaign_id="cmp_commerce")
    state.containers["player_pack"] = ItemContainer(
        container_id="player_pack",
        kind="inventory",
        owner_character_id="player",
    )
    event = build_purchase_event(
        actor_id="player",
        world_time=10,
        item=_purchasable_item(),
        transaction_id="txn_confirmed",
        payment_event_id="evt_payment_confirmed",
    )

    apply_event(state, event)

    assert event.schema_version == 3
    assert event.event_type == "item.purchased"
    assert set(event.payload["item"]) == {
        "id",
        "definitionId",
        "name",
        "description",
        "category",
        "isPlotItem",
        "quantity",
        "stackable",
        "unitWeightGrams",
        "valueCrown",
        "condition",
        "durability",
        "containerId",
        "locationId",
        "properties",
    }
    assert state.items["purchased_bread"].container_id == "player_pack"
    assert state.items["purchased_bread"].source_event_id == event.event_id
    assert state.items["purchased_bread"].last_changed_event_id == event.event_id


def test_item_side_purchase_rejects_plot_items_without_story_confirmation() -> None:
    with pytest.raises(ValueError, match="剧情道具"):
        build_purchase_event(
            actor_id="player",
            world_time=10,
            item=_purchasable_item(plot_item=True),
            transaction_id="txn_plot",
            payment_event_id="evt_payment_confirmed",
        )


def test_legacy_text_purchase_route_remains_rejected_without_confirmed_economy_data() -> None:
    command = ParsedCommand(
        action_type="purchase_item",
        actor_id="player",
        target_id="offer_bread",
        parameters={"offerId": "offer_bread", "quantity": 1},
        original_text="购买测试面包",
        authority="player",
    )

    result = resolve_purchase(Projection(campaign_id="cmp_commerce"), command)

    assert result.status == "rejected"
    assert result.outcome == "commerce_integration_pending"
    assert result.events == []
