from __future__ import annotations

from functools import cache
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from trpg_server.map.runtime_ids import atlas_location_id_for_runtime


GRAY_HARBOR_SCENARIO_ID = "gray-harbor-black-tide-throne"
GRAY_HARBOR_ATLAS_PATH = (
    Path(__file__).resolve().parents[5]
    / "content"
    / "campaigns"
    / "gray-harbor"
    / "atlas"
    / "location-atlas.json"
)

class MapAtlasError(ValueError):
    """Raised when an atlas cannot be read or violates its graph contract."""


class AtlasModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow", frozen=True)


class AtlasSource(AtlasModel):
    status: Literal["canon", "inferred"]
    document: str | None = None
    line: int | None = Field(default=None, ge=1)


class AtlasCoordinate(AtlasModel):
    x_km: float = Field(alias="xKm")
    y_km: float = Field(alias="yKm")
    basis: str


class AtlasStructureNode(AtlasModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    parent_id: str = Field(alias="parentId", min_length=1)
    purpose: str = ""
    exists: bool = True
    certainty: Literal["canon", "atlas_design"]
    parent_type: str = Field(default="location", alias="parentType")
    record_type: str = Field(default="sublocation", alias="recordType")
    street_ids: tuple[str, ...] = Field(default=(), alias="streetIds")
    access: Literal["public", "private", "hidden"] = "public"
    discovery_id: str | None = Field(default=None, alias="discoveryId")


class AtlasTravelSummary(AtlasModel):
    distance_km: float = Field(alias="distanceKm", ge=0)
    walk_minutes: int = Field(alias="walkMinutes", ge=0)
    horse_carriage_minutes: int = Field(alias="horseCarriageMinutes", ge=0)
    basis: str
    street_path: tuple[str, ...] = Field(default=(), alias="streetPath")


class AtlasInternalTransit(AtlasModel):
    short: int = Field(ge=0)
    long: int = Field(ge=0)
    basis: str


class AtlasLocation(AtlasModel):
    id: str = Field(min_length=1)
    chapter: str
    name: str = Field(min_length=1)
    source: AtlasSource
    surface: str = ""
    people: str = ""
    resources: str = ""
    risk: str = ""
    aliases: tuple[str, ...] = ()
    region_id: str = Field(alias="regionId", min_length=1)
    region_name: str = Field(default="", alias="regionName")
    kind: str = "building"
    coordinate: AtlasCoordinate
    structure: tuple[AtlasStructureNode, ...] = ()
    canon_notes: str = Field(default="", alias="canonNotes")
    inferred_notes: str = Field(default="", alias="inferredNotes")
    record_type: str = Field(default="location", alias="recordType")
    parent_id: str = Field(alias="parentId", min_length=1)
    parent_type: str = Field(alias="parentType", min_length=1)
    children: tuple[str, ...] = ()
    travel_from_white_heron: AtlasTravelSummary = Field(alias="travelFromWhiteHeron")
    internal_transit_minutes: AtlasInternalTransit = Field(alias="internalTransitMinutes")
    street_ids: tuple[str, ...] = Field(alias="streetIds", min_length=1)
    street_position_m: int = Field(alias="streetPositionM", ge=0)
    street_travel_from_white_heron: AtlasTravelSummary = Field(
        alias="streetTravelFromWhiteHeron"
    )


class AtlasRegion(AtlasModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    anchor: tuple[float, float]
    note: str = ""
    parent_scope_id: str = Field(alias="parentScopeId", min_length=1)


class AtlasScope(AtlasModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: str
    child_region_ids: tuple[str, ...] = Field(alias="childRegionIds")


class AtlasStreet(AtlasModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    region_id: str | None = Field(default=None, alias="regionId")
    region_ids: tuple[str, ...] = Field(default=(), alias="regionIds")
    exists: bool = True
    certainty: Literal["canon", "atlas_design"]
    status: Literal["canon", "inferred"]
    location_ids: tuple[str, ...] = Field(default=(), alias="locationIds")
    centerline: dict[str, Any]
    sequence: int = 0


class AtlasStreetConnection(AtlasModel):
    from_street_id: str = Field(alias="fromStreetId", min_length=1)
    to_street_id: str = Field(alias="toStreetId", min_length=1)
    distance_km: float = Field(alias="distanceKm", ge=0)
    junction: str = ""
    status: Literal["canon", "inferred"]


class AtlasLocationLink(AtlasModel):
    from_location_id: str = Field(alias="fromLocationId", min_length=1)
    to_location_id: str = Field(alias="toLocationId", min_length=1)
    street_id: str = Field(alias="streetId", min_length=1)
    street: str = ""
    distance_km: float = Field(alias="distanceKm", ge=0)
    status: Literal["canon", "inferred"]


class MapAtlas(AtlasModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    atlas_id: str = Field(alias="atlasId", min_length=1)
    source: dict[str, Any]
    coordinate_system: dict[str, Any] = Field(alias="coordinateSystem")
    speed_model: dict[str, float | int] = Field(alias="speedModel")
    scopes: tuple[AtlasScope, ...]
    regions: tuple[AtlasRegion, ...]
    locations: tuple[AtlasLocation, ...]
    streets: tuple[AtlasStreet, ...]
    street_connections: tuple[AtlasStreetConnection, ...] = Field(
        alias="streetConnections"
    )
    route_examples: tuple[dict[str, Any], ...] = Field(default=(), alias="routeExamples")
    street_links: tuple[dict[str, Any], ...] = Field(default=(), alias="streetLinks")
    location_links: tuple[AtlasLocationLink, ...] = Field(alias="locationLinks")

    @model_validator(mode="after")
    def graph_is_consistent(self) -> MapAtlas:
        scope_ids = _unique_ids("scope", self.scopes)
        region_ids = _unique_ids("region", self.regions)
        location_ids = _unique_ids("location", self.locations)
        street_ids = _unique_ids("street", self.streets)

        for scope in self.scopes:
            _require_all("scope child region", scope.child_region_ids, region_ids)
        for region in self.regions:
            if region.parent_scope_id not in scope_ids:
                raise ValueError(f"unknown region parent scope: {region.parent_scope_id}")
        for location in self.locations:
            if location.region_id not in region_ids | scope_ids:
                raise ValueError(f"unknown location region: {location.id} -> {location.region_id}")
            _require_all(f"location street {location.id}", location.street_ids, street_ids)
            structure_ids = _unique_ids(f"structure in {location.id}", location.structure)
            if structure_ids != set(location.children):
                raise ValueError(f"location children do not match structure: {location.id}")
            for node in location.structure:
                if node.parent_id != location.id:
                    raise ValueError(f"structure parent mismatch: {node.id}")
                if node.access != "hidden" and node.discovery_id is not None:
                    raise ValueError(
                        f"only hidden structures may bind discoveryId: {node.id}"
                    )
        for street in self.streets:
            _require_all(f"street location {street.id}", street.location_ids, location_ids)
            if street.region_id is not None and street.region_id not in region_ids:
                raise ValueError(f"unknown street region: {street.id} -> {street.region_id}")
            _require_all(f"street regions {street.id}", street.region_ids, region_ids)
        for connection in self.street_connections:
            _require_all(
                "street connection",
                (connection.from_street_id, connection.to_street_id),
                street_ids,
            )
        for link in self.location_links:
            _require_all(
                "location link",
                (link.from_location_id, link.to_location_id),
                location_ids,
            )
            if link.street_id not in street_ids:
                raise ValueError(f"unknown location-link street: {link.street_id}")

        walking = float(self.speed_model.get("walkingKmh", 0))
        carriage = float(self.speed_model.get("horseCarriageKmh", 0))
        if walking <= 0 or carriage <= 0:
            raise ValueError("atlas speed model must define positive travel speeds")
        return self

    def location(self, location_id: str) -> AtlasLocation:
        try:
            return next(value for value in self.locations if value.id == location_id)
        except StopIteration as error:
            raise KeyError(location_id) from error

    def region(self, region_id: str) -> AtlasRegion:
        try:
            return next(value for value in self.regions if value.id == region_id)
        except StopIteration as error:
            raise KeyError(region_id) from error

    def resolve_node_id(self, node_id: str) -> str:
        """Resolve a runtime location id or an atlas id to an atlas node id."""
        if node_id in {location.id for location in self.locations}:
            return node_id
        for location in self.locations:
            if node_id in {node.id for node in location.structure}:
                return node_id
        alias = atlas_location_id_for_runtime(node_id)
        if alias is not None:
            return alias
        raise KeyError(node_id)


def load_map_atlas(path: Path) -> MapAtlas:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise MapAtlasError(f"cannot read map atlas: {path}") from error
    except json.JSONDecodeError as error:
        raise MapAtlasError(f"map atlas is not valid JSON: {path}") from error
    try:
        return MapAtlas.model_validate(document)
    except ValidationError as error:
        raise MapAtlasError(f"invalid map atlas {path}: {error}") from error


@cache
def _gray_harbor_atlas_for_revision(
    modified_ns: int,
    file_size: int,
) -> MapAtlas:
    """Load one immutable atlas revision.

    The development server can stay alive while the generated atlas is
    rebuilt.  Caching only by the file revision keeps repeated route queries
    cheap, while ensuring a reset/recompile cannot continue using an older
    structure catalogue from the same Python process.
    """
    # ``file_size`` is deliberately part of the key as well as the timestamp:
    # some file systems have coarse timestamp resolution during quick rebuilds.
    _ = file_size
    return load_map_atlas(GRAY_HARBOR_ATLAS_PATH)


def gray_harbor_atlas() -> MapAtlas:
    """Return the current Gray Harbor atlas revision.

    Unlike a permanent no-argument cache, this observes generated-content
    changes made by the map build script during a running development server.
    """
    try:
        stat = GRAY_HARBOR_ATLAS_PATH.stat()
    except OSError as error:
        raise MapAtlasError(f"cannot stat map atlas: {GRAY_HARBOR_ATLAS_PATH}") from error
    return _gray_harbor_atlas_for_revision(stat.st_mtime_ns, stat.st_size)


def atlas_for_scenario(scenario_id: str | None) -> MapAtlas | None:
    """Resolve static map content from scenario identity, never save identity."""

    if scenario_id != GRAY_HARBOR_SCENARIO_ID:
        return None
    return gray_harbor_atlas()


def _unique_ids(kind: str, values: tuple[Any, ...]) -> set[str]:
    identifiers = [str(value.id) for value in values]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"duplicate {kind} ids")
    return set(identifiers)


def _require_all(kind: str, values: tuple[str, ...], allowed: set[str]) -> None:
    missing = sorted(set(values) - allowed)
    if missing:
        raise ValueError(f"unknown {kind}: {', '.join(missing)}")
