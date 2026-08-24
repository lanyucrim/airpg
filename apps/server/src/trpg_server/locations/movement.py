from __future__ import annotations

from dataclasses import dataclass

from trpg_server.core.state import ExitState, Projection
from trpg_server.map.traversal import map_exit_is_allowed, resolve_arrival_location


@dataclass(frozen=True, slots=True)
class MovementDecision:
    allowed: bool
    outcome: str
    reason_code: str
    reason_label: str
    from_location_id: str | None
    to_location_id: str
    travel_minutes: int = 0
    # A street exit can target a building alias for command/UI compatibility,
    # while the actor actually arrives at that building's entry structure.
    arrival_location_id: str | None = None


def evaluate_movement(
    state: Projection,
    actor_id: str,
    destination_id: str,
) -> MovementDecision:
    """Evaluate a direct move without mutating state or creating events."""
    origin_id = state.character_locations.get(actor_id)
    if origin_id is None:
        return MovementDecision(
            False,
            "actor_location_unknown",
            "actor_location_unknown",
            "系统不知道行动者当前在哪里",
            None,
            destination_id,
        )
    if destination_id not in state.locations:
        return MovementDecision(
            False,
            "unknown_destination",
            "unknown_destination",
            "目标地点不存在于权威世界状态",
            origin_id,
            destination_id,
        )
    if origin_id == destination_id:
        return MovementDecision(
            False,
            "already_there",
            "already_there",
            "行动者已经在目标地点",
            origin_id,
            destination_id,
        )
    origin = state.locations.get(origin_id)
    if origin is None:
        return MovementDecision(
            False,
            "actor_location_unknown",
            "actor_location_unknown",
            "行动者当前位置缺少地点定义",
            origin_id,
            destination_id,
        )
    direct_exit = next(
        (
            exit_state
            for exit_state in origin.exits
            if exit_state.to_location_id == destination_id
            and exit_is_visible_to(state, actor_id, exit_state)
        ),
        None,
    )
    if direct_exit is None:
        return MovementDecision(
            False,
            "destination_not_reachable",
            "no_visible_direct_exit",
            "当前地点没有通往目标的可用直接出口",
            origin_id,
            destination_id,
        )
    if not map_exit_is_allowed(
        state,
        origin_id,
        destination_id,
        direct_exit,
    ):
        return MovementDecision(
            False,
            "destination_not_reachable",
            "map_topology_blocked",
            "这条直接通路不符合当前地图的父子结构或街道连接",
            origin_id,
            destination_id,
        )
    inactive_conditions = [
        condition_id
        for condition_id in direct_exit.required_condition_ids
        if condition_id not in state.story_conditions
        or not state.story_conditions[condition_id].active
    ]
    if inactive_conditions:
        return MovementDecision(
            False,
            "exit_blocked",
            "exit_condition_not_met",
            "这条通路目前还不具备通行条件",
            origin_id,
            destination_id,
        )
    if (
        direct_exit.locked
        and not _is_open_structure_transition(state, origin_id, destination_id)
        and not _actor_has_any_key(
            state,
            actor_id,
            direct_exit.key_item_ids,
        )
    ):
        return MovementDecision(
            False,
            "exit_locked",
            "required_key_missing",
            "通路已经上锁，而你没有可用的钥匙",
            origin_id,
            destination_id,
        )
    return MovementDecision(
        True,
        "moved",
        "visible_direct_exit",
        "当前地点存在可用直接出口",
        origin_id,
        destination_id,
        direct_exit.travel_minutes,
        resolve_arrival_location(
            state,
            origin_id,
            destination_id,
            actor_id=actor_id,
        ),
    )


def exit_is_visible_to(
    state: Projection,
    actor_id: str,
    exit_state: ExitState,
) -> bool:
    return exit_state.visible or exit_state.exit_id in state.discovered_exits.get(
        actor_id,
        set(),
    )


def _is_open_structure_transition(
    state: Projection,
    origin_id: str,
    destination_id: str,
) -> bool:
    """Treat every non-hidden transition inside a location as open.

    This also applies to older replayed events that recorded a name-based
    private-room lock before the open-structure rule was adopted. Hidden
    exits are filtered before this check and therefore remain discovery-gated.
    """
    origin = state.locations.get(origin_id)
    destination = state.locations.get(destination_id)
    if origin is None or destination is None:
        return False
    if origin.map_visibility == "gm" or destination.map_visibility == "gm":
        return False
    return (
        origin.parent_id == destination.parent_id
        or origin.parent_id == destination_id
        or destination.parent_id == origin_id
    )


def _actor_has_any_key(
    state: Projection,
    actor_id: str,
    key_item_ids: tuple[str, ...],
) -> bool:
    if not key_item_ids:
        return False
    owned_containers = {
        container.container_id
        for container in state.containers.values()
        if container.owner_character_id == actor_id
    }
    return any(
        item_id in state.items
        and state.items[item_id].container_id in owned_containers
        for item_id in key_item_ids
    )
