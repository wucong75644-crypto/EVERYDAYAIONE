"""Revision admission at real native video markers; zero provider IO."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
from uuid import uuid4
import psycopg
import pytest
from services.task_execution.contracts import TaskState
from services.task_execution.requirement_changes import candidate,projection
from tests.test_task_execution_requirement_changes import review,observation
from tests.test_task_execution_user_changes_postgres import steer,capture
from tests.test_task_execution_requirement_changes_postgres import apply
from tests.test_task_execution_video_queue_postgres import (video_db,future_db,scheduled_db,controls_db,continuation_db,
    children_db,native_result_db,operation_db,task_db,composition_db,ecom_db,isolated_db,setup,connection,rpc,claim)
from tests.fixtures.task_revision_gates import install,drain


@pytest.fixture(scope='module')
def revision_video_db(video_db):
    install(video_db)
    yield video_db
    drain(video_db)


def amendment(f):
    with connection(f) as c:
        steer(c,f,'先只要一份文字报告，不用文件')
        events=c.execute('SELECT read_agent_task_user_change_pending(%s,%s,%s)',(f['task'],f['token'],f['task'])).fetchone()[0]['events']
        for event in events:capture(c,f,event['event_id'])
        cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        state=TaskState.model_validate(cp['_task_execution'])
        book=review(state).model_copy(update={'events':tuple(events)});new=candidate(state,book,None)
        proof={**book.model_dump(mode='json'),'observation':__import__('json').loads(observation()),'base_projection':projection(state)}
        assert apply(c,f,state,proof,{**cp,'_task_execution':new.model_dump(mode='json')})['outcome']=='saved'


def test_video_pending_input_and_superseded_step_do_not_mark_dispatch(revision_video_db):
    f=setup(revision_video_db);token,claimed=claim(f);assert claimed['outcome']=='claimed'
    with connection(f) as c:steer(c,f,'先只要一份文字报告，不用文件')
    assert rpc(f,'agent_video_submission_gate',p_task_id=f['child'])['outcome']=='paused'
    assert rpc(f,'mark_agent_video_dispatch',p_task_id=f['child'],p_claim_token=token)['reason']=='paused'
    amendment(f)
    assert rpc(f,'mark_agent_video_dispatch',p_task_id=f['child'],p_claim_token=token)['outcome']=='superseded'
    assert rpc(f,'agent_video_submission_gate',p_task_id=f['child'])['outcome']=='stopped'
    with connection(f) as c:
        assert c.execute("SELECT request_params#>'{_agent_video_lifecycle_v1,dispatch_started_at}' FROM tasks WHERE id=%s",(f['child'],)).fetchone()[0] is None
        assert c.execute('SELECT count(*) FROM credit_transactions WHERE task_id=%s',(f['child'],)).fetchone()[0]==1


def test_worker_actor_narrowing_restores_context_on_ready_and_error(revision_video_db):
    f=setup(revision_video_db,org=True)
    with connection(f,True) as c:
        before=c.execute("SELECT current_setting('app.actor_user_id'),current_setting('app.access_kind')").fetchone()
        assert c.execute('SELECT agent_child_revision_gate(%s,%s)',(f['child'],f['org'])).fetchone()[0]=='ready'
        assert c.execute("SELECT current_setting('app.actor_user_id'),current_setting('app.access_kind')").fetchone()==before
        with pytest.raises(psycopg.Error,match='DENIED'):
            with c.transaction():c.execute('SELECT agent_child_revision_gate(%s,%s)',(str(uuid4()),f['org']))
        assert c.execute("SELECT current_setting('app.actor_user_id'),current_setting('app.access_kind')").fetchone()==before


def test_dispatch_waits_for_original_conversation_lock_before_logical_state(revision_video_db):
    f=setup(revision_video_db);token,claimed=claim(f);assert claimed['outcome']=='claimed'
    started=Event();name='revision-gate-'+str(uuid4())
    def worker():
        with connection(f,True) as c:
            c.execute("SELECT set_config('application_name',%s,true)",(name,))
            c.execute("SET LOCAL statement_timeout='4s'")
            started.set()
            return c.execute('SELECT mark_agent_video_dispatch(%s,%s,%s)',(f['child'],token,f['org'])).fetchone()[0]
    with ThreadPoolExecutor(max_workers=1) as pool:
        with connection(f) as control:
            control.execute('SELECT id FROM conversations WHERE id=%s FOR UPDATE',(f['conv'],))
            future=pool.submit(worker);assert started.wait(1)
            deadline=time.monotonic()+2
            with psycopg.connect(revision_video_db,autocommit=True) as observer:
                while time.monotonic()<deadline:
                    row=observer.execute('SELECT wait_event_type FROM pg_stat_activity WHERE application_name=%s',(name,)).fetchone()
                    if row and row[0]=='Lock':break
                    time.sleep(.01)
                else:raise AssertionError('worker did not reach the conversation lock')
            # If the worker grabs state first, this update and its subsequent
            # conversation lock form the old deterministic deadlock cycle.
            control.execute("SET LOCAL lock_timeout='1s'")
            control.execute('SELECT root_task_id FROM agent_task_states WHERE root_task_id=%s FOR UPDATE',(f['task'],))
            steer(control,f,'先暂停制作，只保留现有成果')
        assert future.result(timeout=5)['reason']=='paused'
