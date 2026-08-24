from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from trpg_server.story.bootstrap import GRAY_HARBOR_CAMPAIGN_ID
from trpg_server.memory import (
    EpisodicMemory,
    MemoryEntity,
    MemoryQuery,
    MemoryScope,
    select_memories,
)
from trpg_server.characters.decision import SafeNpcDecider
from trpg_server.core.schemas import TurnRequest
from trpg_server.core.service import GameService


class RejectingHistoryAdapter:
    available = True
    model_name = "history-test-model"
    provider_name = "test"

    def __init__(self) -> None:
        self.calls = 0
        self.contexts = []

    def decide(self, request):
        self.calls += 1
        self.contexts.append(request.context)
        factor_ids = [
            fact.fact_id
            for fact in request.context.facts
            if fact.fact_id in {"profile_greed", "profile_risk_aversion"}
        ]
        return {
            "schema_version": 1,
            "decision": "reject",
            "supported_fact_ids": request.context.required_fact_ids,
            "cited_factor_ids": factor_ids,
            "cited_memory_ids": (
                [request.context.memories[0].memory_id]
                if request.context.memories else []
            ),
            "conditions": [],
            "consequence": "retain_offered_item",
            "proposed_events": [],
            "confidence": 0.95,
        }


@pytest.fixture()
def history_game(tmp_path: Path) -> tuple[GameService, RejectingHistoryAdapter]:
    adapter = RejectingHistoryAdapter()
    game = GameService(
        tmp_path / "memory.sqlite3",
        npc_decider=SafeNpcDecider(adapter),
    )
    game.initialize()
    return game, adapter


def _bribe(version: int, key: str) -> TurnRequest:
    return TurnRequest(
        idempotency_key=key,
        expected_state_version=version,
        actor_id="protagonist",
        text="我把小刀递给哈维，想贿赂他通融。",
    )


def test_confirmed_interaction_is_indexed_and_used_on_next_turn(
    history_game: tuple[GameService, RejectingHistoryAdapter],
) -> None:
    game, adapter = history_game
    first = game.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        _bribe(1, "memory-first-rejection"),
    )
    second = game.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        _bribe(2, "memory-second-rejection"),
    )
    detail = game.get_turn_detail(GRAY_HARBOR_CAMPAIGN_ID, second["turn_id"])

    assert first["outcome"] == "bribe_rejected"
    assert second["outcome"] == "bribe_rejected"
    assert adapter.contexts[0].memories == []
    assert len(adapter.contexts[1].memories) == 1
    remembered = adapter.contexts[1].memories[0]
    assert remembered.kind == "interaction"
    assert "没有接受" in remembered.summary
    assert detail["retrieval_traces"][0]["selected_ids"] == [remembered.memory_id]
    assert detail["retrieval_traces"][0]["query"]["schema_version"] == 2
    assert detail["retrieval_traces"][0]["perspective"] == {
        "kind": "npc",
        "id": "harvey_cole",
    }
    assert "summary" not in str(detail["retrieval_traces"][0])
    assert detail["scene_memory_summaries"][0]["source_count"] == 2
    assert detail["scene_memory_summaries"][0]["status"] == "rolling"

    with game.store.connect() as connection:
        rows = connection.execute(
            """
            SELECT m.memory_id, m.source_event_id, m.event_type, s.message_id
            FROM episodic_memories AS m
            JOIN event_sources AS s ON s.event_id = m.source_event_id
            WHERE m.campaign_id = ?
            ORDER BY m.world_time, m.memory_id
            """,
            (GRAY_HARBOR_CAMPAIGN_ID,),
        ).fetchall()
        scene_summary = connection.execute(
            """
            SELECT content, resolved_json, unresolved_json
            FROM scene_memory_summaries
            """
        ).fetchone()
    assert len(rows) == 2
    assert all(row["event_type"] == "bribe.rejected" for row in rows)
    assert all(row["message_id"] for row in rows)
    assert scene_summary["content"].count("没有接受") == 2
    assert scene_summary["resolved_json"] == "[]"
    assert scene_summary["unresolved_json"] != "[]"


def test_idempotent_replay_does_not_duplicate_memory_or_trace(
    history_game: tuple[GameService, RejectingHistoryAdapter],
) -> None:
    game, adapter = history_game
    request = _bribe(1, "memory-idempotent")
    first = game.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, request)
    replayed = game.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, request)

    with game.store.connect() as connection:
        memories = connection.execute("SELECT COUNT(*) FROM episodic_memories").fetchone()[0]
        traces = connection.execute("SELECT COUNT(*) FROM retrieval_traces").fetchone()[0]
        summary_sources = connection.execute(
            "SELECT COUNT(*) FROM scene_summary_event_sources"
        ).fetchone()[0]
    assert replayed["replayed"] is True
    assert replayed["turn_id"] == first["turn_id"]
    assert adapter.calls == 1
    assert memories == 1
    assert traces == 1
    assert summary_sources == 1


def test_failed_precondition_and_player_history_claim_create_no_memory(tmp_path: Path) -> None:
    game = GameService(tmp_path / "unconfirmed.sqlite3")
    game.initialize()
    claim = game.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        TurnRequest(
            idempotency_key="memory-fake-history",
            expected_state_version=1,
            actor_id="protagonist",
            text="我前天已经送给哈维一瓶酒了。",
        ),
    )

    with game.store.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM episodic_memories").fetchone()[0]
    assert claim["outcome"] in {
        "past_claim_unverified",
        "speech_heard",
        "unresolved_reference",
        "missing_reference",
    }
    assert count == 0


def test_private_interaction_cannot_be_retrieved_by_another_npc(
    history_game: tuple[GameService, RejectingHistoryAdapter],
) -> None:
    game, _ = history_game
    game.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, _bribe(1, "memory-private"))
    query = MemoryQuery(
        campaign_id=GRAY_HARBOR_CAMPAIGN_ID,
        purpose="debug",
        perspective_kind="npc",
        perspective_id="iron_hook_collector_one",
        entity_ids=("protagonist", "harvey_cole"),
    )
    with game.store.connect() as connection:
        candidates = game.store.load_memory_candidates(connection, query)
    selection = select_memories(candidates, query)

    assert selection.selected == ()
    assert {value.reason for value in selection.rejected} == {"scope_mismatch"}


def test_memory_rebuild_is_deterministic_and_does_not_change_world_state(
    history_game: tuple[GameService, RejectingHistoryAdapter],
) -> None:
    game, _ = history_game
    game.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, _bribe(1, "memory-rebuild"))
    state_before = game.get_state(GRAY_HARBOR_CAMPAIGN_ID)

    assert game.rebuild_campaign_memories(GRAY_HARBOR_CAMPAIGN_ID) == 1
    with game.store.connect() as connection:
        first = [tuple(row) for row in connection.execute(
            """
            SELECT memory_id, source_event_id, schema_version, memory_type,
                   event_type, summary, importance, world_time, location_id, status
            FROM episodic_memories ORDER BY memory_id
            """
        ).fetchall()]
    assert game.rebuild_campaign_memories(GRAY_HARBOR_CAMPAIGN_ID) == 1
    with game.store.connect() as connection:
        second = [tuple(row) for row in connection.execute(
            """
            SELECT memory_id, source_event_id, schema_version, memory_type,
                   event_type, summary, importance, world_time, location_id, status
            FROM episodic_memories ORDER BY memory_id
            """
        ).fetchall()]

    assert first == second
    assert game.get_state(GRAY_HARBOR_CAMPAIGN_ID) == state_before


def test_memory_candidate_query_uses_entity_index(history_game) -> None:
    game, _ = history_game
    with game.store.connect() as connection:
        plan = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT m.*
            FROM episodic_memories AS m
            JOIN (
                SELECT memory_id
                FROM memory_entities
                WHERE entity_id IN (?, ?)
                GROUP BY memory_id
                HAVING COUNT(DISTINCT entity_id) = ?
            ) AS matched ON matched.memory_id = m.memory_id
            WHERE m.campaign_id = ?
            ORDER BY m.world_time DESC, m.importance DESC, m.memory_id
            LIMIT ?
            """,
            ("protagonist", "harvey_cole", 2, GRAY_HARBOR_CAMPAIGN_ID, 200),
        ).fetchall()
    details = " ".join(str(row["detail"]) for row in plan)
    assert "idx_memory_entities_entity" in details


def test_candidate_limit_is_applied_after_all_entities_match(
    history_game: tuple[GameService, RejectingHistoryAdapter],
) -> None:
    game, _ = history_game
    game.submit_turn(GRAY_HARBOR_CAMPAIGN_ID, _bribe(1, "memory-exact-before-limit"))
    game.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        TurnRequest(
            idempotency_key="memory-newer-unrelated-movement",
            expected_state_version=2,
            actor_id="protagonist",
            text="我去厨房。",
        ),
    )
    query = MemoryQuery(
        campaign_id=GRAY_HARBOR_CAMPAIGN_ID,
        purpose="npc_decision",
        perspective_kind="npc",
        perspective_id="harvey_cole",
        entity_ids=("protagonist", "harvey_cole"),
    )

    with game.store.connect() as connection:
        candidates = game.store.load_memory_candidates(
            connection,
            query,
            candidate_limit=1,
        )

    assert [memory.event_type for memory in candidates] == ["bribe.rejected"]


def test_fts_candidates_remain_subject_to_entity_and_scope_bounds(
    history_game: tuple[GameService, RejectingHistoryAdapter],
) -> None:
    game, _ = history_game
    game.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        _bribe(1, "memory-fts-candidate"),
    )
    with game.store.connect() as connection:
        query = MemoryQuery(
            campaign_id=GRAY_HARBOR_CAMPAIGN_ID,
            purpose="debug",
            perspective_kind="npc",
            perspective_id="harvey_cole",
            entity_ids=("protagonist", "harvey_cole"),
            search_text="贿赂",
            candidate_limit=10,
            retrieval_mode="hybrid",
        )
        fts_hits = connection.execute(
            "SELECT COUNT(*) FROM episodic_memory_fts_ngrams WHERE campaign_id = ? AND episodic_memory_fts_ngrams MATCH ?",
            (GRAY_HARBOR_CAMPAIGN_ID, '"贿赂"'),
        ).fetchone()[0]
        candidates, total, truncated, expanded = (
            game.store.load_memory_candidates_with_stats(connection, query)
        )
    selection = select_memories(
        candidates,
        query,
        candidate_total=total,
        truncated=truncated,
        expanded_ids=expanded,
    )
    assert candidates
    assert fts_hits >= 1
    assert any(memory.event_type.startswith("bribe.") for memory in candidates)
    assert selection.selected
    assert not selection.truncated


def test_link_expansion_is_one_hop_and_reports_truncation(tmp_path: Path) -> None:
    game = GameService(tmp_path / "fts-links.sqlite3")
    game.initialize()
    for index, text in enumerate(("我去厨房。", "我回到大厅。"), start=1):
        game.submit_turn(
            GRAY_HARBOR_CAMPAIGN_ID,
            TurnRequest(
                idempotency_key=f"memory-link-{index}",
                expected_state_version=index,
                actor_id="protagonist",
                text=text,
            ),
        )
    with game.store.connect() as connection:
        query = MemoryQuery(
            campaign_id=GRAY_HARBOR_CAMPAIGN_ID,
            purpose="debug",
            perspective_kind="player",
            perspective_id="protagonist",
            entity_ids=("protagonist",),
            event_types=("character.moved",),
            candidate_limit=1,
            expand_links=True,
        )
        candidates, total, truncated, expanded = (
            game.store.load_memory_candidates_with_stats(connection, query)
        )
    selection = select_memories(
        candidates,
        query,
        candidate_total=total,
        truncated=truncated,
        expanded_ids=expanded,
    )
    assert expanded
    assert len(selection.candidate_ids) >= 2
    assert selection.truncated


def test_fts_pressure_over_200_events_reports_bounded_candidates(tmp_path: Path) -> None:
    game = GameService(tmp_path / "fts-pressure.sqlite3")
    game.initialize()
    with game.store.connect() as connection:
        campaign_version = game.store.campaign_version(
            connection, GRAY_HARBOR_CAMPAIGN_ID
        )
        next_sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM events WHERE campaign_id = ?",
                (GRAY_HARBOR_CAMPAIGN_ID,),
            ).fetchone()[0]
        ) + 1
        for index in range(1, 241):
            event_id = f"pressure-event-{index}"
            connection.execute(
                """
                INSERT INTO events (
                    event_id, campaign_id, sequence, turn_id, event_type,
                    schema_version, world_time, actor_id, causation_id,
                    correlation_id, payload_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    GRAY_HARBOR_CAMPAIGN_ID,
                    next_sequence + index - 1,
                    f"pressure-turn-{index}",
                    "gift.rejected",
                    1,
                    1000 + index,
                    "protagonist",
                    f"pressure-causation-{index}",
                    f"pressure-correlation-{index}",
                    "{}",
                    "2026-08-20T00:00:00+00:00",
                ),
            )
            memory = EpisodicMemory(
                memory_id=f"pressure-memory-{index}",
                campaign_id=GRAY_HARBOR_CAMPAIGN_ID,
                source_event_id=event_id,
                schema_version=2,
                memory_type="interaction",
                event_type="gift.rejected",
                summary=(
                    "关键礼物被哈维拒绝，导致后续交涉受阻。"
                    if index == 1 else f"第{index}次无关记录。"
                ),
                importance=90 if index == 1 else 10,
                world_time=1000 + index,
                location_id="heron_house",
                status="active",
                update_key=None,
                entities=(
                    MemoryEntity("protagonist", "actor"),
                    MemoryEntity("harvey_cole", "target"),
                ),
                scopes=(MemoryScope("npc", "harvey_cole"),),
            )
            game.store.append_memory(connection, memory)
        structured_query = MemoryQuery(
            campaign_id=GRAY_HARBOR_CAMPAIGN_ID,
            purpose="debug",
            perspective_kind="npc",
            perspective_id="harvey_cole",
            entity_ids=("protagonist", "harvey_cole"),
            search_text="关键礼物",
            candidate_limit=200,
        )
        fts_query = replace(structured_query, retrieval_mode="fts")
        hybrid_query = replace(structured_query, retrieval_mode="hybrid")
        structured, total, truncated, _ = game.store.load_memory_candidates_with_stats(
            connection, structured_query
        )
        fts, _, fts_truncated, _ = game.store.load_memory_candidates_with_stats(
            connection, fts_query
        )
        hybrid, _, hybrid_truncated, _ = game.store.load_memory_candidates_with_stats(
            connection, hybrid_query
        )
    assert campaign_version == 1
    assert total == 240
    assert truncated
    assert not fts_truncated
    assert hybrid_truncated
    assert len(structured) == 200
    assert "pressure-memory-1" not in {memory.memory_id for memory in structured}
    assert [memory.memory_id for memory in fts] == ["pressure-memory-1"]
    assert "pressure-memory-1" in {memory.memory_id for memory in hybrid}


def test_movement_history_links_and_scene_summaries_are_stored(tmp_path: Path) -> None:
    game = GameService(tmp_path / "movement-memory.sqlite3")
    game.initialize()
    first = game.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        TurnRequest(
            idempotency_key="memory-move-kitchen",
            expected_state_version=1,
            actor_id="protagonist",
            text="我去厨房。",
        ),
    )
    with game.store.connect() as connection:
        first_memory_created_at = connection.execute(
            "SELECT created_at FROM episodic_memories"
        ).fetchone()["created_at"]
    second = game.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        TurnRequest(
            idempotency_key="memory-move-hall",
            expected_state_version=2,
            actor_id="protagonist",
            text="我回到大厅。",
        ),
    )
    first_detail = game.get_turn_detail(GRAY_HARBOR_CAMPAIGN_ID, first["turn_id"])
    second_detail = game.get_turn_detail(GRAY_HARBOR_CAMPAIGN_ID, second["turn_id"])

    assert second["state"]["scene"]["locationId"] == "white_heron_ground_floor"
    assert first_detail["scene_memory_summaries"][0]["status"] == "closed"
    assert first_detail["scene_memory_summaries"][0]["source_count"] == 1
    assert second_detail["scene_memory_summaries"][0]["segment_index"] == 1
    assert "content" not in first_detail["scene_memory_summaries"][0]

    with game.store.connect() as connection:
        memories = connection.execute(
            """
            SELECT memory_id, schema_version, status, update_key, created_at
            FROM episodic_memories ORDER BY world_time
            """
        ).fetchall()
        links = connection.execute(
            """
            SELECT source_memory_id, target_memory_id, relation_type, source_event_id
            FROM memory_links
            """
        ).fetchall()
        summaries = connection.execute(
            """
            SELECT summary_id, status, start_sequence, end_sequence
            FROM scene_memory_summaries ORDER BY segment_index
            """
        ).fetchall()
        update_plan = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT memory_id FROM episodic_memories
            WHERE campaign_id = ? AND update_key = ?
            """,
            (GRAY_HARBOR_CAMPAIGN_ID, "character_location:protagonist"),
        ).fetchall()
    assert len(memories) == 2
    assert all(row["schema_version"] == 2 for row in memories)
    assert all(row["status"] == "active" for row in memories)
    assert all(row["update_key"] == "character_location:protagonist" for row in memories)
    assert memories[0]["created_at"] == first_memory_created_at
    assert [row["relation_type"] for row in links] == ["updates"]
    assert links[0]["source_memory_id"] == memories[1]["memory_id"]
    assert links[0]["target_memory_id"] == memories[0]["memory_id"]
    assert len(summaries) == 2
    assert all(row["start_sequence"] <= row["end_sequence"] for row in summaries)
    assert "idx_memories_campaign_update_key" in " ".join(
        str(row["detail"]) for row in update_plan
    )


def test_memory_model_rebuild_preserves_links_and_summary_business_fields(tmp_path: Path) -> None:
    game = GameService(tmp_path / "model-rebuild.sqlite3")
    game.initialize()
    game.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        TurnRequest(
            idempotency_key="memory-rebuild-move-one",
            expected_state_version=1,
            actor_id="protagonist",
            text="我去厨房。",
        ),
    )
    game.submit_turn(
        GRAY_HARBOR_CAMPAIGN_ID,
        TurnRequest(
            idempotency_key="memory-rebuild-move-two",
            expected_state_version=2,
            actor_id="protagonist",
            text="我回到大厅。",
        ),
    )

    def snapshot():
        with game.store.connect() as connection:
            links = [tuple(row) for row in connection.execute(
                """
                SELECT link_id, source_memory_id, target_memory_id,
                       relation_type, source_event_id, schema_version
                FROM memory_links ORDER BY link_id
                """
            ).fetchall()]
            summaries = [tuple(row) for row in connection.execute(
                """
                SELECT summary_id, scene_id, segment_index, location_id,
                       schema_version, content, start_sequence, end_sequence,
                       start_world_time, end_world_time, generator,
                       generator_version, status
                FROM scene_memory_summaries ORDER BY summary_id
                """
            ).fetchall()]
            event_sources = [tuple(row) for row in connection.execute(
                """
                SELECT summary_id, event_id, ordinal
                FROM scene_summary_event_sources
                ORDER BY summary_id, ordinal
                """
            ).fetchall()]
            memory_sources = [tuple(row) for row in connection.execute(
                """
                SELECT summary_id, memory_id, ordinal
                FROM scene_summary_memory_sources
                ORDER BY summary_id, ordinal
                """
            ).fetchall()]
        return links, summaries, event_sources, memory_sources

    before = snapshot()
    assert game.rebuild_campaign_memories(GRAY_HARBOR_CAMPAIGN_ID) == 2
    after = snapshot()
    assert after == before
