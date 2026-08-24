"""Character interaction command dispatch."""

from typing import TYPE_CHECKING

from trpg_server.core.state import ParsedCommand, Projection, Resolution

if TYPE_CHECKING:
    from trpg_server.characters.decision import ConfirmedNpcDecision

ACTION_TYPES = frozenset({
    "offer_item", "request_item", "claim_past_gift", "claim_item_possession",
    "find_character", "speak",
})


def resolve_character_command(
    state: Projection,
    command: ParsedCommand,
    npc_decision: "ConfirmedNpcDecision | None" = None,
) -> Resolution:
    from trpg_server.behavior import router

    if command.action_type == "offer_item":
        return router._resolve_offer_item(state, command, npc_decision)
    handlers = {
        "request_item": router._resolve_request_item,
        "claim_past_gift": router._resolve_claim_past_gift,
        "claim_item_possession": router._resolve_claim_item_possession,
        "find_character": router._resolve_find_character,
        "speak": router._resolve_speech,
    }
    return handlers[command.action_type](state, command)
