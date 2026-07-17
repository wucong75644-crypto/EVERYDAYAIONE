"""企业微信外部会话到内部 conversation 的数据库事实源。"""

from __future__ import annotations

from typing import Any


async def resolve_channel_conversation(
    db: Any,
    *,
    user_id: str,
    corp_id: str,
    external_chat_id: str,
    chat_type: str,
) -> str:
    response = db.rpc("resolve_wecom_conversation", {
        "p_user_id": user_id,
        "p_corp_id": corp_id,
        "p_external_chat_id": external_chat_id,
        "p_chat_type": chat_type,
    }).execute()
    result = response.data if response else None
    if not isinstance(result, dict) or not result.get("conversation_id"):
        raise RuntimeError("WECOM_CONVERSATION_RESOLVE_INVALID")
    return str(result["conversation_id"])
