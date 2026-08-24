from __future__ import annotations

import json

import httpx
import pytest

from trpg_server.ai.platform.deepseek import (
    DeepSeekIntentAdapter,
    DeepSeekSettings,
    intent_parser_from_environment,
)
from trpg_server.ai.player.intent import (
    IntentContext,
    IntentParseRequest,
    ModelAdapterResult,
    VisibleEntity,
    VisibleExit,
)


def parse_request() -> IntentParseRequest:
    return IntentParseRequest(
        system_instruction="只解析意图。",
        player_text="我想去厨房。",
        context=IntentContext(
            actor_id="protagonist",
            current_location=VisibleEntity(id="hall", name="大厅"),
            visible_characters=[],
            actor_inventory=[],
            visible_exits=[
                VisibleExit(
                    destination_id="kitchen",
                    name="厨房",
                    label="走进厨房",
                    aliases=["灶间"],
                )
            ],
            available_interactions=[],
            allowed_action_types=["move"],
            max_actions=4,
        ),
    )


def api_response() -> dict[str, object]:
    return {
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps({
                        "schema_version": 1,
                        "actions": [
                            {
                                "action_type": "move",
                                "destination_id": "kitchen",
                            }
                        ],
                        "needs_clarification": False,
                        "confidence": 0.97,
                    })
                },
            }
        ],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 32,
            "total_tokens": 152,
        },
    }


def test_deepseek_adapter_uses_json_mode_without_leaking_key_in_body() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        body = json.loads(request.content)
        captured["body"] = body
        return httpx.Response(200, json=api_response())

    adapter = DeepSeekIntentAdapter(
        DeepSeekSettings(
            api_key="test-secret-key",
            max_attempts=1,
            thinking_mode="disabled",
        ),
        transport=httpx.MockTransport(handler),
    )
    result = adapter.parse_intent(parse_request())

    assert isinstance(result, ModelAdapterResult)
    assert result.output["actions"][0]["destination_id"] == "kitchen"
    assert result.metrics.total_tokens == 152
    assert result.metrics.latency_ms is not None
    assert captured["authorization"] == "Bearer test-secret-key"
    assert "test-secret-key" not in json.dumps(captured["body"])
    body = captured["body"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}
    assert body["temperature"] == 0


def test_deepseek_thinking_mode_is_explicit_and_configurable() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=api_response())

    adapter = DeepSeekIntentAdapter(
        DeepSeekSettings(
            api_key="test-key",
            max_attempts=1,
            thinking_mode="enabled",
            reasoning_effort="max",
        ),
        transport=httpx.MockTransport(handler),
    )
    adapter.parse_intent(parse_request())

    assert captured["thinking"] == {"type": "enabled"}
    assert captured["reasoning_effort"] == "max"


def test_deepseek_retries_only_once_for_transient_failure() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return httpx.Response(200, json=api_response())

    adapter = DeepSeekIntentAdapter(
        DeepSeekSettings(
            api_key="test-key",
            max_attempts=2,
            retry_delay_seconds=0,
        ),
        transport=httpx.MockTransport(handler),
    )

    assert adapter.parse_intent(parse_request()).metrics.total_tokens == 152
    assert calls == 2


def test_deepseek_timeout_becomes_standard_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("late", request=request)

    adapter = DeepSeekIntentAdapter(
        DeepSeekSettings(api_key="test-key", max_attempts=1),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(TimeoutError):
        adapter.parse_intent(parse_request())


def test_environment_configuration_is_disabled_by_default_and_fails_closed() -> None:
    disabled = intent_parser_from_environment({})
    configured = intent_parser_from_environment({
        "TRPG_INTENT_MODEL_ENABLED": "true",
        "TRPG_INTENT_MODEL_PROVIDER": "deepseek",
        "DEEPSEEK_API_KEY": "test-key",
        "DEEPSEEK_THINKING_MODE": "enabled",
    })

    assert disabled.adapter.available is False
    assert configured.adapter.available is True
    assert configured.adapter.provider_name == "deepseek"
    assert configured.adapter.settings.thinking_mode == "enabled"
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        intent_parser_from_environment({"TRPG_INTENT_MODEL_ENABLED": "true"})
