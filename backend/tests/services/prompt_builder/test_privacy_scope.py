from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.prompt_builder.builder import BuildInput, PromptBuilder


@pytest.mark.asyncio
async def test_channel_scope_does_not_read_personal_memory() -> None:
    snapshot = SimpleNamespace(history_messages=[])
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
        memory, history = await builder._parallel_fetch()

    assert memory is None
    assert history == []
    assert builder._persona_text == ""


def test_compaction_precedes_short_recent_history() -> None:
    messages = PromptBuilder._compose_messages(
        static_content="静态规则",
        session_stable_content="会话规则",
        turn_dynamic_content="本轮时间",
        history_messages=[
            {"role": "system", "content": "结构化历史压缩"},
            {"role": "user", "content": "最近问题"},
        ],
        user_result=SimpleNamespace(
            workspace_system_block=None,
            attachments_system_block=None,
            user_message={"role": "user", "content": "当前问题"},
        ),
    )

    compaction_index = next(
        index for index, message in enumerate(messages)
        if message.get("content") == "结构化历史压缩"
    )
    history_index = next(
        index for index, message in enumerate(messages)
        if message.get("content") == "最近问题"
    )
    assert compaction_index < history_index


def test_turn_dynamic_block_follows_stable_context() -> None:
    messages = PromptBuilder._compose_messages(
        static_content="静态规则",
        session_stable_content="会话规则",
        turn_dynamic_content="本轮时间",
        history_messages=[{"role": "user", "content": "最近问题"}],
        data_context_prompt="证据目录",
        user_result=SimpleNamespace(
            workspace_system_block="当前工作区",
            attachments_system_block="当前附件",
            user_message={"role": "user", "content": "当前问题"},
        ),
    )
    contents = [message.get("content") for message in messages]

    assert contents.index("最近问题") < contents.index("证据目录")
    assert contents.index("证据目录") < contents.index("本轮时间")
    assert contents.index("本轮时间") < contents.index("当前工作区")
    assert contents.index("当前附件") < contents.index("当前问题")


def test_empty_history_keeps_current_input_focus() -> None:
    messages = PromptBuilder._compose_messages(
        static_content="静态规则",
        session_stable_content="会话规则",
        turn_dynamic_content="本轮时间",
        history_messages=[],
        user_result=SimpleNamespace(
            workspace_system_block=None,
            attachments_system_block=None,
            user_message={"role": "user", "content": "全新问题"},
        ),
    )

    assert not any(
        "以用户最新一条消息为准" in str(message.get("content"))
        for message in messages
    )
    assert messages[-1] == {"role": "user", "content": "全新问题"}
