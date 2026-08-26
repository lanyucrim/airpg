from __future__ import annotations

import random

import pytest

from trpg_server.characters.checks import (
    ABILITY_LEVEL_MODIFIERS,
    AbilityCheckInput,
    PhysicalRequirements,
    ability_check_input_from_profile,
    ability_modifier,
    difficulty_to_dc,
    physical_requirements_from_injuries,
    resolve_ability_check,
)


def test_explicit_sources_grant_level_modifier_but_inferred_is_untrained() -> None:
    canon = AbilityCheckInput(
        ability_id="mechanical_repair",
        level="advanced",
        source_status="canon",
    )
    player_defined = AbilityCheckInput(
        ability_id="lock_work",
        level="competent",
        source_status="player_defined",
    )
    inferred = AbilityCheckInput(
        ability_id="mechanical_repair",
        level="expert",
        source_status="inferred",
    )

    assert canon.modifier == ABILITY_LEVEL_MODIFIERS["advanced"] == 4
    assert player_defined.modifier == ABILITY_LEVEL_MODIFIERS["competent"] == 2
    assert inferred.modifier == ABILITY_LEVEL_MODIFIERS["untrained"] == -2
    assert ability_modifier("advanced", "player_defined") == 4
    assert ability_modifier("advanced", "inferred") == -2


def test_missing_profile_ability_is_untrained_and_unknown() -> None:
    value = ability_check_input_from_profile(
        [{"abilityId": "other", "level": "expert", "sourceStatus": "canon"}],
        "lock_work",
    )

    assert value.level == "untrained"
    assert value.source_status == "unknown"
    assert value.modifier == -2


def test_difficulty_is_program_mapped_and_seeded_rng_is_reproducible() -> None:
    assert difficulty_to_dc("routine") == 11
    first = resolve_ability_check(
        AbilityCheckInput(
            ability_id="lock_work",
            level="competent",
            source_status="player_defined",
        ),
        difficulty_band="routine",
        rng=random.Random(7),
    )
    second = resolve_ability_check(
        AbilityCheckInput(
            ability_id="lock_work",
            level="competent",
            source_status="player_defined",
        ),
        difficulty_band="routine",
        rng=random.Random(7),
    )

    assert first == second
    assert first.roll is not None
    assert first.total == first.roll + 2
    assert first.succeeded is (first.total >= 11)


def test_physical_hard_limit_blocks_before_rng_is_drawn() -> None:
    calls = 0

    def unexpected_roll() -> int:
        nonlocal calls
        calls += 1
        return 20

    check_input = AbilityCheckInput(
        ability_id="lock_work",
        level="expert",
        source_status="canon",
        physical=PhysicalRequirements(
            required_hand_count=1,
            blocked_body_parts=frozenset({"left_hand", "right_hand"}),
        ),
    )

    result = resolve_ability_check(
        check_input,
        difficulty_band="trivial",
        rng=unexpected_roll,
    )

    assert result.status == "blocked"
    assert result.code == "insufficient_hands"
    assert result.roll is None
    assert calls == 0


def test_required_hand_slot_and_arm_injury_are_respected() -> None:
    input_value = AbilityCheckInput(
        ability_id="fine_motor_work",
        level="working",
        source_status="player_defined",
        physical=PhysicalRequirements(
            required_hand_count=1,
            required_hand_slots=("left_hand",),
            blocked_body_parts=frozenset({"left_arm"}),
            available_hand_slots=frozenset({"left_hand", "right_hand"}),
        ),
    )

    result = resolve_ability_check(
        input_value,
        difficulty_band="routine",
        rng=lambda: 20,
    )

    assert result.status == "blocked"
    assert result.code == "hand_slot_unavailable"


def test_injury_projection_adapter_uses_character_body_rules() -> None:
    requirements = physical_requirements_from_injuries(
        {
            "injury_left_arm": {
                "bodyPart": "left_arm",
                "status": "active",
                "functionalEffects": {"gripAllowed": False},
            }
        },
        purpose="hold",
        required_hand_count=1,
        required_hand_slots=("left_hand",),
    )
    assert "left_hand" in requirements.blocked_body_parts
    assert requirements.blocking_reason() == "hand_slot_unavailable"


def test_invalid_profile_and_difficulty_are_rejected() -> None:
    with pytest.raises(ValueError, match="unknown ability source status"):
        AbilityCheckInput(
            ability_id="lock_work",
            level="competent",
            source_status="model_generated",
        )
    with pytest.raises(ValueError, match="unknown difficulty band"):
        difficulty_to_dc("dc_99")
    with pytest.raises(ValueError, match="duplicate abilityId"):
        ability_check_input_from_profile(
            [
                {"abilityId": "lock_work", "level": "working", "sourceStatus": "canon"},
                {"abilityId": "lock_work", "level": "expert", "sourceStatus": "canon"},
            ],
            "lock_work",
        )
    with pytest.raises(ValueError, match="confidence"):
        ability_check_input_from_profile(
            [{"abilityId": "lock_work", "level": "working", "sourceStatus": "canon", "confidence": True}],
            "lock_work",
        )
