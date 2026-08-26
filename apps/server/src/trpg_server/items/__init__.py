"""15-field item records, furniture containers, events, and validation."""

from .catalog import ItemAtlas, ItemAtlasError, load_item_atlas, validate_item_atlas
from .equipment import ItemEquipmentProfile, item_equipment_profile
from .models import ItemContainer, ItemDefinition, ItemInstance, ItemRecord
from .commands import build_furniture_container_created_event
from .interaction import (
    InteractionRequest,
    ItemInteractionCandidate,
    ItemInteractionError,
    parse_interaction_candidate,
    validate_candidate_evidence,
)
from .provenance import (
    ItemSourceConfirmation,
    build_confirmed_item_creation_events,
    build_item_source_confirmed_event,
    item_is_usable_interaction_instance,
)
from .wear import (
    ClothingDailyWear,
    RepairResolution,
    WearResolution,
    resolve_behavior_wear,
    resolve_clothing_daily_wear,
    resolve_repair,
)

__all__ = [
    "ItemAtlas",
    "ItemAtlasError",
    "ItemContainer",
    "ItemDefinition",
    "ItemEquipmentProfile",
    "ItemInstance",
    "ItemRecord",
    "build_furniture_container_created_event",
    "InteractionRequest",
    "ItemInteractionCandidate",
    "ItemInteractionError",
    "ItemSourceConfirmation",
    "build_confirmed_item_creation_events",
    "build_item_source_confirmed_event",
    "load_item_atlas",
    "item_is_usable_interaction_instance",
    "item_equipment_profile",
    "parse_interaction_candidate",
    "validate_item_atlas",
    "validate_candidate_evidence",
    "ClothingDailyWear",
    "RepairResolution",
    "WearResolution",
    "resolve_behavior_wear",
    "resolve_clothing_daily_wear",
    "resolve_repair",
]
