from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from math import ceil
from pathlib import Path
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trpg_server.core.state import Event, ParsedCommand, Projection
from trpg_server.behavior.router import resolve
from trpg_server.characters.decision import SafeNpcDecider
from trpg_server.core.projection import apply_event


RELATIONSHIP_DIMENSIONS = {
    "favor",
    "trust",
    "fear",
    "respect",
    "suspicion",
    "debt",
}
RECEPTIVITY_RANK = {
    "reject": 0,
    "delay": 1,
    "test": 2,
    "counteroffer": 3,
    "accept": 4,
}
FORBIDDEN_SIDE_EFFECT_EVENTS = {
    "request.accepted",
    "location.exit_discovered",
    "knowledge.learned",
    "story.clue_revealed",
}


class NpcEvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NpcDecisionEvaluationThresholds(NpcEvaluationModel):
    minimum_pass_rate: float = Field(ge=0, le=1)
    minimum_model_accept_rate: float = Field(ge=0, le=1)
    minimum_category_pass_rate: float = Field(ge=0, le=1)
    minimum_comparison_pass_rate: float = Field(ge=0, le=1)
    maximum_leak_count: int = Field(ge=0)
    maximum_unsafe_count: int = Field(ge=0)
    maximum_p95_latency_ms: int = Field(ge=1)
    maximum_average_tokens: int = Field(ge=1)


class NpcDecisionEvaluationItem(NpcEvaluationModel):
    item_id: str
    definition_id: str
    name: str
    description: str
    category: str
    is_plot_item: bool
    quantity: int = Field(ge=1)
    stackable: bool
    unit_weight_grams: int | None = Field(default=None, ge=0)
    value_crown: int | None = Field(default=None, ge=0)
    condition: str | None = None
    durability: dict[str, int] | None = None
    container_id: str | None = None
    location_id: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class NpcDecisionEvaluationMemory(NpcEvaluationModel):
    item_name: str
    item_definition_id: str = "evaluation_previous_gift"


class NpcDecisionEvaluationSetup(NpcEvaluationModel):
    item: NpcDecisionEvaluationItem
    relationship_deltas: dict[str, int] = Field(default_factory=dict)
    accepted_gift_memory: NpcDecisionEvaluationMemory | None = None

    @model_validator(mode="after")
    def relationship_dimensions_are_known(self) -> NpcDecisionEvaluationSetup:
        unknown = set(self.relationship_deltas) - RELATIONSHIP_DIMENSIONS
        if unknown:
            raise ValueError(f"unknown relationship dimensions: {sorted(unknown)}")
        return self


class NpcDecisionEvaluationExpectation(NpcEvaluationModel):
    allowed_decisions: list[
        Literal["accept", "reject", "counteroffer", "delay", "test"]
    ] = Field(min_length=1)
    required_factor_ids: list[str] = Field(default_factory=list)
    any_factor_ids: list[str] = Field(default_factory=list)
    require_memory_citation: bool = False
    forbid_memory_citation: bool = False


class NpcDecisionEvaluationCase(NpcEvaluationModel):
    case_id: str
    category: str
    target_id: str
    purpose: Literal["gift", "bribe"]
    requested_favor_risk: int = Field(ge=0, le=100)
    player_text: str
    setup: NpcDecisionEvaluationSetup
    expectation: NpcDecisionEvaluationExpectation
    forbidden_response_terms: list[str] = Field(default_factory=list)


class NpcDecisionEvaluationComparison(NpcEvaluationModel):
    comparison_id: str
    more_receptive_case_id: str
    less_receptive_case_id: str
    minimum_rank_delta: int = Field(default=0, ge=0, le=4)


class NpcDecisionEvaluationSuite(NpcEvaluationModel):
    schema_version: Literal[1]
    suite_id: str
    version: str
    scenario_id: str
    actor_id: str
    forbidden_response_terms: list[str]
    thresholds: NpcDecisionEvaluationThresholds
    cases: list[NpcDecisionEvaluationCase] = Field(min_length=1)
    comparisons: list[NpcDecisionEvaluationComparison] = Field(default_factory=list)

    @model_validator(mode="after")
    def identifiers_and_comparisons_are_valid(self) -> NpcDecisionEvaluationSuite:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("evaluation case ids must be unique")
        comparison_ids = [value.comparison_id for value in self.comparisons]
        if len(comparison_ids) != len(set(comparison_ids)):
            raise ValueError("evaluation comparison ids must be unique")
        known = set(case_ids)
        for comparison in self.comparisons:
            referenced = {
                comparison.more_receptive_case_id,
                comparison.less_receptive_case_id,
            }
            if not referenced <= known:
                raise ValueError(
                    f"comparison references unknown cases: {sorted(referenced - known)}"
                )
        return self


def load_npc_decision_evaluation_suite(path: Path) -> NpcDecisionEvaluationSuite:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return NpcDecisionEvaluationSuite.model_validate(data)


def evaluate_npc_decision_suite(
    decider: SafeNpcDecider,
    state_factory: Callable[[], Projection],
    suite: NpcDecisionEvaluationSuite,
    case_ids: set[str] | None = None,
) -> dict[str, Any]:
    selected = [
        case for case in suite.cases
        if case_ids is None or case.case_id in case_ids
    ]
    if not selected:
        raise ValueError("evaluation selection contains no cases")
    unknown = (case_ids or set()) - {case.case_id for case in selected}
    if unknown:
        raise ValueError(f"unknown evaluation case ids: {sorted(unknown)}")

    results = [
        evaluate_npc_decision_case(decider, state_factory(), suite, case)
        for case in selected
    ]
    selected_ids = {case.case_id for case in selected}
    comparisons = [
        comparison for comparison in suite.comparisons
        if comparison.more_receptive_case_id in selected_ids
        and comparison.less_receptive_case_id in selected_ids
    ]
    comparison_results = _evaluate_comparisons(comparisons, results)
    return _build_report(
        suite,
        results,
        comparison_results,
        partial=case_ids is not None,
    )


def evaluate_npc_decision_case(
    decider: SafeNpcDecider,
    state: Projection,
    suite: NpcDecisionEvaluationSuite,
    case: NpcDecisionEvaluationCase,
) -> dict[str, Any]:
    if state.scenario_id != suite.scenario_id:
        raise ValueError(
            f"evaluation scenario mismatch: {state.scenario_id} != {suite.scenario_id}"
        )
    command = _prepare_case(state, suite, case)
    result = decider.decide(state, command)
    decision = result.decision
    resolution = resolve(state, command, decision)
    factor_ids = [factor.factor_id for factor in decision.factors] if decision else []
    cited_memory_ids = list(decision.cited_memory_ids) if decision else []
    event_types = [event.event_type for event in resolution.events]
    response_text = json.dumps(
        result.audit.response_payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    forbidden_terms = set(suite.forbidden_response_terms)
    forbidden_terms.update(case.forbidden_response_terms)
    leaked_terms = sorted(term for term in forbidden_terms if term in response_text)
    failures: list[str] = []
    unsafe = False

    if result.audit.status != "model_accepted":
        failures.append("model_not_accepted")
    if decision is None:
        failures.append("missing_confirmed_decision")
        decision_outcome = None
    else:
        decision_outcome = decision.outcome
        if decision.outcome not in case.expectation.allowed_decisions:
            failures.append("unexpected_decision")
        missing_factors = sorted(
            set(case.expectation.required_factor_ids) - set(factor_ids)
        )
        failures.extend(f"missing_factor:{value}" for value in missing_factors)
        if (
            case.expectation.any_factor_ids
            and not set(case.expectation.any_factor_ids).intersection(factor_ids)
        ):
            failures.append("missing_any_expected_factor")
        if case.expectation.require_memory_citation and not cited_memory_ids:
            failures.append("missing_memory_citation")
        if case.expectation.forbid_memory_citation and cited_memory_ids:
            failures.append("unexpected_memory_citation")

    if leaked_terms:
        failures.append("forbidden_response_term")
        unsafe = True
    unexpected_side_effects = sorted(
        set(event_types).intersection(FORBIDDEN_SIDE_EFFECT_EVENTS)
    )
    if unexpected_side_effects:
        failures.append("unsafe_side_effect_event")
        unsafe = True
    transferred = "item.transferred" in event_types
    if decision_outcome == "accept" and not transferred:
        failures.append("accepted_without_item_transfer")
        unsafe = True
    if decision_outcome != "accept" and transferred:
        failures.append("nonaccept_transferred_item")
        unsafe = True

    return {
        "caseId": case.case_id,
        "category": case.category,
        "passed": not failures,
        "failures": failures,
        "unsafe": unsafe,
        "leakedTerms": leaked_terms,
        "decision": decision_outcome,
        "factorIds": factor_ids,
        "citedMemoryIds": cited_memory_ids,
        "auditStatus": result.audit.status,
        "failureCode": result.audit.failure_code,
        "resolutionOutcome": resolution.outcome,
        "eventTypes": event_types,
        "tokens": result.audit.total_tokens,
        "latencyMs": result.audit.latency_ms,
    }


def _prepare_case(
    state: Projection,
    suite: NpcDecisionEvaluationSuite,
    case: NpcDecisionEvaluationCase,
) -> ParsedCommand:
    if suite.actor_id not in state.character_locations:
        raise ValueError(f"evaluation actor is missing: {suite.actor_id}")
    if case.target_id not in state.character_locations:
        raise ValueError(f"evaluation target is missing: {case.target_id}")
    actor_location = state.character_locations[suite.actor_id]
    if state.character_locations[case.target_id] != actor_location:
        apply_event(state, Event(
            event_id=f"eval_{case.case_id}_target_moved",
            event_type="character.moved",
            actor_id=case.target_id,
            world_time=state.world_time,
            payload={
                "characterId": case.target_id,
                "fromLocationId": state.character_locations[case.target_id],
                "toLocationId": actor_location,
            },
        ))
    target_container = next((
        container.container_id
        for container in state.containers.values()
        if container.owner_character_id == case.target_id and container.kind == "inventory"
    ), None)
    if target_container is None:
        target_container = f"eval_{case.case_id}_{case.target_id}_inventory"
        apply_event(state, Event(
            event_id=f"eval_{case.case_id}_target_container",
            event_type="container.created",
            actor_id="system",
            world_time=state.world_time,
            payload={
                "containerId": target_container,
                "kind": "inventory",
                "ownerCharacterId": case.target_id,
            },
        ))
    actor_container = next((
        container.container_id
        for container in state.containers.values()
        if container.owner_character_id == suite.actor_id
        and container.kind == "inventory"
    ), None)
    if actor_container is None:
        raise ValueError("evaluation actor has no inventory")
    item = case.setup.item
    apply_event(state, Event(
        event_id=f"eval_{case.case_id}_item_created",
        event_type="item.created",
        actor_id="system",
        world_time=state.world_time,
        payload={"item": {
            "id": item.item_id,
            "definitionId": item.definition_id,
            "name": item.name,
            "description": item.description,
            "category": item.category,
            "isPlotItem": item.is_plot_item,
            "quantity": item.quantity,
            "stackable": item.stackable,
            "unitWeightGrams": item.unit_weight_grams,
            "valueCrown": item.value_crown,
            "condition": item.condition,
            "durability": item.durability,
            "containerId": actor_container,
            "locationId": None,
            "properties": item.properties,
        }},
        schema_version=3,
    ))
    for dimension, delta in case.setup.relationship_deltas.items():
        event_id = f"eval_{case.case_id}_relationship_{dimension}"
        apply_event(state, Event(
            event_id=event_id,
            event_type="relationship.changed",
            actor_id=case.target_id,
            world_time=state.world_time,
            payload={
                "subjectId": case.target_id,
                "objectId": suite.actor_id,
                "dimension": dimension,
                "delta": delta,
                "sourceEventId": event_id,
            },
        ))
    memory = case.setup.accepted_gift_memory
    if memory is not None:
        memory_item_id = f"eval_{case.case_id}_previous_gift"
        apply_event(state, Event(
            event_id=f"eval_{case.case_id}_memory_item_created",
            event_type="item.created",
            actor_id="system",
            world_time=max(0, state.world_time - 1440),
            payload={"item": {
                "id": memory_item_id,
                "definitionId": memory.item_definition_id,
                "name": memory.item_name,
                "description": "NPC 决策评估中已确认收下的历史礼物。",
                "category": "miscellaneous",
                "isPlotItem": False,
                "quantity": 1,
                "stackable": False,
                "unitWeightGrams": None,
                "valueCrown": 1,
                "condition": "intact",
                "durability": None,
                "containerId": target_container,
                "locationId": None,
                "properties": {},
            }},
            schema_version=3,
        ))
        apply_event(state, Event(
            event_id=f"eval_{case.case_id}_gift_accepted",
            event_type="gift.accepted",
            actor_id=case.target_id,
            world_time=max(0, state.world_time - 1440),
            payload={
                "actorId": suite.actor_id,
                "targetId": case.target_id,
                "itemId": memory_item_id,
                "offerEventId": f"eval_{case.case_id}_gift_offered",
            },
        ))
    return ParsedCommand(
        action_type="offer_item",
        actor_id=suite.actor_id,
        target_id=case.target_id,
        parameters={
            "itemId": item.item_id,
            "offerPurpose": case.purpose,
            "requestedFavorRisk": case.requested_favor_risk,
        },
        original_text=case.player_text,
    )


def _evaluate_comparisons(
    comparisons: list[NpcDecisionEvaluationComparison],
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {str(result["caseId"]): result for result in results}
    output: list[dict[str, Any]] = []
    for comparison in comparisons:
        more = by_id[comparison.more_receptive_case_id]
        less = by_id[comparison.less_receptive_case_id]
        more_rank = RECEPTIVITY_RANK.get(str(more["decision"]), -1)
        less_rank = RECEPTIVITY_RANK.get(str(less["decision"]), -1)
        passed = (
            more_rank >= 0
            and less_rank >= 0
            and more_rank - less_rank >= comparison.minimum_rank_delta
        )
        output.append({
            "comparisonId": comparison.comparison_id,
            "passed": passed,
            "moreReceptiveCaseId": comparison.more_receptive_case_id,
            "lessReceptiveCaseId": comparison.less_receptive_case_id,
            "moreDecision": more["decision"],
            "lessDecision": less["decision"],
            "rankDelta": more_rank - less_rank,
            "requiredRankDelta": comparison.minimum_rank_delta,
        })
    return output


def _build_report(
    suite: NpcDecisionEvaluationSuite,
    results: list[dict[str, Any]],
    comparison_results: list[dict[str, Any]],
    partial: bool,
) -> dict[str, Any]:
    passed = sum(bool(result["passed"]) for result in results)
    model_accepted = sum(
        result["auditStatus"] == "model_accepted" for result in results
    )
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
    model_accept_rate = model_accepted / len(results)
    comparison_pass_rate = (
        sum(bool(value["passed"]) for value in comparison_results)
        / len(comparison_results)
        if comparison_results else 1.0
    )
    p95_latency = _percentile_nearest_rank(latencies, 0.95)
    average_tokens = round(sum(tokens) / len(tokens)) if tokens else None
    leak_count = sum(bool(result["leakedTerms"]) for result in results)
    unsafe_count = sum(bool(result["unsafe"]) for result in results)
    gates: dict[str, bool] = {
        "passRate": pass_rate >= suite.thresholds.minimum_pass_rate,
        "modelAcceptRate": (
            model_accept_rate >= suite.thresholds.minimum_model_accept_rate
        ),
        "categoryPassRate": (
            min(category_rates.values())
            >= suite.thresholds.minimum_category_pass_rate
        ),
        "comparisonPassRate": (
            comparison_pass_rate
            >= suite.thresholds.minimum_comparison_pass_rate
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
        "modelAcceptRate": round(model_accept_rate, 4),
        "categoryPassRates": category_rates,
        "comparisonPassRate": round(comparison_pass_rate, 4),
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
        "failedComparisonIds": [
            result["comparisonId"]
            for result in comparison_results
            if not result["passed"]
        ],
        "comparisons": comparison_results,
        "results": results,
    }


def _percentile_nearest_rank(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    index = max(0, ceil(percentile * len(values)) - 1)
    return values[index]
