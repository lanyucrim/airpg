"""Validated AI candidates for an item's initial durability.

The model classifies prose and proposes bounded ratios.  This module performs
all structural and numeric validation and converts the ratios into the float
profile accepted by the item contract.  It never creates an item, submits an
event, or changes a catalog.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
from typing import Any, Literal, Protocol

from trpg_server.items.ai_items.references import ReferenceCallMetrics
from trpg_server.items.durability import (
    ConditionGrade,
    DurabilityError,
    DurabilityKind,
    DurabilityProfile,
    durability_kind_for_item,
    profile_from_creation_ratios,
)


INITIAL_DURABILITY_SCHEMA_VERSION = 1
MINIMUM_DURABILITY_CONFIDENCE = 0.65
DurabilityClassification = Literal["tool", "clothing", "equipment", "none"]

_CANDIDATE_FIELDS = frozenset(
    {
        "schemaVersion",
        "durabilityKind",
        "condition",
        "conditionGrade",
        "relativeMaximum",
        "remainingRatio",
        "confidence",
        "basis",
    }
)


class InitialDurabilityError(ValueError):
    """Raised when an initial-durability request or candidate is invalid."""


@dataclass(frozen=True, slots=True)
class InitialDurabilityRequest:
    name: str
    description: str
    category: str | None
    properties: Mapping[str, Any] = field(default_factory=dict)
    category_locked: bool = True

    def __post_init__(self) -> None:
        name = _text(self.name, "name", maximum=100)
        description = _text(self.description, "description", maximum=500)
        if self.category is not None:
            category = _text(self.category, "category", maximum=80)
        else:
            category = None
        if self.category_locked and category is None:
            raise InitialDurabilityError(
                "a locked initial-durability request requires category"
            )
        if not isinstance(self.properties, Mapping):
            raise InitialDurabilityError("properties must be an object")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "properties", dict(self.properties))

    @property
    def expected_kind(self) -> DurabilityKind | None:
        if self.category is None:
            return None
        return durability_kind_for_item(self.category, self.properties)

    @property
    def explicitly_consumable(self) -> bool:
        return "consumable" in self.properties


@dataclass(frozen=True, slots=True)
class InitialDurabilityAdapterResult:
    output: Mapping[str, Any]
    metrics: ReferenceCallMetrics = ReferenceCallMetrics()


class InitialDurabilityAdapter(Protocol):
    @property
    def available(self) -> bool: ...

    def assess(
        self,
        request: InitialDurabilityRequest,
    ) -> InitialDurabilityAdapterResult: ...


@dataclass(frozen=True, slots=True)
class InitialDurabilityCandidate:
    durability_kind: DurabilityClassification
    condition: str | None
    condition_grade: ConditionGrade | None
    relative_maximum: float | None
    remaining_ratio: float | None
    confidence: float
    basis: tuple[str, ...]

    @classmethod
    def from_output(
        cls,
        output: Mapping[str, Any],
        *,
        minimum_confidence: float = MINIMUM_DURABILITY_CONFIDENCE,
    ) -> "InitialDurabilityCandidate":
        if not isinstance(output, Mapping) or set(output) != _CANDIDATE_FIELDS:
            raise InitialDurabilityError(
                "model output fields do not match the initial durability contract"
            )
        if output["schemaVersion"] != INITIAL_DURABILITY_SCHEMA_VERSION:
            raise InitialDurabilityError(
                "unsupported initial durability candidate schemaVersion"
            )
        kind = output["durabilityKind"]
        if kind not in {"tool", "clothing", "equipment", "none"}:
            raise InitialDurabilityError("durabilityKind is invalid")
        confidence = _confidence(output["confidence"])
        if confidence < minimum_confidence:
            raise InitialDurabilityError(
                f"confidence is below the acceptance threshold {minimum_confidence}"
            )
        basis = _basis(output["basis"])
        if kind == "none":
            if any(
                output[key] is not None
                for key in (
                    "condition",
                    "conditionGrade",
                    "relativeMaximum",
                    "remainingRatio",
                )
            ):
                raise InitialDurabilityError(
                    "a non-durable candidate must leave durability values null"
                )
            return cls(
                durability_kind="none",
                condition=None,
                condition_grade=None,
                relative_maximum=None,
                remaining_ratio=None,
                confidence=confidence,
                basis=basis,
            )

        condition = _text(output["condition"], "condition", maximum=40)
        grade = output["conditionGrade"]
        if grade not in {"new", "good", "worn", "poor", "broken"}:
            raise InitialDurabilityError("conditionGrade is invalid")
        relative = _number(output["relativeMaximum"], "relativeMaximum")
        remaining = _number(output["remainingRatio"], "remainingRatio")
        try:
            profile_from_creation_ratios(
                kind=kind,
                condition=condition,
                condition_grade=grade,
                relative_maximum=relative,
                remaining_ratio=remaining,
            )
        except DurabilityError as error:
            raise InitialDurabilityError(str(error)) from error
        return cls(
            durability_kind=kind,
            condition=condition,
            condition_grade=grade,
            relative_maximum=relative,
            remaining_ratio=remaining,
            confidence=confidence,
            basis=basis,
        )

    def to_profile(self) -> DurabilityProfile | None:
        if self.durability_kind == "none":
            return None
        assert self.condition is not None
        assert self.condition_grade is not None
        assert self.relative_maximum is not None
        assert self.remaining_ratio is not None
        return profile_from_creation_ratios(
            kind=self.durability_kind,
            condition=self.condition,
            condition_grade=self.condition_grade,
            relative_maximum=self.relative_maximum,
            remaining_ratio=self.remaining_ratio,
        )


@dataclass(frozen=True, slots=True)
class InitialDurabilityResolution:
    status: str
    durability_kind: DurabilityClassification | None
    condition: str | None
    durability: Mapping[str, float] | None
    candidate: InitialDurabilityCandidate | None = None
    reason: str | None = None
    adapter_called: bool = False


def resolve_initial_durability(
    request: InitialDurabilityRequest,
    adapter: InitialDurabilityAdapter | None = None,
    *,
    minimum_confidence: float = MINIMUM_DURABILITY_CONFIDENCE,
) -> InitialDurabilityResolution:
    """Resolve one bounded initial profile, making at most one AI call.

    Locked categories are authoritative item data.  AI may assess their
    condition but cannot silently reclassify them.  An unlocked request can
    use the candidate kind as a classification proposal for a new definition.
    """

    expected_kind = request.expected_kind
    if request.explicitly_consumable or (
        request.category_locked and expected_kind is None
    ):
        return InitialDurabilityResolution(
            status="not_applicable",
            durability_kind=None,
            condition=None,
            durability=None,
            reason="item is not eligible for durability",
        )
    if adapter is None or not adapter.available:
        return InitialDurabilityResolution(
            status="adapter_unavailable",
            durability_kind=expected_kind,
            condition=None,
            durability=None,
            reason="no enabled initial durability adapter",
        )
    try:
        result = adapter.assess(request)
        candidate = InitialDurabilityCandidate.from_output(
            result.output,
            minimum_confidence=minimum_confidence,
        )
        if request.category_locked and candidate.durability_kind != expected_kind:
            raise InitialDurabilityError(
                "candidate durabilityKind conflicts with the locked item category"
            )
        profile = candidate.to_profile()
    except Exception as error:
        return InitialDurabilityResolution(
            status="rejected",
            durability_kind=expected_kind,
            condition=None,
            durability=None,
            reason=f"{type(error).__name__}: {error}",
            adapter_called=True,
        )
    if candidate.durability_kind == "none":
        return InitialDurabilityResolution(
            status="not_applicable",
            durability_kind="none",
            condition=None,
            durability=None,
            candidate=candidate,
            adapter_called=True,
        )
    assert profile is not None
    return InitialDurabilityResolution(
        status="model_accepted",
        durability_kind=candidate.durability_kind,
        condition=candidate.condition,
        durability=profile.to_mapping(),
        candidate=candidate,
        adapter_called=True,
    )


def _text(value: object, label: str, *, maximum: int) -> str:
    if type(value) is not str or not value.strip() or len(value.strip()) > maximum:
        raise InitialDurabilityError(
            f"{label} must be a non-empty string of at most {maximum} characters"
        )
    return value.strip()


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InitialDurabilityError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise InitialDurabilityError(f"{label} must be a finite number")
    return result


def _confidence(value: object) -> float:
    result = _number(value, "confidence")
    if not 0 <= result <= 1:
        raise InitialDurabilityError("confidence must be between 0 and 1")
    return result


def _basis(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 6:
        raise InitialDurabilityError("basis must contain between 1 and 6 strings")
    result = tuple(_text(item, "basis entry", maximum=160) for item in value)
    if len(set(result)) != len(result):
        raise InitialDurabilityError("basis entries must be unique")
    return result


__all__ = [
    "INITIAL_DURABILITY_SCHEMA_VERSION",
    "MINIMUM_DURABILITY_CONFIDENCE",
    "DurabilityClassification",
    "InitialDurabilityAdapter",
    "InitialDurabilityAdapterResult",
    "InitialDurabilityCandidate",
    "InitialDurabilityError",
    "InitialDurabilityRequest",
    "InitialDurabilityResolution",
    "resolve_initial_durability",
]
