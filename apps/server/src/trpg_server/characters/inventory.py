"""Read-only character inventory ownership helpers.

The character domain owns the fact that a character has an inventory
container.  Item definitions, quantities, capacity and transfer rules remain
in the item domain.  These helpers therefore only resolve ownership and
return the item instance ids currently anchored to that container.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from trpg_server.core.state import Projection


@dataclass(frozen=True, slots=True)
class InventoryBinding:
    """A character-to-container ownership binding."""

    character_id: str
    container_id: str


@dataclass(frozen=True, slots=True)
class InventoryContainerSpec:
    """A generated empty inventory container for bootstrap compilation."""

    container_id: str
    owner_character_id: str
    kind: str = "inventory"
    location_id: str | None = None


@dataclass(frozen=True, slots=True)
class InventoryResolution:
    """Result of resolving explicit and generated inventory containers."""

    bindings: tuple[InventoryBinding, ...]
    generated: tuple[InventoryContainerSpec, ...]

    @property
    def by_character(self) -> dict[str, str]:
        return {binding.character_id: binding.container_id for binding in self.bindings}


def _container_field(container: Any, name: str, alias: str | None = None) -> Any:
    """Read a field from a model-like object without importing its domain."""

    if isinstance(container, Mapping):
        if name in container:
            return container[name]
        if alias is not None:
            return container.get(alias)
        return None
    value = getattr(container, name, None)
    if value is not None:
        return value
    if alias is not None:
        return getattr(container, alias, None)
    return None


def ensure_inventory_containers(
    character_ids: Iterable[str],
    explicit_containers: Iterable[Any],
    *,
    id_prefix: str = "inventory_",
) -> InventoryResolution:
    """Resolve exactly one inventory container for every runtime character.

    ``explicit_containers`` may contain scenario models, runtime state models,
    or plain mappings.  Only ``kind == "inventory"`` containers participate;
    all other containers are deliberately ignored.  An owner with multiple
    explicit inventory containers is an authoring error and raises ``ValueError``.
    Missing inventories receive deterministic empty containers.  No item or
    capacity policy is inferred here.
    """

    raw_character_ids = tuple(str(value) for value in character_ids)
    ordered_character_ids = tuple(dict.fromkeys(raw_character_ids))
    if len(ordered_character_ids) != len(raw_character_ids):
        raise ValueError("runtime character ids must be unique")
    character_set = set(ordered_character_ids)

    all_container_ids: set[str] = set()
    explicit_by_owner: dict[str, str] = {}
    for container in explicit_containers:
        container_id = _container_field(container, "id", "container_id")
        if container_id is None and isinstance(container, Mapping):
            container_id = container.get("containerId")
        if not container_id:
            raise ValueError("container id is required")
        container_id = str(container_id)
        if container_id in all_container_ids:
            raise ValueError(f"duplicate container id: {container_id}")
        all_container_ids.add(container_id)
        if _container_field(container, "kind") != "inventory":
            continue
        owner = _container_field(container, "owner_character_id", "ownerCharacterId")
        if not owner:
            raise ValueError(f"inventory container requires an owner: {container_id}")
        owner = str(owner)
        if owner not in character_set:
            raise ValueError(f"inventory owner is not a runtime character: {owner}")
        previous = explicit_by_owner.get(owner)
        if previous is not None:
            raise ValueError(
                f"character has multiple inventory containers: {owner} "
                f"({previous}, {container_id})"
            )
        explicit_by_owner[owner] = container_id

    generated: list[InventoryContainerSpec] = []
    bindings: list[InventoryBinding] = []
    for character_id in sorted(ordered_character_ids):
        container_id = explicit_by_owner.get(character_id)
        if container_id is None:
            container_id = f"{id_prefix}{character_id}"
            if container_id in all_container_ids:
                raise ValueError(
                    f"generated inventory id collides with an existing container: {container_id}"
                )
            all_container_ids.add(container_id)
            generated.append(
                InventoryContainerSpec(
                    container_id=container_id,
                    owner_character_id=character_id,
                )
            )
        bindings.append(
            InventoryBinding(character_id=character_id, container_id=container_id)
        )
    return InventoryResolution(tuple(bindings), tuple(generated))


def character_inventory_container(
    state: Projection,
    character_id: str,
) -> Any | None:
    """Return the character's inventory container, if one is projected."""

    declared_id = inventory_container_id(state, character_id)
    if declared_id is not None:
        container = state.containers.get(declared_id)
        if (
            container is not None
            and container.kind == "inventory"
            and container.owner_character_id == character_id
        ):
            return container
    # Historical events may have no binding declaration, or may have been
    # replayed with the declaration before the container event.  Resolve only
    # from an actually projected, correctly-owned inventory as a fallback.
    candidates = sorted(
        (
            container
            for container in state.containers.values()
            if container.kind == "inventory"
            and container.owner_character_id == character_id
        ),
        key=lambda value: value.container_id,
    )
    return candidates[0] if candidates else None


def inventory_container_id(state: Projection, character_id: str) -> str | None:
    """Return the read-only inventory container id for ``character_id``.

    New ``character.created`` events declare this id in the character profile.
    The container scan is retained as a compatibility fallback for historical
    events that predate ``inventoryContainerId``.
    """

    profile = state.character_profiles.get(character_id, {})
    declared = profile.get("inventoryContainerId")
    if isinstance(declared, str) and declared:
        # The declared id is the character-domain ownership fact.  It may be
        # observed before its container.created event during incremental
        # replay, so do not require the item projection to exist yet.
        return declared
    if declared is not None:
        # Invalid/non-string declarations do not become an inferred binding.
        return None
    candidates = sorted(
        container.container_id
        for container in state.containers.values()
        if container.kind == "inventory"
        and container.owner_character_id == character_id
    )
    return candidates[0] if candidates else None


def inventory_item_ids(state: Projection, character_id: str) -> tuple[str, ...]:
    """Return item instance ids in a character's inventory container.

    This is a read-only projection query.  It intentionally does not inspect
    quantities, item definitions, or operation permissions.
    """

    container = character_inventory_container(state, character_id)
    if container is None:
        return ()
    container_id = container.container_id
    return tuple(
        sorted(
            item.item_id
            for item in state.items.values()
            if item.container_id == container_id
        )
    )


def character_inventory_item_ids(state: Projection, character_id: str) -> tuple[str, ...]:
    """Explicitly named alias for callers in the character domain."""

    return inventory_item_ids(state, character_id)


def character_inventory_container_id(state: Projection, character_id: str) -> str | None:
    """Explicitly named alias for the inventory container id query."""

    return inventory_container_id(state, character_id)


__all__ = [
    "InventoryBinding",
    "InventoryContainerSpec",
    "InventoryResolution",
    "ensure_inventory_containers",
    "character_inventory_container",
    "character_inventory_container_id",
    "character_inventory_item_ids",
    "inventory_container_id",
    "inventory_item_ids",
]
