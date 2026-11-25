"""Expose the existing ERP execution fields to the plan extraction model.

The tool contract must describe the fields its consumer needs. Open-ended params
with only doc_type/scope properties caused providers to omit the actual query.
"""
from copy import deepcopy


def query_plan_params_schema() -> dict:
    from services.tools.definitions.erp_local_schemas import build_local_tools
    from services.kuaimai.erp_unified_schema import OP_COMPAT

    props = deepcopy(next(t for t in build_local_tools() if t['function']['name'] == 'local_data')
                     ['function']['parameters']['properties'])
    props.update({
        'platform': {'type':'string','description':'用户指定的平台；未指定时省略'},
        'shop_name': {'type':'string','description':'用户指定的店铺；未指定时省略'},
        'product_code': {'type':'string','description':'用户指定的精确商品编码；未指定时省略'},
        'time_range': {'type':['string','null'], 'description':
            '单据和daily_stats必填 YYYY-MM-DD ~ YYYY-MM-DD 或带HH:MM；快照表填null。包含用户实际查询区间。'},
        'time_col': {'type':'string','description':'实际时间字段，如pay_time、consign_time、doc_created_at、stat_date'},
        'order_scope': {'type':['string','null'],
            'enum':['valid','all','exclude_scalping','only_scalping',None],
            'description':'order默认valid（租户有效订单）；all包括所有订单；exclude_scalping仅排除租户刷单类别，保留关闭等其他状态；only_scalping仅刷单。其他doc_type填null。'},
        'query_type': {'type':'string','enum':['auto','detail','summary','trend','compare','ratio','cross','alert','distribution']},
        'time_granularity': {'type':'string','enum':['day','week','month']},
        'compare_range': {'type':'string','enum':['mom','yoy','wow']},
        'metrics': {'type':'array','items':{'type':'string'}},
        'alert_type': {'type':'string','enum':['low_stock','slow_moving','overstock','out_of_stock','purchase_overdue']},
        'extra_fields': deepcopy(props['fields']),
        '_required_input': {'type':'object','properties':{'from_step':{'type':'integer'},'field':{'type':'string'}}},
        '_output_fields': {'type':'array','items':{'type':'string'}},
    })
    props['filters']['items']['properties']['op']['enum'] = sorted(set().union(*OP_COMPAT.values()))
    props['numeric_filters'] = deepcopy(props['filters'])
    props['exclude_filters'] = deepcopy(props['filters'])
    return {'type':'object', 'description':'填写完整查询，不执行查询。未指定的可选筛选省略，不要填写占位对象或编造默认仓库。',
            'required':['doc_type','order_scope','time_range','query_type'],'properties':props}
