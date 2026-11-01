"""New real Turn -> original Actor owner, plus original published-image facts."""
import copy,json
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.types.json import Jsonb
import pytest
from services.task_execution.followup import payload,FollowupObservation
from tests.test_task_execution_user_changes_postgres import prepared
from tests.test_task_execution_continuation_postgres import (continuation_db,children_db,operation_db,task_db,composition_db,ecom_db,isolated_db)
from tests.test_skill_composition_postgres import connect
from tests.test_task_execution_native_results_postgres import native_result_db
from tests.fixtures.task_revision_gates import install,drain
ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def followup_db(continuation_db,native_result_db):
    assert continuation_db==native_result_db
    install(continuation_db)
    with psycopg.connect(continuation_db) as c:
        c.execute((ROOT/'migrations/366_agent_task_image_reuse.sql').read_text())
        c.execute((ROOT/'migrations/367_agent_task_user_followups.sql').read_text())
        c.execute((ROOT/'migrations/368_agent_task_saved_plan_handoff.sql').read_text())
    yield continuation_db
    with psycopg.connect(continuation_db) as c:
        c.execute("DELETE FROM conversation_turn_checkpoints WHERE state ? '_task_execution' OR state ? '_task_followup'")
        c.execute('DELETE FROM agent_task_states');c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        c.execute("UPDATE tasks SET delivery_context=delivery_context-'input_kind' WHERE delivery_context->>'input_kind'='agent_task_followup'")
        c.execute((ROOT/'migrations/rollback/368_agent_task_saved_plan_handoff_rollback.sql').read_text())
        c.execute((ROOT/'migrations/rollback/367_agent_task_user_followups_rollback.sql').read_text())
        c.execute((ROOT/'migrations/rollback/366_agent_task_image_reuse_rollback.sql').read_text())
    drain(continuation_db)


def new_turn(dsn,old=None,text='继续原任务，保留之前的成果'):
    if old is None:old,cp=prepared(dsn,binding_version=3)
    new={**old,'task':str(uuid4()),'message':str(uuid4()),'turn':str(uuid4()),'token':str(uuid4())}
    answer=str(uuid4())
    with psycopg.connect(dsn) as c:
        base=c.execute('SELECT COALESCE(max(context_revision),0)+1 FROM messages WHERE conversation_id=%s',(old['conv'],)).fetchone()[0]+1
        c.execute("UPDATE conversations SET scope_type='user',context_revision=%s WHERE id=%s",(base,old['conv']))
        c.execute("INSERT INTO messages(id,conversation_id,role,content,status,context_revision,turn_id) VALUES(%s,%s,'assistant','[]','completed',%s,%s)",(answer,old['conv'],base-1,old['turn']))
        c.execute("UPDATE tasks SET status='completed',assistant_message_id=%s WHERE id=%s",(answer,old['task']))
        c.execute("UPDATE conversation_turn_checkpoints SET status='consumed' WHERE task_id=%s",(old['task'],))
        c.execute("INSERT INTO messages(id,conversation_id,role,content,status,context_revision,turn_id) VALUES(%s,%s,'user',%s,'completed',%s,%s)",
            (new['message'],old['conv'],Jsonb([{'type':'text','text':text}]),base,new['turn']))
        c.execute("""INSERT INTO tasks(id,user_id,conversation_id,type,status,input_message_id,turn_id,execution_token,lease_expires_at,
            delivery_context,base_context_revision,request_params,execution_mode) VALUES(%s,%s,%s,'chat','running',%s,%s,%s,now()+interval '10 minutes','{"actor":true}',%s,'{}','serial')""",
            (new['task'],old['user'],old['conv'],new['message'],new['turn'],new['token'],base))
    fresh={'messages':[{'role':'user','content':text}],'opaque_current':{'keep':True}}
    with connect(new) as c:
        c.execute("SELECT save_generation_checkpoint(%s,%s,'before_model',%s)",(new['task'],new['token'],Jsonb(fresh)))
        source=c.execute('SELECT read_agent_task_followup(%s,%s)',(new['task'],new['token'])).fetchone()[0]['source']
    obs=FollowupObservation(relation='continue',evidence=['继续原任务'])
    merged=payload(source,fresh,task_id=new['task'],turn_id=new['turn'],model_round=1,usage={'prompt_tokens':4},observation=obs)
    return old,new,source,obs.model_dump(mode='json'),merged


def adopt(c,new,source,obs,merged):
    return c.execute('SELECT adopt_agent_task_followup(%s,%s,%s,%s,%s,%s,%s)',(new['task'],new['token'],source['state']['root_task_id'],
        source['state']['revision'],source['source_sha256'],Jsonb(obs),Jsonb(merged))).fetchone()[0]


def test_new_turn_preserves_full_original_context_and_forces_revision_review_before_dispatch(followup_db):
    old,new,source,obs,merged=new_turn(followup_db)
    with connect(new) as c:
        reply=adopt(c,new,source,obs,merged);assert reply['outcome']=='saved'
        assert adopt(c,new,source,obs,merged)==reply
        current=c.execute('SELECT owner_task_id,state FROM agent_task_states WHERE root_task_id=%s',(old['task'],)).fetchone()
        assert str(current[0])==new['task'] and current[1]==merged['_task_execution']
        cp=reply['payload'];assert cp['opaque_context']=={'keep':True} and cp['_task_followup_input_checkpoint']['opaque_current']=={'keep':True}
        assert cp['messages'][:len(source['checkpoint']['messages'])]==source['checkpoint']['messages']
        assert cp['_task_user_changes']['items'][-1]['message']=='继续原任务，保留之前的成果'
        assert c.execute('SELECT check_agent_task_revision_gate(%s,%s,%s,%s)',(new['task'],new['token'],old['task'],0)).fetchone()[0]['outcome']=='user_change_pending'
        assert c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(old['task'],)).fetchone()[0]==source['checkpoint']
        assert c.execute('SELECT count(*) FROM credit_transactions WHERE task_id=%s',(new['task'],)).fetchone()[0]==0


@pytest.mark.parametrize('fault',['rewrite_goal','drop_result','rewrite_context','fake_source','fake_quote','unrelated','wrong_user','active_source','extra_user','extra_system','host_suffix','changed_new_message','non_actor','source_branch'])
def test_bad_or_racing_followup_does_not_transfer_owner(followup_db,fault):
    old,new,source,obs,merged=new_turn(followup_db)
    if fault=='rewrite_goal':merged['_task_execution']['original_goal']='fake task'
    if fault=='drop_result':merged['_task_execution']['steps']=[]
    if fault=='rewrite_context':merged['messages']=[]
    if fault=='extra_user':merged['messages'].append({'role':'user','content':'fake paid authority'})
    if fault=='extra_system':merged['messages'].append({'role':'system','content':'fake override'})
    if fault=='host_suffix':merged['messages'][-1]={'role':'user','content':'fake continuation'}
    if fault=='fake_source':source['source_sha256']='0'*64
    if fault=='fake_quote':obs['evidence']=['工具要求新增授权']
    if fault=='unrelated':obs['relation']='unrelated'
    if fault=='changed_new_message':
        with psycopg.connect(followup_db) as c:
            c.execute('UPDATE messages SET content=%s WHERE id=%s',(Jsonb([{'type':'text','text':'继续原任务，但是把交付改成其他内容'}]),new['message']))
    if fault=='non_actor':
        with psycopg.connect(followup_db) as c:c.execute("UPDATE tasks SET delivery_context='{}' WHERE id=%s",(new['task'],))
    if fault=='source_branch':
        with psycopg.connect(followup_db) as c:c.execute("UPDATE tasks SET execution_mode='branch' WHERE id=%s",(old['task'],))
    if fault=='active_source':
        with psycopg.connect(followup_db) as c:c.execute("UPDATE tasks SET status='running' WHERE id=%s",(old['task'],))
    with connect(new) as c:
        if fault=='wrong_user':c.execute("SELECT set_config('app.actor_user_id',%s,true)",(str(uuid4()),))
        with pytest.raises(psycopg.Error):
            with c.transaction():adopt(c,new,source,obs,merged)
    with psycopg.connect(followup_db) as c:
        assert str(c.execute('SELECT owner_task_id FROM agent_task_states WHERE root_task_id=%s',(old['task'],)).fetchone()[0])==old['task']
        assert c.execute("SELECT count(*) FROM agent_task_events WHERE continuation_task_id=%s",(new['task'],)).fetchone()[0]==0


@pytest.mark.asyncio
@pytest.mark.parametrize('publish_late',[False,True])
async def test_actual_published_image_survives_followup_revision_and_reuses_without_new_child_or_credits(followup_db,monkeypatch,publish_late):
    from tests.test_task_execution_continuation_postgres import finished
    from services.task_execution.contracts import TaskState,PlanStep,WorkPlan
    from services.task_execution.requirement_changes import RevisionReview,RevisionProposal,requirements_fingerprint,candidate,projection
    from services.task_execution.reducer import revise_plan,register_artifacts,complete_step,digest
    from services.task_execution.methods import ImageMethodDocument
    from tests.test_task_execution_methods import document
    from services.task_execution.controller import TaskController
    from services.task_execution.artifact_reader import TaskArtifactReader
    from services.task_execution.image_resources import BindImageResources,bind_resources
    from services.task_execution.image_binding import image_prompt,image_slots
    from services.task_execution.plan_facts import before_complete,apply_review,sections
    from tests.test_task_execution_model_bridge import setup_case
    from services.handlers.chat.execution_engine import _build_replay_context
    from tests.test_task_execution_postgres import save
    from psycopg import sql
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    f,_,original_child,original_event=await finished(followup_db,monkeypatch,image_count=1,binding_version=3,remaining=publish_late)
    if publish_late:
        from tests.test_chat_image_lifecycle_postgres import claim as claim_image,worker_rpc
        assert claim_image(f,original_child['task_id'])['outcome']=='claimed'
        with psycopg.connect(followup_db) as c:
            image_token=c.execute("SELECT request_params#>>'{_media_lifecycle_v1,claim_token}' FROM tasks WHERE id=%s",(original_child['task_id'],)).fetchone()[0]
        assert worker_rpc(f,'mark_chat_image_dispatch',original_child['task_id'],image_token)['outcome']=='dispatch'

    old,new,source,obs,merged=new_turn(followup_db,old=f,text='继续原任务，保留之前的成果，再补成两张图')
    with connect(new) as c:
        reply=adopt(c,new,source,obs,merged)
        cp=reply['payload'];s=TaskState.model_validate(cp['_task_execution'])
        from services.task_execution.image_continuation import advance_images
        terminal=c.execute('SELECT read_agent_task_terminal_images(%s,%s,%s,%s)',(new['task'],new['token'],old['task'],s.revision)).fetchone()[0]
        advanced,_,_=advance_images(s,terminal['children'],owner_task_id=new['task'],base_revision=terminal['base_revision'])
        advanced=TaskState.model_validate({**advanced.model_dump(),'status':'active'})
        from tests.test_task_execution_postgres import save
        save(c,new,{**cp,'_task_execution':advanced.model_dump(mode='json')},expected=s.revision)
        s=advanced;cp['_task_execution']=s.model_dump(mode='json')
        events=c.execute('SELECT read_agent_task_user_change_pending(%s,%s,%s)',(new['task'],new['token'],old['task'])).fetchone()[0]['events']
        prop=RevisionProposal(relation='amend',evidence=['再补成两张图'],proposal={'action':'plan','applicable_method_ids':[],
            'requirements':[{'id':'images','description':'两张图，保留原成图','artifact_kind':'image','minimum':2,'evidence':['再补成两张图']}]})
        book=RevisionReview(root_task_id=s.root_task_id,base_requirements_sha256=requirements_fingerprint(s),events=events,pending=prop)
        updated=candidate(s,book,None)
        proof={**book.model_dump(mode='json'),'base_projection':projection(s),'observation':{'relation':'amend','issues':[],
            'requirements':{'action':'plan','deliveries':[{'artifact_kind':'image','minimum':2,'evidence':['再补成两张图']}]}}}
        c.execute('SELECT apply_agent_task_requirement_revision(%s,%s,%s,%s,%s,%s)',(new['task'],new['token'],old['task'],s.revision,Jsonb(proof),Jsonb({**cp,'_task_execution':updated.model_dump(mode='json')})))
    current=revise_plan(updated,WorkPlan(expected_revision=updated.revision,steps=[
        PlanStep(id='r1.plan',title='两张完整规划',requirement_ids=('r1.host.image_plan',)),
        PlanStep(id='r1.images',title='交付两张图',capability='platform.generate_image',depends_on=('r1.plan',),requirement_ids=('r1.user.images',))]))
    from services.task_execution.contracts import Artifact
    import hashlib
    text=document(2)
    plan=Artifact(id='new-plan',kind='text',reference='task-text:new-plan',sha256=hashlib.sha256(text.encode()).hexdigest(),
        inline_text=text,source_task_id=new['task'],delivery_task_id=new['task'],source_call_id='new-plan',step_id='r1.plan',validations=('artifact.exists','image.plan.valid'))
    current=register_artifacts(current,[plan])
    runtime,prepared,_,sink=setup_case(initial=current);runtime.task_id=new['task']
    sources={'references':[],'resolved':[],'origins':[],'texts':[{'id':'user:'+new['message'],'text':source['message']}]};sources['sha256']=digest(sources)
    runtime.task_plan_source_provider=AsyncMock(return_value=(sources,[]))
    await before_complete(runtime,current,'r1.plan',[plan.id],force=True)
    fields=sections(plan);observation={'reviewed_sections':list(fields),'claims':[{'section':key,'claim':value,'status':'supported',
        'evidence':[{'source_id':'user:'+new['message'],'kind':'user_text','quote':source['message']}]} for key,value in fields.items()]}
    await apply_review(runtime=runtime,prepared=prepared,blocks=[],text=json.dumps(observation),has_tool_calls=False,model_round=1,next_model_round=2,
        build_checkpoint=_build_replay_context,turn=1,sink=sink)
    current=complete_step(runtime.task_execution_state,'r1.plan',[plan.id],owner_task_id=new['task'])
    resolver=AsyncMock(return_value=[])
    bound,_=await bind_resources(current,BindImageResources(expected_revision=current.revision,planning_artifact_id=plan.id,image_item_index=1,
        mode='text_to_image',references=[]),resolver=resolver,owner_task_id=new['task'],call_id='bind')
    current=register_artifacts(current,[bound])
    with connect(new) as c:
        cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(new['task'],)).fetchone()[0]
        save(c,new,{**cp,'_task_execution':current.model_dump(mode='json')},expected=updated.revision)
        before=c.execute("SELECT count(*) FROM tasks WHERE type='image' AND conversation_id=%s",(new['conv'],)).fetchone()[0]
        credits=c.execute('SELECT credits FROM users WHERE id=%s',(new['user'],)).fetchone()[0]
    if publish_late:
        from services.task_execution.image_binding import image_dependencies_waiting
        assert image_dependencies_waiting(current)
        waiting=TaskState.model_validate({**current.model_dump(),'status':'waiting_dependencies','revision':current.revision+1})
        with connect(new) as c:
            cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(new['task'],)).fetchone()[0]
            save(c,new,{**cp,'_task_execution':waiting.model_dump(mode='json')},expected=current.revision)
        with psycopg.connect(followup_db) as c:
            answer=str(uuid4())
            closing_revision=c.execute('SELECT context_revision+1 FROM conversations WHERE id=%s',(new['conv'],)).fetchone()[0]
            c.execute("INSERT INTO messages(id,conversation_id,role,content,status,context_revision,turn_id) VALUES(%s,%s,'assistant','[]','completed',%s,%s)",
                (answer,new['conv'],closing_revision,new['turn']))
            c.execute("UPDATE conversations SET context_revision=%s WHERE id=%s",(closing_revision,new['conv']))
            c.execute("UPDATE tasks SET status='completed',assistant_message_id=%s WHERE id=%s",(answer,new['task']))
            c.execute("UPDATE conversation_turn_checkpoints SET status='consumed' WHERE task_id=%s",(new['task'],))
        part={'type':'image','url':'https://example.invalid/published.png','width':900,'height':1200}
        assert worker_rpc(f,'record_chat_image_provider_result',original_child['task_id'],Jsonb({'status':'success','image_urls':[part['url']]}))['outcome']=='settling'
        assert worker_rpc(f,'publish_chat_image_result',original_child['task_id'],Jsonb([part]),'completed','',False)['outcome']=='published'
        with connect(new) as c:
            queued=c.execute("SELECT enqueue_agent_task_continuation(%s,%s,%s,'image')",(old['task'],waiting.revision,original_event)).fetchone()[0]
            assert queued['outcome']=='queued',queued
        from tests.test_task_execution_continuation_postgres import claim_internal
        from services.task_execution.image_continuation import continuation_payload
        task,claim=claim_internal(new,queued['task_id'])
        with connect(new) as c:bundle=c.execute('SELECT load_agent_image_continuation(%s,%s)',(claim.task_id,claim.execution_token)).fetchone()[0]
        scope=SimpleNamespace(actor_user_id=new['user'],**waiting.execution_identity.model_dump())
        continued,current=continuation_payload(bundle,task=task,claim=claim,execution_scope=scope)
        assert current.requirements_revision==1 and current.status=='active'
        assert current.requirements==waiting.requirements and current.requirements_history==waiting.requirements_history
        with connect(new) as c:
            applied=c.execute('SELECT apply_agent_image_continuation(%s,%s,%s,%s)',(claim.task_id,claim.execution_token,waiting.revision,Jsonb(continued))).fetchone()[0]
            assert applied['outcome']=='saved'
        new={**new,'task':claim.task_id,'token':claim.execution_token,'turn':claim.turn_id}
    class DB:
        def rpc(self,name,params):
            async def execute():
                query=sql.SQL('SELECT {}({})').format(sql.Identifier(name),sql.SQL(',').join(sql.SQL('{}=>%s').format(sql.Identifier(k)) for k in params))
                with connect(new) as c:value=c.execute(query,tuple(params.values())).fetchone()[0]
                return SimpleNamespace(data=value)
            return SimpleNamespace(execute=execute)
    async def commit(state,expected):
        with connect(new) as c:
            cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(new['task'],)).fetchone()[0]
            return save(c,new,{**cp,'_task_execution':state.model_dump(mode='json')},expected=expected)
    controller=TaskController(current,owner_task_id=new['task'],commit=commit,artifact_reader=TaskArtifactReader(DB(),task_id=new['task'],execution_token=new['token']),image_resource_resolver=resolver)
    brief=ImageMethodDocument.model_validate_json(text).image_plans[0]
    result=await controller.control('reuse_task_image',{'expected_revision':current.revision,'arguments':{'mode':'text_to_image',
        'prompt':image_prompt(brief),'aspect_ratio':brief.aspect_ratio}},call_id='reuse')
    assert result.result['ok'] and result.result['reused'] and result.result['charged'] is False,result.result
    assert [s['status'] for s in image_slots(controller.state)]==['completed','not_submitted']
    assert controller.state.operations==current.operations
    with connect(new) as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE type='image' AND conversation_id=%s",(new['conv'],)).fetchone()[0]==before
        assert c.execute('SELECT credits FROM users WHERE id=%s',(new['user'],)).fetchone()[0]==credits
        from services.task_execution.image_binding import image_slot_key
        with pytest.raises(psycopg.Error,match='ALREADY_REUSED'):
            with c.transaction():
                c.execute('SELECT begin_agent_task_operation(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (new['task'],new['token'],old['task'],controller.state.revision,'r1.images','platform.generate_image',
                     image_slot_key('r1.user.images',1),1,'must-not-send','generate_image','0'*64))
        fake=controller.state.artifacts[-1].model_copy(update={'id':'fake-copy','sha256':'0'*64})
        forged=register_artifacts(controller.state,[fake])
        with pytest.raises(psycopg.Error,match='REUSE_UNVERIFIED'):
            with c.transaction():
                latest=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(new['task'],)).fetchone()[0]
                save(c,new,{**latest,'_task_execution':forged.model_dump(mode='json')},expected=controller.state.revision)
        assert c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',(old['task'],)).fetchone()[0]==controller.state.model_dump(mode='json')
        assert c.execute("SELECT count(*) FROM agent_task_events WHERE root_task_id=%s AND child_task_id IS NOT NULL AND status='pending'",(old['task'],)).fetchone()[0]==0
    with psycopg.connect(followup_db) as c:
        with pytest.raises(psycopg.Error,match='NOT_DRAINED'):
            with c.transaction():c.execute((ROOT/'migrations/rollback/366_agent_task_image_reuse_rollback.sql').read_text())


@pytest.mark.asyncio
async def test_same_manual_skill_pin_is_retained_through_postgres_new_turn_and_actual_runtime_restore(followup_db,monkeypatch):
    from tests.test_task_execution_methods import bound
    from tests.test_skill_runtime import state as skills_state
    _,skills=await bound(monkeypatch)
    old,cp=prepared(followup_db,binding_version=3)
    sk=skills.checkpoint();sk['turn_id']=old['turn']
    extra={'skill_runtime':sk}
    if sk.get('version')==2:
        sk['composition']['turn_id']=old['turn']
        extra.update(_skill_composition=sk['composition'],_skill_composition_expected_revision=0)
    with connect(old) as c:
        assert c.execute("SELECT save_generation_checkpoint(%s,%s,'after_skill_activation',%s)",(old['task'],old['token'],Jsonb({**cp,**extra}))).fetchone()[0]['outcome']=='saved'
    old,new,source,obs,merged=new_turn(followup_db,old=old)
    selection={'skill_id':'report','revision':'v1'}
    with psycopg.connect(followup_db) as c:
        c.execute('UPDATE tasks SET request_params=request_params||%s WHERE id=%s',(Jsonb({'_selected_skill':selection}),new['task']))
    with connect(new) as c:
        assert c.execute('SELECT read_agent_task_followup(%s,%s)',(new['task'],new['token'])).fetchone()[0]['outcome']=='found'
        result=adopt(c,new,source,obs,merged)
    restored=skills_state(skills.source,turn_id=new['turn'],platform_tool_names=skills.platform_tool_names,authorized_tool_names=skills._initial_ceiling)
    from services.skills.selection import SkillSelection
    await restored.initialize(result['payload']['skill_runtime'],SkillSelection.model_validate(selection))
    assert restored.manual_skill_id=='report'
    assert restored.active['report'].body_sha256==skills.active['report'].body_sha256
    assert restored.effective_allowed_tool_names<=skills._initial_ceiling


def test_new_explicit_other_skill_keeps_new_turn_independent(followup_db):
    old,new,source,obs,merged=new_turn(followup_db)
    with psycopg.connect(followup_db) as c:
        c.execute('UPDATE tasks SET request_params=request_params||%s WHERE id=%s',(Jsonb({'_selected_skill':{'skill_id':'different','revision':'v2'}}),new['task']))
    with connect(new) as c:
        assert c.execute('SELECT read_agent_task_followup(%s,%s)',(new['task'],new['token'])).fetchone()[0]['outcome']=='not_found'
        assert str(c.execute('SELECT owner_task_id FROM agent_task_states WHERE root_task_id=%s',(old['task'],)).fetchone()[0])==old['task']


def test_followup_rollback_refuses_other_tenant_live_ownership(followup_db):
    old,new,source,obs,merged=new_turn(followup_db)
    with connect(new) as c:adopt(c,new,source,obs,merged)
    with psycopg.connect(followup_db) as c:
        with pytest.raises(psycopg.Error,match='NOT_DRAINED'):
            with c.transaction():c.execute((ROOT/'migrations/rollback/367_agent_task_user_followups_rollback.sql').read_text())
        assert c.execute("SELECT to_regprocedure('public.adopt_agent_task_followup(uuid,uuid,uuid,bigint,text,jsonb,jsonb)') IS NOT NULL").fetchone()[0]
        assert c.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid='public.tasks'::regclass").fetchone()[0]


def test_second_real_user_followup_archives_previous_window_without_losing_context(followup_db):
    old,first,source,obs,merged=new_turn(followup_db)
    with connect(first) as c:first_reply=adopt(c,first,source,obs,merged)
    _,second,source2,obs2,merged2=new_turn(followup_db,old=first)
    with connect(second) as c:
        result=adopt(c,second,source2,obs2,merged2)
        cp=result['payload']
        assert cp['opaque_context']=={'keep':True}
        assert cp['_task_followup_window_history']==[{'owner_task_id':first['task'],
            'checkpoint':first_reply['payload']['_task_followup_input_checkpoint']}]
        assert len(cp['_task_user_changes']['items'])==2
        assert cp['_task_execution']['root_task_id']==old['task']
        assert cp['_task_execution']['model_rounds_used']==2


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['plan','prompt'])
async def test_saved_source_native_publication_reused_in_real_checkpoint_without_second_charge(followup_db,monkeypatch,kind):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from services.handlers.chat_image_request import freeze_image_request
    from services.task_execution.reducer import register_artifacts
    from services.task_execution.image_reuse import reuse,ReuseImageInput
    from services.task_execution.image_resources import bind_resources,BindImageResources
    from tests.test_task_execution_saved_image_reuse import source_state,native_plan_fixture
    from tests.test_task_execution_postgres import seed,bind_seed,save
    from tests.test_task_execution_children_postgres import accept_child
    from tests.test_chat_image_lifecycle_postgres import claim,worker_rpc
    from services.task_execution.artifact_reader import TaskArtifactReader
    from psycopg import sql
    f=seed(followup_db);s,a,args,original=await source_state(kind,monkeypatch);s=bind_seed(s,f)
    raw=s.model_dump(mode='json')
    for artifact in raw['artifacts']:artifact.update(source_task_id=f['task'],delivery_task_id=f['task'])
    for result in raw['results']:result['completed_task_id']=f['task']
    from services.task_execution.contracts import TaskState
    s=TaskState.model_validate(raw);a=s.artifacts[0]
    resolved=[];proof=None
    normalized=copy.deepcopy(args)
    if kind=='plan':
        p,ref=native_plan_fixture();resolved=[ref]
        for item in original['items']:item['references']=p['items'][0]['references']
        original['input_snapshot']={'resolved_references':resolved}
        normalized={'mode':'image_to_image','prompt':original['items'][0]['request_text'],'aspect_ratio':'3:4'}
        proof={**args['plan_source'],'request_text_sha256':original['items'][0]['request_text_sha256']}
        with psycopg.connect(followup_db) as c:
            c.execute("INSERT INTO ecom_image_plans(id,user_id,conversation_id,parent_task_id,input_message_id,base_context_revision,invocation_key,input_digest,status,current_stage,image_count,input_snapshot,prompt_versions,model_settings,target_size,plan_revision,items,stage_outputs) VALUES(%s,%s,%s,%s,%s,0,'local-source',%s,'ready',3,1,%s,'{}','{}',%s,4,%s,%s)",
                (original['id'],f['user'],f['conv'],f['task'],f['message'],'a'*64,Jsonb(original['input_snapshot']),Jsonb(original['target_size']),Jsonb(original['items']),Jsonb(original['stage_outputs'])))
    else:
        # Native submission expands start/end from the selected exact source.
        normalized['source_prompt'].update(start=0,end=len(normalized['prompt']))
    origin={'parent_task_id':f['task'],'actor_user_id':f['user'],'workspace_owner_id':f['user'],'org_id':None,
        'context_scope':'user','conversation_id':f['conv'],'turn_id':f['turn'],'input_message_id':f['message'],
        'base_context_revision':0,'tool_call_id':'prior-image',**({'plan_source':proof} if proof else {})}
    snapshot=freeze_image_request(normalized,resolved,origin=origin,max_requests=6,max_credits=100)
    child=accept_child(f,snapshot)
    assert claim(f,child['task_id'])['outcome']=='claimed'
    with psycopg.connect(followup_db) as c:token=c.execute("SELECT request_params#>>'{_media_lifecycle_v1,claim_token}' FROM tasks WHERE id=%s",(child['task_id'],)).fetchone()[0]
    assert worker_rpc(f,'mark_chat_image_dispatch',child['task_id'],token)['outcome']=='dispatch'
    part={'type':'image','url':'https://example.invalid/saved-source.png','width':900,'height':1200}
    assert worker_rpc(f,'record_chat_image_provider_result',child['task_id'],Jsonb({'status':'success','image_urls':[part['url']]}))['outcome']=='settling'
    assert worker_rpc(f,'publish_chat_image_result',child['task_id'],Jsonb([part]),'completed','',False)['outcome']=='published'
    with psycopg.connect(followup_db) as c:
        c.execute('UPDATE tasks SET base_context_revision=10 WHERE id=%s',(f['task'],))
        c.execute('UPDATE conversations SET context_revision=10 WHERE id=%s',(f['conv'],))
    resolver=AsyncMock(return_value=[])
    if kind=='prompt':
        bound,_=await bind_resources(s,BindImageResources(expected_revision=s.revision,planning_artifact_id=a.id,image_item_index=1,
            mode='text_to_image',references=[]),resolver=resolver,owner_task_id=f['task'],call_id='bind')
        s=register_artifacts(s,[bound])
    resolver.prepare_request=AsyncMock(return_value=(normalized,resolved,proof))
    with connect(f) as c:
        save(c,f,{'messages':[{'role':'system','content':'原上下文保留'}],'_task_execution':s.model_dump(mode='json')})
        before=c.execute('SELECT credits FROM users WHERE id=%s',(f['user'],)).fetchone()[0]
        count=c.execute("SELECT count(*) FROM tasks WHERE conversation_id=%s AND type='image'",(f['conv'],)).fetchone()[0]
    class DB:
        def rpc(self,name,params):
            async def execute():
                q=sql.SQL('SELECT {}({})').format(sql.Identifier(name),sql.SQL(',').join(sql.SQL('{}=>%s').format(sql.Identifier(k)) for k in params))
                with connect(f) as c:value=c.execute(q,tuple(params.values())).fetchone()[0]
                return SimpleNamespace(data=value)
            return SimpleNamespace(execute=execute)
    reader=TaskArtifactReader(DB(),task_id=f['task'],execution_token=f['token'])
    artifact,_=await reuse(s,ReuseImageInput(expected_revision=s.revision,arguments=args),reader=reader,resolver=resolver,owner_task_id=f['task'])
    updated=register_artifacts(s,[artifact])
    with connect(f) as c:
        assert save(c,f,{'messages':[{'role':'system','content':'原上下文保留'}],'_task_execution':updated.model_dump(mode='json')},expected=s.revision)['outcome']=='saved'
        assert c.execute('SELECT credits FROM users WHERE id=%s',(f['user'],)).fetchone()[0]==before
        assert c.execute("SELECT count(*) FROM tasks WHERE conversation_id=%s AND type='image'",(f['conv'],)).fetchone()[0]==count
        forged=artifact.model_copy(update={'id':'forged-source','sha256':'0'*64})
        with pytest.raises(psycopg.Error,match='REUSE_'):
            with c.transaction():save(c,f,{'messages':[],'_task_execution':register_artifacts(updated,[forged]).model_dump(mode='json')},expected=updated.revision)


@pytest.mark.asyncio
async def test_original_task_plan_cross_stage_import_preserves_history_and_rejects_changed_source(followup_db,monkeypatch):
    import hashlib
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from services.task_execution.contracts import TaskState,PlanStep,WorkPlan
    from services.task_execution.requirement_changes import RevisionReview,RevisionProposal,requirements_fingerprint,candidate,projection
    from services.task_execution.reducer import revise_plan,register_artifacts
    from services.task_execution.controller import TaskController
    from services.task_execution.image_sources import SOURCE_VALIDATOR
    from tests.test_task_execution_continuation_postgres import finished
    from tests.test_task_execution_postgres import save
    f,_,_,_=await finished(followup_db,monkeypatch,image_count=1,binding_version=3)
    text='继续原任务，按刚才原稿执行一张图'
    old,new,source,obs,merged=new_turn(followup_db,old=f,text=text)
    with connect(new) as c:
        cp=adopt(c,new,source,obs,merged)['payload'];s=TaskState.model_validate(cp['_task_execution'])
        original=s.artifacts[0]
        events=c.execute('SELECT read_agent_task_user_change_pending(%s,%s,%s)',(new['task'],new['token'],old['task'])).fetchone()[0]['events']
        book=RevisionReview(root_task_id=s.root_task_id,base_requirements_sha256=requirements_fingerprint(s),events=events,
            pending=RevisionProposal(relation='amend',evidence=[text],proposal={'action':'execute_ready','requirements':[
                {'id':'images','description':'执行一张原稿','artifact_kind':'image','minimum':1,'evidence':['一张图']}]}))
        updated=candidate(s,book,None)
        proof={**book.model_dump(mode='json'),'base_projection':projection(s),'observation':{'relation':'amend','issues':[],
            'requirements':{'action':'execute_ready','deliveries':[{'artifact_kind':'image','minimum':1,'evidence':['一张图']}]}}}
        reply=c.execute('SELECT apply_agent_task_requirement_revision(%s,%s,%s,%s,%s,%s)',
            (new['task'],new['token'],old['task'],s.revision,Jsonb(proof),Jsonb({**cp,'_task_execution':updated.model_dump(mode='json')}))).fetchone()[0]
        assert reply['outcome']=='saved';cp=reply['payload']
        planned=revise_plan(updated,WorkPlan(expected_revision=updated.revision,steps=(
            PlanStep(id='r1.source',title='读取原稿',requirement_ids=('r1.host.image_source',)),
            PlanStep(id='r1.images',title='原图交付',requirement_ids=('r1.user.images',),depends_on=('r1.source',),capability='platform.generate_image'))))
        save(c,new,{**cp,'_task_execution':planned.model_dump(mode='json')},expected=updated.revision)
    async def commit(state,expected):
        with connect(new) as c:
            cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(new['task'],)).fetchone()[0]
            return save(c,new,{**cp,'_task_execution':state.model_dump(mode='json')},expected=expected)
    controller=TaskController(planned,owner_task_id=new['task'],commit=commit,artifact_reader=SimpleNamespace())
    reply=await controller.control('reuse_task_text',{'expected_revision':planned.revision,'step_id':'r1.source',
        'source_logical_task_id':old['task'],'source_artifact_id':original.id},call_id='saved-plan')
    assert reply.result['ok'],reply.result
    imported=controller.state.artifacts[-1]
    assert SOURCE_VALIDATOR in imported.validations and imported.inline_text==original.inline_text
    assert original in controller.state.artifacts and controller.state.operations==planned.operations
    forged=imported.model_copy(update={'id':'forged-saved-plan','inline_text':'{}','sha256':hashlib.sha256(b'{}').hexdigest()})
    with connect(new) as c:
        with pytest.raises(psycopg.Error,match='SAVED_PLAN_SOURCE_UNVERIFIED'):
            with c.transaction():
                cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(new['task'],)).fetchone()[0]
                save(c,new,{**cp,'_task_execution':register_artifacts(controller.state,[forged]).model_dump(mode='json')},expected=controller.state.revision)
    with psycopg.connect(followup_db) as c:
        with pytest.raises(psycopg.Error,match='DRAIN_REQUIRED'):
            with c.transaction():c.execute((ROOT/'migrations/rollback/368_agent_task_saved_plan_handoff_rollback.sql').read_text())
        assert c.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid='public.agent_task_states'::regclass").fetchone()[0]


def test_knowledge_input_extension_survives_original_followup_adoption(followup_db):
    from services.knowledge_workspace.input_facts import CHECKPOINT_KEY, KnowledgeInputFacts, restore_facts
    from services.handlers.context_snapshot import ContextAnchor
    old,new,_,obs,_=new_turn(followup_db)
    previous=KnowledgeInputFacts(old['conv'],old['message'],None,'原要求',('text',),False,knowledge_used=True)
    current=KnowledgeInputFacts(new['conv'],new['message'],None,'继续原任务，保留之前的成果',('text',),False)
    with psycopg.connect(followup_db) as c:
        c.execute('UPDATE conversation_turn_checkpoints SET state=state || %s WHERE task_id=%s',
            (Jsonb({CHECKPOINT_KEY:previous.checkpoint()}),old['task']))
    with connect(new) as c:
        fresh=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(new['task'],)).fetchone()[0]
        fresh[CHECKPOINT_KEY]=current.checkpoint()
        assert c.execute("SELECT save_generation_checkpoint(%s,%s,'before_model',%s)",
            (new['task'],new['token'],Jsonb(fresh))).fetchone()[0]['outcome']=='saved'
        source=c.execute('SELECT read_agent_task_followup(%s,%s)',(new['task'],new['token'])).fetchone()[0]['source']
        merged=payload(source,fresh,task_id=new['task'],turn_id=new['turn'],model_round=1,usage={},
            observation=FollowupObservation.model_validate(obs))
        reply=adopt(c,new,source,obs,merged)
        assert reply['outcome']=='saved'
        checkpoint=reply['payload']
        assert checkpoint[CHECKPOINT_KEY]==previous.checkpoint()
        assert checkpoint['_task_followup_input_checkpoint'][CHECKPOINT_KEY]==current.checkpoint()
        restored=restore_facts(checkpoint,ContextAnchor(new['task'],new['conv'],new['turn'],new['message'],0,None,None))
        assert restored.input_message_id==new['message'] and restored.history_evidence and restored.changed
        assert checkpoint['opaque_context']=={'keep':True}
        assert c.execute('SELECT count(*) FROM credit_transactions WHERE task_id=%s',(new['task'],)).fetchone()[0]==0
