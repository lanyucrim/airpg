"""AI candidate contract for location furniture.

The adapter returns descriptions only. Stable IDs, structure ownership,
one-to-three cardinality and runtime container events are all decided by the
program after validation.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Protocol

from trpg_server.locations.furniture import FURNITURE_KINDS


class FurnitureGenerationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FurnitureStructureRequest:
    structure_id: str
    location_id: str
    location_name: str
    structure_name: str
    purpose: str
    canon_notes: str = ""

    def to_mapping(self) -> dict[str, str]:
        return {
            "structureId": self.structure_id,
            "locationId": self.location_id,
            "locationName": self.location_name,
            "structureName": self.structure_name,
            "purpose": self.purpose,
            "canonNotes": self.canon_notes,
        }


@dataclass(frozen=True, slots=True)
class FurnitureCandidate:
    structure_id: str
    kind: str
    name: str
    description: str
    capacity_weight_grams: int
    capacity_volume_cm3: int
    confidence: float
    basis: tuple[str, ...]

    def validate(self, *, path: str = "candidate") -> None:
        if not self.structure_id or not self.kind or not self.name or not self.description:
            raise FurnitureGenerationError(f"{path} has empty required text")
        if self.kind not in FURNITURE_KINDS:
            raise FurnitureGenerationError(f"{path}.kind is not allowed: {self.kind}")
        if type(self.capacity_weight_grams) is not int or self.capacity_weight_grams <= 0 or self.capacity_weight_grams > 2_000_000:
            raise FurnitureGenerationError(f"{path}.capacityWeightGrams is invalid")
        if type(self.capacity_volume_cm3) is not int or self.capacity_volume_cm3 <= 0 or self.capacity_volume_cm3 > 5_000_000:
            raise FurnitureGenerationError(f"{path}.capacityVolumeCm3 is invalid")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)) or not math.isfinite(float(self.confidence)) or not 0 <= float(self.confidence) <= 1:
            raise FurnitureGenerationError(f"{path}.confidence is invalid")
        if not self.basis or not all(type(value) is str and value.strip() for value in self.basis):
            raise FurnitureGenerationError(f"{path}.basis must contain evidence")


@dataclass(frozen=True, slots=True)
class FurnitureAdapterResult:
    output: Mapping[str, Any]
    metrics: Any = None


class FurnitureGenerationAdapter(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def generate(
        self,
        structures: tuple[FurnitureStructureRequest, ...],
    ) -> FurnitureAdapterResult: ...


def resolve_furniture_candidates(
    adapter: FurnitureGenerationAdapter,
    structures: tuple[FurnitureStructureRequest, ...],
) -> tuple[FurnitureCandidate, ...]:
    if not structures:
        return ()
    if len(structures) > 24:
        raise FurnitureGenerationError("a furniture AI request may contain at most 24 structures")
    expected = {value.structure_id for value in structures}
    if len(expected) != len(structures):
        raise FurnitureGenerationError("furniture structures must have unique ids")
    result = adapter.generate(structures)
    raw_structures = result.output.get("structures") if isinstance(result.output, Mapping) else None
    if result.output.get("schemaVersion") != 1 or not isinstance(raw_structures, list):
        raise FurnitureGenerationError("furniture AI output schema is invalid")
    seen: set[str] = set()
    candidates: list[FurnitureCandidate] = []
    for index, raw in enumerate(raw_structures):
        if not isinstance(raw, Mapping):
            raise FurnitureGenerationError(f"structures[{index}] must be an object")
        structure_id = raw.get("structureId")
        if structure_id not in expected or structure_id in seen:
            raise FurnitureGenerationError("AI returned unknown or duplicate structureId")
        seen.add(structure_id)
        furniture = raw.get("furniture")
        if not isinstance(furniture, list) or not 1 <= len(furniture) <= 3:
            raise FurnitureGenerationError(f"{structure_id} must contain 1-3 furniture candidates")
        for candidate_index, value in enumerate(furniture):
            if not isinstance(value, Mapping):
                raise FurnitureGenerationError(f"{structure_id}.furniture[{candidate_index}] must be an object")
            candidate = FurnitureCandidate(
                structure_id=structure_id,
                kind=value.get("kind", ""),
                name=value.get("name", ""),
                description=value.get("description", ""),
                capacity_weight_grams=value.get("capacityWeightGrams", 0),
                capacity_volume_cm3=value.get("capacityVolumeCm3", 0),
                confidence=value.get("confidence", 0),
                basis=tuple(value.get("basis", ())),
            )
            candidate.validate(path=f"{structure_id}.furniture[{candidate_index}]")
            candidates.append(candidate)
    if seen != expected:
        missing = sorted(expected - seen)
        raise FurnitureGenerationError("AI omitted structures: " + ", ".join(missing[:8]))
    return tuple(candidates)


__all__ = [
    "FurnitureAdapterResult",
    "FurnitureCandidate",
    "FurnitureGenerationAdapter",
    "FurnitureGenerationError",
    "FurnitureStructureRequest",
    "resolve_furniture_candidates",
]
