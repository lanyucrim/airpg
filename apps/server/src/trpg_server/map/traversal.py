from __future__ import annotations

from trpg_server.core.state import ExitState, Projection
from trpg_server.map.atlas import MapAtlas, atlas_for_scenario
from trpg_server.map.runtime_ids import (
    atlas_location_id_for_runtime,
    atlas_street_id_for_runtime,
    runtime_structure_id,
)


def map_exit_is_allowed(
    state: Projection,
    origin_id: str,
    destination_id: str,
    exit_state: ExitState,
) -> bool:
    """Apply atlas topology to an already authored direct exit.

    An authored hidden/discovered exit is allowed to bypass ordinary street
    routing because it represents a special physical connection. Streets are
    not runtime locations; ordinary exits between real places are checked
    against their shared street or an atlas street junction.
    """
    atlas = atlas_for_scenario(state.scenario_id)
    if atlas is None:
        return True
    if exit_state.discovery_id is not None:
        return True

    origin = state.locations.get(origin_id)
    destination = state.locations.get(destination_id)
    if (
        origin is not None
        and destination is not None
        and origin.kind == "floor"
        and destination.kind in {"street", "district", "city"}
    ):
        # Floors must descend through the building's authored entry room or
        # another intermediate room before reaching an exterior node.
        return False

    origin_streets = _street_ids(atlas, origin_id)
    destination_streets = _street_ids(atlas, destination_id)

    # A runtime parent link is not enough to authorize movement.  A legacy
    # catalog location may have been attached directly to a district or to an
    # old street in the opening package while the atlas places it elsewhere.
    # When both sides have an atlas street identity, the physical topology
    # wins over that historical parent link.
    if origin_streets and destination_streets and not origin_streets & destination_streets:
        return _street_connection_exists(atlas, origin_streets, destination_streets)

    if _same_parent_or_ancestor(state, origin_id, destination_id):
        if origin_streets and destination_streets:
            return bool(origin_streets & destination_streets)
        # City roots may expose only the explicit gateway exits emitted by the
        # atlas compiler; the destination is still a real place.
        if _is_district_or_city(state, origin_id) and destination_streets:
            return True
        return True

    if not origin_streets or not destination_streets:
        if _is_district_or_city(state, origin_id) and destination_streets:
            return True
        return True
    if origin_streets & destination_streets:
        return True
    return _street_connection_exists(atlas, origin_streets, destination_streets)


def resolve_arrival_location(
    state: Projection,
    origin_id: str,
    destination_id: str,
    actor_id: str | None = None,
) -> str:
    """Resolve a street-to-building alias to the building entry structure.

    Building ids remain the public command target so existing aliases and map
    buttons keep working.  The authoritative movement event, however, should
    place the actor in the first ordinary structure immediately on entry. This
    preserves the street/building topology while making the exact interior
    location available to the player after one move.
    """
    origin = state.locations.get(origin_id)
    destination = state.locations.get(destination_id)
    if origin is None or destination is None or origin.kind != "street":
        return destination_id
    if destination.kind in {"street", "city", "district", "room", "floor", "yard"}:
        return destination_id

    atlas = atlas_for_scenario(state.scenario_id)
    if atlas is None:
        return destination_id
    atlas_location_id = atlas_location_id_for_runtime(destination_id)
    if atlas_location_id is None:
        return destination_id
    atlas_location = next(
        (value for value in atlas.locations if value.id == atlas_location_id),
        None,
    )
    if atlas_location is None:
        return destination_id
    for node in atlas_location.structure:
        if _atlas_structure_is_hidden(node):
            continue
        runtime_id = runtime_structure_id(node.id)
        if runtime_id not in state.locations:
            continue
        # The atlas is content input, not an executable edge.  Only redirect
        # into a room when the materialized building-to-room edge is actually
        # usable under the current runtime rules.  This avoids turning a
        # stale/malformed building alias into a teleport into a room that has
        # no entrance, a hidden entrance, or an inactive story condition.
        if _entry_structure_is_reachable(
            state,
            destination_id,
            runtime_id,
            actor_id,
        ):
            return runtime_id
        # The first ordinary structure is the authored entry point.  Falling
        # back to a later room would bypass the intended interior topology.
        break
    return destination_id


def _entry_structure_is_reachable(
    state: Projection,
    building_id: str,
    structure_id: str,
    actor_id: str | None,
) -> bool:
    """Check the runtime edge used for a street-entry room redirect."""
    building = state.locations.get(building_id)
    structure = state.locations.get(structure_id)
    if building is None or structure is None:
        return False
    if structure.parent_id != building_id:
        return False
    entry_exit = next(
        (
            value
            for value in building.exits
            if value.to_location_id == structure_id
        ),
        None,
    )
    if entry_exit is None:
        return False
    if not entry_exit.visible and (
        actor_id is None
        or entry_exit.exit_id not in state.discovered_exits.get(actor_id, set())
    ):
        return False
    if not map_exit_is_allowed(state, building_id, structure_id, entry_exit):
        return False
    return all(
        condition_id in state.story_conditions
        and state.story_conditions[condition_id].active
        for condition_id in entry_exit.required_condition_ids
    )


def _atlas_structure_is_hidden(node: object) -> bool:
    return getattr(node, "access", "public") == "hidden"


def _same_parent_or_ancestor(state: Projection, origin_id: str, destination_id: str) -> bool:
    if origin_id not in state.locations or destination_id not in state.locations:
        return False
    current = state.locations[origin_id].parent_id
    seen: set[str] = set()
    while current and current not in seen:
        if current == destination_id:
            return True
        seen.add(current)
        current = state.locations.get(current).parent_id if current in state.locations else None
    current = state.locations[destination_id].parent_id
    seen.clear()
    while current and current not in seen:
        if current == origin_id:
            return True
        seen.add(current)
        current = state.locations.get(current).parent_id if current in state.locations else None
    return False


def _street_ids(atlas: MapAtlas, location_id: str) -> set[str]:
    # The city root is an executable orientation node, not the atlas's
    # internal runtime shell with a street assignment.
    if location_id == "gray_harbor":
        return set()
    street_id = atlas_street_id_for_runtime(location_id)
    if street_id is not None:
        return {street_id} if any(street.id == street_id for street in atlas.streets) else set()
    try:
        atlas_node_id = atlas.resolve_node_id(location_id)
    except KeyError:
        return set()
    for location in atlas.locations:
        if location.id == atlas_node_id:
            return set(location.street_ids)
        for structure in location.structure:
            if structure.id == atlas_node_id:
                return set(location.street_ids)
    return set()


def _is_district_or_city(state: Projection, location_id: str) -> bool:
    location = state.locations.get(location_id)
    return location is not None and location.kind in {"city", "district"}


def _street_connection_exists(
    atlas: MapAtlas,
    origin_streets: set[str],
    destination_streets: set[str],
) -> bool:
    return any(
        connection.from_street_id in origin_streets
        and connection.to_street_id in destination_streets
        for connection in atlas.street_connections
    )
