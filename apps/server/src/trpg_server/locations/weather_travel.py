from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from trpg_server.core.state import ExitState, Projection
from trpg_server.map.traversal import resolve_arrival_location
from trpg_server.world.weather import WEATHER_CONDITION_NAMES, WeatherCondition


# The location domain owns how confirmed weather changes travel through Gray
# Harbor's wet, uneven streets. World weather remains a read-only input.
WEATHER_TRAVEL_DELAY_PERCENT: dict[WeatherCondition, int] = {
    "clear": 0,
    "partly_cloudy": 0,
    "cloudy": 0,
    "overcast": 0,
    "rain_clearing": 5,
    "fog": 8,
    "drizzle": 8,
    "light_rain": 10,
    "rain": 15,
    "strong_wind": 20,
    "heavy_rain": 25,
    "light_snow": 25,
    "thunderstorm": 35,
    "sleet": 35,
    "snow": 35,
    "heavy_snow": 55,
}

INTERNAL_STRUCTURE_KINDS = frozenset({"room", "floor", "yard"})


@dataclass(frozen=True, slots=True)
class WeatherTravelAdjustment:
    base_travel_minutes: int
    weather_delay_minutes: int
    travel_minutes: int
    origin_place_id: str
    destination_place_id: str
    weather_event_id: str | None
    weather_condition: WeatherCondition | None
    weather_condition_name: str | None
    weather_multiplier_percent: int
    reason_code: str


def adjust_travel_time_for_weather(
    state: Projection,
    origin_location_id: str,
    destination_location_id: str,
    base_travel_minutes: int,
) -> WeatherTravelAdjustment:
    """Apply confirmed daily weather only when crossing top-level places."""
    if base_travel_minutes < 0:
        raise ValueError("base travel minutes cannot be negative")

    origin_place_id = top_level_place_id(state, origin_location_id)
    destination_place_id = top_level_place_id(state, destination_location_id)
    if origin_place_id == destination_place_id:
        return _unchanged_adjustment(
            base_travel_minutes,
            origin_place_id,
            destination_place_id,
            "same_top_level_location",
        )

    weather = current_confirmed_weather(state)
    if weather is None:
        return _unchanged_adjustment(
            base_travel_minutes,
            origin_place_id,
            destination_place_id,
            "confirmed_weather_unavailable",
        )

    condition_value = str(weather["condition"])
    if condition_value not in WEATHER_TRAVEL_DELAY_PERCENT:
        return _unchanged_adjustment(
            base_travel_minutes,
            origin_place_id,
            destination_place_id,
            "weather_condition_unsupported",
        )
    condition = cast(WeatherCondition, condition_value)
    multiplier = WEATHER_TRAVEL_DELAY_PERCENT[condition]
    delay = _round_to_nearest_minute(base_travel_minutes, multiplier)
    weather_event_id = str(weather["eventId"])
    return WeatherTravelAdjustment(
        base_travel_minutes=base_travel_minutes,
        weather_delay_minutes=delay,
        travel_minutes=base_travel_minutes + delay,
        origin_place_id=origin_place_id,
        destination_place_id=destination_place_id,
        weather_event_id=weather_event_id,
        weather_condition=condition,
        weather_condition_name=WEATHER_CONDITION_NAMES[condition],
        weather_multiplier_percent=multiplier,
        reason_code=(
            "weather_delay_applied" if delay else "weather_delay_below_one_minute"
        ),
    )


def estimate_exit_travel_time(
    state: Projection,
    actor_id: str,
    origin_location_id: str,
    exit_state: ExitState,
) -> WeatherTravelAdjustment:
    """Preview an exit with the same destination and weather rules as movement."""

    arrival_location_id = resolve_arrival_location(
        state,
        origin_location_id,
        exit_state.to_location_id,
        actor_id=actor_id,
    )
    return adjust_travel_time_for_weather(
        state,
        origin_location_id,
        arrival_location_id,
        exit_state.travel_minutes,
    )


def top_level_place_id(state: Projection, location_id: str) -> str:
    """Collapse rooms, floors and yards to their owning playable place."""
    current_id = location_id
    seen: set[str] = set()
    while current_id not in seen:
        seen.add(current_id)
        location = state.locations.get(current_id)
        if (
            location is None
            or location.kind not in INTERNAL_STRUCTURE_KINDS
            or location.parent_id is None
            or location.parent_id not in state.locations
        ):
            break
        current_id = location.parent_id
    return current_id


def current_confirmed_weather(state: Projection) -> dict[str, Any] | None:
    """Return the latest weather fact effective at the departure time."""
    candidates = [
        weather
        for weather in state.weather_by_date.values()
        if int(weather.get("effectiveFromWorldTime", 0)) <= state.world_time
        and str(weather.get("eventId", "")) in state.confirmed_event_ids
        and state.event_types_by_id.get(str(weather.get("eventId", "")))
        == "world.weather_determined"
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda weather: (
            int(weather.get("effectiveFromWorldTime", 0)),
            str(weather.get("dateKey", "")),
        ),
    )


def _round_to_nearest_minute(base_minutes: int, multiplier_percent: int) -> int:
    return (base_minutes * multiplier_percent + 50) // 100


def _unchanged_adjustment(
    base_minutes: int,
    origin_place_id: str,
    destination_place_id: str,
    reason_code: str,
) -> WeatherTravelAdjustment:
    return WeatherTravelAdjustment(
        base_travel_minutes=base_minutes,
        weather_delay_minutes=0,
        travel_minutes=base_minutes,
        origin_place_id=origin_place_id,
        destination_place_id=destination_place_id,
        weather_event_id=None,
        weather_condition=None,
        weather_condition_name=None,
        weather_multiplier_percent=0,
        reason_code=reason_code,
    )


__all__ = [
    "WEATHER_TRAVEL_DELAY_PERCENT",
    "WeatherTravelAdjustment",
    "adjust_travel_time_for_weather",
    "current_confirmed_weather",
    "estimate_exit_travel_time",
    "top_level_place_id",
]
