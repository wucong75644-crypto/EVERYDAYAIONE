"""Pause/resume/cancel across the closed-source and pre-apply queue windows."""
from pathlib import Path
from uuid import uuid4
import psycopg
import pytest
from tests.test_task_execution_continuation_postgres import (
    continuation_db,children_db,operation_db,task_db,composition_db,ecom_db,isolated_db,finished,enqueue,bundle_for)
from tests.test_skill_composition_postgres import connect

ROOT=Path(__file__).parents[1]

@pytest.fixture(scope='module')
def controls_db(continuation_db):
    from tests.test_task_execution_legacy_actor_postgres import install_legacy_actor_schema
    install_legacy_actor_schema(continuation_db)
    with psycopg.connect(continuation_db) as c:
        c.execute('SET LOCAL ROLE everydayai_owner')
        c.execute((ROOT/'migrations/345_agent_task_controls.sql').read_text())
        c.execute((ROOT/'migrations/346_agent_task_actor_control_lock_order.sql').read_text())
    yield continuation_db
    with psycopg.connect(continuation_db) as c:
        c.execute("DELETE FROM conversation_turn_checkpoints WHERE state ? '_task_execution'")
        c.execute('DELETE FROM agent_task_states')
        c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        c.execute('SET LOCAL ROLE everydayai_owner')
        c.execute((ROOT/'migrations/rollback/346_agent_task_actor_control_lock_order_rollback.sql').read_text())
        c.execute((ROOT/'migrations/rollback/345_agent_task_controls_rollback.sql').read_text())


def control(f,action,*,user=None):
    with connect(f) as c:
        return c.execute('SELECT control_agent_task(%s,%s,%s,%s)',(f['task'],user or f['user'],None,action)).fetchone()[0]


def facts(f):
    with connect(f) as c:
        state=c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',(f['task'],)).fetchone()[0]
        cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
    assert state==cp['_task_execution']
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize('already_queued',[False,True])
async def test_waiting_goal_pause_keeps_children_and_resume_reuses_original_context(controls_db,monkeypatch,already_queued):
    f,state,child,event=await finished(controls_db,monkeypatch)
    queued=enqueue(f,state,event) if already_queued else None
    assert control(f,'pause')['outcome']=='paused'
    paused=facts(f)
    assert paused['status']=='paused' and paused['artifacts']==state.model_dump(mode='json')['artifacts']
    assert enqueue(f,type(state).model_validate(paused),event)['outcome'] in {'waiting','already_queued'}
    resumed=control(f,'resume')
    assert resumed['outcome']=='enqueued'
    current=type(state).model_validate(facts(f))
    assert current.status=='waiting_dependencies' and current.original_goal==state.original_goal
    queued=queued or enqueue(f,current,event)
    task,claim,payload,candidate=bundle_for(f,queued['task_id'])
    assert candidate.status=='completed' and candidate.original_goal==state.original_goal
    with connect(f) as c:
        assert c.execute('SELECT status FROM tasks WHERE id=%s',(child['task_id'],)).fetchone()[0]=='completed'
        assert c.execute('SELECT context_revision FROM messages WHERE id=%s',(f['message'],)).fetchone()[0]==1


@pytest.mark.asyncio
@pytest.mark.parametrize('already_queued',[False,True])
async def test_cancel_waiting_goal_preserves_published_artifacts_and_never_resumes(controls_db,monkeypatch,already_queued):
    f,state,child,event=await finished(controls_db,monkeypatch)
    queued=enqueue(f,state,event) if already_queued else None
    assert control(f,'cancel')['outcome']=='cancelled'
    assert facts(f)['status']=='cancelled'
    assert control(f,'resume')['outcome']=='already_cancelled'
    with connect(f) as c:
        assert c.execute('SELECT status FROM agent_task_events WHERE id=%s',(event,)).fetchone()[0]=='obsolete'
        assert c.execute('SELECT status FROM tasks WHERE id=%s',(child['task_id'],)).fetchone()[0]=='completed'
        if queued: assert c.execute('SELECT status FROM tasks WHERE id=%s',(queued['task_id'],)).fetchone()[0]=='cancelled'


@pytest.mark.asyncio
async def test_cross_actor_control_is_rejected_without_state_change(controls_db,monkeypatch):
    f,state,child,event=await finished(controls_db,monkeypatch)
    with pytest.raises(psycopg.errors.InsufficientPrivilege): control(f,'cancel',user=str(uuid4()))
    assert facts(f)==state.model_dump(mode='json')


@pytest.mark.asyncio
@pytest.mark.parametrize('action',['pause','cancel'])
async def test_running_continuation_owner_uses_original_fenced_control_and_keeps_context(controls_db,monkeypatch,action):
    from psycopg.types.json import Jsonb
    from services.task_execution.contracts import TaskState
    f,state,child,event=await finished(controls_db,monkeypatch,downstream=True)
    queued=enqueue(f,state,event)
    task,claim,payload,candidate=bundle_for(f,queued['task_id'])
    with connect(f) as c:
        assert c.execute('SELECT apply_agent_image_continuation(%s,%s,%s,%s)',
            (claim.task_id,claim.execution_token,state.revision,Jsonb(payload))).fetchone()[0]['outcome']=='saved'
    assert control(f,action)['outcome']=='delegate'
    with connect(f) as c:
        outcome=c.execute('SELECT '+action+'_generation_turn_owned(%s,%s,%s)',
            (claim.task_id,claim.execution_token,'user_'+action)).fetchone()[0]
        assert outcome['outcome']==('paused' if action=='pause' else 'cancelled')
        logical=c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',(f['task'],)).fetchone()[0]
        cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(claim.task_id,)).fetchone()[0]
        assert cp['_task_execution']==logical and logical['status']==('paused' if action=='pause' else 'cancelled')
        assert logical['artifacts']==candidate.model_dump(mode='json')['artifacts']
        assert logical['original_goal']==state.original_goal and cp['messages']==payload['messages']
        assert c.execute('SELECT context_revision FROM messages WHERE id=%s',(f['message'],)).fetchone()[0]==1
        if action=='pause':
            resumed=c.execute('SELECT resume_paused_generation_turn(%s,%s,%s)',(claim.task_id,f['user'],None)).fetchone()[0]
            assert resumed['outcome']=='enqueued'
            logical=c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',(f['task'],)).fetchone()[0]
            assert logical['status']=='active' and logical['requirements']==state.model_dump(mode='json')['requirements']
            paused=TaskState.model_validate(logical)
            # A paused attempt never retains authority after explicit resume.
            assert c.execute('SELECT save_agent_task_checkpoint(%s,%s,%s,%s,%s)',
                (claim.task_id,claim.execution_token,'before_model',Jsonb({'messages':cp['messages'],'_task_execution':logical}),paused.revision)).fetchone()[0]['outcome']=='ownership_lost'
