"""Real completed A, explicit history display, then B selection/admission.

One paid supplier job only. B remains queued and is not called a visual pass.
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
pytestmark = pytest.mark.skipif(os.environ.get('RUN_TURN_CATALOG_IMAGE_ACCEPTANCE') != '1', reason='Explicit one-image real acceptance')


@pytest.mark.asyncio
@pytest.mark.parametrize('task_db', ['everydayai'], indirect=True, scope='module')
async def test_completed_a_history_display_and_new_b(content_db, monkeypatch):
    async def suite(env):
        from schemas.message import TextPart
        assert os.environ['SKILL_SUPPLIER_MAX_POSTS'] == '1'
        f = env['formal']['f']; results = []; root = env['root']

        def persist():
            p = root/'turn-image-results.json'
            p.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str)); p.chmod(0o600)

        async def turn(name, text, *, uploads=(), images=0, base=None, generate=False, shown=None):
            row = {'case': name, 'user_text': text, 'status': 'running'}
            results.append(row); persist(); offset = len(env['wire']); start = time.monotonic()
            try:
                reply = await env['formal']['run'](f, [TextPart(text=text), *uploads])
                row.update(reply)
                with psycopg.connect(content_db) as c:
                    children = c.execute("SELECT id,assistant_message_id,request_params->'_media_request_v1' FROM tasks WHERE type='image' AND request_params#>>'{_media_request_v1,origin,parent_task_id}'=%s ORDER BY created_at", (f['task'],)).fetchall()
                row['image_requests'] = [{'task_id': str(t), 'message_id': str(m), 'snapshot': s} for t, m, s in children]
                assert len(children) == images, f'Expected {images} image requests; got {len(children)}'
                if base:
                    assert children and all(s['references'][0]['content_sha256'] == base for _, _, s in children), 'Wrong image base'
                if generate:
                    output = await env['generate_all'](f, [{'receipt': {'task_id': str(t), 'message_id': str(m)}, 'native': s} for t,m,s in children], name, parent_terminal=True)
                    row['image'] = output[0]
                if shown:
                    parts = reply['message']['content']
                    parts = json.loads(parts) if isinstance(parts, str) else parts
                    assert any(p.get('type') == 'image' and p.get('source_message_id') == shown for p in parts), 'Requested old image was not delivered'
                row['status'] = 'passed'
                return row
            except Exception as error:
                row.update(status='failed', error=str(error), trace=traceback.format_exc())
                return row
            finally:
                row['wire_errors'] = wire_errors(env['wire'][offset:])
                row['seconds'] = round(time.monotonic()-start, 3); persist()
                print(json.dumps({k: row[k] for k in ('case','status','seconds')}, ensure_ascii=False), flush=True)

        capture = Path(os.environ['SKILL_FAILURE_CAPTURE_ROOT'])
        data = json.loads((capture/'fixed-live-image-off-3.json').read_text())
        refs = sorted(data['native'][0]['recipes'][0]['resolved'], key=lambda r: r['content_index'])
        files = [next((capture/'workspace').rglob(Path(r['workspace_path']).name)) for r in refs]
        assert all(hashlib.sha256(p.read_bytes()).hexdigest() == r['content_sha256'] for p, r in zip(files, refs))
        a = await env['upload'](f, files[0], '客户验收/A原图.png')
        b = await env['upload'](f, files[1], '客户验收/B原图.jpg')
        first = await turn('a_actual_completion', '把这张背景改成暖米白色，商品、文字、图案和摆放都保持不变。直接做一张，1K就行。', uploads=[a], images=1, base=refs[0]['content_sha256'], generate=True)
        assert first['status'] == 'passed', 'A did not actually complete; dependent cases were not run'
        await turn('b_new_image_discussion', '换个新任务。这张本子上的大五角星改成同位置的细线月牙轮廓，不加其他元素，原来的文字、尺寸、结构和摆放全部保留。先确认你理解我的要求，不要生成。', uploads=[b])
        await turn('show_completed_a_after_b', '把第1轮生成的暖米白背景那张结果图再发我看看，不是上传原图，不要重新生成。', shown=first['image']['receipt']['message_id'])
        await turn('return_to_b_discussion', '回到第2轮的月牙任务，要求不变：只把大五角星改成同位置细线月牙轮廓，原来的文字、尺寸、结构和摆放全部保留。先复述，暂不生成。')
        await turn('confirm_b_without_upload', '没问题，开始生成吧。', images=1, base=refs[1]['content_sha256'])
        assert len([w for w in env['wire'] if w.get('kind') == 'supplier_submit']) == 1
        failures = [r for r in results if r['status'] != 'passed' or r['wire_errors']]
        assert not failures, [(r['case'], r['status'], r.get('error'), len(r['wire_errors'])) for r in failures]
    await formal_entry_acceptance(content_db, monkeypatch, customer_suite=suite)
