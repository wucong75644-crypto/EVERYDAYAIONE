"""Refresh observer compatibility; all HTTP and token storage are local substitutes."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from services.kuaimai.client import KuaiMaiClient
from services.kuaimai.session_rotations import rotation_recorder
from services.task_execution.erp_postconditions import source_fingerprint
from core.db_scope import DatabaseScope,DatabaseAccessKind,AsyncScopedDatabaseClient


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['success','observer_failed','no_observer','no_new_token','same_token','provider_failed'])
async def test_original_refresh_survives_optional_observer_and_records_only_new_provider_session(monkeypatch,mode):
    observer=AsyncMock(side_effect=RuntimeError('local failure') if mode=='observer_failed' else None)
    client=KuaiMaiClient(app_key='local',app_secret='local',access_token='local',org_id=str(uuid4()),
        token_persister=AsyncMock(),token_rotation_recorder=None if mode=='no_observer' else observer)
    before=source_fingerprint(client)
    session={'accessToken':'local' if mode=='same_token' else 'new-local','refreshToken':'new-refresh'}
    if mode=='no_new_token':session.pop('accessToken')
    response=SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'success':mode!='provider_failed','session':session})
    client._client=SimpleNamespace(is_closed=False,post=AsyncMock(return_value=response))
    client._cache_token=AsyncMock();client._persist_token_to_db=AsyncMock()
    monkeypatch.setattr('services.kuaimai.erp_sync_healthcheck.push_token_refresh_alert',AsyncMock())
    assert await client._do_refresh_token() is (mode!='provider_failed')
    client._client.post.assert_awaited_once()
    if mode=='provider_failed':
        client._cache_token.assert_not_awaited();client._persist_token_to_db.assert_not_awaited()
    else:
        client._cache_token.assert_awaited_once();client._persist_token_to_db.assert_awaited_once()
    if mode in {'success','observer_failed'}:observer.assert_awaited_once_with(client._org_id,before,source_fingerprint(client))
    else:observer.assert_not_awaited()


def test_recorder_never_rebinds_existing_runtime_or_actor_authority():
    actor,org,other=map(str,(uuid4(),uuid4(),uuid4()))
    db=AsyncScopedDatabaseClient(SimpleNamespace(),DatabaseScope(actor,org,DatabaseAccessKind.RUNTIME))
    assert rotation_recorder(db,org,actor) is not None
    assert rotation_recorder(db,other,actor) is None
    assert rotation_recorder(db,org,other) is None
    worker=AsyncScopedDatabaseClient(SimpleNamespace(),DatabaseScope(None,None,DatabaseAccessKind.WORKER))
    assert rotation_recorder(worker,org) is not None
    assert rotation_recorder(worker,org,actor) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('success,token', [('false','new'),('true','new'),(1,'new'),(True,{'access':'new'}),(True,['new'])])
async def test_legacy_truthy_or_malformed_response_does_not_mint_trusted_lineage(success,token):
    observer=AsyncMock()
    client=KuaiMaiClient(app_key='local',app_secret='local',access_token='local',org_id=str(uuid4()),
        token_persister=AsyncMock(),token_rotation_recorder=observer)
    client._client=SimpleNamespace(is_closed=False,post=AsyncMock(return_value=SimpleNamespace(
        raise_for_status=lambda:None,json=lambda:{'success':success,'session':{'accessToken':token}})))
    client._cache_token=AsyncMock();client._persist_token_to_db=AsyncMock()
    assert await client._do_refresh_token() is True
    observer.assert_not_awaited()
    client._cache_token.assert_awaited_once();client._persist_token_to_db.assert_awaited_once()


@pytest.mark.parametrize('fault',['none','missing','gap','cycle','wrong_org','future','reverse_time','too_many'])
def test_python_rechecks_every_edge_and_bounded_time_order(fault):
    from services.task_execution.erp_postconditions import same_verified_source,KEY
    org=str(uuid4());old='a'*64;middle='b'*64;new='c'*64
    edges=[{'org_id':org,'old':old,'new':middle,'observed_at':'2026-10-30T10:00:00+00:00'},
        {'org_id':org,'old':middle,'new':new,'observed_at':'2026-10-30T11:00:00+00:00'}]
    if fault=='missing':edges=[]
    if fault=='gap':edges[1]['old']='d'*64
    if fault=='cycle':edges[1]['new']=old
    if fault=='wrong_org':edges[0]['org_id']=str(uuid4())
    if fault=='future':edges[1]['observed_at']='2026-10-31T11:00:00+00:00'
    if fault=='reverse_time':edges[1]['observed_at']='2026-10-30T09:00:00+00:00'
    if fault=='too_many':edges=edges*17
    proof={'domain':'erp','org_id':org,'intent':{'category':'product','action':'product_add_update_simple'},
        'dispatch':{'method':'item.general.addorupdate','postcondition':{'version':1,'kind':'product.title',
        'outer_id':'P-001','title':'test','source_fingerprint':old}},'source_rotations':edges,
        'readback':{'completed_at':'2026-10-30T12:00:00+00:00','result':{'tool_result':{'raw':{'metadata':{KEY:{'source_fingerprint':new}}}}}}}
    assert same_verified_source(proof) is (fault=='none')


@pytest.mark.asyncio
async def test_actual_chat_org_factory_preserves_token_persister_and_attaches_scoped_observer(monkeypatch):
    from core.db_scope import ScopedDatabaseClient
    from services.agent.erp_tool_executor import ErpToolMixin
    from services.kuaimai.dispatcher import ErpDispatcher
    actor,org=map(str,(uuid4(),uuid4()))
    executor=ErpToolMixin()
    executor.db=ScopedDatabaseClient(SimpleNamespace(),DatabaseScope(actor,org,DatabaseAccessKind.RUNTIME))
    executor.user_id=actor;executor.org_id=org
    resolver=SimpleNamespace(get_erp_credentials=lambda _:{'kuaimai_app_key':'local','kuaimai_app_secret':'local',
        'kuaimai_access_token':'local','kuaimai_refresh_token':'local'},update_erp_token=lambda *_:None)
    monkeypatch.setattr('services.org.config_resolver.OrgConfigResolver',lambda _:resolver)
    cached=AsyncMock();monkeypatch.setattr(KuaiMaiClient,'load_cached_token',cached)
    result=await executor._get_erp_dispatcher()
    assert isinstance(result,ErpDispatcher),result
    assert result._client._org_id==org
    assert callable(result._client._token_persister)
    assert callable(result._client._token_rotation_recorder)
    cached.assert_awaited_once()
    await result.close()
