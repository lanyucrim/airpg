from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from typing import Protocol, runtime_checkable

from trpg_server.core.state import Event, RawMessage
from trpg_server.memory import (
    EpisodicMemory,
    MemoryEntity,
    MemoryLink,
    MemoryQuery,
    MemoryReadModel,
    MemoryScope,
    MemorySelection,
    SceneMemorySummary,
)


class StoreIntegrityError(RuntimeError):
    """Backend-neutral constraint violation used for idempotent retries."""


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    state_version INTEGER NOT NULL DEFAULT 0,
    scenario_id TEXT,
    scenario_version TEXT,
    scenario_content_hash TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    turn_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    world_time INTEGER NOT NULL,
    actor_id TEXT NOT NULL,
    causation_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE(campaign_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_events_campaign_type
ON events(campaign_id, event_type, sequence);

CREATE INDEX IF NOT EXISTS idx_events_campaign_turn
ON events(campaign_id, turn_id, sequence);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    speaker_type TEXT NOT NULL,
    speaker_id TEXT NOT NULL,
    message_kind TEXT NOT NULL,
    content TEXT NOT NULL,
    world_time INTEGER NOT NULL,
    authority TEXT NOT NULL,
    token_count INTEGER,
    recorded_at TEXT NOT NULL,
    UNIQUE(campaign_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_messages_campaign_turn
ON messages(campaign_id, turn_id, sequence);

CREATE TABLE IF NOT EXISTS event_sources (
    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    message_id TEXT NOT NULL REFERENCES messages(message_id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    PRIMARY KEY(event_id, message_id, source_kind)
);

CREATE INDEX IF NOT EXISTS idx_event_sources_message
ON event_sources(message_id, event_id);

CREATE TABLE IF NOT EXISTS commands (
    command_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    expected_state_version INTEGER NOT NULL,
    request_json TEXT NOT NULL,
    response_json TEXT NOT NULL,
    status TEXT NOT NULL,
    turn_id TEXT,
    source_message_id TEXT,
    narrator_message_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(campaign_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS intent_attempts (
    attempt_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_name TEXT,
    model_name TEXT,
    request_json TEXT,
    response_json TEXT,
    failure_code TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    latency_ms INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_intent_attempts_campaign_turn
ON intent_attempts(campaign_id, turn_id, created_at);

CREATE TABLE IF NOT EXISTS narration_attempts (
    attempt_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_name TEXT,
    model_name TEXT,
    request_json TEXT,
    response_json TEXT,
    failure_code TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    latency_ms INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_narration_attempts_campaign_turn
ON narration_attempts(campaign_id, turn_id, created_at);

CREATE TABLE IF NOT EXISTS npc_decision_attempts (
    attempt_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_name TEXT,
    model_name TEXT,
    request_json TEXT,
    response_json TEXT,
    failure_code TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    latency_ms INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_npc_decision_attempts_campaign_turn
ON npc_decision_attempts(campaign_id, turn_id, created_at);

CREATE TABLE IF NOT EXISTS routine_attempts (
    attempt_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_name TEXT,
    model_name TEXT,
    request_json TEXT,
    response_json TEXT,
    rejected_json TEXT,
    failure_code TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    latency_ms INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_routine_attempts_campaign_turn
ON routine_attempts(campaign_id, turn_id, created_at);

CREATE TABLE IF NOT EXISTS episodic_memories (
    memory_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    source_event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id) ON DELETE CASCADE,
    schema_version INTEGER NOT NULL,
    memory_type TEXT NOT NULL,
    event_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    importance INTEGER NOT NULL CHECK(importance BETWEEN 0 AND 100),
    world_time INTEGER NOT NULL CHECK(world_time >= 0),
    location_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('active')),
    update_key TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_campaign_time
ON episodic_memories(campaign_id, world_time DESC, importance DESC);

CREATE INDEX IF NOT EXISTS idx_memories_campaign_type_time
ON episodic_memories(campaign_id, event_type, world_time DESC);

-- FTS is a rebuildable candidate index only.  It never stores scope or state
-- authority, which remain in the normalized tables and deterministic filters.
CREATE VIRTUAL TABLE IF NOT EXISTS episodic_memory_fts USING fts5(
    memory_id UNINDEXED,
    campaign_id UNINDEXED,
    summary,
    tokenize = 'unicode61 remove_diacritics 0'
);

CREATE VIRTUAL TABLE IF NOT EXISTS episodic_memory_fts_ngrams USING fts5(
    memory_id UNINDEXED,
    campaign_id UNINDEXED,
    terms,
    tokenize = 'unicode61 remove_diacritics 0'
);

CREATE TABLE IF NOT EXISTS memory_entities (
    memory_id TEXT NOT NULL REFERENCES episodic_memories(memory_id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL,
    role TEXT NOT NULL,
    PRIMARY KEY(memory_id, entity_id, role)
);

CREATE INDEX IF NOT EXISTS idx_memory_entities_entity
ON memory_entities(entity_id, memory_id);

CREATE TABLE IF NOT EXISTS memory_scopes (
    memory_id TEXT NOT NULL REFERENCES episodic_memories(memory_id) ON DELETE CASCADE,
    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('player', 'npc')),
    scope_id TEXT NOT NULL,
    PRIMARY KEY(memory_id, scope_kind, scope_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_scopes_perspective
ON memory_scopes(scope_kind, scope_id, memory_id);

CREATE TABLE IF NOT EXISTS memory_links (
    link_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    source_memory_id TEXT NOT NULL REFERENCES episodic_memories(memory_id) ON DELETE CASCADE,
    target_memory_id TEXT NOT NULL REFERENCES episodic_memories(memory_id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK(relation_type IN ('updates', 'caused_by')),
    source_event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    schema_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source_memory_id, target_memory_id, relation_type),
    CHECK(source_memory_id != target_memory_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_links_target_relation
ON memory_links(target_memory_id, relation_type, source_memory_id);

CREATE TABLE IF NOT EXISTS scene_memory_summaries (
    summary_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    scene_id TEXT NOT NULL,
    segment_index INTEGER NOT NULL CHECK(segment_index >= 0),
    location_id TEXT,
    schema_version INTEGER NOT NULL,
    content TEXT NOT NULL,
    start_sequence INTEGER NOT NULL,
    end_sequence INTEGER NOT NULL,
    start_world_time INTEGER NOT NULL,
    end_world_time INTEGER NOT NULL,
    generator TEXT NOT NULL,
    generator_version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('rolling', 'closed')),
    resolved_json TEXT NOT NULL,
    unresolved_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(campaign_id, scene_id, segment_index),
    CHECK(start_sequence <= end_sequence),
    CHECK(start_world_time <= end_world_time)
);

CREATE INDEX IF NOT EXISTS idx_scene_summaries_campaign_scene
ON scene_memory_summaries(campaign_id, scene_id, segment_index);

CREATE TABLE IF NOT EXISTS scene_summary_event_sources (
    summary_id TEXT NOT NULL REFERENCES scene_memory_summaries(summary_id) ON DELETE CASCADE,
    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    PRIMARY KEY(summary_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_scene_summary_events_event
ON scene_summary_event_sources(event_id, summary_id);

CREATE TABLE IF NOT EXISTS scene_summary_memory_sources (
    summary_id TEXT NOT NULL REFERENCES scene_memory_summaries(summary_id) ON DELETE CASCADE,
    memory_id TEXT NOT NULL REFERENCES episodic_memories(memory_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    PRIMARY KEY(summary_id, memory_id)
);

CREATE TABLE IF NOT EXISTS retrieval_traces (
    trace_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    turn_id TEXT,
    purpose TEXT NOT NULL,
    perspective_kind TEXT NOT NULL,
    perspective_id TEXT NOT NULL,
    query_json TEXT NOT NULL,
    candidate_json TEXT NOT NULL,
    rejected_json TEXT NOT NULL,
    selected_json TEXT NOT NULL,
    used_characters INTEGER NOT NULL,
    route TEXT NOT NULL,
    candidate_total INTEGER NOT NULL DEFAULT 0,
    candidate_limit INTEGER NOT NULL DEFAULT 0,
    truncated INTEGER NOT NULL DEFAULT 0,
    expanded_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_retrieval_traces_campaign_turn
ON retrieval_traces(campaign_id, turn_id, created_at);
"""


@runtime_checkable
class EventStoreBackend(Protocol):
    """Persistence boundary used by GameService.

    The concrete backend must preserve event ordering, transaction boundaries,
    idempotency, and the rebuildability of every non-authoritative read model.
    """

    def initialize(self) -> None: ...

    def campaign_exists(self, campaign_id: str) -> bool: ...

    def reset_campaign(self, *args: Any, **kwargs: Any) -> None: ...

    def connect(self) -> Iterator[Any]: ...


class EventStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)
            connection.execute("PRAGMA optimize")

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        command_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(commands)").fetchall()
        }
        additions = {
            "turn_id": "TEXT",
            "source_message_id": "TEXT",
            "narrator_message_id": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in command_columns:
                connection.execute(
                    f"ALTER TABLE commands ADD COLUMN {name} {declaration}"
                )
        campaign_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(campaigns)").fetchall()
        }
        campaign_additions = {
            "scenario_id": "TEXT",
            "scenario_version": "TEXT",
            "scenario_content_hash": "TEXT",
        }
        for name, declaration in campaign_additions.items():
            if name not in campaign_columns:
                connection.execute(
                    f"ALTER TABLE campaigns ADD COLUMN {name} {declaration}"
                )
        intent_attempt_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(intent_attempts)"
            ).fetchall()
        }
        intent_attempt_additions = {
            "provider_name": "TEXT",
            "prompt_tokens": "INTEGER",
            "completion_tokens": "INTEGER",
            "total_tokens": "INTEGER",
            "latency_ms": "INTEGER",
        }
        for name, declaration in intent_attempt_additions.items():
            if name not in intent_attempt_columns:
                connection.execute(
                    f"ALTER TABLE intent_attempts ADD COLUMN {name} {declaration}"
                )
        memory_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(episodic_memories)"
            ).fetchall()
        }
        if "update_key" not in memory_columns:
            connection.execute(
                "ALTER TABLE episodic_memories ADD COLUMN update_key TEXT"
            )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memories_campaign_update_key
            ON episodic_memories(campaign_id, update_key)
            WHERE update_key IS NOT NULL
            """
        )
        summary_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(scene_memory_summaries)"
            ).fetchall()
        }
        for name in ("resolved_json", "unresolved_json"):
            if name not in summary_columns:
                connection.execute(
                    f"ALTER TABLE scene_memory_summaries "
                    f"ADD COLUMN {name} TEXT NOT NULL DEFAULT '[]'"
                )
        trace_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(retrieval_traces)"
            ).fetchall()
        }
        for name, declaration in {
            "candidate_total": "INTEGER NOT NULL DEFAULT 0",
            "candidate_limit": "INTEGER NOT NULL DEFAULT 0",
            "truncated": "INTEGER NOT NULL DEFAULT 0",
            "expanded_json": "TEXT NOT NULL DEFAULT '[]'",
        }.items():
            if name not in trace_columns:
                connection.execute(
                    f"ALTER TABLE retrieval_traces ADD COLUMN {name} {declaration}"
                )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_commands_campaign_turn
            ON commands(campaign_id, turn_id)
            """
        )
        connection.execute(
            """
            INSERT INTO episodic_memory_fts (memory_id, campaign_id, summary)
            SELECT m.memory_id, m.campaign_id, m.summary
            FROM episodic_memories AS m
            WHERE NOT EXISTS (
                SELECT 1 FROM episodic_memory_fts AS f
                WHERE f.memory_id = m.memory_id
            )
            """
        )
        memory_rows = connection.execute(
            """
            SELECT m.memory_id, m.campaign_id, m.summary
            FROM episodic_memories AS m
            WHERE NOT EXISTS (
                SELECT 1 FROM episodic_memory_fts_ngrams AS f
                WHERE f.memory_id = m.memory_id
            )
            """
        ).fetchall()
        connection.executemany(
            """
            INSERT INTO episodic_memory_fts_ngrams (memory_id, campaign_id, terms)
            VALUES (?, ?, ?)
            """,
            [
                (str(row["memory_id"]), str(row["campaign_id"]), _fts_ngrams(str(row["summary"])))
                for row in memory_rows
            ],
        )
        connection.execute(
            """
            DELETE FROM campaigns
            WHERE campaign_id = 'cmp_guard_wine'
              AND scenario_id IN ('gray-harbor', 'guard-wine-demo')
            """
        )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def campaign_version(self, connection: sqlite3.Connection, campaign_id: str) -> int:
        row = connection.execute(
            "SELECT state_version FROM campaigns WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        if row is None:
            raise KeyError(campaign_id)
        return int(row["state_version"])

    def replace_campaign_memories(
        self,
        connection: sqlite3.Connection,
        campaign_id: str,
        memories: list[EpisodicMemory],
    ) -> None:
        connection.execute(
            "DELETE FROM episodic_memories WHERE campaign_id = ?",
            (campaign_id,),
        )
        connection.execute(
            "DELETE FROM episodic_memory_fts WHERE campaign_id = ?",
            (campaign_id,),
        )
        connection.execute(
            "DELETE FROM episodic_memory_fts_ngrams WHERE campaign_id = ?",
            (campaign_id,),
        )
        for memory in memories:
            self.append_memory(connection, memory)

    def replace_campaign_memory_model(
        self,
        connection: sqlite3.Connection,
        campaign_id: str,
        model: MemoryReadModel,
    ) -> None:
        connection.execute(
            "DELETE FROM scene_memory_summaries WHERE campaign_id = ?",
            (campaign_id,),
        )
        connection.execute(
            "DELETE FROM episodic_memories WHERE campaign_id = ?",
            (campaign_id,),
        )
        connection.execute(
            "DELETE FROM episodic_memory_fts WHERE campaign_id = ?",
            (campaign_id,),
        )
        connection.execute(
            "DELETE FROM episodic_memory_fts_ngrams WHERE campaign_id = ?",
            (campaign_id,),
        )
        for memory in model.memories:
            self.append_memory(connection, memory)
        for link in model.links:
            self.append_memory_link(connection, link)
        for summary in model.scene_summaries:
            self.append_scene_summary(connection, summary)

    def append_memory_model_delta(
        self,
        connection: sqlite3.Connection,
        model: MemoryReadModel,
    ) -> None:
        for memory in model.memories:
            self.append_memory(connection, memory)
        for link in model.links:
            self.append_memory_link(connection, link)
        for summary in model.scene_summaries:
            self.upsert_scene_summary(connection, summary)

    def load_memory_anchor_indexes(
        self,
        connection: sqlite3.Connection,
        campaign_id: str,
        source_event_ids: set[str],
        update_keys: set[str],
    ) -> tuple[dict[str, str], dict[str, str]]:
        by_source_event: dict[str, str] = {}
        latest_by_update_key: dict[str, str] = {}
        if source_event_ids:
            placeholders = ",".join("?" for _ in source_event_ids)
            rows = connection.execute(
                f"""
                SELECT source_event_id, memory_id
                FROM episodic_memories
                WHERE campaign_id = ? AND source_event_id IN ({placeholders})
                """,
                [campaign_id, *sorted(source_event_ids)],
            ).fetchall()
            by_source_event = {
                str(row["source_event_id"]): str(row["memory_id"])
                for row in rows
            }
        if update_keys:
            placeholders = ",".join("?" for _ in update_keys)
            rows = connection.execute(
                f"""
                SELECT m.update_key, m.memory_id
                FROM episodic_memories AS m
                JOIN events AS e ON e.event_id = m.source_event_id
                WHERE m.campaign_id = ? AND m.update_key IN ({placeholders})
                ORDER BY e.sequence
                """,
                [campaign_id, *sorted(update_keys)],
            ).fetchall()
            for row in rows:
                latest_by_update_key[str(row["update_key"])] = str(row["memory_id"])
        return by_source_event, latest_by_update_key

    def append_memory(
        self,
        connection: sqlite3.Connection,
        memory: EpisodicMemory,
    ) -> None:
        connection.execute(
            """
            INSERT INTO episodic_memories (
                memory_id, campaign_id, source_event_id, schema_version,
                memory_type, event_type, summary, importance, world_time,
                location_id, status, update_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory.memory_id,
                memory.campaign_id,
                memory.source_event_id,
                memory.schema_version,
                memory.memory_type,
                memory.event_type,
                memory.summary,
                memory.importance,
                memory.world_time,
                memory.location_id,
                memory.status,
                memory.update_key,
                _now(),
            ),
        )
        connection.executemany(
            """
            INSERT INTO memory_entities (memory_id, entity_id, role)
            VALUES (?, ?, ?)
            """,
            [
                (memory.memory_id, entity.entity_id, entity.role)
                for entity in memory.entities
            ],
        )
        connection.executemany(
            """
            INSERT INTO memory_scopes (memory_id, scope_kind, scope_id)
            VALUES (?, ?, ?)
            """,
            [
                (memory.memory_id, scope.scope_kind, scope.scope_id)
                for scope in memory.scopes
            ],
        )
        connection.execute(
            """
            INSERT INTO episodic_memory_fts (memory_id, campaign_id, summary)
            VALUES (?, ?, ?)
            """,
            (memory.memory_id, memory.campaign_id, memory.summary),
        )
        connection.execute(
            """
            INSERT INTO episodic_memory_fts_ngrams (memory_id, campaign_id, terms)
            VALUES (?, ?, ?)
            """,
            (memory.memory_id, memory.campaign_id, _fts_ngrams(memory.summary)),
        )

    def append_memory_link(
        self,
        connection: sqlite3.Connection,
        link: MemoryLink,
    ) -> None:
        connection.execute(
            """
            INSERT INTO memory_links (
                link_id, campaign_id, source_memory_id, target_memory_id,
                relation_type, source_event_id, schema_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                link.link_id,
                link.campaign_id,
                link.source_memory_id,
                link.target_memory_id,
                link.relation_type,
                link.source_event_id,
                link.schema_version,
                _now(),
            ),
        )

    def append_scene_summary(
        self,
        connection: sqlite3.Connection,
        summary: SceneMemorySummary,
    ) -> None:
        connection.execute(
            """
            INSERT INTO scene_memory_summaries (
                summary_id, campaign_id, scene_id, segment_index, location_id,
                schema_version, content, start_sequence, end_sequence,
                start_world_time, end_world_time, generator,
                generator_version, status, resolved_json, unresolved_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary.summary_id,
                summary.campaign_id,
                summary.scene_id,
                summary.segment_index,
                summary.location_id,
                summary.schema_version,
                summary.content,
                summary.start_sequence,
                summary.end_sequence,
                summary.start_world_time,
                summary.end_world_time,
                summary.generator,
                summary.generator_version,
                summary.status,
                json.dumps(summary.resolved_items, ensure_ascii=False, separators=(",", ":")),
                json.dumps(summary.unresolved_items, ensure_ascii=False, separators=(",", ":")),
                _now(),
            ),
        )
        connection.executemany(
            """
            INSERT INTO scene_summary_event_sources (summary_id, event_id, ordinal)
            VALUES (?, ?, ?)
            """,
            [
                (summary.summary_id, event_id, sequence)
                for sequence, event_id in zip(
                    summary.source_sequences,
                    summary.source_event_ids,
                    strict=True,
                )
            ],
        )
        connection.executemany(
            """
            INSERT INTO scene_summary_memory_sources (summary_id, memory_id, ordinal)
            VALUES (?, ?, ?)
            """,
            [
                (summary.summary_id, memory_id, sequence)
                for sequence, memory_id in zip(
                    summary.source_sequences,
                    summary.memory_ids,
                    strict=True,
                )
            ],
        )

    def upsert_scene_summary(
        self,
        connection: sqlite3.Connection,
        summary: SceneMemorySummary,
    ) -> None:
        existing = connection.execute(
            """
            SELECT campaign_id, scene_id, segment_index, location_id,
                   schema_version, content, start_sequence, end_sequence,
                   start_world_time, end_world_time, generator,
                   generator_version, status, resolved_json, unresolved_json
            FROM scene_memory_summaries
            WHERE summary_id = ?
            """,
            (summary.summary_id,),
        ).fetchone()
        if existing is None:
            self.append_scene_summary(connection, summary)
            return
        identity = (
            str(existing["campaign_id"]),
            str(existing["scene_id"]),
            int(existing["segment_index"]),
            _optional_str(existing["location_id"]),
            int(existing["schema_version"]),
            str(existing["generator"]),
            int(existing["generator_version"]),
        )
        expected_identity = (
            summary.campaign_id,
            summary.scene_id,
            summary.segment_index,
            summary.location_id,
            summary.schema_version,
            summary.generator,
            summary.generator_version,
        )
        if identity != expected_identity:
            raise ValueError("scene summary identity changed during incremental update")
        existing_resolved = json.loads(existing["resolved_json"])
        existing_unresolved = json.loads(existing["unresolved_json"])
        resolved = _unique_strings([*existing_resolved, *summary.resolved_items])
        unresolved = _unique_strings([*existing_unresolved, *summary.unresolved_items])
        content = " ".join(
            value for value in (str(existing["content"]), summary.content) if value
        )
        connection.execute(
            """
            UPDATE scene_memory_summaries
            SET content = ?, end_sequence = ?, end_world_time = ?,
                status = ?, resolved_json = ?, unresolved_json = ?
            WHERE summary_id = ?
            """,
            (
                content,
                max(int(existing["end_sequence"]), summary.end_sequence),
                max(int(existing["end_world_time"]), summary.end_world_time),
                (
                    "closed"
                    if existing["status"] == "closed" or summary.status == "closed"
                    else "rolling"
                ),
                json.dumps(resolved, ensure_ascii=False, separators=(",", ":")),
                json.dumps(unresolved, ensure_ascii=False, separators=(",", ":")),
                summary.summary_id,
            ),
        )
        connection.executemany(
            """
            INSERT INTO scene_summary_event_sources (summary_id, event_id, ordinal)
            VALUES (?, ?, ?)
            """,
            [
                (
                    summary.summary_id,
                    event_id,
                    sequence,
                )
                for sequence, event_id in zip(
                    summary.source_sequences,
                    summary.source_event_ids,
                    strict=True,
                )
            ],
        )
        connection.executemany(
            """
            INSERT INTO scene_summary_memory_sources (summary_id, memory_id, ordinal)
            VALUES (?, ?, ?)
            """,
            [
                (
                    summary.summary_id,
                    memory_id,
                    sequence,
                )
                for sequence, memory_id in zip(
                    summary.source_sequences,
                    summary.memory_ids,
                    strict=True,
                )
            ],
        )
    def load_memory_candidates(
        self,
        connection: sqlite3.Connection,
        query: MemoryQuery,
        candidate_limit: int = 200,
    ) -> list[EpisodicMemory]:
        rows, _, _, _ = self._load_memory_candidate_rows(
            connection, query, candidate_limit
        )
        if not rows:
            return []
        memory_ids = [str(row["memory_id"]) for row in rows]
        placeholders = ",".join("?" for _ in memory_ids)
        entity_rows = connection.execute(
            f"""
            SELECT memory_id, entity_id, role
            FROM memory_entities
            WHERE memory_id IN ({placeholders})
            ORDER BY memory_id, entity_id, role
            """,
            memory_ids,
        ).fetchall()
        scope_rows = connection.execute(
            f"""
            SELECT memory_id, scope_kind, scope_id
            FROM memory_scopes
            WHERE memory_id IN ({placeholders})
            ORDER BY memory_id, scope_kind, scope_id
            """,
            memory_ids,
        ).fetchall()
        entities: dict[str, list[MemoryEntity]] = {}
        for row in entity_rows:
            entities.setdefault(str(row["memory_id"]), []).append(MemoryEntity(
                str(row["entity_id"]),
                str(row["role"]),  # type: ignore[arg-type]
            ))
        scopes: dict[str, list[MemoryScope]] = {}
        for row in scope_rows:
            scopes.setdefault(str(row["memory_id"]), []).append(MemoryScope(
                str(row["scope_kind"]),  # type: ignore[arg-type]
                str(row["scope_id"]),
            ))
        return [
            EpisodicMemory(
                memory_id=str(row["memory_id"]),
                campaign_id=str(row["campaign_id"]),
                source_event_id=str(row["source_event_id"]),
                schema_version=int(row["schema_version"]),  # type: ignore[arg-type]
                memory_type=str(row["memory_type"]),  # type: ignore[arg-type]
                event_type=str(row["event_type"]),
                summary=str(row["summary"]),
                importance=int(row["importance"]),
                world_time=int(row["world_time"]),
                location_id=_optional_str(row["location_id"]),
                status=str(row["status"]),  # type: ignore[arg-type]
                update_key=_optional_str(row["update_key"]),
                entities=tuple(entities.get(str(row["memory_id"]), [])),
                scopes=tuple(scopes.get(str(row["memory_id"]), [])),
            )
            for row in rows
        ]

    def load_memory_candidates_with_stats(
        self,
        connection: sqlite3.Connection,
        query: MemoryQuery,
        candidate_limit: int | None = None,
    ) -> tuple[list[EpisodicMemory], int, bool, tuple[str, ...]]:
        """Load candidates and expose truncation/link-expansion facts for tracing."""
        rows, total, truncated, expanded_ids = self._load_memory_candidate_rows(
            connection,
            query,
            candidate_limit if candidate_limit is not None else query.candidate_limit,
        )
        if not rows:
            return [], total, truncated, expanded_ids
        memory_ids = [str(row["memory_id"]) for row in rows]
        return self._hydrate_memories(connection, rows), total, truncated, expanded_ids

    def _load_memory_candidate_rows(
        self,
        connection: sqlite3.Connection,
        query: MemoryQuery,
        candidate_limit: int,
    ) -> tuple[list[sqlite3.Row], int, bool, tuple[str, ...]]:
        if candidate_limit < 1 or candidate_limit > 1_000:
            raise ValueError("candidate_limit must be between 1 and 1000")
        entity_filter = ""
        entity_parameters: list[object] = []
        if query.entity_ids:
            placeholders = ",".join("?" for _ in query.entity_ids)
            entity_filter = f"""
                JOIN (
                    SELECT memory_id
                    FROM memory_entities
                    WHERE entity_id IN ({placeholders})
                    GROUP BY memory_id
                    HAVING COUNT(DISTINCT entity_id) = ?
                ) AS matched ON matched.memory_id = m.memory_id
            """
            entity_parameters = [*query.entity_ids, len(query.entity_ids)]
        base_where = f"FROM episodic_memories AS m {entity_filter} WHERE m.campaign_id = ?"
        base_parameters = [*entity_parameters, query.campaign_id]
        structured_total = int(connection.execute(
            f"SELECT COUNT(*) {base_where}", base_parameters
        ).fetchone()[0])
        fts_total = 0
        ids: list[str] = []
        if query.search_text and query.retrieval_mode in {"fts", "hybrid"}:
            fts_query = _fts_ngram_match_query(query.search_text)
            if fts_query:
                fts_total = int(connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM episodic_memory_fts_ngrams AS f
                    JOIN episodic_memories AS m ON m.memory_id = f.memory_id
                    {entity_filter}
                    WHERE f.campaign_id = ? AND episodic_memory_fts_ngrams MATCH ?
                    """.format(entity_filter=entity_filter),
                    [*entity_parameters, query.campaign_id, fts_query],
                ).fetchone()[0])
                fts_rows = connection.execute(
                    """
                    SELECT f.memory_id
                    FROM episodic_memory_fts_ngrams AS f
                    JOIN episodic_memories AS m ON m.memory_id = f.memory_id
                    {entity_filter}
                    WHERE f.campaign_id = ? AND episodic_memory_fts_ngrams MATCH ?
                    ORDER BY m.world_time DESC, m.importance DESC, m.memory_id
                    LIMIT ?
                    """.format(entity_filter=entity_filter),
                    [*entity_parameters, query.campaign_id, fts_query, candidate_limit],
                ).fetchall()
                ids.extend(str(row["memory_id"]) for row in fts_rows)
        if query.retrieval_mode in {"structured", "hybrid"}:
            structured_rows = connection.execute(
                f"""
                SELECT m.memory_id
                {base_where}
                ORDER BY m.world_time DESC, m.importance DESC, m.memory_id
                LIMIT ?
                """,
                [*base_parameters, candidate_limit],
            ).fetchall()
            ids.extend(str(row["memory_id"]) for row in structured_rows)
        ordered_ids = list(dict.fromkeys(ids))[:candidate_limit]
        expanded_ids: tuple[str, ...] = ()
        if query.expand_links and ordered_ids:
            placeholders = ",".join("?" for _ in ordered_ids)
            neighbors = connection.execute(
                f"""
                SELECT source_memory_id, target_memory_id
                FROM memory_links
                WHERE campaign_id = ?
                  AND (source_memory_id IN ({placeholders})
                       OR target_memory_id IN ({placeholders}))
                ORDER BY source_memory_id, target_memory_id
                """,
                [query.campaign_id, *ordered_ids, *ordered_ids],
            ).fetchall()
            expanded = []
            known = set(ordered_ids)
            for row in neighbors:
                for value in (str(row["source_memory_id"]), str(row["target_memory_id"])):
                    if value not in known:
                        known.add(value)
                        expanded.append(value)
            expanded_ids = tuple(expanded)
            ordered_ids.extend(expanded)
        total = fts_total if query.retrieval_mode == "fts" else structured_total
        truncated = total > candidate_limit
        if not ordered_ids:
            return [], total, truncated, expanded_ids
        placeholders = ",".join("?" for _ in ordered_ids)
        rows = connection.execute(
            f"SELECT m.* FROM episodic_memories AS m WHERE m.memory_id IN ({placeholders})",
            ordered_ids,
        ).fetchall()
        by_id = {str(row["memory_id"]): row for row in rows}
        return [by_id[value] for value in ordered_ids if value in by_id], total, truncated, expanded_ids

    def _hydrate_memories(
        self,
        connection: sqlite3.Connection,
        rows: list[sqlite3.Row],
    ) -> list[EpisodicMemory]:
        memory_ids = [str(row["memory_id"]) for row in rows]
        placeholders = ",".join("?" for _ in memory_ids)
        entity_rows = connection.execute(
            f"""
            SELECT memory_id, entity_id, role FROM memory_entities
            WHERE memory_id IN ({placeholders}) ORDER BY memory_id, entity_id, role
            """, memory_ids,
        ).fetchall()
        scope_rows = connection.execute(
            f"""
            SELECT memory_id, scope_kind, scope_id FROM memory_scopes
            WHERE memory_id IN ({placeholders}) ORDER BY memory_id, scope_kind, scope_id
            """, memory_ids,
        ).fetchall()
        entities: dict[str, list[MemoryEntity]] = {}
        for row in entity_rows:
            entities.setdefault(str(row["memory_id"]), []).append(MemoryEntity(
                str(row["entity_id"]), str(row["role"]),  # type: ignore[arg-type]
            ))
        scopes: dict[str, list[MemoryScope]] = {}
        for row in scope_rows:
            scopes.setdefault(str(row["memory_id"]), []).append(MemoryScope(
                str(row["scope_kind"]), str(row["scope_id"]),
            ))
        return [EpisodicMemory(
            memory_id=str(row["memory_id"]), campaign_id=str(row["campaign_id"]),
            source_event_id=str(row["source_event_id"]), schema_version=int(row["schema_version"]),  # type: ignore[arg-type]
            memory_type=str(row["memory_type"]), event_type=str(row["event_type"]),  # type: ignore[arg-type]
            summary=str(row["summary"]), importance=int(row["importance"]),
            world_time=int(row["world_time"]), location_id=_optional_str(row["location_id"]),
            status=str(row["status"]), update_key=_optional_str(row["update_key"]),  # type: ignore[arg-type]
            entities=tuple(entities.get(str(row["memory_id"]), [])),
            scopes=tuple(scopes.get(str(row["memory_id"]), [])),
        ) for row in rows]

    def load_turn_scene_summary_traces(
        self,
        connection: sqlite3.Connection,
        campaign_id: str,
        turn_id: str,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT DISTINCT s.summary_id, s.scene_id, s.segment_index,
                   s.location_id, s.schema_version, s.start_sequence,
                   s.end_sequence, s.start_world_time, s.end_world_time,
                   s.generator, s.generator_version, s.status,
                   s.resolved_json, s.unresolved_json,
                   (SELECT COUNT(*) FROM scene_summary_event_sources AS counts
                    WHERE counts.summary_id = s.summary_id) AS source_count
            FROM scene_memory_summaries AS s
            JOIN scene_summary_event_sources AS sources
              ON sources.summary_id = s.summary_id
            JOIN events AS e ON e.event_id = sources.event_id
            WHERE s.campaign_id = ? AND e.turn_id = ?
            ORDER BY s.segment_index, s.summary_id
            """,
            (campaign_id, turn_id),
        ).fetchall()
        return [
            {
                "summary_id": str(row["summary_id"]),
                "scene_id": str(row["scene_id"]),
                "segment_index": int(row["segment_index"]),
                "location_id": _optional_str(row["location_id"]),
                "schema_version": int(row["schema_version"]),
                "sequence_range": [
                    int(row["start_sequence"]),
                    int(row["end_sequence"]),
                ],
                "world_time_range": [
                    int(row["start_world_time"]),
                    int(row["end_world_time"]),
                ],
                "generator": str(row["generator"]),
                "generator_version": int(row["generator_version"]),
                "status": str(row["status"]),
                "resolved_count": len(json.loads(row["resolved_json"])),
                "unresolved_count": len(json.loads(row["unresolved_json"])),
                "source_count": int(row["source_count"]),
            }
            for row in rows
        ]

    def append_retrieval_trace(
        self,
        connection: sqlite3.Connection,
        trace_id: str,
        turn_id: str,
        query: MemoryQuery,
        selection: MemorySelection,
    ) -> None:
        query_payload = {
            "schema_version": 2,
            "information_need": query.information_need,
            "entity_ids": list(query.entity_ids),
            "event_types": list(query.event_types),
            "time_mode": query.time_mode,
            "time_start": query.time_start,
            "time_end": query.time_end,
            "limit": query.limit,
            "character_budget": query.character_budget,
            "search_text": query.search_text,
            "candidate_limit": query.candidate_limit,
            "expand_links": query.expand_links,
            "retrieval_mode": query.retrieval_mode,
        }
        connection.execute(
            """
            INSERT INTO retrieval_traces (
                trace_id, campaign_id, turn_id, purpose, perspective_kind,
                perspective_id, query_json, candidate_json, rejected_json,
                selected_json, used_characters, route, candidate_total,
                candidate_limit, truncated, expanded_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace_id,
                query.campaign_id,
                turn_id,
                query.purpose,
                query.perspective_kind,
                query.perspective_id,
                json.dumps(query_payload, ensure_ascii=False, separators=(",", ":")),
                json.dumps(selection.candidate_ids, ensure_ascii=False, separators=(",", ":")),
                json.dumps(
                    [
                        {"memory_id": value.memory_id, "reason": value.reason}
                        for value in selection.rejected
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                json.dumps(
                    [value.memory_id for value in selection.selected],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                selection.used_characters,
                selection.route,
                selection.candidate_total,
                selection.candidate_limit,
                int(selection.truncated),
                json.dumps(selection.expanded_ids, ensure_ascii=False, separators=(",", ":")),
                _now(),
            ),
        )

    def load_turn_retrieval_traces(
        self,
        connection: sqlite3.Connection,
        campaign_id: str,
        turn_id: str,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT trace_id, purpose, perspective_kind, perspective_id,
                   query_json, candidate_json, rejected_json, selected_json,
                   used_characters, route, candidate_total, candidate_limit,
                   truncated, expanded_json, created_at
            FROM retrieval_traces
            WHERE campaign_id = ? AND turn_id = ?
            ORDER BY created_at, trace_id
            """,
            (campaign_id, turn_id),
        ).fetchall()
        return [
            {
                "trace_id": str(row["trace_id"]),
                "purpose": str(row["purpose"]),
                "perspective": {
                    "kind": str(row["perspective_kind"]),
                    "id": str(row["perspective_id"]),
                },
                "query": json.loads(row["query_json"]),
                "candidate_ids": json.loads(row["candidate_json"]),
                "rejected": json.loads(row["rejected_json"]),
                "selected_ids": json.loads(row["selected_json"]),
                "used_characters": int(row["used_characters"]),
                "route": str(row["route"]),
                "candidate_total": int(row["candidate_total"]),
                "candidate_limit": int(row["candidate_limit"]),
                "truncated": bool(row["truncated"]),
                "expanded_ids": json.loads(row["expanded_json"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def load_events(self, connection: sqlite3.Connection, campaign_id: str) -> list[Event]:
        rows = connection.execute(
            """
            SELECT event_id, event_type, actor_id, world_time, schema_version, payload_json
            FROM events
            WHERE campaign_id = ?
            ORDER BY sequence
            """,
            (campaign_id,),
        ).fetchall()
        return [
            Event(
                event_id=row["event_id"],
                event_type=row["event_type"],
                actor_id=row["actor_id"],
                world_time=int(row["world_time"]),
                schema_version=int(row["schema_version"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def next_sequence(self, connection: sqlite3.Connection, campaign_id: str) -> int:
        row = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS maximum FROM events WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        return int(row["maximum"]) + 1

    def append_message(
        self,
        connection: sqlite3.Connection,
        message: RawMessage,
    ) -> None:
        row = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS maximum FROM messages WHERE campaign_id = ?",
            (message.campaign_id,),
        ).fetchone()
        sequence = int(row["maximum"]) + 1
        connection.execute(
            """
            INSERT INTO messages (
                message_id, campaign_id, turn_id, sequence, speaker_type,
                speaker_id, message_kind, content, world_time, authority,
                token_count, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.message_id,
                message.campaign_id,
                message.turn_id,
                sequence,
                message.speaker_type,
                message.speaker_id,
                message.message_kind,
                message.content,
                message.world_time,
                message.authority,
                message.token_count,
                _now(),
            ),
        )

    def load_messages(
        self,
        connection: sqlite3.Connection,
        campaign_id: str,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT * FROM (
                SELECT message_id, campaign_id, turn_id, sequence, speaker_type,
                       speaker_id, message_kind, content, world_time, authority,
                       token_count, recorded_at
                FROM messages
                WHERE campaign_id = ?
                ORDER BY sequence DESC
                LIMIT ?
            )
            ORDER BY sequence
            """,
            (campaign_id, limit),
        ).fetchall()
        return [_message_row(row) for row in rows]

    def load_turn_messages(
        self,
        connection: sqlite3.Connection,
        campaign_id: str,
        turn_id: str,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT message_id, campaign_id, turn_id, sequence, speaker_type,
                   speaker_id, message_kind, content, world_time, authority,
                   token_count, recorded_at
            FROM messages
            WHERE campaign_id = ? AND turn_id = ?
            ORDER BY sequence
            """,
            (campaign_id, turn_id),
        ).fetchall()
        return [_message_row(row) for row in rows]

    def link_event_sources(
        self,
        connection: sqlite3.Connection,
        event_ids: list[str],
        message_id: str,
        source_kind: str = "trigger_input",
    ) -> None:
        connection.executemany(
            """
            INSERT INTO event_sources (event_id, message_id, source_kind)
            VALUES (?, ?, ?)
            """,
            [(event_id, message_id, source_kind) for event_id in event_ids],
        )

    def event_sources(
        self,
        connection: sqlite3.Connection,
        event_id: str,
    ) -> list[dict[str, str]]:
        rows = connection.execute(
            """
            SELECT message_id, source_kind
            FROM event_sources
            WHERE event_id = ?
            ORDER BY message_id
            """,
            (event_id,),
        ).fetchall()
        return [
            {"message_id": str(row["message_id"]), "source_kind": str(row["source_kind"])}
            for row in rows
        ]

    def load_turn_events(
        self,
        connection: sqlite3.Connection,
        campaign_id: str,
        turn_id: str,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT event_id, sequence, event_type, schema_version, world_time,
                   actor_id, causation_id, correlation_id, payload_json, recorded_at
            FROM events
            WHERE campaign_id = ? AND turn_id = ?
            ORDER BY sequence
            """,
            (campaign_id, turn_id),
        ).fetchall()
        return [
            {
                "event_id": str(row["event_id"]),
                "sequence": int(row["sequence"]),
                "event_type": str(row["event_type"]),
                "schema_version": int(row["schema_version"]),
                "world_time": int(row["world_time"]),
                "actor_id": str(row["actor_id"]),
                "causation_id": str(row["causation_id"]),
                "correlation_id": str(row["correlation_id"]),
                "payload": json.loads(row["payload_json"]),
                "recorded_at": str(row["recorded_at"]),
                "sources": self.event_sources(connection, str(row["event_id"])),
            }
            for row in rows
        ]

    def append_intent_attempt(
        self,
        connection: sqlite3.Connection,
        attempt_id: str,
        campaign_id: str,
        turn_id: str,
        command_id: str,
        status: str,
        provider_name: str | None,
        model_name: str | None,
        request_payload: dict[str, Any] | None,
        response_payload: dict[str, Any] | None,
        failure_code: str | None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        latency_ms: int | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO intent_attempts (
                attempt_id, campaign_id, turn_id, command_id, status,
                provider_name, model_name, request_json, response_json, failure_code,
                prompt_tokens, completion_tokens, total_tokens, latency_ms, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                campaign_id,
                turn_id,
                command_id,
                status,
                provider_name,
                model_name,
                (
                    json.dumps(
                        request_payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    if request_payload is not None
                    else None
                ),
                (
                    json.dumps(
                        response_payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    if response_payload is not None
                    else None
                ),
                failure_code,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                latency_ms,
                _now(),
            ),
        )

    def load_turn_intent_attempts(
        self,
        connection: sqlite3.Connection,
        campaign_id: str,
        turn_id: str,
        include_payloads: bool = False,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT attempt_id, campaign_id, turn_id, command_id, status,
                   provider_name, model_name, request_json, response_json, failure_code,
                   prompt_tokens, completion_tokens, total_tokens, latency_ms, created_at
            FROM intent_attempts
            WHERE campaign_id = ? AND turn_id = ?
            ORDER BY created_at, attempt_id
            """,
            (campaign_id, turn_id),
        ).fetchall()
        return [
            {
                "attempt_id": str(row["attempt_id"]),
                "campaign_id": str(row["campaign_id"]),
                "turn_id": str(row["turn_id"]),
                "command_id": str(row["command_id"]),
                "status": str(row["status"]),
                "provider_name": (
                    str(row["provider_name"])
                    if row["provider_name"] is not None
                    else None
                ),
                "model_name": (
                    str(row["model_name"])
                    if row["model_name"] is not None
                    else None
                ),
                "request": (
                    json.loads(row["request_json"])
                    if include_payloads and row["request_json"] is not None
                    else None
                ),
                "response": (
                    json.loads(row["response_json"])
                    if include_payloads and row["response_json"] is not None
                    else None
                ),
                "failure_code": (
                    str(row["failure_code"])
                    if row["failure_code"] is not None
                    else None
                ),
                "prompt_tokens": (
                    int(row["prompt_tokens"])
                    if row["prompt_tokens"] is not None
                    else None
                ),
                "completion_tokens": (
                    int(row["completion_tokens"])
                    if row["completion_tokens"] is not None
                    else None
                ),
                "total_tokens": (
                    int(row["total_tokens"])
                    if row["total_tokens"] is not None
                    else None
                ),
                "latency_ms": (
                    int(row["latency_ms"])
                    if row["latency_ms"] is not None
                    else None
                ),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def append_narration_attempt(
        self,
        connection: sqlite3.Connection,
        attempt_id: str,
        campaign_id: str,
        turn_id: str,
        command_id: str,
        status: str,
        provider_name: str | None,
        model_name: str | None,
        request_payload: dict[str, Any] | None,
        response_payload: dict[str, Any] | None,
        failure_code: str | None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        latency_ms: int | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO narration_attempts (
                attempt_id, campaign_id, turn_id, command_id, status,
                provider_name, model_name, request_json, response_json, failure_code,
                prompt_tokens, completion_tokens, total_tokens, latency_ms, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                campaign_id,
                turn_id,
                command_id,
                status,
                provider_name,
                model_name,
                _json_or_none(request_payload),
                _json_or_none(response_payload),
                failure_code,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                latency_ms,
                _now(),
            ),
        )

    def load_turn_narration_attempts(
        self,
        connection: sqlite3.Connection,
        campaign_id: str,
        turn_id: str,
        include_payloads: bool = False,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT attempt_id, campaign_id, turn_id, command_id, status,
                   provider_name, model_name, request_json, response_json, failure_code,
                   prompt_tokens, completion_tokens, total_tokens, latency_ms, created_at
            FROM narration_attempts
            WHERE campaign_id = ? AND turn_id = ?
            ORDER BY created_at, attempt_id
            """,
            (campaign_id, turn_id),
        ).fetchall()
        return [
            {
                "attempt_id": str(row["attempt_id"]),
                "campaign_id": str(row["campaign_id"]),
                "turn_id": str(row["turn_id"]),
                "command_id": str(row["command_id"]),
                "status": str(row["status"]),
                "provider_name": _optional_str(row["provider_name"]),
                "model_name": _optional_str(row["model_name"]),
                "request": (
                    json.loads(row["request_json"])
                    if include_payloads and row["request_json"] is not None
                    else None
                ),
                "response": (
                    json.loads(row["response_json"])
                    if include_payloads and row["response_json"] is not None
                    else None
                ),
                "failure_code": _optional_str(row["failure_code"]),
                "prompt_tokens": _optional_int(row["prompt_tokens"]),
                "completion_tokens": _optional_int(row["completion_tokens"]),
                "total_tokens": _optional_int(row["total_tokens"]),
                "latency_ms": _optional_int(row["latency_ms"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def append_npc_decision_attempt(
        self,
        connection: sqlite3.Connection,
        attempt_id: str,
        campaign_id: str,
        turn_id: str,
        command_id: str,
        status: str,
        provider_name: str | None,
        model_name: str | None,
        request_payload: dict[str, Any] | None,
        response_payload: dict[str, Any] | None,
        failure_code: str | None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        latency_ms: int | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO npc_decision_attempts (
                attempt_id, campaign_id, turn_id, command_id, status,
                provider_name, model_name, request_json, response_json, failure_code,
                prompt_tokens, completion_tokens, total_tokens, latency_ms, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                campaign_id,
                turn_id,
                command_id,
                status,
                provider_name,
                model_name,
                _json_or_none(request_payload),
                _json_or_none(response_payload),
                failure_code,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                latency_ms,
                _now(),
            ),
        )

    def load_turn_npc_decision_attempts(
        self,
        connection: sqlite3.Connection,
        campaign_id: str,
        turn_id: str,
        include_payloads: bool = False,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT attempt_id, campaign_id, turn_id, command_id, status,
                   provider_name, model_name, request_json, response_json, failure_code,
                   prompt_tokens, completion_tokens, total_tokens, latency_ms, created_at
            FROM npc_decision_attempts
            WHERE campaign_id = ? AND turn_id = ?
            ORDER BY created_at, attempt_id
            """,
            (campaign_id, turn_id),
        ).fetchall()
        return [
            {
                "attempt_id": str(row["attempt_id"]),
                "campaign_id": str(row["campaign_id"]),
                "turn_id": str(row["turn_id"]),
                "command_id": str(row["command_id"]),
                "status": str(row["status"]),
                "provider_name": _optional_str(row["provider_name"]),
                "model_name": _optional_str(row["model_name"]),
                "request": (
                    json.loads(row["request_json"])
                    if include_payloads and row["request_json"] is not None
                    else None
                ),
                "response": (
                    json.loads(row["response_json"])
                    if include_payloads and row["response_json"] is not None
                    else None
                ),
                "failure_code": _optional_str(row["failure_code"]),
                "prompt_tokens": _optional_int(row["prompt_tokens"]),
                "completion_tokens": _optional_int(row["completion_tokens"]),
                "total_tokens": _optional_int(row["total_tokens"]),
                "latency_ms": _optional_int(row["latency_ms"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def append_routine_attempt(
        self,
        connection: sqlite3.Connection,
        attempt_id: str,
        campaign_id: str,
        turn_id: str,
        command_id: str,
        status: str,
        provider_name: str | None,
        model_name: str | None,
        request_payload: dict[str, Any] | None,
        response_payload: dict[str, Any] | None,
        rejected: list[dict[str, str]] | None,
        failure_code: str | None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        latency_ms: int | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO routine_attempts (
                attempt_id, campaign_id, turn_id, command_id, status,
                provider_name, model_name, request_json, response_json, rejected_json,
                failure_code, prompt_tokens, completion_tokens, total_tokens, latency_ms, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                campaign_id,
                turn_id,
                command_id,
                status,
                provider_name,
                model_name,
                _json_or_none(request_payload),
                _json_or_none(response_payload),
                _json_or_none(rejected),
                failure_code,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                latency_ms,
                _now(),
            ),
        )

    def load_turn_routine_attempts(
        self,
        connection: sqlite3.Connection,
        campaign_id: str,
        turn_id: str,
        include_payloads: bool = False,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT attempt_id, campaign_id, turn_id, command_id, status,
                   provider_name, model_name, request_json, response_json, rejected_json,
                   failure_code, prompt_tokens, completion_tokens, total_tokens, latency_ms, created_at
            FROM routine_attempts
            WHERE campaign_id = ? AND turn_id = ?
            ORDER BY created_at, attempt_id
            """,
            (campaign_id, turn_id),
        ).fetchall()
        return [
            {
                "attempt_id": str(row["attempt_id"]),
                "campaign_id": str(row["campaign_id"]),
                "turn_id": str(row["turn_id"]),
                "command_id": str(row["command_id"]),
                "status": str(row["status"]),
                "provider_name": _optional_str(row["provider_name"]),
                "model_name": _optional_str(row["model_name"]),
                "request": json.loads(row["request_json"]) if include_payloads and row["request_json"] is not None else None,
                "response": json.loads(row["response_json"]) if include_payloads and row["response_json"] is not None else None,
                "rejected": json.loads(row["rejected_json"]) if row["rejected_json"] is not None else [],
                "failure_code": _optional_str(row["failure_code"]),
                "prompt_tokens": _optional_int(row["prompt_tokens"]),
                "completion_tokens": _optional_int(row["completion_tokens"]),
                "total_tokens": _optional_int(row["total_tokens"]),
                "latency_ms": _optional_int(row["latency_ms"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def load_command_by_turn(
        self,
        connection: sqlite3.Connection,
        campaign_id: str,
        turn_id: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            """
            SELECT command_id, expected_state_version, response_json, status,
                   source_message_id, narrator_message_id
            FROM commands
            WHERE campaign_id = ? AND turn_id = ?
            """,
            (campaign_id, turn_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "command_id": str(row["command_id"]),
            "expected_state_version": int(row["expected_state_version"]),
            "response": json.loads(row["response_json"]),
            "status": str(row["status"]),
            "source_message_id": str(row["source_message_id"]),
            "narrator_message_id": str(row["narrator_message_id"]),
        }

    def append_events(
        self,
        connection: sqlite3.Connection,
        campaign_id: str,
        turn_id: str,
        command_id: str,
        events: list[Event],
    ) -> None:
        sequence = self.next_sequence(connection, campaign_id)
        recorded_at = _now()
        for event in events:
            connection.execute(
                """
                INSERT INTO events (
                    event_id, campaign_id, sequence, turn_id, event_type,
                    schema_version, world_time, actor_id, causation_id,
                    correlation_id, payload_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    campaign_id,
                    sequence,
                    turn_id,
                    event.event_type,
                    event.schema_version,
                    event.world_time,
                    event.actor_id,
                    command_id,
                    turn_id,
                    json.dumps(event.payload, ensure_ascii=False, separators=(",", ":")),
                    recorded_at,
                ),
            )
            sequence += 1

    def find_command_response(
        self,
        connection: sqlite3.Connection,
        campaign_id: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            """
            SELECT response_json FROM commands
            WHERE campaign_id = ? AND idempotency_key = ?
            """,
            (campaign_id, idempotency_key),
        ).fetchone()
        return json.loads(row["response_json"]) if row else None

    def save_command(
        self,
        connection: sqlite3.Connection,
        command_id: str,
        campaign_id: str,
        idempotency_key: str,
        expected_state_version: int,
        request: dict[str, Any],
        response: dict[str, Any],
        status: str,
        turn_id: str | None = None,
        source_message_id: str | None = None,
        narrator_message_id: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO commands (
                command_id, campaign_id, idempotency_key, expected_state_version,
                request_json, response_json, status, turn_id, source_message_id,
                narrator_message_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                command_id,
                campaign_id,
                idempotency_key,
                expected_state_version,
                json.dumps(request, ensure_ascii=False, separators=(",", ":")),
                json.dumps(response, ensure_ascii=False, separators=(",", ":")),
                status,
                turn_id,
                source_message_id,
                narrator_message_id,
                _now(),
            ),
        )

    def update_campaign_version(
        self,
        connection: sqlite3.Connection,
        campaign_id: str,
        expected_version: int,
    ) -> int:
        new_version = expected_version + 1
        cursor = connection.execute(
            """
            UPDATE campaigns SET state_version = ?
            WHERE campaign_id = ? AND state_version = ?
            """,
            (new_version, campaign_id, expected_version),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("campaign version changed during commit")
        return new_version

    def reset_campaign(
        self,
        campaign_id: str,
        name: str,
        events: list[Event],
        state_version: int = 1,
        scenario_id: str | None = None,
        scenario_version: str | None = None,
        scenario_content_hash: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM campaigns WHERE campaign_id = ?", (campaign_id,))
            connection.execute(
                """
                INSERT INTO campaigns (
                    campaign_id, name, state_version, scenario_id,
                    scenario_version, scenario_content_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    campaign_id,
                    name,
                    state_version,
                    scenario_id,
                    scenario_version,
                    scenario_content_hash,
                    _now(),
                ),
            )
            self.append_events(
                connection,
                campaign_id,
                turn_id="turn_bootstrap",
                command_id="cmd_bootstrap",
                events=events,
            )

    def campaign_exists(self, campaign_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            return row is not None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json_or_none(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None


def _unique_strings(values: list[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _message_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "message_id": str(row["message_id"]),
        "campaign_id": str(row["campaign_id"]),
        "turn_id": str(row["turn_id"]),
        "sequence": int(row["sequence"]),
        "speaker_type": str(row["speaker_type"]),
        "speaker_id": str(row["speaker_id"]),
        "message_kind": str(row["message_kind"]),
        "content": str(row["content"]),
        "world_time": int(row["world_time"]),
        "authority": str(row["authority"]),
        "token_count": int(row["token_count"]) if row["token_count"] is not None else None,
        "recorded_at": str(row["recorded_at"]),
    }


def _fts_ngrams(text: str) -> str:
    normalized = "".join(text.strip().split())
    if not normalized:
        return ""
    tokens: list[str] = []
    for index in range(len(normalized) - 1):
        tokens.append(normalized[index:index + 2])
    if not tokens:
        tokens.append(normalized)
    return " ".join(tokens)


def _fts_ngram_match_query(text: str) -> str:
    tokens = _fts_ngrams(text).split()
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def database_backend_from_environment(database_path: Path) -> EventStoreBackend:
    """Choose the configured backend without silently falling back from Postgres."""
    url = os.getenv("TRPG_DATABASE_URL", "").strip()
    if not url:
        return EventStore(database_path)
    if not url.startswith(("postgres://", "postgresql://")):
        raise ValueError("TRPG_DATABASE_URL must be a PostgreSQL URL")
    try:
        from trpg_server.core.postgres_store import PostgresEventStore
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "PostgreSQL is configured but psycopg is not installed; install trpg-server[postgres]"
        ) from error
    return PostgresEventStore(url)
