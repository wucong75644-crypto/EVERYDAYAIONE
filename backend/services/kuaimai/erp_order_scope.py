"""Explicit order scopes reuse tenant classification for summaries and comparisons."""
from datetime import datetime, timedelta
import json

from services.agent.tool_output import ColumnMeta, OutputFormat, OutputStatus, ToolOutput
from services.kuaimai.erp_unified_schema import GROUP_BY_MAP, TIME_COLUMNS
from services.kuaimai.order_classifier import OrderClassifier

SCOPE_LABELS = {
    'valid': '有效订单', 'all': '全部订单',
    'exclude_scalping': '按租户规则排除刷单', 'only_scalping': '仅刷单',
}


def scope_error(message: str) -> ToolOutput:
    return ToolOutput(summary=message, source='erp', status=OutputStatus.ERROR, error_message=message)


def scalping_categories(classifier: OrderClassifier) -> set[str]:
    names = {
        r['rule_name'] for r in classifier.rules
        if r['rule_name'] in {'空包/刷单', '刷单', '空包'} or any(
            (c.get('field') == 'is_scalping' and c.get('op') == 'eq' and c.get('value') in (1, '1', True))
            or (c.get('field') == 'order_type' and c.get('op') == 'list_has'
                and any(str(v) == '10' for v in c.get('value', [])))
            for c in r.get('conditions', [])
        )
    }
    if not names:
        raise ValueError('当前租户未配置刷单分类规则，无法保证该统计口径')
    return names


def scope_case_condition(classifier: OrderClassifier, scope: str) -> str:
    """Same classification CASE as the export label, applied before sorting/limit."""
    case = classifier.to_case_sql()
    if scope == 'all':
        return 'TRUE'
    if scope == 'valid':
        return f"({case}) = '有效订单'"
    names = ', '.join("'" + n.replace("'", "''") + "'" for n in sorted(scalping_categories(classifier)))
    op = 'NOT IN' if scope == 'exclude_scalping' else 'IN'
    return f'({case}) {op} ({names})'


def _selected(classifier, rows: list[dict], scope: str) -> list[dict]:
    if scope == 'all':
        return rows
    scalping = scalping_categories(classifier) if scope != 'valid' else set()
    selected = []
    for row in rows:
        name = next((r['rule_name'] for r in classifier.rules
                     if classifier._match_all_conditions(row, r.get('conditions', []))), '有效订单')
        keep = name == '有效订单' if scope == 'valid' else (name not in scalping if scope == 'exclude_scalping' else name in scalping)
        if keep:
            selected.append(row)
    return selected


def _rpc(db, org_id, filters, tr, group_by):
    dsl = [{'field': f.field, 'op': f.op, 'value': f.value} for f in filters
           if f.field not in TIME_COLUMNS and f.field != 'doc_type']
    response = db.rpc('erp_order_stats_grouped', {
        'p_org_id': org_id, 'p_start': tr.start_iso, 'p_end': tr.end_iso,
        'p_time_col': tr.time_col, 'p_filters': json.dumps(dsl) if dsl else None,
        'p_group_by': GROUP_BY_MAP.get(group_by[0], group_by[0]) if group_by else None,
    }).execute().data
    if not isinstance(response, list):
        raise ValueError('分类统计未返回有效数据，无法保证指定口径')
    return response


def _metrics(classifier, rows, scope, group_by):
    rows = _selected(classifier, rows, scope)
    if not group_by:
        return classifier.classify(rows).total
    groups = classifier.classify_grouped(rows)
    return [{'group_key': k, **v.total} for k, v in groups.items()]


async def query_order_scope(db, org_id, filters, tr, scope, group_by=None,
                            query_type='summary', compare_range='mom', metrics=None) -> ToolOutput:
    from services.kuaimai.erp_analytics_trend import (
        shift_time_range, compute_comparison, format_compare_summary, _build_compare_columns,
    )
    try:
        classifier = OrderClassifier.for_org(db, org_id)
        raw = _rpc(db, org_id, filters, tr, group_by)
        values = _metrics(classifier, raw, scope, group_by)
        label = SCOPE_LABELS[scope]
        if query_type == 'compare':
            from dataclasses import replace
            start, end = shift_time_range(datetime.fromisoformat(tr.start_iso), datetime.fromisoformat(tr.end_iso), compare_range)
            previous_tr = replace(tr, start_iso=start.isoformat(), end_iso=end.isoformat())
            previous = _metrics(classifier, _rpc(db, org_id, filters, previous_tr, group_by), scope, group_by)
            data = compute_comparison(values, previous, group_by[0] if group_by else None)
            body = format_compare_summary(data, compare_range, tr.label, f'{start.isoformat()} ~ {end.isoformat()}')
            return ToolOutput(summary=f'[统计口径] {label}\n{body}', source='erp', format=OutputFormat.TABLE,
                              columns=_build_compare_columns(group_by[0] if group_by else None), data=data,
                              metadata={'order_scope': scope, 'query_type': 'compare', 'time_column': tr.time_col})
        rows = values if isinstance(values, list) else [values]
        count = sum(r['doc_count'] for r in rows)
        amount = sum(r['total_amount'] for r in rows)
        text = [f'[统计口径] {label}', f'[统计区间] {tr.label}',
                f'本次查询订单数：{count:,} 笔；金额：¥{amount:,.2f}']
        for row in rows:
            if group_by:
                text.append(f"{row['group_key']}：{row['doc_count']:,} 笔；¥{row['total_amount']:,.2f}")
        # Keep the original category breakdown as facts; selected fields govern calculations.
        classified = classifier.classify(raw)
        text.append('查询范围分类（筛选口径前）：' + '；'.join(f"{name} {v['doc_count']} 笔" for name, v in classified.categories.items()))
        text.append('后续同口径计算使用本次查询的 doc_count / total_amount，不要替换为默认有效订单数。')
        columns = [ColumnMeta('doc_count', 'integer', '本次查询订单数'),
                   ColumnMeta('total_qty', 'numeric', '本次查询数量'), ColumnMeta('total_amount', 'numeric', '本次查询金额')]
        if group_by:
            columns.insert(0, ColumnMeta('group_key', 'text', '分组'))
        return ToolOutput(summary='\n'.join(text), source='erp', format=OutputFormat.TABLE, columns=columns, data=rows,
                          metadata={'order_scope': scope, 'recommended_key': 'doc_count', 'time_column': tr.time_col,
                                    'categories': classified.categories, 'query_type': 'summary'})
    except Exception as exc:
        return scope_error(f'无法按指定订单口径查询：{exc}')


async def query_order_scope_trend(db, org_id, filters, tr, scope, group_by=None,
                                  time_granularity='day', metrics=None, limit=366):
    """Aggregate order details once, then apply the same tenant categories per period."""
    from collections import defaultdict
    from services.kuaimai.erp_analytics_trend import (
        _auto_adjust_granularity, _fill_zero_periods, _build_trend_columns,
        _translate_platform_in_rows, format_trend_summary,
    )
    from services.kuaimai.erp_unified_schema import RPC_ORDER_STATS_FILTER_FIELDS
    aliases = {'count': 'order_count', 'amount': 'order_amount', 'qty': 'order_qty'}
    requested = list(dict.fromkeys(aliases.get(m, m) for m in (metrics or ['order_count', 'order_amount'])))
    columns = {'order_count': 'doc_count', 'order_qty': 'total_qty', 'order_amount': 'total_amount'}
    if set(requested) - columns.keys():
        return scope_error('订单口径趋势仅支持订单数、数量和金额；未忽略指标执行')
    if any(f.field != tr.time_col and f.field not in (RPC_ORDER_STATS_FILTER_FIELDS | {'doc_created_at','pay_time','consign_time'}) and f.field != 'doc_type' for f in filters):
        return scope_error('订单口径趋势不支持该筛选字段；未忽略条件执行')
    dimensions = [GROUP_BY_MAP.get(g, g) for g in (group_by or [])]
    if len(dimensions) > 1 or any(g not in {'status','product','shop','supplier','warehouse','platform'} for g in dimensions):
        return scope_error('订单口径趋势不支持该分组；未忽略分组执行')
    if time_granularity not in {'day','week','month'}:
        return scope_error('无效的趋势时间粒度')
    granularity = _auto_adjust_granularity(time_granularity, tr.start_iso, tr.end_iso)
    try:
        classifier = OrderClassifier.for_org(db, org_id)
        dsl = [{'field': f.field, 'op': f.op, 'value': f.value} for f in filters
               if f.field != tr.time_col and f.field != 'doc_type']
        raw = db.rpc('erp_order_stats_grouped', {
            'p_org_id': org_id, 'p_start': tr.start_iso, 'p_end': tr.end_iso,
            'p_time_col': tr.time_col, 'p_filters': json.dumps(dsl) if dsl else None,
            'p_group_by': dimensions[0] if dimensions else None,
            'p_time_granularity': granularity,
        }).execute().data
        if not isinstance(raw, list):
            raise ValueError('分类趋势未返回有效数据')
        periods = defaultdict(list)
        for row in raw:
            periods[str(row['period'])[:10]].append(row)
        rows = []
        for period, items in sorted(periods.items()):
            values = _metrics(classifier, items, scope, dimensions)
            for value in values if isinstance(values, list) else [values]:
                row = {'period': period, **{m: value[columns[m]] for m in requested}}
                if dimensions:
                    row['group_key'] = value['group_key']
                rows.append(row)
        if not dimensions:
            end = datetime.fromisoformat(tr.end_iso)
            # A half-open interval ending at noon still contains that calendar day.
            fill_end = tr.end_iso
            if end.time() != datetime.min.time():
                fill_end = (end.replace(hour=0, minute=0, second=0, microsecond=0)
                            + timedelta(days=1)).isoformat()
            rows = _fill_zero_periods(rows, tr.start_iso, fill_end, granularity, requested)
        # Never truncate an accounting result silently; this is the existing chart row budget.
        if len(rows) > min(limit, 366):
            return scope_error('趋势结果超过展示行数，请缩小时间范围或分组；未返回不完整合计')
        _translate_platform_in_rows(rows)
        return ToolOutput(source='erp', format=OutputFormat.TABLE, data=rows,
            columns=_build_trend_columns(requested, dimensions[0] if dimensions else None),
            summary=f'[统计口径] {SCOPE_LABELS[scope]}\n[统计时间列] {tr.time_col}\n' +
                format_trend_summary(rows, granularity, requested, dimensions[0] if dimensions else None),
            metadata={'order_scope': scope, 'time_column': tr.time_col, 'query_type': 'trend',
                      'granularity': granularity, 'metrics': requested, 'row_count': len(rows)})
    except Exception as exc:
        return scope_error(f'无法按指定订单口径查询趋势：{exc}')
