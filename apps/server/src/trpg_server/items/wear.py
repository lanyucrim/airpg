"""Pure rules for item wear, clothing use, and repairs.

This module intentionally has no knowledge of events, projections, storage,
model SDKs, or item instances.  It receives an already validated durability
profile and returns immutable calculation results.  Callers in the behavior
and event layers are responsible for deciding *whether* an action happened,
checking provenance/ownership, and committing the result as an event.

The AI wear candidate is only an estimate.  :func:`resolve_behavior_wear`
clamps that estimate to the program-owned wear-band range, applies the d20
quality multiplier, and enforces the per-action cap.  The same calculation is
deterministic for a supplied roll, which makes event replay and tests
straightforward.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import Literal


WearBand = Literal["trace", "light", "moderate", "heavy", "critical"]
RepairLevel = Literal["patch", "standard", "major", "rebuild"]
DifficultyBand = Literal["trivial", "routine", "demanding", "hard", "extreme"]


# Fractions of an item's maximum durability.  These are program-owned bounds;
# a model may suggest a point inside a band but cannot expand the band.
WEAR_BAND_RANGES: Mapping[str, tuple[float, float]] = {
    "trace": (0.002, 0.005),
    "light": (0.005, 0.015),
    "moderate": (0.015, 0.04),
    "heavy": (0.04, 0.10),
    "critical": (0.10, 0.25),
}
WEAR_BANDS = frozenset(WEAR_BAND_RANGES)

# A normal action can never consume more than this fraction of max durability
# in one event.  A critical candidate can still be bounded by this cap.
MAX_SINGLE_WEAR_RATIO = 0.30

# A complete clothing-wear day is eight hours; 180 such days is the default
# normal-life estimate agreed for everyday clothing.
DEFAULT_CLOTHING_LIFESPAN_DAYS = 180.0
DEFAULT_FULL_WEAR_HOURS = 8.0

REPAIR_RECOVERY_RATIOS: Mapping[str, float] = {
    "patch": 0.10,
    "standard": 0.25,
    "major": 0.50,
    "rebuild": 0.75,
}
REPAIR_LEVELS = frozenset(REPAIR_RECOVERY_RATIOS)

# Kept local deliberately: importing the character domain here would make a
# pure item calculation depend on a cross-domain module.  The labels and
# values are the same public d20 contract used by character checks.
DIFFICULTY_DC: Mapping[str, int] = {
    "trivial": 8,
    "routine": 11,
    "demanding": 14,
    "hard": 17,
    "extreme": 20,
}
DIFFICULTY_BANDS = frozenset(DIFFICULTY_DC)


class WearRuleError(ValueError):
    """Raised when a wear or repair calculation has invalid input."""


def _finite_number(value: object, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WearRuleError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise WearRuleError(f"{field} must be a finite number")
    if minimum is not None and result < minimum:
        raise WearRuleError(f"{field} must be at least {minimum}")
    return result


def _positive(value: object, field: str) -> float:
    result = _finite_number(value, field)
    if result <= 0:
        raise WearRuleError(f"{field} must be positive")
    return result


def _integer(value: object, field: str) -> int:
    if type(value) is not int:
        raise WearRuleError(f"{field} must be an integer")
    return value


def _roll(value: object) -> int:
    result = _integer(value, "roll")
    if not 1 <= result <= 20:
        raise WearRuleError("roll must be an integer from 1 to 20")
    return result


def _round(value: float) -> float:
    """Normalize persisted numeric values to the item contract precision."""

    # Avoid ``-0.0`` appearing in an event payload.
    result = round(float(value), 2)
    return 0.0 if result == 0 else result


def wear_band_range(wear_band: str) -> tuple[float, float]:
    """Return the program-owned inclusive ratio range for one wear band."""

    if type(wear_band) is not str or wear_band not in WEAR_BAND_RANGES:
        raise WearRuleError(f"unknown wear band: {wear_band}")
    return WEAR_BAND_RANGES[wear_band]


def default_wear_ratio(wear_band: str) -> float:
    """Return the deterministic midpoint used by a rule-based fallback."""

    lower, upper = wear_band_range(wear_band)
    return (lower + upper) / 2


def clamp_estimated_loss_ratio(
    wear_band: str,
    estimated_loss_ratio: float,
) -> float:
    """Clamp an AI estimate to its selected band.

    Negative and non-finite estimates are malformed candidates and are
    rejected.  Oversized positive estimates are safely bounded to the band;
    this preserves the useful part of a model suggestion without allowing it
    to bypass the numerical policy.
    """

    lower, upper = wear_band_range(wear_band)
    value = _finite_number(estimated_loss_ratio, "estimatedLossRatio", minimum=0)
    return min(upper, max(lower, value))


def difficulty_to_dc(difficulty_band: str) -> int:
    """Map an allow-listed difficulty label to the program-owned DC."""

    if type(difficulty_band) is not str or difficulty_band not in DIFFICULTY_DC:
        raise WearRuleError(f"unknown difficulty band: {difficulty_band}")
    return DIFFICULTY_DC[difficulty_band]


def _resolve_dc(*, dc: int | None, difficulty_band: str | None) -> int:
    if dc is None and difficulty_band is None:
        raise WearRuleError("provide dc or difficulty_band")
    mapped = None if difficulty_band is None else difficulty_to_dc(difficulty_band)
    if dc is not None:
        result = _integer(dc, "dc")
        if result <= 0:
            raise WearRuleError("dc must be positive")
        # A raw numeric DC is accepted for event replay/convenience only when
        # it is one of the published program mappings.  Otherwise callers
        # could bypass the allow-listed difficulty contract with an arbitrary
        # model- or player-supplied number.
        if result not in DIFFICULTY_DC.values():
            raise WearRuleError("dc must match a program difficulty band")
        if mapped is not None and result != mapped:
            raise WearRuleError("dc does not match difficulty_band")
        return result
    # ``mapped`` is non-null because the no-input case returned above.
    assert mapped is not None
    return mapped


def operation_quality_multiplier(roll: int, margin: int) -> float:
    """Return the wear multiplier for a d20 operation result.

    Natural 1 and natural 20 are checked before the margin.  A natural 1 is
    especially rough (2x); a natural 20 is careful (0.5x).  Other results are
    determined solely by ``total - DC``.
    """

    die = _roll(roll)
    difference = _integer(margin, "margin")
    if die == 1:
        return 2.0
    if die == 20:
        return 0.5
    if difference <= -5:
        return 1.5
    if difference < 0:
        return 1.25
    if difference <= 4:
        return 1.0
    if difference <= 9:
        return 0.75
    return 0.5


def quality_multiplier_for_check(
    roll: int,
    *,
    modifier: int,
    dc: int | None = None,
    difficulty_band: str | None = None,
) -> float:
    """Convenience wrapper deriving margin from a d20 check input."""

    die = _roll(roll)
    ability_modifier = _integer(modifier, "modifier")
    target_dc = _resolve_dc(dc=dc, difficulty_band=difficulty_band)
    return operation_quality_multiplier(die, die + ability_modifier - target_dc)


@dataclass(frozen=True, slots=True)
class WearResolution:
    """Auditable result of one behavior-triggered wear calculation."""

    previous_current: float
    maximum: float
    wear_band: str
    estimated_loss_ratio: float
    bounded_loss_ratio: float
    base_loss: float
    uncapped_loss: float
    single_action_cap: float
    multiplier: float
    loss: float
    current: float
    roll: int
    modifier: int
    dc: int
    total: int
    margin: int

    @property
    def new_current(self) -> float:
        """Alias used by event builders."""

        return self.current

    @property
    def depleted(self) -> bool:
        return self.current <= 0

    def to_mapping(self) -> dict[str, object]:
        """Return an event-friendly audit mapping without creating an event."""

        return {
            "previousCurrent": self.previous_current,
            "max": self.maximum,
            "wearBand": self.wear_band,
            "estimatedLossRatio": self.estimated_loss_ratio,
            "boundedLossRatio": self.bounded_loss_ratio,
            "baseLoss": self.base_loss,
            "uncappedLoss": self.uncapped_loss,
            "singleActionCap": self.single_action_cap,
            "multiplier": self.multiplier,
            "loss": self.loss,
            "current": self.current,
            "roll": self.roll,
            "modifier": self.modifier,
            "dc": self.dc,
            "total": self.total,
            "margin": self.margin,
        }


def resolve_behavior_wear(
    *,
    current: float,
    maximum: float,
    wear_band: str,
    estimated_loss_ratio: float,
    roll: int,
    modifier: int,
    dc: int | None = None,
    difficulty_band: str | None = None,
) -> WearResolution:
    """Resolve one confirmed contact/force action against an item.

    ``estimated_loss_ratio`` is deliberately retained in the result for
    audit, while ``bounded_loss_ratio`` is the only ratio used for math.  The
    actual loss is additionally capped by the remaining current durability,
    so applying a repeated event cannot produce a negative profile.
    """

    maximum_value = _positive(maximum, "maximum")
    current_value = _finite_number(current, "current", minimum=0)
    if current_value > maximum_value:
        raise WearRuleError("current must not exceed maximum")
    # Validate the band before evaluating the estimate so malformed labels do
    # not get hidden by another input error.
    wear_band_range(wear_band)
    estimated_value = _finite_number(
        estimated_loss_ratio, "estimatedLossRatio", minimum=0
    )
    bounded = clamp_estimated_loss_ratio(wear_band, estimated_value)
    die = _roll(roll)
    ability_modifier = _integer(modifier, "modifier")
    target_dc = _resolve_dc(dc=dc, difficulty_band=difficulty_band)
    total = die + ability_modifier
    margin = total - target_dc
    multiplier = operation_quality_multiplier(die, margin)

    base_loss = _round(maximum_value * bounded)
    uncapped_loss = max(0.0, base_loss * multiplier)
    cap = _round(maximum_value * MAX_SINGLE_WEAR_RATIO)
    loss = _round(min(current_value, cap, uncapped_loss))
    new_current = _round(current_value - loss)
    return WearResolution(
        previous_current=_round(current_value),
        maximum=_round(maximum_value),
        wear_band=wear_band,
        estimated_loss_ratio=estimated_value,
        bounded_loss_ratio=bounded,
        base_loss=base_loss,
        uncapped_loss=_round(uncapped_loss),
        single_action_cap=cap,
        multiplier=multiplier,
        loss=loss,
        current=new_current,
        roll=die,
        modifier=ability_modifier,
        dc=target_dc,
        total=total,
        margin=margin,
    )


def calculate_wear_loss(**kwargs: object) -> float:
    """Return only the applied loss for callers that do not need the audit."""

    return resolve_behavior_wear(**kwargs).loss  # type: ignore[arg-type]


# Short aliases make the policy convenient for command code while retaining a
# descriptive canonical name for new integrations.
resolve_wear = resolve_behavior_wear
quality_multiplier = operation_quality_multiplier


@dataclass(frozen=True, slots=True)
class ClothingDailyWear:
    """Applied result for normal clothing wear over a measured duration."""

    previous_current: float
    maximum: float
    worn_hours: float
    lifespan_days: float
    full_wear_hours: float
    loss: float
    current: float

    @property
    def new_current(self) -> float:
        return self.current

    def to_mapping(self) -> dict[str, object]:
        return {
            "previousCurrent": self.previous_current,
            "max": self.maximum,
            "wornHours": self.worn_hours,
            "lifespanDays": self.lifespan_days,
            "fullWearHours": self.full_wear_hours,
            "loss": self.loss,
            "current": self.current,
        }


def clothing_daily_wear(
    maximum: float,
    worn_hours: float,
    *,
    lifespan_days: float = DEFAULT_CLOTHING_LIFESPAN_DAYS,
    full_wear_hours: float = DEFAULT_FULL_WEAR_HOURS,
) -> float:
    """Calculate normal clothing loss for a measured wearing duration.

    The formula is ``max / lifespan_days * (worn_hours / full_wear_hours)``.
    It intentionally does not inspect weather, moisture, or garment
    descriptions.  Callers should pass only time for which the item was
    actually equipped; storage and idle time contribute zero.
    """

    maximum_value = _positive(maximum, "maximum")
    hours = _finite_number(worn_hours, "wornHours", minimum=0)
    lifespan = _positive(lifespan_days, "lifespanDays")
    daily_hours = _positive(full_wear_hours, "fullWearHours")
    loss = maximum_value / lifespan * (hours / daily_hours)
    return _round(min(maximum_value, loss))


def resolve_clothing_daily_wear(
    *,
    current: float,
    maximum: float,
    worn_hours: float,
    lifespan_days: float = DEFAULT_CLOTHING_LIFESPAN_DAYS,
    full_wear_hours: float = DEFAULT_FULL_WEAR_HOURS,
) -> ClothingDailyWear:
    """Apply normal clothing wear without a die or AI call."""

    maximum_value = _positive(maximum, "maximum")
    current_value = _finite_number(current, "current", minimum=0)
    if current_value > maximum_value:
        raise WearRuleError("current must not exceed maximum")
    hours = _finite_number(worn_hours, "wornHours", minimum=0)
    loss = clothing_daily_wear(
        maximum_value,
        hours,
        lifespan_days=lifespan_days,
        full_wear_hours=full_wear_hours,
    )
    applied = _round(min(current_value, loss))
    return ClothingDailyWear(
        previous_current=_round(current_value),
        maximum=_round(maximum_value),
        worn_hours=hours,
        lifespan_days=_positive(lifespan_days, "lifespanDays"),
        full_wear_hours=_positive(full_wear_hours, "fullWearHours"),
        loss=applied,
        current=_round(current_value - applied),
    )


calculate_clothing_daily_wear = clothing_daily_wear


def repair_recovery_ratio(repair_level: str) -> float:
    """Return the maximum fraction recoverable by a repair level."""

    if type(repair_level) is not str or repair_level not in REPAIR_RECOVERY_RATIOS:
        raise WearRuleError(f"unknown repair level: {repair_level}")
    return REPAIR_RECOVERY_RATIOS[repair_level]


def repair_recovery_cap(maximum: float, repair_level: str) -> float:
    """Return the program-owned maximum recovery amount for one repair."""

    maximum_value = _positive(maximum, "maximum")
    return _round(maximum_value * repair_recovery_ratio(repair_level))


@dataclass(frozen=True, slots=True)
class RepairResolution:
    """Auditable d20 repair result; materials and events remain caller-owned."""

    previous_current: float
    maximum: float
    repair_level: str
    recovery_cap: float
    recovered: float
    current: float
    roll: int
    modifier: int
    dc: int
    total: int
    margin: int
    succeeded: bool

    @property
    def status(self) -> str:
        return "succeeded" if self.succeeded else "failed"

    @property
    def new_current(self) -> float:
        return self.current

    def to_mapping(self) -> dict[str, object]:
        return {
            "previousCurrent": self.previous_current,
            "max": self.maximum,
            "repairLevel": self.repair_level,
            "recoveryCap": self.recovery_cap,
            "recovered": self.recovered,
            "current": self.current,
            "roll": self.roll,
            "modifier": self.modifier,
            "dc": self.dc,
            "total": self.total,
            "margin": self.margin,
            "status": self.status,
        }


def resolve_repair(
    *,
    current: float,
    maximum: float,
    repair_level: str,
    roll: int,
    modifier: int,
    dc: int | None = None,
    difficulty_band: str | None = None,
) -> RepairResolution:
    """Resolve a repair check and cap recovery without touching state.

    A failed check recovers nothing.  A successful check restores at most the
    selected level's fraction of ``maximum`` and never raises ``current``
    above ``maximum``.  Material validity, tool wear, and event atomicity are
    intentionally outside this pure function.
    """

    maximum_value = _positive(maximum, "maximum")
    current_value = _finite_number(current, "current", minimum=0)
    if current_value > maximum_value:
        raise WearRuleError("current must not exceed maximum")
    cap = repair_recovery_cap(maximum_value, repair_level)
    die = _roll(roll)
    ability_modifier = _integer(modifier, "modifier")
    target_dc = _resolve_dc(dc=dc, difficulty_band=difficulty_band)
    total = die + ability_modifier
    margin = total - target_dc
    succeeded = total >= target_dc
    recovered = _round(min(cap, maximum_value - current_value)) if succeeded else 0.0
    return RepairResolution(
        previous_current=_round(current_value),
        maximum=_round(maximum_value),
        repair_level=repair_level,
        recovery_cap=cap,
        recovered=recovered,
        current=_round(current_value + recovered),
        roll=die,
        modifier=ability_modifier,
        dc=target_dc,
        total=total,
        margin=margin,
        succeeded=succeeded,
    )


__all__ = [
    "ClothingDailyWear",
    "DEFAULT_CLOTHING_LIFESPAN_DAYS",
    "DEFAULT_FULL_WEAR_HOURS",
    "DIFFICULTY_BANDS",
    "DIFFICULTY_DC",
    "DifficultyBand",
    "MAX_SINGLE_WEAR_RATIO",
    "REPAIR_LEVELS",
    "REPAIR_RECOVERY_RATIOS",
    "RepairLevel",
    "RepairResolution",
    "WearBand",
    "WEAR_BANDS",
    "WEAR_BAND_RANGES",
    "WearResolution",
    "WearRuleError",
    "calculate_clothing_daily_wear",
    "calculate_wear_loss",
    "clamp_estimated_loss_ratio",
    "clothing_daily_wear",
    "default_wear_ratio",
    "difficulty_to_dc",
    "operation_quality_multiplier",
    "quality_multiplier",
    "quality_multiplier_for_check",
    "repair_recovery_cap",
    "repair_recovery_ratio",
    "resolve_behavior_wear",
    "resolve_clothing_daily_wear",
    "resolve_repair",
    "resolve_wear",
    "wear_band_range",
]
