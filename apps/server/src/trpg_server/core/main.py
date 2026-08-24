from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from trpg_server.ai.platform.environment import load_backend_environment
from trpg_server.ai.platform.weather_adapter import weather_director_from_environment
from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID
from trpg_server.ai.platform.deepseek import intent_parser_from_environment
from trpg_server.ai.player.narration import narrator_from_environment
from trpg_server.characters.decision import npc_decider_from_environment
from trpg_server.behavior.routine_rules import routine_director_from_environment
from trpg_server.core.schemas import (
    CampaignResetResponse,
    MessageSchema,
    TurnDetailResponse,
    TurnRequest,
    TurnResponse,
)
from trpg_server.core.service import (
    CampaignNotFoundError,
    GameService,
    StateVersionConflictError,
    TurnNotFoundError,
)
from trpg_server.map import MapRouteError, atlas_for_scenario, atlas_summary, calculate_route


load_backend_environment()


def _database_path() -> Path:
    configured = os.getenv("TRPG_DATABASE_PATH")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "data" / "trpg.sqlite3"


service = GameService(
    _database_path(),
    intent_parser_from_environment(),
    narrator_from_environment(),
    npc_decider_from_environment(),
    routine_director_from_environment(),
    weather_director_from_environment(),
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    service.initialize()
    yield


app = FastAPI(
    title="AI-TRPG Authoritative API",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Idempotency-Key"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/system/intent-model")
def get_intent_model_status() -> dict[str, object]:
    adapter = service.intent_parser.adapter
    return {
        "enabled": adapter.available,
        "provider": getattr(adapter, "provider_name", None),
        "model": adapter.model_name,
        "minimumConfidence": service.intent_parser.minimum_confidence,
    }


@app.get("/api/v1/system/narrator-model")
def get_narrator_model_status() -> dict[str, object]:
    adapter = service.narrator.adapter
    return {
        "enabled": adapter.available,
        "provider": getattr(adapter, "provider_name", None),
        "model": adapter.model_name,
        "minimumConfidence": service.narrator.minimum_confidence,
        "maxCharacters": service.narrator.max_characters,
    }


@app.get("/api/v1/system/npc-decision-model")
def get_npc_decision_model_status() -> dict[str, object]:
    adapter = service.npc_decider.adapter
    return {
        "enabled": adapter.available,
        "provider": getattr(adapter, "provider_name", None),
        "model": adapter.model_name,
        "minimumConfidence": service.npc_decider.minimum_confidence,
    }


@app.get("/api/v1/system/routine-model")
def get_routine_model_status() -> dict[str, object]:
    adapter = service.routine_director.adapter
    return {
        "enabled": adapter.available,
        "provider": getattr(adapter, "provider_name", None),
        "model": adapter.model_name,
        "minimumConfidence": service.routine_director.minimum_confidence,
    }


@app.get("/api/v1/system/weather-model")
def get_weather_model_status() -> dict[str, object]:
    adapter = service.weather_director.adapter
    return {
        "enabled": adapter.available,
        "provider": getattr(adapter, "provider_name", None),
        "model": getattr(adapter, "model_name", None),
    }


@app.get("/api/v1/campaigns/{campaign_id}/state")
def get_campaign_state(campaign_id: str) -> dict[str, object]:
    try:
        return service.get_state(campaign_id)
    except CampaignNotFoundError as error:
        raise HTTPException(status_code=404, detail="campaign not found") from error


@app.get("/api/v1/campaigns/{campaign_id}/map")
def get_campaign_map(campaign_id: str) -> dict[str, object]:
    try:
        state = service.get_state(campaign_id)
    except CampaignNotFoundError as error:
        raise HTTPException(status_code=404, detail="campaign not found") from error
    return {
        "campaignId": campaign_id,
        "map": state["map"],
    }


@app.get("/api/v1/campaigns/{campaign_id}/map/atlas")
def get_campaign_map_atlas(campaign_id: str) -> dict[str, object]:
    try:
        state = service.get_state(campaign_id)
    except CampaignNotFoundError as error:
        raise HTTPException(status_code=404, detail="campaign not found") from error
    scenario = state.get("scenario")
    scenario_id = scenario.get("scenarioId") if isinstance(scenario, dict) else None
    atlas = atlas_for_scenario(scenario_id if isinstance(scenario_id, str) else None)
    if atlas is None:
        raise HTTPException(status_code=404, detail="map atlas not found")
    return {"campaignId": campaign_id, "atlas": atlas_summary(atlas)}


@app.get("/api/v1/campaigns/{campaign_id}/map/route")
def get_campaign_map_route(
    campaign_id: str,
    from_location_id: str = Query(alias="fromLocationId", min_length=1),
    to_location_id: str = Query(alias="toLocationId", min_length=1),
    mode: Literal["walking", "horse_carriage"] = Query(default="walking"),
) -> dict[str, object]:
    try:
        state = service.get_state(campaign_id)
    except CampaignNotFoundError as error:
        raise HTTPException(status_code=404, detail="campaign not found") from error
    scenario = state.get("scenario")
    scenario_id = scenario.get("scenarioId") if isinstance(scenario, dict) else None
    atlas = atlas_for_scenario(scenario_id if isinstance(scenario_id, str) else None)
    if atlas is None:
        raise HTTPException(status_code=404, detail="map atlas not found")
    try:
        route = calculate_route(atlas, from_location_id, to_location_id, mode)
    except (KeyError, MapRouteError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"campaignId": campaign_id, "route": route}


@app.post("/api/v1/campaigns/{campaign_id}/turns", response_model=TurnResponse)
def submit_turn(campaign_id: str, request: TurnRequest) -> dict[str, object]:
    try:
        return service.submit_turn(campaign_id, request)
    except CampaignNotFoundError as error:
        raise HTTPException(status_code=404, detail="campaign not found") from error
    except StateVersionConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "state_version_conflict",
                "currentStateVersion": error.current_version,
            },
        ) from error


@app.get(
    "/api/v1/campaigns/{campaign_id}/messages",
    response_model=list[MessageSchema],
)
def get_recent_messages(campaign_id: str, limit: int = Query(default=30, ge=1, le=100)):
    try:
        return service.get_recent_messages(campaign_id, limit)
    except CampaignNotFoundError as error:
        raise HTTPException(status_code=404, detail="campaign not found") from error


@app.get(
    "/api/v1/campaigns/{campaign_id}/turns/{turn_id}",
    response_model=TurnDetailResponse,
)
def get_turn_detail(campaign_id: str, turn_id: str):
    try:
        return service.get_turn_detail(campaign_id, turn_id)
    except CampaignNotFoundError as error:
        raise HTTPException(status_code=404, detail="campaign not found") from error
    except TurnNotFoundError as error:
        raise HTTPException(status_code=404, detail="turn not found") from error


@app.post("/api/v1/gray-harbor/reset", response_model=CampaignResetResponse)
def reset_gray_harbor() -> dict[str, object]:
    return {
        "campaign_id": GRAY_HARBOR_CAMPAIGN_ID,
        "state": service.reset_gray_harbor(),
    }
