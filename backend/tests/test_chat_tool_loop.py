"""Chat 工具轮次结构编排测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.multimodal import FileReadResult
from services.handlers.chat.stream_session import StreamDelivery
from services.handlers.chat.tool_loop import (
    append_tool_images,
    apply_tool_results,
    begin_tool_calls,
    compact_tool_context,
    prepare_tool_turn,
    push_emit_payloads,
    push_form_block,
)


def _delivery() -> StreamDelivery:
    return StreamDelivery(
        task_id="task-1",
        conversation_id="conv-1",
        message_id="message-1",
        user_id="user-1",
        org_id="org-1",
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


@pytest.mark.asyncio
async def test_begin_and_apply_tool_results_preserve_protocol() -> None:
    calls = [
        {
            "id": "call-1",
            "name": "code_execute",
            "arguments": '{"code":"print(1)"}',
        }
    ]
    messages: list[dict] = []
    blocks: list[dict] = []
    websocket = MagicMock()
    websocket.send_to_task_or_user = AsyncMock()
    save_blocks = AsyncMock()

    start_times = await begin_tool_calls(
        completed_calls=calls,
        turn_text="处理中",
        turn=0,
        messages=messages,
        content_blocks=blocks,
        delivery=_delivery(),
        websocket=websocket,
        save_blocks=save_blocks,
    )
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
        start_times=start_times,
        tool_context=MagicMock(),
    )
    await asyncio.sleep(0)

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
    save_blocks.assert_awaited_once()


def test_append_tool_images_adds_multimodal_user_message() -> None:
    messages: list[dict] = []

    append_tool_images(messages, ["https://cdn.test/image.png"])

    assert messages[0]["role"] == "user"
    assert messages[0]["content"][1]["image_url"]["url"].endswith("image.png")


@pytest.mark.asyncio
async def test_push_emit_payloads_and_form_keep_delivery_order() -> None:
    blocks: list[dict] = []
    websocket = MagicMock()
    websocket.send_to_task_or_user = AsyncMock()
    save_blocks = AsyncMock()

    await push_emit_payloads(
        payloads=[
            {
                "kind": "image",
                "url": "https://cdn.test/image.png",
                "name": "image.png",
            }
        ],
        content_blocks=blocks,
        delivery=_delivery(),
        websocket=websocket,
        save_blocks=save_blocks,
    )
    hint = await push_form_block(
        form={
            "type": "form",
            "form_id": "form-1",
            "form_type": "confirmation",
            "title": "确认",
            "fields": [],
        },
        content_blocks=blocks,
        delivery=_delivery(),
        websocket=websocket,
        save_blocks=save_blocks,
    )
    await asyncio.sleep(0)

    assert [block["type"] for block in blocks] == ["image", "form"]
    assert hint == "请在上方表单中确认信息后点击提交。"
    assert save_blocks.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["wecom", "web"])
async def test_compact_tool_context_uses_source_budget(source: str) -> None:
    settings = SimpleNamespace(
        context_tool_keep_turns=2,
        context_tool_token_budget=100,
        context_history_token_budget=200,
        context_max_tokens=300,
        context_loop_summary_trigger=250,
        context_web_keep_user_turns=3,
        context_web_compact_trigger=400,
        context_web_max_tokens=500,
        context_web_tool_token_budget=150,
        context_web_history_token_budget=350,
    )
    with (
        patch("core.config.get_settings", return_value=settings),
        patch(
            "services.handlers.context_compressor.compact_stale_tool_results"
        ) as compact_wecom,
        patch(
            "services.handlers.context_compressor.compact_stale_by_user_turns"
        ) as compact_web,
        patch(
            "services.handlers.context_compressor.enforce_tool_budget"
        ),
        patch(
            "services.handlers.context_compressor.enforce_history_budget_sync"
        ),
        patch(
            "services.handlers.context_compressor.compact_loop_with_summary",
            new_callable=AsyncMock,
        ),
    ):
        await compact_tool_context(
            messages=[],
            conversation_source=source,
            turn=3,
        )

    if source == "wecom":
        compact_wecom.assert_called_once()
        compact_web.assert_not_called()
    else:
        compact_web.assert_called_once()
        compact_wecom.assert_not_called()
