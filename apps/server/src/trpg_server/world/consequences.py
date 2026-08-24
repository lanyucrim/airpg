from __future__ import annotations

from typing import Literal
from uuid import uuid4

from trpg_server.core.state import Event, Projection


CognitionStatus = Literal["known", "believed", "suspected", "denied"]
CognitionSource = Literal[
    "witness", "told", "document", "faction_report", "rumor", "inference", "system"
]

LEGAL_PREDECESSORS: dict[str, frozenset[str]] = {
    "witness.observed": frozenset({"crime.committed"}),
    "information.reported": frozenset({"witness.observed"}),
    "information.withheld": frozenset({"witness.observed", "information.reported"}),
    "evidence.registered": frozenset({"information.reported"}),
    "suspect.identified": frozenset({"evidence.registered"}),
    "suspect.described": frozenset({"evidence.registered"}),
    "wanted.issued": frozenset({"suspect.identified"}),
}

COGNITION_SOURCE_EVENTS: dict[CognitionSource, frozenset[str]] = {
    "witness": frozenset({"witness.observed"}),
    "told": frozenset({"information.reported"}),
    "document": frozenset({"evidence.registered"}),
    "faction_report": frozenset({"notice.received"}),
    "rumor": frozenset({"information.reported"}),
    "inference": frozenset({"evidence.registered", "suspect.identified", "suspect.described"}),
    "system": frozenset({"knowledge.learned"}),
}


def sourced_legal_event(
    event_type: str,
    source_event: Event,
    actor_id: str,
    world_time: int,
    payload: dict[str, object],
) -> Event:
    allowed = LEGAL_PREDECESSORS.get(event_type)
    if allowed is None or source_event.event_type not in allowed:
        raise ValueError(
            f"{event_type} cannot follow {source_event.event_type}"
        )
    if world_time < source_event.world_time:
        raise ValueError("consequence cannot predate its source")
    return Event(
        _event_id(), event_type, actor_id, world_time,
        {**payload, "sourceEventId": source_event.event_id},
        schema_version=1,
    )


def cognition_event(
    character_id: str,
    proposition_id: str,
    status: CognitionStatus,
    source_kind: CognitionSource,
    source_event: Event,
    world_time: int,
    *,
    confidence: int = 100,
    scope_id: str | None = None,
    expires_at: int | None = None,
) -> Event:
    if source_event.event_type not in COGNITION_SOURCE_EVENTS[source_kind]:
        raise ValueError(
            f"{source_kind} cognition cannot use {source_event.event_type} as source"
        )
    if not 0 <= confidence <= 100:
        raise ValueError("confidence must be between 0 and 100")
    if world_time < source_event.world_time:
        raise ValueError("cognition cannot predate its source")
    if expires_at is not None and expires_at <= world_time:
        raise ValueError("cognition expiry must be after acquisition")
    return Event(
        _event_id(), "npc.cognition_changed", "system", world_time,
        {
            "characterId": character_id,
            "propositionId": proposition_id,
            "status": status,
            "sourceEventId": source_event.event_id,
            "sourceKind": source_kind,
            "scopeId": scope_id,
            "confidence": confidence,
            "expiresAt": expires_at,
        },
        schema_version=1,
    )


def attitude_event(
    character_id: str,
    subject_id: str,
    dimension: Literal["favor", "trust", "fear", "respect", "suspicion", "debt"],
    delta: int,
    cognition: Event,
    world_time: int,
) -> Event:
    if cognition.event_type != "npc.cognition_changed":
        raise ValueError("attitude changes require a cognition source")
    if cognition.payload.get("characterId") != character_id:
        raise ValueError("an NPC cannot react to another character's cognition")
    if delta == 0 or not -100 <= delta <= 100:
        raise ValueError("attitude delta must be nonzero and bounded")
    return Event(
        _event_id(), "npc.attitude_changed", character_id, world_time,
        {
            "characterId": character_id,
            "subjectId": subject_id,
            "dimension": dimension,
            "delta": delta,
            "sourceEventId": cognition.event_id,
        },
        schema_version=1,
    )


def reputation_event(
    subject_id: str,
    group_id: str,
    delta: int,
    source_event: Event,
    world_time: int,
    *,
    expires_at: int | None = None,
) -> Event:
    if not source_event.event_id or delta == 0:
        raise ValueError("reputation requires a source and nonzero delta")
    return Event(
        _event_id(), "reputation.changed", "system", world_time,
        {
            "reputationId": f"reputation_{uuid4().hex}",
            "subjectId": subject_id,
            "groupId": group_id,
            "delta": delta,
            "sourceEventId": source_event.event_id,
            "expiresAt": expires_at,
        },
        schema_version=1,
    )


def advance_consequences(state: Projection, events: list[Event]) -> list[Event]:
    """Settle due deliveries and expiries without inferring facts or witnesses."""
    generated: list[Event] = []
    projected = state.world_time
    for event in events:
        projected = max(projected, event.world_time)
        if event.event_type == "time.advanced":
            projected = int(event.payload["to"])

    for notice in sorted(state.pending_notices.values(), key=lambda value: value["noticeId"]):
        if int(notice["deliverAt"]) > projected:
            continue
        received = Event(
            _event_id(), "notice.received", "system", projected,
            {
                "noticeId": notice["noticeId"],
                "wantedId": notice["wantedId"],
                "characterId": notice["characterId"],
                "sourceEventId": notice["scheduleEventId"],
                "jurisdictionId": notice.get("jurisdictionId"),
            },
            schema_version=1,
        )
        generated.append(received)
        generated.append(cognition_event(
            str(notice["characterId"]),
            f"wanted:{notice['wantedId']}",
            "known",
            "faction_report",
            received,
            projected,
            scope_id=str(notice.get("jurisdictionId") or "") or None,
        ))

    for cognition in sorted(
        state.cognitions.values(),
        key=lambda value: (value.character_id, value.proposition_id),
    ):
        if cognition.expires_at is None or cognition.expires_at > projected:
            continue
        generated.append(Event(
            _event_id(), "npc.cognition_expired", "system", projected,
            {
                "characterId": cognition.character_id,
                "propositionId": cognition.proposition_id,
                "cognitionSourceEventId": cognition.source_event_id,
            },
            schema_version=1,
        ))
    for effect in sorted(state.effects.values(), key=lambda value: value.effect_id):
        if effect.status != "active" or effect.expires_at is None or effect.expires_at > projected:
            continue
        generated.append(Event(
            _event_id(), "effect.expired", "system", projected,
            {"effectId": effect.effect_id, "sourceEventId": effect.source_event_id},
            schema_version=1,
        ))
    return generated


def schedule_notice(
    wanted_id: str,
    character_id: str,
    source_event_id: str,
    deliver_at: int,
    jurisdiction_id: str,
    world_time: int,
) -> Event:
    if not source_event_id or deliver_at < world_time:
        raise ValueError("notice requires a source and non-past delivery time")
    return Event(
        _event_id(), "notice.scheduled", "system", world_time,
        {
            "noticeId": f"notice_{uuid4().hex}",
            "wantedId": wanted_id,
            "characterId": character_id,
            "sourceEventId": source_event_id,
            "deliverAt": deliver_at,
            "jurisdictionId": jurisdiction_id,
        },
        schema_version=1,
    )


def _event_id() -> str:
    return f"evt_{uuid4().hex}"
