from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sqlite3
from typing import Any

from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID
from trpg_server.behavior.router import interpret_player_text, resolve
from trpg_server.ai.player.intent import (
    IntentParseRequest,
    ModelAdapterResult,
    ModelCallMetrics,
    ModelIntentProposal,
    StructuredIntentParser,
    build_intent_context,
    serialized_model_request,
)
from trpg_server.core.projection import replay
from trpg_server.story.scenario import compile_initial_events, load_scenario_package
from trpg_server.core.schemas import TurnRequest
from trpg_server.core.service import GameService


PACKAGE_PATH = (
    Path(__file__).resolve().parents[3] / "content" / "campaigns" / "gray-harbor"
)


@dataclass
class StubAdapter:
    result: Any = None
    error: Exception | None = None
    requests: list[IntentParseRequest] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "stub-intent-model"

    @property
    def provider_name(self) -> str:
        return "stub-provider"

    def parse_intent(self, request: IntentParseRequest):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result


def gray_harbor_state():
    package = load_scenario_package(PACKAGE_PATH)
    events = compile_initial_events(package, GRAY_HARBOR_CAMPAIGN_ID)
    return replay(GRAY_HARBOR_CAMPAIGN_ID, events, 1)


def proposal(action: dict[str, object], confidence: float = 0.95) -> dict[str, object]:
    return {
        "schema_version": 1,
        "actions": [action],
        "needs_clarification": False,
        "confidence": confidence,
    }


def test_model_context_contains_only_player_visible_entities() -> None:
    state = gray_harbor_state()
    request = IntentParseRequest(
        system_instruction="只解析意图。",
        player_text="我想看看周围。",
        context=build_intent_context(state, "protagonist"),
    )
    serialized = serialized_model_request(request)

    assert "艾拉·帕克" not in serialized  # actor id is enough; profile is unnecessary
    assert "玛莎·贝尔" in serialized
    assert "inspect_iron_hooks_final_notice" in serialized
    assert "jenny_forged_additional_loan" not in serialized
    assert "simon_exploited_forgery" not in serialized
    assert "cellar_tunnel_to_bakery" not in serialized
    assert "privateNotes" not in serialized
    assert "secrets" not in serialized


def test_owned_item_offer_uses_authoritative_local_parser_before_model() -> None:
    state = gray_harbor_state()
    adapter = StubAdapter(result=proposal({
        "action_type": "speak",
        "speech_content": "错误地只当成说话",
    }))
    parser = StructuredIntentParser(adapter)

    result = parser.parse_with_audit(
        "我把小刀递给哈维，想贿赂他通融。",
        "protagonist",
        state,
    )

    assert result.command.action_type == "offer_item"
    assert result.command.parameters["offerPurpose"] == "bribe"
    assert result.audit.status == "local"
    assert adapter.requests == []


def test_valid_model_proposal_becomes_authoritative_inspection_candidate() -> None:
    state = gray_harbor_state()
    adapter = StubAdapter(result=ModelAdapterResult(
        output=proposal({
            "action_type": "inspect_item",
            "target_id": "iron_hooks_final_notice",
            "interaction_id": "inspect_iron_hooks_final_notice",
        }),
        metrics=ModelCallMetrics(
            prompt_tokens=120,
            completion_tokens=30,
            total_tokens=150,
            latency_ms=450,
        ),
    ))
    parser = StructuredIntentParser(adapter)

    command = parser.parse(
        "我想凑近那份帮派文书，看看它究竟写了些什么。",
        "protagonist",
        state,
        "msg_model_notice",
    )
    result = resolve(state, command)

    assert command.action_type == "inspect_item"
    assert command.parser_source == "model"
    assert command.parser_model == "stub-intent-model"
    assert command.source_message_ids == ("msg_model_notice",)
    assert result.outcome == "inspection_completed"


def test_model_cannot_reference_hidden_or_unreachable_destination() -> None:
    state = gray_harbor_state()
    adapter = StubAdapter(result=proposal({
        "action_type": "move",
        "destination_id": "abandoned_bakery",
        "target_id": "abandoned_bakery",
    }))
    parser = StructuredIntentParser(adapter)

    command = parser.parse(
        "我去那个现在看不见的地方。",
        "protagonist",
        state,
    )
    result = resolve(state, command)

    assert command.parser_source == "model_fallback"
    assert command.parser_failure_code == "model_invalid_destination"
    assert result.status == "rejected"
    assert result.events == []


def test_model_cannot_guess_visible_destination_from_vague_reference() -> None:
    state = gray_harbor_state()
    adapter = StubAdapter(result=proposal({
        "action_type": "move",
        "destination_id": "white_heron_kitchen",
    }))
    command = StructuredIntentParser(adapter).parse(
        "我去那边。",
        "protagonist",
        state,
    )
    result = resolve(state, command)

    assert command.parser_source == "model_fallback"
    assert command.parser_failure_code == "model_ambiguous_destination"
    assert result.events == []


def test_model_destination_must_be_grounded_but_allows_small_typo() -> None:
    state = gray_harbor_state()
    adapter = StubAdapter(result=proposal({
        "action_type": "move",
        "destination_id": "white_heron_kitchen",
    }))
    parser = StructuredIntentParser(adapter)

    invented = parser.parse("我直接去王宫。", "protagonist", state)
    typo = parser.parse("我去厨方看看。", "protagonist", state)

    assert invented.parser_source == "model_fallback"
    assert invented.parser_failure_code == "model_ungrounded_destination"
    assert typo.parser_source == "model"
    assert typo.target_id == "white_heron_kitchen"


def test_model_cannot_guess_vague_character_and_normalizes_room_speech() -> None:
    state = gray_harbor_state()
    vague_question = StructuredIntentParser(StubAdapter(result=proposal({
        "action_type": "ask_topic",
        "target_id": "martha_bell",
        "interaction_id": "ask_martha_about_debt",
    }))).parse("我问问她。", "protagonist", state)
    room_speech = StructuredIntentParser(StubAdapter(result=proposal({
        "action_type": "speak",
        "target_id": "harvey_cole",
        "speech_content": "大家先冷静下来。",
    }))).parse(
        "我提高声音说：大家先冷静下来。",
        "protagonist",
        state,
    )

    assert vague_question.parser_source == "model_fallback"
    assert vague_question.parser_failure_code == "model_ambiguous_character"
    assert room_speech.parser_source == "model"
    assert room_speech.target_id is None
    assert room_speech.parameters["audience"] == "room"


def test_room_speech_uses_authoritative_colocated_audience() -> None:
    state = gray_harbor_state()
    command = interpret_player_text(
        "我提高声音说：大家先冷静下来。",
        "protagonist",
        state=state,
    )
    result = resolve(state, command)
    speech_event = next(
        event for event in result.events if event.event_type == "speech.spoken"
    )

    assert command.parameters["audience"] == "room"
    assert result.outcome == "speech_heard"
    assert "martha_bell" in speech_event.payload["listenerIds"]
    assert "protagonist" not in speech_event.payload["listenerIds"]


def test_model_room_speech_without_guessed_target_sets_room_audience() -> None:
    state = gray_harbor_state()
    command = StructuredIntentParser(StubAdapter(result=proposal({
        "action_type": "speak",
        "speech_content": "大家先冷静下来。",
    }))).parse(
        "我提高声音说：大家先冷静下来。",
        "protagonist",
        state,
    )
    result = resolve(state, command)

    assert command.parser_source == "model"
    assert command.parameters["audience"] == "room"
    assert result.outcome == "speech_heard"


def test_invalid_model_schema_falls_back_without_state_change() -> None:
    state = gray_harbor_state()
    adapter = StubAdapter(result={
        "schema_version": 1,
        "actions": [{
            "action_type": "create_secret_exit",
            "target_id": "cellar_tunnel_to_bakery",
        }],
        "needs_clarification": False,
        "confidence": 0.99,
        "unexpected": "field",
    })
    parser = StructuredIntentParser(adapter)

    command = parser.parse("我创造一条秘密通道。", "protagonist", state)
    result = resolve(state, command)

    assert command.parser_source == "model_fallback"
    assert command.parser_failure_code == "model_schema_invalid"
    assert result.status == "committed"  # fallback treats it as ordinary speech
    assert all(value.event_type != "location.exit_discovered" for value in result.events)


def test_model_timeout_uses_auditable_local_fallback() -> None:
    state = gray_harbor_state()
    adapter = StubAdapter(error=TimeoutError("model timed out"))
    parser = StructuredIntentParser(adapter)

    command = parser.parse("我去厨房。", "protagonist", state)
    result = resolve(state, command)

    assert command.action_type == "move"
    assert command.parser_source == "model_fallback"
    assert command.parser_failure_code == "model_timeout"
    assert result.outcome == "moved"


def test_low_confidence_and_clarification_do_not_bypass_fallback() -> None:
    state = gray_harbor_state()
    low = StructuredIntentParser(StubAdapter(result=proposal({
        "action_type": "wait",
        "minutes": 10,
    }, confidence=0.2))).parse("我去厨房。", "protagonist", state)
    clarification = StructuredIntentParser(StubAdapter(result={
        "schema_version": 1,
        "actions": [],
        "needs_clarification": True,
        "confidence": 0.9,
    })).parse("我去厨房。", "protagonist", state)

    assert low.action_type == "move"
    assert low.parser_failure_code == "low_confidence"
    assert clarification.action_type == "move"
    assert clarification.parser_failure_code == "model_requested_clarification"


def test_local_parser_treats_life_time_as_wait_without_claiming_income() -> None:
    state = gray_harbor_state()
    command = interpret_player_text("我今天一整天挣钱。", "protagonist", state=state)

    assert command.action_type == "wait"
    assert command.parameters == {"minutes": 1440, "activity": "work"}


def test_local_parser_supports_week_and_month_waits() -> None:
    state = gray_harbor_state()

    week = interpret_player_text("我等待一周。", "protagonist", state=state)
    month = interpret_player_text("我等待一个月。", "protagonist", state=state)

    assert week.action_type == "wait"
    assert week.parameters == {"minutes": 10_080, "activity": "wait"}
    assert month.action_type == "wait"
    assert month.parameters == {"minutes": 43_200, "activity": "wait"}


def test_claim_scrubber_records_result_claim_but_rules_limit_reveal() -> None:
    state = gray_harbor_state()
    parser = StructuredIntentParser(StubAdapter(result=proposal({
        "action_type": "inspect_item",
        "target_id": "white_heron_operating_ledger",
        "interaction_id": "inspect_white_heron_operating_ledger",
    })))

    command = parser.parse(
        "我已经查明珍妮伪造了签名。",
        "protagonist",
        state,
    )
    result = resolve(state, command)

    assert command.claimed_outcome == "player_claimed_result"
    assert result.outcome == "inspection_completed"
    assert "debt_total_anomaly" in {
        value.payload["clueId"]
        for value in result.events
        if value.event_type == "story.clue_revealed"
    }
    assert all(
        value.payload.get("factId") != "jenny_forged_additional_loan"
        for value in result.events
    )


def test_model_compound_action_respects_existing_scene_beat_limit() -> None:
    state = gray_harbor_state()
    adapter = StubAdapter(result={
        "schema_version": 1,
        "actions": [
            {
                "action_type": "inspect_item",
                "interaction_id": "inspect_iron_hooks_final_notice",
                "target_id": "iron_hooks_final_notice",
            },
            {
                "action_type": "inspect_item",
                "interaction_id": "inspect_white_heron_operating_ledger",
                "target_id": "white_heron_operating_ledger",
            },
        ],
        "needs_clarification": False,
        "confidence": 0.98,
    })
    command = StructuredIntentParser(adapter).parse(
        "我先看通知，再核对账本。",
        "protagonist",
        state,
    )
    result = resolve(state, command)

    assert command.action_type == "compound_action"
    assert command.parser_source == "model"
    assert result.outcome == "compound_pacing_limited"
    assert sum(value.event_type == "item.examined" for value in result.events) == 1


def test_service_persists_model_parse_source_and_idempotency(tmp_path: Path) -> None:
    adapter = StubAdapter(result=ModelAdapterResult(
        output=proposal({
            "action_type": "inspect_item",
            "target_id": "iron_hooks_final_notice",
            "interaction_id": "inspect_iron_hooks_final_notice",
        }),
        metrics=ModelCallMetrics(
            prompt_tokens=120,
            completion_tokens=30,
            total_tokens=150,
            latency_ms=450,
        ),
    ))
    service = GameService(
        tmp_path / "model-turn.sqlite3",
        StructuredIntentParser(adapter),
    )
    service.initialize()
    request = TurnRequest(
        idempotency_key="model-intent-idempotency",
        expected_state_version=1,
        actor_id="protagonist",
        text="读一读那份帮派文书。",
    )

    first = service.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, request)
    second = service.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, request)
    detail = service.get_turn_detail(GRAY_HARBOR_CAMPAIGN_ID, first["turn_id"])

    assert first["command"]["parser_source"] == "model"
    assert first["command"]["parser_model"] == "stub-intent-model"
    assert detail["command"]["parser_source"] == "model"
    assert detail["intent_attempts"][0]["status"] == "model_accepted"
    assert detail["intent_attempts"][0]["provider_name"] == "stub-provider"
    assert detail["intent_attempts"][0]["model_name"] == "stub-intent-model"
    assert detail["intent_attempts"][0]["total_tokens"] == 150
    assert detail["intent_attempts"][0]["latency_ms"] == 450
    assert detail["intent_attempts"][0]["request"] is None
    assert detail["intent_attempts"][0]["response"] is None
    with service.store.connect() as connection:
        private_attempt = service.store.load_turn_intent_attempts(
            connection,
            GRAY_HARBOR_CAMPAIGN_ID,
            first["turn_id"],
            include_payloads=True,
        )[0]
    serialized_attempt = str(private_attempt)
    assert "jenny_forged_additional_loan" not in serialized_attempt
    assert "cellar_tunnel_to_bakery" not in serialized_attempt
    assert private_attempt["prompt_tokens"] == 120
    assert private_attempt["completion_tokens"] == 30
    assert second["replayed"] is True
    assert len(adapter.requests) == 1


def test_service_persists_failed_model_attempt_without_polluting_events(
    tmp_path: Path,
) -> None:
    adapter = StubAdapter(result=proposal({
        "action_type": "move",
        "target_id": "abandoned_bakery",
        "destination_id": "abandoned_bakery",
    }))
    service = GameService(
        tmp_path / "failed-model-turn.sqlite3",
        StructuredIntentParser(adapter),
    )
    service.initialize()
    result = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        TurnRequest(
            idempotency_key="failed-model-audit",
            expected_state_version=1,
            actor_id="protagonist",
            text="我要去一个不存在的密室。",
        ),
    )
    detail = service.get_turn_detail(GRAY_HARBOR_CAMPAIGN_ID, result["turn_id"])

    assert result["command"]["parser_source"] == "model_fallback"
    assert detail["intent_attempts"][0]["status"] == "model_fallback"
    assert detail["intent_attempts"][0]["failure_code"] == (
        "model_invalid_destination"
    )
    assert detail["intent_attempts"][0]["request"] is None
    assert detail["intent_attempts"][0]["response"] is None
    assert "abandoned_bakery" not in str(detail)
    assert all(
        event["event_type"] != "location.exit_discovered"
        for event in detail["events"]
    )


def test_model_call_does_not_hold_sqlite_write_transaction(tmp_path: Path) -> None:
    database_path = tmp_path / "model-outside-write-lock.sqlite3"

    @dataclass
    class LockProbeAdapter(StubAdapter):
        @property
        def model_name(self) -> str:
            return "lock-probe-model"

        def parse_intent(self, request: IntentParseRequest):
            with sqlite3.connect(database_path, timeout=0.1) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
            return super().parse_intent(request)

    adapter = LockProbeAdapter(result=proposal({
        "action_type": "move",
        "destination_id": "white_heron_kitchen",
    }))
    service = GameService(
        database_path,
        StructuredIntentParser(adapter),
    )
    service.initialize()

    result = service.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        TurnRequest(
            idempotency_key="model-call-without-db-write-lock",
            expected_state_version=1,
            actor_id="protagonist",
            text="我去厨房。",
        ),
    )

    assert result["outcome"] == "moved"
    assert result["command"]["parser_source"] == "model"
