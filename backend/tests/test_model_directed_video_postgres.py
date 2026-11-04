"""Original native video queue/asset facts and automatic direct-mode receipt import."""
from pathlib import Path
from types import SimpleNamespace
import psycopg,pytest
from tests.test_task_execution_video_queue_postgres import (video_db,future_db,scheduled_db,controls_db,continuation_db,
    children_db,native_result_db,operation_db,task_db,composition_db,ecom_db,isolated_db,setup,publish_success,connection,rpc,owned_host)
from tests.test_task_execution_followup_reuse_postgres import followup_db
from tests.test_task_execution_operations_postgres import complete,typed_store
from services.task_execution.contracts import TaskState
from services.tools import ToolContext
from tests.test_task_execution_video_storage import storage
from services.handlers.native_video_storage import persist
from services.task_execution.direct_loop import advance_results,end_candidate
ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def direct_video_db(video_db,followup_db):
    assert video_db==followup_db
    with psycopg.connect(video_db) as c:
        for name in ('371_agent_task_model_directed_records','372_agent_task_direct_dispatch'):
            c.execute((ROOT/f'migrations/{name}.sql').read_text())
    yield video_db
    with psycopg.connect(video_db) as c:
        c.execute("DELETE FROM conversation_turn_checkpoints WHERE state ? '_task_execution'")
        c.execute('DELETE FROM agent_task_events');c.execute('DELETE FROM agent_task_states');c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        for name in ('372_agent_task_direct_dispatch','371_agent_task_model_directed_records'):
            c.execute((ROOT/f'migrations/rollback/{name}_rollback.sql').read_text())


@pytest.mark.asyncio
async def test_direct_video_waits_for_real_publication_then_imports_native_receipt(direct_video_db,monkeypatch):
    f=setup(direct_video_db,model_directed=True)
    with connection(f) as c:
        accepted=complete(c,f,f['state'].model_dump(mode='json'),status='accepted',receipt='native-video:'+f['child'])
    state=TaskState.model_validate(accepted['state'])
    runtime=SimpleNamespace(task_id=f['task'],task_execution_state=state,task_operation_store=typed_store(f))
    prepared=SimpleNamespace(messages=[],execution_context=ToolContext(agent_domain='general',permission_mode='auto',execution_mode='interactive',actor_user_id=f['user'],
        workspace_owner_id=f['user'],org_id=None,conversation_id=f['conv'],context_scope='user',personal_context_allowed=True))
    assert end_candidate(state).status=='waiting_dependencies'
    _,oss,_=storage()
    stored=await persist(oss,{'id':f['child'],'user_id':f['user'],'org_id':None},'https://provider.invalid/fixture')
    monkeypatch.setattr('services.oss_service.get_oss_service',lambda:oss)
    output=publish_success(f,stored)
    done=await advance_results(runtime,prepared,reconcile_pending=True)
    assert len(done.results)==1 and done.operations[0].status=='completed'
    assert end_candidate(done).status=='completed'
    assert done.artifacts[0].reference.startswith('native-write:') or 'video' in done.artifacts[0].id
    assert not getattr(runtime,'task_direct_result_issues',[])
    with connection(f) as c:
        assert c.execute('SELECT count(*) FROM tasks WHERE type=\'video\' AND conversation_id=%s',(f['conv'],)).fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM credit_transactions WHERE task_id=%s',(f['child'],)).fetchone()[0]==1
