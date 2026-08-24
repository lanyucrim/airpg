"""Investigation and authored interaction command dispatch."""

from trpg_server.core.state import ParsedCommand, Projection, Resolution

ACTION_TYPES = frozenset({
    "inspect_item", "inspect_item_generic", "ask_topic", "investigate_location",
})


def resolve_story_command(state: Projection, command: ParsedCommand) -> Resolution:
    from trpg_server.behavior import router

    handlers = {
        "inspect_item": router._resolve_inspect_item,
        "inspect_item_generic": router._resolve_inspect_item_generic,
        "ask_topic": router._resolve_ask_topic,
        "investigate_location": router._resolve_investigate_location,
    }
    return handlers[command.action_type](state, command)
