"""Pure item/container validation over the authoritative projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from trpg_server.core.state import Projection
from trpg_server.items.functions import item_consumable_profile
from trpg_server.items.models import ItemContainer, ItemInstance


ItemOperation = Literal[
    "inspect",
    "take",
    "transfer",
    "consume",
    "use",
    "equip",
    "combine",
    "discard",
    "destroy",
]


@dataclass(frozen=True, slots=True)
class InventoryCheck:
    allowed: bool
    code: str
    label: str


def container_for_item(state: Projection, item: ItemInstance) -> ItemContainer | None:
    return state.containers.get(item.container_id) if item.container_id is not None else None


def item_owner(state: Projection, item: ItemInstance) -> str | None:
    """Derive an owner from the current container anchor, never an item field."""

    container = container_for_item(state, item)
    return container.owner_character_id if container is not None else None


def item_is_owned_by(state: Projection, item: ItemInstance, character_id: str) -> bool:
    return item_owner(state, item) == character_id


def item_is_at_location(state: Projection, item: ItemInstance, location_id: str) -> bool:
    """Resolve direct placement and placement through a location container."""

    if item.location_id == location_id:
        return True
    container = container_for_item(state, item)
    return container is not None and container.location_id == location_id


def _unit_volume(item: ItemInstance | Mapping[str, Any]) -> int | None:
    properties: Mapping[str, Any]
    if isinstance(item, ItemInstance):
        properties = item.properties
    else:
        raw = item.get("properties", {})
        properties = raw if isinstance(raw, Mapping) else {}
    value = properties.get("volumeCm3")
    return value if type(value) is int and value >= 0 else None


def _unit_weight(item: ItemInstance | Mapping[str, Any]) -> int | None:
    if isinstance(item, ItemInstance):
        return item.unit_weight_grams
    value = item.get("unitWeightGrams")
    return value if type(value) is int and value >= 0 else None


def validate_container_capacity(
    state: Projection,
    container_id: str,
    item_payload: ItemInstance | Mapping[str, Any],
    quantity: int = 1,
    *,
    exclude_item_id: str | None = None,
) -> InventoryCheck:
    """Check physical capacity without changing an item or a container.

    ``exclude_item_id`` lets a transfer evaluate the destination after its
    source stack leaves the same container.  It is deliberately an instance
    id, never an ownership or rule field on the item record.
    """

    container = state.containers.get(container_id)
    if container is None:
        return InventoryCheck(False, "container_not_found", "目标容器不存在")
    if type(quantity) is not int or quantity < 1:
        return InventoryCheck(False, "invalid_quantity", "数量必须为正数")
    incoming_unit_weight = _unit_weight(item_payload)
    incoming_unit_volume = _unit_volume(item_payload)
    if container.capacity_weight is not None and incoming_unit_weight is None:
        return InventoryCheck(False, "unknown_weight", "物品重量未知，无法验证目标容器容量")
    if container.capacity_volume is not None and incoming_unit_volume is None:
        return InventoryCheck(False, "unknown_volume", "物品体积未知，无法验证目标容器容量")
    incoming_weight = (incoming_unit_weight or 0) * quantity
    incoming_volume = (incoming_unit_volume or 0) * quantity
    current_weight = 0
    current_volume = 0
    for item in state.items.values():
        if item.container_id != container_id or item.item_id == exclude_item_id:
            continue
        unit_weight = _unit_weight(item)
        unit_volume = _unit_volume(item)
        if container.capacity_weight is not None and unit_weight is None:
            return InventoryCheck(
                False,
                "unknown_existing_weight",
                "容器内已有物品重量未知，无法验证容量",
            )
        if container.capacity_volume is not None and unit_volume is None:
            return InventoryCheck(
                False,
                "unknown_existing_volume",
                "容器内已有物品体积未知，无法验证容量",
            )
        current_weight += (unit_weight or 0) * item.quantity
        current_volume += (unit_volume or 0) * item.quantity
    if (
        container.capacity_weight is not None
        and current_weight + incoming_weight > container.capacity_weight
    ):
        return InventoryCheck(False, "capacity_weight_exceeded", "目标容器重量容量不足")
    if (
        container.capacity_volume is not None
        and current_volume + incoming_volume > container.capacity_volume
    ):
        return InventoryCheck(False, "capacity_volume_exceeded", "目标容器体积容量不足")
    return InventoryCheck(True, "allowed", "目标容器容量充足")


def validate_quantity(item: ItemInstance, quantity: int) -> InventoryCheck:
    if type(quantity) is not int or quantity < 1:
        return InventoryCheck(False, "invalid_quantity", "数量必须为正数")
    if quantity > item.quantity:
        return InventoryCheck(False, "insufficient_quantity", "物品数量不足")
    return InventoryCheck(True, "allowed", "数量充足")


def can_operate(
    state: Projection,
    item: ItemInstance | None,
    actor_id: str,
    operation: ItemOperation,
    *,
    target_location_id: str | None = None,
) -> InventoryCheck:
    """Validate generic physical access, not story or behavior permissions.

    The 15-field contract intentionally has no operation list. Detailed action
    effects belong to behavior/story modules. The item layer only verifies that
    a concrete item exists, is accessible, and is held when an action changes
    it.
    """

    if item is None:
        return InventoryCheck(False, "missing_item", "物品不存在")
    if item.condition in {"expired", "destroyed"}:
        return InventoryCheck(False, "item_unavailable", "物品已经失效")
    if operation == "take":
        if target_location_id is None or not item_is_at_location(
            state, item, target_location_id
        ):
            return InventoryCheck(False, "item_not_accessible", "物品不在当前位置")
        return InventoryCheck(True, "allowed", "物品在当前位置可取用")
    if operation == "consume" and item_consumable_profile(item) is None:
        return InventoryCheck(False, "not_consumable", "该物品没有经过验证的消耗品规格")
    if operation in {
        "consume",
        "use",
        "equip",
        "combine",
        "discard",
        "destroy",
        "transfer",
    } and not item_is_owned_by(state, item, actor_id):
        return InventoryCheck(False, "item_not_owned", "行动者没有持有该物品")
    return InventoryCheck(True, "allowed", "物品存在且当前可操作")


def item_public_summary(item: ItemInstance) -> dict[str, Any]:
    """Return the public record shape plus derived values, without audit data."""

    result = item.to_payload()
    result["totalWeightGrams"] = item.total_weight_grams
    result["totalValueCrown"] = item.total_value_crown
    return result


__all__ = [
    "InventoryCheck",
    "can_operate",
    "container_for_item",
    "item_is_at_location",
    "item_is_owned_by",
    "item_owner",
    "item_public_summary",
    "validate_container_capacity",
    "validate_quantity",
]
