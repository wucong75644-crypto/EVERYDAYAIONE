"""管理员用户管理路由 — 列表/积分调整/对话/资产 / 批量 ZIP（在 admin_users_zip 子路由）

权限：仅 super_admin 可访问，所有接口前置 _require_super_admin。
多租户：admin 跨 org 查询走原始 db（绕过 OrgScopedDB 自动隔离）；
       敏感操作（积分调整 / 批量下载）通过 admin_action_logs 表审计。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
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


router = APIRouter(prefix="/admin", tags=["admin-users"])
router.include_router(zip_router)


# ── 请求/响应模型 ────────────────────────────────────────


class RechargeRequest(BaseModel):
    delta: int = Field(..., description="正=充值，负=扣减，禁止 0")
    reason: str = Field("", max_length=200, description="操作备注")
    org_id: Optional[str] = Field(None, description="可选，记录到 credits_history 的 org_id")


# ── 常量 ─────────────────────────────────────────────────


_DEFAULT_UPLOADS_DAYS = 90
_DEFAULT_GENERATIONS_LIMIT = 1000


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
        "id, nickname, phone, avatar_url, role, credits, status, current_org_id, created_at",
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

    offset = (page - 1) * page_size
    result = (
        query.order("created_at", desc=True)
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
        .select("id, nickname, phone, avatar_url, role, credits, status, current_org_id, created_at")
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


# ── API：上传资产 ────────────────────────────────────────


@router.get("/users/{uid}/uploads", summary="用户上传资产（超管）")
async def list_user_uploads(
    uid: str,
    user_id: CurrentUserId,
    db: Database,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    days: int = Query(_DEFAULT_UPLOADS_DAYS, ge=1, le=365),
) -> dict:
    """扫描该用户的 user 消息（默认近 90 天），从 content JSONB 提取附件 URL。"""
    _require_super_admin(user_id, db)

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    convs = db.table("conversations").select("id").eq("user_id", uid).execute()
    conv_ids = [c["id"] for c in (convs.data or [])]
    if not conv_ids:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}

    msgs = (
        db.table("messages")
        .select("id, conversation_id, content, created_at")
        .in_("conversation_id", conv_ids).eq("role", "user")
        .gte("created_at", since).order("created_at", desc=True)
        .limit(_DEFAULT_GENERATIONS_LIMIT).execute()
    )

    assets: list[dict] = []
    for m in (msgs.data or []):
        parsed = _safe_parse_content(m.get("content"))
        for part in _extract_upload_parts(parsed):
            assets.append({
                **part,
                "message_id": m["id"],
                "conversation_id": m["conversation_id"],
                "created_at": m["created_at"],
            })

    total = len(assets)
    start = (page - 1) * page_size
    return {
        "items": assets[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ── API：生成资产 ────────────────────────────────────────


@router.get("/users/{uid}/generations", summary="用户 AI 生成资产（超管）")
async def list_user_generations(
    uid: str,
    user_id: CurrentUserId,
    db: Database,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    kind: Optional[str] = Query(None, description="image / video / 不传则全部"),
) -> dict:
    """聚合 image_generations + tasks(type=video, status=completed)"""
    _require_super_admin(user_id, db)

    items: list[dict] = []

    if kind in (None, "image"):
        img = (
            db.table("image_generations")
            .select(
                "id, conversation_id, prompt, negative_prompt, image_size, "
                "image_url, credits_cost, created_at, model_id"
            )
            .eq("user_id", uid).order("created_at", desc=True)
            .limit(_DEFAULT_GENERATIONS_LIMIT).execute()
        )
        for r in (img.data or []):
            if not r.get("image_url"):
                continue
            items.append({
                "kind": "image",
                "id": r["id"],
                "url": r["image_url"],
                "prompt": r.get("prompt"),
                "negative_prompt": r.get("negative_prompt"),
                "model_id": r.get("model_id"),
                "size": r.get("image_size"),
                "credits_cost": r.get("credits_cost") or 0,
                "conversation_id": r.get("conversation_id"),
                "created_at": r["created_at"],
            })

    if kind in (None, "video"):
        vid = (
            db.table("tasks")
            .select("id, conversation_id, request_params, result, credits_used, created_at")
            .eq("user_id", uid).eq("type", "video").eq("status", "completed")
            .order("created_at", desc=True).limit(500).execute()
        )
        for r in (vid.data or []):
            res = r.get("result") or {}
            params = r.get("request_params") or {}
            url = res.get("video_url")
            if not url:
                continue
            items.append({
                "kind": "video",
                "id": r["id"],
                "url": url,
                "prompt": params.get("prompt") or res.get("prompt"),
                "negative_prompt": params.get("negative_prompt"),
                "model_id": params.get("model_id"),
                "size": None,
                "credits_cost": r.get("credits_used") or 0,
                "conversation_id": r.get("conversation_id"),
                "created_at": r["created_at"],
            })

    # 新链路：assistant 消息的生成图存在 messages.content JSONB（非 image_generations 表）
    # 例：gpt-image-2 / 电商图链路 — 不写 image_generations，直接塞进 message.content
    # 用 url 去重（避免与 image_generations 重复）
    convs = db.table("conversations").select("id").eq("user_id", uid).execute()
    conv_ids = [c["id"] for c in (convs.data or [])]
    existing_urls = {it["url"] for it in items}
    if conv_ids:
        ai_msgs = (
            db.table("messages")
            .select("id, conversation_id, content, generation_params, credits_cost, created_at")
            .in_("conversation_id", conv_ids).eq("role", "assistant")
            .order("created_at", desc=True)
            .limit(_DEFAULT_GENERATIONS_LIMIT).execute()
        )
        for m in (ai_msgs.data or []):
            parsed = _safe_parse_content(m.get("content"))
            params = m.get("generation_params") or {}
            gen_type = params.get("type") if isinstance(params, dict) else None
            for part in _extract_upload_parts(parsed):
                url = part["url"]
                if url in existing_urls:
                    continue
                # 推断 kind：generation_params.type 优先 / 按 url 后缀兜底
                if gen_type == "video":
                    item_kind = "video"
                elif gen_type == "image":
                    item_kind = "image"
                else:
                    item_kind = "video" if url.lower().endswith((".mp4", ".mov", ".webm")) else "image"

                if kind and item_kind != kind:
                    continue

                items.append({
                    "kind": item_kind,
                    "id": m["id"],
                    "url": url,
                    "prompt": params.get("prompt") if isinstance(params, dict) else None,
                    "negative_prompt": params.get("negative_prompt") if isinstance(params, dict) else None,
                    "model_id": params.get("model") or params.get("model_id") if isinstance(params, dict) else None,
                    "size": params.get("resolution") or params.get("aspect_ratio") if isinstance(params, dict) else None,
                    "credits_cost": m.get("credits_cost") or 0,
                    "conversation_id": m.get("conversation_id"),
                    "created_at": m["created_at"],
                })
                existing_urls.add(url)

    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    total = len(items)
    start = (page - 1) * page_size
    return {
        "items": items[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
