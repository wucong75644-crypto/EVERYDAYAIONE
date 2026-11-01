"""Original ToolRuntime/confirmations plus PostgreSQL; no external OSS calls."""
from pathlib import Path
from unittest.mock import AsyncMock
import psycopg
from psycopg.types.json import Jsonb
import pytest

from services.task_execution.reducer import finish
from services.tools.resource_access import ResourceAccessBoundary,ResourceRule
from tests.test_task_execution_file_deletions_postgres import (deletion_db,restore_db,write_db,native_result_db,
    operation_db,task_db,composition_db,ecom_db,isolated_db,setup,scoped,current_domain_revision_schema)
from tests.test_task_execution_children_postgres import children_db
ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def resume_db(deletion_db):return deletion_db


@pytest.mark.asyncio
async def test_resume_original_remaining_files_preserves_new_same_name_and_original_native_result(resume_db,tmp_path,monkeypatch):
    f,runtime,executor,run,controller,paths,bucket,objects,originals=await setup(resume_db,tmp_path,monkeypatch,mode='lost_settlement',count=2)
    await run();assert not paths[0].exists() and paths[1].read_bytes()==originals[1]
    with scoped(f) as c:
        native_before=c.execute("SELECT result FROM tool_invocations WHERE task_id=%s AND tool_call_id='delete-original'",(f['task'],)).fetchone()[0]
        cp_before=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
    paths[0].write_bytes(b'new user file must survive')
    resumed=await run('resume-original-remaining')
    assert resumed.status=='success',repr(resumed.exception)
    assert paths[0].read_bytes()==b'new user file must survive' and not paths[1].exists()
    replayed=await run('resume-original-remaining');assert replayed.execution.replayed,repr(replayed.exception)
    assert bucket.put_object_from_file.call_count==2 and list(objects.values())==originals
    ctrl=controller();op=ctrl.state.operations[-1]
    read=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='read')
    assert read.result['ok'] and read.result['operation_status']=='completed',read.result
    imported=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='import')
    assert imported.result['ok'],imported.result
    done=await ctrl.control('complete_task_step',{'expected_revision':ctrl.state.revision,'step_id':'delete','artifact_ids':[imported.result['imported_artifact_id']]},call_id='complete')
    assert done.result['ok'] and finish(ctrl.state).status=='completed',done.result
    with scoped(f) as c:
        assert c.execute("SELECT result FROM tool_invocations WHERE task_id=%s AND tool_call_id='delete-original'",(f['task'],)).fetchone()[0]==native_before
        current=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert current['messages']==cp_before['messages'] and current['opaque_context']==cp_before['opaque_context']
        assert c.execute('SELECT tool_call_id,status FROM tool_invocations WHERE task_id=%s ORDER BY started_at',(f['task'],)).fetchall()==[('delete-original','uncertain'),('resume-original-remaining','succeeded')]


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['changed','replaced','revoked','approval_denied','backup_changed','expired','paused'])
async def test_resume_rechecks_original_version_backup_current_authority_and_confirmation(resume_db,tmp_path,monkeypatch,mode):
    f,runtime,executor,run,controller,paths,bucket,objects,originals=await setup(resume_db,tmp_path,monkeypatch,mode='lost_settlement',count=2)
    await run()
    if mode=='changed':paths[1].write_bytes(b'changed user file')
    if mode=='replaced':
        paths[1].unlink();paths[1].write_bytes(originals[1])
    if mode=='revoked':executor.resource_access_boundary=ResourceAccessBoundary((ResourceRule(('read','list'),directories=('.',)),),'read-only',True)
    if mode=='approval_denied':executor.tool_confirmer=AsyncMock(return_value=False)
    if mode=='backup_changed':
        from types import SimpleNamespace
        bucket.get_object_meta.return_value=SimpleNamespace(etag='changed')
        bucket.get_object_meta.side_effect=None
    if mode in {'expired','paused'}:
        with psycopg.connect(resume_db) as c:
            if mode=='expired':c.execute("UPDATE tasks SET lease_expires_at=now()-interval '1 second' WHERE id=%s",(f['task'],))
            else:
                current=runtime.task_execution_state.model_copy(update={'status':'paused','revision':runtime.task_execution_state.revision+1})
                c.execute('UPDATE agent_task_states SET revision=%s,state=%s WHERE root_task_id=%s',(current.revision,Jsonb(current.model_dump(mode='json')),f['task']))
                c.execute("UPDATE conversation_turn_checkpoints SET state=jsonb_set(state,'{_task_execution}',%s) WHERE task_id=%s",(Jsonb(current.model_dump(mode='json')),f['task']))
    before=paths[1].read_bytes()
    if mode=='expired':
        from services.conversation_state import ConversationStopRequested
        with pytest.raises(ConversationStopRequested):await run('resume-denied')
    else:
        result=await run('resume-denied')
        assert result.status!='success',repr(result.exception)
    assert paths[1].read_bytes()==before
    bucket.put_object_from_file.assert_called();assert bucket.put_object_from_file.call_count==2
    with scoped(f) as c:assert c.execute("SELECT count(*) FROM agent_task_file_delete_resumptions WHERE owner_task_id=%s",(f['task'],)).fetchone()[0]==0


@pytest.mark.asyncio
async def test_resume_rollback_refuses_foreign_tenant_receipt_without_dropping_evidence(resume_db,tmp_path,monkeypatch):
    f,runtime,executor,run,controller,paths,bucket,objects,originals=await setup(resume_db,tmp_path,monkeypatch,mode='lost_settlement',count=2)
    await run();result=await run('resume-to-audit');assert result.status=='success',repr(result.exception)
    with psycopg.connect(resume_db) as c:
        with pytest.raises(psycopg.Error,match='NOT_DRAINED'):
            with c.transaction():c.execute((ROOT/'migrations/rollback/359_agent_task_file_delete_resumptions_rollback.sql').read_text())
        assert c.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid='agent_task_file_delete_resumptions'::regclass").fetchone()[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['changed_during_confirmation','revoked_after_begin','expired_after_begin','expanded_arguments'])
async def test_resume_last_moment_checks_do_not_authorize_a_changed_or_expanded_delete(resume_db,tmp_path,monkeypatch,mode):
    from services.conversation_state import ConversationStopRequested
    f,runtime,executor,run,controller,paths,bucket,objects,originals=await setup(resume_db,tmp_path,monkeypatch,mode='lost_settlement',count=2)
    await run()
    extra=paths[1].with_name('额外文件.txt');extra.write_bytes(b'unrequested file')
    if mode=='expanded_arguments':paths.append(extra)
    if mode=='changed_during_confirmation':
        async def confirm(*_):paths[1].write_bytes(b'changed during confirmation');return True
        executor.tool_confirmer=AsyncMock(side_effect=confirm)
    if mode in {'revoked_after_begin','expired_after_begin'}:
        original=runtime.task_operation_store.db.rpc
        def rpc(name,params):
            pending=original(name,params)
            if name!='begin_agent_task_file_delete_resume':return pending
            from types import SimpleNamespace
            async def execute():
                response=await pending.execute()
                with psycopg.connect(resume_db) as c:
                    if mode=='revoked_after_begin':c.execute("UPDATE org_members SET status='inactive' WHERE org_id=%s AND user_id=%s",(f['org'],f['user']))
                    else:c.execute("UPDATE tasks SET lease_expires_at=now()-interval '1 second' WHERE id=%s",(f['task'],))
                return response
            return SimpleNamespace(execute=execute)
        runtime.task_operation_store.db.rpc=rpc
    try:result=await run('resume-last-moment')
    except ConversationStopRequested:pass
    else:assert result.status!='success',repr(result.exception)
    assert paths[1].read_bytes()==(b'changed during confirmation' if mode=='changed_during_confirmation' else originals[1])
    assert extra.read_bytes()==b'unrequested file'
    assert bucket.put_object_from_file.call_count==2


@pytest.mark.asyncio
async def test_pending_batch_recovery_replays_a_recorded_resume_but_never_starts_an_unrecorded_one(resume_db,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from services.task_execution.native_results import lifecycle_for
    f,runtime,executor,run,controller,paths,bucket,objects,originals=await setup(resume_db,tmp_path,monkeypatch,mode='lost_settlement',count=2)
    await run()
    handler=SimpleNamespace(_actor_runtime=runtime,_actor_enabled=True,_actor_invocation_store=object(),
        _actor_execution_token=f['token'],_actor_turn_id=f['turn'])
    async def recover(call_id):
        executor._task_execution_state=runtime.task_execution_state
        lifecycle=lifecycle_for(handler,executor.tool_runtime.context(call_id),executor.tool_runtime.registry,'file_delete',recovery_only=True)
        return await executor.tool_runtime.execute('file_delete',{'files':[str(p) for p in paths]},call_id=call_id,lifecycle=lifecycle)
    rejected=await recover('unrecorded-resume');assert rejected.status!='success'
    assert paths[1].read_bytes()==originals[1]
    resumed=await run('recorded-resume');assert resumed.status=='success',repr(resumed.exception)
    replayed=await recover('recorded-resume');assert replayed.execution.replayed,repr(replayed.exception)
    assert not paths[1].exists() and bucket.put_object_from_file.call_count==2


@pytest.mark.asyncio
async def test_resume_confirmation_displays_only_the_actual_remaining_file(resume_db,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from services.handlers.chat_tool_mixin import ChatToolMixin
    from services.websocket_manager import ws_manager
    f,runtime,executor,run,controller,paths,bucket,objects,originals=await setup(resume_db,tmp_path,monkeypatch,mode='lost_settlement',count=2)
    await run();paths[0].write_bytes(b'new file remains')
    sent=AsyncMock();monkeypatch.setattr(ws_manager,'send_to_task_or_user',sent)
    monkeypatch.setattr(ChatToolMixin,'_wait_for_tool_confirmation',AsyncMock(return_value=True))
    harness=SimpleNamespace(_actor_enabled=False)
    async def confirm(call,context,decision):return await ChatToolMixin._confirm_tool_call(harness,call,context,decision,'output')
    executor.tool_confirmer=confirm
    result=await run('resume-with-dialog');assert result.status=='success',repr(result.exception)
    payload=sent.call_args.args[-1]
    import json
    rendered=json.dumps(payload,ensure_ascii=False)
    assert '继续删除原批次剩余 1 个文件' in rendered and paths[1].name in rendered
    assert str(paths[0]) not in rendered and paths[0].read_bytes()==b'new file remains'
