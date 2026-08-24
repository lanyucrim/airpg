from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID
from trpg_server.core.state import RawMessage
from trpg_server.core.service import GameService


def test_raw_message_preserves_exact_text_and_authority(tmp_path: Path) -> None:
    game = GameService(tmp_path / "messages.sqlite3")
    game.initialize()
    message = RawMessage(
        message_id="msg_player_001",
        campaign_id=GRAY_HARBOR_CAMPAIGN_ID,
        turn_id="turn_message_001",
        speaker_type="player",
        speaker_id="protagonist",
        message_kind="player_input",
        content="我已经拿走了最后通牒——这是玩家说过的话，不是世界事实。",
        world_time=0,
        authority="utterance_only",
    )

    with game.store.connect() as connection:
        game.store.append_message(connection, message)

    with game.store.connect() as connection:
        stored = game.store.load_messages(connection, GRAY_HARBOR_CAMPAIGN_ID)

    assert stored[-1]["content"] == message.content
    assert stored[-1]["authority"] == "utterance_only"
    assert stored[-1]["message_kind"] == "player_input"


def test_confirmed_event_can_link_to_source_message(tmp_path: Path) -> None:
    game = GameService(tmp_path / "sources.sqlite3")
    game.initialize()
    message = RawMessage(
        message_id="msg_player_002",
        campaign_id=GRAY_HARBOR_CAMPAIGN_ID,
        turn_id="turn_message_002",
        speaker_type="player",
        speaker_id="protagonist",
        message_kind="player_input",
        content="我查看桌上的最后通牒。",
        world_time=0,
        authority="utterance_only",
    )

    with game.store.connect() as connection:
        game.store.append_message(connection, message)
        event_id = connection.execute(
            "SELECT event_id FROM events WHERE campaign_id = ? ORDER BY sequence LIMIT 1",
            (GRAY_HARBOR_CAMPAIGN_ID,),
        ).fetchone()["event_id"]
        game.store.link_event_sources(connection, [event_id], message.message_id)

    with game.store.connect() as connection:
        sources = game.store.event_sources(connection, event_id)

    assert sources == [
        {"message_id": "msg_player_002", "source_kind": "trigger_input"}
    ]


def test_initialize_adds_trace_columns_to_legacy_database(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE commands (
                command_id TEXT PRIMARY KEY,
                campaign_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                expected_state_version INTEGER NOT NULL,
                request_json TEXT NOT NULL,
                response_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(campaign_id, idempotency_key)
            )
            """
        )

    game = GameService(database)
    game.initialize()

    with game.store.connect() as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(commands)").fetchall()
        }

    assert {"turn_id", "source_message_id", "narrator_message_id"} <= columns


def test_initialize_migrates_stage_5a_memory_tables(tmp_path: Path) -> None:
    database = tmp_path / "stage-5a.sqlite3"
    game = GameService(database)
    game.store.initialize()
    with game.store.connect() as connection:
        connection.execute("DROP INDEX idx_memories_campaign_update_key")
        connection.execute("ALTER TABLE episodic_memories DROP COLUMN update_key")
        connection.execute("ALTER TABLE scene_memory_summaries DROP COLUMN resolved_json")
        connection.execute("ALTER TABLE scene_memory_summaries DROP COLUMN unresolved_json")

    game.store.initialize()

    with game.store.connect() as connection:
        memory_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(episodic_memories)"
            ).fetchall()
        }
        summary_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(scene_memory_summaries)"
            ).fetchall()
        }
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            ).fetchall()
        }

    assert "update_key" in memory_columns
    assert {"resolved_json", "unresolved_json"} <= summary_columns
    assert {
        "memory_links",
        "scene_memory_summaries",
        "scene_summary_event_sources",
        "scene_summary_memory_sources",
    } <= tables


@pytest.mark.parametrize("retired_scenario_id", ["gray-harbor", "guard-wine-demo"])
def test_initialize_removes_only_the_retired_demo_campaign(
    tmp_path: Path,
    retired_scenario_id: str,
) -> None:
    database = tmp_path / "retired-demo.sqlite3"
    game = GameService(database)
    game.store.initialize()
    with game.store.connect() as connection:
        connection.execute(
            """
            INSERT INTO campaigns (
                campaign_id, name, state_version, scenario_id,
                scenario_version, scenario_content_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "cmp_guard_wine",
                "已退役演示",
                1,
                retired_scenario_id,
                "0.1.0",
                "retired",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO campaigns (
                campaign_id, name, state_version, scenario_id,
                scenario_version, scenario_content_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "cmp_keep",
                "保留存档",
                1,
                "another-scenario",
                "1.0.0",
                "keep",
                "2026-01-01T00:00:00+00:00",
            ),
        )

    game.store.initialize()

    with game.store.connect() as connection:
        campaigns = {
            row["campaign_id"]
            for row in connection.execute("SELECT campaign_id FROM campaigns")
        }
    assert "cmp_guard_wine" not in campaigns
    assert "cmp_keep" in campaigns
