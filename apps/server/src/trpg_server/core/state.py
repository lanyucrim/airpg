from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from trpg_server.items.models import ItemContainer, ItemInstance


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    event_type: str
    actor_id: str
    world_time: int
    payload: dict[str, Any]
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class RawMessage:
    message_id: str
    campaign_id: str
    turn_id: str
    speaker_type: Literal["player", "narrator", "system"]
    speaker_id: str
    message_kind: Literal["player_input", "narration", "system"]
    content: str
    world_time: int
    authority: Literal["utterance_only", "narration_only", "system_record"]
    token_count: int | None = None


@dataclass(frozen=True, slots=True)
class DecisionProfileState:
    monthly_income_pence: int
    economic_pressure: int
    gift_openness: int
    greed: int
    integrity: int
    risk_aversion: int
    institutional_loyalty: int
    corruption_openness: int
    hard_refusals: tuple[str, ...]
    source_event_id: str


@dataclass(slots=True)
class RelationshipState:
    favor: int = 0
    trust: int = 0
    fear: int = 0
    respect: int = 0
    suspicion: int = 0
    debt: int = 0
    sources: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExitState:
    exit_id: str
    to_location_id: str
    label: str
    travel_minutes: int
    visible: bool = True
    locked: bool = False
    key_item_ids: tuple[str, ...] = ()
    required_condition_ids: tuple[str, ...] = ()
    discovery_id: str | None = None


@dataclass(slots=True)
class LocationState:
    location_id: str
    name: str
    aliases: tuple[str, ...] = ()
    kind: str = "area"
    map_visibility: Literal["public", "player", "gm"] = "public"
    parent_id: str | None = None
    description: str = ""
    exits: tuple[ExitState, ...] = ()


@dataclass(slots=True)
class CalendarState:
    era: str
    year: int
    month: int
    day: int
    hour: int
    minute: int
    origin_world_time: int
    days_per_month: int = 30
    months_per_year: int = 12


@dataclass(slots=True)
class OrganizationState:
    organization_id: str
    name: str
    organization_type: str
    visibility: str = "public"
    headquarters_location_id: str | None = None
    leader_character_ids: tuple[str, ...] = ()
    member_character_ids: tuple[str, ...] = ()
    public_description: str = ""
    private_goals: tuple[str, ...] = ()
    resource_tags: tuple[str, ...] = ()
    policy_tags: tuple[str, ...] = ()


@dataclass(slots=True)
class ClockState:
    clock_id: str
    name: str
    starts_at: int
    deadline_at: int
    status: str
    visibility: str
    stakes: str = ""


@dataclass(slots=True)
class ObligationState:
    obligation_id: str
    title: str
    kind: str
    debtor_id: str
    creditor_id: str
    status: str
    terms: str
    due_clock_id: str | None
    evidence_fact_ids: tuple[str, ...]
    visibility: str


@dataclass(slots=True)
class StoryConditionState:
    condition_id: str
    name: str
    active: bool
    visibility: str = "gm"


@dataclass(frozen=True, slots=True)
class CognitionState:
    character_id: str
    proposition_id: str
    status: Literal["known", "believed", "suspected", "denied"]
    source_event_id: str
    source_kind: Literal[
        "witness", "told", "document", "faction_report", "rumor", "inference", "system"
    ]
    acquired_at: int
    scope_id: str | None = None
    confidence: int = 100
    expires_at: int | None = None


@dataclass(frozen=True, slots=True)
class EffectState:
    effect_id: str
    effect_type: str
    subject_id: str
    object_id: str | None
    scope_id: str | None
    value: int
    source_event_id: str
    created_at: int
    expires_at: int | None = None
    status: Literal["active", "consumed", "expired"] = "active"


@dataclass(frozen=True, slots=True)
class WantedState:
    wanted_id: str
    subject_id: str
    jurisdiction_id: str
    source_event_id: str
    issued_at: int
    status: Literal["active", "cleared", "expired"] = "active"


@dataclass(frozen=True, slots=True)
class NpcScheduleState:
    schedule_id: str
    character_id: str
    weekday: int
    start_minute: int
    end_minute: int
    location_id: str
    availability: Literal["public", "appointment", "private", "unavailable"]
    priority: int = 0


@dataclass(frozen=True, slots=True)
class ObservedAffordanceState:
    opportunity_id: str
    location_id: str
    action_kind: str
    resource_kind: str
    source_policy: str
    story_impact_ceiling: str
    observed_at: int
    source_event_id: str


@dataclass(frozen=True, slots=True)
class CatalogAffordanceState:
    affordance_id: str
    location_id: str
    action_kinds: tuple[str, ...]
    resource_categories: tuple[str, ...]
    story_impact_ceiling: str
    temporary_entity_kinds: tuple[str, ...]
    canon_layer: str
    source_refs: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class CatalogEntryState:
    entry_id: str
    title: str
    kind: str
    canon_layer: str
    fact_status: str
    instantiated: bool
    source_refs: tuple[dict[str, Any], ...]
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SceneIssueState:
    issue_id: str
    title: str
    status: Literal["open", "resolved", "expired"]
    source_event_id: str
    created_at: int
    ends_at: int | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryState:
    discovery_id: str
    location_id: str
    aliases: tuple[str, ...]
    fact_id: str
    clue_id: str
    exit_ids: tuple[str, ...]
    required_condition_ids: tuple[str, ...]
    initially_known_by: tuple[str, ...]
    time_minutes: int
    reveal_text: str


@dataclass(frozen=True, slots=True)
class InspectionState:
    interaction_id: str
    label: str
    suggested_prompt: str
    target_item_id: str
    aliases: tuple[str, ...]
    access_policy: str
    required_actor_knowledge_fact_ids: tuple[str, ...]
    revealed_fact_ids: tuple[str, ...]
    clue_ids: tuple[str, ...]
    time_minutes: int
    reveal_text: str
    repeat_text: str


@dataclass(frozen=True, slots=True)
class InquiryState:
    interaction_id: str
    label: str
    suggested_prompt: str
    target_character_id: str
    topic: str
    aliases: tuple[str, ...]
    required_actor_knowledge_fact_ids: tuple[str, ...]
    required_npc_knowledge_fact_ids: tuple[str, ...]
    revealed_fact_ids: tuple[str, ...]
    clue_ids: tuple[str, ...]
    time_minutes: int
    response_text: str
    repeat_text: str
    unknown_text: str


@dataclass(slots=True)
class Projection:
    campaign_id: str
    name: str = ""
    scenario_id: str | None = None
    scenario_version: str | None = None
    scenario_content_hash: str | None = None
    scenario_source_version: str | None = None
    scenario_source_document: str | None = None
    scenario_source_sha256: str | None = None
    scenario_catalog_schema_version: int | None = None
    player_character_id: str = "player"
    world_time: int = 0
    state_version: int = 0
    scene_id: str | None = None
    location_id: str | None = None
    scene_phase: str = "exploration"
    scene_beat: int = 0
    character_locations: dict[str, str] = field(default_factory=dict)
    character_names: dict[str, str] = field(default_factory=dict)
    character_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    character_types: dict[str, str] = field(default_factory=dict)
    # Character-owned physical state is projected from character-domain
    # events.  The dictionaries stay JSON-shaped here so Projection remains
    # the cross-domain aggregate without importing body rules into core.
    character_equipment: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=dict
    )
    character_external_injuries: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=dict
    )
    accepted_gift_definition_ids: dict[str, set[str]] = field(default_factory=dict)
    decision_profiles: dict[str, DecisionProfileState] = field(default_factory=dict)
    location_names: dict[str, str] = field(default_factory=dict)
    locations: dict[str, LocationState] = field(default_factory=dict)
    calendar: CalendarState | None = None
    character_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    organizations: dict[str, OrganizationState] = field(default_factory=dict)
    world_facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    clocks: dict[str, ClockState] = field(default_factory=dict)
    obligations: dict[str, ObligationState] = field(default_factory=dict)
    story_conditions: dict[str, StoryConditionState] = field(default_factory=dict)
    discovery_definitions: dict[str, DiscoveryState] = field(default_factory=dict)
    inspection_definitions: dict[str, InspectionState] = field(default_factory=dict)
    inquiry_definitions: dict[str, InquiryState] = field(default_factory=dict)
    completed_interactions: dict[str, set[str]] = field(default_factory=dict)
    discovered_exits: dict[str, set[str]] = field(default_factory=dict)
    containers: dict[str, "ItemContainer"] = field(default_factory=dict)
    items: dict[str, "ItemInstance"] = field(default_factory=dict)
    relationships: dict[tuple[str, str], RelationshipState] = field(default_factory=dict)
    knowledge: dict[str, set[str]] = field(default_factory=dict)
    cognitions: dict[tuple[str, str], CognitionState] = field(default_factory=dict)
    cognition_history: list[CognitionState] = field(default_factory=list)
    effects: dict[str, EffectState] = field(default_factory=dict)
    wanted: dict[str, WantedState] = field(default_factory=dict)
    legal_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending_notices: dict[str, dict[str, Any]] = field(default_factory=dict)
    npc_schedules: dict[str, NpcScheduleState] = field(default_factory=dict)
    observed_affordances: dict[str, ObservedAffordanceState] = field(default_factory=dict)
    catalog_affordances: dict[str, CatalogAffordanceState] = field(default_factory=dict)
    catalog_entries: dict[str, CatalogEntryState] = field(default_factory=dict)
    scene_issues: dict[str, SceneIssueState] = field(default_factory=dict)
    world_reports: list[dict[str, Any]] = field(default_factory=list)
    world_settlements: dict[str, dict[str, Any]] = field(default_factory=dict)
    weather_by_date: dict[str, dict[str, Any]] = field(default_factory=dict)
    market_prices: dict[str, int] = field(default_factory=dict)
    market_inventory: dict[str, int] = field(default_factory=dict)
    commerce_offers: dict[str, dict[str, Any]] = field(default_factory=dict)
    commerce_transactions: dict[str, dict[str, Any]] = field(default_factory=dict)
    work_opportunities: dict[str, dict[str, Any]] = field(default_factory=dict)
    organization_plans: dict[str, dict[str, Any]] = field(default_factory=dict)
    clues: dict[str, dict[str, Any]] = field(default_factory=dict)
    clue_definitions: dict[str, dict[str, Any]] = field(default_factory=dict)
    accepted_gifts: list[tuple[str, str, str, str]] = field(default_factory=list)
    scene_title: str = ""
    scene_objective: str = ""
    scene_opening_text: str = ""
    scene_present_character_ids: tuple[str, ...] = ()
    scene_narrative_premise: str = ""
    scene_narrative_anchors: tuple[str, ...] = ()
    scene_flexible_approaches: tuple[str, ...] = ()
    scene_stop_before: tuple[str, ...] = ()
    max_major_beats_per_turn: int = 1
    confirmed_event_ids: set[str] = field(default_factory=set)
    event_types_by_id: dict[str, str] = field(default_factory=dict)

    def relationship(self, subject_id: str, object_id: str) -> RelationshipState:
        key = (subject_id, object_id)
        if key not in self.relationships:
            self.relationships[key] = RelationshipState()
        return self.relationships[key]

@dataclass(frozen=True, slots=True)
class ParsedCommand:
    action_type: str
    actor_id: str
    target_id: str | None
    parameters: dict[str, Any]
    original_text: str
    claimed_outcome: str | None = None
    authority: Literal["player", "system", "world"] = "system"
    resolution_required: bool = True
    source_message_ids: tuple[str, ...] = ()
    parser_source: Literal["local", "model", "model_fallback"] = "local"
    parser_model: str | None = None
    parser_failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class DecisionReason:
    code: str
    label: str
    direction: Literal["positive", "negative", "neutral"]
    value: int | None = None
    source_event_id: str | None = None


@dataclass(slots=True)
class Resolution:
    status: Literal["committed", "rejected"]
    outcome: str
    narrative: str
    command: ParsedCommand
    events: list[Event] = field(default_factory=list)
    reasons: list[DecisionReason] = field(default_factory=list)
    visible_changes: list[str] = field(default_factory=list)
