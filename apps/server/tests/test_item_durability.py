from __future__ import annotations

import json

import httpx
import pytest

from trpg_server.ai.platform.deepseek import DeepSeekSettings
from trpg_server.core.state import Event, Projection
from trpg_server.items.ai_items.deepseek_adapter import (
    DeepSeekInitialDurabilityAdapter,
)
from trpg_server.items.ai_items.durability import (
    InitialDurabilityAdapterResult,
    InitialDurabilityRequest,
    resolve_initial_durability,
)
from trpg_server.items.commands import (
    build_item_condition_changed_event,
    build_item_created_event,
)
from trpg_server.items.durability import (
    DurabilityError,
    profile_from_creation_ratios,
    validate_item_durability,
)
from trpg_server.items.events import (
    apply_item_condition_changed,
    apply_item_created,
)
from trpg_server.items.models import ItemInstance


class FakeDurabilityAdapter:
    available = True

    def __init__(self, output: dict[str, object]) -> None:
        self.output = output
        self.calls = 0

    def assess(
        self,
        request: InitialDurabilityRequest,
    ) -> InitialDurabilityAdapterResult:
        del request
        self.calls += 1
        return InitialDurabilityAdapterResult(output=self.output)


def _candidate(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schemaVersion": 1,
        "durabilityKind": "tool",
        "condition": "new",
        "conditionGrade": "new",
        "relativeMaximum": 1.0,
        "remainingRatio": 1.0,
        "confidence": 0.9,
        "basis": ["描述明确写明崭新小刀"],
    }
    value.update(overrides)
    return value


def _item(*, durability: dict[str, float] | None) -> ItemInstance:
    return ItemInstance(
        item_id="knife_1",
        definition_id="small_knife",
        name="崭新的小刀",
        description="一把刚出厂、没有磨损的小刀。",
        category="tool",
        is_plot_item=False,
        quantity=1,
        stackable=False,
        unit_weight_grams=120,
        value_crown=80,
        condition="new",
        durability=durability,
        container_id=None,
        location_id="workbench",
        properties={"equipment": {"mode": "held", "slotIds": ["left_hand", "right_hand"], "handCount": 1}},
    )


def test_new_small_knife_is_the_float_100_calibration() -> None:
    profile = profile_from_creation_ratios(
        kind="tool",
        condition="new",
        condition_grade="new",
        relative_maximum=1.0,
        remaining_ratio=1.0,
    )

    assert profile.to_mapping() == {"current": 100.0, "max": 100.0}
    assert all(type(value) is float for value in profile.to_mapping().values())


def test_condition_and_category_caps_bound_model_ratios() -> None:
    rusted = profile_from_creation_ratios(
        kind="tool",
        condition="rusted",
        condition_grade="worn",
        relative_maximum=1.0,
        remaining_ratio=0.55,
    )
    assert rusted.to_mapping() == {"current": 55.0, "max": 100.0}

    with pytest.raises(DurabilityError, match="between"):
        profile_from_creation_ratios(
            kind="clothing",
            condition="new",
            condition_grade="new",
            relative_maximum=1.2,
            remaining_ratio=1.0,
        )
    with pytest.raises(DurabilityError, match="outside"):
        profile_from_creation_ratios(
            kind="tool",
            condition="rusted",
            condition_grade="worn",
            relative_maximum=1.0,
            remaining_ratio=0.9,
        )


def test_only_non_consumable_tools_clothing_and_equipment_use_durability() -> None:
    assert validate_item_durability(
        category="tool",
        properties={},
        durability={"current": 80, "max": 100},
        require_for_eligible=True,
    ) == {"current": 80.0, "max": 100.0}

    with pytest.raises(DurabilityError, match="only allowed"):
        validate_item_durability(
            category="food",
            properties={},
            durability={"current": 2.0, "max": 3.0},
        )
    with pytest.raises(DurabilityError, match="only allowed"):
        validate_item_durability(
            category="tool",
            properties={"consumable": {}},
            durability={"current": 2.0, "max": 3.0},
        )


def test_new_creation_requires_initial_durability_but_schema_three_replays() -> None:
    with pytest.raises(DurabilityError, match="require initial durability"):
        build_item_created_event(actor_id="system", world_time=0, item=_item(durability=None))

    item = _item(durability={"current": 100.0, "max": 100.0})
    created = build_item_created_event(actor_id="system", world_time=0, item=item)
    assert created.schema_version == 4

    state = Projection(campaign_id="cmp_durability")
    state.locations["workbench"] = object()  # type: ignore[assignment]
    legacy = Event(
        "evt_legacy_knife",
        "item.created",
        "system",
        0,
        {"item": _item(durability=None).to_payload()},
        schema_version=3,
    )
    apply_item_created(state, legacy)
    assert state.items["knife_1"].durability is None


def test_new_condition_event_cannot_change_durability() -> None:
    state = Projection(campaign_id="cmp_condition")
    state.locations["workbench"] = object()  # type: ignore[assignment]
    created = build_item_created_event(
        actor_id="system",
        world_time=0,
        item=_item(durability={"current": 100.0, "max": 100.0}),
    )
    apply_item_created(state, created)
    changed = build_item_condition_changed_event(
        actor_id="system", world_time=1, item_id="knife_1", condition="worn"
    )
    apply_item_condition_changed(state, changed)

    assert changed.schema_version == 4
    assert state.items["knife_1"].condition == "worn"
    assert state.items["knife_1"].durability == {"current": 100.0, "max": 100.0}

    with pytest.raises(ValueError, match="unknown"):
        apply_item_condition_changed(
            state,
            Event(
                "evt_illegal_wear",
                "item.condition_changed",
                "system",
                2,
                {"itemId": "knife_1", "condition": "worn", "durability": {"current": 90.0, "max": 100.0}},
                schema_version=4,
            ),
        )


def test_resolver_skips_consumables_and_locked_non_durable_categories() -> None:
    adapter = FakeDurabilityAdapter(_candidate())

    food = resolve_initial_durability(
        InitialDurabilityRequest("面包", "一块面包", "food", {}), adapter
    )
    consumable_tool = resolve_initial_durability(
        InitialDurabilityRequest(
            "一次性刀片",
            "使用一次后丢弃的刀片",
            "tool",
            {"consumable": {}},
        ),
        adapter,
    )

    assert food.status == consumable_tool.status == "not_applicable"
    assert adapter.calls == 0


def test_resolver_accepts_bounded_candidate_and_rejects_reclassification() -> None:
    request = InitialDurabilityRequest(
        "生锈的小刀", "刀身已有明显锈迹。", "tool", {}
    )
    accepted = resolve_initial_durability(
        request,
        FakeDurabilityAdapter(
            _candidate(
                condition="rusted",
                conditionGrade="worn",
                remainingRatio=0.55,
                basis=["名称和描述均明确写明生锈"],
            )
        ),
    )
    rejected = resolve_initial_durability(
        request,
        FakeDurabilityAdapter(
            _candidate(
                durabilityKind="clothing",
                relativeMaximum=0.8,
            )
        ),
    )

    assert accepted.status == "model_accepted"
    assert accepted.condition == "rusted"
    assert accepted.durability == {"current": 55.0, "max": 100.0}
    assert rejected.status == "rejected"
    assert "locked item category" in str(rejected.reason)


def test_unlocked_description_can_return_a_non_durable_classification() -> None:
    resolution = resolve_initial_durability(
        InitialDurabilityRequest(
            "记事本", "一本普通纸质记事本。", None, {}, category_locked=False
        ),
        FakeDurabilityAdapter(
            _candidate(
                durabilityKind="none",
                condition=None,
                conditionGrade=None,
                relativeMaximum=None,
                remainingRatio=None,
                basis=["纸质日用品不属于耐久类别"],
            )
        ),
    )

    assert resolution.status == "not_applicable"
    assert resolution.durability_kind == "none"
    assert resolution.durability is None


def test_deepseek_adapter_requests_ratios_without_final_values() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(_candidate())},
                    }
                ]
            },
        )

    adapter = DeepSeekInitialDurabilityAdapter(
        DeepSeekSettings(api_key="durability-test-key", max_attempts=1),
        transport=httpx.MockTransport(handler),
    )
    result = adapter.assess(
        InitialDurabilityRequest(
            "崭新的小刀", "一把刚出厂、没有磨损的小刀。", "tool", {}
        )
    )

    assert result.output["relativeMaximum"] == 1.0
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] <= 450
    assert body["temperature"] == 0.1
    prompt = json.dumps(body, ensure_ascii=False)
    assert "100.0/100.0" in prompt
    assert "current/max" in prompt
    assert "durability-test-key" not in prompt
