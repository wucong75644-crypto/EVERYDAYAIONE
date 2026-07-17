"""企业微信对话设置的数据库事实源。"""

from __future__ import annotations

from typing import Any


def get_wecom_conversation_setting(
    db: Any,
    conversation_id: str,
    user_id: str,
    key: str,
    org_id: str | None = None,
) -> str | None:
    """读取模型或思考模式；租户范围由 OrgScopedDB 追加。"""
    if key not in {"model", "thinking_mode"}:
        raise ValueError("unsupported wecom conversation setting")
    query = (
        db.table("conversations")
        .select("model_id,chat_settings")
        .eq("id", conversation_id)
        .eq("user_id", user_id)
        .eq("source", "wecom")
    )
    query = query.eq("org_id", org_id) if org_id else query.is_("org_id", "null")
    response = query.maybe_single().execute()
    row = response.data if response else None
    if not row:
        return None
    if key == "model":
        value = row.get("model_id")
    else:
        settings = row.get("chat_settings")
        value = settings.get("thinking_mode") if isinstance(settings, dict) else None
    return str(value) if value else None


def set_wecom_conversation_setting(
    db: Any,
    conversation_id: str,
    user_id: str,
    key: str,
    value: str,
    org_id: str | None = None,
) -> dict[str, Any]:
    """通过数据库行锁 RPC 原子更新设置，避免 JSONB 并发覆盖。"""
    response = db.rpc(
        "update_wecom_conversation_setting",
        {
            "p_conversation_id": conversation_id,
            "p_user_id": user_id,
            "p_setting_key": key,
            "p_setting_value": value,
            "p_org_id": org_id,
        },
    ).execute()
    result = response.data if response else None
    if not isinstance(result, dict):
        raise RuntimeError("WECOM_SETTING_UPDATE_RESULT_INVALID")
    return result
