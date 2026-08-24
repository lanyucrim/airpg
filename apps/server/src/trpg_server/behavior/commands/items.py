"""Item and commerce command dispatch."""

from trpg_server.core.state import ParsedCommand, Projection, Resolution

ACTION_TYPES = frozenset({
    "take_item", "consume_item", "use_item", "equip_item", "unequip_item", "combine_items",
    "discard_item", "destroy_item", "purchase_item",
})


def resolve_item_command(state: Projection, command: ParsedCommand) -> Resolution:
    from trpg_server.behavior import router

    handlers = {
        "take_item": router._resolve_take_item,
        "consume_item": router._resolve_consume_item,
        "use_item": router._resolve_use_item,
        "equip_item": router._resolve_equip_item,
        "unequip_item": router._resolve_unequip_item,
        "combine_items": router._resolve_combine_items,
        "discard_item": router._resolve_discard_item,
        "destroy_item": router._resolve_destroy_item,
        "purchase_item": router.resolve_purchase,
    }
    return handlers[command.action_type](state, command)
