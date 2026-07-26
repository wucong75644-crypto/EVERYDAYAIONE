"""模型评分服务的 Worker RPC 合同。"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


def _row(**overrides):
    row = {
        "model_id": "model-a",
        "task_type": "chat",
        "owner_user_id": None,
        "total": 100,
        "success_count": 95,
        "p75_latency": 1000,
        "retry_count": 2,
        "timeout_count": 1,
        "hard_error_count": 1,
        "period_start": datetime(2026, 7, 1, tzinfo=timezone.utc),
        "period_end": datetime(2026, 7, 2, tzinfo=timezone.utc),
        "old_score": 0.9,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_snapshot_uses_only_worker_rpc() -> None:
    from services.model_scorer import _query_aggregated_metrics

    db = MagicMock()
    db.rpc.return_value.execute.return_value = SimpleNamespace(
        data=[_row()],
    )

    rows = await _query_aggregated_metrics(db, org_id=None)

    assert rows[0]["model_id"] == "model-a"
    db.rpc.assert_called_once_with(
        "worker_model_scoring_snapshot", {"p_org_id": None},
    )
    db.table.assert_not_called()


@pytest.mark.asyncio
async def test_personal_owner_is_forwarded_to_atomic_commit() -> None:
    from services.model_scorer import _commit_model_score

    owner_id = str(uuid4())
    row = _row(owner_user_id=owner_id)
    db = MagicMock()
    db.rpc.return_value.execute.return_value = SimpleNamespace(data={
        "outcome": "recorded",
        "knowledge_node_id": None,
    })

    await _commit_model_score(
        db, row, 0.9, 0.91, "pending_review", None, org_id=None,
    )

    name, params = db.rpc.call_args.args
    assert name == "worker_commit_model_score"
    assert params["p_owner_user_id"] == owner_id
    assert params["p_status"] == "pending_review"
    assert params["p_title"] is None


@pytest.mark.asyncio
async def test_auto_applied_builds_knowledge_then_commits_once() -> None:
    from services.model_scorer import aggregate_model_scores

    db = MagicMock()
    row = _row(old_score=0.9)
    with (
        patch("services.model_scorer.is_kb_available", return_value=True),
        patch(
            "services.model_scorer._query_aggregated_metrics",
            new_callable=AsyncMock,
            return_value=[row],
        ),
        patch(
            "services.model_scorer._build_score_knowledge",
            new_callable=AsyncMock,
            return_value={"title": "score"},
        ) as build,
        patch(
            "services.model_scorer._commit_model_score",
            new_callable=AsyncMock,
        ) as commit,
    ):
        await aggregate_model_scores(db_source=db)

    build.assert_awaited_once()
    commit.assert_awaited_once()
    assert commit.call_args.args[4] == "auto_applied"
    assert commit.call_args.args[5] == {"title": "score"}


@pytest.mark.asyncio
async def test_single_commit_failure_does_not_abort_next_model() -> None:
    from services.model_scorer import aggregate_model_scores

    db = MagicMock()
    rows = [_row(model_id="bad"), _row(model_id="good")]
    with (
        patch("services.model_scorer.is_kb_available", return_value=True),
        patch(
            "services.model_scorer._query_aggregated_metrics",
            new_callable=AsyncMock,
            return_value=rows,
        ),
        patch(
            "services.model_scorer._build_score_knowledge",
            new_callable=AsyncMock,
            return_value={"title": "score"},
        ),
        patch(
            "services.model_scorer._commit_model_score",
            new_callable=AsyncMock,
            side_effect=[RuntimeError("db"), None],
        ) as commit,
    ):
        await aggregate_model_scores(db_source=db)

    assert commit.await_count == 2
