from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TurnRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=120)
    expected_state_version: int = Field(ge=0)
    actor_id: str = "player"
    text: str = Field(min_length=1, max_length=4000)


class ParsedCommandSchema(BaseModel):
    action_id: str
    action_type: str
    actor_id: str
    target_id: str | None
    target_ids: list[str]
    parameters: dict[str, Any]
    original_text: str
    claimed_outcome: str | None
    authority: Literal["player", "system", "world"]
    resolution_required: bool
    source_message_ids: list[str]
    parser_source: Literal["local", "model", "model_fallback"] = "local"
    parser_model: str | None = None
    parser_failure_code: str | None = None


class ReasonSchema(BaseModel):
    code: str
    label: str
    direction: Literal["positive", "negative", "neutral"]
    value: int | None = None
    source_event_id: str | None = None


class TurnResponse(BaseModel):
    turn_id: str
    status: Literal["committed", "rejected"]
    outcome: str
    state_version: int
    narrative: str
    command: ParsedCommandSchema
    reasons: list[ReasonSchema]
    visible_changes: list[str]
    state: dict[str, Any]
    replayed: bool = False
    trace: "TurnTraceSchema"


class TurnTraceSchema(BaseModel):
    command_id: str
    player_message_id: str
    narrator_message_id: str
    event_ids: list[str]
    state_version_before: int
    state_version_after: int


class MessageSchema(BaseModel):
    message_id: str
    campaign_id: str
    turn_id: str
    sequence: int
    speaker_type: Literal["player", "narrator", "system"]
    speaker_id: str
    message_kind: Literal["player_input", "narration", "system"]
    content: str
    world_time: int
    authority: Literal["utterance_only", "narration_only", "system_record"]
    token_count: int | None
    recorded_at: str


class EventSourceSchema(BaseModel):
    message_id: str
    source_kind: str


class TurnEventSchema(BaseModel):
    event_id: str
    sequence: int
    event_type: str
    schema_version: int
    world_time: int
    actor_id: str
    causation_id: str
    correlation_id: str
    payload: dict[str, Any]
    recorded_at: str
    sources: list[EventSourceSchema]


class TurnDetailResponse(BaseModel):
    campaign_id: str
    turn_id: str
    command_id: str
    status: str
    state_version_before: int
    state_version_after: int
    command: ParsedCommandSchema
    messages: list[MessageSchema]
    events: list[TurnEventSchema]
    intent_attempts: list["IntentAttemptSchema"]
    npc_decision_attempts: list["NpcDecisionAttemptSchema"]
    routine_attempts: list["RoutineAttemptSchema"]
    narration_attempts: list["NarrationAttemptSchema"]
    retrieval_traces: list["RetrievalTraceSchema"]
    scene_memory_summaries: list["SceneMemorySummarySchema"]
    trace: TurnTraceSchema


class RetrievalTraceSchema(BaseModel):
    trace_id: str
    purpose: Literal["npc_decision", "debug"]
    perspective: dict[str, str]
    query: dict[str, Any]
    candidate_ids: list[str]
    rejected: list[dict[str, str]]
    selected_ids: list[str]
    used_characters: int
    route: Literal["episodic_memory", "current_state_required"]
    candidate_total: int
    candidate_limit: int
    truncated: bool
    expanded_ids: list[str]
    created_at: str


class SceneMemorySummarySchema(BaseModel):
    summary_id: str
    scene_id: str
    segment_index: int
    location_id: str | None
    schema_version: int
    sequence_range: list[int]
    world_time_range: list[int]
    generator: str
    generator_version: int
    status: Literal["rolling", "closed"]
    resolved_count: int
    unresolved_count: int
    source_count: int


class IntentAttemptSchema(BaseModel):
    attempt_id: str
    campaign_id: str
    turn_id: str
    command_id: str
    status: Literal["local", "model_accepted", "model_fallback"]
    provider_name: str | None
    model_name: str | None
    request: dict[str, Any] | None
    response: dict[str, Any] | None
    failure_code: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int | None
    created_at: str


class NarrationAttemptSchema(BaseModel):
    attempt_id: str
    campaign_id: str
    turn_id: str
    command_id: str
    status: Literal["local", "model_accepted", "model_fallback"]
    provider_name: str | None
    model_name: str | None
    request: dict[str, Any] | None
    response: dict[str, Any] | None
    failure_code: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int | None
    created_at: str


class RoutineAttemptSchema(BaseModel):
    attempt_id: str
    campaign_id: str
    turn_id: str
    command_id: str
    status: Literal["not_applicable", "local", "model_accepted", "model_fallback"]
    provider_name: str | None
    model_name: str | None
    request: dict[str, Any] | None
    response: dict[str, Any] | None
    rejected: list[dict[str, str]]
    failure_code: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int | None
    created_at: str


class NpcDecisionAttemptSchema(BaseModel):
    attempt_id: str
    campaign_id: str
    turn_id: str
    command_id: str
    status: Literal["local", "model_accepted", "model_fallback"]
    provider_name: str | None
    model_name: str | None
    request: dict[str, Any] | None
    response: dict[str, Any] | None
    failure_code: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int | None
    created_at: str


class CampaignResetResponse(BaseModel):
    campaign_id: str
    state: dict[str, Any]
