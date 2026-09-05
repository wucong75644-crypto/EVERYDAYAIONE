"""ToolLoopExecutor 的多 chunk usage 聚合回归测试。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.agent.loop_types import LoopConfig, LoopStrategy
from services.agent.tool_loop_executor import ToolLoopExecutor


@pytest.mark.asyncio
async def test_stream_one_turn_aggregates_usage_from_all_chunks() -> None:
    class Adapter:
        async def stream_chat(self, **_kwargs):
            yield SimpleNamespace(
                content="a", tool_calls=None,
                prompt_tokens=2, completion_tokens=3,
            )
            yield SimpleNamespace(
                content="b", tool_calls=None,
                prompt_tokens=5, completion_tokens=7,
            )

    loop = ToolLoopExecutor(
        adapter=Adapter(),
        executor=MagicMock(),
        all_tools=[],
        config=LoopConfig(max_turns=2, context_window=1000, tool_timeout=1),
        strategy=LoopStrategy(),
        hooks=[],
    )

    calls, text, total, prompt_tokens, completion_tokens = (
        await loop._stream_one_turn([], [])
    )

    assert calls == {}
    assert text == "ab"
    assert prompt_tokens == 7
    assert completion_tokens == 10
    assert total == 17
