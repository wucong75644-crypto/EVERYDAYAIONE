"""Per-file native deletion proof; backups and cleanup addresses precede IO."""
import asyncio
import os
import shutil
from pathlib import Path
from uuid import UUID

from services.file_resources import FileTarget,content_digest
from services.workspace_coordination import finish_file_io,workspace_lock
from services.tools.spec import thaw
from .contracts import TaskError,TaskState
from .file_restores import RestoreSession,snapshot_path,identity,sync_file,cleanup
from .native_writes import NativeWriteLifecycle,binding,receipt_artifact as native_artifact

VALIDATOR='native.file.delete'


class DeleteSession(RestoreSession):
    async def backup(self,index,etag):
        await self.rpc('record_agent_task_file_backup',{'p_operation_id':self.bound['operation_id'],
            'p_dispatch_token':self.bound['dispatch_token'],'p_item_index':index,'p_etag':etag})

    async def authorize(self,index):
        await self.rpc('authorize_agent_task_file_delete_item',{'p_operation_id':self.bound['operation_id'],
            'p_dispatch_token':self.bound['dispatch_token'],'p_item_index':index},expected='ready')

    async def complete_item(self,index,evidence):
        await self.rpc('complete_agent_task_file_delete_item',{'p_operation_id':self.bound['operation_id'],
            'p_dispatch_token':self.bound['dispatch_token'],'p_item_index':index,'p_evidence':evidence})


async def prepare(executor,arguments,prepared):
    state=getattr(executor,'_task_execution_state',None)
    if not isinstance(state,TaskState) or state.image_binding_version<3:return None
    if (prepared is None or prepared.name!='file_delete' or not prepared.versions
        or len(prepared.versions)!=len(prepared.targets)):raise TaskError('TASK_FILE_DELETE_PREPARED_CALL_REQUIRED')
    files=prepared.resolver.files
    items=[]
    for path,version,checksum in prepared.versions:
        target=files.resolve_safe_path(path)
        items.append({'relative_path':target.relative_to(files._workspace_base).as_posix(),
            'workspace_path':target.relative_to(Path(files.workspace_root)).as_posix(),'version':list(version),'sha256':checksum})
    intent={'action':'delete','items':items}
    from services.tools.dispatcher import current_dispatch_call_id
    from services.tool_invocation_store import hash_tool_arguments
    response=await asyncio.to_thread(lambda:executor.db.rpc('prepare_agent_task_file_delete',{
        'p_task_id':str(executor._task_id),'p_execution_token':str(executor.image_execution_token),
        'p_call_id':current_dispatch_call_id(),'p_args_hash':hash_tool_arguments(arguments),'p_intent':intent}).execute())
    value=response.data if response else None
    try:
        if not isinstance(value,dict) or set(value)!={'operation_id','native_id','dispatch_token','items'} or len(value['operation_id'])!=64:raise ValueError()
        UUID(value['native_id']);UUID(value['dispatch_token'])
        if len(value['items'])!=len(items):raise ValueError()
        for index,item in enumerate(value['items']):
            if item['index']!=index or item['manifest']!=items[index] or item['phase']!='allocated' or item['record_id']<=0:
                raise ValueError()
            if item['oss_object_key']!='workspace/.agent-task-backups/'+value['native_id']+'/'+str(index):raise ValueError()
    except (TypeError,KeyError,ValueError):raise TaskError('TASK_FILE_DELETE_BINDING_REQUIRED') from None
    return DeleteSession(executor,value,intent)


def copy_snapshot(source,destination,manifest,check):
    FileTarget(source,tuple(manifest['version'])).validate();check()
    with source.open('rb') as reader,destination.open('xb') as writer:
        shutil.copyfileobj(reader,writer,1024*1024)
        writer.flush();os.fsync(writer.fileno())
    FileTarget(source,tuple(manifest['version'])).validate()
    if content_digest(destination,check)!=manifest['sha256']:raise TaskError('TASK_FILE_DELETE_SOURCE_CHANGED')


def sync_directory(path):
    descriptor=os.open(path,os.O_RDONLY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


async def delete(files,prepared,session):
    from services.oss_service import get_oss_service
    from services.agent.agent_result import AgentResult
    directory=snapshot_path(files,session.bound['operation_id'],session.bound['native_id'],create=True).parent
    bucket=get_oss_service().bucket;uploads=[];deleted=[]
    try:
        # Every object key already has an original deleted_files expiry record,
        # including a PUT whose response is lost. Unused backups expire normally.
        for item in session.bound['items']:
            index=item['index'];manifest=item['manifest'];source=files.resolve_safe_path(manifest['workspace_path'])
            upload=directory/f'upload-{index}';uploads.append(upload)
            await finish_file_io(copy_snapshot,source,upload,manifest,prepared.check)
            response=await finish_file_io(bucket.put_object_from_file,item['oss_object_key'],str(upload),
                headers={'x-oss-forbid-overwrite':'true'})
            metadata=await finish_file_io(bucket.get_object_meta,item['oss_object_key'])
            etag=getattr(response,'etag',None)
            if not isinstance(etag,str) or not etag or getattr(metadata,'etag',None)!=etag:
                raise TaskError('TASK_FILE_DELETE_BACKUP_UNCONFIRMED')
            if await finish_file_io(content_digest,upload,prepared.check)!=manifest['sha256']:
                raise TaskError('TASK_FILE_DELETE_BACKUP_CHANGED')
            await session.backup(index,etag.strip('"'))
        await prepared.verify()
        await session.dispatch({'method':'file_delete','items':session.intent['items']})
        for item in session.bound['items']:
            index=item['index'];manifest=item['manifest'];source=files.resolve_safe_path(manifest['workspace_path'])
            quarantine=directory/f'deleted-{index}'
            if quarantine.exists() or quarantine.is_symlink():raise TaskError('TASK_FILE_DELETE_QUARANTINE_EXISTS')
            await session.authorize(index)
            prepared.check();FileTarget(source,tuple(manifest['version'])).validate()
            # Original inode is moved atomically. A copied snapshot or path
            # absence alone can never be used as evidence of this deletion.
            os.rename(source,quarantine)
            await finish_file_io(sync_file,quarantine)
            await finish_file_io(sync_directory,source.parent)
            if identity(quarantine)!=manifest['version'][:3] or await finish_file_io(content_digest,quarantine,prepared.check)!=manifest['sha256']:
                raise TaskError('TASK_FILE_DELETE_QUARANTINE_CHANGED')
            await session.complete_item(index,manifest)
            deleted.append(source.name)
            await finish_file_io(cleanup,quarantine)
        return AgentResult(summary='已删除 '+str(len(deleted))+' 个文件，原版本备份保留30天：\n'+'\n'.join(deleted),status='success')
    finally:
        for upload in uploads:
            await finish_file_io(cleanup,upload)


def validate_proof(state,operation,proof):
    try:
        if (operation.tool_name!='file_delete' or operation.capability!='workspace.file.delete' or proof['domain']!='file'
            or proof['operation_id']!=operation.id or proof['task_id']!=str(operation.owner_task_id)
            or proof['turn_id']!=str(operation.owner_turn_id) or proof['tool_call_id']!=operation.invocation_id
            or proof['args_hash']!=operation.arguments_sha256 or proof['actor_user_id']!=str(state.actor_user_id)
            or proof['org_id']!=(str(state.org_id) if state.org_id else None)):raise ValueError()
        UUID(proof['native_id']);intent=proof['intent'];items=proof['items']
        if intent['action']!='delete' or not 1<=len(items)<=100 or len(items)!=len(intent['items']):raise ValueError()
        for index,item in enumerate(items):
            manifest=item['manifest']
            if (item['index']!=index or manifest!=intent['items'][index] or item['record_id']<=0
                or item['oss_object_key']!='workspace/.agent-task-backups/'+proof['native_id']+'/'+str(index)
                or len(manifest['version'])!=4 or len(manifest['sha256'])!=64
                or any(c not in '0123456789abcdef' for c in manifest['sha256'])):raise ValueError()
        dispatch=proof.get('dispatch')
        if dispatch is not None and dispatch!={'method':'file_delete','items':intent['items']}:raise ValueError()
        if proof['phase']=='returned':
            response=proof['response'];body=response['body']
            expected=[{k:item[k] for k in ('index','manifest','record_id','backup_etag')} for item in items]
            if (response['method']!='file_delete' or body['success'] is not True or body['deleted']!=expected
                or any(item['phase']!='completed' or not item['backup_etag'] for item in items)):raise ValueError()
            return 'completed'
    except (TypeError,KeyError,ValueError,AttributeError):raise TaskError('TASK_NATIVE_WRITE_PROOF_UNCONFIRMED') from None
    return 'unknown'


class FileDeleteLifecycle(NativeWriteLifecycle):
    proof_phase=staticmethod(validate_proof)
    def bind(self,state,call):return binding(state,call,self.registry,permitted=frozenset({'file_delete'}))

    async def replay(self,call,context,decision):
        from .file_delete_resume import inspect_resume
        reply=await inspect_resume(self,call,context,decision)
        if getattr(self,'resume_operation',None) is not None:return reply
        return await super().replay(call,context,decision)

    async def prepare_file_resume(self,prepared,call,context):
        if getattr(self,'resume_operation',None) is not None:
            from .file_delete_resume import prepare_remaining
            await prepare_remaining(self,prepared,context)

    async def begin(self,call,context,decision):
        if getattr(self,'resume_operation',None) is None:return await super().begin(call,context,decision)
        from .file_delete_resume import begin_resume
        return await begin_resume(self,call,context,decision)

    async def complete(self,result):
        if getattr(self,'resume_started',False):
            from .file_delete_resume import complete_resume
            return await complete_resume(self,result)
        return await super().complete(result)


def receipt_artifact(state,operation,proof,*,owner_task_id):
    return native_artifact(state,operation,proof,owner_task_id=owner_task_id,validator=validate_proof,validation=VALIDATOR)


async def read_verified(state,operation,proof,*,context,store):
    from core.config import get_settings
    from services.file_executor import FileExecutor
    from services.tools.resource_access import ResourceAccessBoundary
    validate_proof(state,operation,proof)
    files=FileExecutor(get_settings().file_workspace_root,context.workspace_owner_id,context.org_id,create_root=False)
    access=ResourceAccessBoundary.from_dict(thaw(context.resource_access))
    try:
        async with workspace_lock(files.workspace_root,write=True):
            directory=snapshot_path(files,operation.id,proof['native_id']).parent
            for item in proof['items']:
                manifest=item['manifest'];relative=manifest['workspace_path']
                target=files.resolve_safe_path(relative)
                if (target.relative_to(Path(files.workspace_root)).as_posix()!=relative
                    or target.relative_to(files._workspace_base).as_posix()!=manifest['relative_path']):
                    raise TaskError('TASK_FILE_DELETE_SCOPE_DENIED')
                access.require('delete',relative)
                quarantine=directory/f'deleted-{item["index"]}'
                if item['phase']=='completed':
                    if (quarantine.is_file() and not quarantine.is_symlink() and identity(quarantine)==manifest['version'][:3]
                        and await finish_file_io(content_digest,quarantine)==manifest['sha256']):
                        await finish_file_io(cleanup,quarantine)
                    continue
                if item['phase']!='backed_up' or not quarantine.exists():continue
                if quarantine.is_symlink() or identity(quarantine)!=manifest['version'][:3]:
                    raise TaskError('TASK_FILE_DELETE_QUARANTINE_CHANGED')
                if target.exists() and os.path.samefile(target,quarantine):raise TaskError('TASK_FILE_DELETE_NOT_DETACHED')
                if await finish_file_io(content_digest,quarantine)!=manifest['sha256']:
                    raise TaskError('TASK_FILE_DELETE_QUARANTINE_CHANGED')
                access.require('delete',relative)
                proof=await store.reconcile_file_delete(state,operation.id,item['index'],manifest)
                validate_proof(state,operation,proof)
                await finish_file_io(cleanup,quarantine)
        return proof
    except TaskError:raise
    except (OSError,ValueError,TypeError,KeyError) as error:raise TaskError('TASK_FILE_DELETE_EVIDENCE_UNCONFIRMED') from error
