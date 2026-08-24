from __future__ import annotations

from trpg_server.core.state import (
    CalendarState,
    Event,
    ExitState,
    LocationState,
    OrganizationState,
    ObservedAffordanceState,
    Projection,
    CatalogAffordanceState,
    CatalogEntryState,
)
from trpg_server.core.projection_handlers import projection_handlers


@projection_handlers.register("campaign.created")
def apply_campaign_created(state: Projection, event: Event) -> None:
    payload = event.payload
    state.name = payload["name"]
    state.scenario_id = payload.get("scenarioId")
    state.scenario_version = payload.get("scenarioVersion")
    state.scenario_content_hash = payload.get("scenarioContentHash")
    state.scenario_source_version = payload.get("scenarioSourceVersion")
    state.scenario_source_document = payload.get("scenarioSourceDocument")
    state.scenario_source_sha256 = payload.get("scenarioSourceSha256")
    state.scenario_catalog_schema_version = payload.get(
        "scenarioCatalogSchemaVersion"
    )
    state.player_character_id = payload.get("playerCharacterId", "player")
    state.world_time = event.world_time
    if "initialBalances" in payload:
        raise ValueError(
            "campaign.created no longer supports initialBalances; "
            "create physical currency item instances in owned containers instead"
        )
    calendar = payload.get("initialCalendar")
    if calendar is not None:
        state.calendar = CalendarState(
            era=calendar["era"],
            year=calendar["year"],
            month=calendar["month"],
            day=calendar["day"],
            hour=calendar["hour"],
            minute=calendar["minute"],
            origin_world_time=event.world_time,
            days_per_month=calendar.get("daysPerMonth", 30),
            months_per_year=calendar.get("monthsPerYear", 12),
        )


@projection_handlers.register("location.created")
def apply_location_created(state: Projection, event: Event) -> None:
    if event.schema_version not in {1, 2}:
        raise ValueError(
            f"unsupported location.created schema version: {event.schema_version}"
        )
    payload = event.payload
    state.location_names[payload["locationId"]] = payload["name"]
    state.locations[payload["locationId"]] = LocationState(
        location_id=payload["locationId"],
        name=payload["name"],
        aliases=tuple(payload.get("aliases", [])),
        kind=payload.get("kind", "area"),
        map_visibility=(
            payload.get("mapVisibility", "public")
            if event.schema_version == 2
            else "public"
        ),
        parent_id=payload.get("parentId"),
        description=payload.get("description", ""),
        exits=tuple(
            ExitState(
                exit_id=value.get(
                    "id",
                    f"{payload['locationId']}->{value['toLocationId']}",
                ),
                to_location_id=value["toLocationId"],
                label=value.get("label", ""),
                travel_minutes=value.get("travelMinutes", 1),
                visible=value.get("visible", True),
                locked=value.get("locked", False),
                key_item_ids=tuple(value.get("keyItemIds", [])),
                required_condition_ids=tuple(
                    value.get("requiredConditionIds", [])
                ),
                discovery_id=value.get("discoveryId"),
            )
            for value in payload.get("exits", [])
        ),
    )


@projection_handlers.register("location.exits_extended")
def apply_location_exits_extended(state: Projection, event: Event) -> None:
    payload = event.payload
    location = state.locations.get(payload["locationId"])
    if location is None:
        raise ValueError("cannot extend exits for an unknown location")
    existing = {value.exit_id for value in location.exits}
    additions = []
    for value in payload.get("exits", []):
        exit_state = ExitState(
            exit_id=value["id"],
            to_location_id=value["toLocationId"],
            label=value.get("label", ""),
            travel_minutes=value.get("travelMinutes", 1),
            visible=value.get("visible", True),
            locked=value.get("locked", False),
            key_item_ids=tuple(value.get("keyItemIds", [])),
            required_condition_ids=tuple(value.get("requiredConditionIds", [])),
            discovery_id=value.get("discoveryId"),
        )
        if exit_state.exit_id not in existing:
            additions.append(exit_state)
    if additions:
        state.locations[location.location_id] = LocationState(
            location.location_id,
            location.name,
            location.aliases,
            location.kind,
            location.map_visibility,
            location.parent_id,
            location.description,
            (*location.exits, *additions),
        )


@projection_handlers.register("organization.created")
def apply_organization_created(state: Projection, event: Event) -> None:
    payload = event.payload
    organization = OrganizationState(
        organization_id=payload["organizationId"],
        name=payload["name"],
        organization_type=payload["organizationType"],
        visibility=payload.get("visibility", "public"),
        headquarters_location_id=payload.get("headquartersLocationId"),
        leader_character_ids=tuple(payload.get("leaderCharacterIds", [])),
        member_character_ids=tuple(payload.get("memberCharacterIds", [])),
        public_description=payload.get("publicDescription", ""),
        private_goals=tuple(payload.get("privateGoals", [])),
        resource_tags=tuple(payload.get("resourceTags", [])),
        policy_tags=tuple(payload.get("policyTags", [])),
    )
    state.organizations[organization.organization_id] = organization


@projection_handlers.register("character.moved")
def apply_character_moved(state: Projection, event: Event) -> None:
    if event.schema_version not in {1, 2}:
        raise ValueError(
            f"unsupported character.moved schema version: {event.schema_version}"
        )
    if event.schema_version == 2:
        _validate_character_moved_v2(state, event)
    state.character_locations[event.payload["characterId"]] = event.payload[
        "toLocationId"
    ]


def _validate_character_moved_v2(state: Projection, event: Event) -> None:
    payload = event.payload
    base_minutes = int(payload["baseTravelMinutes"])
    delay_minutes = int(payload["weatherDelayMinutes"])
    travel_minutes = int(payload["travelMinutes"])
    multiplier = int(payload["weatherMultiplierPercent"])
    if min(base_minutes, delay_minutes, travel_minutes, multiplier) < 0:
        raise ValueError("character.moved travel values cannot be negative")
    if travel_minutes != base_minutes + delay_minutes:
        raise ValueError("character.moved travel minutes do not add up")
    if delay_minutes != (base_minutes * multiplier + 50) // 100:
        raise ValueError("character.moved weather delay does not match its multiplier")

    weather_event_id = payload.get("weatherEventId")
    weather_condition = payload.get("weatherCondition")
    if weather_event_id is None:
        if weather_condition is not None or multiplier or delay_minutes:
            raise ValueError("character.moved weather fields require a source event")
        return
    weather_event_id = str(weather_event_id)
    if (
        weather_event_id not in state.confirmed_event_ids
        or state.event_types_by_id.get(weather_event_id) != "world.weather_determined"
    ):
        raise ValueError("character.moved requires a confirmed weather source event")
    if not any(
        weather.get("eventId") == weather_event_id
        and weather.get("condition") == weather_condition
        for weather in state.weather_by_date.values()
    ):
        raise ValueError("character.moved weather source does not match its condition")


@projection_handlers.register("time.advanced")
def apply_time_advanced(state: Projection, event: Event) -> None:
    state.world_time = event.payload["to"]


@projection_handlers.register("affordance.observed")
def apply_affordance_observed(state: Projection, event: Event) -> None:
    payload = event.payload
    affordance = ObservedAffordanceState(
        opportunity_id=payload["opportunityId"],
        location_id=payload["locationId"],
        action_kind=payload["actionKind"],
        resource_kind=payload["resourceKind"],
        source_policy=payload["sourcePolicy"],
        story_impact_ceiling=payload.get("storyImpactCeiling", "soft"),
        observed_at=event.world_time,
        source_event_id=event.event_id,
    )
    state.observed_affordances[affordance.opportunity_id] = affordance


@projection_handlers.register("catalog.entry_defined")
def apply_catalog_entry_defined(state: Projection, event: Event) -> None:
    payload = event.payload
    entry = CatalogEntryState(
        entry_id=payload["entryId"],
        title=payload["title"],
        kind=payload["kind"],
        canon_layer=payload["canonLayer"],
        fact_status=payload["factStatus"],
        instantiated=bool(payload.get("instantiated", False)),
        source_refs=tuple(payload.get("sourceRefs", [])),
        attributes=dict(payload.get("attributes", {})),
    )
    state.catalog_entries[entry.entry_id] = entry


@projection_handlers.register("catalog.affordance_defined")
def apply_catalog_affordance_defined(state: Projection, event: Event) -> None:
    payload = event.payload
    affordance = CatalogAffordanceState(
        affordance_id=payload["affordanceId"],
        location_id=payload["locationId"],
        action_kinds=tuple(payload.get("actionKinds", [])),
        resource_categories=tuple(payload.get("resourceCategories", [])),
        story_impact_ceiling=payload.get("storyImpactCeiling", "soft"),
        temporary_entity_kinds=tuple(payload.get("temporaryEntityKinds", [])),
        canon_layer=payload.get("canonLayer", "C2"),
        source_refs=tuple(payload.get("sourceRefs", [])),
    )
    state.catalog_affordances[affordance.affordance_id] = affordance
