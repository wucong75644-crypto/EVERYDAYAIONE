"""
死信重试时的行构建逻辑

从 erp_sync_handlers.py 中各 handler 的行构建逻辑提取，
死信消费者拿到 detail 后调用此模块构建 DB 行。
"""

from __future__ import annotations

from typing import Any

from services.kuaimai.erp_sync_utils import _pick, _safe_ts
from services.kuaimai.erp_sync_service import ErpSyncService


def build_rows_from_detail(
    doc_type: str, doc: dict, detail: dict,
) -> list[dict[str, Any]]:
    """根据 doc_type 分发到对应的行构建函数"""
    builder = _BUILDERS.get(doc_type)
    if builder is None:
        return []
    return builder(doc, detail)


def _build_purchase(doc: dict, detail: dict) -> list[dict[str, Any]]:
    from services.kuaimai.erp_sync_detail_builders import build_purchase
    return build_purchase(doc, detail)


def _build_receipt(doc: dict, detail: dict) -> list[dict[str, Any]]:
    from services.kuaimai.erp_sync_detail_builders import build_receipt
    return build_receipt(doc, detail)


def _build_shelf(doc: dict, detail: dict) -> list[dict[str, Any]]:
    from services.kuaimai.erp_sync_detail_builders import build_shelf
    return build_shelf(doc, detail)


def _build_purchase_return(doc: dict, detail: dict) -> list[dict[str, Any]]:
    from services.kuaimai.erp_sync_detail_builders import build_purchase_return
    return build_purchase_return(doc, detail)


_BUILDERS: dict[str, Any] = {
    "purchase": _build_purchase,
    "receipt": _build_receipt,
    "shelf": _build_shelf,
    "purchase_return": _build_purchase_return,
}
