"""Native sandbox effects and emitted files; never infer files from stdout."""
import asyncio
import hashlib
from uuid import UUID

from services.conversation_state import ConversationStopRequested
from services.tools.spec import thaw
from .contracts import Artifact,TaskError
from .operation_store import TaskOperationPersistenceError
from .reach_operations import ReachOperationLifecycle
from .reducer import digest,ready_steps

VALIDATOR='native.sandbox.result'


def binding(state,call):
    if state.model_directed:
        if state.status!='active':raise TaskError('TASK_NOT_ACTIVE')
        from .model_directed import operation_binding
        bound=operation_binding(state,call)
        if any(op.capability=='sandbox.code.execute' and op.effect_key!=bound[1] and op.status in {'reserved','uncertain','accepted'} for op in state.operations):
            raise TaskError('TASK_SANDBOX_PREVIOUS_EXECUTION_UNCERTAIN')
        return bound
    steps=[s for s in ready_steps(state) if s.capability=='sandbox.code.execute']
    if state.status!='active' or len(steps)!=1:raise TaskError('TASK_SANDBOX_READY_STEP_REQUIRED')
    code=thaw(call.arguments).get('code')
    if not isinstance(code,str) or not code.strip():raise TaskError('TASK_SANDBOX_CODE_REQUIRED')
    key='sandbox-'+digest([steps[0].id,hashlib.sha256(code.encode()).hexdigest()])
    if any(op.capability=='sandbox.code.execute' and op.effect_key!=key and op.status in {'reserved','uncertain','accepted'} for op in state.operations):
        raise TaskError('TASK_SANDBOX_PREVIOUS_EXECUTION_UNCERTAIN')
    from .reducer import effect_version
    return steps[0].id,key,effect_version(state,key)


def phase(result):
    if (result.audit.get('tool_name')!='code_execute' or result.kind!='agent' or result.exception is not None
        or result.execution.status!='succeeded' or result.execution.cancelled or not result.execution.handler_started
        or result.execution.cached or result.execution.replayed):return 'unknown'
    value=result.metadata.get('code_execution',{})
    code=thaw(result.audit.get('args',{})).get('code')
    if not isinstance(code,str) or value.get('code_sha256')!=hashlib.sha256(code.encode()).hexdigest():return 'unknown'
    if value.get('phase')=='not_started' and result.status=='error':return 'not_started'
    if (value.get('phase')=='finished' and value.get('kernel_status') in {'ok','error'}
        and value.get('request_id') and result.status in {'success','error'}):return 'finished'
    return 'unknown'


class SandboxOperationLifecycle(ReachOperationLifecycle):
    def verify(self,call,decision):
        if decision.operation!='analysis' or decision.outcome!='allow' or not self._enabled(decision):
            raise PermissionError('TASK_OPERATION_ACTOR_REQUIRED')
        current=binding(self.runtime.task_execution_state,call)
        if self.binding is not None and current!=self.binding:raise TaskError('TASK_SANDBOX_BINDING_CHANGED')
        self.binding=current
        if not hasattr(self.runtime,'task_operation_lock'):self.runtime.task_operation_lock=asyncio.Lock()

    async def begin_write(self,call):
        step,key,version=self.binding
        try:
            write=await self.operation_store().begin(self.runtime.task_execution_state,step_id=step,effect_key=key,effect_version=version,call=call)
        except (TaskOperationPersistenceError,TaskError) as error:
            if isinstance(error,TaskOperationPersistenceError) or str(error)=='TASK_REVISION_STALE':
                raise ConversationStopRequested('task_operation_commit_unconfirmed') from None
            raise
        self.adopt(write)
        return write

    async def replay(self,call,context,decision):
        from dataclasses import replace
        if decision.outcome not in {'allow','require_confirmation'}:raise PermissionError('TASK_OPERATION_ACTOR_REQUIRED')
        self.verify(call,replace(decision,outcome='allow'))
        _,key,version=self.binding
        existing=next((op for op in self.runtime.task_execution_state.operations if op.effect_key==key and op.effect_version==version),None)
        if existing is None:
            if self.recovery_only:
                from services.tools.result import UncertainToolInvocationError
                raise UncertainToolInvocationError('ACTOR_RECOVERY_UNRECORDED_EFFECT')
            return None
        async with self.runtime.task_operation_lock:
            if existing.status=='failed_before_dispatch':
                if existing.invocation_id==call.call_id and str(existing.owner_task_id)==str(context.task_id):
                    record=await self.operation_store().effect_result(self.runtime.task_execution_state,existing.id)
                    return self._replayed(record['result'],call,context,decision)
                if self.recovery_only:
                    from services.tools.result import UncertainToolInvocationError
                    raise UncertainToolInvocationError('ACTOR_RECOVERY_UNRECORDED_EFFECT')
                return None  # Known not-started still requires a fresh native approval.
            return self.replay_result(await self.begin_write(call),call,context,decision)

    async def complete(self,result):
        if self.operation_id is None:return
        observed=phase(result)
        status='completed' if observed=='finished' else 'failed_before_dispatch' if observed=='not_started' else 'uncertain'
        reference='sandbox-result:'+self.operation_id if status=='completed' else None
        async with self.runtime.task_operation_lock:
            try:write=await self.operation_store().complete_invocation(self.runtime.task_execution_state,
                operation_id=self.operation_id,result=result,status=status,receipt_ref=reference)
            except (TaskOperationPersistenceError,TaskError):raise ConversationStopRequested('task_operation_completion_unconfirmed') from None
            self.adopt(write)


def emitted_file(state,payload,context):
    from pathlib import Path
    from core.config import get_settings
    from services.file_executor import FileExecutor
    from services.file_resources import content_digest,file_version
    from services.tools.resource_access import ResourceAccessBoundary
    if (context.actor_user_id!=str(state.actor_user_id) or context.org_id!=(str(state.org_id) if state.org_id else None)
        or context.workspace_owner_id!=state.execution_identity.workspace_owner_id
        or context.context_scope!=state.execution_identity.context_scope
        or context.personal_context_allowed!=state.execution_identity.personal_context_allowed
        or context.conversation_id!=str(state.conversation_id)):raise TaskError('TASK_SANDBOX_OUTPUT_SCOPE_DENIED')
    try:
        if payload.get('kind') not in {'file','image'}:raise ValueError()
        relative=payload['workspace_path']
        if not isinstance(relative,str) or Path(relative).is_absolute():raise ValueError()
        files=FileExecutor(get_settings().file_workspace_root,context.workspace_owner_id,context.org_id,create_root=False)
        target=files.resolve_safe_path(relative)
        if target.relative_to(Path(files.workspace_root)).as_posix()!=relative:raise ValueError()
        access=ResourceAccessBoundary.from_dict(thaw(context.resource_access));access.require('read',relative)
        if target.name!=payload.get('name') or target.stat().st_size!=payload.get('size'):raise ValueError()
        checksum=content_digest(target)
        if checksum!=payload.get('content_sha256') or list(file_version(target))!=payload.get('file_version'):raise ValueError()
        access.require('read',relative)
        if files.resolve_safe_path(relative)!=target:raise ValueError()
        return {'workspace_path':relative,'content_sha256':checksum,'payload':{k:v for k,v in payload.items() if k!='path'}}
    except (ValueError,TypeError,KeyError,OSError,PermissionError):raise TaskError('TASK_SANDBOX_OUTPUT_UNVERIFIED') from None


def import_result(state,operation,record,*,kind,index,context,registry,owner_task_id):
    from services.tools.result_payload import extension_of,decode_raw
    from services.tools.policy import ToolPolicy
    try:
        extension=extension_of(record['result']);raw=decode_raw(extension);audit=extension['audit'];execution=extension['execution']
        meta=raw.metadata.get('code_execution',{})
        spec=registry.require('code_execute')
        decision=ToolPolicy(registry).decide(spec.name,context,{'code':'# read original evidence'})
        if (context.actor_user_id!=str(state.actor_user_id) or context.org_id!=(str(state.org_id) if state.org_id else None)
            or context.workspace_owner_id!=state.execution_identity.workspace_owner_id
            or context.conversation_id!=str(state.conversation_id) or context.context_scope!=state.execution_identity.context_scope
            or context.personal_context_allowed!=state.execution_identity.personal_context_allowed
            or decision.outcome=='deny' or operation.tool_name!=spec.name or operation.capability!=spec.capability
            or operation.status!='completed' or operation.receipt_ref!='sandbox-result:'+operation.id
            or record['status']!='succeeded' or extension['kind']!='agent' or extension['status']!='success' or raw.status!='success'
            or execution['status']!='succeeded' or execution['cancelled'] or extension['error'] is not None
            or meta.get('phase')!='finished' or meta.get('kernel_status') not in {'ok','error'}
            or operation.effect_key!='sandbox-'+digest([operation.step_id,meta['code_sha256']])
            or str(record['task_id'])!=str(operation.owner_task_id) or str(record['turn_id'])!=str(operation.owner_turn_id)
            or record['tool_call_id']!=operation.invocation_id or record['args_hash']!=operation.arguments_sha256
            or audit.get('tool_name')!=spec.name or audit.get('tool_call_id')!=operation.invocation_id
            or audit.get('args_hash')!=operation.arguments_sha256 or audit.get('task_id')!=str(operation.owner_task_id)
            or audit.get('actor_user_id')!=str(state.actor_user_id) or audit.get('org_id')!=(str(state.org_id) if state.org_id else None)
            or audit.get('workspace_owner_id')!=state.execution_identity.workspace_owner_id
            or audit.get('conversation_id')!=str(state.conversation_id)):raise ValueError()
        if kind=='file':
            value=emitted_file(state,raw.emit_payloads[index],context);checksum=digest(value);text=None
        elif kind=='text':
            text=raw.to_tool_content()
            if not text or len(text)>50000:raise ValueError()
            value={'text':text};checksum=hashlib.sha256(text.encode()).hexdigest()
        else:raise ValueError()
    except (ValueError,TypeError,KeyError,AttributeError,IndexError):raise TaskError('TASK_SANDBOX_RESULT_UNVERIFIED') from None
    identity=digest([operation.id,kind,index,checksum])
    artifact=Artifact(id='sandbox-'+identity[:32],kind=kind,reference='sandbox-result:'+operation.id+':'+str(index),sha256=checksum,
        source_task_id=operation.owner_task_id,delivery_task_id=UUID(str(owner_task_id)),source_call_id=operation.invocation_id,
        step_id=operation.step_id,inline_text=text,validations=('artifact.exists',VALIDATOR))
    return artifact,value
