from __future__ import annotations

import re

from trpg_server.core.store import SCHEMA


POSTGRES_SCHEMA_VERSION = 1


def postgres_schema() -> str:
    schema = re.sub(r"PRAGMA[^;]+;", "", SCHEMA)
    schema = re.sub(
        r"CREATE VIRTUAL TABLE IF NOT EXISTS episodic_memory_fts USING fts5\(.*?\);",
        "CREATE TABLE IF NOT EXISTS episodic_memory_fts (memory_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, summary TEXT NOT NULL);",
        schema,
        flags=re.S,
    )
    schema = re.sub(
        r"CREATE VIRTUAL TABLE IF NOT EXISTS episodic_memory_fts_ngrams USING fts5\(.*?\);",
        """CREATE TABLE IF NOT EXISTS episodic_memory_fts_ngrams (
            memory_id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            terms TEXT NOT NULL,
            search_document TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', terms)) STORED
        );
        CREATE INDEX IF NOT EXISTS idx_pg_memory_ngram_fts
        ON episodic_memory_fts_ngrams USING GIN(search_document);""",
        schema,
        flags=re.S,
    )
    schema += """
    CREATE TABLE IF NOT EXISTS trpg_schema_metadata (
        component TEXT PRIMARY KEY,
        schema_version INTEGER NOT NULL,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    INSERT INTO trpg_schema_metadata(component, schema_version)
    VALUES ('event_store', 1)
    ON CONFLICT(component) DO UPDATE SET schema_version=EXCLUDED.schema_version,
        applied_at=CURRENT_TIMESTAMP;
    DO $$ BEGIN
      CREATE EXTENSION IF NOT EXISTS vector;
      ALTER TABLE episodic_memories ADD COLUMN IF NOT EXISTS embedding VECTOR(1024);
    EXCEPTION WHEN insufficient_privilege OR undefined_file THEN
      RAISE NOTICE 'pgvector unavailable; structured and FTS retrieval remain enabled';
    END $$;
    """
    return schema
