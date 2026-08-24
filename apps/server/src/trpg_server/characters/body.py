"""Character body slots and external injury rules.

This is a deliberately small physical model.  It covers the externally
observable body parts needed for clothing, held objects, and future action
restrictions.  Internal injuries are intentionally outside this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal


BODY_PARTS: tuple[str, ...] = (
    "head",
    "torso",
    "left_arm",
    "right_arm",
    "left_leg",
    "right_leg",
    "left_hand",
    "right_hand",
    "left_foot",
    "right_foot",
)
HAND_SLOTS: frozenset[str] = frozenset({"left_hand", "right_hand"})
FOOT_SLOTS: frozenset[str] = frozenset({"left_foot", "right_foot"})
ARM_SLOTS: frozenset[str] = frozenset({"left_arm", "right_arm"})
LEG_SLOTS: frozenset[str] = frozenset({"left_leg", "right_leg"})
INJURY_SEVERITIES = frozenset({"minor", "moderate", "severe", "critical"})
INJURY_STATUSES = frozenset({"active", "healing", "missing"})

BodyPartKind = Literal[
    "head", "torso", "arm", "leg", "hand", "foot"
]


def is_body_part(value: object) -> bool:
    return type(value) is str and value in BODY_PARTS


def body_part_parents(slot_id: str) -> tuple[str, ...]:
    """Return the slot and the limb controlling it, when applicable."""

    if slot_id == "left_hand":
        return ("left_hand", "left_arm")
    if slot_id == "right_hand":
        return ("right_hand", "right_arm")
    if slot_id == "left_foot":
        return ("left_foot", "left_leg")
    if slot_id == "right_foot":
        return ("right_foot", "right_leg")
    return (slot_id,)


def default_functional_effects(body_part: str, status: str) -> dict[str, bool]:
    """Conservative defaults for an injury with no detailed medical data."""

    missing = status == "missing"
    return {
        "gripAllowed": not missing and body_part not in HAND_SLOTS | ARM_SLOTS,
        "movementAllowed": not missing and body_part not in FOOT_SLOTS | LEG_SLOTS,
        "wearAllowed": not missing,
    }


def normalize_functional_effects(
    body_part: str,
    status: str,
    raw: Mapping[str, Any] | None,
) -> dict[str, bool]:
    defaults = default_functional_effects(body_part, status)
    if raw is None:
        return defaults
    result = dict(defaults)
    for key in defaults:
        value = raw.get(key)
        if value is not None:
            if type(value) is not bool:
                raise ValueError(f"functionalEffects.{key} must be boolean")
            result[key] = value
    unknown = set(raw).difference(defaults)
    if unknown:
        raise ValueError(
            "functionalEffects has unknown fields: " + ", ".join(sorted(unknown))
        )
    return result


def validate_external_injury(
    *, body_part: str, severity: str, status: str, functional_effects: Mapping[str, Any] | None
) -> dict[str, bool]:
    if not is_body_part(body_part):
        raise ValueError(f"unknown external injury body part: {body_part}")
    if severity not in INJURY_SEVERITIES:
        raise ValueError(f"unknown external injury severity: {severity}")
    if status not in INJURY_STATUSES:
        raise ValueError(f"unknown external injury status: {status}")
    if status == "missing" and severity != "critical":
        raise ValueError("a missing body part must use critical severity")
    return normalize_functional_effects(body_part, status, functional_effects)


def injury_blocks(
    injuries: Mapping[str, Mapping[str, Any]],
    slot_id: str,
    purpose: Literal["hold", "wear", "movement"],
) -> bool:
    """Return whether an active injury blocks a requested body function."""

    if not is_body_part(slot_id):
        return True
    effect_key = {
        "hold": "gripAllowed",
        "wear": "wearAllowed",
        "movement": "movementAllowed",
    }[purpose]
    for body_part in body_part_parents(slot_id):
        for injury in injuries.values():
            if injury.get("bodyPart") != body_part:
                continue
            if injury.get("status") not in INJURY_STATUSES:
                continue
            effects = injury.get("functionalEffects", {})
            if not isinstance(effects, Mapping) or not effects.get(effect_key, False):
                return True
    return False


def all_slots_blocked(
    injuries: Mapping[str, Mapping[str, Any]],
    slot_ids: tuple[str, ...] | frozenset[str],
    purpose: Literal["hold", "wear", "movement"],
) -> bool:
    """Return true only when every candidate body slot is unavailable."""

    return bool(slot_ids) and all(injury_blocks(injuries, slot_id, purpose) for slot_id in slot_ids)


def any_slot_blocked(
    injuries: Mapping[str, Mapping[str, Any]],
    slot_ids: tuple[str, ...] | frozenset[str],
    purpose: Literal["hold", "wear", "movement"],
) -> bool:
    return any(injury_blocks(injuries, slot_id, purpose) for slot_id in slot_ids)


__all__ = [
    "ARM_SLOTS",
    "BODY_PARTS",
    "FOOT_SLOTS",
    "HAND_SLOTS",
    "INJURY_SEVERITIES",
    "INJURY_STATUSES",
    "LEG_SLOTS",
    "body_part_parents",
    "default_functional_effects",
    "all_slots_blocked",
    "any_slot_blocked",
    "injury_blocks",
    "is_body_part",
    "normalize_functional_effects",
    "validate_external_injury",
]
