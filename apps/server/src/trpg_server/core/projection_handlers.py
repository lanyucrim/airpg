from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from trpg_server.core.state import Event, Projection


ProjectionHandler = Callable[[Projection, Event], None]


@dataclass(slots=True)
class ProjectionHandlerRegistry:
    _handlers: dict[str, ProjectionHandler] = field(default_factory=dict)

    def register(
        self,
        *event_types: str,
    ) -> Callable[[ProjectionHandler], ProjectionHandler]:
        def decorator(handler: ProjectionHandler) -> ProjectionHandler:
            for event_type in event_types:
                if event_type in self._handlers:
                    raise ValueError(f"duplicate projection handler: {event_type}")
                self._handlers[event_type] = handler
            return handler

        return decorator

    def handler_for(self, event_type: str) -> ProjectionHandler | None:
        return self._handlers.get(event_type)

    @property
    def event_types(self) -> frozenset[str]:
        return frozenset(self._handlers)


projection_handlers = ProjectionHandlerRegistry()
