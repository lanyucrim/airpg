from __future__ import annotations

import copy
import json
from pathlib import Path
import runpy
import shutil

import pytest

from trpg_server.items.catalog import ItemAtlasError, load_item_atlas, validate_item_atlas
from trpg_server.items.contract import ITEM_RECORD_FIELD_SET


ATLAS_PATH = (
    Path(__file__).resolve().parents[3]
    / "content"
    / "campaigns"
    / "gray-harbor"
    / "items-atlas"
    / "important-items.json"
)
SEED_PATH = (
    Path(__file__).resolve().parents[3]
    / "content"
    / "campaigns"
    / "gray-harbor"
    / "items.json"
)
BUILDER_PATH = Path(__file__).resolve().parents[3] / "scripts" / "build_gray_harbor_items.py"


def _document() -> dict[str, object]:
    return json.loads(ATLAS_PATH.read_text(encoding="utf-8"))


def test_gray_harbor_item_atlas_has_40_definitions_and_6_instances() -> None:
    atlas = load_item_atlas(ATLAS_PATH)

    assert len(atlas.definitions) == 40
    assert len(atlas.instances) == 6
    assert atlas.document["fieldContractRef"] == "item-field-specification.json"
    assert atlas.document["counts"] == {
        "plotDefinitions": 32,
        "ordinaryDefinitions": 8,
        "currencyDefinitions": 4,
        "definitions": 40,
        "instances": 6,
    }
    assert atlas.currency_system["schemaVersion"] == 5
    assert atlas.currency_system["itemPolicy"] == {
        "physicalCurrencyIsItem": True,
        "currencyCategory": "currency",
        "currencyIsStackable": True,
        "playerHoldingPolicy": "physical_item_instances_in_owned_containers",
        "numericPlayerBalanceSupported": False,
        "transactionSystemStatus": "deferred",
        "definitionIdentityField": "definitionId",
        "unitValueField": "valueCrown",
        "valueField": "valueCrown",
        "quantityMeaning": "同一面额货币单位的数量",
        "totalValueFormula": "quantity * valueCrown",
        "stackMergeKey": "definitionId + condition + containerId/locationId + properties",
        "note": "玩家可用货币只由其自有容器中的实体钱币实例构成；definitionId 区分面额，valueCrown 表示单枚或单张价值，总值由程序派生。当前不定义账户余额、支付、找零或交易流程。",
    }
    assert {
        denomination["itemDefinitionId"]
        for denomination in atlas.currency_system["denominations"]
    } == {
        "currency_kron",
        "currency_silver_shield",
        "currency_gold_crown",
        "currency_royal_treasury_note",
    }
    assert all(set(record) == ITEM_RECORD_FIELD_SET for record in atlas.definitions)
    assert all(set(record) == ITEM_RECORD_FIELD_SET for record in atlas.instances)
    assert atlas.instance("protagonist_small_knife")["containerId"] == (
        "protagonist_equipment"
    )
    assert atlas.instance("protagonist_small_knife")["durability"] == {
        "current": 85.0,
        "max": 100.0,
    }


def test_currency_system_rejects_unknown_item_definition_mapping(tmp_path: Path) -> None:
    copied_atlas = tmp_path / "items-atlas"
    shutil.copytree(ATLAS_PATH.parent, copied_atlas)
    currency_path = copied_atlas / "currency-system.json"
    document = json.loads(currency_path.read_text(encoding="utf-8"))
    document["denominations"][0]["itemDefinitionId"] = "currency_missing"
    currency_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with pytest.raises(ItemAtlasError, match="unknown currency definition"):
        load_item_atlas(copied_atlas / "important-items.json")


def test_currency_system_rejects_a_separate_numeric_player_balance(tmp_path: Path) -> None:
    copied_atlas = tmp_path / "items-atlas"
    shutil.copytree(ATLAS_PATH.parent, copied_atlas)
    currency_path = copied_atlas / "currency-system.json"
    document = json.loads(currency_path.read_text(encoding="utf-8"))
    policy = document["itemPolicy"]
    assert isinstance(policy, dict)
    policy["numericPlayerBalanceSupported"] = True
    currency_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with pytest.raises(ItemAtlasError, match="numeric player balance"):
        load_item_atlas(copied_atlas / "important-items.json")


def test_item_atlas_keeps_definition_and_instance_layers_separate() -> None:
    atlas = load_item_atlas(ATLAS_PATH)
    definitions = {record["id"]: record for record in atlas.definitions}

    for instance in atlas.instances:
        definition = definitions[instance["definitionId"]]
        assert instance["id"] != instance["definitionId"]
        assert instance["containerId"] is not None
        assert instance["locationId"] is None
        for field in (
            "name",
            "description",
            "category",
            "isPlotItem",
            "stackable",
            "unitWeightGrams",
            "valueCrown",
            "properties",
        ):
            assert instance[field] == definition[field]


def test_runtime_seed_is_an_exact_mirror_of_the_atlas_initial_instances() -> None:
    atlas = load_item_atlas(ATLAS_PATH)
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    builder = runpy.run_path(str(BUILDER_PATH))
    generated = builder["build_runtime_seed"]()

    assert seed["schemaVersion"] == 3
    assert seed["atlasFile"] == "items-atlas/important-items.json"
    assert seed["instances"] == list(atlas.instances)
    assert generated == seed


def test_item_atlas_rejects_fields_outside_the_15_field_contract() -> None:
    document = _document()
    document["definitions"][0]["fieldUsage"] = {}

    with pytest.raises(ItemAtlasError, match="outside the item contract"):
        validate_item_atlas(document)


def test_item_atlas_rejects_instance_with_unknown_definition() -> None:
    document = _document()
    instances = document["instances"]
    assert isinstance(instances, list)
    broken = copy.deepcopy(instances[0])
    broken["definitionId"] = "does_not_exist"
    instances[0] = broken

    with pytest.raises(ItemAtlasError, match="definitionId does not exist"):
        validate_item_atlas(document)
