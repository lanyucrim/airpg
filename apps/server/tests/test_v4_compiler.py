from __future__ import annotations

from pathlib import Path

from trpg_server.story.v4_compiler import compile_v42_markdown, load_v42_catalog


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "灰港_黑潮王座_V4.2_AI_GM主线状态机与支线条件版.md"
CATALOG = ROOT / "content" / "campaigns" / "gray-harbor" / "v4.2-catalog.json"
LEGACY_SOURCE = ROOT / "灰港_黑潮王座_V3_世界自治时间线与组织终局完整版.md"


def test_v42_catalog_compiles_the_authoritative_content_boundaries() -> None:
    catalog = compile_v42_markdown(SOURCE, "gray-harbor-black-tide-throne")

    assert [layer.id for layer in catalog.canon_layers] == ["C0", "C1", "C2", "G"]
    assert len(catalog.mainline_state_machine.states) == 9
    assert len(catalog.districts) == 7
    assert len(catalog.characters) == 139
    assert len(catalog.organizations) == 16
    assert len(catalog.locations) == 84
    assert len(catalog.affordances) == 84
    assert len(catalog.critical_items) == 30
    assert len(catalog.event_seeds) == 96
    assert len(catalog.documents) == 32
    assert len(catalog.timeline) == 60
    assert len(catalog.side_quests) == 94


def test_every_compiled_entry_has_traceable_source_evidence() -> None:
    catalog = compile_v42_markdown(SOURCE, "gray-harbor-black-tide-throne")
    collections = (
        catalog.mainline_state_machine.states,
        catalog.districts,
        catalog.characters,
        catalog.organizations,
        catalog.locations,
        catalog.affordances,
        catalog.critical_items,
        catalog.event_seeds,
        catalog.documents,
        catalog.timeline,
        catalog.side_quests,
        catalog.generation_policies,
        catalog.audit_rules,
    )

    for entry in (entry for values in collections for entry in values):
        assert entry.sources
        assert all(source.source_line > 0 for source in entry.sources)
        assert all(source.source_end_line >= source.source_line for source in entry.sources)
        assert all(len(source.source_fingerprint) == 64 for source in entry.sources)


def test_g_layer_templates_never_compile_as_world_facts() -> None:
    catalog = compile_v42_markdown(SOURCE, "gray-harbor-black-tide-throne")

    assert all(entry.canon_layer == "G" for entry in catalog.event_seeds)
    assert all(entry.canon_layer == "G" for entry in catalog.documents)
    assert all(entry.canon_layer == "G" for entry in catalog.side_quests)
    assert all(not entry.instantiated for entry in catalog.event_seeds)
    assert all(not entry.instantiated for entry in catalog.documents)
    assert all(not entry.instantiated for entry in catalog.side_quests)
    assert all(
        entry.canon_layer == "G" and not entry.instantiated
        for entry in catalog.timeline[36:]
    )


def test_location_affordances_are_generation_boundaries_not_action_whitelists() -> None:
    catalog = compile_v42_markdown(SOURCE, "gray-harbor-black-tide-throne")

    assert {entry.attributes["locationId"] for entry in catalog.affordances} == {
        entry.attributes["locationId"] for entry in catalog.locations
    }
    assert all(entry.attributes["notActionWhitelist"] for entry in catalog.affordances)
    assert all(
        entry.attributes["storyImpactCeiling"] == "soft"
        for entry in catalog.affordances
    )
    assert all(
        entry.attributes["requiresValidatedInstantiationEvent"]
        for entry in catalog.affordances
    )


def test_v42_compilation_does_not_depend_on_deleted_v3_source() -> None:
    assert not LEGACY_SOURCE.exists()
    catalog = compile_v42_markdown(SOURCE, "gray-harbor-black-tide-throne")
    assert catalog.source_document == SOURCE.name
    assert catalog.source_sha256 == (
        "4795d15a7b03925110456bae6573f66cf7e05bd8cc0017d1f924bee42bef8f3a"
    )


def test_checked_in_catalog_matches_the_current_source_exactly() -> None:
    checked_in = load_v42_catalog(CATALOG)
    compiled = compile_v42_markdown(SOURCE, checked_in.scenario_id)

    assert checked_in.source_sha256 == compiled.source_sha256
    assert checked_in.model_dump() == compiled.model_dump()
