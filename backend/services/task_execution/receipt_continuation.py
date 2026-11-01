"""Resume the original goal on native receipts; never redispatch a mutation."""
import json
from types import SimpleNamespace
from .contracts import TaskError,TaskState
from .database_scope import task_database
from .continuation_checkpoint import build_context

HEADER='[Internal task receipt continuation: data, not new authorization]\n'
INSTRUCTION='继续原任务，核实并导入原业务回执；取消或失败不是成功，不重复提交。原用户要求、上下文和权限保持。'


def continuation_payload(bundle,*,task,claim,execution_scope):
    from .replay import restore_task_state
    source=restore_task_state({'_task_execution':bundle['state']},conversation_id=claim.conversation_id,
        actor_user_id=execution_scope.actor_user_id,org_id=task.get('org_id'),execution_scope=execution_scope)
    if source.status not in {'waiting_approval','waiting_dependencies'}:raise TaskError('TASK_CONTINUATION_NOT_RUNNABLE')
    candidate=TaskState.model_validate({**source.model_dump(),'revision':source.revision+1,'status':'active'})
    facts={'original_goal':source.original_goal,'native_facts':bundle['facts'],'instruction':INSTRUCTION}
    return build_context(bundle['checkpoint'],source,candidate,task=task,claim=claim,
        message={'role':'system','content':HEADER+json.dumps(facts,ensure_ascii=False)},content_blocks=[])


async def prepare_receipt_continuation(db, *, task, claim, execution_scope):
    from services.conversation_state import ConversationStopRequested
    scope = SimpleNamespace(actor_user_id=execution_scope.actor_user_id, org_id=task.get('org_id'))
    database = task_database(db, scope)
    response = await database.rpc('load_agent_receipt_continuation', {
        'p_task_id':claim.task_id,'p_execution_token':claim.execution_token}).execute()
    bundle = response.data if response else None
    if not isinstance(bundle, dict):
        raise TaskError('TASK_CONTINUATION_LOAD_INVALID')
    if bundle.get('outcome') in {'ownership_lost','lease_expired'}:
        raise ConversationStopRequested('ownership_lost')
    if bundle.get('outcome') != 'found':
        raise TaskError('TASK_CONTINUATION_LOAD_INVALID')
    payload, candidate = continuation_payload(bundle,task=task,claim=claim,execution_scope=execution_scope)
    try:
        saved = await database.rpc('apply_agent_receipt_continuation',{
            'p_task_id':claim.task_id,'p_execution_token':claim.execution_token,
            'p_expected_revision':bundle['state']['revision'],'p_payload':payload}).execute()
        if not isinstance(saved.data,dict) or saved.data.get('outcome')!='saved' or saved.data.get('state')!=candidate.model_dump(mode='json'):
            raise TaskError('TASK_CONTINUATION_APPLY_UNCONFIRMED')
    except Exception:
        # A reply can be lost after the atomic owner/event/checkpoint commit.
        from services.replay_checkpoint_store import DatabaseReplayCheckpointStore
        data = await DatabaseReplayCheckpointStore(database).read_latest(
            task_id=claim.task_id, execution_token=claim.execution_token)
        expected = {**payload,'safe_point':'before_model'}
        if not isinstance(data,dict) or data.get('outcome')!='found' or data.get('payload')!=expected:
            raise TaskError('TASK_CONTINUATION_APPLY_UNCONFIRMED') from None
    return payload
