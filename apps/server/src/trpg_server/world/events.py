from __future__ import annotations

from trpg_server.core.state import ClockState, EffectState, Event, ObligationState, Projection, StoryConditionState
from trpg_server.core.projection_handlers import projection_handlers
from trpg_server.world.weather import (
    SeasonWeatherRule,
    WeatherCandidate,
    WeatherDayContext,
    validate_weather_candidate,
)


@projection_handlers.register("effect.applied")
def apply_effect_applied(state: Projection, event: Event) -> None:
    payload = event.payload
    effect = EffectState(
        effect_id=payload["effectId"],
        effect_type=payload["effectType"],
        subject_id=payload["subjectId"],
        object_id=payload.get("objectId"),
        scope_id=payload.get("scopeId"),
        value=payload["value"],
        source_event_id=payload["sourceEventId"],
        created_at=event.world_time,
        expires_at=payload.get("expiresAt"),
        status="active",
    )
    state.effects[effect.effect_id] = effect


@projection_handlers.register("effect.expired", "effect.consumed")
def apply_effect_closed(state: Projection, event: Event) -> None:
    payload = event.payload
    effect = state.effects.get(payload["effectId"])
    if effect is not None and effect.status == "active":
        state.effects[effect.effect_id] = EffectState(
            effect.effect_id,
            effect.effect_type,
            effect.subject_id,
            effect.object_id,
            effect.scope_id,
            effect.value,
            effect.source_event_id,
            effect.created_at,
            effect.expires_at,
            "expired" if event.event_type == "effect.expired" else "consumed",
        )


@projection_handlers.register("clue.defined")
def apply_clue_defined(state: Projection, event: Event) -> None:
    payload = event.payload
    state.clue_definitions[payload["clueId"]] = {
        "factId": payload["factId"],
        "title": payload["title"],
        "description": payload["description"],
    }


@projection_handlers.register("story.clue_revealed")
def apply_story_clue_revealed(state: Projection, event: Event) -> None:
    payload = event.payload
    state.clues[payload["clueId"]] = {
        "title": payload["title"],
        "description": payload["description"],
        "sourceEventId": payload["sourceEventId"],
    }


@projection_handlers.register("world.fact_defined")
def apply_world_fact_defined(state: Projection, event: Event) -> None:
    payload = event.payload
    state.world_facts[payload["factId"]] = {
        "statement": payload["statement"],
        "truthState": payload["truthState"],
        "visibility": payload["visibility"],
        "tags": tuple(payload.get("tags", [])),
        "sourceEventId": event.event_id,
    }


@projection_handlers.register("clock.created")
def apply_clock_created(state: Projection, event: Event) -> None:
    payload = event.payload
    clock = ClockState(
        clock_id=payload["clockId"],
        name=payload["name"],
        starts_at=payload["startsAt"],
        deadline_at=payload["deadlineAt"],
        status=payload["status"],
        visibility=payload["visibility"],
        stakes=payload.get("stakes", ""),
    )
    state.clocks[clock.clock_id] = clock


@projection_handlers.register("obligation.created")
def apply_obligation_created(state: Projection, event: Event) -> None:
    payload = event.payload
    obligation = ObligationState(
        obligation_id=payload["obligationId"],
        title=payload["title"],
        kind=payload["kind"],
        debtor_id=payload["debtorId"],
        creditor_id=payload["creditorId"],
        status=payload["status"],
        terms=payload["terms"],
        due_clock_id=payload.get("dueClockId"),
        evidence_fact_ids=tuple(payload.get("evidenceFactIds", [])),
        visibility=payload["visibility"],
    )
    state.obligations[obligation.obligation_id] = obligation


@projection_handlers.register("story.condition_defined")
def apply_condition_defined(state: Projection, event: Event) -> None:
    payload = event.payload
    condition = StoryConditionState(
        condition_id=payload["conditionId"],
        name=payload["name"],
        active=payload.get("active", False),
        visibility=payload.get("visibility", "gm"),
    )
    state.story_conditions[condition.condition_id] = condition


@projection_handlers.register("story.condition_activated", "story.condition_deactivated")
def apply_condition_status(state: Projection, event: Event) -> None:
    condition = state.story_conditions[event.payload["conditionId"]]
    condition.active = event.event_type == "story.condition_activated"


@projection_handlers.register("world.daily_settled", "world.weekly_settled", "world.monthly_settled")
def apply_world_settlement(state: Projection, event: Event) -> None:
    payload = event.payload
    settlement_id = str(payload["settlementId"])
    state.world_settlements[settlement_id] = {
        **dict(payload),
        "eventId": event.event_id,
        "eventType": event.event_type,
    }


@projection_handlers.register("world.weather_determined")
def apply_world_weather_determined(state: Projection, event: Event) -> None:
    if event.schema_version != 1:
        raise ValueError(
            f"unsupported world.weather_determined schema version: {event.schema_version}"
        )
    payload = event.payload
    date_key = str(payload["dateKey"])
    if date_key in state.weather_by_date:
        raise ValueError(f"weather already determined for date: {date_key}")
    source_event_id = str(payload.get("sourceEventId", ""))
    if not source_event_id:
        raise ValueError("weather requires sourceEventId")
    if source_event_id == event.event_id or source_event_id not in state.confirmed_event_ids:
        raise ValueError("weather requires an earlier confirmed source event")
    allowed = tuple(str(value) for value in payload.get("allowedConditions", ()))
    if not allowed:
        from trpg_server.world.weather import TEMPERATE_FOUR_SEASON_RULES

        rule: SeasonWeatherRule = TEMPERATE_FOUR_SEASON_RULES[str(payload["season"])]
        allowed = rule.allowed_conditions
        minimum = rule.minimum_temperature_c
        maximum = rule.maximum_temperature_c
    else:
        minimum = int(payload["minimumTemperatureC"])
        maximum = int(payload["maximumTemperatureC"])
    day = WeatherDayContext(
        dateKey=date_key,
        era=str(payload["era"]),
        year=int(payload["year"]),
        month=int(payload["month"]),
        day=int(payload["day"]),
        season=str(payload["season"]),
        seasonName=str(payload["seasonName"]),
        effectiveWorldTime=int(payload["effectiveFromWorldTime"]),
        allowedConditions=allowed,
        minimumTemperatureC=minimum,
        maximumTemperatureC=maximum,
        requiredCondition=None,
    )
    candidate = WeatherCandidate(
        dateKey=date_key,
        condition=str(payload["condition"]),
        lowTemperatureC=int(payload["lowTemperatureC"]),
        highTemperatureC=int(payload["highTemperatureC"]),
    )
    validation = validate_weather_candidate(day, candidate)
    if not validation.accepted:
        raise ValueError(f"invalid confirmed weather: {validation.code}")
    state.weather_by_date[date_key] = {
        **dict(payload),
        "eventId": event.event_id,
        "eventWorldTime": event.world_time,
    }


@projection_handlers.register("market.price_changed")
def apply_market_price_changed(state: Projection, event: Event) -> None:
    payload = event.payload
    state.market_prices[str(payload["marketKey"])] = int(payload["price"])


@projection_handlers.register("market.inventory_refreshed")
def apply_market_inventory_refreshed(state: Projection, event: Event) -> None:
    payload = event.payload
    state.market_inventory[str(payload["marketKey"])] = int(payload["quantity"])


@projection_handlers.register("work.opportunity_defined")
def apply_work_opportunity_defined(state: Projection, event: Event) -> None:
    payload = event.payload
    state.work_opportunities[str(payload["opportunityId"])] = {
        **dict(payload),
        "sourceEventId": event.event_id,
    }


@projection_handlers.register("organization.plan_changed")
def apply_organization_plan_changed(state: Projection, event: Event) -> None:
    payload = event.payload
    state.organization_plans[str(payload["organizationId"])] = {
        **dict(payload),
        "sourceEventId": event.event_id,
    }


@projection_handlers.register("commerce.offer_found")
def apply_commerce_offer_found(state: Projection, event: Event) -> None:
    if event.schema_version != 1:
        raise ValueError(f"unsupported commerce.offer_found schema version: {event.schema_version}")
    payload = event.payload
    offer_id = str(payload["offerId"])
    previous = state.commerce_offers.get(offer_id)
    offer = {
        **dict(payload),
        "sourceEventId": payload.get("sourceEventId", event.event_id),
        "eventId": event.event_id,
    }
    if previous is not None and "availableQuantity" in previous:
        offer["availableQuantity"] = min(
            int(payload.get("availableQuantity", 0)),
            int(previous["availableQuantity"]),
        )
    state.commerce_offers[offer_id] = offer


@projection_handlers.register("commerce.completed")
def apply_commerce_completed(state: Projection, event: Event) -> None:
    if event.schema_version != 1:
        raise ValueError(f"unsupported commerce.completed schema version: {event.schema_version}")
    payload = event.payload
    state.commerce_transactions[str(payload["transactionId"])] = {
        **dict(payload),
        "eventId": event.event_id,
    }
    offer = state.commerce_offers.get(str(payload.get("offerId")))
    if offer is not None:
        offer["availableQuantity"] = max(
            0,
            int(offer.get("availableQuantity", 0)) - int(payload.get("quantity", 0)),
        )
