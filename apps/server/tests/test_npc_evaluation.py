from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events
from trpg_server.ai.platform.contracts import ModelCallMetrics
from trpg_server.characters.decision import NpcDecisionAdapterResult, SafeNpcDecider
from trpg_server.evaluation.npc_decision import (
    NpcDecisionEvaluationCase,
    NpcDecisionEvaluationComparison,
    NpcDecisionEvaluationExpectation,
    NpcDecisionEvaluationItem,
    NpcDecisionEvaluationSetup,
    NpcDecisionEvaluationSuite,
    NpcDecisionEvaluationThresholds,
    evaluate_npc_decision_suite,
    load_npc_decision_evaluation_suite,
)
from trpg_server.core.projection import replay


SUITE_PATH = (
    Path(__file__).resolve().parents[3]
    / "evals"
    / "npc-decision"
    / "gray-harbor-v1.json"
)


def state():
    return replay(GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events(), 1)


@dataclass
class MappingDecisionAdapter:
    decisions: dict[str, str]
    mutate: Any = None
    available = True
    provider_name = "evaluation-stub"
    model_name = "evaluation-stub-model"

    def decide(self, request):
        decision = self.decisions[request.context.player_text]
        factors = [
            fact.fact_id
            for fact in request.context.facts
            if fact.fact_id in {
                "offered_item_value",
                "requested_favor_risk",
                "relationship_trust",
            }
        ][:2]
        proposal = {
            "schema_version": 1,
            "decision": decision,
            "supported_fact_ids": request.context.required_fact_ids,
            "cited_factor_ids": factors,
            "cited_memory_ids": (
                [request.context.memories[0].memory_id]
                if request.context.memories else []
            ),
            "conditions": [],
            "consequence": (
                "transfer_offered_item"
                if decision == "accept"
                else "retain_offered_item"
            ),
            "proposed_events": [],
            "confidence": 0.95,
        }
        if self.mutate is not None:
            self.mutate(proposal, request)
        return NpcDecisionAdapterResult(
            output=proposal,
            metrics=ModelCallMetrics(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                latency_ms=250,
            ),
        )


def thresholds() -> NpcDecisionEvaluationThresholds:
    return NpcDecisionEvaluationThresholds(
        minimum_pass_rate=1,
        minimum_model_accept_rate=1,
        minimum_category_pass_rate=1,
        minimum_comparison_pass_rate=1,
        maximum_leak_count=0,
        maximum_unsafe_count=0,
        maximum_p95_latency_ms=1000,
        maximum_average_tokens=200,
    )


def evaluation_case(
    case_id: str,
    text: str,
    allowed: list[str],
    value: int = 100,
) -> NpcDecisionEvaluationCase:
    return NpcDecisionEvaluationCase(
        case_id=case_id,
        category="test",
        target_id="harvey_cole",
        purpose="bribe",
        requested_favor_risk=20,
        player_text=text,
        setup=NpcDecisionEvaluationSetup(item=NpcDecisionEvaluationItem(
            item_id=f"eval_{case_id}",
            definition_id="evaluation_item",
            name="测试物品",
            description="NPC 决策评估使用的测试物品。",
            category="miscellaneous",
            is_plot_item=False,
            quantity=1,
            stackable=False,
            unit_weight_grams=None,
            value_crown=value,
            condition="intact",
            durability=None,
            container_id=None,
            location_id=None,
            properties={},
        )),
        expectation=NpcDecisionEvaluationExpectation(
            allowed_decisions=allowed,
            any_factor_ids=["offered_item_value", "requested_favor_risk"],
        ),
    )


def suite(cases, comparisons=None) -> NpcDecisionEvaluationSuite:
    return NpcDecisionEvaluationSuite(
        schema_version=1,
        suite_id="small-npc-suite",
        version="1.0.0",
        scenario_id="gray-harbor-black-tide-throne",
        actor_id="protagonist",
        forbidden_response_terms=["hidden_secret"],
        thresholds=thresholds(),
        cases=cases,
        comparisons=comparisons or [],
    )


def test_versioned_gray_harbor_npc_suite_loads() -> None:
    loaded = load_npc_decision_evaluation_suite(SUITE_PATH)

    assert loaded.suite_id == "gray-harbor-npc-decision"
    assert loaded.version == "1.0.0"
    assert len(loaded.cases) == 11
    assert len(loaded.comparisons) == 5


def test_evaluator_scores_decision_events_metrics_and_comparison() -> None:
    cases = [
        evaluation_case("less", "较低条件", ["reject"]),
        evaluation_case("more", "较高条件", ["accept"]),
    ]
    comparison = NpcDecisionEvaluationComparison(
        comparison_id="more-is-higher",
        more_receptive_case_id="more",
        less_receptive_case_id="less",
        minimum_rank_delta=1,
    )
    decider = SafeNpcDecider(MappingDecisionAdapter({
        "较低条件": "reject",
        "较高条件": "accept",
    }))

    report = evaluate_npc_decision_suite(
        decider,
        state,
        suite(cases, [comparison]),
    )

    assert report["gatesPassed"] is True
    assert report["modelAcceptRate"] == 1
    assert report["averageTokens"] == 120
    assert report["comparisons"][0]["rankDelta"] == 4
    assert "item.transferred" not in report["results"][0]["eventTypes"]
    assert "item.transferred" in report["results"][1]["eventTypes"]
    assert "request.accepted" not in report["results"][1]["eventTypes"]


def test_evaluator_flags_model_fallback_without_marking_safe_fallback_unsafe() -> None:
    case = evaluation_case("worthless", "废纸贿赂", ["reject"], value=0)
    decider = SafeNpcDecider(MappingDecisionAdapter({"废纸贿赂": "accept"}))

    report = evaluate_npc_decision_suite(decider, state, suite([case]))

    result = report["results"][0]
    assert report["gatesPassed"] is False
    assert result["auditStatus"] == "model_fallback"
    assert result["failureCode"] == "worthless_bribe"
    assert result["unsafe"] is False
    assert "item.transferred" not in result["eventTypes"]


def test_evaluator_supports_partial_selection() -> None:
    cases = [
        evaluation_case("one", "第一项", ["reject"]),
        evaluation_case("two", "第二项", ["accept"]),
    ]
    decider = SafeNpcDecider(MappingDecisionAdapter({"第一项": "reject"}))

    report = evaluate_npc_decision_suite(
        decider,
        state,
        suite(cases),
        {"one"},
    )

    assert report["partial"] is True
    assert report["gatesPassed"] is True
    assert report["total"] == 1


def test_suite_rejects_duplicate_and_unknown_comparison_case_ids() -> None:
    case = evaluation_case("duplicate", "测试", ["reject"])
    with pytest.raises(ValidationError, match="unique"):
        suite([case, case])

    with pytest.raises(ValidationError, match="unknown cases"):
        suite([case], [NpcDecisionEvaluationComparison(
            comparison_id="invalid",
            more_receptive_case_id="duplicate",
            less_receptive_case_id="missing",
        )])
