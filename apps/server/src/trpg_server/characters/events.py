"""Projection handlers for character-owned events."""

from __future__ import annotations

from trpg_server.core.projection_handlers import projection_handlers
from trpg_server.core.state import (
    DecisionProfileState,
    Event,
    NpcScheduleState,
    Projection,
)
from trpg_server.characters.body import validate_external_injury
from trpg_server.characters.equipment import validate_equipment_binding


_LANGUAGE_STYLE_LIST_FIELDS = {
    "addressTerms",
    "catchphrases",
    "taboos",
    "sourceRefs",
}
_LANGUAGE_STYLE_FIELDS = (
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
    "sourceStatus",
    "sourceRefs",
    "notes",
)


def _profile_abilities(payload: dict[str, object]) -> list[dict[str, object]]:
    """Copy optional ability metadata without making it authoritative."""

    raw = payload.get("abilities", [])
    if not isinstance(raw, list):
        return []
    fields = (
        "abilityId",
        "name",
        "level",
        "sourceStatus",
        "confidence",
        "basis",
        "sourceRefs",
        "notes",
    )
    result: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        value = {field: item.get(field) for field in fields}
        value["sourceStatus"] = value["sourceStatus"] or "unknown"
        value["sourceRefs"] = (
            list(value["sourceRefs"])
            if isinstance(value["sourceRefs"], list)
            else []
        )
        value["basis"] = value["basis"] or ""
        value["notes"] = value["notes"] or ""
        result.append(value)
    return result


def _profile_language_style(payload: dict[str, object]) -> dict[str, object]:
    """Return a stable language-style shape for new and historical events."""

    raw = payload.get("languageStyle")
    source = raw if isinstance(raw, dict) else {}
    result: dict[str, object] = {}
    for field in _LANGUAGE_STYLE_FIELDS:
        if field in _LANGUAGE_STYLE_LIST_FIELDS:
            value = source.get(field, [])
            result[field] = list(value) if isinstance(value, list) else []
        else:
            result[field] = source.get(field)
    if result["sourceStatus"] is None:
        result["sourceStatus"] = "unknown"
    if result["notes"] is None:
        result["notes"] = ""
    return result


@projection_handlers.register("character.created")
def apply_character_created(state: Projection, event: Event) -> None:
    if event.schema_version not in {1, 2}:
        raise ValueError(
            f"unsupported character.created schema version: {event.schema_version}"
        )
    payload = event.payload
    character_id = payload["characterId"]
    state.character_locations[character_id] = payload["locationId"]
    state.character_names[character_id] = payload.get("name") or character_id
    state.character_aliases[character_id] = tuple(payload.get("aliases", []))
    state.character_types[character_id] = payload["characterType"]
    state.accepted_gift_definition_ids[character_id] = set(
        payload.get("acceptedGiftDefinitionIds", [])
    )
    state.character_profiles[character_id] = {
        # Schema 2 permits optional catalog identity metadata. Historical
        # schema-2 events without it remain valid and project null values.
        "catalogCharacterId": payload.get("catalogCharacterId"),
        "catalogId": payload.get("catalogId"),
        "role": payload.get("role", ""),
        "birthplace": payload.get("birthplace", ""),
        "age": payload.get("age"),
        "adult": payload.get("adult"),
        "publicDescription": payload.get("publicDescription", ""),
        "privateNotes": payload.get("privateNotes", ""),
        "motivations": tuple(payload.get("motivations", [])),
        "fears": tuple(payload.get("fears", [])),
        "secrets": tuple(payload.get("secrets", [])),
        "organizationIds": tuple(payload.get("organizationIds", [])),
        "tags": tuple(payload.get("tags", [])),
        "playerDefinedFields": tuple(payload.get("playerDefinedFields", [])),
        # Profile metadata is read-only context.  Missing fields on schema-1
        # historical events intentionally become empty/unknown values.
        "abilities": _profile_abilities(payload),
        "languageStyle": _profile_language_style(payload),
    }
    if "inventoryContainerId" in payload:
        state.character_profiles[character_id]["inventoryContainerId"] = payload.get(
            "inventoryContainerId"
        )
    decision_profile = payload.get("decisionProfile")
    if event.schema_version == 2 and decision_profile is not None:
        state.decision_profiles[character_id] = DecisionProfileState(
            monthly_income_pence=decision_profile["monthlyIncomePence"],
            economic_pressure=decision_profile["economicPressure"],
            gift_openness=decision_profile["giftOpenness"],
            greed=decision_profile["greed"],
            integrity=decision_profile["integrity"],
            risk_aversion=decision_profile["riskAversion"],
            institutional_loyalty=decision_profile["institutionalLoyalty"],
            corruption_openness=decision_profile["corruptionOpenness"],
            hard_refusals=tuple(decision_profile.get("hardRefusals", [])),
            source_event_id=event.event_id,
        )


def _event_fields(
    event: Event,
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> dict[str, object]:
    if event.schema_version != 1:
        raise ValueError(
            f"unsupported {event.event_type} schema version: {event.schema_version}"
        )
    keys = set(event.payload)
    missing = required.difference(keys)
    extra = keys.difference(required | optional)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if extra:
            details.append(f"unknown {sorted(extra)}")
        raise ValueError(f"{event.event_type} payload has " + "; ".join(details))
    return event.payload


@projection_handlers.register("character.item_equipped")
def apply_character_item_equipped(state: Projection, event: Event) -> None:
    payload = _event_fields(
        event,
        required=frozenset({"characterId", "itemId", "slotIds"}),
    )
    character_id = payload["characterId"]
    item_id = payload["itemId"]
    slot_ids = payload["slotIds"]
    if type(character_id) is not str or not character_id:
        raise ValueError("characterId must be a non-empty string")
    if type(item_id) is not str or not item_id:
        raise ValueError("itemId must be a non-empty string")
    if (
        not isinstance(slot_ids, list)
        or not slot_ids
        or any(type(value) is not str or not value for value in slot_ids)
    ):
        raise ValueError("slotIds must be a non-empty string list")
    item = state.items.get(item_id)
    check = validate_equipment_binding(state, character_id, item, tuple(slot_ids))
    if not check.allowed:
        raise ValueError(check.label)
    bindings = state.character_equipment.setdefault(character_id, {})
    raw_equipment = item.properties.get("equipment", {}) if item is not None else {}
    mode = raw_equipment.get("mode") if isinstance(raw_equipment, dict) else None
    if mode not in {"held", "worn"}:
        raise ValueError("item equipment mode is invalid")
    for slot_id in check.slot_ids:
        bindings[f"{mode}:{slot_id}"] = {
            "itemId": item_id,
            "slotId": slot_id,
            "mode": mode,
            "sourceEventId": event.event_id,
            "equippedAt": event.world_time,
        }


@projection_handlers.register("character.item_unequipped")
def apply_character_item_unequipped(state: Projection, event: Event) -> None:
    payload = _event_fields(
        event,
        required=frozenset({"characterId", "itemId"}),
    )
    character_id = payload["characterId"]
    item_id = payload["itemId"]
    if type(character_id) is not str or type(item_id) is not str:
        raise ValueError("characterId and itemId must be strings")
    bindings = state.character_equipment.get(character_id, {})
    matching = [slot for slot, value in bindings.items() if value.get("itemId") == item_id]
    if not matching:
        raise ValueError("item is not equipped by character")
    for slot in matching:
        bindings.pop(slot, None)


@projection_handlers.register("character.external_injury_applied")
def apply_external_injury_applied(state: Projection, event: Event) -> None:
    payload = _event_fields(
        event,
        required=frozenset(
            {
                "characterId",
                "injuryId",
                "bodyPart",
                "severity",
                "status",
                "functionalEffects",
                "notes",
            }
        ),
    )
    character_id = payload["characterId"]
    injury_id = payload["injuryId"]
    body_part = payload["bodyPart"]
    severity = payload["severity"]
    status = payload["status"]
    if any(type(value) is not str or not value for value in (character_id, injury_id, body_part)):
        raise ValueError("characterId, injuryId and bodyPart must be non-empty strings")
    if type(severity) is not str or type(status) is not str:
        raise ValueError("severity and status must be strings")
    effects = payload["functionalEffects"]
    if not isinstance(effects, dict):
        raise ValueError("functionalEffects must be an object")
    normalized_effects = validate_external_injury(
        body_part=body_part,
        severity=severity,
        status=status,
        functional_effects=effects,
    )
    injuries = state.character_external_injuries.setdefault(character_id, {})
    if injury_id in injuries:
        raise ValueError(f"external injury already exists: {injury_id}")
    if any(
        value.get("bodyPart") == body_part
        and value.get("status") in {"active", "healing", "missing"}
        for value in injuries.values()
    ):
        raise ValueError("character already has an active injury on that body part")
    candidate = {
        "injuryId": injury_id,
        "bodyPart": body_part,
        "severity": severity,
        "status": status,
        "functionalEffects": normalized_effects,
        "notes": payload["notes"] if isinstance(payload["notes"], str) else "",
        "sourceEventId": event.event_id,
        "appliedAt": event.world_time,
    }
    # Existing bindings remain auditable after an injury.  They are no longer
    # usable for a blocked function; a later unequip event can remove them.
    injuries[injury_id] = candidate


@projection_handlers.register("character.external_injury_cleared")
def apply_external_injury_cleared(state: Projection, event: Event) -> None:
    payload = _event_fields(
        event,
        required=frozenset({"characterId", "injuryId"}),
    )
    character_id = payload["characterId"]
    injury_id = payload["injuryId"]
    if type(character_id) is not str or type(injury_id) is not str:
        raise ValueError("characterId and injuryId must be strings")
    injuries = state.character_external_injuries.setdefault(character_id, {})
    if injury_id not in injuries:
        raise ValueError("external injury does not exist")
    if injuries[injury_id].get("status") == "missing":
        raise ValueError("a missing body part cannot be restored by clearing an injury")
    injuries.pop(injury_id)


@projection_handlers.register("npc.schedule_defined")
def apply_npc_schedule_defined(state: Projection, event: Event) -> None:
    payload = event.payload
    schedule = NpcScheduleState(
        schedule_id=payload["id"],
        character_id=payload["characterId"],
        weekday=payload["weekday"],
        start_minute=payload["startMinute"],
        end_minute=payload["endMinute"],
        location_id=payload["locationId"],
        availability=payload.get("availability", "public"),
        priority=payload.get("priority", 0),
    )
    state.npc_schedules[schedule.schedule_id] = schedule
