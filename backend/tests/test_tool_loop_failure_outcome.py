"""未生成最终结论时，工具循环必须保留失败原因给 headless 调用方。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.agent.loop_types import HookContext, LoopConfig, LoopStrategy
from services.agent.tool_loop_executor import ToolLoopExecutor


@pytest.mark.asyncio
async def test_finalize_retains_tool_failure_when_wrap_up_synthesis_fails():
    loop = ToolLoopExecutor(
        adapter=MagicMock(),
        executor=MagicMock(),
        all_tools=[],
        config=LoopConfig(max_turns=2, context_window=1000, tool_timeout=1),
        strategy=LoopStrategy(),
        hooks=[],
    )
    loop._emit_payloads = []
    context = HookContext(
        db=MagicMock(), user_id="u", org_id="o", conversation_id="c",
        task_id=None, request_ctx=MagicMock(), messages=[],
    )

    with patch(
        "services.agent.stop_policy.synthesize_wrap_up",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await loop._finalize(
            accumulated_text="❌ 沙盒内核启动失败,请稍后重试",
            total_tokens=42,
            turn=1,
            is_llm_synthesis=False,
            hook_ctx=context,
            stop_reason="wrap_up_failure",
            failure_message="❌ 沙盒内核启动失败,请稍后重试",
        )

    assert result.is_llm_synthesis is False
    assert result.failure_message == "❌ 沙盒内核启动失败,请稍后重试"
