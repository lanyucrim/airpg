from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from trpg_server.behavior.router import resolve
from trpg_server.core.projection import apply_event, public_state, replay
from trpg_server.core.state import Event, ParsedCommand
from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events
from trpg_server.world.weather import (
    DisabledWeatherAdapter,
    SafeWeatherDirector,
    WEATHER_CONDITION_NAMES,
    materialize_weather_events,
)
from trpg_server.locations.weather_travel import adjust_travel_time_for_weather


def _opening_state():
    events = gray_harbor_events()
    return events, replay(GRAY_HARBOR_CAMPAIGN_ID, events, 1)


def _state_with_confirmed_weather(condition: str = "heavy_rain"):
    events, state = _opening_state()
    proposal = SafeWeatherDirector(DisabledWeatherAdapter()).propose(
        state,
        previous_world_time=state.world_time,
    )
    authored = materialize_weather_events(state, proposal, events[-1].event_id)[0]
    payload = {
        **authored.payload,
        "condition": condition,
        "conditionName": WEATHER_CONDITION_NAMES[condition],
    }
    weather_event = replace(authored, payload=payload)
    apply_event(state, weather_event)
    return state, weather_event


def test_heavy_rain_adds_time_only_between_top_level_places() -> None:
    state, weather_event = _state_with_confirmed_weather()

    cross_place = adjust_travel_time_for_weather(
        state,
        "oak_street",
        "atlas_room_loc_5_1_7__1",
        48,
    )
    internal = adjust_travel_time_for_weather(
        state,
        "white_heron_ground_floor",
        "white_heron_kitchen",
        12,
    )

    assert cross_place.origin_place_id == "oak_street"
    assert cross_place.destination_place_id == "catalog_l007"
    assert cross_place.weather_delay_minutes == 12
    assert cross_place.travel_minutes == 60
    assert cross_place.weather_event_id == weather_event.event_id
    assert cross_place.weather_multiplier_percent == 25
    assert internal.origin_place_id == internal.destination_place_id == "white_heron_house"
    assert internal.weather_delay_minutes == 0
    assert internal.travel_minutes == 12
    assert internal.weather_event_id is None


def test_room_to_street_and_street_to_building_are_cross_place_travel() -> None:
    state, weather_event = _state_with_confirmed_weather()

    leaving = adjust_travel_time_for_weather(
        state,
        "white_heron_ground_floor",
        "oak_street",
        16,
    )
    entering = adjust_travel_time_for_weather(
        state,
        "oak_street",
        "white_heron_ground_floor",
        8,
    )

    assert leaving.weather_delay_minutes == 4
    assert entering.weather_delay_minutes == 2
    assert leaving.weather_event_id == entering.weather_event_id == weather_event.event_id


def test_missing_confirmed_weather_preserves_base_travel_time() -> None:
    _, state = _opening_state()

    adjustment = adjust_travel_time_for_weather(
        state,
        "oak_street",
        "atlas_room_loc_5_2_2__1",
        48,
    )

    assert adjustment.reason_code == "confirmed_weather_unavailable"
    assert adjustment.weather_delay_minutes == 0
    assert adjustment.travel_minutes == 48
    assert adjustment.weather_event_id is None


def test_public_exits_preview_base_weather_delay_and_estimated_time() -> None:
    state, _ = _state_with_confirmed_weather()
    state.character_locations["protagonist"] = "oak_street"
    state.location_id = "oak_street"

    published = public_state(state)
    scene_exit = next(
        value
        for value in published["scene"]["exits"]
        if value["toLocationId"] == "catalog_l007"
    )
    map_street = next(
        value
        for value in published["map"]["locations"]
        if value["locationId"] == "oak_street"
    )
    map_exit = next(
        value
        for value in map_street["exits"]
        if value["toLocationId"] == "catalog_l007"
    )

    for preview in (scene_exit, map_exit):
        assert preview["travelMinutes"] == 48
        assert preview["baseTravelMinutes"] == 48
        assert preview["weatherDelayMinutes"] == 12
        assert preview["estimatedTravelMinutes"] == 60
        assert preview["weatherCondition"] == "heavy_rain"
        assert preview["weatherConditionName"] == "大雨"


def test_public_internal_exit_explicitly_previews_zero_weather_delay() -> None:
    state, _ = _state_with_confirmed_weather()
    state.character_locations["protagonist"] = "white_heron_ground_floor"
    state.location_id = "white_heron_ground_floor"

    kitchen = next(
        value
        for value in public_state(state)["scene"]["exits"]
        if value["toLocationId"] == "white_heron_kitchen"
    )

    assert kitchen["baseTravelMinutes"] == kitchen["travelMinutes"] == 1
    assert kitchen["weatherDelayMinutes"] == 0
    assert kitchen["estimatedTravelMinutes"] == 1
    assert kitchen["weatherCondition"] is None
    assert kitchen["weatherConditionName"] is None


def test_move_uses_adjusted_time_and_records_weather_source_in_schema_two() -> None:
    state, weather_event = _state_with_confirmed_weather()
    state.character_locations["protagonist"] = "oak_street"
    state.location_id = "oak_street"
    command = ParsedCommand(
        action_type="move",
        actor_id="protagonist",
        target_id="catalog_l007",
        parameters={"destinationId": "catalog_l007"},
        original_text="前往圣奥德里克教堂",
        authority="player",
    )

    result = resolve(state, command)
    movement = next(event for event in result.events if event.event_type == "character.moved")
    time_event = next(event for event in result.events if event.event_type == "time.advanced")

    assert result.status == "committed"
    assert movement.schema_version == 2
    assert movement.payload["baseTravelMinutes"] == 48
    assert movement.payload["weatherDelayMinutes"] == 12
    assert movement.payload["travelMinutes"] == 60
    assert movement.payload["weatherEventId"] == weather_event.event_id
    assert movement.payload["weatherCondition"] == "heavy_rain"
    assert movement.payload["weatherMultiplierPercent"] == 25
    assert time_event.payload["minutes"] == 60

    replayed = deepcopy(state)
    for event in result.events:
        apply_event(replayed, event)
    assert replayed.world_time == state.world_time + 60
    assert replayed.character_locations["protagonist"] == movement.payload["toLocationId"]


def test_illegal_move_does_not_create_weather_or_time_events() -> None:
    state, _ = _state_with_confirmed_weather()
    command = ParsedCommand(
        action_type="move",
        actor_id="protagonist",
        target_id="catalog_l007",
        parameters={"destinationId": "catalog_l007"},
        original_text="直接去圣奥德里克教堂",
        authority="player",
    )

    result = resolve(state, command)

    assert result.status == "rejected"
    assert result.events == []


def test_character_moved_schema_one_replays_and_schema_two_rejects_fake_weather() -> None:
    _, state = _opening_state()
    legacy = Event(
        "evt_legacy_move",
        "character.moved",
        "protagonist",
        state.world_time,
        {
            "characterId": "protagonist",
            "fromLocationId": "white_heron_ground_floor",
            "toLocationId": "white_heron_kitchen",
            "travelMinutes": 1,
        },
        schema_version=1,
    )
    apply_event(state, legacy)
    assert state.character_locations["protagonist"] == "white_heron_kitchen"

    invalid = Event(
        "evt_invalid_weather_move",
        "character.moved",
        "protagonist",
        state.world_time,
        {
            "characterId": "protagonist",
            "fromLocationId": "white_heron_kitchen",
            "toLocationId": "oak_street",
            "baseTravelMinutes": 8,
            "weatherDelayMinutes": 2,
            "travelMinutes": 10,
            "weatherEventId": "evt_weather_does_not_exist",
            "weatherCondition": "heavy_rain",
            "weatherMultiplierPercent": 25,
        },
        schema_version=2,
    )
    with pytest.raises(ValueError, match="confirmed weather source"):
        apply_event(state, invalid)
