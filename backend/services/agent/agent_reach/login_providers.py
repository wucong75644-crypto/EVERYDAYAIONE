"""Fixed login endpoints; no browser cookie extraction or password handling."""
import base64
import hashlib
import json
import time
from urllib.parse import urlencode, urlsplit, parse_qs

from .contracts import ReachError
from .network import PublicHTTP
from .runner import ToolRunner
from .social_adapters import credential_options, unwrap
from .xhs_adapter import XHSAdapter

YOUTUBE_SCOPE = 'https://www.googleapis.com/auth/youtube.force-ssl https://www.googleapis.com/auth/youtube.upload'


def google_config(settings):
    uri = settings.agent_reach_google_redirect_uri or ''
    parsed = urlsplit(uri)
    if (not settings.agent_reach_google_client_id or not settings.agent_reach_google_client_secret
        or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
        or parsed.query or parsed.fragment or parsed.path != '/api/agent-reach/oauth/callback'):
        raise ReachError('CONFIG_REQUIRED', 'YouTube官方授权尚未配置，请联系管理员配置应用与HTTPS回调地址')
    return uri


def google_authorize(settings, state, verifier):
    return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id':settings.agent_reach_google_client_id, 'redirect_uri':google_config(settings),
        'response_type':'code', 'scope':YOUTUBE_SCOPE, 'access_type':'offline',
        'prompt':'consent select_account', 'state':state,
        'code_challenge':base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),
        'code_challenge_method':'S256'})


async def google_token(settings, fields, http=None):
    google_config(settings)
    data = json.loads(await (http or PublicHTTP()).request('POST', 'https://oauth2.googleapis.com/token',
        content=urlencode({'client_id':settings.agent_reach_google_client_id,
            'client_secret':settings.agent_reach_google_client_secret, **fields}).encode(),
        headers={'Content-Type':'application/x-www-form-urlencoded'}, allowed_statuses=(400,401)))
    if not isinstance(data.get('access_token'), str) or not data['access_token']:
        raise ReachError('AUTH_EXPIRED', 'YouTube授权已失效，请重新连接')
    data['expires_at'] = time.time() + int(data.get('expires_in', 3600))
    return {k:data[k] for k in ('access_token','refresh_token','expires_at','scope') if k in data}


async def youtube_credentials(connection, settings, http=None):
    secret = connection['secret']
    if secret.get('refresh_token') and float(secret.get('expires_at', 0)) <= time.time()+60:
        fresh = await google_token(settings, {'grant_type':'refresh_token','refresh_token':secret['refresh_token']}, http)
        return {**connection, 'secret':{**secret, **fresh}}
    return connection


def xhs_slots(settings, org_id):
    """Slots are operator-provisioned and bound to one organization globally."""
    from uuid import UUID
    try:
        groups = json.loads(getattr(settings, 'agent_reach_xhs_slots_json', '{}'))
        slots, ids, origins = [], set(), set()
        if not isinstance(groups, dict):
            raise ValueError()
        legacy = json.loads(settings.agent_reach_xhs_origins_json)
        for org, entries in groups.items():
            UUID(org)
            for entry in entries:
                cid = str(UUID(entry['connection_id']))
                origin = entry['origin'].rstrip('/')
                if cid in ids or cid in legacy or origin in origins or origin in {str(value).rstrip('/') for value in legacy.values()}:
                    raise ValueError()
                ids.add(cid); origins.add(origin)
                # Reuse the exact localhost origin/token validation used by operations.
                XHSAdapter(json.dumps({cid:origin}), {'id':cid,'account_id':'',
                    'secret':{'service_token':entry['service_token']}})
                if org == org_id:
                    slots.append({**entry,'connection_id':cid,'origin':origin})
        return slots
    except (ValueError, TypeError, KeyError, ReachError):
        raise ReachError('CONFIG_REQUIRED', '小红书专用扫码服务配置无效，请联系运维') from None


def xhs_origins(settings):
    mapping = json.loads(settings.agent_reach_xhs_origins_json)
    groups = json.loads(getattr(settings, 'agent_reach_xhs_slots_json', '{}'))
    for org in groups:
        for entry in xhs_slots(settings, org):
            mapping[entry['connection_id']] = entry['origin']
    return json.dumps(mapping)


class LoginProviders:
    def __init__(self, settings, *, http=None, runner=None):
        self.settings = settings
        self.http = http or PublicHTTP()
        self.runner = runner or ToolRunner(settings.agent_reach_bin_dir)

    def xhs(self, record):
        return XHSAdapter(xhs_origins(self.settings), record)

    async def identity(self, platform, secret, *, connection_id=''):
        if platform == 'xiaohongshu':
            data = await self.xhs({'id':connection_id, 'account_id':'', 'secret':secret}).call('GET','login/status')
            if data.get('is_logged_in') is not True or not data.get('user_id'):
                raise ReachError('AUTH_EXPIRED', '小红书尚未登录，或无法识别账号，请重新扫码')
            return str(data['user_id']), str(data.get('username') or data['user_id'])
        if platform == 'youtube':
            data = json.loads(await self.http.get('https://www.googleapis.com/youtube/v3/channels?part=id,snippet&mine=true',
                headers={'Authorization':'Bearer '+secret['access_token']}))
            items = data.get('items') or []
            if len(items) != 1 or not items[0].get('id'):
                raise ReachError('AUTH_REQUIRED', '请授权一个可用的YouTube频道')
            return items[0]['id'], items[0].get('snippet',{}).get('title') or items[0]['id']
        if platform == 'bilibili':
            credential_options(platform, {'secret':secret})
            data = json.loads(await self.http.get('https://api.bilibili.com/x/web-interface/nav',
                headers={'Cookie':'SESSDATA='+secret['sessdata']+'; bili_jct='+secret['bili_jct'],
                    'User-Agent':'Mozilla/5.0', 'Referer':'https://www.bilibili.com/'}))
            user = data.get('data') or {}
            if data.get('code') != 0 or user.get('isLogin') is not True or not user.get('mid'):
                raise ReachError('AUTH_EXPIRED', 'B站登录已失效，请重新扫码')
            return str(user['mid']), str(user.get('uname') or user['mid'])
        options = credential_options(platform, {'secret':secret})
        data = await self.runner.run('twitter' if platform == 'twitter' else 'rdt', ['whoami','--json'], **options)
        data = unwrap(data) if isinstance(data, dict) and 'ok' in data else data
        user = (data.get('user') or {}) if platform == 'twitter' else (data.get('user') or data)
        if not user.get('id'):
            raise ReachError('AUTH_EXPIRED', '无法验证平台账号，请检查登录会话')
        return str(user['id']), str(user.get('name') or user.get('screen_name') or user['id'])

    async def bili_start(self):
        data = json.loads(await self.http.get('https://passport.bilibili.com/x/passport-login/web/qrcode/generate?source=main-fe-header'))
        if data.get('code') != 0:
            raise ReachError('PLATFORM_BLOCKED', 'B站暂时无法生成二维码')
        payload = data['data']; url = payload['url']
        if urlsplit(url).scheme != 'https' or urlsplit(url).hostname != 'passport.bilibili.com':
            raise ReachError('UPSTREAM_CHANGED', 'B站返回的登录地址无法验证')
        image = await self.runner.run('qr-image', [], input_json={'text':url})
        return {'qrcode_key':payload['qrcode_key']}, image['image']

    async def bili_poll(self, payload):
        body, _, cookies = await self.http.request('GET',
            'https://passport.bilibili.com/x/passport-login/web/qrcode/poll?' + urlencode({
                'qrcode_key':payload['qrcode_key'], 'source':'main-fe-header'}), include_cookies=True)
        data = json.loads(body)
        if data.get('code') != 0:
            raise ReachError('PLATFORM_BLOCKED', 'B站扫码检查失败')
        event = data['data']; code = event['code']
        if code in {86101,86090,86038}:
            return {86101:'waiting',86090:'confirming',86038:'expired'}[code], None
        if code != 0:
            raise ReachError('UPSTREAM_CHANGED', 'B站返回未知扫码状态')
        # New responses may return credentials ONLY in Set-Cookie. The pinned
        # upstream library reads URL parameters only, which can silently be empty.
        query = parse_qs(urlsplit(event.get('url','')).query)
        values = {**{k:v[0] for k,v in query.items()}, **cookies}
        secret = {dest:values[src] for src,dest in [('SESSDATA','sessdata'),('bili_jct','bili_jct'),
            ('DedeUserID','dedeuserid'),('buvid3','buvid3')] if values.get(src)}
        credential_options('bilibili', {'secret':secret})
        return 'connected', secret
