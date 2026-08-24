from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from trpg_server.ai.platform.deepseek import DeepSeekAdapterError, DeepSeekSettings
from trpg_server.items.ai_items.deepseek_adapter import (
    DeepSeekDailyItemGenerationAdapter,
)
from trpg_server.items.ai_items.generation import (
    GENERATION_FIELD_POLICIES,
    DailyItemDefinitionCatalog,
    DailyItemGenerationAdapterResult,
    DailyItemGenerationRequest,
    render_daily_item_definition_markdown,
    resolve_daily_item_definition,
)
from trpg_server.items.ai_items.references import (
    DailyItemReferenceTable,
    ReferenceCallMetrics,
)
from trpg_server.items.contract import ITEM_RECORD_FIELDS, ITEM_RECORD_FIELD_SET
from trpg_server.items.catalog import load_item_atlas
from trpg_server.items.models import ItemDefinition


AI_ITEM_PATH = (
    Path(__file__).resolve().parents[3]
    / "content"
    / "campaigns"
    / "gray-harbor"
    / "items-atlas"
    / "ai-items"
)
CATALOG_PATH = AI_ITEM_PATH / "daily-item-definitions.json"
REFERENCE_PATH = AI_ITEM_PATH / "daily-item-references.json"
KNOWN_ATLAS_PATH = AI_ITEM_PATH.parent / "important-items.json"


class FakeGenerationAdapter:
    available = True
    provider_name = "fake"
    model_name = "fake-daily-item-model"

    def __init__(self, output: dict[str, object] | Exception) -> None:
        self.output = output
        self.calls = 0

    def generate(
        self,
        request: DailyItemGenerationRequest,
    ) -> DailyItemGenerationAdapterResult:
        del request
        self.calls += 1
        if isinstance(self.output, Exception):
            raise self.output
        return DailyItemGenerationAdapterResult(
            output=self.output,
            metrics=ReferenceCallMetrics(
                prompt_tokens=90,
                completion_tokens=50,
                total_tokens=140,
                latency_ms=18,
            ),
        )


def _catalog() -> DailyItemDefinitionCatalog:
    return DailyItemDefinitionCatalog.load(CATALOG_PATH)


def _references() -> DailyItemReferenceTable:
    return DailyItemReferenceTable.load(REFERENCE_PATH)


def _bread_candidate(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schemaVersion": 1,
        "isDailyItem": True,
        "itemKey": "bread_piece_each",
        "canonicalName": "面包",
        "aliases": ["烤面包"],
        "description": "一块供单人食用的普通烘烤面包。",
        "category": "food",
        "unitDescription": "一块单人份面包",
        "stackable": True,
        "estimatedRetailUsd": 1.2,
        "unitWeightGrams": 120,
        "equipment": None,
        "consumable": {
            "schemaVersion": 1,
            "quantityPerUse": 1,
            "method": "eat",
            "targetKinds": ["character"],
            "riskClass": "low",
            "effectCandidates": [
                {
                    "domain": "characters",
                    "effectKind": "nourishment",
                    "summary": "作为普通食物缓解饥饿",
                    "magnitude": "minor",
                    "durationMinutes": None,
                    "requiresDomainResolution": True,
                }
            ],
        },
        "confidence": 0.85,
        "assumptions": ["按普通零售单块面包估算"],
    }
    value.update(overrides)
    return value


def test_empty_generated_catalog_uses_current_item_contract() -> None:
    catalog = _catalog()
    document = catalog.to_document()

    assert catalog.definitions == ()
    assert document["itemContract"]["fields"] == list(ITEM_RECORD_FIELDS)
    assert document["itemContract"]["schemaVersion"] == 7
    assert document["itemContract"]["fingerprint"].startswith("sha256:")
    assert document["itemContract"]["generationPolicyFingerprint"].startswith(
        "sha256:"
    )


def test_generation_policy_explicitly_covers_every_official_item_field() -> None:
    assert tuple(field for field, _ in GENERATION_FIELD_POLICIES) == ITEM_RECORD_FIELDS


def test_one_model_call_generates_definition_price_weight_and_reference() -> None:
    catalog = _catalog()
    references = _references()
    adapter = FakeGenerationAdapter(_bread_candidate())

    resolution = resolve_daily_item_definition(
        catalog,
        references,
        DailyItemGenerationRequest("香喷喷的面包"),
        adapter,
    )

    assert resolution.status == "model_accepted"
    assert resolution.adapter_called is True
    assert adapter.calls == 1
    assert resolution.entry is not None
    entry = resolution.entry
    item = entry.item
    assert set(item) == ITEM_RECORD_FIELD_SET
    assert item["id"] == "daily_food_bread_piece_each"
    assert item["definitionId"] == item["id"]
    assert item["name"] == "面包"
    assert item["isPlotItem"] is False
    assert item["quantity"] == 1
    assert item["unitWeightGrams"] == 120
    assert item["valueCrown"] == 15
    assert item["condition"] is None
    assert item["containerId"] is None
    assert item["locationId"] is None
    assert item["properties"]["consumable"]["method"] == "eat"
    assert item["properties"]["consumable"]["effectCandidates"][0][
        "requiresDomainResolution"
    ] is True
    ItemDefinition.from_payload(item)
    assert "香喷喷的面包" in entry.aliases
    assert len(catalog.definitions) == 0
    assert len(references.references) == 2
    assert len(resolution.catalog.definitions) == 1
    cached_reference = resolution.reference_table.lookup("bread_piece_each")
    assert cached_reference is not None
    assert cached_reference.value_crown == 15
    assert cached_reference.unit_weight_grams == 120
    assert cached_reference.model_audit is not None
    assert cached_reference.model_audit.total_tokens == 140


def test_generated_phrase_cache_hit_never_calls_model_again() -> None:
    first_adapter = FakeGenerationAdapter(_bread_candidate())
    first = resolve_daily_item_definition(
        _catalog(),
        _references(),
        DailyItemGenerationRequest("香喷喷的面包"),
        first_adapter,
    )
    second_adapter = FakeGenerationAdapter(RuntimeError("must not run"))

    second = resolve_daily_item_definition(
        first.catalog,
        first.reference_table,
        DailyItemGenerationRequest("香喷喷的面包"),
        second_adapter,
    )

    assert second.status == "cache_hit"
    assert second.adapter_called is False
    assert second_adapter.calls == 0
    assert second.entry == first.entry


def test_exact_known_atlas_definition_is_reused_without_model_call() -> None:
    adapter = FakeGenerationAdapter(RuntimeError("must not run"))
    known = load_item_atlas(KNOWN_ATLAS_PATH).definitions

    resolution = resolve_daily_item_definition(
        _catalog(),
        _references(),
        DailyItemGenerationRequest("半条黑麦面包"),
        adapter,
        known_definitions=known,
    )

    assert resolution.status == "known_definition"
    assert resolution.adapter_called is False
    assert adapter.calls == 0
    assert resolution.entry is None
    assert resolution.definition is not None
    assert resolution.definition["id"] == "kitchen_bread_loaf"
    assert resolution.catalog.definitions == ()


def test_model_normalization_reuses_equivalent_known_atlas_definition() -> None:
    known = load_item_atlas(KNOWN_ATLAS_PATH).definitions
    candidate = _bread_candidate(
        itemKey="rye_bread_half_loaf",
        canonicalName="半条黑麦面包",
        aliases=[],
        description="半条供多人分食的普通黑麦面包。",
        unitDescription="半条黑麦面包",
        unitWeightGrams=225,
    )
    adapter = FakeGenerationAdapter(candidate)

    resolution = resolve_daily_item_definition(
        _catalog(),
        _references(),
        DailyItemGenerationRequest("那半条香喷喷的黑麦面包"),
        adapter,
        known_definitions=known,
    )

    assert resolution.status == "known_definition_reused"
    assert resolution.adapter_called is True
    assert adapter.calls == 1
    assert resolution.definition is not None
    assert resolution.definition["id"] == "kitchen_bread_loaf"
    assert resolution.catalog.definitions == ()
    assert len(resolution.reference_table.references) == 2
    assert (
        resolution.catalog.known_definition_id("那半条香喷喷的黑麦面包")
        == "kitchen_bread_loaf"
    )

    no_call_adapter = FakeGenerationAdapter(RuntimeError("must not run"))
    cached = resolve_daily_item_definition(
        resolution.catalog,
        resolution.reference_table,
        DailyItemGenerationRequest("那半条香喷喷的黑麦面包"),
        no_call_adapter,
        known_definitions=known,
    )
    assert cached.status == "known_alias_hit"
    assert cached.adapter_called is False
    assert no_call_adapter.calls == 0
    assert cached.definition is not None
    assert cached.definition["id"] == "kitchen_bread_loaf"


def test_new_wording_with_same_canonical_key_reuses_definition() -> None:
    first = resolve_daily_item_definition(
        _catalog(),
        _references(),
        DailyItemGenerationRequest("香喷喷的面包"),
        FakeGenerationAdapter(_bread_candidate()),
    )
    adapter = FakeGenerationAdapter(
        _bread_candidate(aliases=["普通烤面包"])
    )

    second = resolve_daily_item_definition(
        first.catalog,
        first.reference_table,
        DailyItemGenerationRequest("刚烤好的普通面包"),
        adapter,
    )

    assert second.status == "equivalent_reused"
    assert adapter.calls == 1
    assert len(second.catalog.definitions) == 1
    assert second.entry is not None
    assert second.entry.definition_id == "daily_food_bread_piece_each"
    assert "刚烤好的普通面包" in second.entry.aliases
    assert second.catalog.lookup("刚烤好的普通面包") == second.entry
    assert len(second.reference_table.references) == 3


def test_existing_price_weight_reference_overrides_new_model_estimate() -> None:
    adapter = FakeGenerationAdapter(
        {
            "schemaVersion": 1,
            "isDailyItem": True,
            "itemKey": "banana_medium_each",
            "canonicalName": "香蕉",
            "aliases": ["中等香蕉"],
            "description": "一根中等大小的普通香蕉。",
            "category": "food",
            "unitDescription": "一根中等大小、带皮的完整香蕉",
            "stackable": True,
            "estimatedRetailUsd": 99,
            "unitWeightGrams": 999,
            "equipment": None,
            "consumable": {
                "schemaVersion": 1,
                "quantityPerUse": 1,
                "method": "eat",
                "targetKinds": ["character"],
                "riskClass": "low",
                "effectCandidates": [
                    {
                        "domain": "characters",
                        "effectKind": "nourishment",
                        "summary": "作为普通食物缓解饥饿",
                        "magnitude": "minor",
                        "durationMinutes": None,
                        "requiresDomainResolution": True,
                    }
                ],
            },
            "confidence": 0.9,
            "assumptions": ["定义属性候选"],
        }
    )

    resolution = resolve_daily_item_definition(
        _catalog(),
        _references(),
        DailyItemGenerationRequest("一根香蕉"),
        adapter,
    )

    assert resolution.status == "model_accepted"
    assert resolution.entry is not None
    assert resolution.entry.item["valueCrown"] == 4
    assert resolution.entry.item["unitWeightGrams"] == 150
    assert len(resolution.reference_table.references) == 2


def test_generated_cache_rejects_price_weight_reference_drift() -> None:
    generated = resolve_daily_item_definition(
        _catalog(),
        _references(),
        DailyItemGenerationRequest("香喷喷的面包"),
        FakeGenerationAdapter(_bread_candidate()),
    )
    reference_document = generated.reference_table.to_document()
    bread_reference = next(
        value
        for value in reference_document["references"]
        if value["itemKey"] == "bread_piece_each"
    )
    bread_reference["unitWeightGrams"] = 121
    drifted = DailyItemReferenceTable.from_document(reference_document)

    resolution = resolve_daily_item_definition(
        generated.catalog,
        drifted,
        DailyItemGenerationRequest("香喷喷的面包"),
    )

    assert resolution.status == "rejected"
    assert resolution.adapter_called is False
    assert resolution.reason is not None
    assert "weight differs" in resolution.reason


@pytest.mark.parametrize(
    "candidate",
    [
        _bread_candidate(isDailyItem=False),
        _bread_candidate(category="currency"),
        _bread_candidate(confidence=0.2),
        _bread_candidate(unitWeightGrams=0),
        _bread_candidate(equipment={}),
    ],
)
def test_rejected_candidate_does_not_modify_catalogs(
    candidate: dict[str, object],
) -> None:
    catalog = _catalog()
    references = _references()
    adapter = FakeGenerationAdapter(candidate)

    resolution = resolve_daily_item_definition(
        catalog,
        references,
        DailyItemGenerationRequest("香喷喷的面包"),
        adapter,
    )

    assert resolution.status == "rejected"
    assert resolution.entry is None
    assert adapter.calls == 1
    assert resolution.catalog is catalog
    assert resolution.reference_table is references
    assert catalog.definitions == ()
    assert len(references.references) == 2


def test_adapter_failure_does_not_modify_catalogs() -> None:
    catalog = _catalog()
    references = _references()

    resolution = resolve_daily_item_definition(
        catalog,
        references,
        DailyItemGenerationRequest("香喷喷的面包"),
        FakeGenerationAdapter(TimeoutError("late")),
    )

    assert resolution.status == "rejected"
    assert resolution.entry is None
    assert catalog.definitions == ()
    assert len(references.references) == 2


def test_stale_item_contract_snapshot_is_rejected() -> None:
    document = _catalog().to_document()
    document["itemContract"]["fields"].append("newField")

    with pytest.raises(ValueError, match="stale item contract"):
        DailyItemDefinitionCatalog.from_document(document)


def test_generated_catalog_markdown_is_derived_from_json() -> None:
    catalog = _catalog()
    expected = CATALOG_PATH.with_suffix(".md").read_text(encoding="utf-8")

    assert render_daily_item_definition_markdown(catalog).rstrip() == expected.rstrip()


def test_deepseek_generation_adapter_requests_all_fields_in_one_call() -> None:
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
                        "message": {"content": json.dumps(_bread_candidate())},
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 60,
                    "total_tokens": 160,
                },
            },
        )

    adapter = DeepSeekDailyItemGenerationAdapter(
        DeepSeekSettings(api_key="daily-item-test-key", max_attempts=1),
        transport=httpx.MockTransport(handler),
    )
    result = adapter.generate(DailyItemGenerationRequest("香喷喷的面包"))

    assert result.output["canonicalName"] == "面包"
    assert result.output["estimatedRetailUsd"] == 1.2
    assert result.output["unitWeightGrams"] == 120
    assert result.metrics.total_tokens == 160
    assert captured["authorization"] == "Bearer daily-item-test-key"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0.2
    assert body["max_tokens"] <= 700
    assert "daily-item-test-key" not in json.dumps(body)


def test_deepseek_generation_adapter_rejects_invalid_json() -> None:
    adapter = DeepSeekDailyItemGenerationAdapter(
        DeepSeekSettings(api_key="daily-item-test-key", max_attempts=1),
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

    with pytest.raises(DeepSeekAdapterError, match="invalid daily item generation JSON"):
        adapter.generate(DailyItemGenerationRequest("香喷喷的面包"))
