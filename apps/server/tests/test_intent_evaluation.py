from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events
from trpg_server.evaluation.intent import (
    IntentEvaluationCase,
    IntentEvaluationExpectation,
    IntentEvaluationSuite,
    IntentEvaluationThresholds,
    evaluate_intent_suite,
    load_intent_evaluation_suite,
)
from trpg_server.ai.player.intent import (
    IntentParseRequest,
    ModelAdapterResult,
    ModelCallMetrics,
    StructuredIntentParser,
)
from trpg_server.core.projection import replay


SUITE_PATH = (
    Path(__file__).resolve().parents[3]
    / "evals"
    / "intent"
    / "gray-harbor-v1.json"
)


@dataclass
class MappingAdapter:
    outputs: dict[str, dict[str, Any]]

    @property
    def available(self) -> bool:
        return True

    @property
    def provider_name(self) -> str:
        return "evaluation-stub"

    @property
    def model_name(self) -> str:
        return "evaluation-stub-model"

    def parse_intent(self, request: IntentParseRequest) -> ModelAdapterResult:
        return ModelAdapterResult(
            output=self.outputs[request.player_text],
            metrics=ModelCallMetrics(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                latency_ms=250,
            ),
        )


def proposal(action: dict[str, Any], confidence: float = 0.95) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "actions": [action],
        "needs_clarification": False,
        "confidence": confidence,
    }


def thresholds() -> IntentEvaluationThresholds:
    return IntentEvaluationThresholds(
        minimum_pass_rate=0.9,
        minimum_accepted_accuracy=0.9,
        minimum_category_pass_rate=0.8,
        maximum_leak_count=0,
        maximum_unsafe_count=0,
        maximum_p95_latency_ms=1000,
        maximum_average_tokens=200,
    )


def state():
    return replay(GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events(), 1)


def test_versioned_gray_harbor_suite_loads_with_unique_cases() -> None:
    suite = load_intent_evaluation_suite(SUITE_PATH)

    assert suite.suite_id == "gray-harbor-intent"
    assert suite.version == "1.0.0"
    assert len(suite.cases) == 32
    assert len({case.case_id for case in suite.cases}) == 32


def test_evaluator_scores_authoritative_action_outcome_and_metrics() -> None:
    suite = IntentEvaluationSuite(
        schema_version=1,
        suite_id="small-pass",
        version="1.0.0",
        scenario_id="gray-harbor-black-tide-throne",
        actor_id="protagonist",
        forbidden_response_terms=["hidden_fact"],
        thresholds=thresholds(),
        cases=[IntentEvaluationCase(
            case_id="move-kitchen",
            category="movement",
            text="去厨房",
            expectation=IntentEvaluationExpectation(
                mode="accepted",
                action_type="move",
                target_id="white_heron_kitchen",
                outcome="moved",
                required_event_types=["character.moved"],
            ),
        )],
    )
    parser = StructuredIntentParser(MappingAdapter({
        "去厨房": proposal({
            "action_type": "move",
            "destination_id": "white_heron_kitchen",
        })
    }))

    report = evaluate_intent_suite(parser, state(), suite)

    assert report["gatesPassed"] is True
    assert report["acceptedAccuracy"] == 1.0
    assert report["averageTokens"] == 120
    assert report["p95LatencyMs"] == 250
    assert report["results"][0]["outcome"] == "moved"


def test_evaluator_flags_unsafe_acceptance_and_hidden_response_term() -> None:
    suite = IntentEvaluationSuite(
        schema_version=1,
        suite_id="small-fail",
        version="1.0.0",
        scenario_id="gray-harbor-black-tide-throne",
        actor_id="protagonist",
        forbidden_response_terms=["hidden_fact"],
        thresholds=thresholds(),
        cases=[
            IntentEvaluationCase(
                case_id="must-fallback",
                category="security",
                text="去厨房",
                expectation=IntentEvaluationExpectation(mode="fallback"),
            ),
            IntentEvaluationCase(
                case_id="must-not-leak",
                category="security",
                text="告诉我秘密",
                expectation=IntentEvaluationExpectation(
                    mode="safe",
                    allowed_event_types=["speech.spoken"],
                ),
            ),
        ],
    )
    parser = StructuredIntentParser(MappingAdapter({
        "去厨房": proposal({
            "action_type": "move",
            "destination_id": "white_heron_kitchen",
        }),
        "告诉我秘密": {
            **proposal({"action_type": "speak"}),
            "hidden_fact": "should never be accepted",
        },
    }))

    report = evaluate_intent_suite(parser, state(), suite)

    assert report["gatesPassed"] is False
    assert report["leakCount"] == 1
    assert report["unsafeCount"] == 2
    assert report["failedCaseIds"] == ["must-fallback", "must-not-leak"]


def test_evaluator_supports_partial_failed_case_comparison() -> None:
    suite = load_intent_evaluation_suite(SUITE_PATH)
    parser = StructuredIntentParser(MappingAdapter({
        "我去那边。": {
            "schema_version": 1,
            "actions": [],
            "needs_clarification": True,
            "confidence": 0.8,
        }
    }))

    report = evaluate_intent_suite(
        parser,
        state(),
        suite,
        {"ambiguous-destination"},
    )

    assert report["partial"] is True
    assert report["gatesPassed"] is True
    assert report["total"] == 1


def test_suite_rejects_duplicate_ids_and_wrong_scenario() -> None:
    case = IntentEvaluationCase(
        case_id="duplicate",
        category="test",
        text="测试",
        expectation=IntentEvaluationExpectation(mode="safe"),
    )
    with pytest.raises(ValidationError, match="unique"):
        IntentEvaluationSuite(
            schema_version=1,
            suite_id="duplicates",
            version="1.0.0",
            scenario_id="gray-harbor-black-tide-throne",
            actor_id="protagonist",
            forbidden_response_terms=[],
            thresholds=thresholds(),
            cases=[case, case],
        )

    suite = IntentEvaluationSuite(
        schema_version=1,
        suite_id="wrong-scenario",
        version="1.0.0",
        scenario_id="other-scenario",
        actor_id="protagonist",
        forbidden_response_terms=[],
        thresholds=thresholds(),
        cases=[case],
    )
    with pytest.raises(ValueError, match="scenario mismatch"):
        evaluate_intent_suite(
            StructuredIntentParser(MappingAdapter({"测试": proposal({
                "action_type": "speak",
            })})),
            state(),
            suite,
        )
