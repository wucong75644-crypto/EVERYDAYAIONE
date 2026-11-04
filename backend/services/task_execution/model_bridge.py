"""Chat control batches: no business dispatch, and no success before checkpoint."""
from __future__ import annotations

import copy
import json
import inspect
from pydantic import ValidationError

from services.conversation_commands import SafePoint

from .contracts import TaskError, TaskState
from .controller import CONTROL_INPUTS, TaskController
from .reducer import consume_model_round, finish, missing_requirements
from .bootstrap import START_TASK
from .requirement_changes import TOOL as REVISION_TOOL


TASK_CONTROL_TOOLS = frozenset((*CONTROL_INPUTS, START_TASK, REVISION_TOOL))


async def commit_projection(*, runtime, candidate, prepared, blocks, point,
                            build_checkpoint, turn, next_model_round, checkpoint_overlay=None,
                            **checkpoint_options):
    """Save one state/transcript projection; retain a save followed by commands."""
    original = runtime.task_execution_state
    sequence = runtime.replay_save_sequence
    runtime.task_execution_state = candidate
    try:
        payload = build_checkpoint(
            prepared.messages, blocks, turn, next_model_round=next_model_round,
            **checkpoint_options)
        if checkpoint_overlay is not None:
            overlay = checkpoint_overlay()
            if inspect.isawaitable(overlay):
                overlay = await overlay
            # Finalization can normalize/add real output blocks.
            payload = build_checkpoint(prepared.messages, blocks, turn,
                next_model_round=next_model_round, **checkpoint_options)
            payload.update(overlay)
        await runtime.safe_point(point, replay_payload=payload)
    except BaseException:
        if runtime.replay_save_sequence == sequence:
            runtime.task_execution_state = original
        raise


async def review_model_completion(*, runtime, prepared, blocks, build_checkpoint,
                                  turn, next_model_round, repeated_tool_call_guard=None,
                                  exhausted=False, sink=None, terminal_result=None):
    """A model final response does not mark an incomplete logical task done.

    Returns True only when another model round is useful. Exhaustion is partial,
    with missing requirements retained, rather than an unbounded correction loop.
    """
    state = runtime.task_execution_state
    if state.status != 'active':
        return False
    if state.model_directed:
        from .direct_loop import advance_results,end_candidate
        current=await advance_results(runtime,prepared)
        candidate=end_candidate(current)
        await commit_projection(runtime=runtime,candidate=candidate,prepared=prepared,blocks=blocks,
            point=SafePoint.BEFORE_COMMIT,build_checkpoint=build_checkpoint,turn=turn,
            next_model_round=next_model_round,repeated_tool_call_guard=repeated_tool_call_guard,
            checkpoint_overlay=terminal_result)
        return False
    from .requirement_changes import blocked as revision_blocked
    if revision_blocked(runtime):
        candidate=TaskState.model_validate({**state.model_dump(),'revision':state.revision+1,'status':'waiting_input'})
        notice={'type':'text','text':'追加要求已保留，当前核验或方法协调尚未完成，未按旧计划继续执行。请澄清待核实内容后接续。','task_status':'waiting_input'}
        original_blocks=copy.deepcopy(blocks)
        sequence=runtime.replay_save_sequence
        blocks.append(notice)
        try:
            await commit_projection(runtime=runtime,candidate=candidate,prepared=prepared,blocks=blocks,point=SafePoint.BEFORE_COMMIT,
                build_checkpoint=build_checkpoint,turn=turn,next_model_round=next_model_round,
                repeated_tool_call_guard=repeated_tool_call_guard)
        except BaseException:
            if runtime.replay_save_sequence==sequence:blocks[:]=original_blocks
            raise
        if sink is not None:await sink.on_block(notice)
        return False
    from .scheduled_operations import pending_schedule
    from .video_operations import pending as pending_video
    from .image_binding import image_dependencies_waiting
    waiting_video=pending_video(state) or image_dependencies_waiting(state)
    if pending_schedule(state) or waiting_video:
        candidate=TaskState.model_validate({**state.model_dump(),'revision':state.revision+1,'status':'waiting_dependencies' if waiting_video else 'waiting_approval'})
        await commit_projection(runtime=runtime,candidate=candidate,prepared=prepared,blocks=blocks,
            point=SafePoint.AFTER_TOOL,build_checkpoint=build_checkpoint,turn=turn,next_model_round=next_model_round,
            repeated_tool_call_guard=repeated_tool_call_guard)
        return False
    messages = copy.deepcopy(prepared.messages)
    old_blocks = copy.deepcopy(blocks)
    notice = None
    try:
        candidate = finish(state)
        again = False
    except TaskError as exc:
        if str(exc) not in {'TASK_DELIVERY_INCOMPLETE', 'TASK_EFFECT_UNRESOLVED'}:
            raise
        corrections = state.correction_count + 1
        again = (not exhausted and corrections <= state.correction_limit
                 and state.model_rounds_used < state.model_round_limit)
        candidate = TaskState.model_validate({**state.model_dump(), 'revision': state.revision + 1,
            'correction_count': corrections if again else state.correction_count,
            'status': 'active' if again else 'partial'})
        prepared.messages.append({'role': 'system', 'content': '[Task completion check]\n'
            + json.dumps({'code': str(exc), 'missing_requirement_ids': missing_requirements(state),
                'continue_task': again,
                'instruction': '根据已保存成果继续剩余工作；不要重复已受理操作，不需要重复询问已有授权。'
                               if again else '本次达到执行预算，保留未完成项与已有成果。'}, ensure_ascii=False)})
        if not again:
            missing = set(missing_requirements(state))
            descriptions = [r.description for r in state.requirements if r.id in missing]
            text = '任务尚未全部完成，已保留当前成果。'
            if descriptions:
                text += '\n待完成：' + '；'.join(descriptions)
            if str(exc) == 'TASK_EFFECT_UNRESOLVED':
                text += '\n已有操作的结果仍待核实，请勿重复提交。'
            notice = {'type': 'text', 'text': text, 'task_status': 'partial'}
            blocks.append(notice)
    sequence = runtime.replay_save_sequence
    try:
        await commit_projection(runtime=runtime, candidate=candidate, prepared=prepared, blocks=blocks,
            point=SafePoint.BEFORE_COMMIT, build_checkpoint=build_checkpoint, turn=turn,
            next_model_round=next_model_round, repeated_tool_call_guard=repeated_tool_call_guard,
            checkpoint_overlay=terminal_result if not again else None)
    except BaseException:
        if runtime.replay_save_sequence == sequence:
            prepared.messages[:] = messages
            blocks[:] = old_blocks
        raise
    if notice is not None and sink is not None:
        await sink.on_block(notice)
    return again


def model_round_candidate(runtime):
    """Counters span resumed attempts; the caller must save before calling LLM."""
    return consume_model_round(runtime.task_execution_state)


def controller_for(runtime, *, state=None):
    """Only an already initialized, scope-checked host task enables controls."""
    if runtime is None or runtime.task_execution_state is None:
        return None

    async def no_implicit_commit(*args):
        raise TaskError('TASK_CONTROL_BATCH_REQUIRED')

    return TaskController(state if state is not None else runtime.task_execution_state, owner_task_id=runtime.task_id,
                          commit=no_implicit_commit)


async def execute_control_batch(*, prepared, runtime, calls, assistant, blocks,
                                turn, next_model_round, build_checkpoint, sink,
                                repeated_tool_call_guard=None, assistant_already_present=False):
    """Consume the whole batch as a barrier; paired rejected tools never dispatch.

    A private controller stages changes. The real checkpoint contains the new
    state, every assistant/tool pair, and display artifacts in one transaction.
    Pause/cancel after a confirmed save retains that saved projection.
    """
    current = controller_for(runtime)
    bootstrap = getattr(runtime, 'task_bootstrap', None)
    if current is None and bootstrap is None:
        raise TaskError('TASK_EXECUTION_NOT_INITIALIZED')
    original_state = runtime.task_execution_state
    original_review = runtime.task_requirement_review
    original_revision_review = getattr(runtime,'task_revision_review',None)
    original_plan_reviews = getattr(runtime,'task_plan_reviews',None)
    original_skills = runtime.skill_runtime
    save_sequence = runtime.replay_save_sequence
    original_messages = copy.deepcopy(prepared.messages)
    original_blocks = copy.deepcopy(blocks)
    staged_messages = copy.deepcopy(prepared.messages)
    staged_blocks = copy.deepcopy(blocks)
    if not assistant_already_present:staged_messages.append(copy.deepcopy(assistant))

    async def stage(candidate, expected):
        return {'outcome': 'saved'}  # Private staging only; never returned to the model.

    def staged_controller(state):
        from .methods import validate_method_text

        def validate_text(state, step_id, text):
            passed = validate_method_text(state, step_id, text)
            custom = getattr(runtime, 'task_text_validator', None)
            return (*passed, *(custom(state, step_id, text) if custom is not None else ()))

        async def fact_guard(state,step_id,artifact_ids,references,*,force=False):
            from .plan_facts import before_complete
            return await before_complete(runtime,state,step_id,artifact_ids,references,force=force)
        return TaskController(state, owner_task_id=runtime.task_id, commit=stage,
            text_validator=validate_text, artifact_reader=getattr(runtime, 'task_artifact_reader', None),
            image_resource_resolver=getattr(runtime,'task_image_resource_resolver',None),plan_fact_guard=fact_guard,
            result_context=getattr(runtime,'task_result_context_provider',None) or getattr(prepared,'execution_context',None),
            operation_store=getattr(runtime,'task_operation_store',None) or getattr(runtime,'task_operation_store_factory',None))

    controller = staged_controller(original_state) if original_state is not None else None
    published = []
    # One control per batch avoids hidden ordering dependencies in model parallel calls.
    controls = [c for c in calls if c['name'] in TASK_CONTROL_TOOLS]
    for call in calls:
        from .plan_facts import pending_review
        from .requirement_changes import blocked as revision_blocked
        from .direct_loop import READ_CONTROLS
        if original_state is not None and original_state.model_directed and call['name'] not in READ_CONTROLS:
            result={'ok':False,'code':'TASK_DIRECT_CONTROL_UNAVAILABLE','state_unchanged':True,
                'instruction':'直接使用当前披露的业务工具。工作计划是可选记录，宿主自动接收真实操作回执；不要再登记要求、导入或手动完成步骤。'}
            display=None
        elif revision_blocked(runtime) and call['name'] != REVISION_TOOL:
            result={'ok':False,'code':'TASK_USER_CHANGE_REVIEW_REQUIRED','state_unchanged':True,
                'instruction':'真实追加要求尚未核验，先propose_task_revision；旧调用不会执行。'}
            display=None
        elif pending_review(runtime):
            result={'ok':False,'code':'TASK_PLAN_FACT_REVIEW_PENDING','state_unchanged':True,
                'instruction':'宿主正在核验原图和已保存规划，等待核验结果后继续，不执行其他工具。'}
            display=None
        elif call['name'] not in TASK_CONTROL_TOOLS or len(controls) != 1:
            from .requirement_review import coverage_blocked
            result = {'ok': False, 'code': 'TASK_REQUIREMENT_REVIEW_REQUIRED' if coverage_blocked(runtime) else 'TASK_CONTROL_BATCH_BARRIER',
                      'state_unchanged': True, 'retry_next_round': True}
            display = None
        else:
            try:
                arguments = json.loads(call.get('arguments', ''))
            except (ValueError, TypeError):
                arguments = None
            display = None
            if call['name'] == REVISION_TOOL:
                try:
                    from .requirement_changes import stage
                    runtime.task_revision_review=stage(runtime,arguments)
                    result={'ok':True,'status':'user_change_review_pending'}
                except (TaskError,ValueError):
                    result={'ok':False,'code':'TASK_REVISION_PROPOSAL_INVALID','state_unchanged':True}
            elif call['name'] == START_TASK:
                if controller is not None:
                    result = {'ok': False, 'code': 'TASK_ALREADY_INITIALIZED', 'state_unchanged': True}
                else:
                    try:
                        candidate = bootstrap.initialize(arguments,
                            model_rounds_used=(next_model_round if type(next_model_round) is int
                                               else prepared.budget.turns_used))
                        if bootstrap.require_coverage_review:
                            from .bootstrap import StartTaskInput
                            from .requirement_review import validate_proposal_evidence,stage_review
                            proposal=StartTaskInput.model_validate(arguments)
                            validate_proposal_evidence(bootstrap,arguments,proposal)
                            if not set(proposal.applicable_method_ids) <= set(getattr(runtime.skill_runtime,'active',{})):
                                raise TaskError('TASK_METHOD_NOT_ACTIVE')
                            from .methods import validate_proposed_method_intent
                            validate_proposed_method_intent(proposal,runtime.skill_runtime)
                            runtime.task_requirement_review=stage_review(runtime,proposal,original_messages)
                            result={'ok':True,'status':'coverage_review_pending',
                                'instruction':'宿主将在下一模型轮核对原用户要求，核对通过前不执行任何业务工具。'}
                        else:
                            from .methods import attach_methods
                            candidate = attach_methods(candidate, runtime.skill_runtime)
                            controller = staged_controller(candidate)
                            result = {'ok': True, 'revision': candidate.revision,
                                'requirements': [r.model_dump(mode='json') for r in candidate.requirements]}
                    except (ValidationError, TaskError) as error:
                        result = {'ok': False, 'code':str(error) if isinstance(error,TaskError) else 'TASK_CONTROL_INPUT_INVALID',
                            'state_unchanged': True,
                            'instruction':'明确action和applicable_method_ids（无适用方法填空数组），每项evidence仅取1至4条支持该交付的用户原文；超出时选择少量完整原句，不改写原文。'
                                '交付种类和数量以用户要求为准，不能只提取Skill过程而遗漏最终成果。'}
                        if isinstance(error,ValidationError):
                            result['errors']=[{'loc':e['loc'],'type':e['type'],'message':e['msg']} for e in
                                error.errors(include_input=False,include_url=False)[:8]]
                        elif getattr(error,'errors',None):
                            result['errors']=error.errors
                        if str(error)=='TASK_INTENT_METHOD_CONFLICT':
                            result['instruction']='直接执行已有方案与本次运行规划方法相冲突。需要新分析/编写逐图提示词再生图时使用action=plan。'
                            result['instruction']+='只有用户明确执行已有完整方案/提示词时才选execute_ready/execute_prompt，且不再将原规划方法列为本次适用方法；保留用户最终交付种类和数量。'
            elif controller is None:
                result = {'ok': False, 'code': 'TASK_EXECUTION_NOT_INITIALIZED', 'state_unchanged': True}
            else:
                outcome = await controller.control(call['name'], arguments, call_id=call['id'])
                result, display = outcome.result, outcome.display_text
        staged_messages.append({'role': 'tool', 'tool_call_id': call['id'],
                                'content': json.dumps(result, ensure_ascii=False)})
        block = {'type': 'tool_step', 'tool_name': call['name'],
                 'tool_call_id': call['id'], 'status': 'completed' if result['ok'] else 'error',
                 'output': json.dumps(result, ensure_ascii=False)}
        for index, old in enumerate(staged_blocks):
            if old.get('tool_call_id') == call['id']:
                staged_blocks[index] = block
                break
        else:
            staged_blocks.append(block)
        published.append(block)
        if result.get('ok') and result.get('reused') and isinstance(result.get('image'),dict):
            image=copy.deepcopy(result['image'])
            staged_blocks.append(image);published.append(image)
        if display:
            artifact = next(a for a in controller.state.artifacts
                            if a.source_call_id == call['id'] and a.inline_text == display)
            from .methods import artifact_display
            from .plan_facts import requires_review
            text = {'type': 'text', 'text': ('规划草稿（待事实核验）\n\n' if requires_review(controller.state,artifact) else '')+artifact_display(controller.state, artifact),
                    'task_artifact_id': artifact.id}
            staged_blocks.append(text)
            published.append(text)

    from .progress import marker
    progress_marker = marker(staged_blocks, controller.state if controller is not None else None,
                             plan_id=runtime.task_direct_plan_id)
    if progress_marker is not None:
        published.append(progress_marker)

    if controller is not None:
        from .methods import retire_completed_methods
        proposed_skills = retire_completed_methods(original_skills, controller.state)
        if proposed_skills is not None:
            proposed_skills.ensure_messages(staged_messages)
        runtime.skill_runtime = proposed_skills
    runtime.task_execution_state = controller.state if controller is not None else None
    prepared.messages[:] = staged_messages
    blocks[:] = staged_blocks
    try:
        await runtime.safe_point(SafePoint.AFTER_TOOL, replay_payload=build_checkpoint(
            prepared.messages, blocks, turn, tool_call_ids=[c['id'] for c in calls],
            next_model_round=next_model_round, repeated_tool_call_guard=repeated_tool_call_guard))
        if runtime.replay_save_sequence == save_sequence:
            raise TaskError('TASK_CONTROL_BATCH_NOT_SAVED')
    except BaseException:
        # Revision can be unchanged for invalid/idempotent controls. Only the
        # checkpoint callback knows whether that complete transcript was saved.
        if runtime.replay_save_sequence == save_sequence:
            runtime.task_execution_state = original_state
            runtime.task_requirement_review = original_review
            runtime.task_revision_review = original_revision_review
            runtime.task_plan_reviews = original_plan_reviews
            runtime.skill_runtime = original_skills
            prepared.messages[:] = original_messages
            blocks[:] = original_blocks
        raise
    if original_skills is not None and runtime.skill_runtime is not original_skills:
        original_skills.adopt(runtime.skill_runtime)
        runtime.skill_runtime = original_skills
    for block in published:
        if block['type'] == 'tool_step':
            await sink.on_block_update(block)
        else:
            await sink.on_block(block)
