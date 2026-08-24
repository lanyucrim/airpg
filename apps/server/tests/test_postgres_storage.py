from __future__ import annotations

import importlib.util
from pathlib import Path

from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID
from trpg_server.core.postgres_schema import POSTGRES_SCHEMA_VERSION, postgres_schema
from trpg_server.core.service import GameService


MIGRATION_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_sqlite_to_postgres.py"


def _migration_module():
    spec = importlib.util.spec_from_file_location("sqlite_pg_migration", MIGRATION_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_postgres_schema_has_native_chinese_fts_and_optional_vector_boundary() -> None:
    schema = postgres_schema()
    assert POSTGRES_SCHEMA_VERSION == 1
    assert "CREATE VIRTUAL TABLE" not in schema
    assert "TSVECTOR GENERATED ALWAYS" in schema
    assert "idx_pg_memory_ngram_fts" in schema
    assert "CREATE EXTENSION IF NOT EXISTS vector" in schema
    assert "PRAGMA" not in schema


def test_sqlite_migration_fingerprint_covers_projection_sources_and_read_models(
    tmp_path: Path,
) -> None:
    database = tmp_path / "migration-source.sqlite3"
    game = GameService(database)
    game.initialize()
    module = _migration_module()

    first = module.sqlite_fingerprint(database, GRAY_HARBOR_CAMPAIGN_ID)
    second = module.sqlite_fingerprint(database, GRAY_HARBOR_CAMPAIGN_ID)

    assert first == second
    assert first["event_count"] > 0
    assert len(first["event_ids"]) == first["event_count"]
    assert len(first["event_digest"]) == 64
    assert len(first["source_digest"]) == 64
    assert len(first["projection_digest"]) == 64
    assert len(first["memory_digest"]) == 64
    assert set(first["table_counts"]) >= {
        "events", "messages", "event_sources", "episodic_memories",
        "memory_links", "scene_memory_summaries", "retrieval_traces",
    }
