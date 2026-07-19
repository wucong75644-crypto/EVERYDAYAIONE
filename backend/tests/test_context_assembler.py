"""统一 Context Assembler 与结构化压缩测试。"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from services.agent.runtime.context.assembler import assemble_history
from services.agent.runtime.context.budget import (
    ContextBudget,
    derive_context_budget,
)


def _message(role, content, sequence, **extra):
    return {
        "role": role,
        "content": content,
        "_context_sequence": sequence,
        "_context_revision": 1,
        **extra,
    }


def _budget(*, soft=300, hard=1_200):
    return ContextBudget(
        context_window=4_000,
        reserved_output=500,
        safety_margin=500,
        usable_input=3_000,
        soft_compaction=soft,
        hard_compaction=hard,
        emergency_trim=1_500,
    )


@pytest.mark.asyncio
async def test_under_soft_limit_preserves_history_and_strips_internal_metadata():
    messages = [
        _message("user", "问题", 1000),
        _message("assistant", "回答", 1500),
    ]

    plan = await assemble_history(
        messages,
        derive_context_budget(10_000, 1_000),
    )

    assert plan.compaction is None
    assert plan.trimmed_refs == ()
    assert plan.messages == [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "回答"},
    ]


@pytest.mark.asyncio
async def test_compacts_stable_prefix_and_keeps_latest_two_user_turns():
    messages = [
        _message("user", "旧问题 " + "甲" * 600, 1000),
        _message("assistant", "旧回答 " + "乙" * 600, 1500),
        _message("user", "近期问题", 2000),
        _message(
            "assistant",
            None,
            2500,
            tool_calls=[{
                "id": "call-1",
                "type": "function",
                "function": {"name": "analy", "arguments": "{}"},
            }],
        ),
        _message(
            "tool",
            json.dumps({"artifact_id": "artifact-1"}),
            2501,
            tool_call_id="call-1",
        ),
        _message("assistant", "近期回答", 2502),
        _message("user", "最新追问", 3000),
        _message("assistant", "最新回答", 3500),
    ]
    summary = json.dumps({
        "goals": ["旧问题"],
        "constraints": [],
        "decisions": [],
        "facts": ["旧回答"],
        "artifact_refs": [],
        "failures": [],
        "unfinished": [],
    }, ensure_ascii=False)

    with patch(
        "services.context_summarizer._call_summary_model",
        new=AsyncMock(return_value=summary),
    ):
        plan = await assemble_history(
            messages,
            _budget(soft=100),
        )

    assert plan.compaction is not None
    assert plan.compaction["from_sequence"] == 1000
    assert plan.compaction["through_sequence"] == 1500
    assert plan.compaction["prompt_version"] == "unified-context-v1"
    assert plan.trimmed_refs == (1000, 1500)
    assert plan.messages[0]["role"] == "system"
    assert [message["content"] for message in plan.messages if message["role"] == "user"] == [
        "近期问题", "最新追问",
    ]
    assert any(message.get("tool_call_id") == "call-1" for message in plan.messages)
    assert all(
        not any(key.startswith("_context_") for key in message)
        for message in plan.messages
    )


@pytest.mark.asyncio
async def test_two_model_failures_use_deterministic_structured_fallback():
    messages = [
        _message("user", "旧目标 " + "甲" * 700, 1000),
        _message("assistant", "事实 artifact_id=artifact-9", 1500),
        _message("user", "近期", 2000),
        _message("assistant", "近期答复", 2500),
        _message("user", "最新", 3000),
    ]

    with patch(
        "services.context_summarizer._call_summary_model",
        new=AsyncMock(return_value=None),
    ) as summarize:
        plan = await assemble_history(
            messages,
            _budget(soft=100),
        )

    assert summarize.await_count == 2
    assert plan.compaction is not None
    assert plan.compaction["model"] == "deterministic"
    payload = plan.compaction["summary_payload"]
    assert payload["goals"]
    assert payload["artifact_refs"]


@pytest.mark.asyncio
async def test_required_recent_turns_over_hard_limit_raise_explicit_error():
    messages = [
        _message("user", "第一轮 " + "甲" * 3_000, 1000),
        _message("assistant", "第一答 " + "乙" * 3_000, 1500),
        _message("user", "第二轮 " + "丙" * 3_000, 2000),
    ]

    with pytest.raises(
        RuntimeError,
        match="CONTEXT_REQUIRED_BLOCKS_EXCEED_HARD_LIMIT",
    ):
        await assemble_history(
            messages,
            _budget(soft=300, hard=600),
        )
