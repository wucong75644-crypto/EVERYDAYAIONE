"""Opt-in real customer wording, actual local Actor, supplier and completion events.

Reuse the owned DB/workspace harness. No prescribed model tool calls and no
production database writes. Records exact provider input/tools/output privately.
"""
import copy
import json
import re
import shutil
from uuid import uuid4
import os
from pathlib import Path
import time

import psycopg
import pytest

from tests.manual_skill_formal_entry import formal_entry_acceptance
from tests.manual_request_context_customer_acceptance import install_reference, assert_saved_draft
from tests.manual_two_bug_customer_acceptance import wire_errors

pytest_plugins = ['tests.test_content_objects_postgres']
pytestmark = pytest.mark.skipif(os.environ.get('RUN_IMAGE_REPLY_BOUNDARY_ACCEPTANCE') != '1', reason='Explicit paid real supplier acceptance')


@pytest.mark.asyncio
@pytest.mark.parametrize('task_db', ['everydayai'], indirect=True, scope='module')
async def test_natural_receipt_history_revision_edit_and_completion_policy(content_db, monkeypatch):
    from schemas.message import TextPart
    restore_root = os.environ.get('IMAGE_REPLY_CAPTURE_RESTORE')
    captured = None
    captured_owner = {}
    if restore_root:
        # A failed assertion in this harness must not repurchase published images.
        import tests.manual_skill_formal_entry as entry
        import tests.test_skill_composition_postgres as seeds
        source = Path(restore_root)
        captured = json.loads((source/'reply-boundary-results.json').read_text())[-1]
        original_install = entry.install_entry_schema
        def install_and_restore(dsn, root, settings, patch):
            result = original_install(dsn, root, settings, patch)
            info = psycopg.conninfo.conninfo_to_dict(dsn)
            assert info['dbname'].startswith('chat_image_test_') and info['host'].startswith('/')
            dump = (source/'isolated-database.sql').read_text()
            with psycopg.connect(dsn) as c:
                c.execute('SET session_replication_role=replica')
                copies = re.findall(r'^(COPY public\.[^\n]+ FROM stdin;)\n(.*?)^\\\.\n', dump, re.M|re.S)
                assert copies
                for command, rows in copies:
                    if not rows: continue
                    match = re.fullmatch(r'COPY public\.(\w+) (\(.+\)) FROM stdin;', command)
                    assert match, command
                    table, columns = match.groups()
                    c.execute(f'CREATE TEMP TABLE capture_restore (LIKE public.{table}) ON COMMIT DROP')
                    with c.cursor().copy(f'COPY capture_restore {columns} FROM stdin') as target:
                        target.write(rows)
                    c.execute(f'INSERT INTO public.{table} {columns} SELECT {columns[1:-1]} FROM capture_restore ON CONFLICT DO NOTHING')
                    c.execute('DROP TABLE capture_restore')
                c.execute('SET session_replication_role=origin')
                c.execute("SELECT setval('task_queue_sequence_seq',GREATEST(COALESCE((SELECT max(queue_sequence) FROM tasks),1),1),true)")
                task = c.execute('SELECT to_jsonb(t) FROM tasks t WHERE id=%s',(captured['task_id'],)).fetchone()[0]
                captured_owner.update(dsn=dsn,user=task['user_id'],conv=task['conversation_id'],task=task['id'],
                    message=task['input_message_id'],turn=task['turn_id'],token=task['execution_token'])
            shutil.copytree(source/'skills', root/'skills', dirs_exist_ok=True)
            return result
        monkeypatch.setattr(entry,'install_entry_schema',install_and_restore)
        def captured_seed(_dsn):
            # Same authenticated actor/conversation, disposable nonexistent seed IDs.
            return {**captured_owner, **{key:str(uuid4()) for key in ('task','message','turn','token')}}
        monkeypatch.setattr(seeds,'seed',captured_seed)
    async def suite(env):
        selection = None if captured else install_reference(content_db, env)
        with psycopg.connect(content_db) as c:
            c.execute((Path(__file__).parents[1]/'migrations/389_internal_continuation_presentation.sql').read_text())
        facts = env['formal']['f']; rows=[]
        def persist():
            file=env['root']/'reply-boundary-results.json'
            file.write_text(json.dumps(rows,ensure_ascii=False,indent=2,default=str)); file.chmod(0o600)
        def model_calls():
            return sum(w.get('kind')=='formal_model_request' for w in env['wire'])
        async def completion(row, expected_scope):
            cp=row['checkpoint']; rr=cp['_request_resolution_v1']
            actual_scope=rr['action']['request_context'].get('image_completion_scope',{}).get('after_images','unknown')
            assert actual_scope in {expected_scope,'unknown'}
            with env['connect'](facts) as c:
                state=c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',(cp['_task_execution']['root_task_id'],)).fetchone()[0]
                event=c.execute("SELECT id FROM agent_task_events WHERE root_task_id=%s AND status='pending' ORDER BY created_at LIMIT 1",(state['root_task_id'],)).fetchone()[0]
                queued=c.execute('SELECT enqueue_agent_image_continuation(%s,%s,%s)',(state['root_task_id'],state['revision'],event)).fetchone()[0]
                assert queued['outcome']=='queued',queued
            claim=await env['formal']['service'].claim_serial(facts['conv']); assert claim.task_id==queued['task_id']
            before=model_calls(); offset=len(env['events'])
            result=await env['formal']['service'].execute_claim(claim)
            with psycopg.connect(content_db) as c:
                task=c.execute('SELECT to_jsonb(t) FROM tasks t WHERE id=%s',(claim.task_id,)).fetchone()[0]
                msg=c.execute('SELECT to_jsonb(m) FROM messages m WHERE id=%s',(queued['message_id'],)).fetchone()[0]
                state=c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',(state['root_task_id'],)).fetchone()[0]
            row['completion']={'result':result,'task':task,'message':msg,'state':state,'model_calls':model_calls()-before,'scope':actual_scope,
                'events':env['events'][offset:]};persist()
            assert task['status']=='completed'
            if expected_scope=='terminal' and actual_scope=='terminal':
                assert model_calls()==before, 'Pure completed image request must not sample another model'
                assert not any(e.get('type')=='message_start' and e.get('message_id')==queued['message_id'] for e in env['events'][offset:])
                assert msg['generation_params']['delivery_presentation']['output_started'] is False
                assert msg['content'] in ([], '[]')
            else:
                assert model_calls()>before, 'Image-dependent followup must retain the original model loop'
                assert msg['generation_params']['delivery_presentation']['output_started'] is True
        async def turn(name,text,*,attachments=(),selected=None,images=0,scope=None,saved=False):
            row={'case':name,'user_text':text,'status':'running'};rows.append(row);persist()
            print(json.dumps({'case':name,'stage':'sending'},ensure_ascii=False),flush=True)
            before=len(env['wire']); started=time.monotonic()
            try:
                reply=await env['formal']['run'](facts,[TextPart(text=text),*attachments],selection=selected);row.update(reply)
                with psycopg.connect(content_db) as c:
                    children=c.execute("SELECT id,assistant_message_id,request_params->'_media_request_v1' FROM tasks WHERE type='image' AND request_params#>>'{_media_request_v1,origin,parent_task_id}'=%s ORDER BY created_at",(facts['task'],)).fetchall()
                native=[{'receipt':{'task_id':str(t),'message_id':str(m)},'native':snapshot} for t,m,snapshot in children]
                row['native']=native;assert len(native)==images
                if saved:row['saved_draft']=assert_saved_draft(reply,facts['task'])
                if images:
                    row['completed_images']=await env['generate_all'](facts,native,name,parent_terminal=True)
                    await completion(row,scope)
                row['wire_errors']=wire_errors(env['wire'][before:])
                assert not row['wire_errors'],row['wire_errors']
                row['status']='passed';return row
            except BaseException as error:
                row.update(status='failed',error=str(error),error_type=type(error).__name__);raise
            finally:
                row['seconds']=round(time.monotonic()-started,2);persist()
                print(json.dumps({'case':name,'status':row['status'],'seconds':row['seconds']},ensure_ascii=False),flush=True)
        if captured:
            facts.update(captured_owner)
            row=copy.deepcopy(captured); rows.append(row); row.update(status='running',error=None,error_type=None)
            persist()
            await completion(row,'continue')
            assert row['completion']['model_calls']>0
            content=row['completion']['message']['content']
            content=json.loads(content) if isinstance(content,str) else content
            assert any(p.get('type')=='text' and p.get('text','').strip() for p in content)
            row['status']='passed'; persist()
            print('CAPTURED_EDIT_COMPLETION_AND_COMPARE_PASSED_WITHOUT_NEW_SUPPLIER_POST',flush=True)
            return
        upload=await env['upload'](facts,Path(os.environ['REQUEST_CONTEXT_PRODUCT_IMAGE']),'客户测试/手账主体.jpg')
        await turn('reference_skill_three_drafts','帮我想三个蓝白灯塔封面方案，本子的样子别改。先看方案，不出图。',attachments=[upload],selected=selection,saved=True)
        made=await turn('third_square_1k','第三个做成一张图，正方形，一千就行。',images=1,scope='terminal',saved=True)
        await turn('draft_revision_only','第三个清楚点，改成两千，其他别改。先不用出图。',saved=True)
        shown=await turn('display_earliest','最早那张蓝白灯塔图再发我看看，不用重新做。')
        content=shown['message']['content'];content=json.loads(content) if isinstance(content,str) else content
        assert any(p.get('type')=='image' and p.get('source_message_id')==made['completed_images'][0]['receipt']['message_id'] for p in content)
        edited=await turn('edit_then_compare','就用刚才找回的这张图，把桌布换成浅蓝色，封面的图案和字别动。做好后再说说这版和原来哪里不一样。',images=1,scope='continue')
        assert edited['native'][0]['native']['references'][0]['content_sha256']==made['completed_images'][0]['sha256']
    await formal_entry_acceptance(content_db,monkeypatch,customer_suite=suite)
