"""
ERP 主数据同步处理器（4 种）

product / stock / supplier / platform_map
每个处理器写入对应的主数据表（非 erp_document_items）

设计文档: docs/document/TECH_ERP数据本地索引系统.md §7.1

包结构（2026-04-11 拆分）：
- product.py        — sync_product
- stock.py          — sync_stock + sync_stock_full + helpers
- supplier.py       — sync_supplier
- platform_map.py   — sync_platform_map + helpers + 常量

外部 import 路径不变：
    from services.kuaimai.erp_sync_master_handlers import sync_product
"""

# ── 4 个 sync 函数（公开 API）────────────────────────
from services.kuaimai.erp_sync_master_handlers.platform_map import (
    sync_platform_map,
)
from services.kuaimai.erp_sync_master_handlers.product import sync_product
from services.kuaimai.erp_sync_master_handlers.stock import (
    _fetch_stock_by_codes,
    _map_stock_item,
    sync_stock,
    sync_stock_full,
)
from services.kuaimai.erp_sync_master_handlers.supplier import sync_supplier

# ── platform_map 内部 helper（dead_letter 重试需要）────
from services.kuaimai.erp_sync_master_handlers.platform_map import (
    _PLATFORM_MAP_BATCH_SIZE,
    _PLATFORM_MAP_LOCK_EXTEND_EVERY,
    _PLATFORM_MAP_SPLIT_MAX_DEPTH,
    _PLATFORM_MAP_SPLIT_MIN_SIZE,
    _PLATFORM_MAP_STALE_DAYS,
    _PLATFORM_MAP_UPDATE_CHUNK,
    _dedupe_platform_rows,
    _enqueue_failed_batch_to_dlq,
    _mark_skus_checked,
    _parse_platform_map_items,
    _process_platform_map_batch,
    _select_skus_for_round,
)

# ── 工具函数 re-export（向后兼容，测试和外部代码使用）─
# 历史：原 master_handlers.py 顶层用 noqa F401 re-export 这些工具
# 拆包后保持外部 import 路径不变
from services.kuaimai.erp_sync_utils import (  # noqa: F401
    _batch_upsert,
    _fmt_dt,
    _ms_to_iso,
    _pick,
    _safe_ts,
    _strip_html,
)

__all__ = [
    # sync 函数
    "sync_product",
    "sync_stock",
    "sync_stock_full",
    "sync_supplier",
    "sync_platform_map",
    # stock 内部 helper（测试需要）
    "_map_stock_item",
    "_fetch_stock_by_codes",
    # platform_map helper（DLQ 重试需要 cross-import）
    "_parse_platform_map_items",
    "_dedupe_platform_rows",
    "_process_platform_map_batch",
    "_enqueue_failed_batch_to_dlq",
    "_select_skus_for_round",
    "_mark_skus_checked",
    # platform_map 常量（测试 patch 需要）
    "_PLATFORM_MAP_BATCH_SIZE",
    "_PLATFORM_MAP_STALE_DAYS",
    "_PLATFORM_MAP_SPLIT_MAX_DEPTH",
    "_PLATFORM_MAP_SPLIT_MIN_SIZE",
    "_PLATFORM_MAP_LOCK_EXTEND_EVERY",
    "_PLATFORM_MAP_UPDATE_CHUNK",
    # 工具函数 re-export
    "_strip_html",
    "_fmt_dt",
    "_ms_to_iso",
    "_pick",
    "_safe_ts",
    "_batch_upsert",
]
