"""Provider-neutral contract for future location capability suggestions.

This module intentionally contains no model SDK, network call, event creation,
or projection mutation.  A future AI adapter may consume the context and
return candidates, while story/behavior rules remain responsible for deciding
whether a capability is real and whether it can produce an event.
"""

from dataclasses import dataclass
from typing import Protocol

from trpg_server.core.state import Projection


@dataclass(frozen=True, slots=True)
class LocationCapabilityContext:
    location_id: str
    name: str
    kind: str
    parent_location_id: str | None
    description: str
    catalog_affordance_ids: tuple[str, ...] = ()
    visible_item_ids: tuple[str, ...] = ()
    co_located_character_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LocationCapabilityCandidate:
    capability_id: str
    action_kind: str
    summary: str
    source_affordance_id: str | None = None


class LocationCapabilityProvider(Protocol):
    """Future candidate provider; it must never write authoritative state."""

    def propose(
        self,
        context: LocationCapabilityContext,
    ) -> tuple[LocationCapabilityCandidate, ...]: ...


def build_location_capability_context(
    state: Projection,
    location_id: str,
) -> LocationCapabilityContext | None:
    """Build a safe, provider-neutral context for a future capability adapter."""
    location = state.locations.get(location_id)
    if location is None:
        return None
    catalog_affordances = tuple(
        sorted(
            affordance.affordance_id
            for affordance in state.catalog_affordances.values()
            if affordance.location_id == location_id
        )
    )
    visible_item_ids = tuple(
        sorted(
            item.item_id
            for item in state.items.values()
            if (
                item.location_id == location_id
                or (
                    state.containers.get(item.container_id) is not None
                    and state.containers[item.container_id].location_id == location_id
                )
            )
        )
    )
    co_located_character_ids = tuple(
        sorted(
            character_id
            for character_id, current_location in state.character_locations.items()
            if current_location == location_id
        )
    )
    return LocationCapabilityContext(
        location_id=location.location_id,
        name=location.name,
        kind=location.kind,
        parent_location_id=location.parent_id,
        description=location.description,
        catalog_affordance_ids=catalog_affordances,
        visible_item_ids=visible_item_ids,
        co_located_character_ids=co_located_character_ids,
    )


__all__ = [
    "LocationCapabilityCandidate",
    "LocationCapabilityContext",
    "LocationCapabilityProvider",
    "build_location_capability_context",
]
