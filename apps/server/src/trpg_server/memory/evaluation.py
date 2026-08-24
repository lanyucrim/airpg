from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trpg_server.memory.memory import (
    EpisodicMemory,
    MemoryEntity,
    MemoryLink,
    MemoryQuery,
    MemoryScope,
    select_memories,
)


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EntityFixture(EvaluationModel):
    entity_id: str
    role: Literal[
        "actor",
        "target",
        "item",
        "subject",
        "object",
        "character",
        "from_location",
        "to_location",
    ]


class ScopeFixture(EvaluationModel):
    scope_kind: Literal["player", "npc"]
    scope_id: str


class MemoryFixture(EvaluationModel):
    memory_id: str
    source_event_id: str
    memory_type: Literal["interaction", "relationship", "state_change"]
    event_type: str
    summary: str
    importance: int = Field(ge=0, le=100)
    world_time: int = Field(ge=0)
    location_id: str | None = None
    update_key: str | None = None
    entities: list[EntityFixture] = Field(min_length=1)
    scopes: list[ScopeFixture] = Field(min_length=1)


class LinkFixture(EvaluationModel):
    source_memory_id: str
    target_memory_id: str
    relation_type: Literal["updates", "caused_by"]
    source_event_id: str


class QueryFixture(EvaluationModel):
    perspective_kind: Literal["player", "npc"]
    perspective_id: str
    information_need: Literal["historical", "current"] = "historical"
    entity_ids: list[str] = Field(default_factory=list)
    event_types: list[str] = Field(default_factory=list)
    time_mode: Literal["any", "earliest", "latest", "before", "after", "between"] = "any"
    time_start: int | None = None
    time_end: int | None = None
    limit: int = Field(default=12, ge=1, le=100)
    character_budget: int = Field(default=4_000, ge=1)
    search_text: str | None = None
    candidate_limit: int = Field(default=200, ge=1, le=1_000)
    semantic_rewrite: bool = False


class MemoryEvaluationCase(EvaluationModel):
    case_id: str
    ability: Literal[
        "factual_recall",
        "temporal_recall",
        "knowledge_update",
        "causal_link",
        "abstention",
        "npc_scope",
        "current_state_routing",
        "exact_entity_match",
    ]
    operation: Literal["retrieve", "link"]
    query: QueryFixture | None = None
    expected_selected_ids: list[str] = Field(default_factory=list)
    forbidden_selected_ids: list[str] = Field(default_factory=list)
    expected_route: Literal["episodic_memory", "current_state_required"] = "episodic_memory"
    expected_link: LinkFixture | None = None

    @model_validator(mode="after")
    def operation_has_expected_payload(self) -> MemoryEvaluationCase:
        if self.operation == "retrieve" and self.query is None:
            raise ValueError("retrieve case requires query")
        if self.operation == "link" and self.expected_link is None:
            raise ValueError("link case requires expected_link")
        return self


class MemoryEvaluationDataset(EvaluationModel):
    schema_version: Literal[1]
    dataset_id: str
    scenario_id: str
    campaign_id: str
    description: str
    memories: list[MemoryFixture]
    links: list[LinkFixture]
    cases: list[MemoryEvaluationCase]

    @model_validator(mode="after")
    def identifiers_are_consistent(self) -> MemoryEvaluationDataset:
        memory_ids = [memory.memory_id for memory in self.memories]
        case_ids = [case.case_id for case in self.cases]
        if len(memory_ids) != len(set(memory_ids)):
            raise ValueError("memory fixture ids must be unique")
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("evaluation case ids must be unique")
        known = set(memory_ids)
        for link in self.links:
            if link.source_memory_id not in known or link.target_memory_id not in known:
                raise ValueError("memory link references unknown memory")
        return self


@dataclass(frozen=True, slots=True)
class MemoryCaseResult:
    case_id: str
    ability: str
    passed: bool
    selected_ids: tuple[str, ...]
    route: str
    failure_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MemoryEvaluationReport:
    dataset_id: str
    total_cases: int
    passed_cases: int
    exact_match_rate: float
    scope_leak_count: int
    abstention_failure_count: int
    current_route_failure_count: int
    candidate_recall_by_mode: dict[str, float]
    semantic_hybrid_recall_rate: float
    results: tuple[MemoryCaseResult, ...]


def load_memory_evaluation_dataset(path: Path) -> MemoryEvaluationDataset:
    return MemoryEvaluationDataset.model_validate_json(path.read_text(encoding="utf-8"))


def evaluate_memory_dataset(dataset: MemoryEvaluationDataset) -> MemoryEvaluationReport:
    memories = tuple(_memory(dataset.campaign_id, value) for value in dataset.memories)
    links = {_link_key(value) for value in dataset.links}
    results: list[MemoryCaseResult] = []
    for case in dataset.cases:
        failures: list[str] = []
        selected_ids: tuple[str, ...] = ()
        route = "episodic_memory"
        if case.operation == "link":
            expected = case.expected_link
            assert expected is not None
            if _link_key(expected) not in links:
                failures.append("missing_link")
        else:
            query_fixture = case.query
            assert query_fixture is not None
            query = MemoryQuery(
                campaign_id=dataset.campaign_id,
                purpose="debug",
                perspective_kind=query_fixture.perspective_kind,
                perspective_id=query_fixture.perspective_id,
                information_need=query_fixture.information_need,
                entity_ids=tuple(query_fixture.entity_ids),
                event_types=tuple(query_fixture.event_types),
                time_mode=query_fixture.time_mode,
                time_start=query_fixture.time_start,
                time_end=query_fixture.time_end,
                limit=query_fixture.limit,
                character_budget=query_fixture.character_budget,
                search_text=query_fixture.search_text,
            )
            selection = select_memories(memories, query)
            selected_ids = tuple(memory.memory_id for memory in selection.selected)
            route = selection.route
            if selected_ids != tuple(case.expected_selected_ids):
                failures.append("selected_ids_mismatch")
            if set(selected_ids) & set(case.forbidden_selected_ids):
                failures.append("forbidden_memory_selected")
            if route != case.expected_route:
                failures.append("route_mismatch")
        results.append(MemoryCaseResult(
            case.case_id,
            case.ability,
            not failures,
            selected_ids,
            route,
            tuple(failures),
        ))

    passed = sum(result.passed for result in results)
    recall_by_mode, semantic_hybrid_recall = _candidate_recall_metrics(
        dataset, memories
    )
    return MemoryEvaluationReport(
        dataset_id=dataset.dataset_id,
        total_cases=len(results),
        passed_cases=passed,
        exact_match_rate=passed / len(results) if results else 0.0,
        scope_leak_count=sum(
            "forbidden_memory_selected" in result.failure_codes
            for result in results
            if result.ability == "npc_scope"
        ),
        abstention_failure_count=sum(
            not result.passed for result in results if result.ability == "abstention"
        ),
        current_route_failure_count=sum(
            not result.passed
            for result in results
            if result.ability == "current_state_routing"
        ),
        candidate_recall_by_mode=recall_by_mode,
        semantic_hybrid_recall_rate=semantic_hybrid_recall,
        results=tuple(results),
    )


def report_as_dict(report: MemoryEvaluationReport) -> dict[str, object]:
    return {
        "dataset_id": report.dataset_id,
        "total_cases": report.total_cases,
        "passed_cases": report.passed_cases,
        "exact_match_rate": report.exact_match_rate,
        "scope_leak_count": report.scope_leak_count,
        "abstention_failure_count": report.abstention_failure_count,
        "current_route_failure_count": report.current_route_failure_count,
        "candidate_recall_by_mode": report.candidate_recall_by_mode,
        "semantic_hybrid_recall_rate": report.semantic_hybrid_recall_rate,
        "results": [
            {
                "case_id": result.case_id,
                "ability": result.ability,
                "passed": result.passed,
                "selected_ids": list(result.selected_ids),
                "route": result.route,
                "failure_codes": list(result.failure_codes),
            }
            for result in report.results
        ],
    }


def report_as_json(report: MemoryEvaluationReport) -> str:
    return json.dumps(report_as_dict(report), ensure_ascii=False, indent=2)


def _memory(campaign_id: str, fixture: MemoryFixture) -> EpisodicMemory:
    return EpisodicMemory(
        memory_id=fixture.memory_id,
        campaign_id=campaign_id,
        source_event_id=fixture.source_event_id,
        schema_version=2,
        memory_type=fixture.memory_type,
        event_type=fixture.event_type,
        summary=fixture.summary,
        importance=fixture.importance,
        world_time=fixture.world_time,
        location_id=fixture.location_id,
        status="active",
        update_key=fixture.update_key,
        entities=tuple(
            MemoryEntity(value.entity_id, value.role) for value in fixture.entities
        ),
        scopes=tuple(
            MemoryScope(value.scope_kind, value.scope_id) for value in fixture.scopes
        ),
    )


def _link_key(link: LinkFixture | MemoryLink) -> tuple[str, str, str, str]:
    return (
        link.source_memory_id,
        link.target_memory_id,
        link.relation_type,
        link.source_event_id,
    )


def _candidate_recall_metrics(
    dataset: MemoryEvaluationDataset,
    memories: tuple[EpisodicMemory, ...],
) -> tuple[dict[str, float], float]:
    cases = [
        case for case in dataset.cases
        if case.operation == "retrieve"
        and case.query is not None
        and case.query.search_text
        and case.expected_selected_ids
    ]
    hits = {"structured": 0, "fts": 0, "hybrid": 0}
    totals = {"structured": 0, "fts": 0, "hybrid": 0}
    semantic_hits = 0
    semantic_total = 0
    for case in cases:
        query = case.query
        assert query is not None and query.search_text is not None
        required_entities = set(query.entity_ids)
        structured = sorted(
            (
                memory for memory in memories
                if required_entities <= {entity.entity_id for entity in memory.entities}
            ),
            key=lambda memory: (-memory.world_time, -memory.importance, memory.memory_id),
        )[:query.candidate_limit]
        terms = _ngrams(query.search_text)
        fts = [
            memory for memory in structured
            if terms and terms <= _ngrams(memory.summary)
        ][:query.candidate_limit]
        hybrid = list(dict.fromkeys([*fts, *structured]))[:query.candidate_limit]
        expected = set(case.expected_selected_ids)
        for mode, candidates in {
            "structured": structured,
            "fts": fts,
            "hybrid": hybrid,
        }.items():
            hits[mode] += len(expected & {memory.memory_id for memory in candidates})
            totals[mode] += len(expected)
        if query.semantic_rewrite:
            semantic_hits += len(
                expected & {memory.memory_id for memory in hybrid}
            )
            semantic_total += len(expected)
    return (
        {
            mode: (hits[mode] / totals[mode] if totals[mode] else 0.0)
            for mode in ("structured", "fts", "hybrid")
        },
        semantic_hits / semantic_total if semantic_total else 0.0,
    )


def _ngrams(text: str) -> set[str]:
    normalized = "".join(text.strip().split())
    if not normalized:
        return set()
    if len(normalized) == 1:
        return {normalized}
    return {normalized[index:index + 2] for index in range(len(normalized) - 1)}
