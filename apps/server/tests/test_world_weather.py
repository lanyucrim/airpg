from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from trpg_server.ai.platform.deepseek import DeepSeekSettings
from trpg_server.ai.platform.weather_adapter import (
    DeepSeekWeatherAdapter,
    weather_director_from_environment,
)
from trpg_server.core.projection import apply_event, public_state, replay
from trpg_server.core.schemas import TurnRequest
from trpg_server.core.service import GameService
from trpg_server.core.state import Event
from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events
from trpg_server.world.weather import (
    DisabledWeatherAdapter,
    SafeWeatherDirector,
    WeatherAdapterResult,
    WeatherCallMetrics,
    WeatherCandidate,
    WeatherProposal,
    materialize_weather_events,
    season_for_month,
    weather_policy_for_scenario,
)


class FixedWeatherAdapter:
    available = True
    model_name = "fixed-weather"
    provider_name = "test"

    def __init__(self, condition: str, low: int, high: int) -> None:
        self.condition = condition
        self.low = low
        self.high = high
        self.calls = 0

    def propose(self, request):
        self.calls += 1
        return WeatherAdapterResult(
            WeatherProposal(
                candidates=tuple(
                    WeatherCandidate(
                        dateKey=day.date_key,
                        condition=self.condition,
                        lowTemperatureC=self.low,
                        highTemperatureC=self.high,
                    )
                    for day in request.days
                )
            ),
            WeatherCallMetrics(total_tokens=12),
        )


class FailingWeatherAdapter:
    available = True
    model_name = "failing-weather"
    provider_name = "test"

    def propose(self, request):
        del request
        raise TimeoutError("late")


def _initial_state():
    events = gray_harbor_events()
    return events, replay(GRAY_HARBOR_CAMPAIGN_ID, events, 1)


def test_default_climate_has_four_program_owned_seasons() -> None:
    policy = weather_policy_for_scenario("scenario-without-authored-climate")

    assert policy.climate_id == "temperate_four_season"
    assert policy.climate_source_status == "default"
    assert [season_for_month(month) for month in (3, 6, 9, 12)] == [
        "spring",
        "summer",
        "autumn",
        "winter",
    ]
    assert "snow" not in policy.season_rules["summer"].allowed_conditions
    assert "snow" in policy.season_rules["winter"].allowed_conditions


def test_gray_harbor_opening_weather_obeys_authored_autumn_rain() -> None:
    events, state = _initial_state()
    director = SafeWeatherDirector(DisabledWeatherAdapter())

    result = director.propose(state, previous_world_time=state.world_time)
    weather_events = materialize_weather_events(state, result, events[-1].event_id)
    replayed = replay(GRAY_HARBOR_CAMPAIGN_ID, [*events, *weather_events], 1)
    weather = public_state(replayed)["weather"]

    assert result.audit.status == "program_fallback"
    assert len(weather_events) == 1
    assert weather_events[0].event_type == "world.weather_determined"
    assert weather["dateKey"] == "海历621-10-17"
    assert weather["season"] == "autumn"
    assert weather["condition"] == "rain_clearing"
    assert weather["climateSourceStatus"] == "default"


def test_valid_ai_candidate_becomes_confirmed_weather_event() -> None:
    events, state = _initial_state()
    adapter = FixedWeatherAdapter("rain_clearing", 7, 13)
    result = SafeWeatherDirector(adapter).propose(
        state,
        previous_world_time=state.world_time,
    )
    weather_events = materialize_weather_events(state, result, events[-1].event_id)

    assert result.audit.status == "model_accepted"
    assert result.audit.metrics.total_tokens == 12
    assert adapter.calls == 1
    assert weather_events[0].payload["generation"]["source"] == "ai"
    assert weather_events[0].payload["lowTemperatureC"] == 7


def test_illegal_ai_weather_is_rejected_and_program_falls_back() -> None:
    events, state = _initial_state()
    adapter = FixedWeatherAdapter("heavy_snow", -10, -2)
    result = SafeWeatherDirector(adapter).propose(
        state,
        previous_world_time=state.world_time,
    )
    weather_events = materialize_weather_events(state, result, events[-1].event_id)

    assert result.audit.status == "model_partial_fallback"
    assert result.rejected == ({
        "dateKey": "海历621-10-17",
        "reason": "condition_not_allowed",
    },)
    assert weather_events[0].payload["condition"] == "rain_clearing"
    assert weather_events[0].payload["generation"]["source"] == "program_fallback"


def test_model_failure_falls_back_without_losing_daily_weather() -> None:
    events, state = _initial_state()
    result = SafeWeatherDirector(FailingWeatherAdapter()).propose(
        state,
        previous_world_time=state.world_time,
    )
    weather_events = materialize_weather_events(state, result, events[-1].event_id)

    assert result.audit.status == "model_fallback"
    assert result.audit.failure_code == "TimeoutError"
    assert len(weather_events) == 1


def test_duplicate_confirmed_weather_for_one_date_is_rejected() -> None:
    events, state = _initial_state()
    result = SafeWeatherDirector(DisabledWeatherAdapter()).propose(
        state,
        previous_world_time=state.world_time,
    )
    weather_event = materialize_weather_events(state, result, events[-1].event_id)[0]
    apply_event(state, weather_event)
    duplicate = Event(
        "evt_duplicate_weather",
        "world.weather_determined",
        "system",
        state.world_time,
        dict(weather_event.payload),
        schema_version=1,
    )

    with pytest.raises(ValueError, match="already determined"):
        apply_event(state, duplicate)


def test_same_day_turn_does_not_call_weather_model_again(tmp_path: Path) -> None:
    adapter = FixedWeatherAdapter("rain_clearing", 7, 13)
    game = GameService(
        tmp_path / "weather.sqlite3",
        weather_director=SafeWeatherDirector(adapter),
    )
    game.initialize()
    assert adapter.calls == 1

    result = game.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        TurnRequest(
            idempotency_key="same-day-weather",
            expected_state_version=1,
            actor_id="protagonist",
            text="等待十分钟",
        ),
    )

    assert result["state"]["weather"]["dateKey"] == "海历621-10-17"
    assert adapter.calls == 1


def test_crossing_midnight_requests_only_missing_date(tmp_path: Path) -> None:
    opening = FixedWeatherAdapter("rain_clearing", 7, 13)
    game = GameService(
        tmp_path / "weather-midnight.sqlite3",
        weather_director=SafeWeatherDirector(opening),
    )
    game.initialize()
    next_day = FixedWeatherAdapter("cloudy", 8, 14)
    game.weather_director = SafeWeatherDirector(next_day)
    game.turn_pipeline = game.turn_pipeline.__class__(
        intent_parser=game.intent_parser,
        resolver=game.turn_pipeline.resolver,
        npc_decider=game.npc_decider,
        routine_director=game.routine_director,
        weather_director=game.weather_director,
        narrator=game.narrator,
        projection_runner=game.turn_pipeline.projection_runner,
    )

    result = game.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        TurnRequest(
            idempotency_key="cross-midnight-weather",
            expected_state_version=1,
            actor_id="protagonist",
            text="等待两个小时",
        ),
    )

    assert next_day.calls == 1
    assert result["state"]["weather"]["dateKey"] == "海历621-10-18"
    assert result["state"]["weather"]["condition"] == "cloudy"


def test_deepseek_weather_adapter_uses_shared_key_and_json_contract() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({
                    "schemaVersion": 1,
                    "candidates": [{
                        "schemaVersion": 1,
                        "dateKey": "海历621-10-17",
                        "condition": "rain_clearing",
                        "lowTemperatureC": 7,
                        "highTemperatureC": 13,
                    }],
                })},
            }],
            "usage": {"total_tokens": 22},
        })

    _, state = _initial_state()
    director = SafeWeatherDirector(DeepSeekWeatherAdapter(
        DeepSeekSettings(api_key="shared-test-key", max_attempts=1),
        transport=httpx.MockTransport(handler),
    ))
    result = director.propose(state, previous_world_time=state.world_time)

    assert result.audit.status == "model_accepted"
    assert captured["authorization"] == "Bearer shared-test-key"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["temperature"] == 0.4
    system_prompt = captured["body"]["messages"][0]["content"]
    assert "requiredCondition" in system_prompt
    assert "不得超过 18 摄氏度" in system_prompt
    assert "shared-test-key" not in json.dumps(captured["body"])


def test_weather_environment_is_disabled_by_default_and_uses_deepseek() -> None:
    disabled = weather_director_from_environment({})
    configured = weather_director_from_environment({
        "TRPG_WEATHER_MODEL_ENABLED": "true",
        "TRPG_WEATHER_MODEL_PROVIDER": "deepseek",
        "DEEPSEEK_API_KEY": "test-key",
    })

    assert disabled.adapter.available is False
    assert configured.adapter.available is True
    assert configured.adapter.provider_name == "deepseek"
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        weather_director_from_environment({"TRPG_WEATHER_MODEL_ENABLED": "true"})
