import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.agent.tool_executor import ToolExecutor
from services.handlers.chat.stream_setup import _prepare_permission_and_tools
from services.prompt_builder.builder import BuildInput, PromptBuilder


@pytest.mark.asyncio
async def test_channel_prompt_omits_personal_dynamic_context() -> None:
    request_ctx = SimpleNamespace(
        for_prompt_injection=lambda: "CURRENT_TIME",
    )
    builder = PromptBuilder(BuildInput(
        user_id="sender-1",
        conversation_id="group-1",
        org_id="org-1",
        text_content="分析群文件",
        user_preferences="PRIVATE_PREFERENCE",
        user_location="PRIVATE_LOCATION",
        context_snapshot=SimpleNamespace(
            summary_prompt=None,
            history_messages=[],
        ),
        request_ctx=request_ctx,
        personal_context_allowed=False,
    ))

    result = await builder.build()
    serialized = json.dumps(result.messages, ensure_ascii=False)

    assert "PRIVATE_PREFERENCE" not in serialized
    assert "PRIVATE_LOCATION" not in serialized
    assert result.memory_injected is False
    assert result.persona_injected is False


def test_channel_tool_catalog_omits_personal_tools() -> None:
    tools = [
        {"type": "function", "function": {"name": "file_search"}},
        {
            "type": "function",
            "function": {"name": "get_conversation_context"},
        },
        {
            "type": "function",
            "function": {"name": "manage_scheduled_task"},
        },
    ]
    with patch(
        "config.chat_tools.get_tools_for_mode",
        return_value=tools,
    ):
        _, selected = _prepare_permission_and_tools(
            "auto", "org-1", personal_context_allowed=False,
        )

    names = [tool["function"]["name"] for tool in selected]
    assert names == [
        "artifact_get",
        "artifact_read",
        "artifact_search",
        "file_search",
    ]
    assert "get_conversation_context" not in names
    assert "manage_scheduled_task" not in names


def test_tool_executor_separates_actor_and_workspace_owner() -> None:
    executor = ToolExecutor(
        MagicMock(),
        user_id="sender-1",
        conversation_id="group-1",
        org_id="org-1",
        workspace_user_id="channels/wecom/channel-key",
    )

    assert executor.user_id == "sender-1"
    assert executor.workspace_user_id == "channels/wecom/channel-key"
    assert executor.has_handler("file_search")
    assert not executor.has_handler("get_conversation_context")
    assert not executor.has_handler("manage_scheduled_task")
