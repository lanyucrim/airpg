from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from trpg_server.core.state import Event
from trpg_server.core.projection import public_state, replay


CAMPAIGN_TABLES = (
    "campaigns",
    "events",
    "messages",
    "commands",
    "intent_attempts",
    "narration_attempts",
    "npc_decision_attempts",
    "episodic_memories",
    "memory_links",
    "scene_memory_summaries",
    "retrieval_traces",
)

DEPENDENT_TABLE_QUERIES = {
    "event_sources": """
        SELECT value.* FROM event_sources value
        JOIN events parent ON parent.event_id=value.event_id
        WHERE parent.campaign_id=?
    """,
    "memory_entities": """
        SELECT value.* FROM memory_entities value
        JOIN episodic_memories parent ON parent.memory_id=value.memory_id
        WHERE parent.campaign_id=?
    """,
    "memory_scopes": """
        SELECT value.* FROM memory_scopes value
        JOIN episodic_memories parent ON parent.memory_id=value.memory_id
        WHERE parent.campaign_id=?
    """,
    "scene_summary_event_sources": """
        SELECT value.* FROM scene_summary_event_sources value
        JOIN scene_memory_summaries parent ON parent.summary_id=value.summary_id
        WHERE parent.campaign_id=?
    """,
    "scene_summary_memory_sources": """
        SELECT value.* FROM scene_summary_memory_sources value
        JOIN scene_memory_summaries parent ON parent.summary_id=value.summary_id
        WHERE parent.campaign_id=?
    """,
}

IMPORT_ORDER = (
    "campaigns",
    "events",
    "messages",
    "event_sources",
    "commands",
    "intent_attempts",
    "narration_attempts",
    "npc_decision_attempts",
    "episodic_memories",
    "memory_entities",
    "memory_scopes",
    "memory_links",
    "scene_memory_summaries",
    "scene_summary_event_sources",
    "scene_summary_memory_sources",
    "retrieval_traces",
)


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _dict_rows(cursor: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


def campaign_rows(connection: Any, campaign_id: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for table in CAMPAIGN_TABLES:
        rows = _dict_rows(connection.execute(
            f"SELECT * FROM {table} WHERE campaign_id=?", (campaign_id,)
        ))
        result[table] = _sorted_rows(rows)
    for table, query in DEPENDENT_TABLE_QUERIES.items():
        result[table] = _sorted_rows(
            _dict_rows(connection.execute(query, (campaign_id,)))
        )
    return result


def _sorted_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
    )


def campaign_fingerprint(connection: Any, campaign_id: str) -> dict[str, object]:
    rows = campaign_rows(connection, campaign_id)
    campaigns = rows["campaigns"]
    if len(campaigns) != 1:
        raise KeyError(f"campaign not found or duplicated: {campaign_id}")
    events = sorted(rows["events"], key=lambda row: int(row["sequence"]))
    domain_events = [
        Event(
            event_id=str(row["event_id"]),
            event_type=str(row["event_type"]),
            actor_id=str(row["actor_id"]),
            world_time=int(row["world_time"]),
            payload=json.loads(str(row["payload_json"])),
            schema_version=int(row["schema_version"]),
        )
        for row in events
    ]
    state = replay(campaign_id, domain_events, int(campaigns[0]["state_version"]))
    memory_summary = [
        {
            "memory_id": row["memory_id"],
            "source_event_id": row["source_event_id"],
            "summary": row["summary"],
            "world_time": row["world_time"],
        }
        for row in rows["episodic_memories"]
    ]
    return {
        "campaign_id": campaign_id,
        "event_count": len(events),
        "event_ids": [row["event_id"] for row in events],
        "event_digest": _canonical_digest(events),
        "source_digest": _canonical_digest(rows["event_sources"]),
        "projection_digest": _canonical_digest(public_state(state)),
        "memory_digest": _canonical_digest(memory_summary),
        "table_counts": {table: len(values) for table, values in sorted(rows.items())},
        "storage_digest": _canonical_digest(rows),
    }


def sqlite_fingerprint(path: Path, campaign_id: str) -> dict[str, object]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return campaign_fingerprint(connection, campaign_id)


def _insert_rows(connection: Any, table: str, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    columns = tuple(rows[0].keys())
    placeholders = ",".join("?" for _ in columns)
    connection.executemany(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
        [tuple(row[column] for column in columns) for row in rows],
    )


def migrate_campaign(sqlite_path: Path, campaign_id: str, database_url: str) -> dict[str, object]:
    from trpg_server.core.postgres_store import PostgresEventStore
    from trpg_server.core.store import _fts_ngrams

    source_manifest = sqlite_fingerprint(sqlite_path, campaign_id)
    with sqlite3.connect(sqlite_path) as sqlite_connection:
        sqlite_connection.row_factory = sqlite3.Row
        source_rows = campaign_rows(sqlite_connection, campaign_id)

    store = PostgresEventStore(database_url)
    store.initialize()
    if store.campaign_exists(campaign_id):
        raise RuntimeError(
            "target campaign already exists; migration refuses to overwrite it"
        )
    with store.connect() as target:
        for table in IMPORT_ORDER:
            _insert_rows(target, table, source_rows[table])
        for memory in source_rows["episodic_memories"]:
            target.execute(
                "INSERT INTO episodic_memory_fts(memory_id,campaign_id,summary) "
                "VALUES(?,?,?)",
                (memory["memory_id"], memory["campaign_id"], memory["summary"]),
            )
            target.execute(
                "INSERT INTO episodic_memory_fts_ngrams(memory_id,campaign_id,terms) "
                "VALUES(?,?,?)",
                (
                    memory["memory_id"],
                    memory["campaign_id"],
                    _fts_ngrams(str(memory["summary"])),
                ),
            )
    with store.connect() as target:
        target_manifest = campaign_fingerprint(target, campaign_id)
    if source_manifest != target_manifest:
        raise RuntimeError(json.dumps(
            {"source": source_manifest, "target": target_manifest},
            ensure_ascii=False,
            sort_keys=True,
        ))
    return target_manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate one campaign from SQLite to PostgreSQL and verify it"
    )
    parser.add_argument("sqlite_path", type=Path)
    parser.add_argument("campaign_id")
    parser.add_argument("--database-url", default=os.getenv("TRPG_DATABASE_URL", ""))
    parser.add_argument("--fingerprint-only", action="store_true")
    args = parser.parse_args()
    if args.fingerprint_only:
        result = sqlite_fingerprint(args.sqlite_path, args.campaign_id)
    else:
        if not args.database_url.startswith(("postgres://", "postgresql://")):
            parser.error("--database-url or TRPG_DATABASE_URL must be a PostgreSQL URL")
        result = migrate_campaign(args.sqlite_path, args.campaign_id, args.database_url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
