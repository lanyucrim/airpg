from __future__ import annotations

from functools import cache
from pathlib import Path
from trpg_server.core.state import Event
from trpg_server.story.scenario import ScenarioPackage, compile_initial_events, load_scenario_package


GRAY_HARBOR_CAMPAIGN_ID = "cmp_gray_harbor"
GRAY_HARBOR_SCENARIO_PATH = (
    Path(__file__).resolve().parents[5] / "content" / "campaigns" / "gray-harbor"
)


@cache
def gray_harbor_scenario() -> ScenarioPackage:
    return load_scenario_package(GRAY_HARBOR_SCENARIO_PATH)


def gray_harbor_events() -> list[Event]:
    return compile_initial_events(gray_harbor_scenario(), GRAY_HARBOR_CAMPAIGN_ID)
