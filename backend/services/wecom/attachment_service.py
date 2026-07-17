"""企业微信附件的持久化暂存入口。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb


_ATTACHMENT_NAMESPACE = uuid.UUID("73463419-7280-45a4-80a4-7981d5180410")


@dataclass(frozen=True)
class StagedAttachment:
    attachment_id: str
    message_id: str
    already_staged: bool


def stage_wecom_attachment(
    db: Any,
    *,
    msgid: str,
    conversation_id: str,
    sender_user_id: str,
    sender_identity: str,
    file_payload: dict[str, Any],
    storage_scope: str,
) -> StagedAttachment:
    message_id = str(uuid.uuid5(_ATTACHMENT_NAMESPACE, f"{msgid}:message"))
    content = [{"type": "file", **file_payload}]
    response = db.rpc("stage_wecom_attachment", {
        "p_conversation_id": conversation_id,
        "p_source_message_id": message_id,
        "p_source_provider_id": msgid,
        "p_sender_user_id": sender_user_id,
        "p_sender_channel_identity": sender_identity,
        "p_content": Jsonb(content),
        "p_original_name": file_payload["name"],
        "p_url": file_payload["url"],
        "p_workspace_path": file_payload["workspace_path"],
        "p_storage_scope": storage_scope,
        "p_mime_type": file_payload["mime_type"],
        "p_size": file_payload["size"],
    }).execute()
    result = response.data if response else None
    if not isinstance(result, dict) or not result.get("attachment_id"):
        raise RuntimeError("WECOM_ATTACHMENT_STAGE_INVALID")
    return StagedAttachment(
        attachment_id=str(result["attachment_id"]),
        message_id=str(result.get("message_id") or message_id),
        already_staged=bool(result.get("already_staged")),
    )
