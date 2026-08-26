from __future__ import annotations

import pytest

from trpg_server.core.projection import replay
from trpg_server.behavior.router import interpret_player_text, resolve
from trpg_server.core.state import Event, Projection
from trpg_server.items.commands import (
    build_item_consumed_event,
    build_item_created_event,
    build_item_transferred_event,
)
from trpg_server.items.models import ItemContainer, ItemInstance


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


def _bread() -> ItemInstance:
    return ItemInstance(
        item_id="bread_stack",
        definition_id="bread",
        name="黑麦面包",
        description="一份可以分取的黑麦面包。",
        category="food",
        is_plot_item=False,
        quantity=2,
        stackable=True,
        unit_weight_grams=450,
        value_crown=12,
        condition="intact",
        durability=None,
        container_id="pantry",
        location_id=None,
        properties={
            "consumable": {
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
        },
    )


def test_create_take_split_and_consume_are_replayable_current_events() -> None:
    bread = _bread()
    created = build_item_created_event(actor_id="system", world_time=1, item=bread)
    taken = build_item_transferred_event(
        actor_id="player",
        world_time=2,
        item_id=bread.item_id,
        to_container_id="player_pack",
        quantity=1,
        to_item_id="bread_taken",
    )
    consumed = build_item_consumed_event(
        actor_id="player",
        world_time=3,
        item_id="bread_taken",
    )
    events = [
        _container_event("pantry", "keeper"),
        _container_event("player_pack", "player"),
        created,
        taken,
        consumed,
    ]

    state = replay("cmp_item_lifecycle", events, len(events))
    replayed = replay("cmp_item_lifecycle", events, len(events))

    assert created.schema_version == 4
    assert taken.schema_version == consumed.schema_version == 3
    assert state.items["bread_stack"].quantity == 1
    assert "bread_taken" not in state.items
    assert replayed.items["bread_stack"].to_payload() == state.items["bread_stack"].to_payload()


def test_replay_rejects_duplicate_item_creation() -> None:
    created = build_item_created_event(actor_id="system", world_time=1, item=_bread())
    events = [_container_event("pantry", "keeper"), created, created]

    with pytest.raises(ValueError, match="item already exists"):
        replay("cmp_duplicate_item", events, len(events))


def test_discard_transfers_an_owned_item_to_the_actor_location() -> None:
    state = Projection(campaign_id="cmp_discard", player_character_id="player")
    state.character_locations["player"] = "market_square"
    state.locations["market_square"] = object()  # type: ignore[assignment]
    state.containers["player_pack"] = ItemContainer(
        container_id="player_pack",
        kind="inventory",
        owner_character_id="player",
    )
    bread = _bread()
    bread.container_id = "player_pack"
    state.items[bread.item_id] = bread

    resolution = resolve(
        state,
        interpret_player_text("丢弃黑麦面包", actor_id="player", state=state),
    )

    assert resolution.status == "committed"
    assert resolution.outcome == "item_discarded"
    assert resolution.events[0].event_type == "item.transferred"
    replayed = replay(
        "cmp_discard",
        [
            Event(
                "evt_market",
                "location.created",
                "system",
                0,
                {
                    "locationId": "market_square",
                    "name": "市场广场",
                    "exits": [],
                },
            ),
            _container_event("player_pack", "player"),
            build_item_created_event(actor_id="system", world_time=0, item=bread),
            resolution.events[0],
        ],
        4,
    )
    assert replayed.items[bread.item_id].container_id is None
    assert replayed.items[bread.item_id].location_id == "market_square"


def test_retired_deletion_style_discard_event_is_rejected() -> None:
    with pytest.raises(ValueError, match="item.discarded is retired"):
        replay(
            "cmp_retired_discard",
            [
                Event(
                    "evt_discard",
                    "item.discarded",
                    "player",
                    0,
                    {"itemId": "bread_stack"},
                    schema_version=3,
                )
            ],
            1,
        )


def test_plot_item_cannot_be_destroyed_by_generic_player_command() -> None:
    state = Projection(campaign_id="cmp_plot_destroy", player_character_id="player")
    state.character_locations["player"] = "market_square"
    state.locations["market_square"] = object()  # type: ignore[assignment]
    state.containers["player_pack"] = ItemContainer(
        container_id="player_pack",
        kind="inventory",
        owner_character_id="player",
    )
    plot_item = _bread()
    plot_item.item_id = "plot_ledger"
    plot_item.definition_id = "plot_ledger_definition"
    plot_item.name = "黑潮账本"
    plot_item.is_plot_item = True
    plot_item.container_id = "player_pack"
    state.items[plot_item.item_id] = plot_item

    command = interpret_player_text("销毁黑潮账本", actor_id="player", state=state)
    resolution = resolve(state, command)

    assert resolution.status == "rejected"
    assert resolution.outcome == "plot_item_requires_story_confirmation"
    assert not resolution.events


def test_projection_rejects_generic_plot_item_destroy_event() -> None:
    plot_item = _bread()
    plot_item.item_id = "plot_ledger"
    plot_item.definition_id = "plot_ledger_definition"
    plot_item.is_plot_item = True
    created = build_item_created_event(actor_id="system", world_time=1, item=plot_item)
    with pytest.raises(ValueError, match="story-confirmed"):
        replay(
            "cmp_plot_destroy_replay",
            [
                _container_event("pantry", "keeper"),
                created,
                Event(
                    "evt_plot_destroy",
                    "item.destroyed",
                    "player",
                    2,
                    {"itemId": "plot_ledger", "characterId": "player"},
                    schema_version=3,
                ),
            ],
            3,
        )
