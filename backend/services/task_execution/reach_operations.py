"""Bind native Reach writes to one immutable user receipt requirement.

No new business executor, permissions or confirmations. Native partial/error
outcomes remain unresolved and never authorize another send.
"""
import asyncio
from uuid import UUID

from services.conversation_state import ConversationStopRequested
from services.handlers.chat.tool_lifecycle import ActorToolLifecycle
from services.tools.result import UncertainToolInvocationError
from services.tools.spec import thaw
from .contracts import Artifact,TaskError
from .operation_store import TaskOperationPersistenceError
from .reducer import digest,ready_steps

VALIDATOR='native.reach.receipt'


def bind_write(state,call,registry):
    spec=registry.get(call.name)
    if spec is None or spec.name!='agent_reach' or spec.capability!='platform.agent_reach':
        raise TaskError('TASK_WRITE_DOMAIN_UNSUPPORTED')
    if state.status!='active':raise TaskError('TASK_NOT_ACTIVE')
    # One mutation per original user obligation. Renaming/adding model steps
    # cannot manufacture a second effect slot for the same obligation.
    candidates=[]
    for step in ready_steps(state):
        if step.capability!=spec.capability:continue
        targets=[r for r in state.requirements if r.id in (*step.requirement_ids,*step.contributes_to)]
        if len(targets)==1 and targets[0].artifact_kind=='receipt' and targets[0].minimum==1:
            candidates.append((step,targets[0]))
    if len(candidates)!=1:raise TaskError('TASK_WRITE_RECEIPT_STEP_REQUIRED')
    step,target=candidates[0]
    key='write-'+digest([spec.capability,target.id])
    from .reducer import effect_version
    return step.id,key,effect_version(state,key)


def validate_receipt(proof,*,result=None,call=None):
    """The domain ledger is positive evidence; success text is insufficient."""
    try:
        UUID(proof['native_id']);UUID(proof['connection_id'])
        receipt=proof['receipt'];action=receipt['action']
        if proof['state']!='succeeded' or not isinstance(receipt,dict):raise ValueError()
        if action not in {'create_post','create_comment','set_like','upload_video'}:raise ValueError()
        if receipt.get('stage')=='backend_submitted':raise ValueError()
        if action!='set_like' and not receipt.get('remote_id') and not (proof['platform']=='xiaohongshu' and action=='create_comment' and receipt.get('confirmed') is True):raise ValueError()
        if action=='set_like' and not receipt.get('target'):raise ValueError()
        if call is not None:
            args=thaw(call.arguments)
            if (args.get('connection_id')!=proof['connection_id'] or args.get('platform')!=proof['platform']
                or args.get('action')!=action):raise ValueError()
            if action in {'set_like','create_comment'} and args.get('url') is not None and args['url']!=receipt.get('target'):raise ValueError()
            if action=='set_like' and 'liked' in receipt and args.get('liked')!=receipt['liked']:raise ValueError()
        if result is not None:
            if (result.kind!='agent' or result.status!='success' or result.error is not None or result.exception is not None
                or result.execution.status!='succeeded' or result.execution.cancelled
                or result.metadata.get('platform')!=proof['platform'] or result.metadata.get('receipt')!=receipt):raise ValueError()
    except (ValueError,KeyError,TypeError,AttributeError):raise TaskError('TASK_WRITE_RECEIPT_UNCONFIRMED') from None
    return {'native_id':proof['native_id'],'connection_id':proof['connection_id'],'platform':proof['platform'],'receipt':receipt}


class ReachOperationLifecycle(ActorToolLifecycle):
    def __init__(self,handler,context,*,registry,recovery_only=False):
        super().__init__(handler,context,recovery_only=recovery_only)
        self.registry=registry;self.runtime=handler._actor_runtime
        self.operation_id=None;self.binding=None

    def adopt(self,write):
        if write.state!=self.runtime.task_execution_state:
            self.runtime.task_execution_state=write.state
            self.runtime.committed_task_execution_revision=write.state.revision
            self.runtime.replay_save_sequence+=1

    def operation_store(self):
        if getattr(self.runtime,'task_operation_store',None) is None:
            factory=getattr(self.runtime,'task_operation_store_factory',None)
            if factory is None:raise TaskError('TASK_OPERATION_STORE_UNAVAILABLE')
            self.runtime.task_operation_store=factory()
        return self.runtime.task_operation_store

    def verify(self,call,decision):
        if (decision.operation!='business_write' or decision.outcome!='allow'
            or not self._enabled(decision)):raise PermissionError('TASK_OPERATION_ACTOR_REQUIRED')
        binding=bind_write(self.runtime.task_execution_state,call,self.registry)
        if self.binding is not None and binding!=self.binding:raise TaskError('TASK_WRITE_BINDING_CHANGED')
        self.binding=binding
        if not hasattr(self.runtime,'task_operation_lock'):self.runtime.task_operation_lock=asyncio.Lock()

    async def begin_write(self,call):
        step,key,version=self.binding
        try:
            write=await self.operation_store().begin(self.runtime.task_execution_state,step_id=step,effect_key=key,effect_version=version,call=call)
        except TaskOperationPersistenceError:raise ConversationStopRequested('task_operation_commit_unknown') from None
        except TaskError as error:
            if str(error)=='TASK_REVISION_STALE':raise ConversationStopRequested('task_operation_revision_changed') from None
            raise
        self.adopt(write)
        if (write.operation.status in {'reserved','uncertain','completed'} and write.outcome!='execute'
            and not (write.operation.status=='completed' and write.outcome=='replay')
            and callable(getattr(self.operation_store(),'reconcile_reach',None))):
            try:write=await self.operation_store().reconcile_reach(self.runtime.task_execution_state,operation_id=write.operation.id)
            except (TaskOperationPersistenceError,TaskError):raise ConversationStopRequested('task_operation_reconciliation_unconfirmed') from None
            self.adopt(write)
        return write

    def replay_result(self,write,call,context,decision):
        if write.operation.status!='completed':raise UncertainToolInvocationError('TASK_OPERATION_RECONCILIATION_REQUIRED')
        if write.outcome=='replay':return self._replayed(write.result,call,context,decision)
        if write.outcome=='reconciled':
            from dataclasses import replace
            from services.agent.agent_result import AgentResult
            from services.tools import ToolCall,ToolResult
            proof=validate_receipt(write.receipt)
            original=ToolCall(write.operation.invocation_id,call.name,thaw(call.arguments))
            source=replace(context,task_id=str(write.operation.owner_task_id))
            result=ToolResult.wrap(AgentResult('已核实原平台操作回执，复用原结果继续任务。',source='native_reach_reconciliation',
                metadata={'platform':proof['platform'],'receipt':proof['receipt'],'reconciled':True}),
                call=original,context=source,decision=decision)
            return result.reused(call=call,context=context,decision=decision,replayed=True)
        raise UncertainToolInvocationError('TASK_OPERATION_RECONCILIATION_REQUIRED')

    async def replay(self,call,context,decision):
        # This method runs before approval. It only reads an already reserved
        # slot; a fresh write must return to native ToolRuntime confirmation.
        if decision.operation!='business_write':return await super().replay(call,context,decision)
        # require_confirmation is not an execution grant, but permits lookup of
        # an existing result after the current Policy/resource checks.
        from dataclasses import replace
        if decision.outcome not in {'allow','require_confirmation'}:raise PermissionError('TASK_OPERATION_ACTOR_REQUIRED')
        self.verify(call,replace(decision,outcome='allow'))
        _,key,version=self.binding
        identity=digest([str(self.runtime.task_execution_state.root_task_id),key,version])
        existing=next((op for op in self.runtime.task_execution_state.operations if op.id==identity),None)
        if existing is None:
            if self.recovery_only:raise UncertainToolInvocationError('ACTOR_RECOVERY_UNRECORDED_EFFECT')
            return None
        if existing.status=='failed_before_dispatch':
            if existing.invocation_id==call.call_id and str(existing.owner_task_id)==str(context.task_id):
                return await super().replay(call,context,decision)
            if self.recovery_only:raise UncertainToolInvocationError('ACTOR_RECOVERY_UNRECORDED_EFFECT')
            return None  # A fresh retry still needs the original current approval.
        async with self.runtime.task_operation_lock:
            write=await self.begin_write(call)
            return self.replay_result(write,call,context,decision)

    async def begin(self,call,context,decision):
        self.verify(call,decision)
        async with self.runtime.task_operation_lock:
            write=await self.begin_write(call)
            if write.outcome!='execute':
                return self.replay_result(write,call,context,decision)
            self.operation_id=write.operation.id;self.call=call

    async def complete(self,result):
        if self.operation_id is None:return
        async with self.runtime.task_operation_lock:
            status='uncertain';reference=None
            try:
                from .operation_store import reach_rejection_candidate
                if (result.status=='success' and result.exception is None and result.error is None) or reach_rejection_candidate(result):
                    proof=await self.operation_store().reach_receipt(self.runtime.task_execution_state,self.operation_id)
                    operation=next(op for op in self.runtime.task_execution_state.operations if op.id==self.operation_id)
                    if (proof.get('task_id')!=str(operation.owner_task_id)
                        or proof.get('turn_id')!=str(operation.owner_turn_id)
                        or proof.get('tool_call_id')!=operation.invocation_id
                        or proof.get('args_hash')!=operation.arguments_sha256):raise TaskError('TASK_WRITE_RECEIPT_UNCONFIRMED')
                    if (reach_rejection_candidate(result) and proof.get('linked_native_id') is not None
                        and proof.get('state')=='failed' and proof.get('receipt',{}).get('stage')=='not_dispatched'
                        and proof.get('receipt',{}).get('code')==getattr(result.raw,'error_message',None)):
                        status='failed_before_dispatch'
                    else:
                        verified=validate_receipt(proof,result=result,call=self.call)
                        status='completed';reference='reach-operation:'+verified['native_id']
            except TaskError as error:
                if str(error)!='TASK_WRITE_RECEIPT_UNCONFIRMED':
                    raise ConversationStopRequested('task_operation_completion_unconfirmed') from None
                status='uncertain';reference=None
            except TaskOperationPersistenceError:raise ConversationStopRequested('task_operation_completion_unconfirmed') from None
            try:
                write=await self.operation_store().complete_invocation(self.runtime.task_execution_state,operation_id=self.operation_id,
                    result=result,status=status,receipt_ref=reference)
            except (TaskOperationPersistenceError,TaskError):raise ConversationStopRequested('task_operation_completion_unconfirmed') from None
            self.adopt(write)
            if status=='uncertain' and callable(getattr(self.operation_store(),'reconcile_reach',None)):
                try:write=await self.operation_store().reconcile_reach(self.runtime.task_execution_state,operation_id=self.operation_id)
                except (TaskOperationPersistenceError,TaskError):raise ConversationStopRequested('task_operation_reconciliation_unconfirmed') from None
                self.adopt(write)


def receipt_artifact(state,operation,proof,*,owner_task_id):
    from services.tools.result_payload import extension_of,decode_raw
    value=validate_receipt(proof)
    try:
        if (operation.status!='completed' or operation.receipt_ref!='reach-operation:'+value['native_id']
            or proof['task_id']!=str(operation.owner_task_id) or proof['turn_id']!=str(operation.owner_turn_id)
            or proof['tool_call_id']!=operation.invocation_id or proof['args_hash']!=operation.arguments_sha256):raise ValueError()
        recovered=proof.get('linked_native_id') is not None and proof['ledger_status'] in {'running','uncertain'}
        if recovered:
            UUID(proof['linked_native_id'])
        else:
            extension=extension_of(proof['tool_result']);raw=decode_raw(extension)
            audit=extension['audit'];execution=extension['execution']
            if (operation.status!='completed' or operation.receipt_ref!='reach-operation:'+value['native_id']
                or proof['ledger_status']!='succeeded' or extension['kind']!='agent' or extension['status']!='success'
                or raw.status!='success' or extension['error'] is not None or execution['status']!='succeeded' or execution['cancelled']
                or audit.get('actor_user_id')!=str(state.actor_user_id) or audit.get('workspace_owner_id')!=state.execution_identity.workspace_owner_id
                or audit.get('org_id')!=(str(state.org_id) if state.org_id else None)
                or audit.get('conversation_id')!=str(state.conversation_id)
                or audit.get('tool_name')!='agent_reach' or audit.get('tool_call_id')!=operation.invocation_id
                or audit.get('task_id')!=str(operation.owner_task_id) or proof['turn_id']!=str(operation.owner_turn_id)
                or raw.metadata.get('receipt')!=value['receipt'] or raw.metadata.get('platform')!=value['platform']
                or proof['task_id']!=str(operation.owner_task_id) or proof['tool_call_id']!=operation.invocation_id
                or proof['args_hash']!=operation.arguments_sha256 or audit.get('args_hash')!=operation.arguments_sha256):raise ValueError()
    except (ValueError,KeyError,TypeError,AttributeError):raise TaskError('TASK_WRITE_RECEIPT_UNCONFIRMED') from None
    artifact=Artifact(id='receipt-'+operation.id[:32],kind='receipt',reference='task-operation:'+operation.id,
        sha256=digest(value),source_task_id=operation.owner_task_id,delivery_task_id=UUID(str(owner_task_id)),
        source_call_id=operation.invocation_id,step_id=operation.step_id,validations=('artifact.exists',VALIDATOR))
    return artifact,value
