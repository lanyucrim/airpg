from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


SERVER_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SERVER_ROOT.parents[1]
SOURCE_ROOT = SERVER_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events
from trpg_server.ai.platform.deepseek import DeepSeekIntentAdapter, DeepSeekSettings
from trpg_server.evaluation import evaluate_intent_suite, load_intent_evaluation_suite
from trpg_server.ai.player.intent import StructuredIntentParser
from trpg_server.core.projection import replay


DEFAULT_SUITE = REPOSITORY_ROOT / "evals" / "intent" / "gray-harbor-v1.json"


def main() -> int:
    arguments = _arguments()
    settings = DeepSeekSettings.from_environment()
    if arguments.thinking is not None:
        settings = DeepSeekSettings(
            api_key=settings.api_key,
            base_url=settings.base_url,
            model=settings.model,
            timeout_seconds=settings.timeout_seconds,
            max_tokens=settings.max_tokens,
            max_attempts=settings.max_attempts,
            retry_delay_seconds=settings.retry_delay_seconds,
            thinking_mode=arguments.thinking,
            reasoning_effort=arguments.reasoning_effort,
        )
    suite = load_intent_evaluation_suite(arguments.suite)
    parser = StructuredIntentParser(DeepSeekIntentAdapter(settings))
    state = replay(GRAY_HARBOR_CAMPAIGN_ID, gray_harbor_events(), 1)
    report = evaluate_intent_suite(
        parser,
        state,
        suite,
        set(arguments.case_id) if arguments.case_id else None,
    )
    report["provider"] = "deepseek"
    report["model"] = settings.model
    report["thinkingMode"] = settings.thinking_mode
    if arguments.compact:
        report["results"] = [
            result for result in report["results"] if not result["passed"]
        ]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["gatesPassed"] else 1


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--thinking", choices=("disabled", "enabled"))
    parser.add_argument("--compact", action="store_true")
    parser.add_argument(
        "--reasoning-effort",
        choices=("low", "high", "max"),
        default="high",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
