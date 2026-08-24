from __future__ import annotations

from dataclasses import dataclass

from trpg_server.core.state import Projection


@dataclass(frozen=True, slots=True)
class MapLocationContents:
    """Derived contents of one location from authoritative projection state."""

    location_id: str
    character_ids: tuple[str, ...] = ()
    container_ids: tuple[str, ...] = ()
    item_ids: tuple[str, ...] = ()
    carried_item_ids: tuple[str, ...] = ()


def build_location_contents(state: Projection) -> dict[str, MapLocationContents]:
    """Index every character, location container, and item by current location.

    The projection remains authoritative. This index is rebuilt from it instead
    of being mutated independently, so replay and NPC movement cannot leave a
    second stale location store behind.
    """
    character_ids_by_location: dict[str, list[str]] = {}
    for character_id, location_id in state.character_locations.items():
        if location_id in state.locations:
            character_ids_by_location.setdefault(location_id, []).append(character_id)

    container_ids_by_location: dict[str, list[str]] = {}
    carried_containers_by_location: dict[str, list[str]] = {}
    for container in state.containers.values():
        if container.location_id in state.locations:
            container_ids_by_location.setdefault(container.location_id, []).append(
                container.container_id
            )
        elif container.owner_character_id:
            location_id = state.character_locations.get(container.owner_character_id)
            if location_id in state.locations:
                carried_containers_by_location.setdefault(location_id, []).append(
                    container.container_id
                )

    item_ids_by_location: dict[str, list[str]] = {}
    carried_item_ids_by_location: dict[str, list[str]] = {}
    for item in state.items.values():
        if item.location_id in state.locations:
            item_ids_by_location.setdefault(item.location_id, []).append(item.item_id)
            continue
        container = state.containers.get(item.container_id)
        if container is None:
            continue
        if container.location_id in state.locations:
            item_ids_by_location.setdefault(container.location_id, []).append(item.item_id)
        elif container.owner_character_id:
            location_id = state.character_locations.get(container.owner_character_id)
            if location_id in state.locations:
                carried_item_ids_by_location.setdefault(location_id, []).append(item.item_id)

    return {
        location_id: MapLocationContents(
            location_id=location_id,
            character_ids=tuple(sorted(character_ids_by_location.get(location_id, []))),
            container_ids=tuple(
                sorted(
                    set(container_ids_by_location.get(location_id, []))
                    | set(carried_containers_by_location.get(location_id, []))
                )
            ),
            item_ids=tuple(sorted(item_ids_by_location.get(location_id, []))),
            carried_item_ids=tuple(
                sorted(carried_item_ids_by_location.get(location_id, []))
            ),
        )
        for location_id in sorted(state.locations)
    }
