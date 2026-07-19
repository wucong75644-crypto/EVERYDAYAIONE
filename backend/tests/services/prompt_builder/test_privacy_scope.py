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


def test_active_summary_precedes_short_recent_history() -> None:
    messages = PromptBuilder._compose_messages(
        static_content="静态规则",
        session_stable_content="会话规则",
        turn_dynamic_content="本轮时间",
        summary_prompt="活动摘要",
        history_messages=[{"role": "user", "content": "最近问题"}],
        user_result=SimpleNamespace(
            workspace_system_block=None,
            attachments_system_block=None,
            user_message={"role": "user", "content": "当前问题"},
        ),
    )

    summary_index = next(
        index for index, message in enumerate(messages)
        if message.get("content") == "活动摘要"
    )
    history_index = next(
        index for index, message in enumerate(messages)
        if message.get("content") == "最近问题"
    )
    assert summary_index < history_index


def test_turn_dynamic_block_follows_stable_context() -> None:
    messages = PromptBuilder._compose_messages(
        static_content="静态规则",
        session_stable_content="会话规则",
        turn_dynamic_content="本轮时间",
        summary_prompt="活动摘要",
        history_messages=[{"role": "user", "content": "最近问题"}],
        data_context_prompt="证据目录",
        user_result=SimpleNamespace(
            workspace_system_block="当前工作区",
            attachments_system_block="当前附件",
            user_message={"role": "user", "content": "当前问题"},
        ),
    )
    contents = [message.get("content") for message in messages]

    assert contents.index("活动摘要") < contents.index("最近问题")
    assert contents.index("最近问题") < contents.index("证据目录")
    assert contents.index("证据目录") < contents.index("本轮时间")
    assert contents.index("本轮时间") < contents.index("当前工作区")
    assert contents.index("当前附件") < contents.index("当前问题")


def test_active_summary_without_recent_history_keeps_current_input_focus() -> None:
    messages = PromptBuilder._compose_messages(
        static_content="静态规则",
        session_stable_content="会话规则",
        turn_dynamic_content="本轮时间",
        summary_prompt="活动摘要",
        history_messages=[],
        user_result=SimpleNamespace(
            workspace_system_block=None,
            attachments_system_block=None,
            user_message={"role": "user", "content": "全新问题"},
        ),
    )

    assert any(message.get("content") == "活动摘要" for message in messages)
    assert any(
        "以用户最新一条消息为准" in str(message.get("content"))
        for message in messages
    )
    assert messages[-1] == {"role": "user", "content": "全新问题"}
