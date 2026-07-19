"""ContextReceipt 影子观测测试。"""

from __future__ import annotations

import copy

from services.agent.runtime.context import build_context_receipt
from services.handlers.chat.stream_setup import _record_context_receipt


def _inputs() -> tuple[list[dict], list[dict]]:
    messages = [
        {"role": "system", "content": "固定规则"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "erp_query", "arguments": '{"date":"昨天"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "查询完成"},
    ]
    tools = [{
        "type": "function",
        "function": {
            "name": "erp_query",
            "description": "查询 ERP",
            "parameters": {"type": "object"},
        },
    }]
    return messages, tools


def test_build_context_receipt_is_deterministic_and_contains_no_content() -> None:
    messages, tools = _inputs()

    first = build_context_receipt(
        messages=messages,
        tools=tools,
        conversation_id="conv-1",
        task_id="task-1",
        model_id="model-1",
    )
    second = build_context_receipt(
        messages=messages,
        tools=tools,
        conversation_id="conv-1",
        task_id="task-1",
        model_id="model-1",
    )

    assert first == second
    assert first.message_count == 3
    assert first.tool_count == 1
    assert first.estimated_prompt_tokens > 0
    assert first.estimated_tool_tokens > 0
    serialized = str(first.to_log_fields())
    assert "固定规则" not in serialized
    assert "查询完成" not in serialized
    assert '"date":"昨天"' not in serialized


def test_record_context_receipt_does_not_mutate_provider_inputs() -> None:
    messages, tools = _inputs()
    messages_before = copy.deepcopy(messages)
    tools_before = copy.deepcopy(tools)

    _record_context_receipt(
        messages=messages,
        tools=tools,
        conversation_id="conv-1",
        task_id="task-1",
        model_id="model-1",
    )

    assert messages == messages_before
    assert tools == tools_before


def test_record_context_receipt_failure_does_not_block_request(
    monkeypatch,
) -> None:
    messages, tools = _inputs()

    def fail_build(**_kwargs):
        raise RuntimeError("observer unavailable")

    monkeypatch.setattr(
        "services.agent.runtime.context.build_context_receipt",
        fail_build,
    )

    _record_context_receipt(
        messages=messages,
        tools=tools,
        conversation_id="conv-1",
        task_id="task-1",
        model_id="model-1",
    )
