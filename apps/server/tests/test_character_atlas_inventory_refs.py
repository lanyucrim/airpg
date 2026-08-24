from __future__ import annotations

import json
from pathlib import Path

from trpg_server.story.scenario import compile_initial_events, load_scenario_package


CAMPAIGN_PATH = Path(__file__).resolve().parents[3] / "content" / "campaigns" / "gray-harbor"
CHARACTER_ATLAS_PATH = CAMPAIGN_PATH / "characters-atlas"


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_character_inventory_atlas_uses_item_references_not_item_copies() -> None:
    inventory_atlas = load_json(CHARACTER_ATLAS_PATH / "character-inventories.json")

    assert inventory_atlas["schemaVersion"] == 3
    assert inventory_atlas["itemAtlasRef"] == "../items-atlas/important-items.json"
    inventories = inventory_atlas["inventories"]
    assert isinstance(inventories, list)

    legacy_item_fields = {
        "id",
        "name",
        "description",
        "category",
        "isPlotItem",
        "quantity",
        "stackable",
        "unitWeightGrams",
        "valueCrown",
        "condition",
        "durability",
        "locationId",
        "properties",
        "criticality",
        "operations",
        "aliases",
        "rights",
        "storyBindingPolicy",
        "ownershipBasis",
    }
    for inventory in inventories:
        assert isinstance(inventory, dict)
        assert "items" not in inventory
        assert isinstance(inventory["itemRefs"], list)
        for item_ref in inventory["itemRefs"]:
            assert isinstance(item_ref, dict)
            assert set(item_ref) == {
                "instanceId",
                "definitionId",
                "containerId",
                "source",
            }
            assert not legacy_item_fields.intersection(item_ref)


def test_character_inventory_references_resolve_to_owned_container_items() -> None:
    inventory_atlas = load_json(CHARACTER_ATLAS_PATH / "character-inventories.json")
    item_atlas = load_json(CAMPAIGN_PATH / "items-atlas" / "important-items.json")
    instances = item_atlas["instances"]
    assert isinstance(instances, list)
    instances_by_id = {item["id"]: item for item in instances if isinstance(item, dict)}

    inventories = inventory_atlas["inventories"]
    assert isinstance(inventories, list)
    for inventory in inventories:
        assert isinstance(inventory, dict)
        container_ids = {
            container["id"]
            for container in inventory["containers"]
            if isinstance(container, dict)
        }
        for item_ref in inventory["itemRefs"]:
            assert isinstance(item_ref, dict)
            instance = instances_by_id[item_ref["instanceId"]]
            assert instance["definitionId"] == item_ref["definitionId"]
            assert instance["containerId"] == item_ref["containerId"]
            assert item_ref["containerId"] in container_ids
            assert item_ref["source"] == {"status": "canon", "file": "items.json"}


def test_character_atlas_has_one_profile_and_inventory_per_runtime_character() -> None:
    profiles_document = load_json(CHARACTER_ATLAS_PATH / "character-profiles.json")
    inventory_document = load_json(CHARACTER_ATLAS_PATH / "character-inventories.json")
    profiles = profiles_document["characters"]
    inventories = inventory_document["inventories"]
    assert isinstance(profiles, list)
    assert isinstance(inventories, list)

    events = compile_initial_events(
        load_scenario_package(CAMPAIGN_PATH),
        "cmp_character_atlas_identity",
    )
    runtime_ids = {
        event.payload["characterId"]
        for event in events
        if event.event_type == "character.created"
    }
    atlas_runtime_ids = {profile["runtimeCharacterId"] for profile in profiles}

    assert len(profiles) == 142
    assert len(atlas_runtime_ids) == len(profiles)
    assert atlas_runtime_ids == runtime_ids
    assert "catalog_p011" not in runtime_ids
    assert next(profile for profile in profiles if profile["id"] == "P011")[
        "runtimeCharacterId"
    ] == "harvey_cole"
    for runtime_id in ("iron_hook_collector_one", "iron_hook_collector_two"):
        profile = next(profile for profile in profiles if profile["id"] == runtime_id)
        assert profile["runtimeCharacterId"] == runtime_id
        assert profile["canonProfile"]["canonLayer"] == "runtime_supplement"
        assert profile["supplementalProfile"]["status"] == (
            "runtime_authored_non_catalog_character"
        )

    inventories_by_character = {
        inventory["characterId"]: inventory for inventory in inventories
    }
    assert set(inventories_by_character) == {profile["id"] for profile in profiles}
    for inventory in inventories:
        inventory_containers = [
            container
            for container in inventory["containers"]
            if container["kind"] == "inventory"
        ]
        assert len(inventory_containers) == 1
