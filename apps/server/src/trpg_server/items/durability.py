"""Pure durability policy for item creation and future event integration.

Durability is an objective item value, but deciding initial values from prose
is delegated to the isolated AI-item layer.  This module owns the numerical
limits and item-category invariants.  It deliberately contains no wear,
repair, corrosion, moisture, or time-evolution rules.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import Any, Literal


DurabilityKind = Literal["tool", "clothing", "equipment"]
ConditionGrade = Literal["new", "good", "worn", "poor", "broken"]

BASE_NEW_SMALL_KNIFE_DURABILITY = 100.0
DURABILITY_CATEGORIES = frozenset({"tool", "clothing", "equipment"})

# Maximum durability is expressed relative to a brand-new small knife.  The
# caps are intentionally conservative; clothing cannot exceed the knife
# baseline, while robust tools/equipment still have a finite ceiling.
RELATIVE_MAXIMUM_RANGES: dict[DurabilityKind, tuple[float, float]] = {
    "tool": (0.20, 1.80),
    "clothing": (0.10, 0.90),
    "equipment": (0.20, 1.80),
}

CONDITION_CODES = frozenset(
    {"new", "intact", "worn", "rusted", "poor", "damaged", "broken"}
)
CONDITION_GRADES = frozenset({"new", "good", "worn", "poor", "broken"})
CONDITION_GRADE_COMPATIBILITY: dict[str, frozenset[str]] = {
    "new": frozenset({"new"}),
    "intact": frozenset({"new", "good"}),
    "worn": frozenset({"worn"}),
    "rusted": frozenset({"worn", "poor"}),
    "poor": frozenset({"poor"}),
    "damaged": frozenset({"worn", "poor"}),
    "broken": frozenset({"broken"}),
}


class DurabilityError(ValueError):
    """Raised when durability violates the item contract."""


@dataclass(frozen=True, slots=True)
class DurabilityProfile:
    current: float
    maximum: float

    def __post_init__(self) -> None:
        current = _finite_number(self.current, "durability.current")
        maximum = _finite_number(self.maximum, "durability.max")
        if maximum <= 0:
            raise DurabilityError("durability.max must be positive")
        if current < 0 or current > maximum:
            raise DurabilityError("durability must contain 0 <= current <= max")
        object.__setattr__(self, "current", round(current, 2))
        object.__setattr__(self, "maximum", round(maximum, 2))

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "DurabilityProfile":
        if not isinstance(value, Mapping) or set(value) != {"current", "max"}:
            raise DurabilityError(
                "durability must be null or an object with current and max"
            )
        return cls(current=value["current"], maximum=value["max"])  # type: ignore[arg-type]

    def to_mapping(self) -> dict[str, float]:
        return {"current": self.current, "max": self.maximum}


def durability_kind_for_item(
    category: str,
    properties: Mapping[str, Any],
) -> DurabilityKind | None:
    """Return the applicable class without guessing from item names.

    A one-use consumable never has durability, even when it is also a tool or
    wearable object.  An explicit equipment profile makes an otherwise named
    category eligible as equipment.
    """

    if "consumable" in properties:
        return None
    if category in DURABILITY_CATEGORIES:
        return category  # type: ignore[return-value]
    if "equipment" in properties:
        return "equipment"
    return None


def validate_item_durability(
    *,
    category: str,
    properties: Mapping[str, Any],
    durability: Mapping[str, object] | None,
    require_for_eligible: bool = False,
) -> dict[str, float] | None:
    """Validate one absolute profile and normalize its numbers to floats.

    This is the future event-module integration point.  Event logic may later
    decide a proposed absolute value, but it must call this function before a
    confirmed event can project the change.
    """

    kind = durability_kind_for_item(category, properties)
    if durability is None:
        if require_for_eligible and kind is not None:
            raise DurabilityError(
                f"{kind} item instances require initial durability"
            )
        return None
    if kind is None:
        raise DurabilityError(
            "durability is only allowed for non-consumable tools, clothing, or equipment"
        )
    return DurabilityProfile.from_mapping(durability).to_mapping()


def profile_from_creation_ratios(
    *,
    kind: DurabilityKind,
    condition: str,
    condition_grade: ConditionGrade,
    relative_maximum: float,
    remaining_ratio: float,
) -> DurabilityProfile:
    """Convert a bounded AI candidate into authoritative float values."""

    if kind not in RELATIVE_MAXIMUM_RANGES:
        raise DurabilityError("unknown durability kind")
    if condition not in CONDITION_CODES:
        raise DurabilityError("condition is not an allowed durability condition")
    if condition_grade not in CONDITION_GRADES:
        raise DurabilityError("conditionGrade is invalid")
    if condition_grade not in CONDITION_GRADE_COMPATIBILITY[condition]:
        raise DurabilityError("condition and conditionGrade are inconsistent")

    relative = _finite_number(relative_maximum, "relativeMaximum")
    minimum, maximum = RELATIVE_MAXIMUM_RANGES[kind]
    if not minimum <= relative <= maximum:
        raise DurabilityError(
            f"relativeMaximum for {kind} must be between {minimum} and {maximum}"
        )
    remaining = _finite_number(remaining_ratio, "remainingRatio")
    if not _remaining_ratio_matches_grade(remaining, condition_grade):
        raise DurabilityError(
            "remainingRatio is outside the selected conditionGrade band"
        )

    maximum_value = round(BASE_NEW_SMALL_KNIFE_DURABILITY * relative, 2)
    current_value = round(maximum_value * remaining, 2)
    return DurabilityProfile(current=current_value, maximum=maximum_value)


def _remaining_ratio_matches_grade(value: float, grade: str) -> bool:
    if grade == "new":
        return 0.95 <= value <= 1.0
    if grade == "good":
        return 0.75 <= value < 0.95
    if grade == "worn":
        return 0.40 <= value < 0.75
    if grade == "poor":
        return 0 < value < 0.40
    return grade == "broken" and value == 0


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DurabilityError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise DurabilityError(f"{label} must be a finite number")
    return result


__all__ = [
    "BASE_NEW_SMALL_KNIFE_DURABILITY",
    "CONDITION_CODES",
    "ConditionGrade",
    "CONDITION_GRADES",
    "DURABILITY_CATEGORIES",
    "RELATIVE_MAXIMUM_RANGES",
    "DurabilityError",
    "DurabilityKind",
    "DurabilityProfile",
    "durability_kind_for_item",
    "profile_from_creation_ratios",
    "validate_item_durability",
]
