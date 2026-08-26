from __future__ import annotations

from collections import deque
from dataclasses import replace

from trpg_server.behavior.router import interpret_player_text, resolve
from trpg_server.core.projection import apply_event, public_state, replay
from trpg_server.core.state import ParsedCommand, StoryConditionState
from trpg_server.locations.movement import evaluate_movement
from trpg_server.map.capabilities import build_location_capability_context
from trpg_server.map.atlas import gray_harbor_atlas
from trpg_server.map.occupancy import build_location_contents
from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events


def gray_harbor_state():
    events = gray_harbor_events()
    return replay(GRAY_HARBOR_CAMPAIGN_ID, events, 1)


def _allowed_movement_graph(state):
    graph: dict[str, tuple[str, ...]] = {}
    for location_id, location in state.locations.items():
        state.character_locations["protagonist"] = location_id
        state.location_id = location_id
        graph[location_id] = tuple(
            exit_state.to_location_id
            for exit_state in location.exits
            if evaluate_movement(
                state,
                "protagonist",
                exit_state.to_location_id,
            ).allowed
        )
    return graph


def _reachable(graph: dict[str, tuple[str, ...]], start: str) -> set[str]:
    seen = {start}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for destination in graph.get(current, ()):
            if destination not in seen:
                seen.add(destination)
                queue.append(destination)
    return seen


def test_map_occupancy_derives_characters_location_containers_and_items() -> None:
    state = gray_harbor_state()
    contents = build_location_contents(state)

    ground_floor = contents["white_heron_ground_floor"]
    kitchen = contents["white_heron_kitchen"]

    assert "protagonist" in ground_floor.character_ids
    assert "martha_bell" in ground_floor.character_ids
    assert "protagonist_equipment" in ground_floor.container_ids
    assert "protagonist_small_knife" in ground_floor.carried_item_ids
    assert "iron_hooks_final_notice" in ground_floor.item_ids
    assert "white_heron_kitchen_bread" in kitchen.item_ids


def test_gray_harbor_materializes_street_transit_and_atlas_structure_rooms() -> None:
    state = gray_harbor_state()

    assert state.locations["oak_street"].kind == "street"
    assert not any(
        event.payload.get("atlasStructureId")
        for event in gray_harbor_events()
        if event.event_type == "location.created"
        and event.payload.get("locationId") == "oak_street"
    )
    assert "atlas_housing_organ_court" in state.locations
    assert "atlas_daily_harbor_rope_shop" in state.locations
    assert "atlas_daily_candle_newsstand" in state.locations
    assert "atlas_housing_oak_back" in state.locations
    room = state.locations["atlas_room_loc_5_1_2__1"]
    assert room.parent_id == "catalog_l002"
    assert room.kind == "room"
    assert any(
        exit_state.to_location_id == "atlas_room_loc_5_1_2__2"
        for exit_state in room.exits
    )


def test_gray_harbor_opening_events_drop_legacy_cross_street_shortcuts() -> None:
    events = gray_harbor_events()
    location_events = {
        event.payload["locationId"]: event.payload
        for event in events
        if event.event_type == "location.created"
    }

    assert "oak_street" in location_events
    assert "atlas_street_candle_organ" in location_events
    extension_events = {
        event.payload["locationId"]: event.payload
        for event in events
        if event.event_type == "location.exits_extended"
    }
    bakery_targets = {
        value["toLocationId"]
        for value in extension_events["abandoned_bakery"]["exits"]
    }
    assert "atlas_street_candle_organ" in bakery_targets

    assert "candle_ward" not in location_events
    assert not any(
        location_id.startswith("catalog_district_")
        for location_id in location_events
    )

    ground_targets = {
        value["toLocationId"]
        for value in location_events["white_heron_ground_floor"]["exits"]
    }
    assert "oak_street" in ground_targets
    assert "white_heron_kitchen" in ground_targets

    cellar_tunnel = next(
        value
        for value in location_events["white_heron_cellar"]["exits"]
        if value["id"] == "cellar_tunnel_to_bakery"
    )
    assert cellar_tunnel["visible"] is False
    assert cellar_tunnel["discoveryId"] == "cellar_drainage_tunnel"


def test_second_floor_cannot_exit_directly_and_cross_street_direct_exit_is_blocked() -> None:
    state = gray_harbor_state()
    state.character_locations["protagonist"] = "white_heron_second_floor"
    state.location_id = "white_heron_second_floor"

    upstairs_to_street = evaluate_movement(state, "protagonist", "oak_street")
    assert not upstairs_to_street.allowed
    assert upstairs_to_street.reason_code == "no_visible_direct_exit"

    # Even if a legacy event accidentally adds a direct floor-to-street exit,
    # the topology gate must reject it rather than turning the floor into a
    # teleport point.
    floor = state.locations["white_heron_second_floor"]
    state.locations["white_heron_second_floor"] = type(floor)(
        floor.location_id,
        floor.name,
        floor.aliases,
        floor.kind,
        floor.map_visibility,
        floor.parent_id,
        floor.description,
        (*floor.exits, type(floor.exits[0])(
            "legacy_floor_to_street",
            "oak_street",
            "错误的直达街道出口",
            1,
        )),
    )
    malformed = evaluate_movement(state, "protagonist", "oak_street")
    assert not malformed.allowed
    assert malformed.reason_code == "map_topology_blocked"

    state.character_locations["protagonist"] = "oak_street"
    state.location_id = "oak_street"
    street_to_bakery = evaluate_movement(state, "protagonist", "abandoned_bakery")
    assert not street_to_bakery.allowed
    assert street_to_bakery.reason_code == "no_visible_direct_exit"


def test_street_graph_uses_transit_nodes_without_structures() -> None:
    state = gray_harbor_state()
    street = state.locations["oak_street"]
    assert street.kind == "street"
    assert any(
        value.to_location_id in {"white_heron_house", "white_heron_ground_floor"}
        for value in street.exits
    )
    assert all(
        event.payload.get("atlasStructureId") is None
        for event in gray_harbor_events()
        if event.event_type == "location.created"
        and event.payload.get("locationId") == "oak_street"
    )


def test_street_entry_arrives_at_first_public_structure_and_exits_directly() -> None:
    state = gray_harbor_state()
    state.character_locations["protagonist"] = "oak_street"
    state.location_id = "oak_street"

    command = ParsedCommand(
        action_type="move",
        actor_id="protagonist",
        target_id="catalog_l007",
        parameters={"destinationId": "catalog_l007"},
        original_text="去夜莺歌厅",
        authority="player",
    )
    result = resolve(state, command)

    assert result.status == "committed"
    assert result.outcome == "moved"
    moved = next(event for event in result.events if event.event_type == "character.moved")
    changed = next(
        event for event in result.events if event.event_type == "scene.location_changed"
    )
    assert moved.payload["toLocationId"] == "atlas_room_loc_5_1_7__1"
    assert changed.payload["toLocationId"] == "atlas_room_loc_5_1_7__1"

    for event in result.events:
        apply_event(state, event)
    assert state.character_locations["protagonist"] == "atlas_room_loc_5_1_7__1"
    assert public_state(state)["map"]["currentLocationDisplayName"].endswith("·售票门厅")
    assert any(
        value.to_location_id == "oak_street"
        for value in state.locations["atlas_room_loc_5_1_7__1"].exits
    )


def test_street_entry_does_not_bypass_missing_building_entry_edge() -> None:
    state = gray_harbor_state()
    state.character_locations["protagonist"] = "oak_street"
    state.location_id = "oak_street"
    building = state.locations["catalog_l007"]
    building.exits = tuple(
        value
        for value in building.exits
        if value.to_location_id != "atlas_room_loc_5_1_7__1"
    )

    decision = evaluate_movement(state, "protagonist", "catalog_l007")

    # The street-to-building command remains a valid move, but the runtime
    # must not jump into a room whose authored entrance edge is absent.
    assert decision.allowed is True
    assert decision.arrival_location_id == "catalog_l007"


def test_street_entry_respects_conditioned_building_entry_edge() -> None:
    state = gray_harbor_state()
    state.character_locations["protagonist"] = "oak_street"
    state.location_id = "oak_street"
    state.story_conditions["entry_condition"] = StoryConditionState(
        condition_id="entry_condition",
        name="测试入口条件",
        active=False,
        visibility="gm",
    )
    building = state.locations["catalog_l007"]
    building.exits = tuple(
        replace(
            value,
            required_condition_ids=("entry_condition",),
        )
        if value.to_location_id == "atlas_room_loc_5_1_7__1"
        else value
        for value in building.exits
    )

    blocked = evaluate_movement(state, "protagonist", "catalog_l007")
    assert blocked.allowed is True
    assert blocked.arrival_location_id == "catalog_l007"

    state.story_conditions["entry_condition"].active = True
    open_decision = evaluate_movement(state, "protagonist", "catalog_l007")
    assert open_decision.arrival_location_id == "atlas_room_loc_5_1_7__1"


def test_each_executable_start_can_eventually_enter_another_location() -> None:
    state = gray_harbor_state()
    graph = _allowed_movement_graph(state)

    for start, location in state.locations.items():
        if location.map_visibility == "gm":
            continue
        reachable = _reachable(graph, start)
        assert len(reachable) >= 2, f"起点 {start} 没有可达的其他地点"

    # Exercise actual step-by-step movement from roots, streets, buildings,
    # and an ordinary room rather than checking graph reachability alone.
    for start in (
        "gray_harbor",
        "catalog_l002",
        "catalog_l002",
        "atlas_room_loc_5_1_2__1",
    ):
        reachable = _reachable(graph, start)
        target = next(value for value in sorted(reachable) if value != start)
        # Find a real path to the selected target.
        queue = deque([(start, ())])
        visited = {start}
        path = None
        while queue:
            current, prefix = queue.popleft()
            if current == target:
                path = prefix
                break
            for destination in graph.get(current, ()):
                if destination not in visited:
                    visited.add(destination)
                    queue.append((destination, (*prefix, destination)))
        assert path is not None
        execution_state = gray_harbor_state()
        execution_state.character_locations["protagonist"] = start
        execution_state.location_id = start
        current = start
        for destination in path:
            command = ParsedCommand(
                action_type="move",
                actor_id="protagonist",
                target_id=destination,
                parameters={"destinationId": destination},
                original_text=f"test move to {destination}",
                authority="player",
            )
            resolution = resolve(execution_state, command)
            assert resolution.status == "committed", (
                f"{current} -> {destination} 无法提交: {resolution.outcome}"
            )
            for event in resolution.events:
                apply_event(execution_state, event)
            current = execution_state.character_locations["protagonist"]
        actual = execution_state.character_locations["protagonist"]
        # Building ids remain compatibility targets, but a street entry now
        # lands in the building's first ordinary structure.
        assert actual == target or (
            actual in execution_state.locations
            and execution_state.locations[actual].parent_id == target
        )


def test_every_atlas_structure_is_materialized_and_public_rooms_are_enterable() -> None:
    state = gray_harbor_state()
    events = gray_harbor_events()
    atlas = gray_harbor_atlas()
    graph = _allowed_movement_graph(state)
    structure_runtime_ids = {
        event.payload["atlasStructureId"]: event.payload["locationId"]
        for event in events
        if event.event_type == "location.created"
        and event.payload.get("atlasStructureId")
    }
    structure_runtime_ids.update({
        "loc_5_1_1__1": "white_heron_ground_floor",
        "loc_5_1_1__2": "white_heron_kitchen",
        "loc_5_1_1__3": "white_heron_second_floor",
        "loc_5_1_1__4": "white_heron_third_floor",
        "loc_5_1_1__5": "white_heron_cellar",
        "loc_5_1_1__6": "white_heron_backyard",
    })
    top_level_runtime_ids = {
        event.payload["atlasLocationId"]: event.payload["locationId"]
        for event in events
        if event.event_type == "location.created"
        and event.payload.get("atlasLocationId")
    }
    top_level_runtime_ids.update({
        "loc_5_1_1": "white_heron_house",
        "loc_5_1_8": "abandoned_bakery",
        "runtime_red_mill_tavern": "red_mill_tavern",
    })
    non_executable_shells = {"runtime_gray_harbor", "runtime_candle_ward"}

    open_structure_count = 0
    private_structure_count = 0
    for location in atlas.locations:
        if not location.structure or location.id in non_executable_shells:
            continue
        start = top_level_runtime_ids.get(location.id)
        assert start in state.locations, f"地点 {location.id} 未物化"
        reachable = _reachable(graph, start)
        for structure in location.structure:
            runtime_id = structure_runtime_ids.get(structure.id)
            assert runtime_id in state.locations, f"房间 {structure.id} 未物化"
            inbound = [
                exit_state
                for origin in state.locations.values()
                for exit_state in origin.exits
                if exit_state.to_location_id == runtime_id
            ]
            access = getattr(structure, "access", "public")
            if access == "hidden":
                if structure.discovery_id is None:
                    assert not inbound
                    assert runtime_id not in reachable
                else:
                    assert inbound
                    assert any(
                        not value.visible
                        and value.discovery_id == structure.discovery_id
                        for value in inbound
                    )
                continue
            assert inbound, f"房间 {runtime_id} 没有任何进入出口"
            # ``private`` is authored atmosphere, not an automatic gameplay
            # lock. It remains in the ordinary open interior route unless a
            # future package adds an explicit permission condition.
            if access == "private":
                private_structure_count += 1
            open_structure_count += 1
            assert all(not value.locked for value in inbound), (
                f"公开结构 {runtime_id} 不应因房间名称或用途被锁定"
            )
            assert runtime_id in reachable, (
                f"公开结构 {runtime_id} 无法从地点入口进入"
            )

    assert open_structure_count >= 400
    assert private_structure_count > 0


def test_exact_street_name_wins_over_building_substring_match() -> None:
    state = gray_harbor_state()
    state.character_locations["protagonist"] = "catalog_l002"
    state.location_id = "catalog_l002"
    command = interpret_player_text("去白鹭屋。", actor_id="protagonist", state=state)
    assert command.action_type == "move"
    assert command.parameters["destinationId"] == "white_heron_house"


def test_atlas_rooms_are_open_by_default_and_secret_tunnel_stays_hidden() -> None:
    state = gray_harbor_state()
    ordinary_room = state.locations["atlas_room_loc_5_1_2__1"]

    assert ordinary_room.exits
    assert all(value.visible for value in ordinary_room.exits)
    assert all(value.discovery_id is None for value in ordinary_room.exits)

    cellar = state.locations["white_heron_cellar"]
    tunnel = next(
        value for value in cellar.exits if value.exit_id == "cellar_tunnel_to_bakery"
    )
    assert tunnel.visible is False
    assert tunnel.discovery_id == "cellar_drainage_tunnel"


def test_public_map_exposes_full_city_without_room_nodes() -> None:
    state = gray_harbor_state()
    map_state = public_state(state)["map"]
    location_ids = {value["locationId"] for value in map_state["locations"]}

    assert "white_heron_house" in location_ids
    assert "white_heron_ground_floor" not in location_ids
    assert "white_heron_kitchen" not in location_ids
    assert "catalog_district_old_port" not in location_ids
    assert "catalog_l013" in location_ids
    assert any(region["regionId"] == "old_harbor" for region in map_state["regions"])
    assert all(
        value["kind"] != "room"
        for value in map_state["locations"]
    )


def test_location_capability_context_is_provider_neutral() -> None:
    context = build_location_capability_context(
        gray_harbor_state(),
        "white_heron_kitchen",
    )

    assert context is not None
    assert "white_heron_kitchen_bread" in context.visible_item_ids
    assert context.catalog_affordance_ids == ()
    assert "protagonist" not in context.co_located_character_ids


def test_location_capability_context_hides_items_in_invisible_containers() -> None:
    state = gray_harbor_state()
    pantry = state.containers["white_heron_kitchen_pantry"]
    pantry.visible = False

    context = build_location_capability_context(state, "white_heron_kitchen")

    assert context is not None
    assert "white_heron_kitchen_bread" not in context.visible_item_ids
    assert "white_heron_kitchen_stew" not in context.visible_item_ids
