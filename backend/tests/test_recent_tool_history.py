"""近期正常 Turn 的安全工具历史投影测试。"""

import json

import pytest

from tests.conftest import MockSupabaseClient


def _message(role: str, content: object) -> dict[str, object]:
    return {
        "role": role,
        "content": content,
        "status": "completed",
        "conversation_id": "conv1",
        "created_at": "2026-07-18T10:00:00Z",
        "generation_params": {},
        "context_revision": None,
        "message_kind": "conversation",
    }


@pytest.fixture
def chat_handler():
    from services.handlers.chat_handler import ChatHandler

    return ChatHandler(db=MockSupabaseClient())


@pytest.mark.asyncio
async def test_recent_three_user_turns_restore_completed_tool_pairs(chat_handler):
    """最近三个历史用户 Turn 恢复成功工具对，第四个仅保留正文。"""
    rows = [_message("user", "当前问题")]
    for turn in range(1, 5):
        rows.extend([
            _message("assistant", [
                {
                    "type": "tool_step",
                    "tool_name": "erp_agent",
                    "tool_call_id": f"call-{turn}",
                    "status": "completed",
                    "input": json.dumps({"turn": turn}),
                    "output": f"第{turn}轮结果",
                },
                {"type": "text", "text": f"第{turn}轮答复"},
            ]),
            _message("user", f"第{turn}轮问题"),
        ])
    chat_handler.db.set_table_data("messages", rows)

    result = await chat_handler._build_context_messages("conv1", "当前问题")

    restored_ids = {
        call["id"]
        for message in result
        for call in (message.get("tool_calls") or [])
    }
    assert restored_ids == {"call-1", "call-2", "call-3"}
    assert not any(message.get("tool_call_id") == "call-4" for message in result)
    assert "第4轮答复" in json.dumps(result, ensure_ascii=False)


@pytest.mark.asyncio
async def test_recent_tool_projection_excludes_unsafe_or_unclosed_results(
    chat_handler,
):
    """失败、空结果、超大结果和含凭证参数的工具步骤不回灌协议。"""
    blocks = [
        {
            "type": "tool_step",
            "tool_name": "erp_agent",
            "tool_call_id": "error-call",
            "status": "error",
            "output": "数据库堆栈",
        },
        {
            "type": "tool_step",
            "tool_name": "erp_agent",
            "tool_call_id": "empty-call",
            "status": "completed",
            "output": "",
        },
        {
            "type": "tool_step",
            "tool_name": "erp_agent",
            "tool_call_id": "large-call",
            "status": "completed",
            "output": "中" * 3000,
        },
        {
            "type": "tool_step",
            "tool_name": "erp_agent",
            "tool_call_id": "secret-call",
            "status": "completed",
            "input": '{"api_key":"private"}',
            "output": "不应恢复",
        },
        {
            "type": "tool_step",
            "tool_name": "erp_agent",
            "tool_call_id": "secret-output-call",
            "status": "completed",
            "output": '{"authorization":"Bearer private-value"}',
        },
        {"type": "text", "text": "已处理"},
    ]
    chat_handler.db.set_table_data("messages", [
        _message("user", "当前"),
        _message("assistant", blocks),
        _message("user", "上一问"),
    ])

    result = await chat_handler._build_context_messages("conv1", "当前")

    assert not any(message.get("tool_calls") for message in result)
    assert not any(message.get("role") == "tool" for message in result)
    assert result[-1]["content"] == "已处理"
