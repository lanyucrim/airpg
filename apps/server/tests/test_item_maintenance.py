from __future__ import annotations

from dataclasses import dataclass

from trpg_server.behavior.commands.maintenance import resolve_maintenance_command
from trpg_server.core.projection import replay
from trpg_server.core.state import Event, ParsedCommand, Projection
from trpg_server.items.models import ItemInstance


@dataclass
class _Adapter:
    repair_output: dict[str, object]
    wear_output: dict[str, object]
    available: bool = True
    calls: list[str] | None = None

    def __post_init__(self) -> None:
        self.calls = []

    def assess_repair(self, request):  # type: ignore[no-untyped-def]
        del request
        assert self.calls is not None
        self.calls.append("repair")
        return self.repair_output

    def assess_wear(self, request):  # type: ignore[no-untyped-def]
        del request
        assert self.calls is not None
        self.calls.append("wear")
        return self.wear_output


def _item(
    item_id: str,
    *,
    category: str,
    durability: dict[str, float] | None,
    container_id: str | None = "bag",
) -> ItemInstance:
    return ItemInstance(
        item_id=item_id,
        definition_id=f"{item_id}_definition",
        name=item_id,
        description="可观察的材料和结构描述。",
        category=category,
        is_plot_item=False,
        quantity=1,
        stackable=False,
        unit_weight_grams=100,
        value_crown=5,
        condition="worn" if durability else "good",
        durability=durability,
        container_id=container_id,
        location_id=None,
        properties=(
            {"equipment": {"mode": "held", "slotIds": ["left_hand"], "handCount": 1}}
            if category == "tool"
            else {}
        ),
    )


def _state(*, include_tool: bool = True) -> tuple[Projection, list[Event]]:
    state = Projection(campaign_id="maintenance")
    state.world_time = 10
    state.character_locations["player"] = "room"
    state.character_profiles["player"] = {
        "abilities": [
            {
                "abilityId": "mechanical_repair",
                "level": "competent",
                "sourceStatus": "canon",
            }
        ]
    }
    target = _item("target", category="tool", durability={"current": 40.0, "max": 100.0})
    material = _item("material", category="material", durability=None)
    state.items.update({target.item_id: target, material.item_id: material})
    if include_tool:
        tool = _item("repair_tool", category="tool", durability={"current": 100.0, "max": 100.0})
        state.items[tool.item_id] = tool
    state.containers["bag"] = type("Container", (), {"owner_character_id": "player", "location_id": None})()  # type: ignore[assignment]
    return state, []


def _command(**parameters: object) -> ParsedCommand:
    values = {
        "itemId": "target",
        "materialItemIds": ["material"],
        "toolItemIds": ["repair_tool"],
        "repairLevel": "standard",
        **parameters,
    }
    return ParsedCommand(
        action_type="repair_item",
        actor_id="player",
        target_id="target",
        parameters=values,
        original_text="维修目标",
        authority="player",
    )


def _repair_output(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "schemaVersion": 1,
        "itemId": "target",
        "repairLevel": "standard",
        "materialKinds": ["material"],
        "abilityId": "mechanical_repair",
        "difficultyBand": "routine",
        "physicalBasis": ["可观察的破损结构和维修材料"],
        "confidence": 0.9,
    }
    result.update(overrides)
    return result


def _wear_output(item_id: str = "repair_tool") -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "itemId": item_id,
        "trigger": "repair_tool_use",
        "wearBand": "light",
        "estimatedLossRatio": 0.01,
        "abilityId": "mechanical_repair",
        "difficultyBand": "routine",
        "physicalBasis": ["可观察的维修接触"],
        "confidence": 0.9,
    }


def test_success_repair_projects_recovery_and_tool_wear() -> None:
    state, _ = _state()
    adapter = _Adapter(_repair_output(), _wear_output())
    result = resolve_maintenance_command(state, _command(), adapter=adapter, rng=lambda: 12)

    assert result.outcome == "repaired"
    assert [event.event_type for event in result.events] == [
        "item.repair_attempted",
        "item.repaired",
        "item.wear_applied",
        "item.consumed",
    ]
    assert adapter.calls == ["repair", "wear"]

    target = state.items["target"]
    material = state.items["material"]
    tool = state.items["repair_tool"]
    location = Event("loc", "location.created", "system", 0, {"locationId": "room", "name": "房间", "exits": []})
    bag = Event("bag", "container.created", "system", 0, {"containerId": "bag", "kind": "inventory", "ownerCharacterId": "player"})
    created = [
        Event("target_create", "item.created", "system", 0, {"item": target.to_payload()}, 4),
        Event("material_create", "item.created", "system", 0, {"item": material.to_payload()}, 4),
        Event("tool_create", "item.created", "system", 0, {"item": tool.to_payload()}, 4),
    ]
    projected = replay("maintenance", [location, bag, *created, *result.events], len(result.events) + 5)
    assert projected.items["target"].durability == {"current": 65.0, "max": 100.0}
    assert projected.items["repair_tool"].durability["current"] < 100.0
    assert "material" not in projected.items


def test_failed_check_keeps_target_and_material_but_can_wear_tool() -> None:
    state, _ = _state()
    result = resolve_maintenance_command(
        state,
        _command(),
        adapter=_Adapter(_repair_output(), _wear_output()),
        rng=lambda: 1,
    )
    assert result.outcome == "failed_check"
    assert [event.event_type for event in result.events] == [
        "item.repair_attempted",
        "item.wear_applied",
    ]


def test_missing_material_and_non_durable_target_are_rejected_without_events() -> None:
    state, _ = _state()
    missing = resolve_maintenance_command(
        state,
        _command(materialItemIds=[]),
        adapter=None,
    )
    assert missing.outcome == "missing_material"
    assert missing.events == []

    state.items["target"] = _item("target", category="food", durability=None)
    not_durable = resolve_maintenance_command(state, _command(), adapter=None)
    assert not_durable.outcome == "not_durable"
    assert not_durable.events == []


def test_missing_hands_block_before_roll_or_events() -> None:
    state, _ = _state()
    state.character_external_injuries["player"] = {
        "injury_left_hand": {
            "bodyPart": "left_hand",
            "status": "missing",
            "functionalEffects": {"gripAllowed": False},
        },
        "injury_right_hand": {
            "bodyPart": "right_hand",
            "status": "missing",
            "functionalEffects": {"gripAllowed": False},
        },
    }
    calls = 0

    def unexpected_roll() -> int:
        nonlocal calls
        calls += 1
        return 20

    result = resolve_maintenance_command(state, _command(), adapter=None, rng=unexpected_roll)
    assert result.outcome == "body_part_unavailable"
    assert calls == 0
    assert result.events == []
