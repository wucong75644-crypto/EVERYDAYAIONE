"""企微聊天目标管理 REST API

群聊和私聊目标的管理面板（独立于定时任务的推送目标列表）。

路由：
- GET   /wecom-chat-targets/groups          列出所有群（管理员）
- PATCH /wecom-chat-targets/{id}/name       修改群名（管理员）

权限：仅老板/admin（与 org_members_assignments 一致）
设计文档: docs/document/UI_定时任务面板设计.md
"""
from __future__ import annotations
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import CurrentUserId, OrgCtx, Database


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


def _governed_rpc(db: Any, name: str, params: Dict[str, Any]) -> Any:
    try:
        return db.rpc(name, params).execute().data
    except Exception as exc:
        if "GOVERNANCE_AUTHORITY_DENIED" in str(exc):
            raise HTTPException(403, "仅老板/管理员可管理群聊") from exc
        if "WECOM_TARGET_ARGUMENT_INVALID" in str(exc):
            raise HTTPException(400, "企微聊天目标参数无效") from exc
        raise


# ════════════════════════════════════════════════════════
# 路由
# ════════════════════════════════════════════════════════

@router.get("/groups", summary="列出企业所有群聊（含手动标注的群名）")
async def list_groups(
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    db: Database,
) -> Dict[str, Any]:
    """
    列出 wecom_chat_targets 中所有 chat_type='group' 的记录。

    数据来源：被动收集——机器人在群里被 @ 时记录到 wecom_chat_targets。
    群名(chat_name)企微 API 拿不到，必须管理员手动标注。

    权限：仅老板/admin
    """
    org_id = _require_org(org_ctx)
    rows = _governed_rpc(db, "list_governed_wecom_chat_targets", {
        "p_org_id": org_id,
    })

    return {
        "success": True,
        "data": list(rows or []),
        "total": len(rows or []),
    }


@router.patch("/{target_id}/name", summary="修改群名（手动标注）")
async def update_chat_name(
    target_id: str,
    payload: UpdateChatNameRequest,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    db: Database,
) -> Dict[str, Any]:
    """
    手动修改群名。企微 API 拿不到群名，所有名字都靠管理员标注。

    校验：
    - target_id 必须属于当前企业（OrgScopedDB 自动隔离）
    - chat_name 必须非空
    """
    org_id = _require_org(org_ctx)
    new_name = payload.chat_name.strip()
    if not new_name:
        raise HTTPException(400, "群名不能为空")
    outcome = _governed_rpc(
        db, "update_governed_wecom_chat_target_name", {
            "p_org_id": org_id,
            "p_target_id": target_id,
            "p_chat_name": new_name,
        },
    )
    if not isinstance(outcome, dict) or outcome.get("updated") != 1:
        raise HTTPException(404, "群聊目标不存在")

    logger.info(
        f"Chat target name updated | actor={user_id} | "
        f"target_id={target_id} | org={org_id} | new_name={new_name}"
    )

    return {"success": True}
