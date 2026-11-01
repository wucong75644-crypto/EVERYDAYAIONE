"""Knowledge integration boundaries; no model, embedding or business IO."""
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from schemas.message import TextPart
from services.knowledge_workspace.chat import KnowledgeRun
from services.knowledge_workspace import answer_plan
from tests.test_chat_execution_engine import _request


@pytest.mark.asyncio
@pytest.mark.parametrize('automatic,question,should_prepare', [
    (True, '这张图片是什么风格？', False),
    (True, '文案应该怎么写？', False),
    (True, '知识库的售后规则是什么？', True),
    (True, '公司制度有什么要求？', True),
    (False, '售后规则是什么？', True),
])
async def test_preparation_requires_source_binding_or_explicit_lookup(
        monkeypatch, automatic, question, should_prepare):
    request = replace(_request(), content=[TextPart(text=question)])
    repo = SimpleNamespace(scope=SimpleNamespace(actor_user_id=request.user_id, org_id=None))
    run = KnowledgeRun(repo, 'saved-snapshot', {'automatic': automatic})
    h = SimpleNamespace(_knowledge_run=run, org_id=None)
    p = SimpleNamespace(execution_context=SimpleNamespace(context_scope='user',
        authorized_tool_names=None), messages=[])
    collect = Mock(return_value={'mode': 'expand', 'items': []})
    monkeypatch.setattr(answer_plan, 'collect', collect)
    monkeypatch.setattr(answer_plan, 'named_source_plan', lambda *_: None)
    monkeypatch.setattr(answer_plan, 'restore_saved_plan', lambda *_: None)
    await answer_plan.prepare(h, p, request)
    assert collect.call_count == int(should_prepare)
    assert h._knowledge_run is run  # Retrieval preparation preserves the bound source.


@pytest.mark.asyncio
async def test_automatic_snapshot_replay_reuses_evidence_without_retrieval(monkeypatch):
    request = replace(_request(), content=[TextPart(text='售后规则是什么？')], replay_context={
        'messages': [{'role': 'user', 'content': '原问题'}]})
    run = KnowledgeRun(SimpleNamespace(scope=SimpleNamespace(actor_user_id=request.user_id,
        org_id=None)), 'saved', {'automatic': True})
    run.revalidate = Mock()
    packet = {'role': 'system', 'content': answer_plan.PREFIX + json.dumps({
        'mode': 'answer', 'reason': 'saved_result', 'items': [{'body': '原文'}]})}
    p = SimpleNamespace(execution_context=SimpleNamespace(context_scope='user',
        authorized_tool_names=None), messages=[packet])
    collect = Mock(side_effect=AssertionError('duplicate retrieval'))
    monkeypatch.setattr(answer_plan, 'collect', collect)
    await answer_plan.prepare(SimpleNamespace(_knowledge_run=run, org_id=None), p, request)
    assert p.messages == [packet] and p.knowledge_answer_plan['replay']
    run.revalidate.assert_called_once()
    collect.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome,status', [('empty', 'success'), ('timeout', 'timeout'),
    ('denied', 'error')])
async def test_original_tool_runtime_returns_knowledge_outcomes_without_owning_turn(outcome, status):
    from tests.tool_runtime_support import MockHandlerExecutor
    run = SimpleNamespace(revalidate=lambda: None)
    def execute(*_):
        if outcome == 'timeout':
            raise TimeoutError('knowledge lookup timed out')
        if outcome == 'denied':
            raise PermissionError('knowledge access revoked')
        return {'items': [], 'status': 'not_found_in_scope'}
    run.execute = execute
    executor = MockHandlerExecutor(agent_domain='general', knowledge_run=run)
    executor._handlers['knowledge_search'] = executor._knowledge_handler('search')
    result = await executor.tool_runtime.execute('knowledge_search', {'query': '规则'}, call_id='lookup')
    assert result.status == status
    content = result.model_content('chat')
    if outcome == 'empty':
        assert json.loads(content[0]['text'])['items'] == []
    else:
        assert isinstance(content, str)
        assert '工具执行失败' in content
    # Retrieval cannot settle the conversation, dispatch another tool, or retry.
    executor.handler.assert_not_awaited()
