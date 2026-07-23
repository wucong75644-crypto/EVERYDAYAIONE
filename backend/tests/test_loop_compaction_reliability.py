"""当前 Run 循环摘要的并发、抑制与 prefix 校验。"""

import asyncio
import copy
from unittest.mock import AsyncMock, patch

import pytest

from services.agent.runtime.context import (
    clear_loop_compaction_scope,
    compact_context,
)


def _messages(turns: int = 5) -> list[dict]:
    messages = [
        {"role": "system", "content": "你是AI助手"},
        {"role": "user", "content": "帮我查一下"},
    ]
    for turn in range(turns):
        call_id = f"call-{turn}"
        messages.extend([
            {
                "role": "assistant",
                "content": f"第{turn}轮",
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "query", "arguments": "{}"},
                }],
            },
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": "结果" + "x" * 2_500,
            },
        ])
    return messages


@pytest.mark.asyncio
async def test_failure_suppresses_same_prefix_in_run() -> None:
    messages = _messages()
    summarize = AsyncMock(return_value=None)
    with patch(
        "services.agent.runtime.context.summary_model.call_summary_model",
        new=summarize,
    ):
        first = await compact_context(
            messages,
            usable_input=10,
            trigger_ratio=0.01,
            suppression_scope="task-failure",
        )
        second = await compact_context(
            messages,
            usable_input=10,
            trigger_ratio=0.01,
            suppression_scope="task-failure",
        )
    await clear_loop_compaction_scope("task-failure")

    assert first.outcome == "failed"
    assert second.outcome == "suppressed"
    assert summarize.await_count == 2


@pytest.mark.asyncio
async def test_same_prefix_has_single_in_flight_summary() -> None:
    first_messages = _messages()
    second_messages = copy.deepcopy(first_messages)
    started = asyncio.Event()
    release = asyncio.Event()

    async def summarize(*_args, **_kwargs):
        started.set()
        await release.wait()
        return "摘要"

    with patch(
        "services.agent.runtime.context.summary_model.call_summary_model",
        new=AsyncMock(side_effect=summarize),
    ) as call:
        first_task = asyncio.create_task(compact_context(
            first_messages,
            usable_input=10,
            trigger_ratio=0.01,
            suppression_scope="task-concurrent",
        ))
        await started.wait()
        second = await compact_context(
            second_messages,
            usable_input=10,
            trigger_ratio=0.01,
            suppression_scope="task-concurrent",
        )
        release.set()
        first = await first_task
    await clear_loop_compaction_scope("task-concurrent")

    assert first.outcome == "compacted"
    assert second.outcome == "in_flight"
    assert call.await_count == 1


@pytest.mark.asyncio
async def test_changed_prefix_discards_generated_summary() -> None:
    messages = _messages()
    original_len = len(messages)

    async def summarize(*_args, **_kwargs):
        messages[3]["content"] = "并发更新后的工具结果"
        return "过期摘要"

    with patch(
        "services.agent.runtime.context.summary_model.call_summary_model",
        new=AsyncMock(side_effect=summarize),
    ):
        result = await compact_context(
            messages,
            usable_input=10,
            trigger_ratio=0.01,
            suppression_scope="task-prefix-change",
        )
    await clear_loop_compaction_scope("task-prefix-change")

    assert result.outcome == "stale_prefix"
    assert len(messages) == original_len
    assert all(
        "[工具循环摘要]" not in str(message.get("content"))
        for message in messages
    )


@pytest.mark.asyncio
async def test_model_exception_returns_failed_receipt_and_preserves_input() -> None:
    messages = _messages()
    original = copy.deepcopy(messages)
    with patch(
        "services.agent.runtime.context.summary_model.call_summary_model",
        new=AsyncMock(side_effect=RuntimeError("provider unavailable")),
    ):
        receipt = await compact_context(
            messages,
            usable_input=10,
            trigger_ratio=0.01,
            suppression_scope="task-exception",
            model_step=3,
        )
    await clear_loop_compaction_scope("task-exception")

    assert messages == original
    assert receipt.outcome == "failed"
    assert receipt.model_step == 3
    assert receipt.to_dict()["prefix_hash"]
