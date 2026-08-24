"""Run one non-persistent DeepSeek smoke call for each active-module adapter.

This script never saves catalogs, materializes events, or opens the game store.
It prints only status and usage metadata; model payloads and credentials are
deliberately excluded from its output.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
SERVER_SRC = ROOT / "apps" / "server" / "src"
sys.path.insert(0, str(SERVER_SRC))

from trpg_server.ai.platform.deepseek import DeepSeekSettings  # noqa: E402
from trpg_server.ai.platform.environment import load_backend_environment  # noqa: E402
from trpg_server.ai.platform.weather_adapter import (  # noqa: E402
    DeepSeekWeatherAdapter,
)
from trpg_server.behavior.router import interpret_player_text  # noqa: E402
from trpg_server.characters.decision import (  # noqa: E402
    DeepSeekNpcDecisionAdapter,
    SafeNpcDecider,
)
from trpg_server.core.projection import replay  # noqa: E402
from trpg_server.items.ai_items.deepseek_adapter import (  # noqa: E402
    DeepSeekDailyItemGenerationAdapter,
    DeepSeekItemReferenceAdapter,
    DeepSeekRecipeAssessmentAdapter,
)
from trpg_server.items.ai_items.era import EraTechnologyProfile  # noqa: E402
from trpg_server.items.ai_items.generation import (  # noqa: E402
    DailyItemGenerationCandidate,
    DailyItemGenerationRequest,
)
from trpg_server.items.ai_items.recipes import (  # noqa: E402
    RecipeAssessmentCandidate,
    RecipeAssessmentRequest,
)
from trpg_server.items.ai_items.references import (  # noqa: E402
    DailyItemReferenceRequest,
    ItemReferenceCandidate,
)
from trpg_server.items.recipe_models import RecipeIngredient  # noqa: E402
from trpg_server.story.bootstrap import (  # noqa: E402
    GRAY_HARBOR_CAMPAIGN_ID,
    gray_harbor_events,
)
from trpg_server.world.weather import SafeWeatherDirector  # noqa: E402


AI_CONTENT = (
    ROOT
    / "content"
    / "campaigns"
    / "gray-harbor"
    / "items-atlas"
    / "ai-items"
)


def _load_environment(path: Path) -> dict[str, str]:
    load_backend_environment(path)
    return dict(os.environ)


def _settings(environment: dict[str, str]) -> DeepSeekSettings:
    configured = DeepSeekSettings.from_environment(environment)
    return replace(
        configured,
        max_attempts=1,
        retry_delay_seconds=0,
        max_tokens=min(configured.max_tokens, 256),
        thinking_mode="disabled",
    )


def _definition(
    definition_id: str,
    name: str,
    weight: int,
) -> dict[str, Any]:
    return {
        "id": definition_id,
        "definitionId": definition_id,
        "name": name,
        "description": f"用于非持久化 AI 冒烟验证的{name}。",
        "category": "material",
        "isPlotItem": False,
        "quantity": 1,
        "stackable": True,
        "unitWeightGrams": weight,
        "valueCrown": 1,
        "condition": None,
        "durability": None,
        "containerId": None,
        "locationId": None,
        "properties": {},
    }


def _npc_smoke(settings: DeepSeekSettings) -> dict[str, Any]:
    state = replay(GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events(), 1)
    item = state.items["protagonist_small_knife"]
    original = (state.world_time, item.container_id, item.value_crown)
    item.value_crown = 36
    command = interpret_player_text(
        "我把小刀递给哈维，想贿赂他通融。",
        actor_id="protagonist",
        state=state,
    )
    result = SafeNpcDecider(DeepSeekNpcDecisionAdapter(settings)).decide(
        state,
        command,
    )
    if result.audit.status != "model_accepted" or result.decision is None:
        raise RuntimeError(f"NPC decision failed: {result.audit.failure_code}")
    if (state.world_time, item.container_id) != original[:2]:
        raise RuntimeError("NPC decision mutated authoritative state")
    return {
        "status": result.audit.status,
        "decision": result.decision.outcome,
        "tokens": result.audit.total_tokens,
    }


def _reference_smoke(settings: DeepSeekSettings) -> dict[str, Any]:
    request = DailyItemReferenceRequest(
        item_key="orange_medium_each_smoke",
        name="橙子",
        aliases=("中等橙子",),
        unit_description="一个中等大小、完整可购买的橙子",
    )
    result = DeepSeekItemReferenceAdapter(settings).estimate(request)
    candidate = ItemReferenceCandidate.from_output(result.output, request)
    return {
        "status": "model_accepted",
        "weightGrams": candidate.unit_weight_grams,
        "tokens": result.metrics.total_tokens,
    }


def _daily_item_smoke(settings: DeepSeekSettings) -> dict[str, Any]:
    request = DailyItemGenerationRequest("一块刚烤好的燕麦饼")
    result = DeepSeekDailyItemGenerationAdapter(settings).generate(request)
    candidate = DailyItemGenerationCandidate.from_output(result.output)
    return {
        "status": "model_accepted",
        "category": candidate.category,
        "tokens": result.metrics.total_tokens,
    }


def _recipe_smoke(settings: DeepSeekSettings) -> dict[str, Any]:
    request = RecipeAssessmentRequest(
        "用棉布包住木棍的一端并用酒精浸润，制成简易火把",
        (
            RecipeIngredient("alcohol_portion", 1),
            RecipeIngredient("cotton_strip", 1),
            RecipeIngredient("wooden_stick", 1),
        ),
    )
    era = EraTechnologyProfile.load(AI_CONTENT / "era-technology-profile.json")
    definitions = (
        _definition("alcohol_portion", "一份酒精", 100),
        _definition("cotton_strip", "一条棉布", 20),
        _definition("wooden_stick", "一根木棍", 150),
    )
    result = DeepSeekRecipeAssessmentAdapter(settings).assess(
        request,
        era,
        definitions,
    )
    candidate = RecipeAssessmentCandidate.from_output(result.output, request, era)
    return {
        "status": "model_accepted",
        "output": candidate.output_text,
        "tokens": result.metrics.total_tokens,
    }


def _weather_smoke(settings: DeepSeekSettings) -> dict[str, Any]:
    state = replay(GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events(), 1)
    original = (state.world_time, len(state.weather_by_date))
    result = SafeWeatherDirector(DeepSeekWeatherAdapter(settings)).propose(
        state,
        previous_world_time=state.world_time,
    )
    if result.audit.status != "model_accepted":
        raise RuntimeError(f"weather generation failed: {result.audit.failure_code}")
    if (state.world_time, len(state.weather_by_date)) != original:
        raise RuntimeError("weather proposal mutated authoritative state")
    return {
        "status": result.audit.status,
        "days": len(result.accepted),
        "tokens": result.audit.metrics.total_tokens,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Non-persistent AI smoke for characters, items, and weather",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=ROOT / "apps" / "server" / ".env",
    )
    parser.add_argument(
        "--only",
        choices=(
            "npc_decision",
            "item_reference",
            "daily_item_generation",
            "recipe_assessment",
            "weather",
        ),
        help="run one capability instead of the complete smoke set",
    )
    args = parser.parse_args()
    settings = _settings(_load_environment(args.env_file))
    checks: tuple[tuple[str, Callable[[DeepSeekSettings], dict[str, Any]]], ...] = (
        ("npc_decision", _npc_smoke),
        ("item_reference", _reference_smoke),
        ("daily_item_generation", _daily_item_smoke),
        ("recipe_assessment", _recipe_smoke),
        ("weather", _weather_smoke),
    )
    if args.only is not None:
        checks = tuple(value for value in checks if value[0] == args.only)
    report: list[dict[str, Any]] = []
    for name, check in checks:
        try:
            report.append({"capability": name, **check(settings)})
        except Exception as error:
            report.append({
                "capability": name,
                "status": "failed",
                "errorType": type(error).__name__,
                "message": str(error),
            })
            break
    output = {"persistentWrites": False, "results": report}
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if settings.api_key in rendered:
        raise RuntimeError("credential appeared in smoke output")
    print(rendered)
    return 0 if len(report) == len(checks) and all(
        result["status"] == "model_accepted" for result in report
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
