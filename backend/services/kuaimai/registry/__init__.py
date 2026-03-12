"""
快麦ERP API注册表

每个API = 一条ApiEntry配置（method、param_map、formatter）。
按类别分文件存储，统一导出供Dispatcher使用。
"""

from services.kuaimai.registry.base import ApiEntry
from services.kuaimai.registry.basic import BASIC_REGISTRY
from services.kuaimai.registry.product import PRODUCT_REGISTRY
from services.kuaimai.registry.trade import TRADE_REGISTRY
from services.kuaimai.registry.aftersales import AFTERSALES_REGISTRY
from services.kuaimai.registry.warehouse import WAREHOUSE_REGISTRY
from services.kuaimai.registry.purchase import PURCHASE_REGISTRY
from services.kuaimai.registry.distribution import DISTRIBUTION_REGISTRY
from services.kuaimai.registry.qimen import QIMEN_REGISTRY

# 工具名 → 注册表映射（Dispatcher查表用）
TOOL_REGISTRIES = {
    "erp_info_query": BASIC_REGISTRY,
    "erp_product_query": PRODUCT_REGISTRY,
    "erp_trade_query": TRADE_REGISTRY,
    "erp_aftersales_query": AFTERSALES_REGISTRY,
    "erp_warehouse_query": WAREHOUSE_REGISTRY,
    "erp_purchase_query": PURCHASE_REGISTRY,
    "erp_taobao_query": QIMEN_REGISTRY,
    "erp_execute": {
        **{k: v for k, v in BASIC_REGISTRY.items() if v.is_write},
        **{k: v for k, v in PRODUCT_REGISTRY.items() if v.is_write},
        **{k: v for k, v in TRADE_REGISTRY.items() if v.is_write},
        **{k: v for k, v in AFTERSALES_REGISTRY.items() if v.is_write},
        **{k: v for k, v in WAREHOUSE_REGISTRY.items() if v.is_write},
        **{k: v for k, v in PURCHASE_REGISTRY.items() if v.is_write},
        **{k: v for k, v in DISTRIBUTION_REGISTRY.items() if v.is_write},
    },
}

__all__ = [
    "ApiEntry",
    "TOOL_REGISTRIES",
    "BASIC_REGISTRY",
    "PRODUCT_REGISTRY",
    "TRADE_REGISTRY",
    "AFTERSALES_REGISTRY",
    "WAREHOUSE_REGISTRY",
    "PURCHASE_REGISTRY",
    "DISTRIBUTION_REGISTRY",
    "QIMEN_REGISTRY",
]
