"""Projection handlers for item-domain events.

All new item creation uses schema version 4 and carries one complete
15-field item record under ``payload.item``.  Events remain the source of
truth; these handlers only project already-confirmed events into state.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from trpg_server.core.projection_handlers import projection_handlers
from trpg_server.core.state import Event, Projection
from trpg_server.items.inventory import validate_container_capacity
from trpg_server.items.durability import validate_item_durability
from trpg_server.items.models import ItemContainer, ItemInstance


def _require_payload_fields(
    payload: Mapping[str, Any],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    event_type: str,
) -> None:
    keys = set(payload)
    missing = required.difference(keys)
    extra = keys.difference(required | optional)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if extra:
            details.append(f"unknown {sorted(extra)}")
        raise ValueError(f"{event_type} payload has " + "; ".join(details))


def _item(state: Projection, item_id: object) -> ItemInstance:
    if type(item_id) is not str or not item_id:
        raise ValueError("itemId must be a non-empty string")
    value = state.items.get(item_id)
    if value is None:
        raise ValueError(f"unknown item: {item_id}")
    return value


def _reject_if_equipped(state: Projection, item_id: str) -> None:
    """Keep an equipped binding from pointing at a moved or removed item."""

    if any(
        binding.get("itemId") == item_id
        for bindings in state.character_equipment.values()
        for binding in bindings.values()
    ):
        raise ValueError("equipped item must be unequipped before it can move or disappear")


def _observed_item(state: Projection, event: Event) -> ItemInstance:
    """Load the referenced item from a schema-3 non-mutating item event."""

    if event.schema_version != 3:
        raise ValueError(
            f"unsupported {event.event_type} schema version: {event.schema_version}"
        )
    _require_payload_fields(
        event.payload,
        required=frozenset({"itemId"}),
        optional=frozenset(
            {"characterId", "containerId", "interactionId", "sourceText", "targetId"}
        ),
        event_type=event.event_type,
    )
    return _item(state, event.payload["itemId"])


def _destination(
    state: Projection,
    payload: Mapping[str, Any],
    *,
    prefix: str = "to",
) -> tuple[str | None, str | None]:
    container_id = payload.get(f"{prefix}ContainerId")
    location_id = payload.get(f"{prefix}LocationId")
    if container_id is not None:
        if type(container_id) is not str or not container_id:
            raise ValueError("destination containerId must be a non-empty string")
        if container_id not in state.containers:
            raise ValueError(f"unknown destination container: {container_id}")
    if location_id is not None:
        if type(location_id) is not str or not location_id:
            raise ValueError("destination locationId must be a non-empty string")
        if location_id not in state.locations:
            raise ValueError(f"unknown destination location: {location_id}")
    if container_id is not None and location_id is not None:
        raise ValueError("item destination cannot include both containerId and locationId")
    if container_id is None and location_id is None:
        raise ValueError("item destination requires containerId or locationId")
    return container_id, location_id


def _validate_destination_capacity(
    state: Projection,
    item: ItemInstance,
    *,
    container_id: str | None,
    quantity: int,
    source_item_id: str | None = None,
) -> None:
    """Reject writes that would exceed a physical container's capacity."""

    if container_id is None:
        return
    if source_item_id is not None and container_id == item.container_id:
        # Moving a stack within its existing container has no net capacity
        # cost, including a split that remains in the same container.
        return
    check = validate_container_capacity(
        state,
        container_id,
        item,
        quantity,
        exclude_item_id=source_item_id,
    )
    if not check.allowed:
        raise ValueError(check.label)


def _project_new_item(
    state: Projection,
    record: Mapping[str, Any],
    *,
    source_event_id: str,
) -> None:
    """Project a validated 15-field instance while retaining its real source."""

    item = ItemInstance.from_payload(
        record,
        source_event_id=source_event_id,
        last_changed_event_id=source_event_id,
    )
    if item.item_id in state.items:
        raise ValueError(f"item already exists: {item.item_id}")
    if item.container_id is not None and item.container_id not in state.containers:
        raise ValueError(f"item references unknown container: {item.container_id}")
    if item.location_id is not None and item.location_id not in state.locations:
        raise ValueError(f"item references unknown location: {item.location_id}")
    # A generated daily definition is descriptive until a confirmed
    # acquisition event precedes this creation.  Catalog instances and legacy
    # replay records retain their existing path.
    confirmation = getattr(state, "item_source_confirmations", {}).get(item.item_id)
    if item.definition_id.startswith("daily_"):
        if confirmation is None or confirmation.get("definitionStatus") != "generated_daily":
            raise ValueError("generated daily item requires a confirmed acquisition source")
        if confirmation.get("definitionId") != item.definition_id:
            raise ValueError("daily item source confirmation does not match definition")
    _validate_destination_capacity(
        state,
        item,
        container_id=item.container_id,
        quantity=item.quantity,
    )
    state.items[item.item_id] = item


@projection_handlers.register("container.created")
def apply_container_created(state: Projection, event: Event) -> None:
    if event.schema_version != 1:
        raise ValueError(
            f"unsupported container.created schema version: {event.schema_version}"
        )
    payload = event.payload
    container_id = str(payload["containerId"])
    if container_id in state.containers:
        raise ValueError(f"container already exists: {container_id}")
    furniture_kind = payload.get("furnitureKind")
    furniture_name = payload.get("furnitureName")
    furniture_description = payload.get("furnitureDescription")
    structure_id = payload.get("structureId")
    fixed = payload.get("fixed", False)
    visible = payload.get("visible", True)
    if any(
        value is not None
        for value in (furniture_kind, furniture_name, furniture_description, structure_id)
    ) and str(payload.get("kind")) != "furniture":
        raise ValueError("furniture metadata requires kind=furniture")
    if type(fixed) is not bool or type(visible) is not bool:
        raise ValueError("container fixed and visible must be boolean")
    if str(payload.get("kind")) == "furniture":
        if type(furniture_kind) is not str or not furniture_kind:
            raise ValueError("furnitureKind must be a non-empty string")
        if type(furniture_name) is not str or not furniture_name:
            raise ValueError("furnitureName must be a non-empty string")
        if type(furniture_description) is not str or not furniture_description:
            raise ValueError("furnitureDescription must be a non-empty string")
        if type(structure_id) is not str or not structure_id:
            raise ValueError("structureId must be a non-empty string")
        location_id = payload.get("locationId")
        location = state.locations.get(location_id)
        if location is None:
            raise ValueError(f"furniture references unknown location: {location_id}")
        if location.kind == "street":
            raise ValueError("street cannot contain furniture")
        if location.kind not in {"room", "floor", "yard"}:
            raise ValueError("furniture location must be an internal structure")
        if location.parent_id is None:
            raise ValueError("furniture structure must have a parent location")
        if structure_id != location_id:
            raise ValueError("furniture structureId must match its internal locationId")
        if not fixed:
            raise ValueError("furniture containers must be fixed")
        for field_name in ("capacityWeight", "capacityVolume"):
            raw = payload.get(field_name)
            if type(raw) is not int or raw <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        source_status = payload.get("sourceStatus")
        if source_status not in {"model_generated", "reviewed", "program_seeded"}:
            raise ValueError("furniture sourceStatus is invalid")
        confidence = payload.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= float(confidence) <= 1
        ):
            raise ValueError("furniture confidence must be between 0 and 1")
        for field_name in ("basis", "sourceRefs"):
            values = payload.get(field_name)
            if not isinstance(values, list) or not values or not all(
                type(value) is str and value.strip() for value in values
            ):
                raise ValueError(f"furniture {field_name} must be a non-empty string array")
        if payload.get("modelAudit") is not None and not isinstance(
            payload.get("modelAudit"), Mapping
        ):
            raise ValueError("furniture modelAudit must be an object")
    container = ItemContainer(
        container_id=container_id,
        kind=str(payload["kind"]),
        owner_character_id=(
            str(payload["ownerCharacterId"])
            if payload.get("ownerCharacterId") is not None
            else None
        ),
        location_id=(
            str(payload["locationId"]) if payload.get("locationId") is not None else None
        ),
        capacity_weight=(
            int(payload["capacityWeight"])
            if payload.get("capacityWeight") is not None
            else None
        ),
        capacity_volume=(
            int(payload["capacityVolume"])
            if payload.get("capacityVolume") is not None
            else None
        ),
        source_event_id=event.event_id,
        furniture_kind=furniture_kind,
        furniture_name=furniture_name,
        furniture_description=furniture_description,
        structure_id=structure_id,
        fixed=fixed,
        visible=visible,
        source_status=payload.get("sourceStatus"),
        confidence=(
            float(payload["confidence"])
            if payload.get("confidence") is not None
            else None
        ),
        basis=tuple(payload.get("basis", ())),
        source_refs=tuple(payload.get("sourceRefs", ())),
        model_audit=(
            dict(payload["modelAudit"])
            if isinstance(payload.get("modelAudit"), Mapping)
            else None
        ),
    )
    container.validate_anchor()
    state.containers[container_id] = container


@projection_handlers.register("item.created")
def apply_item_created(state: Projection, event: Event) -> None:
    if event.schema_version not in {3, 4}:
        raise ValueError(f"unsupported item.created schema version: {event.schema_version}")
    payload = event.payload
    _require_payload_fields(
        payload,
        required=frozenset({"item"}),
        event_type="item.created",
    )
    record = payload.get("item")
    if not isinstance(record, Mapping):
        raise ValueError("item.created requires a 15-field item payload")
    if event.schema_version == 4:
        properties = record.get("properties")
        if not isinstance(properties, Mapping):
            raise ValueError("item.created item.properties must be an object")
        validate_item_durability(
            category=record.get("category"),
            properties=properties,
            durability=record.get("durability"),
            require_for_eligible=True,
        )
    # Schema 3 remains solely so already-recorded events can be replayed.  It
    # predates the rule that every durable instance needs an initial profile.
    _project_new_item(state, record, source_event_id=event.event_id)


@projection_handlers.register("item.transferred")
def apply_item_transferred(state: Projection, event: Event) -> None:
    if event.schema_version != 3:
        raise ValueError(
            f"unsupported item.transferred schema version: {event.schema_version}"
    )
    payload = event.payload
    _require_payload_fields(
        payload,
        required=frozenset({"itemId"}),
        optional=frozenset(
            {"toContainerId", "toLocationId", "quantity", "toItemId"}
        ),
        event_type="item.transferred",
    )
    item = _item(state, payload["itemId"])
    _reject_if_equipped(state, item.item_id)
    quantity = payload.get("quantity", item.quantity)
    if type(quantity) is not int:
        raise ValueError("transfer quantity must be an integer")
    if quantity < 1 or quantity > item.quantity:
        raise ValueError("transferred quantity exceeds item quantity")
    destination_container, destination_location = _destination(state, payload)
    _validate_destination_capacity(
        state,
        item,
        container_id=destination_container,
        quantity=quantity,
        source_item_id=item.item_id,
    )
    if quantity == item.quantity:
        item.container_id = destination_container
        item.location_id = destination_location
        item.last_changed_event_id = event.event_id
        return
    if not item.stackable:
        raise ValueError("cannot split a non-stackable item")
    new_item_id = payload.get("toItemId")
    if type(new_item_id) is not str or not new_item_id or new_item_id in state.items:
        raise ValueError("partial transfer requires an unused toItemId")
    moved = deepcopy(item)
    moved.item_id = new_item_id
    moved.quantity = quantity
    moved.container_id = destination_container
    moved.location_id = destination_location
    moved.source_event_id = event.event_id
    moved.last_changed_event_id = event.event_id
    moved.validate()
    item.quantity -= quantity
    item.last_changed_event_id = event.event_id
    state.items[moved.item_id] = moved


@projection_handlers.register("item.consumed")
def apply_item_consumed(state: Projection, event: Event) -> None:
    if event.schema_version != 3:
        raise ValueError(f"unsupported item.consumed schema version: {event.schema_version}")
    payload = event.payload
    _require_payload_fields(
        payload,
        required=frozenset({"itemId"}),
        optional=frozenset({"quantity"}),
        event_type="item.consumed",
    )
    item = _item(state, payload["itemId"])
    _reject_if_equipped(state, item.item_id)
    quantity = payload.get("quantity", 1)
    if type(quantity) is not int:
        raise ValueError("consumed quantity must be an integer")
    if quantity < 1 or quantity > item.quantity:
        raise ValueError("consumed quantity exceeds item quantity")
    if quantity == item.quantity:
        state.items.pop(item.item_id)
    else:
        item.quantity -= quantity
        item.last_changed_event_id = event.event_id


@projection_handlers.register("item.condition_changed")
def apply_item_condition_changed(state: Projection, event: Event) -> None:
    if event.schema_version not in {3, 4}:
        raise ValueError(
            f"unsupported item.condition_changed schema version: {event.schema_version}"
        )
    payload = event.payload
    _require_payload_fields(
        payload,
        required=frozenset({"itemId", "condition"}),
        optional=(
            frozenset({"durability"})
            if event.schema_version == 3
            else frozenset()
        ),
        event_type="item.condition_changed",
    )
    item = _item(state, payload["itemId"])
    condition = payload["condition"]
    candidate = deepcopy(item)
    candidate.condition = condition
    # Only historical schema-3 events may carry a durability snapshot.  New
    # condition changes cannot invent wear semantics before that event design
    # is agreed.
    if event.schema_version == 3 and "durability" in payload:
        candidate.durability = deepcopy(payload["durability"])
    candidate.last_changed_event_id = event.event_id
    candidate.validate()
    item.condition = candidate.condition
    if event.schema_version == 3 and "durability" in payload:
        item.durability = candidate.durability
    item.last_changed_event_id = event.event_id


@projection_handlers.register("item.purchased")
def apply_item_purchased(state: Projection, event: Event) -> None:
    if event.schema_version != 3:
        raise ValueError(f"unsupported item.purchased schema version: {event.schema_version}")
    _require_payload_fields(
        event.payload,
        required=frozenset({"item"}),
        optional=frozenset({"transactionId", "paymentEventId"}),
        event_type="item.purchased",
    )
    record = event.payload.get("item")
    if not isinstance(record, Mapping):
        raise ValueError("item.purchased requires a 15-field item payload")
    _project_new_item(state, record, source_event_id=event.event_id)


@projection_handlers.register("item.used", "item.inspected", "item.examined")
def apply_item_observed(state: Projection, event: Event) -> None:
    """Observation/use is auditable but does not invent item effects here."""

    item = _observed_item(state, event)
    item.last_changed_event_id = event.event_id


@projection_handlers.register("item.discarded")
def reject_retired_item_discarded(state: Projection, event: Event) -> None:
    """Reject the old deletion-style discard event.

    Discarding is now a normal ``item.transferred`` to the actor's current
    location.  Retaining a rejecting handler avoids silently ignoring a
    retired event type during replay.
    """

    del state, event
    raise ValueError("item.discarded is retired; use item.transferred")


@projection_handlers.register("item.destroyed", "item.expired")
def apply_item_removed(state: Projection, event: Event) -> None:
    item = _observed_item(state, event)
    if event.event_type == "item.destroyed" and item.is_plot_item:
        raise ValueError(
            "plot items require a story-confirmed removal event"
        )
    _reject_if_equipped(state, item.item_id)
    state.items.pop(item.item_id)


__all__ = [
    "apply_container_created",
    "apply_item_condition_changed",
    "apply_item_consumed",
    "apply_item_created",
    "apply_item_purchased",
    "apply_item_transferred",
]
