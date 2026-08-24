from __future__ import annotations

from pathlib import Path

import pytest

from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID
from trpg_server.core.projection import replay
from trpg_server.core.schemas import TurnRequest
from trpg_server.core.service import GameService, StateVersionConflictError


@pytest.fixture()
def service(tmp_path: Path) -> GameService:
    game = GameService(tmp_path / "test.sqlite3")
    game.initialize()
    return game


def request(version: int, text: str, key: str) -> TurnRequest:
    return TurnRequest(
        idempotency_key=key,
        expected_state_version=version,
        actor_id="protagonist",
        text=text,
    )


def test_campaign_starts_from_authored_gray_harbor_opening(service: GameService) -> None:
    state = service.get_state(GRAY_HARBOR_CAMPAIGN_ID)

    assert state["stateVersion"] == 1
    assert state["scenario"]["scenarioId"] == "gray-harbor-black-tide-throne"
    assert state["scenario"]["version"] == "0.8.0"
    assert state["player"]["name"] == "艾拉·帕克"
    assert state["player"]["profile"]["birthplace"] == "灰港黑坡区·北沟"
    assert len(state["scenario"]["contentHash"]) == 64
    assert state["worldTime"] == 0
    assert state["scene"]["title"] == "最后七天"
    assert state["scene"]["locationId"] == "white_heron_ground_floor"

    with service.store.connect() as connection:
        stored = connection.execute(
            """
            SELECT scenario_id, scenario_version, scenario_content_hash
            FROM campaigns WHERE campaign_id = ?
            """,
            (GRAY_HARBOR_CAMPAIGN_ID,),
        ).fetchone()
    assert dict(stored) == {
        "scenario_id": state["scenario"]["scenarioId"],
        "scenario_version": state["scenario"]["version"],
        "scenario_content_hash": state["scenario"]["contentHash"],
    }


def test_same_idempotency_key_returns_same_move_without_new_effects(service: GameService) -> None:
    turn = request(1, "我去厨房。", "idem-repeat-move")
    first = service.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, turn)
    second = service.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, turn)

    assert first["outcome"] == "moved"
    assert second["replayed"] is True
    assert second["turn_id"] == first["turn_id"]
    assert service.get_state(GRAY_HARBOR_CAMPAIGN_ID)["stateVersion"] == 2


def test_player_can_spend_a_full_day_working_without_forced_story_progress(
    service: GameService,
) -> None:
    result = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(1, "我今天一整天都去挣钱。", "idem-full-day-work"),
    )

    assert result["status"] == "committed"
    assert result["outcome"] == "waited"
    assert result["state"]["worldTime"] == 1440
    assert "只记录时间" in result["narrative"]
    assert result["state"]["scene"]["beat"] == 0


def test_idle_day_is_idempotent_and_does_not_invent_a_reward(
    service: GameService,
) -> None:
    turn = request(1, "我发呆一整天，什么也不做。", "idem-idle-day")
    first = service.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, turn)
    second = service.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, turn)

    assert first["outcome"] == "waited"
    assert first["state"]["worldTime"] == 1440
    assert "没有替你制造新的线索、奖励或剧情进展" in first["narrative"]
    assert second["replayed"] is True
    assert second["turn_id"] == first["turn_id"]


def test_stale_state_version_is_rejected(service: GameService) -> None:
    service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(1, "我去厨房。", "idem-version-first"),
    )

    with pytest.raises(StateVersionConflictError) as error:
        service.submit_turn(
            GRAY_HARBOR_CAMPAIGN_ID,
            request(1, "我回到大厅。", "idem-version-stale"),
        )

    assert error.value.current_version == 2


def test_player_claim_cannot_move_final_notice_into_inventory(service: GameService) -> None:
    result = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(1, "我已经拿到了最后通牒。", "idem-false-possession"),
    )

    assert result["command"]["action_type"] == "claim_item_possession"
    assert result["outcome"] == "claim_disputed"
    assert all(
        item["itemId"] != "iron_hooks_final_notice"
        for item in result["state"]["player"]["inventory"]
    )


def test_kitchen_search_finds_authored_food_then_requires_take_event(
    service: GameService,
) -> None:
    moved = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(1, "我去厨房。", "kitchen-search-move"),
    )
    found = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(moved["state_version"], "我想找一些食物。", "kitchen-search-food"),
    )

    assert found["command"]["action_type"] == "search_location"
    assert found["outcome"] == "items_found"
    assert "黑麦面包" in found["narrative"]
    assert "可用通路" not in found["narrative"]
    assert all(item["itemId"] != "white_heron_kitchen_bread" for item in found["state"]["player"]["inventory"])

    taken = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(found["state_version"], "我拿走半条黑麦面包。", "kitchen-take-bread"),
    )
    assert taken["outcome"] == "item_taken"
    assert any(item["itemId"] == "white_heron_kitchen_bread" for item in taken["state"]["player"]["inventory"])


def test_taking_restricted_ledger_commits_item_and_legal_risk(service: GameService) -> None:
    result = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(1, "我拿走白鹭屋营业账本。", "take-restricted-ledger"),
    )
    detail = service.get_turn_detail(GRAY_HARBOR_CAMPAIGN_ID, result["turn_id"])

    assert result["outcome"] == "item_taken_with_legal_risk"
    assert any(item["itemId"] == "white_heron_operating_ledger" for item in result["state"]["player"]["inventory"])
    assert [event["event_type"] for event in detail["events"]].count("crime.committed") == 1
    assert not result["state"].get("wanted")


def test_search_without_matching_resource_is_a_result_not_a_route_rejection(
    service: GameService,
) -> None:
    result = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(1, "我找找能喝的水。", "hall-search-drink"),
    )
    assert result["status"] == "committed"
    assert result["outcome"] == "nothing_found"
    assert "饮品" in result["narrative"]
    assert "可用通路" not in result["narrative"]


def test_week_and_month_waits_advance_clock_and_emit_world_reports(
    service: GameService,
) -> None:
    week = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(1, "我等待一周。", "wait-one-week"),
    )
    assert week["outcome"] == "waited"
    assert week["state"]["worldTime"] == 10_080
    assert week["state"].get("worldReports")

    month = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(week["state_version"], "我等待一个月。", "wait-one-month"),
    )
    assert month["outcome"] == "waited"
    assert month["state"]["worldTime"] == 53_280
    reports = month["state"].get("worldReports", [])
    assert any(str(report["candidateId"]).startswith("monthly_") for report in reports)


def test_reasonable_mundane_environment_action_is_not_treated_as_speech(
    service: GameService,
) -> None:
    result = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(1, "我把厨房的桌面擦干净，整理一下餐具。", "mundane-environment-action"),
    )

    assert result["command"]["action_type"] == "environment_action"
    assert result["outcome"] == "environment_action_completed"
    assert "没有凭空改变物品、关系或剧情状态" in result["narrative"]
    assert "speech_without_listener" not in result["narrative"]
    assert result["state"]["worldTime"] == 5


def test_character_search_uses_authored_schedule_without_auto_moving_player(
    service: GameService,
) -> None:
    result = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(1, "我想找奥蒂斯。", "find-otis"),
    )
    assert result["command"]["action_type"] == "find_character"
    assert result["outcome"] == "character_located"
    assert "白鹭屋厨房" in result["narrative"]
    assert result["state"]["scene"]["locationId"] == "white_heron_ground_floor"


def test_searching_for_secret_passage_is_not_misclassified_as_character_search(
    service: GameService,
) -> None:
    result = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        TurnRequest(
            idempotency_key="search-secret-passage-not-character",
            expected_state_version=1,
            actor_id="protagonist",
            text="我仔细寻找厨房里的秘密通道。",
        ),
    )

    assert result["command"]["action_type"] == "investigate_location"
    assert result["outcome"] != "missing_reference"


def test_processed_turn_preserves_messages_and_event_sources(service: GameService) -> None:
    original_text = "  我对玛莎说：‘这笔债务到底怎么回事？’  "
    result = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(1, original_text, "idem-trace-speech"),
    )
    detail = service.get_turn_detail(GRAY_HARBOR_CAMPAIGN_ID, result["turn_id"])

    assert [message["message_kind"] for message in detail["messages"]] == [
        "player_input",
        "narration",
    ]
    assert detail["messages"][0]["content"] == original_text
    assert detail["messages"][0]["authority"] == "utterance_only"
    assert detail["messages"][1]["authority"] == "narration_only"
    assert detail["command"]["source_message_ids"] == [
        detail["messages"][0]["message_id"]
    ]
    assert detail["trace"]["event_ids"] == [event["event_id"] for event in detail["events"]]
    assert all(
        event["sources"] == [{
            "message_id": detail["messages"][0]["message_id"],
            "source_kind": "trigger_input",
        }]
        for event in detail["events"]
    )


def test_rejected_non_adjacent_move_is_recorded_without_world_change(service: GameService) -> None:
    result = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(1, "我直接去地窖。", "idem-record-rejection"),
    )
    detail = service.get_turn_detail(GRAY_HARBOR_CAMPAIGN_ID, result["turn_id"])

    assert result["status"] == "rejected"
    assert result["outcome"] == "destination_not_reachable"
    assert detail["state_version_before"] == 1
    assert detail["state_version_after"] == 1
    assert detail["events"] == []
    assert len(detail["messages"]) == 2


def test_idempotent_replay_does_not_duplicate_raw_messages(service: GameService) -> None:
    turn = request(1, "我去厨房。", "idem-message-replay")
    first = service.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, turn)
    second = service.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, turn)

    messages = service.get_recent_messages(GRAY_HARBOR_CAMPAIGN_ID)
    assert second["replayed"] is True
    assert second["trace"] == first["trace"]
    assert len(messages) == 2


def test_in_character_statement_is_speech_not_world_claim(service: GameService) -> None:
    result = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(
            1,
            "我对玛莎说：‘我已经拿到了最后通牒。’",
            "idem-in-character-claim",
        ),
    )

    assert result["command"]["action_type"] == "speak"
    assert result["command"]["claimed_outcome"] is None
    assert result["command"]["authority"] == "player"
    assert result["outcome"] == "speech_heard"
    assert all(
        item["itemId"] != "iron_hooks_final_notice"
        for item in result["state"]["player"]["inventory"]
    )


def test_compound_actions_resolve_in_order(service: GameService) -> None:
    result = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(
            1,
            "我先等待十分钟，然后我去厨房。",
            "idem-compound-action",
        ),
    )

    assert result["status"] == "committed"
    assert result["outcome"] == "compound_committed"
    assert result["state_version"] == 2
    assert result["state"]["worldTime"] == 11
    assert result["state"]["scene"]["locationId"] == "white_heron_kitchen"


def test_gray_harbor_notice_is_examined_as_a_real_item(service: GameService) -> None:
    result = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(1, "我查看铁钩帮最后通牒。", "idem-inspect-final-notice"),
    )
    detail = service.get_turn_detail(GRAY_HARBOR_CAMPAIGN_ID, result["turn_id"])

    assert result["command"]["action_type"] == "inspect_item"
    assert result["outcome"] == "inspection_completed"
    assert result["state"]["worldTime"] == 2
    assert any(event["event_type"] == "item.examined" for event in detail["events"])
    assert all(
        item["itemId"] != "iron_hooks_final_notice"
        for item in result["state"]["player"]["inventory"]
    )
    assert "inspect_iron_hooks_final_notice" not in {
        action["interactionId"] for action in result["state"]["availableActions"]
    }


def test_martha_answers_only_authoritative_debt_topic(service: GameService) -> None:
    result = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(
            1,
            "我询问玛莎：这笔债务到底是怎么来的？",
            "idem-ask-martha-debt",
        ),
    )

    assert result["command"]["action_type"] == "ask_topic"
    assert result["command"]["target_id"] == "martha_bell"
    assert result["outcome"] == "answer_received"
    assert "妹妹" in result["narrative"]
    assert "珍妮" not in result["narrative"]
    assert "伪造" not in result["narrative"]
    with service.store.connect() as connection:
        events = service.store.load_events(connection, GRAY_HARBOR_CAMPAIGN_ID)
    state = replay(GRAY_HARBOR_CAMPAIGN_ID, events, result["state_version"])
    assert {
        "martha_borrowed_for_sister",
        "martha_disputes_notice_total",
    } <= state.knowledge["protagonist"]
    assert "jenny_forged_additional_loan" not in state.knowledge["protagonist"]


def test_ledger_reveals_only_debt_anomaly_and_is_idempotent(service: GameService) -> None:
    turn = request(
        1,
        "我查看白鹭屋营业账本，核对债务数字。",
        "idem-inspect-ledger",
    )
    first = service.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, turn)
    second = service.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, turn)

    assert first["outcome"] == "inspection_completed"
    assert second["replayed"] is True
    assert {clue["clueId"] for clue in first["state"]["clues"]} == {
        "debt_total_anomaly"
    }
    assert "伪造签名" not in first["narrative"]
    assert "珍妮" not in first["narrative"]
    assert service.get_state(GRAY_HARBOR_CAMPAIGN_ID)["stateVersion"] == 2


def test_player_cannot_force_forgery_reveal_through_claimed_result(
    service: GameService,
) -> None:
    result = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        request(
            1,
            "我检查账本，发现珍妮伪造了玛莎的签名。",
            "idem-forced-forgery-claim",
        ),
    )

    assert result["outcome"] == "inspection_completed"
    assert result["command"]["claimed_outcome"] == (
        "player_claimed_investigation_result"
    )
    assert {clue["clueId"] for clue in result["state"]["clues"]} == {
        "debt_total_anomaly"
    }
    with service.store.connect() as connection:
        events = service.store.load_events(connection, GRAY_HARBOR_CAMPAIGN_ID)
    state = replay(GRAY_HARBOR_CAMPAIGN_ID, events, result["state_version"])
    assert "white_heron_debt_inflated" in state.knowledge["protagonist"]
    assert "jenny_forged_additional_loan" not in state.knowledge["protagonist"]
