from copy import deepcopy
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.types.json import Jsonb
import pytest
from tests.test_task_execution_postgres import seed,connect,task_payload,save
from tests.test_task_execution_continuation_postgres import (continuation_db,children_db,operation_db,task_db,composition_db,ecom_db,isolated_db,
    finished,enqueue,claim_internal,continuation_payload)
ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def change_db(continuation_db):
    task_db=continuation_db
    with psycopg.connect(task_db) as c:
        prefix=(ROOT/'migrations/138_conversation_control_events.sql').read_text().split('CREATE OR REPLACE FUNCTION append_conversation_control_command')[0]
        c.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
        c.execute(prefix)
        c.execute('ALTER TABLE conversation_control_events OWNER TO everydayai')
        c.execute('ALTER SEQUENCE conversation_control_event_sequence_seq OWNER TO everydayai')
        c.execute('SET LOCAL ROLE everydayai')
        c.execute((ROOT/'migrations/242_conversation_actor_steer.sql').read_text())
        c.execute('RESET ROLE')
        c.execute((ROOT/'migrations/363_agent_task_user_changes.sql').read_text())
    yield task_db
    with psycopg.connect(task_db) as c:
        roots=[row[0] for row in c.execute("SELECT DISTINCT (state->'_task_user_changes'->>'root_task_id')::uuid FROM conversation_turn_checkpoints WHERE state ? '_task_user_changes'").fetchall()]
        c.execute("DELETE FROM conversation_turn_checkpoints WHERE state ? '_task_user_changes'")
        c.execute('DELETE FROM agent_task_states WHERE root_task_id=ANY(%s)',(roots,))
        c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        c.execute((ROOT/'migrations/rollback/363_agent_task_user_changes_rollback.sql').read_text())


def prepared(dsn,binding_version=1):
    f=seed(dsn)
    p=task_payload(f)
    if binding_version>=2:p['_task_execution']['image_binding_version']=binding_version
    p['messages']=[{'role':'system','content':'原摘要'},{'role':'user','content':'原要求'}];p['opaque_context']={'keep':True}
    with connect(f) as c:save(c,f,p)
    return f,p


def steer(c,f,text='先只写提示词，不要生图',key=None):
    return c.execute('SELECT append_conversation_steer(%s,%s,%s,%s,%s)',(f['conv'],f['task'],f['turn'],key or str(uuid4()),Jsonb({'message':text}))).fetchone()[0]['event_id']


def capture(c,f,event):
    return c.execute('SELECT capture_agent_task_user_change(%s,%s,%s)',(f['task'],f['token'],event)).fetchone()[0]


def test_original_steer_capture_is_atomic_idempotent_preserves_full_context_and_requirements(change_db):
    f,p=prepared(change_db)
    with connect(f) as c:
        event=steer(c,f);value=capture(c,f,event);assert value['outcome']=='captured'
        assert capture(c,f,event)==value
        checkpoint=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert checkpoint['_task_execution']==p['_task_execution']
        assert checkpoint['messages']==p['messages'] and checkpoint['opaque_context']==p['opaque_context']
        assert checkpoint['_task_user_changes']==value['inbox']
        assert c.execute('SELECT status FROM conversation_control_events WHERE id=%s',(event,)).fetchone()[0]=='applied'
        restored=deepcopy(checkpoint);restored['_task_user_changes']['items'][0]['delivered']=True
        assert save(c,f,restored,expected=p['_task_execution']['revision'])['outcome']=='saved'
        with pytest.raises(psycopg.Error,match='IMMUTABLE'):
            with c.transaction():save(c,f,checkpoint,expected=p['_task_execution']['revision'])
        with pytest.raises(psycopg.Error,match='INVALID'):
            with c.transaction():save(c,f,p,expected=p['_task_execution']['revision'])


@pytest.mark.parametrize('fault',['wrong_event','wrong_token','expired','other_actor','other_org','changed_source'])
def test_bad_capture_or_save_failure_never_acknowledges_or_loses_original_input(change_db,fault):
    f,p=prepared(change_db)
    with connect(f) as c:event=steer(c,f)
    if fault=='expired':
        with psycopg.connect(change_db) as c:c.execute("UPDATE tasks SET lease_expires_at=now()-interval '1 minute' WHERE id=%s",(f['task'],))
    with connect(f) as c:
        if fault=='other_actor':c.execute("SELECT set_config('app.actor_user_id',%s,true)",(str(uuid4()),))
        if fault=='other_org':c.execute("SELECT set_config('app.org_id',%s,true)",(str(uuid4()),))
        if fault=='wrong_token':f['token']=str(uuid4())
        if fault=='changed_source':
            # A failing CP trigger rolls back the ACK in the same RPC transaction.
            c.execute("UPDATE conversation_control_events SET payload='{}' WHERE id=%s",(event,))
        with pytest.raises(psycopg.Error):
            with c.transaction():capture(c,f,str(uuid4()) if fault=='wrong_event' else event)
    with psycopg.connect(change_db) as c:
        assert c.execute('SELECT status FROM conversation_control_events WHERE id=%s',(event,)).fetchone()[0]=='pending'
        assert c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]==p


@pytest.mark.asyncio
async def test_last_owner_steer_survives_actual_image_queue_claim_apply_and_cross_owner_delivery(change_db,monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from services.task_execution.user_changes import restore
    from services.conversation_turn_runtime import ConversationTurnRuntime
    f,state,child,event=await finished(change_db,monkeypatch)
    # Recreate the precise final-boundary race: input arrives before the old
    # running owner closes, after its waiting checkpoint was saved.
    with psycopg.connect(change_db) as c:
        c.execute("UPDATE tasks SET status='running',lease_expires_at=now()+interval '10 minutes' WHERE id=%s",(f['task'],))
        c.execute("UPDATE conversation_turn_checkpoints SET status='ready' WHERE task_id=%s",(f['task'],))
    with connect(f) as c:
        user_event=steer(c,f,'保留已完成的图片，先不要追加生图')
        original=capture(c,f,user_event)['inbox']
    with psycopg.connect(change_db) as c:
        c.execute("UPDATE tasks SET status='completed' WHERE id=%s",(f['task'],))
        c.execute("UPDATE conversation_turn_checkpoints SET status='consumed' WHERE task_id=%s",(f['task'],))
    queued=enqueue(f,state,event);assert queued['outcome']=='queued',queued
    task,claim=claim_internal(f,queued['task_id'])
    with connect(f) as c:bundle=c.execute('SELECT load_agent_image_continuation(%s,%s)',(claim.task_id,claim.execution_token)).fetchone()[0]
    scope=SimpleNamespace(actor_user_id=f['user'],**state.execution_identity.model_dump())
    payload,candidate=continuation_payload(bundle,task=task,claim=claim,execution_scope=scope)
    assert candidate.status=='active'  # New input must still reach the model.
    assert payload['_task_user_changes']==original
    with connect(f) as c:
        result=c.execute('SELECT apply_agent_image_continuation(%s,%s,%s,%s)',(claim.task_id,claim.execution_token,state.revision,Jsonb(payload))).fetchone()[0]
        assert result['outcome']=='saved'
    inbox=restore(payload,root_task_id=f['task'],owner_task_id=claim.task_id,inherited_root_task_id=task['delivery_context']['logical_task_id'])
    runtime=ConversationTurnRuntime(conversation_id=f['conv'],task_id=claim.task_id,turn_id=claim.turn_id,cancellation_event=asyncio.Event())
    runtime.task_user_changes=inbox;runtime._steer_messages.extend(i.message for i in inbox.items if not i.delivered)
    assert runtime.consume_steer_messages()==['保留已完成的图片，先不要追加生图']
    assert runtime.consume_steer_messages()==[]
    assert str(runtime.task_user_changes.items[0].owner_task_id)==f['task']
    with connect(f) as c:
        assert c.execute('SELECT status FROM conversation_control_events WHERE id=%s',(user_event,)).fetchone()[0]=='applied'
        assert c.execute("SELECT count(*) FROM tasks WHERE type='image' AND user_id=%s",(f['user'],)).fetchone()[0]==1


@pytest.mark.asyncio
async def test_actual_scoped_async_capture_and_lost_reply_replay_keep_original_source(change_db):
    from core.local_db import AsyncLocalDBClient
    from services.task_execution.user_changes import capture as actual_capture,restore
    f,p=prepared(change_db)
    with connect(f) as c:event=steer(c,f)
    db=AsyncLocalDBClient(psycopg.conninfo.make_conninfo(f['dsn'],user='everydayai'),min_size=1,max_size=1)
    await db._pool.open()
    try:
        await actual_capture(db,actor_user_id=f['user'],org_id=None,task_id=f['task'],execution_token=f['token'],event_id=event)
        # Discard the first return, as after a connection is lost post-commit.
        again=await actual_capture(db,actor_user_id=f['user'],org_id=None,task_id=f['task'],execution_token=f['token'],event_id=event)
        with connect(f) as c:cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert again==cp['_task_user_changes'] and len(again['items'])==1
        assert not restore(cp,root_task_id=f['task'],owner_task_id=f['task']).items[0].delivered
    finally:await db._pool.close()


def test_all_tenant_rollback_rejects_acknowledged_input_and_preserves_rls(change_db):
    f,p=prepared(change_db)
    with connect(f) as c:capture(c,f,steer(c,f))
    with psycopg.connect(change_db) as c:
        forced=c.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid='conversation_turn_checkpoints'::regclass").fetchone()[0]
        with pytest.raises(psycopg.Error,match='NOT_DRAINED'):
            with c.transaction():c.execute((ROOT/'migrations/rollback/363_agent_task_user_changes_rollback.sql').read_text())
        assert c.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid='conversation_turn_checkpoints'::regclass").fetchone()[0]==forced


def test_bootstrap_steer_is_saved_before_any_requirements_are_frozen(change_db):
    from services.task_execution.bootstrap import TaskBootstrap
    from services.task_execution.contracts import ExecutionIdentity
    from uuid import UUID
    f=seed(change_db)
    source=TaskBootstrap(task_id=UUID(f['task']),conversation_id=UUID(f['conv']),actor_user_id=UUID(f['user']),
        input_message_id=UUID(f['message']),original_goal='原要求',execution_identity=ExecutionIdentity(
            workspace_owner_id=f['user'],context_scope='user',personal_context_allowed=True),require_coverage_review=True)
    payload={'messages':[{'role':'user','content':'原要求'}],'_task_bootstrap':source.checkpoint()}
    with connect(f) as c:
        assert c.execute('SELECT save_generation_checkpoint(%s,%s,%s,%s)',(f['task'],f['token'],'before_model',Jsonb(payload))).fetchone()[0]['outcome']=='saved'
        event=steer(c,f);captured=capture(c,f,event)
        cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert cp['_task_bootstrap']==source.checkpoint() and '_task_execution' not in cp
        assert cp['messages']==payload['messages'] and cp['_task_user_changes']==captured['inbox']


@pytest.mark.parametrize('revoked',[False,True])
def test_original_org_steer_capture_requires_current_active_member(change_db,revoked):
    f=seed(change_db);org=str(uuid4())
    with psycopg.connect(change_db) as c:
        c.execute('INSERT INTO organizations(id) VALUES(%s)',(org,))
        c.execute('INSERT INTO org_members(org_id,user_id) VALUES(%s,%s)',(org,f['user']))
        c.execute('UPDATE tasks SET org_id=%s WHERE id=%s',(org,f['task']))
        c.execute('UPDATE conversations SET org_id=%s WHERE id=%s',(org,f['conv']))
    with connect(f) as c:
        c.execute("SELECT set_config('app.org_id',%s,true)",(org,))
        p=task_payload(f);p['_task_execution']['org_id']=org;save(c,f,p)
        event=steer(c,f)
    if revoked:
        with psycopg.connect(change_db) as c:c.execute("UPDATE org_members SET status='inactive' WHERE user_id=%s AND org_id=%s",(f['user'],org))
    with connect(f) as c:
        c.execute("SELECT set_config('app.org_id',%s,true)",(org,))
        if revoked:
            with pytest.raises(psycopg.Error,match='DENIED'):
                with c.transaction():capture(c,f,event)
        else:assert capture(c,f,event)['outcome']=='captured'
    with psycopg.connect(change_db) as c:
        assert c.execute('SELECT status FROM conversation_control_events WHERE id=%s',(event,)).fetchone()[0]==('pending' if revoked else 'applied')
