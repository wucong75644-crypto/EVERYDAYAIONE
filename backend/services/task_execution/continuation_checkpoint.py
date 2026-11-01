"""Original Actor continuation context, shared by trusted domain adapters."""
import copy
from uuid import UUID
from .contracts import ContinuationInput,TaskError


def build_context(checkpoint,source,candidate,*,task,claim,message,content_blocks,event_artifact_ids=()):
    payload = copy.deepcopy(checkpoint)
    from .user_changes import restore
    changes = restore(checkpoint, root_task_id=source.root_task_id, owner_task_id=task['id'],
        inherited_root_task_id=source.root_task_id)
    if changes is not None and any(not i.delivered for i in changes.items) and candidate.status == 'completed':
        from .contracts import TaskState
        candidate = TaskState.model_validate({**candidate.model_dump(), 'status': 'active'})
    from .pending_batch import pending_calls
    if pending_calls(payload['messages'])[0]:
        raise TaskError('TASK_CONTINUATION_PENDING_SOURCE_BATCH')
    payload.pop('checkpoint_kind', None)
    for key in ('result_content','usage','credits_cost','tool_digest','_task_bootstrap'):
        payload.pop(key, None)
    payload.pop('_task_usage',None)  # Source owner has already settled its model usage.
    payload.pop('_task_read_arguments',None)  # Native argument witnesses belong to their original Actor owner.
    payload['content_blocks'] = copy.deepcopy(content_blocks)
    payload['_task_execution'] = candidate.model_dump(mode='json')
    payload['safe_point'] = 'before_model'
    payload['_task_execution_expected_revision'] = source.revision
    incoming = ContinuationInput(logical_task_id=source.root_task_id,
        event_id=UUID(task['delivery_context']['event_id']), expected_revision=source.revision,
        conversation_id=source.conversation_id, actor_user_id=source.actor_user_id,
        execution_identity=source.execution_identity, org_id=source.org_id, branch_id=source.branch_id,
        source_message_ids=source.source_message_ids, base_context_revision=claim.base_context_revision,
        through_message_id=UUID(claim.context_through_message_id) if claim.context_through_message_id else None,
        event_artifact_ids=event_artifact_ids)
    payload['_continuation_input'] = incoming.model_dump(mode='json')
    payload['messages'].append(copy.deepcopy(message))
    # Keep source method versions, identities and ceilings. Only rebind the
    # existing workflow's current execution window, as its native protocol does.
    composition = payload.get('_skill_composition')
    if composition is not None:
        payload['_skill_composition_expected_revision'] = composition['revision']
        workflow = payload.get('_ecom_workflow')
        if workflow is not None:
            workflow['window_task_id'] = claim.task_id
            composition['workflow_links']['ecommerce'] = copy.deepcopy(workflow)
            composition['revision'] += 1
        payload['skill_runtime']['composition'] = copy.deepcopy(composition)
    return payload, candidate

