"""Event constructors owned by the item domain.

These functions validate candidate item changes and return events. They never
mutate a projection or write storage, preserving the authority boundary.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from trpg_server.core.state import Event
from trpg_server.items.durability import validate_item_durability
from trpg_server.items.models import ItemInstance


def _event_id(kind: str) -> str:
    return f"evt_item_{kind}_{uuid4().hex}"


def build_item_created_event(
    *,
    actor_id: str,
    world_time: int,
    item: ItemInstance,
) -> Event:
    """Build a schema-4 creation event from one complete 15-field record."""

    item.validate()
    validate_item_durability(
        category=item.category,
        properties=item.properties,
        durability=item.durability,
        require_for_eligible=True,
    )
    return Event(
        event_id=_event_id("created"),
        event_type="item.created",
        actor_id=actor_id,
        world_time=world_time,
        payload={"item": item.to_payload()},
        schema_version=4,
    )


def build_item_transferred_event(
    *,
    actor_id: str,
    world_time: int,
    item_id: str,
    to_container_id: str | None = None,
    to_location_id: str | None = None,
    quantity: int | None = None,
    to_item_id: str | None = None,
) -> Event:
    if (to_container_id is None) == (to_location_id is None):
        raise ValueError("transfer requires exactly one destination")
    if quantity is not None and quantity < 1:
        raise ValueError("transfer quantity must be positive")
    if to_item_id is not None and not to_item_id:
        raise ValueError("to_item_id cannot be empty")
    payload: dict[str, Any] = {
        "itemId": item_id,
        "toContainerId": to_container_id,
        "toLocationId": to_location_id,
    }
    if quantity is not None:
        payload["quantity"] = quantity
    if to_item_id is not None:
        payload["toItemId"] = to_item_id
    return Event(
        event_id=_event_id("transferred"),
        event_type="item.transferred",
        actor_id=actor_id,
        world_time=world_time,
        payload=payload,
        schema_version=3,
    )


def build_item_consumed_event(
    *, actor_id: str, world_time: int, item_id: str, quantity: int = 1
) -> Event:
    if quantity < 1:
        raise ValueError("consumed quantity must be positive")
    return Event(
        event_id=_event_id("consumed"),
        event_type="item.consumed",
        actor_id=actor_id,
        world_time=world_time,
        payload={"itemId": item_id, "quantity": quantity},
        schema_version=3,
    )


def build_item_condition_changed_event(
    *,
    actor_id: str,
    world_time: int,
    item_id: str,
    condition: str | None,
) -> Event:
    return Event(
        event_id=_event_id("condition"),
        event_type="item.condition_changed",
        actor_id=actor_id,
        world_time=world_time,
        payload={"itemId": item_id, "condition": condition},
        schema_version=4,
    )


def build_furniture_container_created_event(
    *,
    actor_id: str,
    world_time: int,
    container_id: str,
    structure_id: str,
    furniture_kind: str,
    name: str,
    description: str,
    capacity_weight_grams: int,
    capacity_volume_cm3: int,
    visible: bool = True,
) -> Event:
    """Build a fixed, structure-bound furniture container event.

    The event deliberately carries no item instance.  Contents can only be
    added later through normal item transfer/creation events.
    """

    if not all(
        type(value) is str and value.strip()
        for value in (container_id, structure_id, furniture_kind, name, description)
    ):
        raise ValueError("furniture identifiers and text must be non-empty strings")
    if type(capacity_weight_grams) is not int or capacity_weight_grams <= 0:
        raise ValueError("capacity_weight_grams must be positive")
    if type(capacity_volume_cm3) is not int or capacity_volume_cm3 <= 0:
        raise ValueError("capacity_volume_cm3 must be positive")
    if type(visible) is not bool:
        raise ValueError("visible must be boolean")
    return Event(
        event_id=_event_id("furniture_container"),
        event_type="container.created",
        actor_id=actor_id,
        world_time=world_time,
        payload={
            "containerId": container_id,
            "kind": "furniture",
            "ownerCharacterId": None,
            "locationId": structure_id,
            "capacityWeight": capacity_weight_grams,
            "capacityVolume": capacity_volume_cm3,
            "furnitureKind": furniture_kind,
            "furnitureName": name,
            "furnitureDescription": description,
            "structureId": structure_id,
            "fixed": True,
            "visible": visible,
            "sourceStatus": "reviewed",
            "confidence": 1.0,
            "basis": ["explicit furniture command"],
            "sourceRefs": ["runtime_command"],
            "modelAudit": None,
        },
        schema_version=1,
    )


__all__ = [
    "build_furniture_container_created_event",
    "build_item_condition_changed_event",
    "build_item_consumed_event",
    "build_item_created_event",
    "build_item_transferred_event",
]
