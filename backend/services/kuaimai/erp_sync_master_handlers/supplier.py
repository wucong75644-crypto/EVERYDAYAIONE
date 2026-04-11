"""ERP 供应商同步：supplier.list.query → erp_suppliers"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from services.kuaimai.erp_sync_utils import _batch_upsert

if TYPE_CHECKING:
    from services.kuaimai.erp_sync_service import ErpSyncService


async def sync_supplier(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """供应商全量同步：supplier.list.query → erp_suppliers（翻页拉取）"""
    suppliers = await svc.fetch_all_pages(
        "supplier.list.query", {},
        response_key="list",
        page_size=200,  # API max=200
    )
    if not suppliers:
        return 0

    rows: list[dict[str, Any]] = []
    for s in suppliers:
        code = s.get("code")
        if not code:
            continue
        rows.append({
            "code": code,
            "name": s.get("name", ""),
            "status": s.get("status", 1),
            "contact_name": s.get("contactName"),
            "mobile": s.get("mobile"),
            "phone": s.get("phone"),
            "email": s.get("email"),
            "category_name": s.get("categoryName"),
            "bill_type": s.get("billType"),
            "plan_receive_day": s.get("planReceiveDay"),
            "address": s.get("address"),
            "remark": s.get("remark"),
        })

    count = await _batch_upsert(
        svc.db, "erp_suppliers", rows, "code,org_id", org_id=svc.org_id,
    )
    return count
