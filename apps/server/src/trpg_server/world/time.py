"""Time-related cross-domain materialization helpers.

Only the small, deterministic clothing-use rule lives here for now.  The
function scans a proposed event list on a private projection copy and emits
wear events after each elapsed interval.  It never writes storage or mutates
the caller's state.
"""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy

from trpg_server.core.projection import apply_event
from trpg_server.core.state import Event, Projection
from trpg_server.items.durability import durability_kind_for_item
from trpg_server.items.wear import resolve_clothing_daily_wear
from trpg_server.items.wear_events import build_clothing_wear_event


def materialize_clothing_wear_events(
    state: Projection,
    events: Iterable[Event],
) -> tuple[Event, ...]:
    """Return missing clothing daily-wear events for proposed time advances.

    The clothing bindings present immediately before a ``time.advanced``
    event are the only ones eligible for that interval.  This naturally
    handles equip/unequip events interleaved with multiple time advances and
    prevents storage or idle clothing from wearing down.
    """

    proposed = tuple(events)
    known_pairs = {
        (str(event.payload.get("sourceEventId")), str(event.payload.get("itemId")))
        for event in proposed
        if event.event_type == "item.wear_applied"
        and event.payload.get("mode") == "clothing_daily"
    }
    working = deepcopy(state)
    generated: list[Event] = []
    for event in proposed:
        if event.event_type == "time.advanced":
            before_time = working.world_time
            equipped_before = _equipped_clothing(working)
        else:
            before_time = working.world_time
            equipped_before = {}
        apply_event(working, event)
        if event.event_type != "time.advanced":
            continue
        payload = event.payload
        start = payload.get("from", before_time)
        end = payload.get("to")
        if type(start) is not int or type(end) is not int or end < start:
            raise ValueError("time.advanced has an invalid interval")
        # A time event may carry a from value that differs from the private
        # projection's clock in legacy data.  Use the event's explicit span,
        # but never manufacture negative wearing time.
        elapsed_minutes = end - start
        if elapsed_minutes <= 0:
            continue
        for item_id, equipped_at in sorted(equipped_before.items()):
            if (event.event_id, item_id) in known_pairs:
                continue
            item = working.items.get(item_id)
            if item is None or item.durability is None:
                continue
            if durability_kind_for_item(item.category, item.properties) != "clothing":
                continue
            interval_start = max(start, equipped_at)
            minutes = max(0, end - interval_start)
            if minutes <= 0:
                continue
            resolution = resolve_clothing_daily_wear(
                current=float(item.durability["current"]),
                maximum=float(item.durability["max"]),
                worn_hours=minutes / 60.0,
            )
            if resolution.loss <= 0:
                continue
            wear = build_clothing_wear_event(
                actor_id="system",
                world_time=event.world_time,
                item_id=item_id,
                source_event_id=event.event_id,
                resolution=resolution,
            )
            generated.append(wear)
            known_pairs.add((event.event_id, item_id))
            # Apply immediately so consecutive time events use the reduced
            # current value rather than calculating from the stale snapshot.
            apply_event(working, wear)
    return tuple(generated)


def materialize_clothing_wear_event_plan(
    state: Projection,
    events: Iterable[Event],
) -> tuple[Event, ...]:
    """Return the original plan with daily-wear events inserted causally.

    ``materialize_clothing_wear_events`` remains useful to callers that only
    need the generated records.  Commit pipelines should use this variant so
    each wear event is immediately after the ``time.advanced`` event it
    references; this matters when one turn contains multiple waits.
    """

    proposed = tuple(events)
    working = deepcopy(state)
    merged: list[Event] = []
    known_pairs = {
        (str(event.payload.get("sourceEventId")), str(event.payload.get("itemId")))
        for event in proposed
        if event.event_type == "item.wear_applied"
        and event.payload.get("mode") == "clothing_daily"
    }
    for event in proposed:
        merged.append(event)
        if event.event_type != "time.advanced":
            apply_event(working, event)
            continue
        before_time = working.world_time
        equipped_before = _equipped_clothing(working)
        apply_event(working, event)
        # Read the interval after applying the clock event, but fall back to
        # the pre-event clock rather than the new end time when legacy events
        # omit ``from``.
        start = event.payload.get("from", before_time)
        end = event.payload.get("to", event.world_time)
        if type(start) is not int or type(end) is not int or end < start:
            raise ValueError("time.advanced has an invalid interval")
        for item_id, equipped_at in sorted(equipped_before.items()):
            if (event.event_id, item_id) in known_pairs:
                continue
            item = working.items.get(item_id)
            if item is None or item.durability is None:
                continue
            if durability_kind_for_item(item.category, item.properties) != "clothing":
                continue
            minutes = max(0, end - max(start, equipped_at))
            if minutes <= 0:
                continue
            resolution = resolve_clothing_daily_wear(
                current=float(item.durability["current"]),
                maximum=float(item.durability["max"]),
                worn_hours=minutes / 60.0,
            )
            if resolution.loss <= 0:
                continue
            wear = build_clothing_wear_event(
                actor_id="system",
                world_time=event.world_time,
                item_id=item_id,
                source_event_id=event.event_id,
                resolution=resolution,
            )
            merged.append(wear)
            known_pairs.add((event.event_id, item_id))
            apply_event(working, wear)
    return tuple(merged)


def _equipped_clothing(state: Projection) -> dict[str, int]:
    result: dict[str, int] = {}
    for bindings in state.character_equipment.values():
        for binding in bindings.values():
            if binding.get("mode") != "worn":
                continue
            item_id = binding.get("itemId")
            if not isinstance(item_id, str) or not item_id:
                continue
            equipped_at = binding.get("equippedAt", state.world_time)
            if type(equipped_at) is not int:
                equipped_at = state.world_time
            result[item_id] = min(result.get(item_id, equipped_at), equipped_at)
    return result


__all__ = [
    "materialize_clothing_wear_event_plan",
    "materialize_clothing_wear_events",
]
