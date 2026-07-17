"""ContextSnapshot 不可变历史边界测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.handlers.context_snapshot import (
    ContextAnchor,
    ContextSnapshot,
    build_context_snapshot,
    context_anchor_from_binding,
)


@pytest.fixture(autouse=True)
def _resource_manifest_boundary():
    manifest = MagicMock(assets=(), allowed_paths=frozenset())
    with patch(
        "services.handlers.resource_manifest.build_resource_manifest",
        return_value=manifest,
    ):
        yield


def _query(data):
    query = MagicMock()
    for method in (
        "select", "eq", "in_", "lte", "order", "range", "maybe_single",
    ):
        getattr(query, method).return_value = query
    query.execute.return_value = SimpleNamespace(data=data)
    return query


def _anchor(base_revision: int = 3) -> ContextAnchor:
    return ContextAnchor(
        task_id="task-1",
        conversation_id="conv-1",
        turn_id="turn-1",
        input_message_id="input-1",
        base_revision=base_revision,
        through_message_id="assistant-0",
        org_id="org-1",
    )


def test_context_anchor_from_binding_preserves_transaction_boundary():
    anchor = context_anchor_from_binding(
        {"id": "task-1", "conversation_id": "conv-1", "org_id": "org-1"},
        "input-1",
        "turn-1",
        {
            "base_context_revision": 7,
            "context_through_message_id": "assistant-6",
        },
    )

    assert anchor.base_revision == 7
    assert anchor.through_message_id == "assistant-6"
    assert anchor.input_message_id == "input-1"


@pytest.mark.asyncio
async def test_snapshot_uses_revision_boundary_and_keeps_duplicate_text():
    input_query = _query({
        "id": "input-1",
        "conversation_id": "conv-1",
        "role": "user",
        "turn_id": "turn-1",
    })
    history_query = _query([
        {
            "role": "user",
            "content": [{"type": "text", "text": "重复问题"}],
            "status": "completed",
            "created_at": "2026-07-17T01:00:00Z",
            "generation_params": {},
            "context_revision": 2,
            "message_kind": "conversation",
        },
    ])
    conversation_query = _query({
        "context_summary": "更早的事实",
        "summary_revision": 2,
        "source": "wecom",
    })
    db = MagicMock()
    db.table.side_effect = [input_query, history_query, conversation_query]

    snapshot = await build_context_snapshot(db, _anchor(), "重复问题")

    history_query.lte.assert_called_once_with("context_revision", 3)
    history_query.eq.assert_any_call("message_kind", "conversation")
    assert snapshot.history_messages == [
        {"role": "user", "content": "重复问题"},
    ]
    assert "更早的事实" in (snapshot.summary_prompt or "")
    assert snapshot.summary_revision == 2
    assert snapshot.conversation_source == "wecom"


@pytest.mark.asyncio
async def test_snapshot_rejects_future_summary():
    input_query = _query({
        "id": "input-1",
        "conversation_id": "conv-1",
        "role": "user",
        "turn_id": "turn-1",
    })
    history_query = _query([])
    conversation_query = _query({
        "context_summary": "未来摘要",
        "summary_revision": 4,
        "source": "",
    })
    db = MagicMock()
    db.table.side_effect = [input_query, history_query, conversation_query]

    snapshot = await build_context_snapshot(db, _anchor(base_revision=3), "当前")

    assert snapshot.summary_prompt is None
    assert snapshot.summary_revision == 0


@pytest.mark.asyncio
async def test_snapshot_rejects_mismatched_input_anchor():
    db = MagicMock()
    db.table.return_value = _query({
        "id": "input-1",
        "conversation_id": "other-conversation",
        "role": "user",
        "turn_id": "turn-1",
    })

    with pytest.raises(ValueError, match="CONTEXT_INPUT_ANCHOR_MISMATCH"):
        await build_context_snapshot(db, _anchor(), "当前")


@pytest.mark.asyncio
async def test_snapshot_propagates_history_database_failure():
    input_query = _query({
        "id": "input-1",
        "conversation_id": "conv-1",
        "role": "user",
        "turn_id": "turn-1",
    })
    history_query = _query([])
    history_query.execute.side_effect = RuntimeError("database unavailable")
    db = MagicMock()
    db.table.side_effect = [input_query, history_query]

    with pytest.raises(RuntimeError, match="database unavailable"):
        await build_context_snapshot(db, _anchor(), "当前")


@pytest.mark.asyncio
async def test_snapshot_cache_hit_does_not_query_history_database():
    input_query = _query({
        "id": "input-1",
        "conversation_id": "conv-1",
        "role": "user",
        "turn_id": "turn-1",
    })
    conversation_query = _query({
        "context_summary": None,
        "summary_revision": 0,
        "source": "web",
    })
    db = MagicMock()
    db.table.side_effect = [input_query, conversation_query]

    with (
        patch(
            "services.handlers.conversation_cache.get_closed_messages",
            new=AsyncMock(return_value=[{"role": "user", "content": "缓存历史"}]),
        ) as get_cached,
        patch(
            "services.handlers.conversation_cache.set_closed_messages",
            new=AsyncMock(),
        ) as set_cached,
    ):
        snapshot = await build_context_snapshot(db, _anchor(), "当前")

    assert snapshot.history_messages == [{"role": "user", "content": "缓存历史"}]
    get_cached.assert_awaited_once_with(
        "conv-1",
        3,
        "assistant-0",
        "org-1",
        task_id="task-1",
        turn_id="turn-1",
    )
    set_cached.assert_not_awaited()
    assert db.table.call_count == 2


@pytest.mark.asyncio
async def test_prompt_builder_snapshot_bypasses_mutable_history_cache():
    from services.prompt_builder.builder import BuildInput, PromptBuilder

    snapshot = ContextSnapshot(
        anchor=_anchor(),
        history_messages=[{"role": "user", "content": "固定历史"}],
        summary_prompt="固定摘要",
        summary_revision=2,
        conversation_source="",
    )
    builder = PromptBuilder(BuildInput(
        user_id="user-1",
        conversation_id="conv-1",
        org_id="org-1",
        text_content="当前",
        context_snapshot=snapshot,
        db=MagicMock(),
    ))

    with (
        patch(
            "services.prompt_builder.session_memory_cache.get_session_memory",
            new=AsyncMock(return_value=(None, "")),
        ),
        patch(
            "services.handlers.conversation_cache.get_closed_messages",
            new=AsyncMock(),
        ) as get_cached,
        patch(
            "services.handlers.chat_context.summary_manager.get_context_summary",
            new=AsyncMock(),
        ) as get_summary,
    ):
        _, summary, history = await builder._parallel_fetch()

    assert summary == "固定摘要"
    assert history == [{"role": "user", "content": "固定历史"}]
    history[0]["content"] = "任务私有修改"
    assert snapshot.history_messages[0]["content"] == "固定历史"
    get_cached.assert_not_awaited()
    get_summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_wecom_snapshot_uses_bounded_budget_profile():
    from core.config import get_settings
    from services.prompt_builder.builder import BuildInput, PromptBuilder

    snapshot = ContextSnapshot(
        anchor=_anchor(),
        history_messages=[],
        summary_prompt=None,
        summary_revision=0,
        conversation_source="wecom",
    )
    builder = PromptBuilder(BuildInput(
        user_id="user-1",
        conversation_id="conv-1",
        text_content="当前",
        context_snapshot=snapshot,
    ))
    messages = [{"role": "user", "content": "当前"}]

    with (
        patch(
            "services.handlers.context_compressor.enforce_tool_budget",
        ) as enforce_tool,
        patch(
            "services.handlers.context_compressor.enforce_history_budget",
            new=AsyncMock(),
        ) as enforce_history,
        patch(
            "services.handlers.context_compressor.enforce_budget",
        ) as enforce_total,
    ):
        await builder._apply_budgets(messages, "当前")

    settings = get_settings()
    enforce_tool.assert_called_once_with(
        messages, settings.context_tool_token_budget,
    )
    enforce_history.assert_awaited_once_with(
        messages,
        settings.context_history_token_budget,
        current_query="当前",
    )
    enforce_total.assert_called_once_with(messages, settings.context_max_tokens)
