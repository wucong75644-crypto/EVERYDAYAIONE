"""ContextSummary 闭合 revision 选择与原子提交契约测试。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.handlers.chat_context.summary_manager import (
    _apply_summary,
    _select_closed_summary_window,
    update_summary_if_needed,
)
from services.agent.runtime.context import SummaryCoordination


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "137_context_summary_revision_rpc.sql"
ROLLBACK = (
    MIGRATIONS / "rollback" / "137_context_summary_revision_rpc_rollback.sql"
)


def _turn(revision: int) -> list[dict]:
    return [
        {
            "id": f"user-{revision}",
            "role": "user",
            "content": f"问题{revision}",
            "context_revision": revision,
            "message_kind": "conversation",
        },
        {
            "id": f"assistant-{revision}",
            "role": "assistant",
            "content": f"回答{revision}",
            "context_revision": revision,
            "message_kind": "conversation",
        },
    ]


def test_summary_window_never_covers_partial_turn() -> None:
    rows = [row for revision in range(1, 5) for row in _turn(revision)]

    selected, new_rows, revision, message_id = _select_closed_summary_window(
        rows,
        context_limit=5,
        current_summary_revision=0,
    )

    assert [row["context_revision"] for row in selected] == [1, 1]
    assert new_rows == selected
    assert revision == 1
    assert message_id == "assistant-1"


def test_summary_window_stops_at_revision_gap() -> None:
    rows = _turn(1) + _turn(3) + _turn(4) + _turn(5)

    selected, new_rows, revision, message_id = _select_closed_summary_window(
        rows,
        context_limit=2,
        current_summary_revision=1,
    )

    assert selected == []
    assert new_rows == []
    assert revision == 0
    assert message_id is None


def test_apply_summary_uses_expected_revision_cas() -> None:
    db = MagicMock()
    db.rpc.return_value.execute.return_value = SimpleNamespace(
        data={"outcome": "stale"}
    )

    outcome = _apply_summary(
        db,
        conversation_id="conv-1",
        expected_revision=4,
        through_revision=6,
        through_message_id="assistant-6",
        summary="摘要",
        message_count=30,
    )

    assert outcome == "stale"
    db.rpc.assert_called_once_with("apply_context_summary", {
        "p_conversation_id": "conv-1",
        "p_expected_summary_revision": 4,
        "p_through_revision": 6,
        "p_through_message_id": "assistant-6",
        "p_summary": "摘要",
        "p_summary_message_count": 30,
    })


def test_migration_locks_validates_and_cas_updates_summary() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE OR REPLACE FUNCTION apply_context_summary" in sql
    assert "FROM conversations" in sql and "FOR UPDATE" in sql
    assert "summary_revision <> p_expected_summary_revision" in sql
    assert "'outcome', 'stale'" in sql
    assert "p_through_revision > v_conversation.context_revision" in sql
    assert "v_boundary.role::TEXT <> 'assistant'" in sql
    assert "v_boundary.status::TEXT <> 'completed'" in sql
    assert "v_boundary.context_revision IS DISTINCT FROM p_through_revision" in sql
    assert "summary_through_message_id = p_through_message_id" in sql
    assert "SECURITY INVOKER" in sql
    assert "TO service_role" in sql


def test_rollback_removes_only_summary_rpc() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")

    assert "DROP FUNCTION IF EXISTS apply_context_summary" in rollback
    assert "DROP COLUMN" not in rollback


@pytest.mark.asyncio
async def test_suppressed_prefix_does_not_call_summary_model() -> None:
    db = MagicMock()
    conversation = SimpleNamespace(data={
        "message_count": 30,
        "summary_message_count": 0,
        "context_summary": None,
        "context_revision": 15,
        "summary_revision": 0,
    })
    rows = SimpleNamespace(
        data=[row for revision in range(1, 16) for row in _turn(revision)]
    )
    db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = conversation
    messages_query = db.table.return_value.select.return_value.eq.return_value.eq.return_value.in_.return_value.order.return_value.order.return_value
    messages_query.execute.return_value = rows

    with (
        patch(
            "services.agent.runtime.context.acquire_summary_coordination",
            new=AsyncMock(return_value=SummaryCoordination(
                "suppressed",
                "context-summary:hash",
            )),
        ),
        patch(
            "services.context_summarizer.summarize_messages",
            new=AsyncMock(),
        ) as summarize,
    ):
        await update_summary_if_needed(db, "conv-1")

    summarize.assert_not_awaited()
