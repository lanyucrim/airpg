"""Character, NPC, relationship, cognition and inventory ownership domain."""

from trpg_server.characters.body import BODY_PARTS, HAND_SLOTS
from trpg_server.characters.equipment import (
    EquipmentCheck,
    build_external_injury_applied_event,
    build_external_injury_cleared_event,
    build_item_equipped_event,
    build_item_unequipped_event,
    choose_equipment_slots,
)

from trpg_server.characters.inventory import (
    InventoryBinding,
    InventoryContainerSpec,
    InventoryResolution,
    character_inventory_container,
    character_inventory_container_id,
    character_inventory_item_ids,
    ensure_inventory_containers,
)

__all__ = [
    "InventoryBinding",
    "InventoryContainerSpec",
    "InventoryResolution",
    "character_inventory_container",
    "character_inventory_container_id",
    "character_inventory_item_ids",
    "ensure_inventory_containers",
    "BODY_PARTS",
    "HAND_SLOTS",
    "EquipmentCheck",
    "build_external_injury_applied_event",
    "build_external_injury_cleared_event",
    "build_item_equipped_event",
    "build_item_unequipped_event",
    "choose_equipment_slots",
]
