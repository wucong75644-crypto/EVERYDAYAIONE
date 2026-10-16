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
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=getattr(self, 'timeout', 60)) as client:
            async with client.stream(method, self.origin + '/api/v1/' + path, json=body,
                                     headers={'Authorization': 'Bearer ' + self.token}) as response:
                if response.status_code in {401, 403}:
                    raise ReachError('AUTH_EXPIRED', '小红书执行服务认证失效')
                if response.status_code == 429:
                    raise ReachError('RATE_LIMITED', '执行服务明确返回限频，已暂停账号访问')
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise ReachError('OUTPUT_LIMIT', '小红书返回资料超过上限')
                try:
                    payload = json.loads(data)
                except (ValueError, UnicodeError):
                    if response.status_code != 200:
                        raise ReachError('PLATFORM_BLOCKED', '小红书执行服务未完成操作') from None
                    raise
                if response.status_code != 200:
                    if path.startswith('commerce/'):
                        known = {'COMMERCE_AUTH_REQUIRED': ('AUTH_EXPIRED', '小红书商品读取要求登录'),
                                 'COMMERCE_TIMEOUT': ('BUDGET_EXHAUSTED', '小红书商品读取超过时间预算'),
                                 'COMMERCE_CHANGED': ('UPSTREAM_CHANGED', '小红书商品接口未返回可验证结果'),
                                 'COMMERCE_CAPTCHA': ('CAPTCHA_REQUIRED', '小红书商品读取要求平台安全验证')}
                        code, message = known.get(payload.get('code'), ('PLATFORM_BLOCKED', '小红书商品读取未完成'))
                        raise ReachError(code, message)
                    if path == 'feeds/search' and isinstance(payload, dict) and payload.get('code') == 'SEARCH_FEEDS_FAILED':
                        failure = payload.get('details')
                        known = {
                            'XHS_SEARCH_TIMEOUT': ('SEARCH_TIMEOUT', '小红书搜索页未在限定时间内加载出结果；请稍后再试'),
                            'XHS_SEARCH_AUTH_REQUIRED': ('AUTH_EXPIRED', '小红书搜索页要求登录，请重新连接账号'),
                            'XHS_SEARCH_CAPTCHA_REQUIRED': ('CAPTCHA_REQUIRED', '小红书搜索页要求安全验证，请先处理平台验证'),
                        }
                        if isinstance(failure, str) and failure in known:
                            code, message = known[failure]
                            raise ReachError(code, message)
                    raise ReachError('PLATFORM_BLOCKED', '小红书操作未完成，请检查登录态或平台限制')
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
        from .xhs_commerce import commerce_target, read_commerce
        target = commerce_target(request.url)
        if target and request.action != 'read':
            raise ReachError('UNSUPPORTED_OPERATION', '商品和店铺链接只支持read，买家评价尚未启用')
        await self.identity()
        if target:
            return await read_commerce(self, request, target)
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
                    'note_id：' + str(row['id']) + '\n' + (note.get('displayTitle') or ''), 'snippet'))
            return ReachResult('xiaohongshu', 'xhs-rest', items,
                warnings=['搜索仅返回卡片摘要，需读取帖子详情；读取时原样复制完整URL，必须保留xsec_token和xsec_source参数，不能只传笔记ID或删去问号后的内容。'] if items else [])
        comments_mode = request.action == 'read_comments'
        end = request.page * request.max_results
        data = await self.call('POST', 'feeds/detail', {**self.target(request.url),
            'load_all_comments': comments_mode,
            'comment_config': {'max_comment_items': end + 1 if comments_mode else 1,
                               'click_more_replies': comments_mode}})
        detail = data.get('data', data) if isinstance(data, dict) else None
        if not isinstance(detail, dict) or not isinstance(detail.get('note'), dict):
            raise ReachError('UPSTREAM_CHANGED', '小红书详情格式变化')
        note = detail['note']
        if note.get('noteId') and note['noteId'] != self.target(request.url)['feed_id']:
            raise ReachError('UPSTREAM_CHANGED', '详情返回的帖子ID与请求不一致')
        if not comments_mode:
            author = (note.get('user') or {}).get('nickname', '')
            interaction = note.get('interactInfo') or {}
            if not isinstance(interaction, dict):
                interaction = {}
            from services.social_ops.analytics import nullable_metrics
            target = self.target(request.url)['feed_id']
            remote_id = note.get('noteId')
            if remote_id and remote_id != target:
                raise ReachError('UPSTREAM_CHANGED', '详情返回的帖子ID与请求不一致')
            raw = {'likes': interaction.get('likedCount'), 'comments': interaction.get('commentCount'),
                   'saves': interaction.get('collectedCount'), 'shares': interaction.get('sharedCount')}
            raw = {k:(v[:64] if isinstance(v,str) else v if isinstance(v,(int,float,bool)) or v is None else None) for k,v in raw.items()}
            metadata = {'remote_id': remote_id or target, 'author_id': (note.get('user') or {}).get('userId'),
                        'raw_metrics': raw, 'adapter_version': 'xhs-rest:metrics-v1',
                        'source': 'feeds/detail.note.interactInfo', 'scope': 'public_cumulative',
                        'published_at_raw': note.get('time')}
            return ReachResult('xiaohongshu', 'xhs-rest', [ReachItem(
                note.get('title') or '小红书正文', request.url,
                '作者：' + author + '\n' + (note.get('desc') or ''), 'full_text',
                metrics=nullable_metrics(raw), metric_metadata=metadata)])
        comments = detail.get('comments') or {}
        rows = comments.get('list')
        if not isinstance(rows, list):
            raise ReachError('UPSTREAM_CHANGED', '小红书未返回评论列表')
        start = (request.page - 1) * request.max_results
        selected = rows[start:end]
        more = len(rows) > end
        warnings = ['仅包含本次加载的顶层评论及已展开回复，未保证覆盖全部楼中楼。']
        if len(rows) <= start and comments.get('hasMore'):
            raise ReachError('PLATFORM_BLOCKED', '平台未能加载到所请求评论页，请稍后重试；不能据此判断没有评论')
        if not more and comments.get('hasMore'):
            warnings.append('平台仍标记有更多评论，但本次未能加载；不能声称已读完全部评论。')
        items = [ReachItem('小红书评论', request.url.split('#')[0] + '#comment-' + str(row['id']),
                           json.dumps(row, ensure_ascii=False), 'comment', metric_metadata={'remote_id': note.get('noteId') or self.target(request.url)['feed_id'], 'author_id': (note.get('user') or {}).get('userId')}) for row in selected]
        return ReachResult('xiaohongshu', 'xhs-rest', items, warnings=warnings,
            pagination={'page': request.page, 'returned_count': len(selected),
                        'loaded_count': len(rows), 'platform_has_more': bool(comments.get('hasMore')),
                        'next_page': request.page + 1 if more and request.page < 100 else None})

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
