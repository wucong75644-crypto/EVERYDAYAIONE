"""Read-only public progress. Native facts stay in their original ledgers."""
from .contracts import TaskError, TaskState
from .reducer import missing_requirements, ready_steps


def _marker(blocks, root, *, plan=None, model_directed=False, plan_id=None):
    from .reducer import digest
    block = {'type': 'task_progress', 'root_task_id': str(root)}
    if model_directed:
        block.update(presentation='model_directed', plan_revision=digest(plan.model_dump(mode='json')))
    previous = next((b for b in blocks if b.get('type') == 'task_progress'
                     and b.get('root_task_id') == str(root)), None)
    if model_directed and (plan_id or (previous and previous.get('plan_id'))):
        block['plan_id'] = plan_id or previous['plan_id']
    if previous == block:
        return None
    if previous is not None:
        previous.clear()
        previous.update(block)
    else:
        blocks.append(block)
    return block


def marker(blocks, state, *, plan_id=None):
    if state is None or state.image_binding_version < 3 or state.execution_identity.context_scope != 'user':
        return None
    if state.model_directed and not (state.working_plan and state.working_plan.steps):
        return None  # Native receipts alone are not a user-facing work plan.
    return _marker(blocks, state.root_task_id, plan=state.working_plan, model_directed=state.model_directed, plan_id=plan_id)


def plan_marker(blocks, runtime):
    from .model_directed import enabled
    if not enabled(runtime):
        return None
    plan = getattr(runtime, 'task_direct_plan', None)
    bootstrap = runtime.task_bootstrap
    state = runtime.task_execution_state
    identity = state.execution_identity if state else bootstrap.execution_identity if bootstrap else None
    if not (plan is not None and identity and identity.context_scope == 'user'):
        return None
    root = state.root_task_id if state else bootstrap.task_id
    if not plan.steps and not runtime.task_direct_plan_id and not any(b.get('type') == 'task_progress' and b.get('root_task_id') == str(root) for b in blocks):
        return None
    return _marker(blocks, root, plan=plan, model_directed=True, plan_id=runtime.task_direct_plan_id)


def needs_verification(operation):
    return operation.status in {'reserved','uncertain'} or (operation.status=='accepted' and operation.tool_name=='erp_execute')


def project(state: TaskState):
    done = {r.step_id for r in state.results}
    ready = {s.id for s in ready_steps(state)}
    steps = []
    for step in state.steps:
        operations = [o for o in state.operations if o.step_id == step.id]
        if any(needs_verification(o) for o in operations):
            status = 'needs_verification'
        elif any(o.status == 'accepted' for o in operations):
            status = 'waiting_result'
        elif step.id in done:
            status = 'completed'
        elif state.status in {'paused', 'cancelled'}:
            status = state.status
        elif any(o.status == 'failed_after_dispatch' for o in operations):
            status = 'needs_attention'
        elif step.id in ready:
            status = 'ready'
        else:
            status = 'waiting_dependency'
        steps.append({'id': step.id, 'title': step.title, 'status': status})
    result = {
        'root_task_id': str(state.root_task_id), 'revision': state.revision,
        'status': state.status, 'steps': steps,
        'completed_steps': sum(s['status'] == 'completed' for s in steps),
        'total_steps': len(steps),
        'completed_requirements': len(state.requirements) - len(missing_requirements(state)),
        'total_requirements': len(state.requirements),
        'pending_results': sum(o.status == 'accepted' and not needs_verification(o) for o in state.operations),
        'unconfirmed_results': sum(needs_verification(o) for o in state.operations),
    }
    if state.model_directed:
        image_steps = [s for s in state.steps if s.capability == 'platform.generate_image' and s.id not in state.retired_call_steps]
        result.update(presentation='model_directed', image_jobs={
            'completed': sum(s.id in done for s in image_steps), 'total': len(image_steps)})
        if state.working_plan is not None:
            result['working_plan'] = state.working_plan.model_dump(mode='json')
    return result


def read(db, *, root_task_id, message_id, user_id, org_id):
    from core.db_scope import database_scope_from_client, DatabaseAccessKind
    from core.exceptions import NotFoundError
    from .user_control import scope_database
    current = database_scope_from_client(db)
    if current is not None and (
        current.actor_user_id != str(user_id) or current.org_id != (str(org_id) if org_id else None)
        or current.access_kind != DatabaseAccessKind.RUNTIME
    ):
        raise NotFoundError('任务进度不存在或不可访问')
    database = scope_database(db, str(user_id), str(org_id) if org_id else None)
    result = database.rpc('read_agent_task_progress', {
        'p_root_task_id': str(root_task_id), 'p_message_id': str(message_id),
    }).execute()
    data = result.data if result else None
    if not isinstance(data, dict) or data.get('outcome') != 'found':
        raise NotFoundError('任务进度不存在或不可访问')
    if data.get('plan_only') is True:
        from .contracts import WorkingPlan
        try:
            plan = WorkingPlan.model_validate(data['working_plan'])
        except ValueError:
            raise NotFoundError('任务进度不存在或不可访问') from None
        if (data.get('root_task_id') != str(root_task_id) or data.get('actor_user_id') != str(user_id)
            or data.get('org_id') != (str(org_id) if org_id else None)):
            raise TaskError('TASK_PROGRESS_SCOPE_MISMATCH')
        status = ('paused' if data['checkpoint_status'] == 'paused' else
                  {'completed': 'completed', 'failed': 'failed', 'cancelled': 'cancelled'}.get(data['task_status'], 'active'))
        return dict(root_task_id=str(root_task_id), revision=data['revision'], status=status,
            presentation='model_directed', plan_only=True, working_plan=plan.model_dump(mode='json'),
            completed_steps=0, total_steps=0, completed_requirements=0, total_requirements=0,
            pending_results=0, unconfirmed_results=0, steps=[])
    state = TaskState.model_validate(data['state'])
    if (str(state.root_task_id) != str(root_task_id) or str(state.actor_user_id) != str(user_id)
        or (str(state.org_id) if state.org_id else None) != (str(org_id) if org_id else None)):
        raise TaskError('TASK_PROGRESS_SCOPE_MISMATCH')
    return project(state)
