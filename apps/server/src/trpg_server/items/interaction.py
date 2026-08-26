"""Small, provider-neutral contracts for cross-domain item interactions.

The item domain owns the shape of an interaction request and the validation of
the model's *physical* candidate.  It does not decide a character's roll or
write events.  The behavior layer composes these contracts with character and
location rules and submits the resulting event plan through the core service.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import math
import re
from typing import Any, Literal, Protocol


INTERACTION_SCHEMA_VERSION = 1
InteractionTargetKind = Literal["item", "furniture", "location"]
InteractionOperation = Literal["combine", "apply", "store", "retrieve"]
InteractionDecision = Literal["possible", "impossible", "clarify"]
ToolFit = Literal["none", "weak", "plausible", "strong"]
DifficultyBand = Literal["trivial", "routine", "demanding", "hard", "extreme"]

TARGET_KINDS = frozenset({"item", "furniture", "location"})
OPERATIONS = frozenset({"combine", "apply", "store", "retrieve"})
DECISIONS = frozenset({"possible", "impossible", "clarify"})
TOOL_FITS = frozenset({"none", "weak", "plausible", "strong"})
DIFFICULTY_BANDS = frozenset(
    {"trivial", "routine", "demanding", "hard", "extreme"}
)


class ItemInteractionError(ValueError):
    """Raised when an interaction contract is malformed or unsafe."""


def _text(value: object, field_name: str, *, maximum: int = 500) -> str:
    if type(value) is not str or not value.strip() or len(value.strip()) > maximum:
        raise ItemInteractionError(
            f"{field_name} must be a non-empty string of at most {maximum} characters"
        )
    return value.strip()


def _ids(value: object, field_name: str, *, maximum: int = 8) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= maximum:
        raise ItemInteractionError(
            f"{field_name} must contain between 1 and {maximum} ids"
        )
    result = tuple(_text(item, field_name, maximum=120) for item in value)
    if len(result) != len(set(result)):
        raise ItemInteractionError(f"{field_name} must not contain duplicates")
    return result


def _string_list(value: object, field_name: str, *, maximum: int = 8) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise ItemInteractionError(f"{field_name} must be an array of at most {maximum} strings")
    result = tuple(_text(item, field_name, maximum=300) for item in value)
    if len(result) != len(set(result)):
        raise ItemInteractionError(f"{field_name} must not contain duplicates")
    return result


@dataclass(frozen=True, slots=True)
class InteractionRequest:
    """A neutral request built from player text and authoritative state."""

    actor_id: str
    source_item_ids: tuple[str, ...]
    target_kind: InteractionTargetKind
    target_id: str
    operation: InteractionOperation
    action_text: str
    required_ability_id: str | None = None
    requested_effect_kind: str | None = None
    quantity: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor_id", _text(self.actor_id, "actor_id", maximum=120))
        object.__setattr__(self, "source_item_ids", _ids(self.source_item_ids, "source_item_ids"))
        if self.target_kind not in TARGET_KINDS:
            raise ItemInteractionError(f"unknown target_kind: {self.target_kind}")
        object.__setattr__(self, "target_id", _text(self.target_id, "target_id", maximum=160))
        if self.operation not in OPERATIONS:
            raise ItemInteractionError(f"unknown operation: {self.operation}")
        object.__setattr__(self, "action_text", _text(self.action_text, "action_text"))
        if self.required_ability_id is not None:
            object.__setattr__(
                self,
                "required_ability_id",
                _text(self.required_ability_id, "required_ability_id", maximum=120),
            )
        if self.requested_effect_kind is not None:
            object.__setattr__(
                self,
                "requested_effect_kind",
                _text(self.requested_effect_kind, "requested_effect_kind", maximum=120),
            )
        if type(self.quantity) is not int or self.quantity < 1:
            raise ItemInteractionError("quantity must be a positive integer")
        if self.operation == "combine":
            if self.target_kind != "item":
                raise ItemInteractionError("combine interactions require an item target")
            if len(self.source_item_ids) < 2:
                raise ItemInteractionError("combine interactions require at least two item instances")
        elif self.operation in {"store", "retrieve"}:
            if self.target_kind != "furniture":
                raise ItemInteractionError("store/retrieve interactions require a furniture target")
            if len(self.source_item_ids) != 1:
                raise ItemInteractionError("store/retrieve interactions require exactly one item instance")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schemaVersion": INTERACTION_SCHEMA_VERSION,
            "actorId": self.actor_id,
            "sourceItemIds": list(self.source_item_ids),
            "targetKind": self.target_kind,
            "targetId": self.target_id,
            "operation": self.operation,
            "actionText": self.action_text,
            "requiredAbilityId": self.required_ability_id,
            "requestedEffectKind": self.requested_effect_kind,
            "quantity": self.quantity,
        }


@dataclass(frozen=True, slots=True)
class ItemInteractionCandidate:
    """AI's bounded physical proposal; it contains no outcome or events."""

    decision: InteractionDecision
    operation: InteractionOperation
    required_ability_ids: tuple[str, ...]
    tool_fit: ToolFit
    difficulty_band: DifficultyBand
    physical_basis: tuple[str, ...]
    missing_facts: tuple[str, ...]
    risk_hints: tuple[str, ...]
    confidence: float
    effect_kind: str | None = None
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise ItemInteractionError("candidate decision is invalid")
        if self.operation not in OPERATIONS:
            raise ItemInteractionError("candidate operation is invalid")
        object.__setattr__(
            self, "required_ability_ids", _string_list(self.required_ability_ids, "required_ability_ids")
        )
        if self.tool_fit not in TOOL_FITS:
            raise ItemInteractionError("candidate tool_fit is invalid")
        if self.difficulty_band not in DIFFICULTY_BANDS:
            raise ItemInteractionError("candidate difficulty_band is invalid")
        object.__setattr__(
            self, "physical_basis", _string_list(self.physical_basis, "physical_basis", maximum=12)
        )
        object.__setattr__(
            self, "missing_facts", _string_list(self.missing_facts, "missing_facts", maximum=8)
        )
        object.__setattr__(
            self, "risk_hints", _string_list(self.risk_hints, "risk_hints", maximum=8)
        )
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ItemInteractionError("candidate confidence must be numeric")
        confidence = float(self.confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ItemInteractionError("candidate confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", confidence)
        if self.effect_kind is not None:
            object.__setattr__(self, "effect_kind", _text(self.effect_kind, "effect_kind", maximum=120))
        if self.rejection_reason is not None:
            object.__setattr__(self, "rejection_reason", _text(self.rejection_reason, "rejection_reason", maximum=300))
        if self.decision == "possible":
            if not self.physical_basis:
                raise ItemInteractionError("possible candidate requires physical_basis")
            if self.missing_facts:
                raise ItemInteractionError("possible candidate cannot have missing_facts")
            if self.rejection_reason is not None:
                raise ItemInteractionError("possible candidate cannot have rejection_reason")
        else:
            if not self.rejection_reason and not self.missing_facts:
                raise ItemInteractionError(
                    "impossible or clarify candidate requires a reason or missing fact"
                )

    @classmethod
    def from_output(
        cls,
        output: Mapping[str, Any],
        request: InteractionRequest,
        *,
        allowed_ability_ids: Sequence[str] = (),
        minimum_confidence: float = 0.65,
    ) -> "ItemInteractionCandidate":
        if type(minimum_confidence) not in {int, float} or not 0 <= minimum_confidence <= 1:
            raise ItemInteractionError("minimum_confidence must be between 0 and 1")
        expected = {
            "schemaVersion",
            "decision",
            "operation",
            "requiredAbilityIds",
            "toolFit",
            "difficultyBand",
            "physicalBasis",
            "missingFacts",
            "riskHints",
            "confidence",
            "effectKind",
            "rejectionReason",
        }
        if not isinstance(output, Mapping) or set(output) != expected:
            raise ItemInteractionError("interaction candidate fields do not match the contract")
        if output["schemaVersion"] != INTERACTION_SCHEMA_VERSION:
            raise ItemInteractionError("unsupported interaction candidate schemaVersion")
        candidate = cls(
            decision=output["decision"],  # type: ignore[arg-type]
            operation=output["operation"],  # type: ignore[arg-type]
            required_ability_ids=tuple(output["requiredAbilityIds"]),  # type: ignore[arg-type]
            tool_fit=output["toolFit"],  # type: ignore[arg-type]
            difficulty_band=output["difficultyBand"],  # type: ignore[arg-type]
            physical_basis=tuple(output["physicalBasis"]),  # type: ignore[arg-type]
            missing_facts=tuple(output["missingFacts"]),  # type: ignore[arg-type]
            risk_hints=tuple(output["riskHints"]),  # type: ignore[arg-type]
            confidence=output["confidence"],  # type: ignore[arg-type]
            effect_kind=output["effectKind"],  # type: ignore[arg-type]
            rejection_reason=output["rejectionReason"],  # type: ignore[arg-type]
        )
        if candidate.confidence < minimum_confidence:
            raise ItemInteractionError("interaction candidate confidence is too low")
        allowed = set(allowed_ability_ids)
        if any(value not in allowed for value in candidate.required_ability_ids):
            raise ItemInteractionError("candidate references an unknown ability")
        if candidate.operation != request.operation:
            raise ItemInteractionError("candidate changed the requested operation")
        if request.required_ability_id is not None and (
            request.required_ability_id not in candidate.required_ability_ids
        ):
            raise ItemInteractionError("candidate omitted the requested ability")
        if candidate.decision == "possible" and candidate.tool_fit == "none":
            raise ItemInteractionError("possible candidate cannot have tool_fit=none")
        return candidate

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schemaVersion": INTERACTION_SCHEMA_VERSION,
            "decision": self.decision,
            "operation": self.operation,
            "requiredAbilityIds": list(self.required_ability_ids),
            "toolFit": self.tool_fit,
            "difficultyBand": self.difficulty_band,
            "physicalBasis": list(self.physical_basis),
            "missingFacts": list(self.missing_facts),
            "riskHints": list(self.risk_hints),
            "confidence": self.confidence,
            "effectKind": self.effect_kind,
            "rejectionReason": self.rejection_reason,
        }


@dataclass(frozen=True, slots=True)
class ItemInteractionAdapterResult:
    output: Mapping[str, Any]
    provider_name: str | None = None
    model_name: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None


class ItemInteractionAdapter(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def provider_name(self) -> str | None: ...

    @property
    def model_name(self) -> str | None: ...

    def assess(
        self,
        request: InteractionRequest,
        source_summaries: tuple[Mapping[str, Any], ...],
        target_summary: Mapping[str, Any],
    ) -> ItemInteractionAdapterResult | Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class DisabledItemInteractionAdapter:
    """Safe default used when no model is configured."""

    available: bool = False
    provider_name: str | None = None
    model_name: str | None = None

    def assess(
        self,
        request: InteractionRequest,
        source_summaries: tuple[Mapping[str, Any], ...],
        target_summary: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        del request, source_summaries, target_summary
        raise RuntimeError("item interaction model is disabled")


def parse_interaction_candidate(
    output: Mapping[str, Any],
    request: InteractionRequest,
    *,
    allowed_ability_ids: Sequence[str] = (),
    minimum_confidence: float = 0.65,
) -> ItemInteractionCandidate:
    """Validate one model proposal without performing any state change."""

    return ItemInteractionCandidate.from_output(
        output,
        request,
        allowed_ability_ids=allowed_ability_ids,
        minimum_confidence=minimum_confidence,
    )


def validate_candidate_evidence(
    candidate: ItemInteractionCandidate,
    source_summaries: Sequence[Mapping[str, Any]],
    target_summary: Mapping[str, Any],
) -> None:
    """Require the model's physical basis to touch observable facts.

    This is intentionally a conservative evidence gate, not a semantic
    replacement for a domain resolver.  It matches the candidate against
    concrete observable fields (names, descriptions and physical properties),
    while refusing a bare category word such as ``tool`` or ``家具`` as proof.
    A candidate can still describe an open-ended action, but it cannot justify
    it solely with facts absent from the supplied summaries.
    """

    summaries = (*source_summaries, target_summary)
    generic_terms = frozenset(
        {
            "tool",
            "item",
            "location",
            "room",
            "furniture",
            "container",
            "工具",
            "物品",
            "地点",
            "房间",
            "家具",
            "容器",
        }
    )
    fact_text = " ".join(
        str(summary.get(key, ""))
        for summary in summaries
        if isinstance(summary, Mapping)
        for key in (
            "name",
            "description",
            "material",
            "materials",
            "structure",
            "sizeDescription",
            "observableFeatures",
            "furnitureName",
            "furnitureDescription",
        )
    ).casefold()
    for basis in candidate.physical_basis:
        normalized = basis.casefold()
        ascii_tokens = {
            token
            for token in re.findall(r"[a-z0-9_]{3,}", normalized)
            if token not in generic_terms
        }
        if ascii_tokens and any(token in fact_text for token in ascii_tokens):
            continue
        cjk_fragments = {
            normalized[index : index + 2]
            for index in range(max(0, len(normalized) - 1))
            if all(
                "\u3400" <= char <= "\u9fff"
                for char in normalized[index : index + 2]
            )
            and normalized[index : index + 2] not in generic_terms
        }
        if cjk_fragments and any(fragment in fact_text for fragment in cjk_fragments):
            continue
        if not ascii_tokens and not cjk_fragments:
            raise ItemInteractionError("physical_basis must contain observable evidence")
        raise ItemInteractionError("physical_basis is not grounded in observable summaries")


def json_like_text(values: Sequence[Mapping[str, Any]]) -> str:
    """Serialize bounded summaries for the evidence gate without leaking state."""

    return " ".join(
        str(value)
        for value in values
        if isinstance(value, Mapping)
    ).casefold()


__all__ = [
    "DECISIONS",
    "DIFFICULTY_BANDS",
    "DisabledItemInteractionAdapter",
    "INTERACTION_SCHEMA_VERSION",
    "ItemInteractionAdapter",
    "ItemInteractionAdapterResult",
    "ItemInteractionCandidate",
    "ItemInteractionError",
    "InteractionRequest",
    "OPERATIONS",
    "TARGET_KINDS",
    "TOOL_FITS",
    "parse_interaction_candidate",
    "validate_candidate_evidence",
]
