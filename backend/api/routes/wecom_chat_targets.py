"""企微聊天目标管理 REST API

群聊和私聊目标的管理面板（独立于定时任务的推送目标列表）。

路由：
- GET   /wecom-chat-targets/groups          列出所有群（管理员）
- PATCH /wecom-chat-targets/{id}/name       修改群名（管理员）

权限：仅老板/admin（与 org_members_assignments 一致）
设计文档: docs/document/UI_定时任务面板设计.md
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import CurrentUserId, OrgCtx, ScopedDB, Database


router = APIRouter(prefix="/wecom-chat-targets", tags=["企微聊天目标管理"])


# ════════════════════════════════════════════════════════
# Schemas
# ════════════════════════════════════════════════════════

class UpdateChatNameRequest(BaseModel):
    """修改群/单聊名"""
    chat_name: str = Field(..., min_length=1, max_length=256)


# ════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════

def _require_org(org_ctx: Any) -> str:
    if not org_ctx.org_id:
        raise HTTPException(403, "此功能仅企业用户可用")
    return org_ctx.org_id


def _require_admin(db: Any, user_id: str, org_id: str) -> str:
    """要求当前用户是 owner 或 admin（org_members.role）"""
    result = db.table("org_members") \
        .select("role") \
        .eq("org_id", org_id) \
        .eq("user_id", user_id) \
        .limit(1) \
        .execute()
    if not result.data:
        raise HTTPException(403, "您不是该组织成员")
    role = result.data[0]["role"]
    if role not in ("owner", "admin"):
        raise HTTPException(403, "仅老板/管理员可管理群聊")
    return role


# ════════════════════════════════════════════════════════
# 路由
# ════════════════════════════════════════════════════════

@router.get("/groups", summary="列出企业所有群聊（含手动标注的群名）")
async def list_groups(
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
) -> Dict[str, Any]:
    """
    列出 wecom_chat_targets 中所有 chat_type='group' 的记录。

    数据来源：被动收集——机器人在群里被 @ 时记录到 wecom_chat_targets。
    群名(chat_name)企微 API 拿不到，必须管理员手动标注。

    权限：仅老板/admin
    """
    org_id = _require_org(org_ctx)
    _require_admin(db, user_id, org_id)

    result = scoped_db.table("wecom_chat_targets") \
        .select("id, chatid, chat_type, chat_name, last_active, "
                "first_seen, message_count, is_active") \
        .eq("chat_type", "group") \
        .order("last_active", desc=True) \
        .execute()

    return {
        "success": True,
        "data": list(result.data or []),
        "total": len(result.data or []),
    }


@router.patch("/{target_id}/name", summary="修改群名（手动标注）")
async def update_chat_name(
    target_id: str,
    payload: UpdateChatNameRequest,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
) -> Dict[str, Any]:
    """
    手动修改群名。企微 API 拿不到群名，所有名字都靠管理员标注。

    校验：
    - target_id 必须属于当前企业（OrgScopedDB 自动隔离）
    - chat_name 必须非空
    """
    org_id = _require_org(org_ctx)
    _require_admin(db, user_id, org_id)

    # 校验目标存在且属于当前企业
    existing = scoped_db.table("wecom_chat_targets") \
        .select("id, chat_type") \
        .eq("id", target_id) \
        .limit(1) \
        .execute()
    if not existing.data:
        raise HTTPException(404, "群聊目标不存在")

    new_name = payload.chat_name.strip()
    if not new_name:
        raise HTTPException(400, "群名不能为空")

    scoped_db.table("wecom_chat_targets").update({
        "chat_name": new_name,
    }).eq("id", target_id).execute()

    logger.info(
        f"Chat target name updated | actor={user_id} | "
        f"target_id={target_id} | org={org_id} | new_name={new_name}"
    )

    return {"success": True}
