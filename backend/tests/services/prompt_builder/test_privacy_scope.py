from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.prompt_builder.builder import BuildInput, PromptBuilder


@pytest.mark.asyncio
async def test_channel_scope_does_not_read_personal_memory() -> None:
    snapshot = SimpleNamespace(
        summary_prompt=None,
        history_messages=[],
    )
    builder = PromptBuilder(BuildInput(
        user_id="sender-1",
        conversation_id="group-1",
        org_id="org-1",
        text_content="分析群文件",
        context_snapshot=snapshot,
        personal_context_allowed=False,
    ))

    with patch(
        "services.memory.memory_service_v2.MemoryServiceV2",
        side_effect=AssertionError("personal memory must not be constructed"),
    ):
        memory, summary, history = await builder._parallel_fetch()

    assert memory is None
    assert summary is None
    assert history == []
    assert builder._persona_text == ""
