from __future__ import annotations

import json
from pathlib import Path
import shutil

from trpg_server.core.projection import public_state, replay
from trpg_server.core.state import Event
from trpg_server.characters.traits import ABILITY_CATALOG
from trpg_server.story.scenario import compile_initial_events, load_scenario_package


CAMPAIGN = Path(__file__).resolve().parents[3] / "content" / "campaigns" / "gray-harbor"
LANGUAGE_STYLE_KEYS = {
    "formality",
    "politeness",
    "directness",
    "verbosity",
    "pacing",
    "sentenceStyle",
    "addressTerms",
    "catchphrases",
    "pressureShift",
    "taboos",
    "sourceStatus",
    "sourceRefs",
    "notes",
}


def test_gray_harbor_bootstrap_has_profile_metadata_for_every_character() -> None:
    assert 20 <= len(ABILITY_CATALOG) <= 60
    package = load_scenario_package(CAMPAIGN)
    events = compile_initial_events(package, "cmp_profile_metadata_all")
    character_events = [
        event for event in events if event.event_type == "character.created"
    ]
    assert len(character_events) == 142
    by_id = {event.payload["characterId"]: event.payload for event in character_events}
    assert "catalog_p011" not in by_id
    assert by_id["harvey_cole"]["name"] == "哈维·科尔"
    assert by_id["harvey_cole"]["catalogCharacterId"] == "P011"
    assert by_id["harvey_cole"]["catalogId"] == "CHARACTER-P011"
    assert by_id["protagonist"]["abilities"] == []
    assert by_id["protagonist"]["languageStyle"]["sourceStatus"] == "player_defined"
    assert by_id["martha_bell"]["abilities"]
    assert by_id["martha_bell"]["languageStyle"]["sourceStatus"] == "canon"
    for event in character_events:
        payload = event.payload
        assert isinstance(payload.get("abilities"), list)
        assert isinstance(payload.get("languageStyle"), dict)
        assert set(payload["languageStyle"]) == LANGUAGE_STYLE_KEYS
        assert payload["languageStyle"]["sourceStatus"] in {
            "canon",
            "inferred",
            "unknown",
            "player_defined",
        }
        for ability in payload["abilities"]:
            assert set(
                (
                    "abilityId",
                    "name",
                    "level",
                    "sourceStatus",
                    "confidence",
                    "basis",
                    "sourceRefs",
                    "notes",
                )
            ) <= set(ability)


def test_character_profile_metadata_compiles_and_replays(tmp_path: Path) -> None:
    package_path = tmp_path / "scenario"
    shutil.copytree(CAMPAIGN, package_path)
    characters_path = package_path / "characters.json"
    document = json.loads(characters_path.read_text(encoding="utf-8"))
    protagonist = document["characters"][0]
    protagonist["abilities"] = [
        {
            "abilityId": "street_memory",
            "name": "街区记忆",
            "level": "working",
            "sourceStatus": "player_defined",
            "confidence": None,
            "basis": "玩家选择",
            "sourceRefs": [],
            "notes": "",
        }
    ]
    protagonist["languageStyle"] = {
        "formality": "口语",
        "politeness": None,
        "directness": "直接",
        "verbosity": None,
        "pacing": "快",
        "sentenceStyle": None,
        "addressTerms": ["你"],
        "catchphrases": [],
        "pressureShift": None,
        "taboos": [],
        "sourceStatus": "player_defined",
        "sourceRefs": [],
        "notes": "",
    }
    characters_path.write_text(
        json.dumps(document, ensure_ascii=False),
        encoding="utf-8",
    )

    package = load_scenario_package(package_path)
    events = compile_initial_events(package, "cmp_profile_metadata")
    character_event = next(
        event
        for event in events
        if event.event_type == "character.created"
        and event.payload["characterId"] == "protagonist"
    )
    assert character_event.payload["abilities"][0]["abilityId"] == "street_memory"
    assert character_event.payload["languageStyle"]["directness"] == "直接"

    projection = replay("cmp_profile_metadata", events, len(events))
    profile = projection.character_profiles["protagonist"]
    assert profile["abilities"][0]["level"] == "working"
    assert profile["languageStyle"]["sourceStatus"] == "player_defined"
    assert profile["catalogCharacterId"] is None

    visible = public_state(projection)
    assert "abilities" not in visible["player"]["profile"]
    assert "languageStyle" not in visible["player"]["profile"]


def test_legacy_character_event_gets_stable_empty_profile_metadata() -> None:
    event = Event(
        event_id="evt_legacy_character",
        event_type="character.created",
        actor_id="system",
        world_time=0,
        payload={
            "characterId": "legacy_npc",
            "characterType": "npc",
            "name": "旧人物",
            "locationId": "room",
        },
        schema_version=1,
    )
    projection = replay("cmp_legacy_profile", [event], 1)
    profile = projection.character_profiles["legacy_npc"]
    assert profile["catalogCharacterId"] is None
    assert profile["catalogId"] is None
    assert profile["abilities"] == []
    assert profile["languageStyle"]["sourceStatus"] == "unknown"
    assert profile["languageStyle"]["addressTerms"] == []
