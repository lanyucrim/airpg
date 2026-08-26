from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import httpx

from trpg_server.ai.platform.deepseek import DeepSeekSettings
from trpg_server.core.projection import replay
from trpg_server.core.state import Event, Projection
from trpg_server.items.ai_items.deepseek_adapter import DeepSeekRecipeAssessmentAdapter
from trpg_server.items.ai_items.era import EraTechnologyProfile
from trpg_server.items.ai_items.generation import (
    DailyItemDefinitionCatalog,
    DailyItemGenerationAdapterResult,
    DailyItemGenerationRequest,
)
from trpg_server.items.ai_items.recipes import (
    GeneratedRecipeCatalog,
    RecipeAssessmentAdapterResult,
    RecipeAssessmentRequest,
    render_generated_recipe_markdown,
    resolve_item_recipe,
)
from trpg_server.items.ai_items.references import (
    DailyItemReferenceTable,
    ReferenceCallMetrics,
)
from trpg_server.items.models import ItemContainer, ItemInstance
from trpg_server.items.recipes import (
    RecipeConversionInput,
    RecipeIngredient,
    build_recipe_conversion_plan,
)


ROOT = Path(__file__).resolve().parents[3]
AI_CONTENT = ROOT / "content" / "campaigns" / "gray-harbor" / "items-atlas" / "ai-items"
ERA_PATH = AI_CONTENT / "era-technology-profile.json"
RECIPES_PATH = AI_CONTENT / "generated-recipes.json"
DAILY_PATH = AI_CONTENT / "daily-item-definitions.json"
REFERENCE_PATH = AI_CONTENT / "daily-item-references.json"


class FakeRecipeAdapter:
    available = True
    provider_name = "fake"
    model_name = "recipe-model"

    def __init__(self, output: Mapping[str, object] | Exception) -> None:
        self.output = output
        self.calls = 0

    def assess(self, request, era_profile, ingredient_definitions):  # type: ignore[no-untyped-def]
        del request, era_profile, ingredient_definitions
        self.calls += 1
        if isinstance(self.output, Exception):
            raise self.output
        return RecipeAssessmentAdapterResult(
            output=self.output,
            metrics=ReferenceCallMetrics(total_tokens=80, latency_ms=10),
        )


class FakeGenerationAdapter:
    available = True
    provider_name = "fake"
    model_name = "generation-model"

    def __init__(self, output: Mapping[str, object]) -> None:
        self.output = output
        self.calls = 0

    def generate(self, request: DailyItemGenerationRequest) -> DailyItemGenerationAdapterResult:
        del request
        self.calls += 1
        return DailyItemGenerationAdapterResult(
            output=self.output,
            metrics=ReferenceCallMetrics(total_tokens=90, latency_ms=12),
        )


def _definition(
    definition_id: str,
    name: str,
    *,
    category: str = "material",
    weight: int | None = 100,
    plot: bool = False,
    stackable: bool = True,
    properties: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": definition_id,
        "definitionId": definition_id,
        "name": name,
        "description": f"普通的{name}。",
        "category": category,
        "isPlotItem": plot,
        "quantity": 1,
        "stackable": stackable,
        "unitWeightGrams": weight,
        "valueCrown": 3,
        "condition": None,
        "durability": None,
        "containerId": None,
        "locationId": None,
        "properties": dict(properties or {}),
    }


def _known() -> tuple[Mapping[str, object], ...]:
    return (
        _definition("alcohol_portion", "一份酒精", weight=100),
        _definition("cotton_strip", "一条棉布", weight=20),
        _definition("wooden_stick", "一根木棍", weight=150),
        _definition(
            "improvised_torch",
            "简易火把",
            category="tool",
            weight=250,
            stackable=False,
            properties={
                "consumable": {
                    "schemaVersion": 1,
                    "quantityPerUse": 1,
                    "method": "burn",
                    "targetKinds": ["location"],
                    "riskClass": "moderate",
                    "effectCandidates": [
                        {
                            "domain": "locations",
                            "effectKind": "illumination",
                            "summary": "燃烧时提供有限照明",
                            "magnitude": "minor",
                            "durationMinutes": 60,
                            "requiresDomainResolution": True,
                        }
                    ],
                }
            },
        ),
    )


def _request() -> RecipeAssessmentRequest:
    return RecipeAssessmentRequest(
        "用棉布包住木棍的一端并用酒精浸润",
        (
            RecipeIngredient("alcohol_portion", 1),
            RecipeIngredient("cotton_strip", 1),
            RecipeIngredient("wooden_stick", 1),
        ),
    )


def _candidate(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schemaVersion": 1,
        "decision": "accepted",
        "ingredients": [value.to_mapping() for value in _request().ingredients],
        "outputText": "简易火把",
        "outputQuantity": 1,
        "processSummary": "将棉布固定在木棍一端并浸润酒精。",
        "eraCompatible": True,
        "eraEvidence": ["manual_craft"],
        "confidence": 0.92,
        "rejectionReason": None,
    }
    value.update(overrides)
    return value


def _era() -> EraTechnologyProfile:
    return EraTechnologyProfile.load(ERA_PATH)


def _recipe_catalog() -> GeneratedRecipeCatalog:
    return GeneratedRecipeCatalog.load(RECIPES_PATH, _era())


def _daily_catalog() -> DailyItemDefinitionCatalog:
    return DailyItemDefinitionCatalog.load(DAILY_PATH)


def _references() -> DailyItemReferenceTable:
    return DailyItemReferenceTable.load(REFERENCE_PATH)


def test_era_profile_and_empty_recipe_catalog_are_current() -> None:
    era = _era()
    catalog = _recipe_catalog()

    assert era.analogue_period == "现实十九世纪末至二十世纪初之间"
    assert "automatic_weapons" in era.technology_ids
    assert catalog.recipes == ()
    assert render_generated_recipe_markdown(catalog).rstrip() == RECIPES_PATH.with_suffix(
        ".md"
    ).read_text(encoding="utf-8").rstrip()


def test_era_compatible_recipe_reuses_existing_output_definition() -> None:
    adapter = FakeRecipeAdapter(_candidate())

    resolution = resolve_item_recipe(
        _recipe_catalog(),
        _daily_catalog(),
        _references(),
        _era(),
        _request(),
        adapter,
        known_definitions=_known(),
    )

    assert resolution.status == "model_accepted"
    assert adapter.calls == 1
    assert resolution.generation_adapter_called is False
    assert resolution.entry is not None
    assert resolution.entry.output_definition_id == "improvised_torch"
    assert len(resolution.recipe_catalog.recipes) == 1


def test_recipe_cache_hit_calls_neither_model() -> None:
    first = resolve_item_recipe(
        _recipe_catalog(),
        _daily_catalog(),
        _references(),
        _era(),
        _request(),
        FakeRecipeAdapter(_candidate()),
        known_definitions=_known(),
    )
    recipe_adapter = FakeRecipeAdapter(RuntimeError("must not run"))
    generation_adapter = FakeGenerationAdapter({})

    second = resolve_item_recipe(
        first.recipe_catalog,
        first.daily_catalog,
        first.reference_table,
        _era(),
        _request(),
        recipe_adapter,
        generation_adapter,
        known_definitions=_known(),
    )

    assert second.status == "cache_hit"
    assert recipe_adapter.calls == 0
    assert generation_adapter.calls == 0


def test_recipe_rejects_changed_inputs_low_confidence_and_blocked_technology() -> None:
    changed = _candidate(
        ingredients=[RecipeIngredient("wooden_stick", 1).to_mapping()]
    )
    for candidate in (
        changed,
        _candidate(confidence=0.5),
        _candidate(eraEvidence=["digital_electronics"]),
        _candidate(eraEvidence=["automatic_weapons"]),
    ):
        resolution = resolve_item_recipe(
            _recipe_catalog(),
            _daily_catalog(),
            _references(),
            _era(),
            _request(),
            FakeRecipeAdapter(candidate),
            known_definitions=_known(),
        )
        assert resolution.status == "rejected"
        assert resolution.recipe_catalog.recipes == ()


def test_recipe_rejects_plot_inputs_unknown_weights_and_mass_creation() -> None:
    variants = (
        (_known()[:-1] + (_definition("improvised_torch", "简易火把", category="tool", weight=400, stackable=False),)),
        tuple(
            _definition(value["definitionId"], value["name"], weight=None)
            if value["definitionId"] == "cotton_strip"
            else value
            for value in _known()
        ),
        tuple(
            _definition(value["definitionId"], value["name"], plot=True)
            if value["definitionId"] == "cotton_strip"
            else value
            for value in _known()
        ),
    )
    for known in variants:
        resolution = resolve_item_recipe(
            _recipe_catalog(),
            _daily_catalog(),
            _references(),
            _era(),
            _request(),
            FakeRecipeAdapter(_candidate()),
            known_definitions=known,
        )
        assert resolution.status == "rejected"
        assert resolution.recipe_catalog.recipes == ()


def test_missing_output_uses_daily_item_generation_once() -> None:
    known = _known()[:-1]
    generation = FakeGenerationAdapter(
        {
            "schemaVersion": 2,
            "isDailyItem": True,
            "itemKey": "improvised_torch_each",
            "canonicalName": "简易火把",
            "aliases": [],
            "materials": ["木棍", "棉布"],
            "formAndStructure": "棉布紧密缠绕并固定在木棍一端",
            "sizeDescription": "可以单手握持的短杆大小",
            "observableFeatures": ["缠绕端明显增粗", "棉布表面湿润"],
            "unknownFacts": ["棉布吸收液体的具体成分无法从外观确认"],
            "description": (
                "这是一支由木棍和棉布组成的简易火把，棉布紧密缠绕并固定在木棍一端，"
                "可以单手握持的短杆大小。缠绕端明显增粗，棉布表面湿润；"
                "棉布吸收液体的具体成分无法从外观确认。"
            ),
            "category": "tool",
            "unitDescription": "一支简易火把",
            "stackable": False,
            "estimatedRetailUsd": 1.0,
            "unitWeightGrams": 250,
            "equipment": {
                "mode": "held",
                "slotIds": ["left_hand", "right_hand"],
                "handCount": 1,
            },
            "consumable": {
                "schemaVersion": 1,
                "quantityPerUse": 1,
                "method": "burn",
                "targetKinds": ["location"],
                "riskClass": "moderate",
                "effectCandidates": [
                    {
                        "domain": "locations",
                        "effectKind": "illumination",
                        "summary": "燃烧时提供有限照明",
                        "magnitude": "minor",
                        "durationMinutes": 60,
                        "requiresDomainResolution": True,
                    }
                ],
            },
            "confidence": 0.9,
            "assumptions": ["按一支临时火把估算"],
        }
    )

    resolution = resolve_item_recipe(
        _recipe_catalog(),
        _daily_catalog(),
        _references(),
        _era(),
        _request(),
        FakeRecipeAdapter(_candidate()),
        generation,
        known_definitions=known,
    )

    assert resolution.status == "model_accepted"
    assert generation.calls == 1
    assert resolution.generation_adapter_called is True
    assert resolution.output_definition is not None
    assert resolution.output_definition["properties"]["equipment"]["handCount"] == 1


def test_conversion_plan_validates_instances_and_replays_candidate_events() -> None:
    accepted = resolve_item_recipe(
        _recipe_catalog(),
        _daily_catalog(),
        _references(),
        _era(),
        _request(),
        FakeRecipeAdapter(_candidate()),
        known_definitions=_known(),
    )
    assert accepted.entry is not None and accepted.output_definition is not None
    state = Projection(campaign_id="cmp_recipe", player_character_id="player")
    state.containers["pack"] = ItemContainer(
        container_id="pack", kind="inventory", owner_character_id="player"
    )
    create_events: list[Event] = [
        Event(
            "evt_pack",
            "container.created",
            "system",
            0,
            {"containerId": "pack", "kind": "inventory", "ownerCharacterId": "player"},
        )
    ]
    inputs: list[RecipeConversionInput] = []
    for index, definition in enumerate(_known()[:3]):
        item = ItemInstance(
            item_id=f"input_{index}",
            definition_id=str(definition["definitionId"]),
            name=str(definition["name"]),
            description=str(definition["description"]),
            category=str(definition["category"]),
            is_plot_item=False,
            quantity=1,
            stackable=True,
            unit_weight_grams=int(definition["unitWeightGrams"]),
            value_crown=3,
            condition="intact",
            durability=None,
            container_id="pack",
            location_id=None,
            properties={},
        )
        state.items[item.item_id] = item
        from trpg_server.items.commands import build_item_created_event

        create_events.append(build_item_created_event(actor_id="system", world_time=0, item=item))
        inputs.append(RecipeConversionInput(item.item_id, 1))

    plan = build_recipe_conversion_plan(
        state,
        actor_id="player",
        world_time=5,
        blueprint=accepted.entry.blueprint,
        output_definition=accepted.output_definition,
        inputs=tuple(inputs),
        output_item_id="crafted_torch",
        destination_container_id="pack",
    )

    assert [event.event_type for event in plan.events] == [
        "item.consumed",
        "item.consumed",
        "item.consumed",
        "item.created",
    ]
    replayed = replay(
        "cmp_recipe", [*create_events, *plan.events], len(create_events) + len(plan.events)
    )
    assert all(value.item_id not in replayed.items for value in inputs)
    assert replayed.items["crafted_torch"].definition_id == "improvised_torch"


def test_deepseek_recipe_adapter_includes_exact_inputs_and_era_profile() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured["body"] = body
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(_candidate())}}
                ],
                "usage": {"total_tokens": 100},
            },
        )

    adapter = DeepSeekRecipeAssessmentAdapter(
        DeepSeekSettings(api_key="recipe-test-key", max_attempts=1),
        transport=httpx.MockTransport(handler),
    )
    result = adapter.assess(_request(), _era(), _known()[:3])

    assert result.output["decision"] == "accepted"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["temperature"] == 0.1
    prompt = json.dumps(body, ensure_ascii=False)
    assert "manual_craft" in prompt
    assert "alcohol_portion" in prompt
    assert "recipe-test-key" not in prompt
