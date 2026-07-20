"""管理员用户管理路由 — 列表/积分调整/对话/资产 / 批量 ZIP（在 admin_users_zip 子路由）

权限：仅 super_admin 可访问，所有接口前置 _require_super_admin。
多租户：admin 跨 org 查询走原始 db（绕过 OrgScopedDB 自动隔离）；
       敏感操作（积分调整 / 批量下载）通过 admin_action_logs 表审计。
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from api.deps import CurrentUserId, Database

from .admin_users_helpers import (
    _extract_upload_parts,
    _mask_phone,
    _require_super_admin,
    _safe_parse_content,
    _log_admin_action,
    admin_adjust_credits,
)
from .admin_users_zip import zip_router
from .admin_user_assets import assets_router
router = APIRouter(prefix="/admin", tags=["admin-users"])
router.include_router(assets_router)
router.include_router(zip_router)

# ── 请求/响应模型 ────────────────────────────────────────


class RechargeRequest(BaseModel):
    delta: int = Field(..., description="正=充值，负=扣减，禁止 0")
    reason: str = Field("", max_length=200, description="操作备注")
    org_id: Optional[str] = Field(None, description="可选，记录到 credits_history 的 org_id")


# ── API：用户列表 ────────────────────────────────────────


@router.get("/users", summary="用户列表（超管）")
async def list_users(
    user_id: CurrentUserId,
    db: Database,
    search: Optional[str] = Query(None, description="昵称或手机号"),
    org_id: Optional[str] = Query(None, description="按企业过滤；'none' 表示散客"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict:
    _require_super_admin(user_id, db)

    query = db.table("users").select(
        "id, nickname, phone, avatar_url, role, credits, status, "
        "current_org_id, created_at, last_login_at, last_active_at",
        count="exact",
    )

    if search:
        s = search.strip()
        if re.fullmatch(r"1[3-9]\d{9}", s):
            query = query.eq("phone", s)
        else:
            safe = s.replace("%", "\\%").replace("_", "\\_")
            query = query.ilike("nickname", f"%{safe}%")

    if org_id == "none":
        query = query.is_("current_org_id", "null")
    elif org_id:
        query = query.eq("current_org_id", org_id)

    # 按上次活跃倒序（行业标准 admin 默认）。从未活跃的（NULL）排末尾
    offset = (page - 1) * page_size
    result = (
        query.order("last_active_at", desc=True, nulls_first=False)
        .order("created_at", desc=True)  # 同活跃时间时按注册时间次排
        .range(offset, offset + page_size - 1)
        .execute()
    )

    raw_items = result.data or []

    # 批量查 org_name（避免 N+1）
    org_ids = list({u["current_org_id"] for u in raw_items if u.get("current_org_id")})
    org_map: dict[str, str] = {}
    if org_ids:
        orgs = db.table("organizations").select("id, name").in_("id", org_ids).execute()
        org_map = {r["id"]: r.get("name") or "" for r in (orgs.data or [])}

    items = []
    for u in raw_items:
        items.append({
            **u,
            "phone": _mask_phone(u.get("phone")),
            "org_name": org_map.get(u.get("current_org_id")) if u.get("current_org_id") else None,
        })

    total = result.count if hasattr(result, "count") and result.count is not None else len(items)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ── API：用户概览 ────────────────────────────────────────


@router.get("/users/{uid}/summary", summary="用户概览（超管）")
async def get_user_summary(uid: str, user_id: CurrentUserId, db: Database) -> dict:
    _require_super_admin(user_id, db)

    user_result = (
        db.table("users")
        .select(
            "id, nickname, phone, avatar_url, role, credits, status, "
            "current_org_id, created_at, last_login_at, last_active_at"
        )
        .eq("id", uid).maybe_single().execute()
    )
    if not user_result or not user_result.data:
        raise HTTPException(status_code=404, detail="用户不存在")
    user = user_result.data

    org_name = None
    if user.get("current_org_id"):
        org_result = (
            db.table("organizations").select("name")
            .eq("id", user["current_org_id"]).maybe_single().execute()
        )
        if org_result and org_result.data:
            org_name = org_result.data.get("name")

    consumed_result = (
        db.table("credits_history").select("change_amount")
        .eq("user_id", uid).lt("change_amount", 0).execute()
    )
    total_consumed = sum(abs(r["change_amount"]) for r in (consumed_result.data or []))

    conv_result = (
        db.table("conversations").select("id", count="exact")
        .eq("user_id", uid).execute()
    )
    conversation_count = (
        conv_result.count if hasattr(conv_result, "count") and conv_result.count is not None else 0
    )

    return {
        **user,
        "phone": _mask_phone(user.get("phone")),
        "org_name": org_name,
        "total_consumed": total_consumed,
        "conversation_count": conversation_count,
    }


# ── API：积分充值/扣减 ───────────────────────────────────


@router.post("/users/{uid}/credits/recharge", summary="管理员调整积分（超管）")
async def recharge_credits(
    uid: str,
    body: RechargeRequest,
    user_id: CurrentUserId,
    db: Database,
) -> dict:
    _require_super_admin(user_id, db)

    if body.delta == 0:
        raise HTTPException(status_code=422, detail="调整数量不能为 0")

    user_check = db.table("users").select("id").eq("id", uid).maybe_single().execute()
    if not user_check or not user_check.data:
        raise HTTPException(status_code=404, detail="用户不存在")

    new_balance = await admin_adjust_credits(
        db=db,
        user_id=uid,
        delta=body.delta,
        reason=body.reason or "管理员调整",
        operator_id=user_id,
        org_id=body.org_id,
    )

    _log_admin_action(
        db,
        admin_id=user_id,
        action_type="credits_adjust",
        description=f"调整积分 {body.delta:+d}",
        target_user_id=uid,
        target_resource_type="credits",
        reason=body.reason or None,
        changes_data={"delta": body.delta, "new_balance": new_balance},
    )

    return {"success": True, "new_balance": new_balance, "delta": body.delta}


# ── API：积分流水 ────────────────────────────────────────


@router.get("/users/{uid}/credits/history", summary="积分流水（超管）")
async def get_credits_history(
    uid: str,
    user_id: CurrentUserId,
    db: Database,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict:
    _require_super_admin(user_id, db)

    offset = (page - 1) * page_size
    result = (
        db.table("credits_history")
        .select("*", count="exact").eq("user_id", uid)
        .order("created_at", desc=True)
        .range(offset, offset + page_size - 1).execute()
    )

    items = result.data or []
    operator_ids = list({r["operator_id"] for r in items if r.get("operator_id")})
    operators_map: dict[str, str] = {}
    if operator_ids:
        op_result = db.table("users").select("id, nickname").in_("id", operator_ids).execute()
        operators_map = {r["id"]: r.get("nickname") or "" for r in (op_result.data or [])}

    enriched = [
        {**r, "operator_name": operators_map.get(r.get("operator_id")) if r.get("operator_id") else None}
        for r in items
    ]

    total = result.count if hasattr(result, "count") and result.count is not None else len(items)
    return {"items": enriched, "total": total, "page": page, "page_size": page_size}


# ── API：对话列表 ────────────────────────────────────────


@router.get("/users/{uid}/conversations", summary="用户对话列表（超管）")
async def list_user_conversations(
    uid: str,
    user_id: CurrentUserId,
    db: Database,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict:
    _require_super_admin(user_id, db)

    offset = (page - 1) * page_size
    result = (
        db.table("conversations")
        .select(
            "id, title, model_id, message_count, credits_consumed, "
            "last_message_preview, updated_at, created_at",
            count="exact",
        )
        .eq("user_id", uid).order("updated_at", desc=True)
        .range(offset, offset + page_size - 1).execute()
    )
    total = result.count if hasattr(result, "count") and result.count is not None else len(result.data or [])
    return {"items": result.data or [], "total": total, "page": page, "page_size": page_size}


# ── API：对话内消息 ──────────────────────────────────────


@router.get("/users/{uid}/conversations/{cid}/messages", summary="对话内消息（超管）")
async def get_conversation_messages(
    uid: str,
    cid: str,
    user_id: CurrentUserId,
    db: Database,
    limit: int = Query(500, ge=1, le=2000),
) -> dict:
    _require_super_admin(user_id, db)

    conv = (
        db.table("conversations").select("user_id, title")
        .eq("id", cid).maybe_single().execute()
    )
    if not conv or not conv.data:
        raise HTTPException(status_code=404, detail="对话不存在")
    if str(conv.data["user_id"]) != str(uid):
        raise HTTPException(status_code=403, detail="对话不属于该用户")

    result = (
        db.table("messages")
        .select(
            "id, conversation_id, role, content, image_url, video_url, "
            "credits_cost, is_error, generation_params, created_at"
        )
        .eq("conversation_id", cid)
        .order("created_at", desc=False)
        .limit(limit).execute()
    )

    messages = []
    for m in (result.data or []):
        parsed = _safe_parse_content(m.get("content"))
        # 所有 role 都解析 content：用户消息→上传附件；助手消息→生成结果（图/视频）
        messages.append({
            **m,
            "content_parsed": parsed,
            "attachments": _extract_upload_parts(parsed),
        })

    return {
        "conversation": {"id": cid, "title": conv.data.get("title")},
        "items": messages,
        "total": len(messages),
    }
