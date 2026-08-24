from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from trpg_server.core.state import DecisionReason, Event, ParsedCommand, Projection, Resolution


@dataclass(frozen=True, slots=True)
class EnvironmentOpportunity:
    opportunity_id: str
    location_id: str
    action_kind: str
    resource_kind: str
    source_policy: str
    story_impact_ceiling: str = "soft"


def resolve_environment_search(
    state: Projection,
    command: ParsedCommand,
) -> Resolution:
    location_id = state.character_locations.get(command.actor_id)
    if location_id is None:
        return Resolution(
            status="rejected",
            outcome="actor_location_unknown",
            narrative="系统无法确认你当前的位置，因此这次寻找没有发生。",
            command=command,
            reasons=[
                DecisionReason(
                    "actor_location_unknown",
                    "系统不知道行动者当前在哪里",
                    "negative",
                )
            ],
        )

    kind = str(command.parameters.get("searchKind", "general"))
    location_containers = {
        container.container_id
        for container in state.containers.values()
        if container.location_id == location_id
    }
    candidates = [
        item
        for item in state.items.values()
        if (item.container_id in location_containers or item.location_id == location_id)
        and _item_matches_search_kind(item.category, kind)
    ]
    candidates.sort(key=lambda item: item.item_id)
    now = state.world_time
    opportunity = _temporary_opportunity(state, location_id, kind)
    events: list[Event] = []
    if opportunity is not None and not candidates:
        affordance_event = Event(
            _event_id(),
            "affordance.observed",
            command.actor_id,
            now,
            {
                "opportunityId": opportunity.opportunity_id,
                "locationId": opportunity.location_id,
                "actionKind": opportunity.action_kind,
                "resourceKind": opportunity.resource_kind,
                "sourcePolicy": opportunity.source_policy,
                "storyImpactCeiling": opportunity.story_impact_ceiling,
                "instantiated": False,
            },
            schema_version=1,
        )
        events.append(affordance_event)

    search_event = Event(
        _event_id(),
        "search.performed",
        command.actor_id,
        now,
        {
            "characterId": command.actor_id,
            "locationId": location_id,
            "searchKind": kind,
            "result": (
                "items_found"
                if candidates
                else "opportunity_found"
                if opportunity is not None
                else "nothing_found"
            ),
            "itemIds": [item.item_id for item in candidates],
            "opportunityId": (
                opportunity.opportunity_id if opportunity is not None else None
            ),
        },
    )
    events.append(search_event)
    minutes = 5
    if candidates:
        names = "、".join(item.name for item in candidates)
        narrative = (
            f"你在这里找了一圈，找到：{names}。它们仍在原处；"
            "如果要带走，需要明确说出取用哪一件。"
        )
        outcome = "items_found"
        reason = DecisionReason(
            "location_items_found",
            "当前位置存在符合条件的物品",
            "positive",
            source_event_id=search_event.event_id,
        )
    elif opportunity is not None:
        label = {"food": "食物", "drink": "饮品"}.get(kind, "日常资源")
        narrative = (
            f"你没有在当前容器里直接找到{label}，但注意到附近存在一个"
            "符合当前环境的临时机会。它只是可追踪的线索，不会自动进入背包；"
            "你可以继续询问、购买或寻找具体来源。"
        )
        outcome = "opportunity_found"
        reason = DecisionReason(
            "environment_opportunity_found",
            "当前位置存在可进一步确认的日常环境机会",
            "positive",
            source_event_id=events[0].event_id,
        )
    else:
        labels = {"food": "合适的食物", "drink": "合适的饮品", "document": "相关文件"}
        narrative = f"你花了一点时间寻找，但这里没有找到{labels.get(kind, '符合条件的东西')}。"
        outcome = "nothing_found"
        reason = DecisionReason(
            "nothing_found",
            "当前位置没有符合条件的物品或已知环境机会",
            "neutral",
            source_event_id=search_event.event_id,
        )
    events.extend([
        Event(
            _event_id(),
            "time.advanced",
            command.actor_id,
            now,
            {"from": now, "to": now + minutes, "minutes": minutes, "reason": "location_search"},
        ),
        Event(
            _event_id(),
            "scene.beat_advanced",
            "system",
            now + minutes,
            {"beats": 1, "reason": "location_search", "sourceEventId": search_event.event_id},
        ),
    ])
    return Resolution(
        status="committed",
        outcome=outcome,
        narrative=narrative,
        command=command,
        events=events,
        reasons=[reason],
        visible_changes=[f"世界时间推进 {minutes} 分钟"],
    )


def _temporary_opportunity(
    state: Projection,
    location_id: str,
    resource_kind: str,
) -> EnvironmentOpportunity | None:
    if resource_kind not in {"food", "drink"}:
        return None
    catalog_matches = [
        value
        for value in state.catalog_affordances.values()
        if value.location_id == location_id
        and "search" in value.action_kinds
        and _catalog_resource_matches(value.resource_categories, resource_kind)
    ]
    if catalog_matches:
        value = sorted(catalog_matches, key=lambda item: item.affordance_id)[0]
        return EnvironmentOpportunity(
            opportunity_id=value.affordance_id,
            location_id=location_id,
            action_kind="search",
            resource_kind=resource_kind,
            source_policy="v42_catalog_affordance",
            story_impact_ceiling=value.story_impact_ceiling,
        )
    location = state.locations.get(location_id)
    if location is None:
        return None
    text = f"{location.name} {location.kind}".lower()
    street_like = any(token in text for token in ("街", "市场", "码头", "酒馆", "旅馆", "商店"))
    if not street_like:
        return None
    return EnvironmentOpportunity(
        opportunity_id=f"opportunity_{location_id}_{resource_kind}",
        location_id=location_id,
        action_kind="search",
        resource_kind=resource_kind,
        source_policy="location_kind_and_current_time",
    )


def _catalog_resource_matches(categories: tuple[str, ...], resource_kind: str) -> bool:
    text = " ".join(categories)
    if resource_kind == "food":
        return any(token in text for token in ("食", "餐", "面包", "厨房", "市场", "商户"))
    return any(token in text for token in ("饮", "酒", "水", "咖啡", "餐", "市场", "商户"))


def _item_matches_search_kind(category: str, search_kind: str) -> bool:
    if search_kind == "general":
        return True
    if search_kind == "food":
        return category == "food"
    if search_kind == "drink":
        return category == "drink"
    if search_kind == "document":
        return category == "document"
    return False


def _event_id() -> str:
    return f"evt_{uuid4().hex}"
