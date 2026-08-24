from __future__ import annotations

from trpg_server.core.state import (
    DiscoveryState,
    Event,
    InquiryState,
    InspectionState,
    Projection,
)
from trpg_server.core.projection_handlers import projection_handlers


@projection_handlers.register("discovery.defined")
def apply_discovery_defined(state: Projection, event: Event) -> None:
    payload = event.payload
    discovery = DiscoveryState(
        discovery_id=payload["discoveryId"],
        location_id=payload["locationId"],
        aliases=tuple(payload.get("aliases", [])),
        fact_id=payload["factId"],
        clue_id=payload["clueId"],
        exit_ids=tuple(payload.get("exitIds", [])),
        required_condition_ids=tuple(payload.get("requiredConditionIds", [])),
        initially_known_by=tuple(payload.get("initiallyKnownBy", [])),
        time_minutes=payload.get("timeMinutes", 10),
        reveal_text=payload["revealText"],
    )
    state.discovery_definitions[discovery.discovery_id] = discovery


@projection_handlers.register("location.exit_discovered")
def apply_location_exit_discovered(state: Projection, event: Event) -> None:
    payload = event.payload
    state.discovered_exits.setdefault(payload["characterId"], set()).add(payload["exitId"])


@projection_handlers.register("inspection.defined")
def apply_inspection_defined(state: Projection, event: Event) -> None:
    payload = event.payload
    definition = InspectionState(
        interaction_id=payload["interactionId"],
        label=payload["label"],
        suggested_prompt=payload["suggestedPrompt"],
        target_item_id=payload["targetItemId"],
        aliases=tuple(payload.get("aliases", [])),
        access_policy=payload["accessPolicy"],
        required_actor_knowledge_fact_ids=tuple(payload.get("requiredActorKnowledgeFactIds", [])),
        revealed_fact_ids=tuple(payload.get("revealedFactIds", [])),
        clue_ids=tuple(payload.get("clueIds", [])),
        time_minutes=payload["timeMinutes"],
        reveal_text=payload["revealText"],
        repeat_text=payload["repeatText"],
    )
    state.inspection_definitions[definition.interaction_id] = definition


@projection_handlers.register("inquiry.defined")
def apply_inquiry_defined(state: Projection, event: Event) -> None:
    payload = event.payload
    definition = InquiryState(
        interaction_id=payload["interactionId"],
        label=payload["label"],
        suggested_prompt=payload["suggestedPrompt"],
        target_character_id=payload["targetCharacterId"],
        topic=payload["topic"],
        aliases=tuple(payload.get("aliases", [])),
        required_actor_knowledge_fact_ids=tuple(payload.get("requiredActorKnowledgeFactIds", [])),
        required_npc_knowledge_fact_ids=tuple(payload.get("requiredNpcKnowledgeFactIds", [])),
        revealed_fact_ids=tuple(payload.get("revealedFactIds", [])),
        clue_ids=tuple(payload.get("clueIds", [])),
        time_minutes=payload["timeMinutes"],
        response_text=payload["responseText"],
        repeat_text=payload["repeatText"],
        unknown_text=payload["unknownText"],
    )
    state.inquiry_definitions[definition.interaction_id] = definition


@projection_handlers.register("interaction.completed")
def apply_interaction_completed(state: Projection, event: Event) -> None:
    payload = event.payload
    state.completed_interactions.setdefault(payload["characterId"], set()).add(
        payload["interactionId"]
    )
