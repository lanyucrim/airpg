from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Sequence

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError as error:  # pragma: no cover - optional runtime
    raise ModuleNotFoundError("psycopg is required for PostgreSQL storage") from error

from trpg_server.memory import MemoryQuery
from trpg_server.core.postgres_schema import postgres_schema
from trpg_server.core.store import EventStore, StoreIntegrityError, _fts_ngrams


class _HybridRow(dict[str, Any]):
    def __getitem__(self, key: object) -> Any:
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)  # type: ignore[arg-type]


class _Cursor:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor

    def fetchone(self) -> _HybridRow | None:
        value = self.cursor.fetchone()
        return _HybridRow(value) if value is not None else None

    def fetchall(self) -> list[_HybridRow]:
        return [_HybridRow(value) for value in self.cursor.fetchall()]

    @property
    def rowcount(self) -> int:
        return int(self.cursor.rowcount)


class _Connection:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def execute(self, statement: str, parameters: Sequence[Any] = ()) -> _Cursor:
        normalized = statement.strip()
        if normalized.upper() == "BEGIN IMMEDIATE":
            normalized = "BEGIN"
        try:
            cursor = self.connection.execute(
                normalized.replace("?", "%s"), tuple(parameters)
            )
        except psycopg.IntegrityError as error:
            raise StoreIntegrityError(str(error)) from error
        return _Cursor(cursor)

    def executemany(self, statement: str, parameters: Sequence[Sequence[Any]]) -> None:
        try:
            self.connection.executemany(statement.replace("?", "%s"), parameters)
        except psycopg.IntegrityError as error:
            raise StoreIntegrityError(str(error)) from error

    def executescript(self, statement: str) -> None:
        self.connection.execute(statement)


class PostgresEventStore(EventStore):
    """PostgreSQL implementation reusing database-neutral EventStore methods."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @contextmanager
    def connect(self) -> Iterator[_Connection]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as raw:
            connection = _Connection(raw)
            try:
                yield connection
                raw.commit()
            except Exception:
                raw.rollback()
                raise

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(postgres_schema())

    @staticmethod
    def _migrate(connection: Any) -> None:
        del connection

    def _load_memory_candidate_rows(
        self, connection: _Connection, query: MemoryQuery, candidate_limit: int,
    ) -> tuple[list[_HybridRow], int, bool, tuple[str, ...]]:
        if not 1 <= candidate_limit <= 1_000:
            raise ValueError("candidate_limit must be between 1 and 1000")
        where = ["m.campaign_id = ?"]
        params: list[Any] = [query.campaign_id]
        for entity_id in query.entity_ids:
            where.append(
                "EXISTS (SELECT 1 FROM memory_entities e "
                "WHERE e.memory_id=m.memory_id AND e.entity_id=?)"
            )
            params.append(entity_id)
        predicate = " AND ".join(where)
        total_row = connection.execute(
            f"SELECT COUNT(*) AS total FROM episodic_memories m WHERE {predicate}", params
        ).fetchone()
        structured_total = int(total_row["total"] if total_row else 0)
        fts_total = 0
        ordered_ids: list[str] = []
        search_terms = _fts_ngrams(query.search_text or "")
        if search_terms and query.retrieval_mode in {"fts", "hybrid"}:
            fts_predicate = (
                f"{predicate} AND n.search_document @@ plainto_tsquery('simple', ?)"
            )
            fts_params = [*params, search_terms]
            fts_count = connection.execute(
                "SELECT COUNT(*) AS total FROM episodic_memories m "
                "JOIN episodic_memory_fts_ngrams n ON n.memory_id=m.memory_id "
                f"WHERE {fts_predicate}",
                fts_params,
            ).fetchone()
            fts_total = int(fts_count["total"] if fts_count else 0)
            fts_rows = connection.execute(
                "SELECT m.memory_id FROM episodic_memories m "
                "JOIN episodic_memory_fts_ngrams n ON n.memory_id=m.memory_id "
                f"WHERE {fts_predicate} "
                "ORDER BY m.world_time DESC, m.importance DESC, m.memory_id LIMIT ?",
                [*fts_params, candidate_limit],
            ).fetchall()
            ordered_ids.extend(str(row["memory_id"]) for row in fts_rows)
        if query.retrieval_mode in {"structured", "hybrid"}:
            structured_rows = connection.execute(
                f"SELECT m.memory_id FROM episodic_memories m WHERE {predicate} "
                "ORDER BY m.world_time DESC, m.importance DESC, m.memory_id LIMIT ?",
                [*params, candidate_limit],
            ).fetchall()
            ordered_ids.extend(str(row["memory_id"]) for row in structured_rows)
        ordered_ids = list(dict.fromkeys(ordered_ids))[:candidate_limit]
        expanded_ids: tuple[str, ...] = ()
        if query.expand_links and ordered_ids:
            placeholders = ",".join("?" for _ in ordered_ids)
            linked = connection.execute(
                f"SELECT source_memory_id,target_memory_id FROM memory_links WHERE campaign_id=? AND (source_memory_id IN ({placeholders}) OR target_memory_id IN ({placeholders}))",
                [query.campaign_id, *ordered_ids, *ordered_ids],
            ).fetchall()
            expanded: list[str] = []
            known = set(ordered_ids)
            for link in linked:
                for memory_id in (str(link["source_memory_id"]), str(link["target_memory_id"])):
                    if memory_id not in known:
                        known.add(memory_id)
                        expanded.append(memory_id)
            if expanded:
                ordered_ids.extend(expanded)
                expanded_ids = tuple(expanded)
        if not ordered_ids:
            total = fts_total if query.retrieval_mode == "fts" else structured_total
            return [], total, total > candidate_limit, expanded_ids
        placeholders = ",".join("?" for _ in ordered_ids)
        rows = connection.execute(
            f"SELECT m.* FROM episodic_memories m WHERE m.memory_id IN ({placeholders})",
            ordered_ids,
        ).fetchall()
        by_id = {str(row["memory_id"]): row for row in rows}
        total = fts_total if query.retrieval_mode == "fts" else structured_total
        return (
            [by_id[memory_id] for memory_id in ordered_ids if memory_id in by_id],
            total,
            total > candidate_limit,
            expanded_ids,
        )
