"""admin_users 路由内部工具与共享逻辑

包含：
- _require_super_admin：权限校验依赖
- _safe_parse_content / _extract_upload_parts：messages.content JSONB 解析
- _filename_from_url / _mask_phone / _ascii_zip_name：通用工具
- _log_admin_action：审计日志写入
- admin_adjust_credits：调用 RPC admin_adjust_credits（manager 后台专用）
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from fastapi import HTTPException
from loguru import logger

from core.exceptions import AppException


# ── 权限校验 ─────────────────────────────────────────────


def _require_super_admin(user_id: str, db) -> None:
    """仅 super_admin 可访问（与 error_monitor 范式对齐）"""
    result = db.table("users").select("role").eq("id", user_id).maybe_single().execute()
    if not result or not result.data or result.data.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="仅超级管理员可访问")


# ── content 解析 ─────────────────────────────────────────


def _safe_parse_content(raw: Any) -> Any:
    """messages.content 可能是 JSON 字符串、纯文本或已解析的 list/dict — 统一解析"""
    if raw is None:
        return None
    if isinstance(raw, (list, dict)):
        return raw
    if not isinstance(raw, str):
        return raw
    s = raw.strip()
    if not s or s[0] not in ("[", "{"):
        return raw
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return raw


def _extract_upload_parts(parts: Any) -> list[dict]:
    """从 content JSONB 数组里挑出用户上传的附件 ContentPart

    识别 type ∈ {file, image, image_url}，提取 {url, name?, size?, mime?, type}
    """
    if not isinstance(parts, list):
        return []
    out: list[dict] = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        t = p.get("type")
        url = None
        if t in ("file", "image"):
            url = p.get("url")
        elif t == "image_url":
            url_obj = p.get("image_url") or {}
            url = url_obj.get("url") if isinstance(url_obj, dict) else None
            t = "image"
        if not url or not isinstance(url, str):
            continue
        out.append({
            "url": url,
            **_asset_url_fields(url, t, p),
            "name": p.get("name") or p.get("filename") or _filename_from_url(url),
            "type": t,
            "size": p.get("size"),
            "mime": p.get("mime") or p.get("mime_type"),
        })
    return out


def _asset_url_fields(
    url: str,
    kind: str = "image",
    part: Optional[dict] = None,
) -> dict[str, Optional[str]]:
    """统一资产 URL 语义字段；缩略仅用于小图展示，下载/预览默认原图。"""
    part = part or {}
    original_url = part.get("original_url") or part.get("download_url") or part.get("preview_url") or url
    return {
        "original_url": original_url,
        "preview_url": part.get("preview_url") or original_url,
        "download_url": part.get("download_url") or original_url,
        "thumbnail_url": part.get("thumbnail_url") if kind == "image" else None,
    }


# ── 通用工具 ─────────────────────────────────────────────


def _filename_from_url(url: str) -> str:
    try:
        path = urlparse(url).path
        name = unquote(path.rsplit("/", 1)[-1])
        return name or "file"
    except Exception:
        return "file"


def _mask_phone(phone: Optional[str]) -> Optional[str]:
    if not phone or len(phone) < 7:
        return phone
    return phone[:3] + "****" + phone[-4:]


def _ascii_zip_name(name: str) -> str:
    safe = re.sub(r"[^\x20-\x7e]", "_", name).replace('"', "_").replace("\\", "_")
    if not safe.lower().endswith(".zip"):
        return "download.zip"
    return safe


# ── 审计 ─────────────────────────────────────────────────


def _log_admin_action(
    db,
    admin_id: str,
    action_type: str,
    description: str,
    target_user_id: Optional[str] = None,
    target_resource_type: Optional[str] = None,
    target_resource_id: Optional[str] = None,
    reason: Optional[str] = None,
    changes_data: Optional[dict] = None,
) -> None:
    """写 admin_action_logs（失败不阻断主流程）"""
    try:
        db.table("admin_action_logs").insert({
            "admin_id": admin_id,
            "admin_role": "super_admin",
            "action_type": action_type,
            "action_description": description,
            "target_user_id": target_user_id,
            "target_resource_type": target_resource_type,
            "target_resource_id": target_resource_id,
            "reason": reason,
            "changes_data": changes_data,
        }).execute()
    except Exception as e:
        logger.warning(f"admin_action_logs 写入失败 | {e}")


# ── 积分调整（admin 专用，调用 RPC admin_adjust_credits）────


async def admin_adjust_credits(
    db,
    user_id: str,
    delta: int,
    reason: str,
    operator_id: str,
    org_id: Optional[str] = None,
) -> int:
    """管理员手动调整积分（正=充值 / 负=扣减），调用 migration 115 的 RPC

    单事务保证：行锁串行化 + 余额不为负校验 + 写 credits_history.operator_id 审计。
    """
    if delta == 0:
        raise AppException(code="INVALID_DELTA", message="调整数量不能为 0", status_code=422)
    try:
        result = db.rpc(
            "admin_adjust_credits",
            {
                "p_user_id": user_id,
                "p_delta": delta,
                "p_reason": reason,
                "p_operator_id": operator_id,
                "p_org_id": org_id,
            },
        ).execute()
        data = result.data or {}
        if not data.get("success"):
            reason_code = data.get("reason", "unknown")
            if reason_code == "insufficient_balance":
                bal = db.table("users").select("credits").eq("id", user_id).maybe_single().execute()
                current = (bal.data or {}).get("credits", 0) if bal else 0
                raise AppException(
                    code="INSUFFICIENT_BALANCE",
                    message=f"余额不足，当前余额 {current}，扣减后将为负",
                    status_code=422,
                )
            if reason_code == "user_not_found":
                raise AppException(code="USER_NOT_FOUND", message="用户不存在", status_code=404)
            raise AppException(
                code="ADMIN_ADJUST_FAILED",
                message=f"积分调整失败：{reason_code}",
                status_code=500,
            )
        new_balance = data.get("new_balance", 0)
        logger.info(
            "管理员积分调整成功",
            user_id=user_id, delta=delta, new_balance=new_balance,
            operator_id=operator_id, reason=reason,
        )
        return new_balance
    except AppException:
        raise
    except Exception as e:
        logger.error(
            "管理员积分调整异常",
            user_id=user_id, delta=delta, operator_id=operator_id, error=str(e),
        )
        raise AppException(code="ADMIN_ADJUST_FAILED", message="积分调整失败", status_code=500)
