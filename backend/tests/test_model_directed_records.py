"""Model work plans cannot replace native execution records or grant access."""
from uuid import uuid4
from types import SimpleNamespace
import pytest
from services.task_execution.bootstrap import TaskBootstrap
from services.task_execution.contracts import ExecutionIdentity,TaskError
from services.task_execution.model_directed import record_calls,call_binding,WorkingPlan
from services.tools import ToolCall


def bootstrap():
    user=uuid4()
    return TaskBootstrap(task_id=uuid4(),conversation_id=uuid4(),actor_user_id=user,
        input_message_id=uuid4(),original_goal='分析原图并完成用户要求',model_directed=True,
        execution_identity=ExecutionIdentity(workspace_owner_id=str(user),context_scope='user',personal_context_allowed=True))


class Registry:
    def get(self,name):
        return SimpleNamespace(capability='platform.generate_image' if name=='generate_image' else 'sandbox.code.execute')


def test_calls_are_concrete_obligations_not_extracted_user_goals():
    b=bootstrap();call=ToolCall('call-1','generate_image',{'prompt':'原提示词'})
    s=record_calls(None,b,[call],Registry())
    assert s.model_directed and s.original_goal==b.original_goal
    assert s.requirements[0].id.startswith('host.result.')
    assert s.requirements[0].artifact_kind=='image'
    assert s.steps[0].capability=='platform.generate_image'
    assert call_binding(s,call)[0]==s.steps[0]
    assert record_calls(s,b,[call],Registry()) is s
    with pytest.raises(TaskError,match='ARGUMENTS_CHANGED'):
        record_calls(s,b,[ToolCall('call-1','generate_image',{'prompt':'不同参数'})],Registry())


def test_parallel_same_prompt_calls_have_distinct_slots_and_stable_serialization():
    calls=[ToolCall('call-'+str(i),'generate_image',{'prompt':'same'}) for i in range(2)]
    s=record_calls(None,bootstrap(),calls,Registry())
    assert len(s.steps)==2 and len(s.requirements)==2
    assert s.model_validate(s.model_dump(mode='json'))==s
    assert WorkingPlan(steps=[]).steps==()


def test_optional_plan_is_not_permission_or_delivery_evidence():
    from pydantic import ValidationError
    from services.task_execution.model_directed import update_working_plan
    from services.task_execution.reducer import finish
    b=bootstrap();s=record_calls(None,b,[ToolCall('call','generate_image',{'prompt':'原文'})],Registry())
    updated=update_working_plan(s,{'steps':[{'step':'已完成整套图片','status':'completed'}]})
    assert updated.steps==s.steps and updated.requirements==s.requirements
    assert updated.operations==s.operations and updated.results==s.results==()
    assert updated.original_goal==s.original_goal and updated.status=='active'
    with pytest.raises(TaskError,match='DELIVERY_INCOMPLETE'):finish(updated)
    with pytest.raises(ValidationError):
        update_working_plan(s,{'steps':[],'permissions':['generate_image']})
    cleared=update_working_plan(updated,{'steps':[]})
    assert cleared.steps==s.steps and cleared.call_bindings==s.call_bindings


def operation_state(status='uncertain'):
    from services.task_execution.reducer import digest
    from services.task_execution.contracts import Operation,TaskState
    b=bootstrap();call=ToolCall('first','generate_image',{'prompt':'原文'})
    s=record_calls(None,b,[call],Registry())
    op=Operation(id=digest('operation'),step_id=s.steps[0].id,
        capability=s.steps[0].capability,effect_key='image-slot',effect_version=1,
        arguments_sha256=digest(dict(call.arguments)),owner_task_id=b.task_id,
        invocation_id=call.call_id,tool_name=call.name,status=status,
        receipt_ref='task:'+str(uuid4()) if status in {'accepted','completed'} else None)
    return b,TaskState.model_validate({**s.model_dump(),'operations':[op]})


def test_unknown_acceptance_cannot_be_bypassed_with_new_parameters():
    b,s=operation_state()
    same=ToolCall('new-id','generate_image',{'prompt':'原文'})
    rebound=record_calls(s,b,[same],Registry())
    assert call_binding(rebound,same)[0].id==s.steps[0].id
    assert rebound.operations==s.operations and len(rebound.steps)==1
    with pytest.raises(TaskError,match='EFFECT_UNRESOLVED'):
        record_calls(s,b,[ToolCall('new-id','generate_image',{'prompt':'changed'})],Registry())


def test_completed_output_requires_proven_reuse_instead_of_a_new_dispatch_slot():
    b,s=operation_state('completed')
    with pytest.raises(TaskError,match='RESULT_REUSE_REQUIRED'):
        record_calls(s,b,[ToolCall('new-id','generate_image',{'prompt':'原文'})],Registry())


def test_old_states_keep_their_saved_protocol_and_cannot_acquire_new_plan_semantics():
    from services.task_execution.model_directed import update_working_plan
    from tests.test_task_execution_reducer import state
    legacy=state()
    raw=legacy.model_dump(mode='json')
    assert not {'model_directed','call_bindings','working_plan'} & raw.keys()
    assert legacy.model_validate(raw)==legacy
    with pytest.raises(TaskError,match='POLICY_REQUIRED'):
        update_working_plan(legacy,{'steps':[]})


def test_progress_distinguishes_model_notes_from_actual_results_and_preserves_message_marker():
    from services.task_execution.model_directed import update_working_plan
    from services.task_execution.progress import project,marker
    from services.handlers.chat.outcome_builder import build_content_parts
    from schemas.message import serialize_content_parts
    b=bootstrap();s=record_calls(None,b,[ToolCall('call','generate_image',{'prompt':'原文'})],Registry())
    s=update_working_plan(s,{'steps':[{'step':'完成','status':'completed'}]})
    value=project(s)
    assert value['image_jobs']=={'completed':0,'total':1}
    assert value['completed_steps']==value['completed_requirements']==0
    assert value['working_plan']['steps'][0]['status']=='completed'
    blocks=[];marker(blocks,s)
    assert serialize_content_parts(build_content_parts(blocks,fallback_text=''))==blocks
    assert blocks[0]['presentation']=='model_directed'


def test_direct_bootstrap_cannot_silently_create_a_legacy_task():
    from dataclasses import replace
    b=bootstrap()
    with pytest.raises(TaskError,match='CALL_RECORD_REQUIRED'):
        b.initialize({'requirements':[]},model_rounds_used=0)
    with pytest.raises(TaskError,match='POLICY_CONFLICT'):
        replace(b,require_coverage_review=True)


def test_host_resolved_arguments_preserve_raw_binding_and_cannot_be_rebound():
    from services.task_execution.model_directed import resolve_call
    from services.tools.catalog import build_capability_catalog
    b=bootstrap();raw=ToolCall('delete','file_delete',{'files':'原文件.txt'})
    state=record_calls(None,b,[raw],build_capability_catalog())
    resolved=ToolCall('delete','file_delete',{'files':['原文件.txt']})
    bound=resolve_call(state,raw,resolved)
    assert bound.call_bindings==state.call_bindings
    assert call_binding(bound,resolved)[0]==state.steps[0]
    assert resolve_call(bound,raw,resolved) is bound
    with pytest.raises(TaskError,match='RESOLVED_ARGUMENTS_CHANGED'):
        resolve_call(bound,raw,ToolCall('delete','file_delete',{'files':['另一个文件.txt']}))
    with pytest.raises(TaskError,match='BINDING_MISMATCH'):
        call_binding(bound,raw)
