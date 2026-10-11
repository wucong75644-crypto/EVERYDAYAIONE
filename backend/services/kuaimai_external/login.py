"""Bound, short-lived ERP password login; never persist passwords or follow redirects."""
import base64
import hashlib
import json
import secrets
import time

import httpx

from . import cookie_crypto

ORIGIN = 'https://erp.superboss.cc'
PREFIX = 'kuaimai:login:'
TTL = 600


class LoginError(Exception):
    pass


def company_id(data, cookies):
    ids = set()
    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() == 'companyid' and str(item).isdigit() and int(item) > 0:
                    ids.add(int(item))
                elif isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(data)
    visit(cookies)
    if len(ids) != 1:
        raise LoginError('登录响应未能唯一识别公司，请使用原有凭据导入方式；不会保存未验证的连接。')
    return ids.pop()


class ERPLogin:
    def __init__(self, redis, org, actor, *, transport=None):
        self.redis, self.org, self.actor, self.transport = redis, org, actor, transport

    def client(self, cookies=None):
        return httpx.AsyncClient(base_url=ORIGIN, cookies=cookies or {}, timeout=20,
            follow_redirects=False, transport=self.transport,
            headers={'Origin': ORIGIN, 'Referer': ORIGIN+'/login.html',
                     'User-Agent': 'Mozilla/5.0', 'X-Requested-With': 'XMLHttpRequest'})

    async def put(self, sid, payload):
        remain = int(payload['expires_at']-time.time())
        if remain <= 0:
            raise LoginError('登录会话已过期，请重新开始。')
        encrypted = await cookie_crypto.encrypt_cookie(org_id=self.org, plaintext=json.dumps(payload))
        await self.redis.set(PREFIX+sid, encrypted, ex=remain)

    async def consume(self, sid):
        raw = await self.redis.get(PREFIX+sid)
        if not raw:
            raise LoginError('登录会话已过期或已使用，请重新开始。')
        payload = json.loads(await cookie_crypto.decrypt_cookie(org_id=self.org, stored=raw))
        if payload['org'] != self.org or payload['actor'] != self.actor:
            raise LoginError('无权访问该登录会话。')
        # One-use consumption is atomic across workers. Never retry a password POST.
        script = "if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end"
        if payload['expires_at'] <= time.time() or not await self.redis.eval(script, 1, PREFIX+sid, raw):
            raise LoginError('登录会话已过期或正在使用，请重新开始。')
        return payload

    async def captcha(self, client):
        response = await client.get('/gainValidateCode', params={'key': 'l'})
        response.raise_for_status()
        kind = response.headers.get('content-type', '').split(';')[0]
        if kind not in {'image/png', 'image/jpeg', 'image/gif'} or len(response.content) > 512000:
            raise LoginError('快麦验证码响应异常，请稍后重试。')
        return 'data:'+kind+';base64,'+base64.b64encode(response.content).decode()

    async def start(self):
        # A bounded org/user rate limit also covers newly created sessions.
        key = f'{PREFIX}limit:{self.org}:{self.actor}'
        count = await self.redis.eval("local n=redis.call('incr',KEYS[1]); if n==1 then redis.call('expire',KEYS[1],60) end; return n", 1, key)
        if count > 6:
            raise LoginError('连接操作过于频繁，请一分钟后再试。')
        async with self.client() as client:
            page = await client.get('/login.html')
            page.raise_for_status()
            response = await client.post('/account/login/count', data={})
            response.raise_for_status()
            data = response.json()
            image = await self.captcha(client) if data.get('data', {}).get('result') == 20 else None
            payload = {'org': self.org, 'actor': self.actor, 'expires_at': time.time()+TTL,
                       'cookies': dict(client.cookies), 'captcha_required': bool(image)}
        sid = secrets.token_urlsafe(32)
        await self.put(sid, payload)
        return {'id': sid, 'expires_at': payload['expires_at'], 'captcha_image': image}

    async def submit(self, db, sid, company, username, password, validation_code):
        payload = await self.consume(sid)
        if payload['captcha_required'] and not validation_code:
            raise LoginError('请输入图片验证码后重新连接。')
        async with self.client(payload['cookies']) as client:
            response = await client.post('/account/login', data={
                'companyName': company, 'userName': username,
                'password': hashlib.md5(password.encode()).hexdigest().upper(),
                'salt': str(int(time.time()*1000)), 'validationCode': validation_code,
                'phoneVerifyCode': '', 'deviceId': ''})
            response.raise_for_status()
            result = response.json()
            if result.get('result') != 1:
                details = result.get('data') or {}
                if details.get('result') == 20:
                    image = await self.captcha(client)
                    payload.update(cookies=dict(client.cookies), captcha_required=True)
                    next_sid = secrets.token_urlsafe(32)
                    await self.put(next_sid, payload)
                    return {'state': 'captcha', 'id': next_sid, 'expires_at': payload['expires_at'],
                            'captcha_image': image, 'message': '请输入新的图片验证码，并重新输入密码。'}
                if result.get('result') == 9 or str(details.get('status', '')).startswith('login_verify'):
                    raise LoginError('快麦要求短信或身份验证。请先到快麦官网完成验证，再重新连接。')
                raise LoginError('快麦登录未成功，请核对公司名称、账号和密码；未自动重试。')
            cookies = dict(client.cookies)
            censeid = cookies.get('_censeid')
            if not censeid:
                raise LoginError('快麦未返回可用登录凭据，请先到官网完成账号验证。')
            cid = company_id(result, cookies)
            # Check the authenticated session before touching existing credentials.
            check = await client.post('/kmzk/meal/getFunctionList',
                json={'companyId': cid, 'version': 2}, headers={'companyid': str(cid)})
            check.raise_for_status()
            checked = check.json()
            if (not isinstance(checked, dict) or checked.get('success') is False
                or not (checked.get('result') == 1 or checked.get('success') is True)):
                raise LoginError('登录凭据未通过快麦验证，不会覆盖原有连接。')
            cookie = '; '.join(f'{key}={value}' for key, value in cookies.items())
        from datetime import datetime
        encrypted_censeid = await cookie_crypto.encrypt_cookie(org_id=self.org, plaintext=censeid)
        encrypted_cookie = await cookie_crypto.encrypt_cookie(org_id=self.org, plaintext=cookie)
        now = datetime.now().isoformat()
        connected = ['thinktank', 'viperp']
        # One SQL upsert keeps both sources consistent if persistence fails.
        saved = await db.table('kuaimai_external_credentials').upsert([
            {'org_id': self.org, 'source': source, 'kuaimai_company_id': cid,
             'censeid_cookie': encrypted_censeid, 'cookie_full': encrypted_cookie,
             'status': 'active', 'last_health_check_at': now, 'updated_at': now}
            for source in connected], on_conflict='org_id,source').execute()
        if not saved.data or len(saved.data) != 2:
            raise LoginError('凭据保存结果未确认，请刷新账号状态后检查。')
        return {'state': 'connected', 'company_id': cid, 'sources': connected,
                'message': '登录凭据已连接；各报表权限请通过数据源连接检查验证。'}
