from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from trpg_server.core.projection import replay
from trpg_server.items.inventory import item_is_at_location, item_is_owned_by
from trpg_server.locations.furniture import load_furniture_atlas
from trpg_server.story.scenario import compile_initial_events, load_scenario_package


CAMPAIGN = (
    Path(__file__).resolve().parents[3]
    / "content"
    / "campaigns"
    / "gray-harbor"
)


def test_gray_harbor_character_item_location_bootstrap_is_consistent() -> None:
    events = compile_initial_events(
        load_scenario_package(CAMPAIGN),
        "cmp_three_module_integration",
    )
    state = replay("cmp_three_module_integration", events, len(events))
    character_events = [event for event in events if event.event_type == "character.created"]
    container_events = [event for event in events if event.event_type == "container.created"]
    furniture_atlas = load_furniture_atlas(CAMPAIGN / "furniture-atlas.json")

    assert len(character_events) == 142
    assert len(state.character_profiles) == 142
    assert len(state.character_locations) == 142
    assert len(state.locations) == 615
    assert not any(location.kind == "district" for location in state.locations.values())
    assert {location.location_id for location in state.locations.values() if location.kind == "city"} == {
        "gray_harbor"
    }
    assert len([value for value in state.containers.values() if value.kind != "furniture"]) == 148
    assert len(state.containers) == 148 + len(furniture_atlas.records)
    assert len(state.items) == 6
    assert "catalog_p011" not in state.character_profiles
    assert state.character_profiles["harvey_cole"]["catalogCharacterId"] == "P011"
    assert state.character_profiles["catalog_p005"]["catalogCharacterId"] == "P005"
    assert max(events.index(event) for event in container_events) < min(
        events.index(event) for event in character_events
    )

    reachable: set[str] = set()
    queue = deque(["white_heron_ground_floor"])
    while queue:
        location_id = queue.popleft()
        if location_id in reachable:
            continue
        reachable.add(location_id)
        queue.extend(
            exit_state.to_location_id
            for exit_state in state.locations[location_id].exits
            if exit_state.visible
            and not exit_state.locked
            and exit_state.to_location_id not in reachable
        )
    public_playable_locations = {
        location.location_id
        for location in state.locations.values()
        if location.map_visibility != "gm" and location.kind != "city"
    }
    assert public_playable_locations <= reachable
    assert {
        location.location_id
        for location in state.locations.values()
        if location.map_visibility == "gm"
    } == {
        "atlas_room_loc_5_1_8__5",
        "atlas_room_loc_5_2_3__5",
        "atlas_room_loc_5_7_12__4",
        "atlas_room_loc_5_7_12__5",
    }

    for character_id, profile in state.character_profiles.items():
        location_id = state.character_locations[character_id]
        assert location_id in state.locations
        inventory_id = profile["inventoryContainerId"]
        inventory = state.containers[inventory_id]
        assert inventory.kind == "inventory"
        assert inventory.owner_character_id == character_id

    for container in state.containers.values():
        assert (container.owner_character_id is None) != (container.location_id is None)
        if container.owner_character_id is not None:
            assert container.owner_character_id in state.character_profiles
        if container.location_id is not None:
            assert container.location_id in state.locations

    for item in state.items.values():
        assert (item.container_id is None) != (item.location_id is None)
        if item.container_id is not None:
            assert item.container_id in state.containers
        if item.location_id is not None:
            assert item.location_id in state.locations

    knife = state.items["protagonist_small_knife"]
    jewelry_box = state.items["protagonist_cheap_jewelry_box"]
    assert item_is_owned_by(state, knife, "protagonist")
    assert not item_is_owned_by(state, jewelry_box, "protagonist")
    assert item_is_at_location(state, jewelry_box, "white_heron_third_floor")
    assert not item_is_at_location(state, jewelry_box, "white_heron_ground_floor")


def test_character_atlas_runtime_ids_match_bootstrap_character_ids() -> None:
    profiles_document = json.loads(
        (CAMPAIGN / "characters-atlas" / "character-profiles.json").read_text(
            encoding="utf-8"
        )
    )
    atlas_runtime_ids = {
        profile["runtimeCharacterId"]
        for profile in profiles_document["characters"]
    }
    events = compile_initial_events(
        load_scenario_package(CAMPAIGN),
        "cmp_three_module_identity",
    )
    bootstrap_ids = {
        event.payload["characterId"]
        for event in events
        if event.event_type == "character.created"
    }

    assert atlas_runtime_ids == bootstrap_ids
