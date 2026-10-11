"""Explicit write operations. No retries or backend switching after dispatch."""
import json
from urllib.parse import parse_qs, urlsplit

from .contracts import ReachError, ReachResult, youtube_id
from .social_adapters import credential_options, target_id
from .xhs_adapter import XHSAdapter
from .login_providers import xhs_origins


def check_write(request):
    supported = {'twitter': {'create_post', 'create_comment', 'set_like'},
                 'bilibili': {'create_post', 'create_comment', 'set_like', 'upload_video'},
                 'xiaohongshu': {'create_post', 'create_comment', 'set_like', 'upload_video'},
                 'youtube': {'create_comment', 'set_like', 'upload_video'},
                 'reddit': {'create_post', 'create_comment', 'set_like'}}
    if request.action not in supported.get(request.platform, set()):
        raise ReachError('UNSUPPORTED_OPERATION', '当前平台的该写入操作尚未通过接口核验，不能执行')


async def write(request, connection, runner, http, settings, *, media=None):
    check_write(request)
    platform = request.platform
    if platform == 'xiaohongshu':
        return await XHSAdapter(xhs_origins(settings), connection).write(request, media=media)
    if platform == 'youtube' and request.action == 'upload_video':
        from .video_upload import youtube_upload
        return await youtube_upload(request, connection, http, media)
    if platform == 'bilibili' and request.action == 'upload_video':
        payload = await runner.run('bili-bridge', [], input_json={'request': request.model_dump(exclude={'media'}),
            'account_id': connection['account_id'], 'secret': connection['secret'], 'media': media})
        return ReachResult(platform, 'bilibili-api', receipt=payload['receipt'], status='partial',
            warnings=['已提交视频投稿，平台处理与审核状态尚未核验。'])
    if platform == 'youtube':
        video_id = youtube_id(request.url)
        token = connection['secret'].get('access_token')
        if not token:
            raise ReachError('AUTH_REQUIRED', 'YouTube写入需要管理员授权的账号OAuth Token')
        headers = {'Authorization': 'Bearer ' + token}
        # Check current identity before any external write.
        identity = json.loads(await http.get('https://www.googleapis.com/youtube/v3/channels?part=id&mine=true', headers=headers))
        ids = [row['id'] for row in identity.get('items', [])]
        if connection['account_id'] not in ids:
            raise ReachError('ACCOUNT_MISMATCH', '当前授权账号与组织连接不一致')
        if request.action == 'set_like':
            url = 'https://www.googleapis.com/youtube/v3/videos/rate?id=' + video_id + '&rating=' + ('like' if request.liked else 'none')
            await http.request('POST', url, headers=headers)
            receipt = {'action': 'set_like', 'target': request.url, 'liked': request.liked}
        else:
            payload = {'snippet': {'channelId': connection['account_id'], 'videoId': video_id, 'topLevelComment': {'snippet': {'textOriginal': request.content}}}}
            data = json.loads(await http.request('POST', 'https://www.googleapis.com/youtube/v3/commentThreads?part=snippet',
                                                 headers=headers, json=payload))
            if not data.get('id'):
                raise ReachError('WRITE_UNCERTAIN', 'YouTube没有返回评论ID，请勿重复提交')
            receipt = {'action': 'create_comment', 'remote_id': data['id'], 'target': request.url}
        return ReachResult(platform, 'youtube-api', receipt=receipt)
    if platform == 'reddit':
        credential_options(platform, connection)
        if request.action == 'create_post' and not request.title.strip():
            raise ReachError('INVALID_ARGUMENTS', 'Reddit发布需要标题和目标社区链接')
        payload = await runner.run('reddit-bridge', [], input_json={'request': request.model_dump(),
            'account_id': connection['account_id'], 'secret': connection['secret']})
        return ReachResult(platform, 'rdt-api', receipt=payload['receipt'])
    options = credential_options(platform, connection)
    binary = 'twitter' if platform == 'twitter' else 'bili'
    identity = await runner.run(binary, ['whoami', '--json'], **options)
    data = identity.get('data', {})
    user = data.get('user') or {}
    current_id = str(user.get('id') or user.get('mid') or '')
    if identity.get('ok') is not True or current_id != connection['account_id']:
        raise ReachError('ACCOUNT_MISMATCH', '当前登录账号与组织连接不一致')
    if platform == 'bilibili' and request.action == 'create_comment':
        payload = await runner.run('bili-bridge', [], input_json={'request': request.model_dump(),
            'bvid': target_id(platform, request.url), 'account_id': connection['account_id'], 'secret': connection['secret']})
        return ReachResult(platform, 'bilibili-api', receipt=payload['receipt'])
    if request.action == 'create_post':
        args = ['post' if platform == 'twitter' else 'dynamic-post', '--json', '--', request.content]
    elif request.action == 'create_comment':
        args = ['reply', '--json', '--', target_id(platform, request.url), request.content]
    else:
        args = ['like' if platform == 'bilibili' or request.liked else 'unlike', '--json', *(['--undo'] if platform == 'bilibili' and not request.liked else []), '--', target_id(platform, request.url)]
    payload = await runner.run(binary, args, **options)
    # twitter-cli's writes return a raw success receipt; bilibili wraps data.
    receipt = payload.get('data', {}) if 'ok' in payload else payload
    if not isinstance(receipt, dict) or receipt.get('success') is not True:
        raise ReachError('WRITE_UNCERTAIN', '平台未返回明确操作回执，请勿重复提交')
    if request.action != 'set_like' and not (receipt.get('id') or receipt.get('dynamic_id')):
        raise ReachError('WRITE_UNCERTAIN', '平台未返回发布ID，请勿重复提交')
    safe = {'action': request.action, 'target': request.url,
            'remote_id': str(receipt.get('id') or receipt.get('dynamic_id') or ''),
            'account_id': connection['account_id']}
    if platform == 'twitter' and safe['remote_id'].isdigit():
        safe['url'] = 'https://x.com/i/status/' + safe['remote_id']
    return ReachResult(platform, binary, receipt=safe)
