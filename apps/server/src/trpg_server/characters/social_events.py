from __future__ import annotations

from trpg_server.core.state import Event, Projection
from trpg_server.core.projection_handlers import projection_handlers


@projection_handlers.register("relationship.changed")
def apply_relationship_changed(state: Projection, event: Event) -> None:
    payload = event.payload
    relationship = state.relationship(payload["subjectId"], payload["objectId"])
    dimension = payload["dimension"]
    if not hasattr(relationship, dimension):
        raise ValueError(f"unknown relationship dimension: {dimension}")
    setattr(relationship, dimension, getattr(relationship, dimension) + int(payload["delta"]))
    relationship.sources.setdefault(dimension, []).append(payload["sourceEventId"])


@projection_handlers.register("relationship.initialized")
def apply_relationship_initialized(state: Projection, event: Event) -> None:
    payload = event.payload
    relationship = state.relationship(payload["subjectId"], payload["objectId"])
    for dimension, value in payload["dimensions"].items():
        if not hasattr(relationship, dimension):
            raise ValueError(f"unknown relationship dimension: {dimension}")
        setattr(relationship, dimension, int(value))
        if value:
            relationship.sources.setdefault(dimension, []).append(event.event_id)


@projection_handlers.register("gift.accepted")
def apply_gift_accepted(state: Projection, event: Event) -> None:
    payload = event.payload
    state.accepted_gifts.append(
        (payload["actorId"], payload["targetId"], payload["itemId"], event.event_id)
    )


@projection_handlers.register("npc.attitude_changed")
def apply_npc_attitude_changed(state: Projection, event: Event) -> None:
    if event.schema_version != 1:
        raise ValueError(
            f"unsupported npc.attitude_changed schema version: {event.schema_version}"
        )
    payload = event.payload
    relationship = state.relationship(payload["characterId"], payload["subjectId"])
    dimension = payload["dimension"]
    if not hasattr(relationship, dimension):
        raise ValueError(f"unknown relationship dimension: {dimension}")
    setattr(relationship, dimension, getattr(relationship, dimension) + int(payload["delta"]))
    relationship.sources.setdefault(dimension, []).append(event.event_id)


@projection_handlers.register("reputation.changed")
def apply_reputation_changed(state: Projection, event: Event) -> None:
    from trpg_server.core.state import EffectState

    payload = event.payload
    effect = EffectState(
        effect_id=payload["reputationId"],
        effect_type="reputation",
        subject_id=payload["subjectId"],
        object_id=None,
        scope_id=payload["groupId"],
        value=int(payload["delta"]),
        source_event_id=payload["sourceEventId"],
        created_at=event.world_time,
        expires_at=payload.get("expiresAt"),
        status="active",
    )
    state.effects[effect.effect_id] = effect
