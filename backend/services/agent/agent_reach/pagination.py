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
                  'instruction': '保持参数不变，用next_cursor继续读取当前页。' if more else '本次已加载内容已读完；评论是否还有下一页见next_page。'}
    warnings = list(result.warnings)
    if request.page == 100 and pagination.get('platform_has_more'):
        warnings.append('达到本工具100页限制，平台可能仍有更多评论；并非全部读取完成。')
    return replace(result, items=items, pagination=pagination, warnings=warnings,
                   status='partial' if more else result.status)
