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
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import Database, OrgCtx
from services.kuaimai_external import (
    credential_store,
    curl_parser,
    http_base,
    thinktank_sync,
    viperp_sync,
)


router = APIRouter(prefix="/admin/kuaimai", tags=["快麦 Web 数据接入"])


# ──────────────────────── 权限校验 ────────────────────────


def _require_admin(org_ctx) -> str:
    """要求 owner/admin，返回 org_id。"""
    if not org_ctx.org_id:
        raise HTTPException(status_code=400, detail="必须在企业上下文中操作（X-Org-Id）")
    if org_ctx.org_role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="仅企业管理员可访问")
    return org_ctx.org_id


# ──────────────────────── 模型 ────────────────────────


class CredentialOut(BaseModel):
    id: str
    source: str
    kuaimai_company_id: int
    status: str
    censeid_preview: str = Field(description="cookie 预览（脱敏）")
    last_health_check_at: Optional[datetime]
    last_sync_at: Optional[datetime]
    last_sync_status: Optional[str]
    last_sync_error: Optional[str]
    created_at: datetime
    updated_at: datetime


def _to_credential_out(cred) -> CredentialOut:
    """凭证脱敏 — 不暴露完整 cookie 给前端。"""
    censeid = cred.censeid_cookie or ""
    preview = f"{censeid[:8]}...{censeid[-6:]}" if len(censeid) > 14 else "***"
    return CredentialOut(
        id=cred.id,
        source=cred.source,
        kuaimai_company_id=cred.kuaimai_company_id,
        status=cred.status,
        censeid_preview=preview,
        last_health_check_at=cred.last_health_check_at,
        last_sync_at=cred.last_sync_at,
        last_sync_status=cred.last_sync_status,
        last_sync_error=cred.last_sync_error,
        created_at=cred.created_at,
        updated_at=cred.updated_at,
    )


class CreateCredentialIn(BaseModel):
    """通过粘贴 cURL 创建/更新凭证"""
    curl_text: str = Field(
        description="浏览器 DevTools 复制的完整 cURL 字符串",
        min_length=20,
    )
    source: Optional[Literal["thinktank", "viperp"]] = Field(
        default=None,
        description="数据源，留空则从 URL 自动识别",
    )


class CreateCredentialOut(BaseModel):
    credential: CredentialOut
    detected_source: str
    detected_companyid: int


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


# ──────────────────────── 凭证 ────────────────────────


@router.get("/credentials", summary="列出当前企业的快麦凭证")
async def list_credentials(
    org_ctx: OrgCtx,
    db: Database,
) -> list[CredentialOut]:
    org_id = _require_admin(org_ctx)
    creds = credential_store.list_credentials(db, org_id=org_id)
    return [_to_credential_out(c) for c in creds]


@router.post("/credentials", summary="粘贴 cURL 创建/更新凭证")
async def create_credential(
    body: CreateCredentialIn,
    org_ctx: OrgCtx,
    db: Database,
) -> CreateCredentialOut:
    org_id = _require_admin(org_ctx)

    try:
        parsed = curl_parser.parse_curl(body.curl_text)
    except curl_parser.CurlParseError as e:
        raise HTTPException(status_code=400, detail=f"cURL 解析失败: {e}")

    if not parsed.companyid:
        raise HTTPException(
            status_code=400,
            detail="cURL 中缺少 companyid header（请确认复制了完整 cURL）",
        )
    if not parsed.censeid:
        raise HTTPException(
            status_code=400,
            detail="cURL 中缺少 _censeid cookie（可能未登录或 cURL 不完整）",
        )

    detected = curl_parser.detect_source(parsed)
    source = body.source or detected
    if source not in ("thinktank", "viperp"):
        raise HTTPException(
            status_code=400,
            detail=f"无法识别数据源（URL: {parsed.url}），请显式指定 source 字段",
        )

    cred_id = credential_store.save_credential(
        db,
        org_id=org_id,
        source=source,  # type: ignore
        kuaimai_company_id=parsed.companyid,
        censeid_cookie=parsed.censeid,
        cookie_full=parsed.cookie_full,
    )

    cred = credential_store.get_credential(db, org_id=org_id, source=source)  # type: ignore
    if not cred:
        raise HTTPException(status_code=500, detail="凭证保存后无法读回")

    logger.info(
        f"kuaimai_external 凭证已创建/更新 | "
        f"org={org_id} source={source} user={org_ctx.user_id} id={cred_id}"
    )
    return CreateCredentialOut(
        credential=_to_credential_out(cred),
        detected_source=source,
        detected_companyid=parsed.companyid,
    )


@router.delete("/credentials/{credential_id}", summary="删除凭证")
async def delete_credential(
    credential_id: str,
    org_ctx: OrgCtx,
    db: Database,
):
    org_id = _require_admin(org_ctx)
    ok = credential_store.delete_credential(
        db, credential_id=credential_id, org_id=org_id
    )
    if not ok:
        raise HTTPException(status_code=404, detail="凭证不存在或无权限")
    return {"deleted": True}


@router.post("/credentials/{credential_id}/test", summary="测试连接（探活）")
async def test_credential(
    credential_id: str,
    org_ctx: OrgCtx,
    db: Database,
):
    org_id = _require_admin(org_ctx)

    # 找凭证
    resp = (
        db.table("kuaimai_external_credentials")
        .select("*")
        .eq("id", credential_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not resp or not resp.data:
        raise HTTPException(status_code=404, detail="凭证不存在")

    cred = resp.data
    client = http_base.KuaimaiWebClient(
        companyid=cred["kuaimai_company_id"],
        cookie=cred.get("cookie_full") or f"_censeid={cred['censeid_cookie']}",
    )

    # 用最小 payload 调一次 thinktank（即使凭证是 viperp 也能用，因为是同源探活）
    # 仅为了快速验证 cookie 有效性
    test_payload = {
        "api_name": "ttps%3A__erp.superboss.cc_kmzk_profit_report_shop",
        "sysStatus": "1",
        "startTime": "1779552000000",
        "endTime": "1779638399000",
        "shopUniIds": "",
        "formulaId": "658",
        "ruleId": "230290901203812352",
        "showDimension": "0",
        "dateShowType": "0",
        "costType": "0",
        "isTrusted": "true",
    }

    try:
        if cred["source"] == "thinktank":
            await client.post(
                url="https://erp.superboss.cc/kmzk/profit/report/shop",
                payload=test_payload,
                module_path="/think_tank/profit_shop/",
                origin="https://erp.superboss.cc",
                referer="https://erp.superboss.cc/index.html",
            )
        else:
            # viperp 测试用 list 接口
            await client.post(
                url="https://erp.superboss.cc/report/sale/dimensions/finance/list",
                payload={
                    "pageNo": "1",
                    "pageSize": "1",
                    "pageId": "1123",
                    "queryFlag": "shop",
                    "startTime": "1779552000000",
                    "endTime": "1779638399000",
                    "containType": "1",
                    "exceptType": "1",
                    "containTradeOut": "true",
                    "onlyTradeOut": "false",
                    "containNonConsign": "true",
                    "containCancel": "false",
                    "matchFlag": "1",
                    "virtualFlag": "1",
                    "api_name": "report_sale_dimensions_finance_list",
                },
                module_path="/report/sale_multidimension_finance_next/",
                origin="https://erp.superboss.cc",
                referer="https://erp.superboss.cc/index.html",
            )

        # 测试通过：更新健康状态
        credential_store.record_sync_success(db, credential_id=credential_id)
        return {"ok": True, "message": "Cookie 有效，连接正常"}

    except http_base.CookieExpiredError as e:
        credential_store.mark_expired(db, credential_id=credential_id, error_msg=str(e))
        return {"ok": False, "message": f"Cookie 已失效: {e}"}
    except Exception as e:
        return {"ok": False, "message": f"调用失败: {e}"}
    finally:
        await client.close()


# ──────────────────────── 手动触发同步 ────────────────────────


async def _run_sync_in_background(
    source: str,
    org_id: str,
    sync_type: str,
    start_date,
    end_date,
    dimension: str,
) -> None:
    """
    后台运行同步任务，独立 DB 连接 + 异常吞掉。

    关键设计：内部异常必须自己 catch，不能冒泡到 asyncio.create_task 引发
    "Task exception was never retrieved" 警告。
    """
    from loguru import logger as _logger
    from core.database import get_db as _get_db

    db = _get_db()
    try:
        if source == "thinktank":
            await thinktank_sync.sync_thinktank(
                db,
                org_id=org_id,
                sync_type=sync_type,
                start_date=start_date,
                end_date=end_date,
            )
        else:
            await viperp_sync.sync_viperp(
                db,
                org_id=org_id,
                sync_type=sync_type,
                start_date=start_date,
                end_date=end_date,
                dimension=dimension,
            )
    except Exception as e:
        _logger.error(
            f"trigger_sync background task failed | "
            f"source={source} org={org_id} | err={e}"
        )


@router.post("/sync/{source}", summary="手动触发同步（异步：立即返回，后台运行）")
async def trigger_sync(
    source: Literal["thinktank", "viperp"],
    body: SyncRequest,
    org_ctx: OrgCtx,
    db: Database,
) -> SyncResultOut:
    """
    异步触发同步：

    - 立即创建 sync_logs 记录（status=running）
    - 启动 asyncio task 后台跑实际同步逻辑（不阻塞 HTTP worker）
    - 立即返回 log_id

    前端拿到 log_id 后，通过轮询 /sync-logs 看进度（success/failed）。
    """
    import asyncio
    from datetime import date as _date, timedelta as _timedelta
    from uuid import uuid4

    org_id = _require_admin(org_ctx)

    # 校验凭证存在（提前 fail，避免后台任务白跑）
    cred = credential_store.get_active_credential(db, org_id=org_id, source=source)
    if not cred:
        raise HTTPException(
            status_code=400,
            detail=f"{source} 没有可用凭证，请先配置或重新登录",
        )

    # 创建 sync_log 记录（status=running，前端立即能看到）
    end_d = body.end_date or _date.today()
    start_d = body.start_date or (end_d - _timedelta(days=7))
    log_resp = db.table("kuaimai_sync_logs").insert({
        "org_id": org_id,
        "source": source,
        "sync_type": body.sync_type,
        "status": "running",
        "date_range_start": start_d.isoformat(),
        "date_range_end": end_d.isoformat(),
    }).execute()
    placeholder_log_id = log_resp.data[0]["id"] if log_resp.data else str(uuid4())

    # 后台启动真实同步（不阻塞当前 HTTP 请求）
    # 注：实际 sync 内部会再创建 sync_log，这个 placeholder 仅供前端立即查询
    # 简化策略：把 placeholder 标记成 success/failed 由后台判断；
    # 但因为 sync 内部建自己的 log，placeholder 会冗余。
    # → 直接删 placeholder，让 sync 内部建唯一记录
    db.table("kuaimai_sync_logs").delete().eq("id", placeholder_log_id).execute()

    asyncio.create_task(_run_sync_in_background(
        source=source,
        org_id=org_id,
        sync_type=body.sync_type,
        start_date=body.start_date,
        end_date=body.end_date,
        dimension=body.dimension or "shop",
    ))

    return SyncResultOut(
        success=True,
        log_id=None,  # 实际 log_id 由后台 sync 内部生成
        rows_synced=0,
        error=None,
        summary={"queued": True, "message": "同步已在后台开始，请到「同步记录」tab 查看进度"},
    )


# ──────────────────────── 同步记录 ────────────────────────


@router.get("/sync-logs", summary="同步记录")
async def list_sync_logs(
    org_ctx: OrgCtx,
    db: Database,
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
    resp = q.execute()
    return [SyncLogOut(**row) for row in (resp.data or [])]


# ──────────────────────── 运营管理 ────────────────────────


@router.get("/operators", summary="运营列表（含店铺数）")
async def list_operators(
    org_ctx: OrgCtx,
    db: Database,
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
    resp = q.execute()
    operators = resp.data or []

    # 计算每个运营管的店铺数
    op_names = [o["operator_name"] for o in operators]
    shop_counts: dict[str, int] = {}
    if op_names:
        with db.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT operator_name, COUNT(*) AS cnt
                    FROM erp_shop_operators
                    WHERE org_id = %s AND is_active = TRUE
                      AND operator_name = ANY(%s)
                    GROUP BY operator_name
                    """,
                    (org_id, op_names),
                )
                for r in cur.fetchall():
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
    db: Database,
):
    org_id = _require_admin(org_ctx)

    # 校验运营存在且属于本 org
    op_resp = (
        db.table("erp_operators")
        .select("id, operator_name")
        .eq("id", operator_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not op_resp or not op_resp.data:
        raise HTTPException(status_code=404, detail="运营不存在")

    # 校验 wecom_userid 在该 org 的企微员工表里存在且在职
    emp_resp = (
        db.table("wecom_employees")
        .select("name, status")
        .eq("org_id", org_id)
        .eq("wecom_userid", body.wecom_userid)
        .eq("status", 1)
        .maybe_single()
        .execute()
    )
    if not emp_resp or not emp_resp.data:
        raise HTTPException(
            status_code=400,
            detail=f"企微账号 {body.wecom_userid} 不存在或已离职",
        )

    # 绑定
    now = datetime.now().isoformat()
    db.table("erp_operators").update({
        "wecom_userid": body.wecom_userid,
        "operator_user_id": body.operator_user_id,
        "is_bound": True,
        "bound_at": now,
        "bound_by": org_ctx.user_id,
        "notes": f"管理员手动绑定（{emp_resp.data['name']}）",
        "updated_at": now,
    }).eq("id", operator_id).execute()

    logger.info(
        f"kuaimai_external 运营手动绑定 | "
        f"org={org_id} operator={op_resp.data['operator_name']} "
        f"→ wecom={body.wecom_userid} by={org_ctx.user_id}"
    )
    return {"bound": True}


@router.patch("/operators/{operator_id}/unbind", summary="手动解绑运营")
async def unbind_operator(
    operator_id: str,
    org_ctx: OrgCtx,
    db: Database,
):
    org_id = _require_admin(org_ctx)
    resp = (
        db.table("erp_operators")
        .update({
            "wecom_userid": None,
            "operator_user_id": None,
            "is_bound": False,
            "notes": "管理员手动解绑",
            "updated_at": datetime.now().isoformat(),
        })
        .eq("id", operator_id)
        .eq("org_id", org_id)
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=404, detail="运营不存在")
    return {"unbound": True}
