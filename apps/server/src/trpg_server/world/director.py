from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from trpg_server.core.state import Event, Projection


@dataclass(frozen=True, slots=True)
class WorldEventCandidate:
    candidate_id: str
    kind: str
    title: str
    due_at: int
    source_event_id: str
    visibility: str = "player"
    validation_notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DirectorProposal:
    schema_version: int
    candidates: tuple[WorldEventCandidate, ...]
    requested_beats: int = 0


def propose_world_events(
    state: Projection,
    source_event_id: str,
    previous_world_time: int | None = None,
) -> DirectorProposal:
    """Return deterministic candidates only; this function never mutates state."""
    candidates: list[WorldEventCandidate] = []
    previous = state.world_time - 1 if previous_world_time is None else previous_world_time
    for due_at in _crossed_boundaries(previous, state.world_time, 10_080):
        candidates.append(WorldEventCandidate(
            candidate_id=f"weekly_{due_at}", kind="weekly_report",
            title="灰港周报", due_at=due_at, source_event_id=source_event_id,
            validation_notes=("world_time_boundary", "one_week"),
        ))
    for due_at in _crossed_boundaries(previous, state.world_time, 43_200):
        candidates.append(WorldEventCandidate(
            candidate_id=f"monthly_{due_at}", kind="monthly_report",
            title="灰港月度结算", due_at=due_at, source_event_id=source_event_id,
            validation_notes=("world_time_boundary", "one_month"),
        ))
    return DirectorProposal(schema_version=1, candidates=tuple(candidates))


def validate_director_proposal(state: Projection, proposal: DirectorProposal) -> list[Event]:
    if proposal.schema_version != 1 or proposal.requested_beats < 0:
        return []
    events: list[Event] = []
    for candidate in proposal.candidates:
        if candidate.due_at > state.world_time:
            continue
        if candidate.kind not in {"weekly_report", "monthly_report"}:
            continue
        if not candidate.source_event_id:
            continue
        if any(report.get("candidateId") == candidate.candidate_id for report in state.world_reports):
            continue
        events.append(Event(
            f"evt_{uuid4().hex}", "world.reported", "system", candidate.due_at,
            {"candidateId": candidate.candidate_id, "title": candidate.title,
             "sourceEventId": candidate.source_event_id, "visibility": candidate.visibility,
             "summary": (
                 "本月的城市照常结算：价格、账目与组织安排发生了可观察的微小变化。"
                 if candidate.kind == "monthly_report"
                 else "本周的城市照常运转：有人结账、有人赴约，也有人在无人注视处改变了自己的计划。"
             )},
        ))
    return events


def _crossed_boundaries(previous: int, current: int, interval: int) -> list[int]:
    if current <= previous:
        return []
    first = ((previous // interval) + 1) * interval
    return list(range(first, current + 1, interval))


def scheduled_npc_state(state: Projection, character_id: str) -> tuple[str, str | None]:
    """Return current availability and location from authored weekly schedules."""
    day = (state.world_time // 1440) % 7
    minute = state.world_time % 1440
    matches = sorted(
        (value for value in state.npc_schedules.values()
         if value.character_id == character_id and value.weekday == day
         and value.start_minute <= minute < value.end_minute),
        key=lambda value: (-value.priority, value.schedule_id),
    )
    if not matches:
        return "public", state.character_locations.get(character_id)
    return matches[0].availability, matches[0].location_id
