"""
格式化器注册表

所有格式化函数按类别分文件，统一通过 get_formatter(name) 获取。
"""

from typing import Callable, Dict, Optional

from services.kuaimai.formatters.common import (
    format_generic_list,
    format_generic_detail,
    format_timestamp,
    parse_date,
)
from services.kuaimai.formatters.trade import TRADE_FORMATTERS
from services.kuaimai.formatters.product import PRODUCT_FORMATTERS
from services.kuaimai.formatters.basic import BASIC_FORMATTERS
from services.kuaimai.formatters.aftersales import AFTERSALES_FORMATTERS
from services.kuaimai.formatters.warehouse import WAREHOUSE_FORMATTERS
from services.kuaimai.formatters.purchase import PURCHASE_FORMATTERS
from services.kuaimai.formatters.qimen import QIMEN_FORMATTERS

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


def get_formatter(name: str) -> Optional[Callable]:
    """根据名称获取格式化函数"""
    return _FORMATTER_REGISTRY.get(name)
