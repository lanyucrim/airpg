from __future__ import annotations

import pytest
from pydantic import ValidationError

from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events
from trpg_server.behavior.router import interpret_player_text, resolve
from trpg_server.core.projection import replay
from trpg_server.behavior.routine_rules import RoutineCandidate, validate_routine_candidate


def _street_state():
    events = gray_harbor_events()
    state = replay(GRAY_HARBOR_CAMPAIGN_ID, events, len(events))
    actor = state.player_character_id
    state.character_locations[actor] = "red_mill_tavern"
    state.location_id = "red_mill_tavern"
    search = resolve(
        state,
        interpret_player_text("我去街上找点吃的", actor_id=actor, state=state),
    )
    events = [*events, *search.events]
    final_state = replay(GRAY_HARBOR_CAMPAIGN_ID, events, len(events))
    final_state.character_locations[actor] = "red_mill_tavern"
    final_state.location_id = "red_mill_tavern"
    return final_state, actor


def test_routine_candidate_accepts_only_observed_soft_opportunity() -> None:
    state, actor = _street_state()
    candidate = RoutineCandidate(
        candidateId="street_food_vendor_1",
        sourceAffordanceId="opportunity_red_mill_tavern_food",
        locationId="red_mill_tavern",
        actionKind="search",
        outcome="success",
        storyImpact="routine",
        timeMinutes=10,
        temporaryEntityKind="vendor",
        summary="街角摊位仍有普通面包可卖。",
    )

    result = validate_routine_candidate(state, candidate)
    assert result.accepted
    assert result.code == "accepted"
    assert actor == state.player_character_id


def test_routine_candidate_rejects_story_impact_at_schema_boundary() -> None:
    with pytest.raises(ValidationError, match="story or major impact"):
        RoutineCandidate(
            candidateId="illegal_story_candidate",
            sourceAffordanceId="AFFORDANCE-L010",
            locationId="red_mill_tavern",
            actionKind="search",
            outcome="success",
            storyImpact="story",
            timeMinutes=10,
            summary="凭空发现主线证据。",
        )


def test_routine_candidate_rejects_unobserved_source() -> None:
    state, _ = _street_state()
    candidate = RoutineCandidate(
        candidateId="invented_food_source",
        sourceAffordanceId="opportunity_unknown_food",
        locationId="red_mill_tavern",
        actionKind="search",
        outcome="success",
        storyImpact="routine",
        timeMinutes=10,
        summary="没有来源的食物。",
    )

    result = validate_routine_candidate(state, candidate)
    assert not result.accepted
    assert result.code == "missing_affordance_source"


def test_routine_candidate_rejects_an_ai_proposed_item_record() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RoutineCandidate(
            candidateId="illegal_item_candidate",
            sourceAffordanceId="AFFORDANCE-L010",
            locationId="red_mill_tavern",
            actionKind="search",
            outcome="success",
            storyImpact="routine",
            timeMinutes=10,
            item={"name": "凭空出现的物品"},
            summary="模型不能凭空创建物品。",
        )
