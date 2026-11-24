"""Internal-loop timing diagnostic; formal acceptance is manual_skill_formal_entry.

This deliberately measures isolated activation stages, not the HTTP/Actor entry.
"""
import copy,json,hashlib,base64,os,time,traceback
from dataclasses import replace
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

@pytest.mark.asyncio
@pytest.mark.parametrize('task_db',['everydayai'],indirect=True,scope='module')
async def test_manual_activation_and_plain_chat_comparison(content_db,monkeypatch):
    async def suite(env):
        from schemas.message import TextPart
        from services.skills.runtime import create_skill_runtime
        from services.skills.selection import SkillSelection
        from services.handlers.chat.execution_engine import _apply_skill_context,_build_replay_context
        from services.conversation_commands import SafePoint
        from services.agent.observability.chat_lifecycle import capture_chat_lifecycle
        from services.agent.execution_budget import ExecutionBudget
        from services.task_execution import content_objects as co
        from services.task_execution.direct_loop import end_candidate
        from services.handlers.chat_image_request import REQUEST_KEY
        root=env['root'];settings=env['settings'];rows=[];schema=Path(__file__).parents[1]/'migrations'
        captures=[]
        for key in ['current','ecom']:
            path=Path('/private/tmp/skill-employee-20-20261122')/f'manual-compare-{key}-private.json'
            if path.exists() and path.stat().st_size:captures.append(json.loads(path.read_text()))
        skillroot=root/'skills';skillroot.mkdir()
        monkeypatch.setattr(settings,'skill_storage_root',str(skillroot))
        for key in ['skill_runtime_enabled','skill_catalog_enabled','ecom_image_planning_enabled']:monkeypatch.setattr(settings,key,True)
        # Planner and workflow submodels still use the real shared gateway.
        from services.model_gateway import ModelGateway
        from services.adapters.factory import create_chat_adapter
        subadapters=[]
        def subfactory(model_id,**kwargs):
            adapter=create_chat_adapter(model_id,**kwargs);subadapters.append(adapter)
            get_client=adapter._get_client
            async def client():
                value=await get_client()
                if not getattr(value,'_skill_compare_hook',False):
                    async def request(req):
                        env['wire'].append({'kind':'submodel','model':model_id,'at_monotonic':time.monotonic(),'body':json.loads(req.content)});env['persist']()
                    value.event_hooks['request'].append(request);value._skill_compare_hook=True
                return value
            adapter._get_client=client
            return adapter
        subgateway=ModelGateway(adapter_factory=subfactory,event_publisher=SimpleNamespace(publish=lambda *a,**k:None))
        monkeypatch.setattr('services.model_gateway._MODEL_GATEWAY',subgateway)
        # Add the tracked catalog/binding contracts absent from the minimal image fixture.
        with psycopg.connect(content_db) as c:
            c.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS scope_id TEXT,ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'web'")
            c.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS features JSONB DEFAULT '{}'")
            c.execute((schema/'263_conversation_skill_bindings.sql').read_text())
            for name in ['skill_packages','skill_revisions','skill_assignments','skill_activation_audits','conversation_skill_bindings']:
                c.execute(sql.SQL('ALTER TABLE {} OWNER TO everydayai').format(sql.Identifier(name)))
            funcs=c.execute("SELECT oid::regprocedure::text FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname LIKE 'skill_%'").fetchall()
            for fn, in funcs:c.execute(sql.SQL('ALTER FUNCTION {} OWNER TO everydayai').format(sql.SQL(fn)))
            c.execute((schema/'280_skill_platform_assignments.sql').read_text());c.execute('RESET ROLE')
            if os.environ.get('SKILL_COMPARE_ARM')=='manual':
                for name in ['060_org_departments.sql','061_org_positions.sql','064_org_member_assignments.sql']:
                    c.execute((schema/name).read_text())
                audit=(schema/'046_tool_audit_log.sql').read_text()
                c.execute(audit[:audit.index('-- 2. 索引')])
                c.execute('CREATE TABLE tool_audit_log_current PARTITION OF tool_audit_log DEFAULT')
                c.execute((schema/'088_extend_tool_audit_log.sql').read_text())
                for name in ['tool_audit_log','tool_audit_log_current']:
                    c.execute(sql.SQL('ALTER TABLE {} OWNER TO everydayai').format(sql.Identifier(name)))
                c.execute((schema/'267_mcp_tool_audit_columns.sql').read_text());c.execute('RESET ROLE')
                c.execute('GRANT SELECT ON org_departments,org_positions,org_member_assignments TO everydayai')
                c.execute('GRANT INSERT,SELECT ON tool_audit_log TO everydayai')
            for cap in captures:
                p,r=cap['package'],cap['revision'];raw=base64.b64decode(cap['document_base64'])
                assert hashlib.sha256(raw).hexdigest()==r['content_sha256']
                dest=skillroot/r['nas_path'];dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
                c.execute("SELECT set_config('app.access_kind','runtime_admin',true),set_config('app.request_id','isolated-skill-benchmark-seed',true),set_config('app.skill_action','platform_assign',true)")
                c.execute('INSERT INTO skill_packages(id,skill_key,source,scope_kind) VALUES(%s,%s,%s,%s)',(p['id'],p['skill_key'],p['source'],p['scope_kind']))
                c.execute('INSERT INTO skill_revisions(id,package_id,revision,nas_path,content_sha256,body_sha256,summary,catalog_metadata) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                    (r['id'],p['id'],r['revision'],r['nas_path'],r['content_sha256'],r['body_sha256'],r['summary'],Jsonb(r['catalog_metadata'])))
                assert cap['assignment']['org_id'] is None,'Keep the actual global grant type'
                c.execute('INSERT INTO skill_assignments(org_id,package_id,revision_id,enabled) VALUES(NULL,%s,%s,true)',(p['id'],r['id']))
        def record():
            (root/'activation-comparison.json').write_text(json.dumps(rows,ensure_ascii=False,default=str,indent=2))
        prior=json.loads((Path(os.environ['SKILL_FAILURE_CAPTURE_ROOT'])/'fixed-live-image-off-3.json').read_text())
        ref=sorted(prior['native'][0]['recipes'][0]['resolved'],key=lambda x:x['content_index'])[0]
        original=next((Path(os.environ['SKILL_FAILURE_CAPTURE_ROOT'])/'workspace').rglob(Path(ref['workspace_path']).name))
        assert hashlib.sha256(original.read_bytes()).hexdigest()==ref['content_sha256']
        user=('这是白色手账本。请基于这张真实产品图写3套京东电商主图方案，先只写方案和完整生图提示词，不生成图片。'
              '保留原产品的外形、原有封面图案文字和全部结构，只变化波点疏密与拍摄背景；不编造未提供的规格、材质、性能或卖点。'
              '每套明确方案内容、视觉设定、参考图用法、生图提示词和负面提示词，并给出编号。输出16:9、2K，中文。')
        arm=os.environ.get('SKILL_COMPARE_ARM','plain')
        selected_key=os.environ.get('SKILL_COMPARE_KEY','ecommerce-main-images')
        current={'arm':arm,'skill_key':selected_key if arm=='manual' else None,'status':'running','user_text':user};rows.append(current);record()
        cap=next(x for x in captures if x['package']['skill_key']==selected_key)
        selection=SkillSelection(skill_id=selected_key,revision=cap['revision']['revision']) if arm=='manual' else None
        f=env['seed'](content_db)
        with psycopg.connect(content_db) as c:
            c.execute('UPDATE users SET credits=10000 WHERE id=%s',(f['user'],))
            c.execute("UPDATE tasks SET request_params=request_params||%s WHERE id=%s",(Jsonb({'thinking_mode':'disabled'}),f['task']))
            c.execute('UPDATE conversations SET scope_id=user_id::text WHERE id=%s',(f['conv'],))
        photo=await env['upload'](f,original,'激活对比/原产品图.png')
        traces=[];configure_metrics={}
        async def configure(r,p,h,q,receipts):
            q=replace(q,selected_skill=selection)
            if selection and selected_key=='ecommerce-main-images':
                from services.handlers.chat_handler import ChatHandler
                from services.adapters.factory import create_chat_adapter
                from services.tool_invocation_store import DatabaseToolInvocationStore
                from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow
                actual=ChatHandler(h.db);actual._adapter=create_chat_adapter('qwen3.8-max');actual.org_id=None
                subadapters.append(actual._adapter)
                actual._actor_execution_token=f['token'];actual._actor_turn_id=f['turn'];actual._actor_enabled=True;actual._actor_runtime=r
                actual._actor_invocation_store=DatabaseToolInvocationStore(h.db)
                actual._workspace_user_id=f['user'];actual._personal_context_allowed=True
                with psycopg.connect(content_db) as c:
                    out=c.execute('SELECT assistant_message_id FROM tasks WHERE id=%s',(f['task'],)).fetchone()[0]
                    if out is None:
                        out=str(uuid4());c.execute("INSERT INTO messages(id,conversation_id,role,content,status,turn_id,reply_to_message_id) VALUES(%s,%s,'assistant','[]','pending',%s,%s)",(out,f['conv'],f['turn'],f['message']))
                        c.execute('UPDATE tasks SET assistant_message_id=%s WHERE id=%s',(out,f['task']))
                q=replace(q,message_id=str(out))
                native_dispatch=actual._execute_tool_calls
                async def dispatched(calls,*args,**kwargs):
                    results=await native_dispatch(calls,*args,**kwargs)
                    for call,result,error,display in results:
                        env['wire'].append({'kind':'benchmark_tool','call':call,'error':error,'display':display,'metadata':getattr(result,'metadata',{})});env['persist']()
                        if call['name']=='generate_image' and not error and getattr(result,'metadata',{}).get('accepted'):
                            receipts.append({'receipt':copy.deepcopy(result.metadata),'native':json.loads(call['arguments'])})
                    return results
                actual._execute_tool_calls=dispatched;h=actual
                from services.task_execution.image_resources import create_resolver
                r.task_image_resource_resolver=create_resolver(h,q,p.execution_context)
                t=time.monotonic();p.ecom_workflow=EcommerceWorkflow(h,q,r,p);await p.ecom_workflow.initialize()
                configure_metrics['workflow_routing_ms']=(time.monotonic()-t)*1000
            t=time.monotonic()
            if selection:
                r.skill_coordination=True
                skills=await create_skill_runtime(handler=h,context=p.execution_context,runtime=r,selection=selection,recommendations=False)
                configure_metrics['discovery_ms']=(time.monotonic()-t)*1000
                t=time.monotonic();activated=await skills.activate_manual(selection)
                configure_metrics['load_validate_render_ms']=(time.monotonic()-t)*1000
                assert activated['ok'],activated
                t=time.monotonic();_apply_skill_context(p,skills)
                if getattr(p,'ecom_workflow',None):await p.ecom_workflow.coordinate(skills)
                await r.safe_point(SafePoint.AFTER_SKILL_ACTIVATION,replay_payload=_build_replay_context(p.messages,[],0,next_model_round=0,skill_runtime=skills))
                configure_metrics['inject_checkpoint_ms']=(time.monotonic()-t)*1000
                configure_metrics['active_revision']=skills.active[selected_key].revision
                configure_metrics['injected_bytes']=len(skills.active[selected_key].rendered.encode())
            else:
                configure_metrics.update(discovery_ms=0,load_validate_render_ms=0,inject_checkpoint_ms=0,injected_bytes=0)
            current['activation']=dict(configure_metrics);record()
            return r,p,h,q
        started=time.monotonic()
        try:
            with capture_chat_lifecycle(traces):author=await env['run'](f,[TextPart(text=user),photo],configure=configure)
            runtime,p,h,q,receipts,blocks=author
            current.update(author_seconds=time.monotonic()-started,blocks=blocks,lifecycle=traces)
            assert not receipts,'Only writing is authorized in the first turn'
            visible='\n'.join(b.get('text','') for b in blocks if b.get('type')=='text')
            with env['connect'](f) as c:
                plans=c.execute('SELECT to_jsonb(p) FROM ecom_image_plans p WHERE parent_task_id=%s ORDER BY created_at DESC',(f['task'],)).fetchall()
            plan=plans[0][0] if plans else None
            catalog=co.catalog(runtime.task_execution_state)
            current['catalog']=catalog;current['server_plan']=plan
            if selection and selected_key=='ecommerce-main-images':
                assert plan is not None,'Generic text registration is not a native ecommerce plan'
            if plan:
                assert plan['status']=='ready' and len(plan['items'])==3,plan['status']
                from services.agent.image.ecommerce_planner.delivery import render_ready_plan
                canonical=render_ready_plan(plan)
                assert canonical in visible,'Full native plan, not an acknowledgement, must be visible'
                expected_prompt=plan['items'][0]['request_text']
            else:
                catalog_row,=catalog;assert len(catalog_row['items'])==3
                expected_prompt=co.load(runtime.task_execution_state,{'artifact_id':catalog_row['artifact_id'],'item_index':1})[2].body
                assert len(expected_prompt)>80 and expected_prompt in visible
            if selection:
                body=str(env['wire'])
                assert selected_key in body and cap['revision']['revision'] in body,'Manual Skill must reach real model request'
            if runtime.task_execution_state is not None:
                before=runtime.task_execution_state;closed=end_candidate(before)
                cp=copy.deepcopy(runtime.checkpoint_base);cp.update(_task_execution=closed.model_dump(mode='json'),_task_execution_expected_revision=before.revision)
                with env['connect'](f) as c:env['save'](c,f,cp,expected=before.revision)
            await env['close_outer'](f,blocks)
            current['author_status']='passed';record()
            f=await env['next_user'](f,'把第一个方案生成一张图片，按保存的完整原文和素材执行，不要重新写方案。')
            with env['connect'](f) as c:source=c.execute('SELECT read_agent_task_followup(%s,%s)',(f['task'],f['token'])).fetchone()[0].get('source')
            call_start=time.monotonic()
            selected=await env['run'](f,[TextPart(text='把第一个方案生成一张图片，按保存的完整原文和素材执行，不要重新写方案。')],followup=source)
            current['ordinal_acceptance_seconds']=time.monotonic()-call_start
            rt,_,_,_,accepted,selected_blocks=selected;current['selection_blocks']=selected_blocks
            assert len(accepted)==1,'Exactly one numbered image must be accepted'
            task=await env['worker'].refresh({'id':accepted[0]['receipt']['task_id'],'user_id':f['user'],'org_id':None})
            snapshot=task['request_params'][REQUEST_KEY]
            assert snapshot['prompt']==expected_prompt
            assert snapshot['references'][0]['content_sha256']==ref['content_sha256']
            assert (snapshot['aspect_ratio'],snapshot['resolution'])==('16:9','2K'),'Saved user canvas must survive ordinal execution'
            image=(await env['generate_all'](f,accepted,'manual-activation-'+arm))[0]
            await env['complete_parent'](f,rt)
            current.update(status='passed',image=image,total_seconds=time.monotonic()-started)
        except Exception as e:
            current.update(status='failed',error_type=type(e).__name__,error=str(e),trace=traceback.format_exc(),total_seconds=time.monotonic()-started);record();raise
        finally:
            current['activation']=configure_metrics;current['lifecycle']=traces;record()
            for adapter in subadapters:await adapter.close()
    await _real_supplier_acceptance(content_db,monkeypatch,suite=suite)
