"""Lost acceptance confirmation is reconciled from native committed facts."""
from pathlib import Path

import psycopg
import pytest

from tests.test_task_execution_children_postgres import (
    children_db,operation_db,task_db,composition_db,ecom_db,isolated_db,child_case,accept_child,connect,typed_store)

ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def reconciliation_db(children_db):
    with psycopg.connect(children_db) as c:
        c.execute('SET LOCAL ROLE everydayai_owner')
        c.execute((ROOT/'migrations/347_agent_task_image_reconciliation.sql').read_text())
    yield children_db
    with psycopg.connect(children_db) as c:
        c.execute('SET LOCAL ROLE everydayai_owner')
        c.execute((ROOT/'migrations/rollback/347_agent_task_image_reconciliation_rollback.sql').read_text())


@pytest.mark.asyncio
async def test_committed_child_lost_return_reconciles_without_rewriting_native_unknown(reconciliation_db,monkeypatch):
    f,first,snapshot=await child_case(reconciliation_db,monkeypatch)
    child=accept_child(f,snapshot)
    store=typed_store(f)
    restored=await store.reconcile_image(first.state,operation_id=first.operation.id)
    assert restored.outcome=='reconciled' and restored.receipt['task_id']==child['task_id']
    assert restored.operation.status=='accepted'
    assert restored.operation.receipt_ref=='task:'+child['task_id']
    assert restored.state.revision==first.state.revision+1
    again=await store.reconcile_image(restored.state,operation_id=first.operation.id)
    assert again.state==restored.state
    with connect(f) as c:
        assert c.execute('SELECT status FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]=='running'
        assert c.execute('SELECT count(*) FROM agent_task_events WHERE root_task_id=%s',(f['task'],)).fetchone()[0]==1
        assert c.execute("SELECT count(*) FROM tasks WHERE type='image' AND user_id=%s",(f['user'],)).fetchone()[0]==1
        cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert cp['_task_execution']==restored.state.model_dump(mode='json')


@pytest.mark.asyncio
async def test_absent_native_child_is_uncertain_never_a_retry_permission(reconciliation_db,monkeypatch):
    f,first,_=await child_case(reconciliation_db,monkeypatch)
    restored=await typed_store(f).reconcile_image(first.state,operation_id=first.operation.id)
    assert restored.outcome=='reconcile' and restored.state==first.state and restored.receipt is None


@pytest.mark.asyncio
async def test_stale_reconciliation_cannot_overwrite_checkpoint(reconciliation_db,monkeypatch):
    from services.task_execution.contracts import TaskError
    f,first,snapshot=await child_case(reconciliation_db,monkeypatch)
    accept_child(f,snapshot)
    await typed_store(f).reconcile_image(first.state,operation_id=first.operation.id)
    with pytest.raises(TaskError,match='REVISION_STALE'):
        await typed_store(f).reconcile_image(first.state,operation_id=first.operation.id)


@pytest.mark.asyncio
async def test_uncertain_original_return_reconciles_through_native_lifecycle_without_dispatch(reconciliation_db,monkeypatch):
    from types import SimpleNamespace
    from services.task_execution.image_operations import ImageOperations
    from services.task_execution.image_binding import bind_image_call,image_slot_key
    from services.tools import ToolCall,ToolContext
    from services.tools.policy import ToolPolicy
    from tests.test_task_execution_operation_store import native_result
    f,first,snapshot=await child_case(reconciliation_db,monkeypatch,effect_key=image_slot_key('user.images',1))
    child=accept_child(f,snapshot)
    store=typed_store(f)
    args={k:snapshot[k] for k in ('mode','prompt','aspect_ratio')}
    original=ToolCall('native-1','generate_image',args)
    uncertain=await store.complete_invocation(first.state,operation_id=first.operation.id,
        result=native_result(first.state,original,error=True),status='uncertain')
    runtime=SimpleNamespace(task_execution_state=uncertain.state,task_operation_store=store,
        committed_task_execution_revision=uncertain.state.revision,replay_save_sequence=0)
    call=ToolCall('replacement-model-id','generate_image',args)
    binding=bind_image_call(uncertain.state,call,store.registry)
    context=ToolContext(actor_user_id=f['user'],workspace_owner_id=f['user'],org_id=None,
        context_scope='user',personal_context_allowed=True,agent_domain='general',permission_mode='auto',
        execution_mode='interactive',task_id=f['task'],conversation_id=f['conv'],
        feature_flags={'chat_image_async_enabled':True})
    handler=SimpleNamespace(_actor_enabled=True,_actor_invocation_store=object(),
        _actor_turn_id=f['turn'],_actor_execution_token=f['token'])
    coordinator=ImageOperations(runtime,{call.call_id:binding})
    result=await coordinator.lifecycle(handler,context,call.call_id).replay(call,context,
        ToolPolicy(store.registry).decide(call.name,context,call.arguments))
    assert result.metadata['task_id']==child['task_id'] and result.metadata['reconciled'] is True
    assert result.execution.replayed and runtime.task_execution_state.operations[0].status=='accepted'
    with connect(f) as c:
        assert c.execute('SELECT status FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]=='uncertain'


@pytest.mark.asyncio
async def test_incomplete_handler_receipt_is_reconciled_before_another_model_decision(reconciliation_db,monkeypatch):
    from types import SimpleNamespace
    from services.task_execution.image_operations import ImageOperations
    from services.task_execution.image_binding import bind_image_call,image_slot_key
    from services.tools import ToolCall
    from tests.test_task_execution_operation_store import native_result
    f,first,snapshot=await child_case(reconciliation_db,monkeypatch,effect_key=image_slot_key('user.images',1))
    child=accept_child(f,snapshot)
    store=typed_store(f)
    call=ToolCall('native-1','generate_image',{k:snapshot[k] for k in ('mode','prompt','aspect_ratio')})
    runtime=SimpleNamespace(task_execution_state=first.state,task_operation_store=store,
        committed_task_execution_revision=first.state.revision,replay_save_sequence=0)
    coordinator=ImageOperations(runtime,{call.call_id:bind_image_call(first.state,call,store.registry)})
    lifecycle=coordinator.lifecycle(SimpleNamespace(),None,call.call_id)
    lifecycle.operation_id=first.operation.id
    # The result omits task_id/message_id. Native acceptance remains the sole
    # authority; neither a second model call nor a second handler is required.
    await lifecycle.complete(native_result(first.state,call))
    assert runtime.task_execution_state.operations[0].receipt_ref=='task:'+child['task_id']
    assert runtime.task_execution_state.operations[0].status=='accepted'
    with connect(f) as c:
        assert c.execute('SELECT status FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]=='uncertain'


@pytest.mark.asyncio
@pytest.mark.parametrize('damage',['origin','message','actor','token','lease'])
async def test_reconciliation_rechecks_native_scope_and_fence(reconciliation_db,monkeypatch,damage):
    from services.task_execution.operation_store import TaskOperationPersistenceError
    from services.conversation_state import ConversationStopRequested
    from uuid import uuid4
    f,first,snapshot=await child_case(reconciliation_db,monkeypatch)
    child=accept_child(f,snapshot)
    with psycopg.connect(f['dsn']) as c:
        if damage=='origin':c.execute("UPDATE tasks SET request_params=jsonb_set(request_params,'{_media_request_v1,origin,tool_call_id}','\"forged\"') WHERE id=%s",(child['task_id'],))
        if damage=='message':c.execute("UPDATE messages SET generation_params='{}' WHERE id=%s",(child['message_id'],))
        if damage=='actor':c.execute("UPDATE tasks SET delivery_context='{}' WHERE id=%s",(f['task'],))
        if damage=='lease':c.execute("UPDATE tasks SET lease_expires_at=now()-interval '1 second' WHERE id=%s",(f['task'],))
    if damage=='token':f={**f,'token':str(uuid4())}
    with pytest.raises((TaskOperationPersistenceError,ConversationStopRequested)):
        await typed_store(f).reconcile_image(first.state,operation_id=first.operation.id)
    with psycopg.connect(f['dsn']) as c:
        state=c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',(f['task'],)).fetchone()[0]
        assert state==first.state.model_dump(mode='json')
