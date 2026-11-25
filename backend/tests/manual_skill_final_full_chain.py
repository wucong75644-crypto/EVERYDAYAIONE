"""Opt-in paid final-candidate chain: fresh authoring, in-flight material, ordinal, 3 real images."""
import copy
import hashlib
import json
import os
from pathlib import Path
import time
import traceback
from uuid import uuid4
import psycopg
from psycopg.types.json import Jsonb
import pytest
from tests.manual_skill_supplier_acceptance import _real_supplier_acceptance

pytest_plugins=['tests.test_content_objects_postgres']
pytestmark=pytest.mark.skipif(os.environ.get('RUN_SKILL_REAL_SUPPLIER_ACCEPTANCE')!='1',reason='Explicit paid isolated acceptance only')

@pytest.mark.asyncio
@pytest.mark.parametrize('task_db',['everydayai'],indirect=True,scope='module')
async def test_final_candidate_fresh_full_chain(content_db,monkeypatch):
    migration=Path(__file__).parents[1]/'migrations/380_conversation_steer_attachments.sql'
    with psycopg.connect(content_db) as c:c.execute(migration.read_text())
    async def suite(env):
        from schemas.message import TextPart
        from services.conversation_commands import SafePoint,ConversationCommand,CommandType
        from services.task_execution.steer_attachments import bind,Selection
        from services.task_execution.direct_loop import end_candidate
        from services.task_execution import content_objects as co
        from services.handlers.chat_image_request import REQUEST_KEY
        root=env['root'];results=[];attempt_start=time.monotonic()
        names=['text_only_with_inflight_material','ordinal_generation_approximate_size','continuous_blue','continuous_gray']
        def record():
            p=root/'full-chain-results.json'
            p.write_text(json.dumps({'elapsed_seconds':round(time.monotonic()-attempt_start,3),'stages':results},ensure_ascii=False,default=str,indent=2));p.chmod(0o600)
        capture_root=Path(os.environ['SKILL_FAILURE_CAPTURE_ROOT'])
        prior=json.loads((capture_root/'fixed-live-image-off-3.json').read_text())
        refs=sorted(prior['native'][0]['recipes'][0]['resolved'],key=lambda x:x['content_index'])
        originals=[next((capture_root/'workspace').rglob(Path(ref['workspace_path']).name)) for ref in refs]
        assert all(hashlib.sha256(path.read_bytes()).hexdigest()==ref['content_sha256'] for path,ref in zip(originals,refs))
        f=env['seed'](content_db)
        with psycopg.connect(content_db) as c:c.execute('UPDATE users SET credits=10000 WHERE id=%s',(f['user'],))
        target=await env['upload'](f,originals[0],'完整链路/产品底图.png')
        style=await env['upload'](f,originals[1],'完整链路/运行中追加背景参考.jpg')
        user='参考这张产品图，帮我写3套可以生图的完整提示词，文字就行，不要实际生成。保留最初产品底图的外形、封面原有图案、文字和全部结构，只变化封面波点的疏密和拍摄背景。每套使用16:9、2K，给出清晰编号，方便下一轮按编号执行。'
        extra='补充这张图片供你观察拍摄背景氛围，只作背景分析，不作为后续生图输入，不把它的产品结构或封面内容移到最初产品上；后续生图只使用最初上传的产品底图。三套方案的桌面背景都设为浅米灰色，仍然只写文字，不要生成图片。'
        appended={};current={'case':names[0],'status':'running','user_text':user};results.append(current);record();started=time.monotonic()
        async def configure(r,p,h,q,receipts):
            materials=bind([Selection(kind='image',workspace_path=style.workspace_path)],user_id=f['user'],org_id=None,conversation_id=f['conv'])
            original=r.safe_point
            async def capture(event):
                with env['connect'](f) as c:
                    result=c.execute('SELECT capture_agent_task_user_change(%s,%s,%s)',(f['task'],f['token'],event)).fetchone()[0]
                assert result['outcome']=='captured'
                return result['inbox']
            r.task_user_change_capture=capture
            async def point(where,**kwargs):
                if where==SafePoint.MODEL_CHUNK and not appended:
                    payload={'message':extra,'attachments':materials}
                    with env['connect'](f) as c:
                        event=c.execute('SELECT append_conversation_steer(%s,%s,%s,%s,%s)',(f['conv'],f['task'],f['turn'],str(uuid4()),Jsonb(payload))).fetchone()[0]
                    appended.update(event_id=str(event['event_id']),at=time.time(),material=materials[0],during='actual_model_chunk')
                    r.push(ConversationCommand(command_id='test-inflight-'+appended['event_id'],command_type=CommandType.STEER,
                        conversation_id=f['conv'],task_id=f['task'],turn_id=f['turn'],event_id=appended['event_id'],payload=payload))
                    current['append']=copy.deepcopy(appended);record()
                await original(where,**kwargs)
            r.safe_point=point
        try:
            author=await env['run'](f,[TextPart(text=user),target],configure=configure)
            r,p,h,q,receipts,blocks=author;current['blocks']=blocks
            assert appended and not receipts
            catalog=co.catalog(r.task_execution_state);current['catalog']=catalog
            row,=catalog
            assert len(row['items'])==3
            visible='\n'.join(b.get('text','') for b in blocks if b.get('type')=='text')
            bodies=[co.load(r.task_execution_state,{'artifact_id':row['artifact_id'],'item_index':i})[2].body for i in range(1,4)]
            import re
            assert all(len(body)>80 and body in visible for body in bodies)
            assert all(re.search(r'米灰|beige[ -]?(?:gray|grey)',body,re.I) for body in bodies),'Each saved body must reflect the in-flight background requirement'
            assert r.task_user_changes and all(i.delivered for i in r.task_user_changes.items)
            with env['connect'](f) as c:
                event_status=c.execute('SELECT status FROM conversation_control_events WHERE id=%s',(appended['event_id'],)).fetchone()[0]
                assert event_status=='applied'
                assert c.execute("SELECT count(*) FROM tasks WHERE type='image' AND user_id=%s",(f['user'],)).fetchone()[0]==0
            model_calls=[w for w in env['wire'] if w.get('kind')=='model']
            assert any(style.url in json.dumps(w['body'],ensure_ascii=False) and extra in json.dumps(w['body'],ensure_ascii=False) for w in model_calls),'Appended image and text must reach an actual model request'
            before=r.task_execution_state;closed=end_candidate(before)
            cp=copy.deepcopy(r.checkpoint_base);cp.update(_task_execution=closed.model_dump(mode='json'),_task_execution_expected_revision=before.revision)
            with env['connect'](f) as c:env['save'](c,f,cp,expected=before.revision)
            await env['close_outer'](f,blocks)
            current.update(status='passed',seconds=round(time.monotonic()-started,3),image_acceptances=0,append_event_status=event_status,delivered=True);record()
            previous=None
            for index,(name,text) in enumerate([
                (names[1],'把第一个方案生成一张图片。输出约1920×1080，接近即可。按原方案执行，不重新写提示词。'),
                (names[2],'把你刚生成的那张图的桌面背景改为浅蓝色，产品外形、封面原有图案、文字、结构和全部物品布局保持不变，只改桌面颜色，直接生成一张。'),
                (names[3],'把你刚生成的那张浅蓝色背景图的桌面背景改为浅米灰色，产品外形、封面原有图案、文字、结构和全部物品布局保持不变，只改桌面颜色，直接生成一张。')]):
                current={'case':name,'status':'running','user_text':text};results.append(current);record();started=time.monotonic()
                f=await env['next_user'](f,text)
                with env['connect'](f) as c:source=c.execute('SELECT read_agent_task_followup(%s,%s)',(f['task'],f['token'])).fetchone()[0].get('source')
                output=await env['run'](f,[TextPart(text=text)],followup=source)
                rt,_,_,_,receipts,blocks=output;current.update(blocks=blocks,image_acceptances=len(receipts))
                assert len(receipts)==1,'Exactly one new image must be accepted'
                accepted=await env['worker'].refresh({'id':receipts[0]['receipt']['task_id'],'user_id':f['user'],'org_id':None})
                snap=accepted['request_params'][REQUEST_KEY];current['snapshot']=snap;record()
                assert (snap['aspect_ratio'],snap['resolution'])==('16:9','2K')
                assert len(snap['references'])==1
                if index==0:
                    assert receipts[0]['native']['prompt']==bodies[0],'Ordinal must execute the exact saved first body'
                    assert receipts[0]['native']['source_prompt']['content_object']['source']['item_index']==1
                    assert snap['references'][0]['content_sha256']==refs[0]['content_sha256']
                    assert snap['size_requirement']['approximate'] and snap['size_requirement']['requested_width']==1920
                else:
                    assert snap['references'][0]['content_sha256']==previous['sha256']
                    assert snap['references'][0]['message_id']==previous['receipt']['message_id']
                one=(await env['generate_all'](f,receipts,name))[0]
                assert not one['reused_completed_child'],'Full chain must not reuse a prior finished image'
                await env['complete_parent'](f,rt)
                previous=one;current.update(status='passed',seconds=round(time.monotonic()-started,3),image=one);record()
            posts=[w for w in env['wire'] if w.get('kind')=='supplier_submit']
            assert len(posts)==3 and len({r['image']['external_task_id'] for r in results if r.get('image')})==3
            assert all(w['body'].get('enable_thinking') is False or (w['body'].get('reasoning') or {}).get('effort')=='none' for w in env['wire'] if w.get('kind')=='model')
        except Exception as error:
            current.update(status='failed',seconds=round(time.monotonic()-started,3),error_type=type(error).__name__,error=str(error),trace=traceback.format_exc());record()
            for name in names:
                if name not in {r['case'] for r in results}:results.append({'case':name,'status':'not_run','reason':'Earlier dependent stage did not pass'})
            record();raise
    try:await _real_supplier_acceptance(content_db,monkeypatch,suite=suite)
    finally:
        with psycopg.connect(content_db) as c:c.execute((migration.parent/'rollback/380_conversation_steer_attachments_rollback.sql').read_text())
