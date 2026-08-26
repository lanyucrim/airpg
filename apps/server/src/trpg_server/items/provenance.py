"""Explicit acquisition confirmation for dynamically defined daily items.

The 15-field item record intentionally stays small.  Acquisition provenance
therefore lives in an event and a read-only projection index, never in extra
item fields.  Existing atlas instances remain replayable; new dynamically
generated definitions use :func:`build_confirmed_item_creation_events` so the
source decision is auditable before the ``item.created`` event.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from trpg_server.core.state import Event, Projection
from trpg_server.items.commands import build_item_created_event
from trpg_server.items.models import ItemInstance


ItemSourceKind = Literal[
    "catalog_seed",
    "scenario_grant",
    "location_take",
    "furniture_take",
    "npc_transfer",
    "purchase",
    "theft",
    "recipe",
    "player_defined",
]

ITEM_SOURCE_KINDS = frozenset(
    {
        "catalog_seed",
        "scenario_grant",
        "location_take",
        "furniture_take",
        "npc_transfer",
        "purchase",
        "theft",
        "recipe",
        "player_defined",
    }
)
GENERATED_DAILY_SOURCE_KINDS = frozenset(
    {"location_take", "furniture_take", "npc_transfer", "purchase", "theft", "recipe"}
)


class ItemProvenanceError(ValueError):
    pass


def _text(value: object, field: str, maximum: int = 160) -> str:
    if type(value) is not str or not value.strip() or len(value.strip()) > maximum:
        raise ItemProvenanceError(f"{field} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True, slots=True)
class ItemSourceConfirmation:
    item_id: str
    definition_id: str
    source_kind: ItemSourceKind
    source_event_id: str | None = None
    definition_status: Literal["catalog", "generated_daily", "player_defined"] = "catalog"

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_id", _text(self.item_id, "item_id"))
        object.__setattr__(self, "definition_id", _text(self.definition_id, "definition_id"))
        if self.source_kind not in ITEM_SOURCE_KINDS:
            raise ItemProvenanceError(f"unknown source kind: {self.source_kind}")
        if self.source_event_id is not None:
            object.__setattr__(self, "source_event_id", _text(self.source_event_id, "source_event_id"))
        if self.definition_status not in {"catalog", "generated_daily", "player_defined"}:
            raise ItemProvenanceError("definition_status is invalid")
        if self.definition_status == "generated_daily" and self.source_kind not in GENERATED_DAILY_SOURCE_KINDS:
            raise ItemProvenanceError(
                "generated_daily definitions require a confirmed acquisition source"
            )

    def to_payload(self) -> dict[str, Any]:
        return {
            "itemId": self.item_id,
            "definitionId": self.definition_id,
            "sourceKind": self.source_kind,
            "sourceEventId": self.source_event_id,
            "definitionStatus": self.definition_status,
        }


def build_item_source_confirmed_event(
    *,
    actor_id: str,
    world_time: int,
    confirmation: ItemSourceConfirmation,
) -> Event:
    """Build a source decision event; it never creates an item itself."""

    return Event(
        event_id=f"evt_item_source_confirmed_{uuid4().hex}",
        event_type="item.source_confirmed",
        actor_id=actor_id,
        world_time=world_time,
        payload=confirmation.to_payload(),
        schema_version=1,
    )


def build_confirmed_item_creation_events(
    *,
    actor_id: str,
    world_time: int,
    item: ItemInstance,
    source_kind: ItemSourceKind,
    source_event_id: str | None = None,
    definition_status: Literal["catalog", "generated_daily", "player_defined"] = "catalog",
) -> tuple[Event, Event]:
    """Return ``source_confirmed`` followed by a normal item creation event."""

    confirmation = ItemSourceConfirmation(
        item_id=item.item_id,
        definition_id=item.definition_id,
        source_kind=source_kind,
        source_event_id=source_event_id,
        definition_status=definition_status,
    )
    source = build_item_source_confirmed_event(
        actor_id=actor_id,
        world_time=world_time,
        confirmation=confirmation,
    )
    created = build_item_created_event(
        actor_id=actor_id,
        world_time=world_time,
        item=item,
    )
    return source, created


def validate_source_confirmation_against_state(
    state: Projection,
    confirmation: ItemSourceConfirmation,
) -> None:
    """Validate a confirmation's causal predecessor and uniqueness."""

    if confirmation.item_id in state.items:
        raise ItemProvenanceError("source confirmation cannot be added after item creation")
    existing = getattr(state, "item_source_confirmations", {}).get(confirmation.item_id)
    if existing is not None:
        raise ItemProvenanceError("item already has a source confirmation")
    if confirmation.source_event_id is not None:
        if confirmation.source_event_id not in state.confirmed_event_ids:
            raise ItemProvenanceError("sourceEventId must reference an earlier confirmed event")


def item_source_confirmation(
    state: Projection,
    item_id: str,
) -> Mapping[str, Any] | None:
    return getattr(state, "item_source_confirmations", {}).get(item_id)


def item_is_usable_interaction_instance(
    state: Projection,
    item: ItemInstance | None,
    *,
    require_dynamic_confirmation: bool = True,
) -> tuple[bool, str]:
    """Return whether an item can enter an interaction request.

    Every current runtime item has a creation source event.  Dynamically
    generated definitions additionally require an explicit source confirmation
    record; legacy atlas instances are accepted for replay compatibility.
    """

    if item is None:
        return False, "missing_item"
    if item.item_id not in state.items:
        return False, "item_not_projected"
    if item.source_event_id is None or item.source_event_id not in state.confirmed_event_ids:
        return False, "item_source_unconfirmed"
    confirmation = item_source_confirmation(state, item.item_id)
    is_generated_daily = item.definition_id.startswith("daily_")
    if require_dynamic_confirmation and is_generated_daily:
        if confirmation is None:
            return False, "daily_item_source_unconfirmed"
        if confirmation.get("definitionStatus") != "generated_daily":
            return False, "daily_item_definition_status_invalid"
        if confirmation.get("definitionId") != item.definition_id:
            return False, "daily_item_definition_mismatch"
        if confirmation.get("sourceKind") not in GENERATED_DAILY_SOURCE_KINDS:
            return False, "daily_item_source_invalid"
    elif confirmation is not None:
        status = confirmation.get("definitionStatus")
        if status == "generated_daily" and confirmation.get("sourceKind") not in GENERATED_DAILY_SOURCE_KINDS:
            return False, "daily_item_source_invalid"
    return True, "allowed"


__all__ = [
    "GENERATED_DAILY_SOURCE_KINDS",
    "ITEM_SOURCE_KINDS",
    "ItemProvenanceError",
    "ItemSourceConfirmation",
    "ItemSourceKind",
    "build_confirmed_item_creation_events",
    "build_item_source_confirmed_event",
    "item_is_usable_interaction_instance",
    "item_source_confirmation",
    "validate_source_confirmation_against_state",
]
