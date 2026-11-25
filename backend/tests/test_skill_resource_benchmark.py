"""Measured local file IO and optional real-provider, bounded synthetic acceptance."""
import json
import os
import statistics
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from services.skills.assets import AssetSourceDraft
from services.skills.renderer import digest
from services.skills.runtime import READ_SKILL_ASSET, READ_SKILL_ASSET_SCHEMA, skill_budget_options
from tests.test_skill_assets import attachment, document, publish
from tests.test_skill_runtime import Source, item, state, activate
from tests.test_skill_storage import storage  # noqa: F401
from tests.test_skill_runtime_actor import actor, prepared, batch


def source_for_real_files(storage, skill):
    candidate = item(revision=skill.revision)
    source = Source([candidate], body=skill.body)
    async def load(c, **kwargs):
        # Re-read and hash the actual immutable body on every load/replay.
        from services.skills.contracts import PublishRevision
        from tests.test_skill_storage import PACKAGE
        verified = storage.validate(PACKAGE, PublishRevision(revision=c.revision,
            content_sha256=skill.content_sha256, body_sha256=skill.body_sha256), verify_assets=False)
        return replace(verified, catalog_metadata=c.catalog_metadata)
    source.load = AsyncMock(side_effect=load)
    source.load_assets = AsyncMock(side_effect=lambda c, v, ids: storage.read_assets(v, ids))
    return source


async def test_measured_file_io_benchmark(storage):
    original = AssetSourceDraft.from_bytes('txt', b'original-file\n' * 40000)
    assets = [attachment(f'ref{i}', content=('REFERENCE %d.\n' % i) * 1800, source=original) for i in range(8)]
    body = '核心规则始终完整保留。' + '\n'.join('[[asset:ref%d]]' % i for i in range(8))
    eager = publish(storage, document(*assets, body=body), revision='v1')
    lazy = publish(storage, document(*(a.model_copy(update={'loading': 'on_demand'}) for a in assets), body=body), revision='v2')
    raw_read = storage._read
    metrics = {'reads': 0, 'bytes': 0}
    def read(*args, **kwargs):
        value = raw_read(*args, **kwargs)
        metrics['reads'] += 1
        metrics['bytes'] += len(value)
        return value
    storage._read = read
    report = {}
    for label, skill, requested in [('eager', eager, None), ('lazy_start', lazy, None), ('lazy_one_asset', lazy, 'ref0')]:
        samples = []
        for _ in range(12):
            metrics.update(reads=0, bytes=0)
            source = source_for_real_files(storage, skill)
            runtime = state(source, **skill_budget_options(SimpleNamespace()))
            start = time.perf_counter()
            await runtime.initialize()
            assert (await runtime.activate(activate()))['ok']
            if requested:
                assert (await runtime.read_asset(json.dumps({'skill_id': 'report', 'asset_id': requested})))['ok']
            samples.append((time.perf_counter() - start) * 1000)
        report[label] = {**metrics, 'median_ms': round(statistics.median(samples), 3),
                         'rendered_bytes': len(runtime.active['report'].rendered.encode())}
    assert report['eager']['reads'] == 17  # body plus 8 originals and 8 extracted texts
    assert report['lazy_start']['reads'] == 1
    assert report['lazy_one_asset']['reads'] == 4  # body revalidation + original + text
    assert report['lazy_start']['rendered_bytes'] < report['eager']['rendered_bytes'] / 10
    print('\nSKILL_RESOURCE_FILE_BENCHMARK ' + json.dumps(report))
    if os.getenv('SKILL_RESOURCE_BENCHMARK_OUTPUT'):
        Path(os.environ['SKILL_RESOURCE_BENCHMARK_OUTPUT']).write_text(json.dumps(report, indent=2))


@pytest.mark.skipif(os.getenv('SKILL_RESOURCE_LIVE') != '1', reason='explicit real-model resource acceptance')
async def test_real_model_resource_speed_and_execution_effect(storage):
    """At most six calls, no retries or business IO; preserve partial evidence on failure."""
    key = os.getenv('DASHSCOPE_API_KEY')
    if not key and os.getenv('SKILL_LIVE_EVAL_ENV_FILE'):
        from dotenv import dotenv_values
        key = dotenv_values(os.environ['SKILL_LIVE_EVAL_ENV_FILE']).get('DASHSCOPE_API_KEY')
    assert key, 'A test provider connection is required'
    model = os.getenv('SKILL_RESOURCE_LIVE_MODEL', 'qwen3.8-max')
    evidence = {'model': model, 'max_requests': 6, 'max_output_tokens_per_request': 512,
                'requests': [], 'cases': [], 'outcome': 'running'}
    output = Path(os.getenv('SKILL_RESOURCE_LIVE_OUTPUT', '/tmp/skill-resource-live.json'))
    body = ('核心规则：如果用户只问简单加法，直接只回答数字，不需要附件。'
            '用户询问验收口令时，必须读取附件原文后只回答口令，不能根据摘要猜。'
            '附件 [[asset:guide]] 是扩展参考资料，只有当前任务需要时读取。')
    resource = ('Reference note: standard package handling.\n' * 1050) + '\n验收口令：青岚-7294。'
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            async def infer(runtime, messages, tools):
                assert len(evidence['requests']) < 6, 'hard request budget exhausted'
                row = {'index': len(evidence['requests']) + 1, 'usage': {}, 'outcome': 'started'}
                evidence['requests'].append(row)  # Count before submission, including failures.
                payload = {'model': model, 'messages': runtime.model_messages(messages, tools),
                    'stream': True, 'stream_options': {'include_usage': True}, 'enable_thinking': False,
                    'temperature': 0, 'max_tokens': 512}
                if tools: payload['tools'] = tools
                start = time.perf_counter()
                text, calls = '', {}
                first = None
                async with client.stream('POST', 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
                    json=payload, headers={'Authorization': 'Bearer ' + key}) as response:
                    assert response.status_code == 200, f'Provider HTTP {response.status_code}'
                    async for line in response.aiter_lines():
                        if not line.startswith('data: '): continue
                        data = line[6:]
                        if data == '[DONE]': break
                        event = json.loads(data)
                        if event.get('usage'): row['usage'] = event['usage']
                        if not event.get('choices'): continue
                        delta = event['choices'][0].get('delta') or {}
                        if first is None and (delta.get('content') or delta.get('tool_calls')):
                            first = (time.perf_counter() - start) * 1000
                            row['first_event_kind'] = 'tool' if delta.get('tool_calls') else 'text'
                        text += delta.get('content') or ''
                        for part in delta.get('tool_calls') or []:
                            target = calls.setdefault(part['index'], {'id': '', 'name': '', 'arguments': ''})
                            target['id'] += part.get('id') or ''
                            target['name'] += (part.get('function') or {}).get('name') or ''
                            target['arguments'] += (part.get('function') or {}).get('arguments') or ''
                row.update(outcome='completed', first_event_ms=round(first, 3) if first is not None else None,
                           total_ms=round((time.perf_counter() - start) * 1000, 3))
                return text.strip(), list(calls.values())

            for index, (case, loading) in enumerate([('unused', 'eager'), ('unused', 'on_demand'),
                                                   ('needed', 'eager'), ('needed', 'on_demand')]):
                version = 'v' + str(index + 1)
                skill = publish(storage, document(attachment(content=resource, loading=loading), body=body), revision=version)
                source = source_for_real_files(storage, skill)
                runtime = state(source, **skill_budget_options(SimpleNamespace()))
                await runtime.initialize()
                start = time.perf_counter()
                assert (await runtime.activate(activate()))['ok']
                activation_ms = (time.perf_counter() - start) * 1000
                request_start = len(evidence['requests'])
                messages = [{'role': 'user', 'content': '只回答数字：2+3=?' if case == 'unused' else '附件中的验收口令是什么？只返回口令。'}]
                runtime.ensure_messages(messages)
                tools = [READ_SKILL_ASSET_SCHEMA] if runtime.has_deferred_assets else []
                start = time.perf_counter()
                answer, calls = await infer(runtime, messages, tools)
                if case == 'needed' and loading == 'on_demand':
                    assert len(calls) == 1 and calls[0]['name'] == READ_SKILL_ASSET, 'required resource was not requested'
                    args = json.loads(calls[0]['arguments'])
                    assert args == {'skill_id': 'report', 'asset_id': 'guide'}
                    actor_runtime, p = actor(), prepared()
                    actor_runtime.skill_runtime = runtime
                    p.messages = messages
                    await batch(actor_runtime, p, calls)
                    assert actor_runtime.skill_runtime.active['report'].loaded_asset_ids == ('guide',)
                    answer, calls = await infer(actor_runtime.skill_runtime, p.messages, tools)
                assert not calls, 'unexpected extra resource/tool round'
                assert answer == ('5' if case == 'unused' else '青岚-7294'), 'answer did not follow the method/reference'
                if case == 'unused' and loading == 'on_demand': source.load_assets.assert_not_awaited()
                evidence['cases'].append({'case': case, 'loading': loading, 'passed': True, 'answer': answer,
                    'activation_ms': round(activation_ms, 3), 'answer_total_ms': round((time.perf_counter() - start) * 1000, 3),
                    'request_indices': list(range(request_start + 1, len(evidence['requests']) + 1))})
        evidence['outcome'] = 'passed'
    except BaseException as error:
        evidence['outcome'] = 'failed'
        evidence['failure_type'] = type(error).__name__
        raise
    finally:
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        print('SKILL_RESOURCE_LIVE_REPORT', str(output), evidence['outcome'], 'requests', len(evidence['requests']))
