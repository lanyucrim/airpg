"""Authoritative orchestration for character/item/location interactions.

This module is deliberately small at the domain boundaries: item rules only
validate concrete instances, character rules only resolve the d20 check, and
location rules only validate the current furniture/location anchor.  The
function below composes those pure pieces into an event plan; it never mutates
the projection or writes storage.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import random
from typing import Any, Protocol
from uuid import uuid4

from trpg_server.ai.platform.item_interaction import (
    item_interaction_adapter_from_environment,
)
from trpg_server.characters.checks import (
    PhysicalRequirements,
    ability_check_input_from_profile,
    physical_requirements_from_injuries,
    resolve_ability_check,
)
from trpg_server.characters.body import HAND_SLOTS
from trpg_server.characters.inventory import inventory_container_id
from trpg_server.core.state import DecisionReason, Event, ParsedCommand, Projection, Resolution
from trpg_server.items.ai_items.era import EraTechnologyProfile
from trpg_server.items.ai_items.generation import DailyItemDefinitionCatalog
from trpg_server.items.ai_items.references import DailyItemReferenceTable
from trpg_server.items.ai_items.recipes import (
    GeneratedRecipeCatalog,
    RecipeAssessmentRequest,
    resolve_item_recipe,
)
from trpg_server.items.commands import (
    build_item_transferred_event,
)
from trpg_server.items.interaction import (
    ItemInteractionAdapter,
    ItemInteractionAdapterResult,
    ItemInteractionCandidate,
    InteractionRequest,
    parse_interaction_candidate,
    validate_candidate_evidence,
)
from trpg_server.items.inventory import (
    item_is_at_location,
    item_is_owned_by,
    validate_container_capacity,
)
from trpg_server.items.equipment import item_equipment_profile
from trpg_server.items.models import ItemInstance
from trpg_server.items.provenance import item_is_usable_interaction_instance
from trpg_server.items.provenance import build_confirmed_item_creation_events
from trpg_server.items.wear import resolve_behavior_wear
from trpg_server.items.wear_events import build_item_wear_event
from trpg_server.ai.platform.item_wear import (
    ItemWearCandidate,
    ItemWearRequest,
    ItemWearError,
    item_wear_adapter_from_environment,
    parse_wear_candidate,
    validate_wear_candidate_evidence,
)
from trpg_server.items.recipe_models import RecipeIngredient
from trpg_server.items.recipes import (
    RecipeConversionInput,
    RecipeError,
    build_recipe_conversion_plan,
)


class ItemInteractionResolverError(ValueError):
    """A safe, player-facing interaction rejection."""


class RecipePlanResolver(Protocol):
    def __call__(
        self,
        state: Projection,
        actor_id: str,
        world_time: int,
        source_items: tuple[ItemInstance, ...],
        action_text: str,
        interaction_event_id: str,
    ) -> tuple[Event, ...]: ...


class InteractionEffectResolver(Protocol):
    """Domain-owned resolver for one approved item↔location/furniture effect."""

    def __call__(
        self,
        state: Projection,
        request: InteractionRequest,
        source_items: tuple[ItemInstance, ...],
        candidate: ItemInteractionCandidate,
        interaction_event: Event,
    ) -> tuple[Event, ...]: ...


@dataclass(frozen=True, slots=True)
class InteractionRuntime:
    """Optional dependencies used by tests and by the application service."""

    adapter: ItemInteractionAdapter | None = None
    rng: random.Random | Callable[[], int] | None = None
    recipe_plan_resolver: RecipePlanResolver | None = None
    effect_resolver: InteractionEffectResolver | None = None
    wear_adapter: Any | None = None
    wear_rng: random.Random | Callable[[], int] | None = None
    failure_minutes: int = 1

    def __post_init__(self) -> None:
        if type(self.failure_minutes) is not int or self.failure_minutes < 0:
            raise ValueError("failure_minutes must be a non-negative integer")


def resolve_item_interaction(
    state: Projection,
    command: ParsedCommand,
    *,
    runtime: InteractionRuntime | None = None,
    adapter: ItemInteractionAdapter | None = None,
    rng: random.Random | Callable[[], int] | None = None,
    recipe_plan_resolver: RecipePlanResolver | None = None,
    effect_resolver: InteractionEffectResolver | None = None,
) -> Resolution:
    """Resolve one item interaction into a validated, replayable event list."""

    runtime = runtime or InteractionRuntime(
        adapter=adapter,
        rng=rng,
        recipe_plan_resolver=recipe_plan_resolver,
            effect_resolver=effect_resolver,
            wear_adapter=None,
            wear_rng=rng,
        )
    if (
        adapter is not None
        or rng is not None
        or recipe_plan_resolver is not None
        or effect_resolver is not None
    ):
        runtime = InteractionRuntime(
            adapter=adapter if adapter is not None else runtime.adapter,
            rng=rng if rng is not None else runtime.rng,
            recipe_plan_resolver=(
                recipe_plan_resolver
                if recipe_plan_resolver is not None
                else runtime.recipe_plan_resolver
            ),
            effect_resolver=(
                effect_resolver
                if effect_resolver is not None
                else runtime.effect_resolver
            ),
            wear_adapter=runtime.wear_adapter,
            wear_rng=runtime.wear_rng,
            failure_minutes=runtime.failure_minutes,
        )
    try:
        request = _request_from_command(command)
        source_items, target_summary, auto_picked = _validate_request(state, request)
    except ItemInteractionResolverError as error:
        return _rejected(command, "rejected_precondition", str(error), str(error))

    candidate: ItemInteractionCandidate
    if request.operation in {"store", "retrieve"}:
        candidate = _deterministic_transfer_candidate(request)
    else:
        interaction_adapter = runtime.adapter
        if interaction_adapter is None:
            try:
                interaction_adapter = item_interaction_adapter_from_environment()
            except Exception:
                interaction_adapter = None
        model_candidate: ItemInteractionCandidate | None = None
        if interaction_adapter is None or not interaction_adapter.available:
            # A previously confirmed recipe may run without a network call.
            # No generic free-form action is allowed to fall through here.
            if request.operation == "combine" and _has_cached_recipe(request, source_items):
                model_candidate = _cached_recipe_candidate(request)
            else:
                return _rejected(
                    command,
                    "ai_unavailable",
                    "当前没有可用的物品交互判定模型；没有物品或时间发生变化。",
                    "物品交互模型不可用",
                )
        if model_candidate is not None:
            candidate = model_candidate
        else:
            try:
                # The adapter only proposes a bounded physical candidate. It
                # never receives a projection or an event sink.
                result = interaction_adapter.assess(
                    request,
                    tuple(_item_summary(item) for item in source_items),
                    target_summary,
                )
                output = (
                    result.output
                    if isinstance(result, ItemInteractionAdapterResult)
                    else result
                )
                allowed_abilities = tuple(
                    str(value.get("abilityId"))
                    for value in state.character_profiles.get(request.actor_id, {}).get(
                        "abilities", ()
                    )
                    if isinstance(value, Mapping) and value.get("abilityId")
                )
                candidate = parse_interaction_candidate(
                    output,
                    request,
                    allowed_ability_ids=allowed_abilities,
                )
                validate_candidate_evidence(
                    candidate,
                    tuple(_item_summary(item) for item in source_items),
                    target_summary,
                )
            except Exception as error:
                return _rejected(
                    command,
                    "rejected_physics",
                    "物品与目标的物理关系无法确认；没有物品或时间发生变化。",
                    f"物理候选无效: {type(error).__name__}",
                )
        if candidate.decision != "possible":
            reason = candidate.rejection_reason or "模型要求补充事实"
            return _rejected(command, "clarify" if candidate.decision == "clarify" else "rejected_physics", reason, reason)

    check = _resolve_check(state, request, source_items, candidate, runtime.rng)
    interaction_id = _interaction_id(command)
    audit = _interaction_event(
        interaction_id=interaction_id,
        request=request,
        status=("succeeded" if check.status == "not_required" else check.code),
        check=check,
        auto_picked=auto_picked,
        world_time=state.world_time,
    )

    if check.status == "blocked":
        audit.payload["status"] = "rejected_precondition"
        return Resolution(
            status="committed",
            outcome="rejected_precondition",
            narrative="你的身体状态无法完成这次操作；没有物品被消耗。",
            command=command,
            events=[audit],
            reasons=[DecisionReason("body_part_unavailable", "身体前置条件不满足", "negative", source_event_id=audit.event_id)],
            visible_changes=["记录交互判定；没有物品被消耗"],
        )
    if check.status == "failed":
        wear_events = _materialize_wear_events(
            state, request, source_items, candidate, audit, check, runtime
        )
        events = [audit, *wear_events]
        if runtime.failure_minutes:
            events.append(_time_event(state.world_time, runtime.failure_minutes, "item_interaction_failed"))
        return Resolution(
            status="committed",
            outcome="failed_check",
            narrative=(
                "你尝试了这次操作，但检定没有通过；没有物品被消耗。"
                + (f"这次尝试耗时 {runtime.failure_minutes} 分钟。" if runtime.failure_minutes else "")
            ),
            command=command,
            events=events,
            reasons=[DecisionReason("failed_check", "d20 检定未达到程序映射的 DC", "negative", source_event_id=audit.event_id)],
            visible_changes=["检定失败；没有物品被消耗"]
            + ([f"世界时间推进 {runtime.failure_minutes} 分钟"] if runtime.failure_minutes else []),
        )

    try:
        effect_events = _materialize_success(
            state,
            request,
            source_items,
            candidate,
            audit,
            runtime.recipe_plan_resolver,
            runtime.effect_resolver,
        )
    except (ItemInteractionResolverError, RecipeError, ValueError) as error:
        audit.payload["status"] = "unsupported_effect"
        return Resolution(
            status="committed",
            outcome="unsupported_effect",
            narrative=f"这次操作的目标效果尚未实现：{error}。没有物品被消耗。",
            command=command,
            events=[audit],
            reasons=[DecisionReason("unsupported_effect", str(error), "neutral", source_event_id=audit.event_id)],
            visible_changes=["目标效果未实现；没有物品被消耗"],
        )

    wear_events = _materialize_wear_events(
        state, request, source_items, candidate, audit, check, runtime
    )
    events = [audit, *wear_events, *effect_events, _time_event(state.world_time, 1, "item_interaction")]
    names = "、".join(item.name for item in source_items)
    feedback = (
        f"行动中从背包取用并拿起：{names}（仅限本次操作）。" if auto_picked else ""
    )
    return Resolution(
        status="committed",
        outcome="succeeded",
        narrative=f"{feedback}你完成了这次{_operation_label(request.operation)}。",
        command=command,
        events=events,
        reasons=[DecisionReason("interaction_succeeded", "物理候选与人物检定均通过", "positive", source_event_id=audit.event_id)],
        visible_changes=([feedback] if feedback else []) + [f"完成：{_operation_label(request.operation)}", "世界时间推进 1 分钟"],
    )


def _request_from_command(command: ParsedCommand) -> InteractionRequest:
    params = command.parameters
    action_type = command.action_type
    operation = str(params.get("operation", ""))
    if action_type == "combine_items":
        operation = "combine"
    elif action_type == "store_item":
        operation = "store"
    elif action_type == "retrieve_item":
        operation = "retrieve"
    if not operation:
        raise ItemInteractionResolverError("缺少交互操作类型")
    raw_ids = params.get("itemIds", params.get("sourceItemIds"))
    if raw_ids is None and command.target_id:
        raw_ids = [command.target_id]
    if not isinstance(raw_ids, (list, tuple)) or not raw_ids:
        raise ItemInteractionResolverError("没有明确的物品实例")
    source_ids = tuple(str(value) for value in raw_ids)
    target_id = str(params.get("targetId", command.target_id or ""))
    target_kind = str(params.get("targetKind", ""))
    if not target_kind:
        target_kind = "item" if operation == "combine" else "location"
    try:
        return InteractionRequest(
            actor_id=command.actor_id,
            source_item_ids=source_ids,
            target_kind=target_kind,  # type: ignore[arg-type]
            target_id=target_id,
            operation=operation,  # type: ignore[arg-type]
            action_text=command.original_text,
            required_ability_id=(
                str(params["requiredAbilityId"])
                if params.get("requiredAbilityId") is not None
                else None
            ),
            requested_effect_kind=(
                str(params["effectKind"])
                if params.get("effectKind") is not None
                else None
            ),
        )
    except (ValueError, TypeError) as error:
        raise ItemInteractionResolverError(str(error)) from error


def _validate_request(
    state: Projection,
    request: InteractionRequest,
) -> tuple[tuple[ItemInstance, ...], Mapping[str, Any], tuple[str, ...]]:
    actor_location = state.character_locations.get(request.actor_id)
    if actor_location is None:
        raise ItemInteractionResolverError("无法确认行动者当前位置")
    if request.target_kind == "furniture":
        furniture = state.containers.get(request.target_id)
        if furniture is None or furniture.kind != "furniture":
            raise ItemInteractionResolverError("目标家具不存在")
        if furniture.location_id != actor_location or not furniture.visible:
            raise ItemInteractionResolverError("目标家具不在当前位置或不可见")
        target_summary: Mapping[str, Any] = _furniture_summary(state, furniture.container_id)
    elif request.target_kind == "location":
        location = state.locations.get(request.target_id)
        if location is None or not _location_is_current(state, request.actor_id, request.target_id):
            raise ItemInteractionResolverError("目标地点不在当前位置")
        target_summary = _location_summary(location)
    else:
        target = state.items.get(request.target_id)
        if target is None:
            raise ItemInteractionResolverError("目标物品实例不存在")
        target_usable, target_reason = item_is_usable_interaction_instance(
            state,
            target,
        )
        if not target_usable:
            raise ItemInteractionResolverError(
                f"目标物品当前不可交互：{target_reason}"
            )
        # A free-form action still needs an observable target. Ownership is
        # sufficient for inventory/equipment items; otherwise the item must be
        # anchored at the actor's current location.
        target_owned = item_is_owned_by(state, target, request.actor_id)
        target_at_location = item_is_at_location(state, target, actor_location)
        target_container = state.containers.get(target.container_id)
        target_visible = not target_at_location or (
            target_container is None or target_container.visible
        )
        if not (target_owned or (target_at_location and target_visible)):
            raise ItemInteractionResolverError("目标物品不在行动者可访问范围内")
        target_summary = _item_summary(target)

    source_items: list[ItemInstance] = []
    auto_picked: list[str] = []
    equipped_ids = {
        str(value.get("itemId"))
        for value in state.character_equipment.get(request.actor_id, {}).values()
        if value.get("itemId")
    }
    for item_id in request.source_item_ids:
        item = state.items.get(item_id)
        usable, reason = item_is_usable_interaction_instance(state, item)
        if not usable or item is None:
            raise ItemInteractionResolverError(f"物品 {item_id} 当前不可交互：{reason}")
        if item.condition in {"broken", "destroyed", "expired"}:
            raise ItemInteractionResolverError(f"物品 {item.name} 已损坏或失效")
        if (
            item.durability is not None
            and float(item.durability.get("current", 0)) <= 0
        ):
            raise ItemInteractionResolverError(f"物品 {item.name} 的耐久已耗尽")
        if request.operation == "retrieve":
            if item.container_id != request.target_id:
                raise ItemInteractionResolverError("要取出的物品不在目标家具中")
        else:
            if not item_is_owned_by(state, item, request.actor_id):
                raise ItemInteractionResolverError(f"物品 {item.name} 不属于行动者")
            if item.item_id not in equipped_ids:
                auto_picked.append(item.item_id)
        source_items.append(item)
    if request.operation == "combine" and request.target_id not in request.source_item_ids:
        raise ItemInteractionResolverError("组合目标必须是选中的具体物品实例")
    if request.operation == "store":
        destination = state.containers.get(request.target_id)
        if destination is None:
            raise ItemInteractionResolverError("存放目标不存在")
        capacity = validate_container_capacity(state, request.target_id, source_items[0], source_items[0].quantity)
        if not capacity.allowed:
            raise ItemInteractionResolverError(capacity.label)
        if any(item.item_id in {
            str(value.get("itemId"))
            for value in state.character_equipment.get(request.actor_id, {}).values()
            if value.get("itemId")
        } for item in source_items):
            raise ItemInteractionResolverError("已装备的物品必须先卸下")
    if request.operation == "retrieve":
        destination_id = inventory_container_id(state, request.actor_id)
        if destination_id is None:
            raise ItemInteractionResolverError("行动者没有背包容器")
        capacity = validate_container_capacity(state, destination_id, source_items[0], source_items[0].quantity)
        if not capacity.allowed:
            raise ItemInteractionResolverError(capacity.label)
    return tuple(source_items), target_summary, tuple(auto_picked)


def _resolve_check(
    state: Projection,
    request: InteractionRequest,
    source_items: tuple[ItemInstance, ...],
    candidate: ItemInteractionCandidate,
    rng: random.Random | Callable[[], int] | None,
):
    if request.operation in {"store", "retrieve"}:
        return _NoCheck()
    ability_ids = candidate.required_ability_ids
    ability_id = ability_ids[0] if ability_ids else request.required_ability_id or "general_item_handling"
    held_bindings = state.character_equipment.get(request.actor_id, {})
    source_ids = {item.item_id for item in source_items}
    held_slots_by_item = {
        str(binding.get("itemId")): str(slot_key).split(":", 1)[1]
        for slot_key, binding in held_bindings.items()
        if str(slot_key).startswith("held:") and binding.get("itemId")
    }
    occupied_by_other = {
        slot
        for slot_key, binding in held_bindings.items()
        if str(slot_key).startswith("held:")
        and binding.get("itemId") not in source_ids
        for slot in (str(slot_key).split(":", 1)[1],)
    }
    required_free_hands = 0
    for item in source_items:
        if item.item_id in held_slots_by_item:
            continue
        profile = item_equipment_profile(item)
        required_free_hands += (
            profile.hand_count
            if profile is not None and profile.mode == "held"
            else 1
        )
    hands = min(2, max(1, required_free_hands))
    physical: PhysicalRequirements = physical_requirements_from_injuries(
        state.character_external_injuries.get(request.actor_id, {}),
        purpose="hold",
        required_hand_count=hands,
        available_hand_slots=frozenset(HAND_SLOTS - occupied_by_other),
    )
    check_input = ability_check_input_from_profile(
        state.character_profiles.get(request.actor_id, {}).get("abilities", ()),
        ability_id,
        physical=physical,
    )
    return resolve_ability_check(
        check_input,
        difficulty_band=candidate.difficulty_band,
        rng=rng or random.Random(),
    )


@dataclass(frozen=True, slots=True)
class _NoCheck:
    status: str = "not_required"
    code: str = "succeeded"
    roll: int | None = None
    total: int | None = None
    dc: int | None = None
    modifier: int = 0
    margin: int | None = None
    ability_id: str = "none"
    level: str = "none"
    source_status: str = "system"
    difficulty_band: str = "trivial"

    def to_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "code": self.code,
            "abilityId": self.ability_id,
            "level": self.level,
            "sourceStatus": self.source_status,
            "difficultyBand": self.difficulty_band,
            "dc": self.dc,
            "modifier": self.modifier,
            "roll": self.roll,
            "total": self.total,
            "margin": self.margin,
        }


def _materialize_success(
    state: Projection,
    request: InteractionRequest,
    source_items: tuple[ItemInstance, ...],
    candidate: ItemInteractionCandidate,
    audit: Event,
    recipe_plan_resolver: RecipePlanResolver | None,
    effect_resolver: InteractionEffectResolver | None,
) -> tuple[Event, ...]:
    if request.operation == "store":
        event = build_item_transferred_event(
            actor_id=request.actor_id,
            world_time=state.world_time,
            item_id=source_items[0].item_id,
            to_container_id=request.target_id,
        )
        return (event,)
    if request.operation == "retrieve":
        destination = inventory_container_id(state, request.actor_id)
        if destination is None:
            raise ItemInteractionResolverError("行动者没有背包容器")
        event = build_item_transferred_event(
            actor_id=request.actor_id,
            world_time=state.world_time,
            item_id=source_items[0].item_id,
            to_container_id=destination,
        )
        return (event,)
    if request.operation == "combine":
        if recipe_plan_resolver is not None:
            return recipe_plan_resolver(
                state,
                request.actor_id,
                state.world_time,
                source_items,
                request.action_text,
                audit.event_id,
            )
        return _default_recipe_events(
            state,
            request.actor_id,
            state.world_time,
            source_items,
            request.action_text,
            audit.event_id,
        )
    if request.operation == "apply":
        if effect_resolver is not None:
            return effect_resolver(
                state,
                request,
                source_items,
                candidate,
                audit,
            )
        if len(source_items) != 1:
            raise ItemInteractionResolverError(
                "多个物品作用于地点或家具需要专用效果处理器"
            )
        if request.target_kind not in {"location", "furniture"}:
            raise ItemInteractionResolverError("物品对该目标类型的作用尚未声明正式效果处理器")
        effect_kind = candidate.effect_kind or request.requested_effect_kind
        if not effect_kind:
            raise ItemInteractionResolverError("AI 没有提供目标效果类型")
        if effect_kind not in {"observe", "inspect", "illumination", "observed_contact"}:
            raise ItemInteractionResolverError("目标地点尚无该效果的正式处理器")
        # This event is an auditable hand-off to the location domain.  The
        # location resolver is intentionally conservative until a concrete
        # effect (lock, illumination, repair, etc.) is authored.
        effect_location_id = request.target_id
        if request.target_kind == "furniture":
            furniture = state.containers.get(request.target_id)
            if furniture is None or furniture.location_id is None:
                raise ItemInteractionResolverError("家具没有有效的所在结构")
            effect_location_id = furniture.location_id
        effect = Event(
            event_id=f"evt_location_item_effect_{uuid4().hex}",
            event_type="location.item_effect_applied",
            actor_id=request.actor_id,
            world_time=state.world_time,
            payload={
                "effectId": f"effect_{uuid4().hex}",
                "locationId": effect_location_id,
                "itemId": source_items[0].item_id,
                "effectKind": effect_kind,
                "summary": "; ".join(candidate.physical_basis),
                "sourceInteractionId": audit.event_id,
            },
            schema_version=1,
        )
        return (effect,)
    raise ItemInteractionResolverError("未知的物品交互操作")


def _wear_trigger(action_text: str, operation: str) -> str | None:
    """Classify only explicit contact/force actions as wear candidates.

    Ordinary inspection, storage and the existing generic ``处理`` effect do
    not imply physical stress.  This narrow gate preserves old interaction
    semantics while still allowing free-form forceful attempts to wear tools.
    """

    if operation in {"store", "retrieve"}:
        return None
    text = action_text.lower()
    if any(value in text for value in ("撬", "切", "割", "砍", "拆", "撬开", "拧断", "锯")):
        return "forceful_tool_use"
    if any(value in text for value in ("砸", "撞", "敲", "击打", "冲击", "摔")):
        return "impact"
    if any(value in text for value in ("撕", "扯", "拉破", "撕裂", "扯破")):
        return "tear"
    if any(value in text for value in ("磨损", "磨", "刮")):
        return "abrasion"
    return None


def _materialize_wear_events(
    state: Projection,
    request: InteractionRequest,
    source_items: tuple[ItemInstance, ...],
    interaction_candidate: ItemInteractionCandidate,
    source_event: Event,
    check: Any,
    runtime: InteractionRuntime,
) -> tuple[Event, ...]:
    """Turn an explicit contact into one or more auditable wear events.

    Wear is a secondary consequence: a failed main check can still produce it
    when contact happened, while a blocked physical prerequisite cannot.  A
    disabled/invalid model falls back only for the registered trigger classes.
    """

    trigger = _wear_trigger(request.action_text, request.operation)
    if trigger is None or getattr(check, "roll", None) is None:
        return ()
    adapter = runtime.wear_adapter
    if adapter is None:
        try:
            adapter = item_wear_adapter_from_environment()
        except Exception:
            adapter = None
    allowed_abilities = tuple(
        str(value.get("abilityId"))
        for value in state.character_profiles.get(request.actor_id, {}).get("abilities", ())
        if isinstance(value, Mapping) and value.get("abilityId")
    )
    result: list[Event] = []
    for item in source_items:
        if item.durability is None:
            continue
        candidate: ItemWearCandidate | None = None
        if adapter is not None and getattr(adapter, "available", False):
            try:
                wear_request = ItemWearRequest(
                    item_id=item.item_id,
                    trigger=trigger,
                    item_summary=_item_summary(item),
                    target_summary=_location_summary(state.locations.get(state.location_id))
                    if state.locations.get(state.location_id) is not None
                    else None,
                    context_summary={"actionText": request.action_text, "operation": request.operation},
                )
                raw = adapter.assess_wear(wear_request)
                output = raw.output if hasattr(raw, "output") else raw
                candidate = parse_wear_candidate(
                    output,
                    wear_request,
                    allowed_ability_ids=allowed_abilities,
                )
                validate_wear_candidate_evidence(candidate, wear_request)
            except Exception:
                candidate = None
        if candidate is None:
            # Program fallback is intentionally conservative and deterministic.
            default_band = {
                "forceful_tool_use": "moderate",
                "impact": "heavy",
                "tear": "heavy",
                "abrasion": "light",
            }.get(trigger)
            if default_band is None:
                continue
            candidate_band = default_band
            estimated = {
                "light": 0.01,
                "moderate": 0.025,
                "heavy": 0.06,
            }.get(candidate_band, 0.025)
            ability_id = getattr(check, "ability_id", None) or "general_item_handling"
            basis = (f"程序确认行为类型为 {trigger}，物品存在耐久档案。",)
            confidence = 1.0
            difficulty = getattr(check, "difficulty_band", "routine")
        else:
            candidate_band = candidate.wear_band
            estimated = candidate.estimated_loss_ratio
            ability_id = candidate.ability_id or getattr(check, "ability_id", None) or "general_item_handling"
            basis = candidate.physical_basis
            confidence = candidate.confidence
            difficulty = candidate.difficulty_band
        # Reuse the main action's die and modifier.  The model cannot choose
        # a second roll or a new modifier; its difficulty label is retained
        # only when it agrees with the already-resolved check.
        modifier = int(getattr(check, "modifier", 0))
        roll = int(getattr(check, "roll"))
        dc = int(getattr(check, "dc", 11) or 11)
        if difficulty != getattr(check, "difficulty_band", difficulty):
            difficulty = getattr(check, "difficulty_band", difficulty)
        try:
            resolution = resolve_behavior_wear(
                current=float(item.durability["current"]),
                maximum=float(item.durability["max"]),
                wear_band=candidate_band,
                estimated_loss_ratio=float(estimated),
                roll=roll,
                modifier=modifier,
                dc=dc,
            )
            result.append(
                build_item_wear_event(
                    actor_id=request.actor_id,
                    world_time=source_event.world_time,
                    item_id=item.item_id,
                    source_event_id=source_event.event_id,
                    trigger=trigger,
                    resolution=resolution,
                    ability_id=str(ability_id),
                    level=str(getattr(check, "level", "untrained")),
                    source_status=str(getattr(check, "source_status", "unknown")),
                    physical_basis=basis,
                    confidence=confidence,
                )
            )
        except Exception:
            # A secondary wear calculation must never turn a valid primary
            # action into an uncommittable plan.  Unknown/malformed candidates
            # are rejected here and remain visible through the interaction
            # audit, with no durability mutation.
            continue
    return tuple(result)


def _default_recipe_events(
    state: Projection,
    actor_id: str,
    world_time: int,
    source_items: tuple[ItemInstance, ...],
    action_text: str,
    interaction_event_id: str,
) -> tuple[Event, ...]:
    root = Path(__file__).resolve().parents[5]
    ai_root = root / "content" / "campaigns" / "gray-harbor" / "items-atlas" / "ai-items"
    try:
        era = EraTechnologyProfile.load(ai_root / "era-technology-profile.json")
        recipes = GeneratedRecipeCatalog.load(ai_root / "generated-recipes.json", era)
        daily = DailyItemDefinitionCatalog.load(ai_root / "daily-item-definitions.json")
        refs = DailyItemReferenceTable.load(ai_root / "daily-item-references.json")
    except Exception as error:
        raise ItemInteractionResolverError(f"物品配方资料不可用: {error}") from error
    definitions = tuple(_definition_from_instance(item) for item in source_items)
    request = RecipeAssessmentRequest(
        action_text,
        tuple(RecipeIngredient(item.definition_id, item.quantity) for item in source_items),
    )
    assessment = None
    generation = None
    import os
    if os.environ.get("TRPG_ITEM_RECIPE_MODEL_ENABLED", "false").lower() in {"1", "true", "yes", "on"}:
        from trpg_server.ai.platform.deepseek import DeepSeekSettings
        from trpg_server.items.ai_items.deepseek_adapter import (
            DeepSeekDailyItemGenerationAdapter,
            DeepSeekRecipeAssessmentAdapter,
        )
        settings = DeepSeekSettings.from_environment()
        assessment = DeepSeekRecipeAssessmentAdapter(settings)
        generation = DeepSeekDailyItemGenerationAdapter(settings)
    resolved = resolve_item_recipe(
        recipes,
        daily,
        refs,
        era,
        request,
        assessment,
        generation,
        known_definitions=definitions,
    )
    if resolved.entry is None or resolved.output_definition is None:
        raise ItemInteractionResolverError(resolved.reason or "没有已确认的配方")
    destination = inventory_container_id(state, actor_id)
    if destination is None:
        raise ItemInteractionResolverError("行动者没有背包容器")
    plan = build_recipe_conversion_plan(
        state,
        actor_id=actor_id,
        world_time=world_time,
        blueprint=resolved.entry.blueprint,
        output_definition=resolved.output_definition,
        inputs=tuple(RecipeConversionInput(item.item_id, item.quantity) for item in source_items),
        output_item_id=f"item_recipe_{uuid4().hex}",
        destination_container_id=destination,
    )
    created_event = plan.events[-1]
    output_item = ItemInstance.from_payload(
        created_event.payload["item"],
        source_event_id=None,
        last_changed_event_id=None,
    )
    definition_status = "generated_daily" if output_item.definition_id.startswith("daily_") else "catalog"
    source_event, created = build_confirmed_item_creation_events(
        actor_id=actor_id,
        world_time=world_time,
        item=output_item,
        source_kind="recipe",
        source_event_id=interaction_event_id,
        definition_status=definition_status,
    )
    return tuple([*plan.events[:-1], source_event, created])


def _recipe_catalog_context() -> tuple[EraTechnologyProfile, GeneratedRecipeCatalog] | None:
    root = Path(__file__).resolve().parents[5]
    ai_root = root / "content" / "campaigns" / "gray-harbor" / "items-atlas" / "ai-items"
    try:
        era = EraTechnologyProfile.load(ai_root / "era-technology-profile.json")
        return era, GeneratedRecipeCatalog.load(ai_root / "generated-recipes.json", era)
    except Exception:
        return None


def _has_cached_recipe(request: InteractionRequest, source_items: tuple[ItemInstance, ...]) -> bool:
    context = _recipe_catalog_context()
    if context is None:
        return False
    _, catalog = context
    recipe_request = RecipeAssessmentRequest(
        request.action_text,
        tuple(RecipeIngredient(item.definition_id, item.quantity) for item in source_items),
    )
    return catalog.lookup(recipe_request) is not None


def _cached_recipe_candidate(request: InteractionRequest) -> ItemInteractionCandidate:
    return ItemInteractionCandidate(
        decision="possible",
        operation=request.operation,
        required_ability_ids=(),
        tool_fit="plausible",
        difficulty_band="routine",
        physical_basis=("该组合已存在经过时代与质量校验的配方缓存。",),
        missing_facts=(),
        risk_hints=(),
        confidence=1.0,
        effect_kind=None,
        rejection_reason=None,
    )


def _definition_from_instance(item: ItemInstance) -> Mapping[str, Any]:
    payload = item.to_payload()
    payload["id"] = item.definition_id
    payload["definitionId"] = item.definition_id
    payload["quantity"] = 1
    payload["condition"] = None
    payload["durability"] = None
    payload["containerId"] = None
    payload["locationId"] = None
    return payload


def _deterministic_transfer_candidate(request: InteractionRequest) -> ItemInteractionCandidate:
    return ItemInteractionCandidate(
        decision="possible",
        operation=request.operation,
        required_ability_ids=(),
        tool_fit="strong",
        difficulty_band="trivial",
        physical_basis=("目标家具位于当前位置，且操作是直接存取。",),
        missing_facts=(),
        risk_hints=(),
        confidence=1.0,
        effect_kind=None,
        rejection_reason=None,
    )


def _item_summary(item: ItemInstance) -> dict[str, Any]:
    properties = dict(item.properties)
    observable_properties = {
        key: deepcopy(properties[key])
        for key in (
            "material",
            "materials",
            "structure",
            "sizeDescription",
            "observableFeatures",
            "equipment",
            "consumable",
        )
        if key in properties
    }
    return {
        "itemId": item.item_id,
        "name": item.name,
        "description": item.description,
        "category": item.category,
        "quantity": item.quantity,
        "condition": item.condition,
        "durability": deepcopy(item.durability),
        "properties": observable_properties,
        # Do not leak ownership, location, value or plot flags into the model
        # prompt: they are authoritative facts but not physical evidence.
        "observable": {
            "name": item.name,
            "description": item.description,
            "category": item.category,
            "condition": item.condition,
            "properties": observable_properties,
        },
    }


def _furniture_summary(state: Projection, container_id: str) -> dict[str, Any]:
    container = state.containers[container_id]
    contents = [
        {"itemId": item.item_id, "name": item.name, "quantity": item.quantity}
        for item in state.items.values()
        if item.container_id == container_id
    ]
    contents.sort(key=lambda value: value["itemId"])
    return {
        "targetKind": "furniture",
        "containerId": container.container_id,
        "furnitureKind": container.furniture_kind,
        "name": container.furniture_name,
        "description": container.furniture_description,
        "structureId": container.structure_id,
        "contents": contents,
    }


def _location_summary(location: Any) -> dict[str, Any]:
    return {
        "targetKind": "location",
        "locationId": location.location_id,
        "name": location.name,
        "kind": location.kind,
        "description": location.description,
        "allowedOperations": ["apply"],
    }


def _location_is_current(state: Projection, actor_id: str, target_id: str) -> bool:
    current = state.character_locations.get(actor_id)
    visited: set[str] = set()
    while current is not None:
        if current in visited:
            return False
        visited.add(current)
        if current not in state.locations:
            return False
        if current == target_id:
            return True
        location = state.locations.get(current)
        current = location.parent_id if location is not None else None
    return False


def _interaction_event(
    *,
    interaction_id: str,
    request: InteractionRequest,
    status: str,
    check: Any,
    auto_picked: tuple[str, ...],
    world_time: int,
) -> Event:
    return Event(
        event_id=interaction_id,
        event_type="item.interaction_resolved",
        actor_id=request.actor_id,
        world_time=world_time,
        payload={
            "interactionId": interaction_id,
            "actorId": request.actor_id,
            "operation": request.operation,
            "sourceItemIds": list(request.source_item_ids),
            "targetKind": request.target_kind,
            "targetId": request.target_id,
            "status": status,
            "check": check.to_mapping(),
            "autoPickedItemIds": list(auto_picked),
            "sourceText": request.action_text,
        },
        schema_version=1,
    )


def _interaction_id(command: ParsedCommand) -> str:
    explicit = command.parameters.get("interactionId")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    return f"evt_item_interaction_{uuid4().hex}"


def _time_event(current: int, minutes: int, reason: str) -> Event:
    target = current + minutes
    return Event(
        event_id=f"evt_{uuid4().hex}",
        event_type="time.advanced",
        actor_id="system",
        world_time=target,
        payload={
            "from": current,
            "to": target,
            "minutes": minutes,
            "reason": reason,
        },
        schema_version=1,
    )


def _operation_label(operation: str) -> str:
    return {"combine": "物品组合", "store": "物品存放", "retrieve": "物品取出", "apply": "物品作用"}.get(operation, "物品交互")


def _rejected(command: ParsedCommand, outcome: str, narrative: str, label: str) -> Resolution:
    return Resolution(
        status="rejected",
        outcome=outcome,
        narrative=narrative,
        command=command,
        reasons=[DecisionReason(outcome, label, "negative")],
    )


__all__ = [
    "InteractionRuntime",
    "ItemInteractionResolverError",
    "InteractionEffectResolver",
    "RecipePlanResolver",
    "resolve_item_interaction",
]
