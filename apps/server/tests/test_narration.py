from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import httpx
import pytest

from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events
from trpg_server.ai.platform.deepseek import DeepSeekSettings
from trpg_server.core.state import Event, ParsedCommand, Resolution
from trpg_server.ai.player.narration import (
    DeepSeekNarrationAdapter,
    NarrationAdapterResult,
    SafeNarrator,
    build_narration_context,
    build_narrative_plan,
    hidden_narration_terms,
    narrator_from_environment,
)
from trpg_server.core.projection import replay
from trpg_server.core.schemas import TurnRequest
from trpg_server.core.service import GameService


def valid_proposal(plan) -> dict[str, object]:
    atom_ids = [atom.atom_id for atom in plan.atoms]
    placements = []
    for index, atom_id in enumerate(atom_ids):
        if index < 2:
            paragraph = 1
        elif index == len(atom_ids) - 1:
            paragraph = 3
        else:
            paragraph = 2
        placements.append({
            "atom_id": atom_id,
            "paragraph": paragraph,
            "prose_before": (
                "雨声压低了屋里的喧响。"
                if index == 0
                else "动作在片刻间连贯下来。"
                if index == 2
                else "空气重新安静下来。"
                if index == len(atom_ids) - 1
                else ""
            ),
            "prose_after": "",
        })
    return {
        "schema_version": 3,
        "placements": placements,
        "supported_atom_ids": atom_ids,
        "beat_count": 1,
        "returns_control": True,
        "proposed_events": [],
        "confidence": 0.96,
    }


class ProposalAdapter:
    available = True
    model_name = "fake-narrator"
    provider_name = "test"

    def __init__(self, proposal: dict[str, object] | None = None) -> None:
        self.proposal = proposal
        self.calls = 0
        self.requests = []

    def narrate(self, request):
        self.calls += 1
        self.requests.append(request)
        return self.proposal or valid_proposal(request.context.plan)


class TimeoutAdapter(ProposalAdapter):
    def narrate(self, request):
        self.calls += 1
        self.requests.append(request)
        raise TimeoutError("late")


def initial_state():
    return replay(GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events(), 1)


def resolution(
    *,
    status: str = "committed",
    narrative: str = "你走进厨房。",
    visible_changes: list[str] | None = None,
    outcome: str | None = None,
    action_type: str = "move",
    target_id: str | None = "white_heron_kitchen",
) -> Resolution:
    return Resolution(
        status=status,
        outcome=outcome or ("moved" if status == "committed" else "destination_not_reachable"),
        narrative=narrative,
        command=ParsedCommand(action_type, "protagonist", target_id, {}, "去厨房"),
        visible_changes=visible_changes or [],
    )


def turn_request(key: str = "narration-test-key") -> TurnRequest:
    return TurnRequest(
        idempotency_key=key,
        expected_state_version=1,
        actor_id="protagonist",
        text="我去厨房。",
    )


def test_narrative_plan_separates_micro_action_fact_and_decision_boundary() -> None:
    state = initial_state()
    plan = build_narrative_plan(
        resolution(visible_changes=["位置变为白鹭屋厨房。"]),
        state,
    )
    serialized = plan.model_dump_json()

    assert plan.schema_version == 1
    assert plan.target_paragraphs == 3
    assert plan.major_beat_budget == 1
    assert "七日债务期限" in plan.scenario_premise
    assert any("危机不能" in anchor for anchor in plan.hard_anchors)
    assert any("协商" in approach for approach in plan.flexible_approaches)
    assert any("责任人" in boundary for boundary in plan.stop_before)
    assert [atom.kind for atom in plan.atoms] == [
        "scene_anchor",
        "micro_action",
        "confirmed_result",
        "visible_change",
        "decision_boundary",
    ]
    assert plan.atoms[-1].atom_id == "decision_boundary"
    assert plan.decision_boundary == "arrival_choice"
    assert "已经确认可行的路线" in plan.atoms[1].text
    assert "player_text" not in serialized
    assert "events" not in serialized
    assert "jenny_forged_additional_loan" not in serialized
    assert "simon_exploited_forgery" not in serialized
    assert "cellar_tunnel_to_bakery" not in serialized
    assert "伪造了玛莎的签名" not in serialized


def test_movement_plan_opens_at_confirmed_origin_before_describing_travel() -> None:
    moved = resolution()
    moved.events = [
        Event(
            "evt_move_test",
            "character.moved",
            "protagonist",
            1,
            {
                "characterId": "protagonist",
                "fromLocationId": "white_heron_ground_floor",
                "toLocationId": "white_heron_kitchen",
                "travelMinutes": 1,
            },
        ),
        Event(
            "evt_time_test",
            "time.advanced",
            "system",
            1,
            {"from": 0, "to": 1, "minutes": 1, "reason": "character_movement"},
        ),
    ]
    post_move_state = initial_state()
    post_move_state.location_id = "white_heron_kitchen"
    post_move_state.world_time = 1

    plan = build_narrative_plan(moved, post_move_state)

    assert "白鹭屋一楼大厅" in plan.atoms[0].text
    assert "23:00" in plan.atoms[0].text
    assert "白鹭屋厨房" in plan.atoms[1].text


@pytest.mark.parametrize(
    ("action_type", "outcome", "target_id", "expected_text", "boundary"),
    [
        ("inspect_item", "inspection_completed", "iron_hooks_final_notice", "耐心检查", "evidence_choice"),
        ("ask_topic", "answer_received", "martha_bell", "玛莎", "conversation_choice"),
        ("speak", "speech_heard", "martha_bell", "完整地说", "response_boundary"),
    ],
)
def test_plan_uses_action_specific_micro_steps_and_stop_points(
    action_type: str,
    outcome: str,
    target_id: str,
    expected_text: str,
    boundary: str,
) -> None:
    plan = build_narrative_plan(
        resolution(action_type=action_type, outcome=outcome, target_id=target_id),
        initial_state(),
    )

    assert expected_text in plan.atoms[1].text
    assert plan.decision_boundary == boundary


def test_rejected_plan_stops_at_obstacle_without_claiming_success() -> None:
    plan = build_narrative_plan(
        resolution(status="rejected", narrative="你无法从这里直接前往王宫。"),
        initial_state(),
    )

    assert plan.decision_boundary == "obstacle_choice"
    assert "没有越过" in plan.atoms[1].text
    assert "是否换一种方法" in plan.atoms[-1].text


def test_hidden_lexicon_excludes_known_facts_but_keeps_gm_secrets() -> None:
    state = initial_state()
    terms = hidden_narration_terms(state, "protagonist")

    assert "jenny_forged_additional_loan" in terms
    assert "cellar_tunnel_to_bakery" in terms
    assert "white_heron_debt_exists" not in terms
    state.knowledge["protagonist"].add("jenny_forged_additional_loan")
    assert "jenny_forged_additional_loan" not in hidden_narration_terms(
        state, "protagonist"
    )


def test_valid_multi_paragraph_model_narration_preserves_every_plan_atom() -> None:
    adapter = ProposalAdapter()
    narrator = SafeNarrator(adapter)
    original = resolution(visible_changes=["位置：白鹭屋厨房", "世界时间推进 1 分钟"])

    result = narrator.narrate(original, initial_state())
    plan = adapter.requests[0].context.plan

    assert result.audit.status == "model_accepted"
    assert len(result.text.split("\n\n")) == 3
    assert result.text.startswith("雨声压低了屋里的喧响")
    assert all(result.text.count(atom.text) == 1 for atom in plan.atoms)
    assert result.text.index(plan.atoms[0].text) < result.text.index(plan.atoms[-1].text)
    assert original.narrative == "你走进厨房。"
    assert original.events == []


def test_exact_authoritative_text_repeated_in_model_prose_is_removed() -> None:
    original = resolution(visible_changes=["位置：白鹭屋厨房"])
    plan = build_narrative_plan(original, initial_state())
    proposal = valid_proposal(plan)
    proposal["placements"][0]["prose_before"] += plan.atoms[2].text

    result = SafeNarrator(ProposalAdapter(proposal)).narrate(
        original,
        initial_state(),
    )

    assert result.audit.status == "model_accepted"
    assert result.text.count(plan.atoms[2].text) == 1


def test_rephrased_time_and_arrival_restatements_are_removed() -> None:
    original = resolution(visible_changes=["位置：白鹭屋厨房"])
    plan = build_narrative_plan(original, initial_state())
    proposal = valid_proposal(plan)
    proposal["placements"][0]["prose_before"] = (
        f"你站在大厅，时间是{plan.world_time_label}。雨声贴着窗格落下。"
    )
    proposal["placements"][2]["prose_before"] = "你来到白鹭屋一楼大厅。"

    result = SafeNarrator(ProposalAdapter(proposal)).narrate(
        original,
        initial_state(),
    )

    assert result.audit.status == "model_accepted"
    assert "你站在大厅" not in result.text
    assert "雨声贴着窗格落下" in result.text
    assert result.text.count("你来到白鹭屋一楼大厅") == 0


def test_immediate_sensory_feedback_does_not_count_as_player_control() -> None:
    plan = build_narrative_plan(resolution(), initial_state())
    proposal = valid_proposal(plan)
    proposal["placements"][0]["prose_before"] = "你立刻听见檐下的滴水声。"

    result = SafeNarrator(ProposalAdapter(proposal)).narrate(
        resolution(),
        initial_state(),
    )

    assert result.audit.status == "model_accepted"
    assert "你立刻听见檐下的滴水声" in result.text


def test_disabled_model_uses_readable_multi_paragraph_plan_fallback() -> None:
    narrator = narrator_from_environment({})
    result = narrator.narrate(
        resolution(visible_changes=["位置：白鹭屋厨房"]),
        initial_state(),
    )

    assert result.audit.status == "local"
    assert len(result.text.split("\n\n")) == 3
    assert "你走进厨房。" in result.text
    assert "由你决定" in result.text


def test_proposal_rejects_missing_unknown_or_reordered_atoms() -> None:
    plan = build_narrative_plan(resolution(), initial_state())

    missing = valid_proposal(plan)
    missing["supported_atom_ids"] = missing["supported_atom_ids"][:-1]
    result = SafeNarrator(ProposalAdapter(missing)).narrate(resolution(), initial_state())
    assert result.audit.failure_code == "missing_required_atom"

    unknown = valid_proposal(plan)
    unknown["supported_atom_ids"] = [*unknown["supported_atom_ids"], "invented_atom"]
    result = SafeNarrator(ProposalAdapter(unknown)).narrate(resolution(), initial_state())
    assert result.audit.failure_code == "unknown_atom_reference"

    reordered = valid_proposal(plan)
    reordered["placements"][0], reordered["placements"][1] = (
        reordered["placements"][1],
        reordered["placements"][0],
    )
    result = SafeNarrator(ProposalAdapter(reordered)).narrate(resolution(), initial_state())
    assert result.audit.failure_code == "atom_order_invalid"


def test_proposal_rejects_duplicate_and_invalid_paragraph_sequences() -> None:
    plan = build_narrative_plan(resolution(), initial_state())

    duplicate = valid_proposal(plan)
    duplicate["placements"].append(dict(duplicate["placements"][2]))
    result = SafeNarrator(ProposalAdapter(duplicate)).narrate(resolution(), initial_state())
    assert result.audit.failure_code == "required_atom_count_invalid"

    gap = valid_proposal(plan)
    gap["placements"][-1]["paragraph"] = 4
    result = SafeNarrator(ProposalAdapter(gap)).narrate(resolution(), initial_state())
    assert result.audit.failure_code == "paragraph_sequence_invalid"

    one_paragraph = valid_proposal(plan)
    for placement in one_paragraph["placements"]:
        placement["paragraph"] = 1
    result = SafeNarrator(ProposalAdapter(one_paragraph)).narrate(
        resolution(), initial_state()
    )
    assert result.audit.failure_code == "paragraph_count_invalid"


@pytest.mark.parametrize(
    ("added_text", "failure_code"),
    [
        ("她低声说：“我会帮你。”", "unconfirmed_dialogue"),
        ("你决定立刻追上去。", "player_control_violation"),
        ("你立刻转身追出去。", "player_control_violation"),
        ("你发现珍妮留下了一张纸。", "unconfirmed_state_claim"),
        ("你得知 jenny_forged_additional_loan。", "forbidden_hidden_term"),
    ],
)
def test_proposal_rejects_unplanned_story_content(
    added_text: str,
    failure_code: str,
) -> None:
    plan = build_narrative_plan(resolution(), initial_state())
    proposal = valid_proposal(plan)
    proposal["placements"][2]["prose_before"] = added_text

    result = SafeNarrator(ProposalAdapter(proposal)).narrate(
        resolution(),
        initial_state(),
    )

    assert result.audit.status == "model_fallback"
    assert result.audit.failure_code == failure_code


def test_rejected_action_cannot_be_narrated_as_success() -> None:
    rejected = resolution(status="rejected", narrative="你无法从这里直接前往王宫。")
    plan = build_narrative_plan(rejected, initial_state())
    proposal = valid_proposal(plan)
    proposal["placements"][2]["prose_before"] = "你成功抵达王宫。"

    result = SafeNarrator(ProposalAdapter(proposal)).narrate(rejected, initial_state())

    assert "你无法从这里直接前往王宫。" in result.text
    assert result.audit.failure_code == "contradicts_rejected_result"


def test_low_confidence_illegal_events_and_timeout_fall_back() -> None:
    plan = build_narrative_plan(resolution(), initial_state())
    low = valid_proposal(plan)
    low["confidence"] = 0.2
    result = SafeNarrator(ProposalAdapter(low)).narrate(resolution(), initial_state())
    assert result.audit.failure_code == "low_confidence"

    illegal_event = valid_proposal(plan)
    illegal_event["proposed_events"] = [{"event_type": "item.transferred"}]
    result = SafeNarrator(ProposalAdapter(illegal_event)).narrate(
        resolution(), initial_state()
    )
    assert result.audit.failure_code == "model_schema_invalid"

    result = SafeNarrator(TimeoutAdapter()).narrate(resolution(), initial_state())
    assert result.audit.failure_code == "model_timeout"
    assert "你走进厨房。" in result.text


def test_service_uses_plan_narration_only_as_text_and_redacts_payloads(
    tmp_path: Path,
) -> None:
    adapter = ProposalAdapter()
    game = GameService(
        tmp_path / "narration.sqlite3",
        narrator=SafeNarrator(adapter),
    )
    game.initialize()

    result = game.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, turn_request())
    detail = game.get_turn_detail(GRAY_HARBOR_CAMPAIGN_ID, result["turn_id"])

    assert result["outcome"] == "moved"
    assert result["state"]["scene"]["locationId"] == "white_heron_kitchen"
    assert len(result["narrative"].split("\n\n")) == 3
    assert detail["messages"][1]["content"] == result["narrative"]
    assert detail["narration_attempts"][0]["status"] == "model_accepted"
    assert detail["narration_attempts"][0]["request"] is None
    assert detail["narration_attempts"][0]["response"] is None
    with game.store.connect() as connection:
        private = game.store.load_turn_narration_attempts(
            connection,
            GRAY_HARBOR_CAMPAIGN_ID,
            result["turn_id"],
            include_payloads=True,
        )[0]
    serialized_request = json.dumps(private["request"], ensure_ascii=False)
    assert private["request"]["context"]["plan"]["schema_version"] == 1
    assert "jenny_forged_additional_loan" not in serialized_request
    assert "cellar_tunnel_to_bakery" not in serialized_request


def test_idempotent_replay_does_not_call_narrator_twice(tmp_path: Path) -> None:
    adapter = ProposalAdapter()
    game = GameService(
        tmp_path / "idempotent.sqlite3",
        narrator=SafeNarrator(adapter),
    )
    game.initialize()
    request = turn_request("narration-idempotent-key")

    first = game.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, request)
    second = game.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, request)

    assert second["turn_id"] == first["turn_id"]
    assert second["replayed"] is True
    assert adapter.calls == 1


def test_narrator_call_does_not_hold_sqlite_write_lock(tmp_path: Path) -> None:
    database_path = tmp_path / "outside-lock.sqlite3"

    class LockCheckingAdapter(ProposalAdapter):
        def narrate(self, request):
            with sqlite3.connect(database_path, timeout=0.1) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
            return super().narrate(request)

    adapter = LockCheckingAdapter()
    game = GameService(database_path, narrator=SafeNarrator(adapter))
    game.initialize()

    result = game.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, turn_request("outside-lock-key"))

    assert result["outcome"] == "moved"
    assert adapter.calls == 1


def test_deepseek_narrator_uses_plan_template_json_without_secret_context() -> None:
    captured: dict[str, object] = {}
    context = build_narration_context(resolution(), initial_state())

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = body
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps(valid_proposal(context.plan))},
                }],
                "usage": {"prompt_tokens": 180, "completion_tokens": 90, "total_tokens": 270},
            },
        )

    adapter = DeepSeekNarrationAdapter(
        DeepSeekSettings(api_key="narrator-test-secret", max_attempts=1),
        transport=httpx.MockTransport(handler),
    )
    from trpg_server.ai.player.narration import NarrationRequest

    result = adapter.narrate(NarrationRequest(system_instruction="按计划叙述。", context=context))

    assert isinstance(result, NarrationAdapterResult)
    assert result.metrics.total_tokens == 270
    assert captured["authorization"] == "Bearer narrator-test-secret"
    body_text = json.dumps(captured["body"], ensure_ascii=False)
    assert "narrator-test-secret" not in body_text
    assert "jenny_forged_additional_loan" not in body_text
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert '"schema_version":3' in captured["body"]["messages"][0]["content"]
    assert "第一段氛围" not in captured["body"]["messages"][0]["content"]
    assert "第三段收束" not in captured["body"]["messages"][0]["content"]


def test_narrator_environment_is_disabled_by_default_and_validates_settings() -> None:
    disabled = narrator_from_environment({})
    configured = narrator_from_environment({
        "TRPG_NARRATOR_MODEL_ENABLED": "true",
        "DEEPSEEK_API_KEY": "test-key",
        "DEEPSEEK_NARRATOR_MODEL": "deepseek-v4-flash",
        "DEEPSEEK_NARRATOR_THINKING_MODE": "enabled",
    })

    assert disabled.adapter.available is False
    assert disabled.max_characters == 1200
    assert configured.adapter.available is True
    assert configured.adapter.settings.thinking_mode == "enabled"
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        narrator_from_environment({"TRPG_NARRATOR_MODEL_ENABLED": "true"})
