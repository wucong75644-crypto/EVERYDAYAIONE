"""Native business dispatch proof, independent of response/UI formatting."""
import asyncio
from uuid import UUID

from services.conversation_state import ConversationStopRequested
from .contracts import Artifact,TaskError,TaskState
from .operation_store import TaskOperationPersistenceError
from .reducer import digest,ready_steps
from .scheduled_operations import ScheduledOperationLifecycle

VALIDATOR='native.write.receipt'
# Completion semantics are admitted per native method, never inferred from a
# generic HTTP success envelope. Other original write methods still execute
# under the same journal, but remain accepted until a domain adapter proves
# their final result (including method-specific partial/async protocols).
COMPLETION_METHODS=frozenset({'erp.item.tag.add'})
def erp_write_dispatch_enabled():
    from core.config import get_settings
    return get_settings().agent_task_erp_write_enabled


def require_erp_write_dispatch():
    if not erp_write_dispatch_enabled():raise TaskError('TASK_ERP_WRITE_ADAPTER_DEFERRED')


ERP_TOOLS={'basic':'erp_info_query','product':'erp_product_query','trade':'erp_trade_query','aftersales':'erp_aftersales_query',
    'warehouse':'erp_warehouse_query','purchase':'erp_purchase_query','distribution':'erp_execute'}


def binding(state,call,registry,*,permitted=frozenset({'erp_execute'}),artifact_kinds=frozenset({'receipt'})):
    spec=registry.require(call.name)
    if call.name not in permitted or spec.capability!={'erp_execute':'platform.erp_execute','restore_file':'platform.restore_file','file_delete':'workspace.file.delete','generate_video':'platform.generate_video'}.get(call.name) or state.status!='active':
        raise TaskError('TASK_NATIVE_WRITE_DOMAIN_UNSUPPORTED')
    if state.model_directed:
        from .model_directed import operation_binding
        return operation_binding(state,call)
    candidates=[]
    for step in ready_steps(state):
        targets=[r for r in state.requirements if r.id in (*step.requirement_ids,*step.contributes_to)]
        if step.capability==spec.capability and len(targets)==1 and targets[0].artifact_kind in artifact_kinds and targets[0].minimum==1:
            candidates.append((step,targets[0]))
    if len(candidates)!=1:raise TaskError('TASK_WRITE_RECEIPT_STEP_REQUIRED')
    step,target=candidates[0]
    key='write-'+digest([spec.capability,target.id])
    from .reducer import effect_version
    return step.id,key,effect_version(state,key)


class NativeWriteSession:
    def __init__(self,executor,bound,intent,arguments=None):
        self.executor=executor;self.bound=bound;self.intent=intent;self.dispatched=False;self.arguments=arguments

    async def observe(self,phase,*,tool_name,action,entry,parameters=None,response=None,connection_fingerprint=None):
        from services.kuaimai.registry import TOOL_REGISTRIES
        expected=ERP_TOOLS.get(self.intent.get('category'))
        current=TOOL_REGISTRIES.get(expected,{}).get(self.intent.get('action'))
        if tool_name!=expected or action!=self.intent.get('action') or current is not entry or not entry.is_write:
            raise TaskError('TASK_NATIVE_WRITE_METHOD_CHANGED')
        if phase=='dispatched':
            require_erp_write_dispatch()
            from .erp_recovery import request_evidence
            value={'method':entry.method,'parameters_sha256':digest(parameters),'tool_name':tool_name,'action':action,
                'request_evidence':request_evidence(entry.method,parameters)}
            from .erp_postconditions import product_expectation,original_expectation
            postcondition=product_expectation(entry.method,parameters)
            if postcondition is not None and connection_fingerprint is not None and original_expectation(entry.method,self.arguments)==postcondition:
                value['postcondition']={**postcondition,'source_fingerprint':connection_fingerprint}
        elif phase=='returned':value={'method':entry.method,'body':response}
        else:raise TaskError('TASK_NATIVE_WRITE_PHASE_INVALID')
        reply=await asyncio.to_thread(lambda:self.executor.db.rpc('record_agent_task_native_write',{
            'p_operation_id':self.bound['operation_id'],'p_dispatch_token':self.bound['dispatch_token'],
            'p_phase':phase,'p_value':value}).execute())
        if not reply or reply.data!={'outcome':'recorded'}:raise TaskError('TASK_NATIVE_WRITE_RECORD_UNCONFIRMED')
        if phase=='dispatched':self.dispatched=True


async def prepare_erp(executor,arguments):
    state=getattr(executor,'_task_execution_state',None)
    if not isinstance(state,TaskState) or state.image_binding_version<3:return None
    require_erp_write_dispatch()
    from services.tools.dispatcher import current_dispatch_call_id
    from services.tool_invocation_store import hash_tool_arguments
    intent={key:arguments.get(key) for key in ('category','action')}
    reply=await asyncio.to_thread(lambda:executor.db.rpc('prepare_agent_task_native_write',{
        'p_task_id':str(executor._task_id),'p_execution_token':str(executor.image_execution_token),
        'p_call_id':current_dispatch_call_id(),'p_args_hash':hash_tool_arguments(arguments),'p_domain':'erp','p_intent':intent}).execute())
    value=reply.data if reply else None
    try:
        if not isinstance(value,dict) or set(value)!={'operation_id','native_id','dispatch_token'} or len(value['operation_id'])!=64:raise ValueError()
        UUID(value['native_id']);UUID(value['dispatch_token'])
    except (TypeError,KeyError,ValueError):raise TaskError('TASK_NATIVE_WRITE_BINDING_REQUIRED') from None
    return NativeWriteSession(executor,value,intent,arguments=arguments)


def receipt_needs_follow_up(body):
    """Inspect the persisted envelope, including batch items, without new IO.

    This only prevents premature completion. It never grants a resend or adds
    an unreviewed method to the final business-completion allowlist.
    """
    pending=[body];visited=0
    statuses={'pending','processing','accepted','queued','running','in_progress',
        'failed','failure','error','partial','cancelled','canceled'}
    counters=('failCount','failedCount','errorCount')
    while pending:
        item=pending.pop();visited+=1
        if visited>2048:return True
        if isinstance(item,list):
            if len(item)>2048-visited:return True
            pending.extend(item);continue
        if not isinstance(item,dict):continue
        if any(item.get(key) for key in ('asyncTaskId','jobId','taskId')):return True
        status=item.get('status')
        if isinstance(status,str) and status.strip().lower() in statuses:return True
        if item.get('partial') is True or str(item.get('partial','')).strip().lower()=='true':return True
        if any(item.get(key) is False for key in ('success','isSuccess','data','result')):return True
        if any(item.get(key) for key in ('errors','errorList','failList','failedItems')):return True
        for key in counters:
            if key not in item:continue
            value=item[key]
            if type(value) in {int,float} and value==0:continue
            if isinstance(value,str):
                import re
                if re.fullmatch(r'0+(?:\.0+)?',value.strip()):continue
            return True
        pending.extend(v for v in item.values() if isinstance(v,(dict,list)))
    return False


def verified_phase(proof):
    """An API acknowledgement does not prove an asynchronous business result."""
    if proof.get('phase')!='returned':return 'unknown'
    response=proof.get('response') or {};dispatch=proof.get('dispatch') or {};body=response.get('body')
    if (not isinstance(body,dict) or response.get('method')!=dispatch.get('method')
        or not (body.get('success') is True or body.get('flag')=='success')):return 'unknown'
    if receipt_needs_follow_up(body):return 'accepted'
    return 'completed' if dispatch.get('method') in COMPLETION_METHODS else 'accepted'


def validate_proof(state,operation,proof):
    try:
        if (operation.tool_name!='erp_execute' or operation.capability!='platform.erp_execute' or proof['domain']!='erp'
            or proof['operation_id']!=operation.id or proof['task_id']!=str(operation.owner_task_id)
            or proof['turn_id']!=str(operation.owner_turn_id) or proof['tool_call_id']!=operation.invocation_id
            or proof['args_hash']!=operation.arguments_sha256 or proof['actor_user_id']!=str(state.actor_user_id)
            or proof['org_id']!=(str(state.org_id) if state.org_id else None)):raise ValueError()
        UUID(proof['native_id'])
        from services.kuaimai.registry import TOOL_REGISTRIES
        intent=proof['intent'];entry=TOOL_REGISTRIES.get(ERP_TOOLS.get(intent['category']),{}).get(intent['action'])
        if not entry or not entry.is_write:raise ValueError()
        if proof.get('dispatch') is not None and proof['dispatch'].get('method')!=entry.method:raise ValueError()
    except (TypeError,ValueError,KeyError,AttributeError):raise TaskError('TASK_NATIVE_WRITE_PROOF_UNCONFIRMED') from None
    observed=verified_phase(proof)
    if observed=='accepted':
        from .erp_postconditions import verified_readback
        if verified_readback(state,proof):return 'completed'
    return observed


class NativeWriteLifecycle(ScheduledOperationLifecycle):
    proof_phase=staticmethod(validate_proof)
    permitted_operations=frozenset({'business_write'})

    def bind(self,state,call):return binding(state,call,self.registry)

    async def bind_resolved_arguments(self,original,call,context,decision):
        if not self.runtime.task_execution_state.model_directed:return
        from .model_directed import resolve_call
        from services.conversation_commands import SafePoint
        import copy
        if not hasattr(self.runtime,'task_operation_lock'):self.runtime.task_operation_lock=asyncio.Lock()
        async with self.runtime.task_operation_lock:
            state=self.runtime.task_execution_state
            candidate=resolve_call(state,original,call)
            if candidate is state:return
            sequence=self.runtime.replay_save_sequence
            self.runtime.task_execution_state=candidate
            try:
                await self.runtime.safe_point(SafePoint.BEFORE_TOOL,replay_payload=copy.deepcopy(self.runtime.checkpoint_base))
            except BaseException as error:
                if self.runtime.replay_save_sequence==sequence:self.runtime.task_execution_state=state
                from services.conversation_state import ConversationPauseRequested
                if isinstance(error,(asyncio.CancelledError,ConversationPauseRequested)):raise
                raise ConversationStopRequested('task_argument_binding_commit_unconfirmed') from None

    def verify(self,call,decision):
        if decision.operation not in self.permitted_operations or decision.outcome!='allow' or not self._enabled(decision):
            raise PermissionError('TASK_OPERATION_ACTOR_REQUIRED')
        current=self.bind(self.runtime.task_execution_state,call)
        if self.binding is not None and self.binding!=current:raise TaskError('TASK_WRITE_BINDING_CHANGED')
        self.binding=current
        if not hasattr(self.runtime,'task_operation_lock'):self.runtime.task_operation_lock=asyncio.Lock()

    async def replay(self,call,context,decision):
        from dataclasses import replace
        from services.tools.result import UncertainToolInvocationError
        if decision.outcome not in {'allow','require_confirmation'}:raise PermissionError('TASK_OPERATION_ACTOR_REQUIRED')
        self.verify(call,replace(decision,outcome='allow'))
        _,key,version=self.binding
        existing=next((op for op in self.runtime.task_execution_state.operations if op.effect_key==key and op.effect_version==version),None)
        if call.name=='erp_execute' and existing is None:require_erp_write_dispatch()
        if existing is not None and existing.status=='failed_before_dispatch':
            if existing.invocation_id==call.call_id and str(existing.owner_task_id)==str(context.task_id):
                record=await self.operation_store().effect_result(self.runtime.task_execution_state,existing.id)
                return self._replayed(record['result'],call,context,decision)
            if self.recovery_only:raise UncertainToolInvocationError('ACTOR_RECOVERY_UNRECORDED_EFFECT')
            if call.name=='erp_execute':require_erp_write_dispatch()
            return None
        return await super().replay(call,context,decision)

    async def begin(self,call,context,decision):
        if call.name=='erp_execute':require_erp_write_dispatch()
        return await super().begin(call,context,decision)

    async def complete(self,result):
        if self.operation_id is None:return
        status='uncertain';reference=None
        try:
            proof=await self.operation_store().native_write_receipt(self.runtime.task_execution_state,self.operation_id)
            operation=next(op for op in self.runtime.task_execution_state.operations if op.id==self.operation_id)
            observed=self.proof_phase(self.runtime.task_execution_state,operation,proof)
            if result.exception is None and result.execution.status=='succeeded' and not result.execution.cancelled:
                if proof.get('phase')=='prepared' and result.kind=='agent' and result.status=='error':status='failed_before_dispatch'
                elif result.status=='success' and observed in {'accepted','completed'}:
                    status=observed;reference='native-write:'+proof['native_id']
        except TaskError as error:
            if str(error)!='TASK_NATIVE_WRITE_PROOF_UNCONFIRMED':raise
        except TaskOperationPersistenceError:raise ConversationStopRequested('task_operation_completion_unconfirmed') from None
        async with self.runtime.task_operation_lock:
            try:write=await self.operation_store().complete_invocation(self.runtime.task_execution_state,operation_id=self.operation_id,
                result=result,status=status,receipt_ref=reference)
            except (TaskError,TaskOperationPersistenceError):raise ConversationStopRequested('task_operation_completion_unconfirmed') from None
            self.adopt(write)


def receipt_artifact(state,operation,proof,*,owner_task_id,validator=validate_proof,validation=VALIDATOR):
    if validator(state,operation,proof)!='completed' or operation.status!='completed' or operation.receipt_ref!='native-write:'+proof['native_id']:
        raise TaskError('TASK_NATIVE_WRITE_PROOF_UNCONFIRMED')
    body={key:proof[key] for key in ('native_id','domain','intent','dispatch','response')}
    if proof.get('readback') is not None:body['readback']=proof['readback']
    if proof.get('source_rotations'):body['source_rotations']=proof['source_rotations']
    checksum=digest(body)
    return Artifact(id='write-'+digest([operation.id,checksum])[:32],kind='receipt',reference=operation.receipt_ref,sha256=checksum,
        source_task_id=operation.owner_task_id,source_call_id=operation.invocation_id,delivery_task_id=UUID(str(owner_task_id)),
        step_id=operation.step_id,validations=('artifact.exists',validation)),body
