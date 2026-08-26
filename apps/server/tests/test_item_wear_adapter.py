from __future__ import annotations

import json

import httpx
import pytest

from trpg_server.ai.platform.deepseek import DeepSeekAdapterError, DeepSeekSettings
from trpg_server.ai.platform.item_wear import (
    DeepSeekItemWearAdapter,
    ItemRepairCandidate,
    ItemRepairRequest,
    ItemWearCandidate,
    ItemWearError,
    ItemWearRequest,
    item_wear_adapter_from_environment,
    parse_repair_candidate,
    parse_wear_candidate,
    validate_repair_candidate_evidence,
    validate_wear_candidate_evidence,
)


def _wear_request() -> ItemWearRequest:
    return ItemWearRequest(
        item_id="knife-1",
        trigger="用小刀抵住木框并施力",
        item_summary={
            "itemId": "knife-1",
            "name": "小刀",
            "description": "钢制刀片，木柄，刀背较厚。",
            "category": "tool",
            "materials": ["钢", "木"],
            "secretAuthorityFact": "不要发送",
        },
        target_summary={
            "name": "木框",
            "description": "干燥的松木框，接缝处有细缝。",
            "hiddenRule": "不要发送",
        },
        context_summary={"locationName": "白鹭屋", "privateState": "不要发送"},
    )


def _repair_request() -> ItemRepairRequest:
    return ItemRepairRequest(
        item_id="coat-1",
        context="给磨破的外套缝补肘部",
        item_summary={
            "itemId": "coat-1",
            "name": "旧外套",
            "description": "粗呢布外套，右肘处磨薄。",
            "category": "clothing",
            "condition": "worn",
        },
        material_summaries=(
            {"name": "粗线", "description": "结实的棉线", "secret": "不发送"},
        ),
        tool_summaries=({"name": "针", "description": "细钢针"},),
        location_summary={"locationName": "裁缝铺", "description": "有缝纫台"},
    )


def _wear_output(**overrides: object) -> dict[str, object]:
    output: dict[str, object] = {
        "schemaVersion": 1,
        "itemId": "knife-1",
        "trigger": "用小刀抵住木框并施力",
        "wearBand": "light",
        "estimatedLossRatio": 0.01,
        "abilityId": "mechanical_repair",
        "difficultyBand": "routine",
        "physicalBasis": ["钢制刀片", "木框接缝处有细缝"],
        "confidence": 0.9,
    }
    output.update(overrides)
    return output


def _repair_output(**overrides: object) -> dict[str, object]:
    output: dict[str, object] = {
        "schemaVersion": 1,
        "itemId": "coat-1",
        "repairLevel": "standard",
        "materialKinds": ["粗线"],
        "abilityId": "tailoring",
        "difficultyBand": "routine",
        "physicalBasis": ["粗呢布外套右肘处磨薄", "结实棉线"],
        "confidence": 0.88,
    }
    output.update(overrides)
    return output


def _settings() -> DeepSeekSettings:
    return DeepSeekSettings(
        api_key="wear-test-secret",
        max_attempts=1,
        max_tokens=900,
    )


def _adapter(handler):
    return DeepSeekItemWearAdapter(
        _settings(), transport=httpx.MockTransport(handler)
    )


def test_wear_request_only_serializes_observable_summary_fields() -> None:
    payload = _wear_request().to_mapping()
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "secretAuthorityFact" not in encoded
    assert "hiddenRule" not in encoded
    assert "privateState" not in encoded
    assert payload["itemId"] == "knife-1"


def test_wear_adapter_returns_structured_output_and_metrics() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(_wear_output())},
                    }
                ],
                "usage": {"prompt_tokens": 31, "completion_tokens": 22, "total_tokens": 53},
            },
        )

    result = _adapter(handler).assess_wear(_wear_request())
    assert result.output["wearBand"] == "light"
    assert result.metrics.total_tokens == 53
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] <= 600
    assert body["temperature"] == 0.0
    encoded = json.dumps(body, ensure_ascii=False)
    assert "wear-test-secret" not in encoded
    assert "secretAuthorityFact" not in encoded
    assert "roll" in encoded


def test_repair_adapter_returns_structured_output() -> None:
    adapter = _adapter(
        lambda _: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(_repair_output())},
                    }
                ]
            },
        )
    )
    result = adapter.assess_repair(_repair_request())
    assert result.output["repairLevel"] == "standard"
    assert adapter.assess(_repair_request(), mode="repair").output["itemId"] == "coat-1"


@pytest.mark.parametrize(
    "factory",
    [
        lambda request: parse_wear_candidate(
            _wear_output(), request, allowed_ability_ids=("mechanical_repair",)
        ),
        lambda request: ItemWearCandidate.from_output(
            _wear_output(), request, allowed_ability_ids=("mechanical_repair",)
        ),
    ],
)
def test_wear_candidate_is_strict_and_matches_request(factory) -> None:
    candidate = factory(_wear_request())
    assert candidate.to_mapping()["estimatedLossRatio"] == 0.01
    with pytest.raises(ItemWearError, match="fields"):
        parse_wear_candidate({**_wear_output(), "roll": 20}, _wear_request())
    with pytest.raises(ItemWearError, match="itemId"):
        parse_wear_candidate(_wear_output(itemId="other"), _wear_request())
    with pytest.raises(ItemWearError, match="unknown ability"):
        parse_wear_candidate(
            _wear_output(abilityId="invented_skill"),
            _wear_request(),
            allowed_ability_ids=("mechanical_repair",),
        )


def test_repair_candidate_rejects_outcome_fields_and_invalid_values() -> None:
    candidate = parse_repair_candidate(
        _repair_output(), _repair_request(), allowed_ability_ids=("tailoring",)
    )
    assert isinstance(candidate, ItemRepairCandidate)
    assert candidate.material_kinds == ("粗线",)
    with pytest.raises(ItemWearError, match="forbidden"):
        parse_repair_candidate({**_repair_output(), "recovered": 10}, _repair_request())
    with pytest.raises(ItemWearError, match="repairLevel"):
        parse_repair_candidate(
            _repair_output(repairLevel="full"), _repair_request()
        )
    with pytest.raises(ItemWearError, match="confidence"):
        parse_repair_candidate(_repair_output(confidence=0.1), _repair_request())


def test_candidate_schema_rejects_non_finite_ratio() -> None:
    with pytest.raises(ItemWearError, match="finite"):
        parse_wear_candidate(_wear_output(estimatedLossRatio=float("nan")), _wear_request())


def test_candidate_evidence_must_be_grounded_in_observable_summaries() -> None:
    wear = parse_wear_candidate(
        _wear_output(), _wear_request(), allowed_ability_ids=("mechanical_repair",)
    )
    validate_wear_candidate_evidence(wear, _wear_request())
    with pytest.raises(ItemWearError, match="grounded"):
        validate_wear_candidate_evidence(
            ItemWearCandidate.from_output(
                _wear_output(physicalBasis=["钻石刀刃"]),
                _wear_request(),
                allowed_ability_ids=("mechanical_repair",),
            ),
            _wear_request(),
        )
    repair = parse_repair_candidate(
        _repair_output(), _repair_request(), allowed_ability_ids=("tailoring",)
    )
    validate_repair_candidate_evidence(repair, _repair_request())
    # Material categories may be abstractions (for example ``布料`` for a
    # concrete ``粗呢布`` summary); instance matching belongs to the resolver.
    validate_repair_candidate_evidence(
        ItemRepairCandidate.from_output(
            _repair_output(materialKinds=["布料"]),
            _repair_request(),
            allowed_ability_ids=("tailoring",),
        ),
        _repair_request(),
    )


@pytest.mark.parametrize(
    "response,expected",
    [
        (httpx.Response(401, json={"error": {}}), "HTTP 401"),
        (httpx.Response(200, text="not-json"), "non-JSON"),
        (
            httpx.Response(
                200,
                json={"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
            ),
            "truncated",
        ),
        (
            httpx.Response(
                200,
                json={"choices": [{"finish_reason": "stop", "message": {"content": "{bad"}}]},
            ),
            "invalid",
        ),
    ],
)
def test_adapter_rejects_http_and_malformed_responses(response, expected) -> None:
    adapter = _adapter(lambda _: response)
    with pytest.raises(DeepSeekAdapterError, match=expected):
        adapter.assess_wear(_wear_request())


def test_environment_factory_obeys_explicit_flag_and_provider() -> None:
    assert item_wear_adapter_from_environment({}) is None
    configured = item_wear_adapter_from_environment(
        {
            "TRPG_ITEM_WEAR_MODEL_ENABLED": "true",
            "TRPG_ITEM_WEAR_MODEL_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "factory-key",
            "DEEPSEEK_MAX_ATTEMPTS": "1",
        }
    )
    assert configured is not None
    assert configured.provider_name == "deepseek"
    with pytest.raises(ValueError, match="unsupported"):
        item_wear_adapter_from_environment(
            {
                "TRPG_ITEM_WEAR_MODEL_ENABLED": "true",
                "TRPG_ITEM_WEAR_MODEL_PROVIDER": "other",
                "DEEPSEEK_API_KEY": "factory-key",
            }
        )
