"""Generate or reuse one validated ordinary item definition.

The command only updates design-time catalogs.  It never creates a runtime
item instance, submits an event, or decides that a character acquired an item.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SERVER_SRC = ROOT / "apps" / "server" / "src"
AI_ITEM_DIR = (
    ROOT
    / "content"
    / "campaigns"
    / "gray-harbor"
    / "items-atlas"
    / "ai-items"
)
DEFAULT_CATALOG = AI_ITEM_DIR / "daily-item-definitions.json"
DEFAULT_REFERENCES = AI_ITEM_DIR / "daily-item-references.json"
DEFAULT_KNOWN_ATLAS = AI_ITEM_DIR.parent / "important-items.json"
if str(SERVER_SRC) not in sys.path:
    sys.path.insert(0, str(SERVER_SRC))

from trpg_server.ai.platform.deepseek import DeepSeekSettings  # noqa: E402
from trpg_server.ai.platform.environment import load_backend_environment  # noqa: E402
from trpg_server.items.ai_items.deepseek_adapter import (  # noqa: E402
    DeepSeekDailyItemGenerationAdapter,
)
from trpg_server.items.ai_items.generation import (  # noqa: E402
    DailyItemDefinitionCatalog,
    DailyItemGenerationRequest,
    render_daily_item_definition_markdown,
    resolve_daily_item_definition,
)
from trpg_server.items.ai_items.references import (  # noqa: E402
    DailyItemReferenceTable,
    render_daily_item_reference_markdown,
)
from trpg_server.items.catalog import load_item_atlas  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read or generate one reusable ordinary item definition."
    )
    parser.add_argument("--text", required=True, help="Observed daily item phrase.")
    parser.add_argument(
        "--allow-ai",
        action="store_true",
        help="Allow one DeepSeek call when no generated definition matches.",
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--references", type=Path, default=DEFAULT_REFERENCES)
    parser.add_argument("--known-atlas", type=Path, default=DEFAULT_KNOWN_ATLAS)
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    catalog = DailyItemDefinitionCatalog.load(args.catalog)
    references = DailyItemReferenceTable.load(args.references)
    known_definitions = load_item_atlas(args.known_atlas).definitions
    request = DailyItemGenerationRequest(args.text)

    cached = resolve_daily_item_definition(
        catalog,
        references,
        request,
        known_definitions=known_definitions,
    )
    if cached.status in {"cache_hit", "known_definition", "known_alias_hit"}:
        definition = cached.definition
        assert definition is not None
        print(
            f"{cached.status}: {definition['definitionId']} = "
            f"{_display_value(definition['valueCrown'], 'crowns')}, "
            f"{_display_value(definition['unitWeightGrams'], 'g')}; no AI call"
        )
        return
    if not args.allow_ai:
        raise SystemExit(
            "cache miss: rerun with --allow-ai to request one daily item definition"
        )

    load_backend_environment()
    adapter = DeepSeekDailyItemGenerationAdapter(
        DeepSeekSettings.from_environment()
    )
    resolution = resolve_daily_item_definition(
        catalog,
        references,
        request,
        adapter,
        known_definitions=known_definitions,
    )
    if resolution.status == "known_definition_reused":
        definition = resolution.definition
        assert definition is not None
        _write_catalog_pair(
            args.catalog,
            resolution.catalog,
            render_daily_item_definition_markdown(resolution.catalog),
        )
        print(
            f"known_definition_reused: {definition['definitionId']}; "
            "AI catalog unchanged"
        )
        return
    if resolution.entry is None or resolution.status not in {
        "model_accepted",
        "equivalent_reused",
    }:
        raise SystemExit(f"definition rejected: {resolution.reason or resolution.status}")

    _write_catalog_pair(
        args.catalog,
        resolution.catalog,
        render_daily_item_definition_markdown(resolution.catalog),
    )
    _write_reference_pair(
        args.references,
        resolution.reference_table,
        render_daily_item_reference_markdown(resolution.reference_table),
    )
    entry = resolution.entry
    audit = entry.model_audit
    print(
        f"{resolution.status}: {entry.definition_id} = "
        f"{entry.item['valueCrown']} crowns, {entry.item['unitWeightGrams']} g, "
        f"tokens={audit.total_tokens if audit and audit.total_tokens is not None else 'unknown'}"
    )


def _write_catalog_pair(
    path: Path,
    catalog: DailyItemDefinitionCatalog,
    markdown: str,
) -> None:
    catalog.save(path)
    path.with_suffix(".md").write_text(markdown, encoding="utf-8")


def _write_reference_pair(
    path: Path,
    table: DailyItemReferenceTable,
    markdown: str,
) -> None:
    table.save(path)
    path.with_suffix(".md").write_text(markdown, encoding="utf-8")


def _display_value(value: object, unit: str) -> str:
    return f"{value} {unit}" if value is not None else f"unknown {unit}"


if __name__ == "__main__":
    main()
