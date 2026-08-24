"""Authoritative runtime models for the 15-field item contract.

Definitions and instances share the same observable record shape. A runtime
instance is the only one of those records that may live in a projection; a
definition in the atlas is descriptive content and does not assert existence.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from collections.abc import Mapping
from typing import Any

from trpg_server.items.contract import record_field_error
from trpg_server.items.durability import DurabilityError, validate_item_durability
from trpg_server.items.functions import ItemFunctionError, validate_item_properties


@dataclass(slots=True)
class ItemContainer:
    """A physical container anchored to exactly one character or location."""

    container_id: str
    kind: str
    owner_character_id: str | None = None
    location_id: str | None = None
    capacity_weight: int | None = None
    capacity_volume: int | None = None
    source_event_id: str | None = None
    # Furniture is a location-bound container, not a portable item.  These
    # fields remain optional so historical and ordinary containers keep their
    # published shape and replay behavior.
    furniture_kind: str | None = None
    furniture_name: str | None = None
    furniture_description: str | None = None
    structure_id: str | None = None
    fixed: bool = False
    visible: bool = True
    source_status: str | None = None
    confidence: float | None = None
    basis: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    model_audit: dict[str, Any] | None = None

    def validate_anchor(self) -> None:
        if (self.owner_character_id is None) == (self.location_id is None):
            raise ValueError("container must be anchored to exactly one character or location")
        if self.kind == "furniture":
            if self.owner_character_id is not None or self.location_id is None:
                raise ValueError("furniture container must be anchored to a location")
            if not self.furniture_kind:
                raise ValueError("furniture container requires furnitureKind")
            if not self.furniture_name:
                raise ValueError("furniture container requires furnitureName")
            if not self.furniture_description:
                raise ValueError("furniture container requires furnitureDescription")
            if not self.structure_id:
                raise ValueError("furniture container requires structureId")
            if not self.fixed:
                raise ValueError("furniture containers must be fixed in place")
            for label, value in (
                ("capacityWeight", self.capacity_weight),
                ("capacityVolume", self.capacity_volume),
            ):
                if value is None or type(value) is not int or value <= 0:
                    raise ValueError(f"furniture {label} must be a positive integer")
            if self.source_status not in {"model_generated", "reviewed", "program_seeded"}:
                raise ValueError("furniture sourceStatus is invalid")
            if self.confidence is None or not 0 <= float(self.confidence) <= 1:
                raise ValueError("furniture confidence must be between 0 and 1")
            if not self.basis or not all(type(value) is str and value for value in self.basis):
                raise ValueError("furniture basis must contain evidence")
            if not all(type(value) is str and value for value in self.source_refs):
                raise ValueError("furniture sourceRefs must contain strings")
            if self.model_audit is not None and not isinstance(self.model_audit, Mapping):
                raise ValueError("furniture modelAudit must be an object")
        elif any(
            value is not None
            for value in (
                self.furniture_kind,
                self.furniture_name,
                self.furniture_description,
                self.structure_id,
            )
        ):
            raise ValueError("non-furniture container cannot carry furniture metadata")
        elif any(
            value not in (None, (), [], {})
            for value in (self.source_status, self.confidence, self.basis, self.source_refs, self.model_audit)
        ):
            raise ValueError("non-furniture container cannot carry furniture audit metadata")


@dataclass(slots=True)
class ItemRecord:
    """One concrete item or stack in the authoritative projection.

    Attribute names are Pythonic, while :meth:`to_payload` and
    :meth:`from_payload` preserve the published camel-case content contract.
    No ownership, audit, story meaning, operations, or visibility fields are
    embedded here.
    """

    item_id: str
    definition_id: str
    name: str
    description: str
    category: str
    is_plot_item: bool
    quantity: int
    stackable: bool
    unit_weight_grams: int | None
    value_crown: int | None
    condition: str | None
    durability: dict[str, float] | None
    container_id: str | None
    location_id: str | None
    properties: dict[str, Any] = field(default_factory=dict)
    source_event_id: str | None = None
    last_changed_event_id: str | None = None

    def __post_init__(self) -> None:
        """Validate direct construction as strictly as event deserialization."""

        self.validate()

    @property
    def id(self) -> str:
        return self.item_id

    @property
    def total_weight_grams(self) -> int | None:
        if self.unit_weight_grams is None:
            return None
        return self.quantity * self.unit_weight_grams

    @property
    def total_value_crown(self) -> int | None:
        if self.value_crown is None:
            return None
        return self.quantity * self.value_crown

    def validate(self, *, definition: bool = False) -> None:
        _require_string(self.item_id, "id")
        _require_string(self.definition_id, "definitionId")
        _require_string(self.name, "name")
        _require_string(self.description, "description")
        _require_string(self.category, "category")
        if type(self.is_plot_item) is not bool:
            raise ValueError("isPlotItem must be a boolean")
        if type(self.quantity) is not int or self.quantity < 1:
            raise ValueError("quantity must be a positive integer")
        if type(self.stackable) is not bool:
            raise ValueError("stackable must be a boolean")
        if not self.stackable and self.quantity != 1:
            raise ValueError("non-stackable item quantity must be 1")
        _validate_nullable_non_negative_int(
            self.unit_weight_grams, "unitWeightGrams"
        )
        _validate_nullable_non_negative_int(self.value_crown, "valueCrown")
        if self.condition is not None and (
            type(self.condition) is not str or not self.condition
        ):
            raise ValueError("condition must be null or a non-empty string")
        _validate_nullable_string(self.container_id, "containerId")
        _validate_nullable_string(self.location_id, "locationId")
        if self.container_id is not None and self.location_id is not None:
            raise ValueError("item cannot be directly in a container and a location")
        if not definition and self.container_id is None and self.location_id is None:
            raise ValueError("runtime item requires containerId or locationId")
        if not isinstance(self.properties, Mapping):
            raise ValueError("properties must be an object")
        try:
            self.properties = validate_item_properties(
                self.properties,
                category=self.category,
                path="properties",
            )
        except ItemFunctionError as error:
            raise ValueError(str(error)) from error
        try:
            self.durability = validate_item_durability(
                category=self.category,
                properties=self.properties,
                durability=self.durability,
            )
        except DurabilityError as error:
            raise ValueError(str(error)) from error
        _validate_nullable_string(self.source_event_id, "sourceEventId")
        _validate_nullable_string(self.last_changed_event_id, "lastChangedEventId")
        if definition:
            if self.item_id != self.definition_id:
                raise ValueError("definition id must equal record id")
            if self.quantity != 1:
                raise ValueError("definition quantity must be 1")
            if self.condition is not None or self.durability is not None:
                raise ValueError("definition cannot hold mutable state")
            if self.container_id is not None or self.location_id is not None:
                raise ValueError("definition cannot hold runtime placement")

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.item_id,
            "definitionId": self.definition_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "isPlotItem": self.is_plot_item,
            "quantity": self.quantity,
            "stackable": self.stackable,
            "unitWeightGrams": self.unit_weight_grams,
            "valueCrown": self.value_crown,
            "condition": self.condition,
            "durability": deepcopy(self.durability),
            "containerId": self.container_id,
            "locationId": self.location_id,
            "properties": deepcopy(dict(self.properties)),
        }

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        source_event_id: str | None = None,
        last_changed_event_id: str | None = None,
    ) -> "ItemRecord":
        if not isinstance(payload, Mapping):
            raise ValueError("item must be an object")
        raw = dict(payload)
        error = record_field_error(raw, path="item")
        if error is not None:
            raise ValueError(error)
        _validate_payload_types(raw)
        durability = raw["durability"]
        result = cls(
            item_id=raw["id"],
            definition_id=raw["definitionId"],
            name=raw["name"],
            description=raw["description"],
            category=raw["category"],
            is_plot_item=raw["isPlotItem"],
            quantity=raw["quantity"],
            stackable=raw["stackable"],
            unit_weight_grams=raw["unitWeightGrams"],
            value_crown=raw["valueCrown"],
            condition=raw["condition"],
            durability=deepcopy(durability),
            container_id=raw["containerId"],
            location_id=raw["locationId"],
            properties=deepcopy(raw["properties"]),
            source_event_id=source_event_id,
            last_changed_event_id=last_changed_event_id,
        )
        result.validate()
        return result


def _validate_payload_types(raw: Mapping[str, Any]) -> None:
    """Reject malformed event records before any Python coercion can occur."""

    for key in ("id", "definitionId", "name", "description", "category"):
        if not isinstance(raw[key], str):
            raise ValueError(f"item.{key} must be a string")
    for key in ("isPlotItem", "stackable"):
        if not isinstance(raw[key], bool):
            raise ValueError(f"item.{key} must be boolean")
    if isinstance(raw["quantity"], bool) or not isinstance(raw["quantity"], int):
        raise ValueError("item.quantity must be an integer")
    for key in ("unitWeightGrams", "valueCrown"):
        value = raw[key]
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError(f"item.{key} must be null or an integer")
    for key in ("condition", "containerId", "locationId"):
        value = raw[key]
        if value is not None and not isinstance(value, str):
            raise ValueError(f"item.{key} must be null or a string")
    if raw["durability"] is not None and not isinstance(raw["durability"], Mapping):
        raise ValueError("item.durability must be null or an object")
    if not isinstance(raw["properties"], Mapping):
        raise ValueError("item.properties must be an object")


class ItemDefinition(ItemRecord):
    """Named alias for a 15-field definition record in content tooling."""

    def validate(self, *, definition: bool = True) -> None:
        del definition
        super().validate(definition=True)


class ItemInstance(ItemRecord):
    """Named alias for a 15-field concrete runtime record."""

    def validate(self, *, definition: bool = False) -> None:
        if definition:
            raise ValueError("a runtime item cannot be validated as a definition")
        super().validate(definition=False)
        if self.item_id == self.definition_id:
            raise ValueError("runtime item id must differ from definitionId")


def _require_string(value: object, label: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{label} must be a non-empty string")


def _validate_nullable_string(value: object, label: str) -> None:
    if value is not None:
        _require_string(value, label)


def _validate_nullable_non_negative_int(value: object, label: str) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{label} must be null or a non-negative integer")


__all__ = [
    "ItemContainer",
    "ItemDefinition",
    "ItemInstance",
    "ItemRecord",
]
