"""V1 knowledge canvas. Server-side rollout and ACL apply to every request."""
from pydantic import BaseModel, ConfigDict, Field, model_validator
from uuid import UUID
from fastapi import APIRouter, Query, Response
from fastapi.responses import JSONResponse
from psycopg import Error as DatabaseError
from api.deps import Database, OrgCtx
from core.config import get_settings
from core.db_scope import DatabaseScope, DatabaseAccessKind
from services.knowledge_workspace.contracts import Command, BranchSnapshotRequest
from services.knowledge_workspace.repository import Repository
from services.knowledge_workspace.content_input import ContentInput

router = APIRouter(prefix='/knowledge', tags=['知识画布'])

def enabled(ctx):
    settings = get_settings()
    users = {x.strip() for x in settings.knowledge_workspace_user_ids.split(',') if x.strip()}
    orgs = {x.strip() for x in settings.knowledge_workspace_org_ids.split(',') if x.strip()}
    return settings.knowledge_workspace_enabled and (
        (not users and not orgs) or ctx.user_id in users
        or (ctx.org_id is not None and ctx.org_id in orgs)
    )

def failure(status, code, message):
    return JSONResponse(status_code=status, content={'error': {'code': code, 'message': message, 'details': {}}},
                        headers={'Cache-Control': 'no-store'})

def call(ctx, db, response, method, *args):
    response.headers['Cache-Control'] = 'no-store'
    if not enabled(ctx):
        return failure(404,'KNOWLEDGE_UNAVAILABLE','知识画布尚未向当前账号开放')
    repo = Repository(db.pool, DatabaseScope(ctx.user_id, ctx.org_id, DatabaseAccessKind.RUNTIME))
    try:
        data = getattr(repo, method)(*args)
        if data is None and method in {'object', 'members', 'versions', 'version', 'search', 'document', 'organize'}:
            return failure(404,'KNOWLEDGE_NOT_FOUND','内容不存在或无权访问')
        return {'success': True, 'data': data}
    except DatabaseError as e:
        message = str(e.diag.message_primary or '')
        errors = {
            'KB_SNAPSHOT_EXPIRED': (409,'CLIPBOARD_SNAPSHOT_EXPIRED','复制快照已过期或未完成，请重新复制'),
            'KB_SNAPSHOT_CAPACITY': (422,'CLIPBOARD_SNAPSHOT_CAPACITY','复制内容超过16MB，请减少选中分支'),
            'KB_RELATION_DUPLICATE': (422,'RELATION_DUPLICATE','这些节点之间已有同类型关联线，请选择其他节点'),
            'KB_INVALID_RELATION': (422,'INVALID_RELATION','关联线需连接同一空间内的两个不同节点'),
            'KB_NOT_FOUND': (404,'KNOWLEDGE_NOT_FOUND','内容不存在或无权访问'),
            'KB_ACCESS_DENIED': (403,'KNOWLEDGE_ACCESS_DENIED','没有修改权限'),
            'KB_ORG_REQUIRED': (403,'KNOWLEDGE_ORG_REQUIRED','请先选择企业'),
            'KB_VERSION_CONFLICT': (409,'VERSION_CONFLICT','内容已由其他操作更新，请刷新后重试'),
            'KB_IDEMPOTENCY_PAYLOAD_CONFLICT': (409,'IDEMPOTENCY_PAYLOAD_CONFLICT','重试内容与原操作不同'),
            'KB_HISTORY_CONFLICT': (409,'HISTORY_CONFLICT','知识或撤销记录已更新，请刷新核对；未覆盖新修改'),
            'KB_TREE_CYCLE': (422,'TREE_CYCLE','分支不能移到自身或后代中'),
            'KB_FILE_CAPACITY': (422,'KNOWLEDGE_FILE_CAPACITY','每个节点最多绑定 100 个文件'),
            'KB_INVALID_FILE': (422,'KNOWLEDGE_INVALID_FILE','文件信息不符合约束'),
            'KB_SELF_ROLE_CHANGE': (422,'SELF_ROLE_CHANGE','不能修改自己的管理角色'),
        }
        if message in errors:
            return failure(*errors[message])
        # Do not expose SQL, private identifiers or connection details.
        if e.sqlstate and e.sqlstate.startswith(('22','23')):
            return failure(422,'KNOWLEDGE_INVALID_COMMAND','内容或关联关系不符合约束')
        raise
    except ValueError as e:
        return failure(422,'KNOWLEDGE_CAPACITY_LIMIT',str(e))

@router.get('/availability')
def availability(ctx: OrgCtx, response: Response):
    response.headers['Cache-Control']='no-store'
    return {'success': True, 'data': {'enabled': enabled(ctx)}}

@router.post('/initialize')
def initialize(ctx: OrgCtx, db: Database, response: Response):
    return call(ctx,db,response,'initialize')

@router.get('/canvas')
def canvas(ctx: OrgCtx, db: Database, response: Response, cursor: UUID | None = None):
    return call(ctx,db,response,'canvas',cursor)

@router.get('/roots')
def root_candidates(ctx: OrgCtx, db: Database, response: Response, query: str = Query(default='', max_length=500)):
    from services.knowledge_workspace.chat import roots
    response.headers['Cache-Control']='no-store'
    if not enabled(ctx): return failure(404,'KNOWLEDGE_UNAVAILABLE','知识画布尚未向当前账号开放')
    return {'success':True,'data':roots(Repository(db.pool, DatabaseScope(ctx.user_id,ctx.org_id,DatabaseAccessKind.RUNTIME)),query)}

@router.get('/roots/{kind}/{root_id}')
def root_label(kind: str, root_id: UUID, ctx: OrgCtx, db: Database, response: Response):
    from services.knowledge_workspace.chat import check_roots
    from core.exceptions import AppException
    response.headers['Cache-Control']='no-store'
    if not enabled(ctx) or kind not in {'object','container'}:return failure(404,'KNOWLEDGE_NOT_FOUND','知识引用不可用')
    repo=Repository(db.pool,DatabaseScope(ctx.user_id,ctx.org_id,DatabaseAccessKind.RUNTIME))
    try:
        with repo.cursor() as c:
            check_roots(c,[{'root':{'kind':kind,'id':str(root_id)}}])
            table='kb_objects' if kind=='object' else 'kb_containers'
            c.execute('SELECT title FROM public.'+table+' WHERE id=%s',(root_id,))
            return {'success':True,'data':c.fetchone()}
    except AppException:return failure(404,'KNOWLEDGE_NOT_FOUND','知识引用不可用')

@router.get('/objects/{object_id}')
def object_detail(object_id: UUID, ctx: OrgCtx, db: Database, response: Response):
    return call(ctx,db,response,'object',object_id)

@router.get('/containers/{container_id}/members')
def members(container_id: UUID, ctx: OrgCtx, db: Database, response: Response):
    return call(ctx,db,response,'members',container_id)

@router.post('/commands')
def command(body: Command, ctx: OrgCtx, db: Database, response: Response):
    return call(ctx,db,response,'command',body)


@router.post('/content-plan')
async def content_plan(body: ContentInput, ctx: OrgCtx, db: Database, response: Response):
    from services.knowledge_workspace.content_input import prepare
    response.headers['Cache-Control'] = 'no-store'
    if not enabled(ctx):
        return failure(404, 'KNOWLEDGE_UNAVAILABLE', '知识画布尚未向当前账号开放')
    if not get_settings().knowledge_ai_processing_enabled:
        return failure(409,'KNOWLEDGE_AI_PROCESSING_DISABLED','请直接编辑节点原文，AI 整理暂未启用')
    repo = Repository(db.pool, DatabaseScope(ctx.user_id, ctx.org_id, DatabaseAccessKind.RUNTIME))
    try:
        return {'success': True, 'data': await prepare(repo, body)}
    except PermissionError:
        return failure(403, 'KNOWLEDGE_ACCESS_DENIED', '内容不存在或无权修改')
    except LookupError:
        return failure(409, 'VERSION_CONFLICT', '内容已更新，请重新打开后重试')
    except ValueError:
        return failure(422, 'KNOWLEDGE_CONTENT_INVALID', '输入或整理结果超出容量，请缩小本次输入后重试')
    except Exception:
        return failure(502, 'KNOWLEDGE_CONTENT_FAILED', 'AI 整理暂未完成，输入已保留，请稍后重试')

@router.get('/objects/{object_id}/versions')
def object_versions(object_id: UUID, ctx: OrgCtx, db: Database, response: Response,
                    before: int | None = Query(default=None, ge=1)):
    return call(ctx,db,response,'versions',object_id,before)

@router.get('/objects/{object_id}/versions/{revision}')
def object_version(object_id: UUID, revision: int, ctx: OrgCtx, db: Database, response: Response):
    if revision < 1:
        return failure(422,'KNOWLEDGE_INVALID_REVISION','版本号必须为正整数')
    return call(ctx,db,response,'version',object_id,revision)

@router.get('/containers/{container_id}/search')
def search(container_id: UUID, ctx: OrgCtx, db: Database, response: Response,
           q: str = Query(min_length=1, max_length=200), cursor: UUID | None = None):
    query = q.strip()
    if not query:
        return failure(422,'KNOWLEDGE_INVALID_QUERY','请输入搜索关键词')
    return call(ctx,db,response,'search',container_id,query,cursor)

@router.get('/canvas-history')
def canvas_history(ctx: OrgCtx, db: Database, response: Response):
    return call(ctx,db,response,'canvas_history')

@router.post('/branch-snapshots')
def capture_branch_snapshot(body: BranchSnapshotRequest, ctx: OrgCtx, db: Database, response: Response):
    return call(ctx,db,response,'branch_snapshot',body.snapshot_id,body.model_dump(mode='json'))


class KnowledgeOverrideRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    command_id: UUID
    key: str = Field(min_length=1,max_length=160,pattern=r'^(section:|tag:)')
    value: dict
    expected_revision: int = Field(ge=0)

    @model_validator(mode='after')
    def validate_value(self):
        if set(self.value)-{'text','hidden'} or not self.value or ('text' in self.value and (not isinstance(self.value['text'],str) or len(self.value['text'])>12000)) or ('hidden' in self.value and type(self.value['hidden']) is not bool):
            raise ValueError('invalid manual knowledge override')
        if self.key.startswith('tag:') and set(self.value)!={'hidden'}:raise ValueError('tag override only supports hiding')
        return self

@router.get('/objects/{object_id}/document')
def directory_document(object_id: UUID, ctx: OrgCtx, db: Database, response: Response, generation: UUID | None = None):
    return call(ctx,db,response,'document',object_id,generation)

@router.post('/objects/{object_id}/knowledge-overrides')
def knowledge_override(object_id: UUID, body: KnowledgeOverrideRequest, ctx: OrgCtx, db: Database, response: Response):
    return call(ctx,db,response,'knowledge_override',object_id,body.key,body.value,body.expected_revision,body.command_id)


@router.post('/containers/{container_id}/organize')
def organize(container_id: UUID, ctx: OrgCtx, db: Database, response: Response, cursor: UUID | None = None):
    if not get_settings().knowledge_ai_processing_enabled or not get_settings().knowledge_derivation_enabled:
        return failure(404,'KNOWLEDGE_UNAVAILABLE','自动整理尚未启用')
    return call(ctx,db,response,'organize',container_id,cursor)
