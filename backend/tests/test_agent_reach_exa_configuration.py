from types import SimpleNamespace
import pytest
from fastapi import Response, HTTPException
from api.routes import agent_reach as routes
from services.agent.agent_reach.contracts import ReachError, ReachRequest, ReachResult
from services.agent.agent_reach import public_adapters, service

@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['success','denied','invalid'])
async def test_save_checks_role_and_upstream_before_persisting(monkeypatch, mode):
    saved = []; calls = []
    class Store:
        def assert_admin(self):
            if mode == 'denied': raise ReachError('ACCESS_DENIED', '没有管理权限')
        def save_exa_key(self, key): saved.append(key)
        def exa_status(self, fallback): return {'configured':True,'source':'organization','verified_at':'now'}
    monkeypatch.setattr(routes, 'store', lambda *args: Store())
    monkeypatch.setattr(routes, 'get_settings', lambda: SimpleNamespace(agent_reach_exa_api_key='fallback'))
    async def fake_exa(request, http, key):
        calls.append(key)
        if mode == 'invalid': raise ReachError('PLATFORM_BLOCKED', '拒绝访问')
        return ReachResult('exa','exa-api',[])
    monkeypatch.setattr(public_adapters, 'exa', fake_exa)
    data = routes.SaveExaKey(api_key='test-only-key')
    assert 'test-only-key' not in repr(data)
    if mode == 'success':
        result = await routes.save_exa_configuration(data,None,None,Response())
        assert result['configured'] and 'test-only-key' not in str(result)
        assert saved == ['test-only-key']
    else:
        with pytest.raises(HTTPException) as error:
            await routes.save_exa_configuration(data,None,None,Response())
        assert error.value.status_code == (403 if mode == 'denied' else 422)
        assert not saved
        if mode == 'denied': assert not calls

@pytest.mark.asyncio
@pytest.mark.parametrize('override,expected', [(None,'server-key'),('org-key','org-key')])
async def test_execution_uses_organization_override(monkeypatch, override, expected):
    keys=[]
    async def fake_exa(request,http,key):
        keys.append(key); return ReachResult('exa','exa-api',[])
    monkeypatch.setattr(service, 'exa', fake_exa)
    config = SimpleNamespace(agent_reach_platforms='exa',agent_reach_bin_dir='/tmp',agent_reach_exa_api_key='server-key')
    await service.execute(ReachRequest(task='test',platform='exa'),config,timeout=1,exa_api_key=override)
    assert keys == [expected]

@pytest.mark.asyncio
async def test_tool_resolves_key_from_trusted_organization_context(monkeypatch):
    from core import config
    from services.agent.agent_reach import handler
    from services.agent.agent_result import AgentResult
    scopes=[]; keys=[]
    settings=SimpleNamespace(agent_reach_enabled=True,agent_reach_timeout_seconds=10,
        agent_reach_platforms='exa',agent_reach_exa_api_key='server-key')
    monkeypatch.setattr(config,'get_settings',lambda:settings)
    class Store:
        def __init__(self,pool,scope): scopes.append(scope)
        def exa_key(self,fallback):
            assert fallback=='server-key'
            return 'org-key'
    monkeypatch.setattr(handler,'Connections',Store)
    async def execute(request,settings,**kwargs):
        keys.append(kwargs['exa_api_key'])
        return AgentResult(summary='ok',status='success')
    monkeypatch.setattr(handler,'execute',execute)
    executor=SimpleNamespace(execution_budget=None,db=SimpleNamespace(pool=None),
        org_id='00000000-0000-0000-0000-000000000001',user_id='00000000-0000-0000-0000-000000000011')
    result=await handler.execute_reach(executor,{'task':'search','platform':'exa','action':'search','query':'test'})
    assert result.status=='success' and keys==['org-key'], result
    assert scopes[0].org_id==executor.org_id and scopes[0].actor_user_id==executor.user_id
