"""A mid-batch directive stops unsent file items and preserves settled ones."""
from types import SimpleNamespace
import psycopg
import pytest
from tests.test_task_execution_file_deletions_postgres import (deletion_db,restore_db,write_db,native_result_db,
    operation_db,task_db,composition_db,ecom_db,isolated_db,setup,scoped)
from tests.test_task_execution_children_postgres import children_db
from tests.test_task_execution_user_changes_postgres import steer
from tests.fixtures.task_revision_gates import install,drain


@pytest.fixture(scope='module')
def revision_file_db(deletion_db,children_db):
    assert deletion_db==children_db
    install(deletion_db)
    yield deletion_db
    drain(deletion_db)


@pytest.mark.asyncio
async def test_new_directive_between_native_file_items_stops_remaining_delete(revision_file_db,tmp_path,monkeypatch):
    f,runtime,executor,run,controller,paths,bucket,objects,originals=await setup(revision_file_db,tmp_path,monkeypatch,count=2)
    original=executor.db.rpc;injected=False
    def rpc(name,params):
        value=original(name,params)
        if name!='complete_agent_task_file_delete_item':return value
        def execute():
            nonlocal injected
            result=value.execute()
            if not injected:
                injected=True
                with scoped(f) as c:steer(c,f,'停止删除剩下的文件，保留已经完成的记录')
            return result
        return SimpleNamespace(execute=execute)
    executor.db.rpc=rpc
    result=await run()
    assert injected and not paths[0].exists() and paths[1].read_bytes()==originals[1]
    with psycopg.connect(revision_file_db) as c:
        rows=c.execute('SELECT item_index,phase FROM agent_task_file_deletions WHERE operation_id IN (SELECT operation_id FROM agent_task_native_writes WHERE owner_task_id=%s) ORDER BY item_index',(f['task'],)).fetchall()
        assert rows==[(0,'completed'),(1,'backed_up')]
        assert c.execute('SELECT count(*) FROM agent_task_native_writes WHERE owner_task_id=%s',(f['task'],)).fetchone()[0]==1
    assert result.status!='success'


@pytest.mark.asyncio
async def test_late_directive_also_blocks_original_version_remaining_item_resume(revision_file_db,tmp_path,monkeypatch):
    f,runtime,executor,run,controller,paths,bucket,objects,originals=await setup(revision_file_db,tmp_path,monkeypatch,mode='lost_settlement',count=2)
    await run();assert not paths[0].exists() and paths[1].read_bytes()==originals[1]
    original=runtime.task_operation_store.db.rpc;injected=False
    def rpc(name,params):
        pending=original(name,params)
        if name!='authorize_agent_task_file_delete_resume':return pending
        async def execute():
            nonlocal injected
            if not injected:
                injected=True
                with scoped(f) as c:steer(c,f,'停止删除剩下的文件')
            return await pending.execute()
        return SimpleNamespace(execute=execute)
    runtime.task_operation_store.db.rpc=rpc
    result=await run('resume-after-change')
    assert injected and result.status!='success' and paths[1].read_bytes()==originals[1]
    assert bucket.put_object_from_file.call_count==2
