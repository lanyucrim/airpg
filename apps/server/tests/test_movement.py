from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from trpg_server.core.state import Event
from trpg_server.behavior.router import interpret_player_text, resolve
from trpg_server.locations.movement import evaluate_movement
from trpg_server.core.projection import apply_event, public_state, replay
from trpg_server.story.scenario import compile_initial_events, load_scenario_package
from trpg_server.core.schemas import TurnRequest
from trpg_server.core.service import GameService


PACKAGE_PATH = Path(__file__).resolve().parents[3] / "content" / "campaigns" / "gray-harbor"


def opening_state():
    package = load_scenario_package(PACKAGE_PATH)
    events = compile_initial_events(package, "cmp_movement")
    return events, replay("cmp_movement", events, 1)


def test_direct_visible_exit_moves_character_and_scene_once() -> None:
    _, state = opening_state()
    command = interpret_player_text("我去厨房。", actor_id="protagonist", state=state)
    result = resolve(state, command)
    updated = deepcopy(state)
    for event in result.events:
        apply_event(updated, event)

    assert command.action_type == "move"
    assert command.target_id == "white_heron_kitchen"
    assert result.status == "committed"
    assert result.outcome == "moved"
    assert [event.event_type for event in result.events] == [
        "time.advanced",
        "character.moved",
        "scene.location_changed",
        "scene.beat_advanced",
    ]
    assert [event.world_time for event in result.events] == [1, 1, 1, 1]
    assert state.character_locations["protagonist"] == "white_heron_ground_floor"
    assert updated.character_locations["protagonist"] == "white_heron_kitchen"
    assert updated.location_id == "white_heron_kitchen"
    assert updated.world_time == 1
    assert updated.scene_beat == 1


def test_known_but_non_adjacent_destination_is_not_teleportation() -> None:
    _, state = opening_state()
    command = interpret_player_text("我直接去地窖。", actor_id="protagonist", state=state)
    result = resolve(state, command)

    assert command.action_type == "move"
    assert result.status == "rejected"
    assert result.outcome == "destination_not_reachable"
    assert result.events == []
    assert state.character_locations["protagonist"] == "white_heron_ground_floor"


def test_hidden_exit_cannot_be_used_before_it_is_available() -> None:
    _, state = opening_state()
    # Put the actor in the cellar without changing the exit's undiscovered state.
    state.character_locations["protagonist"] = "white_heron_cellar"
    state.location_id = "white_heron_cellar"

    command = interpret_player_text(
        "我去废弃面包房。",
        actor_id="protagonist",
        state=state,
    )
    result = resolve(state, command)

    assert result.status == "rejected"
    assert result.outcome == "destination_not_reachable"
    assert result.events == []


def test_unknown_location_reference_does_not_change_world() -> None:
    _, state = opening_state()
    command = interpret_player_text("我去王宫。", actor_id="protagonist", state=state)
    result = resolve(state, command)

    assert command.action_type == "unresolved_reference"
    assert result.status == "rejected"
    assert result.outcome == "missing_reference"
    assert result.events == []


def test_successful_move_is_idempotent_through_service(tmp_path: Path) -> None:
    package = load_scenario_package(PACKAGE_PATH)
    campaign_id = "cmp_movement_service"
    game = GameService(tmp_path / "movement.sqlite3")
    game.store.initialize()
    game.store.reset_campaign(
        campaign_id,
        package.manifest.name,
        compile_initial_events(package, campaign_id),
        scenario_id=package.manifest.scenario_id,
        scenario_version=package.manifest.version,
        scenario_content_hash=package.content_hash,
    )
    request = TurnRequest(
        idempotency_key="move-to-kitchen-once",
        expected_state_version=1,
        actor_id="protagonist",
        text="我去厨房。",
    )

    first = game.submit_turn(campaign_id, request)
    second = game.submit_turn(campaign_id, request)

    assert first["outcome"] == "moved"
    assert second["replayed"] is True
    assert second["turn_id"] == first["turn_id"]
    assert game.get_state(campaign_id)["stateVersion"] == 2
    assert game.get_state(campaign_id)["scene"]["locationId"] == "white_heron_kitchen"


def test_public_state_never_lists_hidden_tunnel_exit() -> None:
    events, _ = opening_state()
    state = replay("cmp_movement", events, 1)
    state.character_locations["protagonist"] = "white_heron_cellar"
    state.location_id = "white_heron_cellar"

    exits = public_state(state)["scene"]["exits"]
    assert {exit_state["toLocationId"] for exit_state in exits} == {
        "white_heron_kitchen",
        "white_heron_third_floor",
        "white_heron_backyard",
    }


def test_public_map_exposes_hierarchy_and_only_present_npcs() -> None:
    events, state = opening_state()

    initial = public_state(state)
    map_state = initial["map"]
    assert map_state["currentLocationId"] == "white_heron_ground_floor"
    assert map_state["displayLocationId"] == "white_heron_house"
    location_ids = {value["locationId"] for value in map_state["locations"]}
    assert "gray_harbor" in location_ids
    assert "white_heron_house" in location_ids
    assert "white_heron_ground_floor" not in location_ids
    current = next(
        value
        for value in map_state["locations"]
        if value["locationId"] == "white_heron_house"
    )
    assert {value["name"] for value in current["visibleCharacters"]} >= {
        "玛莎·贝尔",
        "哈维·科尔",
    }

    state.character_locations["protagonist"] = "white_heron_cellar"
    state.location_id = "white_heron_cellar"
    cellar_map = public_state(state)["map"]
    assert cellar_map["displayLocationId"] == "white_heron_house"
    assert "white_heron_cellar" not in {
        value["locationId"] for value in cellar_map["locations"]
    }


def test_public_map_collapses_internal_room_to_parent_place() -> None:
    _, state = opening_state()
    state.character_locations["protagonist"] = "white_heron_kitchen"
    state.location_id = "white_heron_kitchen"

    map_state = public_state(state)["map"]

    assert map_state["currentLocationId"] == "white_heron_kitchen"
    assert map_state["displayLocationId"] == "white_heron_house"
    assert map_state["displayLocationName"] == "白鹭屋"
    assert all(
        location["locationId"] not in {"white_heron_ground_floor", "white_heron_kitchen"}
        for location in map_state["locations"]
    )
    assert next(
        location for location in map_state["locations"]
        if location["locationId"] == "white_heron_house"
    )["isCurrent"] is True


def test_public_map_never_exposes_gm_only_location() -> None:
    _, state = opening_state()
    state.locations["abandoned_bakery"].map_visibility = "gm"

    location_ids = {
        value["locationId"] for value in public_state(state)["map"]["locations"]
    }

    assert "abandoned_bakery" not in location_ids


def test_search_before_authored_trigger_does_not_reveal_tunnel() -> None:
    _, state = opening_state()
    state.character_locations["protagonist"] = "white_heron_cellar"
    state.location_id = "white_heron_cellar"

    command = interpret_player_text(
        "我仔细检查地窖和墙壁。",
        actor_id="protagonist",
        state=state,
    )
    result = resolve(state, command)
    updated = deepcopy(state)
    for event in result.events:
        apply_event(updated, event)

    assert command.action_type == "investigate_location"
    assert result.status == "committed"
    assert result.outcome == "nothing_new_found"
    assert "cellar_tunnel_to_bakery" not in updated.discovered_exits.get(
        "protagonist",
        set(),
    )
    assert {value["toLocationId"] for value in public_state(updated)["scene"]["exits"]} == {
        "white_heron_kitchen",
        "white_heron_third_floor",
        "white_heron_backyard",
    }


def test_resource_question_is_not_routed_to_colocated_npc_inquiry() -> None:
    _, state = opening_state()
    state.character_locations["protagonist"] = "white_heron_kitchen"
    state.location_id = "white_heron_kitchen"

    command = interpret_player_text(
        "厨房里有没有能吃的？",
        actor_id="protagonist",
        state=state,
    )

    assert command.action_type == "search_location"
    assert command.parameters["searchKind"] == "food"


def test_authored_trigger_allows_tunnel_discovery_and_movement() -> None:
    _, state = opening_state()
    state.character_locations["protagonist"] = "white_heron_cellar"
    state.location_id = "white_heron_cellar"
    apply_event(state, Event(
        "evt_test_flood",
        "story.condition_activated",
        "system",
        state.world_time,
        {"conditionId": "cellar_tunnel_entrance_exposed"},
    ))

    discovery = resolve(
        state,
        interpret_player_text(
            "我检查积水后的排水口。",
            actor_id="protagonist",
            state=state,
        ),
    )
    updated = deepcopy(state)
    for event in discovery.events:
        apply_event(updated, event)

    assert discovery.outcome == "location_feature_discovered"
    assert "cellar_tunnel_exists" in updated.knowledge["protagonist"]
    assert "cellar_tunnel_to_bakery" in updated.discovered_exits["protagonist"]
    assert "bakery_tunnel_to_cellar" in updated.discovered_exits["protagonist"]
    assert {value["toLocationId"] for value in public_state(updated)["scene"]["exits"]} == {
        "white_heron_kitchen",
        "white_heron_third_floor",
        "white_heron_backyard",
        "abandoned_bakery",
    }

    movement = resolve(
        updated,
        interpret_player_text(
            "我穿过排水通道去废弃面包房。",
            actor_id="protagonist",
            state=updated,
        ),
    )
    assert movement.outcome == "moved"


def test_repeating_discovery_with_new_command_creates_no_duplicate_events() -> None:
    _, state = opening_state()
    state.character_locations["protagonist"] = "white_heron_cellar"
    state.location_id = "white_heron_cellar"
    apply_event(state, Event(
        "evt_test_known_exit",
        "location.exit_discovered",
        "protagonist",
        0,
        {
            "characterId": "protagonist",
            "exitId": "cellar_tunnel_to_bakery",
            "discoveryId": "cellar_drainage_tunnel",
            "sourceEventId": "evt_test_source",
        },
    ))
    apply_event(state, Event(
        "evt_test_known_reverse_exit",
        "location.exit_discovered",
        "protagonist",
        0,
        {
            "characterId": "protagonist",
            "exitId": "bakery_tunnel_to_cellar",
            "discoveryId": "cellar_drainage_tunnel",
            "sourceEventId": "evt_test_source",
        },
    ))

    result = resolve(
        state,
        interpret_player_text(
            "我再次检查排水通道。",
            actor_id="protagonist",
            state=state,
        ),
    )

    assert result.status == "rejected"
    assert result.outcome == "already_discovered"
    assert result.events == []


def test_structure_exit_is_open_even_when_legacy_event_recorded_a_lock() -> None:
    _, state = opening_state()
    location = state.locations["white_heron_ground_floor"]
    kitchen_exit = next(
        value for value in location.exits
        if value.to_location_id == "white_heron_kitchen"
    )
    other_exits = tuple(
        value for value in location.exits if value is not kitchen_exit
    )

    location.exits = other_exits + (replace(
        kitchen_exit,
        locked=True,
        key_item_ids=("missing_cellar_key",),
    ),)
    allowed = evaluate_movement(state, "protagonist", "white_heron_kitchen")
    assert allowed.allowed is True


def test_visible_exit_can_be_blocked_by_inactive_story_condition() -> None:
    _, state = opening_state()
    location = state.locations["white_heron_ground_floor"]
    kitchen_exit = next(
        value for value in location.exits
        if value.to_location_id == "white_heron_kitchen"
    )
    location.exits = tuple(
        replace(
            value,
            required_condition_ids=("cellar_tunnel_entrance_exposed",),
        )
        if value is kitchen_exit
        else value
        for value in location.exits
    )

    blocked = evaluate_movement(state, "protagonist", "white_heron_kitchen")
    assert blocked.outcome == "exit_blocked"

    apply_event(state, Event(
        "evt_test_condition_ready",
        "story.condition_activated",
        "system",
        0,
        {"conditionId": "cellar_tunnel_entrance_exposed"},
    ))
    assert evaluate_movement(
        state,
        "protagonist",
        "white_heron_kitchen",
    ).allowed is True


def test_successful_discovery_is_idempotent_through_service(tmp_path: Path) -> None:
    package = load_scenario_package(PACKAGE_PATH)
    campaign_id = "cmp_discovery_service"
    events = compile_initial_events(package, campaign_id)
    events.extend([
        Event(
            "evt_discovery_test_move",
            "character.moved",
            "system",
            0,
            {
                "characterId": "protagonist",
                "fromLocationId": "white_heron_ground_floor",
                "toLocationId": "white_heron_cellar",
                "travelMinutes": 0,
            },
        ),
        Event(
            "evt_discovery_test_scene",
            "scene.location_changed",
            "system",
            0,
            {
                "fromLocationId": "white_heron_ground_floor",
                "toLocationId": "white_heron_cellar",
                "movementEventId": "evt_discovery_test_move",
            },
        ),
        Event(
            "evt_discovery_test_trigger",
            "story.condition_activated",
            "system",
            0,
            {"conditionId": "cellar_tunnel_entrance_exposed"},
        ),
    ])
    game = GameService(tmp_path / "discovery.sqlite3")
    game.store.initialize()
    game.store.reset_campaign(
        campaign_id,
        package.manifest.name,
        events,
        scenario_id=package.manifest.scenario_id,
        scenario_version=package.manifest.version,
        scenario_content_hash=package.content_hash,
    )
    request = TurnRequest(
        idempotency_key="discover-cellar-tunnel-once",
        expected_state_version=1,
        actor_id="protagonist",
        text="我检查积水冲开的排水口。",
    )

    first = game.submit_turn(campaign_id, request)
    second = game.submit_turn(campaign_id, request)

    assert first["outcome"] == "location_feature_discovered"
    assert second["replayed"] is True
    assert second["turn_id"] == first["turn_id"]
    assert game.get_state(campaign_id)["stateVersion"] == 2


def test_movement_preconditions_have_distinct_explainable_failures() -> None:
    _, state = opening_state()

    assert evaluate_movement(
        state,
        "missing_actor",
        "white_heron_kitchen",
    ).outcome == "actor_location_unknown"
    assert evaluate_movement(
        state,
        "protagonist",
        "missing_place",
    ).outcome == "unknown_destination"
    assert evaluate_movement(
        state,
        "protagonist",
        "white_heron_ground_floor",
    ).outcome == "already_there"

    state.locations.pop("white_heron_ground_floor")
    assert evaluate_movement(
        state,
        "protagonist",
        "white_heron_kitchen",
    ).outcome == "actor_location_unknown"
