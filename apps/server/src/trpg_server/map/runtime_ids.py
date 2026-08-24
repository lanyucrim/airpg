"""Canonical runtime identifiers for the authored location atlas.

The atlas uses stable content identifiers while bootstrap events use runtime
identifiers.  Keeping the translation here prevents the compiler, public map,
traversal and furniture projection from inventing slightly different maps.

The two package-authored names ``oak_street`` and ``red_mill_tavern`` remain
canonical runtime ids because they are part of the published Gray Harbor
opening package.  Stale names that were never emitted, such as
``runtime_oak_street``, are deliberately not accepted as compatibility ids.
"""

from __future__ import annotations

import re


_LOCATION_OVERRIDES = {
    "loc_5_1_1": "white_heron_house",
    "loc_5_1_8": "abandoned_bakery",
}

_STRUCTURE_OVERRIDES = {
    "loc_5_1_1__1": "white_heron_ground_floor",
    "loc_5_1_1__2": "white_heron_kitchen",
    "loc_5_1_1__3": "white_heron_second_floor",
    "loc_5_1_1__4": "white_heron_third_floor",
    "loc_5_1_1__5": "white_heron_cellar",
    "loc_5_1_1__6": "white_heron_backyard",
}
_STRUCTURE_RUNTIME_TO_ATLAS = {
    runtime_id: atlas_id
    for atlas_id, runtime_id in _STRUCTURE_OVERRIDES.items()
}

# These ids are authored in locations.json and therefore remain stable public
# runtime ids.  All atlas-only streets use the ``atlas_street_<id>`` form.
_STREET_OVERRIDES = {"candle_oak": "oak_street"}

_SPECIAL_RUNTIME_TO_ATLAS = {
    "gray_harbor": "runtime_gray_harbor",
    "candle_ward": "runtime_candle_ward",
    "oak_street": "candle_oak",
    "red_mill_tavern": "runtime_red_mill_tavern",
    "white_heron_house": "loc_5_1_1",
    "abandoned_bakery": "loc_5_1_8",
}


def runtime_location_id(atlas_location_id: str) -> str:
    """Return the one runtime id used for an atlas location record."""

    override = _LOCATION_OVERRIDES.get(atlas_location_id)
    if override is not None:
        return override
    if atlas_location_id.startswith("runtime_"):
        return atlas_location_id.removeprefix("runtime_")
    match = re.fullmatch(r"loc_5_(\d+)_(\d+)", atlas_location_id)
    if match is not None:
        number = (int(match.group(1)) - 1) * 12 + int(match.group(2))
        if number > 0:
            return f"catalog_l{number:03d}"
    return f"atlas_{atlas_location_id}"


def runtime_structure_id(atlas_structure_id: str) -> str:
    """Return the one runtime id used for an atlas structure record."""

    return _STRUCTURE_OVERRIDES.get(
        atlas_structure_id,
        f"atlas_room_{atlas_structure_id}",
    )


def runtime_street_id(atlas_street_id: str) -> str:
    """Return the one runtime id used for an atlas street record."""

    return _STREET_OVERRIDES.get(
        atlas_street_id,
        f"atlas_street_{atlas_street_id}",
    )


def atlas_location_id_for_runtime(runtime_id: str) -> str | None:
    """Resolve a current or historical runtime location id to an atlas id."""

    structure_override = _STRUCTURE_RUNTIME_TO_ATLAS.get(runtime_id)
    if structure_override is not None:
        return structure_override
    if runtime_id in _SPECIAL_RUNTIME_TO_ATLAS:
        return _SPECIAL_RUNTIME_TO_ATLAS[runtime_id]
    if runtime_id.startswith("atlas_room_"):
        return runtime_id.removeprefix("atlas_room_")
    if runtime_id.startswith("atlas_street_"):
        return runtime_id.removeprefix("atlas_street_")
    if runtime_id.startswith("atlas_"):
        return runtime_id.removeprefix("atlas_")
    match = re.fullmatch(r"catalog_l(\d{3})", runtime_id)
    if match is not None:
        number = int(match.group(1))
        if 1 <= number <= 84:
            region, place = divmod(number - 1, 12)
            return f"loc_5_{region + 1}_{place + 1}"
    return None


def atlas_structure_id_for_runtime(runtime_id: str) -> str | None:
    """Resolve a current or historical room runtime id to a structure id."""

    override = _STRUCTURE_RUNTIME_TO_ATLAS.get(runtime_id)
    if override is not None:
        return override
    if runtime_id.startswith("atlas_room_"):
        return runtime_id.removeprefix("atlas_room_")
    return None


def atlas_street_id_for_runtime(runtime_id: str) -> str | None:
    """Resolve a current or historical street runtime id to an atlas id."""

    if runtime_id == "oak_street":
        return "candle_oak"
    if runtime_id.startswith("atlas_street_"):
        return runtime_id.removeprefix("atlas_street_")
    return None


__all__ = [
    "atlas_location_id_for_runtime",
    "atlas_structure_id_for_runtime",
    "atlas_street_id_for_runtime",
    "runtime_location_id",
    "runtime_street_id",
    "runtime_structure_id",
]
