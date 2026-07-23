"""Runtime Context 当前 Run Compaction 测试。"""

from unittest.mock import AsyncMock, patch

import pytest

from services.agent.runtime.context import compact_context
from services.agent.runtime.context.compaction import _build_compaction_input


def _messages(turns: int, content_size: int = 2_500) -> list[dict]:
    messages = [
        {"role": "system", "content": "你是AI助手"},
        {"role": "user", "content": "帮我查一下"},
    ]
    for turn in range(turns):
        messages.extend([
            {
                "role": "assistant",
                "content": f"turn{turn}思考",
                "tool_calls": [{
                    "id": f"tc{turn}",
                    "type": "function",
                    "function": {"name": f"tool_{turn}", "arguments": "{}"},
                }],
            },
            {
                "role": "tool",
                "tool_call_id": f"tc{turn}",
                "content": f"工具{turn}结果 " + "x" * content_size,
            },
        ])
    return messages


@pytest.mark.asyncio
async def test_compaction_stays_idle_below_threshold() -> None:
    receipt = await compact_context(_messages(2), usable_input=50_000)
    assert receipt.outcome == "below_threshold"


@pytest.mark.asyncio
async def test_compaction_requires_a_stale_tool_prefix() -> None:
    receipt = await compact_context(
        _messages(2), usable_input=10, trigger_ratio=0.01,
    )
    assert receipt.outcome == "no_stale_prefix"


@pytest.mark.asyncio
async def test_compaction_replaces_stale_prefix() -> None:
    messages = _messages(5)
    original_length = len(messages)
    with patch(
        "services.agent.runtime.context.summary_model.call_summary_model",
        new=AsyncMock(return_value="摘要：库存100，订单50"),
    ):
        receipt = await compact_context(
            messages, usable_input=10, trigger_ratio=0.01,
        )

    assert receipt.outcome == "compacted"
    assert len(messages) < original_length
    assert sum(
        "[工具循环摘要]" in str(message.get("content", ""))
        for message in messages
    ) == 1


@pytest.mark.asyncio
async def test_compaction_preserves_messages_when_both_models_fail() -> None:
    messages = _messages(5)
    original_length = len(messages)
    with patch(
        "services.agent.runtime.context.summary_model.call_summary_model",
        new=AsyncMock(return_value=None),
    ):
        receipt = await compact_context(
            messages, usable_input=10, trigger_ratio=0.01,
        )

    assert receipt.outcome == "failed"
    assert len(messages) == original_length


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            {
                "role": "assistant",
                "content": "让我查一下",
                "tool_calls": [{"function": {"name": "stock"}}],
            },
            ("AI 调用工具: stock", "AI: 让我查一下"),
        ),
        (
            {"role": "tool", "content": "库存100件，金额¥5,000"},
            ("工具结果: 库存100件",),
        ),
        (
            {"role": "system", "content": "已识别编码: A→001"},
            ("系统: 已识别编码",),
        ),
    ],
)
def test_build_compaction_input_formats_supported_roles(
    message: dict,
    expected: tuple[str, ...],
) -> None:
    result = _build_compaction_input([message], [0])
    assert all(value in result for value in expected)


def test_build_compaction_input_bounds_long_content() -> None:
    result = _build_compaction_input(
        [{"role": "tool", "content": "x" * 500}],
        [0],
    )
    assert "..." in result
    assert len(result) < 500


def test_build_compaction_input_skips_long_system() -> None:
    messages = [{"role": "system", "content": "x" * 300}]
    assert _build_compaction_input(messages, [0]) == ""


def test_build_compaction_input_handles_empty_indices() -> None:
    assert _build_compaction_input(_messages(3), []) == ""


def test_build_compaction_input_keeps_tool_name_without_assistant_text() -> None:
    result = _build_compaction_input(
        [{
            "role": "assistant",
            "content": None,
            "tool_calls": [{"function": {"name": "erp_agent"}}],
        }],
        [0],
    )
    assert "AI 调用工具: erp_agent" in result
