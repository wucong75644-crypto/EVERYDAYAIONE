"""Keep an explicit order-statistics scope separate from classification rules."""
import re

ORDER_SCOPES = frozenset({'valid', 'all', 'exclude_scalping', 'only_scalping'})


def _text_scope(text: str) -> str | None:
    if re.search(r'(不排除|不剔除).{0,4}(刷单|空包)', text):
        return 'all'
    if re.search(r'(排除|剔除|不含|不包含|去掉|扣除).{0,4}(刷单|空包)', text):
        return 'exclude_scalping'
    if re.search(r'(包含|包括|计入).{0,4}(刷单|空包)', text):
        return 'all'
    if re.search(r'(只查|仅查|只统计|仅统计).{0,4}(刷单|空包)', text):
        return 'only_scalping'
    if re.search(r'(只查|仅查|统计|查询).{0,3}有效订单', text):
        return 'valid'
    if re.search(r'(全部|所有|全量)订单|(包含|包括)(全部|所有|全量)|订单.{0,4}(全部状态|所有状态)', text):
        return 'all'
    return None


def normalize_order_scope(params: dict, query: str) -> None:
    if params.get('doc_type') not in {'order', 'daily_stats'}:
        return
    current, _, background = query.partition('（背景：')
    scope = _text_scope(current)
    retention_text = current
    if scope is None and background and re.search(r'同样|相同|沿用|同一口径', current):
        scope = _text_scope(background)
        retention_text = background
    if scope is None and params.get('is_scalping') is True and re.search(r'刷单|空包', current):
        scope = 'only_scalping'
    if scope is None:
        scope = params.get('order_scope')
    if scope is None and (params.get('query_type') == 'compare' or params.get('compare_range')):
        scope = 'all' if params.get('include_invalid') else 'valid'
    if scope not in ORDER_SCOPES:
        return
    params['order_scope'] = scope
    params['include_invalid'] = scope != 'valid'
    # Natural-language "刷单" means the tenant's category, not a single flag.
    params.pop('is_scalping', None)
    for key in ('filters', 'exclude_filters'):
        if isinstance(params.get(key), list):
            params[key] = [f for f in params[key] if f.get('field') != 'is_scalping']
    # "Only exclude scalping, keep other states" must not become CLOSED-only.
    restricted_status = re.search(r'(查询|统计|查看|只查|仅查|只看).{0,4}(已关闭|已取消|已作废|关闭订单|取消订单|作废订单)|(已关闭|已取消|已作废)的?订单', current)
    keep_states = re.search(r'(只排除|仅排除).{0,4}(刷单|空包)|(保留|包含|包括).{0,12}(关闭|作废|取消)|其余状态保留', retention_text)
    if scope in {'all', 'exclude_scalping'} and keep_states and not restricted_status:
        params.pop('order_status', None)
        params.pop('is_cancel', None)
        for key in ('filters', 'exclude_filters'):
            if isinstance(params.get(key), list):
                params[key] = [f for f in params[key] if f.get('field') not in {'order_status', 'is_cancel'}]
