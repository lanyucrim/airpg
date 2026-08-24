from __future__ import annotations

from pathlib import Path

from trpg_server.story.v4_compiler import write_v42_catalog


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "灰港_黑潮王座_V4.2_AI_GM主线状态机与支线条件版.md"
OUTPUT = ROOT / "content" / "campaigns" / "gray-harbor" / "v4.2-catalog.json"


if __name__ == "__main__":
    catalog = write_v42_catalog(
        SOURCE,
        OUTPUT,
        "gray-harbor-black-tide-throne",
    )
    print({
        "source": catalog.source_document,
        "sha256": catalog.source_sha256,
        "canonLayers": len(catalog.canon_layers),
        "mainlineStates": len(catalog.mainline_state_machine.states),
        "characters": len(catalog.characters),
        "organizations": len(catalog.organizations),
        "locations": len(catalog.locations),
        "affordances": len(catalog.affordances),
        "criticalItems": len(catalog.critical_items),
        "eventSeeds": len(catalog.event_seeds),
        "documents": len(catalog.documents),
        "timeline": len(catalog.timeline),
        "sideQuests": len(catalog.side_quests),
    })
