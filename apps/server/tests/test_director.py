from __future__ import annotations

from trpg_server.world.director import DirectorProposal, WorldEventCandidate, propose_world_events, validate_director_proposal
from trpg_server.core.projection import replay


def test_director_proposes_but_cannot_directly_change_state() -> None:
    state = replay("cmp", [], 1)
    state.world_time = 10_080
    proposal = propose_world_events(state, "evt_source")
    assert state.world_reports == []
    events = validate_director_proposal(state, proposal)
    assert [event.event_type for event in events] == ["world.reported"]


def test_invalid_director_candidate_is_rejected_without_world_event() -> None:
    state = replay("cmp", [], 1)
    proposal = DirectorProposal(1, (WorldEventCandidate("bad", "weekly_report", "bad", 10, "evt"),))
    assert validate_director_proposal(state, proposal) == []


def test_director_catches_week_and_month_boundaries_crossed_by_long_wait() -> None:
    state = replay("cmp", [], 1)
    state.world_time = 43_201
    proposal = propose_world_events(state, "evt_source", previous_world_time=0)
    assert [candidate.kind for candidate in proposal.candidates] == [
        "weekly_report", "weekly_report", "weekly_report", "weekly_report",
        "monthly_report",
    ]
    events = validate_director_proposal(state, proposal)
    assert len(events) == 5
    assert [event.world_time for event in events] == [10_080, 20_160, 30_240, 40_320, 43_200]


def test_director_rejects_unknown_kind_and_future_candidate() -> None:
    state = replay("cmp", [], 1)
    state.world_time = 100
    proposal = DirectorProposal(1, (
        WorldEventCandidate("future", "weekly_report", "future", 200, "evt"),
        WorldEventCandidate("unknown", "invented", "unknown", 100, "evt"),
    ))
    assert validate_director_proposal(state, proposal) == []
