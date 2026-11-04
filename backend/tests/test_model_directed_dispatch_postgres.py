"""Direct mode through original production SQL; no provider or image worker IO."""
import copy,json
from dataclasses import replace
from pathlib import Path
from uuid import UUID,uuid4
import psycopg,pytest
from psycopg.types.json import Jsonb
from tests.test_task_execution_followup_reuse_postgres import (
    followup_db,continuation_db,children_db,operation_db,task_db,composition_db,ecom_db,isolated_db,native_result_db,
)
from tests.test_skill_composition_postgres import seed,connect
from tests.test_task_execution_postgres import save
from tests.test_task_execution_user_changes_postgres import steer,capture
from tests.test_task_execution_operations_postgres import typed_store,begin
from tests.test_task_execution_children_postgres import accept_child
from tests.test_task_execution_controls_postgres import controls_db
from tests.test_model_directed_records import bootstrap,Registry
from services.task_execution.model_directed import record_calls
from services.task_execution.image_binding import bind_image_call
from services.tools import ToolCall
from services.tools.catalog import build_capability_catalog
from services.task_execution.reducer import digest
from services.handlers.chat_image_request import freeze_image_request

ROOT=Path(__file__).parents[1]
ARGS={'mode':'text_to_image','prompt':'原提示词，保留商品外观','aspect_ratio':'3:4'}


@pytest.fixture(scope='module')
def dispatch_db(controls_db,followup_db):
    assert followup_db==controls_db
    with psycopg.connect(followup_db) as c:
        for name in ('371_agent_task_model_directed_records','372_agent_task_direct_dispatch'):
            c.execute((ROOT/f'migrations/{name}.sql').read_text())
    yield followup_db
    with psycopg.connect(followup_db) as c:
        c.execute("DELETE FROM conversation_turn_checkpoints WHERE state ? '_task_execution'")
        c.execute('DELETE FROM agent_task_events');c.execute('DELETE FROM agent_task_states')
        c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        for name in ('372_agent_task_direct_dispatch','371_agent_task_model_directed_records'):
            c.execute((ROOT/f'migrations/rollback/{name}_rollback.sql').read_text())


def initial(dsn):
    f=seed(dsn);b=bootstrap()
    b=replace(b,task_id=UUID(f['task']),actor_user_id=UUID(f['user']),conversation_id=UUID(f['conv']),input_message_id=UUID(f['message']),
        execution_identity=b.execution_identity.model_copy(update={'workspace_owner_id':f['user']}))
    call=ToolCall('call-1','generate_image',ARGS)
    s=record_calls(None,b,[call],build_capability_catalog())
    p={'messages':[{'role':'system','content':'原完整记忆与已有成果'}, {'role':'user','content':s.original_goal}],
        '_task_execution':s.model_dump(mode='json'),'_task_direct_seen':[],'opaque_context':{'keep':True}}
    with connect(f) as c:assert save(c,f,p)['outcome']=='saved'
    return f,b,s,p,call


def gate(c,f):return c.execute('SELECT check_agent_task_revision_gate(%s,%s,%s,0)',(f['task'],f['token'],f['task'])).fetchone()[0]


@pytest.mark.parametrize('acknowledged',[False,True])
def test_actual_input_must_reach_main_model_before_the_original_dispatch_gate_opens(dispatch_db,acknowledged):
    f,b,s,p,call=initial(dispatch_db)
    with connect(f) as c:
        event=steer(c,f,'先暂停生图，修改为只写提示词')
        capture(c,f,event)
        cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert gate(c,f)['outcome']=='user_change_pending'
        cp['_task_direct_seen']=[event]
        if acknowledged:
            for item in cp['_task_user_changes']['items']:item['delivered']=True
            cp['messages'].append({'role':'user','content':'先暂停生图，修改为只写提示词'})
        assert save(c,f,cp,expected=s.revision)['outcome']=='saved'
        assert gate(c,f)['outcome']==('ready' if acknowledged else 'user_change_pending')
        assert c.execute('SELECT count(*) FROM agent_task_user_change_reviews WHERE root_task_id=%s',(f['task'],)).fetchone()[0]==0
        assert c.execute('SELECT agent_task_direct_input_hash(%s)',(Jsonb(cp),)).fetchone()[0]==digest([event])
        # An old model response remains stale even after the newer input is seen.
        bound=bind_image_call(s,call,build_capability_catalog())
        with pytest.raises(psycopg.Error,match='TASK_DIRECT_CALL_BINDING_STALE'):
            with c.transaction():begin(c,f,s.revision,call=call.call_id,args_hash=digest(ARGS),step=bound.step_id,effect=bound.effect_key)
        assert c.execute('SELECT count(*) FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]==0
        assert cp['opaque_context']=={'keep':True}


@pytest.mark.asyncio
async def test_original_native_acceptance_registers_async_dependency_without_a_planning_review(dispatch_db):
    f,b,s,p,call=initial(dispatch_db)
    bound=bind_image_call(s,call,build_capability_catalog())
    first=await typed_store(f).begin(s,step_id=bound.step_id,effect_key=bound.effect_key,effect_version=1,call=call)
    origin={'parent_task_id':f['task'],'actor_user_id':f['user'],'workspace_owner_id':f['user'],'org_id':None,
        'context_scope':'user','conversation_id':f['conv'],'turn_id':f['turn'],'input_message_id':f['message'],
        'base_context_revision':0,'tool_call_id':call.call_id}
    snapshot=freeze_image_request(ARGS,[],origin=origin,max_requests=6,max_credits=100)
    child=accept_child(f,snapshot)
    assert accept_child(f,snapshot)['task_id']==child['task_id']
    with connect(f) as c:
        rows=c.execute('SELECT child_task_id,operation_id FROM agent_task_events WHERE root_task_id=%s',(f['task'],)).fetchall()
        assert rows==[(UUID(child['task_id']),first.operation.id)]
        assert c.execute("SELECT count(*) FROM tasks WHERE type='image' AND conversation_id=%s",(f['conv'],)).fetchone()[0]==1
        cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert cp['opaque_context']=={'keep':True}
        assert not cp['_task_execution']['method_bindings']


@pytest.mark.asyncio
async def test_foreign_or_unbound_child_rolls_back_original_acceptance_and_budget(dispatch_db):
    f,b,s,p,call=initial(dispatch_db)
    bound=bind_image_call(s,call,build_capability_catalog())
    await typed_store(f).begin(s,step_id=bound.step_id,effect_key=bound.effect_key,effect_version=1,call=call)
    origin={'parent_task_id':f['task'],'actor_user_id':f['user'],'workspace_owner_id':f['user'],'org_id':None,
        'context_scope':'user','conversation_id':f['conv'],'turn_id':f['turn'],'input_message_id':f['message'],
        'base_context_revision':0,'tool_call_id':'unbound-id'}
    snapshot=freeze_image_request(ARGS,[],origin=origin,max_requests=6,max_credits=100)
    with pytest.raises(psycopg.Error,match='TASK_IMAGE_OPERATION_BINDING_REQUIRED'):accept_child(f,snapshot)
    with psycopg.connect(dispatch_db) as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE type='image' AND conversation_id=%s",(f['conv'],)).fetchone()[0]==0
        assert c.execute('SELECT credits FROM users WHERE id=%s',(f['user'],)).fetchone()[0]==100


@pytest.mark.asyncio
async def test_canonical_arguments_use_same_original_operation_and_cannot_be_rebound(dispatch_db):
    from services.task_execution.model_directed import resolve_call,operation_binding
    f=seed(dispatch_db);b=replace(bootstrap(),task_id=UUID(f['task']),actor_user_id=UUID(f['user']),
        conversation_id=UUID(f['conv']),input_message_id=UUID(f['message']))
    b=replace(b,execution_identity=b.execution_identity.model_copy(update={'workspace_owner_id':f['user']}))
    raw=ToolCall('file-1','file_delete',{'files':'report.csv'})
    canonical=ToolCall('file-1','file_delete',{'files':['report.csv']})
    s=record_calls(None,b,[raw],build_capability_catalog())
    cp={'messages':[{'role':'user','content':s.original_goal}],'_task_execution':s.model_dump(mode='json'),'_task_direct_seen':[]}
    with connect(f) as c:assert save(c,f,cp)['outcome']=='saved'
    resolved=resolve_call(s,raw,canonical)
    with connect(f) as c:assert save(c,f,{**cp,'_task_execution':resolved.model_dump(mode='json')},expected=s.revision)['outcome']=='saved'
    step,key,version=operation_binding(resolved,canonical)
    with connect(f) as c:
        with pytest.raises(psycopg.Error,match='TASK_DIRECT_CALL_BINDING_STALE'):
            with c.transaction():begin(c,f,resolved.revision,call=raw.call_id,step=step,effect=key,
                tool=raw.name,capability='platform.file_delete',args_hash=digest({'files':'report.csv'}))
    result=await typed_store(f).begin(resolved,step_id=step,effect_key=key,effect_version=version,call=canonical)
    assert result.operation.arguments_sha256==digest({'files':['report.csv']})
    with connect(f) as c:
        stored=c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',(f['task'],)).fetchone()[0]
        tampered=copy.deepcopy(stored);tampered['revision']+=1
        tampered['resolved_call_bindings'][0]['arguments_sha256']='f'*64
        with pytest.raises(psycopg.Error):
            with c.transaction():save(c,f,{**cp,'_task_execution':tampered},expected=stored['revision'])
        assert c.execute('SELECT count(*) FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]==1


def test_followup_after_several_main_rounds_preserves_context_and_cumulative_budget(dispatch_db):
    from tests.test_task_execution_followup_reuse_postgres import new_turn,adopt
    from services.task_execution.followup import payload,FollowupObservation
    f,b,s,cp,call=initial(dispatch_db)
    s=s.model_copy(update={'revision':s.revision+1,'model_rounds_used':2,'status':'partial'})
    cp={**cp,'_task_execution':s.model_dump(mode='json')}
    with connect(f) as c:assert save(c,f,cp,expected=0)['outcome']=='saved'
    _,fresh,source,obs,_=new_turn(dispatch_db,old=f)
    current={'messages':[{'role':'user','content':source['message']}],'opaque_current':{'keep':True},
        'next_model_round':3,'_task_direct_plan_owner':fresh['task'],
        '_task_direct_plan':{'explanation':'保留原成果','steps':[{'step':'读取原结果','status':'in_progress'}]}}
    with connect(fresh) as c:
        c.execute("SELECT save_generation_checkpoint(%s,%s,'before_model',%s)",(fresh['task'],fresh['token'],Jsonb(current)))
        merged=payload(source,current,task_id=fresh['task'],turn_id=fresh['turn'],model_round=3,
            usage={'prompt_tokens':9},observation=FollowupObservation.model_validate(obs))
        reply=adopt(c,fresh,source,obs,merged)
        assert reply['outcome']=='saved'
        restored=reply['payload']
        assert restored['_task_execution']['model_rounds_used']==5
        assert restored['opaque_context']=={'keep':True}
        assert restored['_task_followup_input_checkpoint']==current
        assert restored['messages'][:len(cp['messages'])]==cp['messages']
        assert '须经原要求修订核验' not in restored['messages'][-1]['content']
        assert c.execute('SELECT count(*) FROM credit_transactions WHERE task_id=%s',(fresh['task'],)).fetchone()[0]==0


@pytest.mark.asyncio
async def test_published_image_uses_original_queue_and_continues_main_model(dispatch_db):
    from tests.test_task_execution_operation_store import native_result
    from tests.test_chat_image_lifecycle_postgres import claim as claim_image,worker_rpc
    from tests.test_task_execution_continuation_postgres import enqueue,claim_internal
    from services.task_execution.image_continuation import continuation_payload
    from types import SimpleNamespace
    f,b,s,cp,call=initial(dispatch_db)
    bound=bind_image_call(s,call,build_capability_catalog())
    store=typed_store(f)
    reserved=await store.begin(s,step_id=bound.step_id,effect_key=bound.effect_key,effect_version=1,call=call)
    origin={'parent_task_id':f['task'],'actor_user_id':f['user'],'workspace_owner_id':f['user'],'org_id':None,
        'context_scope':'user','conversation_id':f['conv'],'turn_id':f['turn'],'input_message_id':f['message'],
        'base_context_revision':0,'tool_call_id':call.call_id}
    child=accept_child(f,freeze_image_request(ARGS,[],origin=origin,max_requests=6,max_credits=100))
    observed=native_result(reserved.state,call)
    observed.raw.metadata.update(task_id=child['task_id'],message_id=child['message_id'])
    accepted=await store.complete_invocation(reserved.state,operation_id=reserved.operation.id,
        result=observed,status='accepted',receipt_ref='task:'+child['task_id'])
    waiting=accepted.state.model_copy(update={'revision':accepted.state.revision+1,'status':'waiting_dependencies'})
    with connect(f) as c:assert save(c,f,{**cp,'_task_execution':waiting.model_dump(mode='json')},expected=accepted.state.revision)['outcome']=='saved'
    with psycopg.connect(dispatch_db) as c:
        c.execute("UPDATE messages SET turn_id=%s,status='completed',context_revision=1,content=%s WHERE id=%s",(f['turn'],Jsonb([{'type':'text','text':waiting.original_goal}]),f['message']))
        c.execute("UPDATE tasks SET status='completed' WHERE id=%s",(f['task'],))
        c.execute("UPDATE conversation_turn_checkpoints SET status='consumed' WHERE task_id=%s",(f['task'],))
        c.execute('UPDATE conversations SET context_revision=1 WHERE id=%s',(f['conv'],))
    assert claim_image(f,child['task_id'])['outcome']=='claimed'
    with psycopg.connect(dispatch_db) as c:
        token=c.execute("SELECT request_params#>>'{_media_lifecycle_v1,claim_token}' FROM tasks WHERE id=%s",(child['task_id'],)).fetchone()[0]
    assert worker_rpc(f,'mark_chat_image_dispatch',child['task_id'],token)['outcome']=='dispatch'
    part={'type':'image','url':'https://example.invalid/already-produced.png','width':900,'height':1200}
    assert worker_rpc(f,'record_chat_image_provider_result',child['task_id'],Jsonb({'status':'success','image_urls':[part['url']]}))['outcome']=='settling'
    assert worker_rpc(f,'publish_chat_image_result',child['task_id'],Jsonb([part]),'completed','',False)['outcome']=='published'
    with connect(f) as c:event=str(c.execute('SELECT id FROM agent_task_events WHERE child_task_id=%s',(child['task_id'],)).fetchone()[0])
    queued=enqueue(f,waiting,event)
    assert queued['outcome']=='queued' and enqueue(f,waiting,event)['task_id']==queued['task_id']
    task,claim=claim_internal(f,queued['task_id'])
    with connect(f) as c:bundle=c.execute('SELECT load_agent_image_continuation(%s,%s)',(claim.task_id,claim.execution_token)).fetchone()[0]
    scope=SimpleNamespace(actor_user_id=f['user'],**waiting.execution_identity.model_dump())
    restored,candidate=continuation_payload(bundle,task=task,claim=claim,execution_scope=scope)
    assert candidate.status=='active'  # Real image receipt does not declare the model's whole goal finished.
    assert len(candidate.results)==1 and candidate.artifacts[-1].reference=='task-image:'+child['task_id']
    assert restored['opaque_context']=={'keep':True} and restored['messages'][:len(cp['messages'])]==cp['messages']
    with connect(f) as c:
        result=c.execute('SELECT apply_agent_image_continuation(%s,%s,%s,%s)',
            (claim.task_id,claim.execution_token,waiting.revision,Jsonb(restored))).fetchone()[0]
        assert result['outcome']=='saved'


@pytest.mark.parametrize('action',['pause','cancel'])
def test_direct_task_uses_original_fenced_controls_and_keeps_all_bindings(dispatch_db,action):
    f,b,s,cp,call=initial(dispatch_db)
    with connect(f) as c:
        result=c.execute('SELECT '+action+'_generation_turn_owned(%s,%s,%s)',
            (f['task'],f['token'],'user_'+action)).fetchone()[0]
        assert result['outcome']==('paused' if action=='pause' else 'cancelled')
        actual=c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',(f['task'],)).fetchone()[0]
        checkpoint=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert actual['model_directed'] is True and actual['call_bindings']==cp['_task_execution']['call_bindings']
        assert actual['original_goal']==s.original_goal
        assert checkpoint['_task_execution']==actual and checkpoint['messages']==cp['messages']
        assert checkpoint['opaque_context']=={'keep':True}
        assert c.execute('SELECT count(*) FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]==0
        if action=='pause':
            resumed=c.execute('SELECT resume_paused_generation_turn(%s,%s,%s)',(f['task'],f['user'],None)).fetchone()[0]
            assert resumed['outcome']=='enqueued'
            assert c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',(f['task'],)).fetchone()[0]['status']=='active'
            assert save(c,f,cp,expected=s.revision)['outcome']=='ownership_lost'


@pytest.mark.asyncio
async def test_consuming_new_input_does_not_reauthorize_an_old_native_child(dispatch_db):
    from tests.test_chat_image_lifecycle_postgres import worker_rpc
    from services.task_execution.contracts import TaskState
    f,b,s,p,call=initial(dispatch_db)
    bound=bind_image_call(s,call,build_capability_catalog())
    reserved=await typed_store(f).begin(s,step_id=bound.step_id,effect_key=bound.effect_key,effect_version=1,call=call)
    origin={'parent_task_id':f['task'],'actor_user_id':f['user'],'workspace_owner_id':f['user'],'org_id':None,
        'context_scope':'user','conversation_id':f['conv'],'turn_id':f['turn'],'input_message_id':f['message'],
        'base_context_revision':0,'tool_call_id':call.call_id}
    child=accept_child(f,freeze_image_request(ARGS,[],origin=origin,max_requests=6,max_credits=100))
    assert worker_rpc(f,'agent_child_revision_gate',child['task_id'],None)=='ready'
    with connect(f) as c:
        event=steer(c,f,'先暂停生图，只做文字')
        capture(c,f,event)
        cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        for item in cp['_task_user_changes']['items']:item['delivered']=True
        cp['_task_direct_seen']=[event]
        cp['messages'].append({'role':'user','content':'先暂停生图，只做文字'})
        assert save(c,f,cp,expected=reserved.state.revision)['outcome']=='saved'
    # A consumed directive opens only the main model's new work; it does not
    # silently release any previously queued paid job.
    assert worker_rpc(f,'agent_child_revision_gate',child['task_id'],None)=='pending'
    with connect(f) as c:
        assert c.execute('SELECT check_agent_task_revision_gate(%s,%s,%s,0)',(f['task'],f['token'],f['task'])).fetchone()[0]['outcome']=='ready'
        continued=steer(c,f,'现在继续原图，不增加数量')
        capture(c,f,continued)
        cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        for item in cp['_task_user_changes']['items']:item['delivered']=True
        cp['_task_direct_seen']=sorted([event,continued])
        cp['messages'].append({'role':'user','content':'现在继续原图，不增加数量'})
        current=TaskState.model_validate(cp['_task_execution'])
        assert save(c,f,cp,expected=current.revision)['outcome']=='saved'
        resumed=record_calls(current,b,[ToolCall('intent-to-continue-original','generate_image',ARGS)],build_capability_catalog(),input_sha256=digest(sorted([event,continued])))
        assert resumed.steps==current.steps and len(resumed.operations)==1
        assert save(c,f,{**cp,'_task_execution':resumed.model_dump(mode='json')},expected=current.revision)['outcome']=='saved'
    assert worker_rpc(f,'agent_child_revision_gate',child['task_id'],None)=='ready'
    with connect(f) as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE type='image' AND conversation_id=%s",(f['conv'],)).fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]==1


@pytest.mark.asyncio
async def test_sync_handler_database_gate_scopes_each_transaction_without_leaking_identity(dispatch_db):
    """Use the raw production handler client, not a pre-scoped test RPC adapter."""
    import asyncio
    from types import SimpleNamespace
    from core.local_db import LocalDBClient
    from core.db_scope import DatabaseScope, DatabaseAccessKind, ScopedDatabaseClient
    from services.task_execution.database_scope import task_database
    from services.task_execution.requirement_changes import gate as dispatch_gate
    from services.task_execution.contracts import TaskError
    first = initial(dispatch_db)
    second = initial(dispatch_db)
    db = LocalDBClient(psycopg.conninfo.make_conninfo(dispatch_db, user='everydayai'), min_size=1, max_size=2)
    def executor(row):
        f, _, state, _, _ = row
        return SimpleNamespace(db=db, _task_execution_state=state, _task_id=f['task'], image_execution_token=f['token'])
    try:
        # The original unscoped RPC fails with precisely the production error.
        f, _, state, _, _ = first
        with pytest.raises(psycopg.Error, match='TASK_REVISION_SCOPE_DENIED'):
            await asyncio.to_thread(lambda: db.rpc('check_agent_task_revision_gate', {
                'p_task_id': f['task'], 'p_execution_token': f['token'],
                'p_logical_task_id': f['task'], 'p_requirements_revision': 0}).execute())
        await asyncio.gather(*(dispatch_gate(executor(row)) for row in [first, second] * 4))
        with db.pool.connection() as connection:
            values = connection.execute("SELECT current_setting('app.actor_user_id',true) AS actor, current_setting('app.org_id',true) AS org, current_setting('app.access_kind',true) AS access").fetchone()
            assert all(value in (None, '') for value in values.values())
        other = executor(second)
        other._task_execution_state = state
        with pytest.raises(psycopg.Error, match='TASK_REVISION_SCOPE_DENIED'):
            await dispatch_gate(other)
        stale = executor(first)
        stale.image_execution_token = str(uuid4())
        with pytest.raises(psycopg.Error, match='TASK_REVISION_SCOPE_DENIED'):
            await dispatch_gate(stale)
        for scope in (DatabaseScope(second[0]['user'], None, DatabaseAccessKind.RUNTIME),
                      DatabaseScope(f['user'], str(uuid4()), DatabaseAccessKind.RUNTIME)):
            with pytest.raises(TaskError, match='TASK_DATABASE_SCOPE_MISMATCH'):
                task_database(ScopedDatabaseClient(db, scope), state)
        with connect(f) as c:
            event = steer(c, f, '先停止，保留已有成果')
            capture(c, f, event)
        with pytest.raises(TaskError, match='TASK_USER_INPUT_CHANGED'):
            await dispatch_gate(executor(first))
        with connect(f) as c:
            cp = c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s', (f['task'],)).fetchone()[0]
            assert cp['opaque_context'] == {'keep': True}
            assert c.execute('SELECT count(*) FROM tool_invocations WHERE task_id=%s', (f['task'],)).fetchone()[0] == 0
    finally:
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy_evidence', [False, True])
async def test_code_failure_followup_reuses_original_root_and_only_dispatches_repair(
    dispatch_db, monkeypatch, legacy_evidence,
):
    from unittest.mock import AsyncMock
    from services.task_execution.contracts import Artifact, TaskState
    from services.task_execution.followup import FollowupRestart
    from services.task_execution.reducer import complete_step, register_artifacts
    from tests.test_model_directed_loop import case, execute
    from tests.test_task_execution_followup_reuse_postgres import new_turn, adopt
    from tests.test_task_execution_model_bridge import call

    old = seed(dispatch_db)
    b = replace(bootstrap(), task_id=UUID(old['task']), actor_user_id=UUID(old['user']),
        conversation_id=UUID(old['conv']), input_message_id=UUID(old['message']))
    b = replace(b, execution_identity=b.execution_identity.model_copy(update={'workspace_owner_id': old['user']}))
    previous_calls = [
        ToolCall('extrema', 'code_execute', {'code': 'print(max([162, 3550, 622]), min([162, 3550, 622]))'}),
        ToolCall('failed', 'code_execute', {'code': 'raise RuntimeError("RESUME_TEST")'}),
    ]
    state = record_calls(None, b, previous_calls, build_capability_catalog())
    previous_output = 'MAX = 3550\nMIN = 162'
    first = state.call_bindings[0]
    receipt = Artifact(id='extrema-receipt', kind='receipt', reference='tool-result:extrema',
        sha256=digest(previous_output), source_task_id=b.task_id, delivery_task_id=b.task_id, source_call_id=first.call_id, step_id=first.step_id)
    state = complete_step(register_artifacts(state, [receipt]), first.step_id, [receipt.id], owner_task_id=b.task_id)
    state = state.model_copy(update={'status': 'partial'})
    previous_messages = []
    for original, output in zip(previous_calls, [previous_output, 'RuntimeError: RESUME_TEST']):
        previous_messages.extend([
            {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': original.call_id, 'type': 'function', 'function': {
                    'name': original.name, 'arguments': json.dumps(dict(original.arguments)),
                },
            }]},
            {'role': 'tool', 'tool_call_id': original.call_id, 'content': output},
        ])
    with connect(old) as connection:
        assert save(connection, old, {'messages': previous_messages,
            '_task_execution': state.model_dump(mode='json')})['outcome'] == 'saved'

    user_text = ('继续刚才失败的接续测试。直接使用上轮第一步工具已经返回的 MAX 和 MIN，'
                 '仅补做第二步：实际调用一次代码工具计算 MAX 减 MIN，并报告结果。'
                 '保留上轮成功输出和失败记录，不读文件、不重算第一步、不再触发测试错误。')
    _, fresh, source, _, _ = new_turn(dispatch_db, old=old, text=user_text)

    async def checkpoint(point, payload):
        with connect(fresh) as connection:
            return connection.execute("SELECT save_generation_checkpoint(%s,%s,%s,%s)",
                (fresh['task'], fresh['token'], point.value, Jsonb(payload))).fetchone()[0]

    runtime, prepared, handler, request = case(checkpoint)
    runtime.task_id, runtime.turn_id, runtime.conversation_id = fresh['task'], fresh['turn'], fresh['conv']
    request = replace(request, task_id=fresh['task'], conversation_id=fresh['conv'], user_id=fresh['user'])
    prepared.execution_context = replace(prepared.execution_context, task_id=fresh['task'],
        conversation_id=fresh['conv'], actor_user_id=fresh['user'], workspace_owner_id=fresh['user'])
    runtime.task_bootstrap = replace(b, task_id=UUID(fresh['task']), input_message_id=UUID(fresh['message']))
    runtime.task_followup_candidate = source
    prepared.messages = [{'role': 'user', 'content': user_text}]

    async def transfer(selected, observation, merged):
        assert observation == {'relation': 'continue', 'evidence': [user_text]}
        with connect(fresh) as connection:
            return adopt(connection, fresh, selected, observation, merged)

    runtime.task_followup_adopt = transfer
    args = {'relation': 'continue'}
    if legacy_evidence:
        # Production sent summaries rather than verbatim excerpts.
        args['evidence'] = [
            '上轮第一步已成功返回 MAX=3550、MIN=162（订单数样本 162、3550、622）',
            '上轮第二步原样执行 raise RuntimeError("RESUME_TEST") 真实失败，未重试',
            '用户要求仅补做第二步：调用代码工具计算 MAX 减 MIN 并报告结果，不重算第一步、不再触发测试错误',
        ]
    with pytest.raises(FollowupRestart) as restart:
        await execute(runtime, prepared, handler, request, [call('resume_task_execution', args, 'resume')])
    assert restart.value.delivery_context['logical_task_id'] == old['task']
    handler._execute_tool_calls.assert_not_awaited()

    with connect(fresh) as connection:
        owner, restored = connection.execute('SELECT owner_task_id,state FROM agent_task_states WHERE root_task_id=%s',
            (old['task'],)).fetchone()
        merged = connection.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',
            (fresh['task'],)).fetchone()[0]
    assert str(owner) == fresh['task']
    assert merged['messages'][:len(previous_messages)] == previous_messages
    runtime.task_execution_state = TaskState.model_validate(restored)
    assert runtime.task_execution_state.results == state.results
    assert runtime.task_execution_state.artifacts == (receipt,)
    prepared.messages = merged['messages']
    # This dispatch assertion uses a local tool double. The original owner and
    # checkpoint above were transferred by the actual PostgreSQL RPC.
    runtime._replay_checkpoint_callback = AsyncMock(return_value={'outcome': 'saved'})
    runtime.committed_task_execution_revision = runtime.task_execution_state.revision
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context', AsyncMock())
    handler._execute_tool_calls.side_effect = lambda calls, *a, **kw: [
        (entry, '3388', False, '3388') for entry in calls
    ]
    await execute(runtime, prepared, handler, request, [
        call('code_execute', {'code': 'print(3550 - 162)'}, 'repair'),
    ])
    handler._execute_tool_calls.assert_awaited_once()
    assert [entry['id'] for entry in handler._execute_tool_calls.call_args.args[0]] == ['repair']
    assert str(runtime.task_execution_state.root_task_id) == old['task']
    assert runtime.task_execution_state.results[0] == state.results[0]
