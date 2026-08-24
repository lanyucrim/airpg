"""Validated design-time price and weight references for ordinary items.

The reference table is static catalog input, not runtime state.  AI may propose
an approximate US retail price and physical unit weight, but this module owns
validation, crown conversion, cache lookup, and the accepted record shape.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Protocol
import unicodedata

from trpg_server.items.contract import record_field_error


REFERENCE_SCHEMA_VERSION = 1
APPLE_GAME_PRICE_CROWN = 10
MINIMUM_ESTIMATE_CONFIDENCE = 0.55
MAX_RETAIL_USD = Decimal("100000")
MAX_UNIT_WEIGHT_GRAMS = 1_000_000

_ITEM_KEY = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
_ROOT_FIELDS = frozenset(
    {
        "schemaVersion",
        "tableId",
        "currencyUnit",
        "benchmarkItemKey",
        "references",
    }
)
_REFERENCE_FIELDS = frozenset(
    {
        "itemKey",
        "name",
        "aliases",
        "unitDescription",
        "estimatedRetailUsd",
        "priceRatioToApple",
        "valueCrown",
        "unitWeightGrams",
        "sourceStatus",
        "confidence",
        "assumptions",
        "modelAudit",
    }
)
_MODEL_AUDIT_FIELDS = frozenset(
    {
        "provider",
        "model",
        "promptTokens",
        "completionTokens",
        "totalTokens",
        "latencyMs",
    }
)
_SOURCE_STATUSES = frozenset(
    {"user_benchmark", "reviewed_estimate", "model_estimate"}
)


class DailyItemReferenceError(ValueError):
    """Raised when a reference candidate or table violates the contract."""


@dataclass(frozen=True, slots=True)
class DailyItemReferenceRequest:
    item_key: str
    name: str
    unit_description: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        item_key = self.item_key.strip().lower()
        name = self.name.strip()
        unit_description = self.unit_description.strip()
        aliases = tuple(alias.strip() for alias in self.aliases)
        if _ITEM_KEY.fullmatch(item_key) is None:
            raise DailyItemReferenceError(
                "item_key must use lowercase ASCII words separated by underscores"
            )
        if not name:
            raise DailyItemReferenceError("name cannot be empty")
        if not unit_description:
            raise DailyItemReferenceError("unit_description cannot be empty")
        if any(not alias for alias in aliases):
            raise DailyItemReferenceError("aliases cannot contain empty values")
        if len({_normalize_lookup(alias) for alias in aliases}) != len(aliases):
            raise DailyItemReferenceError("aliases must be unique")
        object.__setattr__(self, "item_key", item_key)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "unit_description", unit_description)
        object.__setattr__(self, "aliases", aliases)


@dataclass(frozen=True, slots=True)
class ReferenceCallMetrics:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ItemReferenceAdapterResult:
    output: Mapping[str, Any]
    metrics: ReferenceCallMetrics = ReferenceCallMetrics()


class ItemReferenceAdapter(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def estimate(
        self,
        request: DailyItemReferenceRequest,
    ) -> ItemReferenceAdapterResult: ...


@dataclass(frozen=True, slots=True)
class ItemReferenceCandidate:
    item_key: str
    name: str
    aliases: tuple[str, ...]
    unit_description: str
    estimated_retail_usd: Decimal
    unit_weight_grams: int
    confidence: float
    assumptions: tuple[str, ...]

    @classmethod
    def from_output(
        cls,
        output: Mapping[str, Any],
        request: DailyItemReferenceRequest,
        *,
        minimum_confidence: float = MINIMUM_ESTIMATE_CONFIDENCE,
    ) -> "ItemReferenceCandidate":
        if not isinstance(output, Mapping):
            raise DailyItemReferenceError("model output must be an object")
        expected = {
            "schemaVersion",
            "itemKey",
            "name",
            "unitDescription",
            "estimatedRetailUsd",
            "unitWeightGrams",
            "confidence",
            "assumptions",
        }
        if set(output) != expected:
            raise DailyItemReferenceError(
                "model output fields do not match the item reference candidate contract"
            )
        if output["schemaVersion"] != REFERENCE_SCHEMA_VERSION:
            raise DailyItemReferenceError("unsupported candidate schemaVersion")
        if output["itemKey"] != request.item_key:
            raise DailyItemReferenceError("candidate itemKey does not match request")
        if _normalize_lookup(output["name"]) != _normalize_lookup(request.name):
            raise DailyItemReferenceError("candidate name does not match request")
        if _normalize_lookup(output["unitDescription"]) != _normalize_lookup(
            request.unit_description
        ):
            raise DailyItemReferenceError(
                "candidate unitDescription does not match request"
            )
        price = _positive_decimal(
            output["estimatedRetailUsd"],
            "estimatedRetailUsd",
            maximum=MAX_RETAIL_USD,
        )
        weight = output["unitWeightGrams"]
        if type(weight) is not int or not 1 <= weight <= MAX_UNIT_WEIGHT_GRAMS:
            raise DailyItemReferenceError(
                "unitWeightGrams must be an integer between 1 and 1000000"
            )
        confidence = output["confidence"]
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            raise DailyItemReferenceError("confidence must be between 0 and 1")
        if float(confidence) < minimum_confidence:
            raise DailyItemReferenceError(
                f"confidence is below the acceptance threshold {minimum_confidence}"
            )
        assumptions_value = output["assumptions"]
        if not isinstance(assumptions_value, list) or len(assumptions_value) > 8:
            raise DailyItemReferenceError("assumptions must be an array of at most 8 strings")
        assumptions: list[str] = []
        for value in assumptions_value:
            if type(value) is not str or not value.strip() or len(value.strip()) > 200:
                raise DailyItemReferenceError(
                    "each assumption must be a non-empty string of at most 200 characters"
                )
            assumptions.append(value.strip())
        return cls(
            item_key=request.item_key,
            name=request.name,
            aliases=request.aliases,
            unit_description=request.unit_description,
            estimated_retail_usd=price,
            unit_weight_grams=weight,
            confidence=float(confidence),
            assumptions=tuple(assumptions),
        )


@dataclass(frozen=True, slots=True)
class ModelAudit:
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModelAudit":
        if set(value) != _MODEL_AUDIT_FIELDS:
            raise DailyItemReferenceError("modelAudit fields are invalid")
        provider = _non_empty_string(value["provider"], "modelAudit.provider")
        model = _non_empty_string(value["model"], "modelAudit.model")
        metrics = {
            key: _nullable_non_negative_int(value[key], f"modelAudit.{key}")
            for key in (
                "promptTokens",
                "completionTokens",
                "totalTokens",
                "latencyMs",
            )
        }
        return cls(
            provider=provider,
            model=model,
            prompt_tokens=metrics["promptTokens"],
            completion_tokens=metrics["completionTokens"],
            total_tokens=metrics["totalTokens"],
            latency_ms=metrics["latencyMs"],
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "totalTokens": self.total_tokens,
            "latencyMs": self.latency_ms,
        }


@dataclass(frozen=True, slots=True)
class DailyItemReference:
    item_key: str
    name: str
    aliases: tuple[str, ...]
    unit_description: str
    estimated_retail_usd: Decimal
    price_ratio_to_apple: Decimal
    value_crown: int
    unit_weight_grams: int
    source_status: str
    confidence: float
    assumptions: tuple[str, ...]
    model_audit: ModelAudit | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DailyItemReference":
        if not isinstance(value, Mapping) or set(value) != _REFERENCE_FIELDS:
            raise DailyItemReferenceError("reference fields are invalid")
        request = DailyItemReferenceRequest(
            item_key=_non_empty_string(value["itemKey"], "itemKey"),
            name=_non_empty_string(value["name"], "name"),
            unit_description=_non_empty_string(
                value["unitDescription"], "unitDescription"
            ),
            aliases=_string_tuple(value["aliases"], "aliases"),
        )
        price = _positive_decimal(
            value["estimatedRetailUsd"],
            "estimatedRetailUsd",
            maximum=MAX_RETAIL_USD,
        )
        ratio = _positive_decimal(value["priceRatioToApple"], "priceRatioToApple")
        crown = value["valueCrown"]
        if type(crown) is not int or crown < 1:
            raise DailyItemReferenceError("valueCrown must be a positive integer")
        weight = value["unitWeightGrams"]
        if type(weight) is not int or not 1 <= weight <= MAX_UNIT_WEIGHT_GRAMS:
            raise DailyItemReferenceError(
                "unitWeightGrams must be an integer between 1 and 1000000"
            )
        source_status = value["sourceStatus"]
        if source_status not in _SOURCE_STATUSES:
            raise DailyItemReferenceError("sourceStatus is invalid")
        confidence = value["confidence"]
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            raise DailyItemReferenceError("confidence must be between 0 and 1")
        assumptions = _string_tuple(value["assumptions"], "assumptions", maximum=8)
        audit_value = value["modelAudit"]
        if audit_value is not None and not isinstance(audit_value, Mapping):
            raise DailyItemReferenceError("modelAudit must be null or an object")
        audit = ModelAudit.from_mapping(audit_value) if audit_value is not None else None
        if source_status == "model_estimate" and audit is None:
            raise DailyItemReferenceError("model estimates require modelAudit")
        if source_status != "model_estimate" and audit is not None:
            raise DailyItemReferenceError("non-model references cannot carry modelAudit")
        return cls(
            item_key=request.item_key,
            name=request.name,
            aliases=request.aliases,
            unit_description=request.unit_description,
            estimated_retail_usd=price,
            price_ratio_to_apple=ratio,
            value_crown=crown,
            unit_weight_grams=weight,
            source_status=source_status,
            confidence=float(confidence),
            assumptions=assumptions,
            model_audit=audit,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "itemKey": self.item_key,
            "name": self.name,
            "aliases": list(self.aliases),
            "unitDescription": self.unit_description,
            "estimatedRetailUsd": _decimal_json_number(self.estimated_retail_usd),
            "priceRatioToApple": _decimal_json_number(self.price_ratio_to_apple),
            "valueCrown": self.value_crown,
            "unitWeightGrams": self.unit_weight_grams,
            "sourceStatus": self.source_status,
            "confidence": self.confidence,
            "assumptions": list(self.assumptions),
            "modelAudit": self.model_audit.to_mapping() if self.model_audit else None,
        }


class DailyItemReferenceTable:
    """Validated mutable cache used by design-time item tooling."""

    def __init__(
        self,
        *,
        table_id: str,
        currency_unit: str,
        benchmark_item_key: str,
        references: tuple[DailyItemReference, ...],
    ) -> None:
        self.table_id = _non_empty_string(table_id, "tableId")
        self.currency_unit = _non_empty_string(currency_unit, "currencyUnit")
        self.benchmark_item_key = benchmark_item_key
        self._references = list(references)
        self._validate()

    @property
    def references(self) -> tuple[DailyItemReference, ...]:
        return tuple(self._references)

    @property
    def benchmark(self) -> DailyItemReference:
        return next(
            value
            for value in self._references
            if value.item_key == self.benchmark_item_key
        )

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "DailyItemReferenceTable":
        if not isinstance(document, Mapping) or set(document) != _ROOT_FIELDS:
            raise DailyItemReferenceError("reference table root fields are invalid")
        if document["schemaVersion"] != REFERENCE_SCHEMA_VERSION:
            raise DailyItemReferenceError("unsupported reference table schemaVersion")
        raw_references = document["references"]
        if not isinstance(raw_references, list) or not raw_references:
            raise DailyItemReferenceError("references must be a non-empty array")
        references = tuple(
            DailyItemReference.from_mapping(value) for value in raw_references
        )
        return cls(
            table_id=document["tableId"],
            currency_unit=document["currencyUnit"],
            benchmark_item_key=document["benchmarkItemKey"],
            references=references,
        )

    @classmethod
    def load(cls, path: Path) -> "DailyItemReferenceTable":
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DailyItemReferenceError(
                f"cannot read daily item reference table: {path}"
            ) from error
        return cls.from_document(document)

    def lookup(self, query: str) -> DailyItemReference | None:
        normalized = _normalize_lookup(query)
        if not normalized:
            return None
        for reference in self._references:
            terms = (reference.item_key, reference.name, *reference.aliases)
            if normalized in {_normalize_lookup(term) for term in terms}:
                return reference
        return None

    def add(self, reference: DailyItemReference) -> None:
        existing_keys = {value.item_key for value in self._references}
        if reference.item_key in existing_keys:
            raise DailyItemReferenceError(
                f"reference already exists: {reference.item_key}"
            )
        occupied = self._lookup_ownership()
        for term in (reference.item_key, reference.name, *reference.aliases):
            owner = occupied.get(_normalize_lookup(term))
            if owner is not None:
                raise DailyItemReferenceError(
                    f"reference lookup term already belongs to {owner}: {term}"
                )
        self._validate_reference_calculation(reference)
        self._references.append(reference)
        self._references.sort(key=lambda value: value.item_key)

    def to_document(self) -> dict[str, Any]:
        return {
            "schemaVersion": REFERENCE_SCHEMA_VERSION,
            "tableId": self.table_id,
            "currencyUnit": self.currency_unit,
            "benchmarkItemKey": self.benchmark_item_key,
            "references": [value.to_mapping() for value in self._references],
        }

    def save(self, path: Path) -> None:
        path.write_text(
            json.dumps(self.to_document(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _validate(self) -> None:
        if self.currency_unit != "crown":
            raise DailyItemReferenceError("currencyUnit must be crown")
        if _ITEM_KEY.fullmatch(self.benchmark_item_key) is None:
            raise DailyItemReferenceError("benchmarkItemKey is invalid")
        keys = [value.item_key for value in self._references]
        if len(keys) != len(set(keys)):
            raise DailyItemReferenceError("reference itemKey values must be unique")
        if self.benchmark_item_key not in keys:
            raise DailyItemReferenceError("benchmarkItemKey does not exist")
        benchmark = self.benchmark
        if benchmark.value_crown != APPLE_GAME_PRICE_CROWN:
            raise DailyItemReferenceError("apple benchmark must be exactly 10 crowns")
        if benchmark.price_ratio_to_apple != Decimal("1"):
            raise DailyItemReferenceError("apple benchmark ratio must be exactly 1")
        if benchmark.source_status != "user_benchmark":
            raise DailyItemReferenceError("apple benchmark must be user_benchmark")
        self._lookup_ownership()
        for reference in self._references:
            self._validate_reference_calculation(reference)

    def _lookup_ownership(self) -> dict[str, str]:
        owners: dict[str, str] = {}
        for reference in self._references:
            for term in (reference.item_key, reference.name, *reference.aliases):
                normalized = _normalize_lookup(term)
                owner = owners.get(normalized)
                if owner is not None and owner != reference.item_key:
                    raise DailyItemReferenceError(
                        f"duplicate lookup term belongs to {owner} and {reference.item_key}: {term}"
                    )
                owners[normalized] = reference.item_key
        return owners

    def _validate_reference_calculation(self, reference: DailyItemReference) -> None:
        benchmark = self.benchmark
        expected_ratio = price_ratio_to_apple(
            reference.estimated_retail_usd,
            benchmark.estimated_retail_usd,
        )
        if reference.price_ratio_to_apple != expected_ratio:
            raise DailyItemReferenceError(
                f"priceRatioToApple is not program-derived for {reference.item_key}"
            )
        expected_crown = crown_value_from_usd(
            reference.estimated_retail_usd,
            benchmark.estimated_retail_usd,
        )
        if reference.value_crown != expected_crown:
            raise DailyItemReferenceError(
                f"valueCrown is not program-derived for {reference.item_key}"
            )


@dataclass(frozen=True, slots=True)
class DailyItemReferenceResolution:
    status: str
    reference: DailyItemReference | None
    reason: str | None = None
    adapter_called: bool = False


def resolve_daily_item_reference(
    table: DailyItemReferenceTable,
    request: DailyItemReferenceRequest,
    adapter: ItemReferenceAdapter | None = None,
    *,
    minimum_confidence: float = MINIMUM_ESTIMATE_CONFIDENCE,
) -> DailyItemReferenceResolution:
    """Return a cache hit or make at most one validated model request."""

    cached = table.lookup(request.item_key) or table.lookup(request.name)
    if cached is not None:
        if _normalize_lookup(cached.unit_description) != _normalize_lookup(
            request.unit_description
        ):
            return DailyItemReferenceResolution(
                status="unit_mismatch",
                reference=None,
                reason=(
                    "cached item uses a different unit; choose that unit or a new itemKey"
                ),
            )
        return DailyItemReferenceResolution(status="cache_hit", reference=cached)
    if adapter is None or not adapter.available:
        return DailyItemReferenceResolution(
            status="cache_miss",
            reference=None,
            reason="no enabled item reference adapter",
        )
    try:
        result = adapter.estimate(request)
        candidate = ItemReferenceCandidate.from_output(
            result.output,
            request,
            minimum_confidence=minimum_confidence,
        )
        reference = reference_from_candidate(candidate, table, adapter, result.metrics)
        table.add(reference)
    except Exception as error:
        return DailyItemReferenceResolution(
            status="rejected",
            reference=None,
            reason=f"{type(error).__name__}: {error}",
            adapter_called=True,
        )
    return DailyItemReferenceResolution(
        status="model_accepted",
        reference=reference,
        adapter_called=True,
    )


def reference_from_candidate(
    candidate: ItemReferenceCandidate,
    table: DailyItemReferenceTable,
    adapter: ItemReferenceAdapter,
    metrics: ReferenceCallMetrics,
) -> DailyItemReference:
    ratio = price_ratio_to_apple(
        candidate.estimated_retail_usd,
        table.benchmark.estimated_retail_usd,
    )
    return DailyItemReference(
        item_key=candidate.item_key,
        name=candidate.name,
        aliases=candidate.aliases,
        unit_description=candidate.unit_description,
        estimated_retail_usd=candidate.estimated_retail_usd,
        price_ratio_to_apple=ratio,
        value_crown=crown_value_from_usd(
            candidate.estimated_retail_usd,
            table.benchmark.estimated_retail_usd,
        ),
        unit_weight_grams=candidate.unit_weight_grams,
        source_status="model_estimate",
        confidence=candidate.confidence,
        assumptions=candidate.assumptions,
        model_audit=ModelAudit(
            provider=adapter.provider_name,
            model=adapter.model_name,
            prompt_tokens=metrics.prompt_tokens,
            completion_tokens=metrics.completion_tokens,
            total_tokens=metrics.total_tokens,
            latency_ms=metrics.latency_ms,
        ),
    )


def crown_value_from_usd(
    estimated_retail_usd: Decimal | int | float | str,
    apple_retail_usd: Decimal | int | float | str,
) -> int:
    target = _positive_decimal(
        estimated_retail_usd,
        "estimated_retail_usd",
        maximum=MAX_RETAIL_USD,
    )
    apple = _positive_decimal(
        apple_retail_usd,
        "apple_retail_usd",
        maximum=MAX_RETAIL_USD,
    )
    converted = Decimal(APPLE_GAME_PRICE_CROWN) * target / apple
    return max(1, int(converted.quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


def price_ratio_to_apple(
    estimated_retail_usd: Decimal | int | float | str,
    apple_retail_usd: Decimal | int | float | str,
) -> Decimal:
    target = _positive_decimal(estimated_retail_usd, "estimated_retail_usd")
    apple = _positive_decimal(apple_retail_usd, "apple_retail_usd")
    return (target / apple).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def with_reference_measurements(
    item_record: Mapping[str, Any],
    reference: DailyItemReference,
) -> dict[str, Any]:
    """Fill only unknown price/weight fields in an existing 15-field record."""

    record = deepcopy(dict(item_record))
    error = record_field_error(record, path="item")
    if error is not None:
        raise DailyItemReferenceError(error)
    if record["valueCrown"] is None:
        record["valueCrown"] = reference.value_crown
    if record["unitWeightGrams"] is None:
        record["unitWeightGrams"] = reference.unit_weight_grams
    return record


def render_daily_item_reference_markdown(
    table: DailyItemReferenceTable,
) -> str:
    lines = [
        "# 灰港日常物品价格与重量参考表",
        "",
        "苹果是固定购买力基准：一个中等苹果为 10 克朗。美元价格只用于计算相对比例，不是灰港内流通货币。",
        "每条重量都对应明确的单件计量单位；命中缓存时价格和重量均不再调用 AI。",
        "",
        "| itemKey | 物品 | 计价/计重单位 | 估算美元价 | 苹果价格比 | 克朗 | 单件重量 | 来源 | 置信度 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | ---: |",
    ]
    for reference in table.references:
        lines.append(
            "| `{}` | {} | {} | ${} | {} | {} | {} g | `{}` | {:.2f} |".format(
                reference.item_key,
                reference.name,
                reference.unit_description,
                _decimal_display(reference.estimated_retail_usd),
                _decimal_display(reference.price_ratio_to_apple),
                reference.value_crown,
                reference.unit_weight_grams,
                reference.source_status,
                reference.confidence,
            )
        )
    lines.extend(
        [
            "",
            "## 规则",
            "",
            "- 克朗价由程序按 `10 * 物品美元价 / 苹果美元价` 四舍五入计算，正价物品最低 1 克朗；AI 不直接决定克朗价。",
            "- AI 一次只估算同一计量单位的美元零售价与单件克重；候选经过字段、单位、范围和置信度校验后才能写入 JSON 表。",
            "- `daily-item-references.json` 是机器读取记录；本文件由同一数据生成，用于人工审阅。",
            "- 参考记录不代表物品实例已经出现在世界中，也不进入物品 15 字段之外的运行时状态。",
            "",
        ]
    )
    return "\n".join(lines)


def _positive_decimal(
    value: object,
    label: str,
    *,
    maximum: Decimal | None = None,
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float, str)):
        raise DailyItemReferenceError(f"{label} must be a positive number")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise DailyItemReferenceError(f"{label} must be a positive number") from error
    if not result.is_finite() or result <= 0:
        raise DailyItemReferenceError(f"{label} must be a positive number")
    if maximum is not None and result > maximum:
        raise DailyItemReferenceError(f"{label} exceeds the supported maximum")
    return result


def _normalize_lookup(value: object) -> str:
    if type(value) is not str:
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return " ".join(normalized.split())


def _non_empty_string(value: object, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise DailyItemReferenceError(f"{label} must be a non-empty string")
    return value.strip()


def _string_tuple(
    value: object,
    label: str,
    *,
    maximum: int | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise DailyItemReferenceError(f"{label} must be an array")
    if maximum is not None and len(value) > maximum:
        raise DailyItemReferenceError(f"{label} has too many entries")
    result = tuple(_non_empty_string(item, label) for item in value)
    if len({_normalize_lookup(item) for item in result}) != len(result):
        raise DailyItemReferenceError(f"{label} must contain unique values")
    return result


def _nullable_non_negative_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise DailyItemReferenceError(f"{label} must be null or a non-negative integer")
    return value


def _decimal_json_number(value: Decimal) -> int | float:
    integral = value.to_integral_value()
    if value == integral:
        return int(integral)
    return float(value)


def _decimal_display(value: Decimal) -> str:
    return format(value.normalize(), "f")


__all__ = [
    "APPLE_GAME_PRICE_CROWN",
    "DailyItemReference",
    "DailyItemReferenceError",
    "DailyItemReferenceRequest",
    "DailyItemReferenceResolution",
    "DailyItemReferenceTable",
    "ItemReferenceAdapter",
    "ItemReferenceAdapterResult",
    "ItemReferenceCandidate",
    "ModelAudit",
    "ReferenceCallMetrics",
    "crown_value_from_usd",
    "price_ratio_to_apple",
    "render_daily_item_reference_markdown",
    "resolve_daily_item_reference",
    "with_reference_measurements",
]
