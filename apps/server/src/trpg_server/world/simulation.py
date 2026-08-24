"""Deterministic, source-backed world autonomy between player turns."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from trpg_server.core.state import Event, Projection


@dataclass(frozen=True, slots=True)
class SettlementCandidate:
    settlement_id: str
    kind: str
    due_at: int
    source_event_id: str


def propose_world_simulation(
    state: Projection,
    *,
    previous_world_time: int,
    source_event_id: str,
) -> tuple[SettlementCandidate, ...]:
    """Propose only crossed calendar boundaries; does not mutate state."""
    if state.world_time <= previous_world_time:
        return ()
    candidates: list[SettlementCandidate] = []
    for due_at in _crossed_boundaries(previous_world_time, state.world_time, 1440):
        candidates.append(SettlementCandidate(f"daily_{due_at}", "daily", due_at, source_event_id))
    for due_at in _crossed_boundaries(previous_world_time, state.world_time, 10_080):
        candidates.append(SettlementCandidate(f"weekly_{due_at}", "weekly", due_at, source_event_id))
    for due_at in _crossed_boundaries(previous_world_time, state.world_time, 43_200):
        candidates.append(SettlementCandidate(f"monthly_{due_at}", "monthly", due_at, source_event_id))
    return tuple(candidates)


def validate_world_simulation(
    state: Projection,
    candidates: tuple[SettlementCandidate, ...],
) -> list[Event]:
    events: list[Event] = []
    for candidate in candidates:
        if candidate.due_at > state.world_time or not candidate.source_event_id:
            continue
        if candidate.settlement_id in state.world_settlements:
            continue
        event_type = f"world.{candidate.kind}_settled"
        if event_type not in {"world.daily_settled", "world.weekly_settled", "world.monthly_settled"}:
            continue
        events.append(Event(
            f"evt_{uuid4().hex}",
            event_type,
            "system",
            candidate.due_at,
            {
                "settlementId": candidate.settlement_id,
                "period": candidate.kind,
                "sourceEventId": candidate.source_event_id,
                "worldTime": candidate.due_at,
                "summary": _summary(candidate.kind),
            },
            schema_version=1,
        ))
        if candidate.kind == "weekly":
            events.append(Event(
                f"evt_{uuid4().hex}",
                "organization.plan_changed",
                "system",
                candidate.due_at,
                {
                    "organizationId": "gray_harbor_city",
                    "period": candidate.kind,
                    "plan": "组织按自身日程继续处理事务；玩家未参与的行动不会被写成玩家行为。",
                    "sourceEventId": candidate.source_event_id,
                },
                schema_version=1,
            ))
        if candidate.kind == "monthly":
            events.extend(_monthly_market_events(candidate))
    return events


def _monthly_market_events(candidate: SettlementCandidate) -> list[Event]:
    # Stable, bounded price/inventory variation. It is not random per query and
    # cannot create money or an item in a player's inventory by itself.
    cycle = (candidate.due_at // 43_200) % 3
    price = (cycle - 1) * 2
    return [
        Event(
            f"evt_{uuid4().hex}",
            "market.price_changed",
            "system",
            candidate.due_at,
            {
                "marketKey": "common_food",
                "price": max(1, 8 + price),
                "sourceEventId": candidate.source_event_id,
            },
            schema_version=1,
        ),
        Event(
            f"evt_{uuid4().hex}",
            "market.inventory_refreshed",
            "system",
            candidate.due_at,
            {
                "marketKey": "common_food",
                "quantity": 8 + cycle,
                "sourceEventId": candidate.source_event_id,
            },
            schema_version=1,
        ),
    ]


def _summary(kind: str) -> str:
    return {
        "daily": "城市按日结算营业、日程和自然变化；没有因为玩家停留而强行生成剧情。",
        "weekly": "城市按周结算组织计划、预约和公开生活痕迹。",
        "monthly": "城市按月结算价格和库存；普通经济变化有来源且不会凭空增加玩家财富。",
    }[kind]


def _crossed_boundaries(previous: int, current: int, interval: int) -> list[int]:
    first = ((previous // interval) + 1) * interval
    return list(range(first, current + 1, interval))
