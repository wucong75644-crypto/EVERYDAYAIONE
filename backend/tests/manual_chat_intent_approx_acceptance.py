"""Opt-in original incident replay with real Qwen and three native KIE jobs."""
import copy
import hashlib
import json
import os
from pathlib import Path
import time
import traceback
import psycopg
import pytest
from tests.manual_skill_supplier_acceptance import _real_supplier_acceptance

pytest_plugins=['tests.test_content_objects_postgres']
pytestmark=pytest.mark.skipif(os.environ.get('RUN_SKILL_REAL_SUPPLIER_ACCEPTANCE')!='1',reason='Explicit paid local acceptance only')

@pytest.mark.asyncio
@pytest.mark.parametrize('task_db',['everydayai'],indirect=True,scope='module')
async def test_real_original_writing_approximate_canvas_and_two_edits(content_db,monkeypatch):
    async def suite(env):
        from schemas.message import TextPart
        from services.handlers.chat_image_request import REQUEST_KEY
        from services.task_execution import content_objects as co
        root=env['root'];results=[]
        def record():
            p=root/'p1-results.json';p.write_text(json.dumps(results,ensure_ascii=False,default=str,indent=2));p.chmod(0o600)
        prior=json.loads((Path(os.environ['SKILL_FAILURE_CAPTURE_ROOT'])/'fixed-live-image-off-3.json').read_text())
        refs=sorted(prior['native'][0]['recipes'][0]['resolved'],key=lambda x:x['content_index'])
        originals=[next((Path(os.environ['SKILL_FAILURE_CAPTURE_ROOT'])/'workspace').rglob(Path(ref['workspace_path']).name)) for ref in refs]
        assert all(hashlib.sha256(p.read_bytes()).hexdigest()==ref['content_sha256'] for p,ref in zip(originals,refs))
        async def fresh():
            f=env['seed'](content_db)
            with psycopg.connect(content_db) as c:c.execute('UPDATE users SET credits=10000 WHERE id=%s',(f['user'],))
            return f
        async def writing(name,text,photo=True):
            row={'case':name,'user_text':text,'status':'running'};results.append(row);record();started=time.monotonic()
            f=await fresh()
            try:
                parts=[TextPart(text=text)]
                if photo:parts.append(await env['upload'](f,originals[0],f'原始失败素材/{name}{originals[0].suffix}'))
                output=await env['run'](f,parts)
                r,p,h,q,receipts,blocks=output
                row.update(blocks=blocks,image_acceptances=len(receipts))
                assert not receipts,'Text-only request must not accept an image job'
                visible='\n'.join(b.get('text','') for b in blocks if b.get('type')=='text')
                assert len(visible)>50,'Must deliver an actual answer, not an acknowledgement'
                if photo:
                    catalog=co.catalog(r.task_execution_state)
                    row['catalog']=catalog
                    assert len(catalog)==1 and len(catalog[0]['items'])>=2,'Multiple full prompts must be saved for next-turn references'
                    for item in catalog[0]['items']:
                        body=co.load(r.task_execution_state,{'artifact_id':catalog[0]['artifact_id'],'item_index':item['item_index']})[2].body
                        assert len(body)>40 and body in visible
                with psycopg.connect(content_db) as c:
                    assert c.execute("SELECT count(*) FROM tasks WHERE type='image' AND user_id=%s",(f['user'],)).fetchone()[0]==0
                if r.task_execution_state is not None:
                    from services.task_execution.direct_loop import end_candidate
                    before=r.task_execution_state;closed=end_candidate(before)
                    if closed!=before:
                        payload=copy.deepcopy(r.checkpoint_base)
                        payload.update(_task_execution=closed.model_dump(mode='json'),_task_execution_expected_revision=before.revision)
                        with env['connect'](f) as c:env['save'](c,f,payload,expected=before.revision)
                        r.task_execution_state=closed
                await env['close_outer'](f,blocks)
                row['status']='passed'
            except Exception as e:row.update(status='failed',error_type=type(e).__name__,error=str(e),trace=traceback.format_exc())
            row['seconds']=round(time.monotonic()-started,3);record()
        await writing('ordinary_chat','你好，先聊一下你可以怎样协助我整理商品设计想法。',False)
        await writing('original_prompt_request','帮我写几个提示词参考这个产品作新的产品')
        await writing('text_only_modifier','帮我写几个可以生图的提示词，文字就行。')
        await writing('quoted_action_text','帮我写几套“生成图片”的提示词，不要实际执行。')
        f=await fresh();previous=None;runtime=None
        stages=[('two_photos_approx','参考图一的产品里面的波点。给图二的产品加上一些波点。输出约1920×1080，接近即可。直接生成一张。'),
            ('continuous_blue','把你刚生成的那张图的桌面背景改为浅蓝色，产品形态、封面图案和所有物品的布局保持不变，只改桌面颜色，直接生成一张。'),
            ('continuous_gray','把你刚生成的那张蓝色背景图的桌面背景改为浅米灰色，产品形态、封面图案和所有物品的布局保持不变，只改桌面颜色，直接生成一张。')]
        if os.environ.get('P1_WRITING_ONLY')=='1':stages=[]
        for index,(name,text) in enumerate(stages):
            row={'case':name,'user_text':text,'status':'running','controlled_variant':index==0};results.append(row);record();started=time.monotonic()
            if index and previous is None:
                row.update(status='not_run',reason='Prior image did not complete; no replacement purchase');record();continue
            try:
                if index:f=await env['next_user'](f,text)
                parts=[TextPart(text=text)]
                if not index:
                    parts += [await env['upload'](f,p,f'原始失败素材/reference-{i}{p.suffix}') for i,p in enumerate(originals,1)]
                output=await env['run'](f,parts);r,p,h,q,receipts,blocks=output
                row.update(blocks=blocks,image_acceptances=len(receipts))
                assert len(receipts)==1,'One explicit edit must accept exactly one image'
                task=await env['worker'].refresh({'id':receipts[0]['receipt']['task_id'],'user_id':f['user'],'org_id':None})
                snapshot=task['request_params'][REQUEST_KEY];row['snapshot']=snapshot
                assert (snapshot['aspect_ratio'],snapshot['resolution'])==('16:9','2K')
                if not index:
                    assert len(snapshot['references'])==2
                    assert {ref['content_sha256'] for ref in snapshot['references']}=={ref['content_sha256'] for ref in refs}
                    target=snapshot['size_requirement']
                    assert target['approximate'] and (target['requested_width'],target['requested_height'])==(1920,1080)
                    assert 'width' not in target and 'height' not in target
                else:
                    assert len(snapshot['references'])==1
                    assert snapshot['references'][0]['content_sha256']==previous['sha256']
                    assert snapshot['references'][0]['message_id']==previous['receipt']['message_id']
                one=(await env['generate_all'](f,receipts,name))[0]
                await env['complete_parent'](f,r)
                previous=one;row.update(status='passed',image=one)
            except Exception as e:
                row.update(status='failed',error_type=type(e).__name__,error=str(e),trace=traceback.format_exc());previous=None
            row['seconds']=round(time.monotonic()-started,3);record()
        posts=[w for w in env['wire'] if w.get('kind')=='supplier_submit']
        assert len(posts)==sum(1 for r in results if r.get('status')=='passed' and r['case'] in {s[0] for s in stages})
        for item in env['wire']:
            if item.get('kind')=='model':
                assert item['body'].get('enable_thinking') is False or (item['body'].get('reasoning') or {}).get('effort')=='none'
        failures=[r for r in results if r['status']!='passed']
        assert not failures,[(r['case'],r['status'],r.get('error')) for r in failures]
    await _real_supplier_acceptance(content_db,monkeypatch,suite=suite)

@pytest.mark.asyncio
@pytest.mark.parametrize('task_db',['everydayai'],indirect=True,scope='module')
async def test_resume_gray_edit_from_original_completed_blue_image(content_db,monkeypatch):
    async def suite(env):
        import re
        import shutil
        source=Path(os.environ['P1_RECOVERY_SOURCE']).resolve()
        assert str(source).startswith('/private/tmp/skill-supplier-p1-')
        saved=json.loads((source/'p1-results.json').read_text())
        blue=next(r['image'] for r in saved if r['case']=='continuous_blue' and r['status']=='passed')
        failed=next(r for r in saved if r['case']=='continuous_gray' and r['status']=='failed')
        assert failed['image_acceptances']==0
        info=psycopg.conninfo.conninfo_to_dict(content_db)
        assert info['dbname'].startswith('chat_image_test_') and info['host'].startswith('/')
        copies=re.findall(r'^(COPY public\.[^\n]+ FROM stdin;)\n(.*?)^\\\.\n',(source/'isolated-database.sql').read_text(),re.M|re.S)
        with psycopg.connect(content_db) as c:
            c.execute('SET session_replication_role=replica')
            for command,rows in copies:
                if rows:
                    with c.cursor().copy(command.removesuffix(';')) as cp:cp.write(rows)
            c.execute('SET session_replication_role=origin')
            c.execute("SELECT setval('task_queue_sequence_seq',GREATEST(COALESCE((SELECT max(queue_sequence) FROM tasks),1),1),true)")
            conv=blue['snapshot']['origin']['conversation_id']
            old=c.execute("SELECT to_jsonb(t) FROM tasks t WHERE conversation_id=%s AND type='chat' ORDER BY created_at DESC LIMIT 1",(conv,)).fetchone()[0]
            assert c.execute("SELECT count(*) FROM tasks WHERE type='image' AND request_params->'_media_request_v1'->'origin'->>'parent_task_id'=%s",(old['id'],)).fetchone()[0]==0
            # Test-owned restored fixture only: make the known pre-send parent
            # lease usable so its real terminal RPC can close the old window.
            c.execute("UPDATE tasks SET lease_expires_at=now()+interval '10 minutes' WHERE id=%s",(old['id'],))
        shutil.copytree(source/'workspace',Path(env['settings'].file_workspace_root),dirs_exist_ok=True)
        facts={'dsn':content_db,'task':old['id'],'user':old['user_id'],'org':old['org_id'],'conv':conv,
            'message':old['input_message_id'],'turn':old['turn_id'],'token':old['execution_token']}
        await env['close_outer'](facts,failed['blocks'])
        text=failed['user_text']
        facts=await env['next_user'](facts,text)
        from schemas.message import TextPart
        from services.handlers.chat_image_request import REQUEST_KEY
        output=await env['run'](facts,[TextPart(text=text)])
        r,p,h,q,receipts,blocks=output
        result={'case':'continuous_gray_recovery','user_text':text,'blocks':blocks,'acceptances':len(receipts)}
        file=env['root']/'gray-recovery.json';file.write_text(json.dumps(result,ensure_ascii=False,default=str,indent=2))
        assert len(receipts)==1
        task=await env['worker'].refresh({'id':receipts[0]['receipt']['task_id'],'user_id':facts['user'],'org_id':None})
        snap=task['request_params'][REQUEST_KEY]
        assert (snap['aspect_ratio'],snap['resolution'])==('16:9','2K')
        assert len(snap['references'])==1 and snap['references'][0]['content_sha256']==blue['sha256']
        assert snap['references'][0]['message_id']==blue['receipt']['message_id']
        image=(await env['generate_all'](facts,receipts,'continuous-gray-recovery'))[0]
        await env['complete_parent'](facts,r)
        assert len([w for w in env['wire'] if w.get('kind')=='supplier_submit'])==1
        result.update(status='passed',image=image,reused_blue_hash=blue['sha256'])
        file.write_text(json.dumps(result,ensure_ascii=False,default=str,indent=2));file.chmod(0o600)
    await _real_supplier_acceptance(content_db,monkeypatch,suite=suite)
