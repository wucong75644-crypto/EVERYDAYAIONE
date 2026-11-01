"""Lost ERP ACK and batch recovery using original journals, no network calls."""
import copy,json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import psycopg
from psycopg.types.json import Jsonb
import pytest

pytestmark = pytest.mark.usefixtures('erp_write_adapter_enabled')
from services.task_execution.reducer import finish
from services.task_execution.controller import TaskController
from services.task_execution.erp_recovery import recovery_facts
from services.tools import ToolCall
from tests.test_task_execution_erp_session_rotations_postgres import (rotation_db,postcondition_db,write_db,native_result_db,
    operation_db,task_db,composition_db,ecom_db,isolated_db,scoped,prepared,ROOT)
from tests.test_task_execution_native_writes_postgres import setup,save
from tests.test_task_execution_reach_operations import execute


@pytest.fixture(scope='module')
def recovery_db(rotation_db):
    with psycopg.connect(rotation_db) as c:c.execute((ROOT/'migrations/369_agent_task_erp_recovery.sql').read_text())
    yield rotation_db
    with psycopg.connect(rotation_db) as c:
        c.execute("DELETE FROM conversation_turn_checkpoints WHERE state ? '_task_execution'")
        c.execute('DELETE FROM agent_task_states');c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        c.execute((ROOT/'migrations/rollback/369_agent_task_erp_recovery_rollback.sql').read_text())


@pytest.mark.asyncio
async def test_lost_ack_uses_closed_original_invocation_and_fresh_read_without_resending(recovery_db):
    f,runtime,handler,ctx,call,store,http,io,cp,controller,write,read=await prepared(recovery_db,mode='timeout')
    await write();op=runtime.task_execution_state.operations[-1];assert op.status=='uncertain'
    with scoped(f) as c:
        original=c.execute('SELECT phase,response FROM agent_task_native_writes WHERE operation_id=%s',(op.id,)).fetchone()
        invocation=c.execute('SELECT result FROM tool_invocations WHERE task_id=%s AND tool_call_id=%s',(f['task'],call.call_id)).fetchone()[0]
    ctrl=controller();before=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='before')
    assert before.result['operation_status']=='uncertain' and before.result['follow_up']['state']=='query_if_needed'
    await read()
    ctrl=controller();found=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='verify')
    assert found.result['ok'] and found.result['operation_status']=='uncertain',found.result
    assert found.result['response'] is None and found.result['native_phase']=='dispatched'
    assert found.result['recovery']['target_state_observed'] and not found.result['recovery']['effect_terminal']
    assert found.result['recovery']['completion_basis'] is None and not found.result['recovery']['can_resend']
    assert found.result['follow_up']['state']=='await_provider_terminal_evidence'
    imported=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='import')
    assert not imported.result['ok'] and not ctrl.state.artifacts
    with scoped(f) as c:
        assert c.execute('SELECT phase,response FROM agent_task_native_writes WHERE operation_id=%s',(op.id,)).fetchone()==original==('dispatched',None)
        assert c.execute('SELECT result FROM tool_invocations WHERE task_id=%s AND tool_call_id=%s',(f['task'],call.call_id)).fetchone()[0]==invocation
        saved=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert saved['messages']==cp['messages'] and saved['opaque_context']==cp['opaque_context']
    runtime.task_execution_state=ctrl.state
    from services.tools.result import UncertainToolInvocationError
    with pytest.raises(UncertainToolInvocationError):await write()
    # A queued old request may still execute. Matching state is not permission
    # for the next conflicting write; native effect/argument binding stays armed.
    from services.task_execution.native_writes import NativeWriteLifecycle
    from services.task_execution.contracts import TaskError
    changed=ToolCall('new-title','erp_execute',{'category':'product','action':'product_add_update_simple',
        'params':{'outer_id':'P-001','title':'new value'}})
    from services.task_execution.native_results import lifecycle_for
    from services.tools.catalog import build_capability_catalog
    from services.tools.policy import ToolPolicy
    registry=build_capability_catalog();lifecycle=lifecycle_for(handler,ctx,registry,'erp_execute')
    decision=ToolPolicy(registry).decide('erp_execute',ctx,changed.arguments)
    with pytest.raises((TaskError,UncertainToolInvocationError)):
        await lifecycle.replay(changed,ctx,decision)
    http.post.assert_awaited_once();io.assert_awaited_once()



@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['different_title','other_source','before_write_closed','read_unclosed','cached','wrong_actor','write_not_closed'])
async def test_unknown_write_not_completed_by_inexact_or_untrusted_later_read(recovery_db,fault):
    f,runtime,handler,ctx,call,store,http,io,cp,controller,write,read=await prepared(recovery_db,mode='timeout')
    await write();op=runtime.task_execution_state.operations[-1]
    await read(data={'success':True,'outerId':'P-001','title':'wrong'} if fault=='different_title' else None,
        source_token='different-session' if fault=='other_source' else 'local')
    if fault not in {'different_title','other_source'}:
        with psycopg.connect(recovery_db) as c:
            if fault=='before_write_closed':
                c.execute("UPDATE tool_invocations SET started_at=(SELECT completed_at-interval '1 second' FROM tool_invocations WHERE task_id=%s AND tool_call_id=%s) WHERE task_id=%s AND tool_call_id='read-product'",(f['task'],call.call_id,f['task']))
            if fault=='write_not_closed':c.execute('UPDATE tool_invocations SET completed_at=NULL WHERE task_id=%s AND tool_call_id=%s',(f['task'],call.call_id))
            if fault=='read_unclosed':c.execute("UPDATE tool_invocations SET completed_at=NULL WHERE task_id=%s AND tool_call_id='read-product'",(f['task'],))
            if fault in {'cached','wrong_actor'}:
                row=c.execute("SELECT id,result FROM tool_invocations WHERE task_id=%s AND tool_call_id='read-product'",(f['task'],)).fetchone()
                result=row[1]
                if fault=='cached':result['tool_result']['execution']['cached']=True
                else:result['tool_result']['audit']['actor_user_id']='00000000-0000-4000-8000-000000000000'
                c.execute('UPDATE tool_invocations SET result=%s WHERE id=%s',(Jsonb(result),row[0]))
    ctrl=controller();found=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='verify')
    assert found.result['ok'] and found.result['operation_status']=='uncertain',found.result
    assert not ctrl.state.artifacts and found.result['recovery']['completion_basis'] is None
    http.post.assert_awaited_once();io.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_native_request_and_partial_response_survive_context_recovery_without_automatic_resume(recovery_db):
    params={'type':1,'item_info_list':[{'outerId':'A'},{'outerId':'B'}],'tag_ids':[7]}
    f,runtime,handler,ctx,call,store,http,io,service,cp,bindings,rpc=await setup(recovery_db,write_action='tag_batch_update',parameters=params)
    body={'success':True,'data':{'jobId':'job-original','items':[{'outerId':'A','success':True},{'outerId':'B','success':False}]}}
    http.post.return_value=SimpleNamespace(raise_for_status=lambda:None,json=lambda:body)
    await execute(runtime,handler,ctx,call,service)
    op=runtime.task_execution_state.operations[-1]
    proof=await store.native_write_receipt(runtime.task_execution_state,op.id)
    value=recovery_facts(proof,'accepted')
    assert value['request_evidence']['parameters']['itemInfoList']==params['item_info_list']
    assert [i['response_status'] for i in value['item_observations']]==['reported_success','reported_failure']
    assert value['provider_handles'][0]['value']=='job-original' and value['completion_basis'] is None
    with scoped(f) as c:
        c.execute("UPDATE conversation_turn_checkpoints SET state=jsonb_set(state,'{messages}',%s) WHERE task_id=%s",
            (Jsonb([{'role':'system','content':'压缩后摘要，原请求仍从原领域账本核对'}]),f['task']))
    async def commit(candidate,expected):
        with scoped(f) as c:
            old=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
            return save(c,f,{**old,'_task_execution':candidate.model_dump(mode='json')},expected=expected)
    ctrl=TaskController(runtime.task_execution_state,owner_task_id=f['task'],result_context=ctx,operation_store=store,commit=commit)
    inspected=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='inspect')
    assert inspected.result['recovery']==value and inspected.result['operation_status']=='accepted'
    rejected=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='no-false-complete')
    assert not rejected.result['ok']
    await execute(runtime,handler,ctx,call,service)
    http.post.assert_awaited_once();io.assert_awaited_once()
