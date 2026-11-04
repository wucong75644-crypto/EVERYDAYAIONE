"""Reuse exact published output with current scoped checks; never create an image."""
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from tests.test_model_directed_loop import case
from services.tools import ToolCall
from services.tools.catalog import build_capability_catalog
from services.task_execution.model_directed import record_calls
from services.task_execution.direct_loop import bind_image_recipes,reuse_images
from services.task_execution.reducer import digest


@pytest.mark.parametrize('fault',[None,'changed','denied'])
async def test_direct_reuse_binds_original_inputs_and_never_falls_back_on_denial(fault):
    r,p,_,_=case();catalog=build_capability_catalog()
    args={'mode':'text_to_image','prompt':'完整原提示词。保留全部外观。','aspect_ratio':'3:4'}
    raw={'id':'reuse-1','name':'generate_image','arguments':__import__('json').dumps(args)}
    r.task_execution_state=record_calls(None,r.task_bootstrap,[ToolCall(raw['id'],raw['name'],args)],catalog)
    resolver=AsyncMock(return_value=[]);resolver.prepare_request=AsyncMock(return_value=(args,[],None))
    r.task_image_resource_resolver=resolver
    state,errors=await bind_image_recipes(r,[raw],catalog)
    assert not errors
    r.task_execution_state=state
    row={'task_id':str(uuid4()),'message_id':str(uuid4()),'content_index':0,
        'part':{'type':'image','url':'https://example.invalid/existing.png'},'part_sha256':'a'*64,
        'request':{**args,'references':[],'request_hash':'b'*64,'origin':{'tool_call_id':'original'}}}
    if fault=='changed':row['request']['prompt']='不同提示词'
    if fault=='denied':resolver.side_effect=PermissionError('current access revoked')
    reader=SimpleNamespace(reusable_images=AsyncMock(return_value=[row]));r.task_artifact_reader=reader
    done,results,parts,errors=await reuse_images(r,[raw])
    assert not done.operations
    if fault:
        assert not results and not parts and not done.results
        assert bool(errors)==(fault=='denied')  # only an actual miss permits a fresh call
    else:
        assert not errors and parts==[row['part']] and len(done.results)==1
        assert results[0][1].metadata['reused']
        # A later authentic user request may reuse the same image, without a new effect.
        later={**raw,'id':'reuse-2'}
        r.task_execution_state=record_calls(done,r.task_bootstrap,[ToolCall(later['id'],later['name'],args)],catalog,input_sha256=digest(['new-input']))
        r.task_execution_state,errors=await bind_image_recipes(r,[later],catalog)
        assert not errors
        again,results,parts,errors=await reuse_images(r,[later])
        assert not errors and len(again.results)==2 and parts==[row['part']] and not again.operations


@pytest.mark.parametrize('fault',[None,'parameters','resource_version','missing_recipe'])
async def test_native_dispatch_rechecks_frozen_recipe_and_original_resource_version(fault):
    import json
    from services.handlers.chat_image_request import validate_single_image_request
    from services.task_execution.image_resources import verify_native_resources
    from services.task_execution.contracts import TaskError
    r,_,_,_=case();catalog=build_capability_catalog()
    args={'mode':'image_to_image','prompt':'完整原文提示词','aspect_ratio':'3:4',
        'references':[{'message_id':str(uuid4()),'content_index':0,'role':'product'}]}
    settings=validate_single_image_request(args,1)
    normalized={**args,**{k:settings[k] for k in ('model','resolution','output_format','aspect_ratio')}}
    refs=[{'workspace_path':'original.png','content_sha256':'a'*64,'file_version':[1,2,3,4],'role':'product'}]
    raw={'id':'original','name':'generate_image','arguments':json.dumps(args)}
    r.task_execution_state=record_calls(None,r.task_bootstrap,[ToolCall(raw['id'],raw['name'],args)],catalog)
    r.task_image_resource_resolver=SimpleNamespace(prepare_request=AsyncMock(return_value=(normalized,refs,None)))
    state,errors=await bind_image_recipes(r,[raw],catalog)
    assert not errors
    actual=copy.deepcopy(normalized);current=copy.deepcopy(refs)
    if fault=='parameters':actual['prompt']='偷偷换了提示词'
    if fault=='resource_version':current[0]['file_version'][-1]+=1
    if fault=='missing_recipe':state=state.model_copy(update={'artifacts':()})
    if fault:
        with pytest.raises(TaskError,match='TASK_IMAGE_(ORIGINAL_RESOURCE_CHANGED|RESOURCE_BINDING_REQUIRED)'):
            verify_native_resources(state.model_dump(mode='json'),raw['id'],args,actual,current)
    else:verify_native_resources(state.model_dump(mode='json'),raw['id'],args,actual,current)
