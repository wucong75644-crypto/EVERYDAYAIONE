"""Login contracts without real accounts or platform writes."""
import json
import time
from types import SimpleNamespace
from uuid import uuid4
import pytest
from services.agent.agent_reach.contracts import ReachError
from services.agent.agent_reach.login_providers import LoginProviders, google_authorize, youtube_credentials, xhs_slots
from services.agent.agent_reach.login_sessions import LoginSessions, PREFIX
from services.configuration.envelope import LocalKEKProvider
from services.configuration.material_service import SecretMaterialService

ORG='00000000-0000-0000-0000-000000000001'
ACTOR='00000000-0000-0000-0000-000000000011'
CID='00000000-0000-0000-0000-000000000021'

def settings(slots='{}'):
    return SimpleNamespace(agent_reach_google_client_id='test-client',agent_reach_google_client_secret='client-secret',
        agent_reach_google_redirect_uri='https://app.example/api/agent-reach/oauth/callback',
        agent_reach_bin_dir='/not-installed',agent_reach_xhs_origins_json='{}',agent_reach_xhs_slots_json=slots)

class Redis:
    def __init__(self): self.values={}
    async def set(self,key,value,ex=None,nx=False):
        if nx and key in self.values: return False
        self.values[key]=value; return True
    async def get(self,key): return self.values.get(key)
    async def delete(self,key): return self.values.pop(key,None)
    async def getdel(self,key): return self.values.pop(key,None)
    async def eval(self,script,n,key,token):
        if self.values.get(key)==token: return self.values.pop(key)
        return 0

class Store:
    def __init__(self,actor=ACTOR,org=ORG):
        self.scope=SimpleNamespace(actor_user_id=actor,org_id=org)
        self.secrets=SecretMaterialService(LocalKEKProvider(current_version='test',keyring={'test':b'k'*32}))
        self.saved=[]; self.admin=True; self.health=[]
    def assert_admin(self):
        if not self.admin: raise ReachError('ACCESS_DENIED','没有管理权限')
    def occupied_slots(self): return set()
    def save_verified(self,platform,account,name,secret,**kwargs):
        self.assert_admin(); self.saved.append((platform,account,name,secret,kwargs))
        return {'id':CID,'platform':platform,'account_id':account,'display_name':name,'status':'active','login_state':'connected','checked_at':'now'}
    def admin_connection(self,cid): return {'platform':'bilibili','status':'active','credential_version':7}
    def load(self,cid,platform): return {'id':cid,'platform':platform,'account_id':'123','secret':{},'credential_version':7}
    def record_health(self,*args): self.health.append(args)

class Providers:
    def __init__(self): self.calls=0; self.http=None
    async def bili_start(self): self.calls+=1; return {'qrcode_key':'sensitive-key'},'data:image/png;base64,aGVsbG8='
    async def bili_poll(self,payload): self.calls+=1; return 'connected',{'sessdata':'sensitive-cookie','bili_jct':'dummy'}
    async def identity(self,platform,secret,**kwargs): return '123','组织专用账号'

@pytest.mark.asyncio
async def test_encrypted_terminal_replay_does_not_poll_or_save_again():
    redis,store,providers=Redis(),Store(),Providers(); service=LoginSessions(store,settings(),redis,providers=providers)
    begin=await service.begin('bilibili')
    assert 'sensitive-key' not in json.dumps(begin) and 'sensitive-key' not in redis.values[PREFIX+begin['id']]
    done=await service.poll(begin['id'])
    assert done['state']=='connected' and 'sensitive-cookie' not in json.dumps(done)
    assert await service.poll(begin['id'])==done and providers.calls==2 and len(store.saved)==1

@pytest.mark.asyncio
async def test_session_requires_initiating_org_user_and_current_admin():
    redis,store=Redis(),Store(); service=LoginSessions(store,settings(),redis,providers=Providers())
    sid=(await service.begin('bilibili'))['id']
    for other in (Store(actor=str(uuid4())),Store(org=str(uuid4()))):
        with pytest.raises(ReachError,match='其他'):
            await LoginSessions(other,settings(),redis,providers=Providers()).poll(sid)
    store.admin=False
    with pytest.raises(ReachError,match='管理权限'): await service.poll(sid)

@pytest.mark.asyncio
async def test_cancel_and_expired_session_cannot_save():
    store=Store(); redis=Redis(); service=LoginSessions(store,settings(),redis,providers=Providers())
    sid=(await service.begin('bilibili'))['id']; await service.cancel(sid)
    assert (await service.poll(sid))['state']=='cancelled' and not store.saved
    redis.values.pop(PREFIX+sid)
    with pytest.raises(ReachError,match='过期'): await service.poll(sid)

@pytest.mark.asyncio
async def test_reconnect_binds_original_connection_and_version():
    store=Store(); service=LoginSessions(store,settings(),Redis(),providers=Providers())
    sid=(await service.begin('bilibili',CID))['id']; await service.poll(sid)
    assert store.saved[0][-1]=={'connection_id':CID,'expected_version':7}

@pytest.mark.asyncio
async def test_oauth_pkce_single_use_denial_and_cancel():
    from urllib.parse import parse_qs,urlsplit
    redis=Redis(); service=LoginSessions(Store(),settings(),redis,providers=Providers())
    begin=await service.begin('youtube'); query=parse_qs(urlsplit(begin['authorize_url']).query)
    assert query['code_challenge_method']==['S256'] and query['access_type']==['offline']
    assert 'client-secret' not in begin['authorize_url'] and 'verifier' not in json.dumps(begin)
    state=query['state'][0]; raw=json.loads(await redis.getdel(PREFIX+'oauth:'+state))
    assert raw['actor']==ACTOR and raw['org']==ORG and await redis.getdel(PREFIX+'oauth:'+state) is None
    await service.oauth_finish(begin['id'],'',denied=True)
    assert (await service.poll(begin['id']))['state']=='failed'
    with pytest.raises(ReachError): await service.oauth_finish(begin['id'],'reuse')
    another=await service.begin('youtube'); otherstate=parse_qs(urlsplit(another['authorize_url']).query)['state'][0]
    await service.cancel(another['id']); assert await redis.get(PREFIX+'oauth:'+otherstate) is None

@pytest.mark.asyncio
async def test_oauth_secret_never_public(monkeypatch):
    import services.agent.agent_reach.login_sessions as module
    async def token(*args): return {'access_token':'sensitive-access','refresh_token':'sensitive-refresh','expires_at':time.time()+3600}
    monkeypatch.setattr(module,'google_token',token)
    store=Store(); service=LoginSessions(store,settings(),Redis(),providers=Providers())
    sid=(await service.begin('youtube'))['id']; result=await service.oauth_finish(sid,'dummy-code')
    assert store.saved[0][3]['refresh_token']=='sensitive-refresh' and 'sensitive' not in json.dumps(result)

@pytest.mark.asyncio
async def test_expired_youtube_token_refresh_preserves_encrypted_original():
    class HTTP:
        async def request(self,*args,**kwargs):
            assert 'refresh_token=refresh' in kwargs['content'].decode()
            return b'{"access_token":"new","expires_in":3600}'
    original={'secret':{'access_token':'old','refresh_token':'refresh','expires_at':1}}
    result=await youtube_credentials(original,settings(),HTTP())
    assert result['secret']['access_token']=='new' and original['secret']['access_token']=='old'
    assert result['secret']['refresh_token']=='refresh'

def test_google_requires_exact_https_callback():
    config=settings(); config.agent_reach_google_redirect_uri='https://evil.example/other'
    with pytest.raises(ReachError): google_authorize(config,'state','verifier')

@pytest.mark.asyncio
@pytest.mark.parametrize('code,state',[(86101,'waiting'),(86090,'confirming'),(86038,'expired')])
async def test_bili_scan_states(code,state):
    class HTTP:
        async def request(self,*args,**kwargs): return json.dumps({'code':0,'data':{'code':code}}).encode(),{},{}
    assert await LoginProviders(settings(),http=HTTP()).bili_poll({'qrcode_key':'key'})==(state,None)

@pytest.mark.asyncio
async def test_bili_set_cookie_and_empty_credentials_rejection():
    class HTTP:
        cookies={'SESSDATA':'set-cookie-value','bili_jct':'csrf','DedeUserID':'123'}
        async def request(self,*args,**kwargs): return b'{"code":0,"data":{"code":0,"url":""}}',{},self.cookies
    http=HTTP(); provider=LoginProviders(settings(),http=http)
    state,secret=await provider.bili_poll({'qrcode_key':'key'})
    assert state=='connected' and secret['sessdata']=='set-cookie-value'
    http.cookies={}
    with pytest.raises(ReachError): await provider.bili_poll({'qrcode_key':'key'})

@pytest.mark.asyncio
async def test_check_account_mismatch_never_marks_connected():
    providers=Providers()
    async def wrong(*args,**kwargs): return 'other','其他账号'
    providers.identity=wrong; store=Store(); service=LoginSessions(store,settings(),Redis(),providers=providers)
    assert (await service.check(CID))['state']=='expired' and store.health[-1][-1]=='expired'

def test_xhs_slots_org_bound_and_never_shared():
    slot={'connection_id':CID,'origin':'http://127.0.0.1:18060','service_token':'test-only'}
    config=settings(json.dumps({ORG:[slot]}))
    assert xhs_slots(config,ORG)[0]['connection_id']==CID and xhs_slots(config,str(uuid4()))==[]
    config.agent_reach_xhs_slots_json=json.dumps({ORG:[slot],str(uuid4()):[slot]})
    with pytest.raises(ReachError): xhs_slots(config,ORG)

@pytest.mark.asyncio
async def test_cancel_xhs_retains_slot_until_backend_scan_expires():
    slot={'connection_id':CID,'origin':'http://127.0.0.1:18060','service_token':'test-only'}
    class XHS:
        async def call(self,*args): return {'img':'data:image/png;base64,aGVsbG8=','is_logged_in':False}
    providers=Providers(); providers.xhs=lambda record:XHS()
    service=LoginSessions(Store(),settings(json.dumps({ORG:[slot]})),Redis(),providers=providers)
    sid=(await service.begin('xiaohongshu'))['id']; await service.cancel(sid)
    with pytest.raises(ReachError,match='扫码'): await service.begin('xiaohongshu')

def test_login_urls_redacted_in_http_and_access_logs():
    import logging
    from services.agent.agent_reach.login_logging import LoginURLFilter
    record=logging.LogRecord('httpx',20,'',0,'HTTP Request: %s %s',('GET','https://1.2.3.4/qrcode/poll?qrcode_key=secret'),None)
    LoginURLFilter().filter(record)
    assert 'secret' not in record.getMessage() and '[redacted]' in record.getMessage()
    record=logging.LogRecord('uvicorn.access',20,'',0,'%s %s %s %s %s',('client','GET','/api/agent-reach/oauth/callback?code=secret&state=secret','1.1',200),None)
    LoginURLFilter().filter(record)
    assert 'secret' not in record.getMessage()

@pytest.mark.asyncio
async def test_http_cookie_extraction_with_pinned_ip(monkeypatch):
    import httpx
    import services.agent.agent_reach.network as network
    async def address(url): return '8.8.8.8'
    monkeypatch.setattr(network,'public_address',address)
    client_type=httpx.AsyncClient
    def transport(request):
        assert request.url.host=='8.8.8.8' and request.headers['Host']=='passport.bilibili.com'
        return httpx.Response(200,headers=[('set-cookie','SESSDATA=dummy; Domain=.bilibili.com; Path=/; HttpOnly'),
            ('set-cookie','bili_jct=csrf; Domain=.bilibili.com; Path=/')],content=b'{}')
    monkeypatch.setattr(network.httpx,'AsyncClient',lambda **kwargs:client_type(transport=httpx.MockTransport(transport),**kwargs))
    _,_,cookies=await network.PublicHTTP().request('GET','https://passport.bilibili.com/qrcode/poll?qrcode_key=hidden',include_cookies=True)
    assert cookies=={'SESSDATA':'dummy','bili_jct':'csrf'}

@pytest.mark.asyncio
async def test_bili_verification_failure_never_exposes_false_connected_state():
    providers=Providers(); original=providers.identity
    async def fail(*args,**kwargs): raise ReachError('NETWORK_ERROR','暂不可用')
    providers.identity=fail; store=Store(); service=LoginSessions(store,settings(),Redis(),providers=providers)
    sid=(await service.begin('bilibili'))['id']
    with pytest.raises(ReachError): await service.poll(sid)
    assert (await service.get(sid))['state']=='confirming' and not store.saved
    providers.identity=original
    assert (await service.poll(sid))['state']=='connected' and providers.calls==2

@pytest.mark.asyncio
async def test_reddit_whoami_unwraps_fixed_structured_envelope():
    class Runner:
        async def run(self,name,args,**kwargs):
            assert name=='rdt' and args==['whoami','--json']
            return {'ok':True,'schema_version':'1','data':{'id':'abc123','name':'organization'}}
    assert await LoginProviders(settings(),runner=Runner()).identity('reddit',{'cookies':{'reddit_session':'dummy'}})==('abc123','organization')

@pytest.mark.asyncio
async def test_google_invalid_grant_is_reconnectable_auth_error():
    from services.agent.agent_reach.login_providers import google_token
    class HTTP:
        async def request(self,*args,**kwargs):
            assert kwargs['allowed_statuses']==(400,401)
            return b'{"error":"invalid_grant"}'
    with pytest.raises(ReachError) as error:
        await google_token(settings(),{'grant_type':'refresh_token','refresh_token':'revoked'},HTTP())
    assert error.value.code=='AUTH_EXPIRED'

@pytest.fixture
def login_api(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api import deps
    import api.routes.agent_reach as routes
    import core.redis as redis_module
    import services.agent.agent_reach.login_sessions as sessions
    import services.agent.agent_reach.login_providers as providers_module
    store=Store(); redis=Redis(); config=settings(); config.agent_reach_enabled=True
    monkeypatch.setattr(routes,'store',lambda org,db,response:store)
    monkeypatch.setattr(routes,'get_settings',lambda:config)
    monkeypatch.setattr(routes,'Connections',lambda pool,scope:store)
    async def get_redis(): return redis
    monkeypatch.setattr(redis_module,'get_redis',get_redis)
    monkeypatch.setattr(sessions,'LoginProviders',lambda settings:Providers())
    monkeypatch.setattr(providers_module,'LoginProviders',lambda settings:Providers())
    async def token(*args): return {'access_token':'sensitive','refresh_token':'sensitive-refresh','expires_at':time.time()+3600}
    monkeypatch.setattr(sessions,'google_token',token)
    app=FastAPI(); app.include_router(routes.router,prefix='/api')
    app.dependency_overrides[deps.get_org_context]=lambda:deps.OrgContext(user_id=ACTOR,org_id=ORG,org_role='owner')
    app.dependency_overrides[deps.get_db]=lambda:SimpleNamespace(pool=None)
    with TestClient(app) as client: yield client,store


def test_http_oauth_callback_one_use_and_public_poll(login_api):
    from urllib.parse import parse_qs,urlsplit
    client,store=login_api
    begin=client.post('/api/agent-reach/login-sessions',json={'platform':'youtube'})
    assert begin.status_code==201
    state=parse_qs(urlsplit(begin.json()['authorize_url']).query)['state'][0]
    callback=client.get('/api/agent-reach/oauth/callback',params={'state':state,'code':'dummy-code'})
    assert callback.status_code==200 and callback.headers['cache-control']=='no-store'
    assert 'sensitive' not in callback.text
    poll=client.get('/api/agent-reach/login-sessions/'+begin.json()['id'])
    assert poll.json()['state']=='connected' and 'sensitive' not in poll.text
    assert client.get('/api/agent-reach/oauth/callback',params={'state':state,'code':'replay'}).status_code==403
    assert len(store.saved)==1


def test_http_import_verifies_identity_and_rejects_client_org_override(login_api):
    client,store=login_api
    result=client.post('/api/agent-reach/session-import',json={'platform':'twitter','cookie_header':'auth_token=dummy; ct0=dummy'})
    assert result.status_code==201 and result.json()['account_id']=='123' and 'dummy' not in result.text
    assert store.saved[0][3]=={'auth_token':'dummy','ct0':'dummy'}
    assert client.post('/api/agent-reach/session-import',json={'platform':'twitter','cookie_header':'auth_token=dummy; ct0=dummy','org_id':str(uuid4())}).status_code==422


def test_http_non_admin_cannot_start_login(login_api):
    client,store=login_api; store.admin=False
    result=client.post('/api/agent-reach/login-sessions',json={'platform':'bilibili'})
    assert result.status_code==403 and not store.saved

@pytest.mark.asyncio
async def test_transient_check_failure_is_not_reported_as_login_expiry():
    import httpx
    providers=Providers()
    async def unavailable(*args,**kwargs): raise httpx.ConnectError('no network')
    providers.identity=unavailable; store=Store()
    result=await LoginSessions(store,settings(),Redis(),providers=providers).check(CID)
    assert result['state']=='error' and store.health[-1][-1]=='error'
