"""Read new task checkpoints without changing legacy user/context protocols."""
import copy
from decimal import Decimal,InvalidOperation
from .contracts import ExecutionIdentity, TaskError, TaskState


def restore_task_usage(payload,*,task_id):
    value=(payload or {}).get('_task_usage')
    if value is None:return None
    if (not isinstance(value,dict) or value.get('version')!=1 or value.get('owner_task_id')!=str(task_id)
            or not isinstance(value.get('usage'),dict)):
        raise TaskError('TASK_USAGE_REPLAY_INVALID')
    usage=value['usage']
    for key in ('prompt_tokens','completion_tokens','cached_tokens','cache_creation_input_tokens'):
        number=usage.get(key,0)
        if type(number) is not int or number<0:raise TaskError('TASK_USAGE_REPLAY_INVALID')
    if usage.get('api_credits') is not None:
        try:
            credits=Decimal(str(usage['api_credits']))
            if not credits.is_finite() or credits<0:raise ValueError()
        except (ValueError,InvalidOperation):raise TaskError('TASK_USAGE_REPLAY_INVALID') from None
    return {'prompt_tokens':0,'completion_tokens':0,**copy.deepcopy(usage)}


def restore_task_state(payload, *, conversation_id, actor_user_id, org_id, execution_scope):
    if not payload or '_task_execution' not in payload:
        return None
    state = TaskState.model_validate(payload['_task_execution'])
    identity = ExecutionIdentity(
        workspace_owner_id=execution_scope.workspace_owner_id,
        context_scope=execution_scope.context_scope,
        personal_context_allowed=execution_scope.personal_context_allowed,
        channel_scope_id=execution_scope.channel_scope_id,
    )
    if (str(state.conversation_id) != str(conversation_id) or str(state.actor_user_id) != str(actor_user_id)
            or str(state.org_id) != str(org_id)
            or state.execution_identity != identity):
        raise TaskError('TASK_EXECUTION_REPLAY_SCOPE_CHANGED')
    return state
