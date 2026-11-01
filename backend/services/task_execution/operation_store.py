"""Internal transaction adapter, to be called after current ToolPolicy/approval.

This class neither authorizes nor dispatches tools. A domain adapter must supply
the stable effect slot and classify its real receipt. Model controls cannot call
it or choose the Actor fence. Unknown persistence always stops dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from services.conversation_state import ConversationStopRequested
from services.tools.result import ToolResult
from services.tools.spec import thaw
from .contracts import Operation, TaskError, TaskState
from .reducer import digest, reserve_operation, record_operation
from .database_scope import task_database


class TaskOperationPersistenceError(RuntimeError):
    """Unknown commit outcome; never interpreted as permission to redo IO."""


def image_rejected_before_acceptance(result):
    metadata = result.metadata
    return (result.audit.get('tool_name') == 'generate_image' and result.kind == 'agent'
        and result.status == 'error' and result.exception is None
        and result.execution.status == 'succeeded' and result.execution.handler_started
        and not result.execution.cancelled and not result.execution.cached and not result.execution.replayed
        and metadata.get('accepted') is False and metadata.get('completed') is False
        and metadata.get('submission_state') == 'not_accepted'
        and metadata.get('task_id') is None and metadata.get('message_id') is None)


def reach_rejection_candidate(result):
    # The SQL bridge separately requires a linked native not_dispatched record.
    return (result.audit.get('tool_name')=='agent_reach' and result.kind=='agent' and result.status=='error'
        and result.exception is None and result.execution.status=='succeeded' and result.execution.handler_started
        and not result.execution.cancelled and not result.execution.cached and not result.execution.replayed)


@dataclass(frozen=True)
class OperationWrite:
    outcome: str
    state: TaskState
    operation: Operation
    result: dict | None = None
    receipt: dict | None = None


class TaskOperationStore:
    def __init__(self, db, *, task_id, execution_token, turn_id, registry):
        self.db = db
        self.task_id = UUID(str(task_id))
        self.execution_token = UUID(str(execution_token))
        self.turn_id = UUID(str(turn_id))
        self.registry = registry

    async def reach_receipt(self,state,operation_id):
        value=await self._rpc('read_agent_task_reach_receipt',state,{'p_operation_id':operation_id})
        if value.get('outcome')!='found' or not isinstance(value.get('proof'),dict):
            raise TaskError('TASK_WRITE_RECEIPT_UNCONFIRMED')
        return value['proof']

    async def scheduled_receipt(self,state,operation_id):
        value=await self._rpc('read_agent_task_scheduled_receipt',state,{'p_operation_id':operation_id})
        if value.get('outcome')!='found' or not isinstance(value.get('proof'),dict):
            raise TaskError('TASK_SCHEDULED_RECEIPT_UNCONFIRMED')
        return value['proof']

    async def native_write_receipt(self,state,operation_id):
        operation=next((op for op in state.operations if op.id==operation_id),None)
        rpc={'file_delete':'read_agent_task_file_delete','generate_video':'read_agent_task_video'}.get(operation.tool_name if operation else '', 'read_agent_task_native_write')
        value=await self._rpc(rpc,state,{'p_operation_id':operation_id})
        if value.get('outcome')!='found' or not isinstance(value.get('proof'),dict):raise TaskError('TASK_NATIVE_WRITE_PROOF_UNCONFIRMED')
        return value['proof']

    async def erp_postcondition(self,state,operation_id):
        value=await self._rpc('read_agent_task_erp_postcondition',state,{'p_operation_id':operation_id})
        if value.get('outcome')!='found' or not isinstance(value.get('proof'),dict):raise TaskError('TASK_ERP_POSTCONDITION_UNCONFIRMED')
        return value['proof']

    async def reconcile_file_restore(self,state,operation_id,evidence):
        value=await self._rpc('reconcile_agent_task_file_restore',state,{
            'p_operation_id':operation_id,'p_evidence':Jsonb(evidence)})
        if value.get('outcome')!='found' or not isinstance(value.get('proof'),dict):
            raise TaskError('TASK_NATIVE_WRITE_PROOF_UNCONFIRMED')
        return value['proof']

    async def reconcile_file_delete(self,state,operation_id,index,evidence):
        value=await self._rpc('reconcile_agent_task_file_delete',state,{'p_operation_id':operation_id,
            'p_item_index':index,'p_evidence':Jsonb(evidence)})
        if value.get('outcome')!='found' or not isinstance(value.get('proof'),dict):raise TaskError('TASK_NATIVE_WRITE_PROOF_UNCONFIRMED')
        return value['proof']

    async def effect_result(self,state,operation_id):
        operation=next((op for op in state.operations if op.id==operation_id),None)
        if operation is None:raise TaskError('TASK_EFFECT_RESULT_UNAVAILABLE')
        value=await self._rpc('read_agent_task_effect_result',state,{'p_operation_id':operation_id})
        if value.get('outcome')!='found' or not isinstance(value.get('record'),dict):raise TaskError('TASK_EFFECT_RESULT_UNAVAILABLE')
        record=value['record']
        if (record.get('task_id')!=str(operation.owner_task_id) or record.get('turn_id')!=str(operation.owner_turn_id)
            or record.get('tool_name')!=operation.tool_name or record.get('tool_call_id')!=operation.invocation_id
            or record.get('args_hash')!=operation.arguments_sha256):raise TaskError('TASK_EFFECT_RESULT_SCOPE_DENIED')
        return record

    async def reconcile_reach(self,state,*,operation_id):
        operation=next(op for op in state.operations if op.id==operation_id)
        value=await self._rpc('reconcile_agent_reach_operation',state,{'p_operation_id':operation_id})
        if value.get('outcome') in {'unresolved','not_found'}:return OperationWrite('reconcile',state,operation)
        from .reach_operations import validate_receipt
        proof=value.get('receipt')
        try:
            validate_receipt(proof)
            UUID(proof['linked_native_id'])
            if (proof['task_id']!=str(operation.owner_task_id) or proof['turn_id']!=str(operation.owner_turn_id)
                or proof['tool_call_id']!=operation.invocation_id or proof['args_hash']!=operation.arguments_sha256):raise ValueError()
        except (TaskError,ValueError,TypeError,KeyError):raise TaskOperationPersistenceError('TASK_OPERATION_RECEIPT_INVALID') from None
        expected=state if operation.status=='completed' else record_operation(state,operation_id,
            owner_task_id=operation.owner_task_id,status='completed',receipt_ref='reach-operation:'+proof['native_id'])
        updated=next(op for op in expected.operations if op.id==operation_id)
        if updated.receipt_ref!='reach-operation:'+proof['native_id']:raise TaskOperationPersistenceError('TASK_OPERATION_RECEIPT_INVALID')
        return self._confirmed(value,expected,updated,{'reconciled'})

    async def _rpc(self, name, state, parameters):
        try:
            response = await task_database(self.db, state).rpc(name, {
                'p_task_id': str(self.task_id), 'p_execution_token': str(self.execution_token),
                'p_logical_task_id': str(state.root_task_id), 'p_expected_revision': state.revision,
                **parameters,
            }).execute()
        except Exception:
            raise TaskOperationPersistenceError('TASK_OPERATION_COMMIT_UNKNOWN') from None
        result = response.data if response else None
        if not isinstance(result, dict):
            raise TaskOperationPersistenceError('TASK_OPERATION_RESULT_INVALID')
        if result.get('outcome') in {'ownership_lost', 'lease_expired', 'terminal'}:
            raise ConversationStopRequested('ownership_lost')
        if result.get('outcome') == 'stale':
            raise TaskError('TASK_REVISION_STALE')
        if result.get('outcome') in {'user_change_pending','previous_effect_unresolved'}:
            raise TaskError('TASK_USER_CHANGE_REVIEW_REQUIRED' if result['outcome']=='user_change_pending' else 'TASK_PREVIOUS_EFFECT_UNRESOLVED')
        if result.get('outcome') == 'not_active':
            raise TaskError('TASK_NOT_ACTIVE')
        if result.get('outcome')=='retry_wait':
            raise TaskError('TASK_WRITE_WAIT_UNTIL_ELIGIBLE')
        return result

    @staticmethod
    def _confirmed(value, expected, operation, outcomes):
        try:
            actual = TaskState.model_validate(value['state'])
            actual_operation = Operation.model_validate(value['operation'])
            if (value.get('outcome') not in outcomes or actual != expected or actual_operation != operation
                    or actual_operation not in actual.operations
                    or (value.get('outcome') == 'replay' and not isinstance(value.get('result'), dict))):
                raise ValueError('mismatched transaction result')
        except (ValidationError, ValueError, KeyError, TypeError):
            raise TaskOperationPersistenceError('TASK_OPERATION_RESULT_INVALID') from None
        return OperationWrite(value['outcome'], actual, actual_operation, value.get('result'),value.get('receipt'))

    async def reconcile_image(self,state,*,operation_id):
        operation=next(op for op in state.operations if op.id==operation_id)
        value=await self._rpc('reconcile_agent_image_operation',state,{'p_operation_id':operation_id})
        if value.get('outcome')=='unresolved':
            return OperationWrite('reconcile',state,operation)
        receipt=value.get('receipt')
        try:
            if (not isinstance(receipt,dict) or receipt.get('accepted') is not True or receipt.get('completed') is not False
                    or receipt.get('submission_state')!='accepted'):raise ValueError()
            child=UUID(receipt['task_id']);UUID(receipt['message_id'])
        except (ValueError,TypeError,KeyError):
            raise TaskOperationPersistenceError('TASK_OPERATION_RECEIPT_INVALID') from None
        expected=state if operation.status=='accepted' else record_operation(state,operation_id,
            owner_task_id=operation.owner_task_id,status='accepted',receipt_ref='task:'+str(child))
        updated=next(op for op in expected.operations if op.id==operation_id)
        if updated.receipt_ref!='task:'+str(child):raise TaskOperationPersistenceError('TASK_OPERATION_RECEIPT_INVALID')
        return self._confirmed(value,expected,updated,{'reconciled'})

    async def begin(self, state, *, step_id, effect_key, effect_version, call):
        spec = self.registry.get(call.name)
        if spec is None or not spec.capability:
            raise TaskError('TASK_EFFECT_CAPABILITY_MISMATCH')
        candidate, operation, action = reserve_operation(state, expected_revision=state.revision,
            step_id=step_id, capability=spec.capability, effect_key=effect_key, effect_version=effect_version,
            arguments=thaw(call.arguments), owner_task_id=self.task_id, invocation_id=call.call_id)
        if action == 'reserve':
            operation = operation.model_copy(update={'owner_turn_id': self.turn_id, 'tool_name': call.name})
            candidate = TaskState.model_validate({**candidate.model_dump(), 'operations': tuple(
                operation if op.id == operation.id else op for op in candidate.operations)})
        elif operation.tool_name != call.name or operation.owner_turn_id is None:
            raise TaskError('TASK_OPERATION_NATIVE_BINDING_MISSING')
        previous = next((op for op in state.operations if op.id == operation.id), None)
        if (previous is not None and previous.status == 'failed_before_dispatch'
                and previous.owner_task_id == self.task_id and previous.owner_turn_id == self.turn_id
                and previous.invocation_id == call.call_id):
            raise TaskError('TASK_OPERATION_NEW_ATTEMPT_ID_REQUIRED')
        result = await self._rpc('begin_agent_task_operation', state, {
            'p_step_id': step_id, 'p_capability': spec.capability, 'p_effect_key': effect_key,
            'p_effect_version': effect_version, 'p_tool_call_id': call.call_id,
            'p_tool_name': call.name, 'p_args_hash': operation.arguments_sha256,
        })
        return self._confirmed(result, candidate, operation,
            {'execute'} if action == 'reserve' else {'replay', 'reconcile'})

    async def complete_invocation(self, state, *, operation_id, result: ToolResult, status, receipt_ref=None):
        """Record the initial native invocation, not a later child completion.

        The domain adapter decides accepted/completed from verified business
        facts. A returned value alone is not proof of delivery. Reconciliation
        of uncertain calls belongs to that domain's existing query protocol.
        """
        operation = next((op for op in state.operations if op.id == operation_id), None)
        if (operation is None or operation.owner_task_id != self.task_id
                or operation.owner_turn_id != self.turn_id
                or result.audit.get('tool_name') != operation.tool_name
                or result.audit.get('tool_call_id') != operation.invocation_id
                or digest(thaw(result.audit.get('args'))) != operation.arguments_sha256
                or str(result.audit.get('task_id')) != str(self.task_id)
                or str(result.audit.get('actor_user_id')) != str(state.actor_user_id)
                or str(result.audit.get('workspace_owner_id')) != state.execution_identity.workspace_owner_id
                or result.audit.get('org_id') != (str(state.org_id) if state.org_id else None)
                or str(result.audit.get('conversation_id')) != str(state.conversation_id)):
            raise TaskError('TASK_OPERATION_RESULT_SCOPE_MISMATCH')
        if status not in {'accepted', 'completed', 'uncertain', 'failed_before_dispatch'}:
            raise TaskError('TASK_EFFECT_TRANSITION_DENIED')
        if operation.status != 'reserved' and (status != operation.status or receipt_ref != operation.receipt_ref):
            raise TaskError('TASK_EFFECT_TRANSITION_DENIED')
        observed=None
        if operation.tool_name=='code_execute':
            from .sandbox_operations import phase as sandbox_phase
            observed=sandbox_phase(result)
        native_rejection=False
        if status=='failed_before_dispatch' and operation.tool_name in {'erp_execute','restore_file','file_delete'} and result.kind=='agent' and result.status=='error' and result.exception is None:
            proof=await self.native_write_receipt(state,operation.id)
            if operation.tool_name=='file_delete':from .file_deletions import validate_proof
            elif operation.tool_name=='restore_file':from .file_restores import validate_proof
            else:from .native_writes import validate_proof
            validate_proof(state,operation,proof)
            native_rejection=proof.get('phase')=='prepared' and result.execution.status=='succeeded' and result.execution.handler_started and not result.execution.cancelled and not result.execution.cached and not result.execution.replayed
        from .video_operations import rejected as video_rejected
        if status == 'failed_before_dispatch' and (receipt_ref is not None or not (
                image_rejected_before_acceptance(result) or video_rejected(result) or reach_rejection_candidate(result) or observed=='not_started' or native_rejection)):
            raise TaskError('TASK_OPERATION_REJECTION_UNVERIFIED')
        if status not in {'uncertain', 'failed_before_dispatch'} and (result.exception is not None or (result.error is not None and observed!='finished')
                or result.execution.status != 'succeeded' or result.execution.cancelled):
            raise TaskError('TASK_OPERATION_SUCCESS_UNVERIFIED')
        candidate = record_operation(state, operation_id, owner_task_id=self.task_id,
            status=status, receipt_ref=receipt_ref)
        from services.tools.result_payload import encode_result
        try:
            payload = encode_result(result)
        except Exception:
            raise TaskOperationPersistenceError('TASK_OPERATION_RESULT_ENCODING_FAILED') from None
        value = await self._rpc('complete_agent_task_operation', state, {
            'p_operation_id': operation_id, 'p_status': status, 'p_receipt_ref': receipt_ref,
            'p_result': Jsonb(payload), 'p_error_message': str(result.exception) if result.exception else '',
        })
        updated = next(op for op in candidate.operations if op.id == operation_id)
        return self._confirmed(value, candidate, updated, {'saved'})
