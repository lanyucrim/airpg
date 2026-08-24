from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from trpg_server.memory.evaluation import (
    MemoryEvaluationDataset,
    evaluate_memory_dataset,
    load_memory_evaluation_dataset,
    report_as_dict,
)


DATASET = (
    Path(__file__).resolve().parents[3]
    / "evals"
    / "long-term-memory"
    / "gray-harbor-v1.json"
)


def test_gray_harbor_memory_baseline_passes_all_capabilities() -> None:
    dataset = load_memory_evaluation_dataset(DATASET)
    report = evaluate_memory_dataset(dataset)

    assert dataset.schema_version == 1
    assert dataset.scenario_id == "gray-harbor-black-tide-throne"
    assert report.total_cases == 51
    assert report.passed_cases == 51
    assert report.exact_match_rate == 1.0
    assert report.scope_leak_count == 0
    assert report.abstention_failure_count == 0
    assert report.current_route_failure_count == 0
    assert report.candidate_recall_by_mode["structured"] == 1.0
    assert report.candidate_recall_by_mode["hybrid"] == 1.0
    assert report.candidate_recall_by_mode["fts"] < 1.0
    assert report.semantic_hybrid_recall_rate >= 0.9
    assert {result.ability for result in report.results} == {
        "factual_recall",
        "temporal_recall",
        "knowledge_update",
        "causal_link",
        "abstention",
        "npc_scope",
        "current_state_routing",
        "exact_entity_match",
    }


def test_memory_evaluation_report_is_json_serializable() -> None:
    report = evaluate_memory_dataset(load_memory_evaluation_dataset(DATASET))
    payload = report_as_dict(report)

    assert json.loads(json.dumps(payload, ensure_ascii=False))["passed_cases"] == 51


def test_dataset_rejects_duplicate_ids_and_unknown_links() -> None:
    raw = json.loads(DATASET.read_text(encoding="utf-8"))
    raw["memories"].append(dict(raw["memories"][0]))
    with pytest.raises(ValidationError, match="memory fixture ids must be unique"):
        MemoryEvaluationDataset.model_validate(raw)

    raw = json.loads(DATASET.read_text(encoding="utf-8"))
    raw["links"][0]["target_memory_id"] = "mem_unknown"
    with pytest.raises(ValidationError, match="unknown memory"):
        MemoryEvaluationDataset.model_validate(raw)


def test_dataset_rejects_operation_without_required_payload() -> None:
    raw = json.loads(DATASET.read_text(encoding="utf-8"))
    raw["cases"][0]["query"] = None
    with pytest.raises(ValidationError, match="retrieve case requires query"):
        MemoryEvaluationDataset.model_validate(raw)
