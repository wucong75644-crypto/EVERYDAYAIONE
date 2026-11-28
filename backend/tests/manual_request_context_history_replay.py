"""Replay genuinely completed history through the latest HTTP/model code, no image POSTs."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.manual_skill_formal_entry import formal_entry_acceptance
from tests.manual_request_context_customer_acceptance import install_reference
from tests.manual_two_bug_customer_acceptance import wire_errors

pytest_plugins = ['tests.test_content_objects_postgres']
pytestmark = pytest.mark.skipif(os.environ.get('RUN_REQUEST_HISTORY_REPLAY') != '1', reason='Explicit recorded-history real-model acceptance')


@pytest.mark.asyncio
@pytest.mark.parametrize('task_db', ['everydayai'], indirect=True, scope='module')
async def test_current_query_and_display_with_completed_customer_history(content_db, monkeypatch):
    from tests import test_skill_composition_postgres as seed_module
    original_seed = seed_module.seed
    prior = Path(os.environ['REQUEST_HISTORY_REPLAY_ROOT'])
    rows = json.loads((prior / 'request-context-customer-results.json').read_text())
    scenario = os.environ.get('REQUEST_CONTEXT_REPLAY_SCENARIO')
    historical_resize = scenario == 'historical_resize'
    resize = scenario in {'resize', 'historical_resize'}
    text_revision = scenario == 'text_revision'
    image_edit = scenario in {'edit', 'display_edit'}
    made = next(row for row in rows if row['case'] == 'saved_draft_resize_and_generate')
    cutoff_name = ('reference_skill_three_drafts' if resize and not historical_resize else
                   'saved_draft_resize_and_generate' if text_revision else 'new_task_no_generation')
    cutoff = next(row for row in rows if row['case'] == cutoff_name)
    actual = made['completed_images'][0]
    assert actual['status'] == 'completed'
    assert hashlib.sha256(Path(actual['local_file']).read_bytes()).hexdigest() == actual['sha256']
    origin = actual['snapshot']['origin']

    def seed(dsn):
        facts = original_seed(dsn)
        info = psycopg.conninfo.conninfo_to_dict(dsn)
        assert info['dbname'].startswith('chat_image_test_') and info['host'].startswith('/')
        copies = re.findall(r'^(COPY public\.[^\n]+ FROM stdin;)\n(.*?)^\\\.\n', (prior / 'isolated-database.sql').read_text(), re.M | re.S)
        with psycopg.connect(dsn) as c:
            c.execute('SET session_replication_role=replica')
            for command, values in copies:
                table = command.split(' (')[0].split('.')[-1]
                if values and not table.startswith('skill_') and table != 'conversation_skill_bindings':
                    with c.cursor().copy(command.removesuffix(';')) as target: target.write(values)
            initial = cutoff['checkpoint'].get('_task_execution') if (resize and not historical_resize) or text_revision else None
            restored = c.execute('SELECT to_jsonb(s) FROM agent_task_states s WHERE root_task_id=%s', (initial['root_task_id'],)).fetchone()[0] if initial else None
            cutoff_queue = c.execute('SELECT queue_sequence FROM tasks WHERE id=%s', (cutoff['task_id'],)).fetchone()[0]
            c.execute('DELETE FROM agent_task_events')
            c.execute('DELETE FROM agent_task_states WHERE owner_task_id IN (SELECT id FROM tasks WHERE conversation_id=%s AND queue_sequence>%s)', (origin['conversation_id'], cutoff_queue))
            c.execute('DELETE FROM conversation_turn_checkpoints WHERE task_id IN (SELECT id FROM tasks WHERE conversation_id=%s AND queue_sequence>%s)', (origin['conversation_id'], cutoff_queue))
            c.execute('DELETE FROM messages WHERE conversation_id=%s AND context_revision>%s', (origin['conversation_id'], cutoff['message']['context_revision']))
            c.execute('DELETE FROM tasks WHERE conversation_id=%s AND queue_sequence>%s', (origin['conversation_id'], cutoff_queue))
            c.execute('UPDATE conversations SET active_serial_task_id=NULL,context_revision=%s,last_closed_message_id=%s WHERE id=%s',
                (cutoff['message']['context_revision'], cutoff['message']['id'], origin['conversation_id']))
            if restored:
                restored.update(state=initial, revision=initial['revision'], owner_task_id=cutoff['task_id'])
                c.execute('INSERT INTO agent_task_states SELECT * FROM jsonb_populate_record(NULL::agent_task_states,%s) ON CONFLICT(root_task_id) DO UPDATE SET owner_task_id=EXCLUDED.owner_task_id,revision=EXCLUDED.revision,state=EXCLUDED.state', (Jsonb(restored),))
            c.execute('SET session_replication_role=origin')
            c.execute("SELECT setval('task_queue_sequence_seq',GREATEST(COALESCE((SELECT max(queue_sequence) FROM tasks),1),1),true)")
        facts.update(user=origin['actor_user_id'], conv=origin['conversation_id'])
        return facts
    monkeypatch.setattr(seed_module, 'seed', seed)

    async def suite(env):
        from schemas.message import TextPart
        install_reference(content_db, env)
        shutil.copytree(prior / 'workspace', env['root'] / 'workspace', dirs_exist_ok=True)
        facts = env['formal']['f']
        displays = []
        if scenario == 'display_edit':
            for words in ('最早上传的本子原图也发我看看，不用重新做。',
                          '最早那张蓝白灯塔图再发我看看，不用重新做。'):
                displayed = await env['formal']['run'](facts, [TextPart(text=words)])
                parts = displayed['message']['content']
                parts = json.loads(parts) if isinstance(parts, str) else parts
                images = [p for p in parts if p.get('type') == 'image']
                assert len(images) == 1 and images[0].get('source_message_id')
                displays.append(displayed)
            assert images[0]['source_message_id'] == actual['receipt']['message_id']
        text = ('前面蓝白灯塔的第三个方案，做成一张图，正方形，一千就行。' if historical_resize else
                '第三个做成一张图，正方形，一千就行。' if resize else
                '第三个清楚点，改成两千，其他别改。先不用出图。' if text_revision else
                '还是最早那张灯塔图，只把桌布换成浅蓝色，封面的图案和文字别动。' if image_edit else
                '更早的图片也翻一下，最早那张蓝白灯塔图再发我看看，不用重新做。')
        reply = await env['formal']['run'](facts, [TextPart(text=text)])
        if resize or text_revision:
            from services.task_execution import content_objects as co
            from services.task_execution.contracts import TaskState
            prior_draft = next(row for row in rows if row['case'] == 'text_only_resolution_revision') if historical_resize else cutoff
            original = co.catalog(TaskState.model_validate(prior_draft['checkpoint']['_task_execution']))[-1]
            revised = co.catalog(TaskState.model_validate(reply['checkpoint']['_task_execution']))[-1]
            assert original['object_key'] == revised['object_key'] and original['items'][:2] == revised['items'][:2]
            assert revised['items'][2]['options']['aspect_ratio'] == '1:1'
            assert revised['items'][2]['options']['resolution'] == ('2K' if text_revision else '1K')
            with psycopg.connect(content_db) as c:
                requests = c.execute("SELECT request_params->'_media_request_v1' FROM tasks WHERE type='image' AND request_params#>>'{_media_request_v1,origin,parent_task_id}'=%s", (facts['task'],)).fetchall()
            if text_revision:
                assert not requests, 'Text-only revision must not submit a new image task'
            else:
                assert len(requests) == 1 and requests[0][0]['aspect_ratio'] == '1:1' and requests[0][0]['resolution'] == '1K'
            reported = wire_errors(env['wire'])
            # direct_loop publishes this non-dispatch handover before adopting
            # the exact saved object. It is not a failed tool validation. The
            # original-object, unchanged-sibling and accepted-task assertions
            # above still require that the handover really completed.
            transitions = [e for e in reported if e['result'].get('status') == 'resuming'
                and e['result'].get('content_followup') is True
                and e['result'].get('accepted') is False and not e['result'].get('code')]
            errors = [e for e in reported if e not in transitions]
            file = env['root'] / ('saved-text-revision-replay-result.json' if text_revision else 'saved-resize-replay-result.json')
            file.write_text(json.dumps({'user_text': text, 'reply': reply, 'native': requests[0][0] if requests else None, 'wire_errors': errors,
                'expected_handover_transitions': transitions,
                'saved_object_retained': True, 'siblings_unchanged': True, 'image_request_accepted': not text_revision,
                'supplier_called': False}, ensure_ascii=False, indent=2, default=str)); file.chmod(0o600)
            assert not any(w.get('kind') == 'supplier_submit' for w in env['wire'])
            assert not errors, errors
            return
        if image_edit:
            resolution = reply['checkpoint']['_request_resolution_v1']
            assert resolution['target']['kind'] == 'image'
            assert resolution['target']['source_message_id'] == actual['receipt']['message_id']
            with psycopg.connect(content_db) as c:
                requests = c.execute("SELECT request_params->'_media_request_v1' FROM tasks WHERE type='image' AND request_params#>>'{_media_request_v1,origin,parent_task_id}'=%s", (facts['task'],)).fetchall()
            assert len(requests) == 1
            native = requests[0][0]
            assert native['mode'] == 'image_to_image'
            assert native['references'][0]['content_sha256'] == actual['sha256']
            errors = wire_errors(env['wire'])
            file = env['root'] / 'history-image-edit-replay-result.json'
            file.write_text(json.dumps({'user_text': text, 'reply': reply, 'native': native,
                'preceding_displays': displays,
                'wire_errors': errors, 'expected_base_sha256': actual['sha256'],
                'correct_historical_image': True, 'image_request_accepted': True,
                'supplier_called': False}, ensure_ascii=False, indent=2, default=str)); file.chmod(0o600)
            assert not any(w.get('kind') == 'supplier_submit' for w in env['wire'])
            assert not errors, errors
            return
        checkpoint = reply['checkpoint']
        assert checkpoint.get('_historical_image_catalog_v1', {}).get('images'), 'New history query did not actually succeed'
        parts = reply['message']['content']
        if isinstance(parts, str): parts = json.loads(parts)
        assert any(p.get('type') == 'image' and p.get('source_message_id') == actual['receipt']['message_id'] for p in parts)
        errors = wire_errors(env['wire'])
        result = {'user_text': '更早的图片也翻一下，最早那张蓝白灯塔图再发我看看，不用重新做。',
                  'expected_source': actual['receipt']['message_id'], 'reply': reply, 'wire_errors': errors,
                  'query_succeeded': True, 'real_image_displayed': True}
        file = env['root'] / 'history-replay-result.json'
        file.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str)); file.chmod(0o600)
        assert not any(w.get('kind') == 'supplier_submit' for w in env['wire'])
        assert not errors, errors
    await formal_entry_acceptance(content_db, monkeypatch, customer_suite=suite)
