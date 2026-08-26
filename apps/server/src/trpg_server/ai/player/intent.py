from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trpg_server.ai.platform.contracts import ModelCallMetrics
from trpg_server.core.state import ParsedCommand, Projection
from trpg_server.behavior.intent_router import interpret_player_text
from trpg_server.story.investigation import evaluate_inquiry, evaluate_inspection
from trpg_server.locations.movement import exit_is_visible_to
from trpg_server.map.traversal import map_exit_is_allowed


MODEL_ACTION_TYPES = (
    "move",
    "inspect_item",
    "ask_topic",
    "speak",
    "wait",
    "investigate_location",
)

LOCAL_AUTHORITY_ACTION_TYPES = {
    "offer_item",
    "request_item",
    "claim_past_gift",
    "claim_item_possession",
    "search_location",
    "take_item",
    "equip_item",
    "unequip_item",
    "purchase_item",
    "consume_item",
    "combine_items",
    "item_interaction",
    "store_item",
    "retrieve_item",
    "repair_item",
    "environment_action",
    "wait",
}


class IntentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelActionCandidate(IntentModel):
    action_type: Literal[
        "move",
        "inspect_item",
        "ask_topic",
        "speak",
        "wait",
        "investigate_location",
    ]
    target_id: str | None = None
    interaction_id: str | None = None
    destination_id: str | None = None
    minutes: int | None = Field(default=None, ge=1, le=1440)
    speech_content: str | None = Field(default=None, max_length=4000)
    claimed_outcome: str | None = Field(default=None, max_length=200)


class ModelIntentProposal(IntentModel):
    schema_version: Literal[1] = 1
    actions: list[ModelActionCandidate] = Field(default_factory=list, max_length=4)
    needs_clarification: bool = False
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def actions_match_clarification(self) -> ModelIntentProposal:
        if self.needs_clarification and self.actions:
            raise ValueError("clarification proposal cannot include actions")
        if not self.needs_clarification and not self.actions:
            raise ValueError("non-clarification proposal requires an action")
        return self


class VisibleEntity(IntentModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)


class VisibleExit(IntentModel):
    destination_id: str
    name: str
    label: str
    aliases: list[str] = Field(default_factory=list)


class AvailableInteraction(IntentModel):
    interaction_id: str
    action_type: Literal["inspect_item", "ask_topic"]
    label: str
    target_id: str


class IntentContext(IntentModel):
    actor_id: str
    current_location: VisibleEntity
    visible_characters: list[VisibleEntity]
    actor_inventory: list[VisibleEntity]
    visible_exits: list[VisibleExit]
    available_interactions: list[AvailableInteraction]
    allowed_action_types: list[str]
    max_actions: int


class IntentParseRequest(IntentModel):
    system_instruction: str
    player_text: str
    context: IntentContext


class ModelAdapter(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def model_name(self) -> str | None: ...

    @property
    def provider_name(self) -> str | None: ...

    def parse_intent(
        self,
        request: IntentParseRequest,
    ) -> ModelIntentProposal | dict[str, Any] | ModelAdapterResult: ...


@dataclass(frozen=True, slots=True)
class ModelAdapterResult:
    output: ModelIntentProposal | dict[str, Any]
    metrics: ModelCallMetrics = ModelCallMetrics()


class DisabledModelAdapter:
    @property
    def available(self) -> bool:
        return False

    @property
    def model_name(self) -> str | None:
        return None

    @property
    def provider_name(self) -> str | None:
        return None

    def parse_intent(self, request: IntentParseRequest) -> ModelIntentProposal:
        del request
        raise RuntimeError("model adapter is disabled")


class IntentProposalError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class IntentParseAudit:
    status: Literal["local", "model_accepted", "model_fallback"]
    provider_name: str | None
    model_name: str | None
    request_payload: dict[str, Any] | None
    response_payload: dict[str, Any] | None
    failure_code: str | None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None


@dataclass(frozen=True, slots=True)
class IntentParseResult:
    command: ParsedCommand
    audit: IntentParseAudit


@dataclass(frozen=True, slots=True)
class StructuredIntentParser:
    adapter: ModelAdapter
    minimum_confidence: float = 0.55

    def parse(
        self,
        text: str,
        actor_id: str,
        state: Projection,
        source_message_id: str | None = None,
    ) -> ParsedCommand:
        return self.parse_with_audit(
            text,
            actor_id,
            state,
            source_message_id,
        ).command

    def parse_with_audit(
        self,
        text: str,
        actor_id: str,
        state: Projection,
        source_message_id: str | None = None,
    ) -> IntentParseResult:
        local_authority_command = interpret_player_text(
            text,
            actor_id,
            source_message_id,
            state,
        )
        if local_authority_command.action_type in LOCAL_AUTHORITY_ACTION_TYPES:
            return IntentParseResult(
                command=local_authority_command,
                audit=IntentParseAudit("local", None, None, None, None, None),
            )
        if not self.adapter.available:
            return IntentParseResult(
                command=local_authority_command,
                audit=IntentParseAudit("local", None, None, None, None, None),
            )

        request_payload: dict[str, Any] | None = None
        response_payload: dict[str, Any] | None = None
        metrics = ModelCallMetrics()
        try:
            context = build_intent_context(state, actor_id)
            request = IntentParseRequest(
                system_instruction=_system_instruction(),
                player_text=text,
                context=context,
            )
            request_payload = request.model_dump()
            adapter_result = self.adapter.parse_intent(request)
            raw: ModelIntentProposal | dict[str, Any]
            if isinstance(adapter_result, ModelAdapterResult):
                raw = adapter_result.output
                metrics = adapter_result.metrics
            else:
                raw = adapter_result
            response_payload = (
                raw.model_dump()
                if isinstance(raw, ModelIntentProposal)
                else dict(raw)
            )
            proposal = (
                raw
                if isinstance(raw, ModelIntentProposal)
                else ModelIntentProposal.model_validate(raw)
            )
            if proposal.confidence < self.minimum_confidence:
                raise IntentProposalError("low_confidence", "model confidence is too low")
            if proposal.needs_clarification:
                raise IntentProposalError(
                    "model_requested_clarification",
                    "model could not uniquely resolve the action",
                )
            command = _proposal_to_command(
                proposal,
                text,
                actor_id,
                state,
                source_message_id,
            )
            return IntentParseResult(
                command=_with_parser_metadata(
                    command,
                    "model",
                    self.adapter.model_name,
                    None,
                ),
                audit=IntentParseAudit(
                    "model_accepted",
                    getattr(self.adapter, "provider_name", None),
                    self.adapter.model_name,
                    request_payload,
                    response_payload,
                    None,
                    metrics.prompt_tokens,
                    metrics.completion_tokens,
                    metrics.total_tokens,
                    metrics.latency_ms,
                ),
            )
        except ValidationError:
            failure_code = "model_schema_invalid"
        except IntentProposalError as error:
            failure_code = error.code
        except TimeoutError:
            failure_code = "model_timeout"
        except Exception:
            failure_code = "model_adapter_error"

        fallback = interpret_player_text(text, actor_id, source_message_id, state)
        return IntentParseResult(
            command=_with_parser_metadata(
                fallback,
                "model_fallback",
                self.adapter.model_name,
                failure_code,
            ),
            audit=IntentParseAudit(
                "model_fallback",
                getattr(self.adapter, "provider_name", None),
                self.adapter.model_name,
                request_payload,
                response_payload,
                failure_code,
                metrics.prompt_tokens,
                metrics.completion_tokens,
                metrics.total_tokens,
                metrics.latency_ms,
            ),
        )


def build_intent_context(state: Projection, actor_id: str) -> IntentContext:
    location_id = state.character_locations.get(actor_id)
    if location_id is None or location_id not in state.locations:
        raise IntentProposalError("actor_location_unknown", "actor location is unknown")
    location = state.locations[location_id]

    visible_characters = [
        VisibleEntity(
            id=character_id,
            name=state.character_names.get(character_id, character_id),
            aliases=list(state.character_aliases.get(character_id, ())),
        )
        for character_id, character_location in sorted(state.character_locations.items())
        if character_id != actor_id and character_location == location_id
    ]
    actor_container_ids = {
        container.container_id
        for container in state.containers.values()
        if container.owner_character_id == actor_id
    }
    actor_inventory = [
        # The item atlas deliberately has no aliases field.  Names in this
        # context are factual labels from the authoritative item projection;
        # accepting model-invented aliases would weaken that boundary.
        VisibleEntity(id=item.item_id, name=item.name)
        for item in sorted(state.items.values(), key=lambda value: value.item_id)
        if item.container_id in actor_container_ids
    ]
    visible_exits = [
        VisibleExit(
            destination_id=exit_state.to_location_id,
            name=state.location_names.get(
                exit_state.to_location_id,
                exit_state.to_location_id,
            ),
            label=exit_state.label,
            aliases=list(state.locations[exit_state.to_location_id].aliases),
        )
        for exit_state in location.exits
        if exit_is_visible_to(state, actor_id, exit_state)
        and map_exit_is_allowed(
            state,
            location.location_id,
            exit_state.to_location_id,
            exit_state,
        )
    ]
    interactions: list[AvailableInteraction] = []
    for definition in sorted(
        state.inspection_definitions.values(),
        key=lambda value: value.interaction_id,
    ):
        if evaluate_inspection(state, actor_id, definition).allowed:
            interactions.append(AvailableInteraction(
                interaction_id=definition.interaction_id,
                action_type="inspect_item",
                label=definition.label,
                target_id=definition.target_item_id,
            ))
    for definition in sorted(
        state.inquiry_definitions.values(),
        key=lambda value: value.interaction_id,
    ):
        decision = evaluate_inquiry(state, actor_id, definition)
        if decision.allowed:
            interactions.append(AvailableInteraction(
                interaction_id=definition.interaction_id,
                action_type="ask_topic",
                label=definition.label,
                target_id=definition.target_character_id,
            ))

    return IntentContext(
        actor_id=actor_id,
        current_location=VisibleEntity(
            id=location.location_id,
            name=location.name,
            aliases=list(location.aliases),
        ),
        visible_characters=visible_characters,
        actor_inventory=actor_inventory,
        visible_exits=visible_exits,
        available_interactions=interactions,
        allowed_action_types=list(MODEL_ACTION_TYPES),
        max_actions=4,
    )


def _proposal_to_command(
    proposal: ModelIntentProposal,
    original_text: str,
    actor_id: str,
    state: Projection,
    source_message_id: str | None,
) -> ParsedCommand:
    context = build_intent_context(state, actor_id)
    components = [
        _candidate_to_command(
            candidate,
            original_text,
            actor_id,
            state,
            context,
            source_message_id,
        )
        for candidate in proposal.actions
    ]
    if len(components) == 1:
        return components[0]
    return ParsedCommand(
        action_type="compound_action",
        actor_id=actor_id,
        target_id=None,
        parameters={"components": [_command_payload(value) for value in components]},
        original_text=original_text,
        authority="system",
        source_message_ids=(source_message_id,) if source_message_id else (),
    )


def _candidate_to_command(
    candidate: ModelActionCandidate,
    original_text: str,
    actor_id: str,
    state: Projection,
    context: IntentContext,
    source_message_id: str | None,
) -> ParsedCommand:
    sources = (source_message_id,) if source_message_id else ()
    claimed_outcome = candidate.claimed_outcome or _scrub_claimed_outcome(original_text)

    if candidate.action_type == "move":
        destination_id = candidate.destination_id or candidate.target_id
        allowed_destinations = {
            value.destination_id: value for value in context.visible_exits
        }
        if destination_id not in allowed_destinations:
            raise IntentProposalError(
                "model_invalid_destination",
                "model referenced a destination outside visible exits",
            )
        destination = allowed_destinations[str(destination_id)]
        vague_references = ("那边", "那里", "那儿", "那处", "那个地方")
        grounded = (
            destination.label in original_text
            or _text_mentions_entity(
                original_text,
                [destination.name, *destination.aliases],
                allow_typo=True,
            )
        )
        if (
            any(marker in original_text for marker in vague_references)
            and not grounded
        ):
            raise IntentProposalError(
                "model_ambiguous_destination",
                "model guessed a destination from an ungrounded reference",
            )
        if not grounded:
            raise IntentProposalError(
                "model_ungrounded_destination",
                "model destination is not grounded in the player's text",
            )
        return ParsedCommand(
            "move",
            actor_id,
            destination_id,
            {"destinationId": destination_id},
            original_text,
            claimed_outcome,
            "player",
            True,
            sources,
        )

    if candidate.action_type in {"inspect_item", "ask_topic"}:
        interaction_id = candidate.interaction_id
        allowed = {
            value.interaction_id: value
            for value in context.available_interactions
            if value.action_type == candidate.action_type
        }
        if interaction_id not in allowed:
            raise IntentProposalError(
                "model_invalid_interaction",
                "model referenced an unavailable interaction",
            )
        interaction = allowed[str(interaction_id)]
        if candidate.target_id not in {None, interaction.target_id}:
            raise IntentProposalError(
                "model_interaction_target_mismatch",
                "model interaction target does not match the authoritative definition",
            )
        if (
            candidate.action_type == "ask_topic"
            and not _text_mentions_character(
                original_text,
                state,
                interaction.target_id,
            )
        ):
            raise IntentProposalError(
                "model_ambiguous_character",
                "model guessed who the player wanted to ask",
            )
        return ParsedCommand(
            candidate.action_type,
            actor_id,
            interaction.target_id,
            {"interactionId": interaction.interaction_id},
            original_text,
            claimed_outcome,
            "system",
            True,
            sources,
        )

    if candidate.action_type == "speak":
        visible_ids = {value.id for value in context.visible_characters}
        if candidate.target_id is not None and candidate.target_id not in visible_ids:
            raise IntentProposalError(
                "model_invalid_character",
                "model referenced a character who is not visible here",
            )
        target_id = candidate.target_id
        audience = None
        if (
            target_id is not None
            and not _text_mentions_character(original_text, state, target_id)
        ):
            if any(value in original_text for value in ("大家", "所有人", "屋里的人")):
                target_id = None
                audience = "room"
            else:
                raise IntentProposalError(
                    "model_ambiguous_character",
                    "model guessed a speech target without a textual reference",
                )
        if (
            target_id is None
            and any(value in original_text for value in ("大家", "所有人", "屋里的人"))
        ):
            audience = "room"
        speech_content = candidate.speech_content or original_text
        parameters = {"speechContent": speech_content}
        if audience is not None:
            parameters["audience"] = audience
        return ParsedCommand(
            "speak",
            actor_id,
            target_id,
            parameters,
            original_text,
            claimed_outcome,
            "player",
            False,
            sources,
        )

    if candidate.action_type == "wait":
        if candidate.minutes is None:
            raise IntentProposalError("model_missing_minutes", "wait requires minutes")
        return ParsedCommand(
            "wait",
            actor_id,
            None,
            {"minutes": candidate.minutes},
            original_text,
            claimed_outcome,
            "player",
            True,
            sources,
        )

    if candidate.action_type == "investigate_location":
        if candidate.target_id not in {None, context.current_location.id}:
            raise IntentProposalError(
                "model_invalid_investigation_location",
                "model referenced a different location",
            )
        return ParsedCommand(
            "investigate_location",
            actor_id,
            context.current_location.id,
            {},
            original_text,
            claimed_outcome,
            "player",
            True,
            sources,
        )

    raise IntentProposalError("model_action_not_allowed", "model action is not allowed")


def _scrub_claimed_outcome(text: str) -> str | None:
    markers = (
        "成功",
        "已经",
        "拿到",
        "发现",
        "确认",
        "证明",
        "说服",
        "杀死",
        "打开了",
        "偷到",
    )
    return "player_claimed_result" if any(value in text for value in markers) else None


def _text_mentions_character(
    text: str,
    state: Projection,
    character_id: str,
) -> bool:
    return _text_mentions_entity(
        text,
        [
            state.character_names.get(character_id, ""),
            *state.character_aliases.get(character_id, ()),
        ],
    )


def _text_mentions_entity(
    text: str,
    terms: list[str],
    allow_typo: bool = False,
) -> bool:
    normalized = "".join(text.lower().split())
    for term in terms:
        candidate = "".join(term.lower().split())
        if not candidate:
            continue
        if candidate in normalized:
            return True
        if allow_typo and len(candidate) >= 2:
            for index in range(len(normalized) - len(candidate) + 1):
                window = normalized[index:index + len(candidate)]
                if window[0] != candidate[0]:
                    continue
                if sum(left != right for left, right in zip(window, candidate)) == 1:
                    return True
    return False


def _with_parser_metadata(
    command: ParsedCommand,
    parser_source: Literal["local", "model", "model_fallback"],
    parser_model: str | None,
    failure_code: str | None,
) -> ParsedCommand:
    return ParsedCommand(
        action_type=command.action_type,
        actor_id=command.actor_id,
        target_id=command.target_id,
        parameters=command.parameters,
        original_text=command.original_text,
        claimed_outcome=command.claimed_outcome,
        authority=command.authority,
        resolution_required=command.resolution_required,
        source_message_ids=command.source_message_ids,
        parser_source=parser_source,
        parser_model=parser_model,
        parser_failure_code=failure_code,
    )


def _command_payload(command: ParsedCommand) -> dict[str, object]:
    return {
        "action_type": command.action_type,
        "actor_id": command.actor_id,
        "target_id": command.target_id,
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


def _system_instruction() -> str:
    return (
        "你是跑团意图解析器，只把玩家原文映射为候选动作。"
        "不得决定成功、创造实体、补写历史或泄露上下文以外的信息。"
        "只能使用 context.allowed_action_types、visible entities、visible exits "
        "和 available interactions 中的 ID。结果性声明写入 claimed_outcome，"
        "不能当作已经发生。移动目标必须由原文中的地点名称或别名支持；"
        "询问或定向说话必须由原文中的角色名称或别名支持。‘那边’‘那里’"
        "‘她’等没有上下文锚点的指代必须请求澄清，不得从列表中猜目标。"
        "输出必须符合 ModelIntentProposal。"
    )


def serialized_model_request(request: IntentParseRequest) -> str:
    """Stable JSON payload for adapters and audit tests; contains visible context only."""
    return json.dumps(request.model_dump(), ensure_ascii=False, sort_keys=True)
