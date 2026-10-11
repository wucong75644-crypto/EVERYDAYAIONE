"""Bridge the existing trusted ToolExecutor context to the new adapters."""
import asyncio
import hashlib
import json
import httpx

from pydantic import ValidationError

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.agent_result import AgentResult
from services.tools.dispatcher import current_dispatch_call_id
from .connections import Connections
from .contracts import ReachError, ReachRequest, WRITE_ACTIONS, ReachResult
from .locks import account_lock
from .media import media_snapshot
from .network import PublicHTTP
from .runner import ToolRunner
from .service import execute, resolve
from .write_adapters import check_write, write


async def execute_reach(executor, args):
    from core.config import get_settings
    settings = get_settings()
    if not settings.agent_reach_enabled:
        return AgentResult(summary='Agent Reach尚未启用', status='error',
                           error_message='CONFIG_REQUIRED', metadata={'retryable': False})
    timeout = settings.agent_reach_timeout_seconds
    if executor.execution_budget is not None:
        timeout = min(timeout, executor.execution_budget.remaining)

    async def operation():
        request = ReachRequest.model_validate(args)
        if request.action == 'list_connections':
            store = Connections(executor.db.pool, DatabaseScope(actor_user_id=executor.user_id,
                org_id=executor.org_id, access_kind=DatabaseAccessKind.RUNTIME))
            rows = await asyncio.to_thread(store.list)
            safe = [{key: str(row[key]) for key in ('id','platform','account_id','display_name')} for row in rows]
            return AgentResult(summary='当前获授权的组织账号：' + json.dumps(safe, ensure_ascii=False),
                               source='agent_reach', status='success' if safe else 'empty')
        writing = request.action in WRITE_ACTIONS
        request = request if writing else resolve(request)
        if writing:
            check_write(request)
            allowed_writes = {p.strip() for p in settings.agent_reach_write_actions.split(',')}
            if request.platform + ':' + request.action not in allowed_writes:
                raise ReachError('CONFIG_REQUIRED', '该平台写入操作尚未启用或完成账号验证')
        if request.platform not in {p.strip() for p in settings.agent_reach_platforms.split(',')}:
            raise ReachError('CONFIG_REQUIRED', '渠道尚未启用')
        connection, store = None, None
        if request.connection_id:
            store = Connections(executor.db.pool, DatabaseScope(actor_user_id=executor.user_id,
                org_id=executor.org_id, access_kind=DatabaseAccessKind.RUNTIME))
        elif request.platform in {'twitter', 'reddit', 'xiaohongshu'} or writing:
            raise ReachError('AUTH_REQUIRED', '请先在组织设置中连接账号，并指定已授权的连接ID')
        if store:
            connection = await asyncio.to_thread(store.load, request.connection_id, request.platform, write=writing)
        lock = ('account:' + request.platform + ':' + connection['account_id']) if store else ('public:' + request.platform)
        async with account_lock(lock):
            if store:
                connection = await asyncio.to_thread(store.load, request.connection_id, request.platform, write=writing)
            if connection and request.platform == 'youtube':
                from .login_providers import youtube_credentials
                connection = await youtube_credentials(connection, settings)
            if not writing:
                return await execute(request, settings, timeout=timeout, connection=connection)
            call_id = current_dispatch_call_id()
            if not call_id:
                raise ReachError('ACCESS_DENIED', '写入只能通过已授权的公共工具执行入口')
            async with media_snapshot(executor, request.media, settings.agent_reach_media_staging_dir) as media:
                material = request.model_dump()
                material['media_digests'] = [item['sha256'] for item in media]
                material['credential_version'] = connection['credential_version']
                digest = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                operation_id, previous = await asyncio.to_thread(store.reserve, call_id, connection, digest)
                if previous:
                    if previous['state'] == 'succeeded':
                        return ReachResult(request.platform, 'receipt-replay', receipt=previous['receipt']).to_agent_result()
                    raise ReachError('WRITE_UNCERTAIN', '同一操作已执行或结果不确定，不能自动再次提交')
                try:
                    result = await write(request, connection, ToolRunner(settings.agent_reach_bin_dir), PublicHTTP(), settings, media=media)
                    await asyncio.to_thread(store.settle, operation_id, 'succeeded', result.receipt)
                    return result.to_agent_result()
                except BaseException:
                    # Keep a durable uncertain state even when cancellation arrives.
                    # An executing row also prevents replay if this update fails.
                    try:
                        await asyncio.shield(asyncio.to_thread(store.settle, operation_id, 'uncertain', {}))
                    except Exception:
                        pass
                    raise

    try:
        return await asyncio.wait_for(operation(), timeout=max(0, timeout))
    except asyncio.TimeoutError:
        return AgentResult(summary='Agent Reach超过本轮预算；写入若已开始，不能重复发送，请核对账号。',
                           status='timeout', error_message='BUDGET_EXHAUSTED', metadata={'retryable': False})
    except ValidationError:
        return AgentResult(summary='工具参数不完整或不合法，请补齐目标和操作。', status='error',
                           error_message='INVALID_ARGUMENTS', metadata={'retryable': False})
    except ReachError as error:
        return AgentResult(summary=f'操作未完成：{error}。不能据此判断资料不存在。',
                           status='error', error_message=error.code, metadata={'retryable': error.retryable})

    except (httpx.HTTPError, OSError):
        return AgentResult(summary='平台网络或执行环境不可用；已开始的写入请先核对回执，不要重复发送。',
            status='error', error_message='NETWORK_ERROR', metadata={'retryable': False})
    except (ValueError, KeyError, TypeError):
        return AgentResult(summary='平台返回格式无法核验；请检查执行回执，不要重复写入。',
            status='error', error_message='UPSTREAM_CHANGED', metadata={'retryable': False})
