from __future__ import annotations

from copy import deepcopy

from trpg_server.core.state import Event
from trpg_server.behavior.router import interpret_player_text, resolve
from trpg_server.core.projection import apply_event, public_state, replay


def event(
    event_id: str,
    event_type: str,
    payload: dict[str, object],
    *,
    schema_version: int = 1,
) -> Event:
    return Event(
        event_id,
        event_type,
        "system",
        120,
        payload,
        schema_version=schema_version,
    )


def investigation_state(*, witness_location: str = "archive"):
    events = [
        event("evt_campaign", "campaign.created", {
            "campaignId": "cmp_archive",
            "name": "通用调查测试",
            "timeUnit": "minute",
            "playerCharacterId": "investigator",
        }),
        event("evt_archive", "location.created", {
            "locationId": "archive",
            "name": "档案室",
        }),
        event("evt_hall", "location.created", {
            "locationId": "hall",
            "name": "大厅",
        }),
        event("evt_table", "container.created", {
            "containerId": "archive_table",
            "kind": "surface",
            "ownerCharacterId": None,
            "locationId": "archive",
        }),
        event("evt_player", "character.created", {
            "characterId": "investigator",
            "characterType": "player",
            "name": "调查员",
            "aliases": [],
            "locationId": "archive",
        }),
        event("evt_witness", "character.created", {
            "characterId": "witness",
            "characterType": "npc",
            "name": "见证人",
            "aliases": ["证人"],
            "locationId": witness_location,
        }),
        event("evt_scene", "scene.started", {
            "sceneId": "scene_archive",
            "locationId": "archive",
            "phase": "social",
            "maxMajorBeatsPerTurn": 1,
        }),
        event(
            "evt_document",
            "item.created",
            {
                "item": {
                    "id": "shipping_record",
                    "definitionId": "shipping_record_document",
                    "name": "航运记录",
                    "description": "一份放在档案室桌上的航运记录。",
                    "category": "document",
                    "isPlotItem": True,
                    "quantity": 1,
                    "stackable": False,
                    "unitWeightGrams": None,
                    "valueCrown": None,
                    "condition": "intact",
                    "durability": None,
                    "containerId": "archive_table",
                    "locationId": None,
                    "properties": {},
                }
            },
            schema_version=3,
        ),
        event("evt_fact", "world.fact_defined", {
            "factId": "cargo_mismatch",
            "statement": "货物数字不一致。",
            "truthState": "true",
            "visibility": "gm",
            "tags": [],
        }),
        event("evt_testimony", "world.fact_defined", {
            "factId": "witness_saw_crate",
            "statement": "见证人看见过木箱。",
            "truthState": "true",
            "visibility": "gm",
            "tags": [],
        }),
        event("evt_witness_knows", "knowledge.learned", {
            "characterId": "witness",
            "factId": "witness_saw_crate",
            "sourceEventId": "evt_testimony",
        }),
        event("evt_clue", "clue.defined", {
            "clueId": "cargo_number_clue",
            "factId": "cargo_mismatch",
            "title": "不一致的货物数字",
            "description": "两页记录上的货物数量不同。",
        }),
        event("evt_inspection", "inspection.defined", {
            "interactionId": "inspect_shipping_record",
            "label": "核对航运记录",
            "suggestedPrompt": "我检查航运记录。",
            "targetItemId": "shipping_record",
            "aliases": ["航运记录", "记录"],
            "accessPolicy": "location",
            "requiredActorKnowledgeFactIds": [],
            "revealedFactIds": ["cargo_mismatch"],
            "clueIds": ["cargo_number_clue"],
            "timeMinutes": 6,
            "revealText": "你确认两页数字不一致。",
            "repeatText": "记录没有新的变化。",
        }),
        event("evt_inquiry", "inquiry.defined", {
            "interactionId": "ask_witness_about_crate",
            "label": "询问木箱",
            "suggestedPrompt": "我询问见证人木箱的事。",
            "targetCharacterId": "witness",
            "topic": "crate",
            "aliases": ["木箱", "货箱"],
            "requiredActorKnowledgeFactIds": [],
            "requiredNpcKnowledgeFactIds": ["witness_saw_crate"],
            "revealedFactIds": ["witness_saw_crate"],
            "clueIds": [],
            "timeMinutes": 3,
            "responseText": "见证人说自己看见过木箱。",
            "repeatText": "见证人已经回答过。",
            "unknownText": "见证人不知道。",
        }),
    ]
    return replay("cmp_archive", events, 1)


def apply_result(state, result):
    updated = deepcopy(state)
    for confirmed_event in result.events:
        apply_event(updated, confirmed_event)
    return updated


def test_generic_item_inspection_reveals_fact_and_clue_without_moving_item() -> None:
    state = investigation_state()
    command = interpret_player_text(
        "我检查航运记录。",
        actor_id="investigator",
        state=state,
    )
    result = resolve(state, command)
    updated = apply_result(state, result)

    assert command.action_type == "inspect_item"
    assert result.outcome == "inspection_completed"
    assert "cargo_mismatch" in updated.knowledge["investigator"]
    assert "cargo_number_clue" in updated.clues
    assert updated.items["shipping_record"].container_id == "archive_table"
    assert updated.world_time == 126
    assert updated.scene_beat == 1


def test_completed_inspection_cannot_reveal_the_same_clue_twice() -> None:
    state = investigation_state()
    first = resolve(state, interpret_player_text(
        "我检查航运记录。",
        actor_id="investigator",
        state=state,
    ))
    updated = apply_result(state, first)
    repeated = resolve(
        updated,
        interpret_player_text(
            "我再次查看航运记录。",
            actor_id="investigator",
            state=updated,
        ),
    )

    assert repeated.status == "rejected"
    assert repeated.outcome == "already_completed"
    assert repeated.events == []


def test_inspection_fails_when_real_item_is_not_accessible() -> None:
    state = investigation_state()
    state.character_locations["investigator"] = "hall"

    result = resolve(state, interpret_player_text(
        "我检查航运记录。",
        actor_id="investigator",
        state=state,
    ))

    assert result.status == "rejected"
    assert result.outcome == "inspection_target_inaccessible"
    assert result.events == []


def test_generic_inquiry_only_discloses_fact_known_by_present_npc() -> None:
    state = investigation_state()
    command = interpret_player_text(
        "我询问见证人木箱的事。",
        actor_id="investigator",
        state=state,
    )
    result = resolve(state, command)
    updated = apply_result(state, result)

    assert command.action_type == "ask_topic"
    assert result.outcome == "answer_received"
    assert "witness_saw_crate" in updated.knowledge["investigator"]
    assert [value.event_type for value in result.events[:2]] == [
        "question.asked",
        "npc.answer_given",
    ]


def test_inquiry_fails_when_npc_is_not_present() -> None:
    state = investigation_state(witness_location="hall")
    result = resolve(
        state,
        interpret_player_text(
            "我询问见证人木箱的事。",
            actor_id="investigator",
            state=state,
        ),
    )

    assert result.status == "rejected"
    assert result.outcome == "target_not_present"
    assert result.events == []


def test_inquiry_cannot_disclose_fact_the_npc_does_not_know() -> None:
    state = investigation_state()
    state.knowledge["witness"].clear()
    result = resolve(
        state,
        interpret_player_text(
            "我询问见证人木箱的事。",
            actor_id="investigator",
            state=state,
        ),
    )
    updated = apply_result(state, result)

    assert result.status == "committed"
    assert result.outcome == "npc_does_not_know"
    assert "witness_saw_crate" not in updated.knowledge.get("investigator", set())
    answer = next(value for value in result.events if value.event_type == "npc.answer_given")
    assert answer.payload["disclosedFactIds"] == []


def test_public_actions_are_projected_by_backend_and_hide_completed_action() -> None:
    state = investigation_state()
    initial = public_state(state)
    assert {value["interactionId"] for value in initial["availableActions"]} == {
        "inspect_shipping_record",
        "ask_witness_about_crate",
    }

    result = resolve(state, interpret_player_text(
        "我检查航运记录。",
        actor_id="investigator",
        state=state,
    ))
    updated = apply_result(state, result)
    remaining = public_state(updated)["availableActions"]
    assert {value["interactionId"] for value in remaining} == {
        "ask_witness_about_crate"
    }


def test_compound_investigation_stops_after_one_major_beat() -> None:
    state = investigation_state()
    command = interpret_player_text(
        "我先检查航运记录，然后询问见证人木箱的事。",
        actor_id="investigator",
        state=state,
    )
    result = resolve(state, command)
    updated = apply_result(state, result)

    assert result.outcome == "compound_pacing_limited"
    assert "cargo_mismatch" in updated.knowledge["investigator"]
    assert "witness_saw_crate" not in updated.knowledge.get("investigator", set())
    assert sum(
        value.event_type == "scene.beat_advanced" for value in result.events
    ) == 1
