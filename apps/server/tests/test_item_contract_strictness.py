from __future__ import annotations

import copy

import pytest

from trpg_server.core.state import Event, Projection
from trpg_server.items.events import (
    apply_item_consumed,
    apply_item_created,
    apply_item_transferred,
)
from trpg_server.items.models import ItemDefinition, ItemInstance


def _record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "id": "item_bread_001",
        "definitionId": "bread",
        "name": "黑麦面包",
        "description": "一条黑麦面包。",
        "category": "food",
        "isPlotItem": False,
        "quantity": 1,
        "stackable": True,
        "unitWeightGrams": 450,
        "valueCrown": 12,
        "condition": "intact",
        "durability": None,
        "containerId": None,
        "locationId": "kitchen",
        "properties": {},
    }
    record.update(overrides)
    return record


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", True),
        ("quantity", "1"),
        ("isPlotItem", 1),
        ("stackable", "false"),
        ("unitWeightGrams", False),
        ("valueCrown", "12"),
        ("condition", 1),
        ("containerId", 7),
        ("properties", []),
    ],
)
def test_item_record_rejects_implicit_type_coercion(field: str, value: object) -> None:
    payload = _record(**{field: value})

    with pytest.raises(ValueError):
        ItemInstance.from_payload(payload)


def test_runtime_item_requires_exactly_one_placement() -> None:
    with pytest.raises(ValueError, match="requires containerId or locationId"):
        ItemInstance.from_payload(_record(locationId=None))

    with pytest.raises(ValueError, match="cannot be directly in a container and a location"):
        ItemInstance.from_payload(_record(containerId="pack"))


def test_definition_allows_no_placement_but_runtime_instance_does_not() -> None:
    definition = _record(
        id="bread",
        definitionId="bread",
        quantity=1,
        condition=None,
        durability=None,
        containerId=None,
        locationId=None,
    )

    ItemDefinition.from_payload(definition)

    with pytest.raises(ValueError, match="runtime item requires containerId or locationId"):
        ItemInstance.from_payload(definition | {"id": "item_bread_002"})


def test_schema_three_creation_accepts_a_direct_location() -> None:
    state = Projection(campaign_id="test")
    state.locations["kitchen"] = object()  # type: ignore[assignment]
    event = Event(
        "evt_item_created",
        "item.created",
        "system",
        0,
        {"item": _record()},
        schema_version=3,
    )

    apply_item_created(state, event)

    assert state.items["item_bread_001"].location_id == "kitchen"
    assert state.items["item_bread_001"].container_id is None


@pytest.mark.parametrize("event_type", ["item.transferred", "item.consumed"])
def test_transfer_and_consumption_reject_schema_one(event_type: str) -> None:
    state = Projection(campaign_id="test")
    state.locations["kitchen"] = object()  # type: ignore[assignment]
    created = Event(
        "evt_item_created",
        "item.created",
        "system",
        0,
        {"item": _record()},
        schema_version=3,
    )
    apply_item_created(state, created)
    payload = (
        {"itemId": "item_bread_001", "toLocationId": "kitchen"}
        if event_type == "item.transferred"
        else {"itemId": "item_bread_001", "quantity": 1}
    )
    event = Event("evt_legacy", event_type, "system", 1, copy.deepcopy(payload), 1)

    with pytest.raises(ValueError, match="unsupported"):
        if event_type == "item.transferred":
            apply_item_transferred(state, event)
        else:
            apply_item_consumed(state, event)


def test_created_event_rejects_non_contract_payload_fields() -> None:
    state = Projection(campaign_id="test")
    state.locations["kitchen"] = object()  # type: ignore[assignment]
    event = Event(
        "evt_extra_field",
        "item.created",
        "system",
        0,
        {"item": _record(), "rights": {}},
        schema_version=3,
    )

    with pytest.raises(ValueError, match="unknown"):
        apply_item_created(state, event)


def test_item_condition_is_an_open_physical_state_string() -> None:
    item = ItemInstance.from_payload(_record(condition="waterlogged"))

    assert item.condition == "waterlogged"
