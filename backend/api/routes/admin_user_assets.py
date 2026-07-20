"""管理员统一用户资产列表 API。"""
from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query

from api.deps import CurrentUserId, Database

from .admin_users_helpers import _require_super_admin


assets_router = APIRouter()

@assets_router.get(
    "/users/{uid}/assets",
    summary="统一用户资产列表（超管）",
)
async def list_user_assets(
    uid: str,
    user_id: CurrentUserId,
    db: Database,
    source_type: Literal["upload", "generated"] = Query(...),
    media_type: Optional[Literal["image", "video", "file"]] = Query(None),
    limit: int = Query(24, ge=1, le=100),
    cursor: Optional[str] = Query(None, max_length=1024),
) -> dict[str, Any]:
    """按稳定复合游标读取 ready 资产，不扫描消息 JSONB。"""
    _require_super_admin(user_id, db)
    user = (
        db.table("users").select("id").eq("id", uid)
        .maybe_single().execute()
    )
    if not user or not user.data:
        raise HTTPException(status_code=404, detail="用户不存在")

    cursor_value = _decode_cursor(cursor) if cursor else None
    created_at, asset_id = cursor_value or (None, None)
    result = db.rpc(
        "list_admin_user_assets",
        {
            "p_actor_user_id": uid,
            "p_source_type": source_type,
            "p_media_type": media_type,
            "p_limit": limit + 1,
            "p_cursor_created_at": created_at,
            "p_cursor_id": asset_id,
        },
    ).execute()
    payload = result.data
    if isinstance(payload, list) and len(payload) == 1:
        payload = payload[0]
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="资产查询结果无效")
    rows = payload.get("items")
    total = payload.get("total")
    if (
        not isinstance(rows, list)
        or not isinstance(total, int)
        or isinstance(total, bool)
        or total < 0
    ):
        raise HTTPException(status_code=500, detail="资产查询结果无效")

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = (
        _encode_cursor(items[-1]["created_at"], items[-1]["id"])
        if has_more and items else None
    )
    return {
        "items": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "total": total,
    }
def _encode_cursor(created_at: str, asset_id: str) -> str:
    payload = json.dumps(
        {"created_at": created_at, "id": asset_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + padding)
        payload = json.loads(raw.decode("utf-8"))
        created_at = str(payload["created_at"])
        asset_id = str(uuid.UUID(str(payload["id"])))
        parsed_at = datetime.fromisoformat(
            created_at.replace("Z", "+00:00"),
        )
        if parsed_at.utcoffset() is None:
            raise ValueError("cursor timestamp must include timezone")
        return created_at, asset_id
    except (
        binascii.Error, KeyError, TypeError, UnicodeDecodeError, ValueError,
    ) as error:
        raise HTTPException(
            status_code=422, detail="资产游标无效",
        ) from error
