"""One public tool, operation-specific routing and one caller deadline."""
import asyncio
import time

import httpx

from .contracts import ReachError, ReachRequest, READ_ACTIONS, platform_for_url
from .network import PublicHTTP
from .public_adapters import read_web, read_rss, github, exa, youtube, youtube_comments
from .runner import ToolRunner


def resolve(request):
    request = request.model_copy()
    if request.platform == 'auto':
        request.platform = platform_for_url(request.url) if request.url else 'exa'
    elif request.url and request.platform not in {'web', 'rss'} and platform_for_url(request.url) != request.platform:
        raise ReachError('INVALID_URL', '目标链接与指定平台不匹配')
    if request.action == 'auto':
        request.action = ('transcript' if request.platform in {'youtube', 'bilibili'} else 'read') if request.url else 'search'
    if request.action == 'search' and not request.query.strip():
        request.query = request.task
    supported = {'web': {'read'}, 'rss': {'read'}, 'github': {'search', 'read', 'read_comments'},
                 'youtube': {'search', 'read', 'transcript', 'read_comments'}, 'exa': {'search'},
                 'bilibili': {'search', 'read', 'transcript', 'read_comments'},
                 'xiaohongshu': {'search', 'read', 'read_comments'},
                 'twitter': {'search', 'read', 'read_comments'},
                 'reddit': {'search', 'read', 'read_comments'}}
    if request.action not in supported[request.platform]:
        raise ReachError('UNSUPPORTED_OPERATION', '该平台尚未启用此操作')
    if request.action != 'search' and not request.url:
        raise ReachError('INVALID_URL', '读取操作需要目标链接')
    return request


async def execute(request: ReachRequest, settings, *, timeout, connection=None):
    started = time.monotonic()
    request = resolve(request)
    enabled = {value.strip() for value in settings.agent_reach_platforms.split(',')}
    if request.platform not in enabled:
        raise ReachError('CONFIG_REQUIRED', '该渠道尚未启用或完成配置')
    http, runner = PublicHTTP(), ToolRunner(settings.agent_reach_bin_dir)

    async def operation():
        if request.platform == 'web':
            return await read_web(request, http)
        if request.platform == 'rss':
            return await read_rss(request, http)
        if request.platform == 'github':
            return await github(request, http)
        if request.platform == 'exa':
            return await exa(request, http, settings.agent_reach_exa_api_key)
        if request.platform == 'youtube':
            if request.action == 'read_comments':
                return await youtube_comments(request, http, settings.agent_reach_youtube_api_key, connection)
            return await youtube(request, runner, http)
        if request.platform == 'xiaohongshu':
            from .xhs_adapter import XHSAdapter
            from .login_providers import xhs_origins
            return await XHSAdapter(xhs_origins(settings), connection).read(request)
        from .social_adapters import query_social
        return await query_social(request, runner, connection)

    try:
        result = await asyncio.wait_for(operation(), timeout=max(0, timeout))
    except (asyncio.TimeoutError, httpx.TimeoutException):
        raise ReachError('BUDGET_EXHAUSTED', '查询超过本次工具时间预算', retryable=True) from None
    except (httpx.HTTPError, OSError):
        raise ReachError('NETWORK_ERROR', '渠道网络或执行环境不可用', retryable=True) from None
    except (ValueError, KeyError, TypeError):
        raise ReachError('UPSTREAM_CHANGED', '上游资料格式变化，尚未取得可验证结果') from None
    response = result.to_agent_result()
    response.metadata['elapsed_ms'] = int((time.monotonic() - started) * 1000)
    return response
