"""
快麦 Web 数据接入 — 管理员 API

仅 org owner/admin 可访问。

Endpoints:
  GET    /api/admin/kuaimai/credentials             列出本 org 凭证
  POST   /api/admin/kuaimai/credentials             粘贴 cURL 创建/更新凭证
  DELETE /api/admin/kuaimai/credentials/{id}        删除
  POST   /api/admin/kuaimai/credentials/{id}/test   测试连接
  POST   /api/admin/kuaimai/sync/{source}           手动触发同步
  GET    /api/admin/kuaimai/sync-logs               同步记录
  GET    /api/admin/kuaimai/operators               运营列表（含未绑定）
  PATCH  /api/admin/kuaimai/operators/{id}/bind     管理员手动绑定企微
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import AsyncScopedDB, OrgCtx


from api.routes.kuaimai_external_common import require_kuaimai_admin
from api.routes.kuaimai_external_credentials import (
    router as credential_router,
)
router = APIRouter(prefix="/admin/kuaimai", tags=["快麦 Web 数据接入"])
router.include_router(credential_router)


# ──────────────────────── 权限校验 ────────────────────────


def _require_admin(org_ctx) -> str:
    """要求 owner/admin，返回 org_id。"""
    return require_kuaimai_admin(org_ctx)


# ──────────────────────── 模型 ────────────────────────


class SyncRequest(BaseModel):
    sync_type: Literal["daily", "manual", "backfill"] = "manual"
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    dimension: Optional[Literal["shop", "sku", "item", "day", "brand", "distributor"]] = "shop"


class SyncResultOut(BaseModel):
    success: bool
    log_id: Optional[str]
    rows_synced: int = 0
    cookie_expired: bool = False
    error: Optional[str] = None
    summary: Optional[dict] = None


class SyncLogOut(BaseModel):
    id: str
    source: str
    sync_type: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime]
    date_range_start: Optional[date]
    date_range_end: Optional[date]
    rows_synced: int
    error_message: Optional[str]
    metadata: Optional[dict]


class OperatorOut(BaseModel):
    id: str
    operator_name: str
    wecom_userid: Optional[str]
    is_bound: bool
    is_active: bool
    first_seen_at: Optional[datetime]
    last_seen_at: Optional[datetime]
    bound_at: Optional[datetime]
    notes: Optional[str]
    shop_count: int = 0


class BindOperatorIn(BaseModel):
    wecom_userid: str = Field(description="企微 user ID")
    operator_user_id: Optional[str] = Field(
        default=None, description="可选关联 users.id"
    )


# ──────────────────────── 手动触发同步 ────────────────────────


@router.post("/sync/{source}", summary="手动触发同步（异步：立即返回，后台运行）")
async def trigger_sync(
    source: Literal["thinktank", "viperp"],
    body: SyncRequest,
    org_ctx: OrgCtx,
    db: AsyncScopedDB,
) -> SyncResultOut:
    """
    异步触发同步：

    - 校验凭证存在（提前 fail）
    - asyncio.create_task 启动后台任务（async db 不阻塞 event loop）
    - 立即返回，前端通过同步记录 tab 看进度

    跟 services/kuaimai/erp_sync_worker_pool 同样架构：async db + create_task。
    """
    org_id = _require_admin(org_ctx)
    response = await db.rpc("runtime_enqueue_external_sync", {
        "p_org_id": org_id,
        "p_source": source,
        "p_sync_type": body.sync_type,
        "p_start_date": body.start_date,
        "p_end_date": body.end_date,
        "p_dimension": body.dimension or "shop",
    }).execute()
    if not isinstance(response.data, dict):
        raise HTTPException(status_code=503, detail="同步任务入队失败")

    return SyncResultOut(
        success=True,
        log_id=None,  # 实际 log_id 由后台 sync 内部生成
        rows_synced=0,
        error=None,
        summary={
            "queued": True,
            "request_id": response.data.get("request_id"),
            "message": "同步任务已进入持久队列",
        },
    )


# ──────────────────────── 同步记录 ────────────────────────


@router.get("/sync-logs", summary="同步记录")
async def list_sync_logs(
    org_ctx: OrgCtx,
    db: AsyncScopedDB,
    source: Optional[Literal["thinktank", "viperp"]] = None,
    limit: int = 20,
) -> list[SyncLogOut]:
    org_id = _require_admin(org_ctx)
    q = (
        db.table("kuaimai_sync_logs")
        .select("*")
        .eq("org_id", org_id)
        .order("started_at", desc=True)
        .limit(min(limit, 100))
    )
    if source:
        q = q.eq("source", source)
    resp = await q.execute()
    return [SyncLogOut(**row) for row in (resp.data or [])]


# ──────────────────────── 运营管理 ────────────────────────


@router.get("/operators", summary="运营列表（含店铺数）")
async def list_operators(
    org_ctx: OrgCtx,
    db: AsyncScopedDB,
    only_unbound: bool = False,
) -> list[OperatorOut]:
    org_id = _require_admin(org_ctx)

    q = (
        db.table("erp_operators")
        .select("*")
        .eq("org_id", org_id)
        .eq("is_active", True)
        .order("operator_name")
    )
    if only_unbound:
        q = q.eq("is_bound", False)
    resp = await q.execute()
    operators = resp.data or []

    # 计算每个运营管的店铺数（async pool）
    op_names = [o["operator_name"] for o in operators]
    shop_counts: dict[str, int] = {}
    if op_names:
        async with db.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT operator_name, COUNT(*) AS cnt
                    FROM erp_shop_operators
                    WHERE org_id = %s AND is_active = TRUE
                      AND operator_name = ANY(%s)
                    GROUP BY operator_name
                    """,
                    (org_id, op_names),
                )
                for r in await cur.fetchall():
                    shop_counts[r["operator_name"]] = r["cnt"]

    return [
        OperatorOut(
            id=o["id"],
            operator_name=o["operator_name"],
            wecom_userid=o.get("wecom_userid"),
            is_bound=o["is_bound"],
            is_active=o["is_active"],
            first_seen_at=o.get("first_seen_at"),
            last_seen_at=o.get("last_seen_at"),
            bound_at=o.get("bound_at"),
            notes=o.get("notes"),
            shop_count=shop_counts.get(o["operator_name"], 0),
        )
        for o in operators
    ]


@router.patch("/operators/{operator_id}/bind", summary="手动绑定运营到企微")
async def bind_operator(
    operator_id: str,
    body: BindOperatorIn,
    org_ctx: OrgCtx,
    db: AsyncScopedDB,
):
    org_id = _require_admin(org_ctx)
    response = await db.rpc("runtime_bind_erp_operator", {
        "p_org_id": org_id,
        "p_operator_id": operator_id,
        "p_wecom_userid": body.wecom_userid,
        "p_operator_user_id": body.operator_user_id,
    }).execute()
    if not isinstance(response.data, dict):
        raise HTTPException(status_code=503, detail="运营绑定结果无效")

    logger.info(
        f"kuaimai_external 运营手动绑定 | "
        f"org={org_id} operator={response.data.get('operator_name')} "
        f"→ wecom={body.wecom_userid} by={org_ctx.user_id}"
    )
    return {"bound": True}


@router.patch("/operators/{operator_id}/unbind", summary="手动解绑运营")
async def unbind_operator(
    operator_id: str,
    org_ctx: OrgCtx,
    db: AsyncScopedDB,
):
    org_id = _require_admin(org_ctx)
    response = await db.rpc("runtime_unbind_erp_operator", {
        "p_org_id": org_id,
        "p_operator_id": operator_id,
    }).execute()
    if not isinstance(response.data, dict):
        raise HTTPException(status_code=503, detail="运营解绑结果无效")
    return {"unbound": True}
