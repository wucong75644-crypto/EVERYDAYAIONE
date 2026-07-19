"""通用 Curated Memory Search/Get 策略测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.memory.memory_service_v2 import MemoryServiceV2
from services.memory.recall_policy import normalize_relevance, rank_for_recall
from services.memory.retrieval_pipeline import RetrievalPipeline


NOW = datetime(2026, 7, 19, tzinfo=timezone.utc)


def _row(
    record_id: str,
    content: str,
    relevance: float,
    *,
    updated_at: datetime = NOW,
    priority: int = 70,
) -> dict:
    return {
        "record_id": record_id,
        "content": content,
        "kind": "preference",
        "priority": priority,
        "relevance_score": relevance,
        "updated_at": updated_at,
    }


def test_relevance_normalizes_channels_and_agreement() -> None:
    assert normalize_relevance(vector_score=1.5) == 1.0
    assert normalize_relevance(keyword_score=0.0) == 0.0
    assert normalize_relevance(
        vector_score=0.2,
        keyword_score=0.01,
        matched_both=True,
    ) == 0.55


def test_policy_rejects_low_score_and_applies_time_decay() -> None:
    ranked = rank_for_recall(
        [
            _row("low", "低相关", 0.29),
            _row("old", "旧事实", 0.8, updated_at=NOW - timedelta(days=500)),
            _row("new", "新事实", 0.8),
        ],
        max_results=5,
        score_threshold=0.3,
        now=NOW,
    )

    assert [item["record_id"] for item in ranked] == ["new", "old"]


def test_policy_mmr_prefers_diverse_memory() -> None:
    ranked = rank_for_recall(
        [
            _row("a", "用户偏好非常简洁的回答", 0.90),
            _row("b", "用户偏好简洁回答", 0.89),
            _row("c", "用户每周五进行复盘", 0.80),
        ],
        max_results=2,
        score_threshold=0.3,
        now=NOW,
        mmr_lambda=0.55,
    )

    assert ranked[0]["record_id"] == "a"
    assert ranked[1]["record_id"] == "c"


def test_policy_handles_empty_limit_invalid_date_and_short_text() -> None:
    assert rank_for_recall(
        [_row("a", "A", 0.9)],
        max_results=0,
        score_threshold=0.3,
        now=NOW,
    ) == []

    ranked = rank_for_recall(
        [
            {
                **_row("a", "A", 0.9),
                "updated_at": "invalid-date",
            },
            {
                **_row("b", "B", 0.8),
                "updated_at": NOW.isoformat(),
            },
        ],
        max_results=2,
        score_threshold=0.3,
        now=NOW,
    )

    assert {item["record_id"] for item in ranked} == {"a", "b"}


@pytest.mark.asyncio
async def test_search_queries_only_active_current_curated_memories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MagicMock()
    db.fetch = AsyncMock(return_value=[])
    pipeline = RetrievalPipeline(db)
    monkeypatch.setattr(
        "services.memory.retrieval_pipeline.get_embedding",
        AsyncMock(return_value=[0.1, 0.2]),
    )

    await pipeline.search(
        "简洁回答",
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    )

    assert db.fetch.await_count == 2
    for call in db.fetch.await_args_list:
        sql = call.args[0]
        assert "status = 'active'" in sql
        assert "valid_from IS NULL OR valid_from <= NOW()" in sql
        assert "valid_until IS NULL OR valid_until > NOW()" in sql
        assert "metadata->>'domain'" not in sql
        assert "scene_name" not in sql
        assert " content, type" not in sql


@pytest.mark.asyncio
async def test_get_is_tenant_scoped_and_lifecycle_checked() -> None:
    db = MagicMock()
    db.fetchrow = AsyncMock(return_value={
        "record_id": "33333333-3333-3333-3333-333333333333",
        "content": "用户偏好简洁回答",
        "priority": 80,
        "kind": "preference",
        "valid_from": None,
        "valid_until": None,
        "source_message_ids": [
            "44444444-4444-4444-4444-444444444444",
        ],
    })
    pipeline = RetrievalPipeline(db)

    memory = await pipeline.get(
        "33333333-3333-3333-3333-333333333333",
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    )

    assert memory is not None
    assert memory.kind == "preference"
    sql = db.fetchrow.await_args.args[0]
    assert "org_id IS NOT DISTINCT FROM $2::uuid" in sql
    assert "user_id = $3::uuid" in sql
    assert "status = 'active'" in sql
    assert "valid_until IS NULL OR valid_until > NOW()" in sql
    assert "scene_name" not in sql
    assert " content, type" not in sql


@pytest.mark.asyncio
async def test_get_failure_is_fail_closed() -> None:
    db = MagicMock()
    db.fetchrow = AsyncMock(side_effect=TimeoutError("database timeout"))

    memory = await RetrievalPipeline(db).get(
        "33333333-3333-3333-3333-333333333333",
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    )

    assert memory is None


@pytest.mark.asyncio
async def test_search_database_timeout_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def blocked_fetch(*args):
        await asyncio.sleep(0.02)
        return []

    db = MagicMock()
    db.fetch = AsyncMock(side_effect=blocked_fetch)
    pipeline = RetrievalPipeline(db)
    monkeypatch.setattr(pipeline._cfg, "retrieval_timeout", 0.001)
    monkeypatch.setattr(
        "services.memory.retrieval_pipeline.get_embedding",
        AsyncMock(return_value=None),
    )

    result = await pipeline.search(
        "简洁回答",
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        strategy="keyword",
    )

    assert result == []


@pytest.mark.asyncio
async def test_service_get_returns_source_lineage() -> None:
    service = MemoryServiceV2()
    service._db = MagicMock()
    service._retrieval = MagicMock()
    service._retrieval.get = AsyncMock(return_value=MagicMock(
        atom_id="memory-1",
        content="用户偏好简洁回答",
        kind="preference",
        priority=80,
        score=1.0,
        valid_from=None,
        valid_until=None,
        source_message_ids=("message-1",),
    ))

    result = await service.get_memory(
        user_id="user-1",
        memory_id="memory-1",
        org_id="org-1",
    )

    assert result is not None
    assert result["metadata"]["source_message_ids"] == ["message-1"]
    assert "type" not in result["metadata"]
    service._retrieval.get.assert_awaited_once_with(
        atom_id="memory-1",
        user_id="user-1",
        org_id="org-1",
    )


@pytest.mark.asyncio
async def test_service_search_returns_only_generic_metadata() -> None:
    service = MemoryServiceV2()
    service._db = MagicMock()
    service._retrieval = MagicMock()
    service._retrieval.search = AsyncMock(return_value=[MagicMock(
        atom_id="memory-1",
        content="用户偏好简洁回答",
        kind="preference",
        priority=80,
        score=0.9,
    )])

    result = await service.get_relevant_memories(
        user_id="user-1",
        query="回答偏好",
        org_id="org-1",
    )

    assert result[0]["metadata"] == {
        "kind": "preference",
        "priority": 80,
        "score": 0.9,
    }


@pytest.mark.asyncio
async def test_service_get_rejects_missing_scope_without_database_read() -> None:
    service = MemoryServiceV2()
    service._db = MagicMock()
    service._retrieval = MagicMock()
    service._retrieval.get = AsyncMock(return_value=None)

    assert await service.get_memory("user-1", "memory-1", org_id="") is None
    service._retrieval.get.assert_awaited_once_with(
        atom_id="memory-1",
        user_id="user-1",
        org_id="",
    )


@pytest.mark.asyncio
async def test_personal_scope_search_uses_null_safe_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MagicMock()
    db.fetch = AsyncMock(return_value=[])
    pipeline = RetrievalPipeline(db)
    monkeypatch.setattr(
        "services.memory.retrieval_pipeline.get_embedding",
        AsyncMock(return_value=[0.1, 0.2]),
    )

    await pipeline.search("简洁回答", "user-1", None)

    assert db.fetch.await_count == 2
    for call in db.fetch.await_args_list:
        assert "org_id IS NOT DISTINCT FROM $2::uuid" in call.args[0]
        assert call.args[2] is None
