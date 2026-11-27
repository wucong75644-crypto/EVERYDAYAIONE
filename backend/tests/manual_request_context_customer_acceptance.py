"""Opt-in simple customer dialogue through real HTTP/Actor/model/provider.

Only the database, Redis and workspace are isolated. No prescribed model calls.
Private evidence records actual messages/tools, native sources and saved versions.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import time
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb
import pytest
import yaml

from tests.manual_skill_formal_entry import formal_entry_acceptance
from tests.manual_two_bug_customer_acceptance import wire_errors

pytest_plugins = ['tests.test_content_objects_postgres']
pytestmark = pytest.mark.skipif(os.environ.get('RUN_REQUEST_CONTEXT_ACCEPTANCE') != '1', reason='Explicit real model/provider acceptance')


def install_reference(dsn, env):
    """Use the exact already-published method body, not a replacement prompt."""
    from services.skills.contracts import SkillPackage, PublishRevision
    from services.skills.storage import SkillStorage
    from services.task_execution.methods import PROFILES
    captured = json.loads(Path(os.environ['REQUEST_CONTEXT_REFERENCE_CAPTURE']).read_text())
    active, candidate = [], []
    def visit(value):
        if isinstance(value, dict):
            if value.get('skill_key') == 'reference-image-prompts':
                if 'rendered' in value: active.append(value)
                if 'catalog_metadata' in value: candidate.append(value)
            for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit(captured)
    pinned, metadata = active[-1], candidate[-1]
    body = pinned['rendered']
    assert hashlib.sha256(body.encode()).hexdigest() == pinned['body_sha256']
    assert (metadata['package_id'], metadata['skill_key'], metadata['revision'], pinned['body_sha256']) in PROFILES
    header = {'skill_key': metadata['skill_key'], 'revision': metadata['revision'],
              'description': metadata['description'], 'catalog': metadata['catalog_metadata']}
    raw = ('---\n' + yaml.safe_dump(header, allow_unicode=True, sort_keys=False) + '---\n' + body).encode()
    package = SkillPackage(id=metadata['package_id'], skill_key=metadata['skill_key'], source='platform', scope_kind='platform', created_at=datetime.now(timezone.utc))
    publication = PublishRevision(revision=metadata['revision'], content_sha256=hashlib.sha256(raw).hexdigest(), body_sha256=pinned['body_sha256'])
    validated = SkillStorage.validate_bytes(package, publication, raw)
    dest = Path(env['settings'].skill_storage_root) / validated.nas_path
    dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(raw)
    with psycopg.connect(dsn) as c:
        c.execute("SELECT set_config('app.access_kind','runtime_admin',true),set_config('app.request_id','isolated-customer-reference',true),set_config('app.skill_action','platform_assign',true)")
        c.execute("INSERT INTO skill_packages(id,skill_key,source,scope_kind) VALUES(%s,%s,'platform','platform')", (package.id, package.skill_key))
        revision_id = c.execute('INSERT INTO skill_revisions(package_id,revision,nas_path,content_sha256,body_sha256,summary,catalog_metadata) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id',
            (package.id, publication.revision, validated.nas_path, publication.content_sha256, publication.body_sha256, metadata['description'], Jsonb(metadata['catalog_metadata']))).fetchone()[0]
        c.execute('INSERT INTO skill_assignments(org_id,package_id,revision_id,enabled) VALUES(NULL,%s,%s,true)', (package.id, revision_id))
    return {'skill_id': package.skill_key, 'revision': publication.revision}


@pytest.mark.asyncio
@pytest.mark.parametrize('task_db', ['everydayai'], indirect=True, scope='module')
async def test_simple_customer_history_sizes_and_new_request_windows(content_db, monkeypatch):
    root = Path(__file__).parents[1]
    async def suite(env):
        from schemas.message import TextPart
        from services.task_execution import content_objects as co
        from services.task_execution.contracts import TaskState
        selection = install_reference(content_db, env)
        facts = env['formal']['f']
        results = []
        def persist():
            file = env['root'] / 'request-context-customer-results.json'
            file.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str)); file.chmod(0o600)

        async def turn(name, text, *, attachments=(), selected=None, generate=False, images=0):
            row = {'case': name, 'user_text': text, 'status': 'running'}
            results.append(row); persist(); start = time.monotonic(); offset = len(env['wire'])
            print(json.dumps({'case': name, 'stage': 'sending'}, ensure_ascii=False), flush=True)
            try:
                reply = await env['formal']['run'](facts, [TextPart(text=text), *attachments], selection=selected)
                row.update(reply)
                with psycopg.connect(content_db) as c:
                    children = c.execute("SELECT id,assistant_message_id,request_params->'_media_request_v1' FROM tasks WHERE type='image' AND request_params#>>'{_media_request_v1,origin,parent_task_id}'=%s ORDER BY created_at", (facts['task'],)).fetchall()
                row['native'] = [{'receipt': {'task_id': str(t), 'message_id': str(m)}, 'native': snap} for t, m, snap in children]
                assert len(children) == images, f'Expected {images} new image tasks; got {len(children)}'
                if generate:
                    row['completed_images'] = await env['generate_all'](facts, row['native'], name, parent_terminal=True)
                state = (reply.get('checkpoint') or {}).get('_task_execution')
                if state:
                    row['catalog'] = co.catalog(TaskState.model_validate(state))
                row['wire_errors'] = wire_errors(env['wire'][offset:])
                row['status'] = 'passed'
                return row
            except BaseException as error:
                row.update(status='failed', error=str(error), error_type=type(error).__name__)
                raise
            finally:
                row['seconds'] = round(time.monotonic() - start, 3); persist()
                print(json.dumps({k: row[k] for k in ('case', 'status', 'seconds')}, ensure_ascii=False), flush=True)

        photo = Path(os.environ['REQUEST_CONTEXT_PRODUCT_IMAGE'])
        upload = await env['upload'](facts, photo, '客户测试/手账主体.jpg')
        first = await turn('reference_skill_three_drafts', '帮我想三个蓝白灯塔封面方案，本子的样子别改。前两个横着，三比二、一千，第三个竖着，三比四、两千。先看方案，不出图。', attachments=[upload], selected=selection)
        group = first['catalog'][-1]
        assert [i['options']['aspect_ratio'] for i in group['items']] == ['3:2', '3:2', '3:4']
        assert [i['options']['resolution'] for i in group['items']] == ['1K', '1K', '2K']
        made = await turn('saved_draft_resize_and_generate', '第三个做成一张图，正方形，一千就行。', images=1, generate=True)
        original_image = made['completed_images'][0]
        source_message = original_image['receipt']['message_id']
        source_hash = original_image['sha256']
        previous = made['catalog'][-1]
        changed = await turn('text_only_resolution_revision', '第三个清楚点，改成两千，其他别改。先不用出图。')
        revised = changed['catalog'][-1]
        assert revised['object_key'] == previous['object_key']
        assert revised['items'][:2] == previous['items'][:2]
        assert revised['items'][2]['options']['resolution'] == '2K'
        await turn('new_task_no_generation', '换个新任务，想三个绿叶封面方案，本子还是这个样子。先看方案，不要出图。')
        shown = await turn('find_and_display_old_lighthouse', '更早的图片也翻一下，最早那张蓝白灯塔图再发我看看，不用重新做。')
        parts = shown['message']['content']
        if isinstance(parts, str): parts = json.loads(parts)
        assert any(p.get('type') == 'image' and p.get('source_message_id') == source_message for p in parts)
        edited = await turn('edit_same_historical_image', '还是最早那张灯塔图，只把桌布换成浅蓝色，封面的图案和文字别动。', images=1, generate=True)
        assert edited['native'][0]['native']['references'][0]['content_sha256'] == source_hash
        assert all(not row['wire_errors'] for row in results), 'Actual model tool errors retained in private evidence'
        persist()

    await formal_entry_acceptance(content_db, monkeypatch, customer_suite=suite)
