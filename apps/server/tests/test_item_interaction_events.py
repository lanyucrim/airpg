from __future__ import annotations

from copy import deepcopy

import pytest

from trpg_server.core.projection import replay
from trpg_server.core.projection_handlers import projection_handlers
from trpg_server.core.state import Event
from trpg_server.items.interaction import INTERACTION_SCHEMA_VERSION
from trpg_server.items.models import ItemInstance
from trpg_server.items.provenance import build_confirmed_item_creation_events


def _location_event(location_id: str = "room_main") -> Event:
    return Event(
        event_id=f"evt_location_{location_id}",
        event_type="location.created",
        actor_id="system",
        world_time=0,
        payload={
            "locationId": location_id,
            "name": "主房间",
            "kind": "room",
            "parentId": "building",
            "exits": [],
        },
    )


def _item(item_id: str = "item_knife") -> ItemInstance:
    return ItemInstance(
        item_id=item_id,
        definition_id="knife",
        name="小刀",
        description="一把有细长金属刃的旧小刀。",
        category="tool",
        is_plot_item=False,
        quantity=1,
        stackable=False,
        unit_weight_grams=100,
        value_crown=20,
        condition="worn",
        durability={"current": 70.0, "max": 100.0},
        container_id=None,
        location_id="room_main",
        properties={},
    )


def _interaction_event(
    *,
    interaction_id: str = "interaction_1",
    status: str = "succeeded",
    source_item_ids: list[str] | None = None,
    auto_picked_item_ids: list[str] | None = None,
    target_kind: str = "location",
    target_id: str = "room_main",
    operation: str = "apply",
) -> Event:
    if operation in {"store", "retrieve"}:
        check_status = "not_required"
        check_code = "succeeded"
        ability_id = "none"
        level = "none"
        source_status = "system"
        difficulty_band = "trivial"
        dc = roll = total = margin = None
        modifier = 0
    elif status in {"failed_check", "failed"}:
        check_status = "failed"
        check_code = "failed_check"
        ability_id = "fine_handwork"
        level = "competent"
        source_status = "canon"
        difficulty_band = "routine"
        dc = 11
        modifier = 2
        roll = 1
        total = roll + 2
        margin = total - 11
    elif status in {"rejected_precondition", "blocked", "rejected"}:
        check_status = "blocked"
        check_code = "insufficient_hands"
        ability_id = "fine_handwork"
        level = "competent"
        source_status = "canon"
        difficulty_band = "routine"
        dc = 11
        modifier = 2
        roll = total = margin = None
    else:
        check_status = "succeeded"
        check_code = "succeeded"
        ability_id = "fine_handwork"
        level = "competent"
        source_status = "canon"
        difficulty_band = "routine"
        dc = 11
        modifier = 2
        roll = 12
        total = roll + 2
        margin = total - 11
    return Event(
        event_id=f"evt_{interaction_id}",
        event_type="item.interaction_resolved",
        actor_id="player",
        world_time=5,
        payload={
            "interactionId": interaction_id,
            "actorId": "player",
            "operation": operation,
            "sourceItemIds": source_item_ids or ["item_knife"],
            "targetKind": target_kind,
            "targetId": target_id,
            "status": status,
            "check": {
                "status": check_status,
                "code": check_code,
                "abilityId": ability_id,
                "level": level,
                "sourceStatus": source_status,
                "difficultyBand": difficulty_band,
                "dc": dc,
                "modifier": modifier,
                "roll": roll,
                "total": total,
                "margin": margin,
            },
            "autoPickedItemIds": auto_picked_item_ids or [],
            "sourceText": "用小刀处理房间里的旧锁扣",
        },
        schema_version=INTERACTION_SCHEMA_VERSION,
    )


def _effect_event(
    *,
    effect_id: str = "effect_1",
    source_interaction_id: str = "interaction_1",
    item_id: str = "item_knife",
    location_id: str = "room_main",
) -> Event:
    return Event(
        event_id=f"evt_{effect_id}",
        event_type="location.item_effect_applied",
        actor_id="player",
        world_time=5,
        payload={
            "effectId": effect_id,
            "locationId": location_id,
            "itemId": item_id,
            "effectKind": "lock_examined",
            "summary": "记录了锁扣的可观察状态。",
            "sourceInteractionId": source_interaction_id,
        },
        schema_version=1,
    )


def _chain() -> list[Event]:
    source, created = build_confirmed_item_creation_events(
        actor_id="system",
        world_time=1,
        item=_item(),
        source_kind="location_take",
        source_event_id=None,
        definition_status="generated_daily",
    )
    return [_location_event(), source, created, _interaction_event(), _effect_event()]


def test_interaction_handlers_are_registered_with_core_projection() -> None:
    # Importing core.projection is the production registration boundary.
    assert projection_handlers.handler_for("item.source_confirmed") is not None
    assert projection_handlers.handler_for("item.interaction_resolved") is not None
    assert projection_handlers.handler_for("location.item_effect_applied") is not None


def test_source_interaction_and_location_effect_replay_as_one_chain() -> None:
    events = _chain()
    state = replay("cmp_item_interactions", events, len(events))
    replayed = replay("cmp_item_interactions", deepcopy(events), len(events))

    assert state.item_source_confirmations["item_knife"]["sourceKind"] == "location_take"
    assert state.item_source_confirmations["item_knife"]["confirmationEventId"] == events[1].event_id
    assert state.item_interactions["interaction_1"]["status"] == "succeeded"
    assert state.item_interactions["interaction_1"]["sourceEventId"] == events[3].event_id
    assert state.location_item_effects["effect_1"]["sourceInteractionId"] == "interaction_1"
    assert state.location_item_effects["effect_1"]["sourceEventId"] == events[4].event_id
    assert replayed.item_source_confirmations == state.item_source_confirmations
    assert replayed.item_interactions == state.item_interactions
    assert replayed.location_item_effects == state.location_item_effects


def test_duplicate_source_confirmation_is_rejected() -> None:
    events = _chain()[:2]
    with pytest.raises(ValueError, match="already has a source confirmation"):
        replay("cmp_duplicate_source", [*events, events[1]], len(events) + 1)


def test_source_confirmation_cannot_reference_itself() -> None:
    source, _ = build_confirmed_item_creation_events(
        actor_id="system",
        world_time=1,
        item=_item("item_self_ref"),
        source_kind="location_take",
        source_event_id=None,
        definition_status="generated_daily",
    )
    payload = dict(source.payload)
    payload["sourceEventId"] = source.event_id
    self_referencing = Event(
        event_id=source.event_id,
        event_type=source.event_type,
        actor_id=source.actor_id,
        world_time=source.world_time,
        payload=payload,
        schema_version=source.schema_version,
    )
    with pytest.raises(ValueError, match="earlier confirmed event"):
        replay("cmp_self_ref_source", [self_referencing], 1)


def test_duplicate_interaction_id_is_rejected() -> None:
    events = _chain()[:4]
    duplicate = _interaction_event(
        interaction_id="interaction_1",
        source_item_ids=["item_knife"],
    )
    with pytest.raises(ValueError, match="interaction has already been projected"):
        replay("cmp_duplicate_interaction", [*events, duplicate], len(events) + 1)


def test_duplicate_location_effect_id_is_rejected() -> None:
    events = _chain()
    duplicate = _effect_event(effect_id="effect_1")
    with pytest.raises(ValueError, match="effect has already been projected"):
        replay("cmp_duplicate_effect", [*events, duplicate], len(events) + 1)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda p: p.update(operation="combine", targetKind="location"), "combine interactions"),
        (lambda p: p.update(sourceItemIds=["item_knife", "item_knife"]), "must not contain duplicates"),
        (lambda p: p.update(autoPickedItemIds=["item_missing"]), "subset"),
        (lambda p: p.update(status="not_a_status"), "status is not supported"),
    ],
)
def test_interaction_payload_is_strictly_validated(mutator, message: str) -> None:
    events = _chain()[:3]
    event = _interaction_event()
    payload = dict(event.payload)
    mutator(payload)
    invalid = Event(
        event_id="evt_invalid_interaction",
        event_type=event.event_type,
        actor_id=event.actor_id,
        world_time=event.world_time,
        payload=payload,
        schema_version=event.schema_version,
    )
    with pytest.raises(ValueError, match=message):
        replay("cmp_invalid_interaction", [*events, invalid], len(events) + 1)


def test_interaction_payload_actor_must_match_event_actor() -> None:
    events = _chain()[:3]
    event = _interaction_event()
    invalid = Event(
        event_id=event.event_id,
        event_type=event.event_type,
        actor_id="another_actor",
        world_time=event.world_time,
        payload=event.payload,
        schema_version=event.schema_version,
    )
    with pytest.raises(ValueError, match="match the event actor"):
        replay("cmp_actor_mismatch", [*events, invalid], len(events) + 1)


def test_interaction_cannot_reference_unknown_source_item() -> None:
    events = _chain()[:3]
    event = _interaction_event(source_item_ids=["missing_item"])

    with pytest.raises(ValueError, match="unknown source item"):
        replay("cmp_unknown_interaction_source", [*events, event], len(events) + 1)


def _invalid_interaction_replay(mutator, *, status: str = "succeeded") -> None:  # type: ignore[no-untyped-def]
    """Replay one malformed audit after a valid item source chain."""

    events = _chain()[:3]
    event = _interaction_event(status=status)
    payload = deepcopy(event.payload)
    mutator(payload)
    invalid = Event(
        event_id="evt_invalid_check_audit",
        event_type=event.event_type,
        actor_id=event.actor_id,
        world_time=event.world_time,
        payload=payload,
        schema_version=event.schema_version,
    )
    replay("cmp_invalid_check_audit", [*events, invalid], len(events) + 1)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda p: p["check"].update(
                status="failed", code="failed_check", roll=1, total=3, margin=-8
            ),
            "succeeded interaction",
        ),
        (
            lambda p: p["check"].update(
                status="succeeded", code="failed_check"
            ),
            "succeeded check code",
        ),
        (
            lambda p: p["check"].update(total=15),
            "totals do not match",
        ),
        (
            lambda p: p["check"].update(dc=12),
            "check.dc",
        ),
        (
            lambda p: p["check"].update(modifier=6),
            "check.modifier",
        ),
        (
            lambda p: p["check"].update(roll=21),
            "check.roll",
        ),
        (
            lambda p: p["check"].update(modifier=True),
            "check.modifier",
        ),
        (
            lambda p: p["check"].update(untrusted="forged"),
            "unknown",
        ),
    ],
)
def test_interaction_check_audit_is_strictly_validated(mutator, message: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match=message):
        _invalid_interaction_replay(mutator)


def test_forged_succeeded_audit_cannot_enable_location_effect() -> None:
    """A failed/malformed check is rejected before the effect event is read."""

    events = _chain()[:3]
    interaction = _interaction_event()
    payload = deepcopy(interaction.payload)
    payload["check"].update(status="failed", code="failed_check", roll=1, total=3, margin=-8)
    forged = Event(
        event_id=interaction.event_id,
        event_type=interaction.event_type,
        actor_id=interaction.actor_id,
        world_time=interaction.world_time,
        payload=payload,
        schema_version=interaction.schema_version,
    )
    with pytest.raises(ValueError, match="succeeded interaction"):
        replay(
            "cmp_forged_succeeded_effect",
            [*events, forged, _effect_event()],
            len(events) + 2,
        )


def test_transfer_interaction_accepts_only_the_no_check_system_sentinel() -> None:
    events = _chain()[:3]
    valid = _interaction_event(
        interaction_id="transfer_1",
        operation="store",
        target_kind="furniture",
        target_id="furniture_counter",
    )
    state = replay("cmp_valid_transfer_audit", [*events, valid], len(events) + 1)
    assert state.item_interactions["transfer_1"]["check"]["status"] == "not_required"

    invalid_payload = deepcopy(valid.payload)
    invalid_payload["check"]["abilityId"] = "fine_handwork"
    invalid = Event(
        event_id="evt_invalid_transfer_audit",
        event_type=valid.event_type,
        actor_id=valid.actor_id,
        world_time=valid.world_time,
        payload=invalid_payload,
        schema_version=valid.schema_version,
    )
    with pytest.raises(ValueError, match="system sentinel"):
        replay("cmp_invalid_transfer_audit", [*events, invalid], len(events) + 1)


def test_location_effect_requires_successful_matching_interaction_source() -> None:
    events = _chain()[:3]
    failed = _interaction_event(status="failed_check")
    effect = _effect_event()
    with pytest.raises(ValueError, match="succeeded interaction"):
        replay("cmp_failed_effect", [*events, failed, effect], len(events) + 2)

    succeeded = _interaction_event()
    mismatched = _effect_event(item_id="item_other")
    with pytest.raises(ValueError, match="interaction source item"):
        replay("cmp_mismatched_effect", [*events, succeeded, mismatched], len(events) + 2)

    transfer = _interaction_event(
        interaction_id="store_1",
        operation="store",
        target_kind="furniture",
        target_id="furniture_counter",
    )
    with pytest.raises(ValueError, match="apply interaction"):
        replay(
            "cmp_transfer_effect",
            [*events, transfer, _effect_event(source_interaction_id="store_1")],
            len(events) + 2,
        )


def test_generated_daily_instance_requires_acquisition_confirmation() -> None:
    daily = _item("daily_food_bread_1")
    daily.definition_id = "daily_food_bread"
    daily.name = "面包"
    location = _location_event()
    # A bare item.created event is rejected even though the 15-field record is
    # otherwise valid; the source confirmation must be immediately earlier.
    from trpg_server.items.commands import build_item_created_event

    created = build_item_created_event(actor_id="system", world_time=1, item=daily)
    with pytest.raises(ValueError, match="confirmed acquisition source"):
        replay("cmp_daily_without_source", [location, created], 2)

    source, confirmed_created = build_confirmed_item_creation_events(
        actor_id="system",
        world_time=1,
        item=daily,
        source_kind="location_take",
        definition_status="generated_daily",
    )
    state = replay("cmp_daily_with_source", [location, source, confirmed_created], 3)
    assert state.items[daily.item_id].definition_id == "daily_food_bread"
