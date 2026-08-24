"""Static furniture facts attached to executable location structures.

Furniture is authored as a location concern and becomes an ``items`` domain
container only at bootstrap.  This module never creates runtime state and
never treats an AI suggestion as canonical until the complete atlas passes
validation.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping

from trpg_server.map.atlas import GRAY_HARBOR_ATLAS_PATH, MapAtlas, load_map_atlas


FURNITURE_SCHEMA_VERSION = 1
FURNITURE_ATLAS_ID = "gray-harbor-furniture-atlas"
FURNITURE_KINDS = frozenset(
    {
        "bar_counter",
        "bottle_cabinet",
        "serving_sideboard",
        "cabinet",
        "utility_cabinet",
        "wall_cabinet",
        "wardrobe",
        "bedside_table",
        "drawer_chest",
        "lockbox",
        "drawer_desk",
        "key_drawer",
        "bookcase",
        "map_case",
        "medicine_cabinet",
        "apothecary_counter",
        "instrument_cabinet",
        "equipment_cabinet",
        "pantry",
        "cupboard",
        "under_counter_cabinet",
        "stock_cabinet",
        "locker",
        "chest",
        "donation_chest",
        "vestment_cabinet",
        "cashbox",
        "cash_drawer",
        "document_cabinet",
        "archive_cabinet",
        "parts_cabinet",
        "tool_chest",
        "material_bin",
        "equipment_case",
        "display_case",
        "coat_cabinet",
        "parcel_cabinet",
        "linen_cabinet",
        "grill",
        "laundry_basket",
        "weatherproof_cabinet",
        "wood_bin",
        "waste_bin",
    }
)
# Every generated furniture record is a container.  Keeping this explicit
# prevents a decorative table, bench, or open shelf from silently becoming a
# storage location later.
STORAGE_FURNITURE_KINDS = FURNITURE_KINDS
FURNITURE_SOURCE_STATUSES = frozenset({"model_generated", "reviewed", "program_seeded"})
_ID = re.compile(r"^[a-z0-9](?:[a-z0-9_]*[a-z0-9])?$")
_MAX_TEXT = 240
_MIN_CAPACITY = 100
_MAX_WEIGHT = 2_000_000
_MAX_VOLUME = 5_000_000


class FurnitureAtlasError(ValueError):
    """Raised when a furniture atlas cannot be safely used."""


@dataclass(frozen=True, slots=True)
class FurnitureRecord:
    furniture_id: str
    location_id: str
    structure_id: str
    kind: str
    name: str
    description: str
    capacity_weight_grams: int
    capacity_volume_cm3: int
    fixed: bool = True
    visible: bool = True
    source_status: str = "model_generated"
    confidence: float = 0.0
    basis: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    model_audit: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, path: str = "furniture") -> "FurnitureRecord":
        allowed = {
            "furnitureId", "locationId", "structureId", "kind", "name",
            "description", "capacityWeightGrams", "capacityVolumeCm3",
            "fixed", "visible", "sourceStatus", "confidence", "basis",
            "sourceRefs", "modelAudit",
        }
        extra = set(value).difference(allowed)
        missing = {"furnitureId", "locationId", "structureId", "kind", "name", "description", "capacityWeightGrams", "capacityVolumeCm3"}.difference(value)
        if missing or extra:
            details = []
            if missing:
                details.append(f"missing {sorted(missing)}")
            if extra:
                details.append(f"unknown {sorted(extra)}")
            raise FurnitureAtlasError(f"{path}: " + "; ".join(details))
        record = cls(
            furniture_id=value["furnitureId"],
            location_id=value["locationId"],
            structure_id=value["structureId"],
            kind=value["kind"],
            name=value["name"],
            description=value["description"],
            capacity_weight_grams=value["capacityWeightGrams"],
            capacity_volume_cm3=value["capacityVolumeCm3"],
            fixed=value.get("fixed", True),
            visible=value.get("visible", True),
            source_status=value.get("sourceStatus", "model_generated"),
            confidence=value.get("confidence", 0.0),
            basis=tuple(value.get("basis", ())),
            source_refs=tuple(value.get("sourceRefs", ())),
            model_audit=value.get("modelAudit"),
        )
        record.validate(path=path)
        return record

    def validate(self, *, path: str = "furniture") -> None:
        for label, value in (
            ("furnitureId", self.furniture_id),
            ("locationId", self.location_id),
            ("structureId", self.structure_id),
            ("kind", self.kind),
            ("name", self.name),
            ("description", self.description),
        ):
            if type(value) is not str or not value.strip():
                raise FurnitureAtlasError(f"{path}.{label} must be a non-empty string")
        for label, value in (("furnitureId", self.furniture_id), ("locationId", self.location_id), ("structureId", self.structure_id)):
            if not _ID.fullmatch(value):
                raise FurnitureAtlasError(f"{path}.{label} has an invalid id")
        if self.kind not in STORAGE_FURNITURE_KINDS:
            raise FurnitureAtlasError(f"{path}.kind is not an allowed furniture kind: {self.kind}")
        if len(self.name) > _MAX_TEXT or len(self.description) > _MAX_TEXT:
            raise FurnitureAtlasError(f"{path} text is too long")
        if type(self.capacity_weight_grams) is not int or not _MIN_CAPACITY <= self.capacity_weight_grams <= _MAX_WEIGHT:
            raise FurnitureAtlasError(f"{path}.capacityWeightGrams is outside supported bounds")
        if type(self.capacity_volume_cm3) is not int or not _MIN_CAPACITY <= self.capacity_volume_cm3 <= _MAX_VOLUME:
            raise FurnitureAtlasError(f"{path}.capacityVolumeCm3 is outside supported bounds")
        if self.fixed is not True or type(self.visible) is not bool:
            raise FurnitureAtlasError(f"{path} must be fixed and visible must be boolean")
        if self.source_status not in FURNITURE_SOURCE_STATUSES:
            raise FurnitureAtlasError(f"{path}.sourceStatus is invalid")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)) or not 0 <= float(self.confidence) <= 1:
            raise FurnitureAtlasError(f"{path}.confidence must be between 0 and 1")
        if not all(type(value) is str and value.strip() for value in (*self.basis, *self.source_refs)):
            raise FurnitureAtlasError(f"{path}.basis/sourceRefs must contain non-empty strings")
        if self.model_audit is not None and not isinstance(self.model_audit, Mapping):
            raise FurnitureAtlasError(f"{path}.modelAudit must be an object")

    def to_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "furnitureId": self.furniture_id,
            "locationId": self.location_id,
            "structureId": self.structure_id,
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "capacityWeightGrams": self.capacity_weight_grams,
            "capacityVolumeCm3": self.capacity_volume_cm3,
            "fixed": self.fixed,
            "visible": self.visible,
            "sourceStatus": self.source_status,
            "confidence": self.confidence,
            "basis": list(self.basis),
            "sourceRefs": list(self.source_refs),
        }
        if self.model_audit is not None:
            result["modelAudit"] = dict(self.model_audit)
        return result


@dataclass(frozen=True, slots=True)
class FurnitureAtlas:
    atlas_id: str
    location_atlas_id: str
    records: tuple[FurnitureRecord, ...]

    def validate(self, map_atlas: MapAtlas) -> None:
        if self.atlas_id != FURNITURE_ATLAS_ID:
            raise FurnitureAtlasError(f"unexpected furniture atlas id: {self.atlas_id}")
        structures: dict[str, tuple[str, str]] = {}
        for location in map_atlas.locations:
            if location.kind in {"city", "street", "district"}:
                if location.structure:
                    raise FurnitureAtlasError(f"non-place location has structures: {location.id}")
                continue
            for node in location.structure:
                if node.exists:
                    structures[node.id] = (location.id, location.kind)
        counts: dict[str, int] = {}
        ids: set[str] = set()
        for index, record in enumerate(self.records):
            record.validate(path=f"furniture[{index}]")
            if record.furniture_id in ids:
                raise FurnitureAtlasError(f"duplicate furniture id: {record.furniture_id}")
            ids.add(record.furniture_id)
            parent = structures.get(record.structure_id)
            if parent is None:
                raise FurnitureAtlasError(f"unknown internal structure: {record.structure_id}")
            if parent[0] != record.location_id:
                raise FurnitureAtlasError(f"furniture location does not match structure: {record.furniture_id}")
            counts[record.structure_id] = counts.get(record.structure_id, 0) + 1
        missing = sorted(structure_id for structure_id in structures if counts.get(structure_id, 0) == 0)
        invalid = sorted(structure_id for structure_id, count in counts.items() if count < 1 or count > 3)
        if missing:
            raise FurnitureAtlasError("structures without furniture: " + ", ".join(missing[:8]))
        if invalid:
            raise FurnitureAtlasError("structures must have 1-3 furniture records: " + ", ".join(invalid[:8]))

    def for_structure(self, structure_id: str) -> tuple[FurnitureRecord, ...]:
        return tuple(value for value in self.records if value.structure_id == structure_id)


def load_furniture_atlas(path: Path, *, map_atlas: MapAtlas | None = None) -> FurnitureAtlas:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FurnitureAtlasError(f"cannot read furniture atlas: {path}") from error
    if not isinstance(raw, Mapping):
        raise FurnitureAtlasError("furniture atlas root must be an object")
    if raw.get("schemaVersion") != FURNITURE_SCHEMA_VERSION:
        raise FurnitureAtlasError("unsupported furniture atlas schemaVersion")
    records_raw = raw.get("furniture")
    if not isinstance(records_raw, list):
        raise FurnitureAtlasError("furniture atlas furniture must be an array")
    atlas = FurnitureAtlas(
        atlas_id=raw.get("atlasId", ""),
        location_atlas_id=raw.get("locationAtlasId", ""),
        records=tuple(FurnitureRecord.from_mapping(value, path=f"furniture[{index}]") for index, value in enumerate(records_raw) if isinstance(value, Mapping)),
    )
    if len(atlas.records) != len(records_raw):
        raise FurnitureAtlasError("every furniture entry must be an object")
    atlas.validate(map_atlas or load_map_atlas(GRAY_HARBOR_ATLAS_PATH))
    return atlas


__all__ = [
    "FURNITURE_ATLAS_ID",
    "FURNITURE_KINDS",
    "STORAGE_FURNITURE_KINDS",
    "FURNITURE_SCHEMA_VERSION",
    "FurnitureAtlas",
    "FurnitureAtlasError",
    "FurnitureRecord",
    "load_furniture_atlas",
]
