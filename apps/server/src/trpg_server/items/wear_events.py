"""Auditable durability and repair events.

The numerical policy lives in :mod:`trpg_server.items.wear`; this module is
the only item-domain boundary that turns a resolved calculation into a state
change.  Handlers deliberately re-run the pure calculation while replaying,
so an event cannot smuggle an arbitrary ``current`` value into the projection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import math
from typing import Any
from uuid import uuid4

from trpg_server.core.projection_handlers import projection_handlers
from trpg_server.core.state import Event, Projection
from trpg_server.items.durability import durability_kind_for_item
from trpg_server.items.inventory import item_is_at_location, item_is_owned_by
from trpg_server.items.models import ItemInstance
from trpg_server.items.wear import (
    DIFFICULTY_BANDS,
    REPAIR_LEVELS,
    WEAR_BANDS,
    WearResolution,
    ClothingDailyWear,
    RepairResolution,
    resolve_behavior_wear,
    resolve_clothing_daily_wear,
    resolve_repair,
)


WEAR_EVENT_SCHEMA_VERSION = 1
REPAIR_EVENT_SCHEMA_VERSION = 1
_CHECK_STATUSES = frozenset({"succeeded", "failed"})
_CHECK_LEVELS = frozenset({"untrained", "working", "competent", "advanced", "expert"})
_CHECK_SOURCES = frozenset({"canon", "player_defined", "inferred", "unknown", "system"})
_MECHANICAL_SOURCES = frozenset({"canon", "player_defined"})
_LEVEL_MODIFIERS = {
    "untrained": -2,
    "working": 0,
    "competent": 2,
    "advanced": 4,
    "expert": 6,
}
REPAIR_SOURCE_KINDS = frozenset(
    {
        "player_repair_request",
        "npc_repair_request",
        "story_repair",
        "system_repair",
    }
)


class WearEventError(ValueError):
    """Raised when a durability event is malformed or causally invalid."""


def _event_id(prefix: str) -> str:
    return f"evt_item_{prefix}_{uuid4().hex}"


def _number(value: object, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WearEventError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise WearEventError(f"{field} must be a finite number")
    if minimum is not None and result < minimum:
        raise WearEventError(f"{field} must be at least {minimum}")
    return result


def _integer(value: object, field: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise WearEventError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise WearEventError(f"{field} is below the allowed range")
    if maximum is not None and value > maximum:
        raise WearEventError(f"{field} is above the allowed range")
    return value


def _text(value: object, field: str, *, allow_empty: bool = False, maximum: int = 240) -> str:
    if type(value) is not str or len(value) > maximum or (not allow_empty and not value.strip()):
        raise WearEventError(f"{field} must be a string")
    return value.strip()


def _string_list(value: object, field: str, *, allow_empty: bool = False, maximum: int = 12) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise WearEventError(f"{field} must be a string list")
    if not allow_empty and not value:
        raise WearEventError(f"{field} must be a non-empty string list")
    result = [_text(item, field, maximum=400) for item in value]
    if len(result) != len(set(result)):
        raise WearEventError(f"{field} must not contain duplicates")
    return result


def _instance_ids(
    state: Projection,
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    """Validate a list of references to currently existing item instances.

    Repair events are causal audit records, not free-form notes.  Resolving
    every referenced instance while projecting the source attempt prevents a
    later ``item.repaired`` event from claiming that an absent material or
    tool was used.  The references are intentionally checked at the point of
    the attempt, before successful repairs may consume materials.
    """

    result = _string_list(values, field, allow_empty=allow_empty)
    for item_id in result:
        _item(state, item_id)
    return result


def _source_id(value: object, state: Projection, event: Event) -> str:
    source = _text(value, "sourceEventId", maximum=180)
    if source == event.event_id or source not in state.confirmed_event_ids:
        raise WearEventError("event requires an earlier confirmed source event")
    source_time = _source_time(state, source)
    if source_time is not None and event.world_time < source_time:
        raise WearEventError("durability event cannot predate its source")
    return source


def _source_time(state: Projection, source_id: str) -> int | None:
    return state.event_times_by_id.get(source_id)


def _item(state: Projection, value: object) -> ItemInstance:
    item_id = _text(value, "itemId", maximum=180)
    item = state.items.get(item_id)
    if item is None:
        raise WearEventError(f"unknown item: {item_id}")
    return item


def _durable_item(item: ItemInstance) -> dict[str, float]:
    if item.durability is None or durability_kind_for_item(item.category, item.properties) is None:
        raise WearEventError("item is not a non-consumable durable item")
    current = _number(item.durability.get("current"), "item.durability.current", minimum=0)
    maximum = _number(item.durability.get("max"), "item.durability.max", minimum=0.0001)
    if current > maximum:
        raise WearEventError("item durability current exceeds max")
    return {"current": round(current, 2), "max": round(maximum, 2)}


def _same_number(left: object, right: float, field: str) -> None:
    value = _number(left, field)
    if abs(value - right) > 0.005:
        raise WearEventError(f"{field} does not match the resolved calculation")


def _check_mapping(check: object, *, status: str | None = None) -> dict[str, Any]:
    if not isinstance(check, Mapping):
        raise WearEventError("check must be an object")
    required = {
        "status", "abilityId", "level", "sourceStatus", "difficultyBand",
        "dc", "modifier", "roll", "total", "margin",
    }
    optional = {"code", "reason"}
    if not required.issubset(check) or set(check).difference(required | optional):
        raise WearEventError("check fields do not match the durability contract")
    check_status = _text(check["status"], "check.status", maximum=40)
    if check_status not in _CHECK_STATUSES:
        raise WearEventError("check.status is not supported")
    if "code" in check:
        code = _text(check["code"], "check.code", maximum=80)
        expected_code = "succeeded" if check_status == "succeeded" else "failed_check"
        if code != expected_code:
            raise WearEventError("check.code does not match status")
    if "reason" in check and (not isinstance(check["reason"], str) or len(check["reason"]) > 500):
        raise WearEventError("check.reason must be a string")
    if status is not None and check_status != status:
        raise WearEventError("check status does not match durability event")
    ability_id = _text(check["abilityId"], "check.abilityId", maximum=180)
    level = _text(check["level"], "check.level", maximum=40)
    source_status = _text(check["sourceStatus"], "check.sourceStatus", maximum=40)
    difficulty = _text(check["difficultyBand"], "check.difficultyBand", maximum=40)
    if level not in _CHECK_LEVELS or source_status not in _CHECK_SOURCES or difficulty not in DIFFICULTY_BANDS:
        raise WearEventError("check contains an unsupported enum")
    dc = _integer(check["dc"], "check.dc", minimum=1)
    expected_dc = {"trivial": 8, "routine": 11, "demanding": 14, "hard": 17, "extreme": 20}[difficulty]
    if dc != expected_dc:
        raise WearEventError("check.dc does not match difficultyBand")
    modifier = _integer(check["modifier"], "check.modifier")
    expected_modifier = _LEVEL_MODIFIERS[level] if source_status in _MECHANICAL_SOURCES else -2
    if modifier != expected_modifier:
        raise WearEventError("check.modifier does not match ability evidence")
    roll = _integer(check["roll"], "check.roll", minimum=1, maximum=20)
    total = _integer(check["total"], "check.total")
    margin = _integer(check["margin"], "check.margin")
    if total != roll + modifier or margin != total - dc:
        raise WearEventError("check totals do not match d20 + modifier and DC")
    expected_status = "succeeded" if total >= dc else "failed"
    if check_status != expected_status:
        raise WearEventError("check status does not match d20 result")
    return {
        "status": check_status,
        "abilityId": ability_id,
        "level": level,
        "sourceStatus": source_status,
        "difficultyBand": difficulty,
        "dc": dc,
        "modifier": modifier,
        "roll": roll,
        "total": total,
        "margin": margin,
    }


def _check_required_payload(payload: Mapping[str, Any], required: set[str], optional: set[str], event_type: str) -> None:
    missing = required.difference(payload)
    extra = set(payload).difference(required | optional)
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing {sorted(missing)}")
        if extra:
            parts.append(f"unknown {sorted(extra)}")
        raise WearEventError(f"{event_type} payload has " + "; ".join(parts))


def _resolution_check(resolution: WearResolution) -> dict[str, Any]:
    return {
        "status": "succeeded" if resolution.total >= resolution.dc else "failed",
        "abilityId": "unknown",
        "level": "untrained",
        "sourceStatus": "unknown",
        "difficultyBand": next(
            band for band, dc in {"trivial": 8, "routine": 11, "demanding": 14, "hard": 17, "extreme": 20}.items()
            if dc == resolution.dc
        ),
        "dc": resolution.dc,
        "modifier": resolution.modifier,
        "roll": resolution.roll,
        "total": resolution.total,
        "margin": resolution.margin,
    }


def build_item_wear_event(
    *,
    actor_id: str,
    world_time: int,
    item_id: str,
    source_event_id: str,
    trigger: str,
    resolution: WearResolution,
    ability_id: str,
    level: str,
    source_status: str,
    physical_basis: Sequence[str],
    confidence: float = 1.0,
) -> Event:
    """Build a behavior wear event from a program-resolved result."""

    if not source_event_id:
        raise ValueError("source_event_id is required")
    check = _resolution_check(resolution)
    check.update({"abilityId": ability_id, "level": level, "sourceStatus": source_status})
    event_id = _event_id("wear")
    payload = {
        "wearId": event_id,
        "itemId": item_id,
        "sourceEventId": source_event_id,
        "mode": "behavior",
        "trigger": trigger,
        "wearBand": resolution.wear_band,
        "estimatedLossRatio": resolution.estimated_loss_ratio,
        "boundedLossRatio": resolution.bounded_loss_ratio,
        "previousCurrent": resolution.previous_current,
        "max": resolution.maximum,
        "loss": resolution.loss,
        "current": resolution.current,
        "check": check,
        "physicalBasis": list(physical_basis),
        "confidence": confidence,
    }
    return Event(event_id, "item.wear_applied", actor_id, world_time, payload, WEAR_EVENT_SCHEMA_VERSION)


def build_clothing_wear_event(
    *,
    actor_id: str,
    world_time: int,
    item_id: str,
    source_event_id: str,
    resolution: ClothingDailyWear,
) -> Event:
    event_id = _event_id("clothing_wear")
    return Event(
        event_id,
        "item.wear_applied",
        actor_id,
        world_time,
        {
            "wearId": event_id,
            "itemId": item_id,
            "sourceEventId": source_event_id,
            "mode": "clothing_daily",
            "trigger": "clothing_daily",
            "wornHours": resolution.worn_hours,
            "lifespanDays": resolution.lifespan_days,
            "fullWearHours": resolution.full_wear_hours,
            "previousCurrent": resolution.previous_current,
            "max": resolution.maximum,
            "loss": resolution.loss,
            "current": resolution.current,
        },
        WEAR_EVENT_SCHEMA_VERSION,
    )


def build_item_repaired_event(
    *,
    actor_id: str,
    world_time: int,
    item_id: str,
    source_event_id: str,
    resolution: RepairResolution,
    ability_id: str,
    level: str,
    source_status: str,
    material_item_ids: Sequence[str] = (),
    tool_item_ids: Sequence[str] = (),
    physical_basis: Sequence[str] = (),
    confidence: float = 1.0,
) -> Event:
    if not resolution.succeeded:
        raise ValueError("item.repaired can only be built from a successful repair")
    event_id = _event_id("repair")
    check = {
        "status": resolution.status,
        "abilityId": ability_id,
        "level": level,
        "sourceStatus": source_status,
        "difficultyBand": next(
            band for band, dc in {"trivial": 8, "routine": 11, "demanding": 14, "hard": 17, "extreme": 20}.items()
            if dc == resolution.dc
        ),
        "dc": resolution.dc,
        "modifier": resolution.modifier,
        "roll": resolution.roll,
        "total": resolution.total,
        "margin": resolution.margin,
    }
    return Event(
        event_id,
        "item.repaired",
        actor_id,
        world_time,
        {
            "repairId": event_id,
            "itemId": item_id,
            "sourceEventId": source_event_id,
            "repairLevel": resolution.repair_level,
            "previousCurrent": resolution.previous_current,
            "max": resolution.maximum,
            "recovered": resolution.recovered,
            "current": resolution.current,
            "status": resolution.status,
            "recoveryCap": resolution.recovery_cap,
            "check": check,
            "materialItemIds": list(material_item_ids),
            "toolItemIds": list(tool_item_ids),
            "physicalBasis": list(physical_basis),
            "confidence": confidence,
        },
        REPAIR_EVENT_SCHEMA_VERSION,
    )


def build_item_repair_attempt_event(
    *,
    actor_id: str,
    world_time: int,
    item_id: str,
    attempt_id: str,
    repair_level: str,
    check: Mapping[str, Any],
    material_item_ids: Sequence[str] = (),
    tool_item_ids: Sequence[str] = (),
    physical_basis: Sequence[str] = (),
    source_kind: str = "player_repair_request",
) -> Event:
    """Create the auditable behavior source used by a subsequent repair.

    Keeping this as a small event makes ``item.repaired.sourceEventId`` point
    to a confirmed action even when the check fails (in which case no repaired
    event follows).  It contains no state mutation beyond an audit index.
    """

    if type(attempt_id) is not str or not attempt_id.strip():
        raise ValueError("attempt_id must be a non-empty string")
    if type(source_kind) is not str or not source_kind.strip():
        raise ValueError("source_kind must be a non-empty string")
    return Event(
        attempt_id,
        "item.repair_attempted",
        actor_id,
        world_time,
        {
            "attemptId": attempt_id,
            "itemId": item_id,
            "repairLevel": repair_level,
            "check": dict(check),
            "materialItemIds": list(material_item_ids),
            "toolItemIds": list(tool_item_ids),
            "physicalBasis": list(physical_basis),
            "sourceKind": source_kind,
        },
        REPAIR_EVENT_SCHEMA_VERSION,
    )


@projection_handlers.register("item.repair_attempted")
def apply_item_repair_attempted(state: Projection, event: Event) -> None:
    if event.schema_version != REPAIR_EVENT_SCHEMA_VERSION:
        raise WearEventError("unsupported item.repair_attempted schema version")
    payload = event.payload
    _check_required_payload(
        payload,
        {"attemptId", "itemId", "repairLevel", "check", "materialItemIds", "toolItemIds", "physicalBasis", "sourceKind"},
        set(), event.event_type,
    )
    attempt_id = _text(payload["attemptId"], "attemptId", maximum=180)
    if attempt_id != event.event_id:
        raise WearEventError("attemptId must match event id")
    if attempt_id in state.item_repair_attempts:
        raise WearEventError("repair attempt has already been projected")
    target = _item(state, payload["itemId"])
    if target.durability is None or durability_kind_for_item(target.category, target.properties) is None:
        raise WearEventError("repair target must be a non-consumable durable item")
    level = _text(payload["repairLevel"], "repairLevel", maximum=40)
    if level not in REPAIR_LEVELS:
        raise WearEventError("repairLevel is not supported")
    check = _check_mapping(payload["check"])
    material_ids = _instance_ids(
        state, payload["materialItemIds"], "materialItemIds", allow_empty=True
    )
    tool_ids = _instance_ids(
        state, payload["toolItemIds"], "toolItemIds", allow_empty=True
    )
    if target.item_id in {*material_ids, *tool_ids}:
        raise WearEventError("repair target cannot also be a material or tool")
    if set(material_ids).intersection(tool_ids):
        raise WearEventError("material and tool references must not overlap")
    _string_list(payload["physicalBasis"], "physicalBasis", allow_empty=True)
    source_kind = _text(payload["sourceKind"], "sourceKind", maximum=80)
    if source_kind not in REPAIR_SOURCE_KINDS:
        raise WearEventError("sourceKind is not supported")
    state.item_repair_attempts[attempt_id] = deepcopy(dict(payload))


@projection_handlers.register("item.wear_applied")
def apply_item_wear_applied(state: Projection, event: Event) -> None:
    if event.schema_version != WEAR_EVENT_SCHEMA_VERSION:
        raise WearEventError("unsupported item.wear_applied schema version")
    payload = event.payload
    _check_required_payload(
        payload,
        {"wearId", "itemId", "sourceEventId", "mode", "trigger"},
        {
            "wearBand", "estimatedLossRatio", "boundedLossRatio", "previousCurrent", "max", "loss", "current",
            "check", "physicalBasis", "confidence", "wornHours", "lifespanDays", "fullWearHours",
        },
        event.event_type,
    )
    wear_id = _text(payload["wearId"], "wearId", maximum=180)
    if wear_id != event.event_id:
        raise WearEventError("wearId must match event id")
    if wear_id in state.item_wear_records:
        raise WearEventError("wear record has already been projected")
    source_id = _source_id(payload["sourceEventId"], state, event)
    item = _item(state, payload["itemId"])
    profile = _durable_item(item)
    mode = _text(payload["mode"], "mode", maximum=40)
    trigger = _text(payload["trigger"], "trigger", maximum=100)
    if trigger == "repair_tool_use":
        if state.event_types_by_id.get(source_id) != "item.repair_attempted":
            raise WearEventError("repair tool wear requires an item.repair_attempted source")
        attempt = state.item_repair_attempts.get(source_id)
        if attempt is None:
            raise WearEventError("repair tool wear source attempt is not available")
        if payload["itemId"] not in attempt.get("toolItemIds", ()):
            raise WearEventError("repair tool wear item is not listed by its source attempt")
        source_actor = state.event_actors_by_id.get(source_id)
        if source_actor is not None and source_actor != event.actor_id:
            raise WearEventError("repair tool wear actor does not match its source attempt")
    if mode == "behavior":
        required = {"wearBand", "estimatedLossRatio", "boundedLossRatio", "previousCurrent", "max", "loss", "current", "check", "physicalBasis", "confidence"}
        if not required.issubset(payload):
            raise WearEventError("behavior wear payload is incomplete")
        check = _check_mapping(payload["check"])
        _string_list(payload["physicalBasis"], "physicalBasis")
        confidence = _number(payload["confidence"], "confidence", minimum=0)
        if confidence > 1:
            raise WearEventError("confidence must be between 0 and 1")
        expected = resolve_behavior_wear(
            current=profile["current"],
            maximum=profile["max"],
            wear_band=_text(payload["wearBand"], "wearBand", maximum=40),
            estimated_loss_ratio=_number(payload["estimatedLossRatio"], "estimatedLossRatio", minimum=0),
            roll=check["roll"],
            modifier=check["modifier"],
            dc=check["dc"],
        )
        for field, value in (
            ("previousCurrent", expected.previous_current), ("max", expected.maximum),
            ("boundedLossRatio", expected.bounded_loss_ratio), ("loss", expected.loss),
            ("current", expected.current),
        ):
            _same_number(payload[field], value, field)
        if check["status"] != ("succeeded" if expected.total >= expected.dc else "failed"):
            raise WearEventError("check status does not match wear calculation")
    elif mode == "clothing_daily":
        # Daily wear is a calendar rule for garments only.  Keeping the
        # category and trigger explicit prevents a forged event from using
        # the clothing lifespan formula for tools or equipment.
        if durability_kind_for_item(item.category, item.properties) != "clothing":
            raise WearEventError("clothing daily wear requires a clothing item")
        if trigger != "clothing_daily":
            raise WearEventError("clothing daily wear requires a clothing_daily trigger")
        required = {"wornHours", "lifespanDays", "fullWearHours", "previousCurrent", "max", "loss", "current"}
        if not required.issubset(payload):
            raise WearEventError("clothing wear payload is incomplete")
        expected = resolve_clothing_daily_wear(
            current=profile["current"], maximum=profile["max"],
            worn_hours=_number(payload["wornHours"], "wornHours", minimum=0),
            lifespan_days=_number(payload["lifespanDays"], "lifespanDays", minimum=0.0001),
            full_wear_hours=_number(payload["fullWearHours"], "fullWearHours", minimum=0.0001),
        )
        for field, value in (("previousCurrent", expected.previous_current), ("max", expected.maximum), ("loss", expected.loss), ("current", expected.current)):
            _same_number(payload[field], value, field)
        if state.event_types_by_id.get(source_id) != "time.advanced":
            raise WearEventError("clothing daily wear requires a time.advanced source")
    else:
        raise WearEventError("unsupported item wear mode")
    item.durability = {"current": round(float(payload["current"]), 2), "max": profile["max"]}
    item.last_changed_event_id = event.event_id
    state.item_wear_records[wear_id] = deepcopy(dict(payload))


@projection_handlers.register("item.repaired")
def apply_item_repaired(state: Projection, event: Event) -> None:
    if event.schema_version != REPAIR_EVENT_SCHEMA_VERSION:
        raise WearEventError("unsupported item.repaired schema version")
    payload = event.payload
    _check_required_payload(
        payload,
        {"repairId", "itemId", "sourceEventId", "repairLevel", "previousCurrent", "max", "recovered", "current", "status", "recoveryCap", "check", "materialItemIds", "toolItemIds", "physicalBasis", "confidence"},
        set(), event.event_type,
    )
    repair_id = _text(payload["repairId"], "repairId", maximum=180)
    if repair_id != event.event_id:
        raise WearEventError("repairId must match event id")
    if repair_id in state.item_repair_records:
        raise WearEventError("repair record has already been projected")
    source_id = _source_id(payload["sourceEventId"], state, event)
    if state.event_types_by_id.get(source_id) != "item.repair_attempted":
        raise WearEventError("item.repaired requires an item.repair_attempted source")
    attempt = state.item_repair_attempts.get(source_id)
    if attempt is None:
        raise WearEventError("item.repaired source attempt is not available")
    if attempt.get("itemId") != payload["itemId"]:
        raise WearEventError("repair target does not match its source attempt")
    if attempt.get("repairLevel") != payload["repairLevel"]:
        raise WearEventError("repair level does not match its source attempt")
    source_actor = state.event_actors_by_id.get(source_id)
    if source_actor is not None and source_actor != event.actor_id:
        raise WearEventError("repair actor does not match its source attempt")
    item = _item(state, payload["itemId"])
    profile = _durable_item(item)
    level = _text(payload["repairLevel"], "repairLevel", maximum=40)
    if level not in REPAIR_LEVELS:
        raise WearEventError("repairLevel is not supported")
    status = _text(payload["status"], "status", maximum=40)
    if status != "succeeded":
        raise WearEventError("item.repaired requires a succeeded repair")
    check = _check_mapping(payload["check"], status=status)
    material_ids = _instance_ids(
        state, payload["materialItemIds"], "materialItemIds", allow_empty=True
    )
    tool_ids = _instance_ids(
        state, payload["toolItemIds"], "toolItemIds", allow_empty=True
    )
    if item.item_id in {*material_ids, *tool_ids}:
        raise WearEventError("repair target cannot also be a material or tool")
    if set(material_ids).intersection(tool_ids):
        raise WearEventError("material and tool references must not overlap")
    source_material_ids = attempt.get("materialItemIds")
    source_tool_ids = attempt.get("toolItemIds")
    if material_ids != source_material_ids or tool_ids != source_tool_ids:
        raise WearEventError("repair materials and tools do not match source attempt")
    source_check = _check_mapping(attempt.get("check"), status="succeeded")
    if check != source_check:
        raise WearEventError("repair check does not match source attempt")
    _string_list(payload["physicalBasis"], "physicalBasis", allow_empty=True)
    confidence = _number(payload["confidence"], "confidence", minimum=0)
    if confidence > 1:
        raise WearEventError("confidence must be between 0 and 1")
    expected = resolve_repair(
        current=profile["current"], maximum=profile["max"], repair_level=level,
        roll=check["roll"], modifier=check["modifier"], dc=check["dc"],
    )
    for field, value in (("previousCurrent", expected.previous_current), ("max", expected.maximum), ("recoveryCap", expected.recovery_cap), ("recovered", expected.recovered), ("current", expected.current)):
        _same_number(payload[field], value, field)
    if status != expected.status:
        raise WearEventError("repair status does not match check")
    item.durability = {"current": expected.current, "max": profile["max"]}
    item.last_changed_event_id = event.event_id
    state.item_repair_records[repair_id] = deepcopy(dict(payload))


__all__ = [
    "REPAIR_EVENT_SCHEMA_VERSION",
    "WEAR_EVENT_SCHEMA_VERSION",
    "WearEventError",
    "apply_item_repaired",
    "apply_item_repair_attempted",
    "apply_item_wear_applied",
    "build_clothing_wear_event",
    "build_item_repair_attempt_event",
    "build_item_repaired_event",
    "build_item_wear_event",
]
