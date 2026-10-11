"""ERP-bound WeChat QR sessions. Never execute upstream JavaScript or expose codes."""
import base64
import json
import re
import secrets
import time
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from loguru import logger

from . import cookie_crypto
from .login import ERPLogin, LoginError, ORIGIN, company_id

PREFIX = 'kuaimai:wechat:'
OPEN = 'https://open.weixin.qq.com'
CALLBACK = ORIGIN + '/account/wxAuth'
RELEASE = "if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end"


class RedirectRejected(LoginError):
    def __init__(self, message, parsed):
        super().__init__(message)
        # Do not retain URL queries, fragments, credentials, or opaque paths.
        try:
            port = parsed.port
        except ValueError:
            port = 'invalid'
        self.diagnostic = {'scheme': parsed.scheme, 'host': parsed.hostname,
            'port': port, 'path': parsed.path if parsed.path in {'/account/wxAuth', '/login.html', '/index.html', '/'} else '<other>',
            'query_fields': sorted(set(parse_qs(parsed.query)) & {'fromWechat', 'redirectUrl', 'code', 'state'})}


class WeChatLogin(ERPLogin):
    async def put_qr(self, sid, payload):
        ttl = max(1, int(payload['expires_at'] - time.time()))
        encrypted = await cookie_crypto.encrypt_cookie(org_id=self.org, plaintext=json.dumps(payload))
        await self.redis.set(PREFIX + sid, encrypted, ex=ttl)

    async def get_qr(self, sid):
        raw = await self.redis.get(PREFIX + sid)
        if not raw:
            raise LoginError('微信二维码已过期，请刷新二维码。')
        payload = json.loads(await cookie_crypto.decrypt_cookie(org_id=self.org, stored=raw))
        if payload['org'] != self.org or payload['actor'] != self.actor:
            raise LoginError('无权访问该扫码会话。')
        if payload['expires_at'] <= time.time():
            raise LoginError('微信二维码已过期，请刷新二维码。')
        return payload

    @staticmethod
    def public(sid, payload):
        return {key: value for key, value in {'id': sid, 'state': payload['state'],
            'expires_at': payload['expires_at'], 'message': payload.get('message', ''),
            'image': payload.get('image') if payload['state'] in {'waiting', 'confirming'} else None}.items() if value is not None}

    async def start(self):
        # Reuse ERP initialization, rate limiting and cookie isolation.
        prepared = await super().start()
        initial = await self.consume(prepared['id'])
        async with self.client() as client:
            page = await client.get(OPEN + '/connect/qrconnect', params={
                'appid': 'wx7b88e6599e417a3e', 'scope': 'snsapi_login', 'redirect_uri': CALLBACK,
                'state': '', 'login_type': 'jssdk', 'self_redirect': 'false', 'stylelite': '1', 'fast_login': '0'})
            page.raise_for_status()
            match = re.search(r'src=["\'](/connect/qrcode/([A-Za-z0-9_-]{8,128}))["\']', page.text)
            if not match:
                raise LoginError('微信未返回有效二维码，请稍后重试。')
            image = await client.get(OPEN + match[1])
            image.raise_for_status()
            kind = image.headers.get('content-type', '').split(';')[0]
            if kind not in {'image/png', 'image/jpeg'} or len(image.content) > 512000:
                raise LoginError('微信二维码图片响应异常，请重新连接。')
            poll_host = 'https://lp.open.weixin.qq.com' if re.search(r"usenewdomain\s*=\s*['\"]?1", page.text) else 'https://long.open.weixin.qq.com'
        sid = secrets.token_urlsafe(32)
        payload = {'org': self.org, 'actor': self.actor, 'expires_at': time.time() + 240,
            'cookies': initial['cookies'], 'uuid': match[2], 'poll_host': poll_host,
            'state': 'waiting', 'image': 'data:' + kind + ';base64,' + base64.b64encode(image.content).decode()}
        await self.put_qr(sid, payload)
        return self.public(sid, payload)

    @staticmethod
    def safe_redirect(location):
        target = urljoin(ORIGIN, location)
        parsed = urlsplit(target)
        if parsed.scheme != 'https' or parsed.hostname != 'erp.superboss.cc' or parsed.port not in {None, 443} or parsed.username or parsed.password:
            raise RedirectRejected('快麦扫码返回了不支持的跳转地址，连接已停止。', parsed)
        if parsed.path not in {'/account/wxAuth', '/login.html', '/index.html', '/'}:
            raise RedirectRejected('快麦要求额外账号验证，请先到官网完成验证。', parsed)
        return target

    async def complete(self, db, payload, code):
        async with self.client(payload['cookies']) as client:
            response = await client.get(CALLBACK, params={'code': code, 'state': ''})
            # Explicit bounded, same-origin redirects only; no token to arbitrary hosts.
            for _ in range(5):
                if response.status_code not in {301, 302, 303, 307, 308}:
                    break
                target = self.safe_redirect(response.headers.get('location', ''))
                query = parse_qs(urlsplit(target).query)
                outcome = query.get('fromWechat', [''])[0]
                if outcome == 'bind':
                    raise LoginError('此微信尚未绑定快麦账号，请先在快麦官网完成绑定。')
                if outcome == 'fail':
                    raise LoginError('快麦微信登录未成功，请先到快麦官网检查绑定或账号验证。')
                if outcome == 'success' and query.get('redirectUrl'):
                    target = self.safe_redirect(query['redirectUrl'][0])
                response = await client.get(target)
            if response.is_redirect:
                raise LoginError('快麦登录跳转次数异常，请重新连接。')
            response.raise_for_status()
            if not client.cookies.get('_censeid'):
                raise LoginError('快麦未返回登录凭据，请检查微信绑定或到官网完成验证。')
            # The official launcher obtains its current company from /user/info.
            info = await client.get('/user/info', params={'api_name': 'user_info'})
            info.raise_for_status()
            try:
                identity = info.json()
                if identity.get('result') != 1:
                    raise LoginError('扫码后未能确认快麦账号身份，请到官网完成验证。')
                # Inspect only the current user's company, not lists of accessible companies.
                current = identity.get('data') or {}
                cid = company_id({'companyId': current.get('companyId')}, {})
            except (ValueError, TypeError, AttributeError):
                raise LoginError('快麦账号身份响应异常，未保存连接。') from None
            return await self.verify_and_save(db, client, cid)

    async def poll(self, db, sid, *, cancel=False):
        payload = await self.get_qr(sid)
        lock = PREFIX + 'lock:' + sid
        token = secrets.token_urlsafe(24)
        if not await self.redis.set(lock, token, nx=True, ex=90):
            raise LoginError('该二维码正在处理，请稍后再试。')
        try:
            payload = await self.get_qr(sid)
            if payload['state'] in {'connected', 'failed', 'expired', 'cancelled'}:
                return self.public(sid, payload)
            if cancel:
                payload['state'] = 'cancelled'
            else:
                async with self.client() as client:
                    try:
                        response = await client.get(payload['poll_host'] + '/connect/l/qrconnect',
                            params={'uuid': payload['uuid'], **({'last': payload['last']} if 'last' in payload else {})}, timeout=8)
                    except httpx.ReadTimeout:
                        return self.public(sid, payload)
                    response.raise_for_status()
                    match = re.search(r'wx_errcode\s*=\s*(\d+)', response.text)
                    if not match:
                        raise LoginError('微信扫码状态响应异常，请刷新二维码。')
                    status = int(match[1]); payload['last'] = status
                    if status == 405:
                        code = re.search(r'wx_code\s*=\s*["\']([A-Za-z0-9_-]{1,256})["\']', response.text)
                        if not code:
                            raise LoginError('微信授权响应异常，请重新扫码。')
                        # Claim the one-use authorization before any external exchange.
                        payload['state'] = 'failed'
                        payload['message'] = '登录处理未完成，请检查账号状态后重新扫码。'
                        await self.put_qr(sid, payload)
                        result = await self.complete(db, payload, code[1])
                        payload.update(state='connected', message=result['message'])
                    elif status == 404:
                        payload.update(state='confirming', message='已扫码，请在微信中确认登录。')
                    elif status == 403:
                        payload.update(state='cancelled', message='你已取消微信授权，请重新扫码。')
                    elif status in {402, 500}:
                        payload.update(state='expired', message='微信二维码已失效，请刷新二维码。')
                    elif status != 408:
                        raise LoginError('微信返回了未知登录状态，请刷新二维码。')
            if payload['state'] in {'connected', 'failed', 'expired', 'cancelled'}:
                for key in ('cookies', 'uuid', 'image', 'last', 'poll_host'):
                    payload.pop(key, None)
            await self.put_qr(sid, payload)
            return self.public(sid, payload)
        except LoginError as error:
            if isinstance(error, RedirectRejected):
                payload['redirect_diagnostic'] = error.diagnostic
                logger.warning('Kuaimai WeChat redirect rejected | diagnostic={}', json.dumps(error.diagnostic, ensure_ascii=True))
            payload.update(state='failed', message=str(error))
            for key in ('cookies', 'uuid', 'image', 'last', 'poll_host'):
                payload.pop(key, None)
            await self.put_qr(sid, payload)
            return self.public(sid, payload)
        finally:
            await self.redis.eval(RELEASE, 1, lock, token)
