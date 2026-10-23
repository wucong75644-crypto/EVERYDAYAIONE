"""V1 knowledge canvas. Server-side rollout and ACL apply to every request."""
from uuid import UUID
from fastapi import APIRouter, Query, Response
from fastapi.responses import JSONResponse
from psycopg import Error as DatabaseError
from api.deps import Database, OrgCtx
from core.config import get_settings
from core.db_scope import DatabaseScope, DatabaseAccessKind
from services.knowledge_workspace.contracts import Command
from services.knowledge_workspace.repository import Repository

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
        if data is None and method in {'object', 'members', 'versions', 'version', 'search'}:
            return failure(404,'KNOWLEDGE_NOT_FOUND','内容不存在或无权访问')
        return {'success': True, 'data': data}
    except DatabaseError as e:
        message = str(e.diag.message_primary or '')
        errors = {
            'KB_NOT_FOUND': (404,'KNOWLEDGE_NOT_FOUND','内容不存在或无权访问'),
            'KB_ACCESS_DENIED': (403,'KNOWLEDGE_ACCESS_DENIED','没有修改权限'),
            'KB_ORG_REQUIRED': (403,'KNOWLEDGE_ORG_REQUIRED','请先选择企业'),
            'KB_VERSION_CONFLICT': (409,'VERSION_CONFLICT','内容已由其他操作更新，请刷新后重试'),
            'KB_IDEMPOTENCY_PAYLOAD_CONFLICT': (409,'IDEMPOTENCY_PAYLOAD_CONFLICT','重试内容与原操作不同'),
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

@router.get('/objects/{object_id}')
def object_detail(object_id: UUID, ctx: OrgCtx, db: Database, response: Response):
    return call(ctx,db,response,'object',object_id)

@router.get('/containers/{container_id}/members')
def members(container_id: UUID, ctx: OrgCtx, db: Database, response: Response):
    return call(ctx,db,response,'members',container_id)

@router.post('/commands')
def command(body: Command, ctx: OrgCtx, db: Database, response: Response):
    return call(ctx,db,response,'command',body)

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
