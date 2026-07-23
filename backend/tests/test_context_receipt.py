"""ContextReceipt 影子观测测试。"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from schemas.message import TextPart
from services.agent.runtime.context import (
    accumulate_provider_context_usage,
    build_context_receipt,
    prepare_provider_context_plan,
)
from services.agent.runtime.runtime_state import RuntimeState
from services.handlers.chat.stream_setup import (
    _record_context_receipt,
    prepare_chat_stream,
)


def _inputs() -> tuple[list[dict], list[dict]]:
    messages = [
        {"role": "system", "content": "固定规则"},
        {"role": "system", "content": "会话记忆"},
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


@pytest.mark.asyncio
async def test_prepare_chat_stream_carries_explicit_stable_prefix(
    monkeypatch,
):
    adapter = SimpleNamespace()
    handler = SimpleNamespace(
        org_id="org-1",
        db=MagicMock(),
        _personal_context_allowed=True,
        _context_stable_prefix_blocks=1,
        _data_context_snapshot=None,
        _extract_text_content=lambda _content: "你好",
        _build_llm_messages=AsyncMock(return_value=[
            {"role": "system", "content": "固定前缀"},
            {"role": "user", "content": "你好"},
        ]),
    )
    monkeypatch.setattr(
        "services.adapters.factory.create_chat_adapter",
        lambda *_args, **_kwargs: adapter,
    )
    monkeypatch.setattr(
        "services.handlers.chat.stream_setup._prepare_permission_and_tools",
        lambda *_args, **_kwargs: (SimpleNamespace(), []),
    )
    monkeypatch.setattr(
        "services.handlers.chat.stream_setup._prepare_provider_tools",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "services.handlers.chat.stream_setup._prepare_request_context",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "services.handlers.chat.stream_setup._prepare_budget",
        lambda: SimpleNamespace(),
    )

    prepared = await prepare_chat_stream(
        handler=handler,
        content=[TextPart(text="你好")],
        user_id="user-1",
        conversation_id="conv-1",
        task_id="task-1",
        model_id="model-1",
        permission_mode="auto",
        needs_google_search=False,
        params={},
        context_anchor=SimpleNamespace(base_revision=7),
    )

    assert prepared.runtime_state.stable_prefix_blocks == 1
    assert prepared.runtime_state.base_revision == 7


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
    assert first.message_count == 4
    assert first.tool_count == 1
    assert first.estimated_prompt_tokens > 0
    assert first.estimated_tool_tokens > 0
    assert first.epoch.stable_prefix_blocks == 2
    assert first.epoch.stable_prefix_hash
    assert first.cache_identity.stable_prefix_hash == (
        first.epoch.stable_prefix_hash
    )
    assert first.cache_identity.dynamic_suffix_hash
    assert first.cache_identity.tool_schema_hash
    assert first.cache_identity.route_hash
    serialized = str(first.to_log_fields())
    assert "固定规则" not in serialized
    assert "查询完成" not in serialized
    assert '"date":"昨天"' not in serialized


def test_context_epoch_changes_only_when_revision_or_stable_prefix_changes() -> None:
    messages, tools = _inputs()
    first = build_context_receipt(
        messages=messages,
        tools=tools,
        conversation_id="conv-1",
        task_id="task-1",
        model_id="model-1",
        base_revision=4,
    )
    messages.append({"role": "user", "content": "新追问"})
    appended = build_context_receipt(
        messages=messages,
        tools=tools,
        conversation_id="conv-1",
        task_id="task-1",
        model_id="model-1",
        base_revision=4,
    )
    revised = build_context_receipt(
        messages=messages,
        tools=tools,
        conversation_id="conv-1",
        task_id="task-1",
        model_id="model-1",
        base_revision=5,
    )

    assert first.epoch.epoch_id == appended.epoch.epoch_id
    assert first.cache_identity.dynamic_suffix_hash != (
        appended.cache_identity.dynamic_suffix_hash
    )
    assert first.epoch.epoch_id != revised.epoch.epoch_id


def test_cache_control_message_is_single_stable_prefix_block() -> None:
    messages, tools = _inputs()
    messages[:2] = [{
        "role": "system",
        "content": [
            {"type": "text", "text": "静态规则"},
            {
                "type": "text",
                "text": "会话记忆",
                "cache_control": {"type": "ephemeral"},
            },
        ],
    }]

    receipt = build_context_receipt(
        messages=messages,
        tools=tools,
        conversation_id="conv-1",
        task_id="task-1",
        model_id="model-1",
    )

    assert receipt.epoch.stable_prefix_blocks == 1


def test_non_system_second_message_is_not_part_of_stable_prefix() -> None:
    messages, tools = _inputs()
    messages.pop(1)

    receipt = build_context_receipt(
        messages=messages,
        tools=tools,
        conversation_id="conv-1",
        task_id="task-1",
        model_id="model-1",
    )

    assert receipt.epoch.stable_prefix_blocks == 1


def test_provider_receipt_uses_runtime_explicit_stable_prefix(
    monkeypatch,
) -> None:
    messages, tools = _inputs()
    captured: dict = {}

    def record(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(
        "services.handlers.chat.stream_setup._record_context_receipt",
        record,
    )
    runtime_state = RuntimeState(
        task_id="task-1",
        conversation_id="conv-1",
        model_id="model-1",
        stable_prefix_blocks=1,
    )

    with pytest.raises(RuntimeError, match="CONTEXT_RECEIPT_BUILD_FAILED"):
        prepare_provider_context_plan(
            runtime_state,
            messages=messages,
            tools=tools,
        )

    assert captured["stable_prefix_blocks"] == 1


def test_provider_usage_updates_only_latest_model_step() -> None:
    state = RuntimeState(context_receipts=[
        {"provider_tokens": 5, "provider_usage": {"prompt_tokens": 5}},
        {
            "provider_tokens": 0,
            "provider_usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_tokens": 0,
                "cache_creation_tokens": 0,
            },
        },
    ])
    chunk = SimpleNamespace(
        prompt_tokens=12,
        completion_tokens=4,
        cached_tokens=8,
        cache_creation_tokens=2,
    )

    accumulate_provider_context_usage(state, chunk)

    assert state.context_receipts[0]["provider_tokens"] == 5
    assert state.context_receipts[1]["provider_tokens"] == 12
    assert state.context_receipts[1]["provider_usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 4,
        "cached_tokens": 8,
        "cache_creation_tokens": 2,
    }


def test_provider_usage_ignores_missing_receipt_and_negative_tokens() -> None:
    chunk = SimpleNamespace(
        prompt_tokens=-1,
        completion_tokens=-2,
        cached_tokens=-3,
        cache_creation_tokens=-4,
    )
    accumulate_provider_context_usage(RuntimeState(), chunk)
    state = RuntimeState(context_receipts=[{
        "provider_tokens": 0,
        "provider_usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": 0,
            "cache_creation_tokens": 0,
        },
    }])

    accumulate_provider_context_usage(state, chunk)

    assert state.context_receipts[0]["provider_tokens"] == 0
    assert set(state.context_receipts[0]["provider_usage"].values()) == {0}


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
