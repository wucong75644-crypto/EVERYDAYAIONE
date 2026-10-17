"""Regression: explicit scopes must differ from the default valid-order metric."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from config.default_classification_rules import DEFAULT_ORDER_RULES
from services.agent.order_scope import normalize_order_scope
from services.agent.plan_builder import _sanitize_params
from services.agent.param_converter import params_to_filters
from services.kuaimai.order_classifier import OrderClassifier
from services.kuaimai.erp_order_scope import query_order_scope, scope_case_condition
from services.kuaimai.erp_unified_filters import validate_filters, extract_time_range
from services.kuaimai.erp_unified_query import UnifiedQueryEngine

ROWS = [
    {'order_type': '0', 'order_status': 'FINISHED', 'is_scalping': 0, 'doc_count': 10, 'total_qty': 20, 'total_amount': 100},
    {'order_type': '0', 'order_status': 'CLOSED', 'is_scalping': 0, 'doc_count': 3, 'total_qty': 3, 'total_amount': 30},
    {'order_type': '14', 'order_status': 'FINISHED', 'is_scalping': 0, 'doc_count': 2, 'total_qty': 2, 'total_amount': 0},
    {'order_type': '10', 'order_status': 'CLOSED', 'is_scalping': 0, 'doc_count': 4, 'total_qty': 4, 'total_amount': 40},
    {'order_type': '0', 'order_status': 'FINISHED', 'is_scalping': 1, 'doc_count': 1, 'total_qty': 1, 'total_amount': 10},
]
BASE = [{'field': 'doc_created_at', 'op': 'gte', 'value': '2026-10-15T00:00:00+08:00'},
        {'field': 'doc_created_at', 'op': 'lt', 'value': '2026-10-16T00:00:00+08:00'},
        {'field': 'shop_name', 'op': 'eq', 'value': 'shop'}]

@pytest.mark.parametrize('text,expected', [
    ('只排除刷单、保留关闭和作废订单', 'exclude_scalping'), ('不包含刷单', 'exclude_scalping'),
    ('不排除刷单', 'all'), ('包含全部订单', 'all'), ('仅查刷单', 'only_scalping'), ('查询有效订单', 'valid'),
])
def test_text_scope(text, expected):
    p = {'doc_type': 'order', 'is_scalping': True, 'order_scope': 'all', 'exclude_filters': [{'field': 'is_scalping', 'value': 1}]}
    normalize_order_scope(p, text)
    assert p['order_scope'] == expected
    assert 'is_scalping' not in p and not p['exclude_filters']


def test_direct_filters_survive_cleaning():
    original = {'doc_type': 'order', 'filters': [{'field': 'amount', 'op': 'gt', 'value': 5}]}
    assert _sanitize_params(original)['filters'] == original['filters']


def test_followup_scope_and_explicit_override():
    p = {'doc_type': 'order', 'order_scope': 'valid'}
    normalize_order_scope(p, '同一家店、同样口径，查前一天。（背景：只排除刷单、保留关闭和作废订单）')
    assert p['order_scope'] == 'exclude_scalping'
    normalize_order_scope(p, '这次包含刷单。（背景：只排除刷单）')
    assert p['order_scope'] == 'all'


def test_default_is_unchanged_and_keep_statuses():
    p = {'doc_type': 'order'}
    normalize_order_scope(p, '查今天订单数量')
    assert p == {'doc_type': 'order'}
    p.update(order_status=['CLOSED'], filters=[{'field': 'order_status', 'op': 'eq', 'value': 'CLOSED'}])
    normalize_order_scope(p, '只排除刷单、保留关闭和作废订单')
    assert 'order_status' not in p and not p['filters']


def db_with_rows(rows):
    db = MagicMock()
    db.rpc.return_value.execute.return_value = SimpleNamespace(data=rows)
    return db

@pytest.mark.asyncio
@pytest.mark.parametrize('scope,count,amount', [('valid', 10, 100), ('all', 20, 180), ('exclude_scalping', 15, 130), ('only_scalping', 5, 50)])
async def test_scope_uses_category_rules_not_just_flag(scope, count, amount):
    db = db_with_rows(ROWS)
    vf, _ = validate_filters(BASE)
    tr = extract_time_range(vf, None, None, 'summary')
    with patch.object(OrderClassifier, 'for_org', return_value=OrderClassifier(DEFAULT_ORDER_RULES)):
        result = await query_order_scope(db, 'org', vf, tr, scope)
    assert not result.error_message
    assert result.data[0]['doc_count'] == count
    assert result.data[0]['total_amount'] == amount
    assert '实际成交' not in result.summary
    assert result.metadata['order_scope'] == scope

@pytest.mark.asyncio
async def test_compare_preserves_filters_dates_and_scope():
    db = db_with_rows(ROWS)
    vf, _ = validate_filters(BASE + [{'field': 'amount', 'op': 'gt', 'value': 5}])
    tr = extract_time_range(vf, None, None, 'summary')
    with patch.object(OrderClassifier, 'for_org', return_value=OrderClassifier(DEFAULT_ORDER_RULES)):
        result = await query_order_scope(db, 'org', vf, tr, 'exclude_scalping', query_type='compare', compare_range='wow')
    assert not result.error_message
    assert result.data[0]['current_doc_count'] == 15
    assert result.data[0]['prev_doc_count'] == 15
    import json
    calls = db.rpc.call_args_list
    assert calls[0].args[1]['p_start'] != calls[1].args[1]['p_start']
    for call in calls:
        assert call.args[1]['p_time_col'] == 'doc_created_at'
        assert {'field': 'amount', 'op': 'gt', 'value': 5} in json.loads(call.args[1]['p_filters'])

@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['trend', 'ratio', 'distribution', 'cross'])
async def test_unsupported_scope_never_queries(kind):
    db = MagicMock()
    result = await UnifiedQueryEngine(db, 'org').execute('order', 'summary', BASE, order_scope='exclude_scalping', query_type=kind)
    assert result.error_message and '不支持' in result.error_message
    db.rpc.assert_not_called()


def test_export_case_and_summary_select_identical_rules():
    import duckdb
    condition = scope_case_condition(OrderClassifier(DEFAULT_ORDER_RULES), 'exclude_scalping')
    con = duckdb.connect()
    try:
        rows = con.execute(f"select order_type, order_status, is_scalping from (values ('0','FINISHED',0),('0','CLOSED',0),('14','FINISHED',0),('10','CLOSED',0),('0','FINISHED',1)) t(order_type,order_status,is_scalping) where {condition}").fetchall()
        assert rows == [('0', 'FINISHED', 0), ('0', 'CLOSED', 0), ('14', 'FINISHED', 0)]
    finally:
        con.close()

@pytest.mark.asyncio
async def test_engine_export_and_small_detail_apply_scope():
    from unittest.mock import AsyncMock
    from services.agent.tool_output import ToolOutput
    engine = UnifiedQueryEngine(MagicMock(), 'org')
    engine._export = AsyncMock(return_value=ToolOutput(summary='ok', source='erp'))
    for limit in (20, 50, 5000):
        result = await engine.execute('order', 'export', BASE, order_scope='exclude_scalping', limit=limit)
        assert not result.error_message
        assert engine._export.call_args.kwargs['order_scope'] == 'exclude_scalping'

@pytest.mark.asyncio
async def test_extract_plan_recovers_missing_scope_and_keeps_other_filters():
    from unittest.mock import AsyncMock
    from services.agent.erp_agent import ERPAgent
    agent = ERPAgent(MagicMock(), 'user', '', 'org')
    agent._llm_extract = AsyncMock(return_value=([('trade', {
        'doc_type': 'order', 'filters': [{'field': 'amount', 'op': 'gt', 'value': 5}],
    })], None, 'parallel'))
    plan = await agent._extract_plan('只排除刷单，保留关闭和作废订单')
    p = plan.steps[0].params
    assert p['order_scope'] == 'exclude_scalping'
    assert p['filters'] == [{'field': 'amount', 'op': 'gt', 'value': 5}]

@pytest.mark.asyncio
async def test_degraded_plan_does_not_broaden_explicit_scope():
    import time
    from unittest.mock import AsyncMock
    from services.agent.erp_agent import ERPAgent
    agent = ERPAgent(MagicMock(), 'user', '', 'org')
    agent._llm_extract = AsyncMock(side_effect=ValueError('invalid JSON'))
    agent._execute_plan = AsyncMock()
    result = await agent._execute('查询订单，只排除刷单', time.monotonic() + 30)
    assert result.status == 'error' and '未执行' in result.summary
    agent._execute_plan.assert_not_called()

@pytest.mark.asyncio
async def test_unrepresentable_trend_filter_is_not_dropped():
    db = MagicMock()
    result = await UnifiedQueryEngine(db, 'org').execute('order', 'summary', BASE + [{'field': 'is_scalping', 'op': 'ne', 'value': 1}], query_type='trend')
    assert result.error_message and '未忽略' in result.error_message
    db.rpc.assert_not_called()

@pytest.mark.asyncio
async def test_direct_filter_keeps_semantic_shop_and_dates():
    from unittest.mock import AsyncMock
    from services.agent.departments.trade_agent import TradeAgent
    from services.agent.tool_output import ToolOutput
    agent = TradeAgent(db=MagicMock(), org_id='org')
    agent.query_orders = AsyncMock(return_value=ToolOutput(summary='ok', source='erp'))
    await agent.execute('查询订单', dag_mode=True, params={
        'doc_type': 'order', 'mode': 'summary', 'time_range': '2026-10-15 ~ 2026-10-15',
        'shop_name': 'shop', 'order_scope': 'exclude_scalping',
        'filters': [{'field': 'amount', 'op': 'gt', 'value': 5}, {'field': 'doc_created_at', 'op': 'gte', 'value': '2026-10-15T00:00:00'}],
    })
    kwargs = agent.query_orders.call_args.kwargs
    fields = {f['field'] for f in kwargs['filters']}
    assert {'amount', 'shop_name', 'doc_created_at'} <= fields
    assert kwargs['order_scope'] == 'exclude_scalping'
    assert {'gte', 'lt'} <= {f['op'] for f in kwargs['filters'] if f['field'] == 'doc_created_at'}

@pytest.mark.asyncio
async def test_actual_export_applies_classification_before_limit(tmp_path):
    from unittest.mock import AsyncMock
    import services.kuaimai.erp_unified_query as module
    export = AsyncMock(return_value={'row_count': 0, 'size_kb': 0})
    with patch.object(OrderClassifier, 'for_org', return_value=OrderClassifier(DEFAULT_ORDER_RULES)), \
         patch.object(module, 'resolve_export_path', return_value=(tmp_path, tmp_path / 'scope.parquet', 'scope.parquet')), \
         patch.object(module, '_need_archive', return_value=False), \
         patch.object(module, 'check_sync_health', return_value=''), \
         patch('services.kuaimai.erp_export_subprocess.subprocess_export', export):
        result = await UnifiedQueryEngine(MagicMock(), 'org').execute('order', 'export', BASE, order_scope='exclude_scalping', limit=50)
    assert not result.error_message
    sql = export.call_args.args[0]
    assert 'NOT IN' in sql and '空包/刷单' in sql and '订单分类' in sql
    assert sql.index('NOT IN') < sql.index('LIMIT 50')
    assert 'list_contains' in sql and 'is_scalping' in sql


def test_normal_rule_is_not_misidentified_as_scalping():
    from services.kuaimai.erp_order_scope import scalping_categories
    classifier = OrderClassifier([{'rule_name': '有效订单', 'conditions': [{'field': 'is_scalping', 'op': 'eq', 'value': 0}]}])
    with pytest.raises(ValueError, match='未配置刷单'):
        scalping_categories(classifier)


def test_current_explicit_status_survives_old_all_state_background():
    p = {'doc_type': 'order', 'order_status': 'CLOSED'}
    normalize_order_scope(p, '这次查询已关闭订单，包含刷单。（背景：只排除刷单，保留关闭和作废订单）')
    assert p['order_scope'] == 'all'
    assert p['order_status'] == 'CLOSED'


def test_explicit_include_all_overrides_same_scope_background():
    p = {'doc_type': 'order'}
    normalize_order_scope(p, '同一家店，这次包含全部。（背景：只排除刷单）')
    assert p['order_scope'] == 'all'
