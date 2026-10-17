"""Stateless continuation; reject changed content rather than silently skipping it."""
import hashlib
import json
from dataclasses import replace

from .contracts import ReachError


def paginate(result, request):
    material = [request.platform, request.action, request.url, request.connection_id,
                request.page, request.max_results,
                [(i.title, i.url, i.content, i.kind) for i in result.items]]
    digest = hashlib.sha256(json.dumps(material, ensure_ascii=False).encode()).hexdigest()
    index, offset = 0, 0
    if request.cursor:
        try:
            version, previous, index, offset = request.cursor.split(':')
            index, offset = int(index), int(offset)
        except (ValueError, TypeError):
            raise ReachError('INVALID_CURSOR', '续读标记无效，请从当前页重新读取') from None
        if version != 'v1' or previous != digest:
            raise ReachError('CONTENT_CHANGED', '资料或读取参数已变化，请清空cursor重新读取当前页')
        if index < 0 or index >= len(result.items) or offset < 0 or offset > len(result.items[index].content):
            raise ReachError('INVALID_CURSOR', '续读位置无效')
    budget, items = 4500, []
    start = [index, offset]
    while index < len(result.items) and budget > 0 and len(items) < 10:
        item = result.items[index]
        text = item.content[offset:offset + budget]
        items.append(replace(item, content=text))
        budget -= len(text)
        offset += len(text)
        if offset == len(item.content):
            index, offset = index + 1, 0
        else:
            break
    more = index < len(result.items)
    pagination = {**result.pagination, 'has_more_text': more,
                  'next_cursor': f'v1:{digest}:{index}:{offset}' if more else '',
                  'range_start': start, 'range_end': [index, offset],
                  'instruction': '保持参数不变，用next_cursor继续读取当前页。' if more else '本次已加载内容已读完；列表是否还有下一页见next_page。'}
    if result.pagination.get('scope') == 'public_shop_catalog':
        pagination['page_text_complete'] = not more
        pagination['instruction'] = (
            '当前页文本未读完：保持url、page、max_results不变，使用next_cursor续读；不要提前翻页。' if more else
            '当前页文本已读完；用户要求全部商品时按next_page_request顺序继续，无需重复确认，不能自行改为Top N或因尾部销量低停读；仅在目录读完或实际触发访问/执行限制时结束。每页数量由max_results控制。' if pagination.get('next_page') else
            '已到目录末页；这仅确认目录结束，仍须核对是否从第1页连续读取、去重数量及此前有效总数，不能仅凭最后一页声称全店完成。' if pagination.get('catalog_end_reached') else
            '已到工具页数上限但目录未读完：说明已读范围，不能称为全店完成。')
    warnings = list(result.warnings)
    if request.page == 100 and pagination.get('platform_has_more'):
        warnings.append('达到本工具100页限制，平台可能仍有更多列表数据；并非全部读取完成。')
    return replace(result, items=items, pagination=pagination, warnings=warnings,
                   status='partial' if more else result.status)
