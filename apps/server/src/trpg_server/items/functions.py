"""Objective item function subcontracts stored inside ``properties``.

These profiles say what an item physically supports.  Consumable effects are
only bounded candidates for another domain to resolve; they never mutate
character, location, item, or world state by themselves.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any, Literal


BODY_SLOT_IDS = frozenset(
    {
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
    }
)
HAND_SLOT_IDS = frozenset({"left_hand", "right_hand"})
CONSUMABLE_TARGET_KINDS = frozenset(
    {"character", "item", "location", "device", "environment", "none"}
)
EFFECT_DOMAINS = frozenset({"characters", "items", "locations", "world"})
EFFECT_MAGNITUDES = frozenset(
    {"negligible", "minor", "moderate", "major"}
)
CONSUMABLE_RISK_CLASSES = frozenset(
    {"low", "moderate", "high", "restricted"}
)
PROPERTY_KEYS = frozenset({"volumeCm3", "equipment", "consumable"})
_SLUG = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


class ItemFunctionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EquipmentProfile:
    mode: Literal["held", "worn"]
    slot_ids: tuple[str, ...]
    hand_count: int

    def to_mapping(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "slotIds": list(self.slot_ids),
            "handCount": self.hand_count,
        }


@dataclass(frozen=True, slots=True)
class ConsumableEffectCandidate:
    domain: str
    effect_kind: str
    summary: str
    magnitude: str
    duration_minutes: int | None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "effectKind": self.effect_kind,
            "summary": self.summary,
            "magnitude": self.magnitude,
            "durationMinutes": self.duration_minutes,
            "requiresDomainResolution": True,
        }


@dataclass(frozen=True, slots=True)
class ConsumableProfile:
    method: str
    target_kinds: tuple[str, ...]
    risk_class: str
    effects: tuple[ConsumableEffectCandidate, ...]

    @property
    def quantity_per_use(self) -> int:
        return 1

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "quantityPerUse": 1,
            "method": self.method,
            "targetKinds": list(self.target_kinds),
            "riskClass": self.risk_class,
            "effectCandidates": [effect.to_mapping() for effect in self.effects],
        }


def validate_item_properties(
    properties: Mapping[str, Any],
    *,
    category: str,
    path: str = "properties",
) -> dict[str, Any]:
    if not isinstance(properties, Mapping):
        raise ItemFunctionError(f"{path} must be an object")
    unknown = set(properties).difference(PROPERTY_KEYS)
    if unknown:
        raise ItemFunctionError(
            f"{path} has unknown function fields: {sorted(unknown)}"
        )
    normalized: dict[str, Any] = {}
    if "volumeCm3" in properties:
        volume = properties["volumeCm3"]
        if type(volume) is not int or volume < 0:
            raise ItemFunctionError(f"{path}.volumeCm3 must be a non-negative integer")
        normalized["volumeCm3"] = volume
    if "equipment" in properties:
        equipment = parse_equipment_profile(
            properties["equipment"],
            path=f"{path}.equipment",
        )
        normalized["equipment"] = equipment.to_mapping()
    if "consumable" in properties:
        if category == "currency":
            raise ItemFunctionError(f"{path}.consumable cannot be used by currency")
        consumable = parse_consumable_profile(
            properties["consumable"],
            path=f"{path}.consumable",
        )
        normalized["consumable"] = consumable.to_mapping()
    return normalized


def parse_equipment_profile(
    raw: object,
    *,
    path: str = "properties.equipment",
) -> EquipmentProfile:
    if not isinstance(raw, Mapping) or set(raw) != {
        "mode",
        "slotIds",
        "handCount",
    }:
        raise ItemFunctionError(
            f"{path} must contain exactly mode, slotIds and handCount"
        )
    mode = raw["mode"]
    if mode not in {"held", "worn"}:
        raise ItemFunctionError(f"{path}.mode must be held or worn")
    slot_ids = _string_list(raw["slotIds"], f"{path}.slotIds", maximum=10)
    if not slot_ids or any(slot not in BODY_SLOT_IDS for slot in slot_ids):
        raise ItemFunctionError(f"{path}.slotIds contains an unknown body slot")
    hand_count = raw["handCount"]
    if type(hand_count) is not int:
        raise ItemFunctionError(f"{path}.handCount must be an integer")
    if mode == "held":
        if hand_count not in {1, 2}:
            raise ItemFunctionError(f"{path}.handCount must be 1 or 2 for held items")
        if not set(slot_ids).issubset(HAND_SLOT_IDS):
            raise ItemFunctionError(f"{path}.held items can only use hand slots")
        if hand_count == 2 and set(slot_ids) != HAND_SLOT_IDS:
            raise ItemFunctionError(
                f"{path}.two-handed items must allow both hand slots"
            )
    elif hand_count != 0:
        raise ItemFunctionError(f"{path}.handCount must be 0 for worn items")
    return EquipmentProfile(mode=mode, slot_ids=slot_ids, hand_count=hand_count)


def parse_consumable_profile(
    raw: object,
    *,
    path: str = "properties.consumable",
) -> ConsumableProfile:
    expected = {
        "schemaVersion",
        "quantityPerUse",
        "method",
        "targetKinds",
        "riskClass",
        "effectCandidates",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise ItemFunctionError(
            f"{path} fields do not match the consumable profile contract"
        )
    if raw["schemaVersion"] != 1:
        raise ItemFunctionError(f"{path}.schemaVersion must be 1")
    if raw["quantityPerUse"] != 1 or type(raw["quantityPerUse"]) is not int:
        raise ItemFunctionError(
            f"{path}.quantityPerUse must be 1; define item units at one use each"
        )
    method = _slug(raw["method"], f"{path}.method")
    target_kinds = _string_list(
        raw["targetKinds"],
        f"{path}.targetKinds",
        maximum=6,
    )
    if not target_kinds or any(
        value not in CONSUMABLE_TARGET_KINDS for value in target_kinds
    ):
        raise ItemFunctionError(f"{path}.targetKinds contains an unsupported target")
    risk_class = raw["riskClass"]
    if risk_class not in CONSUMABLE_RISK_CLASSES:
        raise ItemFunctionError(f"{path}.riskClass is invalid")
    effect_values = raw["effectCandidates"]
    if not isinstance(effect_values, list) or not 1 <= len(effect_values) <= 6:
        raise ItemFunctionError(
            f"{path}.effectCandidates must contain between 1 and 6 effects"
        )
    effects = tuple(
        _parse_effect_candidate(value, path=f"{path}.effectCandidates[{index}]")
        for index, value in enumerate(effect_values)
    )
    if len({(value.domain, value.effect_kind) for value in effects}) != len(effects):
        raise ItemFunctionError(f"{path}.effectCandidates contains duplicates")
    return ConsumableProfile(
        method=method,
        target_kinds=target_kinds,
        risk_class=str(risk_class),
        effects=effects,
    )


def item_consumable_profile(item: object) -> ConsumableProfile | None:
    properties = getattr(item, "properties", {})
    if not isinstance(properties, Mapping) or "consumable" not in properties:
        return None
    try:
        return parse_consumable_profile(properties["consumable"])
    except ItemFunctionError:
        return None


def validate_generated_function_profiles(
    *,
    equipment: object,
    consumable: object,
) -> dict[str, Any]:
    """Validate the restricted function subset an AI generator may propose."""

    properties: dict[str, Any] = {}
    if equipment is not None:
        properties["equipment"] = parse_equipment_profile(equipment).to_mapping()
    if consumable is not None:
        profile = parse_consumable_profile(consumable)
        if profile.risk_class in {"high", "restricted"}:
            raise ItemFunctionError(
                "AI-generated consumables cannot have high or restricted risk"
            )
        if any(effect.magnitude == "major" for effect in profile.effects):
            raise ItemFunctionError(
                "AI-generated consumables cannot propose major effects"
            )
        properties["consumable"] = profile.to_mapping()
    return properties


def _parse_effect_candidate(
    raw: object,
    *,
    path: str,
) -> ConsumableEffectCandidate:
    expected = {
        "domain",
        "effectKind",
        "summary",
        "magnitude",
        "durationMinutes",
        "requiresDomainResolution",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise ItemFunctionError(f"{path} fields are invalid")
    domain = raw["domain"]
    if domain not in EFFECT_DOMAINS:
        raise ItemFunctionError(f"{path}.domain is invalid")
    effect_kind = _slug(raw["effectKind"], f"{path}.effectKind")
    summary = raw["summary"]
    if type(summary) is not str or not summary.strip() or len(summary.strip()) > 200:
        raise ItemFunctionError(
            f"{path}.summary must be a non-empty string of at most 200 characters"
        )
    magnitude = raw["magnitude"]
    if magnitude not in EFFECT_MAGNITUDES:
        raise ItemFunctionError(f"{path}.magnitude is invalid")
    duration = raw["durationMinutes"]
    if duration is not None and (
        type(duration) is not int or not 1 <= duration <= 525_600
    ):
        raise ItemFunctionError(
            f"{path}.durationMinutes must be null or between 1 and 525600"
        )
    if raw["requiresDomainResolution"] is not True:
        raise ItemFunctionError(f"{path} must require domain resolution")
    return ConsumableEffectCandidate(
        domain=str(domain),
        effect_kind=effect_kind,
        summary=summary.strip(),
        magnitude=str(magnitude),
        duration_minutes=duration,
    )


def _slug(value: object, path: str) -> str:
    if type(value) is not str or len(value) > 80 or _SLUG.fullmatch(value) is None:
        raise ItemFunctionError(
            f"{path} must be a lowercase ASCII identifier with underscores"
        )
    return value


def _string_list(value: object, path: str, *, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ItemFunctionError(f"{path} must be an array of at most {maximum} strings")
    result: list[str] = []
    for item in value:
        if type(item) is not str or not item or len(item) > 80:
            raise ItemFunctionError(f"{path} contains an invalid string")
        result.append(item)
    if len(result) != len(set(result)):
        raise ItemFunctionError(f"{path} must contain unique strings")
    return tuple(result)


__all__ = [
    "BODY_SLOT_IDS",
    "CONSUMABLE_RISK_CLASSES",
    "CONSUMABLE_TARGET_KINDS",
    "EFFECT_DOMAINS",
    "EFFECT_MAGNITUDES",
    "EquipmentProfile",
    "ConsumableEffectCandidate",
    "ConsumableProfile",
    "ItemFunctionError",
    "PROPERTY_KEYS",
    "item_consumable_profile",
    "parse_consumable_profile",
    "parse_equipment_profile",
    "validate_generated_function_profiles",
    "validate_item_properties",
]
