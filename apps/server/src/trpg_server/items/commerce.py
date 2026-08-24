"""Item-side commerce boundary.

The item module can validate the physical record delivered by a confirmed
transaction. Quotes, prices, seller policy, accounts, payment and tax belong
to the future world/economy module and must not be smuggled into item fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from trpg_server.core.state import DecisionReason, Event, ParsedCommand, Projection, Resolution
from trpg_server.items.models import ItemInstance


@dataclass(frozen=True, slots=True)
class CommerceCheck:
    allowed: bool
    code: str
    label: str


def validate_purchase_item(item: ItemInstance) -> CommerceCheck:
    """Check only the item-side constraints of a future confirmed purchase."""

    try:
        item.validate()
    except ValueError as error:
        return CommerceCheck(False, "invalid_item_record", str(error))
    if item.is_plot_item:
        return CommerceCheck(
            False,
            "plot_item_requires_story_confirmation",
            "剧情道具不能通过普通交易直接创建",
        )
    if item.container_id is None and item.location_id is None:
        return CommerceCheck(
            False,
            "purchase_destination_missing",
            "成交物品必须明确进入一个容器或地点",
        )
    return CommerceCheck(True, "allowed", "物品记录符合普通交易的物品侧约束")


def build_purchase_event(
    *,
    actor_id: str,
    world_time: int,
    item: ItemInstance,
    transaction_id: str,
    payment_event_id: str,
) -> Event:
    """Build the item half of an already-confirmed external transaction."""

    check = validate_purchase_item(item)
    if not check.allowed:
        raise ValueError(check.label)
    if not transaction_id or not payment_event_id:
        raise ValueError("purchase requires a transaction id and payment event id")
    return Event(
        event_id=f"evt_item_purchased_{uuid4().hex}",
        event_type="item.purchased",
        actor_id=actor_id,
        world_time=world_time,
        payload={
            "item": item.to_payload(),
            "transactionId": transaction_id,
            "paymentEventId": payment_event_id,
        },
        schema_version=3,
    )


def resolve_purchase(state: Projection, command: ParsedCommand) -> Resolution:
    """Do not let the legacy behavior route create a half-defined item.

    This intentional rejection is preferable to fabricating a price, a payment
    source, a seller or an item definition from player text. The commerce
    integration will be implemented when the world/economy module supplies a
    confirmed payment and a full 15-field candidate record.
    """

    del state
    return Resolution(
        status="rejected",
        outcome="commerce_integration_pending",
        narrative="当前尚未有经过确认的交易报价与支付记录，因此这笔购买没有发生。",
        command=command,
        reasons=[
            DecisionReason(
                "commerce_integration_pending",
                "物品模块不从旧报价或余额字段推定成交物品",
                "neutral",
            )
        ],
    )


__all__ = [
    "CommerceCheck",
    "build_purchase_event",
    "resolve_purchase",
    "validate_purchase_item",
]
