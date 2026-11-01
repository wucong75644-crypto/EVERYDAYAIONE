"""Postconditions are exact host projections, never success text or API guesses."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest

pytestmark = pytest.mark.usefixtures('erp_write_adapter_enabled')
from services.task_execution.erp_postconditions import METHOD,READ_METHOD,KEY,title_expectation,project_detail,verified_readback
from services.task_execution.native_writes import NativeWriteSession
from services.kuaimai.dispatcher import ErpDispatcher
from services.agent.agent_result import AgentResult


@pytest.mark.parametrize('changes',[{'skus':[]},{'brand':'new brand'},{'id':42},{'title':''},{'outerId':True}])
def test_unknown_additional_writes_cannot_be_covered_by_title_only_proof(changes):
    assert title_expectation(METHOD,{'outerId':'P-001','title':'文具8入',**changes}) is None


def test_original_mapper_pagination_is_not_a_new_business_field():
    assert title_expectation(METHOD,{'outerId':'P-001','title':'文具8入','pageNo':1,'pageSize':20})=={
        'version':1,'kind':'product.title','outer_id':'P-001','title':'文具8入'}


@pytest.mark.parametrize('data',[
    {'success':True,'outerId':'OTHER','title':'文具8入'},
    {'success':True,'data':{'outerId':'P-001','title':'文具8入'}},
    {'success':True,'outerId':'P-001','title':'文具8入','partial':True},
    {'success':True,'outerId':'P-001','title':'文具8入','data':{'failCount':'1'}},
    {'success':'true','outerId':'P-001','title':'文具8入'},
])
def test_inexact_or_partial_details_do_not_mint_a_read_projection(data):
    assert project_detail(READ_METHOD,{'outerId':'P-001'},data) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('managed',[False,True])
async def test_actual_dispatcher_preserves_legacy_metadata_and_projects_only_managed_exact_detail(managed):
    client=SimpleNamespace(_app_key='local',_app_secret='local',_access_token='local',_base_url='https://gw.superboss.cc/router',_org_id=None,request_with_retry=AsyncMock(return_value={'success':True,'outerId':'P-001','title':'文具8入'}))
    dispatcher=ErpDispatcher(client);dispatcher._task_receipt_projection=managed
    result=await dispatcher.execute('erp_product_query','product_detail',{'outer_id':'P-001'})
    assert (KEY in result.metadata)==managed
    if managed:assert result.metadata[KEY]['item']=={'outerId':'P-001','title':'文具8入'}
    client.request_with_retry.assert_awaited_once()


@pytest.mark.parametrize('field',['_org_id','_app_key','_app_secret','_access_token','_base_url'])
def test_different_connection_or_session_never_shares_a_receipt_identity(field):
    from services.task_execution.erp_postconditions import source_fingerprint
    original=SimpleNamespace(_org_id='tenant-1',_app_key='app-key',_app_secret='private-secret',_access_token='private-token',_base_url='https://example.invalid')
    current=deepcopy(original);setattr(current,field,'changed-value')
    assert source_fingerprint(current)!=source_fingerprint(original)
    assert len(source_fingerprint(original))==64
    assert source_fingerprint(SimpleNamespace()) is None


@pytest.mark.asyncio
async def test_ignored_extra_write_parameters_cannot_be_silently_covered_by_title_receipt(monkeypatch):
    monkeypatch.setattr(ErpDispatcher,'_record_param_knowledge',lambda *args,**kwargs:None)
    events=[]
    executor=SimpleNamespace(db=SimpleNamespace(rpc=lambda *_:SimpleNamespace(execute=lambda:SimpleNamespace(data={'outcome':'recorded'}))))
    session=NativeWriteSession(executor,{'operation_id':'a'*64,'dispatch_token':str(uuid4())},
        {'category':'product','action':'product_add_update_simple'},arguments={'params':{'outer_id':'P-001','title':'文具8入','unknown_extra':'another edit'}})
    async def record(phase,**kwargs):
        from services.task_execution.erp_postconditions import title_expectation
        events.append(kwargs)
        return await session.observe(phase,**kwargs)
    client=SimpleNamespace(_app_key='local',_app_secret='local',_access_token='local',_base_url='https://gw.superboss.cc/router',_org_id=None,
        request_once=AsyncMock(return_value={'success':True}))
    dispatcher=ErpDispatcher(client);dispatcher._native_write_observer=record
    captured=[]
    executor.db.rpc=lambda name,params:(captured.append(params) or SimpleNamespace(execute=lambda:SimpleNamespace(data={'outcome':'recorded'})))
    await dispatcher.execute('erp_product_query','product_add_update_simple',{'outer_id':'P-001','title':'文具8入','unknown_extra':'another edit'})
    assert 'postcondition' not in captured[0]['p_value']
    client.request_once.assert_awaited_once()


@pytest.mark.asyncio
async def test_purchase_calculation_keeps_original_write_entry_and_never_executes_as_read_or_sandbox_query():
    from services.tools import ToolCall,ToolDispatcher,ToolExecutionService,ToolConfirmation
    from services.tools.catalog import build_capability_catalog
    from services.kuaimai.registry.purchase import PURCHASE_REGISTRY
    from tests.test_task_execution_native_writes import case
    runtime,handler,ctx,_,_,_,_,_=case()
    assert PURCHASE_REGISTRY['purchase_strategy_calculate'].is_write is True
    io=AsyncMock(return_value=AgentResult('已受理计算'))
    service=ToolExecutionService(build_capability_catalog(),ToolDispatcher({('legacy','erp_purchase_query'):io,('legacy','erp_execute'):io}))
    query=ToolCall('wrong-read','erp_purchase_query',{'action':'purchase_strategy_calculate','params':{'warehouse_code':'WH1'}})
    rejected=await service.execute(query,ctx);assert rejected.status=='denied';io.assert_not_awaited()
    write=ToolCall('right-write','erp_execute',{'category':'purchase','action':'purchase_strategy_calculate','params':{'warehouse_code':'WH1'}})
    wait=await service.execute(write,ctx);assert wait.status=='confirmation_required';io.assert_not_awaited()
    from dataclasses import replace
    decision=service.policy.decide(write.name,replace(ctx,call_id=write.call_id),write.arguments)
    approved=await service.execute(write,ctx,confirmation=ToolConfirmation(decision.confirmation_binding,'approved'))
    assert approved.status=='success';io.assert_awaited_once()
    client=SimpleNamespace(request_with_retry=AsyncMock())
    raw=await ErpDispatcher(client).execute_raw('erp_purchase_query','purchase_strategy_calculate',{'warehouse_code':'WH1'})
    assert 'error' in raw;client.request_with_retry.assert_not_awaited()
