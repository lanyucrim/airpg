from __future__ import annotations

import re
from typing import Any

from trpg_server.core.state import LocationState, Projection
from trpg_server.locations.movement import exit_is_visible_to
from trpg_server.locations.weather_travel import estimate_exit_travel_time
from trpg_server.map.atlas import (
    AtlasLocation,
    AtlasStructureNode,
    AtlasStreet,
    MapAtlas,
    atlas_for_scenario,
)
from trpg_server.map.occupancy import build_location_contents
from trpg_server.map.runtime_ids import (
    atlas_location_id_for_runtime,
    atlas_structure_id_for_runtime,
    runtime_location_id,
    runtime_street_id,
)
from trpg_server.map.traversal import map_exit_is_allowed

RUNTIME_REGION_IDS = {
    "catalog_district_old_port": "old_harbor",
    "catalog_district_iron_bay": "iron_bay",
    "catalog_district_black_slope": "black_slope",
    "catalog_district_gold_bell": "golden_bell",
    "catalog_district_saint_bridge": "saint_bridge",
    "catalog_district_white_cliff": "white_cliff",
}

def _clean_region_name(value: str | None) -> str:
    """Return the short player-facing name for an atlas region."""
    if not value:
        return ""
    # Atlas metadata uses suffixes such as ``区`` and ``（城市总览）``;
    # the player breadcrumb names the area itself, not its metadata scope.
    value = re.sub(r"(?:（城市总览）|\(城市总览\))$", "", value.strip())
    return re.sub(r"区$", "", value)


def _atlas_region_name(atlas: MapAtlas, region_id: str | None) -> str:
    if not region_id:
        return ""
    region = next(
        (value for value in atlas.regions if value.id == region_id),
        None,
    )
    return _clean_region_name(region.name if region is not None else None)


def _runtime_street_atlas_location(
    atlas: MapAtlas,
    runtime_id: str,
) -> AtlasStreet | None:
    """Resolve a runtime street node to its authored atlas street."""
    for street in atlas.streets:
        if runtime_street_id(street.id) == runtime_id:
            return street
    return None


def _containing_place_id(state: Projection, location_id: str) -> str:
    """Walk room/floor/yard parents until the containing place is reached."""
    current = location_id
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        location = state.locations.get(current)
        if location is None or location.kind not in {"room", "floor", "yard"}:
            return current
        if location.parent_id is None:
            return current
        current = location.parent_id
    return location_id


def _short_place_name(name: str, region_name: str) -> str:
    """Avoid repeating a region prefix already present in a catalog title."""
    if "·" not in name:
        return name
    prefix, remainder = name.split("·", 1)
    if _clean_region_name(prefix) == region_name:
        return remainder
    return name


def _current_location_details(
    state: Projection,
    atlas: MapAtlas | None,
    location_id: str | None,
) -> tuple[list[str], str | None, str | None]:
    """Build the exact player breadcrumb without changing movement authority.

    The public map intentionally continues to collapse rooms for its landmark
    list.  This separate read-only projection preserves the actor's exact
    runtime node for UI labels and does not expose hidden neighboring rooms.
    """
    if location_id is None:
        return [], None, None
    location = state.locations.get(location_id)
    if location is None:
        return [location_id], location_id, None

    if location.kind == "city":
        return [location.name], location.name, None

    if atlas is None:
        chain: list[str] = []
        current = location_id
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            value = state.locations.get(current)
            if value is None:
                break
            chain.append(value.name)
            current = value.parent_id
        path = list(reversed(chain))
        structure = (
            location.name
            if location.kind in {"room", "floor", "yard"}
            else None
        )
        return path, "·".join(path), structure

    street = _runtime_street_atlas_location(atlas, location_id)
    if street is not None or location.kind == "street":
        street_name = location.name
        if street is not None:
            street_name = street.name
            region_id = street.region_id or (
                street.region_ids[0] if street.region_ids else None
            )
            region_name = _atlas_region_name(atlas, region_id)
            if not region_name and street.region_ids:
                region_name = _atlas_region_name(atlas, street.region_ids[0])
        else:
            parent = state.locations.get(location.parent_id or "")
            region_name = _clean_region_name(parent.name if parent is not None else None)
        path = [value for value in (region_name, street_name) if value]
        return path, "·".join(path), None

    bindings = _runtime_atlas_index(state, atlas)
    binding = bindings.get(location_id)
    atlas_location = binding[0] if binding is not None else None
    structure = binding[1] if binding is not None else None
    place_id = _containing_place_id(state, location_id)
    place_state = state.locations.get(place_id)

    if atlas_location is not None:
        region_name = _clean_region_name(atlas_location.region_name)
        if not region_name:
            region_name = _atlas_region_name(atlas, atlas_location.region_id)
        place_name = state.location_names.get(place_id, atlas_location.name)
    else:
        parent = state.locations.get(place_state.parent_id if place_state else "")
        region_name = _clean_region_name(parent.name if parent is not None else None)
        place_name = place_state.name if place_state is not None else location.name

    place_name = _short_place_name(place_name, region_name)
    path = [value for value in (region_name, place_name) if value]
    structure_name = structure.name if structure is not None else None
    if structure_name is None and location.kind in {"room", "floor", "yard"}:
        structure_name = location.name
    if structure_name:
        path.append(structure_name)
    return path, "·".join(path), structure_name


def _display_location_id(
    state: Projection,
    atlas: MapAtlas | None,
    location_id: str | None,
) -> str | None:
    """Collapse a room/floor location to its containing map location."""
    if location_id is None or location_id not in state.locations:
        return location_id
    if atlas is None:
        current = location_id
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            location = state.locations.get(current)
            if location is None or location.kind not in {"room", "floor", "yard"}:
                return current
            current = location.parent_id
        return location_id
    if location_id == "oak_street" or location_id.startswith("atlas_street_"):
        return location_id
    bindings = _runtime_atlas_index(state, atlas)
    current = location_id
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        binding = bindings.get(current)
        if binding is not None and binding[1] is None:
            return current
        parent = state.locations.get(current)
        current = parent.parent_id if parent is not None else None
    return location_id


def public_map(state: Projection) -> dict[str, object]:
    """Build a complete player-safe atlas map.

    The atlas is a read-only overview.  Runtime exits remain the only
    actionable movement data; atlas links are exposed separately for map
    rendering and never become authoritative exits.
    """
    actor_id = state.player_character_id
    current_id = state.character_locations.get(actor_id, state.location_id)
    atlas = atlas_for_scenario(state.scenario_id)
    display_id = _display_location_id(state, atlas, current_id)
    location_path, current_location_display_name, current_structure_name = (
        _current_location_details(state, atlas, current_id)
    )
    known_ids: set[str] = set()

    def add_ancestors(location_id: str | None) -> None:
        seen: set[str] = set()
        while location_id and location_id not in seen and location_id in state.locations:
            seen.add(location_id)
            known_ids.add(location_id)
            location_id = state.locations[location_id].parent_id

    add_ancestors(current_id)
    current_location = state.locations.get(current_id or "")
    if current_location is not None:
        for exit_state in current_location.exits:
            if (
                exit_is_visible_to(state, actor_id, exit_state)
                and map_exit_is_allowed(
                    state,
                    current_id,
                    exit_state.to_location_id,
                    exit_state,
                )
            ):
                add_ancestors(exit_state.to_location_id)

    def is_visible(location: LocationState) -> bool:
        if location.map_visibility == "gm":
            return False
        if location.map_visibility == "player":
            return location.location_id in known_ids
        return True

    reachable_ids: set[str] = set()
    if current_location is not None:
        reachable_ids = {
            exit_state.to_location_id
            for exit_state in current_location.exits
            if exit_is_visible_to(state, actor_id, exit_state)
            and not exit_state.locked
            and map_exit_is_allowed(
                state,
                current_id,
                exit_state.to_location_id,
                exit_state,
            )
        }

    context_ids = set(reachable_ids)
    if current_id is not None:
        context_ids.add(current_id)
        add_ancestors(current_id)
    for reachable_id in reachable_ids:
        add_ancestors(reachable_id)
    context_ids.update(known_ids)
    # Keep city/district shells as non-interactive orientation context.  The
    # web map filters them out of the travel list, while API consumers can
    # still resolve the current location's regional breadcrumb safely.
    context_ids.update(
        location.location_id
        for location in state.locations.values()
        if location.kind in {"city", "district"}
    )
    occupancy = build_location_contents(state)
    atlas_locations = _runtime_atlas_index(state, atlas) if atlas is not None else {}
    atlas_regions = {region.id: region for region in atlas.regions} if atlas is not None else {}
    locations: list[dict[str, object]] = []

    def visible_state_location(runtime_id: str) -> LocationState | None:
        value = state.locations.get(runtime_id)
        if value is None or not is_visible(value):
            return None
        return value

    def append_location(
        location: LocationState,
        *,
        atlas_location: AtlasLocation | None = None,
        street_id: str | None = None,
    ) -> None:
        location_id = location.location_id
        is_current = location_id == display_id
        is_reachable = location_id in reachable_ids or location_id in reachable_display_ids
        visible_exits = []
        if location_id == current_id:
            for exit_state in location.exits:
                if (
                    not exit_is_visible_to(state, actor_id, exit_state)
                    or exit_state.to_location_id not in state.locations
                    or not is_visible(state.locations[exit_state.to_location_id])
                    or not map_exit_is_allowed(
                        state,
                        location_id,
                        exit_state.to_location_id,
                        exit_state,
                    )
                ):
                    continue
                estimate = estimate_exit_travel_time(
                    state,
                    actor_id,
                    location_id,
                    exit_state,
                )
                visible_exits.append({
                    "exitId": exit_state.exit_id,
                    "toLocationId": exit_state.to_location_id,
                    "name": state.location_names.get(
                        exit_state.to_location_id,
                        exit_state.to_location_id,
                    ),
                    "label": exit_state.label,
                    "travelMinutes": exit_state.travel_minutes,
                    "baseTravelMinutes": estimate.base_travel_minutes,
                    "weatherDelayMinutes": estimate.weather_delay_minutes,
                    "estimatedTravelMinutes": estimate.travel_minutes,
                    "weatherCondition": estimate.weather_condition,
                    "weatherConditionName": estimate.weather_condition_name,
                    "locked": exit_state.locked,
                })
        visible_characters: list[dict[str, object]] = []
        if location_id in {current_id, display_id}:
            for character_id, character_location in sorted(state.character_locations.items()):
                if character_id == actor_id:
                    continue
                if state.character_types.get(character_id) != "npc":
                    continue
                if character_location != current_id and _display_location_id(
                    state, atlas, character_location
                ) != location_id:
                    continue
                profile = state.character_profiles.get(character_id, {})
                visible_characters.append(
                    {
                        "characterId": character_id,
                        "name": state.character_names.get(character_id, character_id),
                        "role": profile.get("role", ""),
                    }
                )
        value: dict[str, object] = {
            "locationId": location.location_id,
            "name": location.name,
            "kind": location.kind,
            "parentLocationId": location.parent_id,
            "isCurrent": is_current,
            "isReachable": is_reachable,
            "exits": visible_exits,
            "visibleCharacters": visible_characters,
        }
        contents = occupancy.get(location.location_id)
        if contents is not None:
            visible_character_ids = tuple(
                value["characterId"] for value in visible_characters
            )
            value["contents"] = {
                "characterIds": list(visible_character_ids),
                "containerCount": len(contents.container_ids),
                "itemCount": len(contents.item_ids),
                "carriedItemCount": len(contents.carried_item_ids),
            }
        if atlas_location is not None:
            value.update(_public_atlas_location(atlas_location))
        elif street_id is not None and atlas is not None:
            street = next((item for item in atlas.streets if item.id == street_id), None)
            if street is not None:
                start = street.centerline.get("startKm")
                end = street.centerline.get("endKm")
                coordinate = None
                if isinstance(start, list) and isinstance(end, list) and len(start) >= 2 and len(end) >= 2:
                    coordinate = {
                        "xKm": (float(start[0]) + float(end[0])) / 2,
                        "yKm": (float(start[1]) + float(end[1])) / 2,
                        "basis": "street_centerline",
                    }
                value.update({
                    "atlasStreetId": street.id,
                    "regionId": street.region_id,
                    "coordinate": coordinate,
                    "streetIds": [street.id],
                    "sourceStatus": street.status,
                })
        elif location.location_id in RUNTIME_REGION_IDS and atlas is not None:
            region = atlas_regions.get(RUNTIME_REGION_IDS[location.location_id])
            if region is not None:
                value.update(
                    {
                        "regionId": region.id,
                        "coordinate": {
                            "xKm": region.anchor[0],
                            "yKm": region.anchor[1],
                            "basis": "region_anchor",
                        },
                        "sourceStatus": "canon",
                    }
                )
        locations.append(value)

    reachable_display_ids = {
        _display_location_id(state, atlas, location_id)
        for location_id in reachable_ids
    }
    reachable_display_ids.discard(None)

    if atlas is not None:
        # Render every authored top-level atlas location, excluding source
        # shells that are not executable places.
        for atlas_location in atlas.locations:
            if atlas_location.id in {"runtime_gray_harbor", "runtime_candle_ward"}:
                continue
            runtime_id = runtime_location_id(atlas_location.id)
            runtime_location = visible_state_location(runtime_id)
            if runtime_location is None:
                continue
            append_location(runtime_location, atlas_location=atlas_location)
        # Streets are first-class map landmarks even when the player is far
        # away; they provide the continuous city-wide road network.
        for street in atlas.streets:
            runtime_id = runtime_street_id(street.id)
            runtime_location = visible_state_location(runtime_id)
            if runtime_location is not None:
                append_location(runtime_location, street_id=street.id)
        city = visible_state_location("gray_harbor")
        if city is not None:
            append_location(city)
    else:
        for location in sorted(state.locations.values(), key=lambda value: value.location_id):
            if (
                location.location_id in context_ids
                and is_visible(location)
                and location.kind not in {"room", "floor", "yard"}
            ):
                append_location(location)

    visible_runtime_ids = {str(value["locationId"]) for value in locations}
    runtime_by_atlas = {
        location.id: runtime_id
        for location in atlas.locations
        if location.id not in {"runtime_gray_harbor", "runtime_candle_ward"}
        and (runtime_id := runtime_location_id(location.id))
        in visible_runtime_ids
    } if atlas is not None else {}

    result: dict[str, object] = {
        "currentLocationId": current_id,
        "displayLocationId": display_id,
        "currentLocationName": (
            state.location_names.get(current_id, current_id)
            if current_id is not None
            else None
        ),
        "displayLocationName": (
            state.location_names.get(display_id, display_id)
            if display_id is not None
            else None
        ),
        # These fields preserve the exact runtime node for player-facing UI
        # breadcrumbs while the legacy display fields remain building-level
        # map landmarks for existing API consumers.
        "locationPath": location_path,
        "currentLocationDisplayName": current_location_display_name,
        "currentStructureName": current_structure_name,
        "locations": locations,
    }
    if atlas is not None:
        result.update(
            {
                "atlasId": atlas.atlas_id,
                "atlasSchemaVersion": atlas.schema_version,
                "coordinateSystem": dict(atlas.coordinate_system),
                "speedModel": dict(atlas.speed_model),
                "regions": [
                    {
                        "regionId": region.id,
                        "name": region.name,
                        "anchor": list(region.anchor),
                        "note": region.note,
                    }
                    for region in atlas.regions
                ],
                "streets": [
                    {
                        "streetId": street.id,
                        "name": street.name,
                        "regionId": street.region_id,
                        "regionIds": list(street.region_ids),
                        "certainty": street.certainty,
                        "centerline": dict(street.centerline),
                        "sequence": street.sequence,
                    }
                    for street in atlas.streets
                ],
                "streetConnections": [
                    {
                        "fromStreetId": connection.from_street_id,
                        "toStreetId": connection.to_street_id,
                        "distanceKm": connection.distance_km,
                        "junction": connection.junction,
                        "status": connection.status,
                    }
                    for connection in atlas.street_connections
                ],
                "locationLinks": [
                    {
                        "fromLocationId": runtime_by_atlas[link.from_location_id],
                        "toLocationId": runtime_by_atlas[link.to_location_id],
                        "streetId": link.street_id,
                        "distanceKm": link.distance_km,
                        "status": link.status,
                    }
                    for link in atlas.location_links
                    if link.from_location_id in runtime_by_atlas
                    and link.to_location_id in runtime_by_atlas
                ],
            }
        )
    return result


def _runtime_atlas_index(
    state: Projection,
    atlas: MapAtlas,
) -> dict[str, tuple[AtlasLocation, AtlasStructureNode | None]]:
    by_id = {location.id: location for location in atlas.locations}
    structures = {
        node.id: (location, node)
        for location in atlas.locations
        for node in location.structure
    }
    by_name: dict[str, AtlasLocation] = {}
    for atlas_location in atlas.locations:
        for name in (atlas_location.name, *atlas_location.aliases):
            by_name.setdefault(_normalize_name(name), atlas_location)

    result: dict[str, tuple[AtlasLocation, AtlasStructureNode | None]] = {}
    for runtime_id, runtime_location in state.locations.items():
        structure_id = atlas_structure_id_for_runtime(runtime_id)
        if structure_id in structures:
            result[runtime_id] = structures[structure_id]
            continue
        atlas_id = atlas_location_id_for_runtime(runtime_id)
        if atlas_id in by_id:
            result[runtime_id] = (by_id[atlas_id], None)
            continue
        match = by_name.get(_normalize_name(runtime_location.name))
        if match is None and "·" in runtime_location.name:
            match = by_name.get(_normalize_name(runtime_location.name.split("·", 1)[1]))
        if match is not None:
            result[runtime_id] = (match, None)
    return result


def _normalize_name(value: str) -> str:
    return re.sub(r"[\s·—\-（）()]+", "", value).casefold()


def _public_atlas_location(
    location: AtlasLocation,
    structure: AtlasStructureNode | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "atlasLocationId": location.id,
        "regionId": location.region_id,
        "coordinate": {
            "xKm": location.coordinate.x_km,
            "yKm": location.coordinate.y_km,
            "basis": location.coordinate.basis,
        },
        "streetIds": list(location.street_ids),
        "streetPositionM": location.street_position_m,
        "sourceStatus": location.source.status,
        "structureCount": len(location.structure),
    }
    if structure is not None:
        value.update(
            {
                "atlasStructureId": structure.id,
                "structureCertainty": structure.certainty,
            }
        )
    return value
