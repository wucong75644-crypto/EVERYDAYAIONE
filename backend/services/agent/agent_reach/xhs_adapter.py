"""XHS's documented REST API, one operator-owned daemon per connection."""
import json
import re
from urllib.parse import parse_qs, urlsplit

import httpx

from .contracts import ReachError, ReachItem, ReachResult
from .network import MAX_BYTES


class XHSAdapter:
    def __init__(self, origins_json, connection):
        if not connection:
            raise ReachError('AUTH_REQUIRED', '小红书需要连接组织专用账号')
        mapping = json.loads(origins_json)
        origin = mapping.get(str(connection['id']), '')
        parsed = urlsplit(origin)
        if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', '::1'}
                or not parsed.port or parsed.path not in {'', '/'} or parsed.query or parsed.fragment
                or parsed.username or parsed.password or list(mapping.values()).count(origin) != 1):
            raise ReachError('CONFIG_REQUIRED', '需要为该连接配置独立的本机小红书REST服务')
        self.origin = origin.rstrip('/')
        self.account_id = connection['account_id']
        self.token = connection['secret'].get('service_token')
        if not self.token:
            raise ReachError('CONFIG_REQUIRED', '小红书执行服务需要配置认证Token')

    async def call(self, method, path, body=None):
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=60) as client:
            async with client.stream(method, self.origin + '/api/v1/' + path, json=body,
                                     headers={'Authorization': 'Bearer ' + self.token}) as response:
                if response.status_code in {401, 403}:
                    raise ReachError('AUTH_EXPIRED', '小红书执行服务认证失效')
                if response.status_code != 200:
                    raise ReachError('PLATFORM_BLOCKED', '小红书操作未完成，请检查登录态或平台限制')
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise ReachError('OUTPUT_LIMIT', '小红书返回资料超过上限')
                payload = json.loads(data)
        if payload.get('success') is not True:
            raise ReachError('UPSTREAM_CHANGED', '小红书执行服务没有确认成功')
        return payload.get('data')

    @staticmethod
    def target(url):
        parsed = urlsplit(url)
        match = re.search(r'/(?:explore|discovery/item)/([a-zA-Z0-9]+)', parsed.path)
        token = parse_qs(parsed.query).get('xsec_token', [''])[0]
        if not match or not token:
            raise ReachError('INVALID_URL', '小红书详情需要搜索返回的完整帖子链接（含xsec_token）')
        return {'feed_id': match[1], 'xsec_token': token}

    async def identity(self):
        data = await self.call('GET', 'login/status')
        if not isinstance(data, dict) or data.get('is_logged_in') is not True:
            raise ReachError('AUTH_EXPIRED', '小红书账号登录已失效')
        if data.get('user_id') != self.account_id:
            raise ReachError('ACCOUNT_MISMATCH', '小红书执行服务当前账号与组织连接不一致')

    async def read(self, request):
        await self.identity()
        if request.action == 'search':
            data = await self.call('POST', 'feeds/search', {'keyword': request.query})
            rows = data.get('feeds')
            if not isinstance(rows, list):
                raise ReachError('UPSTREAM_CHANGED', '小红书搜索格式变化')
            items = []
            from urllib.parse import urlencode
            for row in rows[:request.max_results]:
                note = row.get('noteCard') or {}
                items.append(ReachItem(note.get('displayTitle') or '小红书帖子',
                    'https://www.xiaohongshu.com/explore/' + row['id'] + '?' +
                    urlencode({'xsec_token': row.get('xsecToken', ''), 'xsec_source': 'pc_search'}),
                    note.get('displayTitle') or '', 'snippet'))
            return ReachResult('xiaohongshu', 'xhs-rest', items,
                warnings=['搜索仅返回卡片摘要，需读取帖子详情。'] if items else [])
        data = await self.call('POST', 'feeds/detail', {**self.target(request.url),
            'load_all_comments': False,
            'comment_config': {'max_comment_items': request.max_results, 'click_more_replies': False}})
        return ReachResult('xiaohongshu', 'xhs-rest', [ReachItem('小红书帖子详情', request.url,
            json.dumps(data, ensure_ascii=False), 'comment' if request.action == 'read_comments' else 'full_text')],
            warnings=['仅包含本次加载的正文和评论，未覆盖全部讨论。'])

    async def write(self, request, *, media=None):
        await self.identity()
        if request.action in {'create_post', 'upload_video'}:
            if not media:
                raise ReachError('MEDIA_REQUIRED', '必须选择已授权的媒体素材')
            body = {'title':request.title,'content':request.content,'tags':request.tags,
                'visibility':'公开可见' if request.visibility == 'public' else '仅自己可见'}
            if request.action == 'create_post':
                body['images'] = [item['path'] for item in media]
                data = await self.call('POST', 'publish', body)
            else:
                body['video'] = media[0]['path']
                data = await self.call('POST', 'publish_video', body)
            if not isinstance(data, dict) or data.get('status') != '发布完成':
                raise ReachError('WRITE_UNCERTAIN', '小红书没有确认提交完成，请先核对账号')
            return ReachResult('xiaohongshu', 'xhs-rest', status='partial',
                receipt={'action':request.action,'account_id':self.account_id,'stage':'backend_submitted',
                    'visibility':request.visibility,'remote_id':''},
                warnings=['执行服务报告提交完成，未返回帖子ID；需在平台核验发布及审核状态。'])
        body = self.target(request.url)
        if request.action == 'create_comment':
            body['content'] = request.content
            data = await self.call('POST', 'feeds/comment', body)
        else:
            body['unlike'] = not request.liked
            data = await self.call('POST', 'feeds/like', body)
        if not isinstance(data, dict) or data.get('success') is not True:
            raise ReachError('WRITE_UNCERTAIN', '平台没有返回可核验的操作回执，不能重新发送')
        return ReachResult('xiaohongshu', 'xhs-rest', receipt={'action': request.action, 'target': request.url,
                            'account_id': self.account_id, 'confirmed': True})
