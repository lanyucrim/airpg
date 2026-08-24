"""Build the runtime seed mirror from the authoritative item atlas.

The atlas owns all 40 definitions and the six confirmed initial instances.
This script only emits the runtime package's six-item seed mirror; it never
invents a definition, an item, a price, or an event.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "content" / "campaigns" / "gray-harbor"
ATLAS_PATH = CAMPAIGN / "items-atlas" / "important-items.json"
SERVER_SRC = ROOT / "apps" / "server" / "src"
if str(SERVER_SRC) not in sys.path:
    sys.path.insert(0, str(SERVER_SRC))

from trpg_server.items.catalog import load_item_atlas  # noqa: E402


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_runtime_seed() -> dict[str, Any]:
    atlas = load_item_atlas(ATLAS_PATH)
    containers = _read_json(CAMPAIGN / "containers.json").get("containers", [])
    container_ids = {
        value.get("id")
        for value in containers
        if isinstance(value, dict) and isinstance(value.get("id"), str)
    }
    instances = [dict(value) for value in atlas.instances]
    unknown_containers = sorted(
        value["containerId"]
        for value in instances
        if value["containerId"] is not None and value["containerId"] not in container_ids
    )
    if unknown_containers:
        raise ValueError(
            "initial item instances reference unknown containers: "
            + ", ".join(unknown_containers)
        )
    return {
        "schemaVersion": 3,
        "atlasFile": "items-atlas/important-items.json",
        "instances": instances,
    }


def main() -> None:
    seed = build_runtime_seed()
    _write_json(CAMPAIGN / "items.json", seed)
    print(f"generated {len(seed['instances'])} runtime item instances")


if __name__ == "__main__":
    main()
