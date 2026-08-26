from __future__ import annotations

from copy import deepcopy

import pytest

from trpg_server.core.projection import replay
from trpg_server.core.state import Event
from trpg_server.items.commands import build_item_created_event
from trpg_server.items.models import ItemInstance
from trpg_server.items.wear import resolve_behavior_wear, resolve_clothing_daily_wear, resolve_repair
from trpg_server.items.wear_events import (
    WearEventError,
    build_clothing_wear_event,
    build_item_repair_attempt_event,
    build_item_repaired_event,
    build_item_wear_event,
)


def _base(*, category: str = "tool", item_id: str = "knife_1", durability=None) -> list[Event]:
    location = Event("loc", "location.created", "system", 0, {"locationId": "room", "name": "房间", "exits": []})
    container = Event("bag", "container.created", "system", 0, {"containerId": "bag", "kind": "inventory", "ownerCharacterId": "player"})
    item = ItemInstance(
        item_id=item_id,
        definition_id=item_id + "_def",
        name="测试物品",
        description="金属结构，表面有可观察的坚硬刃口。",
        category=category,
        is_plot_item=False,
        quantity=1,
        stackable=False,
        unit_weight_grams=100,
        value_crown=10,
        condition="new",
        durability=durability or {"current": 100.0, "max": 100.0},
        container_id="bag",
        location_id=None,
        properties={"equipment": {"mode": "held", "slotIds": ["left_hand"], "handCount": 1}} if category == "tool" else {"equipment": {"mode": "worn", "slotIds": ["torso"], "handCount": 0}},
    )
    return [location, container, build_item_created_event(actor_id="system", world_time=0, item=item)]


def _source() -> Event:
    return Event("action_1", "test.action", "player", 1, {"kind": "confirmed"})


def _material_event() -> Event:
    material = ItemInstance(
        item_id="cloth_1",
        definition_id="cloth_def",
        name="维修布料",
        description="一块可见的布料。",
        category="material",
        is_plot_item=False,
        quantity=1,
        stackable=False,
        unit_weight_grams=20,
        value_crown=1,
        condition="good",
        durability=None,
        container_id="bag",
        location_id=None,
        properties={},
    )
    return build_item_created_event(actor_id="player", world_time=0, item=material)


def test_behavior_wear_event_replays_and_updates_only_current() -> None:
    events = _base()
    source = _source()
    resolved = resolve_behavior_wear(current=100, maximum=100, wear_band="moderate", estimated_loss_ratio=.025, roll=12, modifier=0, dc=11)
    wear = build_item_wear_event(
        actor_id="player", world_time=1, item_id="knife_1", source_event_id=source.event_id,
        trigger="forceful_tool_use", resolution=resolved, ability_id="general_item_handling",
        level="working", source_status="canon", physical_basis=["金属结构"],
    )
    state = replay("wear", [*events, source, wear], len(events) + 2)
    assert state.items["knife_1"].durability == {"current": 97.5, "max": 100.0}
    assert state.item_wear_records[wear.event_id]["sourceEventId"] == source.event_id


def test_wear_event_rejects_forged_current_and_duplicate() -> None:
    events = _base()
    source = _source()
    resolved = resolve_behavior_wear(current=100, maximum=100, wear_band="light", estimated_loss_ratio=.01, roll=12, modifier=0, dc=11)
    wear = build_item_wear_event(actor_id="player", world_time=1, item_id="knife_1", source_event_id=source.event_id, trigger="forceful_tool_use", resolution=resolved, ability_id="general_item_handling", level="working", source_status="canon", physical_basis=["金属结构"])
    forged_payload = deepcopy(wear.payload)
    forged_payload["current"] = 1.0
    forged = Event(wear.event_id, wear.event_type, wear.actor_id, wear.world_time, forged_payload, wear.schema_version)
    with pytest.raises(ValueError, match="does not match"):
        replay("forged", [*events, source, forged], len(events) + 2)
    state = replay("dup", [*events, source, wear], len(events) + 2)
    with pytest.raises(ValueError, match="already"):
        replay("dup", [*events, source, wear, wear], len(events) + 3)


def test_clothing_daily_event_requires_time_source() -> None:
    events = _base(category="clothing", item_id="coat_1", durability={"current": 100.0, "max": 100.0})
    source = Event("time_1", "time.advanced", "system", 480, {"from": 0, "to": 480, "minutes": 480, "reason": "wear_test"})
    resolved = resolve_clothing_daily_wear(current=100, maximum=100, worn_hours=8)
    wear = build_clothing_wear_event(actor_id="system", world_time=480, item_id="coat_1", source_event_id=source.event_id, resolution=resolved)
    state = replay("clothing", [*events, source, wear], len(events) + 2)
    assert state.items["coat_1"].durability["current"] == pytest.approx(99.44, abs=.01)
    bad_source = _source()
    bad_wear = build_clothing_wear_event(actor_id="system", world_time=1, item_id="coat_1", source_event_id=bad_source.event_id, resolution=resolved)
    with pytest.raises(ValueError, match="time.advanced"):
        replay("bad_clothing", [*events, bad_source, bad_wear], len(events) + 2)


def test_clothing_daily_event_requires_clothing_kind_and_trigger() -> None:
    source = Event("time_1", "time.advanced", "system", 480, {"from": 0, "to": 480, "minutes": 480, "reason": "wear_test"})
    resolved = resolve_clothing_daily_wear(current=100, maximum=100, worn_hours=8)

    # The calendar formula must never be applicable to a tool, even when a
    # caller supplies an otherwise complete clothing-wear payload.
    tool_events = _base(category="tool", item_id="knife_1")
    forged_tool_event = build_clothing_wear_event(
        actor_id="system", world_time=480, item_id="knife_1",
        source_event_id=source.event_id, resolution=resolved,
    )
    with pytest.raises(ValueError, match="clothing item"):
        replay("daily_tool", [*tool_events, source, forged_tool_event], len(tool_events) + 2)

    # The event discriminator is part of the audit contract, not an
    # informational label that can be changed independently of the mode.
    clothing_events = _base(category="clothing", item_id="coat_1")
    valid_event = build_clothing_wear_event(
        actor_id="system", world_time=480, item_id="coat_1",
        source_event_id=source.event_id, resolution=resolved,
    )
    forged_payload = deepcopy(valid_event.payload)
    forged_payload["trigger"] = "forceful_tool_use"
    forged_trigger = Event(
        valid_event.event_id, valid_event.event_type, valid_event.actor_id,
        valid_event.world_time, forged_payload, valid_event.schema_version,
    )
    with pytest.raises(ValueError, match="clothing_daily trigger"):
        replay("daily_trigger", [*clothing_events, source, forged_trigger], len(clothing_events) + 2)


def test_repair_success_and_failed_attempt_replay() -> None:
    events = _base(durability={"current": 40.0, "max": 100.0})
    source = build_item_repair_attempt_event(
        actor_id="player", world_time=2, item_id="knife_1", attempt_id="attempt_1", repair_level="standard",
        check={"status": "succeeded", "abilityId": "mechanical_repair", "level": "competent", "sourceStatus": "canon", "difficultyBand": "routine", "dc": 11, "modifier": 2, "roll": 12, "total": 14, "margin": 3},
        material_item_ids=["cloth_1"], physical_basis=["可见裂口"],
    )
    resolved = resolve_repair(current=40, maximum=100, repair_level="standard", roll=12, modifier=2, dc=11)
    repaired = build_item_repaired_event(actor_id="player", world_time=2, item_id="knife_1", source_event_id=source.event_id, resolution=resolved, ability_id="mechanical_repair", level="competent", source_status="canon", material_item_ids=["cloth_1"], physical_basis=["可见裂口"])
    state = replay("repair", [*events, _material_event(), source, repaired], len(events) + 3)
    assert state.items["knife_1"].durability == {"current": 65.0, "max": 100.0}
    failed_source = build_item_repair_attempt_event(
        actor_id="player", world_time=3, item_id="knife_1", attempt_id="attempt_2", repair_level="patch",
        check={"status": "failed", "abilityId": "mechanical_repair", "level": "competent", "sourceStatus": "canon", "difficultyBand": "routine", "dc": 11, "modifier": 2, "roll": 1, "total": 3, "margin": -8},
        material_item_ids=["cloth_1"], physical_basis=["可见裂口"],
    )
    failed_state = replay("failed_repair", [*events, _material_event(), source, repaired, failed_source], len(events) + 4)
    assert failed_state.items["knife_1"].durability == {"current": 65.0, "max": 100.0}


def test_repair_events_must_match_source_target_materials_and_actor() -> None:
    events = _base(durability={"current": 40.0, "max": 100.0})
    events.append(_material_event())
    source = build_item_repair_attempt_event(
        actor_id="player",
        world_time=2,
        item_id="knife_1",
        attempt_id="attempt_match",
        repair_level="standard",
        check={
            "status": "succeeded",
            "abilityId": "mechanical_repair",
            "level": "competent",
            "sourceStatus": "canon",
            "difficultyBand": "routine",
            "dc": 11,
            "modifier": 2,
            "roll": 12,
            "total": 14,
            "margin": 3,
        },
        material_item_ids=["cloth_1"],
        physical_basis=["可见裂口"],
    )
    resolved = resolve_repair(
        current=40,
        maximum=100,
        repair_level="standard",
        roll=12,
        modifier=2,
        dc=11,
    )
    repaired = build_item_repaired_event(
        actor_id="player",
        world_time=2,
        item_id="knife_1",
        source_event_id=source.event_id,
        resolution=resolved,
        ability_id="mechanical_repair",
        level="competent",
        source_status="canon",
        material_item_ids=["cloth_1"],
        physical_basis=["可见裂口"],
    )

    forged_payload = deepcopy(repaired.payload)
    forged_payload["materialItemIds"] = []
    forged = Event(
        repaired.event_id,
        repaired.event_type,
        repaired.actor_id,
        repaired.world_time,
        forged_payload,
        repaired.schema_version,
    )
    with pytest.raises(ValueError, match="materials and tools"):
        replay("mismatched_repair", [*events, source, forged], len(events) + 2)

    with pytest.raises(ValueError, match="unknown item"):
        replay(
            "unknown_material",
            [
                *_base(durability={"current": 40.0, "max": 100.0}),
                build_item_repair_attempt_event(
                    actor_id="player",
                    world_time=2,
                    item_id="knife_1",
                    attempt_id="attempt_unknown_material",
                    repair_level="patch",
                    check={
                        "status": "failed",
                        "abilityId": "mechanical_repair",
                        "level": "competent",
                        "sourceStatus": "canon",
                        "difficultyBand": "routine",
                        "dc": 11,
                        "modifier": 2,
                        "roll": 1,
                        "total": 3,
                        "margin": -8,
                    },
                    material_item_ids=["not_an_instance"],
                ),
            ],
            4,
        )


def test_repair_tool_wear_requires_tool_source_membership() -> None:
    events = _base(durability={"current": 40.0, "max": 100.0})
    events.append(_material_event())
    source = build_item_repair_attempt_event(
        actor_id="player",
        world_time=2,
        item_id="knife_1",
        attempt_id="attempt_tool_source",
        repair_level="patch",
        check={
            "status": "failed",
            "abilityId": "mechanical_repair",
            "level": "competent",
            "sourceStatus": "canon",
            "difficultyBand": "routine",
            "dc": 11,
            "modifier": 2,
            "roll": 1,
            "total": 3,
            "margin": -8,
        },
        material_item_ids=["cloth_1"],
        tool_item_ids=[],
    )
    resolution = resolve_behavior_wear(
        current=100,
        maximum=100,
        wear_band="light",
        estimated_loss_ratio=0.01,
        roll=1,
        modifier=2,
        dc=11,
    )
    wear = build_item_wear_event(
        actor_id="player",
        world_time=2,
        item_id="knife_1",
        source_event_id=source.event_id,
        trigger="repair_tool_use",
        resolution=resolution,
        ability_id="mechanical_repair",
        level="competent",
        source_status="canon",
        physical_basis=["维修接触"],
    )
    with pytest.raises(ValueError, match="not listed"):
        replay("forged_tool_wear", [*events, source, wear], len(events) + 2)
