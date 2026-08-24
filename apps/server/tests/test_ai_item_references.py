from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path

import httpx
import pytest

from trpg_server.ai.platform.deepseek import DeepSeekAdapterError, DeepSeekSettings
from trpg_server.items.ai_items.deepseek_adapter import (
    DeepSeekItemReferenceAdapter,
)
from trpg_server.items.ai_items.references import (
    DailyItemReferenceRequest,
    DailyItemReferenceTable,
    ItemReferenceAdapterResult,
    ReferenceCallMetrics,
    crown_value_from_usd,
    render_daily_item_reference_markdown,
    resolve_daily_item_reference,
    with_reference_measurements,
)
from trpg_server.items.contract import ITEM_RECORD_FIELD_SET


REFERENCE_PATH = (
    Path(__file__).resolve().parents[3]
    / "content"
    / "campaigns"
    / "gray-harbor"
    / "items-atlas"
    / "ai-items"
    / "daily-item-references.json"
)


class FakeAdapter:
    available = True
    provider_name = "fake"
    model_name = "fake-item-model"

    def __init__(self, output: dict[str, object] | Exception) -> None:
        self.output = output
        self.calls = 0

    def estimate(
        self,
        request: DailyItemReferenceRequest,
    ) -> ItemReferenceAdapterResult:
        del request
        self.calls += 1
        if isinstance(self.output, Exception):
            raise self.output
        return ItemReferenceAdapterResult(
            output=self.output,
            metrics=ReferenceCallMetrics(total_tokens=41, latency_ms=12),
        )


def _orange_request() -> DailyItemReferenceRequest:
    return DailyItemReferenceRequest(
        item_key="orange_medium_each",
        name="橙子",
        aliases=("orange", "中等橙子"),
        unit_description="一个中等大小、完整可购买的橙子",
    )


def _orange_candidate(**overrides: object) -> dict[str, object]:
    candidate: dict[str, object] = {
        "schemaVersion": 1,
        "itemKey": "orange_medium_each",
        "name": "橙子",
        "unitDescription": "一个中等大小、完整可购买的橙子",
        "estimatedRetailUsd": 0.6,
        "unitWeightGrams": 140,
        "confidence": 0.82,
        "assumptions": ["按普通零售单个水果估算"],
    }
    candidate.update(overrides)
    return candidate


def test_reference_table_uses_apple_as_exact_price_benchmark_and_caches_weight() -> None:
    table = DailyItemReferenceTable.load(REFERENCE_PATH)
    apple = table.lookup("苹果")
    banana = table.lookup("banana")

    assert apple is not None
    assert apple.value_crown == 10
    assert apple.price_ratio_to_apple == Decimal("1")
    assert apple.unit_weight_grams == 180
    assert banana is not None
    assert banana.value_crown == 4
    assert banana.unit_weight_grams == 150
    assert crown_value_from_usd("0.30", "0.80") == 4


def test_cache_hit_by_alias_never_calls_adapter() -> None:
    table = DailyItemReferenceTable.load(REFERENCE_PATH)
    adapter = FakeAdapter(RuntimeError("must not run"))
    request = DailyItemReferenceRequest(
        item_key="banana_medium_each",
        name="香蕉",
        aliases=("banana",),
        unit_description="一根中等大小、带皮的完整香蕉",
    )

    resolution = resolve_daily_item_reference(table, request, adapter)

    assert resolution.status == "cache_hit"
    assert resolution.reference is not None
    assert resolution.reference.unit_weight_grams == 150
    assert resolution.adapter_called is False
    assert adapter.calls == 0


def test_cache_miss_calls_adapter_once_and_program_derives_crown_value() -> None:
    table = DailyItemReferenceTable.load(REFERENCE_PATH)
    adapter = FakeAdapter(_orange_candidate())

    resolution = resolve_daily_item_reference(table, _orange_request(), adapter)

    assert resolution.status == "model_accepted"
    assert resolution.adapter_called is True
    assert adapter.calls == 1
    assert resolution.reference is not None
    assert resolution.reference.value_crown == 8
    assert resolution.reference.unit_weight_grams == 140
    assert resolution.reference.aliases == ("orange", "中等橙子")
    assert resolution.reference.model_audit is not None
    assert resolution.reference.model_audit.total_tokens == 41
    assert table.lookup("orange") == resolution.reference

    second = resolve_daily_item_reference(table, _orange_request(), adapter)
    assert second.status == "cache_hit"
    assert adapter.calls == 1


@pytest.mark.parametrize(
    "candidate",
    [
        _orange_candidate(unitWeightGrams=0),
        _orange_candidate(confidence=0.2),
        _orange_candidate(unitDescription="一公斤橙子"),
        _orange_candidate(valueCrown=8),
    ],
)
def test_invalid_model_candidate_is_not_cached(candidate: dict[str, object]) -> None:
    table = DailyItemReferenceTable.load(REFERENCE_PATH)
    count = len(table.references)
    adapter = FakeAdapter(candidate)

    resolution = resolve_daily_item_reference(table, _orange_request(), adapter)

    assert resolution.status == "rejected"
    assert resolution.reference is None
    assert adapter.calls == 1
    assert len(table.references) == count
    assert table.lookup("orange") is None


def test_adapter_failure_is_not_cached() -> None:
    table = DailyItemReferenceTable.load(REFERENCE_PATH)
    adapter = FakeAdapter(TimeoutError("late"))

    resolution = resolve_daily_item_reference(table, _orange_request(), adapter)

    assert resolution.status == "rejected"
    assert resolution.reference is None
    assert adapter.calls == 1
    assert table.lookup("orange") is None


def test_reference_fills_only_unknown_15_field_measurements() -> None:
    table = DailyItemReferenceTable.load(REFERENCE_PATH)
    banana = table.lookup("香蕉")
    assert banana is not None
    record = {
        "id": "ordinary_banana",
        "definitionId": "ordinary_banana",
        "name": "香蕉",
        "description": "一根普通香蕉。",
        "category": "food",
        "isPlotItem": False,
        "quantity": 1,
        "stackable": True,
        "unitWeightGrams": None,
        "valueCrown": 7,
        "condition": None,
        "durability": None,
        "containerId": None,
        "locationId": None,
        "properties": {},
    }

    filled = with_reference_measurements(record, banana)

    assert set(filled) == ITEM_RECORD_FIELD_SET
    assert filled["unitWeightGrams"] == 150
    assert filled["valueCrown"] == 7
    assert record["unitWeightGrams"] is None


def test_table_rejects_a_crown_value_not_derived_from_apple() -> None:
    document = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    document["references"][1]["valueCrown"] = 5

    with pytest.raises(ValueError, match="program-derived"):
        DailyItemReferenceTable.from_document(document)


def test_markdown_review_table_is_generated_from_json() -> None:
    table = DailyItemReferenceTable.load(REFERENCE_PATH)
    expected = REFERENCE_PATH.with_suffix(".md").read_text(encoding="utf-8")

    assert render_daily_item_reference_markdown(table).rstrip() == expected.rstrip()


def test_deepseek_adapter_requests_price_and_weight_in_one_json_response() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(_orange_candidate())},
                    }
                ],
                "usage": {
                    "prompt_tokens": 80,
                    "completion_tokens": 30,
                    "total_tokens": 110,
                },
            },
        )

    adapter = DeepSeekItemReferenceAdapter(
        DeepSeekSettings(api_key="item-test-key", max_attempts=1),
        transport=httpx.MockTransport(handler),
    )
    result = adapter.estimate(_orange_request())

    assert result.output["estimatedRetailUsd"] == 0.6
    assert result.output["unitWeightGrams"] == 140
    assert result.metrics.total_tokens == 110
    assert captured["authorization"] == "Bearer item-test-key"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0.2
    assert "item-test-key" not in json.dumps(body)


def test_deepseek_adapter_rejects_invalid_json() -> None:
    adapter = DeepSeekItemReferenceAdapter(
        DeepSeekSettings(api_key="item-test-key", max_attempts=1),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": "not json"},
                        }
                    ]
                },
            )
        ),
    )

    with pytest.raises(DeepSeekAdapterError, match="invalid item reference JSON"):
        adapter.estimate(_orange_request())
