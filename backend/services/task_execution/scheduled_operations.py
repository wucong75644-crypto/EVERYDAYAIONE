"""Scheduled definitions use the original form, ChangeSet and commit receipt.

Acceptance is never delivery. Looking up an existing request cannot authorize a
second submission, or bypass the original form/ChangeSet approval workflow.
"""
import asyncio
from uuid import UUID

from services.conversation_state import ConversationStopRequested
from services.tools.result import UncertainToolInvocationError
from services.tools.spec import thaw
from .contracts import Artifact,TaskError
from .operation_store import TaskOperationPersistenceError
from .reach_operations import ReachOperationLifecycle
from .reducer import digest,ready_steps

VALIDATOR='native.scheduled.receipt'
ACTIONS=frozenset({'create','update','pause','resume','delete'})


def binding(state,call):
    if state.status!='active':raise TaskError('TASK_NOT_ACTIVE')
    if call.name!='manage_scheduled_task' or thaw(call.arguments).get('action') not in ACTIONS:
        raise TaskError('TASK_SCHEDULED_MUTATION_REQUIRED')
    if state.model_directed:
        from .model_directed import operation_binding
        return operation_binding(state,call)
    candidates=[]
    for step in ready_steps(state):
        targets=[r for r in state.requirements if r.id in (*step.requirement_ids,*step.contributes_to)]
        if (step.capability=='platform.manage_scheduled_task' and len(targets)==1
            and targets[0].artifact_kind=='receipt' and targets[0].minimum==1):candidates.append((step,targets[0]))
    if len(candidates)!=1:raise TaskError('TASK_WRITE_RECEIPT_STEP_REQUIRED')
    step,target=candidates[0]
    key='scheduled-'+digest(['platform.manage_scheduled_task',target.id])
    from .reducer import effect_version
    return step.id,key,effect_version(state,key)


def request_key(operation_id):return 'agent-task-scheduled:'+operation_id


async def native_request_key(executor,call_id,arguments):
    """Resolve a reserved slot under the current Actor fence, never from JSON."""
    from .contracts import TaskState
    state=getattr(executor,'_task_execution_state',None)
    if not isinstance(state,TaskState) or state.image_binding_version<3:return None
    from services.tool_invocation_store import hash_tool_arguments
    response=await asyncio.to_thread(lambda:executor.db.rpc('agent_task_scheduled_request_key',{
        'p_task_id':str(executor._task_id),'p_execution_token':str(executor.image_execution_token),
        'p_call_id':call_id,'p_action':arguments.get('action'),'p_args_hash':hash_tool_arguments(arguments)}).execute())
    value=response.data if response else None
    if not isinstance(value,str) or not value.startswith('agent-task-scheduled:') or len(value)!=85:
        raise PermissionError('TASK_SCHEDULED_NATIVE_BINDING_REQUIRED')
    return value


def receipt_value(state,operation,proof):
    try:
        if operation.tool_name!='manage_scheduled_task' or operation.capability!='platform.manage_scheduled_task':raise ValueError()
        if (proof['task_id']!=str(operation.owner_task_id) or proof['turn_id']!=str(operation.owner_turn_id)
            or proof['tool_call_id']!=operation.invocation_id or proof['args_hash']!=operation.arguments_sha256
            or proof['actor_user_id']!=str(state.actor_user_id) or proof['org_id']!=str(state.org_id)
            or proof['conversation_id']!=str(state.conversation_id)):raise ValueError()
        receipt=proof['receipt'];UUID(receipt['id']);UUID(receipt['change_set_id']);UUID(receipt['task_id'])
        action=receipt['operation']
        if (action not in ACTIONS or action!=proof['operation'] or receipt['change_set_id']!=proof['change_set_id']
            or receipt['task_id']!=proof['resource_id'] or receipt['org_id']!=str(state.org_id)
            or receipt['result']['task_id']!=receipt['task_id']
            or receipt['result']['outcome']!={'create':'created','update':'updated','pause':'paused','resume':'resumed','delete':'deleted'}[action]
            or receipt['new_revision']!=receipt['result']['new_revision']):raise ValueError()
    except (ValueError,KeyError,TypeError,AttributeError):raise TaskError('TASK_SCHEDULED_RECEIPT_UNCONFIRMED') from None
    return receipt


class ScheduledOperationLifecycle(ReachOperationLifecycle):
    def _enabled(self,decision):
        h=self.handler
        if (not getattr(h,'_actor_enabled',False) or self.store is None or not getattr(h,'_actor_turn_id',None)
            or not getattr(h,'_actor_execution_token',None)):raise PermissionError('TASK_OPERATION_ACTOR_REQUIRED')
        return True

    def verify(self,call,decision):
        if decision.operation not in {'proposal','business_write'} or decision.outcome!='allow' or not self._enabled(decision):
            raise PermissionError('TASK_OPERATION_ACTOR_REQUIRED')
        current=binding(self.runtime.task_execution_state,call)
        if self.binding is not None and self.binding!=current:raise TaskError('TASK_WRITE_BINDING_CHANGED')
        self.binding=current
        if not hasattr(self.runtime,'task_operation_lock'):self.runtime.task_operation_lock=asyncio.Lock()

    async def begin_write(self,call):
        step,key,version=self.binding
        try:write=await self.operation_store().begin(self.runtime.task_execution_state,step_id=step,effect_key=key,effect_version=version,call=call)
        except (TaskOperationPersistenceError,TaskError) as error:
            if isinstance(error,TaskOperationPersistenceError) or str(error)=='TASK_REVISION_STALE':
                raise ConversationStopRequested('task_operation_commit_unconfirmed') from None
            raise
        self.adopt(write)
        return write

    def replay_result(self,write,call,context,decision):
        if write.operation.status not in {'accepted','completed'} or write.outcome!='replay':
            raise UncertainToolInvocationError('TASK_SCHEDULED_RECONCILIATION_REQUIRED')
        return self._replayed(write.result,call,context,decision)

    async def replay(self,call,context,decision):
        from dataclasses import replace
        if decision.outcome not in {'allow','require_confirmation'}:raise PermissionError('TASK_OPERATION_ACTOR_REQUIRED')
        self.verify(call,replace(decision,outcome='allow'))
        _,key,version=self.binding
        existing=next((op for op in self.runtime.task_execution_state.operations if op.effect_key==key and op.effect_version==version),None)
        if existing is None:
            if self.recovery_only:raise UncertainToolInvocationError('ACTOR_RECOVERY_UNRECORDED_EFFECT')
            return None
        async with self.runtime.task_operation_lock:return self.replay_result(await self.begin_write(call),call,context,decision)

    async def complete(self,result):
        if self.operation_id is None:return
        status='uncertain';reference=None
        # Original form bytes are a recoverable UI result, not a business receipt.
        if (result.kind=='form' and result.exception is None and result.execution.status=='succeeded'
            and not result.execution.cancelled and result.raw.form.get('form_type') in {'scheduled_task_create','scheduled_task_update'}
            and result.raw.form.get('form_id')):
            status='accepted';reference='scheduled-form:'+result.raw.form['form_id']
        elif (result.kind=='agent' and result.status=='success' and result.exception is None
            and result.execution.status=='succeeded' and not result.execution.cancelled):
            # The host's native result can acknowledge a candidate. Its own
            # invocation is not saved yet, so domain lookup belongs to the later
            # read control, after the original ledger and UI pair are durable.
            try:
                identity=str(UUID(result.metadata['change_set']['id']))
                status='accepted';reference='scheduled-change:'+identity
            except (ValueError,TypeError,KeyError):pass
        async with self.runtime.task_operation_lock:
            try:write=await self.operation_store().complete_invocation(self.runtime.task_execution_state,
                operation_id=self.operation_id,result=result,status=status,receipt_ref=reference)
            except (TaskOperationPersistenceError,TaskError):raise ConversationStopRequested('task_operation_completion_unconfirmed') from None
            self.adopt(write)


def artifact_from_receipt(state,operation,proof,*,owner_task_id):
    receipt=receipt_value(state,operation,proof)
    if operation.status!='completed' or operation.receipt_ref!='scheduled-receipt:'+receipt['id']:
        raise TaskError('TASK_SCHEDULED_RECEIPT_UNCONFIRMED')
    checksum=digest(receipt)
    artifact=Artifact(id='scheduled-'+digest([operation.id,checksum])[:32],kind='receipt',
        reference=operation.receipt_ref,sha256=checksum,source_task_id=operation.owner_task_id,
        delivery_task_id=UUID(str(owner_task_id)),source_call_id=operation.invocation_id,step_id=operation.step_id,
        validations=('artifact.exists',VALIDATOR))
    return artifact,receipt


def pending_schedule(state,*,force=False):
    """Waiting is saved after the original tool/UI pair, never at acceptance."""
    from .contracts import TaskState
    if not isinstance(state,TaskState) or state.image_binding_version<3 or state.status!='active':return False
    pending={op.step_id for op in state.operations if op.tool_name=='manage_scheduled_task' and op.status in {'reserved','accepted','uncertain'}}
    if not pending:return False
    from .image_binding import paid_work_blocked
    historical_wait=bool(state.requirements_history and pending-{s.id for s in state.steps} and paid_work_blocked(state))
    return force or historical_wait or all(step.id in pending or step.capability=='platform.generate_image' for step in ready_steps(state))


def negative_fact(proof):
    return proof.get('receipt') is None and (proof.get('form_status')=='cancelled'
        or proof.get('change_set_status') in {'cancelled','rejected','expired','failed','conflicted'})
