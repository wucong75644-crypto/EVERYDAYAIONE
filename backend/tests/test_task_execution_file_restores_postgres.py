"""Original ToolRuntime/restore handler + actual native DB; OSS bytes are local."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest
from tests.test_task_execution_children_postgres import children_db
from tests.fixtures.task_revision_gates import install as install_revision_gates, drain as drain_revision_gates

from core.config import get_settings
from services.agent.tool_executor import ToolExecutor
from services.file_executor import FileExecutor
from services.task_execution.contracts import TaskState
from services.task_execution.controller import TaskController
from services.task_execution.native_results import lifecycle_for
from services.task_execution.operation_store import TaskOperationStore
from services.task_execution.reducer import finish
from services.tools.resource_access import ResourceAccessBoundary,ResourceRule
from tests.tool_runtime_support import IdentityDB
from tests.test_task_execution_native_writes_postgres import (write_db,native_result_db,operation_db,task_db,
    composition_db,ecom_db,isolated_db,scoped,seed,task_payload,save)

ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def restore_db(write_db):
    with psycopg.connect(write_db) as c:
        c.execute('SET LOCAL ROLE everydayai')
        c.execute((ROOT/'migrations/111_deleted_files.sql').read_text())
        c.execute((ROOT/'migrations/355_agent_task_file_restores.sql').read_text())
    yield write_db
    with psycopg.connect(write_db) as c:
        c.execute("DELETE FROM agent_task_native_writes WHERE domain='file'")
        c.execute((ROOT/'migrations/rollback/355_agent_task_file_restores_rollback.sql').read_text())
        c.execute('DROP TABLE deleted_files')


async def setup(dsn,tmp_path,monkeypatch,*,mode='ok',artifact_kind='receipt',personal=False):
    from services.tools.catalog import build_capability_catalog
    f=seed(dsn);f['org']=None if personal else str(uuid4())
    monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path))
    monkeypatch.setattr(get_settings(),'file_workspace_enabled',True)
    files=FileExecutor(str(tmp_path),f['user'],f['org']);target=Path(files.workspace_root)/'下载/原文件.txt'
    rel=target.relative_to(files._workspace_base).as_posix();body='原始文件8入'.encode()
    with psycopg.connect(dsn) as c:
        if f['org']:
            c.execute('INSERT INTO organizations(id) VALUES(%s)',(f['org'],))
            c.execute('INSERT INTO org_members(org_id,user_id) VALUES(%s,%s)',(f['org'],f['user']))
        c.execute('UPDATE tasks SET org_id=%s WHERE id=%s',(f['org'],f['task']))
        rid=c.execute("INSERT INTO deleted_files(org_id,user_id,relative_path,oss_object_key,purge_after) VALUES(%s,%s,%s,%s,now()+interval '30 days') RETURNING id",
            (f['org'],f['user'],rel,'workspace/'+rel)).fetchone()[0]
    value=task_payload(f)['_task_execution'];value.update(org_id=f['org'],image_binding_version=3,
        steps=[{'id':'restore','title':'恢复原文件','capability':'platform.restore_file','requirement_ids':['report']}])
    value['requirements'][0]['artifact_kind']=artifact_kind;state=TaskState.model_validate(value)
    cp={'messages':[{'role':'system','content':'原会话摘要'},{'role':'user','content':'恢复原文件'}],
        'opaque_context':{'preserve':True},'_task_execution':state.model_dump(mode='json')}
    with scoped(f) as c:save(c,f,cp)
    calls=[];settled=False
    def rpc(name,params):
        def run():
            nonlocal settled
            calls.append(name)
            if name=='complete_agent_task_file_restore' and mode=='lost_settlement' and not settled:
                settled=True;raise RuntimeError('settlement not acknowledged')
            query=sql.SQL('SELECT {}({})').format(sql.Identifier(name),sql.SQL(',').join(sql.SQL('{}=>%s').format(sql.Identifier(k)) for k in params))
            with scoped(f) as c:value=c.execute(query,tuple(Jsonb(v) if isinstance(v,dict) else v for v in params.values())).fetchone()[0]
            if name=='complete_agent_task_file_restore' and mode=='lost_ack' and not settled:
                settled=True;raise RuntimeError('settlement committed, return lost')
            return SimpleNamespace(data=value)
        return SimpleNamespace(execute=run)
    async def async_run(name,params):return rpc(name,params).execute()
    db=IdentityDB(f['user'],f['org']);db.rpc=rpc
    adb=SimpleNamespace(rpc=lambda name,params:SimpleNamespace(execute=lambda:async_run(name,params)))
    access=ResourceAccessBoundary((ResourceRule(('restore','read','list'),directories=('.',)),),'restore-test',True)
    executor=ToolExecutor(db,f['user'],f['conv'],f['org'],task_id=f['task'],tool_entrypoint='model',
        resource_access_boundary=access,image_execution_token=f['token'])
    executor._task_execution_state=state
    async def find(_filename,*,record_id=None):
        with scoped(f) as c:row=c.execute('SELECT id,relative_path,oss_object_key FROM deleted_files WHERE id=%s AND NOT purged AND purge_after>now()',(record_id or rid,)).fetchone()
        return dict(zip(('id','relative_path','oss_object_key'),row)) if row else None
    executor._find_deleted_record=AsyncMock(side_effect=find);executor._mark_restored=AsyncMock()
    def download(key,path,**options):
        assert key=='workspace/'+rel and options=={'headers':{'If-Match':'"original-etag"'}}
        Path(path).write_bytes(body)
    bucket=SimpleNamespace(get_object_meta=Mock(return_value=SimpleNamespace(etag='original-etag')),
        get_object_to_file=Mock(side_effect=download))
    monkeypatch.setattr('services.oss_service.get_oss_service',lambda:SimpleNamespace(bucket=bucket))
    store=TaskOperationStore(adb,task_id=f['task'],execution_token=f['token'],turn_id=f['turn'],registry=build_capability_catalog())
    runtime=SimpleNamespace(task_id=f['task'],task_execution_state=state,task_operation_store=store,replay_save_sequence=0)
    handler=SimpleNamespace(_actor_runtime=runtime,_actor_enabled=True,_actor_invocation_store=object(),
        _actor_execution_token=f['token'],_actor_turn_id=f['turn'])
    async def run(call_id='restore-original'):
        executor._task_execution_state=runtime.task_execution_state
        context=executor.tool_runtime.context(call_id)
        life=lifecycle_for(handler,context,executor.tool_runtime.registry,'restore_file')
        return await executor.tool_runtime.execute('restore_file',{'record_id':rid,'filename':'原文件.txt'},call_id=call_id,lifecycle=life)
    async def commit(candidate,revision):
        with scoped(f) as c:
            saved=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
            saved['_task_execution']=candidate.model_dump(mode='json');return save(c,f,saved,expected=revision)
    def controller(context=None):return TaskController(runtime.task_execution_state,owner_task_id=f['task'],commit=commit,
        operation_store=store,result_context=context or executor.tool_runtime.context('read'))
    return f,runtime,executor,run,controller,target,rid,bucket,cp,body,calls


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ok','lost_settlement','lost_ack'])
async def test_original_restore_and_lost_confirmation_recover_without_download_or_clobber(restore_db,tmp_path,monkeypatch,mode):
    f,runtime,executor,run,controller,target,rid,bucket,cp,body,calls=await setup(restore_db,tmp_path,monkeypatch,mode=mode)
    result=await run()
    assert runtime.task_execution_state.operations, str(result.exception)
    operation=runtime.task_execution_state.operations[-1]
    assert operation.status==('completed' if mode=='ok' else 'uncertain'),repr(result.exception)
    assert target.read_bytes()==body
    with scoped(f) as c:
        original=c.execute('SELECT result FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert c.execute('SELECT purged FROM deleted_files WHERE id=%s',(rid,)).fetchone()[0] is (mode!='lost_settlement')
    if mode=='ok':
        replay=await run('new-call-id')
        assert replay.execution.replayed and target.read_bytes()==body
        bucket.get_object_to_file.assert_called_once()
    ctrl=controller()
    read=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':operation.id},call_id='read')
    assert read.result['ok'] and read.result['operation_status']=='completed',read.result
    imported=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':operation.id},call_id='import')
    assert imported.result['ok'],imported.result
    done=await ctrl.control('complete_task_step',{'expected_revision':ctrl.state.revision,'step_id':'restore',
        'artifact_ids':[imported.result['imported_artifact_id']]},call_id='done')
    assert done.result['ok'] and finish(ctrl.state).status=='completed',done.result
    with scoped(f) as c:
        assert c.execute('SELECT result FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]==original
        assert c.execute('SELECT purged FROM deleted_files WHERE id=%s',(rid,)).fetchone()[0]
        saved=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert saved['opaque_context']==cp['opaque_context'] and saved['messages']==cp['messages']
    bucket.get_object_to_file.assert_called_once();executor._mark_restored.assert_not_awaited()


@pytest.mark.asyncio
async def test_later_same_name_file_cannot_prove_original_restore_or_be_overwritten(restore_db,tmp_path,monkeypatch):
    f,runtime,executor,run,controller,target,rid,bucket,cp,body,calls=await setup(restore_db,tmp_path,monkeypatch,mode='lost_settlement')
    await run();operation=runtime.task_execution_state.operations[-1]
    target.unlink();target.write_bytes(body)  # Same bytes, different inode.
    ctrl=controller();before=ctrl.state
    read=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':operation.id},call_id='read')
    assert not read.result['ok'] and ctrl.state==before,read.result
    again=await run('another-call-id')
    assert again.status!='success' and target.read_bytes()==body
    bucket.get_object_to_file.assert_called_once()
    with scoped(f) as c:assert not c.execute('SELECT purged FROM deleted_files WHERE id=%s',(rid,)).fetchone()[0]


@pytest.mark.asyncio
async def test_current_resource_restriction_blocks_receipt_reconciliation(restore_db,tmp_path,monkeypatch):
    from dataclasses import replace
    f,runtime,executor,run,controller,target,rid,bucket,cp,body,calls=await setup(restore_db,tmp_path,monkeypatch,mode='lost_settlement')
    await run();operation=runtime.task_execution_state.operations[-1]
    access=ResourceAccessBoundary((ResourceRule(('restore',),paths=('other.txt',)),),'revoked-path',True)
    ctrl=controller(replace(executor.tool_runtime.context('read'),resource_access=access.as_dict()))
    read=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':operation.id},call_id='read')
    assert not read.result['ok'] and ctrl.state==runtime.task_execution_state
    with scoped(f) as c:assert not c.execute('SELECT purged FROM deleted_files WHERE id=%s',(rid,)).fetchone()[0]
    bucket.get_object_to_file.assert_called_once()


@pytest.mark.asyncio
async def test_original_file_delivery_goal_is_preserved_as_file_not_replaced_by_receipt(restore_db,tmp_path,monkeypatch):
    f,runtime,executor,run,controller,target,rid,bucket,cp,body,calls=await setup(restore_db,tmp_path,monkeypatch,artifact_kind='file')
    await run();ctrl=controller();operation=ctrl.state.operations[-1]
    imported=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':operation.id},call_id='import')
    assert imported.result['ok'],imported.result
    artifact=next(a for a in ctrl.state.artifacts if a.id==imported.result['imported_artifact_id'])
    assert artifact.kind=='file' and ctrl.state.requirements[0].artifact_kind=='file'
    done=await ctrl.control('complete_task_step',{'expected_revision':ctrl.state.revision,'step_id':'restore','artifact_ids':[artifact.id]},call_id='complete')
    assert done.result['ok'] and finish(ctrl.state).status=='completed',done.result
    bucket.get_object_to_file.assert_called_once()


@pytest.mark.asyncio
async def test_download_failure_is_proven_not_published_and_new_native_attempt_can_correct(restore_db,tmp_path,monkeypatch):
    f,runtime,executor,run,controller,target,rid,bucket,cp,body,calls=await setup(restore_db,tmp_path,monkeypatch)
    original=bucket.get_object_to_file.side_effect
    bucket.get_object_to_file.side_effect=OSError('backup read unavailable')
    first=await run();operation=runtime.task_execution_state.operations[-1]
    assert first.status=='error' and operation.status=='failed_before_dispatch' and not target.exists()
    replay=await run();assert replay.execution.replayed and replay.status=='error'
    bucket.get_object_to_file.assert_called_once()
    bucket.get_object_to_file.side_effect=original
    corrected=await run('corrected-native')
    assert corrected.status=='success',repr(corrected.exception)
    assert target.read_bytes()==body and runtime.task_execution_state.operations[-1].id==operation.id
    assert len(runtime.task_execution_state.operations[-1].previous_attempts)==1
    assert bucket.get_object_to_file.call_count==2


@pytest.mark.asyncio
async def test_existing_destination_never_downloads_or_reserves_another_write(restore_db,tmp_path,monkeypatch):
    f,runtime,executor,run,controller,target,rid,bucket,cp,body,calls=await setup(restore_db,tmp_path,monkeypatch)
    target.parent.mkdir(parents=True);target.write_bytes(b'new user file')
    result=await run()
    assert result.status!='success' and target.read_bytes()==b'new user file'
    assert not runtime.task_execution_state.operations
    bucket.get_object_to_file.assert_not_called()


@pytest.mark.asyncio
async def test_file_delivery_also_requires_current_read_grant_and_rechecks_bytes(restore_db,tmp_path,monkeypatch):
    from dataclasses import replace
    f,runtime,executor,run,controller,target,rid,bucket,cp,body,calls=await setup(restore_db,tmp_path,monkeypatch,artifact_kind='file')
    await run();operation=runtime.task_execution_state.operations[-1]
    access=ResourceAccessBoundary((ResourceRule(('restore',),directories=('.',)),),'restore-only',True)
    ctrl=controller(replace(executor.tool_runtime.context('read'),resource_access=access.as_dict()))
    denied=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':operation.id},call_id='denied')
    assert not denied.result['ok']
    ctrl=controller()
    imported=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':operation.id},call_id='import')
    assert imported.result['ok']
    target.write_bytes(b'other bytes');before=ctrl.state
    complete=await ctrl.control('complete_task_step',{'expected_revision':ctrl.state.revision,'step_id':'restore',
        'artifact_ids':[imported.result['imported_artifact_id']]},call_id='complete')
    assert not complete.result['ok'] and ctrl.state==before
    bucket.get_object_to_file.assert_called_once()


@pytest.mark.asyncio
async def test_file_settlement_requires_domain_transaction_and_rollback_preserves_evidence(restore_db,tmp_path,monkeypatch):
    f,runtime,executor,run,controller,target,rid,bucket,cp,body,calls=await setup(restore_db,tmp_path,monkeypatch,mode='lost_settlement')
    await run();operation=runtime.task_execution_state.operations[-1]
    with scoped(f) as c:
        nonce=c.execute('SELECT dispatch_token FROM agent_task_native_writes WHERE operation_id=%s',(operation.id,)).fetchone()[0]
        with pytest.raises(psycopg.Error,match='TASK_FILE_DOMAIN_SETTLEMENT_REQUIRED'):
            with c.transaction():c.execute('SELECT record_agent_task_native_write(%s,%s,\'returned\',%s)',(operation.id,nonce,Jsonb({'method':'restore_file','body':{'success':True}})))
        c.execute("SELECT set_config('app.actor_user_id',%s,true)",(str(uuid4()),))
        assert c.execute("SELECT count(*) FROM agent_task_native_writes WHERE domain='file'").fetchone()[0]==0
        with pytest.raises(psycopg.Error,match='TASK_FILE_RESTORES_NOT_DRAINED'):
            with c.transaction():c.execute((ROOT/'migrations/rollback/355_agent_task_file_restores_rollback.sql').read_text())
        assert c.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid='agent_task_native_writes'::regclass").fetchone()[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_save',[False,True])
async def test_reconciliation_and_original_model_pairs_share_atomic_checkpoint(restore_db,tmp_path,monkeypatch,fail_save):
    import copy
    import json
    from tests.test_task_execution_model_bridge import setup_case,execute as controls,call as control_call
    f,runtime,executor,run,controller,target,rid,bucket,cp,body,calls=await setup(restore_db,tmp_path,monkeypatch,mode='lost_settlement')
    await run();initial=runtime.task_execution_state;operation=initial.operations[-1];snapshots=[]
    async def checkpoint(point,payload):
        snapshots.append(copy.deepcopy(payload))
        if fail_save:raise RuntimeError('checkpoint unconfirmed')
        return {'outcome':'saved'}
    bridged,prepared,handler,sink=setup_case(checkpoint,initial=initial)
    bridged.task_operation_store=runtime.task_operation_store
    bridged.task_result_context_provider=AsyncMock(return_value=executor.tool_runtime.context('read'))
    actions=[control_call('read_task_write_result',{'expected_revision':initial.revision,'operation_id':operation.id})]
    if fail_save:
        with pytest.raises(RuntimeError,match='checkpoint unconfirmed'):await controls(bridged,prepared,handler,sink,actions,[])
        assert bridged.task_execution_state==initial and len(prepared.messages)==1
        sink.on_block.assert_not_awaited();sink.on_block_update.assert_not_awaited()
    else:
        await controls(bridged,prepared,handler,sink,actions,[])
        assert bridged.task_execution_state.operations[-1].status=='completed'
        assert len(snapshots)==1 and snapshots[0]['_task_execution']['operations'][-1]['status']=='completed'
        saved=snapshots[0]['messages'];assert saved[1]['role']=='assistant' and saved[2]['role']=='tool'
        assert json.loads(saved[2]['content'])['ok']
    with scoped(f) as c:assert c.execute('SELECT purged FROM deleted_files WHERE id=%s',(rid,)).fetchone()[0]
    bucket.get_object_to_file.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['cancelled','lease_expired'])
async def test_dispatch_rechecks_actor_after_download_before_publishing(restore_db,tmp_path,monkeypatch,mode):
    from services.conversation_state import ConversationStopRequested
    f,runtime,executor,run,controller,target,rid,bucket,cp,body,calls=await setup(restore_db,tmp_path,monkeypatch)
    original=bucket.get_object_to_file.side_effect
    def download(*args,**kwargs):
        original(*args,**kwargs)
        with psycopg.connect(restore_db) as c:
            c.execute('UPDATE tasks SET '+("status='cancelled'" if mode=='cancelled' else "lease_expires_at=now()-interval '1 second'")+' WHERE id=%s',(f['task'],))
    bucket.get_object_to_file.side_effect=download
    with pytest.raises(ConversationStopRequested):await run()
    assert not target.exists()
    with scoped(f) as c:
        assert c.execute('SELECT phase FROM agent_task_native_writes WHERE owner_task_id=%s',(f['task'],)).fetchone()[0]=='prepared'
        assert not c.execute('SELECT purged FROM deleted_files WHERE id=%s',(rid,)).fetchone()[0]
    bucket.get_object_to_file.assert_called_once()


@pytest.fixture(scope='module', autouse=True)
def current_domain_revision_schema(restore_db, children_db):
    """Exercise current dispatch guards with real revision migrations."""
    assert restore_db == children_db
    install_revision_gates(restore_db)
    yield
    drain_revision_gates(restore_db)
