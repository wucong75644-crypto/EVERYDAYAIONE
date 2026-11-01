"""Actual policy and native adapters in direct mode; external IO is substituted."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4
import pytest
from services.task_execution.bootstrap import TaskBootstrap
from services.task_execution.model_directed import record_calls
from services.task_execution.direct_loop import advance_results,end_candidate
from services.task_execution.contracts import TaskError
from tests.test_task_execution_reach_operations import execute


def directify(runtime,call,store):
    old=runtime.task_execution_state
    b=TaskBootstrap(task_id=old.root_task_id,conversation_id=old.conversation_id,actor_user_id=old.actor_user_id,
        input_message_id=old.source_message_ids[0],original_goal=old.original_goal,org_id=old.org_id,
        execution_identity=old.execution_identity,model_directed=True)
    s=record_calls(None,b,[call],store.registry)
    runtime.task_execution_state=s;runtime.task_bootstrap=b;runtime.task_direct_seen=();runtime.task_direct_plan=None
    store.state=s
    return s


@pytest.mark.asyncio
async def test_scheduled_form_waits_then_original_receipt_advances_without_import_calls():
    from tests.test_task_execution_scheduled import case,proof
    runtime,h,ctx,call,service,io,store=case()
    directify(runtime,call,store)
    result=await execute(runtime,h,ctx,call,service)
    assert result.kind=='form' and runtime.task_execution_state.operations[0].status=='accepted'
    waiting=await advance_results(runtime,SimpleNamespace(execution_context=ctx,messages=[]))
    assert waiting.results==() and end_candidate(waiting).status=='waiting_approval'
    op=waiting.operations[0]
    store.proof=proof(waiting,store,op)
    completed=await advance_results(runtime,SimpleNamespace(execution_context=ctx,messages=[]),reconcile_pending=True)
    assert len(completed.results)==1 and completed.operations[0].status=='completed'
    assert end_candidate(completed).status=='completed'
    assert completed.artifacts[0].reference.startswith('scheduled-receipt:')
    io.assert_awaited_once()


@pytest.mark.asyncio
async def test_actual_reach_receipt_is_registered_without_an_extra_model_round():
    from tests.test_task_execution_reach_operations import case
    runtime,h,ctx,call,service,io,store=case()
    directify(runtime,call,store)
    await execute(runtime,h,ctx,call,service)
    done=await advance_results(runtime,SimpleNamespace(execution_context=ctx,messages=[]))
    assert done.operations[0].status=='completed' and len(done.results)==1
    assert done.artifacts[0].reference.startswith('task-operation:')
    assert 'native.reach.receipt' in done.artifacts[0].validations
    assert end_candidate(done).status=='completed'
    io.assert_awaited_once()


@pytest.mark.asyncio
async def test_original_approval_is_not_bypassed_by_concrete_call_records():
    from tests.test_task_execution_reach_operations import case
    runtime,h,ctx,call,service,io,store=case()
    directify(runtime,call,store)
    result=await execute(runtime,h,ctx,call,service,approved=False)
    assert result.status!='success' and runtime.task_execution_state.operations==()
    io.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_code_result_imports_real_file_and_does_not_repeat_worker(tmp_path,monkeypatch):
    from tests.test_task_execution_sandbox import case
    runtime,h,ctx,call,service,io,store,kernel,path=case(tmp_path,monkeypatch)
    directify(runtime,call,store)
    await execute(runtime,h,ctx,call,service)
    done=await advance_results(runtime,SimpleNamespace(execution_context=ctx,messages=[]))
    assert len(done.results)==1 and any(a.kind=='file' for a in done.artifacts)
    assert path.is_file() and kernel.calls==1
    runtime.task_execution_state=done
    assert await advance_results(runtime,SimpleNamespace(execution_context=ctx,messages=[])) is done
    assert kernel.calls==1


@pytest.mark.asyncio
async def test_replaced_code_file_cannot_be_registered_as_valid_delivery(tmp_path,monkeypatch):
    from tests.test_task_execution_sandbox import case
    runtime,h,ctx,call,service,io,store,kernel,path=case(tmp_path,monkeypatch)
    directify(runtime,call,store)
    await execute(runtime,h,ctx,call,service)
    path.write_bytes(b'changed-after-execution')
    prepared=SimpleNamespace(execution_context=ctx,messages=[])
    candidate=await advance_results(runtime,prepared)
    assert not any(a.kind=='file' for a in candidate.artifacts) and kernel.calls==1
    assert runtime.task_direct_result_issues and '未登记为有效文件成果' in prepared.messages[-1]['content']


@pytest.mark.asyncio
async def test_multiple_native_results_are_read_at_one_committed_revision():
    import copy
    from services.agent.agent_result import AgentResult
    from services.tools import ToolCall
    from services.tool_invocation_store import hash_tool_arguments
    from tests.test_task_execution_reach_operations import case
    runtime,h,ctx,call,service,io,store=case()
    directify(runtime,call,store)
    await execute(runtime,h,ctx,call,service)
    proofs={runtime.task_execution_state.operations[0].id:copy.deepcopy(store.proof)}
    second=ToolCall('second-write',call.name,{**dict(call.arguments),'content':'第二条已经授权的评论'})
    state=record_calls(runtime.task_execution_state,runtime.task_bootstrap,[second],store.registry)
    runtime.task_execution_state=state;store.state=state
    receipt={**store.proof['receipt'],'remote_id':'remote-2'}
    store.proof={**store.proof,'native_id':str(uuid4()),'tool_call_id':second.call_id,
        'args_hash':hash_tool_arguments(dict(second.arguments)),'receipt':receipt}
    io.return_value=AgentResult('第二条真实平台回执',metadata={'platform':'twitter','receipt':receipt})
    await execute(runtime,h,replace(ctx,call_id=second.call_id),second,service)
    proofs[runtime.task_execution_state.operations[-1].id]=copy.deepcopy(store.proof)
    committed=runtime.task_execution_state;revisions=[]
    async def read(state,operation_id):
        revisions.append(state.revision)
        assert state.revision==committed.revision
        return proofs[operation_id]
    store.reach_receipt=read
    result=await advance_results(runtime,SimpleNamespace(execution_context=ctx,messages=[]))
    assert len(result.results)==2 and len(result.artifacts)==2
    assert revisions==[committed.revision,committed.revision]
    assert runtime.task_execution_state is committed and io.await_count==2
