from __future__ import annotations

from trpg_server.core.state import Event, WantedState, Projection
from trpg_server.core.projection_handlers import projection_handlers


@projection_handlers.register(
    "crime.committed",
    "witness.observed",
    "information.reported",
    "information.withheld",
    "evidence.registered",
    "suspect.identified",
    "suspect.described",
)
def apply_legal_record(state: Projection, event: Event) -> None:
    state.legal_records[event.event_id] = {
        "eventType": event.event_type,
        "worldTime": event.world_time,
        **dict(event.payload),
    }


@projection_handlers.register("wanted.issued")
def apply_wanted_issued(state: Projection, event: Event) -> None:
    payload = event.payload
    wanted = WantedState(
        wanted_id=payload["wantedId"],
        subject_id=payload["subjectId"],
        jurisdiction_id=payload["jurisdictionId"],
        source_event_id=payload["sourceEventId"],
        issued_at=event.world_time,
    )
    state.wanted[wanted.wanted_id] = wanted


@projection_handlers.register("wanted.cleared", "wanted.expired")
def apply_wanted_closed(state: Projection, event: Event) -> None:
    payload = event.payload
    wanted = state.wanted.get(payload["wantedId"])
    if wanted is not None:
        state.wanted[wanted.wanted_id] = WantedState(
            wanted.wanted_id,
            wanted.subject_id,
            wanted.jurisdiction_id,
            wanted.source_event_id,
            wanted.issued_at,
            "cleared" if event.event_type == "wanted.cleared" else "expired",
        )


@projection_handlers.register("notice.scheduled")
def apply_notice_scheduled(state: Projection, event: Event) -> None:
    state.pending_notices[event.payload["noticeId"]] = {
        **dict(event.payload),
        "scheduleEventId": event.event_id,
    }


@projection_handlers.register("notice.received")
def apply_notice_received(state: Projection, event: Event) -> None:
    state.pending_notices.pop(event.payload["noticeId"], None)
