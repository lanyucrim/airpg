from __future__ import annotations

from trpg_server.core.state import CognitionState, Event, Projection
from trpg_server.core.projection_handlers import projection_handlers


@projection_handlers.register("knowledge.learned")
def apply_knowledge_learned(state: Projection, event: Event) -> None:
    payload = event.payload
    state.knowledge.setdefault(payload["characterId"], set()).add(payload["factId"])
    cognition = CognitionState(
        character_id=payload["characterId"],
        proposition_id=payload["factId"],
        status="known",
        source_event_id=payload.get("sourceEventId", event.event_id),
        source_kind="system",
        acquired_at=event.world_time,
    )
    state.cognitions[(cognition.character_id, cognition.proposition_id)] = cognition
    state.cognition_history.append(cognition)


@projection_handlers.register("npc.cognition_changed")
def apply_npc_cognition_changed(state: Projection, event: Event) -> None:
    if event.schema_version != 1:
        raise ValueError(
            f"unsupported npc.cognition_changed schema version: {event.schema_version}"
        )
    payload = event.payload
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


@projection_handlers.register("npc.cognition_expired")
def apply_npc_cognition_expired(state: Projection, event: Event) -> None:
    payload = event.payload
    key = (payload["characterId"], payload["propositionId"])
    current = state.cognitions.get(key)
    if current is not None and current.source_event_id == payload["cognitionSourceEventId"]:
        state.cognitions.pop(key, None)
        state.knowledge.setdefault(payload["characterId"], set()).discard(
            payload["propositionId"]
        )
