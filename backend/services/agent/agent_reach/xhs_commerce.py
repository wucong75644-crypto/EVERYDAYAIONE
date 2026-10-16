"""Public shop/product data; buyer reviews are deliberately not supported."""
import json
import re
from urllib.parse import urlsplit

from .contracts import ReachError, ReachItem, ReachResult


def commerce_target(url):
    parsed = urlsplit(url)
    if parsed.hostname not in {'www.xiaohongshu.com', 'xiaohongshu.com'}:
        return None
    match = re.fullmatch(r'/(shop|goods-detail)/([0-9a-fA-F]{24})/?', parsed.path)
    return (match[1], match[2].lower()) if match else None


def _object(value):
    return value if isinstance(value, dict) else {}


def _sales(display, raw=None):
    # The card's itemSaleNum=0 is a placeholder, even for goods sold thousands
    # of times. Only the sold tag and the detail's display text are evidence.
    text = display if isinstance(display, str) and display else None
    number = None
    if isinstance(raw, (str, int)) and not isinstance(raw, bool) and re.fullmatch(r'\d+', str(raw)):
        number = int(raw)
    elif text and re.fullmatch(r'已售\s*\d+', text):
        number = int(re.sub(r'\D', '', text))
    return {'sold': number, 'sold_display': text}


def _item(product, source):
    sku = product['sku_id']
    metadata = {'remote_id': sku, 'seller_id': product.get('seller_id'),
                'item_id': product.get('item_id'), 'raw_metrics': {'sold': product['sold_display']},
                'source': source, 'scope': 'public_cumulative', 'adapter_version': 'xhs-rest:commerce-v1'}
    return ReachItem(product['title'], 'https://www.xiaohongshu.com/goods-detail/' + sku,
                     json.dumps(product, ensure_ascii=False), 'product',
                     metrics={'sold': product['sold']}, metric_metadata=metadata)


def shop_result(data, seller, request):
    components = data.get('components')
    if not isinstance(components, list) or _object(data.get('result')).get('success') is not True:
        raise ReachError('UPSTREAM_CHANGED', '小红书未确认店铺商品列表读取成功')
    matches = [c for c in components if _object(c).get('component_name') == 'DefaultMixCardComponent']
    if len(matches) != 1:
        raise ReachError('UPSTREAM_CHANGED', '小红书商品列表组件格式变化')
    component = _object(matches[0].get('component_data'))
    rows = component.get('componentVOList')
    page = _object(component.get('pageParam'))
    if (not isinstance(rows, list) or page.get('page') != request.page
            or page.get('pageSize') != request.max_results or type(component.get('hasMore')) is not bool):
        raise ReachError('UPSTREAM_CHANGED', '小红书未确认所请求的商品分页，不能视为全店结果')
    items, seen = [], set()
    for row in rows:
        card = _object(_object(row).get('defaultGoodsComponentVO'))
        base = _object(card.get('baseInfo'))
        sku, item_id = base.get('skuId'), base.get('itemId')
        if (base.get('sellerId') != seller or not isinstance(sku, str)
                or not re.fullmatch(r'[0-9a-f]{24}', sku) or not isinstance(item_id, str)
                or not re.fullmatch(r'[0-9a-f]{24}', item_id) or not isinstance(base.get('title'), str) or not base['title'] or sku in seen):
            raise ReachError('UPSTREAM_CHANGED', '商品身份、店铺或分页数据不一致')
        seen.add(sku)
        tags = _object(card.get('tagStrategyMap'))
        sold = [t for group in tags.values() if isinstance(group, list)
                for t in group if _object(t).get('type') == 'sold']
        tag = sold[0] if sold else {}
        product = {'title': base['title'], 'item_id': item_id, 'sku_id': sku, 'seller_id': seller,
                   'price': _object(_object(_object(card.get('marketingInfo')).get('priceInfo')).get('expectedPrice')).get('price'),
                   'on_shelf_time': base.get('itemOnShelfTime'), 'image': _object(base.get('image')).get('url'),
                   **_sales(tag.get('content'), _object(tag.get('tag_ext_info')).get('sold'))}
        items.append(_item(product, 'shop_pc.DefaultMixCardComponent'))
    if len(rows) > request.max_results:
        raise ReachError('UPSTREAM_CHANGED', '商品列表未遵循每页数量')
    more = bool(rows) and component['hasMore']
    total = component.get('total')
    if type(total) is not int or total < 0:
        raise ReachError('UPSTREAM_CHANGED', '商品列表未返回有效总数')
    return ReachResult('xiaohongshu', 'xhs-rest', items, pagination={
        'page': request.page, 'page_size': request.max_results, 'returned_count': len(items),
        'reported_total': total if rows or request.page == 1 else None,
        'platform_has_more': more, 'next_page': request.page + 1 if more and request.page < 100 else None,
        'seller_id': seller, 'scope': 'public_shop_catalog'},
        warnings=['销量为平台公开累计展示值，可能缩写或缺失；当前仅为一页商品，需按next_page继续并按item_id/sku_id去重，不能将单页称为全店。'] if more else [])


def product_result(data, sku):
    templates = data.get('template_data')
    if not isinstance(templates, list) or not templates or not isinstance(templates[0], dict):
        raise ReachError('UPSTREAM_CHANGED', '小红书商品详情格式变化')
    t = templates[0]
    description, price, seller = (_object(t.get(k)) for k in ('descriptionH5', 'priceH5', 'sellerH5'))
    if description.get('skuId') != sku or not isinstance(description.get('name'), str) or not description['name']:
        raise ReachError('UPSTREAM_CHANGED', '商品详情身份与请求不一致')
    graphic = _object(t.get('graphicDetailsV4'))
    product = {'title': description['name'], 'sku_id': sku, 'seller_id': seller.get('id'),
               'seller_name': seller.get('name'), 'shop_url': seller.get('link'),
               'price': _object(price.get('dealPrice')).get('price'),
               'listed_price': price.get('highlightPrice'),
               'description': graphic.get('description'), 'images': graphic.get('images') or [],
               'parameters': _object(t.get('variantsParams')).get('list') or [],
               **_sales(price.get('itemAnalysisDataText'))}
    # descriptionMain.itemId can contain the SKU ID. Do not relabel it as the
    # true item ID; shop cards provide that separately.
    return ReachResult('xiaohongshu', 'xhs-rest', [_item(product, 'jpd.h5.toc.template_data')],
                       warnings=['销量为平台公开累计展示值，缺失为null，缩写不推算精确值；图文详情中的图片未进行OCR，不包含买家评价。'])


async def read_commerce(adapter, request, target):
    if request.action != 'read':
        raise ReachError('UNSUPPORTED_OPERATION', '商品和店铺链接支持read，不支持买家评价读取')
    kind, identifier = target
    if kind == 'shop':
        data = await adapter.call('POST', 'commerce/shop', {
            'seller_id': identifier, 'page': request.page, 'page_size': request.max_results})
        if not isinstance(data, dict):
            raise ReachError('UPSTREAM_CHANGED', '小红书商品列表格式变化')
        return shop_result(data, identifier, request)
    if request.page != 1:
        raise ReachError('UNSUPPORTED_OPERATION', '商品详情没有商品分页，请使用cursor续读')
    data = await adapter.call('POST', 'commerce/product', {'sku_id': identifier})
    if not isinstance(data, dict):
        raise ReachError('UPSTREAM_CHANGED', '小红书商品详情格式变化')
    return product_result(data, identifier)
