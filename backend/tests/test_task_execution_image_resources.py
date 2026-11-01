"""New bindings use native original bytes, retaining legacy checkpoint shape."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PIL import Image

from services.task_execution.contracts import TaskError,TaskState
from services.task_execution.image_resources import BindImageResources,bind_resources,verify_native_resources
from services.task_execution.image_binding import bind_image_call
from services.task_execution.reducer import register_artifacts,reserve_operation
from services.tools.catalog import build_capability_catalog
from services.tools.spec import thaw
from tests.test_task_execution_image_binding import ready,native
from tests.test_chat_image_sources import context,resolver
from tests.test_chat_image_reference_continuity import source


async def bound_case(monkeypatch,context):
    old=await ready(monkeypatch)
    assert 'image_binding_version' not in old.model_dump(mode='json')
    state=TaskState.model_validate({**old.model_dump(),'image_binding_version':2})
    refs=[{'message_id':'source-message','content_index':3,'role':'product'},
          {'message_id':'source-message','content_index':1,'role':'style'}]
    async def resolve(refs,**_):return resolver(context).resolve(refs)
    command=BindImageResources(expected_revision=state.revision,planning_artifact_id=state.artifacts[0].id,
        image_item_index=1,mode='image_to_image',references=tuple(refs))
    artifact,proof=await bind_resources(state,command,resolver=resolve,owner_task_id=state.root_task_id,call_id='bind-originals')
    return register_artifacts(state,[artifact]),command,proof,resolve


@pytest.mark.asyncio
async def test_v2_requires_host_binding_and_preserves_original_order_role_and_bytes(monkeypatch,context):
    state,command,proof,resolve=await bound_case(monkeypatch,context)
    assert [r['workspace_path'] for r in proof['resolved']]==['B.png','A.png']
    assert [r['role'] for r in proof['resolved']]==['product','style']
    call=native(references=list(command.references))
    assert bind_image_call(state,call,build_capability_catalog()).item_index==1
    verify_native_resources(state.model_dump(mode='json'),call.call_id,dict(call.arguments),
        {'aspect_ratio':'3:4'},await resolve(list(command.references)))
    original=state.model_copy(update={'artifacts':state.artifacts[:-1]})
    with pytest.raises(TaskError,match='RESOURCE_BINDING_REQUIRED'):
        bind_image_call(original,call,build_capability_catalog())
    artifact,again=await bind_resources(state,command,resolver=resolve,owner_task_id=state.root_task_id,call_id='repeat')
    assert artifact is None and again==proof


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['order','role','mode','bytes','size'])
async def test_changed_original_selection_bytes_or_resolved_ratio_fails_before_acceptance(monkeypatch,context,change):
    state,command,proof,resolve=await bound_case(monkeypatch,context)
    refs=[dict(r) for r in command.references]
    if change=='order':refs.reverse()
    if change=='role':refs[0]['role']='style'
    call=native(references=refs,**({'mode':'text_to_image'} if change=='mode' else {}))
    if change=='bytes':Image.new('RGB',(4,4),'green').save(context[2].resolve_safe_path('B.png'))
    with pytest.raises((TaskError,ValueError)):
        verify_native_resources(state.model_dump(mode='json'),call.call_id,dict(call.arguments),
            {'aspect_ratio':'1:1' if change=='size' else '3:4'},await resolve(refs))


@pytest.mark.asyncio
async def test_binding_correction_is_append_only_and_locked_once_effect_reserved(monkeypatch,context):
    state,command,proof,resolve=await bound_case(monkeypatch,context)
    changed=command.model_copy(update={'references':tuple(reversed(command.references))})
    artifact,_=await bind_resources(state,changed,resolver=resolve,owner_task_id=state.root_task_id,call_id='correction')
    assert artifact.id!=state.artifacts[-1].id
    call=native(references=list(command.references))
    binding=bind_image_call(state,call,build_capability_catalog())
    reserved,_,_=reserve_operation(state,expected_revision=state.revision,step_id=binding.step_id,
        capability='platform.generate_image',effect_key=binding.effect_key,effect_version=1,
        arguments=thaw(call.arguments),owner_task_id=state.root_task_id,invocation_id=call.call_id)
    with pytest.raises(TaskError,match='RESOURCE_BINDING_LOCKED'):
        await bind_resources(reserved,changed,resolver=resolve,owner_task_id=state.root_task_id,call_id='after-reserve')


@pytest.mark.asyncio
@pytest.mark.parametrize('denied',[False,True])
async def test_actor_resolver_preserves_existing_resource_authorization(monkeypatch,context,denied):
    from services.task_execution.image_resources import create_resolver
    from services.tools import ToolContext
    from services.tools.resource_access import ResourceAccessBoundary,ResourceRule
    row,owner,files,current=context
    monkeypatch.setattr('services.file_executor.FileExecutor',lambda *_,**__:files)
    task='00000000-0000-4000-8000-000000000002'
    owner.db.set_table_data('tasks',[{'id':task,'user_id':owner.user_id,'org_id':None,
        'conversation_id':owner.conversation_id,'input_message_id':current['id'],'base_context_revision':1,'request_params':{}}])
    boundary=ResourceAccessBoundary(()) if denied else ResourceAccessBoundary(
        (ResourceRule(('read',),paths=('B.png',)),),'specific_original',True)
    handler=SimpleNamespace(db=owner.db,_resource_access_boundary=boundary)
    request=SimpleNamespace(task_id=task,conversation_id=owner.conversation_id)
    tool_context=ToolContext(actor_user_id=owner.user_id,workspace_owner_id=owner.user_id,org_id=None,
        context_scope='user',personal_context_allowed=True,agent_domain='general',permission_mode='auto',execution_mode='interactive')
    resolve=create_resolver(handler,request,tool_context)
    refs=[{'message_id':row['id'],'content_index':3,'role':'product'}]
    if denied:
        with pytest.raises(PermissionError):await resolve(refs,require_originals=True)
    else:
        selected=await resolve(refs,require_originals=True)
        assert selected[0]['workspace_path']=='B.png'
        with pytest.raises(PermissionError):
            await resolve([{'message_id':row['id'],'content_index':1,'role':'style'}],require_originals=True)


@pytest.mark.asyncio
async def test_explicit_preparation_step_delivers_binding_proof_then_unblocks_generation(monkeypatch,context):
    from services.task_execution.contracts import PlanStep,WorkPlan
    from services.task_execution.reducer import revise_plan,complete_step
    state,command,_,resolve=await bound_case(monkeypatch,context)
    preparation=PlanStep(id='prepare-originals',title='绑定原图',depends_on=('planning',))
    images=state.steps[-1].model_copy(update={'depends_on':(preparation.id,)})
    state=revise_plan(state,WorkPlan(expected_revision=state.revision,steps=(state.steps[0],preparation,images)))
    command=command.model_copy(update={'expected_revision':state.revision,'step_id':preparation.id})
    artifact,proof=await bind_resources(state,command,resolver=resolve,owner_task_id=state.root_task_id,call_id='prepare')
    assert artifact.step_id==preparation.id and 'image.resources.bound' in artifact.validations
    state=complete_step(register_artifacts(state,[artifact]),preparation.id,[artifact.id])
    assert bind_image_call(state,native(references=list(command.references)),build_capability_catalog()).step_id=='images'
    repeated,again=await bind_resources(state,command,resolver=resolve,owner_task_id=state.root_task_id,call_id='repeat')
    assert repeated is None and again==proof
    with pytest.raises(TaskError,match='PREPARATION_STEP_INVALID'):
        await bind_resources(state,command.model_copy(update={'step_id':'images'}),resolver=resolve,
            owner_task_id=state.root_task_id,call_id='not-a-new-image')
