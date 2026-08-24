from __future__ import annotations

import pytest

from trpg_server.characters.inventory import (
    ensure_inventory_containers,
    inventory_container_id,
    inventory_item_ids,
)
from trpg_server.core.projection import replay
from trpg_server.core.state import Projection
from trpg_server.items.models import ItemContainer, ItemInstance
from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events


def test_bootstrap_gives_every_runtime_character_one_inventory_container() -> None:
    events = gray_harbor_events()
    character_events = [event for event in events if event.event_type == "character.created"]
    inventory_events = [
        event
        for event in events
        if event.event_type == "container.created"
        and event.payload.get("kind") == "inventory"
    ]

    character_ids = {event.payload["characterId"] for event in character_events}
    owners = [event.payload["ownerCharacterId"] for event in inventory_events]

    assert character_events
    assert len(character_events) == len(character_ids)
    assert len(owners) == len(character_ids)
    assert set(owners) == character_ids
    assert len(set(event.payload["containerId"] for event in inventory_events)) == len(owners)
    assert all(
        event.payload.get("inventoryContainerId")
        for event in character_events
    )

    projection = replay(
        GRAY_HARBOR_CAMPAIGN_ID,
        events,
        len(events),
    )
    assert all(
        inventory_container_id(projection, character_id) is not None
        for character_id in character_ids
    )
    assert all(
        event_index < next(
            index
            for index, candidate in enumerate(events)
            if candidate.event_type == "character.created"
            and candidate.payload["characterId"] == event.payload["ownerCharacterId"]
        )
        for event_index, event in enumerate(events)
        if event.event_type == "container.created"
        and event.payload.get("kind") == "inventory"
    )


def test_inventory_read_model_returns_item_instance_ids_only() -> None:
    state = Projection(campaign_id="cmp_test")
    state.character_profiles["npc"] = {"inventoryContainerId": "npc_inventory"}
    state.containers["npc_inventory"] = ItemContainer(
        container_id="npc_inventory",
        kind="inventory",
        owner_character_id="npc",
    )
    state.items["item_a"] = ItemInstance(
        item_id="item_a",
        definition_id="definition_a",
        name="物品 A",
        description="用于背包归属查询的测试物品。",
        category="tool",
        is_plot_item=False,
        quantity=1,
        stackable=False,
        unit_weight_grams=None,
        value_crown=None,
        condition="intact",
        durability=None,
        container_id="npc_inventory",
        location_id=None,
        properties={},
    )
    state.items["item_b"] = ItemInstance(
        item_id="item_b",
        definition_id="definition_b",
        name="物品 B",
        description="不在目标背包中的测试物品。",
        category="tool",
        is_plot_item=False,
        quantity=1,
        stackable=False,
        unit_weight_grams=None,
        value_crown=None,
        condition="intact",
        durability=None,
        container_id="other_container",
        location_id=None,
        properties={},
    )

    assert inventory_container_id(state, "npc") == "npc_inventory"
    assert inventory_item_ids(state, "npc") == ("item_a",)


def test_inventory_read_model_supports_historical_events_without_declared_binding() -> None:
    state = Projection(campaign_id="cmp_test")
    state.containers["legacy_inventory"] = ItemContainer(
        container_id="legacy_inventory",
        kind="inventory",
        owner_character_id="legacy_npc",
    )

    assert inventory_container_id(state, "legacy_npc") == "legacy_inventory"


def test_inventory_binding_can_be_read_before_container_projection() -> None:
    state = Projection(campaign_id="cmp_test")
    state.character_profiles["npc"] = {"inventoryContainerId": "npc_inventory"}

    assert inventory_container_id(state, "npc") == "npc_inventory"
    assert inventory_item_ids(state, "npc") == ()


def test_inventory_resolution_rejects_multiple_explicit_containers_for_one_owner() -> None:
    with pytest.raises(ValueError, match="multiple inventory containers"):
        ensure_inventory_containers(
            ["npc"],
            [
                {
                    "id": "npc_inventory_a",
                    "kind": "inventory",
                    "ownerCharacterId": "npc",
                },
                {
                    "id": "npc_inventory_b",
                    "kind": "inventory",
                    "ownerCharacterId": "npc",
                },
            ],
        )


def test_inventory_resolution_preserves_explicit_and_generates_empty_bindings() -> None:
    resolution = ensure_inventory_containers(
        ["npc_a", "npc_b"],
        [
            {
                "id": "npc_a_pack",
                "kind": "inventory",
                "ownerCharacterId": "npc_a",
            }
        ],
    )

    assert resolution.by_character == {
        "npc_a": "npc_a_pack",
        "npc_b": "inventory_npc_b",
    }
    assert [value.owner_character_id for value in resolution.generated] == ["npc_b"]
