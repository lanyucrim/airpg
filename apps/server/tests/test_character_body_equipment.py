from __future__ import annotations

import pytest

from trpg_server.behavior.router import interpret_player_text, resolve
from trpg_server.characters.body import BODY_PARTS
from trpg_server.characters.equipment import (
    build_external_injury_applied_event,
    build_external_injury_cleared_event,
    validate_equipment_binding,
)
from trpg_server.core.projection import public_state, replay
from trpg_server.core.state import Event, ParsedCommand
from trpg_server.items.commands import build_item_created_event, build_item_transferred_event
from trpg_server.items.models import ItemContainer, ItemInstance


def _initial_events() -> list[Event]:
    item = ItemInstance(
        item_id="knife_1",
        definition_id="small_personal_knife",
        name="小刀",
        description="一把小刀。",
        category="tool",
        is_plot_item=False,
        quantity=1,
        stackable=False,
        unit_weight_grams=None,
        value_crown=None,
        condition="intact",
        durability={"current": 85.0, "max": 100.0},
        container_id="player_pack",
        location_id=None,
        properties={
            "equipment": {
                "mode": "held",
                "slotIds": ["left_hand", "right_hand"],
                "handCount": 1,
            }
        },
    )
    return [
        Event(
            "evt_campaign",
            "campaign.created",
            "system",
            0,
            {"name": "测试战役", "playerCharacterId": "protagonist"},
        ),
        Event(
            "evt_location",
            "location.created",
            "system",
            0,
            {"locationId": "room", "name": "房间", "exits": []},
        ),
        Event(
            "evt_container",
            "container.created",
            "system",
            0,
            {"containerId": "player_pack", "kind": "inventory", "ownerCharacterId": "protagonist"},
        ),
        Event(
            "evt_character",
            "character.created",
            "system",
            0,
            {
                "characterId": "protagonist",
                "characterType": "player",
                "name": "主角",
                "locationId": "room",
            },
        ),
        build_item_created_event(actor_id="system", world_time=0, item=item),
    ]


def test_body_has_explicit_left_right_slots_for_limbs_hands_and_feet() -> None:
    assert BODY_PARTS == (
        "head",
        "torso",
        "left_arm",
        "right_arm",
        "left_leg",
        "right_leg",
        "left_hand",
        "right_hand",
        "left_foot",
        "right_foot",
    )


def test_equip_is_an_event_and_duplicate_equip_is_rejected() -> None:
    events = _initial_events()
    state = replay("cmp_body", events, len(events))

    first = resolve(
        state,
        interpret_player_text("装备小刀", actor_id="protagonist", state=state),
    )
    assert first.status == "committed"
    assert first.outcome == "item_equipped"
    assert first.events[0].event_type == "character.item_equipped"

    equipped = replay("cmp_body", [*events, *first.events], len(events) + len(first.events))
    assert set(equipped.character_equipment["protagonist"]) == {"held:left_hand"}
    assert public_state(equipped)["player"]["equipment"][0]["itemId"] == "knife_1"

    repeated = resolve(
        equipped,
        interpret_player_text("装备小刀", actor_id="protagonist", state=equipped),
    )
    assert repeated.status == "rejected"
    assert repeated.outcome == "item_already_equipped"


def test_worn_gloves_and_a_held_item_use_independent_hand_layers() -> None:
    events = _initial_events()
    gloves = ItemInstance(
        item_id="gloves_1",
        definition_id="work_gloves",
        name="工作手套",
        description="一副工作手套。",
        category="clothing",
        is_plot_item=False,
        quantity=1,
        stackable=False,
        unit_weight_grams=None,
        value_crown=None,
        condition="intact",
        durability={"current": 45.0, "max": 60.0},
        container_id="player_pack",
        location_id=None,
        properties={
            "equipment": {
                "mode": "worn",
                "slotIds": ["left_hand", "right_hand"],
                "handCount": 0,
            }
        },
    )
    events.append(build_item_created_event(actor_id="system", world_time=0, item=gloves))
    state = replay("cmp_gloves", events, len(events))
    wear = resolve(
        state,
        interpret_player_text("穿上工作手套", actor_id="protagonist", state=state),
    ).events[0]
    wearing = replay("cmp_gloves", [*events, wear], len(events) + 1)
    hold = resolve(
        wearing,
        interpret_player_text("装备小刀", actor_id="protagonist", state=wearing),
    )
    assert hold.status == "committed"
    final = replay("cmp_gloves", [*events, wear, *hold.events], len(events) + 2)
    assert set(final.character_equipment["protagonist"]) == {
        "worn:left_hand",
        "worn:right_hand",
        "held:left_hand",
    }


def test_hand_injury_blocks_holding_and_clear_restores_it() -> None:
    events = _initial_events()
    state = replay("cmp_body_injury", events, len(events))
    injury = build_external_injury_applied_event(
        actor_id="system",
        world_time=1,
        character_id="protagonist",
        injury_id="injury_left_hand",
        body_part="left_hand",
        severity="moderate",
    )
    injured = replay("cmp_body_injury", [*events, injury], len(events) + 1)
    blocked = resolve(
        injured,
        ParsedCommand(
            action_type="equip_item",
            actor_id="protagonist",
            target_id="knife_1",
            parameters={"itemId": "knife_1", "slotId": "left_hand"},
            original_text="把小刀装备到左手",
            authority="player",
        ),
    )
    assert blocked.status == "rejected"
    assert blocked.outcome == "body_part_unavailable"

    cleared = build_external_injury_cleared_event(
        actor_id="system",
        world_time=2,
        character_id="protagonist",
        injury_id="injury_left_hand",
    )
    healed = replay("cmp_body_injury", [*events, injury, cleared], len(events) + 2)
    restored = resolve(
        healed,
        interpret_player_text("装备小刀", actor_id="protagonist", state=healed),
    )
    assert restored.status == "committed"
    assert public_state(injured)["player"]["externalInjuries"][0]["bodyPart"] == "left_hand"


def test_both_injured_hands_reject_taking_an_item() -> None:
    events = _initial_events()
    state = replay("cmp_both_hands", events, len(events))
    state.items["knife_1"].container_id = None
    state.items["knife_1"].location_id = "room"
    injuries = [
        build_external_injury_applied_event(
            actor_id="system",
            world_time=index + 1,
            character_id="protagonist",
            injury_id=f"injury_hand_{side}",
            body_part=f"{side}_hand",
            severity="severe",
        )
        for index, side in enumerate(("left", "right"))
    ]
    injured = replay("cmp_both_hands", [*events, *injuries], len(events) + 2)
    result = resolve(
        injured,
        interpret_player_text("拿走小刀", actor_id="protagonist", state=injured),
    )
    assert result.status == "rejected"
    assert result.outcome == "hands_unavailable"


def test_equipped_item_must_be_unequipped_before_transfer() -> None:
    events = _initial_events()
    state = replay("cmp_body_transfer", events, len(events))
    equip = resolve(
        state,
        interpret_player_text("装备小刀", actor_id="protagonist", state=state),
    ).events[0]
    transfer = build_item_transferred_event(
        actor_id="protagonist",
        world_time=1,
        item_id="knife_1",
        to_location_id="room",
    )
    with pytest.raises(ValueError, match="equipped item"):
        replay("cmp_body_transfer", [*events, equip, transfer], len(events) + 2)


def test_unequip_is_a_replayable_player_command() -> None:
    events = _initial_events()
    state = replay("cmp_body_unequip", events, len(events))
    equip = resolve(
        state,
        interpret_player_text("装备小刀", actor_id="protagonist", state=state),
    ).events[0]
    equipped = replay("cmp_body_unequip", [*events, equip], len(events) + 1)
    command = interpret_player_text("卸下小刀", actor_id="protagonist", state=equipped)
    assert command.action_type == "unequip_item"
    result = resolve(equipped, command)
    assert result.status == "committed"
    assert result.outcome == "item_unequipped"
    final = replay(
        "cmp_body_unequip",
        [*events, equip, *result.events],
        len(events) + 2,
    )
    assert final.character_equipment["protagonist"] == {}


def test_injury_on_an_equipped_hand_preserves_binding_but_blocks_use() -> None:
    events = _initial_events()
    state = replay("cmp_body_order", events, len(events))
    equip = resolve(
        state,
        interpret_player_text("装备小刀", actor_id="protagonist", state=state),
    ).events[0]
    injury = build_external_injury_applied_event(
        actor_id="system",
        world_time=1,
        character_id="protagonist",
        injury_id="injury_left_hand",
        body_part="left_hand",
        severity="severe",
    )
    injured = replay("cmp_body_order", [*events, equip, injury], len(events) + 2)
    check = validate_equipment_binding(
        injured,
        "protagonist",
        injured.items["knife_1"],
        ("left_hand",),
    )
    assert check.code == "body_part_unavailable"
    assert injured.character_equipment["protagonist"]["held:left_hand"]["itemId"] == "knife_1"


def test_missing_hand_is_persistent_and_cannot_be_cleared_as_a_wound() -> None:
    events = _initial_events()
    missing = build_external_injury_applied_event(
        actor_id="system",
        world_time=1,
        character_id="protagonist",
        injury_id="missing_left_hand",
        body_part="left_hand",
        severity="critical",
        status="missing",
    )
    clear = build_external_injury_cleared_event(
        actor_id="system",
        world_time=2,
        character_id="protagonist",
        injury_id="missing_left_hand",
    )
    with pytest.raises(ValueError, match="missing body part"):
        replay("cmp_missing_hand", [*events, missing, clear], len(events) + 2)
