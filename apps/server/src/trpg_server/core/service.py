from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

from trpg_server.story.bootstrap import (
    GRAY_HARBOR_CAMPAIGN_ID,
    gray_harbor_events,
    gray_harbor_scenario,
)
from trpg_server.core.state import DecisionReason, Event, ParsedCommand, Projection, RawMessage, Resolution
from trpg_server.ai.player.intent import DisabledModelAdapter, StructuredIntentParser
from trpg_server.memory import (
    MEMORY_EVENT_TYPES,
    MemoryQuery,
    MemorySelection,
    project_memory_delta,
    project_memory_read_model,
    select_memories,
)
from trpg_server.ai.player.narration import DisabledNarrationAdapter, SafeNarrator
from trpg_server.characters.decision import DisabledNpcDecisionAdapter, SafeNpcDecider
from trpg_server.core.projection import public_state, replay
from trpg_server.core.ports import DefaultProjectionRunner, LegacyCommandResolver
from trpg_server.behavior.routine_rules import DisabledRoutineAdapter, SafeRoutineDirector
from trpg_server.core.schemas import TurnRequest
from trpg_server.core.store import EventStoreBackend, StoreIntegrityError, database_backend_from_environment
from trpg_server.core.turn_pipeline import AuthoritativeTurnPipeline
from trpg_server.world.weather import (
    DisabledWeatherAdapter,
    SafeWeatherDirector,
    materialize_weather_events,
)


class CampaignNotFoundError(Exception):
    pass


class StateVersionConflictError(Exception):
    def __init__(self, current_version: int) -> None:
        self.current_version = current_version
        super().__init__(f"expected state version does not match {current_version}")


class TurnNotFoundError(Exception):
    pass


class GameService:
    def __init__(
        self,
        database_path: Path,
        intent_parser: StructuredIntentParser | None = None,
        narrator: SafeNarrator | None = None,
        npc_decider: SafeNpcDecider | None = None,
        routine_director: SafeRoutineDirector | None = None,
        weather_director: SafeWeatherDirector | None = None,
        item_interaction_adapter=None,
    ) -> None:
        self.store: EventStoreBackend = database_backend_from_environment(database_path)
        self.intent_parser = intent_parser or StructuredIntentParser(
            DisabledModelAdapter()
        )
        self.narrator = narrator or SafeNarrator(DisabledNarrationAdapter())
        self.npc_decider = npc_decider or SafeNpcDecider(
            DisabledNpcDecisionAdapter()
        )
        self.routine_director = routine_director or SafeRoutineDirector(
            DisabledRoutineAdapter()
        )
        self.weather_director = weather_director or SafeWeatherDirector(
            DisabledWeatherAdapter()
        )
        self.turn_pipeline = AuthoritativeTurnPipeline(
            intent_parser=self.intent_parser,
            resolver=LegacyCommandResolver(
                item_interaction_adapter=item_interaction_adapter,
            ),
            npc_decider=self.npc_decider,
            routine_director=self.routine_director,
            weather_director=self.weather_director,
            narrator=self.narrator,
            projection_runner=DefaultProjectionRunner(),
        )

    def initialize(self) -> None:
        self.store.initialize()
        if not self.store.campaign_exists(GRAY_HARBOR_CAMPAIGN_ID):
            self.reset_gray_harbor()
        else:
            self.rebuild_campaign_memories(GRAY_HARBOR_CAMPAIGN_ID)

    def reset_gray_harbor(self) -> dict[str, object]:
        package = gray_harbor_scenario()
        events = gray_harbor_events()
        initial_state = replay(GRAY_HARBOR_CAMPAIGN_ID, events, 1)
        weather_result = self.weather_director.propose(
            initial_state,
            previous_world_time=initial_state.world_time,
        )
        events.extend(materialize_weather_events(
            initial_state,
            weather_result,
            events[-1].event_id,
        ))
        self.store.reset_campaign(
            GRAY_HARBOR_CAMPAIGN_ID,
            package.manifest.name,
            events,
            scenario_id=package.manifest.scenario_id,
            scenario_version=package.manifest.version,
            scenario_content_hash=package.content_hash,
        )
        self.rebuild_campaign_memories(GRAY_HARBOR_CAMPAIGN_ID)
        return self.get_state(GRAY_HARBOR_CAMPAIGN_ID)

    def rebuild_campaign_memories(self, campaign_id: str) -> int:
        """Recreate the non-authoritative memory index from confirmed events."""
        try:
            with self.store.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self.store.campaign_version(connection, campaign_id)
                events = self.store.load_events(connection, campaign_id)
                model = project_memory_read_model(campaign_id, events)
                self.store.replace_campaign_memory_model(
                    connection,
                    campaign_id,
                    model,
                )
                return len(model.memories)
        except KeyError as error:
            raise CampaignNotFoundError(campaign_id) from error

    def get_state(self, campaign_id: str) -> dict[str, object]:
        try:
            with self.store.connect() as connection:
                version = self.store.campaign_version(connection, campaign_id)
                events = self.store.load_events(connection, campaign_id)
        except KeyError as error:
            raise CampaignNotFoundError(campaign_id) from error
        return public_state(replay(campaign_id, events, version))

    def submit_turn(self, campaign_id: str, request: TurnRequest) -> dict[str, object]:
        command_id = f"cmd_{uuid4().hex}"
        turn_id = f"turn_{uuid4().hex}"
        player_message_id = f"msg_{uuid4().hex}"
        narrator_message_id = f"msg_{uuid4().hex}"

        try:
            with self.store.connect() as connection:
                previous = self.store.find_command_response(
                    connection,
                    campaign_id,
                    request.idempotency_key,
                )
                if previous is not None:
                    previous["replayed"] = True
                    return previous
                version = self.store.campaign_version(connection, campaign_id)
                if version != request.expected_state_version:
                    raise StateVersionConflictError(version)
                events = self.store.load_events(connection, campaign_id)
                state = replay(campaign_id, events, version)

            actor_id = (
                state.player_character_id
                if request.actor_id == "player"
                else request.actor_id
            )

            parse_result = self.turn_pipeline.parse(
                player_text=request.text,
                actor_id=actor_id,
                state=state,
                source_message_id=player_message_id,
            )
            command = parse_result.command
            memory_query = _npc_memory_query(campaign_id, command)
            memory_selection: MemorySelection | None = None
            memory_trace_id: str | None = None
            if memory_query is not None:
                with self.store.connect() as connection:
                    (
                        candidates,
                        candidate_total,
                        candidate_truncated,
                        expanded_ids,
                    ) = self.store.load_memory_candidates_with_stats(
                        connection,
                        memory_query,
                    )
                memory_selection = select_memories(
                    candidates,
                    memory_query,
                    candidate_total=candidate_total,
                    truncated=candidate_truncated,
                    expanded_ids=expanded_ids,
                )
                memory_trace_id = f"retrieval_{uuid4().hex}"

            # The complete pre-commit pipeline runs without a database write
            # lock. Version and idempotency are checked again before commit.
            preparation = self.turn_pipeline.prepare(
                campaign_id=campaign_id,
                actor_id=actor_id,
                player_text=request.text,
                source_message_id=player_message_id,
                command_id=command_id,
                state=state,
                events=events,
                state_version=version,
                memory_selection=memory_selection,
                parse_result=parse_result,
            )
            command = preparation.command
            npc_decision_result = preparation.npc_decision_result
            resolution = preparation.resolution
            predicted_state = preparation.predicted_state
            narration_result = preparation.narration_result

            with self.store.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")

                previous = self.store.find_command_response(
                    connection,
                    campaign_id,
                    request.idempotency_key,
                )
                if previous is not None:
                    previous["replayed"] = True
                    return previous

                locked_version = self.store.campaign_version(connection, campaign_id)
                if locked_version != request.expected_state_version:
                    raise StateVersionConflictError(locked_version)

                events = self.store.load_events(connection, campaign_id)
                state = replay(campaign_id, events, locked_version)
                self.store.append_message(connection, RawMessage(
                    message_id=player_message_id,
                    campaign_id=campaign_id,
                    turn_id=turn_id,
                    speaker_type="player",
                    speaker_id=actor_id,
                    message_kind="player_input",
                    content=request.text,
                    world_time=state.world_time,
                    authority="utterance_only",
                ))
                new_version = locked_version
                if resolution.events:
                    start_sequence = self.store.next_sequence(
                        connection,
                        campaign_id,
                    )
                    causal_event_ids, update_keys = _memory_anchor_requirements(
                        resolution.events
                    )
                    source_memory_ids, latest_update_memory_ids = (
                        self.store.load_memory_anchor_indexes(
                            connection,
                            campaign_id,
                            causal_event_ids,
                            update_keys,
                        )
                    )
                    self.store.append_events(
                        connection,
                        campaign_id,
                        turn_id,
                        command_id,
                        resolution.events,
                    )
                    self.store.link_event_sources(
                        connection,
                        [event.event_id for event in resolution.events],
                        player_message_id,
                    )
                    all_events = [*events, *resolution.events]
                    memory_delta = project_memory_delta(
                        campaign_id,
                        resolution.events,
                        state,
                        start_sequence=start_sequence,
                        scene_segment=_current_scene_segment(events),
                        source_memory_ids=source_memory_ids,
                        latest_update_memory_ids=latest_update_memory_ids,
                    )
                    self.store.append_memory_model_delta(
                        connection,
                        memory_delta,
                    )
                    new_version = self.store.update_campaign_version(
                        connection,
                        campaign_id,
                        locked_version,
                    )
                    events = all_events

                updated_state = replay(campaign_id, events, new_version)
                self.store.append_message(connection, RawMessage(
                    message_id=narrator_message_id,
                    campaign_id=campaign_id,
                    turn_id=turn_id,
                    speaker_type="narrator",
                    speaker_id="gm_narrator",
                    message_kind="narration",
                    content=narration_result.text,
                    world_time=updated_state.world_time,
                    authority="narration_only",
                    token_count=narration_result.audit.completion_tokens,
                ))
                response = self._response(
                    turn_id,
                    command_id,
                    player_message_id,
                    narrator_message_id,
                    locked_version,
                    resolution,
                    updated_state,
                    narration_result.text,
                    memory_trace_id,
                    memory_selection,
                )
                self.store.append_intent_attempt(
                    connection,
                    f"intent_{uuid4().hex}",
                    campaign_id,
                    turn_id,
                    command_id,
                    parse_result.audit.status,
                    parse_result.audit.provider_name,
                    parse_result.audit.model_name,
                    parse_result.audit.request_payload,
                    parse_result.audit.response_payload,
                    parse_result.audit.failure_code,
                    parse_result.audit.prompt_tokens,
                    parse_result.audit.completion_tokens,
                    parse_result.audit.total_tokens,
                    parse_result.audit.latency_ms,
                )
                self.store.append_narration_attempt(
                    connection,
                    f"narration_{uuid4().hex}",
                    campaign_id,
                    turn_id,
                    command_id,
                    narration_result.audit.status,
                    narration_result.audit.provider_name,
                    narration_result.audit.model_name,
                    narration_result.audit.request_payload,
                    narration_result.audit.response_payload,
                    narration_result.audit.failure_code,
                    narration_result.audit.prompt_tokens,
                    narration_result.audit.completion_tokens,
                    narration_result.audit.total_tokens,
                    narration_result.audit.latency_ms,
                )
                if npc_decision_result.audit.status != "not_applicable":
                    self.store.append_npc_decision_attempt(
                        connection,
                        f"npcdecision_{uuid4().hex}",
                        campaign_id,
                        turn_id,
                        command_id,
                        npc_decision_result.audit.status,
                        npc_decision_result.audit.provider_name,
                        npc_decision_result.audit.model_name,
                        npc_decision_result.audit.request_payload,
                        npc_decision_result.audit.response_payload,
                        npc_decision_result.audit.failure_code,
                        npc_decision_result.audit.prompt_tokens,
                        npc_decision_result.audit.completion_tokens,
                        npc_decision_result.audit.total_tokens,
                        npc_decision_result.audit.latency_ms,
                    )
                if preparation.routine_result.audit.status != "not_applicable":
                    self.store.append_routine_attempt(
                        connection,
                        f"routine_{uuid4().hex}",
                        campaign_id,
                        turn_id,
                        command_id,
                        preparation.routine_result.audit.status,
                        preparation.routine_result.audit.provider_name,
                        preparation.routine_result.audit.model_name,
                        preparation.routine_result.audit.request_payload,
                        preparation.routine_result.audit.response_payload,
                        list(preparation.routine_result.rejected),
                        preparation.routine_result.audit.failure_code,
                        preparation.routine_result.audit.metrics.prompt_tokens,
                        preparation.routine_result.audit.metrics.completion_tokens,
                        preparation.routine_result.audit.metrics.total_tokens,
                        preparation.routine_result.audit.metrics.latency_ms,
                    )
                if (
                    memory_query is not None
                    and memory_selection is not None
                    and memory_trace_id is not None
                ):
                    self.store.append_retrieval_trace(
                        connection,
                        memory_trace_id,
                        turn_id,
                        memory_query,
                        memory_selection,
                    )
                self.store.save_command(
                    connection,
                    command_id,
                    campaign_id,
                    request.idempotency_key,
                    request.expected_state_version,
                    request.model_dump(),
                    response,
                    resolution.status,
                    turn_id,
                    player_message_id,
                    narrator_message_id,
                )
                return response
        except KeyError as error:
            raise CampaignNotFoundError(campaign_id) from error
        except (sqlite3.IntegrityError, StoreIntegrityError):
            with self.store.connect() as connection:
                previous = self.store.find_command_response(
                    connection,
                    campaign_id,
                    request.idempotency_key,
                )
                if previous is None:
                    raise
                previous["replayed"] = True
                return previous

    def get_recent_messages(
        self,
        campaign_id: str,
        limit: int = 30,
    ) -> list[dict[str, object]]:
        try:
            with self.store.connect() as connection:
                self.store.campaign_version(connection, campaign_id)
                return self.store.load_messages(connection, campaign_id, limit)
        except KeyError as error:
            raise CampaignNotFoundError(campaign_id) from error

    def get_turn_detail(self, campaign_id: str, turn_id: str) -> dict[str, object]:
        try:
            with self.store.connect() as connection:
                self.store.campaign_version(connection, campaign_id)
                record = self.store.load_command_by_turn(connection, campaign_id, turn_id)
                if record is None:
                    raise TurnNotFoundError(turn_id)
                response = record["response"]
                return {
                    "campaign_id": campaign_id,
                    "turn_id": turn_id,
                    "command_id": record["command_id"],
                    "status": record["status"],
                    "state_version_before": record["expected_state_version"],
                    "state_version_after": response["state_version"],
                    "command": response["command"],
                    "messages": self.store.load_turn_messages(
                        connection, campaign_id, turn_id
                    ),
                    "events": self.store.load_turn_events(
                        connection, campaign_id, turn_id
                    ),
                    "intent_attempts": self.store.load_turn_intent_attempts(
                        connection, campaign_id, turn_id
                    ),
                    "npc_decision_attempts": (
                        self.store.load_turn_npc_decision_attempts(
                            connection,
                            campaign_id,
                            turn_id,
                        )
                    ),
                    "narration_attempts": self.store.load_turn_narration_attempts(
                        connection, campaign_id, turn_id
                    ),
                    "routine_attempts": self.store.load_turn_routine_attempts(
                        connection,
                        campaign_id,
                        turn_id,
                    ),
                    "retrieval_traces": self.store.load_turn_retrieval_traces(
                        connection, campaign_id, turn_id
                    ),
                    "scene_memory_summaries": (
                        self.store.load_turn_scene_summary_traces(
                            connection,
                            campaign_id,
                            turn_id,
                        )
                    ),
                    "trace": response["trace"],
                }
        except KeyError as error:
            raise CampaignNotFoundError(campaign_id) from error

    @staticmethod
    def _response(
        turn_id: str,
        command_id: str,
        player_message_id: str,
        narrator_message_id: str,
        state_version_before: int,
        resolution: Resolution,
        state: Projection,
        narration_text: str,
        memory_trace_id: str | None,
        memory_selection: MemorySelection | None,
    ) -> dict[str, object]:
        command = resolution.command
        return {
            "turn_id": turn_id,
            "status": resolution.status,
            "outcome": resolution.outcome,
            "state_version": state.state_version,
            "narrative": narration_text,
            "command": _command_dict(command_id, command),
            "reasons": [_reason_dict(reason) for reason in resolution.reasons],
            "visible_changes": resolution.visible_changes,
            "state": public_state(state),
            "replayed": False,
            "trace": {
                "command_id": command_id,
                "player_message_id": player_message_id,
                "narrator_message_id": narrator_message_id,
                "event_ids": [event.event_id for event in resolution.events],
                "state_version_before": state_version_before,
                "state_version_after": state.state_version,
                "memory_retrieval": (
                    {
                        "trace_id": memory_trace_id,
                        "route": memory_selection.route,
                        "candidate_count": len(memory_selection.candidate_ids),
                        "selected_ids": [
                            memory.memory_id for memory in memory_selection.selected
                        ],
                        "rejected_count": len(memory_selection.rejected),
                    }
                    if memory_trace_id is not None and memory_selection is not None
                    else None
                ),
            },
        }


def _npc_memory_query(
    campaign_id: str,
    command: ParsedCommand,
) -> MemoryQuery | None:
    if command.action_type != "offer_item" or command.target_id is None:
        return None
    return MemoryQuery(
        campaign_id=campaign_id,
        purpose="npc_decision",
        perspective_kind="npc",
        perspective_id=command.target_id,
        entity_ids=(command.actor_id, command.target_id),
        event_types=tuple(sorted(MEMORY_EVENT_TYPES)),
        time_mode="latest",
        limit=12,
        character_budget=2_400,
    )


def _current_scene_segment(events: list[Event]) -> int:
    segment = 0
    for event in events:
        if event.event_type == "scene.started":
            segment = 0
        elif event.event_type == "scene.location_changed":
            segment += 1
    return segment


def _memory_anchor_requirements(
    events: list[Event],
) -> tuple[set[str], set[str]]:
    causal_event_ids: set[str] = set()
    update_keys: set[str] = set()
    for event in events:
        if event.event_type not in MEMORY_EVENT_TYPES:
            continue
        source_event_id = event.payload.get("sourceEventId")
        if source_event_id:
            causal_event_ids.add(str(source_event_id))
        if event.event_type == "character.moved":
            character_id = event.payload.get("characterId")
            if character_id:
                update_keys.add(f"character_location:{character_id}")
    return causal_event_ids, update_keys


def _command_dict(command_id: str, command: ParsedCommand) -> dict[str, object]:
    return {
        "action_id": command_id,
        "action_type": command.action_type,
        "actor_id": command.actor_id,
        "target_id": command.target_id,
        "target_ids": [command.target_id] if command.target_id else [],
        "parameters": command.parameters,
        "original_text": command.original_text,
        "claimed_outcome": command.claimed_outcome,
        "authority": command.authority,
        "resolution_required": command.resolution_required,
        "source_message_ids": list(command.source_message_ids),
        "parser_source": command.parser_source,
        "parser_model": command.parser_model,
        "parser_failure_code": command.parser_failure_code,
    }


def _reason_dict(reason: DecisionReason) -> dict[str, object]:
    return {
        "code": reason.code,
        "label": reason.label,
        "direction": reason.direction,
        "value": reason.value,
        "source_event_id": reason.source_event_id,
    }
