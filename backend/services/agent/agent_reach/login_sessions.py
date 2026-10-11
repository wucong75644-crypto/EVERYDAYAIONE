"""Short-lived, encrypted Redis login state bound to organization and initiator."""
import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import asdict
import json
import httpx
import secrets
import time
from uuid import uuid4

from .contracts import ReachError
from .login_providers import LoginProviders, google_authorize, google_token, google_config, xhs_slots, youtube_credentials, YOUTUBE_SCOPE
from services.configuration.envelope import SecretEnvelope, SecretMaterialError

TTL = 600
PREFIX = 'reach:login:'
RELEASE = "if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end"


class LoginSessions:
    def __init__(self, store, settings, redis, *, providers=None):
        self.store, self.settings, self.redis = store, settings, redis
        self.providers = providers or LoginProviders(settings)

    async def authorize(self):
        await asyncio.to_thread(self.store.assert_admin)
        if self.redis is None:
            raise ReachError('CONFIG_REQUIRED', '登录会话服务暂不可用，请稍后重试')

    async def put(self, sid, payload):
        remain = int(payload['expires_at']-time.time())
        if remain <= 0:
            raise ReachError('LOGIN_EXPIRED', '连接会话已过期，请重新开始')
        envelope = self.store.secrets.encrypt_payload(scope_kind='organization',scope_id=self.store.scope.org_id,
            secret_name='reach.login.'+sid,payload_version=1,payload=payload)
        await self.redis.set(PREFIX+sid,json.dumps(asdict(envelope)),ex=remain)

    async def get(self, sid):
        raw = await self.redis.get(PREFIX+sid)
        if not raw:
            raise ReachError('LOGIN_EXPIRED', '连接会话已过期，请重新开始')
        try:
            payload = self.store.secrets.decrypt_payload(SecretEnvelope(**json.loads(raw)),
                scope_kind='organization',scope_id=self.store.scope.org_id,secret_name='reach.login.'+sid)
        except (ValueError, TypeError, KeyError, SecretMaterialError):
            raise ReachError('ACCESS_DENIED','不能访问其他组织的登录会话') from None
        if payload['actor'] != self.store.scope.actor_user_id or payload['expires_at'] <= time.time():
            raise ReachError('ACCESS_DENIED','不能访问其他成员的登录会话或会话已过期')
        return payload

    @asynccontextmanager
    async def locked(self, key, duration=120):
        token = secrets.token_urlsafe(24)
        if not await self.redis.set(key,token,nx=True,ex=duration):
            raise ReachError('LOGIN_BUSY','该账号正在连接，请稍后重试')
        try:
            yield
        finally:
            await self.redis.eval(RELEASE,1,key,token)

    @staticmethod
    def public(sid, payload):
        result = {'id':sid,'platform':payload['platform'],'state':payload['state'],
            'expires_at':payload['expires_at']}
        for key in ('image','authorize_url','connection','message'):
            if key in payload and (key not in {'image','authorize_url'} or payload['state'] in {'waiting','confirming'}):
                result[key] = payload[key]
        return result

    async def begin(self, platform, connection_id=None):
        await self.authorize()
        if platform not in {'bilibili','xiaohongshu','youtube'}:
            raise ReachError('UNSUPPORTED_OPERATION','该平台使用会话导入连接，请按页面说明操作')
        sid = str(uuid4())
        payload = {'platform':platform,'actor':self.store.scope.actor_user_id,
            'expires_at':time.time()+TTL,'state':'waiting','connection_id':connection_id}
        if connection_id:
            old = await asyncio.to_thread(self.store.admin_connection,connection_id)
            if old['platform'] != platform or old['status'] != 'active':
                raise ReachError('ACCESS_DENIED','原连接已断开或平台不匹配')
            payload['expected_version'] = old['credential_version']
        if platform == 'bilibili':
            payload['provider'], payload['image'] = await self.providers.bili_start()
            payload['expires_at'] = time.time()+180
        elif platform == 'youtube':
            google_config(self.settings)
            verifier = secrets.token_urlsafe(48); state = secrets.token_urlsafe(32)
            payload['provider'] = {'verifier':verifier,'state':state}
            payload['authorize_url'] = google_authorize(self.settings,state,verifier)
            await self.redis.set(PREFIX+'oauth:'+state,json.dumps({'sid':sid,
                'org':self.store.scope.org_id,'actor':self.store.scope.actor_user_id}),ex=TTL)
        else:
            slots = xhs_slots(self.settings,self.store.scope.org_id)
            if connection_id:
                record = await asyncio.to_thread(self.store.load,connection_id,platform)
                slot = next((item for item in slots if item['connection_id'] == connection_id), None)
                payload['provider'] = {'secret':record['secret']}
            else:
                used = await asyncio.to_thread(self.store.occupied_slots)
                slot = next((item for item in slots if item['connection_id'] not in used), None)
                if not slot:
                    raise ReachError('CONFIG_REQUIRED','本组织尚无可用的小红书扫码服务，请联系运维分配')
                payload['connection_id'] = slot['connection_id']
                payload['provider'] = {'secret':{'service_token':slot['service_token']}}
            key = PREFIX+'slot:'+payload['connection_id']
            if not await self.redis.set(key,sid,nx=True,ex=300):
                raise ReachError('LOGIN_BUSY','该小红书服务正在扫码，请稍后再试')
            payload['slot_lock'] = key
            # Once QR dispatch starts, a failed response may still leave a
            # remote scan running. Keep the slot reservation until its TTL.
            record = {'id':payload['connection_id'],'account_id':'','secret':payload['provider']['secret']}
            data = await self.providers.xhs(record).call('GET','login/qrcode')
            if data.get('is_logged_in') and connection_id:
                return await self.finish(sid,payload,record['secret'])
            if data.get('is_logged_in'):
                raise ReachError('ALREADY_LOGGED_IN','扫码服务已有登录账号，请检查连接；新服务需运维清理残留登录')
            image = data.get('img','')
            if not image.startswith(('data:image/png;base64,','data:image/jpeg;base64,')) or len(image)>1_000_000:
                raise ReachError('UPSTREAM_CHANGED','二维码图片格式无法验证')
            base64.b64decode(image.split(',',1)[1],validate=True)
            payload['image'] = image; payload['expires_at'] = time.time()+240
        await self.put(sid,payload)
        return self.public(sid,payload)

    async def finish(self, sid, payload, secret):
        platform = payload['platform']
        account, name = await self.providers.identity(platform,secret,connection_id=payload.get('connection_id') or '')
        row = await asyncio.to_thread(self.store.save_verified,platform,account,name,secret,
            connection_id=payload.get('connection_id'),expected_version=payload.get('expected_version'))
        # Connection is durable. Only public metadata remains in the session.
        payload['state'] = 'connected'; payload['connection'] = {k:str(row[k]) if k in {'id','checked_at'} else row[k]
            for k in ('id','platform','account_id','display_name','status','login_state','checked_at')}
        payload.pop('image',None); payload.pop('authorize_url',None); payload.pop('provider',None)
        if payload.get('slot_lock'):
            await self.redis.eval(RELEASE,1,payload['slot_lock'],sid)
        await self.put(sid,payload)
        return self.public(sid,payload)

    async def poll(self, sid):
        await self.authorize()
        async with self.locked(PREFIX+'lock:'+sid):
            payload = await self.get(sid)
            if payload['state'] in {'connected','cancelled','failed','expired'} or payload['platform']=='youtube':
                return self.public(sid,payload)
            if payload['platform']=='bilibili':
                secret = payload['provider'].get('secret')
                if not secret:
                    state, secret = await self.providers.bili_poll(payload['provider'])
                    payload['state'] = state
                if secret:
                    payload['provider']['secret'] = secret
                    payload['state'] = 'confirming'
                    await self.put(sid,payload)
                    return await self.finish(sid,payload,secret)
            else:
                record = {'id':payload['connection_id'],'account_id':'','secret':payload['provider']['secret']}
                data = await self.providers.xhs(record).call('GET','login/status')
                if data.get('is_logged_in') is True:
                    return await self.finish(sid,payload,record['secret'])
            await self.put(sid,payload)
            return self.public(sid,payload)

    async def cancel(self, sid):
        await self.authorize()
        async with self.locked(PREFIX+'lock:'+sid):
            payload = await self.get(sid)
            if payload['state'] == 'connected':
                return
            state = payload.get('provider',{}).get('state')
            if state:
                await self.redis.delete(PREFIX+'oauth:'+state)
            # XHS backend scan continues for four minutes; retain its slot lock
            # until TTL instead of permitting another session to adopt this scan.
            payload['state'] = 'cancelled'
            for key in ('provider','image','authorize_url'):
                payload.pop(key,None)
            await self.put(sid,payload)

    async def oauth_finish(self, sid, code, denied=False):
        await self.authorize()
        async with self.locked(PREFIX+'lock:'+sid):
            payload = await self.get(sid)
            if payload['platform'] != 'youtube' or payload['state'] != 'waiting':
                raise ReachError('ACCESS_DENIED','授权会话已使用或已取消')
            # Consumed before token exchange: an interrupted exchange never replays.
            payload['state'] = 'failed'; await self.put(sid,payload)
            if denied:
                payload['message'] = '授权未完成，请重新连接'; await self.put(sid,payload)
                return
            secret = await google_token(self.settings, {'grant_type':'authorization_code','code':code,
                'redirect_uri':google_config(self.settings),'code_verifier':payload['provider']['verifier']},self.providers.http)
            if 'scope' in secret and not set(YOUTUBE_SCOPE.split()).issubset(secret['scope'].split()):
                raise ReachError('AUTH_REQUIRED','YouTube授权权限不完整，请重新连接并同意所需权限')
            if not secret.get('refresh_token'):
                raise ReachError('AUTH_REQUIRED','平台未返回长期授权，请重新同意授权')
            return await self.finish(sid,payload,secret)

    async def check(self, connection_id):
        await self.authorize()
        row = await asyncio.to_thread(self.store.load,connection_id,
            (await asyncio.to_thread(self.store.admin_connection,connection_id))['platform'])
        state, message = 'connected', '账号连接有效'
        try:
            if row['platform']=='youtube':
                row = await youtube_credentials(row,self.settings,self.providers.http)
            account, _ = await self.providers.identity(row['platform'],row['secret'],connection_id=connection_id)
            if account != row['account_id']:
                raise ReachError('ACCOUNT_MISMATCH','实际登录账号与连接不一致，请使用原账号重新连接')
        except ReachError as error:
            state = 'expired' if error.code in {'AUTH_EXPIRED','AUTH_REQUIRED','ACCOUNT_MISMATCH','PLATFORM_BLOCKED'} else 'error'
            message = str(error)
        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
            state, message = 'error', '连接检查暂时失败，请稍后重试'
        await asyncio.to_thread(self.store.record_health,connection_id,row['credential_version'],state)
        return {'state':state,'message':message}
