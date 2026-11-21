"""Best-effort publication of an already persisted chat media placeholder."""

import asyncio
import json

from loguru import logger


async def send_pending_media(db, *, task_id, message_id, user_id, org_id,
                             conversation_id, submission_state):
    from schemas.websocket import build_media_pending
    from services.websocket_manager import ws_manager

    try:
        message = await asyncio.to_thread(
            lambda: db.table("messages").select("*").eq("id", message_id).single().execute().data
        )
        if isinstance(message.get("content"), str):
            message["content"] = json.loads(message["content"])
        await ws_manager.send_to_task_or_user(
            task_id, user_id, build_media_pending(task_id, conversation_id, message, submission_state),
            org_id=org_id,
        )
    except Exception as error:
        logger.warning("Chat media accepted; pending snapshot delivery deferred | task={} | error_type={}",
                       task_id, type(error).__name__)
