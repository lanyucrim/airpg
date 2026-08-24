"""Read-only loading and validation for the gray-harbor item atlas.

The atlas is content input, never runtime state. It contains definitions for
all known kinds of items and the small set of concrete instances that exist at
campaign start. A definition alone must never be treated as an item the player
can find, own, or consume.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from trpg_server.items.contract import (
    ITEM_CONTRACT_SCHEMA_VERSION,
    ITEM_RECORD_FIELDS,
    record_field_error,
)
from trpg_server.items.durability import DurabilityError, validate_item_durability
from trpg_server.items.functions import ItemFunctionError, validate_item_properties


class ItemAtlasError(ValueError):
    """Raised when authored item content violates its published contract."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ItemAtlasError(f"cannot read item atlas: {path}") from error
    if not isinstance(value, dict):
        raise ItemAtlasError("item atlas root must be an object")
    return value


def _require(value: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in value:
        raise ItemAtlasError(f"{path} requires {key}")
    return value[key]


def _is_int(value: object) -> bool:
    """Accept JSON integers, but never Python's boolean subclass of ``int``."""

    return type(value) is int


def _validate_record(
    record: Mapping[str, Any],
    *,
    path: str,
    definition: bool,
) -> None:
    error = record_field_error(dict(record), path=path)
    if error is not None:
        raise ItemAtlasError(error)

    if type(record["id"]) is not str or not record["id"]:
        raise ItemAtlasError(f"{path}.id must be a non-empty string")
    if type(record["definitionId"]) is not str or not record["definitionId"]:
        raise ItemAtlasError(f"{path}.definitionId must be a non-empty string")
    if type(record["name"]) is not str or not record["name"]:
        raise ItemAtlasError(f"{path}.name must be a non-empty string")
    if type(record["description"]) is not str or not record["description"]:
        raise ItemAtlasError(f"{path}.description must be a non-empty string")
    if type(record["category"]) is not str or not record["category"]:
        raise ItemAtlasError(f"{path}.category must be a non-empty string")
    if type(record["isPlotItem"]) is not bool:
        raise ItemAtlasError(f"{path}.isPlotItem must be boolean")
    if not _is_int(record["quantity"]) or record["quantity"] < 1:
        raise ItemAtlasError(f"{path}.quantity must be a positive integer")
    if type(record["stackable"]) is not bool:
        raise ItemAtlasError(f"{path}.stackable must be boolean")
    if not record["stackable"] and record["quantity"] != 1:
        raise ItemAtlasError(f"{path} is not stackable and must have quantity 1")
    for key in ("unitWeightGrams", "valueCrown"):
        value = record[key]
        if value is not None and (not _is_int(value) or value < 0):
            raise ItemAtlasError(f"{path}.{key} must be null or a non-negative integer")
    condition = record["condition"]
    if condition is not None and (type(condition) is not str or not condition):
        raise ItemAtlasError(f"{path}.condition must be null or a non-empty string")
    for key in ("containerId", "locationId"):
        value = record[key]
        if value is not None and (type(value) is not str or not value):
            raise ItemAtlasError(f"{path}.{key} must be null or a non-empty string")
    if record["containerId"] is not None and record["locationId"] is not None:
        raise ItemAtlasError(f"{path} cannot have both containerId and locationId")
    if not isinstance(record["properties"], Mapping):
        raise ItemAtlasError(f"{path}.properties must be an object")
    try:
        properties = validate_item_properties(
            record["properties"],
            category=record["category"],
            path=f"{path}.properties",
        )
    except ItemFunctionError as error:
        raise ItemAtlasError(str(error)) from error
    try:
        validate_item_durability(
            category=record["category"],
            properties=properties,
            durability=record["durability"],
            require_for_eligible=not definition,
        )
    except DurabilityError as error:
        raise ItemAtlasError(f"{path}.{error}") from error
    if definition:
        if record["id"] != record["definitionId"]:
            raise ItemAtlasError(f"{path}.id must equal definitionId")
        if record["quantity"] != 1:
            raise ItemAtlasError(f"{path}.quantity must be 1")
        if record["condition"] is not None or record["durability"] is not None:
            raise ItemAtlasError(f"{path} cannot carry mutable condition or durability")
        if record["containerId"] is not None or record["locationId"] is not None:
            raise ItemAtlasError(f"{path} cannot carry runtime placement")


def _validate_currency_system(
    document: Mapping[str, Any],
    definitions: tuple[Mapping[str, Any], ...],
) -> None:
    if document.get("schemaVersion") != 5:
        raise ItemAtlasError("unsupported currency-system schemaVersion")
    denominations = _require(document, "denominations", "currency-system")
    if not isinstance(denominations, list) or len(denominations) != 4:
        raise ItemAtlasError("currency-system requires four denominations")
    currency_definitions = {
        str(value["id"]): value
        for value in definitions
        if value["category"] == "currency"
    }
    definition_ids: list[str] = []
    values: list[int] = []
    for index, denomination in enumerate(denominations):
        if not isinstance(denomination, Mapping):
            raise ItemAtlasError(
                f"currency-system.denominations[{index}] must be an object"
            )
        value = denomination.get("valueInCrowns")
        if not _is_int(value) or value < 1:
            raise ItemAtlasError(
                f"currency-system.denominations[{index}].valueInCrowns is invalid"
            )
        values.append(value)
        definition_id = denomination.get("itemDefinitionId")
        if type(definition_id) is not str or not definition_id:
            raise ItemAtlasError(
                f"currency-system.denominations[{index}].itemDefinitionId is invalid"
            )
        definition = currency_definitions.get(definition_id)
        if definition is None:
            raise ItemAtlasError(
                f"currency-system denomination references unknown currency definition: {definition_id}"
            )
        if definition["valueCrown"] != value:
            raise ItemAtlasError(
                f"currency-system denomination value differs from item definition: {definition_id}"
            )
        definition_ids.append(definition_id)
    if values != [1, 1_000, 1_000_000, 1_000_000_000]:
        raise ItemAtlasError("currency-system must use the fixed 1000:1 crown ladder")
    if len(set(definition_ids)) != len(definition_ids):
        raise ItemAtlasError("currency-system itemDefinitionId values must be unique")
    if set(definition_ids) != set(currency_definitions):
        raise ItemAtlasError("currency-system must map every currency item definition")
    base_unit = _require(document, "baseUnit", "currency-system")
    if not isinstance(base_unit, Mapping):
        raise ItemAtlasError("currency-system.baseUnit must be an object")
    if (
        base_unit.get("id") != denominations[0].get("id")
        or base_unit.get("itemDefinitionId") != definition_ids[0]
        or base_unit.get("valueInCrowns") != values[0]
    ):
        raise ItemAtlasError("currency-system.baseUnit must mirror the first denomination")
    item_policy = _require(document, "itemPolicy", "currency-system")
    if not isinstance(item_policy, Mapping):
        raise ItemAtlasError("currency-system.itemPolicy must be an object")
    if item_policy.get("physicalCurrencyIsItem") is not True:
        raise ItemAtlasError("currency-system must model currency as physical items")
    if item_policy.get("currencyCategory") != "currency":
        raise ItemAtlasError("currency-system must use the currency item category")
    if item_policy.get("currencyIsStackable") is not True:
        raise ItemAtlasError("currency-system must make currency stackable")
    if (
        item_policy.get("playerHoldingPolicy")
        != "physical_item_instances_in_owned_containers"
    ):
        raise ItemAtlasError(
            "currency-system must keep player currency in owned item containers"
        )
    if item_policy.get("numericPlayerBalanceSupported") is not False:
        raise ItemAtlasError("currency-system must not expose a numeric player balance")
    if item_policy.get("transactionSystemStatus") != "deferred":
        raise ItemAtlasError("currency-system transactions must remain deferred")
    if "accountBalanceIsSeparate" in item_policy:
        raise ItemAtlasError("currency-system uses obsolete accountBalanceIsSeparate")
    if item_policy.get("definitionIdentityField") != "definitionId":
        raise ItemAtlasError("currency-system must identify denominations with definitionId")
    if item_policy.get("unitValueField") != "valueCrown":
        raise ItemAtlasError("currency-system must use valueCrown for unit value")
    if item_policy.get("totalValueFormula") != "quantity * valueCrown":
        raise ItemAtlasError(
            "currency-system must derive total value from quantity * valueCrown"
        )


def _validate_field_contract(document: Mapping[str, Any]) -> None:
    """Ensure the atlas actually references the published 15-field contract."""

    if document.get("schemaVersion") != ITEM_CONTRACT_SCHEMA_VERSION:
        raise ItemAtlasError("unsupported item-field-specification schemaVersion")
    fields = _require(document, "fields", "item-field-specification")
    if fields != list(ITEM_RECORD_FIELDS):
        raise ItemAtlasError(
            "item-field-specification fields do not match the runtime 15-field contract"
        )


def validate_item_atlas(document: Mapping[str, Any]) -> None:
    """Validate the 15-field atlas without producing runtime state."""

    if document.get("schemaVersion") != 5:
        raise ItemAtlasError("unsupported item atlas schemaVersion")
    for key in (
        "atlasId",
        "fieldContractRef",
        "currencySystemRef",
        "definitions",
        "instances",
        "counts",
    ):
        _require(document, key, "item-atlas")
    definitions = document["definitions"]
    instances = document["instances"]
    if not isinstance(definitions, list) or not isinstance(instances, list):
        raise ItemAtlasError("item-atlas definitions and instances must be arrays")

    definition_ids: set[str] = set()
    definitions_by_id: dict[str, Mapping[str, Any]] = {}
    for index, value in enumerate(definitions):
        if not isinstance(value, Mapping):
            raise ItemAtlasError(f"definitions[{index}] must be an object")
        _validate_record(value, path=f"definitions[{index}]", definition=True)
        item_id = str(value["id"])
        if item_id in definition_ids:
            raise ItemAtlasError(f"duplicate item definition id: {item_id}")
        definition_ids.add(item_id)
        definitions_by_id[item_id] = value

    instance_ids: set[str] = set()
    immutable_keys = (
        "definitionId",
        "name",
        "description",
        "category",
        "isPlotItem",
        "stackable",
        "unitWeightGrams",
        "valueCrown",
        "properties",
    )
    for index, value in enumerate(instances):
        if not isinstance(value, Mapping):
            raise ItemAtlasError(f"instances[{index}] must be an object")
        _validate_record(value, path=f"instances[{index}]", definition=False)
        item_id = str(value["id"])
        if item_id in instance_ids:
            raise ItemAtlasError(f"duplicate item instance id: {item_id}")
        instance_ids.add(item_id)
        definition_id = str(value["definitionId"])
        if item_id == definition_id:
            raise ItemAtlasError(
                f"instances[{index}].id must differ from definitionId"
            )
        if item_id in definition_ids:
            raise ItemAtlasError(
                f"item instance id collides with a definition id: {item_id}"
            )
        definition = definitions_by_id.get(definition_id)
        if definition is None:
            raise ItemAtlasError(
                f"instances[{index}].definitionId does not exist: {definition_id}"
            )
        mismatched = [key for key in immutable_keys if value[key] != definition[key]]
        if mismatched:
            raise ItemAtlasError(
                f"instances[{index}] differs from definition {definition_id}: {mismatched}"
            )

    counts = document["counts"]
    if not isinstance(counts, Mapping):
        raise ItemAtlasError("item-atlas.counts must be an object")
    count_keys = (
        "plotDefinitions",
        "ordinaryDefinitions",
        "currencyDefinitions",
        "definitions",
        "instances",
    )
    for key in count_keys:
        if not _is_int(counts.get(key)) or counts[key] < 0:
            raise ItemAtlasError(f"item-atlas.counts.{key} must be a non-negative integer")
    if counts.get("definitions") != len(definitions) or counts.get("instances") != len(instances):
        raise ItemAtlasError("item-atlas counts do not match its records")
    currencies = [value for value in definitions if value["category"] == "currency"]
    if len(currencies) != 4:
        raise ItemAtlasError("item-atlas requires exactly four currency definitions")
    if counts.get("plotDefinitions") != sum(
        1 for value in definitions if value["isPlotItem"]
    ):
        raise ItemAtlasError("item-atlas plotDefinitions count does not match its records")
    if counts.get("ordinaryDefinitions") != sum(
        1 for value in definitions if not value["isPlotItem"]
    ):
        raise ItemAtlasError("item-atlas ordinaryDefinitions count does not match its records")
    if counts.get("currencyDefinitions") != len(currencies):
        raise ItemAtlasError("item-atlas currencyDefinitions count does not match its records")
    if any(value["isPlotItem"] or not value["stackable"] for value in currencies):
        raise ItemAtlasError("currency definitions must be non-plot and stackable")
    values = sorted(value["valueCrown"] for value in currencies)
    if values != [1, 1_000, 1_000_000, 1_000_000_000]:
        raise ItemAtlasError("currency definition values do not match the crown ladder")


@dataclass(frozen=True, slots=True)
class ItemAtlas:
    """Immutable view of validated static item content."""

    document: Mapping[str, Any]
    currency_system: Mapping[str, Any]

    @property
    def definitions(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.document["definitions"])

    @property
    def instances(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.document["instances"])

    def definition(self, definition_id: str) -> Mapping[str, Any] | None:
        return next(
            (value for value in self.definitions if value["id"] == definition_id),
            None,
        )

    def instance(self, item_id: str) -> Mapping[str, Any] | None:
        return next(
            (value for value in self.instances if value["id"] == item_id),
            None,
        )


def load_item_atlas(path: Path) -> ItemAtlas:
    """Load an atlas and its required currency document from the same folder."""

    document = _read_json(path)
    validate_item_atlas(document)
    field_contract_ref = document["fieldContractRef"]
    if (
        type(field_contract_ref) is not str
        or Path(field_contract_ref).name != field_contract_ref
    ):
        raise ItemAtlasError("item-atlas.fieldContractRef must be a local filename")
    field_contract = _read_json(path.parent / field_contract_ref)
    _validate_field_contract(field_contract)

    currency_ref = document["currencySystemRef"]
    if type(currency_ref) is not str or Path(currency_ref).name != currency_ref:
        raise ItemAtlasError("item-atlas.currencySystemRef must be a local filename")
    currency_system = _read_json(path.parent / currency_ref)
    _validate_currency_system(currency_system, tuple(document["definitions"]))
    return ItemAtlas(document=document, currency_system=currency_system)


__all__ = ["ItemAtlas", "ItemAtlasError", "load_item_atlas", "validate_item_atlas"]
