from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trpg_server.core.state import CalendarState, Event, Projection


SeasonId = Literal["spring", "summer", "autumn", "winter"]
WeatherCondition = Literal[
    "clear",
    "partly_cloudy",
    "cloudy",
    "overcast",
    "fog",
    "drizzle",
    "light_rain",
    "rain",
    "heavy_rain",
    "rain_clearing",
    "thunderstorm",
    "sleet",
    "light_snow",
    "snow",
    "heavy_snow",
    "strong_wind",
]

SEASON_NAMES: dict[SeasonId, str] = {
    "spring": "春季",
    "summer": "夏季",
    "autumn": "秋季",
    "winter": "冬季",
}

WEATHER_CONDITION_NAMES: dict[WeatherCondition, str] = {
    "clear": "晴朗",
    "partly_cloudy": "晴间多云",
    "cloudy": "多云",
    "overcast": "阴天",
    "fog": "雾",
    "drizzle": "毛毛雨",
    "light_rain": "小雨",
    "rain": "雨",
    "heavy_rain": "大雨",
    "rain_clearing": "雨后转阴",
    "thunderstorm": "雷雨",
    "sleet": "雨夹雪",
    "light_snow": "小雪",
    "snow": "雪",
    "heavy_snow": "大雪",
    "strong_wind": "强风",
}


@dataclass(frozen=True, slots=True)
class SeasonWeatherRule:
    season: SeasonId
    minimum_temperature_c: int
    maximum_temperature_c: int
    allowed_conditions: tuple[WeatherCondition, ...]


TEMPERATE_FOUR_SEASON_RULES: dict[SeasonId, SeasonWeatherRule] = {
    "spring": SeasonWeatherRule(
        "spring",
        2,
        24,
        (
            "clear",
            "partly_cloudy",
            "cloudy",
            "overcast",
            "fog",
            "drizzle",
            "light_rain",
            "rain",
            "heavy_rain",
            "rain_clearing",
            "thunderstorm",
            "strong_wind",
        ),
    ),
    "summer": SeasonWeatherRule(
        "summer",
        13,
        32,
        (
            "clear",
            "partly_cloudy",
            "cloudy",
            "overcast",
            "fog",
            "drizzle",
            "light_rain",
            "rain",
            "heavy_rain",
            "rain_clearing",
            "thunderstorm",
            "strong_wind",
        ),
    ),
    "autumn": SeasonWeatherRule(
        "autumn",
        0,
        23,
        (
            "clear",
            "partly_cloudy",
            "cloudy",
            "overcast",
            "fog",
            "drizzle",
            "light_rain",
            "rain",
            "heavy_rain",
            "rain_clearing",
            "thunderstorm",
            "sleet",
            "strong_wind",
        ),
    ),
    "winter": SeasonWeatherRule(
        "winter",
        -12,
        10,
        (
            "clear",
            "partly_cloudy",
            "cloudy",
            "overcast",
            "fog",
            "drizzle",
            "light_rain",
            "rain",
            "rain_clearing",
            "sleet",
            "light_snow",
            "snow",
            "heavy_snow",
            "strong_wind",
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class AuthoredWeatherConstraint:
    date_key: str
    condition: WeatherCondition
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WeatherPolicy:
    climate_id: str
    climate_name: str
    climate_source_status: Literal["canon", "inferred", "default"]
    source_refs: tuple[str, ...]
    season_rules: dict[SeasonId, SeasonWeatherRule]
    authored_constraints: dict[str, AuthoredWeatherConstraint]


class WeatherModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class WeatherCandidate(WeatherModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    date_key: str = Field(alias="dateKey", min_length=3, max_length=80)
    condition: WeatherCondition
    low_temperature_c: int = Field(
        alias="lowTemperatureC", ge=-80, le=60, strict=True
    )
    high_temperature_c: int = Field(
        alias="highTemperatureC", ge=-80, le=60, strict=True
    )

    @model_validator(mode="after")
    def temperature_range_is_ordered(self) -> WeatherCandidate:
        if self.low_temperature_c > self.high_temperature_c:
            raise ValueError("lowTemperatureC cannot exceed highTemperatureC")
        if self.high_temperature_c - self.low_temperature_c > 18:
            raise ValueError("daily temperature range cannot exceed 18 C")
        return self


class WeatherProposal(WeatherModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    candidates: tuple[WeatherCandidate, ...] = Field(default=(), max_length=31)


class WeatherDayContext(WeatherModel):
    date_key: str = Field(alias="dateKey")
    era: str
    year: int
    month: int
    day: int
    season: SeasonId
    season_name: str = Field(alias="seasonName")
    effective_world_time: int = Field(alias="effectiveWorldTime", ge=0)
    allowed_conditions: tuple[WeatherCondition, ...] = Field(alias="allowedConditions")
    minimum_temperature_c: int = Field(alias="minimumTemperatureC")
    maximum_temperature_c: int = Field(alias="maximumTemperatureC")
    required_condition: WeatherCondition | None = Field(
        default=None,
        alias="requiredCondition",
    )


class WeatherGenerationRequest(WeatherModel):
    system_instruction: str = Field(alias="systemInstruction")
    climate_id: str = Field(alias="climateId")
    climate_name: str = Field(alias="climateName")
    climate_source_status: Literal["canon", "inferred", "default"] = Field(
        alias="climateSourceStatus"
    )
    days: tuple[WeatherDayContext, ...] = Field(min_length=1, max_length=31)
    previous_weather: dict[str, Any] | None = Field(
        default=None,
        alias="previousWeather",
    )


@dataclass(frozen=True, slots=True)
class WeatherCallMetrics:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None


@dataclass(frozen=True, slots=True)
class WeatherAdapterResult:
    output: WeatherProposal | dict[str, Any]
    metrics: WeatherCallMetrics = WeatherCallMetrics()


class WeatherAdapter(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def model_name(self) -> str | None: ...

    @property
    def provider_name(self) -> str | None: ...

    def propose(
        self,
        request: WeatherGenerationRequest,
    ) -> WeatherProposal | dict[str, Any] | WeatherAdapterResult: ...


class DisabledWeatherAdapter:
    @property
    def available(self) -> bool:
        return False

    @property
    def model_name(self) -> None:
        return None

    @property
    def provider_name(self) -> None:
        return None

    def propose(self, request: WeatherGenerationRequest) -> WeatherProposal:
        del request
        raise RuntimeError("weather model adapter is disabled")


@dataclass(frozen=True, slots=True)
class WeatherValidation:
    accepted: bool
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class AcceptedWeatherCandidate:
    day: WeatherDayContext
    candidate: WeatherCandidate
    generation_source: Literal["ai", "program_fallback"]
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class WeatherGenerationAudit:
    status: Literal[
        "not_applicable",
        "program_fallback",
        "model_accepted",
        "model_partial_fallback",
        "model_fallback",
    ]
    provider_name: str | None
    model_name: str | None
    request_payload: dict[str, Any] | None
    response_payload: dict[str, Any] | None
    failure_code: str | None
    metrics: WeatherCallMetrics = WeatherCallMetrics()


@dataclass(frozen=True, slots=True)
class WeatherGenerationResult:
    accepted: tuple[AcceptedWeatherCandidate, ...]
    rejected: tuple[dict[str, str], ...]
    audit: WeatherGenerationAudit


@dataclass(frozen=True, slots=True)
class SafeWeatherDirector:
    adapter: WeatherAdapter

    def propose(
        self,
        state: Projection,
        *,
        previous_world_time: int,
    ) -> WeatherGenerationResult:
        policy = weather_policy_for_scenario(state.scenario_id)
        days = weather_days_for_window(state, previous_world_time, policy)
        if not days:
            return WeatherGenerationResult(
                (),
                (),
                WeatherGenerationAudit(
                    "not_applicable", None, None, None, None, None
                ),
            )

        previous = _latest_weather_candidate(state)
        if not self.adapter.available:
            accepted = _fallback_days(days, previous)
            return WeatherGenerationResult(
                accepted,
                (),
                WeatherGenerationAudit(
                    "program_fallback",
                    None,
                    None,
                    None,
                    None,
                    "model_disabled",
                ),
            )

        request = WeatherGenerationRequest(
            systemInstruction=_weather_system_instruction(),
            climateId=policy.climate_id,
            climateName=policy.climate_name,
            climateSourceStatus=policy.climate_source_status,
            days=days,
            previousWeather=(
                previous.model_dump(by_alias=True) if previous is not None else None
            ),
        )
        request_payload = request.model_dump(by_alias=True)
        response_payload: dict[str, Any] | None = None
        metrics = WeatherCallMetrics()
        try:
            raw = self.adapter.propose(request)
            metrics = (
                raw.metrics if isinstance(raw, WeatherAdapterResult) else WeatherCallMetrics()
            )
            output = raw.output if isinstance(raw, WeatherAdapterResult) else raw
            response_payload = (
                output.model_dump(by_alias=True)
                if isinstance(output, WeatherProposal)
                else dict(output)
            )
            proposal = (
                output
                if isinstance(output, WeatherProposal)
                else WeatherProposal.model_validate(output)
            )
            accepted, rejected = _validate_proposal(days, proposal, previous)
            status: Literal["model_accepted", "model_partial_fallback"] = (
                "model_accepted" if not rejected else "model_partial_fallback"
            )
            return WeatherGenerationResult(
                accepted,
                rejected,
                WeatherGenerationAudit(
                    status,
                    self.adapter.provider_name,
                    self.adapter.model_name,
                    request_payload,
                    response_payload,
                    None,
                    metrics,
                ),
            )
        except Exception as error:
            return WeatherGenerationResult(
                _fallback_days(days, previous, failure_code=type(error).__name__),
                (),
                WeatherGenerationAudit(
                    "model_fallback",
                    self.adapter.provider_name,
                    self.adapter.model_name,
                    request_payload,
                    response_payload,
                    type(error).__name__,
                    metrics,
                ),
            )


def weather_policy_for_scenario(scenario_id: str | None) -> WeatherPolicy:
    constraints: dict[str, AuthoredWeatherConstraint] = {}
    source_refs = (
        "world.default:temperate_four_season",
    )
    if scenario_id == "gray-harbor-black-tide-throne":
        opening_date = "海历621-10-17"
        constraints[opening_date] = AuthoredWeatherConstraint(
            opening_date,
            "rain_clearing",
            (
                "content/campaigns/gray-harbor/scenes.json#scene_last_seven_days.openingText",
            ),
        )
        source_refs = (
            *source_refs,
            "灰港_黑潮王座_V4.2_AI_GM主线状态机与支线条件版.md#第十编",
        )
    return WeatherPolicy(
        climate_id="temperate_four_season",
        climate_name="温带四季（默认）",
        climate_source_status="default",
        source_refs=source_refs,
        season_rules=TEMPERATE_FOUR_SEASON_RULES,
        authored_constraints=constraints,
    )


def season_for_month(month: int, months_per_year: int = 12) -> SeasonId:
    if months_per_year < 1 or not 1 <= month <= months_per_year:
        raise ValueError("month is outside the calendar")
    normalized_month = ((month - 1) * 12 // months_per_year) + 1
    if normalized_month in {3, 4, 5}:
        return "spring"
    if normalized_month in {6, 7, 8}:
        return "summer"
    if normalized_month in {9, 10, 11}:
        return "autumn"
    return "winter"


def calendar_date_at(
    calendar: CalendarState,
    world_time: int,
) -> tuple[str, int, int, int, int]:
    elapsed = world_time - calendar.origin_world_time
    total_minutes = calendar.hour * 60 + calendar.minute + elapsed
    elapsed_days, _ = divmod(total_minutes, 1440)
    origin_ordinal = _calendar_ordinal(
        calendar.year,
        calendar.month,
        calendar.day,
        calendar.days_per_month,
        calendar.months_per_year,
    )
    ordinal = origin_ordinal + elapsed_days
    year, month, day = _date_from_ordinal(
        ordinal,
        calendar.days_per_month,
        calendar.months_per_year,
    )
    return (
        f"{calendar.era}{year}-{month:02d}-{day:02d}",
        year,
        month,
        day,
        ordinal,
    )


def weather_days_for_window(
    state: Projection,
    previous_world_time: int,
    policy: WeatherPolicy,
) -> tuple[WeatherDayContext, ...]:
    calendar = state.calendar
    if calendar is None or state.world_time < previous_world_time:
        return ()
    _, _, _, _, first_ordinal = calendar_date_at(calendar, previous_world_time)
    _, _, _, _, last_ordinal = calendar_date_at(calendar, state.world_time)
    if last_ordinal - first_ordinal > 30:
        first_ordinal = last_ordinal - 30
    origin_ordinal = _calendar_ordinal(
        calendar.year,
        calendar.month,
        calendar.day,
        calendar.days_per_month,
        calendar.months_per_year,
    )
    origin_minute = calendar.hour * 60 + calendar.minute
    days: list[WeatherDayContext] = []
    for ordinal in range(first_ordinal, last_ordinal + 1):
        year, month, day = _date_from_ordinal(
            ordinal,
            calendar.days_per_month,
            calendar.months_per_year,
        )
        date_key = f"{calendar.era}{year}-{month:02d}-{day:02d}"
        if date_key in state.weather_by_date:
            continue
        season = season_for_month(month, calendar.months_per_year)
        rule = policy.season_rules[season]
        constraint = policy.authored_constraints.get(date_key)
        effective_world_time = calendar.origin_world_time + (
            (ordinal - origin_ordinal) * 1440 - origin_minute
        )
        days.append(WeatherDayContext(
            dateKey=date_key,
            era=calendar.era,
            year=year,
            month=month,
            day=day,
            season=season,
            seasonName=SEASON_NAMES[season],
            effectiveWorldTime=max(calendar.origin_world_time, effective_world_time),
            allowedConditions=rule.allowed_conditions,
            minimumTemperatureC=rule.minimum_temperature_c,
            maximumTemperatureC=rule.maximum_temperature_c,
            requiredCondition=constraint.condition if constraint is not None else None,
        ))
    return tuple(days)


def validate_weather_candidate(
    day: WeatherDayContext,
    candidate: WeatherCandidate,
    previous: WeatherCandidate | None = None,
) -> WeatherValidation:
    if candidate.date_key != day.date_key:
        return WeatherValidation(False, "date_mismatch", "候选日期与请求日期不一致")
    if candidate.condition not in day.allowed_conditions:
        return WeatherValidation(False, "condition_not_allowed", "天气不属于当前季节允许集合")
    if day.required_condition is not None and candidate.condition != day.required_condition:
        return WeatherValidation(False, "authored_condition_conflict", "天气违反剧本明确约束")
    if (
        candidate.low_temperature_c < day.minimum_temperature_c
        or candidate.high_temperature_c > day.maximum_temperature_c
    ):
        return WeatherValidation(False, "temperature_out_of_season", "温度超出当前季节范围")
    if candidate.condition in {"light_snow", "snow", "heavy_snow"} and candidate.high_temperature_c > 4:
        return WeatherValidation(False, "snow_temperature_conflict", "降雪温度过高")
    if candidate.condition == "sleet" and (
        candidate.low_temperature_c > 3 or candidate.high_temperature_c > 7
    ):
        return WeatherValidation(False, "sleet_temperature_conflict", "雨夹雪温度不合理")
    if candidate.condition == "thunderstorm" and candidate.high_temperature_c < 10:
        return WeatherValidation(False, "storm_temperature_conflict", "雷雨温度过低")
    if day.required_condition is None and previous is not None and (
        abs(candidate.low_temperature_c - previous.low_temperature_c) > 14
        or abs(candidate.high_temperature_c - previous.high_temperature_c) > 14
    ):
        return WeatherValidation(False, "temperature_jump_too_large", "相邻日期温度跳变过大")
    return WeatherValidation(True, "accepted", "天气候选通过程序校验")


def materialize_weather_events(
    state: Projection,
    result: WeatherGenerationResult,
    source_event_id: str,
) -> list[Event]:
    if not source_event_id:
        return []
    policy = weather_policy_for_scenario(state.scenario_id)
    events: list[Event] = []
    seen_dates = set(state.weather_by_date)
    for accepted in result.accepted:
        day = accepted.day
        candidate = accepted.candidate
        if day.date_key in seen_dates:
            continue
        constraint = policy.authored_constraints.get(day.date_key)
        events.append(Event(
            event_id=f"evt_{uuid4().hex}",
            event_type="world.weather_determined",
            actor_id="system",
            world_time=state.world_time,
            payload={
                "weatherId": _weather_id(day),
                "dateKey": day.date_key,
                "era": day.era,
                "year": day.year,
                "month": day.month,
                "day": day.day,
                "season": day.season,
                "seasonName": day.season_name,
                "climateId": policy.climate_id,
                "climateName": policy.climate_name,
                "climateSourceStatus": policy.climate_source_status,
                "condition": candidate.condition,
                "conditionName": WEATHER_CONDITION_NAMES[candidate.condition],
                "lowTemperatureC": candidate.low_temperature_c,
                "highTemperatureC": candidate.high_temperature_c,
                "effectiveFromWorldTime": day.effective_world_time,
                "allowedConditions": list(day.allowed_conditions),
                "minimumTemperatureC": day.minimum_temperature_c,
                "maximumTemperatureC": day.maximum_temperature_c,
                "sourceEventId": source_event_id,
                "sourceRefs": list(policy.source_refs),
                "constraintSourceRefs": (
                    list(constraint.source_refs) if constraint is not None else []
                ),
                "generation": {
                    "source": accepted.generation_source,
                    "auditStatus": result.audit.status,
                    "providerName": result.audit.provider_name,
                    "modelName": result.audit.model_name,
                    "failureCode": accepted.failure_code,
                    "rejected": list(result.rejected),
                    "metrics": {
                        "promptTokens": result.audit.metrics.prompt_tokens,
                        "completionTokens": result.audit.metrics.completion_tokens,
                        "totalTokens": result.audit.metrics.total_tokens,
                        "latencyMs": result.audit.metrics.latency_ms,
                    },
                },
                "summary": weather_summary(candidate),
            },
            schema_version=1,
        ))
        seen_dates.add(day.date_key)
    return events


def weather_summary(candidate: WeatherCandidate) -> str:
    condition_name = WEATHER_CONDITION_NAMES[candidate.condition]
    return (
        f"{condition_name}，最低 {candidate.low_temperature_c}°C，"
        f"最高 {candidate.high_temperature_c}°C。"
    )


def _validate_proposal(
    days: tuple[WeatherDayContext, ...],
    proposal: WeatherProposal,
    previous: WeatherCandidate | None,
) -> tuple[tuple[AcceptedWeatherCandidate, ...], tuple[dict[str, str], ...]]:
    requested = {day.date_key: day for day in days}
    proposed: dict[str, WeatherCandidate] = {}
    rejected: list[dict[str, str]] = []
    for candidate in proposal.candidates:
        if candidate.date_key not in requested:
            rejected.append({"dateKey": candidate.date_key, "reason": "unrequested_date"})
            continue
        if candidate.date_key in proposed:
            rejected.append({"dateKey": candidate.date_key, "reason": "duplicate_date"})
            continue
        proposed[candidate.date_key] = candidate

    accepted: list[AcceptedWeatherCandidate] = []
    prior = previous
    for day in days:
        candidate = proposed.get(day.date_key)
        if candidate is None:
            fallback = fallback_weather_candidate(day, prior)
            accepted.append(AcceptedWeatherCandidate(
                day,
                fallback,
                "program_fallback",
                "missing_candidate",
            ))
            rejected.append({"dateKey": day.date_key, "reason": "missing_candidate"})
            prior = fallback
            continue
        validation = validate_weather_candidate(day, candidate, prior)
        if validation.accepted:
            accepted.append(AcceptedWeatherCandidate(day, candidate, "ai"))
            prior = candidate
            continue
        fallback = fallback_weather_candidate(day, prior)
        accepted.append(AcceptedWeatherCandidate(
            day,
            fallback,
            "program_fallback",
            validation.code,
        ))
        rejected.append({"dateKey": day.date_key, "reason": validation.code})
        prior = fallback
    return tuple(accepted), tuple(rejected)


def _fallback_days(
    days: tuple[WeatherDayContext, ...],
    previous: WeatherCandidate | None,
    *,
    failure_code: str = "model_disabled",
) -> tuple[AcceptedWeatherCandidate, ...]:
    accepted: list[AcceptedWeatherCandidate] = []
    prior = previous
    for day in days:
        candidate = fallback_weather_candidate(day, prior)
        accepted.append(AcceptedWeatherCandidate(
            day,
            candidate,
            "program_fallback",
            failure_code,
        ))
        prior = candidate
    return tuple(accepted)


def fallback_weather_candidate(
    day: WeatherDayContext,
    previous: WeatherCandidate | None = None,
) -> WeatherCandidate:
    digest = sha256(
        f"weather-schema-1:{day.date_key}:{day.season}".encode("utf-8")
    ).digest()
    condition = day.required_condition or day.allowed_conditions[
        digest[0] % len(day.allowed_conditions)
    ]
    minimum = day.minimum_temperature_c
    maximum = day.maximum_temperature_c
    if condition in {"light_snow", "snow", "heavy_snow"}:
        maximum = min(maximum, 4)
    elif condition == "sleet":
        maximum = min(maximum, 7)
    elif condition == "thunderstorm":
        minimum = max(minimum, 8)
    span_limit = max(0, min(10, maximum - minimum))
    span = min(span_limit, 3 + digest[1] % 6) if span_limit else 0
    low_ceiling = maximum - span
    low = minimum + digest[2] % (low_ceiling - minimum + 1)
    high = low + span
    if condition == "sleet":
        high = min(high, 7)
        low = min(low, 3, high)
    if previous is not None:
        low = max(previous.low_temperature_c - 14, min(low, previous.low_temperature_c + 14))
        high = max(low, max(previous.high_temperature_c - 14, min(high, previous.high_temperature_c + 14)))
        high = min(high, maximum)
        low = min(low, high)
    if condition in {"light_snow", "snow", "heavy_snow"}:
        high = min(high, 4)
        low = min(low, high)
    elif condition == "sleet":
        high = min(high, 7)
        low = min(low, 3, high)
    elif condition == "thunderstorm":
        high = max(high, 10)
        low = min(low, high)
    return WeatherCandidate(
        dateKey=day.date_key,
        condition=condition,
        lowTemperatureC=low,
        highTemperatureC=high,
    )


def _latest_weather_candidate(state: Projection) -> WeatherCandidate | None:
    existing = [
        weather
        for weather in state.weather_by_date.values()
        if int(weather.get("effectiveFromWorldTime", 0)) <= state.world_time
    ]
    if not existing:
        return None
    latest = max(
        existing,
        key=lambda weather: (
            int(weather.get("effectiveFromWorldTime", 0)),
            str(weather.get("dateKey", "")),
        ),
    )
    return WeatherCandidate(
        dateKey=str(latest["dateKey"]),
        condition=str(latest["condition"]),
        lowTemperatureC=int(latest["lowTemperatureC"]),
        highTemperatureC=int(latest["highTemperatureC"]),
    )


def _weather_system_instruction() -> str:
    return (
        "你是每日天气候选生成器。只为请求中的每个日期各返回一条 WeatherCandidate；"
        "只能使用该日期 allowedConditions 中的值，温度必须是范围内的整数且最低温不高于最高温。"
        "requiredCondition 非空时必须原样采用。不要添加叙述、人物、地点、剧情、灾害后果、"
        "穿着影响或移动影响。候选不是事实，程序会再次校验并由确认事件决定是否生效。"
    )


def _weather_id(day: WeatherDayContext) -> str:
    return f"weather_{day.year}_{day.month:02d}_{day.day:02d}"


def _calendar_ordinal(
    year: int,
    month: int,
    day: int,
    days_per_month: int,
    months_per_year: int,
) -> int:
    return (
        (year * months_per_year + month - 1) * days_per_month
        + day
        - 1
    )


def _date_from_ordinal(
    ordinal: int,
    days_per_month: int,
    months_per_year: int,
) -> tuple[int, int, int]:
    month_ordinal, day_index = divmod(ordinal, days_per_month)
    year, month_index = divmod(month_ordinal, months_per_year)
    return year, month_index + 1, day_index + 1


__all__ = [
    "AcceptedWeatherCandidate",
    "DisabledWeatherAdapter",
    "SafeWeatherDirector",
    "SeasonWeatherRule",
    "TEMPERATE_FOUR_SEASON_RULES",
    "WeatherAdapter",
    "WeatherAdapterResult",
    "WeatherCallMetrics",
    "WeatherCandidate",
    "WeatherDayContext",
    "WeatherGenerationAudit",
    "WeatherGenerationRequest",
    "WeatherGenerationResult",
    "WeatherPolicy",
    "WeatherProposal",
    "WeatherValidation",
    "calendar_date_at",
    "fallback_weather_candidate",
    "materialize_weather_events",
    "season_for_month",
    "validate_weather_candidate",
    "weather_days_for_window",
    "weather_policy_for_scenario",
    "weather_summary",
]
