"""Original restore IO with durable native identity and no-clobber recovery.

The private snapshot is the inode actually published by link(). Mere path
existence or matching bytes cannot prove this operation restored the file.
"""
import asyncio
import os
from pathlib import Path
from uuid import UUID

from services.file_resources import content_digest,file_version
from services.workspace_coordination import finish_file_io,workspace_lock
from services.tools.spec import thaw
from .contracts import TaskError,TaskState
from .native_writes import NativeWriteLifecycle,binding,receipt_artifact as native_artifact

VALIDATOR='native.file.restore'


def snapshot_path(files,operation_id,native_id,*,create=False):
    if len(operation_id)!=64 or any(c not in '0123456789abcdef' for c in operation_id):raise TaskError('TASK_FILE_RESTORE_ID_INVALID')
    UUID(native_id)
    root=Path(files.workspace_root)
    directory=root/'.agent-task-effects'/operation_id/native_id
    current=root
    for part in directory.relative_to(root).parts:
        current=current/part
        if current.is_symlink():raise TaskError('TASK_FILE_RESTORE_SNAPSHOT_UNSAFE')
        if create:current.mkdir(mode=0o700,exist_ok=True)
        if current.exists() and not current.is_dir():raise TaskError('TASK_FILE_RESTORE_SNAPSHOT_UNSAFE')
    path=directory/'snapshot'
    if path.is_symlink():raise TaskError('TASK_FILE_RESTORE_SNAPSHOT_UNSAFE')
    return path


def identity(path):
    # link/unlink changes ctime on both names. Inode + size + mtime plus a full
    # content digest retains byte/identity checks across no-clobber publication.
    return list(file_version(path)[:3])


def sync_file(path):
    with path.open('rb') as stream:os.fsync(stream.fileno())
    descriptor=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def cleanup(path):
    path.unlink(missing_ok=True)
    try:path.parent.rmdir()
    except OSError:pass


class RestoreSession:
    def __init__(self,executor,bound,intent):self.executor=executor;self.bound=bound;self.intent=intent;self.dispatched=False

    async def rpc(self,name,parameters,*,expected='recorded'):
        response=await asyncio.to_thread(lambda:self.executor.db.rpc(name,parameters).execute())
        if not response or response.data!={'outcome':expected}:raise TaskError('TASK_FILE_RESTORE_RECORD_UNCONFIRMED')

    async def dispatch(self,evidence):
        await self.rpc('record_agent_task_native_write',{'p_operation_id':self.bound['operation_id'],
            'p_dispatch_token':self.bound['dispatch_token'],'p_phase':'dispatched','p_value':evidence})
        self.dispatched=True

    async def complete(self,evidence):
        await self.rpc('complete_agent_task_file_restore',{'p_operation_id':self.bound['operation_id'],
            'p_dispatch_token':self.bound['dispatch_token'],'p_evidence':evidence})


async def prepare(executor,arguments,prepared):
    state=getattr(executor,'_task_execution_state',None)
    if not isinstance(state,TaskState) or state.image_binding_version<3:return None
    if prepared is None or prepared.name!='restore_file' or not prepared.record or not prepared.backup_etag:
        raise TaskError('TASK_FILE_RESTORE_PREPARED_CALL_REQUIRED')
    from services.tools.dispatcher import current_dispatch_call_id
    from services.tool_invocation_store import hash_tool_arguments
    intent={'action':'restore','record_id':prepared.record['id'],'relative_path':prepared.record['relative_path'],
        'oss_object_key':prepared.record['oss_object_key'],'backup_etag':prepared.backup_etag}
    response=await asyncio.to_thread(lambda:executor.db.rpc('prepare_agent_task_file_restore',{
        'p_task_id':str(executor._task_id),'p_execution_token':str(executor.image_execution_token),
        'p_call_id':current_dispatch_call_id(),'p_args_hash':hash_tool_arguments(arguments),'p_intent':intent}).execute())
    bound=response.data if response else None
    try:
        if not isinstance(bound,dict) or set(bound)!={'operation_id','native_id','dispatch_token'} or len(bound['operation_id'])!=64:raise ValueError()
        UUID(bound['native_id']);UUID(bound['dispatch_token'])
    except (TypeError,KeyError,ValueError):raise TaskError('TASK_FILE_RESTORE_BINDING_REQUIRED') from None
    return RestoreSession(executor,bound,intent)


async def restore(executor,files,prepared,session):
    """Called under the original ToolRuntime workspace write lease."""
    from services.agent.agent_result import AgentResult
    from services.oss_service import get_oss_service
    target=files.resolve_safe_path(str(Path(files._workspace_base)/session.intent['relative_path']))
    if target!=prepared.targets[0].path:raise TaskError('TASK_FILE_RESTORE_TARGET_CHANGED')
    snapshot=snapshot_path(files,session.bound['operation_id'],session.bound['native_id'],create=True)
    # Never overwrite an old snapshot, even after a lost handler return.
    with snapshot.open('xb'):pass
    try:
        await finish_file_io(get_oss_service().bucket.get_object_to_file,session.intent['oss_object_key'],str(snapshot),
            headers={'If-Match':f'"{prepared.backup_etag}"'})
        await finish_file_io(sync_file,snapshot)
        checksum=await finish_file_io(content_digest,snapshot,prepared.check)
        evidence={'method':'restore_file','relative_path':session.intent['relative_path'],
            'workspace_path':target.relative_to(Path(files.workspace_root)).as_posix(),
            'backup_etag':prepared.backup_etag,'sha256':checksum,'identity':identity(snapshot)}
        await prepared.verify()
        await session.dispatch(evidence)
        prepared.check()
        target.parent.mkdir(parents=True,exist_ok=True)
        os.link(snapshot,target)
        await finish_file_io(sync_file,target)
        if identity(target)!=evidence['identity'] or await finish_file_io(content_digest,target,prepared.check)!=checksum:
            raise TaskError('TASK_FILE_RESTORE_PUBLICATION_CHANGED')
        await session.complete(evidence)
        # A lost settlement acknowledgement retains the snapshot for recovery.
        await finish_file_io(cleanup,snapshot)
        return AgentResult(summary=f'已恢复「{target.name}」到 {session.intent["relative_path"]}',status='success')
    except BaseException:
        if not session.dispatched:await finish_file_io(cleanup,snapshot)
        raise


def validate_proof(state,operation,proof):
    try:
        if (operation.tool_name!='restore_file' or operation.capability!='platform.restore_file' or proof['domain']!='file'
            or proof['operation_id']!=operation.id or proof['task_id']!=str(operation.owner_task_id)
            or proof['turn_id']!=str(operation.owner_turn_id) or proof['tool_call_id']!=operation.invocation_id
            or proof['args_hash']!=operation.arguments_sha256 or proof['actor_user_id']!=str(state.actor_user_id)
            or proof['org_id']!=(str(state.org_id) if state.org_id else None)):raise ValueError()
        UUID(proof['native_id']);intent=proof['intent']
        if intent['action']!='restore' or type(intent['record_id']) is not int or intent['record_id']<1 or not intent['backup_etag']:raise ValueError()
        dispatch=proof.get('dispatch')
        if dispatch is not None:
            if (dispatch['method']!='restore_file' or dispatch['relative_path']!=intent['relative_path']
                or dispatch['backup_etag']!=intent['backup_etag'] or len(dispatch['identity'])!=3
                or any(type(v) is not int or v<0 for v in dispatch['identity'])
                or len(dispatch['sha256'])!=64 or any(c not in '0123456789abcdef' for c in dispatch['sha256'])):raise ValueError()
        if proof['phase']=='returned':
            body=proof['response']['body']
            if (not dispatch or proof['response']['method']!='restore_file' or body['success'] is not True
                or body['record_id']!=intent['record_id'] or body['restored']!=dispatch):raise ValueError()
            return 'completed'
    except (TypeError,KeyError,ValueError,AttributeError):raise TaskError('TASK_NATIVE_WRITE_PROOF_UNCONFIRMED') from None
    return 'unknown'


class FileRestoreLifecycle(NativeWriteLifecycle):
    proof_phase=staticmethod(validate_proof)
    def bind(self,state,call):return binding(state,call,self.registry,permitted=frozenset({'restore_file'}),artifact_kinds=frozenset({'receipt','file'}))


def receipt_artifact(state,operation,proof,*,owner_task_id):
    artifact,body=native_artifact(state,operation,proof,owner_task_id=owner_task_id,validator=validate_proof,validation=VALIDATOR)
    step=next(step for step in state.steps if step.id==operation.step_id)
    target=next(r for r in state.requirements if r.id in (*step.requirement_ids,*step.contributes_to))
    if target.artifact_kind=='file':artifact=artifact.model_copy(update={'kind':'file'})
    return artifact,body


async def _read_verified(state,operation,proof,*,context,store):
    """Reconcile only the inode already published, never repeat link/download."""
    from core.config import get_settings
    from services.file_executor import FileExecutor
    from services.tools.resource_access import ResourceAccessBoundary
    validate_proof(state,operation,proof)
    if proof['phase']=='prepared':return proof
    files=FileExecutor(get_settings().file_workspace_root,context.workspace_owner_id,context.org_id,create_root=False)
    dispatch=proof['dispatch'];relative=dispatch['workspace_path']
    access=ResourceAccessBoundary.from_dict(thaw(context.resource_access));access.require('restore',relative)
    step=next(step for step in state.steps if step.id==operation.step_id)
    if any(r.artifact_kind=='file' and r.id in (*step.requirement_ids,*step.contributes_to) for r in state.requirements):
        access.require('read',relative)
    target=files.resolve_safe_path(relative)
    if (target.relative_to(Path(files.workspace_root)).as_posix()!=relative
        or target.relative_to(files._workspace_base).as_posix()!=proof['intent']['relative_path']):
        raise TaskError('TASK_FILE_RESTORE_SCOPE_DENIED')
    async with workspace_lock(files.workspace_root,write=True):
        if target.is_symlink() or not target.is_file() or identity(target)!=dispatch['identity']:
            raise TaskError('TASK_FILE_RESTORE_PUBLICATION_UNCONFIRMED')
        if await finish_file_io(content_digest,target)!=dispatch['sha256']:raise TaskError('TASK_FILE_RESTORE_PUBLICATION_CHANGED')
        access.require('restore',relative)
        if proof['phase']=='dispatched':
            snapshot=snapshot_path(files,operation.id,proof['native_id'])
            if not snapshot.is_file() or identity(snapshot)!=dispatch['identity'] or not os.path.samefile(snapshot,target):
                raise TaskError('TASK_FILE_RESTORE_PUBLICATION_UNCONFIRMED')
            proof=await store.reconcile_file_restore(state,operation.id,dispatch)
            validate_proof(state,operation,proof)
            await finish_file_io(cleanup,snapshot)
        else:
            snapshot=snapshot_path(files,operation.id,proof['native_id'])
            if snapshot.is_file() and os.path.samefile(snapshot,target):await finish_file_io(cleanup,snapshot)
        return proof


async def read_verified(state,operation,proof,*,context,store):
    try:return await _read_verified(state,operation,proof,context=context,store=store)
    except TaskError:raise
    except (OSError,ValueError,TypeError,KeyError) as error:
        raise TaskError('TASK_FILE_RESTORE_PUBLICATION_UNCONFIRMED') from error
