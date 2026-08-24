from __future__ import annotations

import pytest

from trpg_server.world.consequences import (
    advance_consequences,
    attitude_event,
    cognition_event,
    reputation_event,
    schedule_notice,
    sourced_legal_event,
)
from trpg_server.core.state import Event
from trpg_server.core.projection import apply_event, replay


def _wanted() -> Event:
    return Event("evt_wanted", "wanted.issued", "court", 10, {
        "wantedId": "wanted_ella", "subjectId": "protagonist",
        "jurisdictionId": "gray_harbor", "sourceEventId": "evt_suspect",
    })


def _legal_chain() -> list[Event]:
    crime = Event("evt_crime", "crime.committed", "protagonist", 1, {
        "crimeId": "crime_1", "subjectId": "protagonist",
        "jurisdictionId": "gray_harbor", "locationId": "oak_street",
    })
    witness = sourced_legal_event(
        "witness.observed", crime, "martha_bell", 2,
        {"witnessId": "martha_bell", "observationId": "observation_1"},
    )
    report = sourced_legal_event(
        "information.reported", witness, "martha_bell", 4,
        {"reportId": "report_1", "recipientId": "gray_harbor_police"},
    )
    evidence = sourced_legal_event(
        "evidence.registered", report, "gray_harbor_police", 6,
        {"evidenceId": "evidence_1", "jurisdictionId": "gray_harbor"},
    )
    suspect = sourced_legal_event(
        "suspect.identified", evidence, "gray_harbor_police", 8,
        {"suspectId": "protagonist", "confidence": 90},
    )
    wanted = sourced_legal_event(
        "wanted.issued", suspect, "gray_harbor_court", 10,
        {"wantedId": "wanted_ella", "subjectId": "protagonist", "jurisdictionId": "gray_harbor"},
    )
    return [crime, witness, report, evidence, suspect, wanted]


def test_notice_delivery_is_delayed_and_creates_source_backed_npc_knowledge() -> None:
    chain = _legal_chain()
    state = replay("cmp", chain, 1)
    wanted = chain[-1]
    scheduled = schedule_notice(
        "wanted_ella", "harvey_cole", wanted.event_id, 30, "gray_harbor", 10
    )
    apply_event(state, scheduled)

    assert advance_consequences(state, [Event("evt_time", "time.advanced", "system", 20, {"from": 10, "to": 20, "minutes": 10})]) == []
    generated = advance_consequences(state, [Event("evt_time", "time.advanced", "system", 30, {"from": 10, "to": 30, "minutes": 20})])
    assert [event.event_type for event in generated] == ["notice.received", "npc.cognition_changed"]
    for event in generated:
        apply_event(state, event)
    cognition = state.cognitions[("harvey_cole", "wanted:wanted_ella")]
    assert cognition.status == "known"
    assert cognition.source_event_id == generated[0].event_id


def test_wanted_status_does_not_grant_knowledge_before_notice() -> None:
    state = replay("cmp", _legal_chain(), 1)
    assert ("harvey_cole", "wanted:wanted_ella") not in state.cognitions


def test_full_legal_chain_is_source_ordered_and_attitude_uses_own_cognition() -> None:
    chain = _legal_chain()
    state = replay("cmp", chain, 1)
    scheduled = schedule_notice(
        "wanted_ella", "harvey_cole", chain[-1].event_id, 12,
        "gray_harbor", 10,
    )
    apply_event(state, scheduled)
    delivered = advance_consequences(
        state,
        [Event("evt_time", "time.advanced", "system", 12, {"from": 10, "to": 12, "minutes": 2})],
    )
    for event in delivered:
        apply_event(state, event)
    attitude = attitude_event(
        "harvey_cole", "protagonist", "suspicion", 15,
        delivered[-1], 12,
    )
    apply_event(state, attitude)

    assert len(state.legal_records) == 5
    assert state.wanted["wanted_ella"].source_event_id == chain[-2].event_id
    assert state.relationship("harvey_cole", "protagonist").suspicion == 15
    assert state.relationship("harvey_cole", "protagonist").sources["suspicion"] == [attitude.event_id]


def test_rumor_can_create_wrong_belief_without_changing_world_fact() -> None:
    crime, witness, report, *_ = _legal_chain()
    state = replay("cmp", [crime, witness, report], 1)
    rumor = cognition_event(
        "jenny_bell", "protagonist_burned_the_bakery", "believed", "rumor",
        report, 5, confidence=55, expires_at=50,
    )
    denial = cognition_event(
        "martha_bell", "protagonist_burned_the_bakery", "denied", "told",
        report, 5, confidence=80,
    )
    apply_event(state, rumor)
    apply_event(state, denial)

    assert state.cognitions[("jenny_bell", "protagonist_burned_the_bakery")].status == "believed"
    assert state.cognitions[("martha_bell", "protagonist_burned_the_bakery")].status == "denied"
    assert "protagonist_burned_the_bakery" not in state.world_facts


def test_withheld_report_cannot_be_used_as_registered_evidence() -> None:
    crime, witness, report, *_ = _legal_chain()
    withheld = sourced_legal_event(
        "information.withheld", witness, "martha_bell", 3,
        {"reason": "personal_debt"},
    )
    with pytest.raises(ValueError, match="cannot follow"):
        sourced_legal_event(
            "evidence.registered", withheld, "gray_harbor_police", 4,
            {"evidenceId": "bad"},
        )
    assert report.payload["sourceEventId"] == witness.event_id


def test_expiring_cognition_and_group_reputation_keep_sources() -> None:
    crime, witness, report, *_ = _legal_chain()
    state = replay("cmp", [crime, witness, report], 1)
    cognition = cognition_event(
        "jenny_bell", "rumor:temporary", "suspected", "rumor", report, 5,
        confidence=40, expires_at=10,
    )
    apply_event(state, cognition)
    reputation = reputation_event(
        "protagonist", "iron_hooks", -10, report, 5, expires_at=10,
    )
    apply_event(state, reputation)
    expired = advance_consequences(
        state,
        [Event("evt_time", "time.advanced", "system", 10, {"from": 5, "to": 10, "minutes": 5})],
    )
    for event in expired:
        apply_event(state, event)

    assert ("jenny_bell", "rumor:temporary") not in state.cognitions
    assert state.effects[reputation.payload["reputationId"]].status == "expired"
    assert state.effects[reputation.payload["reputationId"]].source_event_id == report.event_id


def test_projection_rejects_unsourced_or_cross_character_consequences() -> None:
    state = replay("cmp", [], 1)
    with pytest.raises(ValueError, match="earlier confirmed source"):
        apply_event(state, _wanted())
    chain = _legal_chain()
    state = replay("cmp", chain[:3], 1)
    cognition = cognition_event(
        "jenny_bell", "crime:1", "believed", "rumor", chain[2], 5,
    )
    with pytest.raises(ValueError, match="another character"):
        attitude_event("martha_bell", "protagonist", "fear", 5, cognition, 6)


def test_legal_chain_rejects_out_of_order_source() -> None:
    crime = Event("evt_crime_order", "crime.committed", "protagonist", 1, {})
    with pytest.raises(ValueError, match="cannot follow"):
        sourced_legal_event(
            "wanted.issued", crime, "court", 2,
            {"wantedId": "bad", "subjectId": "protagonist", "jurisdictionId": "gray_harbor"},
        )
