from __future__ import annotations

from dataclasses import replace

import pytest

from trpg_server.story.bootstrap import gray_harbor_events
from trpg_server.core.state import Event, Projection
from trpg_server.items.models import ItemInstance
from trpg_server.memory import (
    EpisodicMemory,
    MemoryEntity,
    MemoryQuery,
    MemoryScope,
    project_event_to_memory,
    project_memory_read_model,
    select_memories,
)


def _state() -> Projection:
    state = Projection("cmp_test", player_character_id="pc_ella")
    state.character_names.update({"pc_ella": "艾拉", "npc_harvey": "哈维", "npc_martha": "玛莎"})
    state.character_locations.update({
        "pc_ella": "loc_office",
        "npc_harvey": "loc_office",
        "npc_martha": "loc_office",
    })
    state.items["item_knife"] = ItemInstance(
        item_id="item_knife",
        definition_id="def_knife",
        name="普通小刀",
        description="一把普通小刀。",
        category="tool",
        is_plot_item=False,
        quantity=1,
        stackable=False,
        unit_weight_grams=None,
        value_crown=None,
        condition="intact",
        durability=None,
        container_id="inv_ella",
        location_id=None,
        properties={},
    )
    return state


def _gift_event(event_id: str = "evt_gift", world_time: int = 10) -> Event:
    return Event(event_id, "gift.accepted", "npc_harvey", world_time, {
        "actorId": "pc_ella",
        "targetId": "npc_harvey",
        "itemId": "item_knife",
        "offerEventId": "evt_offer",
    })


def test_only_allowlisted_confirmed_events_project_to_memory() -> None:
    state = _state()
    memory = project_event_to_memory("cmp_test", _gift_event(), state)

    assert memory is not None
    assert memory.memory_id == "mem_gift"
    assert memory.source_event_id == "evt_gift"
    assert memory.schema_version == 2
    assert memory.summary == "哈维确实收下了艾拉递出的普通小刀。"
    assert {scope for scope in memory.scopes} == {
        MemoryScope("player", "pc_ella"),
        MemoryScope("npc", "npc_harvey"),
    }
    assert project_event_to_memory(
        "cmp_test",
        Event("evt_speech", "speech.performed", "pc_ella", 10, {}),
        state,
    ) is None


def test_relationship_memory_is_private_to_subject() -> None:
    memory = project_event_to_memory(
        "cmp_test",
        Event("evt_rel", "relationship.changed", "npc_harvey", 12, {
            "subjectId": "npc_harvey",
            "objectId": "pc_ella",
            "dimension": "trust",
            "delta": 2,
            "sourceEventId": "evt_gift",
        }),
        _state(),
    )

    assert memory is not None
    assert memory.memory_type == "relationship"
    assert memory.scopes == (MemoryScope("npc", "npc_harvey"),)
    assert memory.importance == 60


def test_scope_and_exact_entity_bounding_prevent_cross_npc_leak() -> None:
    memory = project_event_to_memory("cmp_test", _gift_event(), _state())
    assert memory is not None
    query = MemoryQuery(
        campaign_id="cmp_test",
        purpose="npc_decision",
        perspective_kind="npc",
        perspective_id="npc_martha",
        entity_ids=("pc_ella", "npc_harvey"),
    )

    result = select_memories([memory], query)

    assert result.selected == ()
    assert [(item.memory_id, item.reason) for item in result.rejected] == [
        ("mem_gift", "scope_mismatch")
    ]


def test_temporal_modes_are_deterministic_and_current_is_not_inferred() -> None:
    state = _state()
    first = project_event_to_memory("cmp_test", _gift_event("evt_first", 10), state)
    latest = project_event_to_memory("cmp_test", _gift_event("evt_latest", 30), state)
    assert first is not None and latest is not None
    base = MemoryQuery(
        campaign_id="cmp_test",
        purpose="debug",
        perspective_kind="player",
        perspective_id="pc_ella",
        entity_ids=("pc_ella", "npc_harvey"),
        limit=1,
    )

    assert select_memories([latest, first], replace(base, time_mode="earliest")).selected == (first,)
    assert select_memories([first, latest], replace(base, time_mode="latest")).selected == (latest,)
    current = select_memories([first, latest], replace(base, information_need="current"))
    assert current.route == "current_state_required"
    assert current.selected == ()


def test_time_scope_type_and_budget_rejections_are_auditable() -> None:
    state = _state()
    memory = project_event_to_memory("cmp_test", _gift_event(), state)
    assert memory is not None
    query = MemoryQuery(
        campaign_id="cmp_test",
        purpose="debug",
        perspective_kind="player",
        perspective_id="pc_ella",
        event_types=("bribe.accepted",),
        time_mode="after",
        time_start=20,
    )
    result = select_memories([memory], query)
    assert result.rejected[0].reason == "event_type_mismatch"

    tiny_budget = replace(query, event_types=(), time_mode="any", time_start=None, character_budget=1)
    result = select_memories([memory], tiny_budget)
    assert result.rejected[0].reason == "budget_exceeded"


def test_memory_query_rejects_invalid_time_and_duplicate_entities() -> None:
    with pytest.raises(ValueError, match="requires time_start"):
        MemoryQuery("cmp_test", "debug", "player", "pc_ella", time_mode="before")
    with pytest.raises(ValueError, match="unique"):
        MemoryQuery(
            "cmp_test",
            "debug",
            "player",
            "pc_ella",
            entity_ids=("pc_ella", "pc_ella"),
        )
    with pytest.raises(ValueError, match="requires search_text"):
        MemoryQuery(
            "cmp_test",
            "debug",
            "player",
            "pc_ella",
            retrieval_mode="fts",
        )


def test_candidate_from_another_campaign_is_rejected() -> None:
    state = _state()
    memory = project_event_to_memory("cmp_test", _gift_event(), state)
    assert memory is not None
    other = EpisodicMemory(
        memory_id=memory.memory_id,
        campaign_id="cmp_other",
        source_event_id=memory.source_event_id,
        schema_version=2,
        memory_type=memory.memory_type,
        event_type=memory.event_type,
        summary=memory.summary,
        importance=memory.importance,
        world_time=memory.world_time,
        location_id=memory.location_id,
        status="active",
        update_key=None,
        entities=(MemoryEntity("pc_ella", "actor"),),
        scopes=(MemoryScope("player", "pc_ella"),),
    )
    query = MemoryQuery("cmp_test", "debug", "player", "pc_ella")
    assert select_memories([other], query).rejected[0].reason == "campaign_mismatch"


def test_movement_updates_link_keeps_both_historical_locations() -> None:
    events = [*gray_harbor_events()]
    first = Event("evt_move_kitchen", "character.moved", "protagonist", 1, {
        "characterId": "protagonist",
        "fromLocationId": "white_heron_ground_floor",
        "toLocationId": "white_heron_kitchen",
        "travelMinutes": 1,
    })
    events.extend([
        first,
        Event("evt_scene_kitchen", "scene.location_changed", "protagonist", 1, {
            "fromLocationId": "white_heron_ground_floor",
            "toLocationId": "white_heron_kitchen",
            "movementEventId": first.event_id,
        }),
    ])
    second = Event("evt_move_hall", "character.moved", "protagonist", 2, {
        "characterId": "protagonist",
        "fromLocationId": "white_heron_kitchen",
        "toLocationId": "white_heron_ground_floor",
        "travelMinutes": 1,
    })
    events.extend([
        second,
        Event("evt_scene_hall", "scene.location_changed", "protagonist", 2, {
            "fromLocationId": "white_heron_kitchen",
            "toLocationId": "white_heron_ground_floor",
            "movementEventId": second.event_id,
        }),
    ])

    model = project_memory_read_model("cmp_gray_harbor", events)

    movements = [memory for memory in model.memories if memory.event_type == "character.moved"]
    assert [memory.source_event_id for memory in movements] == [first.event_id, second.event_id]
    assert all(memory.status == "active" for memory in movements)
    assert all(memory.update_key == "character_location:protagonist" for memory in movements)
    assert [(link.source_memory_id, link.target_memory_id, link.relation_type) for link in model.links] == [
        ("mem_move_hall", "mem_move_kitchen", "updates")
    ]
    assert [summary.location_id for summary in model.scene_summaries] == [
        "white_heron_ground_floor",
        "white_heron_kitchen",
    ]
    assert all(summary.status == "closed" for summary in model.scene_summaries)
    assert model.scene_summaries[0].source_event_ids == (first.event_id,)
    assert "白鹭屋一楼大厅" in model.scene_summaries[0].content
    assert "白鹭屋厨房" in model.scene_summaries[0].content


def test_relationship_change_has_explicit_causal_memory_link() -> None:
    events = [*gray_harbor_events()]
    accepted = Event("evt_accepted", "gift.accepted", "harvey_cole", 5, {
        "actorId": "protagonist",
        "targetId": "harvey_cole",
        "itemId": "protagonist_small_knife",
        "offerEventId": "evt_offer",
    })
    changed = Event("evt_changed", "relationship.changed", "harvey_cole", 5, {
        "subjectId": "harvey_cole",
        "objectId": "protagonist",
        "dimension": "favor",
        "delta": 2,
        "sourceEventId": accepted.event_id,
    })

    model = project_memory_read_model("cmp_gray_harbor", [*events, accepted, changed])

    causal = [link for link in model.links if link.relation_type == "caused_by"]
    assert len(causal) == 1
    assert causal[0].source_memory_id == "mem_changed"
    assert causal[0].target_memory_id == "mem_accepted"
    assert causal[0].source_event_id == changed.event_id
    summary = model.scene_summaries[0]
    assert summary.source_event_ids == (accepted.event_id, changed.event_id)
    assert summary.start_sequence <= summary.end_sequence
