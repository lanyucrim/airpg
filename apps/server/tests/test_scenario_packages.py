from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from trpg_server.core.state import Event
from trpg_server.core.projection import public_state, replay
from trpg_server.story.scenario import (
    ScenarioPackageError,
    compile_initial_events,
    load_scenario_package,
)
from trpg_server.core.store import EventStore


CAMPAIGNS_PATH = Path(__file__).resolve().parents[3] / "content" / "campaigns"
PACKAGE_PATH = CAMPAIGNS_PATH / "gray-harbor"
GRAY_HARBOR_PATH = PACKAGE_PATH


def package_copy(tmp_path: Path) -> Path:
    target = tmp_path / "scenario"
    shutil.copytree(PACKAGE_PATH, target)
    return target


@pytest.mark.parametrize(
    ("visible", "discovery_id"),
    ((False, None), (True, "cellar_drainage_tunnel")),
)
def test_exit_visibility_and_discovery_binding_must_agree(
    tmp_path: Path,
    visible: bool,
    discovery_id: str | None,
) -> None:
    target = package_copy(tmp_path)
    path = target / "locations.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    tunnel = next(
        exit_definition
        for location in document["locations"]
        for exit_definition in location.get("exits", [])
        if exit_definition.get("id") == "cellar_tunnel_to_bakery"
    )
    tunnel["visible"] = visible
    if discovery_id is None:
        tunnel.pop("discoveryId", None)
    else:
        tunnel["discoveryId"] = discovery_id
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ScenarioPackageError, match="locations.json"):
        load_scenario_package(target)


def rewrite(path: Path, filename: str, mutate: object) -> None:
    document = json.loads((path / filename).read_text(encoding="utf-8"))
    mutate(document)  # type: ignore[operator]
    (path / filename).write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_valid_package_loads_with_stable_identity() -> None:
    first = load_scenario_package(PACKAGE_PATH)
    second = load_scenario_package(PACKAGE_PATH)

    assert first.manifest.scenario_id == "gray-harbor-black-tide-throne"
    assert first.manifest.version == "0.8.0"
    assert first.manifest.source_version == "4.2"
    assert first.catalog is not None
    assert first.catalog.source_document == (
        "灰港_黑潮王座_V4.2_AI_GM主线状态机与支线条件版.md"
    )
    assert len(first.catalog.characters) == 139
    assert len(first.content_hash) == 64
    assert second.content_hash == first.content_hash


def test_invalid_schema_is_rejected(tmp_path: Path) -> None:
    package_path = package_copy(tmp_path)
    rewrite(
        package_path,
        "manifest.json",
        lambda document: document.update({"schemaVersion": 99}),
    )

    with pytest.raises(ScenarioPackageError, match="manifest.json"):
        load_scenario_package(package_path)


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    package_path = package_copy(tmp_path)

    def duplicate_location(document: dict[str, object]) -> None:
        locations = document["locations"]
        assert isinstance(locations, list)
        locations.append(dict(locations[0]))

    rewrite(package_path, "locations.json", duplicate_location)

    with pytest.raises(ScenarioPackageError, match="duplicate location id"):
        load_scenario_package(package_path)


def test_missing_references_are_rejected(tmp_path: Path) -> None:
    package_path = package_copy(tmp_path)

    def break_item_reference(document: dict[str, object]) -> None:
        instances = document["instances"]
        assert isinstance(instances, list)
        instances[0]["containerId"] = "container_missing"

    rewrite(package_path, "items.json", break_item_reference)

    with pytest.raises(
        ScenarioPackageError,
        match="item seed differs from its atlas initial instance",
    ):
        load_scenario_package(package_path)


def test_unknown_accepted_gift_definition_is_rejected(tmp_path: Path) -> None:
    package_path = package_copy(tmp_path)

    def break_gift_reference(document: dict[str, object]) -> None:
        characters = document["characters"]
        assert isinstance(characters, list)
        characters[1]["acceptedGiftDefinitionIds"] = ["missing_definition"]

    rewrite(package_path, "characters.json", break_gift_reference)

    with pytest.raises(ScenarioPackageError, match="accepted gift definition"):
        load_scenario_package(package_path)


def test_unknown_authored_catalog_character_id_is_rejected(tmp_path: Path) -> None:
    package_path = package_copy(tmp_path)

    def break_catalog_character_reference(document: dict[str, object]) -> None:
        characters = document["characters"]
        assert isinstance(characters, list)
        characters[1]["catalogCharacterId"] = "P999"

    rewrite(package_path, "characters.json", break_catalog_character_reference)

    with pytest.raises(
        ScenarioPackageError,
        match="missing authored catalog character reference: P999",
    ):
        load_scenario_package(package_path)


def test_duplicate_authored_catalog_character_id_is_rejected(tmp_path: Path) -> None:
    package_path = package_copy(tmp_path)

    def duplicate_catalog_character_reference(document: dict[str, object]) -> None:
        characters = document["characters"]
        assert isinstance(characters, list)
        characters[2]["catalogCharacterId"] = characters[1]["catalogCharacterId"]

    rewrite(package_path, "characters.json", duplicate_catalog_character_reference)

    with pytest.raises(
        ScenarioPackageError,
        match="duplicate authored catalog character id: P001",
    ):
        load_scenario_package(package_path)


def test_package_compiles_to_deterministic_replayable_initial_events() -> None:
    package = load_scenario_package(PACKAGE_PATH)
    first = compile_initial_events(package, "cmp_gray_harbor")
    second = compile_initial_events(package, "cmp_gray_harbor")

    assert first == second
    assert all(
        event.schema_version == (
            4
            if event.event_type == "item.created"
            else 2
            if event.event_type in {
                "scene.started",
                "character.created",
                "location.created",
            }
            else 1
        )
        for event in first
    )
    assert first[0].event_type == "campaign.created"
    assert first[0].payload["scenarioContentHash"] == package.content_hash
    assert first[0].payload["scenarioSourceVersion"] == "4.2"
    assert first[0].payload["scenarioSourceSha256"] == (
        "4795d15a7b03925110456bae6573f66cf7e05bd8cc0017d1f924bee42bef8f3a"
    )
    assert first[0].payload["scenarioCatalogSchemaVersion"] == 2

    state = public_state(replay("cmp_gray_harbor", first, 1))
    assert state["name"] == "灰港：黑潮王座"
    assert state["player"]["name"] == "艾拉·帕克"
    assert state["player"]["profile"]["birthplace"] == "灰港黑坡区·北沟"
    assert "name" not in state["player"]["profile"]["playerDefinedFields"]
    assert state["worldTime"] == 0
    assert state["scene"]["locationId"] == "white_heron_ground_floor"
    assert {item["itemId"] for item in state["player"]["inventory"]} == {
        "protagonist_small_knife"
    }
    player_item = next(
        item
        for item in state["player"]["inventory"]
        if item["itemId"] == "protagonist_small_knife"
    )
    assert player_item["category"] == "tool"
    assert player_item["isPlotItem"] is False
    projection = replay("cmp_gray_harbor", first, 1)
    assert projection.decision_profiles["harvey_cole"].monthly_income_pence == 720
    knife_event = next(
        event for event in first
        if event.event_type == "item.created"
        and event.payload["item"]["id"] == "protagonist_small_knife"
    )
    assert knife_event.schema_version == 4
    assert knife_event.payload == {
        "item": {
            "id": "protagonist_small_knife",
            "definitionId": "small_personal_knife",
            "name": "小刀",
            "description": "剧本未说明该物品的外形、材质与尺寸。",
            "category": "tool",
            "isPlotItem": False,
            "quantity": 1,
            "stackable": False,
            "unitWeightGrams": None,
            "valueCrown": None,
            "condition": "intact",
            "durability": {"current": 85.0, "max": 100.0},
            "containerId": "protagonist_equipment",
            "locationId": None,
            "properties": {
                "equipment": {
                    "mode": "held",
                    "slotIds": ["left_hand", "right_hand"],
                    "handCount": 1,
                }
            },
        }
    }


def test_schema_v5_requires_player_safe_narrative_guidance(tmp_path: Path) -> None:
    package_path = package_copy(tmp_path)

    def remove_guidance(document: dict[str, object]) -> None:
        scenes = document["scenes"]
        assert isinstance(scenes, list)
        scenes[0].pop("narrativeGuidance")

    rewrite(package_path, "scenes.json", remove_guidance)

    with pytest.raises(ScenarioPackageError, match="requires narrativeGuidance"):
        load_scenario_package(package_path)


def test_schema_v5_rejects_gm_secret_in_narrative_guidance(
    tmp_path: Path,
) -> None:
    package_path = package_copy(tmp_path)

    def leak_secret(document: dict[str, object]) -> None:
        scenes = document["scenes"]
        assert isinstance(scenes, list)
        scenes[0]["narrativeGuidance"]["premise"] = "jenny_forged_additional_loan"

    rewrite(package_path, "scenes.json", leak_secret)

    with pytest.raises(
        ScenarioPackageError,
        match="narrativeGuidance contains GM fact",
    ):
        load_scenario_package(package_path)


def test_schema_v6_requires_decision_profile_for_every_npc(tmp_path: Path) -> None:
    package_path = package_copy(tmp_path)

    def remove_profile(document: dict[str, object]) -> None:
        characters = document["characters"]
        assert isinstance(characters, list)
        characters[1].pop("decisionProfile")

    rewrite(package_path, "characters.json", remove_profile)

    with pytest.raises(ScenarioPackageError, match="requires decisionProfile"):
        load_scenario_package(package_path)


def test_scene_started_rejects_unknown_event_schema_version() -> None:
    package = load_scenario_package(PACKAGE_PATH)
    events = compile_initial_events(package, "cmp_unknown_scene_schema")
    scene_index = next(
        index for index, event in enumerate(events)
        if event.event_type == "scene.started"
    )
    scene_event = events[scene_index]
    events[scene_index] = Event(
        event_id=scene_event.event_id,
        event_type=scene_event.event_type,
        actor_id=scene_event.actor_id,
        world_time=scene_event.world_time,
        payload=scene_event.payload,
        schema_version=99,
    )

    with pytest.raises(ValueError, match="unsupported scene.started schema version"):
        replay("cmp_unknown_scene_schema", events, 1)


def test_character_created_rejects_unknown_event_schema_version() -> None:
    package = load_scenario_package(PACKAGE_PATH)
    events = compile_initial_events(package, "cmp_unknown_character_schema")
    character_index = next(
        index for index, event in enumerate(events)
        if event.event_type == "character.created"
    )
    character_event = events[character_index]
    events[character_index] = Event(
        event_id=character_event.event_id,
        event_type=character_event.event_type,
        actor_id=character_event.actor_id,
        world_time=character_event.world_time,
        payload=character_event.payload,
        schema_version=99,
    )

    with pytest.raises(ValueError, match="unsupported character.created schema version"):
        replay("cmp_unknown_character_schema", events, 1)


def test_content_change_produces_a_new_package_hash(tmp_path: Path) -> None:
    package_path = package_copy(tmp_path)
    original = load_scenario_package(package_path)

    def rename_location(document: dict[str, object]) -> None:
        locations = document["locations"]
        assert isinstance(locations, list)
        locations[0]["name"] = "灰港（修订版）"

    rewrite(package_path, "locations.json", rename_location)
    changed = load_scenario_package(package_path)

    assert changed.content_hash != original.content_hash


def test_catalog_change_produces_a_new_package_hash(tmp_path: Path) -> None:
    package_path = package_copy(tmp_path)
    original = load_scenario_package(package_path)

    def change_excerpt(document: dict[str, object]) -> None:
        audit_rules = document["auditRules"]
        assert isinstance(audit_rules, list)
        audit_rules[0]["sources"][0]["excerpt"] = "测试用目录变化"

    rewrite(package_path, "v4.2-catalog.json", change_excerpt)
    changed = load_scenario_package(package_path)

    assert changed.content_hash != original.content_hash


def test_manifest_rejects_catalog_from_another_source(tmp_path: Path) -> None:
    package_path = package_copy(tmp_path)

    rewrite(
        package_path,
        "manifest.json",
        lambda document: document.update({"sourceSha256": "0" * 64}),
    )

    with pytest.raises(ScenarioPackageError, match="catalog sourceSha256"):
        load_scenario_package(package_path)


def test_schema_v3_rejects_seed_with_item_fields_outside_the_contract(
    tmp_path: Path,
) -> None:
    package_path = package_copy(tmp_path)

    def add_retired_field(document: dict[str, object]) -> None:
        instances = document["instances"]
        assert isinstance(instances, list)
        instances[0]["criticality"] = "major_key"

    rewrite(package_path, "items.json", add_retired_field)

    with pytest.raises(ScenarioPackageError, match="items.json"):
        load_scenario_package(package_path)


def test_schema_v3_rejects_seed_that_drifts_from_the_atlas(
    tmp_path: Path,
) -> None:
    package_path = package_copy(tmp_path)

    def change_story_flag(document: dict[str, object]) -> None:
        instances = document["instances"]
        assert isinstance(instances, list)
        story_item = next(
            item for item in instances if item["id"] == "iron_hooks_final_notice"
        )
        story_item["isPlotItem"] = False

    rewrite(package_path, "items.json", change_story_flag)

    with pytest.raises(ScenarioPackageError, match="item seed differs from its atlas"):
        load_scenario_package(package_path)


def test_existing_sqlite_schema_is_migrated_without_losing_campaign(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE campaigns (
                campaign_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                state_version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO campaigns VALUES (?, ?, ?, ?)",
            ("cmp_legacy", "旧存档", 7, "2026-01-01T00:00:00+00:00"),
        )
        connection.execute(
            """
            CREATE TABLE intent_attempts (
                attempt_id TEXT PRIMARY KEY,
                campaign_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                command_id TEXT NOT NULL,
                status TEXT NOT NULL,
                model_name TEXT,
                request_json TEXT,
                response_json TEXT,
                failure_code TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO intent_attempts VALUES (
                'intent_legacy', 'cmp_legacy', 'turn_legacy', 'cmd_legacy',
                'local', NULL, NULL, NULL, NULL, '2026-01-01T00:00:00+00:00'
            )
            """
        )

    store = EventStore(database_path)
    store.initialize()

    with store.connect() as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(campaigns)")
        }
        row = connection.execute(
            "SELECT name, state_version, scenario_id FROM campaigns WHERE campaign_id = ?",
            ("cmp_legacy",),
        ).fetchone()
        intent_table = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name = 'intent_attempts'"
        ).fetchone()
        npc_decision_table = connection.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type = 'table' AND name = 'npc_decision_attempts'"
        ).fetchone()
        intent_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(intent_attempts)")
        }
        legacy_intent = connection.execute(
            """
            SELECT attempt_id, provider_name, total_tokens, latency_ms
            FROM intent_attempts WHERE attempt_id = 'intent_legacy'
            """
        ).fetchone()

    assert {
        "scenario_id",
        "scenario_version",
        "scenario_content_hash",
    } <= columns
    assert dict(row) == {
        "name": "旧存档",
        "state_version": 7,
        "scenario_id": None,
    }
    assert intent_table is not None
    assert npc_decision_table is not None
    assert {
        "provider_name",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "latency_ms",
    } <= intent_columns
    assert dict(legacy_intent) == {
        "attempt_id": "intent_legacy",
        "provider_name": None,
        "total_tokens": None,
        "latency_ms": None,
    }


def test_initial_relationship_is_an_event_with_an_auditable_source(
    tmp_path: Path,
) -> None:
    package_path = package_copy(tmp_path)

    def add_relationship(document: dict[str, object]) -> None:
        relationships = document["relationships"]
        assert isinstance(relationships, list)
        relationship = next(
            value
            for value in relationships
            if value["subjectId"] == "martha_bell"
            and value["objectId"] == "protagonist"
        )
        relationship["favor"] = 3

    rewrite(package_path, "relationships.json", add_relationship)
    package = load_scenario_package(package_path)
    events = compile_initial_events(package, "cmp_initial_relationship")
    projection = replay("cmp_initial_relationship", events, 1)
    relationship = projection.relationship("martha_bell", "protagonist")
    source_event = next(
        event
        for event in events
        if event.event_type == "relationship.initialized"
        and event.payload["subjectId"] == "martha_bell"
        and event.payload["objectId"] == "protagonist"
    )

    assert relationship.favor == 3
    assert relationship.sources["favor"] == [source_event.event_id]


def test_gray_harbor_v8_compiles_opening_world_without_exposing_gm_secrets() -> None:
    package = load_scenario_package(GRAY_HARBOR_PATH)
    events = compile_initial_events(package, "cmp_gray_harbor_opening")
    projection = replay("cmp_gray_harbor_opening", events, 1)
    state = public_state(projection)

    assert package.manifest.schema_version == 8
    assert package.catalog is not None
    assert package.catalog.scenario_version == "4.2"
    assert package.manifest.scenario_id == "gray-harbor-black-tide-throne"
    assert state["name"] == "灰港：黑潮王座"
    assert state["scene"]["locationId"] == "white_heron_ground_floor"
    assert state["scene"]["name"] == "白鹭屋一楼大厅"
    assert state["worldTimeLabel"] == "海历621年10月17日 · 23:00"
    assert {entry["organizationId"] for entry in state["organizations"]} >= {
        "white_heron_house",
        "iron_hooks",
    }
    assert state["activeClocks"] == [{
        "clockId": "white_heron_seven_day_deadline",
        "name": "白鹭屋最后期限",
        "deadline": 10080,
        "remainingMinutes": 10080,
        "status": "active",
    }]
    assert state["obligations"] == [{
        "obligationId": "white_heron_debt",
        "title": "白鹭屋债务",
        "kind": "debt",
        "status": "active",
        "dueClockId": "white_heron_seven_day_deadline",
    }]
    serialized = repr(state)
    assert "珍妮伪造" not in serialized
    assert "西蒙故意利用" not in serialized
    assert "黑潮计划" not in serialized
    assert "旧排水通道" not in serialized
    assert "废弃面包房地下室" not in serialized
    assert "cellar_drainage_tunnel" not in serialized
    assert "cellar_tunnel_to_bakery" in projection.discovered_exits["martha_bell"]
    assert "cellar_tunnel_to_bakery" in projection.discovered_exits["otis_finn"]
    assert "cellar_tunnel_to_bakery" not in projection.discovered_exits.get(
        "protagonist",
        set(),
    )
    assert {
        action["interactionId"] for action in state["availableActions"]
    } == {
        "ask_martha_about_debt",
        "inspect_iron_hooks_final_notice",
        "inspect_white_heron_operating_ledger",
    }
    assert "伪造签名" not in repr(state["availableActions"])


def test_gray_harbor_v3_rejects_hidden_fact_without_authoritative_definition(
    tmp_path: Path,
) -> None:
    target = tmp_path / "gray-harbor"
    shutil.copytree(GRAY_HARBOR_PATH, target)

    def break_clue_fact(document: dict[str, object]) -> None:
        clues = document["clues"]
        assert isinstance(clues, list)
        clues[0]["factId"] = "missing_fact"

    rewrite(target, "clues.json", break_clue_fact)

    with pytest.raises(ScenarioPackageError, match="missing clue fact reference"):
        load_scenario_package(target)


def test_gray_harbor_v3_rejects_obligation_with_unknown_party(tmp_path: Path) -> None:
    target = tmp_path / "gray-harbor"
    shutil.copytree(GRAY_HARBOR_PATH, target)

    def break_debtor(document: dict[str, object]) -> None:
        obligations = document["obligations"]
        assert isinstance(obligations, list)
        obligations[0]["debtorId"] = "missing_party"

    rewrite(target, "obligations.json", break_debtor)

    with pytest.raises(ScenarioPackageError, match="missing obligation debtor reference"):
        load_scenario_package(target)


def test_gm_only_organization_is_authoritative_but_not_public(tmp_path: Path) -> None:
    target = tmp_path / "gray-harbor"
    shutil.copytree(GRAY_HARBOR_PATH, target)

    def add_hidden_organization(document: dict[str, object]) -> None:
        organizations = document["organizations"]
        assert isinstance(organizations, list)
        organizations.append({
            "id": "hidden_test_group",
            "name": "只有GM知道的组织",
            "type": "conspiracy",
            "visibility": "gm",
        })

    rewrite(target, "organizations.json", add_hidden_organization)
    package = load_scenario_package(target)
    projection = replay(
        "cmp_hidden_organization",
        compile_initial_events(package, "cmp_hidden_organization"),
        1,
    )

    assert "hidden_test_group" in projection.organizations
    assert all(
        value["organizationId"] != "hidden_test_group"
        for value in public_state(projection)["organizations"]
    )


def test_v3_exit_rejects_unknown_discovery_reference(tmp_path: Path) -> None:
    target = package_copy(tmp_path)

    def break_exit_discovery(document: dict[str, object]) -> None:
        locations = document["locations"]
        assert isinstance(locations, list)
        cellar = next(value for value in locations if value["id"] == "white_heron_cellar")
        tunnel = next(
            value
            for value in cellar["exits"]
            if value["id"] == "cellar_tunnel_to_bakery"
        )
        tunnel["discoveryId"] = "missing_discovery"

    rewrite(target, "locations.json", break_exit_discovery)

    with pytest.raises(ScenarioPackageError, match="missing exit discovery reference"):
        load_scenario_package(target)


def test_v3_discovery_rejects_unknown_story_condition(tmp_path: Path) -> None:
    target = package_copy(tmp_path)

    def break_discovery_condition(document: dict[str, object]) -> None:
        discoveries = document["discoveries"]
        assert isinstance(discoveries, list)
        discoveries[0]["requiredConditionIds"] = ["missing_condition"]

    rewrite(target, "discoveries.json", break_discovery_condition)

    with pytest.raises(ScenarioPackageError, match="missing discovery condition reference"):
        load_scenario_package(target)


def test_v4_inspection_rejects_unknown_item_reference(tmp_path: Path) -> None:
    target = package_copy(tmp_path)

    def break_inspection_item(document: dict[str, object]) -> None:
        inspections = document["inspections"]
        assert isinstance(inspections, list)
        inspections[0]["targetItemId"] = "missing_item"

    rewrite(target, "interactions.json", break_inspection_item)

    with pytest.raises(ScenarioPackageError, match="missing inspection item reference"):
        load_scenario_package(target)


def test_v4_inquiry_rejects_unknown_npc_knowledge_fact(tmp_path: Path) -> None:
    target = package_copy(tmp_path)

    def break_inquiry_fact(document: dict[str, object]) -> None:
        inquiries = document["inquiries"]
        assert isinstance(inquiries, list)
        inquiries[0]["requiredNpcKnowledgeFactIds"] = ["missing_fact"]

    rewrite(target, "interactions.json", break_inquiry_fact)

    with pytest.raises(
        ScenarioPackageError,
        match="missing inquiry npc knowledge fact reference",
    ):
        load_scenario_package(target)
