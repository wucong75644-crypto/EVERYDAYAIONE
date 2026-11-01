from copy import deepcopy
from uuid import uuid4
import pytest

pytestmark = pytest.mark.usefixtures('erp_write_adapter_enabled')
from services.task_execution.contracts import TaskState
from services.task_execution.progress import project,marker
from services.task_execution.reducer import reserve_operation,record_operation
from tests.test_task_execution_reducer import state,plan


@pytest.mark.parametrize('phase',['reserved','accepted','uncertain'])
def test_receipt_is_never_displayed_as_completed_delivery(phase):
    s=plan(state(image_binding_version=3))
    s,op,_=reserve_operation(s,expected_revision=s.revision,step_id='research',capability='web_search',
        effect_key='query',effect_version=1,arguments={'private':'do not expose'},owner_task_id=s.root_task_id,invocation_id='secret-invocation')
    if phase!='reserved':s=record_operation(s,op.id,owner_task_id=s.root_task_id,status=phase,receipt_ref='private:receipt' if phase=='accepted' else None)
    value=project(s)
    assert value['status']=='active' and value['completed_steps']==value['completed_requirements']==0
    assert value['steps'][0]['status']==('waiting_result' if phase=='accepted' else 'needs_verification')
    assert value['steps'][1]['status']=='waiting_dependency'
    assert 'private' not in str(value) and 'secret-invocation' not in str(value)


def test_marker_is_only_discovery_and_old_or_channel_protocol_is_unchanged():
    s=state(image_binding_version=3);blocks=[{'type':'text','text':'original'}]
    assert marker(blocks,s)=={'type':'task_progress','root_task_id':str(s.root_task_id)}
    assert marker(blocks,s) is None and len(blocks)==2
    assert marker([],state()) is None
    channel=TaskState.model_validate({**s.model_dump(),'execution_identity':{'workspace_owner_id':'channel',
        'context_scope':'channel','personal_context_allowed':False,'channel_scope_id':'channel'}})
    assert marker([],channel) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[False,True])
async def test_control_marker_is_checkpointed_before_publish_and_failed_save_keeps_original_blocks(failure):
    from tests.test_task_execution_model_bridge import setup_case,execute,call
    saved=[]
    async def checkpoint(point,payload):
        saved.append(deepcopy(payload))
        if failure:raise RuntimeError('save failed')
        return {'outcome':'saved'}
    runtime,prepared,handler,sink=setup_case(checkpoint,initial=state(image_binding_version=3))
    blocks=[{'type':'text','text':'original'}]
    if failure:
        with pytest.raises(RuntimeError):await execute(runtime,prepared,handler,sink,[call()],blocks)
        assert blocks==[{'type':'text','text':'original'}]
        sink.on_block.assert_not_awaited()
    else:
        await execute(runtime,prepared,handler,sink,[call()],blocks)
        assert saved[0]['content_blocks'][-1]['type']=='task_progress'
        assert sink.on_block.await_args.args[0]['type']=='task_progress'


def test_marker_round_trips_actual_outcome_and_message_schema_without_private_payload():
    from services.handlers.chat.outcome_builder import build_content_parts
    from schemas.message import serialize_content_parts
    root=str(uuid4());block={'type':'task_progress','root_task_id':root,'state':{'private':'secret'}}
    value=serialize_content_parts(build_content_parts([block],fallback_text=''))
    assert value==[{'type':'task_progress','root_task_id':root}]


@pytest.mark.asyncio
async def test_coverage_approval_saves_marker_with_full_context_before_ws_publication():
    from tests.test_task_execution_requirement_review import staged,observation
    from services.task_execution.requirement_review import apply_review
    snapshots=[]
    async def save(point,payload):
        snapshots.append(deepcopy(payload))
        return {'outcome':'saved'}
    runtime,prepared,handler,sink=await staged(checkpoint=save)
    original=deepcopy(prepared.messages)
    blocks=[]
    await apply_review(runtime=runtime,prepared=prepared,blocks=blocks,text=observation(),has_tool_calls=False,
        model_round=1,next_model_round=2,build_checkpoint=lambda messages,blocks,turn,**kwargs:{'messages':deepcopy(messages),'content_blocks':deepcopy(blocks)},turn=1,sink=sink)
    assert blocks[-1]['type']=='task_progress'
    assert snapshots[-1]['content_blocks'][-1]['type']=='task_progress'
    assert snapshots[-1]['messages'][:len(original)]==original
    sink.on_block.assert_awaited_once_with(blocks[-1])


@pytest.mark.asyncio
async def test_accepted_erp_is_verification_work_not_an_automatically_arriving_result():
    from tests.test_task_execution_native_writes import case
    from tests.test_task_execution_reach_operations import execute
    runtime,handler,ctx,call,service,io,store,client=case(body={'success':True,'data':{'jobId':'native-job'}})
    await execute(runtime,handler,ctx,call,service)
    s=runtime.task_execution_state;before=s.model_dump(mode='json')
    assert s.operations[-1].status=='accepted'
    value=project(s)
    assert value['pending_results']==0 and value['unconfirmed_results']==1
    assert value['steps'][0]['status']=='needs_verification' and value['completed_steps']==0
    assert 'native-job' not in str(value) and 'postcondition' not in str(value)
    assert s.model_dump(mode='json')==before;io.assert_awaited_once()
