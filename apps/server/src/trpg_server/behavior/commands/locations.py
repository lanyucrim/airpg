"""Movement and environment command dispatch."""

from trpg_server.core.state import ParsedCommand, Projection, Resolution

ACTION_TYPES = frozenset({"move", "search_location", "environment_action"})


def resolve_location_command(state: Projection, command: ParsedCommand) -> Resolution:
    from trpg_server.behavior import router

    handlers = {
        "move": router._resolve_move,
        "search_location": router._resolve_search_location,
        "environment_action": router._resolve_environment_action,
    }
    return handlers[command.action_type](state, command)
