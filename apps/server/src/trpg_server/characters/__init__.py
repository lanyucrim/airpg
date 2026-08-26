"""Character, NPC, relationship, cognition and inventory ownership domain."""

from trpg_server.characters.body import BODY_PARTS, HAND_SLOTS
from trpg_server.characters.checks import (
    ABILITY_LEVEL_MODIFIERS,
    AbilityCheckInput,
    AbilityCheckResult,
    PhysicalPurpose,
    PhysicalRequirements,
    ability_check_input_from_profile,
    ability_modifier,
    difficulty_to_dc,
    resolve_ability_check,
)
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
    "ABILITY_LEVEL_MODIFIERS",
    "AbilityCheckInput",
    "AbilityCheckResult",
    "PhysicalPurpose",
    "PhysicalRequirements",
    "ability_check_input_from_profile",
    "ability_modifier",
    "difficulty_to_dc",
    "resolve_ability_check",
    "EquipmentCheck",
    "build_external_injury_applied_event",
    "build_external_injury_cleared_event",
    "build_item_equipped_event",
    "build_item_unequipped_event",
    "choose_equipment_slots",
]
