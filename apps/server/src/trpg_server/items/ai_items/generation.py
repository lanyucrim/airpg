"""Validated generation and caching of reusable ordinary item definitions.

This module stops at a static definition.  It does not create an item instance,
decide where an item came from, submit an event, or grant anything to a player.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Protocol
import unicodedata

from trpg_server.items.ai_items.references import (
    DailyItemReference,
    DailyItemReferenceTable,
    ModelAudit,
    ReferenceCallMetrics,
    crown_value_from_usd,
    price_ratio_to_apple,
)
from trpg_server.items.contract import (
    ITEM_CONTRACT_SCHEMA_VERSION,
    ITEM_RECORD_FIELDS,
    item_contract_fingerprint,
)
from trpg_server.items.functions import (
    ItemFunctionError,
    validate_generated_function_profiles,
)
from trpg_server.items.models import ItemDefinition


GENERATION_SCHEMA_VERSION = 1
MINIMUM_GENERATION_CONFIDENCE = 0.60
MAX_RETAIL_USD = Decimal("100000")
MAX_UNIT_WEIGHT_GRAMS = 1_000_000

# Each official item field needs an explicit owner.  This intentionally fails
# when the item contract changes until generation policy is updated as part of
# the same development batch.
GENERATION_FIELD_POLICIES: tuple[tuple[str, str], ...] = (
    ("id", "program_stable_definition_id"),
    ("definitionId", "program_same_as_id"),
    ("name", "validated_model_candidate"),
    ("description", "validated_model_candidate"),
    ("category", "validated_daily_category"),
    ("isPlotItem", "program_false"),
    ("quantity", "program_one"),
    ("stackable", "validated_model_candidate"),
    ("unitWeightGrams", "validated_reference_cache"),
    ("valueCrown", "program_apple_ratio"),
    ("condition", "definition_null"),
    ("durability", "definition_null"),
    ("containerId", "definition_null"),
    ("locationId", "definition_null"),
    ("properties", "validated_restricted_function_candidates"),
)

DAILY_ITEM_CATEGORIES = frozenset(
    {
        "food",
        "drink",
        "clothing",
        "household",
        "personal_care",
        "stationery",
        "tool",
        "material",
        "container",
    }
)

_ITEM_KEY = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
_ROOT_FIELDS = frozenset(
    {
        "schemaVersion",
        "catalogId",
        "itemContract",
        "knownDefinitionAliases",
        "definitions",
    }
)
_CONTRACT_FIELDS = frozenset(
    {"schemaVersion", "fields", "fingerprint", "generationPolicyFingerprint"}
)
_ENTRY_FIELDS = frozenset(
    {
        "itemKey",
        "aliases",
        "unitDescription",
        "sourceStatus",
        "confidence",
        "assumptions",
        "modelAudit",
        "item",
    }
)
_AUDIT_FIELDS = frozenset(
    {
        "provider",
        "model",
        "promptTokens",
        "completionTokens",
        "totalTokens",
        "latencyMs",
    }
)
_CANDIDATE_FIELDS = frozenset(
    {
        "schemaVersion",
        "isDailyItem",
        "itemKey",
        "canonicalName",
        "aliases",
        "description",
        "category",
        "unitDescription",
        "stackable",
        "estimatedRetailUsd",
        "unitWeightGrams",
        "equipment",
        "consumable",
        "confidence",
        "assumptions",
    }
)
_SOURCE_STATUSES = frozenset({"model_generated", "reviewed"})


class DailyItemGenerationError(ValueError):
    """Raised when a daily item candidate or generated catalog is invalid."""


def assert_generation_contract_sync() -> None:
    policy_fields = tuple(field for field, _ in GENERATION_FIELD_POLICIES)
    if policy_fields != ITEM_RECORD_FIELDS:
        raise DailyItemGenerationError(
            "daily item generation field policy is out of sync with the item contract"
        )


assert_generation_contract_sync()


def generation_policy_fingerprint() -> str:
    payload = json.dumps(
        {
            "generationSchemaVersion": GENERATION_SCHEMA_VERSION,
            "fieldPolicies": GENERATION_FIELD_POLICIES,
            "dailyCategories": sorted(DAILY_ITEM_CATEGORIES),
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return f"sha256:{sha256(payload).hexdigest()}"


def current_item_contract_snapshot() -> dict[str, Any]:
    assert_generation_contract_sync()
    return {
        "schemaVersion": ITEM_CONTRACT_SCHEMA_VERSION,
        "fields": list(ITEM_RECORD_FIELDS),
        "fingerprint": item_contract_fingerprint(),
        "generationPolicyFingerprint": generation_policy_fingerprint(),
    }


@dataclass(frozen=True, slots=True)
class DailyItemGenerationRequest:
    observed_text: str

    def __post_init__(self) -> None:
        value = _non_empty_string(self.observed_text, "observed_text")
        if len(value) > 200:
            raise DailyItemGenerationError("observed_text cannot exceed 200 characters")
        object.__setattr__(self, "observed_text", value)


@dataclass(frozen=True, slots=True)
class DailyItemGenerationAdapterResult:
    output: Mapping[str, Any]
    metrics: ReferenceCallMetrics = ReferenceCallMetrics()


class DailyItemGenerationAdapter(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def generate(
        self,
        request: DailyItemGenerationRequest,
    ) -> DailyItemGenerationAdapterResult: ...


@dataclass(frozen=True, slots=True)
class DailyItemGenerationCandidate:
    item_key: str
    canonical_name: str
    aliases: tuple[str, ...]
    description: str
    category: str
    unit_description: str
    stackable: bool
    estimated_retail_usd: Decimal
    unit_weight_grams: int
    properties: Mapping[str, Any]
    confidence: float
    assumptions: tuple[str, ...]

    @classmethod
    def from_output(
        cls,
        output: Mapping[str, Any],
        *,
        minimum_confidence: float = MINIMUM_GENERATION_CONFIDENCE,
    ) -> "DailyItemGenerationCandidate":
        if not isinstance(output, Mapping) or set(output) != _CANDIDATE_FIELDS:
            raise DailyItemGenerationError(
                "model output fields do not match the daily item candidate contract"
            )
        if output["schemaVersion"] != GENERATION_SCHEMA_VERSION:
            raise DailyItemGenerationError("unsupported generation candidate schemaVersion")
        if output["isDailyItem"] is not True:
            raise DailyItemGenerationError("candidate is not classified as an ordinary daily item")
        item_key = _non_empty_string(output["itemKey"], "itemKey").lower()
        if len(item_key) > 80 or _ITEM_KEY.fullmatch(item_key) is None:
            raise DailyItemGenerationError(
                "itemKey must use at most 80 lowercase ASCII characters and underscores"
            )
        if item_key.startswith("daily_"):
            raise DailyItemGenerationError("itemKey must not include the daily_ prefix")
        name = _non_empty_string(output["canonicalName"], "canonicalName")
        description = _non_empty_string(output["description"], "description")
        unit_description = _non_empty_string(
            output["unitDescription"], "unitDescription"
        )
        if len(name) > 80:
            raise DailyItemGenerationError("canonicalName cannot exceed 80 characters")
        if len(description) > 300:
            raise DailyItemGenerationError("description cannot exceed 300 characters")
        if len(unit_description) > 120:
            raise DailyItemGenerationError("unitDescription cannot exceed 120 characters")
        aliases = _string_tuple(output["aliases"], "aliases", maximum=8)
        category = _non_empty_string(output["category"], "category")
        if category not in DAILY_ITEM_CATEGORIES:
            raise DailyItemGenerationError(
                f"category is not allowed for generated daily items: {category}"
            )
        stackable = output["stackable"]
        if type(stackable) is not bool:
            raise DailyItemGenerationError("stackable must be boolean")
        price = _positive_decimal(
            output["estimatedRetailUsd"],
            "estimatedRetailUsd",
            maximum=MAX_RETAIL_USD,
        )
        weight = output["unitWeightGrams"]
        if type(weight) is not int or not 1 <= weight <= MAX_UNIT_WEIGHT_GRAMS:
            raise DailyItemGenerationError(
                "unitWeightGrams must be an integer between 1 and 1000000"
            )
        try:
            properties = validate_generated_function_profiles(
                equipment=output["equipment"],
                consumable=output["consumable"],
            )
        except ItemFunctionError as error:
            raise DailyItemGenerationError(
                f"generated item function profile is invalid: {error}"
            ) from error
        confidence = _confidence(output["confidence"])
        if confidence < minimum_confidence:
            raise DailyItemGenerationError(
                f"confidence is below the acceptance threshold {minimum_confidence}"
            )
        assumptions = _string_tuple(
            output["assumptions"], "assumptions", maximum=8, item_maximum=200
        )
        lookup_terms = (name, *aliases)
        if len({_normalize_lookup(value) for value in lookup_terms}) != len(lookup_terms):
            raise DailyItemGenerationError(
                "canonicalName and aliases must not duplicate one another"
            )
        return cls(
            item_key=item_key,
            canonical_name=name,
            aliases=aliases,
            description=description,
            category=category,
            unit_description=unit_description,
            stackable=stackable,
            estimated_retail_usd=price,
            unit_weight_grams=weight,
            properties=properties,
            confidence=confidence,
            assumptions=assumptions,
        )


@dataclass(frozen=True, slots=True)
class DailyItemDefinitionEntry:
    item_key: str
    aliases: tuple[str, ...]
    unit_description: str
    source_status: str
    confidence: float
    assumptions: tuple[str, ...]
    model_audit: ModelAudit | None
    item: Mapping[str, Any]

    @property
    def definition_id(self) -> str:
        return str(self.item["id"])

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DailyItemDefinitionEntry":
        if not isinstance(value, Mapping) or set(value) != _ENTRY_FIELDS:
            raise DailyItemGenerationError("generated definition entry fields are invalid")
        item_key = _non_empty_string(value["itemKey"], "itemKey").lower()
        if _ITEM_KEY.fullmatch(item_key) is None or item_key.startswith("daily_"):
            raise DailyItemGenerationError("generated definition itemKey is invalid")
        aliases = _string_tuple(value["aliases"], "aliases", maximum=16)
        unit_description = _non_empty_string(
            value["unitDescription"], "unitDescription"
        )
        if len(item_key) > 80:
            raise DailyItemGenerationError("generated definition itemKey is too long")
        if len(unit_description) > 120:
            raise DailyItemGenerationError(
                "generated definition unitDescription is too long"
            )
        source_status = value["sourceStatus"]
        if source_status not in _SOURCE_STATUSES:
            raise DailyItemGenerationError("sourceStatus is invalid")
        confidence = _confidence(value["confidence"])
        if source_status == "model_generated" and confidence < MINIMUM_GENERATION_CONFIDENCE:
            raise DailyItemGenerationError(
                "model-generated definition confidence is below the acceptance threshold"
            )
        assumptions = _string_tuple(
            value["assumptions"], "assumptions", maximum=8, item_maximum=200
        )
        audit_value = value["modelAudit"]
        if audit_value is not None and not isinstance(audit_value, Mapping):
            raise DailyItemGenerationError("modelAudit must be null or an object")
        audit = _audit_from_mapping(audit_value) if audit_value is not None else None
        if source_status == "model_generated" and audit is None:
            raise DailyItemGenerationError("model-generated definitions require modelAudit")
        if source_status != "model_generated" and audit is not None:
            raise DailyItemGenerationError("reviewed definitions cannot carry modelAudit")
        item_value = value["item"]
        if not isinstance(item_value, Mapping):
            raise DailyItemGenerationError("item must be an object")
        try:
            definition = ItemDefinition.from_payload(item_value)
        except ValueError as error:
            raise DailyItemGenerationError(f"generated item is invalid: {error}") from error
        item = definition.to_payload()
        expected_id = f"daily_{item['category']}_{item_key}"
        if item["id"] != expected_id:
            raise DailyItemGenerationError(
                f"generated definition id must be {expected_id}"
            )
        if item["isPlotItem"] is not False:
            raise DailyItemGenerationError("generated daily definitions cannot be plot items")
        if item["category"] not in DAILY_ITEM_CATEGORIES:
            raise DailyItemGenerationError("generated definition category is not daily")
        if item["unitWeightGrams"] is None or item["valueCrown"] is None:
            raise DailyItemGenerationError(
                "generated daily definitions require cached price and weight"
            )
        terms = (item["name"], *aliases)
        if len({_normalize_lookup(term) for term in terms}) != len(terms):
            raise DailyItemGenerationError("definition aliases duplicate its name")
        return cls(
            item_key=item_key,
            aliases=aliases,
            unit_description=unit_description,
            source_status=source_status,
            confidence=confidence,
            assumptions=assumptions,
            model_audit=audit,
            item=item,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "itemKey": self.item_key,
            "aliases": list(self.aliases),
            "unitDescription": self.unit_description,
            "sourceStatus": self.source_status,
            "confidence": self.confidence,
            "assumptions": list(self.assumptions),
            "modelAudit": self.model_audit.to_mapping() if self.model_audit else None,
            "item": dict(self.item),
        }


class DailyItemDefinitionCatalog:
    """Validated generated definitions with an explicit item-contract snapshot."""

    def __init__(
        self,
        *,
        catalog_id: str,
        known_definition_aliases: Mapping[str, str] | None = None,
        definitions: tuple[DailyItemDefinitionEntry, ...] = (),
    ) -> None:
        self.catalog_id = _non_empty_string(catalog_id, "catalogId")
        self._known_definition_aliases = dict(known_definition_aliases or {})
        self._definitions = list(definitions)
        self._validate()

    @property
    def definitions(self) -> tuple[DailyItemDefinitionEntry, ...]:
        return tuple(self._definitions)

    @property
    def known_definition_aliases(self) -> Mapping[str, str]:
        return dict(self._known_definition_aliases)

    @classmethod
    def empty(cls, catalog_id: str) -> "DailyItemDefinitionCatalog":
        return cls(catalog_id=catalog_id)

    @classmethod
    def from_document(
        cls,
        document: Mapping[str, Any],
    ) -> "DailyItemDefinitionCatalog":
        if not isinstance(document, Mapping) or set(document) != _ROOT_FIELDS:
            raise DailyItemGenerationError("generated catalog root fields are invalid")
        if document["schemaVersion"] != GENERATION_SCHEMA_VERSION:
            raise DailyItemGenerationError("unsupported generated catalog schemaVersion")
        contract = document["itemContract"]
        if not isinstance(contract, Mapping) or set(contract) != _CONTRACT_FIELDS:
            raise DailyItemGenerationError("generated catalog itemContract is invalid")
        expected_contract = current_item_contract_snapshot()
        if dict(contract) != expected_contract:
            raise DailyItemGenerationError(
                "generated catalog uses a stale item contract or generation policy"
            )
        raw_definitions = document["definitions"]
        if not isinstance(raw_definitions, list):
            raise DailyItemGenerationError("definitions must be an array")
        known_aliases = document["knownDefinitionAliases"]
        if not isinstance(known_aliases, Mapping):
            raise DailyItemGenerationError("knownDefinitionAliases must be an object")
        return cls(
            catalog_id=document["catalogId"],
            known_definition_aliases=dict(known_aliases),
            definitions=tuple(
                DailyItemDefinitionEntry.from_mapping(value)
                for value in raw_definitions
            ),
        )

    @classmethod
    def load(cls, path: Path) -> "DailyItemDefinitionCatalog":
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DailyItemGenerationError(
                f"cannot read generated daily item catalog: {path}"
            ) from error
        return cls.from_document(document)

    def clone(self) -> "DailyItemDefinitionCatalog":
        return self.from_document(self.to_document())

    def lookup(self, query: str) -> DailyItemDefinitionEntry | None:
        normalized = _normalize_lookup(query)
        if not normalized:
            return None
        for entry in self._definitions:
            terms = (
                entry.item_key,
                entry.definition_id,
                str(entry.item["name"]),
                *entry.aliases,
            )
            if normalized in {_normalize_lookup(term) for term in terms}:
                return entry
        return None

    def known_definition_id(self, query: str) -> str | None:
        normalized = _normalize_lookup(query)
        return next(
            (
                definition_id
                for alias, definition_id in self._known_definition_aliases.items()
                if _normalize_lookup(alias) == normalized
            ),
            None,
        )

    def add_known_definition_alias(self, alias: str, definition_id: str) -> None:
        value = _non_empty_string(alias, "known definition alias")
        target = _non_empty_string(definition_id, "known definition id")
        normalized = _normalize_lookup(value)
        existing = next(
            (
                current
                for key, current in self._known_definition_aliases.items()
                if _normalize_lookup(key) == normalized
            ),
            None,
        )
        if existing is not None and existing != target:
            raise DailyItemGenerationError(
                f"known definition alias already points to {existing}: {value}"
            )
        if existing is None:
            if len(self._known_definition_aliases) >= 10_000:
                raise DailyItemGenerationError("known definition alias cache is full")
            self._known_definition_aliases[value] = target

    def add(self, entry: DailyItemDefinitionEntry) -> None:
        if any(value.item_key == entry.item_key for value in self._definitions):
            raise DailyItemGenerationError(f"itemKey already exists: {entry.item_key}")
        if any(value.definition_id == entry.definition_id for value in self._definitions):
            raise DailyItemGenerationError(
                f"definition id already exists: {entry.definition_id}"
            )
        occupied = self._lookup_ownership()
        for term in (entry.item_key, entry.definition_id, entry.item["name"], *entry.aliases):
            owner = occupied.get(_normalize_lookup(term))
            if owner is not None:
                raise DailyItemGenerationError(
                    f"lookup term already belongs to {owner}: {term}"
                )
        self._definitions.append(entry)
        self._definitions.sort(key=lambda value: value.item_key)

    def add_aliases(self, item_key: str, aliases: tuple[str, ...]) -> DailyItemDefinitionEntry:
        index = next(
            (i for i, value in enumerate(self._definitions) if value.item_key == item_key),
            None,
        )
        if index is None:
            raise DailyItemGenerationError(f"unknown generated itemKey: {item_key}")
        current = self._definitions[index]
        occupied = self._lookup_ownership()
        merged = list(current.aliases)
        own_terms = {
            _normalize_lookup(current.item_key),
            _normalize_lookup(current.definition_id),
            _normalize_lookup(current.item["name"]),
            *(_normalize_lookup(value) for value in current.aliases),
        }
        for alias in aliases:
            value = _non_empty_string(alias, "alias")
            normalized = _normalize_lookup(value)
            owner = occupied.get(normalized)
            if owner is not None and owner != current.item_key:
                raise DailyItemGenerationError(
                    f"lookup alias already belongs to {owner}: {value}"
                )
            if normalized not in own_terms:
                if len(merged) >= 16:
                    raise DailyItemGenerationError("generated definition has too many aliases")
                merged.append(value)
                own_terms.add(normalized)
        updated = replace(current, aliases=tuple(merged))
        self._definitions[index] = updated
        return updated

    def to_document(self) -> dict[str, Any]:
        return {
            "schemaVersion": GENERATION_SCHEMA_VERSION,
            "catalogId": self.catalog_id,
            "itemContract": current_item_contract_snapshot(),
            "knownDefinitionAliases": dict(
                sorted(self._known_definition_aliases.items())
            ),
            "definitions": [value.to_mapping() for value in self._definitions],
        }

    def save(self, path: Path) -> None:
        path.write_text(
            json.dumps(self.to_document(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _validate(self) -> None:
        keys = [value.item_key for value in self._definitions]
        ids = [value.definition_id for value in self._definitions]
        if len(keys) != len(set(keys)):
            raise DailyItemGenerationError("generated itemKey values must be unique")
        if len(ids) != len(set(ids)):
            raise DailyItemGenerationError("generated definition ids must be unique")
        normalized_aliases: set[str] = set()
        for alias, definition_id in self._known_definition_aliases.items():
            value = _non_empty_string(alias, "known definition alias")
            _non_empty_string(definition_id, "known definition id")
            normalized = _normalize_lookup(value)
            if normalized in normalized_aliases:
                raise DailyItemGenerationError(
                    "known definition aliases must be unique after normalization"
                )
            normalized_aliases.add(normalized)
        self._lookup_ownership()

    def _lookup_ownership(self) -> dict[str, str]:
        owners: dict[str, str] = {}
        for entry in self._definitions:
            for term in (
                entry.item_key,
                entry.definition_id,
                entry.item["name"],
                *entry.aliases,
            ):
                normalized = _normalize_lookup(term)
                owner = owners.get(normalized)
                if owner is not None and owner != entry.item_key:
                    raise DailyItemGenerationError(
                        f"duplicate lookup term belongs to {owner} and {entry.item_key}: {term}"
                    )
                owners[normalized] = entry.item_key
        return owners


@dataclass(frozen=True, slots=True)
class DailyItemGenerationResolution:
    status: str
    entry: DailyItemDefinitionEntry | None
    definition: Mapping[str, Any] | None
    catalog: DailyItemDefinitionCatalog
    reference_table: DailyItemReferenceTable
    reason: str | None = None
    adapter_called: bool = False


def resolve_daily_item_definition(
    catalog: DailyItemDefinitionCatalog,
    reference_table: DailyItemReferenceTable,
    request: DailyItemGenerationRequest,
    adapter: DailyItemGenerationAdapter | None = None,
    *,
    known_definitions: tuple[Mapping[str, Any], ...] = (),
    minimum_confidence: float = MINIMUM_GENERATION_CONFIDENCE,
) -> DailyItemGenerationResolution:
    """Return a reusable definition, making at most one AI call on a miss."""

    known = _known_definition_match(known_definitions, (request.observed_text,))
    if known is not None:
        return DailyItemGenerationResolution(
            status="known_definition",
            entry=None,
            definition=known,
            catalog=catalog,
            reference_table=reference_table,
        )
    known_alias_id = catalog.known_definition_id(request.observed_text)
    if known_alias_id is not None:
        known_alias_definition = _known_definition_by_id(
            known_definitions,
            known_alias_id,
        )
        if known_alias_definition is None:
            return DailyItemGenerationResolution(
                status="rejected",
                entry=None,
                definition=None,
                catalog=catalog,
                reference_table=reference_table,
                reason=(
                    "known definition alias points to a missing or non-daily definition: "
                    f"{known_alias_id}"
                ),
            )
        return DailyItemGenerationResolution(
            status="known_alias_hit",
            entry=None,
            definition=known_alias_definition,
            catalog=catalog,
            reference_table=reference_table,
        )
    cached = catalog.lookup(request.observed_text)
    if cached is not None:
        consistency_error = _entry_reference_error(cached, reference_table)
        if consistency_error is not None:
            return DailyItemGenerationResolution(
                status="rejected",
                entry=None,
                definition=None,
                catalog=catalog,
                reference_table=reference_table,
                reason=consistency_error,
            )
        return DailyItemGenerationResolution(
            status="cache_hit",
            entry=cached,
            definition=cached.item,
            catalog=catalog,
            reference_table=reference_table,
        )
    if adapter is None or not adapter.available:
        return DailyItemGenerationResolution(
            status="cache_miss",
            entry=None,
            definition=None,
            catalog=catalog,
            reference_table=reference_table,
            reason="no enabled daily item generation adapter",
        )

    try:
        result = adapter.generate(request)
        candidate = DailyItemGenerationCandidate.from_output(
            result.output,
            minimum_confidence=minimum_confidence,
        )
        working_catalog = catalog.clone()
        working_references = DailyItemReferenceTable.from_document(
            reference_table.to_document()
        )
        known = _known_definition_match(
            known_definitions,
            (candidate.item_key, candidate.canonical_name, *candidate.aliases),
        )
        if known is not None:
            for alias in _deduplicated_terms(
                (request.observed_text, *candidate.aliases)
            ):
                working_catalog.add_known_definition_alias(
                    alias,
                    str(known["definitionId"]),
                )
            return DailyItemGenerationResolution(
                status="known_definition_reused",
                entry=None,
                definition=known,
                catalog=working_catalog,
                reference_table=working_references,
                adapter_called=True,
            )
        equivalent = _candidate_catalog_match(working_catalog, candidate)
        if equivalent is not None:
            consistency_error = _entry_reference_error(
                equivalent,
                working_references,
            )
            if consistency_error is not None:
                raise DailyItemGenerationError(consistency_error)
            updated = working_catalog.add_aliases(
                equivalent.item_key,
                _deduplicated_terms((request.observed_text, *candidate.aliases)),
            )
            return DailyItemGenerationResolution(
                status="equivalent_reused",
                entry=updated,
                definition=updated.item,
                catalog=working_catalog,
                reference_table=working_references,
                adapter_called=True,
            )

        reference = _candidate_reference_match(working_references, candidate)
        if reference is None:
            reference = _reference_from_generation_candidate(
                candidate,
                adapter,
                result.metrics,
                working_references,
            )
            working_references.add(reference)
        elif _normalize_lookup(reference.unit_description) != _normalize_lookup(
            candidate.unit_description
        ):
            raise DailyItemGenerationError(
                "cached price/weight reference uses a different unit"
            )

        entry = _entry_from_candidate(
            candidate,
            request,
            reference,
            adapter,
            result.metrics,
        )
        working_catalog.add(entry)
    except Exception as error:
        return DailyItemGenerationResolution(
            status="rejected",
            entry=None,
            definition=None,
            catalog=catalog,
            reference_table=reference_table,
            reason=f"{type(error).__name__}: {error}",
            adapter_called=True,
        )

    return DailyItemGenerationResolution(
        status="model_accepted",
        entry=entry,
        definition=entry.item,
        catalog=working_catalog,
        reference_table=working_references,
        adapter_called=True,
    )


def render_daily_item_definition_markdown(
    catalog: DailyItemDefinitionCatalog,
) -> str:
    lines = [
        "# AI 生成日常物品定义目录",
        "",
        "本目录只保存可复用的日常物品定义，不创建具体实例，不确认物品来源，也不表示玩家已经获得物品。",
        "",
        "| definitionId | 名称 | 类别 | 单位 | 克朗 | 重量 | 可堆叠 | 来源 |",
        "| --- | --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    if not catalog.definitions:
        lines.append("| - | 暂无已生成定义 | - | - | - | - | - | - |")
    for entry in catalog.definitions:
        item = entry.item
        lines.append(
            "| `{}` | {} | `{}` | {} | {} | {} g | {} | `{}` |".format(
                item["id"],
                item["name"],
                item["category"],
                entry.unit_description,
                item["valueCrown"],
                item["unitWeightGrams"],
                "是" if item["stackable"] else "否",
                entry.source_status,
            )
        )
    if catalog.known_definition_aliases:
        lines.extend(
            [
                "",
                "## 正式定义别名缓存",
                "",
                "| 输入说法 | 正式 definitionId |",
                "| --- | --- |",
            ]
        )
        for alias, definition_id in sorted(catalog.known_definition_aliases.items()):
            lines.append(f"| {alias} | `{definition_id}` |")
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 新短语先查询本目录；命中后不调用 AI。",
            "- 未命中时一次候选同时包含名称、描述、类别、堆叠、美元估价和克重；克朗由程序按苹果基准计算。",
            "- 内嵌 `item` 始终通过当前正式物品契约校验；契约或生成字段策略变化后旧目录会拒绝加载，必须显式迁移或重建。",
            "- AI 只可提出受约束的装备规格和通用消耗效果候选；高风险、受限风险和重大效果会被程序拒绝。",
            "- 消耗效果始终标记为需要对应领域再次裁决，不会由物品目录直接修改人物、地点、物品或世界状态。",
            "- 本目录不处理实例创建、来源校验、玩家获得、库存转移或事件提交。",
            "",
        ]
    )
    return "\n".join(lines)


def _entry_from_candidate(
    candidate: DailyItemGenerationCandidate,
    request: DailyItemGenerationRequest,
    reference: DailyItemReference,
    adapter: DailyItemGenerationAdapter,
    metrics: ReferenceCallMetrics,
) -> DailyItemDefinitionEntry:
    definition_id = f"daily_{candidate.category}_{candidate.item_key}"
    payload = {
        "id": definition_id,
        "definitionId": definition_id,
        "name": candidate.canonical_name,
        "description": candidate.description,
        "category": candidate.category,
        "isPlotItem": False,
        "quantity": 1,
        "stackable": candidate.stackable,
        "unitWeightGrams": reference.unit_weight_grams,
        "valueCrown": reference.value_crown,
        "condition": None,
        "durability": None,
        "containerId": None,
        "locationId": None,
        "properties": dict(candidate.properties),
    }
    definition = ItemDefinition.from_payload(payload)
    aliases = _deduplicated_terms((request.observed_text, *candidate.aliases))
    aliases = tuple(
        value
        for value in aliases
        if _normalize_lookup(value) != _normalize_lookup(candidate.canonical_name)
    )
    return DailyItemDefinitionEntry.from_mapping(
        {
            "itemKey": candidate.item_key,
            "aliases": list(aliases),
            "unitDescription": candidate.unit_description,
            "sourceStatus": "model_generated",
            "confidence": candidate.confidence,
            "assumptions": list(candidate.assumptions),
            "modelAudit": _model_audit(adapter, metrics).to_mapping(),
            "item": definition.to_payload(),
        }
    )


def _reference_from_generation_candidate(
    candidate: DailyItemGenerationCandidate,
    adapter: DailyItemGenerationAdapter,
    metrics: ReferenceCallMetrics,
    table: DailyItemReferenceTable,
) -> DailyItemReference:
    benchmark_usd = table.benchmark.estimated_retail_usd
    return DailyItemReference(
        item_key=candidate.item_key,
        name=candidate.canonical_name,
        aliases=candidate.aliases,
        unit_description=candidate.unit_description,
        estimated_retail_usd=candidate.estimated_retail_usd,
        price_ratio_to_apple=price_ratio_to_apple(
            candidate.estimated_retail_usd,
            benchmark_usd,
        ),
        value_crown=crown_value_from_usd(
            candidate.estimated_retail_usd,
            benchmark_usd,
        ),
        unit_weight_grams=candidate.unit_weight_grams,
        source_status="model_estimate",
        confidence=candidate.confidence,
        assumptions=candidate.assumptions,
        model_audit=_model_audit(adapter, metrics),
    )


def _candidate_catalog_match(
    catalog: DailyItemDefinitionCatalog,
    candidate: DailyItemGenerationCandidate,
) -> DailyItemDefinitionEntry | None:
    for term in (candidate.item_key, candidate.canonical_name, *candidate.aliases):
        match = catalog.lookup(term)
        if match is not None:
            return match
    return None


def _known_definition_match(
    definitions: tuple[Mapping[str, Any], ...],
    terms: tuple[str, ...],
) -> Mapping[str, Any] | None:
    wanted = {_normalize_lookup(term) for term in terms}
    for raw in definitions:
        try:
            definition = ItemDefinition.from_payload(raw)
        except ValueError as error:
            raise DailyItemGenerationError(
                f"known item definition is invalid: {error}"
            ) from error
        item = definition.to_payload()
        if item["isPlotItem"] or item["category"] not in DAILY_ITEM_CATEGORIES:
            continue
        identities = {
            _normalize_lookup(item["id"]),
            _normalize_lookup(item["definitionId"]),
            _normalize_lookup(item["name"]),
        }
        if wanted.intersection(identities):
            return item
    return None


def _known_definition_by_id(
    definitions: tuple[Mapping[str, Any], ...],
    definition_id: str,
) -> Mapping[str, Any] | None:
    normalized = _normalize_lookup(definition_id)
    for raw in definitions:
        try:
            definition = ItemDefinition.from_payload(raw)
        except ValueError as error:
            raise DailyItemGenerationError(
                f"known item definition is invalid: {error}"
            ) from error
        item = definition.to_payload()
        if (
            _normalize_lookup(item["definitionId"]) == normalized
            and not item["isPlotItem"]
            and item["category"] in DAILY_ITEM_CATEGORIES
        ):
            return item
    return None


def _candidate_reference_match(
    table: DailyItemReferenceTable,
    candidate: DailyItemGenerationCandidate,
) -> DailyItemReference | None:
    for term in (candidate.item_key, candidate.canonical_name, *candidate.aliases):
        match = table.lookup(term)
        if match is not None:
            return match
    return None


def _entry_reference_error(
    entry: DailyItemDefinitionEntry,
    table: DailyItemReferenceTable,
) -> str | None:
    reference = table.lookup(entry.item_key)
    if reference is None:
        return f"generated definition has no price/weight reference: {entry.item_key}"
    if _normalize_lookup(reference.unit_description) != _normalize_lookup(
        entry.unit_description
    ):
        return f"generated definition unit differs from its reference: {entry.item_key}"
    if entry.item["valueCrown"] != reference.value_crown:
        return f"generated definition price differs from its reference: {entry.item_key}"
    if entry.item["unitWeightGrams"] != reference.unit_weight_grams:
        return f"generated definition weight differs from its reference: {entry.item_key}"
    return None


def _model_audit(
    adapter: DailyItemGenerationAdapter,
    metrics: ReferenceCallMetrics,
) -> ModelAudit:
    return ModelAudit(
        provider=adapter.provider_name,
        model=adapter.model_name,
        prompt_tokens=metrics.prompt_tokens,
        completion_tokens=metrics.completion_tokens,
        total_tokens=metrics.total_tokens,
        latency_ms=metrics.latency_ms,
    )


def _audit_from_mapping(value: Mapping[str, Any]) -> ModelAudit:
    if set(value) != _AUDIT_FIELDS:
        raise DailyItemGenerationError("modelAudit fields are invalid")
    try:
        return ModelAudit.from_mapping(value)
    except ValueError as error:
        raise DailyItemGenerationError(str(error)) from error


def _positive_decimal(
    value: object,
    label: str,
    *,
    maximum: Decimal | None = None,
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float, str)):
        raise DailyItemGenerationError(f"{label} must be a positive number")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise DailyItemGenerationError(f"{label} must be a positive number") from error
    if not result.is_finite() or result <= 0:
        raise DailyItemGenerationError(f"{label} must be a positive number")
    if maximum is not None and result > maximum:
        raise DailyItemGenerationError(f"{label} exceeds the supported maximum")
    return result


def _confidence(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0 <= float(value) <= 1
    ):
        raise DailyItemGenerationError("confidence must be between 0 and 1")
    return float(value)


def _normalize_lookup(value: object) -> str:
    if type(value) is not str:
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return " ".join(normalized.split())


def _non_empty_string(value: object, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise DailyItemGenerationError(f"{label} must be a non-empty string")
    return value.strip()


def _string_tuple(
    value: object,
    label: str,
    *,
    maximum: int,
    item_maximum: int = 120,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise DailyItemGenerationError(
            f"{label} must be an array of at most {maximum} strings"
        )
    result: list[str] = []
    normalized: set[str] = set()
    for item in value:
        text = _non_empty_string(item, label)
        if len(text) > item_maximum:
            raise DailyItemGenerationError(
                f"each {label} value cannot exceed {item_maximum} characters"
            )
        key = _normalize_lookup(text)
        if key in normalized:
            raise DailyItemGenerationError(f"{label} values must be unique")
        normalized.add(key)
        result.append(text)
    return tuple(result)


def _deduplicated_terms(values: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _non_empty_string(value, "alias")
        normalized = _normalize_lookup(text)
        if normalized not in seen:
            seen.add(normalized)
            result.append(text)
    return tuple(result)


__all__ = [
    "DAILY_ITEM_CATEGORIES",
    "GENERATION_FIELD_POLICIES",
    "DailyItemDefinitionCatalog",
    "DailyItemDefinitionEntry",
    "DailyItemGenerationAdapter",
    "DailyItemGenerationAdapterResult",
    "DailyItemGenerationCandidate",
    "DailyItemGenerationError",
    "DailyItemGenerationRequest",
    "DailyItemGenerationResolution",
    "assert_generation_contract_sync",
    "current_item_contract_snapshot",
    "generation_policy_fingerprint",
    "render_daily_item_definition_markdown",
    "resolve_daily_item_definition",
]
