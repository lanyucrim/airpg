"""Deterministic character ability checks.

This module is deliberately independent from the event store, projection and
model adapters.  It turns an already-authoritative character profile into a
small check input and resolves ``d20 + modifier >= DC``.  Callers are expected
to perform ownership, target and item validation before invoking it.

The source status on an ability is important: ``canon`` and
``player_defined`` are explicit evidence and may grant the level modifier;
``inferred`` and ``unknown`` are retained for audit but are treated as
untrained for mechanics.  A physical prerequisite failure is resolved before
the die is drawn, so an impossible action cannot be rescued by a lucky roll.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import random
from typing import Literal, TypeAlias

from trpg_server.characters.body import (
    BODY_PARTS,
    HAND_SLOTS,
    body_part_parents,
    injury_blocks,
)


AbilityLevel = Literal["untrained", "working", "competent", "advanced", "expert"]
AbilitySourceStatus = Literal["canon", "player_defined", "inferred", "unknown"]
DifficultyBand = Literal["trivial", "routine", "demanding", "hard", "extreme"]
CheckStatus = Literal["succeeded", "failed", "blocked"]
PhysicalPurpose = Literal["hold", "wear", "movement"]


# These values are a stable rules contract.  Do not derive them from AI
# confidence or from free-form character text.
ABILITY_LEVEL_MODIFIERS: Mapping[str, int] = {
    "untrained": -2,
    "working": 0,
    "competent": 2,
    "advanced": 4,
    "expert": 6,
}

# AI may only submit one of these labels; the program owns the numerical DC.
DIFFICULTY_DC: Mapping[str, int] = {
    "trivial": 8,
    "routine": 11,
    "demanding": 14,
    "hard": 17,
    "extreme": 20,
}

MECHANICAL_SOURCE_STATUSES: frozenset[str] = frozenset(
    {"canon", "player_defined"}
)
KNOWN_SOURCE_STATUSES: frozenset[str] = frozenset(
    {"canon", "player_defined", "inferred", "unknown"}
)
ABILITY_LEVELS: frozenset[str] = frozenset(ABILITY_LEVEL_MODIFIERS)
DIFFICULTY_BANDS: frozenset[str] = frozenset(DIFFICULTY_DC)


def _non_empty_string(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _string_tuple(values: Iterable[str], field: str) -> tuple[str, ...]:
    result = tuple(_non_empty_string(value, field) for value in values)
    if len(set(result)) != len(result):
        raise ValueError(f"{field} must not contain duplicates")
    return result


@dataclass(frozen=True, slots=True)
class PhysicalRequirements:
    """Hard physical prerequisites for an ability check.

    ``blocked_body_parts`` is a normalized snapshot supplied by the character
    domain.  The check never infers an injury from narration.  When
    ``available_hand_slots`` is supplied it is treated as the authoritative
    usable subset for this action; otherwise hand availability is derived from
    ``blocked_body_parts``.  ``required_body_parts`` is useful for fine motor,
    movement or wear operations in addition to the generic hand count.
    """

    required_body_parts: tuple[str, ...] = ()
    required_hand_count: int = 0
    required_hand_slots: tuple[str, ...] = ()
    blocked_body_parts: frozenset[str] = frozenset()
    available_body_parts: frozenset[str] | None = None
    available_hand_slots: frozenset[str] | None = None

    def __post_init__(self) -> None:
        required = _string_tuple(self.required_body_parts, "required_body_parts")
        required_hands = _string_tuple(
            self.required_hand_slots, "required_hand_slots"
        )
        blocked = frozenset(
            _non_empty_string(value, "blocked_body_parts")
            for value in self.blocked_body_parts
        )
        if any(value not in BODY_PARTS for value in required):
            raise ValueError("required_body_parts contains an unknown body part")
        if any(value not in HAND_SLOTS for value in required_hands):
            raise ValueError("required_hand_slots contains an unknown hand slot")
        if any(value not in BODY_PARTS for value in blocked):
            raise ValueError("blocked_body_parts contains an unknown body part")
        if type(self.required_hand_count) is not int or not 0 <= self.required_hand_count <= 2:
            raise ValueError("required_hand_count must be an integer from 0 to 2")
        if required_hands and len(required_hands) != self.required_hand_count:
            raise ValueError(
                "required_hand_slots count must equal required_hand_count"
            )

        available_body = self.available_body_parts
        if available_body is not None:
            available_body = frozenset(
                _non_empty_string(value, "available_body_parts")
                for value in available_body
            )
            if any(value not in BODY_PARTS for value in available_body):
                raise ValueError(
                    "available_body_parts contains an unknown body part"
                )
        available_hands = self.available_hand_slots
        if available_hands is not None:
            available_hands = frozenset(
                _non_empty_string(value, "available_hand_slots")
                for value in available_hands
            )
            if any(value not in HAND_SLOTS for value in available_hands):
                raise ValueError(
                    "available_hand_slots contains an unknown hand slot"
                )
        object.__setattr__(self, "required_body_parts", required)
        object.__setattr__(self, "required_hand_slots", required_hands)
        object.__setattr__(self, "blocked_body_parts", blocked)
        object.__setattr__(self, "available_body_parts", available_body)
        object.__setattr__(self, "available_hand_slots", available_hands)

    def _is_available(self, body_part: str) -> bool:
        if body_part in self.blocked_body_parts:
            return False
        # A blocked arm controls its hand; a blocked leg controls its foot.
        if any(parent in self.blocked_body_parts for parent in body_part_parents(body_part)):
            return False
        if self.available_body_parts is not None and body_part not in self.available_body_parts:
            return False
        return True

    def usable_hand_slots(self) -> frozenset[str]:
        """Return hand slots usable for this action, conservatively."""

        candidates = HAND_SLOTS if self.available_hand_slots is None else self.available_hand_slots
        return frozenset(slot for slot in candidates if self._is_available(slot))

    def blocking_reason(self) -> str | None:
        """Return a stable rejection code, or ``None`` when prerequisites pass."""

        for body_part in self.required_body_parts:
            if not self._is_available(body_part):
                return "body_part_unavailable"
        usable_hands = self.usable_hand_slots()
        if self.required_hand_slots:
            if not set(self.required_hand_slots).issubset(usable_hands):
                return "hand_slot_unavailable"
        elif self.required_hand_count > len(usable_hands):
            return "insufficient_hands"
        return None


def physical_requirements_from_injuries(
    injuries: Mapping[str, Mapping[str, object]] | None,
    *,
    purpose: PhysicalPurpose,
    required_body_parts: Iterable[str] = (),
    required_hand_count: int = 0,
    required_hand_slots: Iterable[str] = (),
    available_body_parts: Iterable[str] | None = None,
    available_hand_slots: Iterable[str] | None = None,
) -> PhysicalRequirements:
    """Build a check snapshot from the character injury projection.

    ``injury_blocks`` remains the single body-domain rule for active and
    missing injuries.  This adapter only translates its result into the
    check value object and never mutates the injury map.
    """

    if purpose not in {"hold", "wear", "movement"}:
        raise ValueError(f"unknown physical purpose: {purpose}")
    injury_map: Mapping[str, Mapping[str, object]] = injuries or {}
    blocked = frozenset(
        slot
        for slot in BODY_PARTS
        if injury_blocks(injury_map, slot, purpose)
    )
    return PhysicalRequirements(
        required_body_parts=tuple(required_body_parts),
        required_hand_count=required_hand_count,
        required_hand_slots=tuple(required_hand_slots),
        blocked_body_parts=blocked,
        available_body_parts=(
            None if available_body_parts is None else frozenset(available_body_parts)
        ),
        available_hand_slots=(
            None if available_hand_slots is None else frozenset(available_hand_slots)
        ),
    )


@dataclass(frozen=True, slots=True)
class AbilityCheckInput:
    """Immutable, mechanics-ready character ability evidence."""

    ability_id: str
    level: str = "untrained"
    source_status: str = "unknown"
    confidence: float | None = None
    physical: PhysicalRequirements = PhysicalRequirements()

    def __post_init__(self) -> None:
        object.__setattr__(self, "ability_id", _non_empty_string(self.ability_id, "ability_id"))
        if type(self.level) is not str or self.level not in ABILITY_LEVELS:
            raise ValueError(f"unknown ability level: {self.level}")
        if type(self.source_status) is not str or self.source_status not in KNOWN_SOURCE_STATUSES:
            raise ValueError(f"unknown ability source status: {self.source_status}")
        if self.confidence is not None:
            if type(self.confidence) not in {int, float} or not 0 <= self.confidence <= 1:
                raise ValueError("confidence must be null or between 0 and 1")
        if not isinstance(self.physical, PhysicalRequirements):
            raise TypeError("physical must be a PhysicalRequirements instance")

    @property
    def modifier(self) -> int:
        """Return the effective modifier after applying source evidence rules."""

        if self.source_status not in MECHANICAL_SOURCE_STATUSES:
            return ABILITY_LEVEL_MODIFIERS["untrained"]
        return ABILITY_LEVEL_MODIFIERS[self.level]

    @property
    def mechanically_supported(self) -> bool:
        return self.source_status in MECHANICAL_SOURCE_STATUSES


@dataclass(frozen=True, slots=True)
class AbilityCheckResult:
    """Auditable outcome of one check; no event or state mutation is included."""

    status: CheckStatus
    code: str
    ability_id: str
    level: str
    source_status: str
    difficulty_band: str
    dc: int
    modifier: int
    roll: int | None
    total: int | None
    margin: int | None
    reason: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"

    @property
    def success(self) -> bool:
        """Boolean alias for integrations that expose a ``success`` field."""

        return self.succeeded

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"

    def to_mapping(self) -> dict[str, object]:
        return {
            "status": self.status,
            "code": self.code,
            "abilityId": self.ability_id,
            "level": self.level,
            "sourceStatus": self.source_status,
            "difficultyBand": self.difficulty_band,
            "dc": self.dc,
            "modifier": self.modifier,
            "roll": self.roll,
            "total": self.total,
            "margin": self.margin,
            "reason": self.reason,
        }


RandomSource: TypeAlias = random.Random | Callable[[], int]


def difficulty_to_dc(difficulty_band: str) -> int:
    """Map an allow-listed difficulty band to its program-owned DC."""

    if type(difficulty_band) is not str or difficulty_band not in DIFFICULTY_DC:
        raise ValueError(f"unknown difficulty band: {difficulty_band}")
    return DIFFICULTY_DC[difficulty_band]


def ability_modifier(level: str, source_status: str) -> int:
    """Return the effective modifier for a level and evidence status.

    This helper is useful to callers that need to display a preview without
    rolling.  It follows exactly the same source rule as
    :attr:`AbilityCheckInput.modifier` and performs the same strict input
    validation.
    """

    if type(level) is not str or level not in ABILITY_LEVELS:
        raise ValueError(f"unknown ability level: {level}")
    if type(source_status) is not str or source_status not in KNOWN_SOURCE_STATUSES:
        raise ValueError(f"unknown ability source status: {source_status}")
    if source_status not in MECHANICAL_SOURCE_STATUSES:
        return ABILITY_LEVEL_MODIFIERS["untrained"]
    return ABILITY_LEVEL_MODIFIERS[level]


def physical_block_reason(requirements: PhysicalRequirements) -> str | None:
    """Expose the pre-roll physical gate as a small pure predicate."""

    if not isinstance(requirements, PhysicalRequirements):
        raise TypeError("requirements must be a PhysicalRequirements instance")
    return requirements.blocking_reason()


def _draw_d20(rng: RandomSource) -> int:
    if isinstance(rng, random.Random):
        value = rng.randint(1, 20)
    elif callable(rng):
        value = rng()
    else:
        raise TypeError("rng must be random.Random or a zero-argument callable")
    if type(value) is not int or not 1 <= value <= 20:
        raise ValueError("rng must return an integer from 1 to 20")
    return value


def resolve_ability_check(
    check_input: AbilityCheckInput,
    *,
    difficulty_band: str,
    rng: RandomSource,
) -> AbilityCheckResult:
    """Resolve one d20 check after hard physical prerequisites.

    ``difficulty_band`` is intentionally a label rather than a free numeric
    DC, so a model cannot smuggle an arbitrary difficulty into the resolver.
    The RNG is explicit to make replay and tests reproducible.
    """

    if not isinstance(check_input, AbilityCheckInput):
        raise TypeError("check_input must be an AbilityCheckInput instance")
    dc = difficulty_to_dc(difficulty_band)
    blocked_reason = check_input.physical.blocking_reason()
    if blocked_reason is not None:
        return AbilityCheckResult(
            status="blocked",
            code=blocked_reason,
            ability_id=check_input.ability_id,
            level=check_input.level,
            source_status=check_input.source_status,
            difficulty_band=difficulty_band,
            dc=dc,
            modifier=check_input.modifier,
            roll=None,
            total=None,
            margin=None,
            reason="physical prerequisites are not satisfied",
        )
    roll = _draw_d20(rng)
    total = roll + check_input.modifier
    margin = total - dc
    succeeded = total >= dc
    return AbilityCheckResult(
        status="succeeded" if succeeded else "failed",
        code="succeeded" if succeeded else "failed_check",
        ability_id=check_input.ability_id,
        level=check_input.level,
        source_status=check_input.source_status,
        difficulty_band=difficulty_band,
        dc=dc,
        modifier=check_input.modifier,
        roll=roll,
        total=total,
        margin=margin,
        reason="" if succeeded else "check total is below the difficulty",
    )


def ability_check_input_from_profile(
    abilities: Sequence[Mapping[str, object]] | Mapping[str, Mapping[str, object]] | None,
    ability_id: str,
    *,
    physical: PhysicalRequirements | None = None,
) -> AbilityCheckInput:
    """Read one ability from a projected profile without mutating it.

    Missing abilities intentionally become ``untrained/unknown``.  Duplicate
    IDs or malformed records are rejected because silently choosing one would
    make the outcome dependent on profile ordering.
    """

    requested_id = _non_empty_string(ability_id, "ability_id")
    found: Mapping[str, object] | None = None
    if abilities is None:
        records: Iterable[Mapping[str, object]] = ()
    elif isinstance(abilities, Mapping):
        records = []
        for key, value in abilities.items():
            if not isinstance(value, Mapping):
                raise ValueError("ability profile values must be objects")
            record = dict(value)
            record.setdefault("abilityId", key)
            records.append(record)
    else:
        records = abilities
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("ability profile records must be objects")
        raw_id = record.get("abilityId")
        if raw_id is None:
            continue
        record_id = _non_empty_string(raw_id, "abilityId")
        if record_id != requested_id:
            continue
        if found is not None:
            raise ValueError(f"duplicate abilityId: {requested_id}")
        found = record

    if found is None:
        return AbilityCheckInput(
            ability_id=requested_id,
            physical=physical or PhysicalRequirements(),
        )
    level = found.get("level") or "untrained"
    source_status = found.get("sourceStatus") or "unknown"
    confidence = found.get("confidence")
    return AbilityCheckInput(
        ability_id=requested_id,
        level=level,  # type: ignore[arg-type]
        source_status=source_status,  # type: ignore[arg-type]
        confidence=confidence,  # type: ignore[arg-type]
        physical=physical or PhysicalRequirements(),
    )


__all__ = [
    "ABILITY_LEVEL_MODIFIERS",
    "ABILITY_LEVELS",
    "AbilityCheckInput",
    "AbilityCheckResult",
    "AbilityLevel",
    "AbilitySourceStatus",
    "DIFFICULTY_BANDS",
    "DIFFICULTY_DC",
    "DifficultyBand",
    "KNOWN_SOURCE_STATUSES",
    "MECHANICAL_SOURCE_STATUSES",
    "PhysicalRequirements",
    "PhysicalPurpose",
    "RandomSource",
    "ability_check_input_from_profile",
    "ability_modifier",
    "difficulty_to_dc",
    "physical_block_reason",
    "physical_requirements_from_injuries",
    "resolve_ability_check",
]
