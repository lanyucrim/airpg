from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable, Literal, Mapping, Sequence

from trpg_server.core.state import Event, Projection


MEMORY_SCHEMA_VERSION = 2
MEMORY_EVENT_TYPES = frozenset({
    "gift.accepted",
    "gift.rejected",
    "gift.countered",
    "gift.delayed",
    "gift.tested",
    "bribe.accepted",
    "bribe.rejected",
    "bribe.countered",
    "bribe.delayed",
    "bribe.tested",
    "relationship.changed",
    "character.moved",
})


@dataclass(frozen=True, slots=True)
class MemoryEntity:
    entity_id: str
    role: Literal[
        "actor",
        "target",
        "item",
        "subject",
        "object",
        "character",
        "from_location",
        "to_location",
    ]


@dataclass(frozen=True, slots=True)
class MemoryScope:
    scope_kind: Literal["player", "npc"]
    scope_id: str


@dataclass(frozen=True, slots=True)
class EpisodicMemory:
    memory_id: str
    campaign_id: str
    source_event_id: str
    schema_version: Literal[2]
    memory_type: Literal["interaction", "relationship", "state_change"]
    event_type: str
    summary: str
    importance: int
    world_time: int
    location_id: str | None
    status: Literal["active"]
    update_key: str | None
    entities: tuple[MemoryEntity, ...]
    scopes: tuple[MemoryScope, ...]


@dataclass(frozen=True, slots=True)
class MemoryLink:
    link_id: str
    campaign_id: str
    source_memory_id: str
    target_memory_id: str
    relation_type: Literal["updates", "caused_by"]
    source_event_id: str
    schema_version: Literal[1] = 1


@dataclass(frozen=True, slots=True)
class SceneMemorySummary:
    summary_id: str
    campaign_id: str
    scene_id: str
    segment_index: int
    location_id: str | None
    schema_version: Literal[1]
    content: str
    start_sequence: int
    end_sequence: int
    start_world_time: int
    end_world_time: int
    generator: Literal["python-template"]
    generator_version: Literal[1]
    status: Literal["rolling", "closed"]
    resolved_items: tuple[str, ...]
    unresolved_items: tuple[str, ...]
    source_sequences: tuple[int, ...]
    source_event_ids: tuple[str, ...]
    memory_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MemoryReadModel:
    memories: tuple[EpisodicMemory, ...]
    links: tuple[MemoryLink, ...]
    scene_summaries: tuple[SceneMemorySummary, ...]


TimeMode = Literal["any", "earliest", "latest", "before", "after", "between"]
RetrievalMode = Literal["structured", "fts", "hybrid"]
RejectionReason = Literal[
    "campaign_mismatch",
    "entity_mismatch",
    "event_type_mismatch",
    "scope_mismatch",
    "time_mismatch",
    "inactive",
    "budget_exceeded",
]


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    campaign_id: str
    purpose: Literal["npc_decision", "debug"]
    perspective_kind: Literal["player", "npc"]
    perspective_id: str
    information_need: Literal["historical", "current"] = "historical"
    entity_ids: tuple[str, ...] = ()
    event_types: tuple[str, ...] = ()
    time_mode: TimeMode = "any"
    time_start: int | None = None
    time_end: int | None = None
    limit: int = 12
    character_budget: int = 4_000
    search_text: str | None = None
    candidate_limit: int = 200
    expand_links: bool = False
    retrieval_mode: RetrievalMode = "structured"

    def __post_init__(self) -> None:
        if not self.campaign_id or not self.perspective_id:
            raise ValueError("campaign and perspective are required")
        if len(set(self.entity_ids)) != len(self.entity_ids):
            raise ValueError("entity_ids must be unique")
        if len(set(self.event_types)) != len(self.event_types):
            raise ValueError("event_types must be unique")
        if self.limit < 1 or self.limit > 100:
            raise ValueError("limit must be between 1 and 100")
        if self.character_budget < 1:
            raise ValueError("character_budget must be positive")
        if self.candidate_limit < 1 or self.candidate_limit > 1_000:
            raise ValueError("candidate_limit must be between 1 and 1000")
        if self.search_text is not None and not self.search_text.strip():
            raise ValueError("search_text cannot be blank")
        if self.retrieval_mode in {"fts", "hybrid"} and self.search_text is None:
            raise ValueError(f"{self.retrieval_mode} retrieval requires search_text")
        if self.time_mode in {"before", "after"} and self.time_start is None:
            raise ValueError(f"{self.time_mode} requires time_start")
        if self.time_mode == "between":
            if self.time_start is None or self.time_end is None:
                raise ValueError("between requires both time bounds")
            if self.time_start > self.time_end:
                raise ValueError("time_start cannot be after time_end")


@dataclass(frozen=True, slots=True)
class RejectedMemory:
    memory_id: str
    reason: RejectionReason


@dataclass(frozen=True, slots=True)
class MemorySelection:
    candidate_ids: tuple[str, ...]
    selected: tuple[EpisodicMemory, ...]
    rejected: tuple[RejectedMemory, ...]
    used_characters: int
    route: Literal["episodic_memory", "current_state_required"]
    candidate_total: int = 0
    candidate_limit: int = 0
    truncated: bool = False
    expanded_ids: tuple[str, ...] = ()


def project_event_to_memory(
    campaign_id: str,
    event: Event,
    state_at_event: Projection,
) -> EpisodicMemory | None:
    """Project a confirmed event into a rebuildable memory record.

    The caller must only pass committed events in event-log order. Messages,
    narration, and model output have no path into this projector.
    """
    if event.event_type not in MEMORY_EVENT_TYPES:
        return None
    payload = event.payload
    if event.event_type == "character.moved":
        return _movement_memory(campaign_id, event, state_at_event)
    if event.event_type == "relationship.changed":
        return _relationship_memory(campaign_id, event, state_at_event)

    actor_id = _required_payload_id(payload, "actorId")
    target_id = _required_payload_id(payload, "targetId")
    item_id = _required_payload_id(payload, "itemId")
    item = state_at_event.items.get(item_id)
    item_name = item.name if item is not None else item_id
    actor_name = state_at_event.character_names.get(actor_id, actor_id)
    target_name = state_at_event.character_names.get(target_id, target_id)
    family, outcome = event.event_type.split(".", 1)
    summary = _interaction_summary(
        family,
        outcome,
        actor_name,
        target_name,
        item_name,
    )
    return EpisodicMemory(
        memory_id=_memory_id(event.event_id),
        campaign_id=campaign_id,
        source_event_id=event.event_id,
        schema_version=MEMORY_SCHEMA_VERSION,
        memory_type="interaction",
        event_type=event.event_type,
        summary=summary,
        importance=_interaction_importance(family, outcome),
        world_time=event.world_time,
        location_id=state_at_event.character_locations.get(target_id),
        status="active",
        update_key=None,
        entities=(
            MemoryEntity(actor_id, "actor"),
            MemoryEntity(target_id, "target"),
            MemoryEntity(item_id, "item"),
        ),
        scopes=_participant_scopes(state_at_event, actor_id, target_id),
    )


def project_event_stream(
    campaign_id: str,
    events: Iterable[Event],
) -> list[EpisodicMemory]:
    """Rebuild the memory read model deterministically from the event stream."""
    return list(project_memory_read_model(campaign_id, events).memories)


def project_memory_read_model(
    campaign_id: str,
    events: Iterable[Event],
) -> MemoryReadModel:
    """Build event memories, explicit links, and scene segments in one replay."""
    return _project_memory_model(
        campaign_id,
        events,
        Projection(campaign_id),
        start_sequence=1,
        scene_segment=0,
        source_memory_ids={},
        latest_update_memory_ids={},
    )


def project_memory_delta(
    campaign_id: str,
    events: Iterable[Event],
    state_at_start: Projection,
    *,
    start_sequence: int,
    scene_segment: int,
    source_memory_ids: Mapping[str, str],
    latest_update_memory_ids: Mapping[str, str],
) -> MemoryReadModel:
    """Project only newly committed events against an existing read model."""
    if start_sequence < 1 or scene_segment < 0:
        raise ValueError("memory delta sequence and scene segment must be non-negative")
    return _project_memory_model(
        campaign_id,
        events,
        state_at_start,
        start_sequence=start_sequence,
        scene_segment=scene_segment,
        source_memory_ids=source_memory_ids,
        latest_update_memory_ids=latest_update_memory_ids,
    )


def _project_memory_model(
    campaign_id: str,
    events: Iterable[Event],
    state: Projection,
    *,
    start_sequence: int,
    scene_segment: int,
    source_memory_ids: Mapping[str, str],
    latest_update_memory_ids: Mapping[str, str],
) -> MemoryReadModel:
    from trpg_server.core.projection import apply_event

    memories: list[EpisodicMemory] = []
    links: list[MemoryLink] = []
    by_source_event = dict(source_memory_ids)
    latest_by_update_key = dict(latest_update_memory_ids)
    summary_groups: dict[
        tuple[str, int, str | None],
        dict[str, object],
    ] = {}
    for sequence, event in enumerate(events, start=start_sequence):
        if event.event_type == "scene.started":
            scene_segment = 0

        memory = project_event_to_memory(campaign_id, event, state)
        if memory is not None:
            memories.append(memory)
            by_source_event[memory.source_event_id] = memory.memory_id
            if memory.update_key is not None:
                previous_id = latest_by_update_key.get(memory.update_key)
                if previous_id is not None:
                    links.append(_memory_link(memory, previous_id, "updates"))
                latest_by_update_key[memory.update_key] = memory.memory_id
            causal_event_id = event.payload.get("sourceEventId")
            causal_memory_id = by_source_event.get(str(causal_event_id))
            if causal_memory_id is not None:
                links.append(_memory_link(memory, causal_memory_id, "caused_by"))
            if state.scene_id is not None:
                key = (state.scene_id, scene_segment, state.location_id)
                group = summary_groups.setdefault(key, {
                    "start_sequence": sequence,
                    "end_sequence": sequence,
                    "start_world_time": memory.world_time,
                    "end_world_time": memory.world_time,
                    "status": "rolling",
                    "scene_objective": state.scene_objective,
                    "memories": [],
                })
                group["end_sequence"] = sequence
                group["end_world_time"] = memory.world_time
                group_memories = group["memories"]
                assert isinstance(group_memories, list)
                group_memories.append((sequence, memory))

        if event.event_type == "scene.location_changed" and state.scene_id is not None:
            key = (state.scene_id, scene_segment, state.location_id)
            group = summary_groups.get(key)
            if group is not None:
                group["status"] = "closed"

        apply_event(state, event)
        if event.event_type == "scene.location_changed":
            scene_segment += 1

    summaries = tuple(
        _scene_summary(campaign_id, key, value)
        for key, value in sorted(
            summary_groups.items(),
            key=lambda item: (item[0][0], item[0][1], item[0][2] or ""),
        )
    )
    return MemoryReadModel(tuple(memories), tuple(links), summaries)


def select_memories(
    candidates: Sequence[EpisodicMemory],
    query: MemoryQuery,
    *,
    candidate_total: int | None = None,
    truncated: bool = False,
    expanded_ids: Sequence[str] = (),
) -> MemorySelection:
    """Apply deterministic scope, time and budget bounds to candidates."""
    candidate_ids = tuple(value.memory_id for value in candidates)
    if query.information_need == "current":
        return MemorySelection(
            candidate_ids, (), (), 0, "current_state_required",
            candidate_total=(len(candidates) if candidate_total is None else candidate_total),
            candidate_limit=query.candidate_limit,
            truncated=truncated,
            expanded_ids=tuple(expanded_ids),
        )

    accepted: list[EpisodicMemory] = []
    rejected: list[RejectedMemory] = []
    required_entities = set(query.entity_ids)
    allowed_types = set(query.event_types)
    for memory in candidates:
        reason = _rejection_reason(memory, query, required_entities, allowed_types)
        if reason is not None:
            rejected.append(RejectedMemory(memory.memory_id, reason))
        else:
            accepted.append(memory)

    reverse_time = query.time_mode != "earliest"
    accepted.sort(
        key=lambda value: (
            value.world_time if reverse_time else -value.world_time,
            value.importance,
            value.memory_id,
        ),
        reverse=True,
    )
    selected: list[EpisodicMemory] = []
    used_characters = 0
    for memory in accepted:
        size = len(memory.summary)
        if len(selected) >= query.limit or used_characters + size > query.character_budget:
            rejected.append(RejectedMemory(memory.memory_id, "budget_exceeded"))
            continue
        selected.append(memory)
        used_characters += size
    return MemorySelection(
        candidate_ids,
        tuple(selected),
        tuple(rejected),
        used_characters,
        "episodic_memory",
        candidate_total=(len(candidates) if candidate_total is None else candidate_total),
        candidate_limit=query.candidate_limit,
        truncated=truncated,
        expanded_ids=tuple(expanded_ids),
    )


def _relationship_memory(
    campaign_id: str,
    event: Event,
    state: Projection,
) -> EpisodicMemory:
    payload = event.payload
    subject_id = _required_payload_id(payload, "subjectId")
    object_id = _required_payload_id(payload, "objectId")
    dimension = _required_payload_id(payload, "dimension")
    delta = int(payload.get("delta", 0))
    subject_name = state.character_names.get(subject_id, subject_id)
    object_name = state.character_names.get(object_id, object_id)
    labels = {
        "favor": "好感",
        "trust": "信任",
        "fear": "恐惧",
        "respect": "尊重",
        "suspicion": "怀疑",
        "debt": "人情债",
    }
    summary = (
        f"{subject_name}因一次已确认事件改变了对{object_name}的"
        f"{labels.get(dimension, dimension)}。"
    )
    return EpisodicMemory(
        memory_id=_memory_id(event.event_id),
        campaign_id=campaign_id,
        source_event_id=event.event_id,
        schema_version=MEMORY_SCHEMA_VERSION,
        memory_type="relationship",
        event_type=event.event_type,
        summary=summary,
        importance=min(90, 50 + abs(delta) * 5),
        world_time=event.world_time,
        location_id=state.character_locations.get(subject_id),
        status="active",
        update_key=None,
        entities=(
            MemoryEntity(subject_id, "subject"),
            MemoryEntity(object_id, "object"),
        ),
        scopes=(_scope_for_character(state, subject_id),),
    )


def _movement_memory(
    campaign_id: str,
    event: Event,
    state: Projection,
) -> EpisodicMemory:
    payload = event.payload
    character_id = _required_payload_id(payload, "characterId")
    from_location_id = _required_payload_id(payload, "fromLocationId")
    to_location_id = _required_payload_id(payload, "toLocationId")
    character_name = state.character_names.get(character_id, character_id)
    from_name = state.location_names.get(from_location_id, from_location_id)
    to_name = state.location_names.get(to_location_id, to_location_id)
    return EpisodicMemory(
        memory_id=_memory_id(event.event_id),
        campaign_id=campaign_id,
        source_event_id=event.event_id,
        schema_version=MEMORY_SCHEMA_VERSION,
        memory_type="state_change",
        event_type=event.event_type,
        summary=f"{character_name}从{from_name}移动到了{to_name}。",
        importance=55,
        world_time=event.world_time,
        location_id=to_location_id,
        status="active",
        update_key=f"character_location:{character_id}",
        entities=(
            MemoryEntity(character_id, "character"),
            MemoryEntity(from_location_id, "from_location"),
            MemoryEntity(to_location_id, "to_location"),
        ),
        scopes=(_scope_for_character(state, character_id),),
    )


def _memory_link(
    source: EpisodicMemory,
    target_memory_id: str,
    relation_type: Literal["updates", "caused_by"],
) -> MemoryLink:
    digest = sha256(
        f"{source.memory_id}|{relation_type}|{target_memory_id}".encode()
    ).hexdigest()[:24]
    return MemoryLink(
        link_id=f"mlink_{digest}",
        campaign_id=source.campaign_id,
        source_memory_id=source.memory_id,
        target_memory_id=target_memory_id,
        relation_type=relation_type,
        source_event_id=source.source_event_id,
    )


def _scene_summary(
    campaign_id: str,
    key: tuple[str, int, str | None],
    group: dict[str, object],
) -> SceneMemorySummary:
    scene_id, segment_index, location_id = key
    memories = group["memories"]
    assert isinstance(memories, list)
    typed_entries = [
        value
        for value in memories
        if (
            isinstance(value, tuple)
            and len(value) == 2
            and isinstance(value[0], int)
            and isinstance(value[1], EpisodicMemory)
        )
    ]
    typed_memories = [value[1] for value in typed_entries]
    return SceneMemorySummary(
        summary_id=f"scnmem_{scene_id}_{segment_index}",
        campaign_id=campaign_id,
        scene_id=scene_id,
        segment_index=segment_index,
        location_id=location_id,
        schema_version=1,
        content=" ".join(memory.summary for memory in typed_memories),
        start_sequence=int(group["start_sequence"]),
        end_sequence=int(group["end_sequence"]),
        start_world_time=int(group["start_world_time"]),
        end_world_time=int(group["end_world_time"]),
        generator="python-template",
        generator_version=1,
        status=str(group["status"]),  # type: ignore[arg-type]
        resolved_items=(),
        unresolved_items=(
            (str(group["scene_objective"]),)
            if group.get("scene_objective")
            else ()
        ),
        source_sequences=tuple(value[0] for value in typed_entries),
        source_event_ids=tuple(memory.source_event_id for memory in typed_memories),
        memory_ids=tuple(memory.memory_id for memory in typed_memories),
    )


def _rejection_reason(
    memory: EpisodicMemory,
    query: MemoryQuery,
    required_entities: set[str],
    allowed_types: set[str],
) -> RejectionReason | None:
    if memory.campaign_id != query.campaign_id:
        return "campaign_mismatch"
    if memory.status != "active":
        return "inactive"
    entity_ids = {value.entity_id for value in memory.entities}
    if not required_entities <= entity_ids:
        return "entity_mismatch"
    if allowed_types and memory.event_type not in allowed_types:
        return "event_type_mismatch"
    if MemoryScope(query.perspective_kind, query.perspective_id) not in memory.scopes:
        return "scope_mismatch"
    if not _time_matches(memory.world_time, query):
        return "time_mismatch"
    return None


def _time_matches(world_time: int, query: MemoryQuery) -> bool:
    if query.time_mode in {"any", "earliest", "latest"}:
        return True
    if query.time_mode == "before":
        return world_time < int(query.time_start)
    if query.time_mode == "after":
        return world_time > int(query.time_start)
    return int(query.time_start) <= world_time <= int(query.time_end)


def _participant_scopes(
    state: Projection,
    actor_id: str,
    target_id: str,
) -> tuple[MemoryScope, ...]:
    scopes = {_scope_for_character(state, actor_id), _scope_for_character(state, target_id)}
    return tuple(sorted(scopes, key=lambda value: (value.scope_kind, value.scope_id)))


def _scope_for_character(state: Projection, character_id: str) -> MemoryScope:
    kind: Literal["player", "npc"] = (
        "player" if character_id == state.player_character_id else "npc"
    )
    return MemoryScope(kind, character_id)


def _interaction_summary(
    family: str,
    outcome: str,
    actor_name: str,
    target_name: str,
    item_name: str,
) -> str:
    object_label = f"{actor_name}递出的{item_name}"
    if family == "bribe":
        object_label += "作为贿赂"
    verbs = {
        "accepted": "确实收下了",
        "rejected": "没有接受",
        "countered": "没有立刻接受，并提出了条件",
        "delayed": "暂缓决定是否接受",
        "tested": "没有立刻接受，并先试探了对方",
    }
    return f"{target_name}{verbs[outcome]}{object_label}。"


def _interaction_importance(family: str, outcome: str) -> int:
    base = {
        "accepted": 65,
        "rejected": 50,
        "countered": 60,
        "delayed": 55,
        "tested": 60,
    }[outcome]
    return min(100, base + (15 if family == "bribe" else 0))


def _memory_id(event_id: str) -> str:
    normalized = event_id[4:] if event_id.startswith("evt_") else event_id
    return f"mem_{normalized}"


def _required_payload_id(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if value is None or str(value) == "":
        raise ValueError(f"memory event is missing {key}")
    return str(value)
