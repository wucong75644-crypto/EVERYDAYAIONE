import pytest
from services.agent.agent_reach.web_links import prepare_links, resolve_link_args
from services.agent.agent_reach.contracts import ReachError, ReachRequest

SHOP = 'https://www.xiaohongshu.com/shop/65263c56796c030001e298d4?xsec_token=abc%2Bdef=&xsec_source=pc_user'

def prepare(text, **kwargs):
    return prepare_links([{'role': 'user', 'content': text}], org_id='org', user_id='user', conversation_id=kwargs.get('conversation_id', 'chat'))

def test_customer_request_resolves_exact_original_and_pages():
    messages, refs = prepare('帮我查这家店的全部商品和销量：' + SHOP)
    ref, = refs
    for page in (1, 2, 56):
        args = resolve_link_args({'action': 'read', 'platform': 'xiaohongshu', 'task': '查全部商品和销量', 'url_ref': ref, 'page': page}, refs)
        assert ReachRequest.model_validate(args).url == SHOP
        assert args['page'] == page
    assert messages[0]['content'].endswith(SHOP)
    replay, restored = prepare_links(messages, org_id='org', user_id='user', conversation_id='chat')
    assert restored == refs
    assert replay == messages

def test_scope_unknown_and_conflicting_refs_fail_closed():
    _, refs = prepare(SHOP)
    _, other = prepare(SHOP, conversation_id='other')
    ref, = refs
    with pytest.raises(ReachError, match='当前会话'):
        resolve_link_args({'url_ref': ref}, other)
    with pytest.raises(ReachError, match='同时提供'):
        resolve_link_args({'url_ref': ref, 'url': '/shop/123'}, refs)

def test_only_user_text_registers_and_markdown_keeps_query():
    messages = [{'role': 'assistant', 'content': 'https://evil.example/a'},
                {'role': 'tool', 'content': 'https://evil.example/b'},
                {'role': 'user', 'content': [{'type': 'text', 'text': f'看看[店铺]({SHOP})。'}]}]
    _, refs = prepare_links(messages, org_id='o', user_id='u', conversation_id='c')
    assert list(refs.values()) == [SHOP]

def test_reference_survives_frozen_tool_policy_arguments():
    from services.tools import ToolCall, ToolContext, ToolPolicy, build_legacy_catalog
    _, refs = prepare(SHOP)
    ref, = refs
    context = ToolContext(actor_user_id='user', workspace_owner_id='user', org_id='org',
        context_scope='user', personal_context_allowed=True, agent_domain='general',
        permission_mode='auto', execution_mode='interactive', conversation_id='chat',
        web_link_refs=refs, feature_flags={'agent_reach_enabled': True})
    args = {'task': '查全店商品和销量', 'action': 'read', 'url_ref': ref}
    policy = ToolPolicy(build_legacy_catalog())
    decision = policy.plan_batches([ToolCall('c', 'agent_reach', args)], context)[0][0].decision
    assert decision.outcome == 'allow'
    assert policy.decide('agent_reach', context, {**args, 'url_ref': 'wref1_' + '0'*64}).outcome == 'deny'

@pytest.mark.asyncio
async def test_handler_dispatches_original_url_without_model_copy(monkeypatch):
    from types import SimpleNamespace
    from core import config
    from services.agent.agent_reach import handler
    from services.agent.agent_result import AgentResult
    original = 'https://example.com/products?q=a%2Bb&sort=sales'
    _, refs = prepare(original)
    ref, = refs
    monkeypatch.setattr(config, 'get_settings', lambda: SimpleNamespace(
        agent_reach_enabled=True, agent_reach_timeout_seconds=60, agent_reach_platforms='web'))
    captured = []
    async def execute(request, *args, **kwargs):
        captured.append(request.url)
        return AgentResult(summary='fixture result')
    monkeypatch.setattr(handler, 'execute', execute)
    executor = SimpleNamespace(execution_budget=None, web_link_refs=refs, org_id=None)
    result = await handler.execute_reach(executor, {'task':'查商品信息', 'action':'read', 'url_ref':ref})
    assert result.status == 'success'
    assert captured == [original]
    result = await handler.execute_reach(executor, {'task':'查商品信息', 'action':'read', 'url_ref':'wref1_'+'0'*64})
    assert result.error_message == 'INVALID_URL_REFERENCE'
    assert captured == [original]

def test_continuation_keeps_reference_in_metadata_and_model_text():
    import json
    from services.agent.agent_result import AgentResult
    from services.agent.agent_reach.web_links import bind_result_reference
    pagination = {'next_page_request': {'action':'read','url':SHOP,'page':2}}
    result = AgentResult(summary='续读信息：'+json.dumps(pagination, ensure_ascii=False), metadata={'pagination':pagination})
    bind_result_reference(result, 'wref1_test')
    assert pagination['next_page_request'] == {'action':'read','url_ref':'wref1_test','page':2}
    assert 'wref1_test' in result.summary
    assert SHOP not in result.summary

def test_balanced_parentheses_in_original_url_are_preserved():
    original='https://example.com/product/(edition)?filter=(new)'
    _, refs=prepare(f'帮我查这个商品：[链接]({original})。')
    assert list(refs.values()) == [original]
