"""Read-only public progress. Native facts stay in their original ledgers."""
from .contracts import TaskError, TaskState
from .reducer import missing_requirements, ready_steps


def marker(blocks, state):
    if state is None or state.image_binding_version < 3 or state.execution_identity.context_scope != 'user':
        return None
    root = str(state.root_task_id)
    if any(b.get('type') == 'task_progress' and b.get('root_task_id') == root for b in blocks):
        return None
    block = {'type': 'task_progress', 'root_task_id': root}
    if state.model_directed:
        block['presentation'] = 'model_directed'
    blocks.append(block)
    return block


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
    state = TaskState.model_validate(data['state'])
    if (str(state.root_task_id) != str(root_task_id) or str(state.actor_user_id) != str(user_id)
        or (str(state.org_id) if state.org_id else None) != (str(org_id) if org_id else None)):
        raise TaskError('TASK_PROGRESS_SCOPE_MISMATCH')
    return project(state)
