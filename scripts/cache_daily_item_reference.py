"""Cache one validated ordinary-item price and weight reference.

Cache hits never initialize or call an AI adapter.  A cache miss only reaches
DeepSeek when ``--allow-ai`` is explicitly supplied, and one accepted response
updates both the machine-readable JSON and its Markdown review table.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_SRC = ROOT / "apps" / "server" / "src"
DEFAULT_TABLE = (
    ROOT
    / "content"
    / "campaigns"
    / "gray-harbor"
    / "items-atlas"
    / "ai-items"
    / "daily-item-references.json"
)
DEFAULT_MARKDOWN = DEFAULT_TABLE.with_suffix(".md")
if str(SERVER_SRC) not in sys.path:
    sys.path.insert(0, str(SERVER_SRC))

from trpg_server.ai.platform.deepseek import DeepSeekSettings  # noqa: E402
from trpg_server.ai.platform.environment import load_backend_environment  # noqa: E402
from trpg_server.items.ai_items.deepseek_adapter import (  # noqa: E402
    DeepSeekItemReferenceAdapter,
)
from trpg_server.items.ai_items.references import (  # noqa: E402
    DailyItemReferenceRequest,
    DailyItemReferenceTable,
    render_daily_item_reference_markdown,
    resolve_daily_item_reference,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read or cache one ordinary item price/weight reference."
    )
    parser.add_argument("--item-key", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--unit-description", required=True)
    parser.add_argument("--alias", action="append", default=[])
    parser.add_argument(
        "--allow-ai",
        action="store_true",
        help="Allow one DeepSeek estimate when the table has no matching record.",
    )
    parser.add_argument("--table", type=Path, default=DEFAULT_TABLE)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    table = DailyItemReferenceTable.load(args.table)
    request = DailyItemReferenceRequest(
        item_key=args.item_key,
        name=args.name,
        unit_description=args.unit_description,
        aliases=tuple(args.alias),
    )

    cached = resolve_daily_item_reference(table, request)
    if cached.status == "cache_hit":
        reference = cached.reference
        assert reference is not None
        print(
            f"cache hit: {reference.item_key} = {reference.value_crown} crowns, "
            f"{reference.unit_weight_grams} g; no AI call"
        )
        return
    if cached.status == "unit_mismatch":
        raise SystemExit(f"reference rejected: {cached.reason}")
    if not args.allow_ai:
        raise SystemExit(
            "cache miss: rerun with --allow-ai to request one price/weight estimate"
        )

    load_backend_environment()
    adapter = DeepSeekItemReferenceAdapter(DeepSeekSettings.from_environment())
    resolution = resolve_daily_item_reference(table, request, adapter)
    if resolution.status != "model_accepted" or resolution.reference is None:
        raise SystemExit(f"reference rejected: {resolution.reason or resolution.status}")

    table.save(args.table)
    args.markdown.write_text(
        render_daily_item_reference_markdown(table),
        encoding="utf-8",
    )
    reference = resolution.reference
    tokens = reference.model_audit.total_tokens if reference.model_audit else None
    print(
        f"cached: {reference.item_key} = {reference.value_crown} crowns, "
        f"{reference.unit_weight_grams} g, tokens={tokens if tokens is not None else 'unknown'}"
    )


if __name__ == "__main__":
    main()
