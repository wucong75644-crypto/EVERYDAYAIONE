"""Opt-in real customer HTTP/Actor/model decisions; no paid image generation.

Native requests are inspected in an isolated database. Images remain queued:
this proves selection/admission, not supplier completion or visual quality.
"""
import hashlib
import json
import os
from pathlib import Path
import time
import traceback

import psycopg
import pytest

from tests.manual_skill_formal_entry import formal_entry_acceptance
from tests.manual_two_bug_customer_acceptance import wire_errors

pytest_plugins = ['tests.test_content_objects_postgres']
pytestmark = pytest.mark.skipif(os.environ.get('RUN_TURN_CATALOG_ACCEPTANCE') != '1', reason='Explicit real model acceptance')


@pytest.mark.asyncio
@pytest.mark.parametrize('task_db', ['everydayai'], indirect=True, scope='module')
async def test_real_customer_task_switch_and_explicit_history(content_db, monkeypatch):
    async def suite(env):
        from schemas.message import TextPart
        from services.task_execution import content_objects as co
        from services.task_execution.contracts import TaskState
        assert os.environ['SKILL_SUPPLIER_MAX_POSTS'] == '0'
        f = env['formal']['f']; root = env['root']; results = []

        def persist():
            p = root/'turn-customer-results.json'
            p.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str)); p.chmod(0o600)

        async def turn(name, text, *, uploads=(), images=0, base=None, shown=None, saved=False, recipe=None):
            result = {'case': name, 'user_text': text, 'status': 'running'}
            results.append(result); persist(); offset = len(env['wire']); start = time.monotonic()
            try:
                row = await env['formal']['run'](f, [TextPart(text=text), *uploads])
                result.update(row)
                if saved:
                    state = row['checkpoint'].get('_task_execution')
                    assert state and co.catalog(TaskState.model_validate(state)), 'No persisted executable draft'
                with psycopg.connect(content_db) as c:
                    children = c.execute("SELECT id,request_params->'_media_request_v1' FROM tasks WHERE type='image' AND request_params#>>'{_media_request_v1,origin,parent_task_id}'=%s ORDER BY created_at", (f['task'],)).fetchall()
                result['image_requests'] = [{'task_id': str(t), 'snapshot': s} for t, s in children]
                assert len(children) == images, f'Expected {images} new requests; got {len(children)}'
                if base:
                    assert all(s['references'][0]['content_sha256'] == base for _, s in children), 'Wrong edit base'
                if recipe:
                    assert len(children) == 1
                    snapshot = children[0][1]
                    assert snapshot['prompt'].endswith(recipe['body']), 'Saved prompt was rewritten'
                    for key in ('aspect_ratio', 'resolution'):
                        if key in recipe['options']:
                            assert snapshot[key] == recipe['options'][key], f'Saved {key} changed'
                    result['saved_recipe_verified'] = True
                if shown:
                    content = row['message']['content']
                    content = json.loads(content) if isinstance(content, str) else content
                    assert any(p.get('type') == 'image' and p.get('source_message_id') == shown for p in content), 'Only prose, no requested image delivered'
                result['status'] = 'passed'
                return result
            except Exception as error:
                result.update(status='failed', error=str(error), trace=traceback.format_exc())
                return result
            finally:
                result['wire_errors'] = wire_errors(env['wire'][offset:])
                parts = result.get('message', {}).get('content', [])
                parts = json.loads(parts) if isinstance(parts, str) else parts
                result['terminal_tool_errors'] = [p for p in parts if p.get('type') == 'tool_step' and p.get('status') == 'error']
                result['seconds'] = round(time.monotonic()-start, 3)
                persist(); print(json.dumps({k: result[k] for k in ('case','status','seconds')}, ensure_ascii=False), flush=True)

        capture = Path(os.environ['SKILL_FAILURE_CAPTURE_ROOT'])
        data = json.loads((capture/'fixed-live-image-off-3.json').read_text())
        refs = sorted(data['native'][0]['recipes'][0]['resolved'], key=lambda r: r['content_index'])
        files = [next((capture/'workspace').rglob(Path(r['workspace_path']).name)) for r in refs]
        assert all(hashlib.sha256(p.read_bytes()).hexdigest() == r['content_sha256'] for p, r in zip(files, refs))
        a = await env['upload'](f, files[0], '客户验收/A原图.png')
        b = await env['upload'](f, files[1], '客户验收/B原图.jpg')
        first = await turn('a_saved_draft_only', '把这张的背景换成暖米白色，商品、文字、摆放都保留。请保存为一个稍后可执行的方案，只含一张图的完整编辑提示词，保留这张原图作为执行素材，先不要生成。', uploads=[a], saved=True)
        if first['status'] != 'passed': pytest.fail('Baseline draft failed; see private evidence')
        a_message = f['message']
        state = first['checkpoint'].get('_task_execution')
        assert state and co.catalog(TaskState.model_validate(state)), 'No saved A draft'
        discussion = await turn('b_discussion_only', '换个新任务。这张本子上的大五角星改成同位置的细线月牙轮廓，不加其他元素，原来的文字、尺寸、结构和摆放全部保留。先简单复述我的要求，不要生成。', uploads=[b])
        b_message = f['message']
        confirmation = await turn('confirm_b_without_new_image', '没问题，开始生成吧。', images=1, base=refs[1]['content_sha256'])
        if confirmation['status'] == 'passed':
            target = confirmation['checkpoint']['_request_resolution_v1']['target']
            assert target.get('source_message_id') == b_message, 'Did not continue B'
        # These tests deliberately leave native children queued. A new message
        # in that same conversation is a steer to unfinished work, not a fresh
        # independent turn. Exercise explicit history in another real chat;
        # never fabricate a supplier completion to release the first one.
        from uuid import uuid4
        f['conv'] = str(uuid4())
        with psycopg.connect(content_db) as c:
            c.execute("INSERT INTO conversations(id,user_id,scope_type,scope_id) VALUES(%s,%s,'user',%s)", (f['conv'], f['user'], f['user']))
        second_a = await turn('history_chat_a_draft', '这张原图保留商品、文字、摆放，把背景改成暖米白色。请保存成一个稍后可执行的方案，只含一张图的完整图片提示词，保留原图作为素材，不要生成。', uploads=[a], saved=True)
        a_message = f['message']
        await turn('history_chat_b_discussion', '换个新任务，另一张本子封面的五角星想换成月牙。先不要做，简单确认一下你理解我的意思了吗？', uploads=[b])
        await turn('display_a_upload_no_generation', '把第1轮我上传的原图再发我看看，不是生成结果，不要重新生成。', shown=a_message)
        record = None
        if second_a['status'] == 'passed':
            artifact, = co.objects(TaskState.model_validate(second_a['checkpoint']['_task_execution']))
            record = co.ContentRecord.model_validate_json(artifact.inline_text).document.items[0].model_dump(mode='json')
        await turn('explicit_history_a_recipe', '回到第1轮那个暖米白色背景的提示词，按那个方案生成一张，不要做月牙那个任务。', images=1, base=refs[0]['content_sha256'], recipe=record)
        assert not [w for w in env['wire'] if w.get('kind') == 'supplier_submit']
        failures = [r for r in results if r['status'] != 'passed' or r['wire_errors'] or r['terminal_tool_errors']]
        assert not failures, [(r['case'], r['status'], r.get('error'), len(r['wire_errors'])) for r in failures]
    await formal_entry_acceptance(content_db, monkeypatch, customer_suite=suite)
