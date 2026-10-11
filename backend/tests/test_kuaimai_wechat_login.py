import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import httpx
import pytest
from services.kuaimai_external import cookie_crypto
from services.kuaimai_external.login import LoginError
from services.kuaimai_external.wechat_login import WeChatLogin, PREFIX

class Redis:
    def __init__(self): self.values = {}; self.count = 0
    async def set(self, key, value, ex, nx=False):
        if nx and key in self.values: return False
        self.values[key] = value
        return True
    async def get(self, key): return self.values.get(key)
    async def eval(self, script, n, key, *args):
        if 'incr' in script:
            self.count += 1
            return self.count
        if self.values.get(key) == args[0]:
            del self.values[key]
            return 1
        return 0

@pytest.fixture
def setup(monkeypatch):
    async def encrypt(*, org_id, plaintext): return org_id + ':' + plaintext
    async def decrypt(*, org_id, stored):
        if not stored.startswith(org_id + ':'): raise ValueError('different organization')
        return stored[len(org_id) + 1:]
    monkeypatch.setattr(cookie_crypto, 'encrypt_cookie', encrypt)
    monkeypatch.setattr(cookie_crypto, 'decrypt_cookie', decrypt)
    db = MagicMock()
    db.table.return_value.upsert.return_value.execute = AsyncMock(return_value=SimpleNamespace(data=[{}, {}]))
    return Redis(), db

def transport(status=408, redirect='/index.html', identity=None, check=None, calls=None):
    def handle(req):
        path = req.url.path
        if path == '/login.html': return httpx.Response(200, text='login')
        if path == '/account/login/count': return httpx.Response(200, json={'data': {'result': 0}})
        if path == '/connect/qrconnect':
            assert req.url.params['redirect_uri'] == 'https://erp.superboss.cc/account/wxAuth'
            return httpx.Response(200, text='<img src="/connect/qrcode/anonymous-uuid"><script>var usenewdomain = \'1\' * 1</script>')
        if path.startswith('/connect/qrcode/'): return httpx.Response(200, content=b'png', headers={'content-type': 'image/png'})
        if path == '/connect/l/qrconnect':
            assert req.url.host == 'lp.open.weixin.qq.com'
            return httpx.Response(200, text=f'window.wx_errcode={status};window.wx_code="one-use-code";')
        if path == '/account/wxAuth':
            if calls is not None: calls.append(True)
            return httpx.Response(302, headers={'location': redirect, 'set-cookie': '_censeid=private-session; Secure; Path=/'})
        if path == '/index.html': return httpx.Response(200, text='ERP')
        if path == '/user/info':
            assert '_censeid=private-session' in req.headers['cookie']
            return httpx.Response(200, json=identity or {'result': 1, 'data': {'companyId': 123, 'otherCompanies': [{'companyId': 456}]}})
        if path == '/kmzk/meal/getFunctionList':
            assert req.headers['companyid'] == '123'
            return httpx.Response(200, json=check if check is not None else {'result': 1})
        raise AssertionError(str(req.url))
    return httpx.MockTransport(handle)

@pytest.mark.asyncio
async def test_qr_binding_waiting_and_cancellation(setup):
    redis, db = setup; service = WeChatLogin(redis, 'org', 'admin', transport=transport())
    session = await service.start()
    assert session['state'] == 'waiting' and session['image'].startswith('data:image/png;base64,')
    assert 'uuid' not in session and 'cookies' not in session
    assert (await service.poll(db, session['id']))['state'] == 'waiting'
    assert (await service.poll(db, session['id'], cancel=True))['state'] == 'cancelled'
    assert 'image' not in await service.poll(db, session['id'])
    assert 'cookies' not in await service.get_qr(session['id'])
    db.table.assert_not_called()

@pytest.mark.asyncio
async def test_confirmed_saves_both_sources_and_never_replays_code(setup):
    redis, db = setup; calls = []
    service = WeChatLogin(redis, 'org', 'admin', transport=transport(405, calls=calls))
    session = await service.start(); result = await service.poll(db, session['id'])
    assert result['state'] == 'connected'
    assert 'private-session' not in json.dumps(result) and 'one-use-code' not in json.dumps(result)
    rows = db.table.return_value.upsert.call_args.args[0]
    assert [row['source'] for row in rows] == ['thinktank', 'viperp']
    assert all(row['kuaimai_company_id'] == 123 for row in rows)
    assert (await service.poll(db, session['id']))['state'] == 'connected'
    assert calls == [True]
    assert 'cookies' not in await service.get_qr(session['id'])

@pytest.mark.asyncio
@pytest.mark.parametrize('status,state', [(404, 'confirming'), (403, 'cancelled'), (402, 'expired'), (500, 'expired'), (999, 'failed')])
async def test_scan_states(setup, status, state):
    redis, db = setup; service = WeChatLogin(redis, 'org', 'admin', transport=transport(status))
    session = await service.start()
    assert (await service.poll(db, session['id']))['state'] == state
    db.table.assert_not_called()

@pytest.mark.asyncio
@pytest.mark.parametrize('redirect', ['https://evil.example/account/wxAuth', 'http://erp.superboss.cc/index.html', '//evil.example/', '/login.html?fromWechat=bind', '/login.html?fromWechat=success&redirectUrl=https%3A%2F%2Fevil.example%2F'])
async def test_unsafe_or_unbound_callback_never_saves(setup, redirect):
    redis, db = setup; service = WeChatLogin(redis, 'org', 'admin', transport=transport(405, redirect=redirect))
    session = await service.start()
    assert (await service.poll(db, session['id']))['state'] == 'failed'
    db.table.assert_not_called()

@pytest.mark.asyncio
async def test_identity_and_session_check_failures_preserve_old_credentials(setup):
    redis, db = setup
    for kwargs in [{'identity': {'result': 1, 'data': {}}}, {'check': {'success': False}}]:
        service = WeChatLogin(redis, 'org', 'admin', transport=transport(405, **kwargs))
        session = await service.start()
        assert (await service.poll(db, session['id']))['state'] == 'failed'
    db.table.assert_not_called()

@pytest.mark.asyncio
async def test_other_actor_and_expiry_cannot_poll(setup):
    redis, db = setup; service = WeChatLogin(redis, 'org', 'admin', transport=transport())
    session = await service.start(); other = WeChatLogin(redis, 'org', 'member', transport=transport())
    with pytest.raises(LoginError, match='无权'): await other.poll(db, session['id'])
    payload = await service.get_qr(session['id']); payload['expires_at'] = time.time() - 1
    await service.put_qr(session['id'], payload)
    with pytest.raises(LoginError, match='过期'): await service.poll(db, session['id'])
    db.table.assert_not_called()

@pytest.mark.asyncio
async def test_concurrent_poll_is_rejected_without_consuming_session(setup):
    redis, db = setup; service = WeChatLogin(redis, 'org', 'admin', transport=transport(405))
    session = await service.start()
    await redis.set(PREFIX + 'lock:' + session['id'], 'other', ex=90)
    with pytest.raises(LoginError, match='正在处理'): await service.poll(db, session['id'])
    assert (await service.get_qr(session['id']))['state'] == 'waiting'
    db.table.assert_not_called()

@pytest.mark.asyncio
async def test_other_organization_cannot_read_qr(setup):
    redis, db = setup; service = WeChatLogin(redis, 'org', 'admin', transport=transport())
    session = await service.start()
    other = WeChatLogin(redis, 'other-org', 'admin', transport=transport())
    with pytest.raises(ValueError): await other.poll(db, session['id'])
    assert (await service.get_qr(session['id']))['state'] == 'waiting'
    db.table.assert_not_called()

@pytest.mark.asyncio
async def test_failed_exchange_is_not_replayed(setup):
    redis, db = setup; service = WeChatLogin(redis, 'org', 'admin', transport=transport(405))
    service.complete = AsyncMock(side_effect=httpx.ReadTimeout('timeout'))
    session = await service.start()
    with pytest.raises(httpx.ReadTimeout): await service.poll(db, session['id'])
    assert (await service.poll(db, session['id']))['state'] == 'failed'
    service.complete.assert_awaited_once()
    db.table.assert_not_called()

@pytest.mark.asyncio
async def test_member_cannot_start_qr():
    from api.routes.kuaimai_external import start_wechat_login
    from fastapi import HTTPException, Response
    with pytest.raises(HTTPException) as error:
        await start_wechat_login(SimpleNamespace(org_id='org', org_role='member', user_id='member'), Response())
    assert error.value.status_code == 403
