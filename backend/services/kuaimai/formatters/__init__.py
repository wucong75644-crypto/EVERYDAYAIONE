"""
格式化器注册表

所有格式化函数按类别分文件，统一通过 get_formatter(name) 获取。
返回字段信息通过 get_response_fields() 获取，供 erp_api_search 生成文档。
"""

from typing import Any, Callable, Dict, Optional

from services.kuaimai.formatters.common import (
    format_generic_list,
    format_generic_detail,
    format_timestamp,
    parse_date,
)
from services.kuaimai.formatters.trade import TRADE_FORMATTERS, TRADE_RESPONSE_FIELDS
from services.kuaimai.formatters.product import (
    PRODUCT_FORMATTERS, PRODUCT_RESPONSE_FIELDS,
)
from services.kuaimai.formatters.basic import BASIC_FORMATTERS, BASIC_RESPONSE_FIELDS
from services.kuaimai.formatters.aftersales import (
    AFTERSALES_FORMATTERS, AFTERSALES_RESPONSE_FIELDS,
)
from services.kuaimai.formatters.warehouse import (
    WAREHOUSE_FORMATTERS, WAREHOUSE_RESPONSE_FIELDS,
)
from services.kuaimai.formatters.purchase import (
    PURCHASE_FORMATTERS, PURCHASE_RESPONSE_FIELDS,
)
from services.kuaimai.formatters.qimen import QIMEN_FORMATTERS, QIMEN_RESPONSE_FIELDS

# 全局格式化器注册表
_FORMATTER_REGISTRY: Dict[str, Callable] = {
    "format_generic_list": format_generic_list,
    "format_generic_detail": format_generic_detail,
    **TRADE_FORMATTERS,
    **PRODUCT_FORMATTERS,
    **BASIC_FORMATTERS,
    **AFTERSALES_FORMATTERS,
    **WAREHOUSE_FORMATTERS,
    **PURCHASE_FORMATTERS,
    **QIMEN_FORMATTERS,
}

# ---------------------------------------------------------------------------
# 返回字段注册表
# 按 formatter 名查找（专用 formatter）
# ---------------------------------------------------------------------------
_RESPONSE_FIELDS_REGISTRY: Dict[str, Dict[str, Any]] = {
    **TRADE_RESPONSE_FIELDS,
    **PRODUCT_RESPONSE_FIELDS,
    **BASIC_RESPONSE_FIELDS,
    **AFTERSALES_RESPONSE_FIELDS,
    **WAREHOUSE_RESPONSE_FIELDS,
    **PURCHASE_RESPONSE_FIELDS,
    **QIMEN_RESPONSE_FIELDS,
}

# ---------------------------------------------------------------------------
# generic_list action 的返回字段（按 "tool:action" 查找，兜底用）
# 这些 action 使用 format_generic_list，无专用 LABELS，手动补充
# ---------------------------------------------------------------------------
_ACTION_RESPONSE_FIELDS: Dict[str, Dict[str, Any]] = {
    # ── 商品相关 ──
    "erp_product_query:cat_list": {
        "main": {
            "cid": "分类ID", "id": "ID", "name": "分类名称",
            "parentCid": "父分类ID",
        },
    },
    "erp_product_query:classify_list": {
        "main": {
            "id": "类目ID", "name": "类目名称",
            "parentId": "父类目ID",
        },
    },
    "erp_product_query:brand_list": {
        "main": {"brandId": "品牌ID", "brandName": "品牌名称"},
    },
    "erp_product_query:outer_id_list": {
        "main": {
            "numIid": "平台商品ID", "outerId": "系统商家编码",
            "userId": "店铺编码", "taobaoId": "平台店铺ID",
        },
    },
    "erp_product_query:outer_id_by_item": {
        "main": {
            "numIid": "平台商品ID", "outerId": "系统商家编码",
            "userId": "店铺编码", "taobaoId": "平台店铺ID",
        },
    },
    "erp_product_query:item_supplier_list": {
        "main": {
            "supplierName": "供应商名称", "supplierCode": "供应商编码",
            "supplierId": "供应商ID",
            "supplierItemOuterId": "供应商品编码",
            "supplierPurchasePrice": "供应商报价(进价)",
            "sysItemId": "商品ID", "sysSkuId": "规格ID",
            "discountRate": "折扣率", "returnRate": "退货率",
            "caigouUrl": "采购链接",
        },
    },
    "erp_product_query:virtual_warehouse": {
        "main": {
            "id": "虚拟仓ID", "name": "名称",
            "warehouseId": "归属仓库ID", "warehouseName": "归属仓库名称",
            "status": "状态",
        },
    },
    "erp_product_query:history_cost_price": {
        "main": {
            "importPrice": "成本价",
            "startStr": "开始时间", "endStr": "结束时间",
            "warehouseId": "仓库ID", "warehouseName": "仓库名称",
            "sysItemId": "商品ID", "sysSkuId": "规格ID",
            "bargainer": "议价员", "operationType": "操作类型",
        },
    },
    # ── 交易相关 ──
    "erp_trade_query:wave_query": {
        "main": {
            "id": "波次ID", "code": "波次号", "status": "状态",
            "tradesCount": "订单数量", "itemCount": "商品数量",
            "warehouseId": "仓库ID", "pickEndTime": "拣选完成时间",
        },
        "items": {
            "sid": "系统单号", "positionNo": "位置号",
            "tradeWaveStatus": "波次订单状态", "waveId": "波次号",
        },
        "items_key": "list",
    },
    "erp_trade_query:wave_sorting_query": {
        "main": {
            "positionNo": "位置号",
        },
        "items": {
            "outerId": "商品编码", "title": "商品名称",
            "propertiesName": "规格", "itemNum": "商品数量",
            "pickedNum": "已拣选数量", "matchedNum": "已播种数量",
        },
        "items_key": "details",
    },
    "erp_trade_query:unique_code_query": {
        "main": {
            "uniqueCode": "唯一码", "codeType": "码类型",
            "status": "状态", "outerId": "平台商家编码",
            "mainOuterId": "主商家编码", "skuOuterId": "规格编码",
            "sid": "系统订单号", "warehouseId": "仓库ID",
            "sellingPrice": "销售价", "costPrice": "成本价",
        },
    },
    "erp_trade_query:logistics_template_list": {
        "main": {
            "id": "模板ID", "name": "模板名称",
            "expressName": "快递公司", "expressId": "快递编码",
            "cpType": "服务类型", "liveStatus": "状态",
        },
    },
}


def get_formatter(name: str) -> Optional[Callable]:
    """根据名称获取格式化函数"""
    return _FORMATTER_REGISTRY.get(name)


def get_response_fields(
    formatter_name: str,
    tool_name: str = "",
    action_name: str = "",
) -> Optional[Dict[str, Any]]:
    """获取 API 返回字段信息

    查找优先级：
    1. 按 formatter 名精确匹配（专用 formatter 有 LABELS）
    2. 按 "tool:action" 匹配（generic_list 手动补充的字段）

    Returns:
        {"main": {字段名: 中文描述, ...}, "items": ..., "items_key": ...}
        或 None（无字段信息）
    """
    # 1. 按 formatter 名查找
    fields = _RESPONSE_FIELDS_REGISTRY.get(formatter_name)
    if fields:
        return fields

    # 2. 按 tool:action 查找（generic_list 兜底）
    if tool_name and action_name:
        return _ACTION_RESPONSE_FIELDS.get(f"{tool_name}:{action_name}")

    return None
