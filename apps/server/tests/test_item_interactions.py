from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy

import pytest

from trpg_server.behavior.item_interactions import resolve_item_interaction
from trpg_server.behavior.item_interactions import _location_is_current
from trpg_server.behavior.router import interpret_player_text
from trpg_server.core.projection import replay
from trpg_server.core.state import ParsedCommand
from trpg_server.items.interaction import (
    DisabledItemInteractionAdapter,
    InteractionRequest,
)
from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events


@dataclass
class FakeInteractionAdapter:
    output: dict[str, object]
    available: bool = True
    provider_name: str = "fake"
    model_name: str = "fake-interaction"
    calls: int = 0

    def assess(self, request, source_summaries, target_summary):  # type: ignore[no-untyped-def]
        self.calls += 1
        return self.output


def _state():
    return replay(GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events(), 1)


def _command(operation: str = "apply", *, target_kind: str = "location") -> ParsedCommand:
    target_id = "white_heron_ground_floor"
    action = "用小刀处理这里"
    if operation == "store":
        target_id = "furniture_loc_5_1_1__1_1"
        action = "把小刀放进柜台"
    return ParsedCommand(
        action_type="item_interaction" if operation == "apply" else f"{operation}_item",
        actor_id="protagonist",
        target_id=target_id,
        parameters={
            "itemIds": ["protagonist_small_knife"],
            "targetKind": target_kind,
            "targetId": target_id,
            "operation": operation,
        },
        original_text=action,
        authority="player",
    )


def _candidate(*, difficulty: str = "trivial") -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "decision": "possible",
        "operation": "apply",
        "requiredAbilityIds": [],
        "toolFit": "strong",
        "difficultyBand": difficulty,
        "physicalBasis": ["小刀的细长金属刃能够接触当前目标"],
        "missingFacts": [],
        "riskHints": [],
        "confidence": 0.9,
        "effectKind": "observed_contact",
        "rejectionReason": None,
    }


def test_success_records_check_effect_and_auto_pick_feedback() -> None:
    state = _state()
    state.items["protagonist_small_knife"].container_id = "protagonist_inventory"
    adapter = FakeInteractionAdapter(_candidate())

    result = resolve_item_interaction(
        state,
        _command(),
        adapter=adapter,
        rng=lambda: 20,
    )

    assert result.status == "committed"
    assert result.outcome == "succeeded"
    assert "行动中从背包取用并拿起" in result.narrative
    assert [event.event_type for event in result.events] == [
        "item.interaction_resolved",
        "location.item_effect_applied",
        "time.advanced",
    ]
    assert adapter.calls == 1


def test_failed_check_advances_time_without_consuming_item() -> None:
    state = _state()
    adapter = FakeInteractionAdapter(_candidate(difficulty="extreme"))

    result = resolve_item_interaction(state, _command(), adapter=adapter, rng=lambda: 1)

    assert result.outcome == "failed_check"
    assert [event.event_type for event in result.events] == [
        "item.interaction_resolved",
        "time.advanced",
    ]
    assert result.events[-1].payload["minutes"] == 1
    assert state.items["protagonist_small_knife"].quantity == 1


def test_missing_hand_blocks_before_d20() -> None:
    state = _state()
    state.character_external_injuries["protagonist"] = {
        "injury_right_hand": {
            "bodyPart": "right_hand",
            "status": "missing",
            "functionalEffects": {
                "gripAllowed": False,
                "movementAllowed": True,
                "wearAllowed": False,
            },
        },
        "injury_left_hand": {
            "bodyPart": "left_hand",
            "status": "missing",
            "functionalEffects": {
                "gripAllowed": False,
                "movementAllowed": True,
                "wearAllowed": False,
            },
        },
    }
    adapter = FakeInteractionAdapter(_candidate())

    result = resolve_item_interaction(state, _command(), adapter=adapter, rng=lambda: 20)

    assert result.outcome == "rejected_precondition"
    assert result.events[0].payload["status"] == "rejected_precondition"
    assert len(result.events) == 1


def test_furniture_store_is_deterministic_and_does_not_call_ai() -> None:
    state = _state()
    state.character_equipment["protagonist"] = {}
    state.items["protagonist_small_knife"].unit_weight_grams = 100
    state.items["protagonist_small_knife"].properties["volumeCm3"] = 100
    adapter = FakeInteractionAdapter(_candidate())

    result = resolve_item_interaction(
        state,
        _command("store", target_kind="furniture"),
        adapter=adapter,
    )

    assert result.outcome == "succeeded"
    assert [event.event_type for event in result.events] == [
        "item.interaction_resolved",
        "item.transferred",
        "time.advanced",
    ]
    assert adapter.calls == 0


def test_cached_recipe_skips_disabled_adapter(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    state = _state()
    second = deepcopy(state.items["protagonist_small_knife"])
    second.item_id = "protagonist_small_knife_spare"
    state.items[second.item_id] = second
    command = ParsedCommand(
        action_type="combine_items",
        actor_id="protagonist",
        target_id="protagonist_small_knife",
        parameters={
            "itemIds": ["protagonist_small_knife", "protagonist_small_knife_spare"],
            "targetKind": "item",
            "targetId": "protagonist_small_knife",
            "operation": "combine",
        },
        original_text="组合小刀",
        authority="player",
    )
    # The real recipe resolver remains an injected boundary in this test.
    monkeypatch.setattr(
        "trpg_server.behavior.item_interactions._has_cached_recipe",
        lambda request, source_items: True,
    )
    result = resolve_item_interaction(
        state,
        command,
        adapter=DisabledItemInteractionAdapter(),
        rng=lambda: 20,
        recipe_plan_resolver=lambda *args: (),
    )

    assert result.outcome == "succeeded"
    assert [event.event_type for event in result.events] == [
        "item.interaction_resolved",
        "time.advanced",
    ]


def test_local_parser_marks_item_interaction_as_authoritative() -> None:
    state = _state()
    command = interpret_player_text("用小刀处理这里", "protagonist", state=state)
    assert command.action_type == "item_interaction"
    assert command.parameters["operation"] == "apply"


def test_generic_use_item_text_keeps_the_existing_use_route() -> None:
    state = _state()

    command = interpret_player_text("使用小刀", "protagonist", state=state)

    assert command.action_type == "use_item"
    assert command.target_id == "protagonist_small_knife"


def test_apply_rejects_remote_item_target_before_model_call() -> None:
    state = _state()
    remote = deepcopy(state.items["white_heron_kitchen_bread"])
    remote.item_id = "remote_bread"
    remote.location_id = "loc_1_2_1"
    remote.container_id = None
    state.items[remote.item_id] = remote
    adapter = FakeInteractionAdapter(_candidate())
    command = ParsedCommand(
        action_type="item_interaction",
        actor_id="protagonist",
        target_id=remote.item_id,
        parameters={
            "itemIds": ["protagonist_small_knife"],
            "targetKind": "item",
            "targetId": remote.item_id,
            "operation": "apply",
        },
        original_text="用小刀处理远处的面包",
        authority="player",
    )

    result = resolve_item_interaction(state, command, adapter=adapter, rng=lambda: 20)

    assert result.status == "rejected"
    assert result.outcome == "rejected_precondition"
    assert adapter.calls == 0


def test_store_contract_rejects_multiple_source_items() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        InteractionRequest(
            actor_id="protagonist",
            source_item_ids=("one", "two"),
            target_kind="furniture",
            target_id="furniture_loc_5_1_1__1_1",
            operation="store",
            action_text="把两件物品一起放入柜台",
        )


def test_location_parent_cycle_is_rejected_without_looping() -> None:
    state = _state()
    # Corrupt only the in-memory topology to exercise the defensive guard;
    # malformed topology must fail closed rather than hang a player request.
    first = state.character_locations["protagonist"]
    state.locations[first].parent_id = first

    assert _location_is_current(state, "protagonist", "some-unrelated-location") is False


def test_dynamic_daily_target_requires_source_confirmation() -> None:
    state = _state()
    target = deepcopy(state.items["white_heron_kitchen_bread"])
    target.item_id = "daily_food_unconfirmed_target"
    target.definition_id = "daily_food_unconfirmed_target"
    target.container_id = "protagonist_inventory"
    target.location_id = None
    state.items[target.item_id] = target
    command = ParsedCommand(
        action_type="item_interaction",
        actor_id="protagonist",
        target_id=target.item_id,
        parameters={
            "itemIds": ["protagonist_small_knife"],
            "targetKind": "item",
            "targetId": target.item_id,
            "operation": "apply",
        },
        original_text="用小刀处理未确认的面包",
        authority="player",
    )
    adapter = FakeInteractionAdapter(_candidate())

    result = resolve_item_interaction(state, command, adapter=adapter, rng=lambda: 20)

    assert result.status == "rejected"
    assert result.outcome == "rejected_precondition"
    assert adapter.calls == 0


def test_item_target_in_invisible_furniture_is_not_accessible() -> None:
    state = _state()
    pantry = state.containers["white_heron_kitchen_pantry"]
    pantry.visible = False
    target = state.items["white_heron_kitchen_bread"]
    command = ParsedCommand(
        action_type="item_interaction",
        actor_id="protagonist",
        target_id=target.item_id,
        parameters={
            "itemIds": ["protagonist_small_knife"],
            "targetKind": "item",
            "targetId": target.item_id,
            "operation": "apply",
        },
        original_text="用小刀处理柜中的面包",
        authority="player",
    )
    adapter = FakeInteractionAdapter(_candidate())

    result = resolve_item_interaction(state, command, adapter=adapter, rng=lambda: 20)

    assert result.status == "rejected"
    assert result.outcome == "rejected_precondition"
    assert adapter.calls == 0


def test_unknown_current_location_fails_closed() -> None:
    state = _state()
    state.character_locations["protagonist"] = "missing_location"

    assert _location_is_current(state, "protagonist", "missing_location") is False
