from __future__ import annotations

import json

import httpx
import pytest

from trpg_server.ai.platform.deepseek import DeepSeekSettings
from trpg_server.ai.platform.item_interaction import DeepSeekItemInteractionAdapter
from trpg_server.items.interaction import (
    InteractionRequest,
    ItemInteractionError,
    ItemInteractionCandidate,
    validate_candidate_evidence,
)


def request() -> InteractionRequest:
    return InteractionRequest(
        actor_id="player",
        source_item_ids=("knife_1",),
        target_kind="location",
        target_id="room_1",
        operation="apply",
        action_text="用小刀检查锁扣",
    )


def response(output: dict[str, object]) -> dict[str, object]:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": json.dumps(output, ensure_ascii=False)},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def candidate() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "decision": "possible",
        "operation": "apply",
        "requiredAbilityIds": [],
        "toolFit": "strong",
        "difficultyBand": "routine",
        "physicalBasis": ["细长金属结构可接触锁扣"],
        "missingFacts": [],
        "riskHints": [],
        "confidence": 0.9,
        "effectKind": "inspect",
        "rejectionReason": None,
    }


def test_adapter_returns_structured_output_and_does_not_put_key_in_body() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=response(candidate()))

    adapter = DeepSeekItemInteractionAdapter(
        DeepSeekSettings(api_key="secret-key", max_attempts=1),
        transport=httpx.MockTransport(handler),
    )
    result = adapter.assess(request(), ({"itemId": "knife_1"},), {"locationId": "room_1"})

    assert result.output["decision"] == "possible"
    assert result.total_tokens == 30
    assert captured["authorization"] == "Bearer secret-key"
    assert "secret-key" not in json.dumps(captured["body"])
    assert captured["body"]["temperature"] == 0
    assert captured["body"]["response_format"] == {"type": "json_object"}


def test_adapter_rejects_invalid_json_and_http_errors() -> None:
    bad_json = DeepSeekItemInteractionAdapter(
        DeepSeekSettings(api_key="key", max_attempts=1),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="not-json")),
    )
    with pytest.raises(RuntimeError):
        bad_json.assess(request(), (), {})

    http_error = DeepSeekItemInteractionAdapter(
        DeepSeekSettings(api_key="key", max_attempts=1),
        transport=httpx.MockTransport(lambda _: httpx.Response(401, json={"error": {}})),
    )
    with pytest.raises(RuntimeError):
        http_error.assess(request(), (), {})


def test_evidence_gate_requires_concrete_observable_fact() -> None:
    candidate_value = ItemInteractionCandidate.from_output(
        {
            **candidate(),
            "physicalBasis": ["工具可以完成操作"],
        },
        request(),
    )
    with pytest.raises(ItemInteractionError, match="grounded"):
        validate_candidate_evidence(
            candidate_value,
            ({"category": "tool", "description": "一把刀。"},),
            {"targetKind": "location", "description": "一个房间。"},
        )


def test_evidence_gate_accepts_observable_description_fact() -> None:
    candidate_value = ItemInteractionCandidate.from_output(
        candidate(),
        request(),
    )
    validate_candidate_evidence(
        candidate_value,
        ({"category": "tool", "description": "细长金属结构可接触锁扣。"},),
        {"targetKind": "location", "description": "房间里有锁扣。"},
    )
