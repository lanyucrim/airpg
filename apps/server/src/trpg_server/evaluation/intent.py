from __future__ import annotations

from collections import defaultdict
from math import ceil
from pathlib import Path
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trpg_server.core.state import Projection
from trpg_server.behavior.router import resolve
from trpg_server.ai.player.intent import StructuredIntentParser


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntentEvaluationThresholds(EvaluationModel):
    minimum_pass_rate: float = Field(ge=0, le=1)
    minimum_accepted_accuracy: float = Field(ge=0, le=1)
    minimum_category_pass_rate: float = Field(ge=0, le=1)
    maximum_leak_count: int = Field(ge=0)
    maximum_unsafe_count: int = Field(ge=0)
    maximum_p95_latency_ms: int = Field(ge=1)
    maximum_average_tokens: int = Field(ge=1)


class IntentEvaluationExpectation(EvaluationModel):
    mode: Literal["accepted", "fallback", "safe"]
    action_type: str | None = None
    target_id: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    claimed_outcome_required: bool = False
    outcome: str | None = None
    required_event_types: list[str] = Field(default_factory=list)
    allowed_event_types: list[str] | None = None


class IntentEvaluationCase(EvaluationModel):
    case_id: str
    category: str
    text: str
    expectation: IntentEvaluationExpectation
    forbidden_response_terms: list[str] = Field(default_factory=list)


class IntentEvaluationSuite(EvaluationModel):
    schema_version: Literal[1]
    suite_id: str
    version: str
    scenario_id: str
    actor_id: str
    forbidden_response_terms: list[str]
    thresholds: IntentEvaluationThresholds
    cases: list[IntentEvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> IntentEvaluationSuite:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("evaluation case ids must be unique")
        return self


def load_intent_evaluation_suite(path: Path) -> IntentEvaluationSuite:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return IntentEvaluationSuite.model_validate(data)


def evaluate_intent_suite(
    parser: StructuredIntentParser,
    state: Projection,
    suite: IntentEvaluationSuite,
    case_ids: set[str] | None = None,
) -> dict[str, Any]:
    if state.scenario_id != suite.scenario_id:
        raise ValueError(
            f"evaluation scenario mismatch: {state.scenario_id} != {suite.scenario_id}"
        )
    if suite.actor_id not in state.character_locations:
        raise ValueError(f"evaluation actor is missing: {suite.actor_id}")
    selected = [
        case for case in suite.cases
        if case_ids is None or case.case_id in case_ids
    ]
    if not selected:
        raise ValueError("evaluation selection contains no cases")
    unknown = (case_ids or set()) - {case.case_id for case in selected}
    if unknown:
        raise ValueError(f"unknown evaluation case ids: {sorted(unknown)}")

    results = [evaluate_intent_case(parser, state, suite, case) for case in selected]
    return _build_report(suite, selected, results, partial=case_ids is not None)


def evaluate_intent_case(
    parser: StructuredIntentParser,
    state: Projection,
    suite: IntentEvaluationSuite,
    case: IntentEvaluationCase,
) -> dict[str, Any]:
    parsed = parser.parse_with_audit(case.text, suite.actor_id, state)
    command = parsed.command
    resolution = resolve(state, command)
    event_types = [event.event_type for event in resolution.events]
    response_text = json.dumps(
        parsed.audit.response_payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    forbidden_terms = set(suite.forbidden_response_terms)
    forbidden_terms.update(case.forbidden_response_terms)
    leaked_terms = sorted(term for term in forbidden_terms if term in response_text)
    failures: list[str] = []
    unsafe = False

    if leaked_terms:
        failures.append("forbidden_response_term")
        unsafe = True

    expected = case.expectation
    if expected.mode == "accepted":
        _expect(command.parser_source == "model", "model_not_accepted", failures)
        _expect(command.action_type == expected.action_type, "wrong_action", failures)
        _expect(command.target_id == expected.target_id, "wrong_target", failures)
        for key, value in expected.parameters.items():
            _expect(command.parameters.get(key) == value, f"wrong_parameter:{key}", failures)
        if expected.claimed_outcome_required:
            _expect(command.claimed_outcome is not None, "missing_claim_marker", failures)
    elif expected.mode == "fallback":
        _expect(command.parser_source == "model_fallback", "unsafe_model_accept", failures)
        _expect(command.parser_failure_code is not None, "missing_fallback_reason", failures)
        if command.parser_source != "model_fallback":
            unsafe = True

    if expected.outcome is not None:
        _expect(resolution.outcome == expected.outcome, "wrong_outcome", failures)
    for event_type in expected.required_event_types:
        _expect(event_type in event_types, f"missing_event:{event_type}", failures)
    if expected.allowed_event_types is not None:
        unexpected_events = sorted(set(event_types) - set(expected.allowed_event_types))
        if unexpected_events:
            failures.append("unsafe_event_type")
            unsafe = True

    return {
        "caseId": case.case_id,
        "category": case.category,
        "passed": not failures,
        "failures": failures,
        "unsafe": unsafe,
        "leakedTerms": leaked_terms,
        "parserSource": command.parser_source,
        "failureCode": command.parser_failure_code,
        "actionType": command.action_type,
        "targetId": command.target_id,
        "outcome": resolution.outcome,
        "eventTypes": event_types,
        "tokens": parsed.audit.total_tokens,
        "latencyMs": parsed.audit.latency_ms,
    }


def _build_report(
    suite: IntentEvaluationSuite,
    selected: list[IntentEvaluationCase],
    results: list[dict[str, Any]],
    partial: bool,
) -> dict[str, Any]:
    passed = sum(bool(result["passed"]) for result in results)
    accepted = [
        result
        for case, result in zip(selected, results, strict=True)
        if case.expectation.mode == "accepted"
    ]
    category_totals: dict[str, int] = defaultdict(int)
    category_passed: dict[str, int] = defaultdict(int)
    for result in results:
        category = str(result["category"])
        category_totals[category] += 1
        category_passed[category] += int(bool(result["passed"]))
    category_rates = {
        category: round(category_passed[category] / total, 4)
        for category, total in sorted(category_totals.items())
    }
    latencies = sorted(
        int(result["latencyMs"])
        for result in results
        if result["latencyMs"] is not None
    )
    tokens = [
        int(result["tokens"])
        for result in results
        if result["tokens"] is not None
    ]
    pass_rate = passed / len(results)
    accepted_accuracy = (
        sum(bool(result["passed"]) for result in accepted) / len(accepted)
        if accepted else 1.0
    )
    p95_latency = _percentile_nearest_rank(latencies, 0.95)
    average_tokens = round(sum(tokens) / len(tokens)) if tokens else None
    leak_count = sum(bool(result["leakedTerms"]) for result in results)
    unsafe_count = sum(bool(result["unsafe"]) for result in results)

    gates: dict[str, bool] = {
        "passRate": pass_rate >= suite.thresholds.minimum_pass_rate,
        "acceptedAccuracy": (
            accepted_accuracy >= suite.thresholds.minimum_accepted_accuracy
        ),
        "categoryPassRate": (
            min(category_rates.values())
            >= suite.thresholds.minimum_category_pass_rate
        ),
        "leakCount": leak_count <= suite.thresholds.maximum_leak_count,
        "unsafeCount": unsafe_count <= suite.thresholds.maximum_unsafe_count,
        "p95Latency": (
            p95_latency is not None
            and p95_latency <= suite.thresholds.maximum_p95_latency_ms
        ),
        "averageTokens": (
            average_tokens is not None
            and average_tokens <= suite.thresholds.maximum_average_tokens
        ),
    }
    if partial:
        gates = {
            "selectedCases": passed == len(results),
            "leakCount": leak_count == 0,
            "unsafeCount": unsafe_count == 0,
        }

    return {
        "suiteId": suite.suite_id,
        "suiteVersion": suite.version,
        "partial": partial,
        "passed": passed,
        "total": len(results),
        "passRate": round(pass_rate, 4),
        "acceptedAccuracy": round(accepted_accuracy, 4),
        "categoryPassRates": category_rates,
        "leakCount": leak_count,
        "unsafeCount": unsafe_count,
        "totalTokens": sum(tokens),
        "averageTokens": average_tokens,
        "p50LatencyMs": _percentile_nearest_rank(latencies, 0.50),
        "p95LatencyMs": p95_latency,
        "gates": gates,
        "gatesPassed": all(gates.values()),
        "failedCaseIds": [
            result["caseId"] for result in results if not result["passed"]
        ],
        "results": results,
    }


def _expect(condition: bool, code: str, failures: list[str]) -> None:
    if not condition:
        failures.append(code)


def _percentile_nearest_rank(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    index = max(0, ceil(percentile * len(values)) - 1)
    return values[index]
