"""07 representative groups through the real three execution entrypoints."""
from unittest.mock import AsyncMock
import asyncio

import pytest

from services.agent.agent_result import AgentResult
from tests.tool_runtime_support import MockHandlerExecutor
from tests.tool_runtime_support import IdentityDB
from services.tool_executor import ToolExecutor
from tests.test_tool_production_integration import setup, invoke, tc


@pytest.mark.parametrize('entry', ['legacy', 'chat', 'loop'])
@pytest.mark.parametrize('name,args,domain', [
    ('erp_trade_query', {'action': 'order_list'}, 'erp'),
    ('file_search', {}, 'general'),
    ('code_execute', {'code': 'print(1)', 'description': 'contract'}, 'general'),
    ('generate_image', {'prompt': 'contract'}, 'general'),
    ('manage_scheduled_task', {'action': 'list'}, 'general'),
])
async def test_each_migrated_group_reaches_original_handler_once(setup, monkeypatch, entry, name, args, domain):
    executor = MockHandlerExecutor(agent_domain=domain)
    executor.handler.return_value = AgentResult(summary='group contract', status='success')
    dispatch = AsyncMock(wraps=executor.tool_runtime.service.dispatcher.dispatch)
    monkeypatch.setattr(executor.tool_runtime.service.dispatcher, 'dispatch', dispatch)
    result = await invoke(entry, executor, [tc(name, args)], monkeypatch)
    assert result
    assert dispatch.await_count == executor.handler.await_count == 1


async def test_same_call_id_in_two_users_has_independent_execution_state(setup):
    first = MockHandlerExecutor(agent_domain='general', user_id='user-a')
    second = MockHandlerExecutor(agent_domain='general', user_id='user-b')
    first.handler.return_value = AgentResult(summary='a', status='success')
    second.handler.return_value = AgentResult(summary='b', status='success')
    outputs = await asyncio.gather(
        first.execute('web_search', {'query': 'x'}, call_id='same-call'),
        second.execute('web_search', {'query': 'x'}, call_id='same-call'),
    )
    assert [result.summary for result in outputs] == ['a', 'b']
    assert first.handler.await_count == second.handler.await_count == 1
    assert first.tool_runtime.registry is not second.tool_runtime.registry
    assert first.tool_runtime.context().actor_user_id == 'user-a'
    assert second.tool_runtime.context().actor_user_id == 'user-b'


@pytest.mark.parametrize('name,method', [('erp_agent', 'execute'), ('erp_analyze', 'analyze')])
@pytest.mark.parametrize('args,expected', [({'query': 'legacy'}, 'legacy'),
                                        ({'task': 'current', 'query': 'legacy'}, 'current')])
async def test_erp_task_aliases_use_the_original_handler(setup, monkeypatch, name, method, args, expected):
    from services.agent.erp_agent import ERPAgent
    business = AsyncMock(return_value=AgentResult(summary='alias', status='success'))
    monkeypatch.setattr(ERPAgent, method, business)
    executor = ToolExecutor(IdentityDB(), 'u1', 'c1', 'o1')
    result = await executor.execute(name, args)
    assert result.summary == 'alias'
    business.assert_awaited_once_with(expected, conversation_context='')


@pytest.mark.parametrize('args,expected', [({'fields': ['shop_name']}, ['shop_name']),
                                        ({'fields': ['shop_name'], 'extra_fields': ['order_no']}, ['order_no'])])
async def test_local_field_alias_uses_original_dispatch(setup, monkeypatch, args, expected):
    from services.kuaimai.erp_unified_query import UnifiedQueryEngine
    business = AsyncMock(return_value=AgentResult(summary='fields', status='success'))
    monkeypatch.setattr(UnifiedQueryEngine, 'execute', business)
    executor = ToolExecutor(IdentityDB(), 'u1', 'c1', 'o1', agent_domain='erp')
    result = await executor.execute('local_data', {'doc_type': 'order', 'filters': [], **args})
    assert result.summary == 'fields'
    assert business.await_count == 1
    assert business.call_args.kwargs['extra_fields'] == expected
