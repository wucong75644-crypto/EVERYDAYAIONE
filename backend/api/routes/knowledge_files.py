"""Private files are authorized through a specific current knowledge binding."""
from functools import wraps
import io
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.deps import Database, OrgCtx
from api.routes.knowledge import call, enabled, failure
from core.config import get_settings
from core.exceptions import AppException
from services.file_executor import FileExecutor
from services.file_resources import FileTargetError, file_version as workspace_version
from services.knowledge_workspace import files as storage
from services.workspace_coordination import finish_file_io, receive_workspace_upload, workspace_lock


class PrivateFileRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        @wraps(original)
        async def handler(request):
            try:
                result = await original(request)
                if not (request.url.path.endswith('/thumbnail') and result.status_code in (200,304)):
                    result.headers['Cache-Control'] = 'no-store'
                return result
            except AppException as e:
                return failure(e.status_code,e.code,e.message)
            except FileTargetError:
                return failure(409,'RESOURCE_CHANGED','源文件不是普通文件或已变化，请重新选择')
            except (FileNotFoundError,PermissionError):
                return failure(404,'KNOWLEDGE_NOT_FOUND','文件不存在或无权访问')
        return handler


router = APIRouter(prefix='/knowledge',tags=['知识画布文件'],route_class=PrivateFileRoute)


class FileMutation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    command_id: UUID
    binding_id: UUID | None = None
    expected_revision: int | None = Field(default=None,ge=1,le=9223372036854775807)

    @model_validator(mode='after')
    def revision_pair(self):
        if (self.binding_id is None) != (self.expected_revision is None):
            raise ValueError('更新附件须同时提供绑定和版本号')
        return self


class WorkspaceSnapshot(FileMutation):
    workspace_path: str = Field(min_length=1,max_length=2048)


class FileReference(FileMutation):
    source_object_id: UUID
    source_binding_id: UUID
    source_revision: int = Field(ge=1,le=9223372036854775807)


class RemoveFile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    command_id: UUID
    expected_revision: int = Field(ge=1,le=9223372036854775807)


def authorize_upload(object_id, ctx, db, response):
    result = call(ctx,db,response,'object',object_id)
    if isinstance(result,Response): return result
    if not result['data']['can_write']:
        return failure(403,'KNOWLEDGE_ACCESS_DENIED','没有修改权限')
    if not get_settings().file_workspace_enabled:
        return failure(503,'KNOWLEDGE_FILE_UNAVAILABLE','文件服务暂未启用')
    return None


async def persist_upload(object_id, ctx, db, response, upload, command_id, binding_id=None, expected_revision=None):
    denied = await finish_file_io(authorize_upload,object_id,ctx,db,response)
    if denied is not None: return denied
    if (binding_id is None) != (expected_revision is None):
        return failure(422,'KNOWLEDGE_INVALID_FILE','更新附件须同时提供绑定和版本号')
    name = storage.clean_name(upload.filename)
    root = get_settings().file_workspace_root
    vid = storage.version_id(ctx.user_id,ctx.org_id,command_id)
    target = storage.private_path(root,ctx.org_id,ctx.user_id,vid,create=True)
    staged = target.parent / ('.pending-' + str(uuid4()))
    try:
        await receive_workspace_upload(upload,staged,max_bytes=storage.MAX_BYTES,
            too_large=lambda _: AppException('KNOWLEDGE_FILE_SIZE','文件不能超过 20MB',413))
        # Private namespace is outside every user/channel Workspace and OSS sync.
        async with workspace_lock(target.parent.parent,write=True):
            metadata = await finish_file_io(storage.inspect_file,staged,name)
            await finish_file_io(storage.publish_file,staged,target,metadata['sha256'])
            payload = {**metadata,'object_id':str(object_id),'version_id':vid,
                       'binding_id':str(binding_id) if binding_id else None,'expected_revision':expected_revision}
            return await finish_file_io(call,ctx,db,response,'file_command',command_id,'file_upload',payload)
    finally:
        staged.unlink(missing_ok=True)


@router.post('/objects/{object_id}/files')
async def upload_file(object_id: UUID, ctx: OrgCtx, db: Database, response: Response,
                      command_id: Annotated[UUID,Form()], file: Annotated[UploadFile,File()],
                      binding_id: Annotated[UUID | None,Form()] = None,
                      expected_revision: Annotated[int | None,Form(ge=1,le=9223372036854775807)] = None):
    return await persist_upload(object_id,ctx,db,response,file,command_id,binding_id,expected_revision)


@router.post('/objects/{object_id}/files/from-workspace')
async def from_workspace(object_id: UUID, body: WorkspaceSnapshot, ctx: OrgCtx, db: Database, response: Response):
    denied = await finish_file_io(authorize_upload,object_id,ctx,db,response)
    if denied is not None: return denied
    executor = FileExecutor(get_settings().file_workspace_root,ctx.user_id,ctx.org_id,create_root=False)
    async with workspace_lock(executor.workspace_root):
        source = executor.resolve_safe_path(body.workspace_path)
        before = workspace_version(source)
        if not 0<before[1]<=storage.MAX_BYTES:
            return failure(413,'KNOWLEDGE_FILE_SIZE','文件不能为空且不能超过 20MB')
        content = await finish_file_io(storage.read_regular,source)
        if before != workspace_version(source):
            return failure(409,'RESOURCE_CHANGED','源文件在复制期间发生变化，请重新选择')
    upload = UploadFile(filename=source.name,file=io.BytesIO(content))
    try:
        return await persist_upload(object_id,ctx,db,response,upload,body.command_id,body.binding_id,body.expected_revision)
    finally:
        await upload.close()


@router.post('/objects/{object_id}/files/reference')
def reference(object_id: UUID, body: FileReference, ctx: OrgCtx, db: Database, response: Response):
    payload = body.model_dump(mode='json',exclude={'command_id'})
    return call(ctx,db,response,'file_command',body.command_id,'file_reference',{**payload,'object_id':str(object_id)})


@router.delete('/objects/{object_id}/files/{binding_id}')
def remove(object_id: UUID, binding_id: UUID, body: RemoveFile, ctx: OrgCtx, db: Database, response: Response):
    return call(ctx,db,response,'file_command',body.command_id,'file_remove',
                {'object_id':str(object_id),'binding_id':str(binding_id),'expected_revision':body.expected_revision})


@router.get('/objects/{object_id}/files')
def files(object_id: UUID, ctx: OrgCtx, db: Database, response: Response):
    result = call(ctx,db,response,'files',object_id)
    if isinstance(result,Response): return result
    if result['data'] is None: return failure(404,'KNOWLEDGE_NOT_FOUND','内容不存在或无权访问')
    for binding in result['data']:
        binding['version'] = storage.public_version(binding['version'])
    return result


@router.get('/objects/{object_id}/files/{binding_id}/versions')
def versions(object_id: UUID, binding_id: UUID, ctx: OrgCtx, db: Database, response: Response,
             before: int | None = Query(default=None,ge=1,le=9223372036854775807)):
    result = call(ctx,db,response,'file_versions',object_id,binding_id,before)
    if isinstance(result,Response): return result
    if result['data'] is None: return failure(404,'KNOWLEDGE_NOT_FOUND','文件不存在或无权访问')
    result['data']['items'] = [storage.public_version(v) for v in result['data']['items']]
    return result


@router.get('/objects/{object_id}/files/{binding_id}/versions/{revision}/content')
async def content(object_id: UUID, binding_id: UUID, revision: int, ctx: OrgCtx, db: Database, response: Response):
    if revision<1 or revision>9223372036854775807:
        return failure(422,'KNOWLEDGE_INVALID_REVISION','版本号无效')
    result = await finish_file_io(call,ctx,db,response,'file_version',object_id,binding_id,revision)
    if isinstance(result,Response): return result
    if result['data'] is None: return failure(404,'KNOWLEDGE_NOT_FOUND','文件不存在或无权访问')
    version = result['data']
    data = await finish_file_io(storage.read_version,get_settings().file_workspace_root,version)
    # Recheck after disk IO; never turn an old authorized metadata response into a bypass.
    check = await finish_file_io(call,ctx,db,response,'file_version',object_id,binding_id,revision)
    if isinstance(check,Response): return check
    if check['data'] is None: return failure(404,'KNOWLEDGE_NOT_FOUND','文件不存在或无权访问')
    return Response(data,media_type=version['mime_type'],headers={
        'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'",
        'Content-Disposition':"attachment; filename*=UTF-8''"+quote(version['name'],safe=''),
    })


@router.post('/imports/xmind')
async def import_xmind(ctx: OrgCtx, db: Database, response: Response,
                       command_id: Annotated[UUID,Form()], file: Annotated[UploadFile,File()],
                       container_id: Annotated[UUID,Form()], expected_container_revision: Annotated[int,Form(ge=1)]):
    from services.knowledge_workspace import xmind
    response.headers['Cache-Control'] = 'no-store'
    if not enabled(ctx):
        return failure(404,'KNOWLEDGE_UNAVAILABLE','知识画布尚未向当前账号开放')
    settings = get_settings()
    if not settings.file_workspace_enabled:
        return failure(503,'KNOWLEDGE_FILE_UNAVAILABLE','文件服务暂未启用')
    filename = str(file.filename or '')
    if not filename.lower().endswith('.xmind'):
        return failure(422,'KNOWLEDGE_INVALID_XMIND','请选择 .xmind 文件')
    name = storage.clean_name(filename)
    target=await finish_file_io(call,ctx,db,response,'import_target',container_id)
    if isinstance(target,Response):return target
    if target['data'] is None:return failure(403,'KNOWLEDGE_ACCESS_DENIED','目标空间不存在或没有修改权限')
    # Bounded reads avoid materializing an untrusted upload without a limit.
    data = bytearray()
    while chunk := await file.read(1024*1024):
        data.extend(chunk)
        if len(data)>storage.MAX_BYTES:
            return failure(413,'KNOWLEDGE_FILE_SIZE','Xmind 文件不能超过 20MB')
    import hashlib
    receipt = await finish_file_io(call,ctx,db,response,'xmind_receipt',command_id,hashlib.sha256(data).hexdigest(),name,container_id,expected_container_revision)
    if isinstance(receipt,Response) or receipt['data'] is not None:
        return receipt
    plan = await finish_file_io(xmind.parse,bytes(data))
    # Serialize publication for identical retries; all registrations are atomic.
    lock = storage.private_path(settings.file_workspace_root,ctx.org_id,ctx.user_id,
                                storage.version_id(ctx.user_id,ctx.org_id,command_id),create=True).parent
    async with workspace_lock(lock,write=True):
        payload = await finish_file_io(xmind.prepare,plan,bytes(data),name,settings.file_workspace_root,
                                      ctx.user_id,ctx.org_id,command_id)
        payload.update(container_id=str(container_id),expected_container_revision=expected_container_revision)
        return await finish_file_io(call,ctx,db,response,'import_xmind',command_id,payload)


@router.get('/containers/{container_id}/import-progress')
def import_progress(container_id:UUID,ctx:OrgCtx,db:Database,response:Response):
    return call(ctx,db,response,'import_progress',container_id)


@router.post('/objects/{object_id}/files/{binding_id}/versions/{revision}/retry')
def retry_parse(object_id:UUID,binding_id:UUID,revision:int,ctx:OrgCtx,db:Database,response:Response):
    if revision<1 or revision>9223372036854775807:return failure(422,'KNOWLEDGE_INVALID_REVISION','版本号无效')
    if not get_settings().knowledge_ai_processing_enabled:
        return failure(409,'KNOWLEDGE_AI_PROCESSING_DISABLED','原始附件已保留，AI 解析暂未启用')
    return call(ctx,db,response,'retry_file',object_id,binding_id,revision)


@router.get('/objects/{object_id}/files/{binding_id}/versions/{revision}/thumbnail')
async def thumbnail(object_id:UUID,binding_id:UUID,revision:int,ctx:OrgCtx,db:Database,response:Response,request:Request):
    if revision<1 or revision>9223372036854775807:return failure(422,'KNOWLEDGE_INVALID_REVISION','版本号无效')
    from services.knowledge_workspace import media_delivery as media
    result=await finish_file_io(call,ctx,db,response,'file_version',object_id,binding_id,revision)
    if isinstance(result,Response):return result
    version=result['data']
    if not version or not version['mime_type'].startswith('image/'):return failure(404,'KNOWLEDGE_NOT_FOUND','图片不存在或无权访问')
    stream=await finish_file_io(media.verified_file,get_settings().file_workspace_root,version)
    try:
        headers={'ETag':'"'+version['sha256']+'-thumb512-v1"','Cache-Control':'private, max-age=0, must-revalidate','Vary':'Authorization, X-Org-Id','X-Content-Type-Options':'nosniff'}
        data=None if request.headers.get('if-none-match')==headers['ETag'] else await finish_file_io(media.thumbnail,stream,version)
        check=await finish_file_io(call,ctx,db,response,'file_version',object_id,binding_id,revision)
        if isinstance(check,Response):return check
        if not check['data']:return failure(404,'KNOWLEDGE_NOT_FOUND','图片不存在或无权访问')
        return Response(data,status_code=304 if data is None else 200,media_type='image/webp',headers=headers)
    finally:stream.close()


@router.post('/objects/{object_id}/files/{binding_id}/versions/{revision}/playback')
async def playback(object_id:UUID,binding_id:UUID,revision:int,ctx:OrgCtx,db:Database,response:Response,request:Request,quality:Literal['preview','original']='original'):
    if revision<1 or revision>9223372036854775807:return failure(422,'KNOWLEDGE_INVALID_REVISION','版本号无效')
    from services.knowledge_workspace import media_delivery as media
    from core.config import get_settings as security_settings
    result=await finish_file_io(call,ctx,db,response,'file_version',object_id,binding_id,revision)
    if isinstance(result,Response):return result
    if not result['data'] or result['data']['mime_type']!='video/mp4':return failure(404,'KNOWLEDGE_NOT_FOUND','视频不存在或无权访问')
    if quality=='preview':
        from services.knowledge_workspace import video_preview
        await finish_file_io(video_preview.prepare,get_settings().file_workspace_root,result['data'])
        check=await finish_file_io(call,ctx,db,response,'file_version',object_id,binding_id,revision)
        if isinstance(check,Response):return check
        if not check['data']:return failure(404,'KNOWLEDGE_NOT_FOUND','视频不存在或无权访问')
    path=request.url.path.removesuffix('/playback')+'/stream'
    response.set_cookie('kb_media',media.lease(security_settings().jwt_secret_key,ctx,object_id,binding_id,revision),max_age=media.LEASE_SECONDS,path=path,httponly=True,secure=request.url.scheme=='https',samesite='strict')
    return {'success':True,'data':{'url':path+('?quality=preview' if quality=='preview' else ''),'expires_in':media.LEASE_SECONDS}}


@router.api_route('/objects/{object_id}/files/{binding_id}/versions/{revision}/stream',methods=['GET','HEAD'])
async def stream_video(object_id:UUID,binding_id:UUID,revision:int,request:Request,db:Database,response:Response,quality:Literal['preview','original']='original'):
    if revision<1 or revision>9223372036854775807:return failure(422,'KNOWLEDGE_INVALID_REVISION','版本号无效')
    from fastapi.responses import StreamingResponse
    from starlette.background import BackgroundTask
    from api.deps import OrgContext
    from services.knowledge_workspace import media_delivery as media
    from core.config import get_settings as security_settings
    user,org=media.read_lease(security_settings().jwt_secret_key,request.cookies.get('kb_media',''),object_id,binding_id,revision)
    ctx=OrgContext(user,org)
    result=await finish_file_io(call,ctx,db,response,'file_version',object_id,binding_id,revision)
    if isinstance(result,Response):return result
    version=result['data']
    if not version or version['mime_type']!='video/mp4':return failure(404,'KNOWLEDGE_NOT_FOUND','视频不存在或无权访问')
    rendition=None
    if quality=='preview':
        from services.knowledge_workspace import video_preview
        rendition=await finish_file_io(video_preview.prepare,get_settings().file_workspace_root,version)
    size=rendition['size'] if rendition else version['size']
    digest=rendition['sha256'] if rendition else version['sha256']
    etag='"'+digest+'"';header=request.headers.get('range')
    if request.headers.get('if-range') not in (None,etag):header=None
    headers={'X-Accel-Buffering':'no','Accept-Ranges':'bytes','ETag':etag,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}
    try:start,end=media.byte_range(header,size)
    except ValueError:return Response(status_code=416,headers={**headers,'Content-Range':'bytes */'+str(size)})
    stream=io.BytesIO(rendition['data']) if rendition and rendition['data'] is not None else await finish_file_io(media.verified_file,get_settings().file_workspace_root,version)
    try:
        check=await finish_file_io(call,ctx,db,response,'file_version',object_id,binding_id,revision)
    except BaseException:
        stream.close();raise
    if isinstance(check,Response) or not check['data']:
        stream.close();return check if isinstance(check,Response) else failure(404,'KNOWLEDGE_NOT_FOUND','视频不存在或无权访问')
    headers['Content-Length']=str(end-start+1)
    if header:headers['Content-Range']=f"bytes {start}-{end}/{size}"
    if request.method=='HEAD':stream.close();return Response(status_code=206 if header else 200,headers=headers,media_type='video/mp4')
    return StreamingResponse(media.chunks(stream,start,end),status_code=206 if header else 200,headers=headers,media_type='video/mp4',background=BackgroundTask(stream.close))
