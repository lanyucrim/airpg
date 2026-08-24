from __future__ import annotations

from typing import Literal

from trpg_server.map.atlas import MapAtlas
from trpg_server.map.routing import MapRoute, TravelMode, find_map_route


def atlas_summary(atlas: MapAtlas) -> dict[str, object]:
    """Return map metadata suitable for a read-only client endpoint."""
    return {
        "atlasId": atlas.atlas_id,
        "schemaVersion": atlas.schema_version,
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
        "locations": [
            {
                "atlasLocationId": location.id,
                "chapter": location.chapter,
                "name": location.name,
                "aliases": list(location.aliases),
                "kind": location.kind,
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
            for location in atlas.locations
        ],
    }


def route_summary(route: MapRoute) -> dict[str, object]:
    return {
        "fromLocationId": route.from_location_id,
        "toLocationId": route.to_location_id,
        "streetPath": list(route.street_path),
        "distanceKm": route.distance_km,
        "travelMinutes": route.travel_minutes,
        "travelMode": route.travel_mode,
        "internalMinutes": route.internal_minutes,
        "basis": route.basis,
    }


def calculate_route(
    atlas: MapAtlas,
    from_location_id: str,
    to_location_id: str,
    travel_mode: TravelMode = "walking",
) -> dict[str, object]:
    return route_summary(
        find_map_route(atlas, from_location_id, to_location_id, travel_mode)
    )
