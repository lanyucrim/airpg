from __future__ import annotations

from collections.abc import Iterable

from trpg_server.core.state import (
    CalendarState,
    CognitionState,
    ClockState,
    DiscoveryState,
    EffectState,
    Event,
    ExitState,
    InquiryState,
    InspectionState,
    LocationState,
    ObligationState,
    OrganizationState,
    SceneIssueState,
    Projection,
    StoryConditionState,
    WantedState,
)
from trpg_server.story.investigation import evaluate_inquiry, evaluate_inspection
from trpg_server.items.inventory import item_public_summary
from trpg_server.locations.movement import exit_is_visible_to
from trpg_server.locations.weather_travel import estimate_exit_travel_time
from trpg_server.map.traversal import map_exit_is_allowed
from trpg_server.map.public import public_map as build_public_map
from trpg_server.core import events_projection as _core_events
from trpg_server.characters import events as _character_events
from trpg_server.characters import cognition_events as _cognition_events
from trpg_server.characters import social_events as _social_events
from trpg_server.items import events as _item_events
from trpg_server.items import interaction_events as _item_interaction_events
from trpg_server.items import wear_events as _item_wear_events
from trpg_server.story import investigation_events as _investigation_events
from trpg_server.story import scene_events as _scene_events
from trpg_server.world import events as _world_events
from trpg_server.world import legal_events as _legal_events
from trpg_server.core.projection_handlers import projection_handlers


def replay(campaign_id: str, events: Iterable[Event], state_version: int) -> Projection:
    state = Projection(campaign_id=campaign_id, state_version=state_version)
    for event in events:
        apply_event(state, event)
    return state


def apply_event(state: Projection, event: Event) -> None:
    _validate_consequence_source(state, event)
    payload = event.payload
    state.confirmed_event_ids.add(event.event_id)
    state.event_types_by_id[event.event_id] = event.event_type
    state.event_times_by_id[event.event_id] = event.world_time
    state.event_actors_by_id[event.event_id] = event.actor_id

    # New domains register projection handlers incrementally. The legacy
    # branches below remain as a replay compatibility fallback until each
    # event family has completed its migration gate.
    handler = projection_handlers.handler_for(event.event_type)
    if handler is not None:
        handler(state, event)
        return

    if event.event_type == "campaign.created":
        state.name = payload["name"]
        state.scenario_id = payload.get("scenarioId")
        state.scenario_version = payload.get("scenarioVersion")
        state.scenario_content_hash = payload.get("scenarioContentHash")
        state.player_character_id = payload.get("playerCharacterId", "player")
        state.world_time = event.world_time
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
        return

    if event.event_type == "location.created":
        if event.schema_version not in {1, 2}:
            raise ValueError(
                f"unsupported location.created schema version: {event.schema_version}"
            )
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
        return

    if event.event_type == "organization.created":
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
        return

    if event.event_type == "character.moved":
        state.character_locations[payload["characterId"]] = payload["toLocationId"]
        return

    if event.event_type == "scene.started":
        if event.schema_version not in {1, 2}:
            raise ValueError(
                f"unsupported scene.started schema version: {event.schema_version}"
            )
        state.scene_id = payload["sceneId"]
        state.location_id = payload["locationId"]
        state.scene_phase = payload.get("phase", "exploration")
        state.scene_title = payload.get("title", "")
        state.scene_objective = payload.get("objective", "")
        state.scene_opening_text = payload.get("openingText", "")
        state.scene_present_character_ids = tuple(payload.get("presentCharacterIds", []))
        guidance = payload.get("narrativeGuidance") or {}
        state.scene_narrative_premise = guidance.get("premise", "")
        state.scene_narrative_anchors = tuple(guidance.get("hardAnchors", []))
        state.scene_flexible_approaches = tuple(
            guidance.get("flexibleApproaches", [])
        )
        state.scene_stop_before = tuple(guidance.get("stopBefore", []))
        state.max_major_beats_per_turn = payload.get("maxMajorBeatsPerTurn", 1)
        return

    if event.event_type == "scene.beat_advanced":
        state.scene_beat += payload.get("beats", 1)
        return

    if event.event_type == "scene.issue_opened":
        issue = SceneIssueState(
            issue_id=payload["issueId"], title=payload["title"], status="open",
            source_event_id=payload["sourceEventId"], created_at=event.world_time,
            ends_at=payload.get("endsAt"),
        )
        state.scene_issues[issue.issue_id] = issue
        return

    if event.event_type == "scene.issue_resolved":
        issue = state.scene_issues.get(payload["issueId"])
        if issue is not None:
            state.scene_issues[issue.issue_id] = SceneIssueState(
                issue.issue_id, issue.title, "resolved", issue.source_event_id,
                issue.created_at, issue.ends_at,
            )
        return

    if event.event_type == "world.reported":
        state.world_reports.append(dict(payload))
        return

    if event.event_type == "scene.location_changed":
        state.location_id = payload["toLocationId"]
        return

    if event.event_type == "relationship.changed":
        relationship = state.relationship(payload["subjectId"], payload["objectId"])
        dimension = payload["dimension"]
        current_value = getattr(relationship, dimension)
        setattr(relationship, dimension, current_value + payload["delta"])
        relationship.sources.setdefault(dimension, []).append(payload["sourceEventId"])
        return

    if event.event_type == "relationship.initialized":
        relationship = state.relationship(payload["subjectId"], payload["objectId"])
        for dimension, value in payload["dimensions"].items():
            setattr(relationship, dimension, value)
            if value:
                relationship.sources.setdefault(dimension, []).append(event.event_id)
        return

    if event.event_type == "gift.accepted":
        state.accepted_gifts.append((
            payload["actorId"],
            payload["targetId"],
            payload["itemId"],
            event.event_id,
        ))
        return

    if event.event_type == "knowledge.learned":
        state.knowledge.setdefault(payload["characterId"], set()).add(payload["factId"])
        cognition = CognitionState(
            character_id=payload["characterId"],
            proposition_id=payload["factId"],
            status="known",
            source_event_id=payload.get("sourceEventId", event.event_id),
            source_kind="system",
            acquired_at=event.world_time,
        )
        state.cognitions[(payload["characterId"], payload["factId"])] = cognition
        state.cognition_history.append(cognition)
        return

    if event.event_type == "npc.cognition_changed":
        if event.schema_version != 1:
            raise ValueError(f"unsupported npc.cognition_changed schema version: {event.schema_version}")
        cognition = CognitionState(
            character_id=payload["characterId"],
            proposition_id=payload["propositionId"],
            status=payload["status"],
            source_event_id=payload["sourceEventId"],
            source_kind=payload["sourceKind"],
            acquired_at=event.world_time,
            scope_id=payload.get("scopeId"),
            confidence=payload.get("confidence", 100),
            expires_at=payload.get("expiresAt"),
        )
        state.cognitions[(cognition.character_id, cognition.proposition_id)] = cognition
        state.cognition_history.append(cognition)
        if cognition.status == "known":
            state.knowledge.setdefault(cognition.character_id, set()).add(cognition.proposition_id)
        else:
            state.knowledge.setdefault(cognition.character_id, set()).discard(cognition.proposition_id)
        return

    if event.event_type == "npc.cognition_expired":
        key = (payload["characterId"], payload["propositionId"])
        current = state.cognitions.get(key)
        if current is not None and current.source_event_id == payload["cognitionSourceEventId"]:
            state.cognitions.pop(key, None)
            state.knowledge.setdefault(payload["characterId"], set()).discard(
                payload["propositionId"]
            )
        return

    if event.event_type == "effect.applied":
        effect = EffectState(
            effect_id=payload["effectId"], effect_type=payload["effectType"],
            subject_id=payload["subjectId"], object_id=payload.get("objectId"),
            scope_id=payload.get("scopeId"), value=payload["value"],
            source_event_id=payload["sourceEventId"], created_at=event.world_time,
            expires_at=payload.get("expiresAt"), status="active",
        )
        state.effects[effect.effect_id] = effect
        return

    if event.event_type in {"effect.expired", "effect.consumed"}:
        effect = state.effects.get(payload["effectId"])
        if effect is not None and effect.status == "active":
            state.effects[effect.effect_id] = EffectState(
                effect.effect_id, effect.effect_type, effect.subject_id,
                effect.object_id, effect.scope_id, effect.value,
                effect.source_event_id, effect.created_at, effect.expires_at,
                "expired" if event.event_type == "effect.expired" else "consumed",
            )
        return

    if event.event_type == "npc.attitude_changed":
        if event.schema_version != 1:
            raise ValueError(
                f"unsupported npc.attitude_changed schema version: {event.schema_version}"
            )
        relationship = state.relationship(payload["characterId"], payload["subjectId"])
        dimension = payload["dimension"]
        setattr(relationship, dimension, getattr(relationship, dimension) + int(payload["delta"]))
        relationship.sources.setdefault(dimension, []).append(event.event_id)
        return

    if event.event_type == "reputation.changed":
        effect = EffectState(
            effect_id=payload["reputationId"], effect_type="reputation",
            subject_id=payload["subjectId"], object_id=None,
            scope_id=payload["groupId"], value=int(payload["delta"]),
            source_event_id=payload["sourceEventId"], created_at=event.world_time,
            expires_at=payload.get("expiresAt"), status="active",
        )
        state.effects[effect.effect_id] = effect
        return

    if event.event_type == "wanted.issued":
        wanted = WantedState(
            wanted_id=payload["wantedId"], subject_id=payload["subjectId"],
            jurisdiction_id=payload["jurisdictionId"], source_event_id=payload["sourceEventId"],
            issued_at=event.world_time,
        )
        state.wanted[wanted.wanted_id] = wanted
        return

    if event.event_type in {"wanted.cleared", "wanted.expired"}:
        wanted = state.wanted.get(payload["wantedId"])
        if wanted is not None:
            state.wanted[wanted.wanted_id] = WantedState(
                wanted.wanted_id, wanted.subject_id, wanted.jurisdiction_id,
                wanted.source_event_id, wanted.issued_at,
                "cleared" if event.event_type == "wanted.cleared" else "expired",
            )
        return

    if event.event_type in {
        "crime.committed", "witness.observed", "information.reported",
        "information.withheld", "evidence.registered", "suspect.identified",
        "suspect.described",
    }:
        state.legal_records[event.event_id] = {
            "eventType": event.event_type,
            "worldTime": event.world_time,
            **dict(payload),
        }
        return

    if event.event_type == "notice.scheduled":
        state.pending_notices[payload["noticeId"]] = {
            **dict(payload), "scheduleEventId": event.event_id,
        }
        return

    if event.event_type == "notice.received":
        state.pending_notices.pop(payload["noticeId"], None)
        return

    if event.event_type == "story.clue_revealed":
        state.clues[payload["clueId"]] = {
            "title": payload["title"],
            "description": payload["description"],
            "sourceEventId": payload["sourceEventId"],
        }
        return

    if event.event_type == "clue.defined":
        state.clue_definitions[payload["clueId"]] = {
            "factId": payload["factId"],
            "title": payload["title"],
            "description": payload["description"],
        }
        return

    if event.event_type == "world.fact_defined":
        state.world_facts[payload["factId"]] = {
            "statement": payload["statement"],
            "truthState": payload["truthState"],
            "visibility": payload["visibility"],
            "tags": tuple(payload.get("tags", [])),
            "sourceEventId": event.event_id,
        }
        return

    if event.event_type == "clock.created":
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
        return

    if event.event_type == "obligation.created":
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
        return

    if event.event_type == "story.condition_defined":
        condition = StoryConditionState(
            condition_id=payload["conditionId"],
            name=payload["name"],
            active=payload.get("active", False),
            visibility=payload.get("visibility", "gm"),
        )
        state.story_conditions[condition.condition_id] = condition
        return

    if event.event_type == "story.condition_activated":
        condition = state.story_conditions[payload["conditionId"]]
        condition.active = True
        return

    if event.event_type == "story.condition_deactivated":
        condition = state.story_conditions[payload["conditionId"]]
        condition.active = False
        return

    if event.event_type == "discovery.defined":
        discovery = DiscoveryState(
            discovery_id=payload["discoveryId"],
            location_id=payload["locationId"],
            aliases=tuple(payload.get("aliases", [])),
            fact_id=payload["factId"],
            clue_id=payload["clueId"],
            exit_ids=tuple(payload.get("exitIds", [])),
            required_condition_ids=tuple(
                payload.get("requiredConditionIds", [])
            ),
            initially_known_by=tuple(payload.get("initiallyKnownBy", [])),
            time_minutes=payload.get("timeMinutes", 10),
            reveal_text=payload["revealText"],
        )
        state.discovery_definitions[discovery.discovery_id] = discovery
        return

    if event.event_type == "location.exit_discovered":
        state.discovered_exits.setdefault(payload["characterId"], set()).add(
            payload["exitId"]
        )
        return

    if event.event_type == "inspection.defined":
        definition = InspectionState(
            interaction_id=payload["interactionId"],
            label=payload["label"],
            suggested_prompt=payload["suggestedPrompt"],
            target_item_id=payload["targetItemId"],
            aliases=tuple(payload.get("aliases", [])),
            access_policy=payload["accessPolicy"],
            required_actor_knowledge_fact_ids=tuple(
                payload.get("requiredActorKnowledgeFactIds", [])
            ),
            revealed_fact_ids=tuple(payload.get("revealedFactIds", [])),
            clue_ids=tuple(payload.get("clueIds", [])),
            time_minutes=payload["timeMinutes"],
            reveal_text=payload["revealText"],
            repeat_text=payload["repeatText"],
        )
        state.inspection_definitions[definition.interaction_id] = definition
        return

    if event.event_type == "inquiry.defined":
        definition = InquiryState(
            interaction_id=payload["interactionId"],
            label=payload["label"],
            suggested_prompt=payload["suggestedPrompt"],
            target_character_id=payload["targetCharacterId"],
            topic=payload["topic"],
            aliases=tuple(payload.get("aliases", [])),
            required_actor_knowledge_fact_ids=tuple(
                payload.get("requiredActorKnowledgeFactIds", [])
            ),
            required_npc_knowledge_fact_ids=tuple(
                payload.get("requiredNpcKnowledgeFactIds", [])
            ),
            revealed_fact_ids=tuple(payload.get("revealedFactIds", [])),
            clue_ids=tuple(payload.get("clueIds", [])),
            time_minutes=payload["timeMinutes"],
            response_text=payload["responseText"],
            repeat_text=payload["repeatText"],
            unknown_text=payload["unknownText"],
        )
        state.inquiry_definitions[definition.interaction_id] = definition
        return

    if event.event_type == "interaction.completed":
        state.completed_interactions.setdefault(payload["characterId"], set()).add(
            payload["interactionId"]
        )
        return

    if event.event_type == "time.advanced":
        state.world_time = payload["to"]


def public_state(state: Projection) -> dict[str, object]:
    player_containers = {
        container.container_id
        for container in state.containers.values()
        if container.owner_character_id == state.player_character_id
    }
    player_inventory = [
        {
            "itemId": item.item_id,
            **{
                key: value
                for key, value in item_public_summary(item).items()
                if key != "id"
            },
        }
        for item in state.items.values()
        if item.container_id in player_containers
    ]
    player_inventory.sort(key=lambda item: str(item["itemId"]))

    player_equipment = [
        {
            "slotId": slot_id,
            "itemId": binding.get("itemId"),
            "mode": binding.get("mode"),
            "equippedAt": binding.get("equippedAt"),
        }
        for _, binding in sorted(
            state.character_equipment.get(state.player_character_id, {}).items()
        )
        for slot_id in [binding.get("slotId")]
    ]
    player_external_injuries = [
        dict(injury)
        for _, injury in sorted(
            state.character_external_injuries.get(state.player_character_id, {}).items()
        )
    ]

    npc_ids = sorted(
        character_id
        for character_id, character_type in state.character_types.items()
        if character_type == "npc"
    )
    relationships: dict[str, dict[str, object]] = {}
    for npc_id in npc_ids:
        relationship = state.relationship(npc_id, state.player_character_id)
        relationships[npc_id] = {
            "favor": relationship.favor,
            "trust": relationship.trust,
            "fear": relationship.fear,
            "respect": relationship.respect,
            "suspicion": relationship.suspicion,
            "debt": relationship.debt,
            "sources": relationship.sources,
        }
    current_location = state.locations.get(state.location_id or "")
    map_projection = build_public_map(state)
    current_furniture = [
        {
            "containerId": container.container_id,
            "kind": container.furniture_kind,
            "name": container.furniture_name,
            "description": container.furniture_description,
            "capacityWeight": container.capacity_weight,
            "capacityVolume": container.capacity_volume,
        }
        for container in sorted(state.containers.values(), key=lambda value: value.container_id)
        if container.kind == "furniture"
        and container.location_id == state.location_id
        and container.visible
    ]
    active_clocks = [
        {
            "clockId": clock.clock_id,
            "name": clock.name,
            "deadline": clock.deadline_at,
            "remainingMinutes": max(0, clock.deadline_at - state.world_time),
            "status": clock.status,
        }
        for clock in sorted(state.clocks.values(), key=lambda value: value.clock_id)
        if clock.visibility in {"public", "player"} and clock.status in {"active", "paused"}
    ]
    obligations = [
        {
            "obligationId": obligation.obligation_id,
            "title": obligation.title,
            "kind": obligation.kind,
            "status": obligation.status,
            "dueClockId": obligation.due_clock_id,
        }
        for obligation in sorted(
            state.obligations.values(),
            key=lambda value: value.obligation_id,
        )
        if obligation.visibility in {"public", "player"}
    ]
    scene = {
        "sceneId": state.scene_id,
        "locationId": state.location_id,
        "name": state.location_names.get(state.location_id or "", state.location_id or ""),
        # ``name`` remains the authored runtime node name for replay/API
        # compatibility.  The exact player-facing breadcrumb is projected by
        # the map read model and includes the containing area and structure.
        "locationPath": map_projection.get("locationPath", []),
        "currentLocationDisplayName": map_projection.get(
            "currentLocationDisplayName"
        ),
        "currentStructureName": map_projection.get("currentStructureName"),
        "phase": state.scene_phase,
        "beat": state.scene_beat,
        "furniture": current_furniture,
        "openIssues": [
            {
                "issueId": issue.issue_id,
                "title": issue.title,
                "status": issue.status,
                "endsAt": issue.ends_at,
            }
            for issue in sorted(state.scene_issues.values(), key=lambda value: value.issue_id)
            if issue.status == "open"
        ],
    }
    if state.scene_title:
        scene["title"] = state.scene_title
    if state.scene_opening_text:
        scene["openingText"] = state.scene_opening_text
    visible_exits = []
    for exit_state in (current_location.exits if current_location else ()):
        if (
            not exit_is_visible_to(state, state.player_character_id, exit_state)
            or current_location is None
            or not map_exit_is_allowed(
                state,
                current_location.location_id,
                exit_state.to_location_id,
                exit_state,
            )
        ):
            continue
        estimate = estimate_exit_travel_time(
            state,
            state.player_character_id,
            current_location.location_id,
            exit_state,
        )
        visible_exits.append({
            "toLocationId": exit_state.to_location_id,
            "name": state.location_names.get(
                exit_state.to_location_id,
                exit_state.to_location_id,
            ),
            "label": exit_state.label,
            "travelMinutes": exit_state.travel_minutes,
            "baseTravelMinutes": estimate.base_travel_minutes,
            "weatherDelayMinutes": estimate.weather_delay_minutes,
            "estimatedTravelMinutes": estimate.travel_minutes,
            "weatherCondition": estimate.weather_condition,
            "weatherConditionName": estimate.weather_condition_name,
            "locked": exit_state.locked,
        })
    if visible_exits:
        scene["exits"] = visible_exits
    available_actions = [
        {
            "interactionId": definition.interaction_id,
            "kind": "inspect",
            "label": definition.label,
            "suggestedPrompt": definition.suggested_prompt,
        }
        for definition in sorted(
            state.inspection_definitions.values(),
            key=lambda value: value.interaction_id,
        )
        if evaluate_inspection(
            state,
            state.player_character_id,
            definition,
        ).allowed
    ]
    available_actions.extend(
        {
            "interactionId": definition.interaction_id,
            "kind": "ask",
            "label": definition.label,
            "suggestedPrompt": definition.suggested_prompt,
        }
        for definition in sorted(
            state.inquiry_definitions.values(),
            key=lambda value: value.interaction_id,
        )
        if (
            (decision := evaluate_inquiry(
                state,
                state.player_character_id,
                definition,
            )).allowed
            and decision.knows_answer
        )
    )
    current_weather = _current_weather(state)
    return {
        "campaignId": state.campaign_id,
        "name": state.name,
        "scenario": {
            "scenarioId": state.scenario_id,
            "version": state.scenario_version,
            "contentHash": state.scenario_content_hash,
            "sourceVersion": state.scenario_source_version,
            "sourceDocument": state.scenario_source_document,
            "sourceSha256": state.scenario_source_sha256,
            "catalogSchemaVersion": state.scenario_catalog_schema_version,
            "catalogRuntime": {
                "entries": len(state.catalog_entries),
                "affordances": len(state.catalog_affordances),
                "instantiatedEntries": sum(
                    1 for value in state.catalog_entries.values() if value.instantiated
                ),
            },
        },
        "stateVersion": state.state_version,
        "worldTime": state.world_time,
        "worldTimeLabel": world_time_label(state.world_time, state.calendar),
        "weather": (
            {
                "dateKey": current_weather.get("dateKey"),
                "season": current_weather.get("season"),
                "seasonName": current_weather.get("seasonName"),
                "climateId": current_weather.get("climateId"),
                "climateName": current_weather.get("climateName"),
                "climateSourceStatus": current_weather.get("climateSourceStatus"),
                "condition": current_weather.get("condition"),
                "conditionName": current_weather.get("conditionName"),
                "lowTemperatureC": current_weather.get("lowTemperatureC"),
                "highTemperatureC": current_weather.get("highTemperatureC"),
                "summary": current_weather.get("summary"),
            }
            if current_weather is not None
            else None
        ),
        "scene": scene,
        "map": map_projection,
        # Additive aliases make the exact player location available to API
        # consumers that do not traverse the map envelope.  ``map`` remains
        # the canonical read-model owner for these fields.
        "locationPath": map_projection.get("locationPath", []),
        "currentLocationDisplayName": map_projection.get(
            "currentLocationDisplayName"
        ),
        "currentStructureName": map_projection.get("currentStructureName"),
        "player": {
            "characterId": state.player_character_id,
            "name": state.character_names.get(state.player_character_id, "无名旅人"),
            "health": {"current": 8, "maximum": 12},
            "focus": {"current": 5, "maximum": 7},
            "inventory": player_inventory,
            "equipment": player_equipment,
            "externalInjuries": player_external_injuries,
            "profile": {
                key: value
                for key, value in state.character_profiles.get(
                    state.player_character_id,
                    {},
                ).items()
                if key in {
                    "role",
                    "birthplace",
                    "age",
                    "adult",
                    "publicDescription",
                    "playerDefinedFields",
                }
            },
        },
        "relationships": relationships,
        "clues": [
            {"clueId": clue_id, **clue}
            for clue_id, clue in sorted(state.clues.items())
        ],
        "organizations": [
            {
                "organizationId": organization.organization_id,
                "name": organization.name,
                "type": organization.organization_type,
                "publicDescription": organization.public_description,
            }
            for organization in sorted(
                state.organizations.values(),
                key=lambda value: value.organization_id,
            )
            if organization.visibility in {"public", "player"}
        ],
        "activeClocks": active_clocks,
        "obligations": obligations,
        "worldReports": [
            {
                "candidateId": report.get("candidateId"),
                "title": report.get("title"),
                "summary": report.get("summary"),
                "worldTime": report.get("worldTime"),
            }
            for report in state.world_reports[-12:]
            if report.get("visibility", "player") in {"player", "public"}
        ],
        "availableActions": available_actions,
        "observedAffordances": [
            {
                "opportunityId": affordance.opportunity_id,
                "locationId": affordance.location_id,
                "actionKind": affordance.action_kind,
                "resourceKind": affordance.resource_kind,
                "storyImpactCeiling": affordance.story_impact_ceiling,
            }
            for affordance in sorted(
                state.observed_affordances.values(),
                key=lambda value: value.opportunity_id,
            )
            if affordance.location_id == state.location_id
        ],
        "commerceOffers": [
            {
                "offerId": offer_id,
                "itemName": offer.get("itemName"),
                "unitPricePence": offer.get("unitPricePence"),
                "availableQuantity": offer.get("availableQuantity"),
                "locationId": offer.get("locationId"),
            }
            for offer_id, offer in sorted(state.commerce_offers.items())
            if offer.get("locationId") == state.location_id
        ],
        "catalogOpportunities": [
            {
                "affordanceId": affordance.affordance_id,
                "locationId": affordance.location_id,
                "actionKinds": list(affordance.action_kinds),
                "resourceCategories": list(affordance.resource_categories),
                "storyImpactCeiling": affordance.story_impact_ceiling,
            }
            for affordance in sorted(
                state.catalog_affordances.values(),
                key=lambda value: value.affordance_id,
            )
            if affordance.location_id == state.location_id
        ],
    }


def public_map(state: Projection) -> dict[str, object]:
    """Compatibility export for callers that still import the core projection."""
    return build_public_map(state)


def world_time_label(world_time: int, calendar: CalendarState | None = None) -> str:
    if calendar is not None:
        elapsed = world_time - calendar.origin_world_time
        total_minutes = calendar.hour * 60 + calendar.minute + elapsed
        elapsed_days, minute_of_day = divmod(total_minutes, 1440)
        day_index = calendar.day - 1 + elapsed_days
        month_offset, day_index = divmod(day_index, calendar.days_per_month)
        month_index = calendar.month - 1 + month_offset
        year_offset, month_index = divmod(month_index, calendar.months_per_year)
        hour, minute = divmod(minute_of_day, 60)
        return (
            f"{calendar.era}{calendar.year + year_offset}年"
            f"{month_index + 1}月{day_index + 1}日 · {hour:02d}:{minute:02d}"
        )
    day = world_time // 1440 + 1
    remainder = world_time % 1440
    hour = remainder // 60
    minute = remainder % 60
    return f"第{day}日 · {hour:02d}:{minute:02d}"


def _current_weather(state: Projection) -> dict[str, object] | None:
    candidates = [
        weather
        for weather in state.weather_by_date.values()
        if int(weather.get("effectiveFromWorldTime", 0)) <= state.world_time
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


def _validate_consequence_source(state: Projection, event: Event) -> None:
    from trpg_server.world.consequences import COGNITION_SOURCE_EVENTS, LEGAL_PREDECESSORS

    payload = event.payload
    source_id = payload.get("sourceEventId")
    allowed: frozenset[str] | None = LEGAL_PREDECESSORS.get(event.event_type)
    if event.event_type == "notice.scheduled":
        allowed = frozenset({"wanted.issued"})
    elif event.event_type == "notice.received":
        allowed = frozenset({"notice.scheduled"})
        source_id = payload.get("sourceEventId")
    elif event.event_type == "npc.cognition_changed":
        source_kind = payload.get("sourceKind")
        allowed = COGNITION_SOURCE_EVENTS.get(source_kind)  # type: ignore[arg-type]
    elif event.event_type == "npc.attitude_changed":
        allowed = frozenset({"npc.cognition_changed"})
    elif event.event_type in {"reputation.changed", "effect.applied"}:
        allowed = frozenset(state.event_types_by_id.values())
    if allowed is None:
        return
    if not isinstance(source_id, str) or source_id not in state.confirmed_event_ids:
        raise ValueError(f"{event.event_type} requires an earlier confirmed source event")
    source_type = state.event_types_by_id[source_id]
    if source_type not in allowed:
        raise ValueError(
            f"{event.event_type} cannot use {source_type} as its source"
        )
