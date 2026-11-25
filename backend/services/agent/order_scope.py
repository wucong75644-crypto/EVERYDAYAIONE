"""Keep an explicit order-statistics scope separate from classification rules."""

ORDER_SCOPES = frozenset({'valid', 'all', 'exclude_scalping', 'only_scalping'})


def normalize_order_scope(params: dict, query: str) -> None:
    if params.get('doc_type') not in {'order', 'daily_stats'}:
        return
    # The existing plan extractor owns natural-language meaning. Compile its
    # typed scope without overriding it from another interpretation of query.
    scope = params.get('order_scope')
    if scope is None and params.get('is_scalping') is True:
        scope = 'only_scalping'
    if scope is None and type(params.get('include_invalid')) is bool:
        scope = 'all' if params['include_invalid'] else 'valid'
    if scope is None and (params.get('query_type') == 'compare' or params.get('compare_range')):
        scope = 'all' if params.get('include_invalid') else 'valid'
    if scope not in ORDER_SCOPES:
        return
    params['order_scope'] = scope
    params['include_invalid'] = scope != 'valid'
    # Natural-language "刷单" means the tenant's category, not a single flag.
    params.pop('is_scalping', None)
    # Explicit field filters have independent meaning. Never delete them while
    # compiling a category scope; a valid intersection may legitimately be empty.
