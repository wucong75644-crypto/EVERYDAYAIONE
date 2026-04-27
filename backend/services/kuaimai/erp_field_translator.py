"""
旧表字段翻译（Python 层）

DuckDB 导出路径用 SQL CASE 表达式翻译字段（erp_duckdb_helpers._SPECIAL_CASE_MAP），
PG ORM 直查路径不走 DuckDB，需要在 Python 层做等价翻译。

本模块复用 erp_duckdb_helpers 中已有的映射常量，保证翻译一致性。

设计文档: docs/document/TECH_ERP查询架构重构.md §8.2
"""

from __future__ import annotations

from services.kuaimai.erp_duckdb_helpers import (
    _AFTERSALE_TYPE_CN,
    _GOOD_STATUS_CN,
    _ORDER_TYPE_CN,
    _REFUND_STATUS_CN,
    _STATUS_CN,
)
from services.kuaimai.erp_unified_schema import DOC_TYPE_CN, PLATFORM_CN

# 布尔字段集合（与 DuckDB _BOOL_FIELDS 保持一致）
_BOOL_FIELDS = frozenset({
    "is_cancel", "is_refund", "is_exception", "is_halt",
    "is_urgent", "is_scalping", "is_presell",
})

# 状态字段 → 统一用 _STATUS_CN 翻译
_STATUS_FIELDS = frozenset({
    "doc_status", "order_status", "unified_status",
    "online_status", "handler_status",
})


def translate_row(row: dict) -> dict:
    """逐行翻译字段值——与 DuckDB SQL CASE 表达式逻辑完全一致。

    就地修改 row 并返回，避免拷贝开销。
    """
    # platform 编码 → 中文
    if "platform" in row and row["platform"]:
        row["platform"] = PLATFORM_CN.get(row["platform"], row["platform"])

    # doc_type 编码 → 中文
    if "doc_type" in row and row["doc_type"]:
        row["doc_type"] = DOC_TYPE_CN.get(row["doc_type"], row["doc_type"])

    # doc_status / order_status 等状态字段
    for sf in _STATUS_FIELDS:
        if sf in row and row[sf]:
            row[sf] = _STATUS_CN.get(row[sf], row[sf])

    # order_type 复合标记（逗号分隔多值 → 中文用 / 连接）
    if "order_type" in row and row["order_type"]:
        parts = str(row["order_type"]).split(",")
        translated = [_ORDER_TYPE_CN.get(p.strip()) for p in parts]
        joined = "/".join(filter(None, translated))
        row["order_type"] = joined or row["order_type"]

    # aftersale_type 数字 → 中文
    if "aftersale_type" in row and row["aftersale_type"] is not None:
        row["aftersale_type"] = _AFTERSALE_TYPE_CN.get(
            str(row["aftersale_type"]), str(row["aftersale_type"]),
        )

    # refund_status 数字 → 中文
    if "refund_status" in row and row["refund_status"] is not None:
        row["refund_status"] = _REFUND_STATUS_CN.get(
            str(row["refund_status"]), str(row["refund_status"]),
        )

    # good_status 数字 → 中文
    if "good_status" in row and row["good_status"] is not None:
        row["good_status"] = _GOOD_STATUS_CN.get(
            str(row["good_status"]), str(row["good_status"]),
        )

    # 布尔字段 0/1 → 是/否
    for bf in _BOOL_FIELDS:
        if bf in row and row[bf] is not None:
            row[bf] = "是" if row[bf] in (1, True, "1") else "否"

    return row


def translate_rows(rows: list[dict]) -> list[dict]:
    """批量翻译，就地修改并返回。"""
    for row in rows:
        translate_row(row)
    return rows
