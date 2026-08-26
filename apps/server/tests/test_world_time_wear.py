from __future__ import annotations

from trpg_server.core.projection import replay
from trpg_server.core.state import Event
from trpg_server.items.commands import build_item_created_event
from trpg_server.items.models import ItemInstance
from trpg_server.characters.equipment import build_item_equipped_event
from trpg_server.world.time import materialize_clothing_wear_event_plan, materialize_clothing_wear_events


def _events() -> list[Event]:
    item = ItemInstance(
        item_id="coat_1", definition_id="coat_def", name="大衣",
        description="厚实布料制成的大衣。", category="clothing",
        is_plot_item=False, quantity=1, stackable=False,
        unit_weight_grams=1000, value_crown=100, condition="new",
        durability={"current": 100.0, "max": 100.0}, container_id="bag",
        location_id=None,
        properties={"equipment": {"mode": "worn", "slotIds": ["torso"], "handCount": 0}},
    )
    return [
        Event("campaign", "campaign.created", "system", 0, {"name": "t", "playerCharacterId": "player"}),
        Event("room", "location.created", "system", 0, {"locationId": "room", "name": "房间", "exits": []}),
        Event("bag", "container.created", "system", 0, {"containerId": "bag", "kind": "inventory", "ownerCharacterId": "player"}),
        Event("char", "character.created", "system", 0, {"characterId": "player", "characterType": "player", "name": "玩家", "locationId": "room"}),
        build_item_created_event(actor_id="system", world_time=0, item=item),
    ]


def test_clothing_wear_only_when_equipped_and_time_advances() -> None:
    base = _events()
    state = replay("time", base, len(base))
    equip = build_item_equipped_event(actor_id="player", world_time=0, character_id="player", item_id="coat_1", slot_ids=("torso",))
    equipped = replay("time", [*base, equip], len(base) + 1)
    advance = Event("clock_1", "time.advanced", "system", 480, {"from": 0, "to": 480, "minutes": 480, "reason": "wait"})
    generated = materialize_clothing_wear_events(equipped, [advance])
    assert len(generated) == 1
    final = replay("time", [*base, equip, advance, *generated], len(base) + 3)
    assert final.items["coat_1"].durability["current"] == 99.44
    assert materialize_clothing_wear_events(state, [advance]) == ()


def test_plan_inserts_wear_after_each_time_source() -> None:
    base = _events()
    state = replay("time_multi", base, len(base))
    equip = build_item_equipped_event(actor_id="player", world_time=0, character_id="player", item_id="coat_1", slot_ids=("torso",))
    first = Event("clock_a", "time.advanced", "system", 240, {"from": 0, "to": 240, "minutes": 240, "reason": "wait"})
    second = Event("clock_b", "time.advanced", "system", 480, {"from": 240, "to": 480, "minutes": 240, "reason": "wait"})
    plan = materialize_clothing_wear_event_plan(
        replay("time_multi", [*base, equip], len(base) + 1), [first, second]
    )
    assert [event.event_type for event in plan] == [
        "time.advanced", "item.wear_applied", "time.advanced", "item.wear_applied"
    ]
    final = replay("time_multi", [*base, equip, *plan], len(base) + 1 + len(plan))
    assert final.items["coat_1"].durability["current"] == 99.44


def test_plan_uses_pre_event_clock_when_legacy_time_omits_from() -> None:
    base = _events()
    equipped = replay(
        "time_legacy",
        [
            *base,
            build_item_equipped_event(
                actor_id="player",
                world_time=0,
                character_id="player",
                item_id="coat_1",
                slot_ids=("torso",),
            ),
        ],
        len(base) + 1,
    )
    legacy_advance = Event(
        "clock_legacy",
        "time.advanced",
        "system",
        480,
        {"to": 480, "minutes": 480, "reason": "legacy_wait"},
    )

    plan = materialize_clothing_wear_event_plan(equipped, [legacy_advance])

    assert [event.event_type for event in plan] == [
        "time.advanced",
        "item.wear_applied",
    ]
    assert plan[1].payload["sourceEventId"] == "clock_legacy"
