"""Player-facing repair commands.

This module is an intentionally small coordinator: access and material
ownership are checked here, the model may only suggest a repair level, the
character domain performs the d20 check, and item events remain the sole
state mutation boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import random
from typing import Any
from uuid import uuid4

from trpg_server.ai.platform.item_wear import (
    ItemRepairCandidate,
    ItemRepairRequest,
    ItemWearCandidate,
    ItemWearAdapter,
    ItemWearError,
    ItemWearRequest,
    item_wear_adapter_from_environment,
    parse_repair_candidate,
    parse_wear_candidate,
    validate_repair_candidate_evidence,
    validate_wear_candidate_evidence,
)
from trpg_server.characters.checks import (
    ability_check_input_from_profile,
    physical_requirements_from_injuries,
    resolve_ability_check,
)
from trpg_server.core.state import DecisionReason, Event, ParsedCommand, Projection, Resolution
from trpg_server.items.commands import build_item_consumed_event
from trpg_server.items.inventory import item_is_at_location, item_is_owned_by
from trpg_server.items.models import ItemInstance
from trpg_server.items.wear import resolve_behavior_wear, resolve_repair, default_wear_ratio
from trpg_server.items.wear_events import (
    build_item_repair_attempt_event,
    build_item_repaired_event,
    build_item_wear_event,
)


ACTION_TYPES = frozenset({"repair_item", "maintain_item"})
RandomSource = random.Random | Callable[[], int] | None


def resolve_maintenance_command(
    state: Projection,
    command: ParsedCommand,
    *,
    adapter: ItemWearAdapter | None = None,
    rng: RandomSource = None,
) -> Resolution:
    """Resolve a repair request without mutating ``state``."""

    if command.action_type not in ACTION_TYPES:
        raise ValueError(f"unsupported maintenance action: {command.action_type}")
    item_id = command.parameters.get("itemId", command.target_id)
    item = state.items.get(str(item_id)) if item_id is not None else None
    if item is None:
        return _reject(command, "missing_item", "找不到要维修的物品。")
    if item.durability is None:
        return _reject(command, "not_durable", "这件物品不适用耐久维修。")
    if float(item.durability.get("current", 0)) >= float(item.durability.get("max", 0)):
        return _reject(command, "already_pristine", "这件物品目前没有需要维修的耐久损耗。")
    actor_location = state.character_locations.get(command.actor_id)
    accessible = item_is_owned_by(state, item, command.actor_id) or (
        actor_location is not None and item_is_at_location(state, item, actor_location)
    )
    if not accessible:
        return _reject(command, "item_not_accessible", "物品不在你可以维修的位置。")

    raw_material_ids = command.parameters.get("materialItemIds", ())
    raw_tool_ids = command.parameters.get("toolItemIds", ())
    material_ids = _ids(raw_material_ids)
    tool_ids = _ids(raw_tool_ids)
    if raw_material_ids and not material_ids:
        return _reject(command, "invalid_material_list", "维修材料实例列表无效或包含重复项。")
    if raw_tool_ids and not tool_ids:
        return _reject(command, "invalid_tool_list", "维修工具实例列表无效或包含重复项。")
    if set(material_ids).intersection(tool_ids):
        return _reject(command, "overlapping_repair_items", "同一物品不能同时作为维修材料和工具。")
    materials: list[ItemInstance] = []
    tools: list[ItemInstance] = []
    for current_id in (*material_ids, *tool_ids):
        current = state.items.get(current_id)
        if current is None or not item_is_owned_by(state, current, command.actor_id):
            return _reject(command, "material_not_owned", "维修材料和工具必须是你实际持有的物品。")
        if current_id == item.item_id:
            return _reject(command, "invalid_repair_material", "维修目标不能同时作为材料或工具。")
        if current_id in tool_ids and current.durability is not None:
            try:
                if float(current.durability.get("current", 0)) <= 0:
                    return _reject(command, "tool_broken", "维修工具的耐久已经耗尽，无法使用。")
            except (TypeError, ValueError):
                return _reject(command, "invalid_tool_durability", "维修工具的耐久数据无效。")
        if current_id in material_ids:
            materials.append(current)
        else:
            tools.append(current)
    if not materials:
        return _reject(command, "missing_material", "维修需要至少一个真实存在的材料实例。")

    default_ability = "tailoring_costume" if item.category == "clothing" else "mechanical_repair"
    allowed_abilities = tuple(
        str(value.get("abilityId"))
        for value in state.character_profiles.get(command.actor_id, {}).get("abilities", ())
        if isinstance(value, Mapping) and value.get("abilityId")
    )
    request = ItemRepairRequest(
        item_id=item.item_id,
        context=command.original_text or "维修物品",
        item_summary=_item_summary(item),
        material_summaries=tuple(_item_summary(value) for value in materials),
        tool_summaries=tuple(_item_summary(value) for value in tools),
        location_summary=(
            {"name": state.locations[actor_location].name, "description": state.locations[actor_location].description}
            if actor_location in state.locations
            else None
        ),
    )
    candidate: ItemRepairCandidate | None = None
    selected_adapter = adapter
    if selected_adapter is None:
        try:
            selected_adapter = item_wear_adapter_from_environment()
        except Exception:
            selected_adapter = None
    if selected_adapter is not None and getattr(selected_adapter, "available", False):
        try:
            raw = selected_adapter.assess_repair(request)
            output = raw.output if hasattr(raw, "output") else raw
            candidate = parse_repair_candidate(
                output,
                request,
                allowed_ability_ids=allowed_abilities,
            )
            validate_repair_candidate_evidence(candidate, request)
        except Exception:
            candidate = None
    if candidate is None:
        # Offline operation is only allowed when the player explicitly names
        # a bounded level.  An unconstrained repair request fails closed.
        requested_level = command.parameters.get("repairLevel")
        if requested_level not in {"patch", "standard", "major", "rebuild"}:
            return _reject(command, "ai_unavailable", "当前无法确认维修方式；请明确维修等级后再试。")
        candidate_level = str(requested_level)
        candidate_ability = str(command.parameters.get("abilityId") or default_ability)
        candidate_difficulty = str(command.parameters.get("difficultyBand") or "routine")
        basis = ("玩家明确指定维修等级，程序仅采用物品和材料的已知事实。",)
        confidence = 1.0
        material_kinds = tuple(value.category for value in materials)
    else:
        candidate_level = candidate.repair_level
        candidate_ability = candidate.ability_id or default_ability
        candidate_difficulty = candidate.difficulty_band
        basis = candidate.physical_basis
        confidence = candidate.confidence
        material_kinds = candidate.material_kinds
        # A model may request a material category, but it cannot make an
        # absent category exist.  Require each requested kind to match one of
        # the explicit material instances supplied by the player.
        if any(kind not in {value.category for value in materials} for kind in material_kinds):
            return _reject(command, "material_mismatch", "维修候选所需材料与实际材料不匹配。")

    physical = physical_requirements_from_injuries(
        state.character_external_injuries.get(command.actor_id, {}),
        purpose="hold",
        required_hand_count=1,
    )
    check_input = ability_check_input_from_profile(
        state.character_profiles.get(command.actor_id, {}).get("abilities", ()),
        candidate_ability,
        physical=physical,
    )
    try:
        check = resolve_ability_check(
            check_input,
            difficulty_band=candidate_difficulty,
            rng=rng or random.Random(),
        )
    except (ValueError, TypeError) as error:
        return _reject(command, "invalid_repair_candidate", str(error))
    if check.blocked:
        return _reject(command, "body_part_unavailable", "你的手部状态无法完成维修。")

    check_payload = check.to_mapping()
    check_payload.pop("reason", None)
    attempt_id = f"evt_item_repair_attempt_{uuid4().hex}"
    attempt = build_item_repair_attempt_event(
        actor_id=command.actor_id,
        world_time=state.world_time,
        item_id=item.item_id,
        attempt_id=attempt_id,
        repair_level=candidate_level,
        check=check_payload,
        material_item_ids=material_ids,
        tool_item_ids=tool_ids,
        physical_basis=basis,
    )
    if not check.succeeded:
        tool_wear = _materialize_repair_tool_wear_events(
            state,
            item,
            tools,
            attempt,
            check,
            candidate_level,
            selected_adapter,
            allowed_abilities,
        )
        return Resolution(
            status="committed",
            outcome="failed_check",
            narrative="你尝试维修，但检定没有通过；物品和材料均未改变。",
            command=command,
            events=[attempt, *tool_wear],
            reasons=[DecisionReason("failed_check", "维修检定未达到程序 DC", "negative", source_event_id=attempt.event_id)],
            visible_changes=[
                "维修失败；没有恢复耐久或消耗材料",
                *(["维修工具发生了使用损耗"] if tool_wear else []),
            ],
        )
    try:
        resolved = resolve_repair(
            current=float(item.durability["current"]),
            maximum=float(item.durability["max"]),
            repair_level=candidate_level,
            roll=int(check.roll),
            modifier=int(check.modifier),
            dc=int(check.dc),
        )
        repaired = build_item_repaired_event(
            actor_id=command.actor_id,
            world_time=state.world_time,
            item_id=item.item_id,
            source_event_id=attempt.event_id,
            resolution=resolved,
            ability_id=candidate_ability,
            level=check.level,
            source_status=check.source_status,
            material_item_ids=material_ids,
            tool_item_ids=tool_ids,
            physical_basis=basis,
            confidence=confidence,
        )
    except (ValueError, TypeError) as error:
        return _reject(command, "invalid_repair_candidate", str(error))
    tool_wear = _materialize_repair_tool_wear_events(
        state,
        item,
        tools,
        attempt,
        check,
        candidate_level,
        selected_adapter,
        allowed_abilities,
    )
    # Material consumption is explicit and atomic on success.  Tool wear is a
    # separate consequence sourced from the same confirmed repair attempt.
    consumed = tuple(
        build_item_consumed_event(
            actor_id=command.actor_id,
            world_time=state.world_time,
            item_id=value.item_id,
            quantity=1,
        )
        for value in materials
    )
    return Resolution(
        status="committed",
        outcome="repaired",
        narrative=f"你完成了对{item.name}的维修，恢复 {resolved.recovered:.2f} 点耐久。",
        command=command,
        events=[attempt, repaired, *tool_wear, *consumed],
        reasons=[DecisionReason("repair_succeeded", "维修候选与人物检定均通过", "positive", source_event_id=repaired.event_id)],
        visible_changes=[
            f"{item.name} 耐久恢复 {resolved.recovered:.2f}",
            *(["维修工具发生了使用损耗"] if tool_wear else []),
        ],
    )


def _materialize_repair_tool_wear_events(
    state: Projection,
    target: ItemInstance,
    tools: Sequence[ItemInstance],
    attempt: Event,
    check: Any,
    repair_level: str,
    adapter: ItemWearAdapter | None,
    allowed_abilities: Sequence[str],
) -> tuple[Event, ...]:
    """Create wear events for tools that were actually used in a repair.

    Repair and tool-wear use the same d20 result: the repair attempt is one
    physical operation, so a second hidden roll would make the outcome harder
    to explain and replay.  The model may refine the severity for each tool,
    but the program still clamps it and owns all arithmetic.  If the model is
    unavailable, a conservative repair-level default keeps the audit chain
    usable without inventing a new tool or a new event source.
    """

    if not tools or getattr(check, "roll", None) is None:
        return ()
    result: list[Event] = []
    fallback_band = {
        "patch": "light",
        "standard": "moderate",
        "major": "heavy",
        "rebuild": "heavy",
    }.get(repair_level, "light")
    fallback_ratio = default_wear_ratio(fallback_band)
    for tool in tools:
        if tool.durability is None:
            continue
        candidate: ItemWearCandidate | None = None
        if adapter is not None and getattr(adapter, "available", False):
            request = ItemWearRequest(
                item_id=tool.item_id,
                trigger="repair_tool_use",
                item_summary=_item_summary(tool),
                target_summary=_item_summary(target),
                context_summary={"repairLevel": repair_level, "operation": "repair"},
            )
            try:
                raw = adapter.assess_wear(request)
                output = raw.output if hasattr(raw, "output") else raw
                candidate = parse_wear_candidate(
                    output,
                    request,
                    allowed_ability_ids=allowed_abilities,
                )
                validate_wear_candidate_evidence(candidate, request)
            except Exception:
                candidate = None
        if candidate is None:
            band = fallback_band
            estimate = fallback_ratio
            ability_id = getattr(check, "ability_id", None) or "mechanical_repair"
            basis = (f"维修等级 {repair_level} 对该工具产生了实际使用接触。",)
            confidence = 1.0
        else:
            band = candidate.wear_band
            estimate = candidate.estimated_loss_ratio
            ability_id = candidate.ability_id or getattr(check, "ability_id", None) or "mechanical_repair"
            basis = candidate.physical_basis
            confidence = candidate.confidence
        try:
            resolution = resolve_behavior_wear(
                current=float(tool.durability["current"]),
                maximum=float(tool.durability["max"]),
                wear_band=band,
                estimated_loss_ratio=float(estimate),
                roll=int(check.roll),
                modifier=int(check.modifier),
                dc=int(check.dc),
            )
            result.append(
                build_item_wear_event(
                    actor_id=attempt.actor_id,
                    world_time=attempt.world_time,
                    item_id=tool.item_id,
                    source_event_id=attempt.event_id,
                    trigger="repair_tool_use",
                    resolution=resolution,
                    ability_id=str(ability_id),
                    level=str(getattr(check, "level", "untrained")),
                    source_status=str(getattr(check, "source_status", "unknown")),
                    physical_basis=basis,
                    confidence=float(confidence),
                )
            )
        except (TypeError, ValueError, KeyError):
            # A secondary consequence must not make an otherwise valid repair
            # impossible to commit.  The attempt and repair remain auditable.
            continue
    return tuple(result)


def _ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result = tuple(str(item) for item in value if isinstance(item, str) and item)
    return result if len(result) == len(set(result)) else ()


def _item_summary(item: ItemInstance) -> dict[str, Any]:
    return {
        "itemId": item.item_id,
        "name": item.name,
        "description": item.description,
        "category": item.category,
        "condition": item.condition,
        "durability": dict(item.durability or {}),
        "properties": dict(item.properties),
    }


def _reject(command: ParsedCommand, outcome: str, narrative: str) -> Resolution:
    return Resolution(
        status="rejected",
        outcome=outcome,
        narrative=narrative,
        command=command,
        reasons=[DecisionReason(outcome, narrative, "negative")],
    )


__all__ = ["ACTION_TYPES", "resolve_maintenance_command"]
