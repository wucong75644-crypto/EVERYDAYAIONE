"""Read adapters using documented JSON CLI contracts and isolated credentials."""
import json
import re
import time
from urllib.parse import urlsplit

from .contracts import ReachError, ReachItem, ReachResult


def credential_options(platform, connection):
    secret = connection['secret'] if connection else {}
    if platform == 'twitter':
        if not all(isinstance(secret.get(k), str) and secret[k] and not any(ord(c) < 32 for c in secret[k]) for k in ('auth_token', 'ct0')):
            raise ReachError('AUTH_REQUIRED', 'Twitter需要组织管理员连接专用账号')
        return {'env': {'TWITTER_AUTH_TOKEN': secret['auth_token'], 'TWITTER_CT0': secret['ct0']},
                'files': {'config.yaml': 'rateLimit:\n  maxRetries: 0\n  maxCount: 10\n'}}
    if platform == 'bilibili':
        if connection and not all(isinstance(secret.get(k), str) and secret[k] for k in ('sessdata', 'bili_jct')):
            raise ReachError('AUTH_REQUIRED', 'B站凭据需要sessdata和bili_jct')
        return {'files': {'.bilibili-cli/credential.json': json.dumps({**secret, 'saved_at': time.time()})}} if secret else {}
    if platform == 'reddit':
        if not isinstance(secret.get('cookies'), dict) or not secret['cookies'] or not all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in secret['cookies'].items()):
            raise ReachError('AUTH_REQUIRED', 'Reddit需要组织管理员连接专用账号')
        return {'files': {'.config/rdt-cli/credential.json': json.dumps({**secret, 'saved_at': time.time()})}}
    return {}


def target_id(platform, url):
    path = urlsplit(url).path
    pattern = {'twitter': r'/status/(\d+)', 'bilibili': r'/video/(BV[\w]{10})',
               'reddit': r'/comments/([a-z0-9]+)/'}[platform]
    match = re.search(pattern, path + '/')
    if not match:
        raise ReachError('INVALID_URL', '请使用完整的平台帖子或视频链接，不使用索引和短链接')
    return match[1]


def unwrap(payload):
    if not isinstance(payload, dict) or payload.get('ok') is not True or 'data' not in payload:
        raise ReachError('UPSTREAM_CHANGED', '上游JSON协议与已适配版本不匹配')
    return payload['data']


async def query_social(request, runner, connection):
    options = credential_options(request.platform, connection)
    if request.platform == 'twitter':
        args = ['search', '--max', str(request.max_results), '--json', '--', request.query] if request.action == 'search' else [
            'tweet', '--max', str(request.max_results), '--json', '--', target_id('twitter', request.url)]
        data = unwrap(await runner.run('twitter', args, **options))
        rows = data if isinstance(data, list) else [data] if isinstance(data, dict) and 'id' in data else data.get('tweets')
        if not isinstance(rows, list):
            raise ReachError('UPSTREAM_CHANGED', 'Twitter资料结构变化')
        if request.action == 'read_comments':
            root_id = target_id('twitter', request.url)
            rows = [row for row in rows if str(row.get('id')) != root_id]
        items = []
        for row in rows[:request.max_results]:
            tweet_id = str(row.get('id', ''))
            if not tweet_id.isdigit():
                raise ReachError('UPSTREAM_CHANGED', 'Twitter未返回帖子ID')
            author = row.get('author') or {}
            items.append(ReachItem(author.get('name') or '推文', 'https://x.com/i/status/' + tweet_id,
                row.get('articleText') or row.get('text') or '', 'comment' if request.action == 'read_comments' else 'full_text', row.get('created_at') or row.get('createdAt', '')))
        return ReachResult('twitter', 'twitter-cli', items,
            warnings=['当前返回推文正文，回复列表未完整覆盖。'] if request.action == 'read_comments' else [])
    if request.platform == 'bilibili':
        args = ['search', '--type', 'video', '--page', '1', '--max', str(request.max_results), '--json', '--', request.query] if request.action == 'search' else [
            'video', '--json', *(['--subtitle'] if request.action == 'transcript' else ['--comments'] if request.action == 'read_comments' else []), '--', target_id('bilibili', request.url)]
        data = unwrap(await runner.run('bili', args, **options))
        if request.action == 'search':
            rows = data.get('items')
            if not isinstance(rows, list):
                raise ReachError('UPSTREAM_CHANGED', 'B站搜索资料结构变化')
            items = [ReachItem(row.get('title', ''), 'https://www.bilibili.com/video/' + row['bvid'],
                row.get('description') or row.get('desc') or '', 'metadata')
                for row in rows[:request.max_results] if re.fullmatch(r'BV[\w]{10}', row.get('bvid', ''))]
        elif request.action == 'transcript':
            subtitle = data.get('subtitle')
            if not isinstance(subtitle, dict) or subtitle.get('available') is not True or not subtitle.get('text'):
                raise ReachError('NO_TRANSCRIPT', 'B站未返回可用字幕，可能需要登录或字幕不可用')
            items = [ReachItem('B站字幕', request.url, subtitle['text'], 'transcript')]
        elif request.action == 'read_comments':
            comments = data.get('comments')
            if not isinstance(comments, list) or any(w.get('code') == 'comments_unavailable' for w in data.get('warnings', [])):
                raise ReachError('UNSUPPORTED_OPERATION', '当前后端没有返回评论资料')
            items = [ReachItem('B站评论', request.url + '?comment=' + str(row['id']), row.get('message', ''), 'comment') for row in comments[:request.max_results]]
        else:
            video = data.get('video') or {}
            items = [ReachItem(video.get('title', ''), request.url, video.get('desc') or video.get('description') or '', 'metadata')]
        return ReachResult('bilibili', 'bilibili-cli', items,
                           warnings=['仅返回当前页或视频元数据；未覆盖全部讨论。'] if request.action != 'transcript' else [])
    args = ['search', '--limit', str(request.max_results), '--json', '--compact', '--', request.query] if request.action == 'search' else [
        'read', '--json', '--limit', str(request.max_results), '--', target_id('reddit', request.url)]
    data = await runner.run('rdt', args, **options)
    if isinstance(data, dict) and 'ok' in data:
        data = unwrap(data)
    if request.action == 'read_comments':
        comments = data.get('comments') if isinstance(data, dict) else None
        if not isinstance(comments, list):
            raise ReachError('UPSTREAM_CHANGED', 'Reddit未返回评论列表')
        items = [ReachItem('Reddit评论 · ' + row.get('author', ''),
            request.url.split('?')[0].rstrip('/') + '/' + row['id'] + '/', row.get('body') or '', 'comment')
            for row in comments[:request.max_results] if re.fullmatch(r'[a-z0-9]+', row.get('id', ''))]
        return ReachResult('reddit', 'rdt-cli', items,
            warnings=['仅返回本次加载的顶层评论，未覆盖全部回复。'] if items else [])
    rows = data if isinstance(data, list) else data.get('posts', data.get('items'))
    if rows is None and isinstance(data, dict) and isinstance(data.get('post'), dict):
        rows = [data['post']]
    if not isinstance(rows, list):
        raise ReachError('UPSTREAM_CHANGED', 'Reddit资料结构变化')
    items = []
    for row in rows[:request.max_results]:
        permalink = row.get('permalink') or row.get('url', '')
        url = 'https://www.reddit.com' + permalink if permalink.startswith('/') else permalink
        text = row.get('selftext') or row.get('body') or ''
        items.append(ReachItem(row.get('title', ''), url, text,
            'comment' if request.action == 'read_comments' else 'full_text'))
    return ReachResult('reddit', 'rdt-cli', items)
