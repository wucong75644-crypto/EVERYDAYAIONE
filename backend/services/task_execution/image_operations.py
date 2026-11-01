"""Native lifecycle integration; current ToolPolicy/approval always runs first."""
import asyncio
from uuid import UUID

from services.conversation_state import ConversationStopRequested
from services.handlers.chat.tool_lifecycle import ActorToolLifecycle
from services.tools.result import UncertainToolInvocationError
from services.tools.spec import thaw
from .contracts import TaskError
from .image_binding import bind_image_call
from .operation_store import TaskOperationPersistenceError, image_rejected_before_acceptance
from .reducer import digest


def prepare_image_operations(runtime, calls, registry):
    state = runtime.task_execution_state if runtime is not None else None
    from .image_binding import managed_image_task
    if not managed_image_task(state):
        return None, {}
    from services.tools import ToolCall
    from services.tools.argument_validation import parse_model_image_arguments
    bindings, errors = {}, {}
    for call in calls:
        if call['name'] != 'generate_image':
            continue
        try:
            args = parse_model_image_arguments(registry.require(call['name']), call.get('arguments'))
            binding = bind_image_call(state, ToolCall(call['id'], call['name'], args), registry)
            if (getattr(runtime, 'task_operation_store', None) is None
                    and getattr(runtime, 'task_operation_store_factory', None) is not None):
                runtime.task_operation_store = runtime.task_operation_store_factory()
            if getattr(runtime, 'task_operation_store', None) is None:
                raise TaskError('TASK_OPERATION_STORE_UNAVAILABLE')
            bindings[call['id']] = binding
        except (ValueError, TypeError) as error:
            code = getattr(error, 'code', None) or str(error)
            errors[call['id']] = (code, '本次请求未执行：' + code + '。请按已保存的完整逐图提示词、负面提示词和比例修正参数，继续当前任务。')
    return (ImageOperations(runtime, bindings) if bindings else None), errors


class ImageOperations:
    def __init__(self, runtime, bindings):
        self.runtime = runtime
        self.bindings = bindings
        self.store = runtime.task_operation_store
        if not hasattr(runtime, 'task_operation_lock'):
            runtime.task_operation_lock = asyncio.Lock()

    def lifecycle(self, handler, context, call_id):
        return ImageOperationLifecycle(handler, context, self, self.bindings[call_id])

    def adopt(self, write):
        if write.state != self.runtime.task_execution_state:
            self.runtime.task_execution_state = write.state
            self.runtime.committed_task_execution_revision = write.state.revision
            self.runtime.replay_save_sequence += 1


class ImageOperationLifecycle(ActorToolLifecycle):
    def __init__(self, handler, context, coordinator, binding):
        super().__init__(handler, context)
        self.coordinator, self.binding = coordinator, binding
        self.operation_id = None

    def verify(self, call, decision):
        if not self._enabled(decision):
            raise PermissionError('TASK_OPERATION_ACTOR_REQUIRED')
        if digest(thaw(call.arguments)) != self.binding.arguments_sha256:
            raise TaskError('TASK_IMAGE_ARGUMENTS_CHANGED')
        current = bind_image_call(self.coordinator.runtime.task_execution_state, call, self.coordinator.store.registry)
        if current != self.binding:
            raise TaskError('TASK_IMAGE_BINDING_CHANGED')

    async def _begin(self, call):
        store, runtime, binding = self.coordinator.store, self.coordinator.runtime, self.binding
        try:
            write = await store.begin(runtime.task_execution_state, step_id=binding.step_id,
                effect_key=binding.effect_key, effect_version=binding.effect_version, call=call)
            self.coordinator.adopt(write)
            if write.outcome=='reconcile' and callable(getattr(store,'reconcile_image',None)):
                write=await store.reconcile_image(runtime.task_execution_state,operation_id=write.operation.id)
        except TaskOperationPersistenceError:
            raise ConversationStopRequested('task_operation_commit_unknown') from None
        except TaskError as error:
            if str(error) == 'TASK_REVISION_STALE':
                raise ConversationStopRequested('task_operation_revision_changed') from None
            raise
        self.coordinator.adopt(write)
        return write

    def replay_result(self, write, call, context, decision):
        if write.outcome == 'replay':
            return self._replayed(write.result, call, context, decision)
        if write.outcome=='reconciled':
            from dataclasses import replace
            from services.agent.agent_result import AgentResult
            from services.tools import ToolCall,ToolResult
            original=ToolCall(write.operation.invocation_id,call.name,thaw(call.arguments))
            source=replace(context,task_id=str(write.operation.owner_task_id))
            result=ToolResult.wrap(AgentResult('已核实原图片任务已受理，继续等待原任务成果。',
                source='native_image_reconciliation',metadata={**write.receipt,'reconciled':True}),
                call=original,context=source,decision=decision)
            return result.reused(call=call,context=context,decision=decision,replayed=True)
        raise UncertainToolInvocationError('TASK_OPERATION_RECONCILIATION_REQUIRED')

    async def replay(self, call, context, decision):
        self.verify(call, decision)
        runtime, binding = self.coordinator.runtime, self.binding
        async with runtime.task_operation_lock:
            identity = digest([str(runtime.task_execution_state.root_task_id), binding.effect_key, binding.effect_version])
            existing = next((op for op in runtime.task_execution_state.operations if op.id == identity), None)
            if existing is None or existing.status == 'failed_before_dispatch':
                if (existing is not None and str(existing.owner_task_id) == str(self.context.task_id)
                        and existing.invocation_id == call.call_id):
                    return await super().replay(call, context, decision)
                return None  # Fresh/repaired calls still need current approval.
            write = await self._begin(call)
            return self.replay_result(write, call, context, decision)

    async def begin(self, call, context, decision):
        self.verify(call, decision)
        async with self.coordinator.runtime.task_operation_lock:
            write = await self._begin(call)
            if write.outcome != 'execute':
                return self.replay_result(write, call, context, decision)
            self.operation_id = write.operation.id
            return None

    async def complete(self, result):
        if self.operation_id is None:
            return
        status, receipt = 'uncertain', None
        if (result.exception is None and result.error is None and result.execution.status == 'succeeded'
                and result.metadata.get('accepted') is True and result.metadata.get('completed') is False):
            try:
                child = UUID(str(result.metadata['task_id']))
                UUID(str(result.metadata['message_id']))
                status, receipt = 'accepted', 'task:' + str(child)
            except (KeyError, ValueError, TypeError):
                pass  # An incomplete receipt is not permission to resubmit.
        elif image_rejected_before_acceptance(result):
            status = 'failed_before_dispatch'
        async with self.coordinator.runtime.task_operation_lock:
            try:
                write = await self.coordinator.store.complete_invocation(self.coordinator.runtime.task_execution_state,
                    operation_id=self.operation_id, result=result, status=status, receipt_ref=receipt)
                self.coordinator.adopt(write)
                # A missing handler receipt need not wait for the model to
                # choose another call. Check only committed native acceptance;
                # an absent record still cannot authorize dispatch.
                if status=='uncertain' and callable(getattr(self.coordinator.store,'reconcile_image',None)):
                    write=await self.coordinator.store.reconcile_image(write.state,operation_id=self.operation_id)
            except (TaskOperationPersistenceError, TaskError):
                raise ConversationStopRequested('task_operation_completion_unconfirmed') from None
            self.coordinator.adopt(write)
