from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from trpg_server.ai.platform.deepseek import DeepSeekSettings
from trpg_server.items.ai_items.deepseek_adapter import DeepSeekFurnitureGenerationAdapter
from trpg_server.items.ai_items.furniture import (
    FurnitureGenerationError,
    FurnitureStructureRequest,
    resolve_furniture_candidates,
)
from trpg_server.locations.furniture import (
    FurnitureAtlasError,
    load_furniture_atlas,
)
from trpg_server.core.projection import replay
from trpg_server.story.scenario import compile_initial_events, load_scenario_package


ROOT = Path(__file__).resolve().parents[3]
CAMPAIGN = ROOT / "content/campaigns/gray-harbor"


def test_gray_harbor_furniture_atlas_covers_every_internal_structure() -> None:
    atlas = load_furniture_atlas(CAMPAIGN / "furniture-atlas.json")
    assert len(atlas.records) == 1422
    forbidden = {"bench", "table", "shelf", "storage_rack", "coat_rack"}
    assert not forbidden.intersection(record.kind for record in atlas.records)
    assert all("收纳" in record.description or "存放" in record.description for record in atlas.records)
    assert sum("剧本将这里描述为" in record.description for record in atlas.records) >= 400
    counts = {record.structure_id: 0 for record in atlas.records}
    for record in atlas.records:
        counts[record.structure_id] += 1
    assert set(counts.values()) == {3}
    assert all(
        record.visible is False
        for record in atlas.records
        if record.structure_id in {
            "loc_5_1_8__5",
            "loc_5_2_3__5",
            "loc_5_7_12__4",
            "loc_5_7_12__5",
        }
    )


def test_furniture_bootstrap_is_fixed_and_replayable() -> None:
    package = load_scenario_package(CAMPAIGN)
    events = compile_initial_events(package, "furniture-smoke")
    furniture_events = [
        value for value in events
        if value.event_type == "container.created" and value.payload.get("kind") == "furniture"
    ]
    state = replay("furniture-smoke", events, len(events))
    assert len(furniture_events) == 1422
    assert len([value for value in state.containers.values() if value.kind == "furniture"]) == 1422
    sample = state.containers[furniture_events[0].payload["containerId"]]
    assert sample.fixed is True
    assert sample.owner_character_id is None
    assert sample.location_id == sample.structure_id
    assert state.locations[sample.location_id].kind == "room"


def test_furniture_atlas_rejects_a_street_structure(tmp_path: Path) -> None:
    raw = json.loads((CAMPAIGN / "furniture-atlas.json").read_text(encoding="utf-8"))
    raw["furniture"][0]["structureId"] = "candle_oak"
    raw["furniture"][0]["locationId"] = "candle_oak"
    path = tmp_path / "furniture-atlas.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(FurnitureAtlasError):
        load_furniture_atlas(path)


def test_deepseek_furniture_adapter_returns_structured_candidates() -> None:
    request = FurnitureStructureRequest(
        structure_id="loc_5_1_1__1",
        location_id="loc_5_1_1",
        location_name="白鹭屋",
        structure_name="一楼前厅/酒吧/接待",
        purpose="接待、酒水和公开谈话",
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({
                        "schemaVersion": 1,
                        "structures": [{
                            "structureId": request.structure_id,
                            "furniture": [{
                                "kind": "bar_counter",
                                "name": "吧台",
                                "description": "固定酒水服务柜台",
                                "capacityWeightGrams": 80000,
                                "capacityVolumeCm3": 180000,
                                "confidence": 0.9,
                                "basis": ["结构用途为酒吧前厅"],
                            }],
                        }],
                    }, ensure_ascii=False)},
                }],
                "usage": {"total_tokens": 21},
            },
        )

    adapter = DeepSeekFurnitureGenerationAdapter(
        DeepSeekSettings(api_key="test-furniture-key", max_attempts=1),
        transport=httpx.MockTransport(handler),
    )
    candidates = resolve_furniture_candidates(adapter, (request,))
    assert len(candidates) == 1
    assert candidates[0].kind == "bar_counter"


def test_furniture_ai_output_cannot_omit_a_structure() -> None:
    class OmitAdapter:
        provider_name = "test"
        model_name = "test"

        def generate(self, structures):
            return type("Result", (), {"output": {"schemaVersion": 1, "structures": []}})()

    request = FurnitureStructureRequest("one", "place", "地点", "房间", "用途")
    with pytest.raises(FurnitureGenerationError, match="omitted"):
        resolve_furniture_candidates(OmitAdapter(), (request,))


def test_furniture_ai_rejects_decorative_non_storage_types() -> None:
    class DecorativeAdapter:
        provider_name = "test"
        model_name = "test"

        def generate(self, structures):
            return type("Result", (), {"output": {
                "schemaVersion": 1,
                "structures": [{
                    "structureId": structures[0].structure_id,
                    "furniture": [{
                        "kind": "table",
                        "name": "普通桌子",
                        "description": "没有收纳空间",
                        "capacityWeightGrams": 1000,
                        "capacityVolumeCm3": 1000,
                        "confidence": 0.9,
                        "basis": ["test"],
                    }],
                }],
            }})()

    request = FurnitureStructureRequest("one", "place", "地点", "房间", "用途")
    with pytest.raises(FurnitureGenerationError, match="not allowed"):
        resolve_furniture_candidates(DecorativeAdapter(), (request,))
