"""Actual original file tools and native DB; only OSS transport is replaced."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import hashlib

import psycopg
import pytest
from tests.test_task_execution_children_postgres import children_db
from tests.fixtures.task_revision_gates import install as install_revision_gates, drain as drain_revision_gates

from services.file_executor import FileExecutor
from services.task_execution.contracts import PlanStep,WorkPlan
from services.task_execution.reducer import revise_plan,finish
from services.task_execution.native_results import lifecycle_for
from services.task_execution.controller import TaskController
from services.tools.resource_access import ResourceAccessBoundary,ResourceRule
from tests.test_task_execution_file_restores_postgres import (restore_db,write_db,native_result_db,operation_db,task_db,
    composition_db,ecom_db,isolated_db,scoped,save,setup as restore_setup)

ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def deletion_db(restore_db):
    with psycopg.connect(restore_db) as c:
        c.execute((ROOT/'migrations/356_agent_task_file_deletions.sql').read_text())
        c.execute((ROOT/'migrations/359_agent_task_file_delete_resumptions.sql').read_text())
    yield restore_db
    with psycopg.connect(restore_db) as c:
        c.execute('DELETE FROM agent_task_file_delete_resumptions')
        c.execute((ROOT/'migrations/rollback/359_agent_task_file_delete_resumptions_rollback.sql').read_text())
        c.execute('DELETE FROM agent_task_file_deletions')
        c.execute('DELETE FROM deleted_files WHERE native_status IS NOT NULL OR org_id IS NULL')
        c.execute((ROOT/'migrations/rollback/356_agent_task_file_deletions_rollback.sql').read_text())


async def setup(dsn,tmp_path,monkeypatch,*,mode='ok',count=1,personal=False):
    f,runtime,executor,_,_,target,_,bucket,cp,body,calls=await restore_setup(dsn,tmp_path,monkeypatch,personal=personal)
    state=revise_plan(runtime.task_execution_state,WorkPlan(expected_revision=runtime.task_execution_state.revision,
        steps=(PlanStep(id='delete',title='删除原文件并提供恢复记录',capability='workspace.file.delete',requirement_ids=('report',)),)))
    with scoped(f) as c:
        saved=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        saved['_task_execution']=state.model_dump(mode='json');save(c,f,saved,expected=runtime.task_execution_state.revision)
    runtime.task_execution_state=state
    access=ResourceAccessBoundary((ResourceRule(('delete','restore','read','list'),directories=('.',)),),'delete-test',True)
    executor.resource_access_boundary=access;executor.tool_confirmer=AsyncMock(return_value=True)
    paths=[target]+([target.with_name('第二个文件.txt')] if count==2 else [])
    target.parent.mkdir(parents=True,exist_ok=True)
    for index,path in enumerate(paths):path.write_bytes(body+str(index).encode())
    originals=[p.read_bytes() for p in paths];objects={}
    def put(key,path,*,headers):
        assert headers=={'x-oss-forbid-overwrite':'true'} and key not in objects
        if mode=='backup_error':raise OSError('backup unavailable')
        data=Path(path).read_bytes();objects[key]=data
        return SimpleNamespace(etag=hashlib.md5(data).hexdigest())
    bucket.put_object_from_file=Mock(side_effect=put)
    bucket.get_object_meta=Mock(side_effect=lambda key:SimpleNamespace(etag=hashlib.md5(objects[key]).hexdigest() if key in objects else 'original-etag'))
    def download(key,path,**options):
        data=objects[key];assert options=={'headers':{'If-Match':'"'+hashlib.md5(data).hexdigest()+'"'}}
        Path(path).write_bytes(data)
    bucket.get_object_to_file=Mock(side_effect=download)
    old_rpc=executor.db.rpc;failed=False
    def rpc(name,params):
        if name!='complete_agent_task_file_delete_item':return old_rpc(name,params)
        def execute():
            nonlocal failed
            if mode=='lost_settlement' and not failed:failed=True;raise RuntimeError('settlement return unconfirmed')
            result=old_rpc(name,params).execute()
            if mode=='lost_ack' and not failed:failed=True;raise RuntimeError('settlement committed, return lost')
            return result
        return SimpleNamespace(execute=execute)
    executor.db.rpc=rpc
    async def find(_filename,*,record_id=None):
        with scoped(f) as c:
            row=c.execute("SELECT id,relative_path,oss_object_key FROM deleted_files WHERE id=%s AND NOT purged AND purge_after>now() AND COALESCE(native_status,'completed')='completed'",(record_id,)).fetchone()
        return dict(zip(('id','relative_path','oss_object_key'),row)) if row else None
    executor._find_deleted_record=AsyncMock(side_effect=find);executor._record_deleted_files=Mock()
    handler=SimpleNamespace(_actor_runtime=runtime,_actor_enabled=True,_actor_invocation_store=object(),
        _actor_execution_token=f['token'],_actor_turn_id=f['turn'])
    async def run(call_id='delete-original'):
        executor._task_execution_state=runtime.task_execution_state
        life=lifecycle_for(handler,executor.tool_runtime.context(call_id),executor.tool_runtime.registry,'file_delete')
        return await executor.tool_runtime.execute('file_delete',{'files':[str(p) for p in paths]},call_id=call_id,lifecycle=life)
    async def commit(candidate,revision):
        with scoped(f) as c:
            saved=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
            saved['_task_execution']=candidate.model_dump(mode='json');return save(c,f,saved,expected=revision)
    def controller():return TaskController(runtime.task_execution_state,owner_task_id=f['task'],commit=commit,
        operation_store=runtime.task_operation_store,result_context=executor.tool_runtime.context('read'))
    return f,runtime,executor,run,controller,paths,bucket,objects,originals


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ok','lost_settlement','lost_ack'])
async def test_delete_and_confirmation_loss_keep_real_recovery_record_and_original_context(deletion_db,tmp_path,monkeypatch,mode):
    f,runtime,executor,run,controller,paths,bucket,objects,originals=await setup(deletion_db,tmp_path,monkeypatch,mode=mode)
    result=await run();op=runtime.task_execution_state.operations[-1]
    assert op.status==('completed' if mode=='ok' else 'uncertain'),repr(result.exception)
    assert not paths[0].exists() and list(objects.values())==originals
    executor._record_deleted_files.assert_not_called()
    if mode=='ok':
        replay=await run('new-call-id');assert replay.execution.replayed
    with scoped(f) as c:
        original=c.execute('SELECT result FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]
        before=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
    ctrl=controller();read=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='read')
    assert read.result['ok'] and read.result['operation_status']=='completed',read.result
    rid=read.result['items'][0]['record_id'];assert await executor._find_deleted_record('',record_id=rid)
    imported=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='import')
    assert imported.result['ok'],imported.result
    done=await ctrl.control('complete_task_step',{'expected_revision':ctrl.state.revision,'step_id':'delete',
        'artifact_ids':[imported.result['imported_artifact_id']]},call_id='done')
    assert done.result['ok'] and finish(ctrl.state).status=='completed',done.result
    with scoped(f) as c:
        assert c.execute('SELECT result FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]==original
        saved=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert saved['messages']==before['messages'] and saved['opaque_context']==before['opaque_context']
    bucket.put_object_from_file.assert_called_once()
    # The original restore handler consumes the new immutable backup and record.
    executor._task_execution_state=None
    restored=await executor.tool_runtime.execute('restore_file',{'record_id':rid,'filename':paths[0].name},call_id='original-restore')
    assert restored.status=='success' and paths[0].read_bytes()==originals[0],repr(restored.exception)
    bucket.get_object_to_file.assert_called_once()


@pytest.mark.asyncio
async def test_partial_batch_reconciles_only_detached_original_and_never_deletes_new_same_name_file(deletion_db,tmp_path,monkeypatch):
    f,runtime,executor,run,controller,paths,bucket,objects,originals=await setup(deletion_db,tmp_path,monkeypatch,mode='lost_settlement',count=2)
    await run();assert not paths[0].exists() and paths[1].read_bytes()==originals[1]
    paths[0].write_bytes(b'new user file')
    ctrl=controller();op=ctrl.state.operations[-1]
    read=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='read')
    assert read.result['ok'] and read.result['operation_status']=='uncertain',read.result
    assert [i['phase'] for i in read.result['items']]==['completed','backed_up']
    assert read.result['resume']['tool_name']=='file_delete' and read.result['resume']['original_call_id']=='delete-original'
    assert len(read.result['resume']['remaining_paths'])==1
    import json
    assert 'dispatch_token' not in json.dumps(read.result)
    assert await executor._find_deleted_record('',record_id=read.result['items'][0]['record_id'])
    assert await executor._find_deleted_record('',record_id=read.result['items'][1]['record_id']) is None
    refused=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='import')
    assert not refused.result['ok'] and paths[0].read_bytes()==b'new user file' and paths[1].read_bytes()==originals[1]
    again=await run('fresh-id');assert again.status=='success',repr(again.exception)
    assert paths[0].read_bytes()==b'new user file' and not paths[1].exists()
    assert bucket.put_object_from_file.call_count==2


@pytest.mark.asyncio
async def test_backup_failure_keeps_originals_and_preallocates_expiry_address_for_every_attempt(deletion_db,tmp_path,monkeypatch):
    f,runtime,executor,run,controller,paths,bucket,objects,originals=await setup(deletion_db,tmp_path,monkeypatch,mode='backup_error',count=2)
    result=await run();assert result.status=='error'
    assert runtime.task_execution_state.operations[-1].status=='failed_before_dispatch'
    assert [p.read_bytes() for p in paths]==originals
    with scoped(f) as c:
        rows=c.execute("SELECT id,oss_object_key,native_status,purge_after>now() FROM deleted_files WHERE native_status IS NOT NULL AND user_id=%s",(f['user'],)).fetchall()
    assert len(rows)==2 and all(row[2:] == ('allocated',True) for row in rows)
    for row in rows:assert await executor._find_deleted_record('',record_id=row[0]) is None
    await run('new-attempt')
    with scoped(f) as c:
        assert c.execute("SELECT count(DISTINCT oss_object_key) FROM deleted_files WHERE native_status IS NOT NULL AND user_id=%s",(f['user'],)).fetchone()[0]==4
    assert [p.read_bytes() for p in paths]==originals


@pytest.mark.asyncio
async def test_original_task_removal_does_not_block_or_erase_already_detached_file_fact(deletion_db,tmp_path,monkeypatch):
    from services.task_execution.file_restores import snapshot_path,identity
    from services.file_resources import content_digest
    f,runtime,executor,run,controller,paths,bucket,objects,originals=await setup(deletion_db,tmp_path,monkeypatch,mode='lost_settlement')
    await run();op=runtime.task_execution_state.operations[-1]
    with scoped(f) as c:
        row=c.execute('SELECT native_invocation_id,settlement_token,manifest,deleted_record_id FROM agent_task_file_deletions WHERE operation_id=%s',(op.id,)).fetchone()
    files=FileExecutor(str(tmp_path),f['user'],f['org'])
    quarantine=snapshot_path(files,op.id,str(row[0])).parent/'deleted-0'
    assert identity(quarantine)==row[2]['version'][:3] and content_digest(quarantine)==row[2]['sha256']
    with psycopg.connect(deletion_db) as c:c.execute('DELETE FROM tasks WHERE id=%s',(f['task'],))
    result=executor.db.rpc('complete_agent_task_file_delete_item',{'p_operation_id':op.id,'p_dispatch_token':str(row[1]),
        'p_item_index':0,'p_evidence':row[2]}).execute()
    assert result.data=={'outcome':'recorded'}
    assert await executor._find_deleted_record('',record_id=row[3])
    assert not paths[0].exists()


@pytest.mark.asyncio
async def test_cancel_between_files_preserves_completed_record_and_unmodified_remaining_file(deletion_db,tmp_path,monkeypatch):
    from services.conversation_state import ConversationStopRequested
    f,runtime,executor,run,controller,paths,bucket,objects,originals=await setup(deletion_db,tmp_path,monkeypatch,count=2)
    original_rpc=executor.db.rpc
    def rpc(name,params):
        if name=='authorize_agent_task_file_delete_item' and params['p_item_index']==1:
            with psycopg.connect(deletion_db) as c:c.execute("UPDATE tasks SET status='cancelled' WHERE id=%s",(f['task'],))
        return original_rpc(name,params)
    executor.db.rpc=rpc
    with pytest.raises(ConversationStopRequested):await run()
    assert not paths[0].exists() and paths[1].read_bytes()==originals[1]
    with scoped(f) as c:
        rows=c.execute('SELECT f.phase,d.native_status FROM agent_task_file_deletions f JOIN deleted_files d ON d.id=f.deleted_record_id WHERE f.actor_user_id=%s ORDER BY item_index',(f['user'],)).fetchall()
    assert rows==[('completed','completed'),('backed_up','allocated')]


@pytest.mark.asyncio
async def test_copied_snapshot_or_absence_does_not_prove_detachment(deletion_db,tmp_path,monkeypatch):
    import shutil
    from services.task_execution import file_deletions
    f,runtime,executor,run,controller,paths,bucket,objects,originals=await setup(deletion_db,tmp_path,monkeypatch)
    # Deliberately replace only the actual rename syscall with a copy.
    monkeypatch.setattr(file_deletions.os,'rename',lambda source,destination:shutil.copyfile(source,destination))
    await run();ctrl=controller();op=ctrl.state.operations[-1]
    assert paths[0].read_bytes()==originals[0] and op.status=='uncertain'
    read=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='read')
    assert not read.result['ok'] and paths[0].read_bytes()==originals[0]
    with scoped(f) as c:assert c.execute('SELECT native_status FROM deleted_files WHERE native_status IS NOT NULL AND user_id=%s',(f['user'],)).fetchone()[0]=='allocated'


@pytest.mark.asyncio
async def test_rollback_refuses_hidden_foreign_tenant_evidence_and_preserves_rls(deletion_db,tmp_path,monkeypatch):
    from uuid import uuid4
    f,runtime,executor,run,controller,paths,bucket,objects,originals=await setup(deletion_db,tmp_path,monkeypatch)
    await run()
    with scoped(f) as c:
        c.execute("SELECT set_config('app.actor_user_id',%s,true)",(str(uuid4()),))
        assert c.execute('SELECT count(*) FROM agent_task_file_deletions').fetchone()[0]==0
        with pytest.raises(psycopg.Error,match='TASK_FILE_DELETIONS_NOT_DRAINED'):
            with c.transaction():c.execute((ROOT/'migrations/rollback/356_agent_task_file_deletions_rollback.sql').read_text())
        assert c.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid='agent_task_file_deletions'::regclass").fetchone()[0]


@pytest.mark.asyncio
async def test_personal_workspace_without_org_preserves_delete_and_original_restore(deletion_db,tmp_path,monkeypatch):
    f,runtime,executor,run,controller,paths,bucket,objects,originals=await setup(deletion_db,tmp_path,monkeypatch,personal=True)
    assert f['org'] is None
    result=await run();assert result.status=='success',repr(result.exception)
    op=runtime.task_execution_state.operations[-1];ctrl=controller()
    read=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='read')
    assert read.result['ok'] and read.result['operation_status']=='completed',read.result
    rid=read.result['items'][0]['record_id']
    with scoped(f) as c:assert c.execute('SELECT org_id,native_status FROM deleted_files WHERE id=%s',(rid,)).fetchone()==(None,'completed')
    executor._task_execution_state=None
    restored=await executor.tool_runtime.execute('restore_file',{'record_id':rid,'filename':paths[0].name},call_id='restore-personal')
    assert restored.status=='success' and paths[0].read_bytes()==originals[0],repr(restored.exception)


@pytest.fixture(scope='module', autouse=True)
def current_domain_revision_schema(deletion_db, children_db):
    """Exercise current dispatch guards with real revision migrations."""
    assert deletion_db == children_db
    install_revision_gates(deletion_db)
    yield
    drain_revision_gates(deletion_db)
