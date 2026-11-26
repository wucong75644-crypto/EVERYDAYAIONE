"""Opt-in customer dialogue via real HTTP/Actor/Qwen/KIE; no prescribed calls.

Creates a fixture-owned local database and uses at most six paid image jobs.
Every failed turn remains in private evidence. Never reads/writes production DB.
"""
import hashlib
import json
import os
import time
import traceback
from pathlib import Path

import psycopg
import pytest

from tests.manual_skill_formal_entry import formal_entry_acceptance

pytest_plugins = ['tests.test_content_objects_postgres']
pytestmark = pytest.mark.skipif(os.environ.get('RUN_SKILL_REAL_SUPPLIER_ACCEPTANCE') != '1', reason='Explicit real-provider customer acceptance')


def wire_errors(wire):
    """Include control errors that are absent from rendered tool-step blocks."""
    errors = {}
    for event in wire:
        if event.get('kind') != 'formal_model_request':
            continue
        body = event['body']
        for item in body.get('input', body.get('messages', [])):
            if item.get('type') != 'function_call_output':
                continue
            try:
                value = json.loads(item.get('output', ''))
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict) and value.get('ok') is False:
                errors[item.get('call_id')] = value
    return [{'call_id': key, 'result': value} for key, value in errors.items()]


@pytest.mark.asyncio
@pytest.mark.parametrize('task_db', ['everydayai'], indirect=True, scope='module')
async def test_both_bugs_with_customer_dialogue(content_db, monkeypatch):
    async def suite(env):
        from schemas.message import TextPart, ImagePart
        formal = env['formal']; facts = formal['f']; root = env['root']; results = []
        maximum = int(os.environ['SKILL_SUPPLIER_MAX_POSTS'])
        focus_only = os.environ.get('TWO_BUG_FOCUS_ONLY') == '1'
        assert maximum in ({3} if focus_only else {4, 6})

        def persist():
            file = root/'customer-results.json'
            file.write_text(json.dumps(results, ensure_ascii=False, default=str, indent=2))
            file.chmod(0o600)

        def children(parent):
            with psycopg.connect(content_db) as c:
                return c.execute("SELECT id,assistant_message_id,request_params->'_media_request_v1' FROM tasks WHERE type='image' AND request_params#>>'{_media_request_v1,origin,parent_task_id}'=%s ORDER BY created_at,id", (parent,)).fetchall()

        async def turn(name, text, *, attachments=(), expected_images=1, expected_base=None, verify=None):
            result = {'case': name, 'user_text': text, 'status': 'running'}
            results.append(result); persist(); started = time.monotonic()
            print(json.dumps({'case': name, 'stage': 'customer_message'}, ensure_ascii=False), flush=True)
            offset = len(env['wire'])
            try:
                row = await formal['run'](facts, [TextPart(text=text), *attachments])
                result.update(task_id=row['task_id'], message=row['message'], checkpoint=row['checkpoint'])
                issued = children(facts['task'])
                result['image_requests'] = [{'task_id': str(tid), 'message_id': str(mid), 'snapshot': snap} for tid,mid,snap in issued]
                blocks = row['checkpoint'].get('content_blocks', [])
                result['tool_errors'] = [b for b in blocks if b.get('type') == 'tool_step' and b.get('status') == 'error']
                result['model_requests'] = sum(w.get('kind') == 'formal_model_request' for w in env['wire'][offset:])
                result['routes'] = [w['result'] for w in env['wire'][offset:] if w.get('kind') == 'formal_workflow_route']
                assert len(issued) == expected_images, f'Expected {expected_images} new image tasks; got {len(issued)}'
                if expected_images:
                    assert len(issued) == 1
                    snap = issued[0][2]
                    assert 'previous_result_edit' not in snap
                    if expected_base is not None:
                        hashes = [r['content_sha256'] for r in snap['references']]
                        assert hashes and hashes[0] == expected_base, 'Edited the wrong picture or reused an original instead of the selected output'
                    completed = await env['generate_all'](facts, [
                        {'receipt': {'task_id': str(tid), 'message_id': str(mid)}, 'native': snap} for tid,mid,snap in issued],
                        name, parent_terminal=True)
                    result['image'] = completed[0]
                if verify is not None:
                    verify(result)
                result['status'] = 'passed'
                return result
            except Exception as error:
                result.update(status='failed', error_type=type(error).__name__, error=str(error), trace=traceback.format_exc())
                # A completed HTTP turn can still be a business failure. Keep
                # actual child snapshots so provider work can be reconciled.
                result.setdefault('task_id', facts.get('task'))
                result.setdefault('image_requests', [{'task_id':str(tid), 'message_id':str(mid), 'snapshot':snap} for tid,mid,snap in children(facts['task'])])
                return result
            finally:
                result['wire_errors'] = wire_errors(env['wire'][offset:])
                result['seconds'] = round(time.monotonic()-started, 3); persist()
                print(json.dumps({k:result[k] for k in ('case','status','seconds')}, ensure_ascii=False), flush=True)

        captured = json.loads((Path(os.environ['SKILL_FAILURE_CAPTURE_ROOT'])/'fixed-live-image-off-3.json').read_text())
        refs = sorted(captured['native'][0]['recipes'][0]['resolved'], key=lambda r:r['content_index'])
        sources = [next((Path(os.environ['SKILL_FAILURE_CAPTURE_ROOT'])/'workspace').rglob(Path(r['workspace_path']).name)) for r in refs]
        assert all(hashlib.sha256(p.read_bytes()).hexdigest() == r['content_sha256'] for p,r in zip(sources, refs))
        original = await env['upload'](facts, sources[0], '客户验收/波点封面原图.png')
        other = await env['upload'](facts, sources[1], '客户验收/星星封面原图.jpg')

        prior_root = os.environ.get('TWO_BUG_PREVIOUS_EVIDENCE')
        if prior_root:
            prior_root = Path(prior_root).resolve()
            assert str(prior_root).startswith('/private/tmp/skill-supplier-two-bugs-customer-')
            warm = next(r for r in json.loads((prior_root/'customer-results.json').read_text()) if r['case'] == 'edit_initial' and r['status'] == 'passed')
            path = Path(warm['image']['local_file']).resolve()
            assert path.is_relative_to(prior_root) and hashlib.sha256(path.read_bytes()).hexdigest() == warm['image']['sha256']
            uploaded = await env['upload'](facts, path, '客户验收/previous-actual-result.png')
            blue = await turn('edit_actual_result_blue', '这张背景换成浅蓝色，其他都别动，1K就行。', attachments=[uploaded], expected_base=warm['image']['sha256'])
        else:
            warm = await turn('edit_initial', '把背景改成暖米白色，两个本子的形态、封面图案、文字和摆放都保留，直接做一张，1K就行。', attachments=[original], expected_base=refs[0]['content_sha256'])
            if warm['status'] != 'passed':
                pytest.fail('Initial customer edit failed; dependent edits were not purchased. See private evidence.')
            blue = ({'status': 'not_run'} if focus_only else
                    await turn('edit_latest_blue', '这张背景换成浅蓝色，其他都别动。', expected_base=warm['image']['sha256']))
        if blue['status'] == 'passed':
            gray = await turn('edit_latest_gray', '再换成浅灰色，产品和字别变。', expected_base=blue['image']['sha256'])
        else:
            gray = {'status': 'not_run'}

        # Ordinary page quote: image IDs are carried by the client attachment,
        # never supplied as a desired model tool call or server target binding.
        if not prior_root and not focus_only:
            quote = ImagePart(**{**warm['image']['published_part'], 'source_message_id':warm['image']['receipt']['message_id'],
                                 'source_content_index':0, 'source_task_id':warm['image']['receipt']['task_id']})
            await turn('edit_quoted_older', '改这张，背景换成淡绿色，商品和文字保持原样。', attachments=[quote], expected_base=warm['image']['sha256'])

        discussion = await turn('new_task_discussion', '换个新任务。这张本子上的大五角星改成同位置的细线月牙轮廓，不加其他元素，原来的文字、尺寸、结构和摆放全部保留。先简单复述我的要求，不要生成。', attachments=[other], expected_images=0)
        b_message = facts['message']; b_task = facts['task']
        def verify_new_task(result):
            resolution = result['checkpoint'].get('_request_resolution_v1', {})
            target = resolution.get('target', {})
            assert target.get('source_message_id') == b_message or target.get('root_task_id') == b_task, 'Confirmation did not select the newly discussed task'
            assert result['image']['sha256'] not in {warm['image']['sha256'], blue.get('image', {}).get('sha256')}, 'New task delivered an old image'
        approval = await turn('new_task_generic_confirmation', '没问题，开始生成吧。', expected_base=refs[1]['content_sha256'], verify=verify_new_task)
        old = warm if focus_only else blue
        color = '暖米白色' if focus_only else '浅蓝色'
        if old['status'] == 'passed':
            def verify_shown(result):
                content = result['message']['content']
                content = json.loads(content) if isinstance(content, str) else content
                assert any(p.get('type') == 'image' and (
                    p.get('source_message_id') == old['image']['receipt']['message_id']
                    or p.get('task_id') == old['image']['receipt']['task_id']
                    or p.get('workspace_path') == old['image']['published_part']['workspace_path']) for p in content), 'Did not deliver the requested original result'
            shown = await turn('show_older_result', f'把前面{color}背景那张原图再发我看看，不要重新生成。', expected_images=0, verify=verify_shown)
            def verify_repeat(result):
                assert result['image']['snapshot']['prompt'] == old['image']['snapshot']['prompt']
                assert result['image']['receipt']['task_id'] != old['image']['receipt']['task_id']
            repeated = await turn('repeat_older_result', f'回到前面{color}背景那个版本，再生成一张，按当时的方案做，不是把旧图发给我。', verify=verify_repeat)

        persist()
        submissions = [w for w in env['wire'] if w.get('kind') == 'supplier_submit']
        assert len(submissions) <= maximum
        # A clean pass requires both the customer outcome and no corrected
        # validation errors. A recovery success is preserved as such, not perfect.
        failures = [r for r in results if r['status'] != 'passed' or r.get('tool_errors') or r.get('wire_errors')]
        assert not failures, [(r['case'],r['status'],r.get('error'),len(r.get('tool_errors',[]))) for r in failures]
    await formal_entry_acceptance(content_db, monkeypatch, customer_suite=suite)
