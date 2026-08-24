from __future__ import annotations

from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events
from trpg_server.core.projection_handlers import projection_handlers
from trpg_server.ai.player.intent import DisabledModelAdapter, StructuredIntentParser
from trpg_server.ai.player.narration import DisabledNarrationAdapter, SafeNarrator
from trpg_server.characters.decision import DisabledNpcDecisionAdapter, SafeNpcDecider
from trpg_server.core.ports import DefaultProjectionRunner, LegacyCommandResolver
from trpg_server.core.projection import replay
from trpg_server.behavior.routine_rules import DisabledRoutineAdapter, SafeRoutineDirector
from trpg_server.core.turn_pipeline import AuthoritativeTurnPipeline
from trpg_server.world.weather import DisabledWeatherAdapter, SafeWeatherDirector


def test_projection_handlers_are_registered_by_event_family() -> None:
    assert {
        "campaign.created",
        "location.created",
        "character.created",
        "container.created",
        "item.created",
        "item.transferred",
        "time.advanced",
    } <= projection_handlers.event_types


def test_legacy_resolver_is_only_a_compatibility_adapter() -> None:
    assert isinstance(LegacyCommandResolver(), LegacyCommandResolver)
    assert isinstance(DefaultProjectionRunner(), DefaultProjectionRunner)


def test_authoritative_pipeline_prepares_replayable_turn_without_store_lock() -> None:
    events = gray_harbor_events()
    version = len(events)
    state = replay(GRAY_HARBOR_CAMPAIGN_ID, events, version)
    pipeline = AuthoritativeTurnPipeline(
        intent_parser=StructuredIntentParser(DisabledModelAdapter()),
        resolver=LegacyCommandResolver(),
        npc_decider=SafeNpcDecider(DisabledNpcDecisionAdapter()),
        routine_director=SafeRoutineDirector(DisabledRoutineAdapter()),
        weather_director=SafeWeatherDirector(DisabledWeatherAdapter()),
        narrator=SafeNarrator(DisabledNarrationAdapter()),
        projection_runner=DefaultProjectionRunner(),
    )

    preparation = pipeline.prepare(
        campaign_id=GRAY_HARBOR_CAMPAIGN_ID,
        actor_id=state.player_character_id,
        player_text="等待十分钟",
        source_message_id="msg_pipeline",
        command_id="cmd_pipeline",
        state=state,
        events=events,
        state_version=version,
        memory_selection=None,
    )

    assert preparation.command.action_type == "wait"
    assert preparation.resolution.status == "committed"
    assert any(event.event_type == "time.advanced" for event in preparation.resolution.events)
    assert preparation.predicted_state.world_time > state.world_time
    assert preparation.narration_result.text
