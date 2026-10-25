"""Small opt-in provider evaluation; synthetic tasks only, no production DB/data."""
import asyncio
import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(os.getenv('KB_JOURNAL_LIVE') != '1', reason='explicit provider opt-in required')


def test_synthetic_proposals_match_actual_facts(monkeypatch):
    from dotenv import dotenv_values
    from core.config import get_settings
    from services.knowledge_workspace.ai_journal_extract import generate, validated_entries, _client
    settings = get_settings()
    values = dotenv_values(os.environ['KB_JOURNAL_ENV_FILE']) if os.getenv('KB_JOURNAL_ENV_FILE') else os.environ
    assert values.get('DASHSCOPE_API_KEY')
    monkeypatch.setattr(settings, 'dashscope_api_key', values['DASHSCOPE_API_KEY'])
    if values.get('DASHSCOPE_BASE_URL'):
        monkeypatch.setattr(settings, 'dashscope_base_url', values['DASHSCOPE_BASE_URL'])

    def event(user, output, tools=(), status='completed'):
        return dict(id=str(uuid4()), task_id=str(uuid4()), input_message_id=str(uuid4()), conversation_id=str(uuid4()),
                    input=user, output=output, tools=list(tools), task_status=status)

    cases = [
        event('以后报告先给结论再给明细，这是我固定的表达习惯。', '已按要求处理。'),
        event('请把这次成功的库存查询步骤整理为以后可以复用的方法。',
              '已完成查询：先确认仓库和SKU，再调用stock_query，最后核对口径并汇总。此步骤由AI自述已经验证。',
              [{'name': 'stock_query', 'status': 'success'}]),
        event('这一次把文字改成三句话就行，不是长期习惯。', '已改为三句话。'),
        event('继续处理这个开发任务的进展，目前数据库连接阻塞，请记录待办。', '连接失败，尚未修复，后续需要检查连接配置。',
              [{'name': 'code_execute', 'status': 'error'}], 'failed'),
    ]

    async def evaluate():
        try:
            outputs = []
            for item in cases:
                outputs.append(validated_entries(await generate(item, []), item))
            return outputs
        finally:
            await _client.close()
    outputs = asyncio.run(evaluate())
    assert any(r['category'] == 'preference' and r['verification'] == 'explicit' for r in outputs[0])
    assert any(r['category'] == 'method' and r['verification'] == 'candidate' for r in outputs[1])
    assert not any(r['verification'] == 'verified' for group in outputs for r in group)
    assert not any(r['category'] == 'preference' for r in outputs[2])
    assert any(r['category'] == 'work' and '本轮失败' in r['body'] for r in outputs[3])


def test_synthetic_evidence_sufficiency(monkeypatch):
    from dotenv import dotenv_values
    from core.config import get_settings
    from services.knowledge_workspace.evidence_assessment import assess
    settings = get_settings()
    values = dotenv_values(os.environ['KB_JOURNAL_ENV_FILE']) if os.getenv('KB_JOURNAL_ENV_FILE') else os.environ
    assert values.get('DASHSCOPE_API_KEY')
    monkeypatch.setattr(settings, 'dashscope_api_key', values['DASHSCOPE_API_KEY'])
    if values.get('DASHSCOPE_BASE_URL'):
        monkeypatch.setattr(settings, 'dashscope_base_url', values['DASHSCOPE_BASE_URL'])
    monkeypatch.setattr(settings, 'knowledge_evidence_assessment_enabled', True)
    def source(handle, body):
        return {'handle': handle, 'body': body, 'source': {'url': '/knowledge?object=' + handle}, 'rules': []}
    enough = [source('a', '合成品牌橙鹿售后规则：普通商品漏发先核对SKU及拆箱记录，再联系售后。食品已拆封不适用该流程。')]
    assert assess('橙鹿普通商品漏发先核对什么？食品拆封适用吗？', enough)['status'] == 'resolved'
    incomplete = [source('a', '合成品牌橙鹿普通商品漏发先核对SKU及拆箱记录，再联系售后。')]
    assert assess('橙鹿普通商品漏发先核对什么？食品拆封适用吗？', incomplete)['status'] == 'unresolved'
    conflict = [source('a', '合成品牌橙鹿现行有效规则：普通未拆封商品签收后7天内可以退货。'),
                source('b', '合成品牌橙鹿现行有效规则：同样的普通未拆封商品签收后只限3天内可以退货。两个条款同等生效且无先后优先级。')]
    assert assess('橙鹿普通未拆封商品签收后可以退货的期限是多少？', conflict)['status'] == 'conflict'
    assert assess('合成品牌蓝鲸预约需提前几天？', enough)['status'] == 'unresolved'


# Reuse the disposable real-role corpus fixtures; no production DB credentials.
from tests.test_knowledge_search_progress import (derived_db, cleanup_db, resize_db, manual_db, summary_db,
    geometry_db, groups_db, editing_db, entry_db, rev, directional_db, structure_db, current, visual_db,
    tools_db, payload, local_db, command, layout_db, before_db, create, toggle, theme_db, rich_db, start)


def test_automatic_scope_original_read_assessment_and_real_answer(derived_db, monkeypatch):
    import json
    import re
    from dotenv import dotenv_values
    from core.config import get_settings
    from tests.knowledge_corpus import clone
    from services.knowledge_workspace.chat import KnowledgeRun
    from services.knowledge_workspace.answer_plan import collect, message
    from services.knowledge_workspace.derivation import _client
    from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter
    settings = get_settings()
    values = dotenv_values(os.environ['KB_JOURNAL_ENV_FILE'])
    monkeypatch.setattr(settings, 'dashscope_api_key', values['DASHSCOPE_API_KEY'])
    if values.get('DASHSCOPE_BASE_URL'):
        monkeypatch.setattr(settings, 'dashscope_base_url', values['DASHSCOPE_BASE_URL'])
    monkeypatch.setattr(settings, 'knowledge_derivation_enabled', True)
    monkeypatch.setattr(settings, 'knowledge_semantic_enabled', True)
    monkeypatch.setattr(settings, 'knowledge_evidence_assessment_enabled', True)
    repo = derived_db[0](); scopes = clone(repo)
    container = next(c for c in repo.canvas()['containers'] if c['id'] == scopes['normal']['container_id'])
    _, mid, cid = start(derived_db[0], repo, [])
    question = f"根据{container['title']}项目：拼多多订单漏发要先核对什么？食品拆封能按此流程处理吗？请给来源。"
    run = KnowledgeRun.start(repo, conversation_id=cid, input_message_id=mid, default_scope=True, question=question)
    plan = collect(run, question)
    assert run.snapshot['automatic'] and run.snapshot['scope_reason'] == 'task_scope'
    assert plan['mode'] == 'answer' and plan['assessment']['status'] == 'resolved'
    serializer = DashScopeChatAdapter(api_key=values['DASHSCOPE_API_KEY'], model=settings.knowledge_derivation_model, builtin_tools_enabled=False)
    async def answer():
        try:
            client = await _client.get()
            body = serializer._request_body([{'role': 'user', 'content': question}, message(plan)], stream=False,
                reasoning_effort=None, thinking_mode=None, temperature=0.1, tools=None)
            response = await client.post('/chat/completions', json=body); response.raise_for_status()
            return response.json()['choices'][0]['message']['content']
        finally:
            await _client.close()
    text = run.normalize_citations(asyncio.run(answer()))
    assert all(word in text for word in ('订单', '照片', '食品'))
    assert any(word in text for word in ('不适用', '不能', '人工'))
    links = re.findall(r'\]\(([^)]+)\)', text)
    assert links and all(link.startswith('/knowledge?') for link in links)
    run.revalidate()
