"""Character-side equipment binding and event constructors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from trpg_server.characters.body import BODY_PARTS, HAND_SLOTS, injury_blocks
from trpg_server.core.state import Event, Projection


@dataclass(frozen=True, slots=True)
class EquipmentCheck:
    allowed: bool
    code: str
    label: str
    slot_ids: tuple[str, ...] = ()


def _item_properties(item: object) -> Mapping[str, Any]:
    value = getattr(item, "properties", {})
    return value if isinstance(value, Mapping) else {}


def _equipment_profile(item: object) -> tuple[str, tuple[str, ...], int] | None:
    """Read the shared equipment property without importing item code."""

    raw = _item_properties(item).get("equipment")
    if not isinstance(raw, Mapping):
        return None
    mode = raw.get("mode")
    slot_ids = raw.get("slotIds")
    hand_count = raw.get("handCount", 0)
    if mode not in {"held", "worn"}:
        return None
    if (
        not isinstance(slot_ids, list)
        or not slot_ids
        or any(type(value) is not str or not value for value in slot_ids)
        or len(set(slot_ids)) != len(slot_ids)
    ):
        return None
    if type(hand_count) is not int or hand_count < 0 or hand_count > 2:
        return None
    if mode == "held" and hand_count not in {1, 2}:
        return None
    if mode == "worn" and hand_count != 0:
        return None
    return mode, tuple(slot_ids), hand_count


def _item_owner(state: Projection, item: object) -> str | None:
    container = state.containers.get(getattr(item, "container_id", None))
    return container.owner_character_id if container is not None else None


def _current_slots(state: Projection, character_id: str, item_id: str) -> tuple[str, ...]:
    bindings = state.character_equipment.get(character_id, {})
    return tuple(
        sorted(
            str(value.get("slotId"))
            for value in bindings.values()
            if value.get("itemId") == item_id
        )
    )


def _binding_key(mode: str, slot_id: str) -> str:
    return f"{mode}:{slot_id}"


def _validate_profile_slots(
    profile: tuple[str, tuple[str, ...], int]
) -> EquipmentCheck | None:
    mode, slot_ids, hand_count = profile
    if any(slot not in BODY_PARTS for slot in slot_ids):
        return EquipmentCheck(False, "invalid_equipment_slot", "物品引用了不存在的身体槽位")
    if mode == "held":
        if not set(slot_ids).issubset(HAND_SLOTS):
            return EquipmentCheck(False, "invalid_hand_slot", "持有物品只能占用左右手槽位")
        if hand_count == 2 and len(slot_ids) != 2:
            return EquipmentCheck(False, "invalid_hand_use", "双手物品必须声明左右手槽位")
    return None


def validate_equipment_binding(
    state: Projection,
    character_id: str,
    item: object | None,
    slot_ids: tuple[str, ...],
    equipment_profile: tuple[str, tuple[str, ...], int] | None = None,
) -> EquipmentCheck:
    if item is None:
        return EquipmentCheck(False, "missing_item", "物品不存在")
    if _item_owner(state, item) != character_id:
        return EquipmentCheck(False, "item_not_owned", "行动者没有持有该物品")
    profile = equipment_profile or _equipment_profile(item)
    if profile is None:
        return EquipmentCheck(False, "equipment_profile_missing", "物品没有可验证的穿戴或持有规格")
    profile_error = _validate_profile_slots(profile)
    if profile_error is not None:
        return profile_error
    if not slot_ids or len(set(slot_ids)) != len(slot_ids):
        return EquipmentCheck(False, "invalid_equipment_slot", "装备槽位无效")
    mode, profile_slots, hand_count = profile
    if mode == "held" and len(slot_ids) != hand_count:
        return EquipmentCheck(False, "invalid_hand_use", "物品占用的手数与装备记录不一致")
    if mode == "worn" and set(slot_ids) != set(profile_slots):
        return EquipmentCheck(False, "invalid_equipment_slot", "穿戴物品必须占用其声明的全部身体槽位")
    if mode == "held" and not set(slot_ids).issubset(set(profile_slots)):
        return EquipmentCheck(False, "invalid_equipment_slot", "装备槽位不符合物品规格")
    bindings = state.character_equipment.get(character_id, {})
    for slot in slot_ids:
        if slot not in BODY_PARTS:
            return EquipmentCheck(False, "invalid_equipment_slot", "装备槽位不存在")
        current = bindings.get(_binding_key(mode, slot))
        if current is not None and current.get("itemId") != getattr(item, "item_id", None):
            return EquipmentCheck(False, "equipment_slot_occupied", "该身体槽位已经有装备")
        purpose = "hold" if mode == "held" else "wear"
        if injury_blocks(
            state.character_external_injuries.get(character_id, {}), slot, purpose
        ):
            return EquipmentCheck(False, "body_part_unavailable", "该身体部位当前无法使用")
    return EquipmentCheck(True, "allowed", "身体部位和装备槽位均可用", tuple(slot_ids))


def choose_equipment_slots(
    state: Projection,
    character_id: str,
    item: object | None,
    requested_slot_id: str | None = None,
    equipment_profile: tuple[str, tuple[str, ...], int] | None = None,
) -> EquipmentCheck:
    if item is None:
        return EquipmentCheck(False, "missing_item", "物品不存在")
    profile = equipment_profile or _equipment_profile(item)
    if profile is None:
        return EquipmentCheck(False, "equipment_profile_missing", "物品没有可验证的穿戴或持有规格")
    item_id = getattr(item, "item_id", None)
    existing = _current_slots(state, character_id, item_id)
    if existing:
        return EquipmentCheck(False, "item_already_equipped", "这件物品已经装备", existing)
    if _item_owner(state, item) != character_id:
        return EquipmentCheck(False, "item_not_owned", "行动者没有持有该物品")
    profile_error = _validate_profile_slots(profile)
    if profile_error is not None:
        return profile_error
    mode, profile_slots, hand_count = profile
    candidates = list(profile_slots)
    if requested_slot_id is not None:
        if requested_slot_id not in candidates:
            return EquipmentCheck(False, "invalid_equipment_slot", "指定槽位不符合物品规格")
        candidates = [requested_slot_id] + [value for value in candidates if value != requested_slot_id]
    if mode == "worn":
        selected = tuple(candidates)
    elif hand_count == 2:
        selected = tuple(candidates[:2])
    else:
        selected = (candidates[0],) if candidates else ()
    if not selected:
        return EquipmentCheck(False, "invalid_equipment_slot", "物品没有可用装备槽位")
    check = validate_equipment_binding(state, character_id, item, selected)
    if check.allowed:
        return check
    if hand_count == 1 and requested_slot_id is None:
        for candidate in candidates[1:]:
            check = validate_equipment_binding(state, character_id, item, (candidate,))
            if check.allowed:
                return check
    return check


def build_item_equipped_event(
    *, actor_id: str, world_time: int, character_id: str, item_id: str, slot_ids: tuple[str, ...]
) -> Event:
    return Event(
        event_id=f"evt_character_item_equipped_{uuid4().hex}",
        event_type="character.item_equipped",
        actor_id=actor_id,
        world_time=world_time,
        payload={"characterId": character_id, "itemId": item_id, "slotIds": list(slot_ids)},
        schema_version=1,
    )


def build_item_unequipped_event(
    *, actor_id: str, world_time: int, character_id: str, item_id: str
) -> Event:
    return Event(
        event_id=f"evt_character_item_unequipped_{uuid4().hex}",
        event_type="character.item_unequipped",
        actor_id=actor_id,
        world_time=world_time,
        payload={"characterId": character_id, "itemId": item_id},
        schema_version=1,
    )


def build_external_injury_applied_event(
    *,
    actor_id: str,
    world_time: int,
    character_id: str,
    injury_id: str,
    body_part: str,
    severity: str,
    status: str = "active",
    functional_effects: dict[str, bool] | None = None,
    notes: str = "",
) -> Event:
    from trpg_server.characters.body import validate_external_injury

    effects = validate_external_injury(
        body_part=body_part,
        severity=severity,
        status=status,
        functional_effects=functional_effects,
    )
    return Event(
        event_id=f"evt_character_external_injury_{uuid4().hex}",
        event_type="character.external_injury_applied",
        actor_id=actor_id,
        world_time=world_time,
        payload={
            "characterId": character_id,
            "injuryId": injury_id,
            "bodyPart": body_part,
            "severity": severity,
            "status": status,
            "functionalEffects": effects,
            "notes": notes,
        },
        schema_version=1,
    )


def build_external_injury_cleared_event(
    *, actor_id: str, world_time: int, character_id: str, injury_id: str
) -> Event:
    return Event(
        event_id=f"evt_character_external_injury_cleared_{uuid4().hex}",
        event_type="character.external_injury_cleared",
        actor_id=actor_id,
        world_time=world_time,
        payload={"characterId": character_id, "injuryId": injury_id},
        schema_version=1,
    )


__all__ = [
    "EquipmentCheck",
    "build_external_injury_applied_event",
    "build_external_injury_cleared_event",
    "build_item_equipped_event",
    "build_item_unequipped_event",
    "choose_equipment_slots",
    "validate_equipment_binding",
]
