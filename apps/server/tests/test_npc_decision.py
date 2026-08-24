from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import httpx
import pytest

from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events
from trpg_server.ai.platform.deepseek import DeepSeekAdapterError, DeepSeekSettings
from trpg_server.core.state import DecisionProfileState, Event
from trpg_server.behavior.router import interpret_player_text, resolve
from trpg_server.characters.decision import (
    DeepSeekNpcDecisionAdapter,
    NpcDecisionAdapterResult,
    SafeNpcDecider,
    build_npc_decision_context,
    npc_decider_from_environment,
)
from trpg_server.core.projection import apply_event, replay
from trpg_server.core.schemas import TurnRequest
from trpg_server.core.service import GameService


def initial_state():
    return replay(GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events(), 1)


def bribe_command(state):
    return interpret_player_text(
        "我把小刀递给哈维，想贿赂他通融。",
        actor_id="protagonist",
        state=state,
    )


def valid_proposal(context, decision: str = "reject") -> dict[str, object]:
    factors = [
        value.fact_id
        for value in context.facts
        if value.fact_id in {"profile_greed", "profile_risk_aversion"}
    ]
    return {
        "schema_version": 1,
        "decision": decision,
        "supported_fact_ids": context.required_fact_ids,
        "cited_factor_ids": factors,
        "cited_memory_ids": [],
        "conditions": [],
        "consequence": (
            "transfer_offered_item" if decision == "accept" else "retain_offered_item"
        ),
        "proposed_events": [],
        "confidence": 0.95,
    }


class ProposalAdapter:
    available = True
    model_name = "fake-npc-model"
    provider_name = "test"

    def __init__(self, decision: str = "reject", mutate=None) -> None:
        self.decision = decision
        self.mutate = mutate
        self.calls = 0
        self.requests = []

    def decide(self, request):
        self.calls += 1
        self.requests.append(request)
        proposal = valid_proposal(request.context, self.decision)
        if self.mutate is not None:
            self.mutate(proposal, request.context)
        return proposal


def test_context_combines_hard_facts_character_conditions_and_real_history() -> None:
    state = initial_state()
    gift_event = Event(
        "evt_old_gift",
        "gift.accepted",
        "harvey_cole",
        0,
        {
            "actorId": "protagonist",
            "targetId": "harvey_cole",
            "itemId": "protagonist_small_knife",
        },
    )
    apply_event(state, gift_event)
    relationship = state.relationship("harvey_cole", "protagonist")
    relationship.favor = 3
    relationship.sources["favor"] = ["evt_helped_harvey"]

    context = build_npc_decision_context(state, bribe_command(state))

    assert context is not None
    facts = {value.fact_id: value for value in context.facts}
    assert context.purpose == "bribe"
    assert facts["offered_item_value"].private_value == "unknown"
    assert facts["profile_monthly_income"].private_value == 720
    assert facts["profile_greed"].private_value == 65
    assert facts["target_role"].private_value == "铁钩帮收账人与街头执行者"
    assert "完成收账" in str(facts["target_motivations"].private_value)
    assert "失去面子" in str(facts["target_fears"].private_value)
    assert "不能抹掉债务" in str(facts["target_behavioral_notes"].private_value)
    assert facts["relationship_favor"].source_event_ids == ["evt_helped_harvey"]
    assert context.memories[0].source_event_ids == ["evt_old_gift"]
    assert "玩家" not in facts["profile_greed"].public_label
    assert isinstance(context.target_abilities, list)
    assert isinstance(context.target_language_style, dict)


def test_building_decision_context_does_not_create_relationship_state() -> None:
    state = initial_state()
    state.relationships.pop(("harvey_cole", "protagonist"), None)

    context = build_npc_decision_context(state, bribe_command(state))

    assert context is not None
    assert ("harvey_cole", "protagonist") not in state.relationships
    facts = {value.fact_id for value in context.facts}
    assert "relationship_baseline" in facts
    assert "relationship_trust" not in facts
    assert "profile_hard_refusals" not in facts


def test_model_can_accept_item_but_cannot_claim_requested_favor_happened() -> None:
    state = initial_state()
    state.items["protagonist_small_knife"].value_crown = 36
    result = SafeNpcDecider(ProposalAdapter("accept")).decide(
        state,
        bribe_command(state),
    )
    resolution = resolve(state, bribe_command(state), result.decision)

    assert result.audit.status == "model_accepted"
    assert resolution.outcome == "bribe_accepted_pending_favor"
    assert any(value.event_type == "item.transferred" for value in resolution.events)
    assert any(value.event_type == "bribe.accepted" for value in resolution.events)
    assert not any(
        value.event_type in {"request.accepted", "location.exit_discovered"}
        for value in resolution.events
    )
    assert "不等于相关要求已经执行" in resolution.narrative


def test_model_cannot_accept_bribe_with_unknown_item_value() -> None:
    state = initial_state()

    result = SafeNpcDecider(ProposalAdapter("accept")).decide(
        state,
        bribe_command(state),
    )

    assert result.audit.status == "model_fallback"
    assert result.audit.failure_code == "unknown_bribe_value"
    assert result.decision is not None
    assert result.decision.outcome == "reject"


def test_overlong_known_factor_list_is_bounded_without_accepting_unknowns() -> None:
    state = initial_state()

    def cite_every_known_factor(proposal, context) -> None:
        proposal["cited_factor_ids"] = [value.fact_id for value in context.facts]

    adapter = ProposalAdapter("reject", cite_every_known_factor)
    result = SafeNpcDecider(adapter).decide(state, bribe_command(state))

    assert result.audit.status == "model_accepted"
    assert result.decision is not None
    assert len(result.decision.factors) <= 12
    assert result.audit.response_payload is not None
    assert result.audit.response_payload["normalization"]["type"] == "cited_factor_bound"


def test_reject_counteroffer_delay_and_test_retain_authoritative_item() -> None:
    for decision in ("reject", "counteroffer", "delay", "test"):
        state = initial_state()
        result = SafeNpcDecider(ProposalAdapter(decision)).decide(
            state,
            bribe_command(state),
        )
        resolution = resolve(state, bribe_command(state), result.decision)

        assert all(value.event_type != "item.transferred" for value in resolution.events)
        assert state.items["protagonist_small_knife"].container_id == "protagonist_equipment"


def test_hard_refusal_and_fake_history_force_safe_fallback() -> None:
    state = initial_state()
    profile = state.decision_profiles["harvey_cole"]
    state.decision_profiles["harvey_cole"] = DecisionProfileState(
        monthly_income_pence=profile.monthly_income_pence,
        economic_pressure=profile.economic_pressure,
        gift_openness=profile.gift_openness,
        greed=profile.greed,
        integrity=profile.integrity,
        risk_aversion=profile.risk_aversion,
        institutional_loyalty=profile.institutional_loyalty,
        corruption_openness=profile.corruption_openness,
        hard_refusals=("bribery",),
        source_event_id=profile.source_event_id,
    )
    forbidden = SafeNpcDecider(ProposalAdapter("accept")).decide(
        state,
        bribe_command(state),
    )
    assert forbidden.audit.failure_code == "decision_not_allowed"
    assert forbidden.decision is not None and forbidden.decision.outcome == "reject"

    def invent_memory(proposal, _):
        proposal["cited_memory_ids"] = ["memory_player_claimed_it"]

    invented = SafeNpcDecider(ProposalAdapter("reject", invent_memory)).decide(
        initial_state(),
        bribe_command(initial_state()),
    )
    assert invented.audit.failure_code == "unknown_memory"
    assert invented.decision is not None and invented.decision.outcome == "reject"


def test_disabled_model_never_accepts_unconfirmed_bribe() -> None:
    state = initial_state()
    result = npc_decider_from_environment({}).decide(state, bribe_command(state))

    assert result.audit.status == "local"
    assert result.decision is not None
    assert result.decision.outcome == "reject"


def test_deepseek_adapter_receives_private_context_but_does_not_leak_key() -> None:
    captured: dict[str, object] = {}
    state = initial_state()
    context = build_npc_decision_context(state, bribe_command(state))
    assert context is not None

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = body
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {"content": json.dumps(valid_proposal(context))},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            },
        )

    adapter = DeepSeekNpcDecisionAdapter(
        DeepSeekSettings(api_key="npc-test-secret", max_attempts=1),
        transport=httpx.MockTransport(handler),
    )
    from trpg_server.characters.decision import NpcDecisionRequest

    result = adapter.decide(NpcDecisionRequest(system_instruction="判断。", context=context))

    assert isinstance(result, NpcDecisionAdapterResult)
    assert result.metrics.total_tokens == 150
    assert captured["authorization"] == "Bearer npc-test-secret"
    body_text = json.dumps(captured["body"], ensure_ascii=False)
    assert "npc-test-secret" not in body_text
    assert "monthly_income" in body_text
    assert "2 到 12 个最重要" in body_text
    assert "不要罗列全部" in body_text


def test_deepseek_adapter_rejects_truncated_or_non_json_response() -> None:
    state = initial_state()
    context = build_npc_decision_context(state, bribe_command(state))
    assert context is not None
    from trpg_server.characters.decision import NpcDecisionRequest

    request = NpcDecisionRequest(system_instruction="判断。", context=context)

    truncated = DeepSeekNpcDecisionAdapter(
        DeepSeekSettings(api_key="npc-test-secret", max_attempts=1),
        transport=httpx.MockTransport(lambda _: httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}, "finish_reason": "length"}]},
        )),
    )
    with pytest.raises(DeepSeekAdapterError, match="truncated"):
        truncated.decide(request)

    non_json = DeepSeekNpcDecisionAdapter(
        DeepSeekSettings(api_key="npc-test-secret", max_attempts=1),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="not-json")),
    )
    with pytest.raises(DeepSeekAdapterError, match="not JSON"):
        non_json.decide(request)


def test_service_bribe_is_idempotent_and_audited(tmp_path: Path) -> None:
    adapter = ProposalAdapter("accept")
    game = GameService(
        tmp_path / "npc-service.sqlite3",
        npc_decider=SafeNpcDecider(adapter),
    )
    game.initialize()
    request = TurnRequest(
        idempotency_key="npc-idempotency-001",
        expected_state_version=1,
        actor_id="protagonist",
        text="我把小刀递给哈维，想贿赂他通融。",
    )

    first = game.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, request)
    second = game.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, request)
    detail = game.get_turn_detail(GRAY_HARBOR_CAMPAIGN_ID, first["turn_id"])

    assert first["outcome"] == "bribe_rejected"
    assert second["replayed"] is True
    assert adapter.calls == 1
    assert len(detail["npc_decision_attempts"]) == 1
    assert detail["npc_decision_attempts"][0]["status"] == "model_fallback"
    assert detail["npc_decision_attempts"][0]["request"] is None
    assert detail["npc_decision_attempts"][0]["response"] is None
    assert "decision_profiles" not in first["state"]
    with game.store.connect() as connection:
        private = game.store.load_turn_npc_decision_attempts(
            connection,
            GRAY_HARBOR_CAMPAIGN_ID,
            first["turn_id"],
            include_payloads=True,
        )[0]
    assert private["request"]["context"]["schema_version"] == 3
    assert any(
        fact["fact_id"] == "profile_monthly_income"
        for fact in private["request"]["context"]["facts"]
    )


def test_npc_decision_model_call_does_not_hold_sqlite_write_lock(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "npc-outside-lock.sqlite3"

    class LockCheckingAdapter(ProposalAdapter):
        def decide(self, request):
            with sqlite3.connect(database_path, timeout=0.1) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
            return super().decide(request)

    adapter = LockCheckingAdapter("reject")
    game = GameService(
        database_path,
        npc_decider=SafeNpcDecider(adapter),
    )
    game.initialize()
    result = game.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        TurnRequest(
            idempotency_key="npc-outside-lock-001",
            expected_state_version=1,
            actor_id="protagonist",
            text="我把小刀递给哈维，想贿赂他通融。",
        ),
    )

    assert result["outcome"] == "bribe_rejected"
    assert adapter.calls == 1
