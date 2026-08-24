"""Static map atlas, routing, and player-safe map views."""

from trpg_server.map.atlas import (
    MapAtlas,
    MapAtlasError,
    atlas_for_scenario,
    gray_harbor_atlas,
    load_map_atlas,
)
from trpg_server.map.api import atlas_summary, calculate_route, route_summary
from trpg_server.map.capabilities import (
    LocationCapabilityCandidate,
    LocationCapabilityContext,
    LocationCapabilityProvider,
    build_location_capability_context,
)
from trpg_server.map.routing import MapRoute, MapRouteError, find_map_route
from trpg_server.map.occupancy import MapLocationContents, build_location_contents
from trpg_server.map.traversal import map_exit_is_allowed

__all__ = [
    "MapAtlas",
    "MapAtlasError",
    "MapRoute",
    "MapRouteError",
    "LocationCapabilityCandidate",
    "LocationCapabilityContext",
    "LocationCapabilityProvider",
    "build_location_capability_context",
    "find_map_route",
    "MapLocationContents",
    "build_location_contents",
    "map_exit_is_allowed",
    "atlas_summary",
    "atlas_for_scenario",
    "calculate_route",
    "route_summary",
    "gray_harbor_atlas",
    "load_map_atlas",
]
