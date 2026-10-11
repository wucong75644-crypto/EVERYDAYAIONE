"""Organization account configuration; no per-post admin approval queue."""
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from typing import Literal

from api.deps import Database, OrgCtx
from core.config import get_settings
from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.agent_reach.connections import Connections
from services.agent.agent_reach.contracts import ReachError

router = APIRouter(prefix='/agent-reach', tags=['互联网工具'])


class CreateConnection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    platform: Literal['twitter', 'bilibili', 'reddit', 'xiaohongshu', 'youtube']
    account_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(default='', max_length=200)
    secret_json: SecretStr = Field(max_length=64000)


class GrantConnection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: UUID
    can_read: bool = True
    can_write: bool = False


def store(org_ctx, db, response):
    response.headers['Cache-Control'] = 'no-store'
    if not get_settings().agent_reach_enabled:
        raise HTTPException(503, 'Agent Reach尚未启用')
    if not org_ctx.org_id:
        raise HTTPException(403, '需要组织上下文')
    return Connections(db.pool, DatabaseScope(actor_user_id=org_ctx.user_id,
        org_id=org_ctx.org_id, access_kind=DatabaseAccessKind.RUNTIME_ADMIN))


def run(operation, *args):
    try:
        return operation(*args)
    except ReachError as error:
        raise HTTPException(403 if error.code == 'ACCESS_DENIED' else 422, str(error)) from None


@router.get('/connections')
def list_connections(org_ctx: OrgCtx, db: Database, response: Response):
    return run(store(org_ctx, db, response).list)


@router.post('/connections', status_code=201)
async def create_connection(data: CreateConnection, org_ctx: OrgCtx, db: Database, response: Response):
    import json
    from services.agent.agent_reach.social_adapters import credential_options
    try:
        raw = data.secret_json.get_secret_value()
        if len(raw) > 64000:
            raise ValueError()
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not payload:
            raise ValueError()
        if data.platform in {'twitter', 'reddit', 'bilibili'}:
            credential_options(data.platform, {'secret': payload})
        elif data.platform == 'xiaohongshu' and (not isinstance(payload.get('service_token'), str) or not payload['service_token']):
            raise ValueError()
        elif data.platform == 'youtube' and (not isinstance(payload.get('access_token'), str) or not payload['access_token']):
            raise ValueError()
    except (ValueError, TypeError, ReachError):
        raise HTTPException(422, '平台凭据格式不完整或无效') from None
    async def operation():
        import asyncio
        from services.agent.agent_reach.login_providers import LoginProviders
        repository = store(org_ctx,db,response)
        await asyncio.to_thread(repository.assert_admin)
        if data.platform == 'xiaohongshu':
            raise ReachError('UNSUPPORTED_OPERATION','小红书请使用专用服务扫码连接')
        account,name = await LoginProviders(get_settings()).identity(data.platform,payload)
        if account != data.account_id:
            raise ReachError('ACCOUNT_MISMATCH','平台账号ID与实际登录账号不一致')
        return await asyncio.to_thread(repository.save_verified,data.platform,account,data.display_name or name,payload)
    return await login_run(operation())


@router.put('/connections/{connection_id}/grants')
def grant_connection(connection_id: UUID, data: GrantConnection, org_ctx: OrgCtx, db: Database, response: Response):
    run(store(org_ctx, db, response).grant, str(connection_id), str(data.user_id), data.can_read, data.can_write)
    return {'success': True}


@router.delete('/connections/{connection_id}')
def revoke_connection(connection_id: UUID, org_ctx: OrgCtx, db: Database, response: Response):
    run(store(org_ctx, db, response).revoke, str(connection_id))
    return {'success': True}


@router.get('/operations')
def list_operations(org_ctx: OrgCtx, db: Database, response: Response):
    return run(store(org_ctx, db, response).operations)

# Interactive login endpoints never accept organization/user identity from input.
class StartLogin(BaseModel):
    model_config = ConfigDict(extra='forbid')
    platform: Literal['bilibili','xiaohongshu','youtube']
    connection_id: UUID | None = None


class ImportSession(BaseModel):
    model_config = ConfigDict(extra='forbid')
    platform: Literal['twitter','reddit']
    cookie_header: SecretStr = Field(min_length=1,max_length=64000)
    connection_id: UUID | None = None


async def login_service(org_ctx, db, response):
    from core.redis import get_redis
    from services.agent.agent_reach.login_sessions import LoginSessions
    repository = store(org_ctx,db,response)
    # Verify role before acquiring any provider session or reading credentials.
    import asyncio
    await asyncio.to_thread(repository.assert_admin)
    return LoginSessions(repository,get_settings(),await get_redis())


async def login_run(operation):
    import asyncio
    import httpx
    from redis.exceptions import RedisError
    from services.configuration.envelope import SecretMaterialError
    try:
        return await asyncio.wait_for(operation,timeout=90)
    except ReachError as error:
        status = 403 if error.code=='ACCESS_DENIED' else 409 if error.code=='LOGIN_BUSY' else 422
        raise HTTPException(status,str(error)) from None
    except (asyncio.TimeoutError,httpx.HTTPError,RedisError,OSError):
        raise HTTPException(503,'连接服务暂不可用或超时，请稍后检查连接状态') from None
    except SecretMaterialError:
        raise HTTPException(503,'账号加密配置不可用，请联系管理员') from None
    except (KeyError,ValueError,TypeError):
        raise HTTPException(422,'平台返回的登录资料无法验证，请重新连接') from None


@router.get('/login-methods')
async def login_methods(org_ctx: OrgCtx, db: Database, response: Response):
    from services.agent.agent_reach.login_providers import google_config, xhs_slots
    store(org_ctx,db,response)  # Members may see capability, never operator config.
    settings = get_settings()
    methods = []
    for platform,method in [('xiaohongshu','qr'),('bilibili','qr'),('youtube','oauth'),('twitter','import'),('reddit','import')]:
        enabled, message = True, ''
        try:
            from pathlib import Path
            binary = {'bilibili':'python','twitter':'twitter','reddit':'rdt'}.get(platform)
            if binary and not (Path(settings.agent_reach_bin_dir) / binary).is_file():
                raise ReachError('CONFIG_REQUIRED','该平台连接运行环境尚未安装，请联系运维')
            if platform=='youtube':
                google_config(settings)
            if platform=='xiaohongshu' and not xhs_slots(settings,org_ctx.org_id):
                raise ReachError('CONFIG_REQUIRED','本组织扫码服务待配置')
        except ReachError as error:
            enabled, message = False,str(error)
        if method=='import' and enabled:
            message = '当前后端需要导入已登录浏览器的Cookie，不支持一键授权；不会读取你的浏览器或密码。'
        methods.append({'platform':platform,'method':method,'enabled':enabled,'message':message})
    return methods


@router.post('/login-sessions',status_code=201)
async def start_login(data: StartLogin, org_ctx: OrgCtx, db: Database, response: Response):
    async def operation():
        service = await login_service(org_ctx,db,response)
        return await service.begin(data.platform,str(data.connection_id) if data.connection_id else None)
    return await login_run(operation())


@router.get('/login-sessions/{session_id}')
async def poll_login(session_id: UUID, org_ctx: OrgCtx, db: Database, response: Response):
    async def operation():
        service = await login_service(org_ctx,db,response)
        return await service.poll(str(session_id))
    return await login_run(operation())


@router.delete('/login-sessions/{session_id}')
async def cancel_login(session_id: UUID, org_ctx: OrgCtx, db: Database, response: Response):
    async def operation():
        service = await login_service(org_ctx,db,response)
        await service.cancel(str(session_id))
        return {'success':True}
    return await login_run(operation())


@router.post('/session-import',status_code=201)
async def import_session(data: ImportSession, org_ctx: OrgCtx, db: Database, response: Response):
    async def operation():
        import asyncio
        from http.cookies import SimpleCookie, CookieError
        import re
        from services.agent.agent_reach.login_providers import LoginProviders
        repository = store(org_ctx,db,response)
        await asyncio.to_thread(repository.assert_admin)
        raw = data.cookie_header.get_secret_value().strip()
        if any(ord(c)<32 for c in raw) or len(raw)>64000:
            raise ReachError('INVALID_ARGUMENTS','Cookie格式无效，请粘贴单行Cookie内容')
        try:
            parsed = SimpleCookie(); parsed.load(raw)
            cookies = {k:v.value for k,v in parsed.items()}
        except CookieError:
            raise ReachError('INVALID_ARGUMENTS','Cookie格式无法解析') from None
        if not cookies or any(not re.fullmatch(r'[A-Za-z0-9_\-]+',k) or not v for k,v in cookies.items()):
            raise ReachError('INVALID_ARGUMENTS','请输入浏览器Cookie内容，无需JSON或账号ID')
        secret = ({k:cookies[k] for k in ('auth_token','ct0') if k in cookies}
            if data.platform=='twitter' else {'cookies':cookies})
        version = None
        cid = str(data.connection_id) if data.connection_id else None
        if cid:
            previous = await asyncio.to_thread(repository.admin_connection,cid)
            if previous['platform'] != data.platform or previous['status'] != 'active':
                raise ReachError('ACCESS_DENIED','原账号已断开或平台不匹配')
            version = previous['credential_version']
        account,name = await LoginProviders(get_settings()).identity(data.platform,secret)
        return await asyncio.to_thread(repository.save_verified,data.platform,account,name,secret,
            connection_id=cid,expected_version=version)
    return await login_run(operation())


@router.post('/connections/{connection_id}/check')
async def check_connection(connection_id: UUID, org_ctx: OrgCtx, db: Database, response: Response):
    async def operation():
        service = await login_service(org_ctx,db,response)
        return await service.check(str(connection_id))
    return await login_run(operation())


@router.get('/oauth/callback')
async def oauth_callback(db: Database, state: str = '', code: str = '', error: str = ''):
    from fastapi.responses import HTMLResponse
    async def operation():
        import json
        import re
        from core.redis import get_redis
        from services.agent.agent_reach.login_sessions import LoginSessions,PREFIX
        redis = await get_redis()
        if not get_settings().agent_reach_enabled or not redis or not re.fullmatch(r'[A-Za-z0-9_-]{40,100}',state):
            raise ReachError('ACCESS_DENIED','授权会话无效，请重新连接')
        raw = await redis.getdel(PREFIX+'oauth:'+state)
        if not raw or not code and not error or len(code)>4096:
            raise ReachError('ACCESS_DENIED','授权会话已过期或已使用')
        identity = json.loads(raw)
        repository = Connections(db.pool,DatabaseScope(actor_user_id=identity['actor'],
            org_id=identity['org'],access_kind=DatabaseAccessKind.RUNTIME_ADMIN))
        service = LoginSessions(repository,get_settings(),redis)
        await service.oauth_finish(identity['sid'],code,denied=bool(error))
        return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>账号授权</title><p>授权处理已结束。请关闭此页面，回到互联网账号页面查看连接结果。</p></html>',
            headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
                'Content-Security-Policy':"default-src 'none'; frame-ancestors 'none'"})
    return await login_run(operation())
