"""Opt-in synthetic provider checks; never execute a business tool or read user data.

Run with SKILL_LIVE_EVAL=1 and DASHSCOPE_API_KEY (or SKILL_LIVE_EVAL_ENV_FILE).
These checks cost provider tokens and assess samples, not guaranteed model behavior.
"""
import json
import os
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest

from services.prompt_builder.layers.static_layer import StaticLayer
from services.skills.selection import SkillSelection
from services.tools import ToolPolicy, build_legacy_catalog
from tests.test_skill_assets import attachment, document, publish
from tests.test_skill_runtime import Source, item, state
from tests.test_skill_storage import storage  # noqa: F401
from tests.test_tool_policy import context

pytestmark = pytest.mark.skipif(os.getenv('SKILL_LIVE_EVAL') != '1', reason='opt-in provider acceptance')

QUERY_METHOD = ('先确认开始日期和结束日期，缺少时只询问缺失的日期。日期齐全时，'
                '使用 file_search 查找该期间的项目进展文件；根据返回内容总结完成项。'
                '没有可用工具时说明限制，请用户提供数据，不得声称已经查询。')
CASES = [
    ('translation', '按附件词汇表 [[asset:guide]] 将用户提供的英文翻译成中文，只输出译文。',
     '本词汇表约定：payment term 翻译为结算窗口，supplier 翻译为供货方。',
     'Translate: The supplier confirmed the payment term.', [], 'auto'),
    ('report', '使用附件 [[asset:guide]] 整理用户提供的工作记录，不添加不存在的进度。',
     '输出只含两个标题：本周进展、下一步。将已完成项放在前者，将计划放在后者。',
     '已完成登录改造；下一步补充监控。', [], 'auto'),
    ('query', '按附件中的方法处理本轮请求：[[asset:guide]]。', QUERY_METHOD,
     '请分析 2026-09-01 至 2026-09-07 的项目进展。', ['file_search'], 'ask'),
    ('missing', '按附件中的方法处理本轮请求：[[asset:guide]]。', QUERY_METHOD,
     '分析一下。', ['file_search'], 'auto'),
    ('unavailable', '按附件中的方法处理本轮请求：[[asset:guide]]。', QUERY_METHOD,
     '请分析 2026-09-01 至 2026-09-07 的项目进展。', [], 'ask'),
    ('plan', '按附件中的方法处理本轮请求：[[asset:guide]]。',
     '使用 erp_agent 查询指定期间的订单数据，再按结果总结。没有真实结果不能编造。',
     '请规划如何分析 2026-09-01 至 2026-09-07 的订单。', ['erp_agent'], 'plan'),
]


@pytest.mark.parametrize('case,body,resource,user,tools,mode', CASES, ids=[c[0] for c in CASES])
async def test_provider_follows_selected_skill_instead_of_irrelevant_history(storage, case, body, resource, user, tools, mode):
    key = os.getenv('DASHSCOPE_API_KEY')
    if not key and os.getenv('SKILL_LIVE_EVAL_ENV_FILE'):
        from dotenv import dotenv_values
        key = dotenv_values(os.environ['SKILL_LIVE_EVAL_ENV_FILE']).get('DASHSCOPE_API_KEY')
    assert key, 'Provider key must be configured for the opt-in evaluation'
    skill = publish(storage, document(attachment(content=resource), body=body))
    candidate = item(tools=(), tool_policy='platform', model_selectable=False, name='当前任务方法')
    source = Source([candidate], body=body)
    source.load = AsyncMock(return_value=replace(skill, catalog_metadata=candidate.catalog_metadata))
    source.load_assets = AsyncMock(side_effect=lambda c, validated, ids: storage.read_assets(validated, ids))
    runtime = state(source, platform_tool_names=set(tools), authorized_tool_names=set(tools))
    selection = SkillSelection(skill_id='report', revision='v1')
    await runtime.initialize(selection=selection)
    assert (await runtime.activate_manual(selection))['ok']
    messages = [{'role': 'system', 'content': StaticLayer.render() + f'\n当前 permission_mode={mode}。'},
                {'role': 'user', 'content': '此前请比较两个电商平台的退款率。'},
                {'role': 'assistant', 'content': '之前的比较已经完成。'},
                {'role': 'user', 'content': user}]
    runtime.ensure_messages(messages)
    registry = build_legacy_catalog()
    policy = ToolPolicy(registry)
    host = context(permission_mode=mode, authorized_tool_names=runtime.effective_allowed_tool_names)
    schemas = [registry.require(name).to_schema() for name in tools
               if registry.check_access(name, host, policy=policy).allowed]
    usage = {'prompt_tokens': 0, 'completion_tokens': 0}

    async def request(client):
        payload = {'model': 'qwen3.5-plus', 'messages': runtime.model_messages(messages, schemas), 'stream': False,
                   'enable_thinking': False, 'temperature': 0, 'max_tokens': 1200}
        if schemas:
            payload['tools'] = schemas
        response = await client.post('https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
            json=payload, headers={'Authorization': 'Bearer ' + key})
        assert response.status_code == 200, f'Provider HTTP status {response.status_code}'
        result = response.json()
        for name in usage:
            usage[name] += result.get('usage', {}).get(name, 0)
        return result['choices'][0]['message']

    async with httpx.AsyncClient(timeout=55) as client:
        first = await request(client)
        if case == 'query':
            calls = first.get('tool_calls') or []
            assert len(calls) == 1 and calls[0]['function']['name'] == 'file_search', first
            arguments = json.loads(calls[0]['function']['arguments'])
            assert policy.decide('file_search', host, arguments).outcome == 'allow'
            # Synthetic tool result only: the provider cannot invoke platform IO.
            messages.extend([first, {'role': 'tool', 'tool_call_id': calls[0]['id'],
                'content': json.dumps({'files': [{'name': '项目周报.txt',
                    'content': '合成验收数据：2026-09-01 至 2026-09-07，完成登录改造。'}]}, ensure_ascii=False)}])
            answer = await request(client)
            assert not answer.get('tool_calls'), answer
            assert '登录' in (answer.get('content') or ''), answer
        else:
            answer = first
            assert not answer.get('tool_calls'), answer
        text = answer.get('content') or ''
        assert text, answer
        assert not any(marker in text for marker in ('<tool_code>', '<tool>', '<function=', '<invoke')), answer
        # Refund metrics can legitimately appear in a proposed order-analysis
        # plan; in the other domains they indicate unrelated history taking over.
        if case != 'plan':
            assert '退款率' not in text, answer
        if case == 'translation':
            assert '结算窗口' in text and '供货方' in text, answer
        elif case == 'report':
            assert all(term in text for term in ('本周进展', '下一步', '登录', '监控')), answer
            assert len([line for line in text.splitlines() if line.strip()]) == 4, answer
        elif case == 'missing':
            assert any(term in text for term in ('日期', '时间', '期间')), answer
        elif case == 'unavailable':
            assert any(term in text for term in ('无法', '不能', '未提供', '没有', '不可用', '未开放')), answer
        elif case == 'plan':
            assert '计划' in text or '规划' in text, answer
            assert '查询到' not in text, answer
        print(json.dumps({'case': case, 'model': 'qwen3.5-plus', 'usage': usage,
                          'answer': text}, ensure_ascii=False))
