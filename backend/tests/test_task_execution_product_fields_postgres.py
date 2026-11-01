"""Exact product scalar postconditions through original runtime and native PG."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import psycopg
from psycopg.types.json import Jsonb
import pytest

pytestmark = pytest.mark.usefixtures('erp_write_adapter_enabled')
from services.task_execution.reducer import finish
from services.task_execution.erp_postconditions import FIELDS_KEY
from tests.test_task_execution_erp_recovery_postgres import (recovery_db,rotation_db,postcondition_db,write_db,native_result_db,
    operation_db,task_db,composition_db,ecom_db,isolated_db,scoped,prepared,ROOT)
from tests.test_task_execution_erp_session_rotations_postgres import refresh

PARAMS={'outer_id':'P-001','title':'原始文具8入','barcode':'00123456789','unit':'本','remark':'','short_title':'原文具'}
DETAIL={'success':True,'outerId':'P-001','title':PARAMS['title'],'barcode':PARAMS['barcode'],'unit':'本','remark':'','shortTitle':'原文具'}


@pytest.fixture(scope='module')
def product_fields_db(recovery_db):
    with psycopg.connect(recovery_db) as c:c.execute((ROOT/'migrations/370_agent_task_product_fields.sql').read_text())
    yield recovery_db
    with psycopg.connect(recovery_db) as c:
        c.execute('DELETE FROM agent_task_native_writes')
        c.execute((ROOT/'migrations/rollback/370_agent_task_product_fields_rollback.sql').read_text())


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ok','format_loss'])
async def test_all_scalar_fields_require_exact_fresh_read_and_preserve_original_context(product_fields_db,mode):
    f,runtime,handler,ctx,call,store,http,io,cp,controller,write,read=await prepared(product_fields_db,mode=mode,parameters=PARAMS)
    await write();op=runtime.task_execution_state.operations[-1]
    ctrl=controller();waiting=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='inspect')
    assert waiting.result['operation_status']=='accepted' and waiting.result['recovery']['request_evidence']['parameters']['barcode']=='00123456789'
    runtime.task_execution_state=ctrl.state
    await read(data=DETAIL)
    ctrl=controller();verified=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='verify')
    assert verified.result['ok'] and verified.result['operation_status']=='completed',verified.result
    imported=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='import')
    assert imported.result['ok'],imported.result
    artifact_id=imported.result['imported_artifact_id']
    completed=await ctrl.control('complete_task_step',{'expected_revision':ctrl.state.revision,'step_id':op.step_id,
        'artifact_ids':[artifact_id]},call_id='complete')
    assert completed.result['ok'] and finish(ctrl.state).status=='completed'
    with scoped(f) as c:
        cp_now=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert cp_now['messages']==cp['messages'] and cp_now['opaque_context']==cp['opaque_context']
        proof=c.execute('SELECT dispatch,response FROM agent_task_native_writes WHERE operation_id=%s',(op.id,)).fetchone()
        assert proof[0]['postcondition']['values']=={k:DETAIL[k] for k in ('title','barcode','unit','remark','shortTitle')}
        assert proof[1]['body']['success'] is True
    http.post.assert_awaited_once();io.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['barcode','unit','remark','shortTitle','missing','null','numeric_barcode','cached','source','stale','revoked'])
async def test_title_match_alone_does_not_complete_other_requested_fields(product_fields_db,fault):
    f,runtime,handler,ctx,call,store,http,io,cp,controller,write,read=await prepared(product_fields_db,parameters=PARAMS)
    if fault=='stale':await read(data=DETAIL,call_id='before-write')
    await write();op=runtime.task_execution_state.operations[-1]
    data=dict(DETAIL)
    if fault in ('barcode','unit','remark','shortTitle'):data[fault]='different'
    if fault=='missing':del data['unit']
    if fault=='null':data['unit']=None
    if fault=='numeric_barcode':data['barcode']=123456789
    if fault!='stale':await read(data=data,source_token='other-session' if fault=='source' else 'local')
    if fault=='cached':
        with psycopg.connect(product_fields_db) as c:
            c.execute("UPDATE tool_invocations SET result=jsonb_set(result,'{tool_result,execution,cached}','true') WHERE task_id=%s AND tool_call_id='read-product'",(f['task'],))
    if fault=='revoked':
        with psycopg.connect(product_fields_db) as c:c.execute("UPDATE org_members SET status='inactive' WHERE org_id=%s AND user_id=%s",(f['org'],f['user']))
    ctrl=controller()
    if fault=='revoked':
        from services.task_execution.operation_store import TaskOperationPersistenceError
        with pytest.raises(TaskOperationPersistenceError):await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='verify')
    else:
        result=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='verify')
        assert result.result['ok'] and result.result['operation_status']=='accepted',result.result
        rejected=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='reject')
        assert not rejected.result['ok'] and not ctrl.state.artifacts
    http.post.assert_awaited_once();io.assert_awaited_once()


@pytest.mark.asyncio
async def test_fields_proof_survives_verified_native_session_rotation(product_fields_db):
    f,runtime,handler,ctx,call,store,http,io,cp,controller,write,read=await prepared(product_fields_db,parameters=PARAMS,organization_client=True)
    await write();op=runtime.task_execution_state.operations[-1]
    await refresh(f)
    await read(data=DETAIL,source_token='rotated-session')
    ctrl=controller();result=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='verify')
    assert result.result['operation_status']=='completed',result.result
    http.post.assert_awaited_once();io.assert_awaited_once()


@pytest.mark.asyncio
async def test_lost_ack_matching_fields_remain_unknown_and_rollback_checks_other_tenant(product_fields_db):
    f,runtime,handler,ctx,call,store,http,io,cp,controller,write,read=await prepared(product_fields_db,parameters=PARAMS,mode='timeout')
    await write();op=runtime.task_execution_state.operations[-1];await read(data=DETAIL)
    ctrl=controller();result=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='verify')
    assert result.result['operation_status']=='uncertain' and result.result['recovery']['target_state_observed']
    assert result.result['follow_up']['state']=='await_provider_terminal_evidence'
    rejected=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='reject')
    assert not rejected.result['ok'] and not ctrl.state.artifacts
    with psycopg.connect(product_fields_db) as c:
        with pytest.raises(psycopg.Error,match='DRAIN_REQUIRED'):
            with c.transaction():c.execute((ROOT/'migrations/rollback/370_agent_task_product_fields_rollback.sql').read_text())
        assert c.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid='public.agent_task_native_writes'::regclass").fetchone()[0]
    http.post.assert_awaited_once();io.assert_awaited_once()


@pytest.mark.asyncio
async def test_later_change_prevents_old_imported_fields_receipt_from_completing_step(product_fields_db):
    f,runtime,handler,ctx,call,store,http,io,cp,controller,write,read=await prepared(product_fields_db,parameters=PARAMS)
    await write();op=runtime.task_execution_state.operations[-1];await read(data=DETAIL)
    ctrl=controller();await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='verify')
    imported=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='import')
    assert imported.result['ok']
    runtime.task_execution_state=ctrl.state
    await read(data={**DETAIL,'unit':'盒'},call_id='later-read',force_network=True)
    result=await ctrl.control('complete_task_step',{'expected_revision':ctrl.state.revision,'step_id':op.step_id,
        'artifact_ids':[imported.result['imported_artifact_id']]},call_id='complete')
    assert not result.result['ok'];http.post.assert_awaited_once();io.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['async_ack','partial_ack','ignored_argument'])
async def test_all_matching_fields_cannot_cover_async_partial_or_ignored_edits(product_fields_db,mode):
    parameters={**PARAMS,**({'unsupported_edit':'new business field'} if mode=='ignored_argument' else {})}
    f,runtime,handler,ctx,call,store,http,io,cp,controller,write,read=await prepared(product_fields_db,parameters=parameters)
    if mode!='ignored_argument':
        body={'success':True,'data':{'jobId':'original-job'}} if mode=='async_ack' else {'success':True,'data':{'failCount':1}}
        http.post.return_value=SimpleNamespace(raise_for_status=lambda:None,json=lambda:body)
    await write();op=runtime.task_execution_state.operations[-1]
    await read(data=DETAIL)
    ctrl=controller();result=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='verify')
    assert result.result['ok'] and result.result['operation_status']=='accepted',result.result
    assert result.result['recovery']['next_action']=='requires_method_specific_verification'
    rejected=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='reject')
    assert not rejected.result['ok'] and not ctrl.state.artifacts
    if mode=='ignored_argument':
        with scoped(f) as c:
            dispatch=c.execute('SELECT dispatch FROM agent_task_native_writes WHERE operation_id=%s',(op.id,)).fetchone()[0]
            assert 'postcondition' not in dispatch
    http.post.assert_awaited_once();io.assert_awaited_once()
