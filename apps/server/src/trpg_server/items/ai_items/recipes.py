"""Strict AI-assisted recipe assessment and reusable cache handling."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Protocol

from trpg_server.items.ai_items.era import EraTechnologyProfile
from trpg_server.items.ai_items.generation import (
    DAILY_ITEM_CATEGORIES,
    DailyItemDefinitionCatalog,
    DailyItemGenerationAdapter,
    DailyItemGenerationRequest,
    resolve_daily_item_definition,
)
from trpg_server.items.ai_items.references import (
    DailyItemReferenceTable,
    ModelAudit,
    ReferenceCallMetrics,
)
from trpg_server.items.contract import item_contract_fingerprint
from trpg_server.items.models import ItemDefinition
from trpg_server.items.recipe_models import (
    RecipeBlueprint,
    RecipeError,
    RecipeIngredient,
    normalize_ingredients,
)


RECIPE_SCHEMA_VERSION = 1
MINIMUM_RECIPE_CONFIDENCE = 0.85
MAX_OUTPUT_QUANTITY = 100
MAX_MASS_INCREASE_RATIO = 1.05
_WHITESPACE = re.compile(r"\s+")


class RecipeAssessmentError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RecipeAssessmentRequest:
    process_text: str
    ingredients: tuple[RecipeIngredient, ...]

    def __post_init__(self) -> None:
        process = _text(self.process_text, "process_text", 500)
        try:
            ingredients = normalize_ingredients(self.ingredients)
        except RecipeError as error:
            raise RecipeAssessmentError(str(error)) from error
        object.__setattr__(self, "process_text", process)
        object.__setattr__(self, "ingredients", ingredients)

    @property
    def recipe_key(self) -> str:
        payload = json.dumps(
            {
                "process": _normalize(self.process_text),
                "ingredients": [value.to_mapping() for value in self.ingredients],
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        return f"recipe_{sha256(payload).hexdigest()[:24]}"


@dataclass(frozen=True, slots=True)
class RecipeAssessmentAdapterResult:
    output: Mapping[str, Any]
    metrics: ReferenceCallMetrics = ReferenceCallMetrics()


class RecipeAssessmentAdapter(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def assess(
        self,
        request: RecipeAssessmentRequest,
        era_profile: EraTechnologyProfile,
        ingredient_definitions: tuple[Mapping[str, Any], ...],
    ) -> RecipeAssessmentAdapterResult: ...


@dataclass(frozen=True, slots=True)
class RecipeAssessmentCandidate:
    output_text: str
    output_quantity: int
    process_summary: str
    era_evidence: tuple[str, ...]
    confidence: float

    @classmethod
    def from_output(
        cls,
        output: Mapping[str, Any],
        request: RecipeAssessmentRequest,
        era_profile: EraTechnologyProfile,
        *,
        minimum_confidence: float = MINIMUM_RECIPE_CONFIDENCE,
    ) -> "RecipeAssessmentCandidate":
        expected = {
            "schemaVersion",
            "decision",
            "ingredients",
            "outputText",
            "outputQuantity",
            "processSummary",
            "eraCompatible",
            "eraEvidence",
            "confidence",
            "rejectionReason",
        }
        if not isinstance(output, Mapping) or set(output) != expected:
            raise RecipeAssessmentError("recipe candidate fields are invalid")
        if output["schemaVersion"] != RECIPE_SCHEMA_VERSION:
            raise RecipeAssessmentError("unsupported recipe candidate schemaVersion")
        candidate_ingredients = _ingredients_from_output(output["ingredients"])
        if candidate_ingredients != request.ingredients:
            raise RecipeAssessmentError("AI changed the exact recipe ingredients")
        decision = output["decision"]
        if decision not in {"accepted", "rejected", "clarify"}:
            raise RecipeAssessmentError("recipe decision is invalid")
        if decision != "accepted":
            reason = output["rejectionReason"]
            if type(reason) is not str or not reason.strip():
                raise RecipeAssessmentError("rejected recipe requires a reason")
            raise RecipeAssessmentError(f"AI recipe decision was {decision}: {reason.strip()}")
        if output["rejectionReason"] is not None:
            raise RecipeAssessmentError("accepted recipe cannot include a rejection reason")
        if output["eraCompatible"] is not True:
            raise RecipeAssessmentError("recipe is incompatible with the campaign era")
        output_text = _text(output["outputText"], "outputText", 200)
        output_quantity = output["outputQuantity"]
        if type(output_quantity) is not int or not 1 <= output_quantity <= MAX_OUTPUT_QUANTITY:
            raise RecipeAssessmentError("outputQuantity is outside the accepted range")
        process_summary = _text(output["processSummary"], "processSummary", 300)
        evidence = _string_tuple(output["eraEvidence"], "eraEvidence", 12, 100)
        if not evidence:
            raise RecipeAssessmentError("accepted recipe requires era evidence")
        technologies = {
            value.technology_id: value for value in era_profile.technologies
        }
        unknown = sorted(set(evidence).difference(technologies))
        if unknown:
            raise RecipeAssessmentError(f"unknown era evidence ids: {unknown}")
        blocked = sorted(
            value for value in evidence if technologies[value].status in {"limited", "unavailable"}
        )
        if blocked:
            raise RecipeAssessmentError(
                f"ordinary recipe relies on limited or unavailable technology: {blocked}"
            )
        confidence = _confidence(output["confidence"])
        if confidence < minimum_confidence:
            raise RecipeAssessmentError(
                f"recipe confidence is below the acceptance threshold {minimum_confidence}"
            )
        return cls(
            output_text=output_text,
            output_quantity=output_quantity,
            process_summary=process_summary,
            era_evidence=evidence,
            confidence=confidence,
        )


@dataclass(frozen=True, slots=True)
class GeneratedRecipeEntry:
    recipe_key: str
    process_text: str
    ingredients: tuple[RecipeIngredient, ...]
    output_definition_id: str
    output_quantity: int
    process_summary: str
    era_evidence: tuple[str, ...]
    confidence: float
    model_audit: ModelAudit

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "GeneratedRecipeEntry":
        expected = {
            "recipeKey",
            "processText",
            "ingredients",
            "outputDefinitionId",
            "outputQuantity",
            "processSummary",
            "eraEvidence",
            "confidence",
            "modelAudit",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise RecipeAssessmentError("generated recipe entry fields are invalid")
        request = RecipeAssessmentRequest(
            process_text=_text(value["processText"], "processText", 500),
            ingredients=_ingredients_from_output(value["ingredients"]),
        )
        recipe_key = _text(value["recipeKey"], "recipeKey", 100)
        if recipe_key != request.recipe_key:
            raise RecipeAssessmentError("generated recipe key is not stable")
        quantity = value["outputQuantity"]
        if type(quantity) is not int or not 1 <= quantity <= MAX_OUTPUT_QUANTITY:
            raise RecipeAssessmentError("generated recipe outputQuantity is invalid")
        confidence = _confidence(value["confidence"])
        if confidence < MINIMUM_RECIPE_CONFIDENCE:
            raise RecipeAssessmentError("generated recipe confidence is too low")
        audit_raw = value["modelAudit"]
        if not isinstance(audit_raw, Mapping):
            raise RecipeAssessmentError("generated recipe requires modelAudit")
        try:
            audit = ModelAudit.from_mapping(audit_raw)
        except ValueError as error:
            raise RecipeAssessmentError(f"modelAudit is invalid: {error}") from error
        return cls(
            recipe_key=recipe_key,
            process_text=request.process_text,
            ingredients=request.ingredients,
            output_definition_id=_text(
                value["outputDefinitionId"], "outputDefinitionId", 120
            ),
            output_quantity=quantity,
            process_summary=_text(value["processSummary"], "processSummary", 300),
            era_evidence=_string_tuple(value["eraEvidence"], "eraEvidence", 12, 100),
            confidence=confidence,
            model_audit=audit,
        )

    @property
    def blueprint(self) -> RecipeBlueprint:
        return RecipeBlueprint(
            recipe_key=self.recipe_key,
            ingredients=self.ingredients,
            output_definition_id=self.output_definition_id,
            output_quantity=self.output_quantity,
            process_summary=self.process_summary,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "recipeKey": self.recipe_key,
            "processText": self.process_text,
            "ingredients": [value.to_mapping() for value in self.ingredients],
            "outputDefinitionId": self.output_definition_id,
            "outputQuantity": self.output_quantity,
            "processSummary": self.process_summary,
            "eraEvidence": list(self.era_evidence),
            "confidence": self.confidence,
            "modelAudit": self.model_audit.to_mapping(),
        }


class GeneratedRecipeCatalog:
    def __init__(
        self,
        *,
        catalog_id: str,
        era_profile_fingerprint: str,
        recipes: tuple[GeneratedRecipeEntry, ...] = (),
    ) -> None:
        self.catalog_id = _text(catalog_id, "catalogId", 100)
        self.era_profile_fingerprint = _text(
            era_profile_fingerprint, "eraProfileFingerprint", 100
        )
        self._recipes = list(recipes)
        keys = [value.recipe_key for value in recipes]
        if len(keys) != len(set(keys)):
            raise RecipeAssessmentError("generated recipe keys must be unique")

    @property
    def recipes(self) -> tuple[GeneratedRecipeEntry, ...]:
        return tuple(self._recipes)

    @classmethod
    def empty(
        cls, catalog_id: str, era_profile: EraTechnologyProfile
    ) -> "GeneratedRecipeCatalog":
        return cls(
            catalog_id=catalog_id,
            era_profile_fingerprint=era_profile.fingerprint,
        )

    @classmethod
    def load(
        cls,
        path: str | Path,
        era_profile: EraTechnologyProfile,
    ) -> "GeneratedRecipeCatalog":
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_document(document, era_profile)

    @classmethod
    def from_document(
        cls,
        document: Mapping[str, Any],
        era_profile: EraTechnologyProfile,
    ) -> "GeneratedRecipeCatalog":
        expected = {
            "schemaVersion",
            "catalogId",
            "eraProfileFingerprint",
            "itemContractFingerprint",
            "recipes",
        }
        if not isinstance(document, Mapping) or set(document) != expected:
            raise RecipeAssessmentError("generated recipe catalog fields are invalid")
        if document["schemaVersion"] != RECIPE_SCHEMA_VERSION:
            raise RecipeAssessmentError("unsupported generated recipe schemaVersion")
        if document["eraProfileFingerprint"] != era_profile.fingerprint:
            raise RecipeAssessmentError("generated recipe catalog uses a stale era profile")
        if document["itemContractFingerprint"] != item_contract_fingerprint():
            raise RecipeAssessmentError("generated recipe catalog uses a stale item contract")
        recipes_raw = document["recipes"]
        if not isinstance(recipes_raw, list):
            raise RecipeAssessmentError("recipes must be an array")
        recipes = tuple(
            GeneratedRecipeEntry.from_mapping(value) for value in recipes_raw
        )
        for entry in recipes:
            _validate_entry_era(entry, era_profile)
        return cls(
            catalog_id=_text(document["catalogId"], "catalogId", 100),
            era_profile_fingerprint=era_profile.fingerprint,
            recipes=recipes,
        )

    def clone(self) -> "GeneratedRecipeCatalog":
        return GeneratedRecipeCatalog(
            catalog_id=self.catalog_id,
            era_profile_fingerprint=self.era_profile_fingerprint,
            recipes=self.recipes,
        )

    def lookup(self, request: RecipeAssessmentRequest) -> GeneratedRecipeEntry | None:
        return next(
            (value for value in self._recipes if value.recipe_key == request.recipe_key),
            None,
        )

    def add(self, entry: GeneratedRecipeEntry) -> None:
        if any(value.recipe_key == entry.recipe_key for value in self._recipes):
            raise RecipeAssessmentError("recipe key already exists")
        self._recipes.append(entry)

    def to_document(self) -> dict[str, Any]:
        return {
            "schemaVersion": RECIPE_SCHEMA_VERSION,
            "catalogId": self.catalog_id,
            "eraProfileFingerprint": self.era_profile_fingerprint,
            "itemContractFingerprint": item_contract_fingerprint(),
            "recipes": [value.to_mapping() for value in self._recipes],
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_document(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


@dataclass(frozen=True, slots=True)
class RecipeAssessmentResolution:
    status: str
    entry: GeneratedRecipeEntry | None
    output_definition: Mapping[str, Any] | None
    recipe_catalog: GeneratedRecipeCatalog
    daily_catalog: DailyItemDefinitionCatalog
    reference_table: DailyItemReferenceTable
    reason: str | None = None
    assessment_adapter_called: bool = False
    generation_adapter_called: bool = False


def resolve_item_recipe(
    recipe_catalog: GeneratedRecipeCatalog,
    daily_catalog: DailyItemDefinitionCatalog,
    reference_table: DailyItemReferenceTable,
    era_profile: EraTechnologyProfile,
    request: RecipeAssessmentRequest,
    assessment_adapter: RecipeAssessmentAdapter | None = None,
    generation_adapter: DailyItemGenerationAdapter | None = None,
    *,
    known_definitions: tuple[Mapping[str, Any], ...] = (),
    minimum_confidence: float = MINIMUM_RECIPE_CONFIDENCE,
) -> RecipeAssessmentResolution:
    if recipe_catalog.era_profile_fingerprint != era_profile.fingerprint:
        return _rejected(
            "recipe catalog uses a stale era profile",
            recipe_catalog,
            daily_catalog,
            reference_table,
        )
    try:
        definitions = _validated_input_definitions(request, known_definitions, daily_catalog)
    except RecipeAssessmentError as error:
        return _rejected(
            str(error), recipe_catalog, daily_catalog, reference_table
        )
    cached = recipe_catalog.lookup(request)
    if cached is not None:
        output = _definition_by_id(
            cached.output_definition_id, known_definitions, daily_catalog
        )
        if output is None:
            return _rejected(
                "cached recipe output definition is missing",
                recipe_catalog,
                daily_catalog,
                reference_table,
            )
        try:
            validated_output = _validate_output_definition(
                output,
                request,
                definitions,
                cached.output_quantity,
            )
            _validate_entry_era(cached, era_profile)
        except RecipeAssessmentError as error:
            return _rejected(
                f"cached recipe is no longer valid: {error}",
                recipe_catalog,
                daily_catalog,
                reference_table,
            )
        return RecipeAssessmentResolution(
            status="cache_hit",
            entry=cached,
            output_definition=validated_output,
            recipe_catalog=recipe_catalog,
            daily_catalog=daily_catalog,
            reference_table=reference_table,
        )
    if assessment_adapter is None or not assessment_adapter.available:
        return _rejected(
            "no enabled recipe assessment adapter",
            recipe_catalog,
            daily_catalog,
            reference_table,
            status="cache_miss",
        )
    try:
        result = assessment_adapter.assess(request, era_profile, definitions)
        candidate = RecipeAssessmentCandidate.from_output(
            result.output,
            request,
            era_profile,
            minimum_confidence=minimum_confidence,
        )
        daily_resolution = resolve_daily_item_definition(
            daily_catalog,
            reference_table,
            DailyItemGenerationRequest(candidate.output_text),
            generation_adapter,
            known_definitions=known_definitions,
        )
        if daily_resolution.definition is None:
            raise RecipeAssessmentError(
                f"recipe output definition was not resolved: {daily_resolution.reason}"
            )
        output = _validate_output_definition(
            daily_resolution.definition,
            request,
            definitions,
            candidate.output_quantity,
        )
        audit = ModelAudit(
            provider=assessment_adapter.provider_name,
            model=assessment_adapter.model_name,
            prompt_tokens=result.metrics.prompt_tokens,
            completion_tokens=result.metrics.completion_tokens,
            total_tokens=result.metrics.total_tokens,
            latency_ms=result.metrics.latency_ms,
        )
        entry = GeneratedRecipeEntry(
            recipe_key=request.recipe_key,
            process_text=request.process_text,
            ingredients=request.ingredients,
            output_definition_id=str(output["definitionId"]),
            output_quantity=candidate.output_quantity,
            process_summary=candidate.process_summary,
            era_evidence=candidate.era_evidence,
            confidence=candidate.confidence,
            model_audit=audit,
        )
        working = recipe_catalog.clone()
        working.add(entry)
    except Exception as error:
        return _rejected(
            f"{type(error).__name__}: {error}",
            recipe_catalog,
            daily_catalog,
            reference_table,
            assessment_called=True,
        )
    return RecipeAssessmentResolution(
        status="model_accepted",
        entry=entry,
        output_definition=output,
        recipe_catalog=working,
        daily_catalog=daily_resolution.catalog,
        reference_table=daily_resolution.reference_table,
        assessment_adapter_called=True,
        generation_adapter_called=daily_resolution.adapter_called,
    )


def render_generated_recipe_markdown(catalog: GeneratedRecipeCatalog) -> str:
    lines = [
        "# AI 辅助物品配方缓存",
        "",
        "这里只保存已通过时代、输入、类别、重量、置信度与质量守恒校验的普通物品配方。",
        "",
        "| recipeKey | 输入 | 产物 definitionId | 数量 | 置信度 |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    if not catalog.recipes:
        lines.append("| - | 暂无已确认配方 | - | - | - |")
    for entry in catalog.recipes:
        ingredients = " + ".join(
            f"{value.definition_id} x{value.quantity}" for value in entry.ingredients
        )
        lines.append(
            f"| `{entry.recipe_key}` | {ingredients} | `{entry.output_definition_id}` | "
            f"{entry.output_quantity} | {entry.confidence:.2f} |"
        )
    lines.extend(
        [
            "",
            "配方缓存不修改库存。执行前仍需以当前物品实例重新验证所有权、数量和容器，候选事件必须由核心事务层原子提交。",
            "",
        ]
    )
    return "\n".join(lines)


def _validated_input_definitions(
    request: RecipeAssessmentRequest,
    known_definitions: tuple[Mapping[str, Any], ...],
    daily_catalog: DailyItemDefinitionCatalog,
) -> tuple[Mapping[str, Any], ...]:
    values: list[Mapping[str, Any]] = []
    for ingredient in request.ingredients:
        raw = _definition_by_id(
            ingredient.definition_id, known_definitions, daily_catalog
        )
        if raw is None:
            raise RecipeAssessmentError(
                f"recipe input definition does not exist: {ingredient.definition_id}"
            )
        try:
            definition = ItemDefinition.from_payload(raw)
        except ValueError as error:
            raise RecipeAssessmentError(f"recipe input definition is invalid: {error}") from error
        if definition.is_plot_item or definition.category in {"currency", "document"}:
            raise RecipeAssessmentError(
                f"recipe input is protected from ordinary crafting: {ingredient.definition_id}"
            )
        if definition.unit_weight_grams is None or definition.unit_weight_grams <= 0:
            raise RecipeAssessmentError(
                f"recipe input weight is unknown: {ingredient.definition_id}"
            )
        values.append(definition.to_payload())
    return tuple(values)


def _validate_output_definition(
    raw: Mapping[str, Any],
    request: RecipeAssessmentRequest,
    input_definitions: tuple[Mapping[str, Any], ...],
    output_quantity: int,
) -> Mapping[str, Any]:
    try:
        output = ItemDefinition.from_payload(raw)
    except ValueError as error:
        raise RecipeAssessmentError(f"recipe output definition is invalid: {error}") from error
    if output.is_plot_item or output.category not in DAILY_ITEM_CATEGORIES:
        raise RecipeAssessmentError("recipe output is not an allowed ordinary daily item")
    if output.definition_id in {value.definition_id for value in request.ingredients}:
        raise RecipeAssessmentError("recipe output cannot be identical to an input definition")
    if output.unit_weight_grams is None or output.unit_weight_grams <= 0:
        raise RecipeAssessmentError("recipe output weight is unknown")
    if not output.stackable and output_quantity != 1:
        raise RecipeAssessmentError("non-stackable recipe output quantity must be one")
    input_weights = {
        str(value["definitionId"]): int(value["unitWeightGrams"])
        for value in input_definitions
    }
    input_mass = sum(
        input_weights[value.definition_id] * value.quantity
        for value in request.ingredients
    )
    output_mass = output.unit_weight_grams * output_quantity
    if output_mass > math.floor(input_mass * MAX_MASS_INCREASE_RATIO):
        raise RecipeAssessmentError(
            f"recipe output mass {output_mass}g exceeds allowed input mass {input_mass}g"
        )
    return output.to_payload()


def _definition_by_id(
    definition_id: str,
    known_definitions: tuple[Mapping[str, Any], ...],
    daily_catalog: DailyItemDefinitionCatalog,
) -> Mapping[str, Any] | None:
    for value in known_definitions:
        if value.get("definitionId") == definition_id:
            return dict(value)
    for entry in daily_catalog.definitions:
        if entry.definition_id == definition_id:
            return dict(entry.item)
    return None


def _validate_entry_era(
    entry: GeneratedRecipeEntry,
    era_profile: EraTechnologyProfile,
) -> None:
    technologies = {value.technology_id: value for value in era_profile.technologies}
    unknown = sorted(set(entry.era_evidence).difference(technologies))
    if unknown:
        raise RecipeAssessmentError(f"cached recipe has unknown era evidence: {unknown}")
    blocked = sorted(
        value
        for value in entry.era_evidence
        if technologies[value].status in {"limited", "unavailable"}
    )
    if blocked:
        raise RecipeAssessmentError(
            f"cached recipe relies on limited or unavailable technology: {blocked}"
        )


def _ingredients_from_output(value: object) -> tuple[RecipeIngredient, ...]:
    if not isinstance(value, list):
        raise RecipeAssessmentError("ingredients must be an array")
    ingredients: list[RecipeIngredient] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != {"definitionId", "quantity"}:
            raise RecipeAssessmentError(f"ingredients[{index}] fields are invalid")
        try:
            ingredients.append(
                RecipeIngredient(
                    definition_id=raw["definitionId"],
                    quantity=raw["quantity"],
                )
            )
        except RecipeError as error:
            raise RecipeAssessmentError(str(error)) from error
    try:
        return normalize_ingredients(ingredients)
    except RecipeError as error:
        raise RecipeAssessmentError(str(error)) from error


def _rejected(
    reason: str,
    recipe_catalog: GeneratedRecipeCatalog,
    daily_catalog: DailyItemDefinitionCatalog,
    reference_table: DailyItemReferenceTable,
    *,
    status: str = "rejected",
    assessment_called: bool = False,
) -> RecipeAssessmentResolution:
    return RecipeAssessmentResolution(
        status=status,
        entry=None,
        output_definition=None,
        recipe_catalog=recipe_catalog,
        daily_catalog=daily_catalog,
        reference_table=reference_table,
        reason=reason,
        assessment_adapter_called=assessment_called,
    )


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecipeAssessmentError("confidence must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise RecipeAssessmentError("confidence must be between zero and one")
    return result


def _text(value: object, path: str, maximum: int) -> str:
    if type(value) is not str or not value.strip() or len(value.strip()) > maximum:
        raise RecipeAssessmentError(f"{path} must be a non-empty string")
    return value.strip()


def _string_tuple(
    value: object,
    path: str,
    maximum: int,
    item_maximum: int,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise RecipeAssessmentError(f"{path} must be an array")
    result = tuple(_text(item, path, item_maximum) for item in value)
    if len(result) != len(set(result)):
        raise RecipeAssessmentError(f"{path} must not contain duplicates")
    return result


def _normalize(value: str) -> str:
    return _WHITESPACE.sub(" ", value.strip()).casefold()


__all__ = [
    "GeneratedRecipeCatalog",
    "GeneratedRecipeEntry",
    "MINIMUM_RECIPE_CONFIDENCE",
    "RecipeAssessmentAdapter",
    "RecipeAssessmentAdapterResult",
    "RecipeAssessmentCandidate",
    "RecipeAssessmentError",
    "RecipeAssessmentRequest",
    "RecipeAssessmentResolution",
    "render_generated_recipe_markdown",
    "resolve_item_recipe",
]
