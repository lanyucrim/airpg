"""Projection handlers for auditable item interaction events."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from trpg_server.core.projection_handlers import projection_handlers
from trpg_server.core.state import Event, Projection
from trpg_server.items.interaction import (
    DIFFICULTY_BANDS,
    INTERACTION_SCHEMA_VERSION,
    OPERATIONS,
    TARGET_KINDS,
)
from trpg_server.items.provenance import (
    ItemSourceConfirmation,
    validate_source_confirmation_against_state,
)


def _required(payload: Mapping[str, Any], fields: frozenset[str], event_type: str) -> None:
    keys = set(payload)
    missing = fields.difference(keys)
    extra = keys.difference(fields)
    if missing or extra:
        if missing and not extra:
            raise ValueError(f"check is missing fields: {sorted(missing)}")
        if extra and not missing:
            raise ValueError(f"check has unknown fields: {sorted(extra)}")
        details: list[str] = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if extra:
            details.append(f"unknown {sorted(extra)}")
        raise ValueError(f"{event_type} payload has " + "; ".join(details))


def _text(value: object, field: str, *, maximum: int = 500) -> str:
    """Validate an auditable text field without coercing arbitrary values."""

    if type(value) is not str or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _string_list(
    value: object,
    field: str,
    *,
    allow_empty: bool = False,
    maximum: int = 8,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{field} must be a string list")
    if not allow_empty and not value:
        raise ValueError(f"{field} must be a non-empty string list")
    result = [_text(item, field, maximum=160) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{field} must not contain duplicates")
    return result


def _enum(value: object, field: str, allowed: frozenset[str]) -> str:
    if type(value) is not str or value not in allowed:
        raise ValueError(f"{field} is not supported")
    return value


_CHECK_STATUSES = frozenset({"succeeded", "failed", "blocked", "not_required"})
_CHECK_REQUIRED_FIELDS = frozenset(
    {
        "status",
        "code",
        "abilityId",
        "level",
        "sourceStatus",
        "difficultyBand",
        "dc",
        "modifier",
        "roll",
        "total",
        "margin",
    }
)
_CHECK_OPTIONAL_FIELDS = frozenset({"reason"})
_CHECK_LEVEL_MODIFIERS = {
    "untrained": -2,
    "working": 0,
    "competent": 2,
    "advanced": 4,
    "expert": 6,
}
_CHECK_SOURCE_STATUSES = frozenset(
    {"canon", "player_defined", "inferred", "unknown"}
)
_CHECK_MECHANICAL_SOURCE_STATUSES = frozenset({"canon", "player_defined"})
_CHECK_DIFFICULTY_DC = {
    "trivial": 8,
    "routine": 11,
    "demanding": 14,
    "hard": 17,
    "extreme": 20,
}
_CHECK_BLOCK_CODES = frozenset(
    {"body_part_unavailable", "hand_slot_unavailable", "insufficient_hands"}
)


def _check_int(
    value: object,
    field: str,
    *,
    allow_none: bool = False,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    """Validate a JSON integer without accepting booleans as integers."""

    if value is None and allow_none:
        return None
    if type(value) is not int:
        suffix = " or null" if allow_none else ""
        raise ValueError(f"check.{field} must be an integer{suffix}")
    if minimum is not None and value < minimum:
        raise ValueError(f"check.{field} is below the allowed range")
    if maximum is not None and value > maximum:
        raise ValueError(f"check.{field} is above the allowed range")
    return value


def _validate_check_audit(
    check: Mapping[str, Any],
    interaction_status: str,
    *,
    operation: str | None = None,
) -> dict[str, Any]:
    """Validate the deterministic check snapshot before projecting it.

    The interaction event is an audit record, but its status gates downstream
    effect events.  Without this consistency check a malformed event could
    claim ``succeeded`` while carrying a failed or impossible check and then
    authorize a location effect during replay.
    """

    keys = set(check)
    missing = _CHECK_REQUIRED_FIELDS.difference(keys)
    extra = keys.difference(_CHECK_REQUIRED_FIELDS | _CHECK_OPTIONAL_FIELDS)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if extra:
            details.append(f"unknown {sorted(extra)}")
        raise ValueError("check payload has " + "; ".join(details))

    check_status = _enum(check["status"], "check.status", _CHECK_STATUSES)
    code = _text(check["code"], "check.code", maximum=80)
    ability_id = _text(check["abilityId"], "check.abilityId", maximum=160)

    level = check["level"]
    source_status = check["sourceStatus"]
    if type(level) is not str:
        raise ValueError("check.level must be a supported string")
    if type(source_status) is not str:
        raise ValueError("check.sourceStatus must be a supported string")

    difficulty = check["difficultyBand"]
    if type(difficulty) is not str or difficulty not in DIFFICULTY_BANDS:
        raise ValueError("check.difficultyBand is not supported")
    if "reason" in check:
        reason = check["reason"]
        if type(reason) is not str or len(reason) > 500:
            raise ValueError("check.reason must be a string of at most 500 characters")

    # ``not_required`` is emitted only by deterministic furniture transfers;
    # all other checks carry a character ability snapshot.  Keeping these
    # sentinel values explicit prevents a forged no-check from authorizing an
    # apply/combine action.
    if check_status == "not_required":
        if ability_id != "none" or level != "none" or source_status != "system":
            raise ValueError("not_required check must use the system sentinel")
    else:
        if ability_id == "none":
            raise ValueError("rolled check must identify an ability")
        if level not in _CHECK_LEVEL_MODIFIERS:
            raise ValueError("check.level is not supported")
        if source_status not in _CHECK_SOURCE_STATUSES:
            raise ValueError("check.sourceStatus is not supported")

    dc = check["dc"]
    modifier = check["modifier"]
    roll = check["roll"]
    total = check["total"]
    margin = check["margin"]

    expected_dc = _CHECK_DIFFICULTY_DC[difficulty]
    if check_status == "not_required":
        if dc is not None:
            raise ValueError("not_required check must have a null DC")
        if difficulty != "trivial":
            raise ValueError("not_required check must use trivial difficulty")
        _check_int(modifier, "modifier", minimum=0, maximum=0)
        if code != "succeeded":
            raise ValueError("not_required check code must be succeeded")
    else:
        _check_int(dc, "dc", minimum=expected_dc, maximum=expected_dc)
        expected_modifier = (
            _CHECK_LEVEL_MODIFIERS[level]
            if source_status in _CHECK_MECHANICAL_SOURCE_STATUSES
            else _CHECK_LEVEL_MODIFIERS["untrained"]
        )
        _check_int(
            modifier,
            "modifier",
            minimum=expected_modifier,
            maximum=expected_modifier,
        )

    _check_int(roll, "roll", allow_none=True, minimum=1, maximum=20)
    _check_int(total, "total", allow_none=True)
    _check_int(margin, "margin", allow_none=True)

    if check_status == "succeeded" and code != "succeeded":
        raise ValueError("succeeded check code must be succeeded")
    if check_status == "failed" and code != "failed_check":
        raise ValueError("failed check code must be failed_check")
    if check_status == "blocked" and code not in _CHECK_BLOCK_CODES:
        raise ValueError("blocked check code is not supported")

    if check_status == "not_required":
        if any(value is not None for value in (roll, total, margin)):
            raise ValueError("not_required check cannot contain dice totals")
    elif check_status == "blocked":
        if any(value is not None for value in (roll, total, margin)):
            raise ValueError("blocked check cannot contain dice totals")
    else:
        if dc is None or roll is None or total is None or margin is None:
            raise ValueError("rolled check requires dc, roll, total and margin")
        if total != roll + modifier or margin != total - dc:
            raise ValueError("check totals do not match d20 + modifier and DC")
        if check_status == "succeeded" and total < dc:
            raise ValueError("succeeded check total is below DC")
        if check_status == "failed" and total >= dc:
            raise ValueError("failed check total reaches DC")

    # The event status is the state gate used by location effects.  Keep
    # unsupported-effect audits permissive because they can follow a valid
    # successful check but fail later at effect materialization.
    if operation in {"store", "retrieve"} and check_status != "not_required":
        raise ValueError("transfer interaction requires a not_required check")
    if operation in {"apply", "combine"} and check_status == "not_required":
        raise ValueError("item interaction requires a character check")

    if interaction_status == "succeeded":
        allowed_success_checks = {"succeeded"}
        if operation in {"store", "retrieve"}:
            allowed_success_checks.add("not_required")
        if check_status not in allowed_success_checks:
            raise ValueError("succeeded interaction requires a succeeded check")
    if interaction_status in {"failed_check", "failed"} and check_status != "failed":
        raise ValueError("failed interaction requires a failed check")
    if interaction_status in {"rejected_precondition", "blocked", "rejected"} and check_status != "blocked":
        raise ValueError("precondition rejection requires a blocked check")
    if interaction_status in {"unsupported_effect", "transaction_failed"} and check_status not in {
        "succeeded",
        "not_required",
    }:
        raise ValueError("post-check rejection requires a successful check")
    if interaction_status in {"rejected_physics", "clarify"}:
        raise ValueError(f"{interaction_status} cannot be recorded as a resolved check")
    return deepcopy(dict(check))


_INTERACTION_STATUSES = frozenset(
    {
        "succeeded",
        "failed_check",
        "rejected_precondition",
        "rejected_physics",
        "unsupported_effect",
        "clarify",
        "transaction_failed",
        # These aliases are retained for callers that use the generic
        # resolution vocabulary.  They still represent an audit record only.
        "failed",
        "blocked",
        "rejected",
    }
)


@projection_handlers.register("item.source_confirmed")
def apply_item_source_confirmed(state: Projection, event: Event) -> None:
    if event.schema_version != 1:
        raise ValueError("unsupported item.source_confirmed schema version")
    _required(
        event.payload,
        frozenset({"itemId", "definitionId", "sourceKind", "sourceEventId", "definitionStatus"}),
        event.event_type,
    )
    raw = event.payload
    try:
        confirmation = ItemSourceConfirmation(
            item_id=raw["itemId"],
            definition_id=raw["definitionId"],
            source_kind=raw["sourceKind"],
            source_event_id=raw.get("sourceEventId"),
            definition_status=raw["definitionStatus"],
        )
    except (TypeError, ValueError) as error:
        # Normalize malformed JSON-like values to the projection contract's
        # public error type; never leak a container-membership TypeError.
        raise ValueError(str(error)) from error
    # ``core.projection.apply_event`` records the current event ID before
    # dispatching a handler.  Guard explicitly against a self-reference so a
    # malformed source cannot satisfy the earlier-event check by accident.
    if confirmation.source_event_id == event.event_id:
        raise ValueError("sourceEventId must reference an earlier confirmed event")
    validate_source_confirmation_against_state(state, confirmation)
    state.item_source_confirmations[confirmation.item_id] = {
        **confirmation.to_payload(),
        "confirmationEventId": event.event_id,
    }


@projection_handlers.register("item.interaction_resolved")
def apply_item_interaction_resolved(state: Projection, event: Event) -> None:
    if event.schema_version != INTERACTION_SCHEMA_VERSION:
        raise ValueError("unsupported item.interaction_resolved schema version")
    _required(
        event.payload,
        frozenset(
            {
                "interactionId",
                "actorId",
                "operation",
                "sourceItemIds",
                "targetKind",
                "targetId",
                "status",
                "check",
                "autoPickedItemIds",
                "sourceText",
            }
        ),
        event.event_type,
    )
    interaction_id = _text(event.payload["interactionId"], "interactionId", maximum=160)
    if interaction_id in state.item_interactions:
        raise ValueError("item interaction has already been projected")
    actor_id = _text(event.payload["actorId"], "actorId", maximum=160)
    if actor_id != event.actor_id:
        raise ValueError("actorId must match the event actor")
    operation = _enum(event.payload["operation"], "operation", OPERATIONS)
    source_ids = _string_list(event.payload["sourceItemIds"], "sourceItemIds")
    missing_sources = [item_id for item_id in source_ids if item_id not in state.items]
    if missing_sources:
        raise ValueError(
            "item interaction references unknown source item(s): "
            + ", ".join(missing_sources)
        )
    target_kind = _enum(event.payload["targetKind"], "targetKind", TARGET_KINDS)
    target_id = _text(event.payload["targetId"], "targetId", maximum=160)
    status = _enum(event.payload["status"], "status", _INTERACTION_STATUSES)
    if operation == "combine" and target_kind != "item":
        raise ValueError("combine interactions require an item target")
    if operation in {"store", "retrieve"} and target_kind != "furniture":
        raise ValueError("store/retrieve interactions require a furniture target")
    check = event.payload["check"]
    if not isinstance(check, Mapping):
        raise ValueError("check must be an object")
    normalized_check = _validate_check_audit(check, status, operation=operation)
    auto_picked = _string_list(
        event.payload["autoPickedItemIds"],
        "autoPickedItemIds",
        allow_empty=True,
    )
    if not set(auto_picked).issubset(source_ids):
        raise ValueError("autoPickedItemIds must be a subset of sourceItemIds")
    source_text = _text(event.payload["sourceText"], "sourceText")
    state.item_interactions[interaction_id] = {
        **deepcopy(dict(event.payload)),
        "actorId": actor_id,
        "operation": operation,
        "sourceItemIds": source_ids,
        "targetKind": target_kind,
        "targetId": target_id,
        "status": status,
        "check": normalized_check,
        "autoPickedItemIds": auto_picked,
        "sourceText": source_text,
        "sourceEventId": event.event_id,
        "worldTime": event.world_time,
    }


@projection_handlers.register("location.item_effect_applied")
def apply_location_item_effect(state: Projection, event: Event) -> None:
    if event.schema_version != 1:
        raise ValueError("unsupported location.item_effect_applied schema version")
    _required(
        event.payload,
        frozenset({"effectId", "locationId", "itemId", "effectKind", "summary", "sourceInteractionId"}),
        event.event_type,
    )
    effect_id = _text(event.payload["effectId"], "effectId", maximum=160)
    if effect_id in state.location_item_effects:
        raise ValueError("location item effect has already been projected")
    location_id = _text(event.payload["locationId"], "locationId", maximum=160)
    if location_id not in state.locations:
        raise ValueError("location.item_effect_applied references an unknown location")
    item_id = _text(event.payload["itemId"], "itemId", maximum=160)
    effect_kind = _text(event.payload["effectKind"], "effectKind", maximum=160)
    # A summary may contain several bounded physical-basis fragments from the
    # model candidate (12 x 300 chars in the candidate contract).
    summary = _text(event.payload["summary"], "summary", maximum=4000)
    source_interaction_id = _text(
        event.payload["sourceInteractionId"],
        "sourceInteractionId",
        maximum=160,
    )
    if source_interaction_id not in state.item_interactions:
        raise ValueError("location item effect requires a confirmed interaction")
    interaction = state.item_interactions[source_interaction_id]
    if interaction.get("status") != "succeeded":
        raise ValueError("location item effect requires a succeeded interaction")
    if interaction.get("operation") != "apply":
        raise ValueError("location item effect requires an apply interaction")
    if interaction.get("targetKind") not in {"location", "furniture"}:
        raise ValueError("location item effect requires a location or furniture interaction")
    source_item_ids = interaction.get("sourceItemIds", ())
    if item_id not in source_item_ids:
        raise ValueError("location item effect item must be an interaction source item")
    state.location_item_effects[effect_id] = {
        **deepcopy(dict(event.payload)),
        "effectId": effect_id,
        "locationId": location_id,
        "itemId": item_id,
        "effectKind": effect_kind,
        "summary": summary,
        "sourceInteractionId": source_interaction_id,
        "sourceEventId": event.event_id,
        "appliedAt": event.world_time,
    }


__all__ = [
    "apply_item_interaction_resolved",
    "apply_item_source_confirmed",
    "apply_location_item_effect",
]
