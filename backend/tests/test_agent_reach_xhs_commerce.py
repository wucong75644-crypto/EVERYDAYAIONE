"""Commerce contract regressions using reduced real public responses."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.agent.agent_reach.contracts import ReachError, ReachRequest
from services.agent.agent_reach.service import resolve
from services.agent.agent_reach.xhs_adapter import XHSAdapter
from services.agent.agent_reach.xhs_commerce import commerce_target, shop_result, product_result

FIXTURES = Path(__file__).parent / 'fixtures/agent_reach/xhs_commerce'
SELLER = '65263c56796c030001e298d4'
SHOP = 'https://www.xiaohongshu.com/shop/' + SELLER


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def shop_data(page=1):
    data = fixture('shop.json')
    data['components'][0]['component_data']['pageParam'] = {'page': page, 'pageSize': 10}
    return data


def request(**kwargs):
    return resolve(ReachRequest(task='读取店铺商品', url=SHOP, max_results=10, **kwargs))


def test_shop_auto_routing_pages_and_sales():
    req = request(page=3)
    assert (req.platform, req.action) == ('xiaohongshu', 'read')
    result = shop_result(shop_data(3), SELLER, req)
    assert result.pagination['next_page'] == 4
    assert result.pagination['reported_total'] == 555
    products = [json.loads(item.content) for item in result.items]
    assert products[0]['sold'] == 11789
    assert products[0]['sold_display'] == '已售1.1万+'
    assert products[1]['sold'] == 7304
    assert products[1]['item_id'] != products[1]['sku_id']
    response = result.to_agent_result(req)
    assert response.metadata['sources'][0]['metrics']['sold'] == 11789
    assert response.metadata['pagination']['next_page'] == 4


def test_empty_terminal_does_not_report_zero_shop_total():
    data = shop_data(57)
    c = data['components'][0]['component_data']
    c.update(componentVOList=[], empty=True, total=0, hasMore=False)
    result = shop_result(data, SELLER, request(page=57))
    assert result.pagination['reported_total'] is None
    assert result.pagination['next_page'] is None
    assert result.items == []


@pytest.mark.parametrize('change', ['seller', 'page', 'missing', 'duplicate'])
def test_malformed_or_repeated_page_fails_closed(change):
    data = shop_data()
    c = data['components'][0]['component_data']
    if change == 'seller':
        c['componentVOList'][0]['defaultGoodsComponentVO']['baseInfo']['sellerId'] = 'other'
    elif change == 'page': c['pageParam']['page'] = 2
    elif change == 'missing': del c['hasMore']
    else: c['componentVOList'].append(deepcopy(c['componentVOList'][0]))
    with pytest.raises(ReachError, match='.*'):
        shop_result(data, SELLER, request())


def test_missing_sales_never_uses_placeholder_zero_and_details_match():
    data = shop_data()
    card = data['components'][0]['component_data']['componentVOList'][0]['defaultGoodsComponentVO']
    card['tagStrategyMap'] = {}
    result = shop_result(data, SELLER, request())
    assert result.items[0].metrics['sold'] is None
    details = fixture('details.json')
    for name, expected in [('sold', 164), ('missing', None)]:
        detail = details[name]
        sku = detail['template_data'][0]['descriptionH5']['skuId']
        result = product_result(detail, sku)
        product = json.loads(result.items[0].content)
        assert product['sold'] == expected
        assert product['price'] == 16.8
        assert 'item_id' not in product  # descriptionMain.itemId is the SKU, not true item ID.
        with pytest.raises(ReachError): product_result(detail, '0' * 24)


@pytest.mark.asyncio
async def test_adapter_routes_one_page_and_rejects_reviews_before_identity():
    adapter = XHSAdapter('{"c":"http://127.0.0.1:18060"}',
                         {'id':'c', 'account_id':'account', 'secret':{'service_token':'test'}})
    adapter.identity = AsyncMock()
    adapter.call = AsyncMock(return_value=shop_data(2))
    await adapter.read(request(page=2))
    adapter.identity.assert_awaited_once()
    adapter.call.assert_awaited_once_with('POST', 'commerce/shop',
        {'seller_id':SELLER, 'page':2, 'page_size':10})
    adapter.identity.reset_mock()
    with pytest.raises(ReachError) as error:
        await adapter.read(request(action='read_comments'))
    assert error.value.code == 'UNSUPPORTED_OPERATION'
    adapter.identity.assert_not_awaited()


def test_url_boundary_and_non_shop_page():
    assert commerce_target(SHOP + '?source=share') == ('shop', SELLER)
    assert commerce_target('https://evil.test/shop/' + SELLER) is None
    assert commerce_target(SHOP + '/../../login') is None
    with pytest.raises(ReachError):
        resolve(ReachRequest(task='test', url='https://www.xiaohongshu.com/goods-detail/' + 'a'*24, page=2))

@pytest.mark.asyncio
async def test_execute_through_authenticated_rest_transport(monkeypatch):
    import httpx
    from services.agent.agent_reach import xhs_adapter
    from services.agent.agent_reach.service import execute
    real_client = httpx.AsyncClient
    calls = []
    def respond(req):
        calls.append(req.url.path)
        assert req.headers['Authorization'] == 'Bearer test'
        if req.url.path == '/api/v1/login/status':
            return httpx.Response(200, json={'success':True, 'data':{'is_logged_in':True, 'user_id':'account'}})
        assert req.url.path == '/api/v1/commerce/shop'
        assert json.loads(req.content) == {'seller_id':SELLER, 'page':2, 'page_size':10}
        return httpx.Response(200, json={'success':True, 'data':shop_data(2)})
    monkeypatch.setattr(xhs_adapter.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    settings = SimpleNamespace(agent_reach_platforms='xiaohongshu', agent_reach_bin_dir='/tmp', agent_reach_timeout_seconds=60,
        agent_reach_xhs_origins_json='{"c":"http://127.0.0.1:18060"}', agent_reach_xhs_slots_json='{}')
    result = await execute(request(page=2), settings, timeout=10,
        connection={'id':'c','account_id':'account','secret':{'service_token':'test'}})
    assert calls == ['/api/v1/login/status', '/api/v1/commerce/shop']
    assert result.metadata['pagination']['next_page'] == 3
    assert result.metadata['sources'][0]['content_kind'] == 'product'


def test_hundred_page_cap_is_explicit():
    req = request(page=100)
    result = shop_result(shop_data(100), SELLER, req).to_agent_result(req)
    assert result.metadata['pagination']['next_page'] is None
    assert result.metadata['pagination']['platform_has_more'] is True
    assert any('100页' in warning for warning in result.metadata['warnings'])
