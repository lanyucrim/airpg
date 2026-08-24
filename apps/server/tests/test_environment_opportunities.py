from __future__ import annotations

from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events
from trpg_server.behavior.router import interpret_player_text, resolve
from trpg_server.core.projection import replay


def _state():
    events = gray_harbor_events()
    return events, replay(GRAY_HARBOR_CAMPAIGN_ID, events, len(events))


def test_street_food_search_creates_observed_opportunity_not_fake_inventory() -> None:
    events, state = _state()
    actor_id = state.player_character_id
    # Streets are routing data, so opportunities are observed at a real
    # place on the street rather than while standing on a street node.
    state.character_locations[actor_id] = "red_mill_tavern"
    state.location_id = "red_mill_tavern"
    moved_state = state

    search = interpret_player_text(
        "我去街上找点吃的",
        actor_id=actor_id,
        state=moved_state,
    )
    assert search.action_type == "search_location"
    assert search.parameters["searchKind"] == "food"
    result = resolve(moved_state, search)

    assert result.status == "committed"
    assert result.outcome == "opportunity_found"
    assert "临时机会" in result.narrative
    assert any(event.event_type == "affordance.observed" for event in result.events)
    assert all(event.event_type not in {"item.created", "item.transferred"} for event in result.events)


def test_empty_food_search_is_a_normal_result_with_time_cost() -> None:
    events, state = _state()
    actor_id = state.player_character_id
    move = interpret_player_text("去后院", actor_id=actor_id, state=state)
    movement = resolve(state, move)
    assert movement.status == "committed"
    moved_state = replay(
        GRAY_HARBOR_CAMPAIGN_ID,
        [*events, *movement.events],
        len(events),
    )

    search = interpret_player_text("找点吃的", actor_id=actor_id, state=moved_state)
    result = resolve(moved_state, search)

    assert result.status == "committed"
    assert result.outcome == "nothing_found"
    assert "没有找到合适的食物" in result.narrative
    assert any(event.event_type == "time.advanced" for event in result.events)
