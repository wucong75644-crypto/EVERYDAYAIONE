"""Native video acceptance is a job receipt, never a delivered video."""
import asyncio
from uuid import UUID

from services.tools.spec import thaw
from .contracts import TaskError,TaskState
from .native_writes import NativeWriteLifecycle,binding,receipt_artifact as native_artifact
from .operation_store import TaskOperationPersistenceError

REQUEST='_agent_video_request_v1'
LIFECYCLE='_agent_video_lifecycle_v1'
VALIDATOR='native.video.result'


class VideoNotAccepted(ValueError):pass


def rejected(result):
    metadata=result.metadata
    return (result.audit.get('tool_name')=='generate_video' and result.kind=='agent' and result.status=='error'
        and result.exception is None and result.execution.status=='succeeded' and result.execution.handler_started
        and not result.execution.cancelled and not result.execution.cached and not result.execution.replayed
        and metadata.get('accepted') is False and metadata.get('completed') is False
        and metadata.get('submission_state')=='not_accepted' and metadata.get('task_id') is None and metadata.get('message_id') is None)


def frozen_request(owner,args,parent):
    from services.adapters.factory import DEFAULT_VIDEO_MODEL_ID
    from config.kie_models import calculate_video_cost
    from services.handlers.chat_image_request import canonical_hash
    from services.tools.dispatcher import current_dispatch_call_id
    from services.tool_invocation_store import hash_tool_arguments
    state=owner._task_execution_state
    if set(args)!={'prompt'} or not isinstance(args.get('prompt'),str) or not args['prompt'].strip():raise VideoNotAccepted('VIDEO_PROMPT_REQUIRED')
    if state.execution_identity.context_scope!='user' or state.execution_identity.workspace_owner_id!=str(state.actor_user_id):
        raise VideoNotAccepted('VIDEO_CONTEXT_NOT_ADAPTED')
    prompt=args['prompt'].strip()
    if len(prompt)>10000:raise VideoNotAccepted('VIDEO_PROMPT_TOO_LONG')
    try:cost=calculate_video_cost(model_name=DEFAULT_VIDEO_MODEL_ID,duration_seconds=10)
    except ValueError as error:raise VideoNotAccepted('VIDEO_DEFAULT_MODEL_UNAVAILABLE') from error
    snapshot={'schema_version':1,'mode':'text_to_video','model':DEFAULT_VIDEO_MODEL_ID,'prompt':prompt,
        'duration_seconds':10,'aspect_ratio':'landscape','remove_watermark':True,'references':[],
        'estimated_credits':cost['user_credits'],'estimated_provider_credits':cost['kie_cost'],
        'budget':{'max_requests':8,'max_credits':min(100000,8*cost['user_credits'])},
        'origin':{'parent_task_id':str(parent['id']),'logical_task_id':str(state.root_task_id),
            'actor_user_id':str(state.actor_user_id),'org_id':str(state.org_id) if state.org_id else None,
            'workspace_owner_id':state.execution_identity.workspace_owner_id,'context_scope':'user',
            'conversation_id':str(state.conversation_id),'turn_id':str(parent['turn_id']),
            'input_message_id':str(parent['input_message_id']),'base_context_revision':parent['base_context_revision'],
            'tool_call_id':current_dispatch_call_id(),'args_hash':hash_tool_arguments(args)}}
    snapshot['request_hash']=canonical_hash(snapshot)
    return snapshot


async def accept(owner,args):
    parent=await asyncio.to_thread(lambda:owner.db.table('tasks').select('*').eq('id',str(owner._task_id)).single().execute().data)
    snapshot=frozen_request(owner,args,parent)
    # Errors after this boundary may be committed acceptance, never a safe retry.
    response=await asyncio.to_thread(lambda:owner.db.rpc('accept_agent_video_request',{
        'p_parent_task_id':str(owner._task_id),'p_execution_token':str(owner.image_execution_token),
        'p_snapshot':snapshot,'p_org_id':owner.org_id}).execute())
    receipt=response.data if response else None
    try:
        if receipt['outcome'] not in {'accepted','replay'}:raise ValueError()
        UUID(receipt['task_id']);UUID(receipt['message_id'])
    except (TypeError,ValueError,KeyError):raise TaskError('VIDEO_ACCEPTANCE_UNCONFIRMED') from None
    return receipt


def validate_proof(state,operation,proof):
    try:
        if (operation.tool_name!='generate_video' or operation.capability!='platform.generate_video' or proof['domain']!='video'
            or proof['operation_id']!=operation.id or proof['task_id']!=str(operation.owner_task_id)
            or proof['turn_id']!=str(operation.owner_turn_id) or proof['tool_call_id']!=operation.invocation_id
            or proof['args_hash']!=operation.arguments_sha256 or proof['actor_user_id']!=str(state.actor_user_id)
            or proof['org_id']!=(str(state.org_id) if state.org_id else None)):raise ValueError()
        UUID(proof['native_id']);child=proof['video'];UUID(child['id'])
        if (child['id']!=proof['intent']['child_id'] or child['request_hash']!=proof['intent']['request_hash']
            or child['actor_user_id']!=str(state.actor_user_id) or child['org_id']!=proof['org_id']
            or child['conversation_id']!=str(state.conversation_id)):raise ValueError()
        if child['phase']=='published' and child['status']=='completed':
            output=child['output'];part=child['content'];UUID(output['asset_id'])
            if (child.get('asset_available') is not True or part['type']!='video' or part['url']!=output['url'] or part['asset_id']!=output['asset_id']
                or not output['etag'] or len(output['content_sha256'])!=64 or output['size']<=0):raise ValueError()
            return 'completed'
        if child['phase']=='published' and child['status'] in {'failed','cancelled'}:return 'failed'
    except (TypeError,ValueError,KeyError,AttributeError):raise TaskError('TASK_NATIVE_WRITE_PROOF_UNCONFIRMED') from None
    return 'accepted'


class VideoLifecycle(NativeWriteLifecycle):
    permitted_operations=frozenset({'generation'})
    @staticmethod
    def proof_phase(state,operation,proof):
        phase=validate_proof(state,operation,proof)
        return 'accepted' if phase=='failed' else phase
    def bind(self,state,call):return binding(state,call,self.registry,permitted=frozenset({'generate_video'}),artifact_kinds=frozenset({'file','receipt'}))

    async def complete(self,result):
        if self.operation_id is not None and rejected(result):
            from services.conversation_state import ConversationStopRequested
            async with self.runtime.task_operation_lock:
                try:self.adopt(await self.operation_store().complete_invocation(self.runtime.task_execution_state,
                    operation_id=self.operation_id,result=result,status='failed_before_dispatch'))
                except (TaskError,TaskOperationPersistenceError):raise ConversationStopRequested('task_operation_completion_unconfirmed') from None
            return
        await super().complete(result)


def receipt_artifact(state,operation,proof,*,owner_task_id):
    artifact,body=native_artifact(state,operation,proof,owner_task_id=owner_task_id,validator=validate_proof,validation=VALIDATOR)
    step=next(s for s in state.steps if s.id==operation.step_id)
    target=next(r for r in state.requirements if r.id in (*step.requirement_ids,*step.contributes_to))
    from .reducer import digest
    body={**body,'video':proof['video']};checksum=digest(body)
    artifact=artifact.model_copy(update={'id':'video-'+digest([operation.id,checksum])[:32],'sha256':checksum,
        'kind':'file' if target.artifact_kind=='file' else 'receipt'})
    return artifact,body


def pending(state):
    from .reducer import ready_steps
    if state is None or state.image_binding_version<3 or state.status!='active':return False
    waiting={op.step_id for op in state.operations if op.tool_name=='generate_video' and op.status in {'reserved','accepted','uncertain'}}
    ready=ready_steps(state)
    from .reducer import missing_requirements
    current_ids={s.id for s in state.steps}
    from .image_binding import paid_work_blocked
    historical_wait=bool(state.requirements_history and state.steps and (not missing_requirements(state) or paid_work_blocked(state))
        and waiting-current_ids)
    return historical_wait or bool(ready and waiting and all(s.id in waiting for s in ready))
