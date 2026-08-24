"""Location, movement, furniture and environmental opportunity domain."""

from .furniture import (
    FURNITURE_ATLAS_ID,
    FURNITURE_KINDS,
    FurnitureAtlas,
    FurnitureAtlasError,
    FurnitureRecord,
    load_furniture_atlas,
)

__all__ = [
    "FURNITURE_ATLAS_ID",
    "FURNITURE_KINDS",
    "FurnitureAtlas",
    "FurnitureAtlasError",
    "FurnitureRecord",
    "load_furniture_atlas",
]
