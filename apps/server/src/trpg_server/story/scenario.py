from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trpg_server.characters.inventory import ensure_inventory_containers
from trpg_server.characters.traits import build_character_traits
from trpg_server.core.state import Event
from trpg_server.items.catalog import ItemAtlas, ItemAtlasError, load_item_atlas
from trpg_server.items.contract import ITEM_RECORD_FIELDS
from trpg_server.map.atlas import MapAtlas, atlas_for_scenario
from trpg_server.map.runtime_ids import (
    atlas_street_id_for_runtime,
    runtime_location_id,
    runtime_street_id,
    runtime_structure_id,
)
from trpg_server.locations.furniture import (
    FurnitureAtlas,
    FurnitureAtlasError,
    load_furniture_atlas,
)
from trpg_server.story.v4_compiler import V42Catalog


class ScenarioPackageError(ValueError):
    """Raised when a scenario package is structurally or semantically invalid."""


class PackageModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class CalendarDefinition(PackageModel):
    era: str = Field(min_length=1)
    year: int = Field(ge=0)
    month: int = Field(ge=1)
    day: int = Field(ge=1)
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    days_per_month: int = Field(default=30, alias="daysPerMonth", ge=1)
    months_per_year: int = Field(default=12, alias="monthsPerYear", ge=1)


class Manifest(PackageModel):
    schema_version: Literal[1, 2, 3, 4, 5, 6, 7, 8] = Field(alias="schemaVersion")
    scenario_id: str = Field(alias="scenarioId", min_length=1)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    name: str = Field(min_length=1)
    initial_world_time: int = Field(alias="initialWorldTime", ge=0)
    time_unit: Literal["minute"] = Field(alias="timeUnit")
    player_character_id: str = Field(alias="playerCharacterId", min_length=1)
    initial_scene_id: str = Field(alias="initialSceneId", min_length=1)
    description: str = ""
    content_scope: str = Field(default="", alias="contentScope")
    source_version: str | None = Field(default=None, alias="sourceVersion")
    source_document: str | None = Field(default=None, alias="sourceDocument")
    source_sha256: str | None = Field(
        default=None,
        alias="sourceSha256",
        min_length=64,
        max_length=64,
    )
    catalog_file: str | None = Field(default=None, alias="catalogFile")
    initial_calendar: CalendarDefinition | None = Field(
        default=None,
        alias="initialCalendar",
    )

    @model_validator(mode="after")
    def v2_requires_calendar(self) -> Manifest:
        if self.schema_version >= 2 and self.initial_calendar is None:
            raise ValueError("schema version 2+ requires initialCalendar")
        if self.schema_version >= 8:
            required = {
                "sourceVersion": self.source_version,
                "sourceDocument": self.source_document,
                "sourceSha256": self.source_sha256,
                "catalogFile": self.catalog_file,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(
                    f"schema version 8+ requires {', '.join(missing)}"
                )
            if self.source_version != "4.2":
                raise ValueError("schema version 8 requires sourceVersion 4.2")
            if Path(self.catalog_file or "").name != self.catalog_file:
                raise ValueError("catalogFile must be a package-local filename")
        return self


class ExitDefinition(PackageModel):
    id: str | None = Field(default=None, min_length=1)
    to_location_id: str = Field(alias="toLocationId", min_length=1)
    label: str = Field(default="", min_length=0)
    travel_minutes: int = Field(default=1, alias="travelMinutes", ge=0)
    visible: bool = True
    locked: bool = False
    key_item_ids: list[str] = Field(default_factory=list, alias="keyItemIds")
    required_condition_ids: list[str] = Field(
        default_factory=list,
        alias="requiredConditionIds",
    )
    discovery_id: str | None = Field(default=None, alias="discoveryId")

    @model_validator(mode="after")
    def hidden_exit_requires_discovery(self) -> "ExitDefinition":
        if not self.visible and self.discovery_id is None:
            raise ValueError("an invisible exit must declare discoveryId")
        if self.visible and self.discovery_id is not None:
            raise ValueError("a discovery-gated exit must be invisible")
        return self


class LocationDefinition(PackageModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    kind: str = "area"
    map_visibility: Literal["public", "player", "gm"] = Field(
        default="public",
        alias="mapVisibility",
    )
    parent_id: str | None = Field(default=None, alias="parentId")
    description: str = ""
    connections: list[str] = Field(default_factory=list)
    exits: list[ExitDefinition] = Field(default_factory=list)


class DecisionProfileDefinition(PackageModel):
    monthly_income_pence: int = Field(alias="monthlyIncomePence", ge=0)
    economic_pressure: int = Field(alias="economicPressure", ge=0, le=100)
    gift_openness: int = Field(alias="giftOpenness", ge=0, le=100)
    greed: int = Field(ge=0, le=100)
    integrity: int = Field(ge=0, le=100)
    risk_aversion: int = Field(alias="riskAversion", ge=0, le=100)
    institutional_loyalty: int = Field(
        alias="institutionalLoyalty",
        ge=0,
        le=100,
    )
    corruption_openness: int = Field(
        alias="corruptionOpenness",
        ge=0,
        le=100,
    )
    hard_refusals: list[Literal["bribery", "stolen_goods", "violence"]] = Field(
        default_factory=list,
        alias="hardRefusals",
    )


class CharacterAbilityDefinition(PackageModel):
    """A sourced, non-authoritative capability tag for a character.

    Abilities are profile metadata.  They are deliberately not represented as
    events of their own and cannot grant an action permission without a
    domain command checking the current context.  ``level`` and ``confidence``
    remain permissive because the campaign content uses narrative levels and
    may later adopt a numeric scale.
    """

    ability_id: str = Field(alias="abilityId", min_length=1)
    name: str = Field(min_length=1)
    level: Literal["working", "competent", "advanced", "expert"] | None = None
    source_status: Literal["canon", "inferred", "unknown", "player_defined"] = Field(
        default="unknown",
        alias="sourceStatus",
    )
    confidence: str | int | float | None = None
    basis: str = ""
    source_refs: list[Any] = Field(default_factory=list, alias="sourceRefs")
    notes: str = ""


class LanguageStyleDefinition(PackageModel):
    """Sourced presentation guidance used by private narrator/NPC contexts."""

    formality: str | int | float | None = None
    politeness: str | int | float | None = None
    directness: str | int | float | None = None
    verbosity: str | int | float | None = None
    pacing: str | int | float | None = None
    sentence_style: str | int | float | None = Field(
        default=None,
        alias="sentenceStyle",
    )
    address_terms: list[Any] = Field(default_factory=list, alias="addressTerms")
    catchphrases: list[Any] = Field(default_factory=list)
    pressure_shift: str | int | float | None = Field(
        default=None,
        alias="pressureShift",
    )
    taboos: list[Any] = Field(default_factory=list)
    source_status: Literal["canon", "inferred", "unknown", "player_defined"] = Field(
        default="unknown",
        alias="sourceStatus",
    )
    source_refs: list[Any] = Field(default_factory=list, alias="sourceRefs")
    notes: str = ""


class CharacterDefinition(PackageModel):
    id: str = Field(min_length=1)
    catalog_character_id: str | None = Field(
        default=None,
        alias="catalogCharacterId",
        pattern=r"^P\d{3}$",
    )
    type: Literal["player", "npc"]
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    accepted_gift_definition_ids: list[str] = Field(
        default_factory=list,
        alias="acceptedGiftDefinitionIds",
    )
    location_id: str = Field(alias="locationId", min_length=1)
    role: str = ""
    birthplace: str = ""
    age: int | None = Field(default=None, ge=0)
    adult: bool | None = None
    public_description: str = Field(default="", alias="publicDescription")
    private_notes: str = Field(default="", alias="privateNotes")
    motivations: list[str] = Field(default_factory=list)
    fears: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    organization_ids: list[str] = Field(default_factory=list, alias="organizationIds")
    tags: list[str] = Field(default_factory=list)
    player_defined_fields: list[str] = Field(
        default_factory=list,
        alias="playerDefinedFields",
    )
    abilities: list[CharacterAbilityDefinition] = Field(default_factory=list)
    language_style: LanguageStyleDefinition = Field(
        default_factory=LanguageStyleDefinition,
        alias="languageStyle",
    )
    decision_profile: DecisionProfileDefinition | None = Field(
        default=None,
        alias="decisionProfile",
    )


def _profile_fields_from_catalog(
    attributes: dict[str, Any],
    *,
    character_id: str,
    name: str = "",
    source_refs: list[Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate optional profile metadata carried by a catalog character.

    V4.2 catalog entries predate the profile fields, so missing values are
    intentionally represented as an empty list and a fully shaped unknown
    language style.  Invalid authored metadata is a package error rather than
    silently becoming a different character profile.
    """

    raw_abilities = attributes.get("abilities")
    raw_style = attributes.get("languageStyle")
    if raw_style is None:
        raw_style = attributes.get("language_style")
    if raw_abilities is None and raw_style is None:
        traits = build_character_traits(
            character_id=character_id,
            name=name or str(attributes.get("name", "")),
            role=str(attributes.get("identity", "")),
            attributes=attributes,
            source_refs=source_refs or attributes.get("sourceRefs", []),
            source_text="\n".join(
                str(value.get("excerpt", ""))
                for value in (source_refs or [])
                if isinstance(value, dict)
            ),
        )
        return traits["abilities"], traits["languageStyle"]
    try:
        abilities = [
            CharacterAbilityDefinition.model_validate(value).model_dump(by_alias=True)
            for value in (raw_abilities or [])
        ]
        style = LanguageStyleDefinition.model_validate(
            raw_style or {}
        ).model_dump(by_alias=True)
    except ValidationError as error:
        raise ScenarioPackageError(
            f"catalog character profile metadata is invalid: {character_id}: {error}"
        ) from error
    return abilities, style


class ContainerDefinition(PackageModel):
    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    owner_character_id: str | None = Field(default=None, alias="ownerCharacterId")
    location_id: str | None = Field(default=None, alias="locationId")

    @model_validator(mode="after")
    def has_exactly_one_anchor(self) -> ContainerDefinition:
        if (self.owner_character_id is None) == (self.location_id is None):
            raise ValueError("container must reference exactly one owner or location")
        return self


class ItemInstanceSeed(PackageModel):
    """One concrete initial instance in the published 15-field item shape.

    Item definitions are loaded from the atlas rather than being nested into
    seed data.  This keeps a definition from asserting runtime existence and
    prevents the scenario package from recreating the retired item schema.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid", strict=True)

    id: str = Field(min_length=1)
    definition_id: str = Field(alias="definitionId", min_length=1)
    name: str = Field(min_length=1)
    description: str
    category: str = Field(min_length=1)
    is_plot_item: bool = Field(alias="isPlotItem")
    quantity: int = Field(ge=1)
    stackable: bool
    unit_weight_grams: int | None = Field(default=None, alias="unitWeightGrams", ge=0)
    value_crown: int | None = Field(default=None, alias="valueCrown", ge=0)
    condition: str | None = None
    durability: dict[str, float] | None = None
    container_id: str | None = Field(default=None, alias="containerId")
    location_id: str | None = Field(default=None, alias="locationId")
    properties: dict[str, Any]

    @model_validator(mode="after")
    def has_valid_item_record(self) -> "ItemInstanceSeed":
        if not self.stackable and self.quantity != 1:
            raise ValueError("a non-stackable item must have quantity 1")
        if self.condition is not None and not self.condition:
            raise ValueError("item condition must be a non-empty string")
        if self.durability is not None:
            if set(self.durability) != {"current", "max"}:
                raise ValueError("item durability requires current and max")
            current = self.durability["current"]
            maximum = self.durability["max"]
            if current < 0 or maximum < 0 or current > maximum:
                raise ValueError("item durability requires 0 <= current <= max")
        if self.container_id is not None and self.location_id is not None:
            raise ValueError("item cannot have both containerId and locationId")
        return self

    def to_item_record(self) -> dict[str, Any]:
        """Serialize exactly the contract record accepted by ``item.created``."""

        return self.model_dump(by_alias=True)


class RelationshipDefinition(PackageModel):
    subject_id: str = Field(alias="subjectId", min_length=1)
    object_id: str = Field(alias="objectId", min_length=1)
    favor: int = 0
    trust: int = 0
    fear: int = 0
    respect: int = 0
    suspicion: int = 0
    debt: int = 0


class ClueDefinition(PackageModel):
    id: str = Field(min_length=1)
    fact_id: str = Field(alias="factId", min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    initially_known_by: list[str] = Field(default_factory=list, alias="initiallyKnownBy")
    initially_revealed: bool = Field(default=False, alias="initiallyRevealed")


class NarrativeGuidanceDefinition(PackageModel):
    premise: str = Field(min_length=1, max_length=300)
    hard_anchors: list[str] = Field(alias="hardAnchors", min_length=1, max_length=8)
    flexible_approaches: list[str] = Field(
        alias="flexibleApproaches",
        min_length=1,
        max_length=12,
    )
    stop_before: list[str] = Field(alias="stopBefore", min_length=1, max_length=8)


class SceneDefinition(PackageModel):
    id: str = Field(min_length=1)
    location_id: str = Field(alias="locationId", min_length=1)
    phase: Literal["exploration", "social", "combat"] = "exploration"
    title: str = ""
    objective: str = ""
    present_character_ids: list[str] = Field(
        default_factory=list,
        alias="presentCharacterIds",
    )
    opening_text: str = Field(default="", alias="openingText")
    narrative_guidance: NarrativeGuidanceDefinition | None = Field(
        default=None,
        alias="narrativeGuidance",
    )
    max_major_beats_per_turn: int = Field(
        default=1,
        alias="maxMajorBeatsPerTurn",
        ge=1,
        le=3,
    )


class OrganizationDefinition(PackageModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    visibility: Literal["public", "player", "gm"] = "public"
    headquarters_location_id: str | None = Field(
        default=None,
        alias="headquartersLocationId",
    )
    leader_character_ids: list[str] = Field(
        default_factory=list,
        alias="leaderCharacterIds",
    )
    member_character_ids: list[str] = Field(
        default_factory=list,
        alias="memberCharacterIds",
    )
    public_description: str = Field(default="", alias="publicDescription")
    private_goals: list[str] = Field(default_factory=list, alias="privateGoals")
    resource_tags: list[str] = Field(default_factory=list, alias="resourceTags")
    policy_tags: list[str] = Field(default_factory=list, alias="policyTags")


class FactDefinition(PackageModel):
    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    truth_state: Literal["true", "false"] = Field(default="true", alias="truthState")
    visibility: Literal["public", "player", "gm"] = "gm"
    initially_known_by: list[str] = Field(default_factory=list, alias="initiallyKnownBy")
    tags: list[str] = Field(default_factory=list)


class ClockDefinition(PackageModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    starts_at: int = Field(alias="startsAt", ge=0)
    deadline_at: int = Field(alias="deadlineAt", ge=0)
    status: Literal["active", "paused", "resolved", "expired"] = "active"
    visibility: Literal["public", "player", "gm"] = "gm"
    stakes: str = ""

    @model_validator(mode="after")
    def deadline_follows_start(self) -> ClockDefinition:
        if self.deadline_at <= self.starts_at:
            raise ValueError("clock deadlineAt must be greater than startsAt")
        return self


class ObligationDefinition(PackageModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    kind: Literal["debt", "promise", "contract", "duty"]
    debtor_id: str = Field(alias="debtorId", min_length=1)
    creditor_id: str = Field(alias="creditorId", min_length=1)
    status: Literal["active", "fulfilled", "breached", "disputed", "cancelled"] = "active"
    terms: str = Field(min_length=1)
    due_clock_id: str | None = Field(default=None, alias="dueClockId")
    evidence_fact_ids: list[str] = Field(default_factory=list, alias="evidenceFactIds")
    visibility: Literal["public", "player", "gm"] = "gm"


class StoryConditionDefinition(PackageModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    initially_active: bool = Field(default=False, alias="initiallyActive")
    visibility: Literal["public", "player", "gm"] = "gm"


class DiscoveryDefinition(PackageModel):
    id: str = Field(min_length=1)
    location_id: str = Field(alias="locationId", min_length=1)
    aliases: list[str] = Field(default_factory=list)
    fact_id: str = Field(alias="factId", min_length=1)
    clue_id: str = Field(alias="clueId", min_length=1)
    exit_ids: list[str] = Field(alias="exitIds", min_length=1)
    required_condition_ids: list[str] = Field(
        default_factory=list,
        alias="requiredConditionIds",
    )
    initially_known_by: list[str] = Field(
        default_factory=list,
        alias="initiallyKnownBy",
    )
    time_minutes: int = Field(default=10, alias="timeMinutes", ge=1)
    reveal_text: str = Field(alias="revealText", min_length=1)


class InspectionDefinition(PackageModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    suggested_prompt: str = Field(alias="suggestedPrompt", min_length=1)
    target_item_id: str = Field(alias="targetItemId", min_length=1)
    aliases: list[str] = Field(min_length=1)
    access_policy: Literal["location", "actor_owned"] = Field(alias="accessPolicy")
    required_actor_knowledge_fact_ids: list[str] = Field(
        default_factory=list,
        alias="requiredActorKnowledgeFactIds",
    )
    revealed_fact_ids: list[str] = Field(
        default_factory=list,
        alias="revealedFactIds",
    )
    clue_ids: list[str] = Field(default_factory=list, alias="clueIds")
    time_minutes: int = Field(alias="timeMinutes", ge=1)
    reveal_text: str = Field(alias="revealText", min_length=1)
    repeat_text: str = Field(alias="repeatText", min_length=1)


class InquiryDefinition(PackageModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    suggested_prompt: str = Field(alias="suggestedPrompt", min_length=1)
    target_character_id: str = Field(alias="targetCharacterId", min_length=1)
    topic: str = Field(min_length=1)
    aliases: list[str] = Field(min_length=1)
    required_actor_knowledge_fact_ids: list[str] = Field(
        default_factory=list,
        alias="requiredActorKnowledgeFactIds",
    )
    required_npc_knowledge_fact_ids: list[str] = Field(
        default_factory=list,
        alias="requiredNpcKnowledgeFactIds",
    )
    revealed_fact_ids: list[str] = Field(
        default_factory=list,
        alias="revealedFactIds",
    )
    clue_ids: list[str] = Field(default_factory=list, alias="clueIds")
    time_minutes: int = Field(alias="timeMinutes", ge=1)
    response_text: str = Field(alias="responseText", min_length=1)
    repeat_text: str = Field(alias="repeatText", min_length=1)
    unknown_text: str = Field(alias="unknownText", min_length=1)


class NpcScheduleDefinition(PackageModel):
    id: str = Field(min_length=1)
    character_id: str = Field(alias="characterId", min_length=1)
    weekday: int = Field(ge=0, le=6)
    start_minute: int = Field(alias="startMinute", ge=0, le=1439)
    end_minute: int = Field(alias="endMinute", ge=1, le=1440)
    location_id: str = Field(alias="locationId", min_length=1)
    availability: Literal["public", "appointment", "private", "unavailable"] = "public"
    priority: int = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def end_follows_start(self) -> NpcScheduleDefinition:
        if self.end_minute <= self.start_minute:
            raise ValueError("schedule endMinute must be after startMinute")
        return self


class SchedulesFile(PackageModel):
    schedules: list[NpcScheduleDefinition]


class LocationsFile(PackageModel):
    locations: list[LocationDefinition]


class CharactersFile(PackageModel):
    characters: list[CharacterDefinition]


class ContainersFile(PackageModel):
    containers: list[ContainerDefinition]


class ItemsFile(PackageModel):
    schema_version: Literal[3] = Field(alias="schemaVersion")
    atlas_file: str = Field(alias="atlasFile", min_length=1)
    instances: list[ItemInstanceSeed]

    @model_validator(mode="after")
    def has_package_local_atlas_file(self) -> "ItemsFile":
        atlas_path = Path(self.atlas_file)
        if atlas_path.is_absolute() or any(part == ".." for part in atlas_path.parts):
            raise ValueError("atlasFile must be a package-local relative path")
        return self


class RelationshipsFile(PackageModel):
    relationships: list[RelationshipDefinition]


class CluesFile(PackageModel):
    clues: list[ClueDefinition]


class ScenesFile(PackageModel):
    scenes: list[SceneDefinition]


class OrganizationsFile(PackageModel):
    organizations: list[OrganizationDefinition]


class FactsFile(PackageModel):
    facts: list[FactDefinition]


class ClocksFile(PackageModel):
    clocks: list[ClockDefinition]


class ObligationsFile(PackageModel):
    obligations: list[ObligationDefinition]


class StoryConditionsFile(PackageModel):
    conditions: list[StoryConditionDefinition]


class DiscoveriesFile(PackageModel):
    discoveries: list[DiscoveryDefinition]


class InteractionsFile(PackageModel):
    inspections: list[InspectionDefinition] = Field(default_factory=list)
    inquiries: list[InquiryDefinition] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ScenarioPackage:
    manifest: Manifest
    locations: tuple[LocationDefinition, ...]
    characters: tuple[CharacterDefinition, ...]
    containers: tuple[ContainerDefinition, ...]
    items: tuple[ItemInstanceSeed, ...]
    item_atlas: ItemAtlas
    relationships: tuple[RelationshipDefinition, ...]
    clues: tuple[ClueDefinition, ...]
    scenes: tuple[SceneDefinition, ...]
    organizations: tuple[OrganizationDefinition, ...]
    facts: tuple[FactDefinition, ...]
    clocks: tuple[ClockDefinition, ...]
    obligations: tuple[ObligationDefinition, ...]
    story_conditions: tuple[StoryConditionDefinition, ...]
    discoveries: tuple[DiscoveryDefinition, ...]
    inspections: tuple[InspectionDefinition, ...]
    inquiries: tuple[InquiryDefinition, ...]
    schedules: tuple[NpcScheduleDefinition, ...]
    catalog: V42Catalog | None
    content_hash: str
    furniture_atlas: FurnitureAtlas | None = None

    @property
    def item_definition_ids(self) -> frozenset[str]:
        """All catalogued item kinds, including kinds not seeded at start."""

        return frozenset(str(value["id"]) for value in self.item_atlas.definitions)


FILE_MODELS: dict[str, type[BaseModel]] = {
    "manifest.json": Manifest,
    "locations.json": LocationsFile,
    "characters.json": CharactersFile,
    "containers.json": ContainersFile,
    "items.json": ItemsFile,
    "relationships.json": RelationshipsFile,
    "clues.json": CluesFile,
    "scenes.json": ScenesFile,
}

V2_FILE_MODELS: dict[str, type[BaseModel]] = {
    "organizations.json": OrganizationsFile,
    "facts.json": FactsFile,
    "clocks.json": ClocksFile,
    "obligations.json": ObligationsFile,
}

V3_FILE_MODELS: dict[str, type[BaseModel]] = {
    "conditions.json": StoryConditionsFile,
    "discoveries.json": DiscoveriesFile,
}

V4_FILE_MODELS: dict[str, type[BaseModel]] = {
    "interactions.json": InteractionsFile,
}

V7_FILE_MODELS: dict[str, type[BaseModel]] = {
    "schedules.json": SchedulesFile,
}


def load_scenario_package(package_path: Path) -> ScenarioPackage:
    documents: dict[str, dict[str, Any]] = {}
    parsed: dict[str, BaseModel] = {}
    for filename, model_type in FILE_MODELS.items():
        document = _read_document(package_path / filename)
        documents[filename] = document
        try:
            parsed[filename] = model_type.model_validate(document)
        except ValidationError as error:
            raise ScenarioPackageError(f"{filename}: {error}") from error

    items_file = _as(parsed, "items.json", ItemsFile)
    item_atlas = _load_package_item_atlas(package_path, items_file, documents)
    _validate_seed_instances_against_atlas(items_file.instances, item_atlas)

    manifest = _as(parsed, "manifest.json", Manifest)
    if manifest.schema_version >= 2:
        for filename, model_type in V2_FILE_MODELS.items():
            document = _read_document(package_path / filename)
            documents[filename] = document
            try:
                parsed[filename] = model_type.model_validate(document)
            except ValidationError as error:
                raise ScenarioPackageError(f"{filename}: {error}") from error
    if manifest.schema_version >= 3:
        for filename, model_type in V3_FILE_MODELS.items():
            document = _read_document(package_path / filename)
            documents[filename] = document
            try:
                parsed[filename] = model_type.model_validate(document)
            except ValidationError as error:
                raise ScenarioPackageError(f"{filename}: {error}") from error
    if manifest.schema_version >= 4:
        for filename, model_type in V4_FILE_MODELS.items():
            document = _read_document(package_path / filename)
            documents[filename] = document
            try:
                parsed[filename] = model_type.model_validate(document)
            except ValidationError as error:
                raise ScenarioPackageError(f"{filename}: {error}") from error
    if manifest.schema_version >= 7:
        for filename, model_type in V7_FILE_MODELS.items():
            document = _read_document(package_path / filename)
            documents[filename] = document
            try:
                parsed[filename] = model_type.model_validate(document)
            except ValidationError as error:
                raise ScenarioPackageError(f"{filename}: {error}") from error

    catalog: V42Catalog | None = None
    if manifest.schema_version >= 8:
        catalog_filename = manifest.catalog_file
        if catalog_filename is None:
            raise ScenarioPackageError("manifest.json: schema version 8 requires catalogFile")
        document = _read_document(package_path / catalog_filename)
        documents[catalog_filename] = document
        try:
            catalog = V42Catalog.model_validate(document)
        except ValidationError as error:
            raise ScenarioPackageError(f"{catalog_filename}: {error}") from error
        _validate_v42_catalog_binding(manifest, catalog)

    furniture_atlas: FurnitureAtlas | None = None
    furniture_path = package_path / "furniture-atlas.json"
    if furniture_path.exists():
        furniture_document = _read_document(furniture_path)
        documents["furniture-atlas.json"] = furniture_document
        map_atlas = atlas_for_scenario(manifest.scenario_id)
        if map_atlas is None:
            raise ScenarioPackageError(
                "furniture-atlas.json is only supported for an executable map scenario"
            )
        try:
            furniture_atlas = load_furniture_atlas(
                furniture_path,
                map_atlas=map_atlas,
            )
        except FurnitureAtlasError as error:
            raise ScenarioPackageError(f"furniture-atlas.json: {error}") from error

    package = ScenarioPackage(
        manifest=manifest,
        locations=tuple(_as(parsed, "locations.json", LocationsFile).locations),
        characters=tuple(_as(parsed, "characters.json", CharactersFile).characters),
        containers=tuple(_as(parsed, "containers.json", ContainersFile).containers),
        items=tuple(items_file.instances),
        item_atlas=item_atlas,
        relationships=tuple(
            _as(parsed, "relationships.json", RelationshipsFile).relationships
        ),
        clues=tuple(_as(parsed, "clues.json", CluesFile).clues),
        scenes=tuple(_as(parsed, "scenes.json", ScenesFile).scenes),
        organizations=tuple(
            _as(parsed, "organizations.json", OrganizationsFile).organizations
            if manifest.schema_version >= 2
            else []
        ),
        facts=tuple(
            _as(parsed, "facts.json", FactsFile).facts
            if manifest.schema_version >= 2
            else []
        ),
        clocks=tuple(
            _as(parsed, "clocks.json", ClocksFile).clocks
            if manifest.schema_version >= 2
            else []
        ),
        obligations=tuple(
            _as(parsed, "obligations.json", ObligationsFile).obligations
            if manifest.schema_version >= 2
            else []
        ),
        story_conditions=tuple(
            _as(parsed, "conditions.json", StoryConditionsFile).conditions
            if manifest.schema_version >= 3
            else []
        ),
        discoveries=tuple(
            _as(parsed, "discoveries.json", DiscoveriesFile).discoveries
            if manifest.schema_version >= 3
            else []
        ),
        inspections=tuple(
            _as(parsed, "interactions.json", InteractionsFile).inspections
            if manifest.schema_version >= 4
            else []
        ),
        inquiries=tuple(
            _as(parsed, "interactions.json", InteractionsFile).inquiries
            if manifest.schema_version >= 4
            else []
        ),
        schedules=tuple(
            _as(parsed, "schedules.json", SchedulesFile).schedules
            if manifest.schema_version >= 7
            else []
        ),
        catalog=catalog,
        content_hash=_content_hash(documents),
        furniture_atlas=furniture_atlas,
    )
    _validate_atlas_discovery_bindings(package)
    _validate_references(package)
    return package


def compile_initial_events(
    package: ScenarioPackage,
    campaign_id: str,
) -> list[Event]:
    now = package.manifest.initial_world_time
    events: list[Event] = []
    catalog_character_entries = _runtime_catalog_character_entries(package)
    runtime_character_ids = [
        *(character.id for character in package.characters),
        *(character_id for _entry, character_id in catalog_character_entries),
    ]
    inventory_resolution = ensure_inventory_containers(
        runtime_character_ids,
        package.containers,
    )
    inventory_by_character = inventory_resolution.by_character
    def emit(
        event_type: str,
        actor_id: str,
        payload: dict[str, Any],
        schema_version: int = 1,
    ) -> Event:
        event = Event(
            event_id=_bootstrap_event_id(
                campaign_id, package.content_hash, len(events), event_type
            ),
            event_type=event_type,
            actor_id=actor_id,
            world_time=now,
            payload=payload,
            schema_version=schema_version,
        )
        events.append(event)
        return event

    emit("campaign.created", "system", {
        "campaignId": campaign_id,
        "name": package.manifest.name,
        "timeUnit": package.manifest.time_unit,
        "scenarioId": package.manifest.scenario_id,
        "scenarioVersion": package.manifest.version,
        "scenarioContentHash": package.content_hash,
        "scenarioSourceVersion": package.manifest.source_version,
        "scenarioSourceDocument": package.manifest.source_document,
        "scenarioSourceSha256": package.manifest.source_sha256,
        "scenarioCatalogSchemaVersion": (
            package.catalog.schema_version if package.catalog is not None else None
        ),
        "playerCharacterId": package.manifest.player_character_id,
        "initialCalendar": (
            package.manifest.initial_calendar.model_dump(by_alias=True)
            if package.manifest.initial_calendar is not None
            else None
        ),
    })
    atlas = atlas_for_scenario(package.manifest.scenario_id)
    atlas_street_ids = (
        {runtime_street_id(street.id) for street in atlas.streets}
        if atlas is not None
        else set()
    )
    for location in sorted(package.locations, key=lambda value: value.id):
        runtime_exits = location.exits
        if atlas is not None:
            runtime_exits = (
                []
                if location.kind == "street"
                else _filter_initial_atlas_exits(package, atlas, location)
            )
        emit("location.created", "system", {
            "locationId": location.id,
            "name": location.name,
            "aliases": location.aliases,
            "kind": location.kind,
            "mapVisibility": location.map_visibility,
            "parentId": (
                "gray_harbor"
                if atlas is not None and location.parent_id in atlas_street_ids
                else location.parent_id
            ),
            "description": location.description,
            "connections": location.connections,
            "exits": [value.model_dump(by_alias=True) for value in runtime_exits],
        }, schema_version=2)

    # Every runtime character receives its inventory container before any
    # character.created event.  This keeps ownership initialization ordered
    # and makes the binding available to both authored and catalog characters.
    for container in sorted(package.containers, key=lambda value: value.id):
        emit("container.created", "system", {
            "containerId": container.id,
            "kind": container.kind,
            "ownerCharacterId": container.owner_character_id,
            "locationId": container.location_id,
        })
    for generated in sorted(
        inventory_resolution.generated,
        key=lambda value: value.container_id,
    ):
        emit("container.created", "system", {
            "containerId": generated.container_id,
            "kind": generated.kind,
            "ownerCharacterId": generated.owner_character_id,
            "locationId": generated.location_id,
        })

    if package.catalog is not None:
        _emit_v42_runtime_catalog(
            package,
            campaign_id,
            events,
            emit,
            catalog_character_entries=catalog_character_entries,
            inventory_by_character=inventory_by_character,
        )
    catalog_profiles_by_id = {
        str(entry.attributes.get("characterId")): entry
        for entry in (package.catalog.characters if package.catalog is not None else ())
        if entry.instantiated and entry.canon_layer != "G"
    }
    for character in sorted(package.characters, key=lambda value: value.id):
        catalog_entry = catalog_profiles_by_id.get(character.catalog_character_id)
        catalog_abilities: list[dict[str, Any]] = []
        catalog_language_style: dict[str, Any] | None = None
        if catalog_entry is not None:
            catalog_abilities, catalog_language_style = _profile_fields_from_catalog(
                catalog_entry.attributes,
                character_id=character.id,
                name=character.name,
                source_refs=catalog_entry.sources,
            )
        authored_traits = build_character_traits(
            character_id=character.id,
            name=character.name,
            role=character.role,
            attributes={
                "identity": " ".join(
                    [
                        character.role,
                        character.public_description,
                        *character.motivations,
                    ]
                )
            },
            source_refs=[],
            source_text="\n".join(
                [character.private_notes, *character.motivations, *character.fears]
            ),
            player=character.type == "player",
        )
        authored_abilities = (
            [value.model_dump(by_alias=True) for value in character.abilities]
            if character.abilities
            else catalog_abilities
            if catalog_entry is not None
            else authored_traits["abilities"]
        )
        authored_language_style = character.language_style.model_dump(by_alias=True)
        if (
            authored_language_style.get("sourceStatus") == "unknown"
            and not any(
                authored_language_style.get(field)
                for field in (
                    "formality",
                    "politeness",
                    "directness",
                    "verbosity",
                    "pacing",
                    "sentenceStyle",
                    "addressTerms",
                    "catchphrases",
                    "pressureShift",
                    "taboos",
                    "sourceRefs",
                    "notes",
                )
            )
        ):
            authored_language_style = (
                catalog_language_style
                if catalog_entry is not None and catalog_language_style is not None
                else authored_traits["languageStyle"]
            )
        character_payload = {
            "characterId": character.id,
            "catalogCharacterId": character.catalog_character_id,
            "catalogId": catalog_entry.id if catalog_entry is not None else None,
            "characterType": character.type,
            "name": character.name,
            "aliases": character.aliases,
            "acceptedGiftDefinitionIds": character.accepted_gift_definition_ids,
            "locationId": character.location_id,
            "role": character.role,
            "birthplace": character.birthplace,
            "age": character.age,
            "adult": character.adult,
            "publicDescription": character.public_description,
            "privateNotes": character.private_notes,
            "motivations": character.motivations,
            "fears": character.fears,
            "secrets": character.secrets,
            "organizationIds": character.organization_ids,
            "tags": character.tags,
            "playerDefinedFields": character.player_defined_fields,
            "abilities": authored_abilities,
            "languageStyle": authored_language_style,
            "decisionProfile": (
                character.decision_profile.model_dump(by_alias=True)
                if character.decision_profile is not None
                else None
            ),
        }
        character_schema_version = 2 if package.manifest.schema_version >= 6 else 1
        if character_schema_version >= 2:
            character_payload["inventoryContainerId"] = inventory_by_character[
                character.id
            ]
        emit(
            "character.created",
            "system",
            character_payload,
            schema_version=character_schema_version,
        )

    for schedule in sorted(package.schedules, key=lambda value: value.id):
        emit("npc.schedule_defined", "system", schedule.model_dump(by_alias=True))

    for organization in sorted(package.organizations, key=lambda value: value.id):
        emit("organization.created", "system", {
            "organizationId": organization.id,
            "name": organization.name,
            "organizationType": organization.type,
            "visibility": organization.visibility,
            "headquartersLocationId": organization.headquarters_location_id,
            "leaderCharacterIds": organization.leader_character_ids,
            "memberCharacterIds": organization.member_character_ids,
            "publicDescription": organization.public_description,
            "privateGoals": organization.private_goals,
            "resourceTags": organization.resource_tags,
            "policyTags": organization.policy_tags,
        })

    scene = next(
        value for value in package.scenes
        if value.id == package.manifest.initial_scene_id
    )
    guidance = scene.narrative_guidance
    emit("scene.started", "system", {
        "sceneId": scene.id,
        "locationId": scene.location_id,
        "phase": scene.phase,
        "title": scene.title,
        "objective": scene.objective,
        "presentCharacterIds": scene.present_character_ids,
        "openingText": scene.opening_text,
        "narrativeGuidance": (
            guidance.model_dump(by_alias=True) if guidance is not None else None
        ),
        "maxMajorBeatsPerTurn": scene.max_major_beats_per_turn,
    }, schema_version=2 if package.manifest.schema_version >= 5 else 1)
    for item in sorted(package.items, key=lambda value: value.id):
        emit(
            "item.created",
            "system",
            {"item": item.to_item_record()},
            schema_version=4,
        )
    for relationship in sorted(
        package.relationships,
        key=lambda value: (value.subject_id, value.object_id),
    ):
        emit("relationship.initialized", "system", {
            "subjectId": relationship.subject_id,
            "objectId": relationship.object_id,
            "dimensions": {
                "favor": relationship.favor,
                "trust": relationship.trust,
                "fear": relationship.fear,
                "respect": relationship.respect,
                "suspicion": relationship.suspicion,
                "debt": relationship.debt,
            },
        })
    for fact in sorted(package.facts, key=lambda value: value.id):
        definition_event = emit("world.fact_defined", "system", {
            "factId": fact.id,
            "statement": fact.statement,
            "truthState": fact.truth_state,
            "visibility": fact.visibility,
            "tags": fact.tags,
        })
        for character_id in sorted(fact.initially_known_by):
            emit("knowledge.learned", "system", {
                "characterId": character_id,
                "factId": fact.id,
                "sourceEventId": definition_event.event_id,
            })
    for clue in sorted(package.clues, key=lambda value: value.id):
        definition_event = emit("clue.defined", "system", {
            "clueId": clue.id,
            "factId": clue.fact_id,
            "title": clue.title,
            "description": clue.description,
        })
        knowledge_events: list[Event] = []
        for character_id in sorted(clue.initially_known_by):
            knowledge_events.append(emit("knowledge.learned", "system", {
                "characterId": character_id,
                "factId": clue.fact_id,
                "sourceEventId": definition_event.event_id,
            }))
        if clue.initially_revealed:
            source_id = knowledge_events[-1].event_id if knowledge_events else events[0].event_id
            emit("story.clue_revealed", "system", {
                "clueId": clue.id,
                "title": clue.title,
                "description": clue.description,
                "sourceEventId": source_id,
            })
    for clock in sorted(package.clocks, key=lambda value: value.id):
        emit("clock.created", "system", {
            "clockId": clock.id,
            "name": clock.name,
            "startsAt": clock.starts_at,
            "deadlineAt": clock.deadline_at,
            "status": clock.status,
            "visibility": clock.visibility,
            "stakes": clock.stakes,
        })
    for obligation in sorted(package.obligations, key=lambda value: value.id):
        emit("obligation.created", "system", {
            "obligationId": obligation.id,
            "title": obligation.title,
            "kind": obligation.kind,
            "debtorId": obligation.debtor_id,
            "creditorId": obligation.creditor_id,
            "status": obligation.status,
            "terms": obligation.terms,
            "dueClockId": obligation.due_clock_id,
            "evidenceFactIds": obligation.evidence_fact_ids,
            "visibility": obligation.visibility,
        })
    for condition in sorted(package.story_conditions, key=lambda value: value.id):
        emit("story.condition_defined", "system", {
            "conditionId": condition.id,
            "name": condition.name,
            "active": condition.initially_active,
            "visibility": condition.visibility,
        })
    for discovery in sorted(package.discoveries, key=lambda value: value.id):
        definition_event = emit("discovery.defined", "system", {
            "discoveryId": discovery.id,
            "locationId": discovery.location_id,
            "aliases": discovery.aliases,
            "factId": discovery.fact_id,
            "clueId": discovery.clue_id,
            "exitIds": discovery.exit_ids,
            "requiredConditionIds": discovery.required_condition_ids,
            "initiallyKnownBy": discovery.initially_known_by,
            "timeMinutes": discovery.time_minutes,
            "revealText": discovery.reveal_text,
        })
        for character_id in sorted(discovery.initially_known_by):
            for exit_id in sorted(discovery.exit_ids):
                emit("location.exit_discovered", "system", {
                    "characterId": character_id,
                    "exitId": exit_id,
                    "discoveryId": discovery.id,
                    "sourceEventId": definition_event.event_id,
                })
    for inspection in sorted(package.inspections, key=lambda value: value.id):
        emit("inspection.defined", "system", {
            "interactionId": inspection.id,
            "label": inspection.label,
            "suggestedPrompt": inspection.suggested_prompt,
            "targetItemId": inspection.target_item_id,
            "aliases": inspection.aliases,
            "accessPolicy": inspection.access_policy,
            "requiredActorKnowledgeFactIds": (
                inspection.required_actor_knowledge_fact_ids
            ),
            "revealedFactIds": inspection.revealed_fact_ids,
            "clueIds": inspection.clue_ids,
            "timeMinutes": inspection.time_minutes,
            "revealText": inspection.reveal_text,
            "repeatText": inspection.repeat_text,
        })
    for inquiry in sorted(package.inquiries, key=lambda value: value.id):
        emit("inquiry.defined", "system", {
            "interactionId": inquiry.id,
            "label": inquiry.label,
            "suggestedPrompt": inquiry.suggested_prompt,
            "targetCharacterId": inquiry.target_character_id,
            "topic": inquiry.topic,
            "aliases": inquiry.aliases,
            "requiredActorKnowledgeFactIds": inquiry.required_actor_knowledge_fact_ids,
            "requiredNpcKnowledgeFactIds": inquiry.required_npc_knowledge_fact_ids,
            "revealedFactIds": inquiry.revealed_fact_ids,
            "clueIds": inquiry.clue_ids,
            "timeMinutes": inquiry.time_minutes,
            "responseText": inquiry.response_text,
            "repeatText": inquiry.repeat_text,
            "unknownText": inquiry.unknown_text,
        })
    return events


def _runtime_catalog_character_entries(
    package: ScenarioPackage,
) -> tuple[tuple[Any, str], ...]:
    """Return instantiated catalog characters and their runtime ids.

    The same selection is used for bootstrap inventory planning and catalog
    event emission so every resulting ``character.created`` receives exactly
    one inventory binding.
    """

    catalog = package.catalog
    if catalog is None:
        return ()
    authored_catalog_ids = {
        value.catalog_character_id
        for value in package.characters
        if value.catalog_character_id is not None
    }
    entries: list[tuple[Any, str]] = []
    seen_ids: set[str] = {value.id for value in package.characters}
    for entry in catalog.characters:
        character_key = str(
            entry.attributes.get("characterId", entry.id.removeprefix("CHARACTER-"))
        )
        if (
            not entry.instantiated
            or entry.canon_layer == "G"
            or character_key in authored_catalog_ids
        ):
            continue
        runtime_id = f"catalog_{character_key.lower()}"
        if runtime_id in seen_ids:
            raise ScenarioPackageError(
                f"duplicate runtime catalog character id: {runtime_id}"
            )
        seen_ids.add(runtime_id)
        entries.append((entry, runtime_id))
    return tuple(entries)


def _emit_v42_runtime_catalog(
    package: ScenarioPackage,
    campaign_id: str,
    events: list[Event],
    emit: Any,
    *,
    catalog_character_entries: tuple[tuple[Any, str], ...] = (),
    inventory_by_character: dict[str, str] | None = None,
) -> None:
    """Compile V4.2 C0/C2 boundaries into replayable runtime metadata.

    Catalog entries are not facts merely because they are present in the
    catalog.  C0 instantiated locations and characters become executable
    entities; all other entries remain auditable metadata for the Director and
    validators.  G-layer templates never become world entities here.
    """
    catalog = package.catalog
    if catalog is None:
        return

    # High-volume G-layer templates remain in the immutable catalog file and
    # are loaded by Director queries; only executable C0/C2 boundaries are
    # copied into the event stream. This keeps initial replay compact and
    # avoids treating templates as historical world events.
    collections = (
        ("district", catalog.districts),
        ("character", catalog.characters),
        ("organization", catalog.organizations),
        ("location", catalog.locations),
        ("affordance", catalog.affordances),
        ("critical_item", catalog.critical_items),
    )
    for kind, entries in collections:
        for entry in entries:
            emit(
                "catalog.entry_defined",
                "system",
                {
                    "entryId": entry.id,
                    "title": entry.title,
                    "kind": kind,
                    "canonLayer": entry.canon_layer,
                    "factStatus": entry.fact_status,
                    "instantiated": entry.instantiated,
                    "sourceRefs": [value.model_dump(by_alias=True) for value in entry.sources],
                    "attributes": entry.attributes,
                },
            )

    atlas = atlas_for_scenario(package.manifest.scenario_id)
    district_runtime: dict[str, str] = {
        "candle_ward": "candle_ward",
        "old_harbor": "catalog_district_old_port",
        "iron_bay": "catalog_district_iron_bay",
        "black_slope": "catalog_district_black_slope",
        "golden_bell": "catalog_district_gold_bell",
        "saint_bridge": "catalog_district_saint_bridge",
        "white_cliff": "catalog_district_white_cliff",
    }
    district_titles: dict[str, str] = {
        "candle_ward": "烛巷区",
        "old_harbor": "老港区",
        "iron_bay": "铁湾区",
        "black_slope": "黑坡区",
        "golden_bell": "金钟区",
        "saint_bridge": "圣桥区",
        "white_cliff": "白崖区",
    }
    location_runtime_ids: dict[str, str] = {
        "L001": "white_heron_house",
        "L008": "abandoned_bakery",
    }
    location_entries = [
        entry for entry in catalog.locations
        if entry.canon_layer != "G" and entry.instantiated
    ]
    for entry in location_entries:
        catalog_location_id = str(
            entry.attributes.get("locationId", entry.id.removeprefix("LOCATION-"))
        )
        location_runtime_ids.setdefault(
            catalog_location_id,
            f"catalog_{catalog_location_id.lower()}",
        )

    if atlas is not None:
        _emit_atlas_runtime_map(
            atlas,
            package,
            location_entries,
            location_runtime_ids,
            district_runtime,
            district_titles,
            emit,
        )
    else:
        _emit_legacy_catalog_map(
            package,
            location_entries,
            location_runtime_ids,
            district_runtime,
            district_titles,
            emit,
        )

    if package.furniture_atlas is not None:
        _emit_furniture_containers(package, emit)

    # C2 opportunities are policy inputs, never confirmed world events.
    for entry in catalog.affordances:
        attrs = entry.attributes
        emit(
            "catalog.affordance_defined",
            "system",
            {
                "affordanceId": entry.id,
                "locationId": location_runtime_ids.get(
                    str(attrs.get("locationId", "")),
                    f"catalog_{str(attrs.get('locationId', '')).lower()}",
                ),
                "actionKinds": attrs.get("suggestedActionKinds", []),
                "resourceCategories": attrs.get("resourceCategories", []),
                "storyImpactCeiling": attrs.get("storyImpactCeiling", "soft"),
                "temporaryEntityKinds": attrs.get("temporaryEntityKinds", []),
                "canonLayer": entry.canon_layer,
                "sourceRefs": [value.model_dump(by_alias=True) for value in entry.sources],
            },
        )

    # Instantiate C0 characters that are not already represented by the
    # authored opening package. Regions are metadata, so atlas campaigns place
    # unknown characters at the first real location in their region.
    inventory_by_character = inventory_by_character or {}
    if not catalog_character_entries:
        catalog_character_entries = _runtime_catalog_character_entries(package)
    for entry, character_id in catalog_character_entries:
        attrs = entry.attributes
        abilities, language_style = _profile_fields_from_catalog(
            attrs,
            character_id=character_id,
            name=entry.title,
            source_refs=entry.sources,
        )
        district = str(attrs.get("primaryDistrict", "")).split("·", 1)[0]
        location_id = (
            _atlas_region_anchor_location(atlas, district)
            if atlas is not None
            else district_runtime.get(district, "gray_harbor")
        )
        emit(
            "character.created",
            "system",
            {
                "characterId": character_id,
                "characterType": "npc",
                "name": entry.title,
                "aliases": [],
                "acceptedGiftDefinitionIds": [],
                "locationId": location_id,
                "role": attrs.get("identity", "V4.2 目录人物"),
                "publicDescription": attrs.get("identity", ""),
                "organizationIds": [],
                "tags": ["v42_catalog", str(attrs.get("profileKind", "secondary"))],
                "abilities": abilities,
                "languageStyle": language_style,
                "inventoryContainerId": inventory_by_character.get(character_id),
                "decisionProfile": None,
                "catalogId": entry.id,
                "catalogCharacterId": str(attrs.get("characterId")),
                "catalogSourceRefs": [value.model_dump(by_alias=True) for value in entry.sources],
            },
            schema_version=2,
        )


def _emit_furniture_containers(package: ScenarioPackage, emit: Any) -> None:
    """Materialize validated furniture facts after the map structures exist."""

    atlas = package.furniture_atlas
    if atlas is None:
        return
    for record in sorted(atlas.records, key=lambda value: value.furniture_id):
        structure_runtime = runtime_structure_id(record.structure_id)
        emit(
            "container.created",
            "system",
            {
                "containerId": record.furniture_id,
                "kind": "furniture",
                "ownerCharacterId": None,
                "locationId": structure_runtime,
                "capacityWeight": record.capacity_weight_grams,
                "capacityVolume": record.capacity_volume_cm3,
                "furnitureKind": record.kind,
                "furnitureName": record.name,
                "furnitureDescription": record.description,
                "structureId": structure_runtime,
                "fixed": record.fixed,
                "visible": record.visible,
                "sourceStatus": record.source_status,
                "confidence": record.confidence,
                "basis": list(record.basis),
                "sourceRefs": list(record.source_refs),
                "modelAudit": dict(record.model_audit) if record.model_audit else None,
            },
        )


def _emit_atlas_runtime_map(
    atlas: MapAtlas,
    package: ScenarioPackage,
    location_entries: list[Any],
    location_runtime_ids: dict[str, str],
    district_runtime: dict[str, str],
    district_titles: dict[str, str],
    emit: Any,
) -> None:
    """Materialize the atlas hierarchy as an executable location graph.

    The atlas remains content input; these events are the runtime projection
    boundary. Streets are executable routing surfaces but never receive
    structures or building functions. Runtime state contains buildings,
    rooms, and street transit nodes. Existing valid opening IDs and hidden
    exits are preserved; legacy topology shortcuts are removed before opening.
    """
    package_location_ids = {location.id for location in package.locations}
    known_location_ids = set(package_location_ids)
    known_pairs = {
        (location.id, exit_state.to_location_id)
        for location in package.locations
        for exit_state in _filter_initial_atlas_exits(package, atlas, location)
    }
    exit_additions: dict[str, list[dict[str, Any]]] = {}

    def add_exit(
        origin_id: str,
        destination_id: str,
        label: str,
        minutes: int = 1,
        *,
        locked: bool = False,
        visible: bool = True,
        discovery_id: str | None = None,
    ) -> None:
        if origin_id == destination_id or destination_id not in known_location_ids:
            return
        pair = (origin_id, destination_id)
        if pair in known_pairs:
            return
        known_pairs.add(pair)
        exit_additions.setdefault(origin_id, []).append(
            {
                "id": f"atlas_{origin_id}_to_{destination_id}",
                "toLocationId": destination_id,
                "label": label,
                "travelMinutes": max(1, minutes),
                "visible": visible,
                "locked": locked,
                **(
                    {"discoveryId": discovery_id}
                    if discovery_id is not None
                    else {}
                ),
            }
        )

    atlas_to_runtime: dict[str, str] = {}
    non_executable_atlas_ids = {
        # These atlas records are region/city shells used to anchor source
        # data.  The seven atlas regions are metadata, not places a player can
        # enter or occupy.
        "runtime_gray_harbor",
        "runtime_candle_ward",
    }
    for location in atlas.locations:
        if location.id in non_executable_atlas_ids:
            continue
        runtime_id = (
            location.id.removeprefix("runtime_")
            if location.id.startswith("runtime_")
            else runtime_location_id(location.id)
        )
        atlas_to_runtime[location.id] = runtime_id
        catalog_entry = next(
            (
                entry
                for entry in location_entries
                if str(entry.attributes.get("locationId", ""))
                == runtime_id.removeprefix("catalog_").upper()
            ),
            None,
        )
        if runtime_id in known_location_ids:
            continue
        title = catalog_entry.title if catalog_entry is not None else location.name
        aliases = list(location.aliases)
        if catalog_entry is not None:
            aliases.append(catalog_entry.title.split("·", 1)[-1])
        emit(
            "location.created",
            "system",
            {
                "locationId": runtime_id,
                "name": title,
                "aliases": sorted(set(aliases)),
                "kind": location.kind,
                "mapVisibility": "public",
                # Atlas regions are metadata, not executable locations.
                "parentId": "gray_harbor",
                "atlasRegionId": location.region_id,
                "description": (
                    catalog_entry.attributes.get("function", "")
                    if catalog_entry is not None
                    else f"{location.surface} {location.resources}".strip()
                ),
                "exits": [],
                "atlasLocationId": location.id,
            },
            schema_version=2,
        )
        known_location_ids.add(runtime_id)

    # Streets are transit nodes, not buildings. They intentionally have no
    # structure records, room exits, inventory, or location affordances.
    street_runtime: dict[str, str] = {}
    for street in atlas.streets:
        street_runtime.setdefault(street.id, runtime_street_id(street.id))
    for street in atlas.streets:
        runtime_id = street_runtime[street.id]
        if runtime_id in known_location_ids:
            continue
        emit(
            "location.created",
            "system",
            {
                "locationId": runtime_id,
                "name": street.name,
                "aliases": [street.id],
                "kind": "street",
                "mapVisibility": "public",
                "parentId": "gray_harbor",
                "description": "连接建筑地点的公共街道通行段。",
                "exits": [],
                "atlasStreetId": street.id,
            },
            schema_version=2,
        )
        known_location_ids.add(runtime_id)

    structure_runtime: dict[str, str] = {}
    for location in atlas.locations:
        runtime_id = atlas_to_runtime.get(location.id)
        if runtime_id is None:
            continue
        for node in location.structure:
            mapped = runtime_structure_id(node.id)
            structure_runtime[node.id] = mapped
            if mapped in known_location_ids:
                continue
            emit(
                "location.created",
                "system",
                {
                    "locationId": mapped,
                    "name": node.name,
                    "aliases": [],
                    "kind": "room",
                    "mapVisibility": (
                        "gm" if _atlas_structure_is_hidden(node) else "public"
                    ),
                    "parentId": runtime_id,
                    "description": node.purpose,
                    "exits": [],
                    "atlasStructureId": node.id,
                },
                schema_version=2,
            )
            known_location_ids.add(mapped)

    for location in atlas.locations:
        runtime_id = atlas_to_runtime.get(location.id)
        if runtime_id is None:
            continue
        structures = [structure_runtime[node.id] for node in location.structure]
        if structures:
            # Ordinary and ``private`` atlas-design structures form the
            # current open interior route.  ``private`` describes a room's
            # fiction, not an automatic lock; only an explicit hidden/secret
            # node may be discovery-gated.  Chaining visible nodes directly
            # around hidden nodes keeps later ordinary rooms reachable.
            hidden = {
                index
                for index, node in enumerate(location.structure)
                if _atlas_structure_is_hidden(node)
            }
            public_indices = [
                index for index in range(len(structures)) if index not in hidden
            ]
            if public_indices:
                first_public = public_indices[0]
                add_exit(
                    runtime_id,
                    structures[first_public],
                    f"进入{location.name}",
                    1,
                )
                for previous_index, current_index in zip(
                    public_indices,
                    public_indices[1:],
                ):
                    add_exit(
                        structures[previous_index],
                        structures[current_index],
                        "前往相邻房间",
                        1,
                    )
                    add_exit(
                        structures[current_index],
                        structures[previous_index],
                        "回到相邻房间",
                        1,
                    )
            # A hidden structure is not an executable entrance by itself.  It
            # must opt into an authored discovery definition; otherwise it is
            # retained as GM-only atlas content with no fabricated edge.
            for index in sorted(hidden):
                node = location.structure[index]
                if node.discovery_id is None:
                    continue
                anchor_index = next(
                    (
                        candidate
                        for candidate in range(index - 1, -1, -1)
                        if candidate not in hidden
                    ),
                    None,
                )
                anchor = runtime_id if anchor_index is None else structures[anchor_index]
                add_exit(
                    anchor,
                    structures[index],
                    "进入未公开区域",
                    1,
                    visible=False,
                    discovery_id=node.discovery_id,
                )
                add_exit(
                    structures[index],
                    anchor,
                    "离开未公开区域",
                    1,
                    # Leaving an undiscovered hidden room is always allowed;
                    # only entering it is discovery/permission gated.
                    visible=True,
                )

        # Leaving a building reaches its street first. The elapsed time is
        # the frontage distance from the street's authored reference point.
        for street_id in location.street_ids:
            street_node = street_runtime.get(street_id)
            if street_node is None:
                continue
            minutes = _atlas_walk_minutes(atlas, max(0, location.street_position_m) / 1000)
            add_exit(street_node, runtime_id, f"进入{location.name}", minutes)
            add_exit(runtime_id, street_node, f"出门到{next(
                (street.name for street in atlas.streets if street.id == street_id),
                street_id,
            )}", minutes)
            # Street entry resolves to the first ordinary structure.  Give
            # that actual arrival node its own exterior edge as well, so the
            # player can leave a building in one action: structure -> street.
            # The building shell edge above remains as a compatibility node
            # for older commands and replayed events.
            if structures and public_indices:
                entry_structure = structures[public_indices[0]]
                add_exit(
                    entry_structure,
                    street_node,
                    f"出门到{next(
                        (street.name for street in atlas.streets if street.id == street_id),
                        street_id,
                    )}",
                    minutes,
                )

    # Street graph traversal stays on street nodes. This is the only route
    # between different street identities.
    for connection in atlas.street_connections:
        origin = street_runtime.get(connection.from_street_id)
        destination = street_runtime.get(connection.to_street_id)
        if origin is None or destination is None:
            continue
        add_exit(origin, destination, "沿街前往", _atlas_walk_minutes(atlas, connection.distance_km))

    # The city root is an orientation entry point; regions remain metadata.
    for region_id in district_runtime:
        region_streets = sorted(
            (street for street in atlas.streets if street.region_id == region_id),
            key=lambda value: (value.sequence, value.id),
        )
        if region_streets:
            add_exit(
                "gray_harbor",
                street_runtime[region_streets[0].id],
                f"前往{district_titles[region_id]}",
                15,
            )
    for origin_id, exits in exit_additions.items():
        emit("location.exits_extended", "system", {"locationId": origin_id, "exits": exits})

def _filter_initial_atlas_exits(
    package: ScenarioPackage,
    atlas: MapAtlas,
    location: LocationDefinition,
) -> list[ExitDefinition]:
    """Remove legacy direct edges before they become opening events.

    The authored package predates the atlas and contains a few shortcuts such
    as a street-to-building edge across two different streets.  Those edges
    must not enter a newly compiled event stream: the runtime atlas events
    provide the canonical street-node route instead.  Discovery-gated exits
    are deliberately retained because they describe special physical
    connections (for example the cellar drainage tunnel), not public routing.
    """
    locations = {value.id: value for value in package.locations}

    def street_ids(location_id: str) -> set[str]:
        street_id = atlas_street_id_for_runtime(location_id)
        if street_id is not None:
            return {street_id} if any(value.id == street_id for value in atlas.streets) else set()
        try:
            atlas_node_id = atlas.resolve_node_id(location_id)
        except KeyError:
            return set()
        for atlas_location in atlas.locations:
            if atlas_location.id == atlas_node_id:
                return set(atlas_location.street_ids)
            if any(node.id == atlas_node_id for node in atlas_location.structure):
                return set(atlas_location.street_ids)
        return set()

    def is_ancestor(ancestor_id: str, descendant_id: str) -> bool:
        current = locations.get(descendant_id)
        seen: set[str] = set()
        while current is not None and current.parent_id and current.id not in seen:
            if current.parent_id == ancestor_id:
                return True
            seen.add(current.id)
            current = locations.get(current.parent_id)
        return False

    def same_parent_or_ancestor(first_id: str, second_id: str) -> bool:
        return is_ancestor(first_id, second_id) or is_ancestor(second_id, first_id)

    kept: list[ExitDefinition] = []
    origin_streets = street_ids(location.id)
    for exit_definition in location.exits:
        if exit_definition.discovery_id is not None:
            kept.append(exit_definition)
            continue

        destination = locations.get(exit_definition.to_location_id)
        if destination is None:
            # Reference validation normally makes this unreachable.  Keeping
            # the edge here preserves the package compiler's existing error
            # boundary if a future package validator allows external targets.
            kept.append(exit_definition)
            continue

        destination_streets = street_ids(destination.id)
        if location.kind == "street":
            # Street exits are rebuilt from atlas street connections below.
            continue
        if location.kind == "floor" and destination.kind in {"street", "district", "city"}:
            continue
        if origin_streets and destination_streets:
            if origin_streets & destination_streets:
                kept.append(exit_definition)
            elif destination.id == location.parent_id and location.kind != "floor":
                # Leaving an authored building back to its package parent is
                # an explicit egress, even when the atlas later places the
                # building on a more specific street node.
                kept.append(exit_definition)
            # Different atlas streets must route through materialized street
            # nodes, so the authored direct edge is intentionally discarded.
            continue
        if not origin_streets and not destination_streets:
            if same_parent_or_ancestor(location.id, destination.id):
                kept.append(exit_definition)
            continue
        # A district/city edge to a building (or any other one-sided atlas
        # identity) is not a valid opening shortcut.  The atlas compiler will
        # add only the appropriate district gateway and street edges.

    return kept


def _emit_legacy_catalog_map(
    package: ScenarioPackage,
    location_entries: list[Any],
    location_runtime_ids: dict[str, str],
    district_runtime: dict[str, str],
    district_titles: dict[str, str],
    emit: Any,
) -> None:
    """Keep non-atlas campaigns on the pre-atlas catalog hierarchy."""
    prefix_to_region = {
        "烛巷": "candle_ward",
        "老港": "old_harbor",
        "铁湾": "iron_bay",
        "黑坡": "black_slope",
        "金钟": "golden_bell",
        "圣桥": "saint_bridge",
        "白崖": "white_cliff",
    }
    for region_id, runtime_id in district_runtime.items():
        if runtime_id in {location.id for location in package.locations}:
            continue
        emit(
            "location.created",
            "system",
            {
                "locationId": runtime_id,
                "name": district_titles[region_id],
                "aliases": [district_titles[region_id]],
                "kind": "district",
                "mapVisibility": "public",
                "parentId": "gray_harbor",
                "description": f"V4.2 编译目录中的{district_titles[region_id]}。",
                "exits": [],
            },
            schema_version=2,
        )
    for entry in location_entries:
        location_key = str(entry.attributes.get("locationId", entry.id.removeprefix("LOCATION-")))
        runtime_id = location_runtime_ids[location_key]
        if runtime_id in {location.id for location in package.locations}:
            continue
        prefix = entry.title.split("·", 1)[0]
        parent_id = district_runtime.get(prefix_to_region.get(prefix, ""), "gray_harbor")
        emit(
            "location.created",
            "system",
            {
                "locationId": runtime_id,
                "name": entry.title,
                "aliases": [entry.title.split("·", 1)[-1]],
                "kind": entry.attributes.get("locationType", "catalog_location"),
                "mapVisibility": "public",
                "parentId": parent_id,
                "description": entry.attributes.get("function", ""),
                "exits": [],
            },
            schema_version=2,
        )


def _atlas_walk_minutes(atlas: MapAtlas, distance_km: float) -> int:
    speed = float(atlas.speed_model.get("walkingKmh", 0))
    if distance_km <= 0 or speed <= 0:
        return 1
    return max(1, round(distance_km * 60 / speed))


def _atlas_region_anchor_location(atlas: MapAtlas, region_id: str) -> str:
    """Return a real place for a character whose source says only a region."""
    streets = sorted(
        (
            street
            for street in atlas.streets
            if street.region_id == region_id and street.exists
        ),
        key=lambda value: (value.sequence, value.id),
    )
    if not streets:
        return "gray_harbor"
    candidates = sorted(
        (
            location
            for location in atlas.locations
            if location.region_id == region_id
            and streets[0].id in location.street_ids
        ),
        key=lambda value: (value.street_position_m, value.id),
    )
    if not candidates:
        return "gray_harbor"
    return runtime_location_id(candidates[0].id)


def _atlas_structure_access_tag(node: Any) -> str | None:
    value = getattr(node, "access", None)
    return str(value).casefold() if value is not None else None


def _atlas_structure_is_hidden(node: Any) -> bool:
    tag = _atlas_structure_access_tag(node)
    # ``private`` is descriptive atlas metadata.  Ordinary internal structures
    # remain open; only the explicit typed ``hidden`` value participates in
    # discovery gating.  Names and purposes are never a rules source.
    return tag == "hidden"


def _validate_atlas_discovery_bindings(package: ScenarioPackage) -> None:
    """Validate optional discovery bindings on hidden atlas structures.

    A hidden structure without a discovery remains non-executable content. It
    must not acquire a fabricated invisible edge during bootstrap. If a
    structure opts into a discovery, that discovery must be authored in the
    scenario package so its fact, clue, conditions and exit ids are auditable.
    """

    atlas = atlas_for_scenario(package.manifest.scenario_id)
    if atlas is None:
        return
    discoveries = {value.id: value for value in package.discoveries}
    for location in atlas.locations:
        visible_indices = [
            index
            for index, node in enumerate(location.structure)
            if node.access != "hidden"
        ]
        for index, node in enumerate(location.structure):
            if node.discovery_id is None:
                continue
            discovery = discoveries.get(node.discovery_id)
            if discovery is None:
                raise ScenarioPackageError(
                    f"atlas structure discovery is undefined: {node.id} -> "
                    f"{node.discovery_id}"
                )
            previous_index = next(
                (
                    candidate
                    for candidate in range(index - 1, -1, -1)
                    if candidate in visible_indices
                ),
                None,
            )
            anchor_id = (
                runtime_location_id(location.id)
                if previous_index is None
                else runtime_structure_id(location.structure[previous_index].id)
            )
            entrance_id = (
                f"atlas_{anchor_id}_to_{runtime_structure_id(node.id)}"
            )
            if entrance_id not in discovery.exit_ids:
                raise ScenarioPackageError(
                    f"atlas structure discovery does not list generated entrance: "
                    f"{node.id} -> {entrance_id}"
                )


def _read_document(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ScenarioPackageError(f"missing required file: {path.name}") from error
    except json.JSONDecodeError as error:
        raise ScenarioPackageError(f"{path.name}: invalid JSON at line {error.lineno}") from error
    except OSError as error:
        raise ScenarioPackageError(f"cannot read {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise ScenarioPackageError(f"{path.name}: root value must be an object")
    return value


def _load_package_item_atlas(
    package_path: Path,
    items_file: ItemsFile,
    documents: dict[str, dict[str, Any]],
) -> ItemAtlas:
    """Load the item atlas named by the runtime seed and hash its contracts.

    ``items.json`` is intentionally only the materialized start-of-campaign
    instances.  The atlas remains the authoritative source for every known
    definition, including definitions that have no instance at campaign start.
    """

    atlas_relative_path = Path(items_file.atlas_file)
    if atlas_relative_path.is_absolute() or ".." in atlas_relative_path.parts:
        raise ScenarioPackageError("items.json atlasFile must stay inside the package")
    atlas_path = package_path / atlas_relative_path
    try:
        atlas = load_item_atlas(atlas_path)
    except ItemAtlasError as error:
        raise ScenarioPackageError(f"items.json atlasFile: {error}") from error

    # The field contract, primary atlas, and currency policy all affect how a
    # seed is interpreted.  Include each JSON source in the package identity,
    # rather than hashing only the generated six-instance mirror.
    atlas_document = _read_document(atlas_path)
    documents[atlas_relative_path.as_posix()] = atlas_document
    atlas_directory = atlas_relative_path.parent
    for reference_field in ("fieldContractRef", "currencySystemRef"):
        reference = atlas_document.get(reference_field)
        if not isinstance(reference, str) or Path(reference).name != reference:
            raise ScenarioPackageError(
                f"items.json atlasFile: {reference_field} must name a local JSON file"
            )
        reference_path = atlas_path.parent / reference
        documents[(atlas_directory / reference).as_posix()] = _read_document(
            reference_path
        )

    field_contract = documents[
        (atlas_directory / str(atlas_document["fieldContractRef"])).as_posix()
    ]
    fields = field_contract.get("fields")
    if fields != list(ITEM_RECORD_FIELDS):
        raise ScenarioPackageError(
            "item field specification does not match the runtime 15-field contract"
        )
    return atlas


def _validate_seed_instances_against_atlas(
    instances: list[ItemInstanceSeed],
    atlas: ItemAtlas,
) -> None:
    """Reject a seed that drifts from the atlas definitions or initial facts."""

    definitions = {str(value["id"]): value for value in atlas.definitions}
    atlas_instances = {str(value["id"]): value for value in atlas.instances}
    instance_ids = [value.id for value in instances]
    _ensure_unique("item", instance_ids)

    for instance in instances:
        definition = definitions.get(instance.definition_id)
        if definition is None:
            raise ScenarioPackageError(
                "item definitionId is not present in the item atlas: "
                f"{instance.id}->{instance.definition_id}"
            )
        record = instance.to_item_record()
        for field in (
            "definitionId",
            "name",
            "description",
            "category",
            "isPlotItem",
            "stackable",
            "unitWeightGrams",
            "valueCrown",
            "properties",
        ):
            if record[field] != definition[field]:
                raise ScenarioPackageError(
                    "item seed differs from its atlas definition: "
                    f"{instance.id}.{field}"
                )

    seed_ids = set(instance_ids)
    atlas_instance_ids = set(atlas_instances)
    if seed_ids != atlas_instance_ids:
        missing = sorted(atlas_instance_ids - seed_ids)
        unexpected = sorted(seed_ids - atlas_instance_ids)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise ScenarioPackageError(
            "items.json instances must mirror the atlas initial instances: "
            + "; ".join(details)
        )
    for instance in instances:
        expected = dict(atlas_instances[instance.id])
        if instance.to_item_record() != expected:
            raise ScenarioPackageError(
                "item seed differs from its atlas initial instance: " + instance.id
            )


def _as(
    parsed: dict[str, BaseModel],
    filename: str,
    model_type: type[BaseModel],
) -> Any:
    value = parsed[filename]
    if not isinstance(value, model_type):
        raise TypeError(f"unexpected parsed model for {filename}")
    return value


def _content_hash(documents: dict[str, dict[str, Any]]) -> str:
    canonical = json.dumps(
        documents,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def _validate_v42_catalog_binding(
    manifest: Manifest,
    catalog: V42Catalog,
) -> None:
    if catalog.scenario_id != manifest.scenario_id:
        raise ScenarioPackageError(
            "catalog scenarioId does not match manifest scenarioId"
        )
    if catalog.scenario_version != manifest.source_version:
        raise ScenarioPackageError(
            "catalog scenarioVersion does not match manifest sourceVersion"
        )
    if catalog.source_document != manifest.source_document:
        raise ScenarioPackageError(
            "catalog sourceDocument does not match manifest sourceDocument"
        )
    if catalog.source_sha256 != manifest.source_sha256:
        raise ScenarioPackageError(
            "catalog sourceSha256 does not match manifest sourceSha256"
        )


def _bootstrap_event_id(
    campaign_id: str,
    content_hash: str,
    ordinal: int,
    event_type: str,
) -> str:
    identity = f"{campaign_id}:{content_hash}:{ordinal}:{event_type}".encode("utf-8")
    return f"evt_{sha256(identity).hexdigest()[:32]}"


def _validate_narrative_guidance_safety(package: ScenarioPackage) -> None:
    """Keep author-facing secrets out of the player-safe narrator prompt."""
    forbidden_terms: list[tuple[str, str]] = []

    def forbid(source: str, *terms: str | None) -> None:
        forbidden_terms.extend(
            (source, term.strip())
            for term in terms
            if term is not None and term.strip()
        )

    for fact in package.facts:
        if fact.visibility == "gm":
            forbid("GM fact", fact.id, fact.statement)
    for condition in package.story_conditions:
        if condition.visibility == "gm":
            forbid("GM story condition", condition.id, condition.name)
    for location in package.locations:
        for exit_definition in location.exits:
            if not exit_definition.visible:
                forbid("hidden exit", exit_definition.id, exit_definition.label)
    for character in package.characters:
        forbid(
            "character private material",
            character.private_notes,
            *character.motivations,
            *character.fears,
            *character.secrets,
        )
    for organization in package.organizations:
        forbid(
            "organization private material",
            *organization.private_goals,
        )
        if organization.visibility == "gm":
            forbid(
                "GM organization",
                organization.id,
                organization.name,
                organization.public_description,
            )
    for clock in package.clocks:
        if clock.visibility == "gm":
            forbid("GM clock", clock.id, clock.name, clock.stakes)
    for obligation in package.obligations:
        if obligation.visibility == "gm":
            forbid(
                "GM obligation",
                obligation.id,
                obligation.title,
                obligation.terms,
            )

    for scene in package.scenes:
        guidance = scene.narrative_guidance
        if guidance is None:
            continue
        guidance_text = json.dumps(
            guidance.model_dump(by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
        )
        for source, term in forbidden_terms:
            if term in guidance_text:
                raise ScenarioPackageError(
                    f"scene narrativeGuidance contains {source}: {scene.id}"
                )


def _validate_references(package: ScenarioPackage) -> None:
    _ensure_unique("location", [value.id for value in package.locations])
    _ensure_unique("character", [value.id for value in package.characters])
    _ensure_unique("container", [value.id for value in package.containers])
    _ensure_unique("item", [value.id for value in package.items])
    _ensure_unique("clue", [value.id for value in package.clues])
    _ensure_unique("scene", [value.id for value in package.scenes])
    _ensure_unique("organization", [value.id for value in package.organizations])
    _ensure_unique("fact", [value.id for value in package.facts])
    _ensure_unique("clock", [value.id for value in package.clocks])
    _ensure_unique("obligation", [value.id for value in package.obligations])
    _ensure_unique("story condition", [value.id for value in package.story_conditions])
    _ensure_unique("discovery", [value.id for value in package.discoveries])
    _ensure_unique("schedule", [value.id for value in package.schedules])
    if package.furniture_atlas is not None:
        _ensure_unique(
            "furniture container",
            [value.furniture_id for value in package.furniture_atlas.records],
        )
        overlap = {
            value.furniture_id for value in package.furniture_atlas.records
        } & {value.id for value in package.containers}
        if overlap:
            raise ScenarioPackageError(
                "furniture ids collide with authored containers: "
                + ", ".join(sorted(overlap))
            )
    _ensure_unique(
        "interaction",
        [value.id for value in package.inspections]
        + [value.id for value in package.inquiries],
    )
    if package.manifest.schema_version >= 5:
        missing_guidance = [
            scene.id for scene in package.scenes
            if scene.narrative_guidance is None
        ]
        if missing_guidance:
            raise ScenarioPackageError(
                "schema version 5 requires narrativeGuidance for every scene: "
                + ", ".join(missing_guidance)
            )
        _validate_narrative_guidance_safety(package)
    if package.manifest.schema_version >= 6:
        missing_profiles = [
            character.id
            for character in package.characters
            if character.type == "npc" and character.decision_profile is None
        ]
        if missing_profiles:
            raise ScenarioPackageError(
                "schema version 6 requires decisionProfile for every NPC: "
                + ", ".join(missing_profiles)
            )
    _ensure_unique(
        "relationship",
        [f"{value.subject_id}->{value.object_id}" for value in package.relationships],
    )

    authored_catalog_ids = [
        value.catalog_character_id
        for value in package.characters
        if value.catalog_character_id is not None
    ]
    _ensure_unique("authored catalog character", authored_catalog_ids)
    if package.catalog is not None:
        available_catalog_ids = {
            str(value.attributes.get("characterId"))
            for value in package.catalog.characters
        }
        _require_all(
            "authored catalog character",
            authored_catalog_ids,
            available_catalog_ids,
        )

    locations = {value.id for value in package.locations}
    characters = {value.id: value for value in package.characters}
    containers = {value.id for value in package.containers}
    scenes = {value.id for value in package.scenes}
    organizations = {value.id for value in package.organizations}
    facts = {value.id for value in package.facts}
    clocks = {value.id for value in package.clocks}
    conditions = {value.id for value in package.story_conditions}
    discoveries = {value.id for value in package.discoveries}
    exit_ids = {
        value.id
        for location in package.locations
        for value in location.exits
        if value.id is not None
    }
    _ensure_unique(
        "exit",
        [
            value.id
            for location in package.locations
            for value in location.exits
            if value.id is not None
        ],
    )
    parties = set(characters) | organizations

    for location in package.locations:
        _require_all("location connection", location.connections, locations)
        if location.parent_id is not None:
            _require("location parent", location.parent_id, locations)
        _require_all(
            "location exit",
            [value.to_location_id for value in location.exits],
            locations,
        )
        for exit_definition in location.exits:
            if package.manifest.schema_version >= 3 and exit_definition.id is None:
                raise ScenarioPackageError(
                    f"schema version 3 exit requires id: {location.id}"
                )
            _require_all("exit key item", exit_definition.key_item_ids, {
                value.id for value in package.items
            })
            _require_all(
                "exit condition",
                exit_definition.required_condition_ids,
                conditions,
            )
            if exit_definition.discovery_id is not None:
                _require("exit discovery", exit_definition.discovery_id, discoveries)
    _validate_location_hierarchy(package.locations)
    for character in package.characters:
        _require("character location", character.location_id, locations)
        _require_all(
            "accepted gift definition",
            character.accepted_gift_definition_ids,
            package.item_definition_ids,
        )
        _require_all("character organization", character.organization_ids, organizations)
    for schedule in package.schedules:
        _require("schedule character", schedule.character_id, set(characters))
        if characters[schedule.character_id].type != "npc":
            raise ScenarioPackageError(
                f"schedule character must be npc: {schedule.character_id}"
            )
        _require("schedule location", schedule.location_id, locations)
    for container in package.containers:
        if container.owner_character_id is not None:
            _require("container owner", container.owner_character_id, set(characters))
        if container.location_id is not None:
            _require("container location", container.location_id, locations)
    for item in package.items:
        if item.container_id is not None:
            _require("item container", item.container_id, containers)
        if item.location_id is not None:
            _require("item location", item.location_id, locations)
    for relationship in package.relationships:
        _require("relationship subject", relationship.subject_id, set(characters))
        _require("relationship object", relationship.object_id, set(characters))
    for clue in package.clues:
        _require_all("clue knower", clue.initially_known_by, set(characters))
        if package.manifest.schema_version >= 2:
            _require("clue fact", clue.fact_id, facts)
    for scene in package.scenes:
        _require("scene location", scene.location_id, locations)
        _require_all("scene character", scene.present_character_ids, set(characters))
    for organization in package.organizations:
        if organization.headquarters_location_id is not None:
            _require(
                "organization headquarters",
                organization.headquarters_location_id,
                locations,
            )
        _require_all(
            "organization leader",
            organization.leader_character_ids,
            set(characters),
        )
        _require_all(
            "organization member",
            organization.member_character_ids,
            set(characters),
        )
    for fact in package.facts:
        _require_all("fact knower", fact.initially_known_by, set(characters))
    for obligation in package.obligations:
        _require("obligation debtor", obligation.debtor_id, parties)
        _require("obligation creditor", obligation.creditor_id, parties)
        if obligation.due_clock_id is not None:
            _require("obligation clock", obligation.due_clock_id, clocks)
        _require_all("obligation evidence fact", obligation.evidence_fact_ids, facts)
    for discovery in package.discoveries:
        _require("discovery location", discovery.location_id, locations)
        _require("discovery fact", discovery.fact_id, facts)
        _require("discovery clue", discovery.clue_id, {value.id for value in package.clues})
        _require_all("discovery exit", discovery.exit_ids, exit_ids)
        _require_all(
            "discovery condition",
            discovery.required_condition_ids,
            conditions,
        )
        _require_all(
            "discovery knower",
            discovery.initially_known_by,
            set(characters),
        )
    clue_ids = {value.id for value in package.clues}
    item_ids = {value.id for value in package.items}
    for inspection in package.inspections:
        _require("inspection item", inspection.target_item_id, item_ids)
        _require_all(
            "inspection actor knowledge fact",
            inspection.required_actor_knowledge_fact_ids,
            facts,
        )
        _require_all("inspection revealed fact", inspection.revealed_fact_ids, facts)
        _require_all("inspection clue", inspection.clue_ids, clue_ids)
        for clue_id in inspection.clue_ids:
            clue = next(value for value in package.clues if value.id == clue_id)
            if clue.fact_id not in inspection.revealed_fact_ids:
                raise ScenarioPackageError(
                    f"inspection clue fact must be revealed: {inspection.id}->{clue_id}"
                )
    for inquiry in package.inquiries:
        _require("inquiry target", inquiry.target_character_id, set(characters))
        if characters[inquiry.target_character_id].type != "npc":
            raise ScenarioPackageError(
                f"inquiry target must be npc: {inquiry.target_character_id}"
            )
        _require_all(
            "inquiry actor knowledge fact",
            inquiry.required_actor_knowledge_fact_ids,
            facts,
        )
        _require_all(
            "inquiry npc knowledge fact",
            inquiry.required_npc_knowledge_fact_ids,
            facts,
        )
        _require_all("inquiry revealed fact", inquiry.revealed_fact_ids, facts)
        _require_all("inquiry clue", inquiry.clue_ids, clue_ids)
        for clue_id in inquiry.clue_ids:
            clue = next(value for value in package.clues if value.id == clue_id)
            if clue.fact_id not in inquiry.revealed_fact_ids:
                raise ScenarioPackageError(
                    f"inquiry clue fact must be revealed: {inquiry.id}->{clue_id}"
                )

    _require("initial scene", package.manifest.initial_scene_id, scenes)
    _require("player character", package.manifest.player_character_id, set(characters))
    if characters[package.manifest.player_character_id].type != "player":
        raise ScenarioPackageError("player character must have type 'player'")


def _validate_location_hierarchy(locations: tuple[LocationDefinition, ...]) -> None:
    parents = {value.id: value.parent_id for value in locations}
    for location_id in parents:
        visited: set[str] = set()
        current: str | None = location_id
        while current is not None:
            if current in visited:
                raise ScenarioPackageError(
                    f"location hierarchy contains a cycle at: {current}"
                )
            visited.add(current)
            current = parents[current]


def _ensure_unique(label: str, identifiers: list[str]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for identifier in identifiers:
        if identifier in seen:
            duplicates.add(identifier)
        seen.add(identifier)
    if duplicates:
        values = ", ".join(sorted(duplicates))
        raise ScenarioPackageError(f"duplicate {label} id: {values}")


def _require(label: str, identifier: str, available: set[str]) -> None:
    if identifier not in available:
        raise ScenarioPackageError(f"missing {label} reference: {identifier}")


def _require_all(label: str, identifiers: list[str], available: set[str]) -> None:
    for identifier in identifiers:
        _require(label, identifier, available)
