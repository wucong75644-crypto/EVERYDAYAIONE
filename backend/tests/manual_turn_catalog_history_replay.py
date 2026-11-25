"""Replay a verified, test-owned completed history; real HTTP/model, zero new vendor POSTs.

Only the real history prefix through B discussion is restored. This is not a
claim of a newly generated image or of repeat supplier completion.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import httpx
import psycopg
import pytest
from tests.manual_skill_formal_entry import formal_entry_acceptance

pytest_plugins = ['tests.test_content_objects_postgres']
pytestmark = pytest.mark.skipif(os.environ.get('RUN_HISTORY_REPLAY') != '1', reason='Explicit real history replay')

@pytest.mark.asyncio
@pytest.mark.parametrize('task_db', ['everydayai'], indirect=True, scope='module')
async def test_actual_completed_history_display(content_db, monkeypatch):
    from tests import test_skill_composition_postgres as seed_module
    original_seed = seed_module.seed
    prior = Path(os.environ['HISTORY_REPLAY_ROOT']).resolve()
    records = json.loads((prior/'turn-image-results.json').read_text())
    a, b = records[:2]
    assert a['status'] == b['status'] == 'passed'
    actual = a['image']
    assert actual['status'] == 'completed'
    assert hashlib.sha256(Path(actual['local_file']).read_bytes()).hexdigest() == actual['sha256']
    origin = actual['snapshot']['origin']
    imported = False
    def seed(dsn):
        nonlocal imported
        assert not imported
        imported = True
        info = psycopg.conninfo.conninfo_to_dict(dsn)
        assert info['dbname'].startswith('chat_image_test_') and info['host'].startswith('/')
        f = original_seed(dsn)
        copies = re.findall(r'^(COPY public\.[^\n]+ FROM stdin;)\n(.*?)^\\\.\n', (prior/'isolated-database.sql').read_text(), re.M | re.S)
        assert copies
        with psycopg.connect(dsn) as c:
            c.execute('SET session_replication_role=replica')
            for command, rows in copies:
                table = command.split(' (')[0].split('.')[-1]
                if rows and not table.startswith('skill_'):
                    with c.cursor().copy(command.removesuffix(';')) as target: target.write(rows)
            keep_tasks = [a['task_id'], actual['receipt']['task_id'], b['task_id']]
            c.execute('DELETE FROM agent_task_events WHERE root_task_id <> ALL(%s::uuid[])', (keep_tasks,))
            c.execute('DELETE FROM agent_task_states WHERE root_task_id <> ALL(%s::uuid[])', (keep_tasks,))
            c.execute('DELETE FROM conversation_turn_checkpoints WHERE task_id <> ALL(%s::uuid[])', (keep_tasks,))
            c.execute('DELETE FROM messages WHERE conversation_id=%s AND created_at>%s', (origin['conversation_id'],b['message']['created_at']))
            c.execute('DELETE FROM tasks WHERE conversation_id=%s AND id <> ALL(%s::uuid[])', (origin['conversation_id'],keep_tasks))
            c.execute('UPDATE conversations SET active_serial_task_id=NULL, context_revision=%s,last_closed_message_id=%s WHERE id=%s', (b['message']['context_revision'],b['message']['id'],origin['conversation_id']))
            c.execute('SET session_replication_role=origin')
            c.execute("SELECT setval('task_queue_sequence_seq',GREATEST(COALESCE((SELECT max(queue_sequence) FROM tasks),1),1),true)")
        f.update(user=origin['actor_user_id'],conv=origin['conversation_id'])
        return f
    monkeypatch.setattr(seed_module,'seed',seed)
    async def suite(env):
        from schemas.message import TextPart
        assert os.environ['SKILL_SUPPLIER_MAX_POSTS'] == '0'
        shutil.copytree(prior/'workspace',env['root']/'workspace',dirs_exist_ok=True)
        f=env['formal']['f']
        row=await env['formal']['run'](f,[TextPart(text='把第1轮生成的暖米白背景那张结果图再发我看看，不是上传原图，不要重新生成。')])
        parts=row['message']['content']
        parts=json.loads(parts) if isinstance(parts,str) else parts
        delivered=[p for p in parts if p.get('type')=='image']
        row['verified_historical_fixture']={'root':str(prior),'original_supplier_task':actual['external_task_id'],'sha256':actual['sha256']}
        path=env['root']/'history-replay-results.json';path.write_text(json.dumps(row,ensure_ascii=False,indent=2,default=str));path.chmod(0o600)
        assert any(p.get('source_message_id')==actual['receipt']['message_id'] for p in delivered), 'Requested generated image was not actually displayed'
        selected = next(p for p in delivered if p.get('source_message_id')==actual['receipt']['message_id'])
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(selected['url'])
        response.raise_for_status()
        assert hashlib.sha256(response.content).hexdigest() == actual['sha256'], 'Displayed bytes differ from completed image'
        assert not [w for w in env['wire'] if w.get('kind')=='supplier_submit']
        with psycopg.connect(content_db) as c:
            assert c.execute("SELECT count(*) FROM tasks WHERE type='image' AND request_params#>>'{_media_request_v1,origin,parent_task_id}'=%s",(f['task'],)).fetchone()[0]==0
    await formal_entry_acceptance(content_db,monkeypatch,customer_suite=suite)
