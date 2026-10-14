from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from services.kuaimai_external import thinktank_sync as s
from services.kuaimai_external.http_base import HttpResult, KuaimaiWebHttpError


def response(body):
    return HttpResult(200, body, None)


def ok(rows, **extra):
    return response({'result': 1, 'suc': True, 'data': {'list': rows, **extra}})


@pytest.mark.asyncio
async def test_accessible_shops_are_paginated_and_company_scoped():
    client = SimpleNamespace(companyid=123, get=AsyncMock(side_effect=[
        ok([{'userId': -1001}], total=2), ok([{'userId': 456}], total=2)]),
        post=AsyncMock(return_value=ok([{'shop_uni_id': '123_456'}])))
    rows = await s.fetch_thinktank_rows(client, start_date=date(2026, 10, 7), end_date=date(2026, 10, 14))
    assert len(rows) == 1
    assert client.get.call_args_list[1].kwargs['params']['pageNo'] == 2
    assert client.post.call_args.kwargs['payload']['shopUniIds'] == '123_-1001,123_456'


@pytest.mark.parametrize('body', [
    {'result': 3, 'message': '店铺ID不能为空'},
    {'result': 1, 'suc': False, 'data': {'list': []}},
    {'result': 1}, {'result': 1, 'data': {}},
    {'result': 1, 'data': {'list': 'invalid'}}, None,
])
def test_errors_and_malformed_responses_are_not_empty_success(body):
    with pytest.raises(KuaimaiWebHttpError): s._response_rows(response(body))


def test_explicit_empty_report_is_valid():
    assert s._response_rows(ok([]))[1] == []


@pytest.mark.asyncio
async def test_empty_or_incomplete_shops_never_query_report():
    for rows, total in [([], 0), ([], 2), ([{'userId': None}], 1)]:
        client = SimpleNamespace(companyid=123, get=AsyncMock(return_value=ok(rows, total=total)), post=AsyncMock())
        with pytest.raises(KuaimaiWebHttpError):
            await s.fetch_thinktank_rows(client, start_date=date(2026, 10, 7), end_date=date(2026, 10, 14))
        client.post.assert_not_called()


@pytest.mark.asyncio
async def test_business_failure_records_failed_sync_without_success(monkeypatch):
    cred = SimpleNamespace(id='cred', kuaimai_company_id=123, cookie_full='private', censeid_cookie='private')
    monkeypatch.setattr(s.credential_store, 'get_active_credential', AsyncMock(return_value=cred))
    monkeypatch.setattr(s.credential_store, 'record_sync_failure', AsyncMock())
    success = AsyncMock()
    monkeypatch.setattr(s.credential_store, 'record_sync_success', success)
    monkeypatch.setattr(s, '_create_sync_log', AsyncMock(return_value='log'))
    finish = AsyncMock()
    monkeypatch.setattr(s, '_finish_sync_log', finish)
    client = SimpleNamespace(companyid=123, get=AsyncMock(return_value=ok([{'userId': 456}], total=1)),
        post=AsyncMock(return_value=response({'result': 3, 'message': '店铺ID不能为空'})), close=AsyncMock())
    monkeypatch.setattr(s.http_base, 'KuaimaiWebClient', MagicMock(return_value=client))
    result = await s.sync_thinktank(MagicMock(), org_id='org')
    assert result.success is False
    assert '店铺ID不能为空' in result.error
    assert finish.call_args.kwargs['status'] == 'failed'
    success.assert_not_called()
