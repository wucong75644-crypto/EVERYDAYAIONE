"""Opt-in recovery of actual paid outputs; never create a replacement job."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace

import psycopg
import pytest

from tests.manual_skill_supplier_acceptance import _real_supplier_acceptance

pytest_plugins=['tests.test_content_objects_postgres']
pytestmark=pytest.mark.skipif(os.environ.get('RUN_SKILL_REAL_SUPPLIER_ACCEPTANCE')!='1',reason='Explicit paid acceptance only')


@pytest.mark.asyncio
@pytest.mark.parametrize('task_db',['everydayai'],indirect=True,scope='module')
async def test_recover_original_plan_jobs_then_two_real_edits(content_db,monkeypatch):
    async def suite(env):
        from services.task_execution.contracts import TaskState
        from services.task_execution import content_objects as co
        from schemas.message import TextPart
        from services.handlers.chat_image_request import REQUEST_KEY
        source=Path(os.environ['PRODUCT_RECOVERY_SOURCE']).resolve()
        assert str(source).startswith('/private/tmp/skill-supplier-')
        saved=json.loads((source/'evidence.json').read_text())
        original=saved['cases'][0];original_id=original['external_task_id']
        assert original_id and original['snapshot']['origin']['parent_task_id']
        info=psycopg.conninfo.conninfo_to_dict(content_db)
        assert info['dbname'].startswith('chat_image_test_') and info['host'].startswith('/')
        dump=(source/'isolated-database.sql').read_text()
        copies=re.findall(r'^(COPY public\.[^\n]+ FROM stdin;)\n(.*?)^\\\.\n',dump,re.M|re.S)
        assert copies
        with psycopg.connect(content_db) as c:
            c.execute('SET session_replication_role=replica')
            for command,rows in copies:
                if rows:
                    with c.cursor().copy(command.removesuffix(';')) as target:target.write(rows)
            c.execute('SET session_replication_role=origin')
            c.execute("SELECT setval('task_queue_sequence_seq',GREATEST(COALESCE((SELECT max(queue_sequence) FROM tasks),1),1),true)")
            parent=c.execute('SELECT to_jsonb(t) FROM tasks t WHERE id=%s',(original['snapshot']['origin']['parent_task_id'],)).fetchone()[0]
            state=c.execute('SELECT state FROM agent_task_states WHERE owner_task_id=%s',(parent['id'],)).fetchone()[0]
            jobs=[row[0] for row in c.execute("SELECT to_jsonb(t) FROM tasks t WHERE type='image' AND request_params->'_media_request_v1'->'origin'->>'parent_task_id'=%s ORDER BY created_at,id",(str(parent['id']),)).fetchall()]
        facts=dict(dsn=content_db,user=parent['user_id'],org=parent.get('org_id'),conv=parent['conversation_id'],
            task=parent['id'],turn=parent['turn_id'],message=parent['input_message_id'],token=parent['execution_token'])
        runtime=SimpleNamespace(task_bootstrap=SimpleNamespace(execution_identity=TaskState.model_validate(state).execution_identity))
        receipts_by_id={row['receipt']['task_id']:row['receipt'] for row in saved['wire'] if row.get('kind')=='native_image_acceptance' and row['receipt'].get('accepted')}
        assert len(jobs)==len(receipts_by_id)==2
        catalog,=co.catalog(TaskState.model_validate(state))
        entries=[];indices=[]
        for job in jobs:
            snapshot=job['request_params'][REQUEST_KEY]
            index=snapshot['source_prompt']['content_object']['source']['item_index'];indices.append(index)
            assert snapshot['prompt']==co.load(TaskState.model_validate(state),{'artifact_id':catalog['artifact_id'],'item_index':index})[2].body
            assert len(snapshot['references'])==1
            if job['id']==original['receipt']['task_id']:assert job['external_task_id']==original_id
            else:assert job['external_task_id'] is None
            entries.append({'receipt':receipts_by_id[job['id']],'native':{'prompt':snapshot['prompt'],'references':snapshot['references']}})
        assert sorted(indices)==[1,3]
        with env['connect'](facts) as c:
            assert c.execute('SELECT renew_generation_lease(%s,%s,300)',(facts['task'],facts['token'])).fetchone()[0]['outcome']=='renewed'
        env['wire'].extend(copy.deepcopy(saved['wire']));env['persist']()
        chosen=await env['generate_all'](facts,entries,'recovered-plan')
        assert next(row for row in chosen if row['receipt']['task_id']==original['receipt']['task_id'])['external_task_id']==original_id
        # One original supplier job was already accepted. Recovery creates only
        # the previously unsubmitted second job, then the two new user edits.
        assert len([w for w in env['wire'] if w.get('kind')=='supplier_submit'])==2
        await env['complete_parent'](facts,runtime)
        previous=chosen[-1]
        for number,color in enumerate(('浅蓝色','浅米灰色'),1):
            instruction=f'把你刚生成的那张图的桌面背景改为{color}，产品的形态、封面图案和构图都保持不变。直接生成一张。'
            facts=await env['next_user'](facts,instruction)
            await env['set_phase'](f'recovered-edit-{number}-model')
            edit=await env['run'](facts,[TextPart(text=instruction)])
            assert len(edit[4])==1
            task=await env['worker'].refresh({'id':edit[4][0]['receipt']['task_id'],'user_id':facts['user'],'org_id':facts.get('org')})
            snapshot=task['request_params'][REQUEST_KEY]
            assert len(snapshot['references'])==1 and snapshot['references'][0]['content_sha256']==previous['sha256']
            assert snapshot['references'][0]['message_id']==previous['receipt']['message_id']
            result=(await env['generate_all'](facts,edit[4],f'recovered-edit-{number}'))[0]
            assert result['snapshot']['aspect_ratio']==previous['snapshot']['aspect_ratio']
            assert result['snapshot']['resolution']==previous['snapshot']['resolution']
            await env['complete_parent'](facts,edit[0]);previous=result
        assert len([w for w in env['wire'] if w.get('kind')=='supplier_submit'])==4
        print('REAL_PRODUCT_FIDELITY_RECOVERY_COMPLETE: original supplier ID retained; plans 1/3 and two consecutive edits',flush=True)
    await _real_supplier_acceptance(content_db,monkeypatch,suite=suite)


@pytest.mark.asyncio
@pytest.mark.parametrize('task_db',['everydayai'],indirect=True,scope='module')
async def test_two_real_edits_from_approved_source(content_db,monkeypatch):
    async def suite(env):
        from schemas.message import TextPart
        from services.handlers.chat_image_request import REQUEST_KEY
        source=Path(os.environ['PRODUCT_APPROVED_IMAGE']).resolve()
        assert str(source).startswith('/private/tmp/skill-supplier-') and source.is_file()
        f=env['seed'](content_db)
        with psycopg.connect(content_db) as c:c.execute('UPDATE users SET credits=10000 WHERE id=%s',(f['user'],))
        original=await env['upload'](f,source,'验收原图/approved-product.png')
        text='图一是卡册。把图一的桌面背景改为浅蓝色，保留所有物品的布局、封面图案、爱心扣、花边和 Bleeding Heart 文字，文字逐字不改，只改变桌面颜色。直接生成一张。'
        first=await env['run'](f,[TextPart(text=text),original])
        assert len(first[4])==1
        task=await env['worker'].refresh({'id':first[4][0]['receipt']['task_id'],'user_id':f['user'],'org_id':None})
        assert task['request_params'][REQUEST_KEY]['references'][0]['content_sha256']==hashlib.sha256(source.read_bytes()).hexdigest()
        one=(await env['generate_all'](f,first[4],'blue-edit'))[0]
        await env['complete_parent'](f,first[0])
        text='把你刚生成的那张图的桌面背景改为浅米灰色，卡册形态、封面图案和所有物品的布局保持不变，Bleeding Heart 文字逐字不改。只改桌面颜色，直接生成一张。'
        f=await env['next_user'](f,text)
        second=await env['run'](f,[TextPart(text=text)])
        assert len(second[4])==1
        task=await env['worker'].refresh({'id':second[4][0]['receipt']['task_id'],'user_id':f['user'],'org_id':None})
        snapshot=task['request_params'][REQUEST_KEY]
        assert len(snapshot['references'])==1 and snapshot['references'][0]['content_sha256']==one['sha256']
        assert snapshot['references'][0]['message_id']==one['receipt']['message_id']
        two=(await env['generate_all'](f,second[4],'gray-edit'))[0]
        assert two['snapshot']['aspect_ratio']==one['snapshot']['aspect_ratio']=='1:1'
        assert two['snapshot']['resolution']==one['snapshot']['resolution']=='1K'
        await env['complete_parent'](f,second[0])
        assert len([w for w in env['wire'] if w.get('kind')=='supplier_submit'])==2
        print('REAL_TWO_EDIT_ACCEPTANCE_COMPLETE: uploaded approved image -> blue output -> gray output; exact prior hashes and one charge each',flush=True)
    await _real_supplier_acceptance(content_db,monkeypatch,suite=suite)
