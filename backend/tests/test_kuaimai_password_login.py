import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs
import httpx
import pytest
from services.kuaimai_external import login

class Redis:
    def __init__(self): self.values = {}; self.count = 0
    async def set(self, key, value, ex): self.values[key] = value
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
    async def encrypt(*, org_id, plaintext): return org_id+':'+plaintext
    async def decrypt(*, org_id, stored):
        if not stored.startswith(org_id+':'): raise ValueError('wrong org')
        return stored[len(org_id)+1:]
    monkeypatch.setattr(login.cookie_crypto, 'encrypt_cookie', encrypt)
    monkeypatch.setattr(login.cookie_crypto, 'decrypt_cookie', decrypt)
    db = MagicMock()
    db.table.return_value.upsert.return_value.execute = AsyncMock(return_value=SimpleNamespace(data=[{}, {}]))
    return Redis(), db

def transport(result=None, captcha=False, check=None):
    def handle(request):
        path = request.url.path
        if path == '/login.html': return httpx.Response(200, text='login')
        if path == '/account/login/count': return httpx.Response(200, json={'data': {'result': 20 if captcha else 0}})
        if path == '/gainValidateCode': return httpx.Response(200, content=b'png', headers={'content-type':'image/png'})
        if path == '/account/login':
            assert parse_qs(request.content.decode())['password'] == ['5F4DCC3B5AA765D61D8327DEB882CF99']
            return httpx.Response(200, json=result or {'result':1,'data':{'companyId':123}}, headers={'set-cookie':'_censeid=login-secret; Secure; Path=/'})
        if path == '/kmzk/meal/getFunctionList':
            assert request.headers['companyid'] == '123'
            assert '_censeid=login-secret' in request.headers['cookie']
            return httpx.Response(200, json=check if check is not None else {'result':1,'data':[]})
        raise AssertionError(path)
    return httpx.MockTransport(handle)

@pytest.mark.asyncio
async def test_atomic_save_and_replay_protection(setup):
    redis, db = setup
    service = login.ERPLogin(redis,'org','admin',transport=transport())
    session = await service.start()
    result = await service.submit(db,session['id'],'company','user','password','')
    assert result['state'] == 'connected'
    rows = db.table.return_value.upsert.call_args.args[0]
    assert [r['source'] for r in rows] == ['thinktank','viperp']
    assert all(r['cookie_full'].startswith('org:') for r in rows)
    assert 'login-secret' not in json.dumps(result)
    assert 'password' not in json.dumps(redis.values)
    with pytest.raises(login.LoginError): await service.submit(db,session['id'],'company','user','password','')
    assert db.table.return_value.upsert.call_count == 1

@pytest.mark.asyncio
async def test_captcha_and_actor_binding(setup):
    redis, db = setup
    service = login.ERPLogin(redis,'org','admin',transport=transport(captcha=True))
    session = await service.start()
    assert session['captcha_image'].startswith('data:image/png;')
    other = login.ERPLogin(redis,'org','member',transport=transport())
    with pytest.raises(login.LoginError,match='无权'): await other.submit(db,session['id'],'company','user','password','1')
    assert await redis.get(login.PREFIX+session['id'])
    await service.submit(db,session['id'],'company','user','password','1')

@pytest.mark.asyncio
@pytest.mark.parametrize('result,check', [({'result':0,'data':{}},None),({'result':9,'message':'https://secret-risk-url'},None),({'result':1,'data':{}},None),({'result':1,'data':{'companyId':123}},{'success':False,'msg':'未登录'})])
async def test_failed_login_keeps_old_credentials(setup,result,check):
    redis, db = setup
    service = login.ERPLogin(redis,'org','admin',transport=transport(result=result,check=check))
    session = await service.start()
    with pytest.raises(login.LoginError) as error: await service.submit(db,session['id'],'company','user','password','')
    assert 'secret-risk-url' not in str(error.value)
    db.table.assert_not_called()

@pytest.mark.asyncio
async def test_captcha_replaces_session(setup):
    redis, db = setup
    service = login.ERPLogin(redis,'org','admin',transport=transport(result={'result':0,'data':{'result':20}}))
    session = await service.start()
    result = await service.submit(db,session['id'],'company','user','password','')
    assert result['state'] == 'captcha' and result['id'] != session['id']
    assert await redis.get(login.PREFIX+session['id']) is None
    db.table.assert_not_called()

def test_ambiguous_company_rejected():
    with pytest.raises(login.LoginError): login.company_id({'companies':[{'companyId':1},{'companyId':2}]},{})

@pytest.mark.asyncio
async def test_member_denied():
    from api.routes.kuaimai_external import start_password_login
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error: await start_password_login(SimpleNamespace(org_id='org',org_role='member'))
    assert error.value.status_code == 403

@pytest.mark.asyncio
async def test_login_validation_never_returns_password():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routes.kuaimai_external import PasswordLoginIn
    from main import register_exception_handlers
    app = FastAPI()
    register_exception_handlers(app)
    @app.post('/api/admin/kuaimai/login/password')
    async def endpoint(body: PasswordLoginIn): return {}
    response = TestClient(app).post('/api/admin/kuaimai/login/password', json={'password':'must-not-leak'})
    assert response.status_code == 422
    assert 'must-not-leak' not in response.text
    assert response.json()['error']['details'] == [{'type':'invalid_request','loc':['body'],'msg':'请求字段不合法'}]

@pytest.mark.asyncio
async def test_other_organization_cannot_consume_session(setup):
    redis, db = setup
    service = login.ERPLogin(redis,'org','admin',transport=transport())
    session = await service.start()
    other = login.ERPLogin(redis,'other-org','admin',transport=transport())
    with pytest.raises(ValueError): await other.consume(session['id'])
    assert await redis.get(login.PREFIX+session['id'])
    db.table.assert_not_called()

@pytest.mark.asyncio
async def test_start_is_rate_limited(setup):
    redis, db = setup
    service = login.ERPLogin(redis,'org','admin',transport=transport())
    for _ in range(6): await service.start()
    with pytest.raises(login.LoginError,match='频繁'): await service.start()
