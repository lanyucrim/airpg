from __future__ import annotations

import json
import pytest

import trpg_server.map.atlas as atlas_module
from trpg_server.core.projection import public_state, replay
from trpg_server.map import find_map_route, gray_harbor_atlas
from trpg_server.map.atlas import MapAtlasError, load_map_atlas
from trpg_server.map.runtime_ids import (
    atlas_location_id_for_runtime,
    runtime_location_id,
    runtime_street_id,
    runtime_structure_id,
)
from trpg_server.story.bootstrap import (
    GRAY_HARBOR_CAMPAIGN_ID,
    gray_harbor_events,
    gray_harbor_scenario,
)
from trpg_server.story.scenario import compile_initial_events


def test_gray_harbor_atlas_loads_complete_map_graph() -> None:
    atlas = gray_harbor_atlas()

    assert atlas.schema_version == 1
    assert atlas.atlas_id == "gray-harbor-v42-location-atlas"
    assert len(atlas.regions) == 7
    assert len(atlas.locations) == 96
    assert len(atlas.streets) == 45
    assert len(atlas.street_connections) == 96
    assert len(atlas.location_links) == 128
    assert atlas.location("loc_5_1_1").name == "白鹭屋"
    assert len(atlas.location("loc_5_1_1").structure) == 7


def test_structure_access_is_typed_and_defaults_to_public() -> None:
    atlas = gray_harbor_atlas()

    assert atlas.location("loc_5_1_2").structure[0].access == "public"
    assert atlas.location("loc_5_1_2").structure[-1].access == "private"
    hidden = atlas.location("loc_5_1_8").structure[-1]
    assert hidden.access == "hidden"
    assert "access" not in (hidden.model_extra or {})


def test_atlas_rejects_unknown_structure_access(tmp_path) -> None:
    document = json.loads(atlas_module.GRAY_HARBOR_ATLAS_PATH.read_text(encoding="utf-8"))
    document["locations"][0]["structure"][0]["access"] = "staff_only"
    target = tmp_path / "invalid-access-atlas.json"
    target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(MapAtlasError, match="access"):
        load_map_atlas(target)


def test_runtime_id_mapping_has_one_current_output_and_rejects_stale_aliases() -> None:
    assert runtime_location_id("loc_5_1_1") == "white_heron_house"
    assert runtime_location_id("runtime_red_mill_tavern") == "red_mill_tavern"
    assert runtime_structure_id("loc_5_1_1__1") == "white_heron_ground_floor"
    assert runtime_street_id("candle_oak") == "oak_street"
    assert atlas_location_id_for_runtime("runtime_oak_street") is None

    location_ids = {
        event.payload["locationId"]
        for event in gray_harbor_events()
        if event.event_type == "location.created"
    }
    assert "oak_street" in location_ids
    assert "runtime_oak_street" not in location_ids


def test_atlas_selection_uses_scenario_identity_not_save_identity() -> None:
    package = gray_harbor_scenario()
    events = compile_initial_events(package, "arbitrary_save_identity")
    state = replay("arbitrary_save_identity", events, len(events))

    assert state.scenario_id == package.manifest.scenario_id
    assert len(state.locations) == 615
    assert atlas_module.atlas_for_scenario(state.scenario_id) is not None
    assert atlas_module.atlas_for_scenario("another-scenario") is None


def test_atlas_route_matches_authored_market_to_chapel_example() -> None:
    route = find_map_route(
        gray_harbor_atlas(),
        "loc_5_1_9",
        "loc_5_1_10",
    )

    assert route.street_path == (
        "candle_oak",
        "candle_back_lane",
        "candle_candle_lane",
    )
    assert route.distance_km == 0.645
    assert route.travel_minutes == 9
    assert route.basis == "street_graph"


def test_atlas_internal_route_uses_parent_structure_time() -> None:
    route = find_map_route(
        gray_harbor_atlas(),
        "loc_5_1_1__1",
        "loc_5_1_1__2",
    )

    assert route.distance_km == 0
    assert route.travel_minutes == 1
    assert route.basis == "internal_structure"


def test_atlas_cache_refreshes_after_generated_file_revision(tmp_path, monkeypatch) -> None:
    source = atlas_module.GRAY_HARBOR_ATLAS_PATH
    target = tmp_path / "location-atlas.json"
    target.write_bytes(source.read_bytes())
    monkeypatch.setattr(atlas_module, "GRAY_HARBOR_ATLAS_PATH", target)
    atlas_module._gray_harbor_atlas_for_revision.cache_clear()

    first = atlas_module.gray_harbor_atlas()
    document = json.loads(target.read_text(encoding="utf-8"))
    document["atlasId"] = "gray-harbor-v42-location-atlas-revision-2"
    target.write_text(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    second = atlas_module.gray_harbor_atlas()

    assert first.atlas_id == "gray-harbor-v42-location-atlas"
    assert second.atlas_id == "gray-harbor-v42-location-atlas-revision-2"
    atlas_module._gray_harbor_atlas_for_revision.cache_clear()


def test_public_map_exposes_safe_atlas_metadata_without_promoting_links() -> None:
    events = gray_harbor_events()
    state = replay(GRAY_HARBOR_CAMPAIGN_ID, events, len(events))

    map_state = public_state(state)["map"]
    assert map_state["atlasId"] == "gray-harbor-v42-location-atlas"
    assert len(map_state["regions"]) == 7
    assert len(map_state["streets"]) == 45

    white_heron = next(
        location
        for location in map_state["locations"]
        if location["locationId"] == "white_heron_house"
    )
    assert white_heron["atlasLocationId"] == "loc_5_1_1"
    assert white_heron["coordinate"] == {
        "xKm": 0.0,
        "yKm": 0.0,
        "basis": "inferred_grid",
    }
    assert white_heron["structureCount"] == 7
    assert "structure" not in white_heron
    assert len(map_state["locationLinks"]) == 124
    assert len(map_state["streetConnections"]) == 96
    assert map_state["displayLocationId"] == "white_heron_house"
    assert map_state["displayLocationName"] == "白鹭屋"
    assert all("atlasStructureId" not in location for location in map_state["locations"])
    assert len(
        [location for location in map_state["locations"] if location.get("atlasLocationId")]
    ) == 95

    assert all(
        not location["locationId"].startswith("catalog_district_")
        for location in map_state["locations"]
    )
    old_harbor = next(
        region for region in map_state["regions"] if region["regionId"] == "old_harbor"
    )
    assert len(old_harbor["anchor"]) == 2
