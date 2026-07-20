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
    storage_owner_id: str,
    org_id: str | None,
) -> StagedAttachment:
    message_id = str(uuid.uuid5(_ATTACHMENT_NAMESPACE, f"{msgid}:message"))
    identity = file_payload.get("asset_identity")
    if not isinstance(identity, dict):
        raise RuntimeError("WECOM_ATTACHMENT_IDENTITY_MISSING")
    file_part = {
        key: value
        for key, value in file_payload.items()
        if key != "asset_identity"
    }
    content = [{"type": "file", **file_part}]
    response = db.rpc("stage_wecom_attachment_v2", {
        "p_conversation_id": conversation_id,
        "p_source_message_id": message_id,
        "p_source_provider_id": msgid,
        "p_sender_user_id": sender_user_id,
        "p_sender_channel_identity": sender_identity,
        "p_content": Jsonb(content),
        "p_original_name": file_part["name"],
        "p_url": file_part["url"],
        "p_workspace_path": file_part["workspace_path"],
        "p_storage_scope": storage_scope,
        "p_mime_type": file_part["mime_type"],
        "p_size": file_part["size"],
        "p_asset_identity": Jsonb(identity),
    }).execute()
    result = response.data if response else None
    if not isinstance(result, dict) or not result.get("attachment_id"):
        raise RuntimeError("WECOM_ATTACHMENT_STAGE_INVALID")
    staged = StagedAttachment(
        attachment_id=str(result["attachment_id"]),
        message_id=str(result.get("message_id") or message_id),
        already_staged=bool(result.get("already_staged")),
    )
    from services.assets import register_wecom_attachment_best_effort

    register_wecom_attachment_best_effort(
        db,
        attachment_id=staged.attachment_id,
        message_id=staged.message_id,
        conversation_id=conversation_id,
        actor_user_id=sender_user_id,
        org_id=org_id,
        storage_scope=storage_scope,
        storage_owner_key=storage_owner_id,
        file_payload=file_payload,
    )
    return staged
