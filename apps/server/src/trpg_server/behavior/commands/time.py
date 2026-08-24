"""Time passage command dispatch."""

from trpg_server.core.state import ParsedCommand, Projection, Resolution

ACTION_TYPES = frozenset({"wait"})


def resolve_time_command(state: Projection, command: ParsedCommand) -> Resolution:
    from trpg_server.behavior.router import _resolve_wait

    return _resolve_wait(state, command)
