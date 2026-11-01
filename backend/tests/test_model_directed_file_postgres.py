"""Original restore handler and actual file receipt in direct mode; local OSS substitute."""
from pathlib import Path
from types import SimpleNamespace
import psycopg,pytest
from services.tools import ToolCall
from services.tools.catalog import build_capability_catalog
from services.task_execution.bootstrap import TaskBootstrap
from services.task_execution.model_directed import record_calls
from services.task_execution.direct_loop import advance_results,end_candidate
from tests.test_task_execution_followup_reuse_postgres import followup_db,continuation_db
from tests.test_task_execution_file_deletions_postgres import deletion_db
from tests.test_task_execution_file_restores_postgres import (children_db,restore_db,write_db,native_result_db,operation_db,
    task_db,composition_db,ecom_db,isolated_db,scoped,save,setup)
ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def direct_file_db(deletion_db,followup_db):
    assert deletion_db==followup_db
    restore_db=deletion_db
    with psycopg.connect(restore_db) as c:
        for name in ('371_agent_task_model_directed_records','372_agent_task_direct_dispatch'):
            c.execute((ROOT/f'migrations/{name}.sql').read_text())
    yield restore_db
    with psycopg.connect(restore_db) as c:
        c.execute("DELETE FROM conversation_turn_checkpoints WHERE state ? '_task_execution'")
        c.execute('DELETE FROM agent_task_states');c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        for name in ('372_agent_task_direct_dispatch','371_agent_task_model_directed_records'):
            c.execute((ROOT/f'migrations/rollback/{name}_rollback.sql').read_text())


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ok','lost_ack','lost_settlement'])
async def test_real_restore_preserves_receipt_and_never_repeats_download(direct_file_db,tmp_path,monkeypatch,mode):
    def state_factory(old,rid):
        b=TaskBootstrap(task_id=old.root_task_id,conversation_id=old.conversation_id,actor_user_id=old.actor_user_id,
            org_id=old.org_id,input_message_id=old.source_message_ids[0],execution_identity=old.execution_identity,
            original_goal='恢复原文件',model_directed=True)
        return record_calls(None,b,[ToolCall('restore-original','restore_file',{'record_id':rid,'filename':'原文件.txt'})],build_capability_catalog())
    f,r,executor,run,controller,target,rid,bucket,cp,body,_=await setup(direct_file_db,tmp_path,monkeypatch,mode=mode,state_factory=state_factory)
    r.checkpoint_base=cp
    async def safe_point(point,replay_payload=None):
        with scoped(f) as c:
            old=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
            candidate={**old,'_task_execution':r.task_execution_state.model_dump(mode='json')}
            assert save(c,f,candidate,expected=old['_task_execution']['revision'])['outcome']=='saved'
        r.replay_save_sequence+=1
    r.safe_point=safe_point
    observed=await run()
    assert target.exists(),str(observed.exception)
    assert target.read_bytes()==body,str(observed.exception)
    assert r.task_execution_state.operations[0].status==('completed' if mode=='ok' else 'uncertain')
    prepared=SimpleNamespace(execution_context=executor.tool_runtime.context('read'),messages=[])
    candidate=await advance_results(r,prepared,reconcile_pending=True)
    assert candidate.operations[0].status=='completed' and len(candidate.results)==1
    assert end_candidate(candidate).status=='completed'
    with scoped(f) as c:
        saved=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert save(c,f,{**saved,'_task_execution':candidate.model_dump(mode='json')},expected=saved['_task_execution']['revision'])['outcome']=='saved'
        assert saved['opaque_context']=={'preserve':True}
    r.task_execution_state=candidate
    replay=await run()
    assert not replay.execution.handler_started and target.read_bytes()==body,str(replay.exception)
    if mode=='ok':assert replay.execution.replayed
    bucket.get_object_to_file.assert_called_once()


@pytest.mark.asyncio
async def test_direct_delete_keeps_original_confirmation_backups_and_per_item_authorizers(direct_file_db,tmp_path,monkeypatch):
    from tests.test_task_execution_file_deletions_postgres import setup as deletion_setup
    f,r,executor,run,controller,paths,bucket,objects,originals=await deletion_setup(
        direct_file_db,tmp_path,monkeypatch,count=2,model_directed=True)
    result=await run()
    assert result.execution.handler_started and all(not p.exists() for p in paths),str(result.exception)
    assert r.task_execution_state.operations[0].status=='completed'
    executor.tool_confirmer.assert_awaited_once()
    assert sorted(objects.values())==sorted(originals) and bucket.put_object_from_file.call_count==2
    done=await advance_results(r,SimpleNamespace(messages=[],execution_context=executor.tool_runtime.context('read')))
    assert len(done.results)==1 and end_candidate(done).status=='completed'
    replay=await run()
    assert replay.execution.replayed and not replay.execution.handler_started
    assert bucket.put_object_from_file.call_count==2
