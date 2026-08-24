from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from trpg_server.world.consequences import advance_consequences
from trpg_server.world.director import propose_world_events, validate_director_proposal
from trpg_server.core.state import Event, ParsedCommand, Projection, Resolution
from trpg_server.ai.player.intent import IntentParseResult, StructuredIntentParser
from trpg_server.memory import EpisodicMemory, MemorySelection
from trpg_server.ai.player.narration import NarrationResult, SafeNarrator
from trpg_server.characters.decision import NpcDecisionResult, SafeNpcDecider
from trpg_server.core.ports import CommandResolver, ProjectionRunner
from trpg_server.behavior.routine_rules import (
    RoutineProposalResult,
    SafeRoutineDirector,
    materialize_routine_candidates,
)
from trpg_server.world.simulation import propose_world_simulation, validate_world_simulation
from trpg_server.world.weather import (
    SafeWeatherDirector,
    WeatherGenerationAudit,
    WeatherGenerationResult,
    materialize_weather_events,
)


@dataclass(frozen=True, slots=True)
class TurnPreparation:
    parse_result: IntentParseResult
    command: ParsedCommand
    npc_decision_result: NpcDecisionResult
    resolution: Resolution
    predicted_state: Projection
    narration_result: NarrationResult
    routine_result: RoutineProposalResult
    weather_result: WeatherGenerationResult


@dataclass(frozen=True, slots=True)
class AuthoritativeTurnPipeline:
    """Pure pre-commit turn coordinator.

    It prepares a complete, replayable result without opening a database write
    transaction. The service is still responsible for the final version and
    idempotency gate before committing the prepared events.
    """

    intent_parser: StructuredIntentParser
    resolver: CommandResolver
    npc_decider: SafeNpcDecider
    routine_director: SafeRoutineDirector
    weather_director: SafeWeatherDirector
    narrator: SafeNarrator
    projection_runner: ProjectionRunner

    def parse(
        self,
        *,
        player_text: str,
        actor_id: str,
        state: Projection,
        source_message_id: str,
    ) -> IntentParseResult:
        return self.intent_parser.parse_with_audit(
            player_text,
            actor_id,
            state,
            source_message_id,
        )

    def prepare(
        self,
        *,
        campaign_id: str,
        actor_id: str,
        player_text: str,
        source_message_id: str,
        command_id: str,
        state: Projection,
        events: Sequence[Event],
        state_version: int,
        memory_selection: MemorySelection | None,
        parse_result: IntentParseResult | None = None,
    ) -> TurnPreparation:
        parse_result = parse_result or self.parse(
            player_text=player_text,
            actor_id=actor_id,
            state=state,
            source_message_id=source_message_id,
        )
        command = parse_result.command
        memories: Sequence[EpisodicMemory] | None = (
            memory_selection.selected if memory_selection is not None else None
        )
        npc_decision_result = self.npc_decider.decide(
            state,
            command,
            memories,
        )
        resolution = self.resolver.resolve(
            state,
            command,
            npc_decision_result.decision,
        )
        resolution.events.extend(advance_consequences(state, resolution.events))
        provisional_state = self.projection_runner.replay(
            campaign_id,
            [*events, *resolution.events],
            state_version,
        )
        source_event_id = (
            resolution.events[-1].event_id
            if resolution.events
            else command_id
        )
        routine_result = self.routine_director.propose(provisional_state, command)
        if routine_result.accepted:
            routine_source_event_id = next(
                (
                    event.event_id
                    for event in reversed(resolution.events)
                    if event.event_type in {"affordance.observed", "search.performed"}
                ),
                source_event_id,
            )
            resolution.events.extend(
                materialize_routine_candidates(
                    provisional_state,
                    routine_result.accepted,
                    routine_source_event_id,
                )
            )
            provisional_state = self.projection_runner.replay(
                campaign_id,
                [*events, *resolution.events],
                state_version,
            )
        source_event_id = resolution.events[-1].event_id if resolution.events else command_id
        if resolution.events:
            weather_result = self.weather_director.propose(
                provisional_state,
                previous_world_time=state.world_time,
            )
            resolution.events.extend(
                materialize_weather_events(
                    provisional_state,
                    weather_result,
                    source_event_id,
                )
            )
            provisional_state = self.projection_runner.replay(
                campaign_id,
                [*events, *resolution.events],
                state_version,
            )
        else:
            weather_result = WeatherGenerationResult(
                (),
                (),
                WeatherGenerationAudit(
                    "not_applicable", None, None, None, None, None
                ),
            )
        proposal = propose_world_events(
            provisional_state,
            source_event_id,
            previous_world_time=state.world_time,
        )
        resolution.events.extend(
            validate_director_proposal(provisional_state, proposal)
        )
        if resolution.events:
            simulation_candidates = propose_world_simulation(
                provisional_state,
                previous_world_time=state.world_time,
                source_event_id=source_event_id,
            )
            resolution.events.extend(
                validate_world_simulation(provisional_state, simulation_candidates)
            )
        predicted_version = state_version + (1 if resolution.events else 0)
        predicted_state = self.projection_runner.replay(
            campaign_id,
            [*events, *resolution.events],
            predicted_version,
        )
        narration_result = self.narrator.narrate(resolution, predicted_state)
        return TurnPreparation(
            parse_result=parse_result,
            command=command,
            npc_decision_result=npc_decision_result,
            resolution=resolution,
            predicted_state=predicted_state,
            narration_result=narration_result,
            routine_result=routine_result,
            weather_result=weather_result,
        )
