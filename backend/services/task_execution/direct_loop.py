"""Model-led work inside the existing Actor loop and checkpoint transaction.

No executor, authorization source or business queue lives here. Optional notes
can exist before there is an actual operation; native adapters own all effects.
"""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from uuid import UUID

from pydantic import ValidationError
from services.conversation_commands import SafePoint
from services.tools import ToolCall
from services.tools.policy import ToolPolicy
from services.tools.spec import thaw
from .contracts import Artifact, TaskError, TaskState, WorkingPlan
from .model_directed import INSTRUCTIONS, PREFIX, PLAN_SCHEMA, WorkingPlanUpdate, enabled, record_calls
from .reducer import digest, register_artifacts, complete_step

PLAN_KEY='_task_direct_plan'
RESUME_SCHEMA={'type':'function','function':{'name':'resume_task_execution',
    'description':'仅当前用户明确继续或修改给定历史任务时，接回该任务的原上下文、成果和未决操作；新问题直接处理，无需使用。此动作不授予权限或重复执行权。',
    'parameters':{'type':'object','properties':{'relation':{'type':'string','enum':['continue','amend']},
        'evidence':{'type':'array','items':{'type':'string'},'minItems':1,'maxItems':4}},
        'required':['relation','evidence'],'additionalProperties':False}}}


def followup_source(runtime):
    source=getattr(runtime,'task_followup_candidate',None)
    return source if source and source.get('state',{}).get('model_directed') is True else None

READ_CONTROLS=frozenset({'find_task_text','read_task_text','read_task_result',
    'read_task_write_result','read_task_scheduled_result','read_task_code_result'})
NATIVE_TOOLS=frozenset({'generate_image','generate_video','code_execute',
    'agent_reach','manage_scheduled_task','file_delete','restore_file','erp_execute'})


def restore_plan(runtime,payload):
    seen=(payload or {}).get('_task_direct_seen',[])
    if seen:
        if not enabled(runtime) or not isinstance(seen,list) or len(seen)!=len(set(seen)):
            raise TaskError('TASK_DIRECT_INPUT_CURSOR_INVALID')
        runtime.task_direct_seen=tuple(str(UUID(v)) for v in seen)
    raw=(payload or {}).get(PLAN_KEY)
    owner=(payload or {}).get('_task_direct_plan_owner')
    plan_id=(payload or {}).get('_task_direct_plan_id')
    fresh=(payload or {}).get('_task_followup_input_checkpoint',{})
    if (owner!=runtime.task_id and (payload or {}).get('_task_followup')
            and fresh.get('_task_direct_plan_owner')==runtime.task_id and fresh.get(PLAN_KEY) is not None):
        raw=fresh[PLAN_KEY];owner=runtime.task_id
        plan_id=fresh.get('_task_direct_plan_id')
    if raw is None:return
    if not enabled(runtime):raise TaskError('TASK_DIRECT_PLAN_POLICY_MISMATCH')
    runtime.task_direct_plan=WorkingPlan.model_validate(raw)
    runtime.task_direct_plan_owner=owner
    runtime.task_direct_plan_id=str(UUID(plan_id)) if plan_id is not None else None
    state=runtime.task_execution_state
    if state is not None and runtime.task_direct_plan!=state.working_plan:
        from .model_directed import update_working_plan
        runtime.task_execution_state=update_working_plan(state,runtime.task_direct_plan.model_dump(mode='json'))


def context_message(runtime,*,state=None):
    state=runtime.task_execution_state if state is None else state
    bootstrap=runtime.task_bootstrap
    plan=getattr(runtime,'task_direct_plan',None) or (state.working_plan if state else None)
    facts={'original_goal':state.original_goal if state else bootstrap.original_goal,
        'working_plan':plan.model_dump(mode='json') if plan else None}
    if state is None and plan is None and runtime.task_plan_candidates:
        facts['editable_working_plans'] = list(runtime.task_plan_candidates.values())
    source=followup_source(runtime)
    if source is not None and state is None:
        facts['previous_task']={k:copy.deepcopy(source['state'].get(k)) for k in ('root_task_id','original_goal','status','operations','artifacts')}
        for artifact in facts['previous_task'].get('artifacts',[]):artifact.pop('inline_text',None)
    if state is not None:
        facts.update(revision=state.revision,status=state.status,
            operations=[v.model_dump(mode='json') for v in state.operations],
            artifacts=[v.model_dump(mode='json',exclude={'inline_text'}) for v in state.artifacts],
            completed_call_steps=[v.step_id for v in state.results])
    return {'role':'system','content':PREFIX+INSTRUCTIONS+'\n'+json.dumps(facts,ensure_ascii=False)}


def schemas(runtime,controller=None):
    result=[PLAN_SCHEMA]
    if runtime.task_execution_state is None and followup_source(runtime):result.append(RESUME_SCHEMA)
    if controller is not None:
        for schema in controller.schemas():
            if schema['function']['name'] in READ_CONTROLS:
                result.append({**schema,'function':{**schema['function'],'description':
                    '只读查看已保存的真实成果或原操作结果，不重跑原操作。宿主自动登记已核实的原生回执，无需另行导入或标记完成。'}})
    return result


def plan_guard_calls(calls):
    """Use the existing replayable repeat guard for invalid plan variants.

    Do not collapse valid updates or a batch containing business operations.
    Renaming an invalid field is not progress towards a saved working plan.
    """
    if not calls or any(c['name']!='update_task_plan' for c in calls):return calls
    normalized=[]
    for call in calls:
        try:
            WorkingPlanUpdate.model_validate(json.loads(call.get('arguments','')))
        except (ValueError,TypeError):
            normalized.append({**call,'arguments':'{"invalid_working_plan":true}'})
        else:normalized.append(call)
    return normalized


def stage_plan_calls(runtime,prepared,blocks,calls,assistant):
    """Plan notes and independent tool calls may share one model response.

    The caller checkpoints the complete assistant/tool pairing before effects.
    A plan update is not a dependency barrier for the paired business calls.
    """
    plans=[c for c in calls if c['name']=='update_task_plan']
    if not plans:return calls,False
    prepared.messages.append(copy.deepcopy(assistant))
    for call in plans:
        try:
            update = WorkingPlanUpdate.model_validate_json(call.get('arguments', ''))
            plan = WorkingPlan(explanation=update.explanation, steps=update.steps)
            state = runtime.task_execution_state
            plan_id = runtime.task_direct_plan_id
            if update.plan_id is not None or update.expected_plan_version is not None:
                target = runtime.task_plan_candidates.get(str(update.plan_id))
                if (state is not None or runtime.task_direct_plan is not None or target is None
                        or update.expected_plan_version != target['version']):
                    raise TaskError('TASK_WORKING_PLAN_TARGET_CHANGED')
                plan_id = str(update.plan_id)
            if state is not None:
                from .model_directed import update_working_plan
                runtime.task_execution_state = update_working_plan(state, plan.model_dump(mode='json'))
            runtime.task_direct_plan = plan
            runtime.task_direct_plan_owner = runtime.task_id
            runtime.task_direct_plan_id = plan_id
            result={'ok':True,'status':'recorded','instruction':'工作计划已记录；执行事实、权限和成果状态未改变。'}
        except TaskError as error:
            result = {'ok': False, 'code': str(error), 'state_unchanged': True,
                'instruction': '目标计划或版本不可用。核对当前候选及用户意图，不能为修改请求另建计划。'}
        except (ValueError,TypeError) as error:
            errors=error.errors(include_input=False,include_url=False,include_context=False)[:12] if isinstance(error,ValidationError) else [
                {'loc':[],'type':'json_object_required','msg':'arguments必须为JSON对象。'}]
            result={'ok':False,'code':'TASK_WORKING_PLAN_INVALID','state_unchanged':True,
                'errors':errors,'parameters':PLAN_SCHEMA['function']['parameters'],
                'instruction':'按parameters和errors修正参数；未保存任何计划，不得声称登记成功。不要猜测字段名或状态值。'}
        prepared.messages.append({'role':'tool','tool_call_id':call['id'],
            'content':json.dumps(result,ensure_ascii=False)})
        block={'type':'tool_step','tool_name':call['name'],'tool_call_id':call['id'],
            'presentation':'model_directed',
            'status':'completed' if result['ok'] else 'error','output':json.dumps(result,ensure_ascii=False)}
        for i,old in enumerate(blocks):
            if old.get('tool_call_id')==call['id']:blocks[i]=block;break
        else:blocks.append(block)
    return [c for c in calls if c['name']!='update_task_plan'],True


def stage_native_calls(runtime,prepared,calls,registry):
    candidate=runtime.task_execution_state
    errors={}
    for call in calls:
        if call['name'] not in NATIVE_TOOLS:continue
        try:
            args=json.loads(call.get('arguments',''))
            if not isinstance(args,dict):continue
            if call['name']=='generate_image':
                from services.tools.argument_validation import validate_model_image_arguments
                validate_model_image_arguments(registry.require(call['name']),args)
            parsed=ToolCall(call['id'],call['name'],args)
            context=replace(prepared.execution_context,call_id=call['id'])
            decision=ToolPolicy(registry).decide(call['name'],context,args)
            if decision.outcome=='deny':continue  # The original runtime returns the real denial.
            if decision.operation in {'read','analysis'} and call['name']!='code_execute':continue
            try:candidate=record_calls(candidate,runtime.task_bootstrap,[parsed],registry,
                input_sha256=digest(sorted(getattr(runtime,'task_direct_seen',()))))
            except TaskError as error:errors[call['id']]=(str(error),'本次调用未派发：'+str(error)+'。先读取原结果或核实已有操作，不能重复执行。')
        except (ValueError,TypeError):continue  # Native argument validation keeps its original correction path.
    if candidate is not None and runtime.task_execution_state is None:
        candidate=TaskState.model_validate({**candidate.model_dump(),
            'model_rounds_used':getattr(getattr(prepared,'budget',None),'turns_used',0)})
    plan=getattr(runtime,'task_direct_plan',None)
    if candidate is not None and plan is not None and candidate.working_plan!=plan:
        from .model_directed import update_working_plan
        candidate=update_working_plan(candidate,plan.model_dump(mode='json'))
    return candidate,errors


def retire_not_dispatched(state,call_ids):
    if state is None or not state.model_directed:return state
    retired=set(state.retired_call_steps)
    completed={r.step_id for r in state.results}
    for binding in state.call_bindings:
        if binding.call_id not in call_ids or binding.step_id in completed:continue
        operations=[o for o in state.operations if o.step_id==binding.step_id]
        if not operations or all(o.status=='failed_before_dispatch' for o in operations):retired.add(binding.step_id)
    if retired==set(state.retired_call_steps):return state
    return TaskState.model_validate({**state.model_dump(),'revision':state.revision+1,
        'retired_call_steps':(*state.retired_call_steps,*(s.id for s in state.steps if s.id in retired and s.id not in state.retired_call_steps))})


async def advance_results(runtime,prepared,*,reconcile_pending=False):
    """Import only native proofs, without model import/complete round trips."""
    state=runtime.task_execution_state
    if state is None or not state.model_directed:return state
    from .controller import TaskController
    native_store=getattr(runtime,'task_operation_store',None)
    if native_store is None:
        factory=getattr(runtime,'task_operation_store_factory',None)
        native_store=factory() if factory is not None else None
    class SnapshotReads:
        # All proofs are read against the same committed checkpoint while the
        # controller stages several results for one atomic save. Staged fact
        # revisions are not yet valid database read revisions.
        def __getattr__(self,name):
            if name not in {'effect_result','scheduled_receipt','native_write_receipt','erp_postcondition',
                    'reach_receipt','reconcile_file_restore','reconcile_file_delete'}:
                raise AttributeError(name)
            method=getattr(native_store,name)
            async def read(_candidate,*args,**kwargs):return await method(state,*args,**kwargs)
            return read
    async def stage(candidate,expected):return {'outcome':'saved'}
    controller=TaskController(state,owner_task_id=runtime.task_id,commit=stage,
        result_context=getattr(runtime,'task_result_context_provider',None) or prepared.execution_context,
        operation_store=SnapshotReads() if native_store is not None else None)
    retired=set(state.retired_call_steps)
    observations=getattr(runtime,'task_direct_results',{})
    for binding in state.call_bindings:
        result=observations.get(binding.call_id)
        ops=[o for o in state.operations if o.step_id==binding.step_id]
        if ((ops and all(o.status=='failed_before_dispatch' for o in ops))
                or (not ops and result is not None and result.execution.status=='not_started')):
            retired.add(binding.step_id)
    if retired!=set(state.retired_call_steps):
        controller.state=TaskState.model_validate({**state.model_dump(),'revision':state.revision+1,
            'retired_call_steps':(*state.retired_call_steps,*(s.id for s in state.steps if s.id in retired and s.id not in state.retired_call_steps))})
    failures=getattr(runtime,'task_direct_result_probe_failures',set())
    for operation in tuple(state.operations):
        if operation.step_id in controller.state.retired_call_steps:continue
        probe=(operation.id,operation.status,operation.receipt_ref)
        if probe in failures:continue
        before=controller.state
        try:
            if any(r.step_id==operation.step_id for r in controller.state.results):continue
            if operation.tool_name=='generate_image':continue  # The published child callback is authoritative.
            if operation.status!='completed' and reconcile_pending:
                name='read_task_scheduled_result' if operation.tool_name=='manage_scheduled_task' else 'read_task_write_result'
                if operation.tool_name in {'manage_scheduled_task','generate_video','file_delete','restore_file'}:
                    outcome=await controller.control(name,{'expected_revision':controller.state.revision,'operation_id':operation.id},
                        call_id='host-reconcile:'+operation.id)
                    if not outcome.result.get('ok'):raise TaskError(outcome.result.get('code','TASK_NATIVE_RESULT_UNVERIFIED'))
                    if outcome.result.get('native_terminal_failure'):
                        from .reducer import record_operation
                        controller.state=record_operation(controller.state,operation.id,owner_task_id=operation.owner_task_id,status='failed_after_dispatch')
                operation=next(o for o in controller.state.operations if o.id==operation.id)
            if operation.status!='completed':continue
            if operation.tool_name=='code_execute':
                store=controller.operation_store() if callable(controller.operation_store) else controller.operation_store
                # Use the existing scoped result reader and native sandbox proof.
                facts=await controller._code_result(operation.id)
                record=await store.effect_result(controller.state,operation.id)
                if record.get('result') is None:raise TaskError('TASK_SANDBOX_RESULT_UNVERIFIED')
                artifact=Artifact(id='sandbox-receipt-'+digest(operation.id)[:32],kind='receipt',
                    reference='sandbox-result:'+operation.id+':receipt',sha256=digest(record['result']),
                    source_task_id=operation.owner_task_id,source_call_id=operation.invocation_id,
                    delivery_task_id=UUID(str(runtime.task_id)),step_id=operation.step_id)
                controller.state=register_artifacts(controller.state,[artifact])
                ids=[artifact.id]
                if facts.get('result_status')=='success':
                    for output in facts.get('outputs',[]):
                        if output.get('kind')!='file':continue
                        try:file_artifact,_=await controller._code_result(operation.id,kind='file',index=output['payload_index'])
                        except TaskError as error:
                            issue={'operation_id':operation.id,'payload_index':output['payload_index'],'code':str(error)}
                            prepared.messages.append({'role':'system','content':'[Native result availability: facts, not authorization]\n'
                                '该执行已结束，但此文件未通过当前可用性核验，未登记为有效文件成果。请读取原结果并依据真实情况继续或说明，不能声称该文件已验证，也不要盲目重跑原操作。\n'
                                +json.dumps(issue,ensure_ascii=False)})
                            if not hasattr(runtime,'task_direct_result_issues'):runtime.task_direct_result_issues=[]
                            runtime.task_direct_result_issues.append(issue)
                            continue
                        controller.state=register_artifacts(controller.state,[file_artifact]);ids.append(file_artifact.id)
            else:
                outcome=await controller.control('import_task_receipt',
                    {'expected_revision':controller.state.revision,'operation_id':operation.id},call_id='host-import:'+operation.id)
                if not outcome.result.get('ok'):raise TaskError(outcome.result.get('code','TASK_NATIVE_RESULT_UNVERIFIED'))
                ids=[a.id for a in controller.state.artifacts if a.step_id==operation.step_id]
            controller.state=complete_step(controller.state,operation.step_id,ids,owner_task_id=runtime.task_id)
        except TaskError as error:
            controller.state=before
            failures.add(probe)
            issue={'operation_id':operation.id,'code':str(error)}
            prepared.messages.append({'role':'system','content':'[Native result verification: facts, not authorization]\n'
                '原操作的当前成果尚未核实，已保留执行与受理记录，未登记为成功成果。根据该事实读取原结果、补齐必要信息或如实说明；不能重复派发结果不明的操作。\n'
                +json.dumps(issue,ensure_ascii=False)})
            if not hasattr(runtime,'task_direct_result_issues'):runtime.task_direct_result_issues=[]
            runtime.task_direct_result_issues.append(issue)
    runtime.task_direct_result_probe_failures=failures
    return controller.state


async def bind_image_recipes(runtime,calls,registry):
    """Freeze actual resolved inputs, without interpreting or approving prose."""
    state=runtime.task_execution_state
    if state is None:return state,{}
    from .image_binding import bind_image_call
    from .image_resources import fingerprint
    from services.tools.argument_validation import parse_model_image_arguments
    resolver=getattr(runtime,'task_image_resource_resolver',None)
    prepare=getattr(resolver,'prepare_request',None)
    errors={}
    for raw in calls:
        if raw['name']!='generate_image':continue
        try:
            args=parse_model_image_arguments(registry.require('generate_image'),raw.get('arguments'))
            binding=bind_image_call(state,ToolCall(raw['id'],'generate_image',args),registry)
            if any(a.id==binding.planning_artifact_id for a in state.artifacts):continue
            if prepare is None:raise TaskError('TASK_IMAGE_REUSE_NATIVE_PREPARATION_REQUIRED')
            normalized,resolved,proof=await prepare(args,state,raw['id'],binding_required=False)
            body=json.dumps({'arguments_sha256':binding.arguments_sha256,'normalized':normalized,
                'resolved':fingerprint(resolved),'source_proof':proof},ensure_ascii=False,sort_keys=True,separators=(',',':'))
            import hashlib
            artifact=Artifact(id=binding.planning_artifact_id,kind='text',reference='image-input:'+raw['id'],
                sha256=hashlib.sha256(body.encode()).hexdigest(),inline_text=body,
                source_task_id=UUID(str(runtime.task_id)),delivery_task_id=UUID(str(runtime.task_id)),
                source_call_id=raw['id'],step_id=binding.step_id,validations=('artifact.exists','image.direct.recipe'))
            state=register_artifacts(state,[artifact])
        except (ValueError,TypeError,PermissionError,OSError) as error:
            code=str(error) if isinstance(error,TaskError) else 'TASK_IMAGE_INPUT_UNAVAILABLE'
            errors[raw['id']]=(code,'图片输入尚未核实，本次未派发：'+code)
    return state,errors


def end_candidate(state):
    """Physical outcomes, not an independent judgment of natural-language goals."""
    unfinished=[o for o in state.operations if o.status in {'reserved','accepted','uncertain'}]
    if unfinished:
        uncertain=any(o.status in {'reserved','uncertain'} or o.tool_name not in {'generate_image','generate_video','manage_scheduled_task'} for o in unfinished)
        status='partial' if uncertain else 'waiting_dependencies'
        if not uncertain and any(o.tool_name=='manage_scheduled_task' for o in unfinished):status='waiting_approval'
    elif any(o.status in {'failed_before_dispatch','failed_after_dispatch'} and o.step_id not in state.retired_call_steps for o in state.operations):status='partial'
    else:
        status='completed' if len(state.results)==len(state.steps)-len(state.retired_call_steps) else 'partial'
        if not state.results:status='partial'
    return TaskState.model_validate({**state.model_dump(),'revision':state.revision+1,'status':status})


async def reuse_images(runtime,calls):
    from services.agent.agent_result import AgentResult
    from .image_reuse import reuse,ReuseImageInput
    state=runtime.task_execution_state
    results=[];parts=[];errors={}
    if state is None:return state,results,parts,errors
    reader=getattr(runtime,'task_artifact_reader',None)
    resolver=getattr(runtime,'task_image_resource_resolver',None)
    if reader is None or not hasattr(reader,'reusable_images'):return state,results,parts,errors
    for raw in calls:
        if raw['name']!='generate_image':continue
        try:
            args=json.loads(raw.get('arguments',''))
            from .model_directed import call_binding
            step,_=call_binding(state,ToolCall(raw['id'],raw['name'],args))
            if any(o.step_id==step.id for o in state.operations):continue
            artifact,part=await reuse(state,ReuseImageInput(expected_revision=state.revision,arguments=args),
                reader=reader,resolver=resolver,owner_task_id=runtime.task_id,call_id=raw['id'])
            state=register_artifacts(state,[artifact])
            state=complete_step(state,step.id,[artifact.id],owner_task_id=runtime.task_id)
            results.append((raw,AgentResult('已复用真实发布的原图片，无新增生图。',source='native_image_reuse',
                metadata={'reused':True,'completed':True,'accepted':False,'task_id':str(artifact.source_task_id)}),False,'已复用已有图片'))
            parts.append(copy.deepcopy(part))
        except TaskError as error:
            if str(error) not in {'TASK_REUSABLE_IMAGE_NOT_FOUND','TASK_IMAGE_REUSE_REGENERATION_REQUEST'}:
                errors[raw['id']]=(str(error),'原成果或输入尚无法核实，本次未新增生成：'+str(error))
    return state,results,parts,errors


async def resume_from_model(runtime,prepared,blocks,calls,assistant,*,turn,model_round,build_checkpoint):
    """The main model chooses affiliation; the original fenced transfer commits it."""
    from .followup import FollowupObservation,apply
    source=followup_source(runtime)
    chosen=[c for c in calls if c['name']=='resume_task_execution']
    observation=None;error_code=None
    try:
        if source is None or runtime.task_execution_state is not None:raise TaskError('TASK_FOLLOWUP_SOURCE_CHANGED')
        observation=FollowupObservation.model_validate(json.loads(chosen[0]['arguments'])) if len(chosen)==1 else None
        if observation is None or observation.relation not in {'continue','amend'} or any(
            not q.strip() or q not in source['message'] for q in observation.evidence):
            raise TaskError('TASK_FOLLOWUP_SOURCE_EVIDENCE_REQUIRED')
    except (ValueError,TypeError) as error:
        observation=None
        error_code=str(error) if isinstance(error,TaskError) else 'TASK_FOLLOWUP_INPUT_INVALID'
    prepared.messages.append(copy.deepcopy(assistant))
    for call in calls:
        selected=observation is not None and call['id']==chosen[0]['id']
        result={'ok':selected,'status':'resuming' if selected else 'not_executed',
            'instruction':'先接回真实历史任务，再根据恢复上下文决定业务调用；本批未派发任何业务操作。'}
        if error_code:result['code']=error_code
        prepared.messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(result,ensure_ascii=False)})
        if error_code:
            blocks[:]=[b for b in blocks if b.get('tool_call_id')!=call['id']]
            blocks.append({'type':'tool_step','tool_name':call['name'],'tool_call_id':call['id'],
                'presentation':'model_directed','status':'error','output':json.dumps(result,ensure_ascii=False)})
    if observation is None:
        await runtime.safe_point(SafePoint.AFTER_TOOL,replay_payload=build_checkpoint(
            prepared.messages,blocks,turn,next_model_round=model_round))
        return
    await apply(runtime,prepared,blocks,observation.model_dump_json(),False,
        model_round=model_round,turn=turn,build_checkpoint=build_checkpoint)
