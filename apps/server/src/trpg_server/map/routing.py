from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from math import isfinite
from typing import Literal

from trpg_server.map.atlas import AtlasLocation, MapAtlas


TravelMode = Literal["walking", "horse_carriage"]


class MapRouteError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MapRoute:
    from_location_id: str
    to_location_id: str
    street_path: tuple[str, ...]
    distance_km: float
    travel_minutes: int
    travel_mode: TravelMode
    internal_minutes: int = 0
    basis: Literal["same_location", "internal_structure", "same_street", "street_graph"] = "street_graph"


def find_map_route(
    atlas: MapAtlas,
    from_location_id: str,
    to_location_id: str,
    travel_mode: TravelMode = "walking",
) -> MapRoute:
    origin_node_id = atlas.resolve_node_id(from_location_id)
    destination_node_id = atlas.resolve_node_id(to_location_id)
    origin, origin_internal = _resolve_location(atlas, origin_node_id)
    destination, destination_internal = _resolve_location(atlas, destination_node_id)
    internal_minutes = origin_internal + destination_internal

    if origin_node_id == destination_node_id:
        return MapRoute(
            from_location_id,
            to_location_id,
            (),
            0.0,
            0,
            travel_mode,
            basis="same_location",
        )
    if origin.id == destination.id:
        minutes = origin.internal_transit_minutes.short
        return MapRoute(
            from_location_id,
            to_location_id,
            (),
            0.0,
            minutes,
            travel_mode,
            internal_minutes=minutes,
            basis="internal_structure",
        )

    shared_streets = sorted(set(origin.street_ids) & set(destination.street_ids))
    if shared_streets:
        distance_km = round(
            abs(destination.street_position_m - origin.street_position_m) / 1000,
            3,
        )
        return MapRoute(
            from_location_id,
            to_location_id,
            (shared_streets[0],),
            distance_km,
            _travel_minutes(atlas, distance_km, travel_mode) + internal_minutes,
            travel_mode,
            internal_minutes=internal_minutes,
            basis="same_street",
        )

    street_path, distance_km = _shortest_street_path(
        atlas,
        origin.street_ids,
        destination.street_ids,
    )
    return MapRoute(
        from_location_id,
        to_location_id,
        street_path,
        distance_km,
        _travel_minutes(atlas, distance_km, travel_mode) + internal_minutes,
        travel_mode,
        internal_minutes=internal_minutes,
        basis="street_graph",
    )


def _resolve_location(atlas: MapAtlas, node_id: str) -> tuple[AtlasLocation, int]:
    for location in atlas.locations:
        if location.id == node_id:
            return location, 0
        if any(node.id == node_id for node in location.structure):
            return location, location.internal_transit_minutes.short
    raise MapRouteError(f"unknown atlas location: {node_id}")


def _shortest_street_path(
    atlas: MapAtlas,
    start_ids: tuple[str, ...],
    target_ids: tuple[str, ...],
) -> tuple[tuple[str, ...], float]:
    targets = set(target_ids)
    graph: dict[str, list[tuple[str, float]]] = {}
    for connection in atlas.street_connections:
        graph.setdefault(connection.from_street_id, []).append(
            (connection.to_street_id, connection.distance_km)
        )

    queue: list[tuple[float, str, tuple[str, ...]]] = []
    best: dict[str, float] = {}
    for start_id in start_ids:
        heappush(queue, (0.0, start_id, (start_id,)))
    while queue:
        distance, street_id, path = heappop(queue)
        if distance >= best.get(street_id, float("inf")):
            continue
        best[street_id] = distance
        if street_id in targets:
            return path, round(distance, 3)
        for next_id, edge_distance in graph.get(street_id, []):
            candidate = distance + edge_distance
            if candidate < best.get(next_id, float("inf")):
                heappush(queue, (candidate, next_id, (*path, next_id)))

    if any(not isfinite(value) for value in best.values()):
        raise MapRouteError("invalid distance in street graph")
    raise MapRouteError("no street route connects the two atlas locations")


def _travel_minutes(
    atlas: MapAtlas,
    distance_km: float,
    travel_mode: TravelMode,
) -> int:
    speed_key = "walkingKmh" if travel_mode == "walking" else "horseCarriageKmh"
    speed = float(atlas.speed_model[speed_key])
    if distance_km == 0:
        return 0
    return max(1, round(distance_km * 60 / speed))
