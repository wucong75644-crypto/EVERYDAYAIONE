"""Resume only verified untouched originals, through the existing file Policy."""
from dataclasses import replace

from services.file_resources import FileTarget,content_digest
from services.workspace_coordination import finish_file_io
from services.tools.result import UncertainToolInvocationError
from services.tool_invocation_store import hash_tool_arguments
from services.tools.spec import thaw
from .contracts import TaskError
from .file_deletions import read_verified,validate_proof,sync_directory
from .file_restores import snapshot_path,identity,sync_file,cleanup


async def rpc(lifecycle,name,**parameters):
    return await lifecycle.operation_store()._rpc(name,lifecycle.runtime.task_execution_state,
        {'p_operation_id':lifecycle.resume_operation.id,**parameters})


async def inspect_resume(lifecycle,call,context,decision):
    if decision.outcome not in {'allow','require_confirmation'}:return None
    lifecycle.verify(call,replace(decision,outcome='allow'))
    _,key,version=lifecycle.binding
    state=lifecycle.runtime.task_execution_state
    op=next((op for op in state.operations if op.effect_key==key and op.effect_version==version),None)
    if op is None or op.status!='uncertain' or (str(op.owner_task_id)==context.task_id and op.invocation_id==call.call_id):return None
    if hash_tool_arguments(thaw(call.arguments))!=op.arguments_sha256:raise TaskError('TASK_FILE_RESUME_ARGUMENTS_CHANGED')
    lifecycle.resume_operation=op
    found=await rpc(lifecycle,'read_agent_task_file_delete_resume',p_call_id=call.call_id,p_args_hash=op.arguments_sha256)
    if found['outcome']=='found':
        if found['status']!='succeeded':raise UncertainToolInvocationError('TASK_FILE_RESUME_RECONCILIATION_REQUIRED')
        return lifecycle._replayed(found['result'],call,context,decision)
    if lifecycle.recovery_only:raise UncertainToolInvocationError('ACTOR_RECOVERY_UNRECORDED_EFFECT')
    proof=await lifecycle.operation_store().native_write_receipt(state,op.id)
    observed=validate_proof(state,op,proof)
    if proof['phase']!='dispatched' or observed=='completed':raise TaskError('TASK_FILE_DELETE_READ_RESULT_REQUIRED')
    lifecycle.resume_proof=proof
    return None


async def prepare_remaining(lifecycle,prepared,context):
    state=lifecycle.runtime.task_execution_state;op=lifecycle.resume_operation
    # Only factual settlement here. No live source is renamed by the read.
    proof=await read_verified(state,op,lifecycle.resume_proof,context=context,store=lifecycle.operation_store())
    validate_proof(state,op,proof)
    if proof['phase']!='dispatched':raise TaskError('TASK_FILE_DELETE_READ_RESULT_REQUIRED')
    files=prepared.resolver.files
    expected=[files.resolve_safe_path(i['manifest']['workspace_path']) for i in proof['items']]
    if set(expected)!={t.path for t in prepared.targets}:raise TaskError('TASK_FILE_RESUME_ARGUMENTS_CHANGED')
    remaining=[i for i in proof['items'] if i['phase']!='completed']
    if not remaining or any(i['phase']!='backed_up' or not i['backup_etag'] for i in remaining):
        raise TaskError('TASK_FILE_RESUME_BACKUP_UNCONFIRMED')
    # Freeze the original versions, not whatever currently occupies each name.
    prepared.targets=tuple(FileTarget(files.resolve_safe_path(i['manifest']['workspace_path']),tuple(i['manifest']['version'])) for i in remaining)
    prepared.labels=tuple(i['manifest']['workspace_path'] for i in remaining)
    binding={'proof':proof,'items':remaining,'lifecycle':lifecycle,'call_id':context.call_id}
    prepared._native_delete_resume=binding
    lifecycle.resume_binding=binding


async def begin_resume(lifecycle,call,context,decision):
    lifecycle.verify(call,decision)
    from services.oss_service import get_oss_service
    bucket=get_oss_service().bucket
    for item in lifecycle.resume_binding['items']:
        meta=await finish_file_io(bucket.get_object_meta,item['oss_object_key'])
        if getattr(meta,'etag','').strip('"')!=item['backup_etag']:raise TaskError('TASK_FILE_RESUME_BACKUP_CHANGED')
    started=await rpc(lifecycle,'begin_agent_task_file_delete_resume',p_call_id=call.call_id,p_args_hash=hash_tool_arguments(thaw(call.arguments)))
    if started.get('outcome')!='execute':raise UncertainToolInvocationError('TASK_FILE_RESUME_BEGIN_UNCONFIRMED')
    lifecycle.resume_started=True;lifecycle.call=call


async def execute_remaining(files,prepared,bound):
    import os
    from services.agent.agent_result import AgentResult
    lifecycle=bound['lifecycle'];proof=bound['proof']
    directory=snapshot_path(files,proof['operation_id'],proof['native_id'],create=True).parent
    completed=[]
    for item in bound['items']:
        manifest=item['manifest'];source=files.resolve_safe_path(manifest['workspace_path'])
        quarantine=directory/f'deleted-{item["index"]}'
        if quarantine.exists() or quarantine.is_symlink():raise TaskError('TASK_FILE_DELETE_QUARANTINE_EXISTS')
        prepared.check();FileTarget(source,tuple(manifest['version'])).validate()
        if await finish_file_io(content_digest,source,prepared.check)!=manifest['sha256']:raise TaskError('TASK_FILE_DELETE_SOURCE_CHANGED')
        grant=await rpc(lifecycle,'authorize_agent_task_file_delete_resume',p_call_id=lifecycle.call.call_id,p_item_index=item['index'])
        if grant.get('outcome')!='ready' or grant.get('native_id')!=proof['native_id']:raise TaskError('TASK_FILE_RESUME_DISPATCH_DENIED')
        prepared.check();FileTarget(source,tuple(manifest['version'])).validate()
        os.rename(source,quarantine)
        await finish_file_io(sync_file,quarantine);await finish_file_io(sync_directory,source.parent)
        if identity(quarantine)!=manifest['version'][:3] or await finish_file_io(content_digest,quarantine)!=manifest['sha256']:
            raise TaskError('TASK_FILE_DELETE_QUARANTINE_CHANGED')
        # Original settlement has independent armed/private evidence and may
        # finish even if the current Actor expires after the physical rename.
        owner=prepared.resolver.owner
        import asyncio
        response=await asyncio.to_thread(lambda:owner.db.rpc('complete_agent_task_file_delete_item',{
            'p_operation_id':proof['operation_id'],'p_dispatch_token':grant['dispatch_token'],
            'p_item_index':item['index'],'p_evidence':manifest}).execute())
        if not response or response.data.get('outcome')!='recorded':raise TaskError('TASK_FILE_DELETE_EVIDENCE_UNCONFIRMED')
        completed.append(source.name);await finish_file_io(cleanup,quarantine)
    return AgentResult('原批次剩余 '+str(len(completed))+' 个文件已删除，原备份继续保留30天：\n'+'\n'.join(completed),status='success')


async def complete_resume(lifecycle,result):
    from services.tools.result_payload import encode_result
    from services.conversation_state import ConversationStopRequested
    status='uncertain' if result.exception is not None or result.execution.cancelled else 'succeeded'
    try:
        response=await rpc(lifecycle,'complete_agent_task_file_delete_resume',p_call_id=lifecycle.call.call_id,p_status=status,p_result=encode_result(result))
        if response.get('outcome')!=status:raise TaskError('TASK_FILE_RESUME_COMPLETION_UNCONFIRMED')
    except Exception:raise ConversationStopRequested('task_file_resume_completion_unconfirmed') from None
