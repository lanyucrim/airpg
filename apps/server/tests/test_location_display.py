from __future__ import annotations

from copy import deepcopy

from trpg_server.core.projection import public_state, replay
from trpg_server.map.atlas import gray_harbor_atlas
from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events


def _public_state_at(location_id: str) -> dict[str, object]:
    events = gray_harbor_events()
    state = replay(GRAY_HARBOR_CAMPAIGN_ID, events, len(events))
    state = deepcopy(state)
    state.character_locations[state.player_character_id] = location_id
    state.location_id = location_id
    return public_state(state)


def test_exact_location_display_includes_region_place_and_structure() -> None:
    result = _public_state_at("atlas_room_loc_5_1_7__1")
    map_state = result["map"]
    structure_name = gray_harbor_atlas().location("loc_5_1_7").structure[0].name

    assert map_state["locationPath"] == ["烛巷", "夜莺歌厅", structure_name]
    assert map_state["currentLocationDisplayName"] == f"烛巷·夜莺歌厅·{structure_name}"
    assert map_state["currentStructureName"] == structure_name
    assert result["scene"]["currentLocationDisplayName"] == (
        f"烛巷·夜莺歌厅·{structure_name}"
    )
    assert result["currentLocationDisplayName"] == (
        f"烛巷·夜莺歌厅·{structure_name}"
    )


def test_exact_location_display_keeps_building_and_street_distinct() -> None:
    building = _public_state_at("catalog_l007")["map"]
    assert building["locationPath"] == ["烛巷", "夜莺歌厅"]
    assert building["currentStructureName"] is None

    street = _public_state_at("oak_street")["map"]
    assert street["locationPath"] == ["烛巷", "栎木街"]
    assert street["currentLocationDisplayName"] == "烛巷·栎木街"
    assert street["currentStructureName"] is None


def test_city_scope_fallback_does_not_expose_atlas_metadata_suffix() -> None:
    tavern = _public_state_at("red_mill_tavern")["map"]
    assert tavern["locationPath"] == ["灰港", "红磨坊酒馆"]
