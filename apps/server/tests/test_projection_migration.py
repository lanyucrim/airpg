from __future__ import annotations

from dataclasses import asdict

from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events
from trpg_server.core.state import Event
from trpg_server.core.projection_handlers import projection_handlers
from trpg_server.core.projection import replay


def _summary(state):
    return {
        "time": state.world_time,
        "location": state.location_id,
        "characters": dict(sorted(state.character_locations.items())),
        "items": {
            key: (value.container_id, value.quantity, value.definition_id, value.source_event_id)
            for key, value in sorted(state.items.items())
        },
        "relationships": {
            str(key): (value.favor, value.trust, value.fear, value.respect, value.suspicion, value.debt)
            for key, value in sorted(state.relationships.items(), key=lambda pair: str(pair[0]))
        },
        "cognitions": sorted(
            (key, value.status, value.source_event_id)
            for key, value in state.cognitions.items()
        ),
        "wanted": sorted(
            (key, value.status, value.source_event_id)
            for key, value in state.wanted.items()
        ),
        "scene": (state.scene_id, state.scene_beat, tuple(sorted(state.scene_issues))),
        "reports": [report.get("candidateId") for report in state.world_reports],
    }


def test_all_bootstrap_event_families_use_registered_handlers() -> None:
    events = gray_harbor_events()
    missing = sorted({event.event_type for event in events if projection_handlers.handler_for(event.event_type) is None})
    assert missing == []


def test_registered_projection_replay_is_deterministic() -> None:
    events = gray_harbor_events()
    first = replay(GRAY_HARBOR_CAMPAIGN_ID, events, 1)
    second = replay(GRAY_HARBOR_CAMPAIGN_ID, events, 1)
    assert _summary(first) == _summary(second)


def test_new_item_lifecycle_events_replay_without_legacy_branch() -> None:
    events = gray_harbor_events()
    state = replay(GRAY_HARBOR_CAMPAIGN_ID, events, 1)
    item = next(iter(state.items.values()))
    event = Event(
        "evt_test_item_used",
        "item.used",
        state.player_character_id,
        state.world_time,
        {"itemId": item.item_id, "characterId": state.player_character_id},
        schema_version=3,
    )
    replayed = replay(GRAY_HARBOR_CAMPAIGN_ID, [*events, event], 2)
    assert replayed.items[item.item_id].last_changed_event_id == event.event_id
