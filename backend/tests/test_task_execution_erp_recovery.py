"""ERP recovery facts never authorize resending or generic batch completion."""
import json
import pytest
from services.task_execution.erp_recovery import request_evidence,recovery_facts
from services.task_execution.reducer import digest
from services.task_execution.contracts import TaskError


def proof(parameters=None,body=None):
    p=parameters or {'itemInfoList':[{'outerId':'A'},{'outerId':'B'}],'operateType':1}
    return {'domain':'erp','phase':'returned','dispatch':{'method':'erp.item.tag.batch.update',
        'parameters_sha256':digest(p),'request_evidence':request_evidence('erp.item.tag.batch.update',p)},
        'response':{'method':'erp.item.tag.batch.update','body':body or {'success':True,'data':{'items':[{'outerId':'A','success':True},{'outerId':'B','success':False}]}}}}


def test_actual_batch_request_survives_projection_with_secrets_redacted():
    params={'itemInfoList':'[{"outerId":"A","defaultPassword":"private"}]','access_token':'private',
        'nested':{'app_secret':'private'},'pageNo':1}
    original=json.loads(json.dumps(params))
    value=request_evidence('method',params)
    assert 'private' not in json.dumps(value) and params==original
    assert value['parameters']['itemInfoList']['json_value'][0]['outerId']=='A'
    assert len(value['redacted_paths'])==3 and value['parameters_sha256']==digest(params)


def test_partial_batch_keeps_success_failure_and_instance_handles_without_completion_claim():
    p=proof(body={'success':True,'jobId':'original-job','data':{'items':[{'outerId':'A','success':True},{'outerId':'B','success':False}]}})
    value=recovery_facts(p,'accepted')
    assert value['can_resend'] is False and value['completion_basis'] is None and value['needs_follow_up']
    assert [i['response_status'] for i in value['item_observations']]==['reported_success','reported_failure']
    assert value['provider_handles']==[{'path':['jobId'],'value':'original-job'}]
    assert value['next_action']=='requires_method_specific_verification'


@pytest.mark.parametrize('fault',['body','checksum','method'])
def test_changed_request_evidence_rejected(fault):
    p=proof()
    key={'body':'parameters','checksum':'sha256','method':'method'}[fault]
    p['dispatch']['request_evidence'][key]='changed'
    with pytest.raises(TaskError,match='EVIDENCE_UNVERIFIED'):recovery_facts(p,'accepted')


def test_missing_legacy_evidence_is_explicit_and_does_not_break_old_results():
    p=proof();del p['dispatch']['request_evidence']
    value=recovery_facts(p,'accepted');assert value['request_evidence'] is None and not value['can_resend']


@pytest.mark.parametrize('parameters',[{'items':[{'id':n} for n in range(5000)]},{'body':'x'*140000}])
def test_large_evidence_is_incomplete_and_never_changes_original_parameters(parameters):
    value=request_evidence('method',parameters)
    assert value['incomplete'] and value['parameters_sha256']==digest(parameters)


@pytest.mark.parametrize('body',[{'success':True,'jobId':'original-job'},{'success':True,'data':{'failCount':1}}])
def test_title_postcondition_does_not_prompt_blind_detail_queries_for_partial_or_async_receipts(body):
    from services.task_execution.erp_postconditions import read_instruction
    p={'domain':'erp','intent':{'category':'product','action':'product_add_update_simple'},'phase':'returned',
        'dispatch':{'method':'item.general.addorupdate','postcondition':{'version':1,'kind':'product.title',
            'outer_id':'P-001','title':'title','source_fingerprint':'a'*64}},'response':{'body':body}}
    value=read_instruction(p)
    assert value['state']=='requires_method_specific_verification' and 'tool_name' not in value
