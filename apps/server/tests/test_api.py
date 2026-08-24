from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import trpg_server.core.main as main_module
from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID
from trpg_server.core.service import GameService


def test_api_health_and_turn(tmp_path: Path) -> None:
    original_service = main_module.service
    main_module.service = GameService(tmp_path / "api.sqlite3")
    try:
        with TestClient(main_module.app) as client:
            assert client.get("/health").json() == {"status": "ok"}
            assert client.get("/api/v1/system/intent-model").json() == {
                "enabled": False,
                "provider": None,
                "model": None,
                "minimumConfidence": 0.55,
            }
            assert client.get("/api/v1/system/narrator-model").json() == {
                "enabled": False,
                "provider": None,
                "model": None,
                "minimumConfidence": 0.7,
                "maxCharacters": 1200,
            }
            assert client.get("/api/v1/system/npc-decision-model").json() == {
                "enabled": False,
                "provider": None,
                "model": None,
                "minimumConfidence": 0.7,
            }
            assert client.get("/api/v1/system/weather-model").json() == {
                "enabled": False,
                "provider": None,
                "model": None,
            }
            state = client.get(
                f"/api/v1/campaigns/{GRAY_HARBOR_CAMPAIGN_ID}/state"
            ).json()

            response = client.post(
                f"/api/v1/campaigns/{GRAY_HARBOR_CAMPAIGN_ID}/turns",
                json={
                    "idempotency_key": "api-request-key-001",
                    "expected_state_version": state["stateVersion"],
                    "actor_id": "protagonist",
                    "text": "我去厨房。",
                },
            )

            assert response.status_code == 200
            assert response.json()["outcome"] == "moved"
    finally:
        main_module.service = original_service


def test_api_returns_conflict_for_stale_state(tmp_path: Path) -> None:
    original_service = main_module.service
    main_module.service = GameService(tmp_path / "conflict.sqlite3")
    try:
        with TestClient(main_module.app) as client:
            first = {
                "idempotency_key": "api-version-first",
                "expected_state_version": 1,
                "actor_id": "protagonist",
                "text": "我去厨房。",
            }
            assert client.post(
                f"/api/v1/campaigns/{GRAY_HARBOR_CAMPAIGN_ID}/turns",
                json=first,
            ).status_code == 200

            stale = {**first, "idempotency_key": "api-version-stale"}
            response = client.post(
                f"/api/v1/campaigns/{GRAY_HARBOR_CAMPAIGN_ID}/turns",
                json=stale,
            )
            assert response.status_code == 409
            assert response.json()["detail"]["currentStateVersion"] == 2
    finally:
        main_module.service = original_service


def test_api_exposes_turn_sources_and_recent_messages(tmp_path: Path) -> None:
    original_service = main_module.service
    main_module.service = GameService(tmp_path / "trace.sqlite3")
    try:
        with TestClient(main_module.app) as client:
            response = client.post(
                f"/api/v1/campaigns/{GRAY_HARBOR_CAMPAIGN_ID}/turns",
                json={
                    "idempotency_key": "api-trace-request",
                    "expected_state_version": 1,
                    "actor_id": "protagonist",
                    "text": "我去厨房。",
                },
            )
            assert response.status_code == 200
            result = response.json()

            detail_response = client.get(
                f"/api/v1/campaigns/{GRAY_HARBOR_CAMPAIGN_ID}/turns/{result['turn_id']}"
            )
            messages_response = client.get(
                f"/api/v1/campaigns/{GRAY_HARBOR_CAMPAIGN_ID}/messages?limit=10"
            )

            assert detail_response.status_code == 200
            detail = detail_response.json()
            assert detail["trace"] == result["trace"]
            assert len(detail["messages"]) == 2
            assert all(event["sources"] for event in detail["events"])
            assert detail["retrieval_traces"] == []
            assert len(detail["scene_memory_summaries"]) == 1
            assert "content" not in detail["scene_memory_summaries"][0]
            assert detail["narration_attempts"][0]["status"] == "local"
            assert detail["narration_attempts"][0]["request"] is None
            assert detail["narration_attempts"][0]["response"] is None
            assert messages_response.status_code == 200
            assert len(messages_response.json()) == 2
    finally:
        main_module.service = original_service


def test_api_returns_not_found_for_unknown_turn(tmp_path: Path) -> None:
    original_service = main_module.service
    main_module.service = GameService(tmp_path / "missing-turn.sqlite3")
    try:
        with TestClient(main_module.app) as client:
            response = client.get(
                f"/api/v1/campaigns/{GRAY_HARBOR_CAMPAIGN_ID}/turns/turn_missing"
            )
            assert response.status_code == 404
            assert response.json()["detail"] == "turn not found"
    finally:
        main_module.service = original_service


def test_api_exposes_real_gray_harbor_opening_and_can_reset_it(tmp_path: Path) -> None:
    original_service = main_module.service
    main_module.service = GameService(tmp_path / "gray-harbor-api.sqlite3")
    try:
        with TestClient(main_module.app) as client:
            state_response = client.get(
                f"/api/v1/campaigns/{GRAY_HARBOR_CAMPAIGN_ID}/state"
            )
            assert state_response.status_code == 200
            state = state_response.json()
            assert state["scene"]["locationId"] == "white_heron_ground_floor"

            moved = client.post(
                f"/api/v1/campaigns/{GRAY_HARBOR_CAMPAIGN_ID}/turns",
                json={
                    "idempotency_key": "api-gray-harbor-move",
                    "expected_state_version": state["stateVersion"],
                    "actor_id": "protagonist",
                    "text": "我去厨房。",
                },
            )
            assert moved.status_code == 200
            assert moved.json()["outcome"] == "moved"

            reset = client.post("/api/v1/gray-harbor/reset")
            assert reset.status_code == 200
            assert reset.json()["state"]["scene"]["locationId"] == "white_heron_ground_floor"
    finally:
        main_module.service = original_service


def test_api_exposes_read_only_map_atlas_and_route_queries(tmp_path: Path) -> None:
    original_service = main_module.service
    main_module.service = GameService(tmp_path / "map-api.sqlite3")
    try:
        with TestClient(main_module.app) as client:
            before = client.get(
                f"/api/v1/campaigns/{GRAY_HARBOR_CAMPAIGN_ID}/state"
            ).json()["stateVersion"]
            map_response = client.get(
                f"/api/v1/campaigns/{GRAY_HARBOR_CAMPAIGN_ID}/map"
            )
            assert map_response.status_code == 200
            assert map_response.json()["map"]["atlasId"] == "gray-harbor-v42-location-atlas"

            atlas_response = client.get(
                f"/api/v1/campaigns/{GRAY_HARBOR_CAMPAIGN_ID}/map/atlas"
            )
            assert atlas_response.status_code == 200
            atlas = atlas_response.json()["atlas"]
            assert len(atlas["locations"]) == 96
            assert "locationLinks" not in atlas

            route_response = client.get(
                f"/api/v1/campaigns/{GRAY_HARBOR_CAMPAIGN_ID}/map/route",
                params={
                    "fromLocationId": "catalog_l009",
                    "toLocationId": "catalog_l010",
                    "mode": "walking",
                },
            )
            assert route_response.status_code == 200
            assert route_response.json()["route"]["distanceKm"] == 0.645
            assert route_response.json()["route"]["travelMinutes"] == 9
            after = client.get(
                f"/api/v1/campaigns/{GRAY_HARBOR_CAMPAIGN_ID}/state"
            ).json()["stateVersion"]
            assert after == before

            missing_route = client.get(
                f"/api/v1/campaigns/{GRAY_HARBOR_CAMPAIGN_ID}/map/route",
                params={
                    "fromLocationId": "unknown",
                    "toLocationId": "catalog_l010",
                },
            )
            assert missing_route.status_code == 422
    finally:
        main_module.service = original_service
