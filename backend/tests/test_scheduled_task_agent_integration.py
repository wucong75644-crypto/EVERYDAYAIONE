"""回归：旧 ScheduledTaskAgent 已封存，不能重新获得执行权。"""

import inspect
from unittest.mock import MagicMock

import pytest

from services.agent.scheduled_task_agent import (
    LEGACY_SCHEDULED_TASK_OWNER_DISABLED,
    LegacyScheduledTaskOwnerDisabled,
    ScheduledTaskAgent,
)


def make_task() -> dict:
    return {
        "id": "legacy-task",
        "user_id": "user-1",
        "org_id": "org-1",
        "prompt": "执行历史任务",
    }


@pytest.mark.asyncio
async def test_legacy_agent_fails_closed_before_execution():
    agent = ScheduledTaskAgent(MagicMock(), make_task())

    with pytest.raises(LegacyScheduledTaskOwnerDisabled) as exc_info:
        await agent.execute()

    assert exc_info.value.code == LEGACY_SCHEDULED_TASK_OWNER_DISABLED


def test_legacy_agent_has_no_provider_or_toolloop_construction():
    source = inspect.getsource(ScheduledTaskAgent)
    assert "create_chat_adapter(" not in source
    assert "ToolExecutor(" not in source
    assert "ToolLoopExecutor(" not in source


@pytest.mark.asyncio
async def test_legacy_side_effect_helpers_fail_closed():
    agent = ScheduledTaskAgent(MagicMock(), make_task())

    with pytest.raises(LegacyScheduledTaskOwnerDisabled):
        await agent._prepare_template()
    with pytest.raises(LegacyScheduledTaskOwnerDisabled):
        await agent._generate_summary("result", MagicMock())
    with pytest.raises(LegacyScheduledTaskOwnerDisabled):
        agent._build_tool_loop(None, None, [])
