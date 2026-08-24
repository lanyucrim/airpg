from __future__ import annotations

from trpg_server.core.projection import replay
from trpg_server.core.state import Event
from trpg_server.items.commands import (
    build_item_consumed_event,
    build_item_created_event,
    build_item_transferred_event,
)
from trpg_server.items.contract import ITEM_RECORD_FIELD_SET
from trpg_server.items.models import ItemInstance


def _container_event(container_id: str, owner_id: str) -> Event:
    return Event(
        event_id=f"evt_container_{container_id}",
        event_type="container.created",
        actor_id="system",
        world_time=0,
        payload={
            "containerId": container_id,
            "kind": "inventory",
            "ownerCharacterId": owner_id,
        },
    )


def _item(
    *,
    item_id: str = "item_test_food",
    quantity: int = 1,
    stackable: bool = False,
    container_id: str = "inventory_a",
) -> ItemInstance:
    return ItemInstance(
        item_id=item_id,
        definition_id="test_food",
        name="测试面包",
        description="一份用于回放测试的食物。",
        category="food",
        is_plot_item=False,
        quantity=quantity,
        stackable=stackable,
        unit_weight_grams=120,
        value_crown=4,
        condition="intact",
        durability=None,
        container_id=container_id,
        location_id=None,
        properties={"volumeCm3": 180},
    )


def test_schema_four_creation_and_transfer_replay_a_complete_15_field_record() -> None:
    item = _item()
    created = build_item_created_event(
        actor_id="system",
        world_time=10,
        item=item,
    )
    transferred = build_item_transferred_event(
        actor_id="player_a",
        world_time=11,
        item_id=item.item_id,
        to_container_id="inventory_b",
    )
    state = replay(
        "cmp_item_schema_three",
        [
            _container_event("inventory_a", "player_a"),
            _container_event("inventory_b", "player_b"),
            created,
            transferred,
        ],
        4,
    )

    assert created.schema_version == 4
    assert transferred.schema_version == 3
    assert set(created.payload["item"]) == ITEM_RECORD_FIELD_SET
    assert state.items[item.item_id].to_payload() == {
        **item.to_payload(),
        "containerId": "inventory_b",
    }
    assert state.items[item.item_id].source_event_id == created.event_id
    assert state.items[item.item_id].last_changed_event_id == transferred.event_id


def test_schema_three_split_and_consumption_replay_preserve_quantities() -> None:
    item = _item(quantity=3, stackable=True)
    created = build_item_created_event(actor_id="system", world_time=10, item=item)
    split = build_item_transferred_event(
        actor_id="player_a",
        world_time=11,
        item_id=item.item_id,
        to_container_id="inventory_b",
        quantity=2,
        to_item_id="item_test_food_split",
    )
    consume_one = build_item_consumed_event(
        actor_id="player_b",
        world_time=12,
        item_id="item_test_food_split",
        quantity=1,
    )
    events = [
        _container_event("inventory_a", "player_a"),
        _container_event("inventory_b", "player_b"),
        created,
        split,
        consume_one,
    ]
    state = replay("cmp_item_split", events, len(events))

    assert split.schema_version == 3
    assert consume_one.schema_version == 3
    assert state.items[item.item_id].quantity == 1
    assert state.items["item_test_food_split"].quantity == 1
    assert state.items["item_test_food_split"].container_id == "inventory_b"

    consume_last = build_item_consumed_event(
        actor_id="player_b",
        world_time=13,
        item_id="item_test_food_split",
    )
    final_state = replay("cmp_item_split", [*events, consume_last], len(events) + 1)

    assert "item_test_food_split" not in final_state.items
    assert final_state.items[item.item_id].quantity == 1
