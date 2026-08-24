from __future__ import annotations

from dataclasses import dataclass
import json
import os
from time import perf_counter, sleep
from typing import Any, Literal, Mapping, Protocol, Sequence

import httpx

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trpg_server.core.state import Event, Projection
from trpg_server.ai.platform.contracts import ModelCallMetrics
from trpg_server.ai.platform.deepseek import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    TRANSIENT_STATUS_CODES,
    DeepSeekAdapterError,
    DeepSeekSettings,
)


StoryImpact = Literal["routine", "soft", "story", "major"]
RoutineOutcome = Literal[
    "success",
    "partial",
    "nothing_found",
    "unavailable",
    "declined",
    "deferred",
]


class RoutineModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoutineCandidate(RoutineModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    candidate_id: str = Field(
        alias="candidateId",
        pattern=r"^[a-z][a-z0-9_:-]{2,120}$",
    )
    source_affordance_id: str = Field(alias="sourceAffordanceId", min_length=1)
    location_id: str = Field(alias="locationId", min_length=1)
    action_kind: Literal[
        "search",
        "commerce",
        "meal",
        "social",
        "work",
        "rest",
        "observe",
    ] = Field(alias="actionKind")
    outcome: RoutineOutcome
    story_impact: StoryImpact = Field(alias="storyImpact")
    narrative_weight: Literal["low", "medium"] = Field(
        default="low",
        alias="narrativeWeight",
    )
    time_minutes: int = Field(alias="timeMinutes", ge=0, le=1440)
    temporary_entity_kind: Literal["vendor", "customer", "worker", "visitor"] | None = Field(
        default=None,
        alias="temporaryEntityKind",
    )
    summary: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def story_boundary_is_explicit(self) -> RoutineCandidate:
        if self.story_impact in {"story", "major"}:
            raise ValueError("routine candidates cannot directly carry story or major impact")
        return self


class RoutineProposal(RoutineModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    candidates: list[RoutineCandidate] = Field(default_factory=list, max_length=4)
    confidence: float = Field(ge=0, le=1)


class RoutineContext(RoutineModel):
    actor_id: str = Field(alias="actorId")
    current_location_id: str = Field(alias="currentLocationId")
    current_location_name: str = Field(alias="currentLocationName")
    player_text: str = Field(alias="playerText", max_length=4000)
    action_type: str = Field(alias="actionType")
    observed_opportunities: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="observedOpportunities",
        max_length=8,
    )
    visible_items: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="visibleItems",
        max_length=30,
    )


class RoutineRequest(RoutineModel):
    system_instruction: str = Field(alias="systemInstruction")
    context: RoutineContext


class RoutineAdapter(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def model_name(self) -> str | None: ...

    @property
    def provider_name(self) -> str | None: ...

    def propose(
        self,
        request: RoutineRequest,
    ) -> RoutineProposal | dict[str, Any] | "RoutineAdapterResult": ...


@dataclass(frozen=True, slots=True)
class RoutineAdapterResult:
    output: RoutineProposal | dict[str, Any]
    metrics: ModelCallMetrics = ModelCallMetrics()


class DisabledRoutineAdapter:
    @property
    def available(self) -> bool:
        return False

    @property
    def model_name(self) -> None:
        return None

    @property
    def provider_name(self) -> None:
        return None

    def propose(self, request: RoutineRequest) -> RoutineProposal:
        del request
        raise RuntimeError("routine model adapter is disabled")


@dataclass(frozen=True, slots=True)
class RoutineValidation:
    accepted: bool
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class RoutineAudit:
    status: Literal["not_applicable", "local", "model_accepted", "model_fallback"]
    provider_name: str | None
    model_name: str | None
    request_payload: dict[str, Any] | None
    response_payload: dict[str, Any] | None
    failure_code: str | None
    metrics: ModelCallMetrics = ModelCallMetrics()


@dataclass(frozen=True, slots=True)
class RoutineProposalResult:
    accepted: tuple[RoutineCandidate, ...]
    rejected: tuple[dict[str, str], ...]
    audit: RoutineAudit


@dataclass(frozen=True, slots=True)
class SafeRoutineDirector:
    adapter: RoutineAdapter
    minimum_confidence: float = 0.7

    def propose(
        self,
        state: Projection,
        command: Any,
    ) -> RoutineProposalResult:
        context = build_routine_context(state, command)
        if context is None:
            return RoutineProposalResult(
                (),
                (),
                RoutineAudit("not_applicable", None, None, None, None, None),
            )
        request_payload: dict[str, Any] | None = None
        response_payload: dict[str, Any] | None = None
        if not self.adapter.available:
            return RoutineProposalResult(
                (),
                (),
                RoutineAudit("local", None, None, None, None, None),
            )
        try:
            request = RoutineRequest(
                systemInstruction=_routine_system_instruction(),
                context=context,
            )
            request_payload = request.model_dump(by_alias=True)
            raw = self.adapter.propose(request)
            metrics = raw.metrics if isinstance(raw, RoutineAdapterResult) else ModelCallMetrics()
            output = raw.output if isinstance(raw, RoutineAdapterResult) else raw
            response_payload = output.model_dump(by_alias=True) if isinstance(output, RoutineProposal) else dict(output)
            proposal = output if isinstance(output, RoutineProposal) else RoutineProposal.model_validate(output)
            if proposal.confidence < self.minimum_confidence:
                raise ValueError("routine confidence is too low")
            accepted: list[RoutineCandidate] = []
            rejected: list[dict[str, str]] = []
            for candidate in proposal.candidates:
                validation = validate_routine_candidate(state, candidate)
                if validation.accepted and len(accepted) < 1:
                    accepted.append(candidate)
                else:
                    rejected.append({"candidateId": candidate.candidate_id, "reason": validation.code})
            return RoutineProposalResult(
                tuple(accepted),
                tuple(rejected),
                RoutineAudit(
                    "model_accepted",
                    self.adapter.provider_name,
                    self.adapter.model_name,
                    request_payload,
                    response_payload,
                    None,
                    metrics,
                ),
            )
        except Exception as error:
            return RoutineProposalResult(
                (),
                (),
                RoutineAudit(
                    "model_fallback",
                    self.adapter.provider_name,
                    self.adapter.model_name,
                    request_payload,
                    response_payload,
                    type(error).__name__,
                ),
            )


def build_routine_context(state: Projection, command: Any) -> RoutineContext | None:
    if command.action_type not in {"search_location", "environment_action"}:
        return None
    location_id = state.character_locations.get(command.actor_id)
    if location_id is None:
        return None
    location = state.locations.get(location_id)
    if location is None:
        return None
    opportunities = [
        {
            "opportunityId": value.opportunity_id,
            "locationId": value.location_id,
            "actionKind": value.action_kind,
            "resourceKind": value.resource_kind,
            "storyImpactCeiling": value.story_impact_ceiling,
        }
        for value in state.observed_affordances.values()
        if value.location_id == location_id
    ]
    opportunities.extend(
        {
            "opportunityId": value.affordance_id,
            "locationId": value.location_id,
            "actionKind": action_kind,
            "resourceKind": category,
            "storyImpactCeiling": value.story_impact_ceiling,
            "source": "v42_catalog",
        }
        for value in state.catalog_affordances.values()
        if value.location_id == location_id
        for action_kind in value.action_kinds
        for category in value.resource_categories[:4]
    )
    visible_items = [
        {
            "itemId": item.item_id,
            "name": item.name,
            "category": item.category,
            "quantity": item.quantity,
            "condition": item.condition,
        }
        for item in state.items.values()
        if state.containers.get(item.container_id, None) is not None
        and state.containers[item.container_id].location_id == location_id
    ]
    return RoutineContext(
        actorId=command.actor_id,
        currentLocationId=location_id,
        currentLocationName=location.name,
        playerText=command.original_text,
        actionType=command.action_type,
        observedOpportunities=opportunities,
        visibleItems=visible_items,
    )


def _routine_system_instruction() -> str:
    return (
        "你是灰港日常候选生成器。只输出 RoutineProposal JSON。"
        "你只能描述当前地点已观察到的普通生活机会；不得创建主线、关键证据、永久人物、永久地点、通缉或重大关系后果。"
        "storyImpact 只能是 routine 或 soft；不得提议创建、交付或修改任何物品。"
        "候选不是事实，程序会再次校验。"
    )


@dataclass(slots=True)
class DeepSeekRoutineAdapter:
    settings: DeepSeekSettings
    transport: httpx.BaseTransport | None = None

    @property
    def available(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return self.settings.model

    @property
    def provider_name(self) -> str:
        return "deepseek"

    def propose(self, request: RoutineRequest) -> RoutineAdapterResult:
        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": f"{request.system_instruction}严格返回 JSON。"},
                {"role": "user", "content": json.dumps(request.context.model_dump(by_alias=True), ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": self.settings.thinking_mode},
            "max_tokens": self.settings.max_tokens,
            "temperature": 0,
        }
        started = perf_counter()
        response: httpx.Response | None = None
        try:
            with httpx.Client(
                timeout=self.settings.timeout_seconds,
                transport=self.transport,
                headers={
                    "Authorization": f"Bearer {self.settings.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "ai-trpg-routine-director/0.1",
                },
            ) as client:
                for attempt in range(self.settings.max_attempts):
                    response = client.post(
                        f"{self.settings.base_url}/chat/completions",
                        json=payload,
                    )
                    if response.status_code not in TRANSIENT_STATUS_CODES or attempt + 1 >= self.settings.max_attempts:
                        break
                    if self.settings.retry_delay_seconds:
                        sleep(self.settings.retry_delay_seconds)
        except httpx.TimeoutException as error:
            raise TimeoutError("DeepSeek routine request timed out") from error
        except httpx.HTTPError as error:
            raise DeepSeekAdapterError("DeepSeek routine request failed") from error
        if response is None or not response.is_success:
            raise DeepSeekAdapterError("DeepSeek routine request failed")
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        output = json.loads(content)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return RoutineAdapterResult(
            output=output,
            metrics=ModelCallMetrics(
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                latency_ms=max(0, round((perf_counter() - started) * 1000)),
            ),
        )


def routine_director_from_environment(
    environment: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> SafeRoutineDirector:
    values = environment if environment is not None else os.environ
    enabled = values.get("TRPG_ROUTINE_MODEL_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return SafeRoutineDirector(DisabledRoutineAdapter())
    return SafeRoutineDirector(
        DeepSeekRoutineAdapter(DeepSeekSettings.from_environment(values), transport=transport),
        minimum_confidence=float(values.get("TRPG_ROUTINE_MINIMUM_CONFIDENCE", "0.7")),
    )


def validate_routine_candidate(
    state: Projection,
    candidate: RoutineCandidate,
) -> RoutineValidation:
    """Validate an AI candidate without mutating authoritative state."""
    actor_location = state.character_locations.get(state.player_character_id)
    if actor_location != candidate.location_id:
        return RoutineValidation(False, "location_mismatch", "候选地点不是玩家当前所在地点")
    observed = state.observed_affordances.get(candidate.source_affordance_id)
    catalog = state.catalog_affordances.get(candidate.source_affordance_id)
    if observed is None and catalog is None:
        return RoutineValidation(False, "missing_affordance_source", "候选没有对应的已观察环境机会或 V4.2 地点机会")
    if observed is not None:
        if observed.location_id != candidate.location_id:
            return RoutineValidation(False, "affordance_location_mismatch", "机会来源与候选地点不一致")
        if observed.action_kind != candidate.action_kind:
            return RoutineValidation(False, "affordance_action_mismatch", "候选行动类型不符合来源机会")
        ceiling = observed.story_impact_ceiling
    else:
        if catalog.location_id != candidate.location_id:
            return RoutineValidation(False, "affordance_location_mismatch", "V4.2 机会来源与候选地点不一致")
        if candidate.action_kind not in catalog.action_kinds:
            return RoutineValidation(False, "affordance_action_mismatch", "候选行动类型不符合 V4.2 地点机会")
        ceiling = catalog.story_impact_ceiling
    if candidate.story_impact in {"story", "major"}:
        return RoutineValidation(False, "story_impact_forbidden", "日常候选不能直接推进主线或重大剧情")
    if candidate.story_impact == "soft" and ceiling == "routine":
        return RoutineValidation(False, "story_impact_above_ceiling", "候选影响超过地点机会边界")
    return RoutineValidation(True, "accepted", "候选处于已观察机会和日常影响边界内")


def materialize_routine_candidates(
    state: Projection,
    candidates: Sequence[RoutineCandidate],
    source_event_id: str,
) -> list[Event]:
    """Turn validated routine outcomes into auditable, non-item events.

    Routine proposals do not carry item records. The item domain requires an
    atlas-backed definition and a complete 15-field instance candidate, so an
    AI daily proposal cannot create an item by itself.
    """
    events: list[Event] = []
    for candidate in candidates:
        validation = validate_routine_candidate(state, candidate)
        if not validation.accepted or candidate.outcome != "success":
            continue
        outcome_event = Event(
            _routine_event_id(),
            "routine.outcome_confirmed",
            "system",
            state.world_time,
            {
                "candidateId": candidate.candidate_id,
                "sourceAffordanceId": candidate.source_affordance_id,
                "locationId": candidate.location_id,
                "actionKind": candidate.action_kind,
                "storyImpact": candidate.story_impact,
                "sourceEventId": source_event_id,
            },
        )
        events.append(outcome_event)
    return events


def _routine_event_id() -> str:
    from uuid import uuid4

    return f"evt_{uuid4().hex}"
