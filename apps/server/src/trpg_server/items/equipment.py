"""Item-side equipment capability metadata.

The item contract deliberately stays at 15 fields.  Wearable/held behavior is
an objective, category-specific property of a definition, not a second set of
top-level item fields.  This module only reads that metadata; character rules
decide whether a body can use the requested slots.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from trpg_server.items.functions import ItemFunctionError, parse_equipment_profile
from trpg_server.items.models import ItemInstance


@dataclass(frozen=True, slots=True)
class ItemEquipmentProfile:
    mode: Literal["held", "worn"]
    slot_ids: tuple[str, ...]
    hand_count: int = 0


def item_equipment_profile(item: ItemInstance) -> ItemEquipmentProfile | None:
    """Read and validate an item's optional equipment capability.

    Missing metadata means the item is not equipable.  It never gets inferred
    from a name or category, because that would turn incomplete atlas data
    into an authoritative physical capability.
    """

    raw = item.properties.get("equipment")
    if raw is None:
        return None
    try:
        profile = parse_equipment_profile(raw)
    except ItemFunctionError:
        return None
    return ItemEquipmentProfile(profile.mode, profile.slot_ids, profile.hand_count)


__all__ = ["ItemEquipmentProfile", "item_equipment_profile"]
