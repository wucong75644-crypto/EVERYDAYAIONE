"""Chat 工具轮次结构编排测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.multimodal import FileReadResult
from services.handlers.context_compressor import compact_loop_with_summary
from services.handlers.chat.tool_loop import (
    append_tool_images,
    apply_tool_results,
    compact_tool_context,
    prepare_tool_turn,
)


def test_prepare_tool_turn_appends_context_and_permission_prompts() -> None:
    messages: list[dict] = []
    context = SimpleNamespace(
        discovered_tools=set(),
        build_context_prompt=lambda: "动态上下文",
    )
    permission = MagicMock()
    permission.need_exit_attachment = True
    permission.consume_exit_attachment.return_value = "退出附件"
    permission.get_reminder.return_value = "权限提醒"
    tools = [{"function": {"name": "query"}}]

    result = prepare_tool_turn(
        core_tools=tools,
        discovered_names=set(),
        org_id="org-1",
        turn=1,
        messages=messages,
        tool_context=context,
        permission=permission,
    )

    assert result == tools
    assert [message["content"] for message in messages] == [
        "动态上下文",
        "退出附件",
        "权限提醒",
    ]


def test_apply_tool_results_preserves_protocol() -> None:
    calls = [
        {
            "id": "call-1",
            "name": "code_execute",
            "arguments": '{"code":"print(1)"}',
        }
    ]
    messages: list[dict] = []
    blocks: list[dict] = []
    messages.append({
        "role": "assistant",
        "content": "处理中",
        "tool_calls": [{
            "id": "call-1",
            "type": "function",
            "function": {
                "name": "code_execute",
                "arguments": '{"code":"print(1)"}',
            },
        }],
    })
    blocks.append({
        "type": "tool_step",
        "tool_name": "code_execute",
        "tool_call_id": "call-1",
        "status": "running",
        "code": "print(1)",
    })
    image_urls = apply_tool_results(
        tool_results=[
            (
                calls[0],
                FileReadResult(
                    type="image",
                    text="图片",
                    image_url="https://cdn.test/image.png",
                ),
                False,
                "完成",
            )
        ],
        messages=messages,
        content_blocks=blocks,
        start_times={"call-1": 0},
        tool_context=MagicMock(),
    )

    assert messages[0]["role"] == "assistant"
    assert messages[1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "图片",
    }
    assert blocks[0]["code"] == "print(1)"
    assert blocks[0]["status"] == "completed"
    assert blocks[0]["output"] == "完成"
    assert image_urls == ["https://cdn.test/image.png"]


def test_append_tool_images_adds_multimodal_user_message() -> None:
    messages: list[dict] = []

    append_tool_images(messages, ["https://cdn.test/image.png"])

    assert messages[0]["role"] == "user"
    assert messages[0]["content"][1]["image_url"]["url"].endswith("image.png")


@pytest.mark.asyncio
async def test_compact_tool_context_uses_model_budget() -> None:
    context_budget = SimpleNamespace(
        usable_input=1_000,
        soft_compaction=750,
        hard_compaction=850,
        emergency_trim=920,
    )
    with (
        patch(
            "services.handlers.context_compressor.compact_stale_by_user_turns"
        ) as compact_stale,
        patch(
            "services.handlers.context_compressor.enforce_tool_budget"
        ) as enforce_tool,
        patch(
            "services.handlers.context_compressor.enforce_history_budget_sync"
        ) as enforce_history,
        patch(
            "services.handlers.context_compressor.enforce_budget"
        ) as enforce_total,
        patch(
            "services.handlers.context_compressor.compact_loop_with_summary",
            new_callable=AsyncMock,
        ) as compact_summary,
    ):
        await compact_tool_context(
            messages=[],
            context_budget=context_budget,
            turn=3,
        )

    compact_stale.assert_called_once_with(
        [],
        keep_user_turns=3,
        capacity_trigger=0.75,
        max_tokens=1_000,
    )
    enforce_tool.assert_called_once_with([], 850)
    enforce_history.assert_called_once_with([], 850)
    compact_summary.assert_awaited_once_with(
        [],
        1_000,
        0.85,
        suppression_scope=None,
    )
    enforce_total.assert_called_once_with([], 920)


@pytest.mark.asyncio
async def test_compact_tool_context_defers_summary_before_third_turn() -> None:
    context_budget = SimpleNamespace(
        usable_input=1_000,
        soft_compaction=750,
        hard_compaction=850,
        emergency_trim=920,
    )
    with (
        patch(
            "services.handlers.context_compressor.compact_stale_by_user_turns"
        ),
        patch("services.handlers.context_compressor.enforce_tool_budget"),
        patch(
            "services.handlers.context_compressor.enforce_history_budget_sync"
        ),
        patch("services.handlers.context_compressor.enforce_budget"),
        patch(
            "services.handlers.context_compressor.compact_loop_with_summary",
            new_callable=AsyncMock,
        ) as compact_summary,
    ):
        await compact_tool_context(
            messages=[],
            context_budget=context_budget,
            turn=2,
        )

    compact_summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_loop_summary_only_uses_messages_it_replaces() -> None:
    messages: list[dict] = []
    for index in range(4):
        call_id = f"call-{index}"
        messages.extend([
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "erp_agent", "arguments": "{}"},
                }],
            },
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": f"第{index}轮结果",
            },
        ])

    with patch(
        "services.context_summarizer._call_summary_model",
        new=AsyncMock(return_value="旧工具轮次摘要"),
    ) as summarize:
        compacted = await compact_loop_with_summary(
            messages,
            max_tokens=1,
            trigger_ratio=0.01,
        )

    assert compacted is True
    summary_input = summarize.await_args.args[1]
    assert "第0轮结果" in summary_input
    assert "第1轮结果" in summary_input
    assert "第2轮结果" not in summary_input
    assert "第3轮结果" not in summary_input
