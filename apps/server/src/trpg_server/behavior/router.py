from __future__ import annotations

from copy import deepcopy
import re
from typing import TYPE_CHECKING
from uuid import uuid4

from trpg_server.core.state import (
    DecisionReason,
    DiscoveryState,
    Event,
    InquiryState,
    InspectionState,
    ParsedCommand,
    Projection,
    Resolution,
)
from trpg_server.story.investigation import evaluate_inquiry, evaluate_inspection
from trpg_server.locations.movement import evaluate_movement
from trpg_server.core.projection import apply_event
from trpg_server.locations.environment import resolve_environment_search
from trpg_server.characters.inventory import inventory_container_id
from trpg_server.items.inventory import can_operate, item_is_owned_by, validate_container_capacity
from trpg_server.items.equipment import item_equipment_profile
from trpg_server.items.models import ItemInstance
from trpg_server.items.commands import (
    build_item_consumed_event,
    build_item_transferred_event,
)
from trpg_server.characters.equipment import (
    build_item_equipped_event,
    build_item_unequipped_event,
    choose_equipment_slots,
)
from trpg_server.characters.body import (
    FOOT_SLOTS,
    HAND_SLOTS,
    LEG_SLOTS,
    all_slots_blocked,
    any_slot_blocked,
)
from trpg_server.items.commerce import resolve_purchase
from trpg_server.locations.weather_travel import adjust_travel_time_for_weather

if TYPE_CHECKING:
    from trpg_server.characters.decision import ConfirmedNpcDecision


DEFAULT_PLAYER_ID = "player"
RELATIONSHIP_DIMENSIONS = ("favor", "trust", "fear", "respect", "suspicion", "debt")


def interpret_player_text(
    text: str,
    actor_id: str = DEFAULT_PLAYER_ID,
    source_message_id: str | None = None,
    state: Projection | None = None,
) -> ParsedCommand:
    if state is not None and actor_id == DEFAULT_PLAYER_ID:
        actor_id = state.player_character_id
    normalized = text.strip()
    compound_parts = _compound_parts(normalized)
    if len(compound_parts) > 1:
        command = ParsedCommand(
            action_type="compound_action",
            actor_id=actor_id,
            target_id=None,
            parameters={
                "components": [
                    _command_payload(
                        interpret_player_text(
                            part, actor_id, source_message_id, state
                        )
                    )
                    for part in compound_parts
                ]
            },
            original_text=normalized,
            authority="system",
        )
    # Resource questions such as "厨房里有没有能吃的" are environmental
    # searches, not speech directed at a colocated NPC. Keep this check ahead
    # of every speech/inquiry branch so a nearby listener cannot steal the intent.
    elif _looks_like_resource_search(normalized):
        command = ParsedCommand(
            action_type="search_location",
            actor_id=actor_id,
            target_id=(
                state.character_locations.get(actor_id)
                if state is not None
                else None
            ),
            parameters={"searchKind": _search_kind(normalized)},
            original_text=normalized,
            claimed_outcome=_claimed_investigation_outcome(normalized),
            authority="player",
        )
    elif _looks_like_inquiry(normalized):
        inquiry, match_status = _match_inquiry(state, normalized, actor_id)
        if inquiry is not None:
            command = ParsedCommand(
                action_type="ask_topic",
                actor_id=actor_id,
                target_id=inquiry.target_character_id,
                parameters={"interactionId": inquiry.interaction_id},
                original_text=normalized,
                authority="system",
            )
        elif match_status == "ambiguous":
            command = _unresolved_command(actor_id, normalized, "topic", match_status)
        else:
            command = _speech_command(state, actor_id, normalized)
    elif _is_explicit_speech(normalized):
        command = _speech_command(state, actor_id, normalized)
    elif _looks_like_item_possession_claim(normalized):
        item, match_status = _match_item(state, normalized, ("拿到", "有", "属于"))
        command = (
            ParsedCommand(
                action_type="claim_item_possession",
                actor_id=actor_id,
                target_id=None,
                parameters={"itemId": item.item_id},
                original_text=normalized,
                claimed_outcome="item_possessed",
                authority="world",
            )
            if item is not None
            else _unresolved_command(actor_id, normalized, "item", match_status)
        )
    elif _looks_like_purchase(normalized):
        offer_id, quantity = _match_purchase_offer(state, normalized)
        command = (
            ParsedCommand(
                action_type="purchase_item",
                actor_id=actor_id,
                target_id=offer_id,
                parameters={"offerId": offer_id, "quantity": quantity},
                original_text=normalized,
                authority="player",
            )
            if offer_id is not None
            else _unresolved_command(actor_id, normalized, "offer", "missing")
        )
    elif _looks_like_character_search(state, normalized, actor_id):
        character_id = _match_character(state, normalized, actor_id)
        command = (
            ParsedCommand(
                action_type="find_character",
                actor_id=actor_id,
                target_id=character_id,
                parameters={"characterId": character_id},
                original_text=normalized,
                authority="system",
            )
            if character_id is not None
            else _unresolved_command(actor_id, normalized, "target", "missing")
        )
    elif _looks_like_item_operation(normalized):
        command = _parse_item_operation(state, normalized, actor_id)
    elif _looks_like_take_item(normalized):
        item, match_status = _match_item(state, normalized, ("拿", "取", "带走", "带上", "收进", "装进"))
        command = (
            ParsedCommand(
                action_type="take_item",
                actor_id=actor_id,
                target_id=item.item_id,
                parameters={"itemId": item.item_id},
                original_text=normalized,
                authority="system",
            )
            if item is not None
            else _unresolved_command(actor_id, normalized, "item", match_status)
        )
    elif _looks_like_consume(normalized):
        item, match_status = _match_item(
            state,
            normalized,
            ("吃", "食用", "喝", "饮用", "用掉"),
        )
        command = (
            ParsedCommand(
                action_type="consume_item",
                actor_id=actor_id,
                target_id=item.item_id,
                parameters={"itemId": item.item_id},
                original_text=normalized,
                authority="player",
            )
            if item is not None
            else _unresolved_command(actor_id, normalized, "item", match_status)
        )
    elif _looks_like_investigation(normalized):
        inspection, match_status = _match_inspection(state, normalized)
        if inspection is not None:
            command = ParsedCommand(
                action_type="inspect_item",
                actor_id=actor_id,
                target_id=inspection.target_item_id,
                parameters={"interactionId": inspection.interaction_id},
                original_text=normalized,
                claimed_outcome=_claimed_investigation_outcome(normalized),
                authority="system",
            )
        elif match_status == "ambiguous":
            command = _unresolved_command(actor_id, normalized, "inspection", match_status)
        elif any(marker in normalized for marker in ("检查", "查看", "看看")):
            item, item_status = _match_item(state, normalized, ("检查", "查看", "看看"))
            if item is not None:
                command = ParsedCommand(
                    action_type="inspect_item_generic",
                    actor_id=actor_id,
                    target_id=item.item_id,
                    parameters={"itemId": item.item_id},
                    original_text=normalized,
                    authority="player",
                )
            elif any(marker in normalized for marker in ("物品", "东西", "道具", "装备", "背包")):
                # An explicit item reference that is not present must remain unresolved;
                # it must not be silently converted into a location investigation.
                command = _unresolved_command(actor_id, normalized, "item", item_status)
            else:
                # Structural/environmental checks (walls, drains, surroundings, etc.)
                # belong to the location investigation domain even when no authored
                # inspection definition matches them.
                command = ParsedCommand(
                    action_type="investigate_location",
                    actor_id=actor_id,
                    target_id=(
                        state.character_locations.get(actor_id)
                        if state is not None
                        else None
                    ),
                    parameters={"searchKind": "general"},
                    original_text=normalized,
                    claimed_outcome=_claimed_investigation_outcome(normalized),
                    authority="player",
                )
        else:
            command = ParsedCommand(
                action_type=(
                    "search_location"
                    if _looks_like_resource_search(normalized)
                    else "investigate_location"
                ),
                actor_id=actor_id,
                target_id=(
                    state.character_locations.get(actor_id)
                    if state is not None
                    else None
                ),
                parameters={"searchKind": _search_kind(normalized)},
                original_text=normalized,
                claimed_outcome=_claimed_investigation_outcome(normalized),
                authority="player",
            )
    elif _looks_like_time_passage(normalized):
        command = ParsedCommand(
            action_type="wait",
            actor_id=actor_id,
            target_id=None,
            parameters={
                "minutes": _time_passage_minutes(normalized),
                "activity": _time_passage_activity(normalized),
            },
            original_text=normalized,
            authority="player",
        )
    elif _looks_like_movement(normalized):
        location_id, match_status = _match_location(state, normalized, actor_id)
        command = (
            ParsedCommand(
                action_type="move",
                actor_id=actor_id,
                target_id=location_id,
                parameters={"destinationId": location_id},
                original_text=normalized,
                authority="player",
            )
            if location_id is not None
            else _unresolved_command(actor_id, normalized, "location", match_status)
        )
    elif _looks_like_request(normalized):
        item, match_status = _match_item(
            state,
            normalized,
            ("借", "索要", "给我", "请求", "我要", "拿给我"),
        )
        if item is None:
            command = _unresolved_command(actor_id, normalized, "item", match_status)
        else:
            target_id = _infer_target(state, normalized, actor_id, item)
            command = (
                ParsedCommand(
                    action_type="request_item",
                    actor_id=actor_id,
                    target_id=target_id,
                    parameters={"itemId": item.item_id},
                    original_text=normalized,
                    authority="system",
                )
                if target_id is not None
                else _unresolved_command(actor_id, normalized, "target", "missing")
            )
    elif _looks_like_past_gift_claim(normalized):
        item, match_status = _match_item(
            state,
            normalized,
            ("送过", "前天", "之前", "送", "给"),
        )
        if item is None:
            command = _unresolved_command(actor_id, normalized, "item", match_status)
        else:
            target_id = _infer_target(state, normalized, actor_id, item)
            command = (
                ParsedCommand(
                    action_type="claim_past_gift",
                    actor_id=actor_id,
                    target_id=target_id,
                    parameters={
                        "itemId": item.item_id,
                        "itemDefinitionId": item.definition_id,
                    },
                    original_text=normalized,
                    claimed_outcome="past_gift_exists",
                    authority="world",
                )
                if target_id is not None
                else _unresolved_command(actor_id, normalized, "target", "missing")
            )
    elif _looks_like_offer(normalized):
        item, match_status = _match_item(
            state,
            normalized,
            ("送", "递", "交给", "给"),
        )
        if item is None:
            command = _unresolved_command(actor_id, normalized, "item", match_status)
        else:
            target_id = _infer_target(state, normalized, actor_id, item)
            command = (
                ParsedCommand(
                    action_type="offer_item",
                    actor_id=actor_id,
                    target_id=target_id,
                    parameters={
                        "itemId": item.item_id,
                        "offerPurpose": _offer_purpose(normalized),
                        "requestedFavorRisk": _requested_favor_risk(normalized),
                    },
                    original_text=normalized,
                    authority="system",
                )
                if target_id is not None
                else _unresolved_command(actor_id, normalized, "target", "missing")
            )
    elif _looks_like_environment_action(normalized):
        command = ParsedCommand(
            action_type="environment_action",
            actor_id=actor_id,
            target_id=state.character_locations.get(actor_id) if state is not None else None,
            parameters={"actionText": normalized},
            original_text=normalized,
            authority="player",
        )
    else:
        command = _speech_command(state, actor_id, normalized)
    return _with_source(command, source_message_id)


def resolve(
    state: Projection,
    command: ParsedCommand,
    npc_decision: ConfirmedNpcDecision | None = None,
) -> Resolution:
    from trpg_server.behavior.commands import characters, items, locations, story, time

    if command.action_type in items.ACTION_TYPES:
        return items.resolve_item_command(state, command)
    if command.action_type in characters.ACTION_TYPES:
        return characters.resolve_character_command(state, command, npc_decision)
    if command.action_type in locations.ACTION_TYPES:
        return locations.resolve_location_command(state, command)
    if command.action_type in story.ACTION_TYPES:
        return story.resolve_story_command(state, command)
    if command.action_type in time.ACTION_TYPES:
        return time.resolve_time_command(state, command)
    if command.action_type == "compound_action":
        return _resolve_compound_action(state, command)
    if command.action_type == "unresolved_reference":
        return _resolve_unresolved_reference(state, command)
    raise ValueError(f"unsupported action type: {command.action_type}")


def _resolve_search_location(state: Projection, command: ParsedCommand) -> Resolution:
    return resolve_environment_search(state, command)


def _resolve_take_item(state: Projection, command: ParsedCommand) -> Resolution:
    item = state.items.get(str(command.parameters.get("itemId", command.target_id)))
    actor_id = command.actor_id
    location_id = state.character_locations.get(actor_id)
    if location_id is None:
        return Resolution(status="rejected", outcome="missing_item", narrative="你没有找到可以带走的那件物品。", command=command, reasons=[DecisionReason("missing_item", "物品不存在", "negative")])
    injuries = state.character_external_injuries.get(actor_id, {})
    if all_slots_blocked(injuries, HAND_SLOTS, "hold"):
        return Resolution(
            status="rejected",
            outcome="hands_unavailable",
            narrative="你的双手目前都无法抓取物品。",
            command=command,
            reasons=[DecisionReason("hands_unavailable", "没有可用的手部功能", "negative")],
        )
    check = can_operate(state, item, actor_id, "take", target_location_id=location_id)
    if not check.allowed:
        return Resolution(status="rejected", outcome=check.code, narrative="你没有在当前位置找到这件物品。", command=command, reasons=[DecisionReason(check.code, check.label, "negative")])
    destination = _owned_container(state, actor_id, "inventory") or _owned_container(state, actor_id, "equipment")
    if destination is None:
        return Resolution(status="rejected", outcome="inventory_unavailable", narrative="你没有可用的随身容器来放置这件物品。", command=command, reasons=[DecisionReason("inventory_unavailable", "行动者没有可用容器", "negative")])
    capacity = validate_container_capacity(state, destination, item, item.quantity)
    if not capacity.allowed:
        return Resolution(
            status="rejected",
            outcome=capacity.code,
            narrative=f"你暂时不能带走这件物品：{capacity.label}。",
            command=command,
            reasons=[DecisionReason(capacity.code, capacity.label, "negative")],
        )
    now = state.world_time
    moved = build_item_transferred_event(
        actor_id=actor_id,
        world_time=now,
        item_id=item.item_id,
        to_container_id=destination,
    )
    events = [moved]
    outcome = "item_taken"
    narrative = f"你把{item.name}收好，放进了自己的随身物品里。"
    reasons = [DecisionReason("item_available", "物品在当前位置且可以取用", "positive", source_event_id=moved.event_id)]
    events.append(_time_event(now, 1, "item_take"))
    return Resolution(status="committed", outcome=outcome, narrative=narrative, command=command, events=events, reasons=reasons, visible_changes=[f"获得：{item.name}", "世界时间推进 1 分钟"])


def _resolve_consume_item(state: Projection, command: ParsedCommand) -> Resolution:
    item = state.items.get(str(command.parameters.get("itemId", command.target_id)))
    actor_id = command.actor_id
    check = can_operate(state, item, actor_id, "consume")
    if not check.allowed:
        return Resolution(
            status="rejected",
            outcome=check.code,
            narrative=("这件物品不在你的随身物品中，不能直接消耗。" if check.code == "item_not_owned" else f"{item.name if item else '这件物品'}不是可以这样消耗的物品。"),
            command=command,
            reasons=[DecisionReason(check.code, check.label, "negative")],
        )
    now = state.world_time
    remaining = item.quantity - 1
    consumed = build_item_consumed_event(
        actor_id=actor_id,
        world_time=now,
        item_id=item.item_id,
        quantity=1,
    )
    minutes = 1
    return Resolution(
        status="committed",
        outcome="item_consumed",
        narrative=f"你消耗了{item.name}。",
        command=command,
        events=[
            consumed,
            _time_event(now, minutes, "item_consume"),
            Event(
                _event_id(),
                "scene.beat_advanced",
                "system",
                now + minutes,
                {"beats": 1, "reason": "item_consume", "sourceEventId": consumed.event_id},
            ),
        ],
        reasons=[DecisionReason("item_consumable", "物品属于行动者且允许消耗", "positive", source_event_id=consumed.event_id)],
        visible_changes=[f"消耗：{item.name}", f"世界时间推进 {minutes} 分钟"],
    )


def _resolve_use_item(state: Projection, command: ParsedCommand) -> Resolution:
    item = state.items.get(str(command.parameters.get("itemId", command.target_id)))
    check = can_operate(state, item, command.actor_id, "use")
    if not check.allowed:
        return Resolution(
            status="rejected",
            outcome=check.code,
            narrative=f"你暂时不能使用这件物品：{check.label}。",
            command=command,
            reasons=[DecisionReason(check.code, check.label, "negative")],
        )
    now = state.world_time
    used = Event(
        _event_id(),
        "item.used",
        command.actor_id,
        now,
        {
            "itemId": item.item_id,
            "characterId": command.actor_id,
            "targetId": command.parameters.get("targetId"),
            "sourceText": command.original_text,
        },
        schema_version=3,
    )
    minutes = 1
    return Resolution(
        status="committed",
        outcome="item_used",
        narrative=f"你使用了{item.name}。具体效果取决于当前对象和剧本规则。",
        command=command,
        events=[used, _time_event(now, minutes, "item_use")],
        reasons=[DecisionReason("item_usable", "物品属于行动者且允许使用", "positive", source_event_id=used.event_id)],
        visible_changes=[f"使用：{item.name}", "世界时间推进 1 分钟"],
    )


def _resolve_equip_item(state: Projection, command: ParsedCommand) -> Resolution:
    item_id = str(command.parameters.get("itemId", command.target_id))
    item = state.items.get(item_id)
    requested_slot = command.parameters.get("slotId")
    if requested_slot is not None and type(requested_slot) is not str:
        requested_slot = None
    item_profile = item_equipment_profile(item) if item is not None else None
    check = choose_equipment_slots(
        state,
        command.actor_id,
        item,
        requested_slot_id=requested_slot,
        equipment_profile=(
            (item_profile.mode, item_profile.slot_ids, item_profile.hand_count)
            if item_profile is not None
            else None
        ),
    )
    if not check.allowed:
        return Resolution(
            status="rejected",
            outcome=check.code,
            narrative=f"你暂时不能装备这件物品：{check.label}。",
            command=command,
            reasons=[DecisionReason(check.code, check.label, "negative")],
        )
    equipped = build_item_equipped_event(
        actor_id=command.actor_id,
        world_time=state.world_time,
        character_id=command.actor_id,
        item_id=item_id,
        slot_ids=check.slot_ids,
    )
    return Resolution(
        status="committed",
        outcome="item_equipped",
        narrative=f"你装备了{item.name}。",
        command=command,
        events=[equipped],
        reasons=[DecisionReason("equipment_allowed", check.label, "positive", source_event_id=equipped.event_id)],
        visible_changes=[f"装备：{item.name}"],
    )


def _resolve_unequip_item(state: Projection, command: ParsedCommand) -> Resolution:
    item_id = str(command.parameters.get("itemId", command.target_id))
    item = state.items.get(item_id)
    if item is None:
        return Resolution(
            status="rejected",
            outcome="missing_item",
            narrative="你没有找到要卸下的物品。",
            command=command,
            reasons=[DecisionReason("missing_item", "物品不存在", "negative")],
        )
    equipped = state.character_equipment.get(command.actor_id, {})
    if not any(value.get("itemId") == item_id for value in equipped.values()):
        return Resolution(
            status="rejected",
            outcome="item_not_equipped",
            narrative="这件物品当前没有装备在身上。",
            command=command,
            reasons=[DecisionReason("item_not_equipped", "物品未装备", "negative")],
        )
    unequipped = build_item_unequipped_event(
        actor_id=command.actor_id,
        world_time=state.world_time,
        character_id=command.actor_id,
        item_id=item_id,
    )
    return Resolution(
        status="committed",
        outcome="item_unequipped",
        narrative=f"你卸下了{item.name}。",
        command=command,
        events=[unequipped],
        reasons=[DecisionReason("equipment_removed", "装备绑定已移除", "positive", source_event_id=unequipped.event_id)],
        visible_changes=[f"卸下：{item.name}"],
    )


def _resolve_combine_items(state: Projection, command: ParsedCommand) -> Resolution:
    del state
    return Resolution(
        status="rejected",
        outcome="item_combination_not_defined",
        narrative="当前物品基础模块尚未定义配方和组合结果，因此没有组合任何物品。",
        command=command,
        reasons=[DecisionReason(
            "item_combination_not_defined",
            "物品记录没有配方或组合结果字段",
            "neutral",
        )],
    )


def _resolve_discard_item(state: Projection, command: ParsedCommand) -> Resolution:
    item = state.items.get(str(command.parameters.get("itemId", command.target_id)))
    check = can_operate(state, item, command.actor_id, "discard")
    if not check.allowed:
        return Resolution(
            status="rejected",
            outcome=check.code,
            narrative=f"你暂时不能丢弃这件物品：{check.label}。",
            command=command,
            reasons=[DecisionReason(check.code, check.label, "negative")],
        )
    location_id = state.character_locations.get(command.actor_id)
    if location_id is None:
        return Resolution(
            status="rejected",
            outcome="location_unavailable",
            narrative="你当前没有可放下物品的有效地点。",
            command=command,
            reasons=[DecisionReason(
                "location_unavailable", "行动者没有当前位置", "negative"
            )],
        )
    now = state.world_time
    dropped = build_item_transferred_event(
        actor_id=command.actor_id,
        world_time=now,
        item_id=item.item_id,
        to_location_id=location_id,
    )
    return Resolution(
        status="committed",
        outcome="item_discarded",
        narrative=f"你把{item.name}留在了当前位置。",
        command=command,
        events=[dropped, _time_event(now, 1, "item_discard")],
        reasons=[DecisionReason(
            "item_operation_allowed",
            "物品属于行动者且已转移到当前位置",
            "positive",
            source_event_id=dropped.event_id,
        )],
        visible_changes=[f"放下：{item.name}", "世界时间推进 1 分钟"],
    )


def _resolve_destroy_item(state: Projection, command: ParsedCommand) -> Resolution:
    return _resolve_remove_item(state, command, "item.destroyed", "销毁", "item_destroy")


def _resolve_remove_item(
    state: Projection,
    command: ParsedCommand,
    event_type: str,
    verb: str,
    reason: str,
) -> Resolution:
    item = state.items.get(str(command.parameters.get("itemId", command.target_id)))
    operation = "discard" if event_type == "item.discarded" else "destroy"
    check = can_operate(state, item, command.actor_id, operation)
    if not check.allowed:
        return Resolution(
            status="rejected",
            outcome=check.code,
            narrative=f"你暂时不能{verb}这件物品：{check.label}。",
            command=command,
            reasons=[DecisionReason(check.code, check.label, "negative")],
        )
    now = state.world_time
    removed = Event(
        _event_id(),
        event_type,
        command.actor_id,
        now,
        {"itemId": item.item_id, "characterId": command.actor_id, "sourceText": command.original_text},
        schema_version=3,
    )
    return Resolution(
        status="committed",
        outcome=event_type.removeprefix("item."),
        narrative=f"你{verb}了{item.name}。",
        command=command,
        events=[removed, _time_event(now, 1, reason)],
        reasons=[DecisionReason("item_operation_allowed", f"物品属于行动者且允许{verb}", "positive", source_event_id=removed.event_id)],
        visible_changes=[f"{verb}：{item.name}", "世界时间推进 1 分钟"],
    )


def _resolve_find_character(state: Projection, command: ParsedCommand) -> Resolution:
    from trpg_server.world.director import scheduled_npc_state

    target_id = _required_target(command)
    availability, scheduled_location = scheduled_npc_state(state, target_id)
    name = _character_name(state, target_id)
    now = state.world_time
    event = Event(_event_id(), "search.performed", command.actor_id, now, {
        "characterId": command.actor_id, "targetCharacterId": target_id,
        "locationId": state.character_locations.get(command.actor_id), "searchKind": "character",
        "result": availability,
    })
    if availability == "public":
        place = state.location_names.get(scheduled_location or "", "不明地点")
        narrative = f"你打听到{name}现在大概率在{place}。这只是一次寻找，没有替你自动赶过去。"
        outcome = "character_located"
        direction = "positive"
        label = "人物当前按公开日程活动"
    elif availability == "appointment":
        narrative = f"{name}现在正在处理预定事务。你可以留下口信、约之后的时间，或说明紧急原因。"
        outcome = "character_requires_appointment"
        direction = "neutral"
        label = "人物当前需要预约"
    elif availability == "private":
        narrative = f"{name}现在在处理私人事务，通常不会接待临时来访。你可以等待、留言，或在确有紧急理由时再试。"
        outcome = "character_private"
        direction = "neutral"
        label = "人物当前处于私人时间"
    else:
        narrative = f"你暂时找不到{name}。对方当前不在可接触的公开地点，也没有因此留下额外线索。"
        outcome = "character_unavailable"
        direction = "neutral"
        label = "人物当前不可达"
    return Resolution(
        status="committed", outcome=outcome, narrative=narrative, command=command,
        events=[event, _time_event(now, 5, "character_search")],
        reasons=[DecisionReason("character_schedule", label, direction, source_event_id=event.event_id)],
        visible_changes=["世界时间推进 5 分钟"],
    )


def _resolve_offer_item(
    state: Projection,
    command: ParsedCommand,
    npc_decision: ConfirmedNpcDecision | None = None,
) -> Resolution:
    actor_id = command.actor_id
    target_id = _required_target(command)
    item = state.items.get(str(command.parameters["itemId"]))
    target_name = _character_name(state, target_id)
    if item is None or not _item_owned_by(state, item, actor_id):
        return Resolution(
            status="rejected",
            outcome="missing_item",
            narrative=f"你检查了自己的物品，却没有找到可以递给{target_name}的那件东西。这个行动没有发生。",
            command=command,
            reasons=[DecisionReason("missing_item", "角色并未持有这件物品", "negative")],
        )
    transfer_check = can_operate(state, item, actor_id, "transfer")
    if not transfer_check.allowed:
        return Resolution(
            status="rejected",
            outcome=transfer_check.code,
            narrative=f"你不能把这件物品交给{target_name}：{transfer_check.label}。",
            command=command,
            reasons=[DecisionReason(transfer_check.code, transfer_check.label, "negative")],
        )
    if not _same_location(state, actor_id, target_id):
        return Resolution(
            status="rejected",
            outcome="target_not_present",
            narrative=f"{target_name}不在这里，你无法把{item.name}交给对方。",
            command=command,
            reasons=[DecisionReason("target_not_present", "目标不在当前地点", "negative")],
        )

    now = state.world_time
    purpose = (
        npc_decision.purpose
        if npc_decision is not None
        else str(command.parameters.get("offerPurpose", "gift"))
    )
    event_prefix = "bribe" if purpose == "bribe" else "gift"
    offered = Event(_event_id(), f"{event_prefix}.offered", actor_id, now, {
        "actorId": actor_id,
        "targetId": target_id,
        "itemId": item.item_id,
        "purpose": purpose,
        "requestedFavorRisk": command.parameters.get("requestedFavorRisk", 0),
    })
    accepted_definitions = state.accepted_gift_definition_ids.get(target_id, set())
    decision_outcome = (
        npc_decision.outcome
        if npc_decision is not None
        else "accept"
        if purpose == "gift" and item.definition_id in accepted_definitions
        else "reject"
    )
    if decision_outcome != "accept":
        event_suffix = {
            "reject": "rejected",
            "counteroffer": "countered",
            "delay": "delayed",
            "test": "tested",
        }[decision_outcome]
        rejected = Event(_event_id(), f"{event_prefix}.{event_suffix}", target_id, now, {
            "actorId": actor_id,
            "targetId": target_id,
            "itemId": item.item_id,
            "offerEventId": offered.event_id,
            "reasonCode": f"npc_{decision_outcome}",
            "conditionCodes": list(npc_decision.conditions) if npc_decision else [],
            "citedMemoryIds": list(npc_decision.cited_memory_ids) if npc_decision else [],
            "decisionProfileSourceEventId": (
                npc_decision.profile_source_event_id if npc_decision else None
            ),
        })
        action_label = {
            "reject": "没有接受",
            "counteroffer": "没有立刻接受，而是提出了新的条件",
            "delay": "没有立刻表态，决定暂缓",
            "test": "没有立刻接受，先试探你的真实意图",
        }[decision_outcome]
        return Resolution(
            status="committed",
            outcome=f"{event_prefix}_{event_suffix}",
            narrative=(
                f"{target_name}{action_label}{item.name}。在对方明确接受以前，"
                "物品仍然属于你，相关要求也没有发生。"
            ),
            command=command,
            events=[offered, rejected, _time_event(now, 1, "gift_offer")],
            reasons=(
                [
                    DecisionReason(
                        factor.factor_id,
                        factor.public_label,
                        factor.direction,
                        source_event_id=factor.source_event_id,
                    )
                    for factor in npc_decision.factors
                ]
                if npc_decision is not None
                else [DecisionReason(
                    "recipient_did_not_accept",
                    "目标没有明确接受这份物品",
                    "negative",
                )]
            ),
        )

    destination = _owned_container(state, target_id, "inventory")
    if destination is None:
        return Resolution(
            status="rejected",
            outcome="target_has_no_container",
            narrative=f"系统无法确认{target_name}应把物品放在哪里，因此赠礼没有发生。",
            command=command,
            reasons=[DecisionReason(
                "target_has_no_container",
                "目标没有可接收物品的容器",
                "negative",
            )],
        )
    capacity = validate_container_capacity(state, destination, item, item.quantity)
    if not capacity.allowed:
        return Resolution(
            status="rejected",
            outcome=capacity.code,
            narrative=f"{target_name}没有足够空间接收这件物品：{capacity.label}。",
            command=command,
            reasons=[DecisionReason(capacity.code, capacity.label, "negative")],
        )

    accepted_id = _event_id()
    events = [
        offered,
        Event(accepted_id, f"{event_prefix}.accepted", target_id, now, {
            "actorId": actor_id,
            "targetId": target_id,
            "itemId": item.item_id,
            "offerEventId": offered.event_id,
            "conditionCodes": list(npc_decision.conditions) if npc_decision else [],
            "citedMemoryIds": list(npc_decision.cited_memory_ids) if npc_decision else [],
            "decisionProfileSourceEventId": (
                npc_decision.profile_source_event_id if npc_decision else None
            ),
        }),
        build_item_transferred_event(
            actor_id=actor_id,
            world_time=now,
            item_id=item.item_id,
            to_container_id=destination,
        ),
    ]
    events.append(_time_event(now, 3, "gift_exchange"))
    return Resolution(
        status="committed",
        outcome=(
            "bribe_accepted_pending_favor"
            if purpose == "bribe"
            else "gift_accepted"
        ),
        narrative=(
            f"{target_name}收下了{item.name}，这件物品已经真正离开你的行囊；"
            "但收下贿赂不等于相关要求已经执行。"
            if purpose == "bribe"
            else f"{target_name}接过了{item.name}，这件物品已经真正离开你的行囊。"
        ),
        command=command,
        events=events,
        reasons=(
            [
                DecisionReason(
                    factor.factor_id,
                    factor.public_label,
                    factor.direction,
                    source_event_id=factor.source_event_id,
                )
                for factor in npc_decision.factors
            ]
            if npc_decision is not None
            else [DecisionReason(
                "recipient_accepts_item",
                "剧本明确允许目标接受这件物品",
                "positive",
                source_event_id=accepted_id,
            )]
        ),
        visible_changes=[f"{item.name}已移交给{target_name}"],
    )


def _resolve_request_item(state: Projection, command: ParsedCommand) -> Resolution:
    actor_id = command.actor_id
    target_id = _required_target(command)
    item = state.items.get(str(command.parameters["itemId"]))
    target_name = _character_name(state, target_id)
    if item is None:
        return Resolution(
            status="rejected",
            outcome="unknown_item",
            narrative="这里并不存在你所说的那件物品。",
            command=command,
            reasons=[DecisionReason("unknown_item", "目标物品不存在", "negative")],
        )
    if not _same_location(state, actor_id, target_id):
        return Resolution(
            status="rejected",
            outcome="target_not_present",
            narrative=f"{target_name}不在这里，你无法向对方提出请求。",
            command=command,
            reasons=[DecisionReason("target_not_present", "目标不在当前地点", "negative")],
        )
    if _item_owner(state, item) != target_id:
        return Resolution(
            status="rejected",
            outcome="target_does_not_hold_item",
            narrative=f"当前记录不支持{target_name}持有{item.name}，因此无法向对方索取。",
            command=command,
            reasons=[DecisionReason("target_does_not_hold_item", "目标并未持有这件物品", "negative")],
        )

    now = state.world_time
    requested = Event(
        _event_id(),
        "item.requested",
        actor_id,
        now,
        {
            "actorId": actor_id,
            "targetId": target_id,
            "itemId": item.item_id,
            "text": command.original_text,
        },
    )
    refused = Event(
        _event_id(),
        "request.refused",
        target_id,
        now,
        {"requestEventId": requested.event_id, "reasonCode": "owner_discretion"},
    )
    return Resolution(
        status="committed",
        outcome="request_refused",
        narrative=f"{target_name}没有同意交出{item.name}。这次请求没有改变物品归属。",
        command=command,
        events=[
            requested,
            refused,
            Event(_event_id(), "scene.beat_advanced", "system", now, {"beats": 1}),
            _time_event(now, 1, "request_conversation"),
        ],
        reasons=[
            DecisionReason(
                "owner_discretion",
                "物品记录不授予第三方自动交付权限",
                "negative",
            )
        ],
    )


def _resolve_claim_past_gift(state: Projection, command: ParsedCommand) -> Resolution:
    actor_id = command.actor_id
    target_id = _required_target(command)
    definition_id = str(command.parameters["itemDefinitionId"])
    evidence = any(
        gift_actor == actor_id
        and gift_target == target_id
        and state.items.get(item_id) is not None
        and state.items[item_id].definition_id == definition_id
        for gift_actor, gift_target, item_id, _ in state.accepted_gifts
    )
    now = state.world_time
    target_name = _character_name(state, target_id)
    events = [
        Event(_event_id(), "speech.spoken", actor_id, now, {
            "speakerId": actor_id,
            "listenerIds": [target_id],
            "text": command.original_text,
        }),
        Event(_event_id(), "claim.made", actor_id, now, {
            "claimType": "past_gift",
            "targetId": target_id,
            "itemDefinitionId": definition_id,
            "historicalEvidence": evidence,
        }),
        _time_event(now, 1, "conversation"),
    ]
    narrative = (
        f"{target_name}确认自己记得那份礼物。"
        if evidence
        else f"{target_name}不认可你的说法。历史没有因此发生改变。"
    )
    return Resolution(
        status="committed",
        outcome="claim_acknowledged" if evidence else "claim_disputed",
        narrative=narrative,
        command=command,
        events=events,
        reasons=[DecisionReason(
            "historical_evidence" if evidence else "no_historical_evidence",
            "存在真实赠礼记录" if evidence else "事件日志中没有这份赠礼记录",
            "positive" if evidence else "negative",
        )],
    )


def _resolve_claim_item_possession(state: Projection, command: ParsedCommand) -> Resolution:
    actor_id = command.actor_id
    item_id = str(command.parameters["itemId"])
    item = state.items.get(item_id)
    possessed = item is not None and _item_owned_by(state, item, actor_id)
    event = Event(_event_id(), "claim.made", actor_id, state.world_time, {
        "claimType": "item_possession",
        "itemId": item_id,
        "historicalEvidence": possessed,
    })
    return Resolution(
        status="committed",
        outcome="claim_acknowledged" if possessed else "claim_disputed",
        narrative=(
            "当前物品记录支持你的说法。"
            if possessed
            else "你声称自己已经拿到了那件物品，但物品和容器记录不支持这个说法。物品没有因此出现。"
        ),
        command=command,
        events=[event, _time_event(state.world_time, 1, "claim_review")],
        reasons=[DecisionReason(
            "inventory_evidence" if possessed else "no_inventory_evidence",
            "当前物品记录支持该主张" if possessed else "当前物品记录不支持该主张",
            "positive" if possessed else "negative",
        )],
    )


def _resolve_compound_action(state: Projection, command: ParsedCommand) -> Resolution:
    working_state = deepcopy(state)
    events: list[Event] = []
    reasons: list[DecisionReason] = []
    visible_changes: list[str] = []
    narratives: list[str] = []
    completed = 0
    major_beats = 0

    for component_data in command.parameters["components"]:
        if major_beats >= state.max_major_beats_per_turn:
            narratives.append(
                "这个主要行动已经推进了当前回合的一个剧情节拍；后续行动留到下一回合，由你重新确认。"
            )
            visible_changes.append("后续主要行动因场景节奏限制而暂停")
            return Resolution(
                status="committed",
                outcome="compound_pacing_limited",
                narrative="\n\n".join(narratives),
                command=command,
                events=events,
                reasons=reasons + [DecisionReason(
                    "major_beat_limit_reached",
                    "本场景每回合最多推进一个主要节拍",
                    "neutral",
                )],
                visible_changes=visible_changes,
            )
        component = _command_from_payload(component_data)
        component_result = resolve(working_state, component)
        reasons.extend(component_result.reasons)
        narratives.append(component_result.narrative)
        if component_result.status == "rejected":
            if completed:
                visible_changes.append("后续行动因前置条件不足而没有发生")
                return Resolution(
                    status="committed",
                    outcome="compound_partially_committed",
                    narrative="\n\n".join(narratives),
                    command=command,
                    events=events,
                    reasons=reasons,
                    visible_changes=visible_changes,
                )
            return Resolution(
                status="rejected",
                outcome=component_result.outcome,
                narrative=component_result.narrative,
                command=command,
                reasons=reasons,
                visible_changes=component_result.visible_changes,
            )
        completed += 1
        events.extend(component_result.events)
        visible_changes.extend(component_result.visible_changes)
        for event in component_result.events:
            apply_event(working_state, event)
            if event.event_type == "scene.beat_advanced":
                major_beats += int(event.payload.get("beats", 1))

    return Resolution(
        status="committed",
        outcome="compound_committed",
        narrative="\n\n".join(narratives),
        command=command,
        events=events,
        reasons=reasons,
        visible_changes=visible_changes,
    )


def _resolve_unresolved_reference(state: Projection, command: ParsedCommand) -> Resolution:
    del state
    entity_type = command.parameters["entityType"]
    reason = command.parameters["reason"]
    labels = {
        "item": "物品",
        "location": "地点",
        "target": "目标角色",
        "topic": "询问话题",
        "inspection": "调查目标",
    }
    label = labels.get(str(entity_type), "目标")
    narrative = (
        f"这句话可能指向多个{label}，系统没有替你猜测。请说得更具体一些。"
        if reason == "ambiguous"
        else f"系统没有找到你所指的{label}，因此没有执行行动。"
    )
    return Resolution(
        status="rejected",
        outcome="ambiguous_reference" if reason == "ambiguous" else "missing_reference",
        narrative=narrative,
        command=command,
        reasons=[DecisionReason(
            "ambiguous_reference" if reason == "ambiguous" else "missing_reference",
            f"无法唯一确定{label}",
            "negative",
        )],
    )


def _resolve_wait(state: Projection, command: ParsedCommand) -> Resolution:
    minutes = int(command.parameters.get("minutes", 10))
    # Long waits are explicit player actions and must be able to cross weekly
    # and monthly world-clock boundaries so the Director can emit each report.
    minutes = max(1, min(minutes, 43_200))
    activity = str(command.parameters.get("activity", "wait"))
    activity_labels = {
        "rest": "休息",
        "idle": "发呆",
        "work": "做工",
        "walk": "散步",
        "wait": "等待",
    }
    activity_label = activity_labels.get(activity, "度过这段时间")
    if activity == "work":
        result_text = (
            "当前剧本包还没有可确认的工资或经营收入事件，因此这段做工只记录时间，"
            "不会凭空增加金钱。"
        )
    else:
        result_text = "系统没有替你制造新的线索、奖励或剧情进展。"
    return Resolution(
        status="committed",
        outcome="waited",
        narrative=f"你选择{activity_label} {minutes} 分钟。世界时间继续向前。{result_text}",
        command=command,
        events=[_time_event(state.world_time, minutes, f"player_{activity}")],
        visible_changes=[f"世界时间推进 {minutes} 分钟"],
    )


def _resolve_move(state: Projection, command: ParsedCommand) -> Resolution:
    injuries = state.character_external_injuries.get(command.actor_id, {})
    if any_slot_blocked(injuries, LEG_SLOTS | FOOT_SLOTS, "movement"):
        return Resolution(
            status="rejected",
            outcome="movement_body_blocked",
            narrative="你的腿脚外伤目前不允许进行这次移动。",
            command=command,
            reasons=[DecisionReason("movement_body_blocked", "腿脚功能受限", "negative")],
        )
    destination_id = str(command.parameters["destinationId"])
    decision = evaluate_movement(state, command.actor_id, destination_id)
    destination_name = state.location_names.get(destination_id, destination_id)
    if not decision.allowed:
        return Resolution(
            status="rejected",
            outcome=decision.outcome,
            narrative=f"你没有直接到达{destination_name}。{decision.reason_label}。",
            command=command,
            reasons=[DecisionReason(
                decision.reason_code,
                decision.reason_label,
                "negative",
            )],
        )

    now = state.world_time
    # A public street exit targets a building alias for command and map
    # compatibility, but the authoritative arrival node is the building's
    # first ordinary structure.  Keep both values separate: the command still
    # records what the player selected, while the event records where the
    # character actually ends up.
    arrival_location_id = decision.arrival_location_id or destination_id
    arrival_name = state.location_names.get(arrival_location_id, arrival_location_id)
    travel = adjust_travel_time_for_weather(
        state,
        decision.from_location_id or arrival_location_id,
        arrival_location_id,
        decision.travel_minutes,
    )
    arrival_time = now + travel.travel_minutes
    movement_event = Event(_event_id(), "character.moved", command.actor_id, arrival_time, {
        "characterId": command.actor_id,
        "fromLocationId": decision.from_location_id,
        "toLocationId": arrival_location_id,
        "baseTravelMinutes": travel.base_travel_minutes,
        "weatherDelayMinutes": travel.weather_delay_minutes,
        "travelMinutes": travel.travel_minutes,
        "weatherEventId": travel.weather_event_id,
        "weatherCondition": travel.weather_condition,
        "weatherMultiplierPercent": travel.weather_multiplier_percent,
    }, schema_version=2)
    scene_event = Event(_event_id(), "scene.location_changed", command.actor_id, arrival_time, {
        "fromLocationId": decision.from_location_id,
        "toLocationId": arrival_location_id,
        "movementEventId": movement_event.event_id,
    })
    events = []
    if travel.travel_minutes:
        events.append(_time_event(now, travel.travel_minutes, "character_movement"))
    events.extend([movement_event, scene_event])
    events.append(Event(_event_id(), "scene.beat_advanced", "system", arrival_time, {
        "beats": 1,
        "reason": "character_movement",
        "sourceEventId": movement_event.event_id,
    }))
    weather_text = (
        f"受{travel.weather_condition_name}影响，路上多用了"
        f"{travel.weather_delay_minutes}分钟。"
        if travel.weather_delay_minutes
        else ""
    )
    reasons = [DecisionReason(
        decision.reason_code,
        decision.reason_label,
        "positive",
    )]
    visible_changes = [
        f"位置：{arrival_name}",
        f"世界时间推进 {travel.travel_minutes} 分钟",
    ]
    if travel.weather_delay_minutes:
        reasons.append(DecisionReason(
            "weather_travel_delay",
            f"{travel.weather_condition_name}使跨地点移动变慢",
            "negative",
            travel.weather_delay_minutes,
            travel.weather_event_id,
        ))
        visible_changes.append(
            f"天气影响：{travel.weather_condition_name} +{travel.weather_delay_minutes} 分钟"
        )
    return Resolution(
        status="committed",
        outcome="moved",
        narrative=(
            f"你来到{destination_name}，进入{arrival_name}。"
            f"{weather_text}"
            "这次移动在这里结束，控制权回到你手中。"
        ),
        command=command,
        events=events,
        reasons=reasons,
        visible_changes=visible_changes,
    )


def _resolve_investigate_location(
    state: Projection,
    command: ParsedCommand,
) -> Resolution:
    location_id = state.character_locations.get(command.actor_id)
    if location_id is None:
        return Resolution(
            status="rejected",
            outcome="actor_location_unknown",
            narrative="系统无法确认你当前的位置，因此这次调查没有发生。",
            command=command,
            reasons=[DecisionReason(
                "actor_location_unknown",
                "系统不知道行动者当前在哪里",
                "negative",
            )],
        )

    candidates = [
        discovery
        for discovery in state.discovery_definitions.values()
        if discovery.location_id == location_id
    ]
    specifically_matched = [
        discovery
        for discovery in candidates
        if any(alias and alias in command.original_text for alias in discovery.aliases)
    ]
    if specifically_matched:
        candidates = specifically_matched
    if len(candidates) > 1:
        return Resolution(
            status="rejected",
            outcome="ambiguous_investigation_target",
            narrative="这里有多个可以检查的方向，请说明你具体查看哪里。",
            command=command,
            reasons=[DecisionReason(
                "ambiguous_investigation_target",
                "调查目标不唯一",
                "negative",
            )],
        )

    if len(candidates) == 1:
        discovery = candidates[0]
        already_discovered = all(
            exit_id in state.discovered_exits.get(command.actor_id, set())
            for exit_id in discovery.exit_ids
        )
        if already_discovered:
            return Resolution(
                status="rejected",
                outcome="already_discovered",
                narrative="这里没有新的发现；你已经掌握了这条通路。",
                command=command,
                reasons=[DecisionReason(
                    "already_discovered",
                    "相关通路已经被发现",
                    "neutral",
                )],
            )

        conditions_ready = all(
            condition_id in state.story_conditions
            and state.story_conditions[condition_id].active
            for condition_id in discovery.required_condition_ids
        )
        if conditions_ready:
            return _discover_location_feature(state, command, discovery)

    minutes = candidates[0].time_minutes if candidates else 5
    now = state.world_time
    search_event = Event(_event_id(), "investigation.performed", command.actor_id, now, {
        "characterId": command.actor_id,
        "locationId": location_id,
        "result": "nothing_new_found",
    })
    return Resolution(
        status="committed",
        outcome="nothing_new_found",
        narrative="你仔细检查了这里，但目前没有发现新的可用通路。",
        command=command,
        events=[
            search_event,
            _time_event(now, minutes, "location_investigation"),
            Event(_event_id(), "scene.beat_advanced", "system", now + minutes, {
                "beats": 1,
                "reason": "location_investigation",
                "sourceEventId": search_event.event_id,
            }),
        ],
        reasons=[DecisionReason(
            "discovery_conditions_not_met" if candidates else "no_discovery_here",
            "当前条件下没有发现新的通路",
            "neutral",
            source_event_id=search_event.event_id,
        )],
        visible_changes=[f"世界时间推进 {minutes} 分钟"],
    )


def _resolve_inspect_item(state: Projection, command: ParsedCommand) -> Resolution:
    interaction_id = str(command.parameters["interactionId"])
    definition = state.inspection_definitions.get(interaction_id)
    if definition is None:
        return Resolution(
            status="rejected",
            outcome="unknown_interaction",
            narrative="系统没有找到这项调查定义，因此没有擅自生成结果。",
            command=command,
            reasons=[DecisionReason(
                "unknown_interaction",
                "剧本包中不存在这项调查定义",
                "negative",
            )],
        )

    decision = evaluate_inspection(state, command.actor_id, definition)
    if not decision.allowed:
        narrative = (
            definition.repeat_text
            if decision.outcome == "already_completed"
            else f"你没有完成这项检查。{decision.reason_label}。"
        )
        return Resolution(
            status="rejected",
            outcome=decision.outcome,
            narrative=narrative,
            command=command,
            reasons=[DecisionReason(
                decision.reason_code,
                decision.reason_label,
                "neutral" if decision.outcome == "already_completed" else "negative",
            )],
        )

    item = state.items[definition.target_item_id]
    now = state.world_time
    examined = Event(
        _event_id(),
        "item.examined",
        command.actor_id,
        now,
        {
            "characterId": command.actor_id,
            "itemId": item.item_id,
            "containerId": item.container_id,
            "interactionId": definition.interaction_id,
        },
        schema_version=3,
    )
    events: list[Event] = [examined]
    knowledge_event_ids: dict[str, str] = {}
    for fact_id in definition.revealed_fact_ids:
        if fact_id in state.knowledge.get(command.actor_id, set()):
            continue
        learned = Event(_event_id(), "knowledge.learned", command.actor_id, now, {
            "characterId": command.actor_id,
            "factId": fact_id,
            "sourceEventId": examined.event_id,
        })
        knowledge_event_ids[fact_id] = learned.event_id
        events.append(learned)
    events.extend(_new_clue_events(
        state,
        command.actor_id,
        definition.clue_ids,
        knowledge_event_ids,
        examined.event_id,
        now,
    ))
    events.extend(_complete_interaction_events(
        command.actor_id,
        definition.interaction_id,
        examined.event_id,
        now,
        definition.time_minutes,
        "item_inspection",
    ))
    clue_titles = [state.clue_definitions[value]["title"] for value in definition.clue_ids]
    return Resolution(
        status="committed",
        outcome="inspection_completed",
        narrative=definition.reveal_text,
        command=command,
        events=events,
        reasons=[DecisionReason(
            decision.reason_code,
            decision.reason_label,
            "positive",
            source_event_id=examined.event_id,
        )],
        visible_changes=[
            *(f"发现线索：{title}" for title in clue_titles),
            f"世界时间推进 {definition.time_minutes} 分钟",
        ],
    )


def _resolve_inspect_item_generic(state: Projection, command: ParsedCommand) -> Resolution:
    item = state.items.get(str(command.parameters.get("itemId", command.target_id)))
    check = can_operate(state, item, command.actor_id, "inspect")
    if not check.allowed:
        return Resolution(
            status="rejected",
            outcome=check.code,
            narrative=f"你暂时不能检查这件物品：{check.label}。",
            command=command,
            reasons=[DecisionReason(check.code, check.label, "negative")],
        )
    now = state.world_time
    examined = Event(
        _event_id(),
        "item.inspected",
        command.actor_id,
        now,
        {
            "itemId": item.item_id,
            "containerId": item.container_id,
            "characterId": command.actor_id,
            "sourceText": command.original_text,
        },
        schema_version=3,
    )
    details = [item.name]
    if item.category:
        details.append(f"类别：{item.category}")
    if item.condition:
        details.append(f"状态：{item.condition}")
    if item.quantity > 1:
        details.append(f"数量：{item.quantity}")
    return Resolution(
        status="committed",
        outcome="item_inspected",
        narrative="你检查了这件物品：" + "；".join(details) + "。",
        command=command,
        events=[examined, _time_event(now, 1, "item_inspect")],
        reasons=[DecisionReason("item_inspect_allowed", "物品存在且可被检查", "positive", source_event_id=examined.event_id)],
        visible_changes=[f"检查：{item.name}", "世界时间推进 1 分钟"],
    )


def _resolve_ask_topic(state: Projection, command: ParsedCommand) -> Resolution:
    interaction_id = str(command.parameters["interactionId"])
    definition = state.inquiry_definitions.get(interaction_id)
    if definition is None:
        return Resolution(
            status="rejected",
            outcome="unknown_interaction",
            narrative="系统没有找到这个询问话题，因此没有替 NPC 编造回答。",
            command=command,
            reasons=[DecisionReason(
                "unknown_interaction",
                "剧本包中不存在这个询问话题",
                "negative",
            )],
        )

    decision = evaluate_inquiry(state, command.actor_id, definition)
    if not decision.allowed:
        narrative = (
            definition.repeat_text
            if decision.outcome == "already_completed"
            else f"你没能完成这次询问。{decision.reason_label}。"
        )
        return Resolution(
            status="rejected",
            outcome=decision.outcome,
            narrative=narrative,
            command=command,
            reasons=[DecisionReason(
                decision.reason_code,
                decision.reason_label,
                "neutral" if decision.outcome == "already_completed" else "negative",
            )],
        )

    now = state.world_time
    asked = Event(_event_id(), "question.asked", command.actor_id, now, {
        "speakerId": command.actor_id,
        "listenerId": definition.target_character_id,
        "topic": definition.topic,
        "interactionId": definition.interaction_id,
        "text": command.original_text,
    })
    answer_text = definition.response_text if decision.knows_answer else definition.unknown_text
    answered = Event(
        _event_id(),
        "npc.answer_given",
        definition.target_character_id,
        now,
        {
            "speakerId": definition.target_character_id,
            "listenerId": command.actor_id,
            "topic": definition.topic,
            "interactionId": definition.interaction_id,
            "questionEventId": asked.event_id,
            "text": answer_text,
            "disclosedFactIds": (
                list(definition.revealed_fact_ids) if decision.knows_answer else []
            ),
        },
    )
    events: list[Event] = [asked, answered]
    visible_changes: list[str] = []
    if decision.knows_answer:
        knowledge_event_ids: dict[str, str] = {}
        for fact_id in definition.revealed_fact_ids:
            if fact_id in state.knowledge.get(command.actor_id, set()):
                continue
            learned = Event(_event_id(), "knowledge.learned", command.actor_id, now, {
                "characterId": command.actor_id,
                "factId": fact_id,
                "sourceEventId": answered.event_id,
            })
            knowledge_event_ids[fact_id] = learned.event_id
            events.append(learned)
        events.extend(_new_clue_events(
            state,
            command.actor_id,
            definition.clue_ids,
            knowledge_event_ids,
            answered.event_id,
            now,
        ))
        events.extend(_complete_interaction_events(
            command.actor_id,
            definition.interaction_id,
            answered.event_id,
            now,
            definition.time_minutes,
            "npc_inquiry",
        ))
        visible_changes.extend(
            f"发现线索：{state.clue_definitions[value]['title']}"
            for value in definition.clue_ids
        )
    else:
        events.append(_time_event(now, definition.time_minutes, "npc_inquiry"))
    visible_changes.append(f"世界时间推进 {definition.time_minutes} 分钟")
    return Resolution(
        status="committed",
        outcome="answer_received" if decision.knows_answer else "npc_does_not_know",
        narrative=answer_text,
        command=command,
        events=events,
        reasons=[DecisionReason(
            decision.reason_code,
            decision.reason_label,
            "positive" if decision.knows_answer else "neutral",
            source_event_id=answered.event_id,
        )],
        visible_changes=visible_changes,
    )


def _discover_location_feature(
    state: Projection,
    command: ParsedCommand,
    discovery: DiscoveryState,
) -> Resolution:
    now = state.world_time
    search_event = Event(_event_id(), "investigation.performed", command.actor_id, now, {
        "characterId": command.actor_id,
        "locationId": discovery.location_id,
        "result": "discovery_found",
        "discoveryId": discovery.discovery_id,
    })
    events = [search_event]
    for exit_id in discovery.exit_ids:
        events.append(Event(
            _event_id(),
            "location.exit_discovered",
            command.actor_id,
            now,
            {
                "characterId": command.actor_id,
                "exitId": exit_id,
                "discoveryId": discovery.discovery_id,
                "sourceEventId": search_event.event_id,
            },
        ))
    knowledge_event = Event(_event_id(), "knowledge.learned", command.actor_id, now, {
        "characterId": command.actor_id,
        "factId": discovery.fact_id,
        "sourceEventId": search_event.event_id,
    })
    events.append(knowledge_event)
    clue = state.clue_definitions[discovery.clue_id]
    if discovery.clue_id not in state.clues:
        events.append(Event(_event_id(), "story.clue_revealed", command.actor_id, now, {
            "clueId": discovery.clue_id,
            "title": clue["title"],
            "description": clue["description"],
            "sourceEventId": knowledge_event.event_id,
        }))
    events.extend([
        _time_event(now, discovery.time_minutes, "location_discovery"),
        Event(
            _event_id(),
            "scene.beat_advanced",
            "system",
            now + discovery.time_minutes,
            {
                "beats": 1,
                "reason": "location_discovery",
                "sourceEventId": search_event.event_id,
            },
        ),
    ])
    return Resolution(
        status="committed",
        outcome="location_feature_discovered",
        narrative=discovery.reveal_text,
        command=command,
        events=events,
        reasons=[DecisionReason(
            "discovery_conditions_met",
            "现场条件允许发现这条通路",
            "positive",
            source_event_id=search_event.event_id,
        )],
        visible_changes=[
            f"发现线索：{clue['title']}",
            f"世界时间推进 {discovery.time_minutes} 分钟",
        ],
    )


def _resolve_speech(state: Projection, command: ParsedCommand) -> Resolution:
    actor_id = command.actor_id
    actor_location = state.character_locations.get(actor_id)
    if command.target_id is not None:
        listeners = (
            [command.target_id]
            if actor_location is not None
            and state.character_locations.get(command.target_id) == actor_location
            else []
        )
    elif command.parameters.get("audience") == "room":
        listeners = sorted(
            character_id
            for character_id, location_id in state.character_locations.items()
            if character_id != actor_id and location_id == actor_location
        )
    else:
        listeners = []
    event = Event(_event_id(), "speech.spoken", actor_id, state.world_time, {
        "speakerId": actor_id,
        "listenerIds": listeners,
        "text": command.original_text,
    })
    narrative = (
        f"{_character_name(state, command.target_id)}听完了你的话，但系统没有替你或对方作出下一步决定。"
        if command.target_id is not None
        else "在场的人听见了你的话，但系统没有替任何人作出下一步决定。"
        if listeners
        else "你说出了这句话，但没有可以确认的听众。"
    )
    return Resolution(
        status="committed",
        outcome="speech_heard" if listeners else "speech_without_listener",
        narrative=narrative,
        command=command,
        events=[event, _time_event(state.world_time, 1, "conversation")],
    )


def _resolve_environment_action(state: Projection, command: ParsedCommand) -> Resolution:
    """Record a mundane, location-bound action without inventing consequences."""
    location_id = state.character_locations.get(command.actor_id)
    if location_id is None:
        return Resolution(
            status="rejected",
            outcome="actor_location_unknown",
            narrative="系统无法确认你当前的位置，因此这次环境行动没有发生。",
            command=command,
        )
    minutes = 5
    action_text = str(command.parameters.get("actionText", command.original_text))
    action_event = Event(_event_id(), "environment.action_performed", command.actor_id, state.world_time, {
        "actorId": command.actor_id,
        "locationId": location_id,
        "actionText": action_text,
    })
    return Resolution(
        status="committed",
        outcome="environment_action_completed",
        narrative=(
            f"你在{state.location_names.get(location_id, location_id)}完成了这件事：{action_text}。"
            "这次日常行动没有凭空改变物品、关系或剧情状态。"
        ),
        command=command,
        events=[action_event, _time_event(state.world_time, minutes, "environment_action")],
        visible_changes=[f"世界时间推进 {minutes} 分钟"],
    )


def _new_clue_events(
    state: Projection,
    actor_id: str,
    clue_ids: tuple[str, ...],
    knowledge_event_ids: dict[str, str],
    fallback_source_event_id: str,
    world_time: int,
) -> list[Event]:
    events: list[Event] = []
    for clue_id in clue_ids:
        if clue_id in state.clues:
            continue
        clue = state.clue_definitions[clue_id]
        events.append(Event(_event_id(), "story.clue_revealed", actor_id, world_time, {
            "clueId": clue_id,
            "title": clue["title"],
            "description": clue["description"],
            "sourceEventId": knowledge_event_ids.get(
                str(clue["factId"]),
                fallback_source_event_id,
            ),
        }))
    return events


def _complete_interaction_events(
    actor_id: str,
    interaction_id: str,
    source_event_id: str,
    world_time: int,
    minutes: int,
    reason: str,
) -> list[Event]:
    return [
        Event(_event_id(), "interaction.completed", actor_id, world_time, {
            "characterId": actor_id,
            "interactionId": interaction_id,
            "sourceEventId": source_event_id,
        }),
        _time_event(world_time, minutes, reason),
        Event(_event_id(), "scene.beat_advanced", "system", world_time + minutes, {
            "beats": 1,
            "reason": reason,
            "sourceEventId": source_event_id,
        }),
    ]


def _match_inspection(
    state: Projection | None,
    text: str,
) -> tuple[InspectionState | None, str]:
    if state is None:
        return None, "missing"
    matches = [
        definition
        for definition in state.inspection_definitions.values()
        if any(alias and alias in text for alias in definition.aliases)
    ]
    if not matches:
        return None, "missing"
    longest = max(
        max(len(alias) for alias in definition.aliases if alias and alias in text)
        for definition in matches
    )
    best = [
        definition
        for definition in matches
        if max(len(alias) for alias in definition.aliases if alias and alias in text)
        == longest
    ]
    return (best[0], "matched") if len(best) == 1 else (None, "ambiguous")


def _match_inquiry(
    state: Projection | None,
    text: str,
    actor_id: str,
) -> tuple[InquiryState | None, str]:
    if state is None:
        return None, "missing"
    explicit_target = _match_character(state, text, actor_id)
    matches = [
        definition
        for definition in state.inquiry_definitions.values()
        if any(alias and alias in text for alias in definition.aliases)
        and (explicit_target is None or definition.target_character_id == explicit_target)
    ]
    if not matches:
        return None, "missing"
    present_matches = [
        definition
        for definition in matches
        if _same_location(state, actor_id, definition.target_character_id)
    ]
    if present_matches:
        matches = present_matches
    longest = max(
        max(len(alias) for alias in definition.aliases if alias and alias in text)
        for definition in matches
    )
    best = [
        definition
        for definition in matches
        if max(len(alias) for alias in definition.aliases if alias and alias in text)
        == longest
    ]
    return (best[0], "matched") if len(best) == 1 else (None, "ambiguous")


def _match_item(
    state: Projection | None,
    text: str,
    cues: tuple[str, ...],
) -> tuple[ItemInstance | None, str]:
    if state is None:
        return None, "missing"
    cue_positions = _positions(text, cues)
    matches: list[tuple[int, int, ItemInstance]] = []
    for item in state.items.values():
        # The item contract deliberately has no separate alias field.  Text
        # resolution uses the authored display name; richer aliases belong in
        # a future behavior/search index rather than the authoritative item.
        aliases = (item.name,)
        positions = _positions(text, aliases)
        if not positions:
            continue
        distance = min(
            abs(position - cue_position)
            for position in positions
            for cue_position in (cue_positions or positions)
        )
        longest = max(len(alias) for alias in aliases if alias and alias in text)
        matches.append((distance, -longest, item))
    if not matches:
        return None, "missing"
    matches.sort(key=lambda value: (value[0], value[1], value[2].item_id))
    best_score = matches[0][:2]
    best = [value[2] for value in matches if value[:2] == best_score]
    if len({item.item_id for item in best}) != 1:
        return None, "ambiguous"
    return best[0], "matched"


def _match_character(
    state: Projection | None,
    text: str,
    actor_id: str,
) -> str | None:
    if state is None:
        return None
    matched: set[str] = set()
    for character_id, name in state.character_names.items():
        if character_id == actor_id:
            continue
        aliases = (name, *state.character_aliases.get(character_id, ()))
        if any(alias and alias in text for alias in aliases):
            matched.add(character_id)
    return next(iter(matched)) if len(matched) == 1 else None


def _match_location(
    state: Projection | None,
    text: str,
    actor_id: str | None = None,
) -> tuple[str | None, str]:
    if state is None:
        return None, "missing"
    target_text = re.sub(
        r"^(?:我)?(?:去|前往|走到|进入|回到|上楼|下楼)",
        "",
        text.strip(),
    ).strip(" 。！？,.，！?")
    current_id = state.character_locations.get(actor_id or state.player_character_id)
    direct_ids = {
        value.to_location_id
        for value in state.locations.get(current_id, ()).exits
        if value.visible or value.exit_id in state.discovered_exits.get(
            actor_id or state.player_character_id,
            set(),
        )
    } if current_id in state.locations else set()
    matches: list[tuple[int, int, int, str]] = []
    for location_id, location in state.locations.items():
        aliases = (location.name, *location.aliases)
        matching_aliases = [alias for alias in aliases if alias and alias in text]
        if matching_aliases:
            exact = int(any(alias == target_text for alias in matching_aliases))
            reachable = int(location_id in direct_ids)
            matches.append(
                (
                    -exact,
                    -reachable,
                    -max(len(alias) for alias in matching_aliases),
                    location_id,
                )
            )
    if not matches:
        return None, "missing"
    matches.sort()
    best_score = matches[0][:3]
    best = [location_id for *score, location_id in matches if tuple(score) == best_score]
    if len(best) != 1:
        return None, "ambiguous"
    return best[0], "matched"


def _infer_target(
    state: Projection | None,
    text: str,
    actor_id: str,
    item: ItemInstance,
) -> str | None:
    explicit = _match_character(state, text, actor_id)
    if explicit is not None:
        return explicit
    if state is None:
        return None
    owner = _item_owner(state, item)
    if owner is not None and owner != actor_id:
        return owner
    return _sole_colocated_character(state, actor_id)


def _sole_colocated_character(state: Projection | None, actor_id: str) -> str | None:
    if state is None:
        return None
    actor_location = state.character_locations.get(actor_id)
    candidates = [
        character_id
        for character_id, location_id in state.character_locations.items()
        if character_id != actor_id and location_id == actor_location
    ]
    return candidates[0] if len(candidates) == 1 else None


def _item_owner(state: Projection, item: ItemInstance) -> str | None:
    container = state.containers.get(item.container_id)
    return container.owner_character_id if container is not None else None


def _item_owned_by(state: Projection, item: ItemInstance, character_id: str) -> bool:
    return _item_owner(state, item) == character_id


def _owned_container(
    state: Projection,
    character_id: str,
    preferred_kind: str,
) -> str | None:
    if preferred_kind == "inventory":
        # Character ownership is resolved by the character domain.  The
        # fallback inside ``inventory_container_id`` keeps historical events
        # readable while new character events carry an explicit binding.
        return inventory_container_id(state, character_id)
    containers = sorted(
        (
            container
            for container in state.containers.values()
            if container.owner_character_id == character_id
        ),
        key=lambda value: (value.kind != preferred_kind, value.container_id),
    )
    return containers[0].container_id if containers else None


def _same_location(state: Projection, left: str, right: str) -> bool:
    left_location = state.character_locations.get(left)
    return left_location is not None and left_location == state.character_locations.get(right)


def _speech_command(
    state: Projection | None,
    actor_id: str,
    text: str,
) -> ParsedCommand:
    target_id = _match_character(state, text, actor_id)
    if target_id is None:
        target_id = _sole_colocated_character(state, actor_id)
    parameters = {"speechContent": text}
    if (
        target_id is None
        and any(value in text for value in ("大家", "所有人", "屋里的人"))
    ):
        parameters["audience"] = "room"
    return ParsedCommand(
        action_type="speak",
        actor_id=actor_id,
        target_id=target_id,
        parameters=parameters,
        original_text=text,
        authority="player",
        resolution_required=False,
    )


def _required_target(command: ParsedCommand) -> str:
    if command.target_id is None:
        raise ValueError(f"{command.action_type} requires target_id")
    return command.target_id


def _character_name(state: Projection, character_id: str) -> str:
    return state.character_names.get(character_id, character_id)


def _event_id() -> str:
    return f"evt_{uuid4().hex}"


def _time_event(current: int, minutes: int, reason: str) -> Event:
    target = current + minutes
    return Event(_event_id(), "time.advanced", "system", target, {
        "from": current,
        "to": target,
        "minutes": minutes,
        "reason": reason,
    })


def _latest_source(sources: list[str]) -> str | None:
    return sources[-1] if sources else None


def _looks_like_time_passage(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "等待",
            "等一会",
            "等到明天",
            "休息",
            "睡觉",
            "睡一觉",
            "发呆",
            "放空",
            "散步",
            "闲逛",
            "挣钱",
            "赚钱",
            "做工",
            "工作",
            "营业",
            "干活",
        )
    )


def _time_passage_activity(text: str) -> str:
    if any(marker in text for marker in ("挣钱", "赚钱", "做工", "工作", "营业", "干活")):
        return "work"
    if any(marker in text for marker in ("休息", "睡觉", "睡一觉")):
        return "rest"
    if any(marker in text for marker in ("发呆", "放空")):
        return "idle"
    if any(marker in text for marker in ("散步", "闲逛")):
        return "walk"
    return "wait"


def _time_passage_minutes(text: str) -> int:
    if any(marker in text for marker in ("一个月", "一整月", "整月", "三十天", "30天")):
        return 43_200
    if any(marker in text for marker in ("一周", "一星期", "七天", "7天")):
        return 10_080
    if any(marker in text for marker in ("一整天", "整天", "一天", "到明天", "到第二天")):
        return 1440
    if any(marker in text for marker in ("半天", "一下午", "一上午")):
        return 720
    hour_match = re.search(r"([0-9]+|[一二两三四五六七八九十百]+)\s*个?小时", text)
    if hour_match:
        return min(1440, max(1, _chinese_or_arabic_number(hour_match.group(1)) * 60))
    minute_match = re.search(r"([0-9]+|[一二两三四五六七八九十百]+)\s*分钟", text)
    if minute_match:
        return min(1440, max(1, _chinese_or_arabic_number(minute_match.group(1))))
    if "明天" in text:
        return 1440
    return 10


def _chinese_or_arabic_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "百" in value:
        hundreds, remainder = value.split("百", 1)
        return digits.get(hundreds, 1) * 100 + (_chinese_or_arabic_number(remainder) if remainder else 0)
    if "十" in value:
        tens, remainder = value.split("十", 1)
        return (digits.get(tens, 1) * 10) + (digits.get(remainder, 0) if remainder else 0)
    return digits.get(value, 10)


def _looks_like_request(text: str) -> bool:
    return any(marker in text for marker in ("借", "索要", "请求", "给我", "我要", "拿给我"))


def _looks_like_purchase(text: str) -> bool:
    if "买通" in text:
        return False
    return any(marker in text for marker in ("购买", "买下", "买一个", "买一份", "买点", "买"))


def _match_purchase_offer(
    state: Projection | None,
    text: str,
) -> tuple[str | None, int]:
    if state is None:
        return None, 1
    quantity = 1
    quantity_match = re.search(r"([0-9]+|[一二两三四五六七八九十]+)\s*(?:个|份|件|杯|块)", text)
    if quantity_match:
        quantity = max(1, _chinese_or_arabic_number(quantity_match.group(1)))
    matches: list[tuple[int, str]] = []
    for offer_id, offer in state.commerce_offers.items():
        if str(offer.get("locationId")) != str(state.character_locations.get(state.player_character_id)):
            continue
        names = [str(offer_id), str(offer.get("itemName", "")), *[str(value) for value in offer.get("aliases", [])]]
        lengths = [len(name) for name in names if name and name in text]
        if lengths:
            matches.append((max(lengths), str(offer_id)))
    matches.sort(key=lambda value: (-value[0], value[1]))
    return (matches[0][1], quantity) if matches else (None, quantity)


def _looks_like_environment_action(text: str) -> bool:
    """Recognize mundane physical actions that are neither speech nor investigation."""
    return any(
        marker in text
        for marker in (
            "擦干净", "擦一擦", "擦拭", "整理", "打扫", "清理", "收拾",
            "坐下", "坐一会", "站在", "靠着", "环顾四周", "看看周围",
        )
    )


def _looks_like_take_item(text: str) -> bool:
    return any(marker in text for marker in ("拿走", "取走", "带走", "带上", "收进", "装进", "拿", "取"))


def _looks_like_item_operation(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "装备", "穿上", "脱下", "卸下", "取下", "摘下", "使用", "用一下", "丢弃", "扔掉", "销毁", "毁掉", "组合", "合成", "拼接",
        )
    )


def _parse_item_operation(
    state: Projection | None,
    text: str,
    actor_id: str,
) -> ParsedCommand:
    if state is None:
        return _unresolved_command(actor_id, text, "item", "missing")
    if any(marker in text for marker in ("组合", "合成", "拼接")):
        items = _match_items(state, text)
        if len(items) >= 2:
            return ParsedCommand(
                action_type="combine_items",
                actor_id=actor_id,
                target_id=items[0].item_id,
                parameters={"itemIds": [item.item_id for item in items[:4]]},
                original_text=text,
                authority="player",
            )
        return _unresolved_command(actor_id, text, "item", "missing")
    item, status = _match_item(state, text, ("装备", "穿上", "脱下", "卸下", "取下", "摘下", "使用", "用", "丢弃", "扔掉", "销毁", "毁掉"))
    if item is None:
        return _unresolved_command(actor_id, text, "item", status)
    if any(marker in text for marker in ("脱下", "卸下", "取下", "摘下")):
        action_type = "unequip_item"
    elif any(marker in text for marker in ("装备", "穿上")):
        action_type = "equip_item"
    elif any(marker in text for marker in ("丢弃", "扔掉")):
        action_type = "discard_item"
    elif any(marker in text for marker in ("销毁", "毁掉")):
        action_type = "destroy_item"
    else:
        action_type = "use_item"
    return ParsedCommand(
        action_type=action_type,
        actor_id=actor_id,
        target_id=item.item_id,
        parameters={"itemId": item.item_id},
        original_text=text,
        authority="player",
    )


def _match_items(state: Projection, text: str) -> list[ItemInstance]:
    matches: list[tuple[int, ItemInstance]] = []
    for item in state.items.values():
        aliases = (item.name,)
        lengths = [len(alias) for alias in aliases if alias and alias in text]
        if lengths:
            matches.append((max(lengths), item))
    matches.sort(key=lambda value: (-value[0], value[1].item_id))
    return [item for _, item in matches]


def _looks_like_consume(text: str) -> bool:
    if _looks_like_resource_search(text) or any(
        marker in text for marker in ("找", "寻找", "有没有", "想要", "想找")
    ):
        return False
    return any(marker in text for marker in ("吃", "食用", "喝", "饮用", "用掉"))


def _looks_like_character_search(
    state: Projection | None,
    text: str,
    actor_id: str,
) -> bool:
    if not any(marker in text for marker in ("找", "寻找", "打听", "约见", "拜访")):
        return False
    if _looks_like_resource_search(text):
        return False
    environmental_targets = (
        "通道", "通路", "出口", "入口", "暗门", "密道", "线索", "证据",
        "物品", "东西", "食物", "吃的", "水", "武器", "钥匙", "账本",
    )
    if any(target in text for target in environmental_targets):
        return False
    if state is not None and _match_character(state, text, actor_id) is not None:
        return True
    return any(marker in text for marker in ("找人", "寻找某人", "打听某人", "约见某人", "拜访某人"))


def _looks_like_offer(text: str) -> bool:
    return any(marker in text for marker in ("送", "递", "交给", "给你", "给他", "给她"))


def _offer_purpose(text: str) -> str:
    return (
        "bribe"
        if any(
            marker in text
            for marker in ("贿赂", "塞钱", "买通", "通融", "睁一只眼", "别查", "放我")
        )
        else "gift"
    )


def _requested_favor_risk(text: str) -> int:
    if _offer_purpose(text) != "bribe":
        return 0
    if any(
        marker in text
        for marker in ("销毁", "伪造", "栽赃", "放人", "证据", "档案", "钥匙", "逮捕")
    ):
        return 80
    if any(marker in text for marker in ("许可证", "临检", "别查", "通融", "放我")):
        return 40
    return 60


def _looks_like_past_gift_claim(text: str) -> bool:
    return "送过" in text or (
        any(marker in text for marker in ("前天", "之前", "以前"))
        and any(marker in text for marker in ("送", "给"))
    )


def _looks_like_item_possession_claim(text: str) -> bool:
    return any(marker in text for marker in ("拿到了", "已经拿到", "已经有", "属于我"))


def _looks_like_movement(text: str) -> bool:
    return any(marker in text for marker in ("去", "前往", "走到", "进入", "回到", "上楼", "下楼"))


def _looks_like_investigation(text: str) -> bool:
    return any(
        marker in text
        for marker in ("调查", "搜索", "搜查", "检查", "查看", "观察", "寻找", "找找", "找")
    )


def _looks_like_resource_search(text: str) -> bool:
    if _looks_like_take_item(text):
        return False
    if any(marker in text for marker in ("通道", "通路", "密道", "暗门", "排水", "出口", "入口")):
        return False
    return any(marker in text for marker in ("食物", "吃的", "能吃", "饮品", "喝的", "水", "文件", "纸张", "东西", "物资"))


def _search_kind(text: str) -> str:
    if any(marker in text for marker in ("食物", "吃的", "能吃")):
        return "food"
    if any(marker in text for marker in ("饮品", "喝的", "水")):
        return "drink"
    if any(marker in text for marker in ("文件", "纸张", "账本", "账册")):
        return "document"
    return "general"


def _claimed_investigation_outcome(text: str) -> str | None:
    return (
        "player_claimed_investigation_result"
        if any(marker in text for marker in ("发现", "确认", "证明", "看出", "查明"))
        else None
    )


def _looks_like_inquiry(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "询问",
            "问问",
            "我问",
            "怎么回事",
            "为什么",
            "是多少",
            "有多少",
            "哪一笔",
            "告诉我",
            "？",
            "?",
        )
    )


def _is_explicit_speech(text: str) -> bool:
    return any(marker in text for marker in ("我说", "我对", "我告诉", "我回答")) and any(
        marker in text for marker in ("说", "告诉", "回答", "：", ":", "“", '"')
    )


def _compound_parts(text: str) -> list[str]:
    return [
        part.strip(" ，,。")
        for part in re.split(r"然后|接着|随后|之后再", text)
        if part.strip(" ，,。")
    ]


def _positions(text: str, aliases: tuple[str, ...]) -> list[int]:
    return [
        match.start()
        for alias in aliases
        if alias
        for match in re.finditer(re.escape(alias), text)
    ]


def _unresolved_command(
    actor_id: str,
    text: str,
    entity_type: str,
    reason: str,
) -> ParsedCommand:
    return ParsedCommand(
        action_type="unresolved_reference",
        actor_id=actor_id,
        target_id=None,
        parameters={"entityType": entity_type, "reason": reason},
        original_text=text,
        authority="system",
    )


def _with_source(command: ParsedCommand, source_message_id: str | None) -> ParsedCommand:
    if source_message_id is None:
        return command
    return ParsedCommand(
        action_type=command.action_type,
        actor_id=command.actor_id,
        target_id=command.target_id,
        parameters=command.parameters,
        original_text=command.original_text,
        claimed_outcome=command.claimed_outcome,
        authority=command.authority,
        resolution_required=command.resolution_required,
        source_message_ids=(source_message_id,),
        parser_source=command.parser_source,
        parser_model=command.parser_model,
        parser_failure_code=command.parser_failure_code,
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


def _command_from_payload(payload: dict[str, object]) -> ParsedCommand:
    return ParsedCommand(
        action_type=str(payload["action_type"]),
        actor_id=str(payload["actor_id"]),
        target_id=str(payload["target_id"]) if payload["target_id"] is not None else None,
        parameters=dict(payload["parameters"]),
        original_text=str(payload["original_text"]),
        claimed_outcome=(
            str(payload["claimed_outcome"])
            if payload["claimed_outcome"] is not None
            else None
        ),
        authority=str(payload["authority"]),  # type: ignore[arg-type]
        resolution_required=bool(payload["resolution_required"]),
        source_message_ids=tuple(str(value) for value in payload["source_message_ids"]),
        parser_source=str(payload.get("parser_source", "local")),  # type: ignore[arg-type]
        parser_model=(
            str(payload["parser_model"])
            if payload.get("parser_model") is not None
            else None
        ),
        parser_failure_code=(
            str(payload["parser_failure_code"])
            if payload.get("parser_failure_code") is not None
            else None
        ),
    )
