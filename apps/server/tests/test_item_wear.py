from __future__ import annotations

import math

import pytest

from trpg_server.items.wear import (
    MAX_SINGLE_WEAR_RATIO,
    REPAIR_RECOVERY_RATIOS,
    WearRuleError,
    clamp_estimated_loss_ratio,
    clothing_daily_wear,
    operation_quality_multiplier,
    repair_recovery_cap,
    resolve_behavior_wear,
    resolve_clothing_daily_wear,
    resolve_repair,
)


def test_wear_band_estimate_is_clamped_to_program_range() -> None:
    assert clamp_estimated_loss_ratio("light", 0.0001) == 0.005
    assert clamp_estimated_loss_ratio("light", 0.5) == 0.015
    assert clamp_estimated_loss_ratio("heavy", 0.06) == 0.06

    with pytest.raises(WearRuleError, match="estimatedLossRatio"):
        clamp_estimated_loss_ratio("light", -0.01)
    with pytest.raises(WearRuleError, match="unknown wear band"):
        clamp_estimated_loss_ratio("catastrophic", 0.1)


@pytest.mark.parametrize(
    ("roll", "margin", "expected"),
    [
        (1, 20, 2.0),  # natural 1 takes precedence over a positive margin
        (2, -5, 1.5),
        (10, -1, 1.25),
        (10, 0, 1.0),
        (10, 4, 1.0),
        (10, 5, 0.75),
        (10, 9, 0.75),
        (10, 10, 0.5),
        (20, -10, 0.5),  # natural 20 is careful even with a hard DC
    ],
)
def test_operation_quality_multiplier_boundaries(
    roll: int, margin: int, expected: float
) -> None:
    assert operation_quality_multiplier(roll, margin) == expected


def test_behavior_wear_uses_d20_quality_and_single_action_cap() -> None:
    # Heavy AI estimate is clamped to 10%: 100 * .10 = 10.  A very poor
    # operation multiplies it by 1.5, but remains below the 30% cap.
    poor = resolve_behavior_wear(
        current=100,
        maximum=100,
        wear_band="heavy",
        estimated_loss_ratio=0.50,
        roll=8,
        modifier=0,
        dc=14,
    )
    assert poor.bounded_loss_ratio == 0.10
    assert poor.base_loss == 10.0
    assert poor.margin == -6
    assert poor.multiplier == 1.5
    assert poor.loss == 15.0
    assert poor.current == 85.0

    # A natural 1 with a critical candidate is still bounded to 25% * 2 and
    # then to the 30% per-action cap.
    capped = resolve_behavior_wear(
        current=100,
        maximum=100,
        wear_band="critical",
        estimated_loss_ratio=0.25,
        roll=1,
        modifier=0,
        dc=8,
    )
    assert capped.uncapped_loss == 50.0
    assert capped.single_action_cap == 30.0
    assert capped.loss == MAX_SINGLE_WEAR_RATIO * 100
    assert capped.current == 70.0


def test_behavior_wear_never_drives_current_below_zero() -> None:
    result = resolve_behavior_wear(
        current=3,
        maximum=100,
        wear_band="heavy",
        estimated_loss_ratio=0.1,
        roll=1,
        modifier=-2,
        difficulty_band="extreme",
    )
    assert result.loss == 3.0
    assert result.current == 0.0
    assert result.depleted


def test_behavior_wear_requires_a_valid_profile_and_check_target() -> None:
    common = dict(
        wear_band="light",
        estimated_loss_ratio=0.01,
        roll=10,
        modifier=0,
        dc=11,
    )
    with pytest.raises(WearRuleError, match="maximum must be positive"):
        resolve_behavior_wear(current=0, maximum=0, **common)
    with pytest.raises(WearRuleError, match="current must not exceed"):
        resolve_behavior_wear(current=101, maximum=100, **common)
    with pytest.raises(WearRuleError, match="provide dc"):
        resolve_behavior_wear(
            current=100,
            maximum=100,
            wear_band="light",
            estimated_loss_ratio=0.01,
            roll=10,
            modifier=0,
        )


def test_clothing_daily_wear_uses_180_eight_hour_days() -> None:
    assert clothing_daily_wear(100, 8) == pytest.approx(100 / 180, abs=0.005)
    assert clothing_daily_wear(100, 4) == pytest.approx(100 / 360, abs=0.005)
    assert clothing_daily_wear(100, 0) == 0.0
    # The function can aggregate a measured duration spanning multiple normal
    # days; the resulting loss is capped at the profile's maximum.
    assert clothing_daily_wear(100, 8 * 200) == 100.0
    with pytest.raises(WearRuleError, match="wornHours"):
        clothing_daily_wear(100, -1)


def test_clothing_daily_resolution_caps_at_remaining_current() -> None:
    result = resolve_clothing_daily_wear(
        current=0.2,
        maximum=100,
        worn_hours=8 * 200,
    )
    assert result.loss == 0.2
    assert result.current == 0.0
    assert result.new_current == 0.0


def test_repair_caps_and_success_semantics() -> None:
    assert repair_recovery_cap(100, "patch") == 10.0
    assert repair_recovery_cap(100, "standard") == 25.0
    assert repair_recovery_cap(100, "major") == 50.0
    assert repair_recovery_cap(100, "rebuild") == 75.0
    assert set(REPAIR_RECOVERY_RATIOS) == {"patch", "standard", "major", "rebuild"}

    success = resolve_repair(
        current=0,
        maximum=100,
        repair_level="standard",
        roll=15,
        modifier=0,
        difficulty_band="routine",
    )
    assert success.succeeded
    assert success.recovered == 25.0
    assert success.current == 25.0

    # Failure is non-destructive and never consumes a recovery allowance.
    failure = resolve_repair(
        current=20,
        maximum=100,
        repair_level="major",
        roll=1,
        modifier=-2,
        dc=20,
    )
    assert not failure.succeeded
    assert failure.recovered == 0.0
    assert failure.current == 20.0

    # A successful repair near full durability cannot exceed max.
    near_full = resolve_repair(
        current=95,
        maximum=100,
        repair_level="rebuild",
        roll=20,
        modifier=6,
        dc=20,
    )
    assert near_full.recovered == 5.0
    assert near_full.current == 100.0


def test_repair_level_and_numeric_inputs_are_strict() -> None:
    with pytest.raises(WearRuleError, match="unknown repair level"):
        repair_recovery_cap(100, "full")
    with pytest.raises(WearRuleError, match="finite"):
        clothing_daily_wear(math.inf, 1)
    with pytest.raises(WearRuleError, match="roll"):
        operation_quality_multiplier(21, 0)


def test_difficulty_band_and_explicit_dc_must_use_the_same_program_mapping() -> None:
    with pytest.raises(WearRuleError, match="does not match"):
        resolve_behavior_wear(
            current=100,
            maximum=100,
            wear_band="light",
            estimated_loss_ratio=0.01,
            roll=10,
            modifier=0,
            dc=20,
            difficulty_band="routine",
        )
    with pytest.raises(WearRuleError, match="program difficulty band"):
        resolve_behavior_wear(
            current=100,
            maximum=100,
            wear_band="light",
            estimated_loss_ratio=0.01,
            roll=10,
            modifier=0,
            dc=13,
        )


def test_wear_and_repair_results_are_immutable_audit_snapshots() -> None:
    wear = resolve_behavior_wear(
        current=100,
        maximum=100,
        wear_band="light",
        estimated_loss_ratio=0.01,
        roll=10,
        modifier=0,
        difficulty_band="routine",
    )
    repair = resolve_repair(
        current=50,
        maximum=100,
        repair_level="patch",
        roll=15,
        modifier=0,
        difficulty_band="routine",
    )

    with pytest.raises(AttributeError):
        wear.loss = 0.0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        repair.current = 100.0  # type: ignore[misc]
