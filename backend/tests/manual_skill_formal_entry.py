"""Opt-in real model/provider acceptance through the production Actor executor.

Only credentials, an isolated database and event capture are injected. Do not
replace execute_chat, stream preparation, Skill initialization or followup.
"""
import asyncio
import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from tests.manual_skill_supplier_acceptance import _real_supplier_acceptance

pytest_plugins=['tests.test_content_objects_postgres']
pytestmark=pytest.mark.skipif(os.environ.get('RUN_SKILL_REAL_SUPPLIER_ACCEPTANCE')!='1',reason='Explicit paid local acceptance')


def install_entry_schema(dsn,root,settings,monkeypatch):
    migrations=Path(__file__).parents[1]/'migrations'
    skillroot=root/'skills';skillroot.mkdir(exist_ok=True)
    monkeypatch.setattr(settings,'skill_storage_root',str(skillroot))
    for key in ['skill_runtime_enabled','skill_catalog_enabled','ecom_image_planning_enabled',
                'agent_task_model_directed_enabled','conversation_actor_web_enabled']:
        monkeypatch.setattr(settings,key,True)
    captures=[]
    with psycopg.connect(dsn) as c:
        # Match scripts/cloud/bootstrap/schema.sql. The shared minimal fixture
        # creates this legacy table as the cluster admin; quoted-image reads
        # through the real application role must use the tracked owner.
        c.execute('ALTER TABLE user_assets OWNER TO everydayai')
        c.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS scope_id TEXT, ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'web', ADD COLUMN IF NOT EXISTS context_summary TEXT, ADD COLUMN IF NOT EXISTS summary_revision bigint DEFAULT 0")
        c.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS model_id text, ADD COLUMN IF NOT EXISTS message_count integer DEFAULT 0, ADD COLUMN IF NOT EXISTS credits_consumed integer DEFAULT 0, ADD COLUMN IF NOT EXISTS last_message_preview text, ADD COLUMN IF NOT EXISTS chat_settings jsonb DEFAULT '{}', ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now(), ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now()")
        c.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS image_url text, ADD COLUMN IF NOT EXISTS video_url text, ADD COLUMN IF NOT EXISTS client_request_id text, ADD COLUMN IF NOT EXISTS task_id text, ADD COLUMN IF NOT EXISTS error jsonb, ALTER COLUMN id SET DEFAULT gen_random_uuid(), ALTER COLUMN created_at SET DEFAULT now()")
        c.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS features JSONB DEFAULT '{}'")
        credits=(migrations/'039_multi_tenant_organizations.sql').read_text()
        start=credits.index('CREATE OR REPLACE FUNCTION deduct_credits_atomic(')
        c.execute(credits[start:credits.index(';',credits.index('$$ LANGUAGE',start))+1])
        controls=(migrations/'138_conversation_control_events.sql').read_text()
        c.execute(controls[controls.index('CREATE OR REPLACE FUNCTION read_conversation_control_commands'):])
        for name in ['362_agent_task_public_progress.sql','373_agent_task_plan_progress.sql','374_agent_working_plan_followups.sql']:
            c.execute((migrations/name).read_text())
        for name in ['enqueue_generation_turn','claim_next_serial_generation_turn','commit_generation_turn','fail_generation_turn','begin_conversation_delivery_session',
                     'append_conversation_delivery_event','save_conversation_delivery_snapshot','update_generation_progress',
                     'append_conversation_control_command','read_conversation_control_commands','acknowledge_conversation_control_command','read_conversation_delivery_state']:
            for signature, in c.execute('SELECT oid::regprocedure::text FROM pg_proc WHERE pronamespace=\'public\'::regnamespace AND proname=%s',(name,)).fetchall():
                c.execute(sql.SQL('ALTER FUNCTION {} OWNER TO everydayai').format(sql.SQL(signature)))
        c.execute((migrations/'263_conversation_skill_bindings.sql').read_text())
        for name in ['skill_packages','skill_revisions','skill_assignments','skill_activation_audits','conversation_skill_bindings']:
            c.execute(sql.SQL('ALTER TABLE {} OWNER TO everydayai').format(sql.Identifier(name)))
        for fn, in c.execute("SELECT oid::regprocedure::text FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname LIKE 'skill_%'").fetchall():
            c.execute(sql.SQL('ALTER FUNCTION {} OWNER TO everydayai').format(sql.SQL(fn)))
        c.execute((migrations/'280_skill_platform_assignments.sql').read_text());c.execute('RESET ROLE')
        for name in ['060_org_departments.sql','061_org_positions.sql','064_org_member_assignments.sql']:
            c.execute((migrations/name).read_text())
        audit=(migrations/'046_tool_audit_log.sql').read_text()
        c.execute(audit[:audit.index('-- 2. 索引')])
        c.execute('CREATE TABLE tool_audit_log_current PARTITION OF tool_audit_log DEFAULT')
        c.execute((migrations/'088_extend_tool_audit_log.sql').read_text())
        for name in ['tool_audit_log','tool_audit_log_current']:
            c.execute(sql.SQL('ALTER TABLE {} OWNER TO everydayai').format(sql.Identifier(name)))
        c.execute((migrations/'267_mcp_tool_audit_columns.sql').read_text());c.execute('RESET ROLE')
        c.execute((migrations/'380_conversation_steer_attachments.sql').read_text())
        c.execute('GRANT SELECT ON org_departments,org_positions,org_member_assignments TO everydayai')
        for key in ['current','ecom']:
            cap=json.loads((Path('/private/tmp/skill-employee-20-20261122')/f'manual-compare-{key}-private.json').read_text())
            p,r=cap['package'],cap['revision'];raw=base64.b64decode(cap['document_base64'])
            assert hashlib.sha256(raw).hexdigest()==r['content_sha256']
            dest=skillroot/r['nas_path'];dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
            c.execute("SELECT set_config('app.access_kind','runtime_admin',true),set_config('app.request_id','isolated-formal-entry',true),set_config('app.skill_action','platform_assign',true)")
            c.execute('INSERT INTO skill_packages(id,skill_key,source,scope_kind) VALUES(%s,%s,%s,%s)',(p['id'],p['skill_key'],p['source'],p['scope_kind']))
            c.execute('INSERT INTO skill_revisions(id,package_id,revision,nas_path,content_sha256,body_sha256,summary,catalog_metadata) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                (r['id'],p['id'],r['revision'],r['nas_path'],r['content_sha256'],r['body_sha256'],r['summary'],Jsonb(r['catalog_metadata'])))
            assert cap['assignment']['org_id'] is None
            c.execute('INSERT INTO skill_assignments(org_id,package_id,revision_id,enabled) VALUES(NULL,%s,%s,true)',(p['id'],r['id']))
            captures.append(cap)
    return captures


@pytest.mark.asyncio
@pytest.mark.parametrize('task_db',['everydayai'],indirect=True,scope='module')
async def test_formal_entry_real_model_and_supplier_chain(content_db,monkeypatch):
    async def suite(env):
        from core.db_scope import AsyncScopedDatabaseClient,ScopedDatabaseClient,DatabaseScope,DatabaseAccessKind
        from core.local_db import AsyncLocalDBClient
        from services.conversation_command_store import DatabaseConversationCommandStore
        from services.conversation_subtasks import DatabaseConversationSubtaskStore
        from services.replay_checkpoint_store import DatabaseReplayCheckpointStore
        from services.conversation_execution import ConversationExecutionService,GenerationClaim
        from services.conversation_delivery import ActorTerminalDelivery
        from services.handlers.chat.executor import ChatGenerationExecutor
        from services.conversation_runtime import _build_delivery
        from services.handlers.chat.actor_sink import ActorWebSink
        from services.conversation_delivery_store import DatabaseConversationDeliveryStore
        from services.websocket_manager import ws_manager
        from services.model_gateway import ModelGateway
        from services.adapters.factory import create_chat_adapter
        from services.task_execution import content_objects as co
        from services.task_execution.contracts import TaskState
        from schemas.message import TextPart, ImagePart

        root=env['root'];settings=env['settings'];wire=env['wire'];rows=[];adapters=[]
        services=Path(__file__).parents[1]/'services'
        (root/'source-hashes.json').write_text(json.dumps({str(p.relative_to(services)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in services.rglob('*.py')},indent=2))
        captures=install_entry_schema(content_db,root,settings,monkeypatch)
        if image_model:=os.environ.get('SKILL_FORMAL_IMAGE_MODEL'):
            # Paid acceptance profile only; keep production defaults unchanged.
            assert image_model == 'gpt-image-2-text-to-image'
            monkeypatch.setattr('services.handlers.chat_image_request.DEFAULT_IMAGE_MODEL_ID',image_model)
        if model:=os.environ.get('SKILL_FORMAL_PLANNER_MODEL'):
            monkeypatch.setattr(settings,'ecom_image_planning_non_thinking_model',model)
        monkeypatch.setattr(settings,'database_url',psycopg.conninfo.make_conninfo(content_db,user='everydayai'))
        def factory(model_id,**kwargs):
            adapter=create_chat_adapter(model_id,**kwargs);adapters.append(adapter)
            original=adapter._get_client
            async def client():
                value=await original()
                if not getattr(value,'_formal_capture',False):
                    async def capture(req):
                        wire.append({'kind':'formal_model_request','model':model_id,'body':json.loads(req.content),'at':time.monotonic()});env['persist']()
                    async def capture_response(response):
                        row={'kind':'formal_model_response','model':model_id,'status':response.status_code,
                            'path':response.request.url.path,'request_id':response.headers.get('x-request-id'),
                            'raw_sse':'','complete':False}
                        wire.append(row)
                        upstream=response.stream
                        class CaptureStream(httpx.AsyncByteStream):
                            async def __aiter__(self):
                                chunks=[]
                                try:
                                    async for data in upstream:
                                        chunks.append(data)
                                        yield data
                                    row['complete']=True
                                finally:
                                    row['raw_sse']=b''.join(chunks).decode('utf-8',errors='replace')
                                    env['persist']()
                            async def aclose(self):
                                await upstream.aclose()
                        response.stream=CaptureStream()
                    value.event_hooks['request'].append(capture)
                    value.event_hooks['response'].append(capture_response)
                    value._formal_capture=True
                return value
            adapter._get_client=client
            return adapter
        gateway=ModelGateway(adapter_factory=factory,event_publisher=SimpleNamespace(publish=lambda *a,**k:None))
        monkeypatch.setattr('services.model_gateway._MODEL_GATEWAY',gateway)
        from services.agent.image.ecommerce_planner import workflow as workflow_module
        classify=workflow_module.classify_workflow
        from services.intent_router import IntentRouter
        route_transport=IntentRouter.call_tool_model
        async def capture_route_transport(self,**kwargs):
            row={'kind':'formal_router_transport','input':kwargs['text']}
            wire.append(row)
            try:
                value=await route_transport(self,**kwargs)
                row['response']=value
                return value
            except Exception as error:
                row['error_type']=type(error).__name__
                raise
            finally:
                env['persist']()
        monkeypatch.setattr(IntentRouter,'call_tool_model',capture_route_transport)
        async def capture_route(*args,**kwargs):
            nonlocal recovery_injected
            result=await classify(*args,**kwargs)
            wire.append({'kind':'formal_workflow_route','result':result,'selected_skill':kwargs.get('selected_skill')});env['persist']()
            if os.environ.get('SKILL_FORMAL_CRASH_METADATA')=='1' and not recovery_injected:
                with psycopg.connect(content_db) as c:
                    saved=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',
                        (rows[-1]['task_id'],)).fetchone()[0]
                journal=saved['_content_response_v1']
                assert journal['phase']=='proposed' and journal['control_pending'] is True and journal['text']
                rows[-1]['metadata_original_sha256']=hashlib.sha256(journal['text'].encode()).hexdigest()
                recovery_injected=True
                wire.append({'kind':'formal_metadata_worker_loss'});env['persist']()
                raise asyncio.CancelledError('Test worker loss after durable original before control metadata binding')
            return result
        monkeypatch.setattr(workflow_module,'classify_workflow',capture_route)
        f=env['seed'](content_db)
        scope=DatabaseScope(f['user'],None,DatabaseAccessKind.RUNTIME)
        syncdb=ScopedDatabaseClient(env['database'],scope)
        asyncclient=AsyncLocalDBClient(psycopg.conninfo.make_conninfo(content_db,user='everydayai'),min_size=1,max_size=6)
        await asyncclient.open()
        asyncdb=AsyncScopedDatabaseClient(asyncclient,scope)
        monkeypatch.setattr('core.database.get_db',lambda:syncdb)
        def sink(task,claim,cancel):
            return ActorWebSink(asyncdb,_build_delivery(task,claim),cancel,ws_manager,
                delivery_store=DatabaseConversationDeliveryStore(asyncdb))
        checkpoint_store=DatabaseReplayCheckpointStore(asyncdb)
        write=checkpoint_store.write
        recovery_injected=False
        async def capture_write(**kwargs):
            nonlocal recovery_injected
            try:
                result=await write(**kwargs)
                native=kwargs.get('payload',{}).get('_native_plan_text_delivery_v1',{})
                if os.environ.get('SKILL_FORMAL_MANUAL')=='1' and not recovery_injected and native.get('phase')=='pending':
                    recovery_injected=True
                    wire.append({'kind':'formal_worker_loss','at_model_calls':len([w for w in wire if w.get('kind')=='formal_model_request'])});env['persist']()
                    raise asyncio.CancelledError('Test worker loss after actual durable ready-plan write')
                return result
            except Exception as error:
                wire.append({'kind':'formal_checkpoint_error','error':str(error)});env['persist']();raise
        checkpoint_store.write=capture_write
        executor=ChatGenerationExecutor(asyncdb,handler_db_factory=lambda:syncdb,sink_factory=sink,
            command_store=DatabaseConversationCommandStore(asyncdb),subtask_store=DatabaseConversationSubtaskStore(asyncdb),
            replay_checkpoint_store=checkpoint_store)
        service=ConversationExecutionService(asyncdb,executor,renew_interval_seconds=5,terminal_observer=ActorTerminalDelivery(asyncdb,ws_manager))
        from fastapi import FastAPI
        from api import deps
        from api.routes.message import router
        from api.routes import ws
        import httpx
        app=FastAPI();app.include_router(router)
        app.dependency_overrides[deps.get_org_context]=lambda:deps.OrgContext(user_id=f['user'])
        app.dependency_overrides[deps.get_scoped_db]=lambda:syncdb
        app.dependency_overrides[deps.get_task_limit_service]=lambda:env['limiter']
        from core.limiter import limiter
        monkeypatch.setattr(limiter,'enabled',False)
        http=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://isolated-local-test')
        monkeypatch.setattr(ws,'get_db',lambda:syncdb)
        async def get_async():return asyncdb
        monkeypatch.setattr(ws,'get_async_db',get_async)
        async def capture_connection(_connection,event):env['events'].append(event);env['persist']()
        monkeypatch.setattr(ws_manager,'send_to_connection',capture_connection)
        with psycopg.connect(content_db) as c:
            c.execute('DELETE FROM tasks WHERE id=%s',(f['task'],));c.execute('DELETE FROM messages WHERE id=%s',(f['message'],))
        def record():
            (root/'formal-entry.json').write_text(json.dumps(rows,ensure_ascii=False,default=str,indent=2))
        async def run(facts,content,selection=None,append=None):
            text='\n'.join(p.text for p in content if p.type=='text')
            params={'thinking_mode':'disabled','permission_mode':'auto'}
            with psycopg.connect(content_db) as c:
                c.execute('UPDATE users SET credits=10000 WHERE id=%s',(facts['user'],))
                c.execute('UPDATE conversations SET scope_id=user_id::text WHERE id=%s',(facts['conv'],))
            body={'content':[p.model_dump(mode='json') for p in content],'generation_type':'chat','model':'qwen3.8-max','params':params}
            if selection:body['selected_skill']=selection
            response=await http.post(f"/conversations/{facts['conv']}/messages/generate",json=body)
            assert response.status_code==200,response.text
            output=response.json()['assistant_message']['id']
            claim=await service.claim_serial(facts['conv']);assert claim
            facts.update(task=claim.task_id,turn=claim.turn_id,token=claim.execution_token,message=claim.input_message_id)
            row={'request':text,'task_id':facts['task'],'selection':selection,'status':'running',
                'planner_model':settings.ecom_image_planning_non_thinking_model};rows.append(row);record();started=time.monotonic()
            offset=len(wire)
            pending=asyncio.create_task(service.execute_claim(claim))
            if append:
                deadline=time.monotonic()+45
                while not any(w.get('kind')=='formal_model_request' for w in wire[offset:]):
                    if pending.done():
                        result=await pending
                        with psycopg.connect(content_db) as c:
                            current=c.execute('SELECT to_jsonb(t) FROM tasks t WHERE id=%s',(facts['task'],)).fetchone()[0]
                        row.update(status=current['status'],error=current.get('error_message'),result=result);record()
                        raise AssertionError(row['error'] or result)
                    assert time.monotonic()<deadline,'No model request before append deadline'
                    await asyncio.sleep(.02)
                request_id=str(uuid4())
                await ws._handle_message('isolated-user',f['user'],{'type':'user_steer','payload':{
                    'task_id':facts['task'],'conversation_id':f['conv'],'request_id':request_id,**append}})
                receipt=next(e for e in env['events'] if e.get('payload',{}).get('request_id')==request_id)
                row['append_receipt']=receipt
                assert receipt['payload']['outcome']=='enqueued',receipt
            try:result=await pending
            except asyncio.CancelledError:
                assert recovery_injected
                before=len([w for w in wire if w.get('kind')=='formal_model_request'])
                result=await service.execute_claim(claim)
                after=len([w for w in wire if w.get('kind')=='formal_model_request'])
                row['recovery']={'same_claim':True,'model_calls_before':before,'model_calls_after':after}
                assert before==after,'Ready-plan recovery must not call a model or regenerate the plan'
            with psycopg.connect(content_db) as c:
                current=c.execute('SELECT to_jsonb(t) FROM tasks t WHERE id=%s',(facts['task'],)).fetchone()[0]
                message=c.execute('SELECT to_jsonb(m) FROM messages m WHERE id=%s',(output,)).fetchone()[0]
                checkpoint=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(facts['task'],)).fetchone()
            row.update(status=current['status'],result=result,seconds=time.monotonic()-started,message=message,checkpoint=checkpoint[0] if checkpoint else None);record()
            if row.get('metadata_original_sha256'):
                assert hashlib.sha256(row['checkpoint']['_content_response_v1']['text'].encode()).hexdigest()==row['metadata_original_sha256']
            assert current['status']=='completed',{'result':result,'failure':current.get('fail_code'),'error':current.get('error_message')}
            if append:
                delivered=row['checkpoint']['_task_user_changes']['items']
                assert any(i['message']==append['message'] and i['delivered'] for i in delivered)
                assert any(append['message'] in str(w.get('body',{})) for w in wire[offset:] if w.get('kind')=='formal_model_request')
            return row
        try:
            if os.environ.get('SKILL_FORMAL_SMOKE')=='1':
                await run(f,[TextPart(text='你好，请直接回复测试成功，不使用工具。')]);return
            if os.environ.get('SKILL_FORMAL_TEXT_CONDITIONS')=='1':
                from services.adapters.kie.client import KieClient
                async def no_media_submission(*args,**kwargs):
                    raise AssertionError('Text-only waiting test must not submit a paid media task')
                monkeypatch.setattr(KieClient,'create_task_once',no_media_submission)
                for suffix in ('等我说可以了再做成视频。','等我确认后再做成视频。'):
                    facts_for_text={**f,'conv':str(uuid4())}
                    with psycopg.connect(content_db) as c:
                        c.execute('INSERT INTO conversations(id,user_id,scope_id) VALUES(%s,%s,%s)',
                            (facts_for_text['conv'],f['user'],str(f['user'])))
                    authored=await run(facts_for_text,[TextPart(text='产品是奶白色磁吸扣活页本，封面有波点，面向学生，发小红书，每条15秒。帮我写三条产品介绍视频脚本，先给我看，'+suffix)])
                    assert '_task_execution' in authored['checkpoint'], 'The model wrote a reply but did not register the scripts and their waiting conditions'
                    state=TaskState.model_validate(authored['checkpoint']['_task_execution'])
                    saved,=co.catalog(state)
                    _,document,_=co._load_record(state,{'artifact_id':saved['artifact_id'],'item_index':1})
                    assert len(document.document.items)==3
                    assert document.confirmation_items==(1,2,3) and document.confirmation_tool=='generate_video'
                    assert all(len(item.body)>30 for item in document.document.items)
                    visible=authored['message']['content']
                    visible=json.loads(visible) if isinstance(visible,str) else visible
                    assert all(item.body in '\n'.join(p.get('text','') for p in visible) for item in document.document.items)
                    with psycopg.connect(content_db) as c:
                        assert c.execute("SELECT count(*) FROM tasks WHERE type IN ('image','video')").fetchone()[0]==0
                assert not any(w.get('kind')=='supplier_submit' for w in wire)
                return
            prior=json.loads((Path(os.environ['SKILL_FAILURE_CAPTURE_ROOT'])/'fixed-live-image-off-3.json').read_text())
            ref=sorted(prior['native'][0]['recipes'][0]['resolved'],key=lambda x:x['content_index'])[0]
            source=next((Path(os.environ['SKILL_FAILURE_CAPTURE_ROOT'])/'workspace').rglob(Path(ref['workspace_path']).name))
            assert hashlib.sha256(source.read_bytes()).hexdigest()==ref['content_sha256']
            photo=await env['upload'](f,source,'正式入口/原产品图.png')
            canvas_handoff=os.environ.get('SKILL_FORMAL_CANVAS_HANDOFF')=='1'
            style_source=source
            if canvas_handoff:
                target=sorted(prior['native'][0]['recipes'][0]['resolved'],key=lambda x:x['content_index'])[1]
                style_source=next((Path(os.environ['SKILL_FAILURE_CAPTURE_ROOT'])/'workspace').rglob(Path(target['workspace_path']).name))
                assert hashlib.sha256(style_source.read_bytes()).hexdigest()==target['content_sha256']
                assert (target['width'],target['height'])!=(ref['width'],ref['height'])
            style=await env['upload'](f,style_source,'正式入口/追加参考图'+style_source.suffix)
            cap=next(c for c in captures if c['package']['skill_key']=='ecommerce-main-images')
            selection={'skill_id':'ecommerce-main-images','revision':cap['revision']['revision']} if os.environ.get('SKILL_FORMAL_MANUAL')=='1' else None
            customer_language=os.environ.get('SKILL_FORMAL_CUSTOMER_LANGUAGE')=='1'
            waiting_scenario=os.environ.get('SKILL_FORMAL_WAITING')=='1'
            expected_canvas=('auto','1K') if customer_language else ('16:9','2K')
            text='参考这张真实产品图，帮我策划3套京东电商主图方案和可以生图的可复制提示词，先只写文字，不生成图片。保留商品结构和原有文字，只调整背景和波点疏密。每套包含完整正向及负面提示词，编号。每套16:9、2K。'
            if os.environ.get('SKILL_FORMAL_PLAIN')=='1':
                text='参考这张真实产品图，帮我写3套可以生图的可复制提示词，先只写文字，不生成图片。保留商品结构和原有文字，只调整背景和波点疏密。每套包含完整正向及负面提示词，编号。每套16:9、2K。'
            if customer_language:
                text='参考这个产品，帮我想3个新款，把提示词写出来，我先看看。'
                if os.environ.get('SKILL_FORMAL_CUSTOMER_MAIN')=='1':
                    text='参考这个产品，帮我写3套主图方案，我先看看。'
            if waiting_scenario:
                assert customer_language and os.environ.get('SKILL_FORMAL_CUSTOMER_MAIN')!='1'
                text+='三套都等我说可以了再做图。'
            append_only=os.environ.get('SKILL_FORMAL_APPEND_ONLY')=='1'
            if append_only:
                assert customer_language and os.environ.get('SKILL_FORMAL_CUSTOMER_MAIN')!='1'
                text='我接下来补产品图，帮我想3个新款，把提示词写出来，我先看看。'
            authored=await run(f,[TextPart(text=text),*([] if append_only else [photo])],selection,append={
                'message':('三套都用后补这个本子做主体，上一张参考波点，背景用浅米灰色。' if canvas_handoff else
                    '这个也给你参考，背景用浅米灰色。' if customer_language else
                    '补充这张图片供观察，全部方案背景使用浅米灰色，仍然只写文字，不要生成图片。'),
                'attachments':[{'kind':'image','workspace_path':style.workspace_path}]})
            with psycopg.connect(content_db) as c:
                plans=c.execute('SELECT to_jsonb(p) FROM ecom_image_plans p WHERE parent_task_id=%s',(f['task'],)).fetchall()
                assert c.execute("SELECT count(*) FROM tasks WHERE type='image'").fetchone()[0]==0
            authored['server_plans']=[p[0] for p in plans];record()
            if (selection and customer_language and os.environ.get('SKILL_FORMAL_CUSTOMER_MAIN')=='1'
                    and len(plans)==1 and plans[0][0]['status']=='needs_input'):
                first_plan=plans[0][0]
                assert first_plan['image_count']==3
                before=authored['message']['content']
                before=json.loads(before) if isinstance(before,str) else before
                assert any(p.get('type')=='text' and p.get('text') for p in before)
                authored['clarification_reply']='就是图里的本子，只展示封面外观，内页规格先别写，旁边配件不算赠品。继续写3套方案给我看看。'
                record()
                authored=await run(f,[TextPart(text=authored['clarification_reply'])])
                with psycopg.connect(content_db) as c:
                    plans=c.execute('SELECT to_jsonb(p) FROM ecom_image_plans p WHERE parent_task_id=%s',(f['task'],)).fetchall()
                    assert c.execute("SELECT count(*) FROM tasks WHERE type='image'").fetchone()[0]==0
                authored['server_plans']=[p[0] for p in plans];record()
                assert len(plans)==1 and plans[0][0]['supersedes_plan_id']==first_plan['id']
            native_expected=bool(selection and (not customer_language or os.environ.get('SKILL_FORMAL_CUSTOMER_MAIN')=='1'))
            if customer_language and os.environ.get('SKILL_FORMAL_CUSTOMER_MAIN')!='1':
                assert not plans, 'Product design must not enter the inapplicable ecommerce workflow'
            if native_expected or plans:
                assert len(plans)==1 and plans[0][0]['status']=='ready' and len(plans[0][0]['items'])==3
                expected_prompt=plans[0][0]['items'][0]['request_text']
                expected_prompts={plans[0][0]['items'][i]['request_text'] for i in (0,2)}
            else:
                assert not plans, 'Product design must not enter the inapplicable ecommerce workflow'
                state=TaskState.model_validate(authored['checkpoint']['_task_execution'])
                saved=co.catalog(state)[0];assert len(saved['items'])==3
                if append_only or canvas_handoff:
                    _,source_record,_=co._load_record(state,{'artifact_id':saved['artifact_id'],'item_index':1})
                    delivered=authored['checkpoint']['_task_user_changes']['items']
                    resource=next(item['attachments'][0]['resource_ref'] for item in delivered if item.get('attachments'))
                    assert len(source_record.source_images)==(2 if canvas_handoff else 1)
                    assert source_record.source_images[-1].reference.resource_ref==resource
                    if canvas_handoff:
                        for item in source_record.document.items:
                            assert [m.reference for m in item.materials if m.usage=='input']==[i.reference for i in source_record.source_images]
                            assert item.options.get('aspect_ratio')=='auto'
                if waiting_scenario:
                    artifact,saved_record,_=co._load_record(state,{'artifact_id':saved['artifact_id'],'item_index':1})
                    assert saved_record.confirmation_items==(1,2,3) and saved_record.confirmation_tool=='generate_image'
                    assert all(len(item.body)>30 for item in saved_record.document.items)
                expected_prompt=co.load(state,{'artifact_id':saved['artifact_id'],'item_index':1})[2].body
                expected_prompts={co.load(state,{'artifact_id':saved['artifact_id'],'item_index':i})[2].body for i in (1,3)}
            visible=authored['message']['content']
            visible=json.loads(visible) if isinstance(visible,str) else visible
            shown='\n'.join(p.get('text','') for p in visible)
            assert expected_prompt in shown
            if customer_language and not native_expected and not waiting_scenario:
                prompts=re.findall(r'```(?:prompt|text|plaintext)?\r?\n(.*?)\r?\n```',shown,re.S)
                assert len(prompts)==3, 'Each execution prompt must have an explicit boundary'
                assert [co.load(state,{'artifact_id':saved['artifact_id'],'item_index':i})[2].body
                        for i in (1,2,3)]==prompts, 'Presentation or summary entered an execution prompt'
            if os.environ.get('SKILL_FORMAL_AUTHOR_ONLY')=='1':
                return
            previous_sha=ref['content_sha256']
            followups=([('第一个和第三个做成图吧。','ordinal'),
                ('这张背景换成浅蓝色。','edit-blue'),
                ('再换成浅灰色。','edit-gray')] if customer_language else [
                ('第一个和第三个帮我做成图片。按已保存原文执行，不重写。尺寸约1920×1080，匹配接近尺寸即可。','ordinal'),
                ('把刚才生成的那张图片背景改成浅蓝色，商品和原有文字保持不变。','edit-blue'),
                ('把刚才生成的那张图片背景再改成浅灰色，保留商品结构和原有文字。','edit-gray')])
            for text,label in followups:
                parts=[TextPart(text=text)]
                if label=='edit-blue':
                    # Two sibling outputs make “that image” ambiguous. Exercise
                    # the existing page quote contract, then implicit followup
                    # after the one resulting edit. Keep exact hash assertions.
                    selected=completed[-1]
                    parts.append(ImagePart(**{**selected['published_part'],
                        'source_message_id':selected['receipt']['message_id'],
                        'source_content_index':0,'source_task_id':selected['receipt']['task_id']}))
                row=await run(f,parts)
                with psycopg.connect(content_db) as c:
                    children=c.execute("SELECT id,assistant_message_id,request_params FROM tasks WHERE type='image' AND request_params->'_media_request_v1'->'origin'->>'parent_task_id'=%s ORDER BY created_at,id",(f['task'],)).fetchall()
                assert len(children)==(2 if label=='ordinal' else 1),children
                snapshots=[child[2]['_media_request_v1'] for child in children]
                if native_expected and label=='ordinal':
                    assert_native_ordinal(plans[0][0],snapshots)
                if waiting_scenario and label=='ordinal':
                    proofs=row['checkpoint']['_media_decisions_v1']['items']
                    assert any(p['artifact_id']==saved['artifact_id'] and p['revision']==saved['revision']
                               and p['approved_items']==[1,3] for p in proofs)
                for snapshot in snapshots:
                    assert (snapshot['aspect_ratio'],snapshot['resolution'])==expected_canvas
                    assert snapshot['references'][0]['content_sha256']==previous_sha
                    if canvas_handoff and label=='ordinal':
                        assert [r['content_sha256'] for r in snapshot['references']]==[ref['content_sha256'],target['content_sha256']]
                        assert snapshot['plan_item_id'] in {saved['artifact_id']+':1',saved['artifact_id']+':3'}
                if label=='ordinal':assert {snapshot['prompt'] for snapshot in snapshots}==expected_prompts
                completed=await env['generate_all'](f,[{'receipt':{'task_id':str(child[0]),'message_id':str(child[1])},'native':snapshot}
                    for child,snapshot in zip(children,snapshots)],label,parent_terminal=True)
                previous_sha=completed[-1]['sha256']
                from services.handlers.image_size_requirements import nearest_reference_ratio
                from services.adapters.kie.configs import IMAGE_MODEL_CONFIGS
                width,height=completed[-1]['actual_dimensions']
                expected_canvas=(nearest_reference_ratio({'width':width,'height':height},
                    IMAGE_MODEL_CONFIGS[completed[-1]['snapshot']['model']]['supported_sizes']),expected_canvas[1])
            # Replay the exact two-image failure in a fresh conversation, with
            # the same user and original files, through the same HTTP/Actor path.
            incident=json.loads(Path(__file__).with_name('fixtures').joinpath('chat_skill_failures_20261122.json').read_text())['canvas']
            f['conv']=str(uuid4())
            with psycopg.connect(content_db) as c:
                c.execute('INSERT INTO conversations(id,user_id,scope_id) VALUES(%s,%s,%s)',(f['conv'],f['user'],f['user']))
            refs=sorted(prior['native'][0]['recipes'][0]['resolved'],key=lambda x:x['content_index'])
            images=[]
            for i,reference in enumerate(refs):
                path=next((Path(os.environ['SKILL_FAILURE_CAPTURE_ROOT'])/'workspace').rglob(Path(reference['workspace_path']).name))
                assert hashlib.sha256(path.read_bytes()).hexdigest()==reference['content_sha256']
                name=f'用户图{i+1}' if customer_language else f'失败原图{i+1}'
                images.append(await env['upload'](f,path,f'正式入口/{name}{path.suffix}'))
            await run(f,[TextPart(text=incident['user_text']),*images])
            with psycopg.connect(content_db) as c:
                children=c.execute("SELECT id,assistant_message_id,request_params FROM tasks WHERE type='image' AND request_params->'_media_request_v1'->'origin'->>'parent_task_id'=%s",(f['task'],)).fetchall()
            assert len(children)==1
            snapshot=children[0][2]['_media_request_v1']
            assert (snapshot['aspect_ratio'],snapshot['resolution'])==(incident['expected_aspect_ratio'],incident['expected_resolution'])
            assert [x['content_sha256'] for x in snapshot['references']]==[x['content_sha256'] for x in refs]
            assert '输入图片1对应用户图1。' in snapshot['prompt']
            assert '输入图片2对应用户图2。' in snapshot['prompt']
            await env['generate_all'](f,[{'receipt':{'task_id':str(children[0][0]),'message_id':str(children[0][1])},'native':snapshot}],'original-two-image-failure',parent_terminal=True)
            # A normal conversational follow-up must not submit another image.
            with psycopg.connect(content_db) as c:
                count_before=c.execute("SELECT count(*) FROM tasks WHERE type='image'").fetchone()[0]
            chat=await run(f,[TextPart(text='刚才这张改了什么，简单说一下。')])
            chat_parts=chat['message']['content']
            chat_parts=json.loads(chat_parts) if isinstance(chat_parts,str) else chat_parts
            assert any(p.get('type')=='text' and p.get('text','').strip() for p in chat_parts)
            with psycopg.connect(content_db) as c:
                assert c.execute("SELECT count(*) FROM tasks WHERE type='image'").fetchone()[0]==count_before
        finally:
            record()
            for adapter in adapters:await adapter.close()
            await http.aclose()
            await asyncclient.close()
    await _real_supplier_acceptance(content_db,monkeypatch,suite=suite)
    if (os.environ.get('SKILL_FORMAL_IMAGE_MODEL') and os.environ.get('SKILL_FORMAL_AUTHOR_ONLY')!='1'
            and os.environ.get('SKILL_FORMAL_TEXT_CONDITIONS')!='1'):
        assert_image2_transport(json.loads(Path(os.environ['SKILL_SUPPLIER_EVIDENCE']).joinpath('evidence.json').read_text()))


def assert_native_ordinal(plan,snapshots):
    selected=[plan['items'][i] for i in (0,2)]
    assert len(snapshots)==len(selected)
    for item,snapshot in zip(selected,snapshots,strict=True):
        assert snapshot['origin'].get('plan_source')=={
            'plan_id':plan['id'],'revision':plan['plan_revision'],'item_id':item['item_id'],
            'request_text_sha256':item['request_text_sha256']}, 'Native numbered execution lost its saved plan identity'
        assert snapshot['prompt']==item['request_text']


def assert_image2_transport(evidence):
    from urllib.parse import unquote, urlparse
    submitted=[w for w in evidence['wire'] if w.get('kind')=='supplier_submit']
    assert len(submitted)==len(evidence['cases'])==5
    for case,post in zip(evidence['cases'],submitted,strict=True):
        assert post['phase']==case['label']
        assert post['body']['model']==case['snapshot']['model']=='gpt-image-2-image-to-image'
        assert set(post['body']) == {'model', 'input'}
        assert set(post['body']['input']) == {'prompt', 'input_urls', 'aspect_ratio', 'resolution'}
        assert post['body']['input']['prompt']==case['snapshot']['prompt']
        urls=post['body']['input']['input_urls']
        refs=case['snapshot']['references']
        assert len(urls)==len(refs)
        assert all(unquote(urlparse(url).path).endswith('/'+ref['workspace_path'])
            for url,ref in zip(urls,refs,strict=True))
        for ref in refs:
            identities=[ref[key] for key in ('source_id','file_id','resource_ref','asset_id',
                'source_message_id','message_id','source_asset_id','quoted_message_id',
                'quoted_task_id','task_id') if isinstance(ref.get(key),str)]
            assert identities and all(value not in case['snapshot']['prompt'] for value in identities)
    # The original two-image incident must retain the actual main-model edit,
    # rather than replace it with the user's short request during binding.
    edits=[]
    for wire in evidence['wire']:
        if wire.get('kind')!='formal_model_request':
            continue
        body=wire['body']
        calls=[message for message in body.get('input',[]) if message.get('type')=='function_call']
        calls += [call['function'] for message in body.get('messages',[]) for call in message.get('tool_calls',[])]
        for call in calls:
            if call.get('name')!='generate_image':
                continue
            args=json.loads(call['arguments'])
            polished=args.get('edit_instruction') or args.get('draft',{}).get('body')
            if polished:
                edits.append(polished)
    assert edits and evidence['cases'][-1]['snapshot']['prompt'].endswith(edits[-1])
