"""The stable item-record contract shared by atlas data and runtime events.

The gray-harbor item atlas deliberately stores only observable item facts,
placement and a coarse plot-item flag.  It does not carry ownership, story
meaning, evidence, permissions, operations, source citations, or audit data.
Those belong to their respective domains and may refer to an item id.
"""

from __future__ import annotations

from hashlib import sha256
import json


ITEM_CONTRACT_SCHEMA_VERSION = 7


ITEM_RECORD_FIELDS: tuple[str, ...] = (
    "id",
    "definitionId",
    "name",
    "description",
    "category",
    "isPlotItem",
    "quantity",
    "stackable",
    "unitWeightGrams",
    "valueCrown",
    "condition",
    "durability",
    "containerId",
    "locationId",
    "properties",
)

ITEM_RECORD_FIELD_SET = frozenset(ITEM_RECORD_FIELDS)


def item_contract_fingerprint() -> str:
    """Identify the field contract used by generated item catalogs.

    A semantic field change must bump ``ITEM_CONTRACT_SCHEMA_VERSION``.  Field
    additions/removals are also captured directly, so stale generators fail
    closed instead of silently emitting an old record shape.
    """

    payload = json.dumps(
        {
            "schemaVersion": ITEM_CONTRACT_SCHEMA_VERSION,
            "fields": ITEM_RECORD_FIELDS,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return f"sha256:{sha256(payload).hexdigest()}"

# These were accidentally mixed into the previous item model.  Rejecting them
# at the boundary keeps future item records within the published 15-field
# contract instead of silently resurrecting a second schema.
FORBIDDEN_ITEM_FIELDS = frozenset(
    {
        "aliases",
        "availability",
        "criticality",
        "defaultOperations",
        "definition",
        "evidence",
        "importanceTier",
        "legal",
        "operations",
        "ownerCharacterId",
        "relation",
        "relations",
        "rights",
        "source",
        "sourceRefs",
        "sourceStatus",
        "storyBindingPolicy",
        "tags",
        "tradePolicy",
    }
)

def record_field_error(record: object, *, path: str) -> str | None:
    """Return a concise contract error without mutating ``record``."""

    if not isinstance(record, dict):
        return f"{path} must be an object"
    keys = set(record)
    missing = ITEM_RECORD_FIELD_SET.difference(keys)
    if missing:
        return f"{path} is missing item fields: {sorted(missing)}"
    extra = keys.difference(ITEM_RECORD_FIELD_SET)
    if extra:
        return f"{path} has fields outside the item contract: {sorted(extra)}"
    return None


__all__ = [
    "FORBIDDEN_ITEM_FIELDS",
    "ITEM_CONTRACT_SCHEMA_VERSION",
    "ITEM_RECORD_FIELDS",
    "ITEM_RECORD_FIELD_SET",
    "item_contract_fingerprint",
    "record_field_error",
]
