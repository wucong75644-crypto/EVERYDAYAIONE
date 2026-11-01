"""Keep the original ERP Agent entry while ERP write expansion is deferred."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.agent.agent_result import AgentResult
from services.agent.tool_executor import ToolExecutor
from services.tools.catalog import build_capability_catalog


@pytest.mark.asyncio
@pytest.mark.parametrize('channel', [False, True])
@pytest.mark.parametrize('argument', ['task', 'query'])
async def test_original_erp_agent_keeps_context_identity_budget_and_result(monkeypatch, channel, argument):
    result = AgentResult(summary='原ERP结论', data=[{'sku': '001', 'qty': 3}], source='erp_agent')
    execute = AsyncMock(return_value=result)
    agent = MagicMock(return_value=SimpleNamespace(execute=execute))
    monkeypatch.setattr('services.agent.erp_agent.ERPAgent', agent)
    db, request, budget = object(), object(), object()
    cancelled = asyncio.Event()
    workspace = 'channels/wecom/group' if channel else 'actor'
    executor = ToolExecutor(db, user_id='actor', conversation_id='conversation', org_id='org',
        workspace_user_id=workspace, request_ctx=request, execution_budget=budget,
        cancellation_event=cancelled, task_id='original-task', erp_step_timeout_sec=37,
        context_scope='channel' if channel else 'user', personal_context_allowed=not channel)
    executor._message_id = 'original-message'
    register = MagicMock()
    monkeypatch.setattr(executor, '_register_staging_files', register)
    assert executor.has_handler('erp_agent')
    returned = await executor._erp_agent({argument: ' 查询原库存 ', 'conversation_context': '原时间范围与筛选条件'})
    assert returned is result
    execute.assert_awaited_once_with('查询原库存', conversation_context='原时间范围与筛选条件')
    register.assert_called_once_with(result)
    assert agent.call_args.kwargs == dict(db=db, user_id='actor', conversation_id='conversation', org_id='org',
        task_id='original-task', message_id='original-message', request_ctx=request, budget=budget,
        cancellation_event=cancelled, workspace_user_id=workspace, step_timeout_sec=37)


def test_original_erp_agent_registration_remains_legacy_read_not_native_write():
    spec = build_capability_catalog().require('erp_agent')
    assert spec.handler_key == 'erp_agent' and spec.executor_type == 'legacy'
    assert spec.policy_rules.operation == 'read'
    assert spec.capability == 'platform.erp_agent'
