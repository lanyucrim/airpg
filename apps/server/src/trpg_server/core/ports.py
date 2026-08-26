from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from trpg_server.core.state import Event, ParsedCommand, Projection, Resolution
from trpg_server.memory import EpisodicMemory
from trpg_server.ai.player.narration import NarrationAudit
from trpg_server.characters.decision import ConfirmedNpcDecision


@runtime_checkable
class EventStorePort(Protocol):
    """Persistence boundary required by the authoritative service.

    Backend-specific connections remain opaque. Domain code may request event
    history and commit prepared records, but it must not depend on SQL syntax.
    """

    def initialize(self) -> None: ...

    def campaign_exists(self, campaign_id: str) -> bool: ...

    def reset_campaign(self, *args: Any, **kwargs: Any) -> None: ...

    def connect(self) -> Iterator[Any]: ...

    def campaign_version(self, connection: Any, campaign_id: str) -> int: ...

    def load_events(self, connection: Any, campaign_id: str) -> list[Event]: ...

    def find_command_response(
        self,
        connection: Any,
        campaign_id: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None: ...


@runtime_checkable
class CommandResolver(Protocol):
    def resolve(
        self,
        state: Projection,
        command: ParsedCommand,
        npc_decision: ConfirmedNpcDecision | None = None,
    ) -> Resolution: ...


@runtime_checkable
class ProjectionRunner(Protocol):
    def replay(
        self,
        campaign_id: str,
        events: Iterable[Event],
        state_version: int,
    ) -> Projection: ...

    def apply(self, state: Projection, event: Event) -> None: ...

    def public_state(self, state: Projection) -> dict[str, object]: ...


@runtime_checkable
class IntentParserPort(Protocol):
    def parse_with_audit(
        self,
        text: str,
        actor_id: str,
        state: Projection,
        source_message_id: str | None = None,
    ) -> Any: ...


@runtime_checkable
class NpcDeciderPort(Protocol):
    def decide(
        self,
        state: Projection,
        command: ParsedCommand,
        retrieved_memories: Sequence[EpisodicMemory] | None = None,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class NarrativeResult:
    text: str
    audit: NarrationAudit


@runtime_checkable
class NarratorPort(Protocol):
    def narrate(
        self,
        resolution: Resolution,
        state: Projection,
    ) -> NarrativeResult: ...


@dataclass(frozen=True, slots=True)
class LegacyCommandResolver:
    """Compatibility adapter while action families migrate out of engine.py."""

    item_interaction_adapter: Any | None = None

    def resolve(
        self,
        state: Projection,
        command: ParsedCommand,
        npc_decision: ConfirmedNpcDecision | None = None,
    ) -> Resolution:
        from trpg_server.behavior.router import resolve

        if self.item_interaction_adapter is None:
            return resolve(state, command, npc_decision)
        return resolve(
            state,
            command,
            npc_decision,
            item_interaction_adapter=self.item_interaction_adapter,
        )


@dataclass(frozen=True, slots=True)
class DefaultProjectionRunner:
    def replay(
        self,
        campaign_id: str,
        events: Iterable[Event],
        state_version: int,
    ) -> Projection:
        from trpg_server.core.projection import replay

        return replay(campaign_id, events, state_version)

    def apply(self, state: Projection, event: Event) -> None:
        from trpg_server.core.projection import apply_event

        apply_event(state, event)

    def public_state(self, state: Projection) -> dict[str, object]:
        from trpg_server.core.projection import public_state

        return public_state(state)
