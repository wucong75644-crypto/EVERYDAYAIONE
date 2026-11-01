"""Stable product scalars are exact postconditions, not inferred conversions."""
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from services.task_execution.erp_postconditions import (METHOD,READ_METHOD,KEY,FIELDS_KEY,product_expectation,
    original_expectation,project_fields,view_matches,expectation)
from services.task_execution.contracts import TaskError
from services.kuaimai.dispatcher import ErpDispatcher


@pytest.mark.parametrize('fields',[
    {'barcode':'0690123456789'},{'unit':'本'},{'remark':''},{'shortTitle':'荔枝包'},
    {'barcode':'0690123456789','unit':'本','remark':'保留原说明','shortTitle':'荔枝包'}])
def test_only_explicit_string_fields_are_bound_without_normalization(fields):
    value=product_expectation(METHOD,{'outerId':'P-001','title':'原商品',**fields,'pageNo':1,'pageSize':20})
    assert value=={'version':2,'kind':'product.fields','outer_id':'P-001','values':{'title':'原商品',**fields}}
    data={'success':True,'outerId':'P-001','title':'原商品',**fields}
    projected=project_fields(READ_METHOD,{'outerId':'P-001'},data)
    assert view_matches(value,{**projected,'source_fingerprint':'a'*64})
    changed=copy.deepcopy(projected);changed['item'][next(iter(fields))]='changed'
    assert not view_matches(value,{**changed,'source_fingerprint':'a'*64})


@pytest.mark.parametrize('fields',[{'barcode':690123},{'unit':None},{'remark':False},{'brand':'unknown'},
    {'skus':[]},{'purchasePrice':12},{'weight':42},{'activeStatus':1},{'unit':'x'*129}])
def test_unmapped_numeric_nested_or_oversized_edits_do_not_get_a_completion_contract(fields):
    assert product_expectation(METHOD,{'outerId':'P-001','title':'原商品',**fields}) is None


def test_unmapped_original_fields_not_laundered_by_dispatcher_ignoring_them():
    assert original_expectation(METHOD,{'params':{'outer_id':'P-001','title':'原商品','unit':'本','unknown_extra':'edit'}}) is None
    assert original_expectation(METHOD,{'params':{'outer_id':'P-001','title':'原商品','short_title':'短名'}})['values']['shortTitle']=='短名'


@pytest.mark.parametrize('fault',['missing','null','coerced','partial','nested','other_product'])
def test_read_must_contain_all_requested_fields_exactly(fault):
    expected=product_expectation(METHOD,{'outerId':'P-001','title':'原商品','barcode':'00123','unit':'本'})
    data={'success':True,'outerId':'P-001','title':'原商品','barcode':'00123','unit':'本'}
    if fault=='missing':del data['unit']
    if fault=='null':data['unit']=None
    if fault=='coerced':data['barcode']=123
    if fault=='partial':data['data']={'failedCount':1}
    if fault=='nested':data={'success':True,'data':data}
    if fault=='other_product':data['outerId']='P-002'
    view=project_fields(READ_METHOD,{'outerId':'P-001'},data)
    assert view is None or not view_matches(expected,{**view,'source_fingerprint':'a'*64})


@pytest.mark.asyncio
async def test_managed_dispatcher_preserves_v1_and_emits_separate_fields_receipt():
    client=SimpleNamespace(_app_key='local',_app_secret='local',_access_token='local',_base_url='https://gw.superboss.cc/router',_org_id=None,
        request_with_retry=AsyncMock(return_value={'success':True,'outerId':'P-001','title':'原商品','barcode':'00123','unit':'本'}))
    dispatcher=ErpDispatcher(client);dispatcher._task_receipt_projection=True
    result=await dispatcher.execute('erp_product_query','product_detail',{'outer_id':'P-001'})
    assert result.metadata[KEY]['item']=={'outerId':'P-001','title':'原商品'}
    assert result.metadata[FIELDS_KEY]['item']=={'outerId':'P-001','title':'原商品','barcode':'00123','unit':'本'}
    client.request_with_retry.assert_awaited_once()


@pytest.mark.parametrize('fault',['extra','drop','version','unsupported','title_only'])
def test_persisted_contract_shape_cannot_drop_or_add_unverified_fields(fault):
    value={**product_expectation(METHOD,{'outerId':'P-001','title':'原商品','unit':'本'}),'source_fingerprint':'a'*64}
    if fault=='extra':value['extra']='new right'
    if fault=='drop':del value['values']['title']
    if fault=='version':value['version']=1
    if fault=='unsupported':value['values']['weight']=42
    if fault=='title_only':del value['values']['unit']
    with pytest.raises(TaskError):expectation({'domain':'erp','intent':{'category':'product','action':'product_add_update_simple'},
        'dispatch':{'method':METHOD,'postcondition':value}})
