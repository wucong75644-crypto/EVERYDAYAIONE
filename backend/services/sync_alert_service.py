"""Persist and fan out Sync alerts through one narrow database capability."""

from __future__ import annotations

from typing import Any

from loguru import logger


async def send_org_alert(db: Any, org_id: str, text: str) -> bool:
    response = await db.rpc(
        "service_create_org_alert",
        {"p_org_id": org_id, "p_text": text},
    ).execute()
    return await _fanout(org_id, text, response.data or [])


async def send_platform_alert(db: Any, text: str) -> bool:
    response = await db.rpc(
        "service_create_platform_alert",
        {"p_text": text},
    ).execute()
    rows = response.data or []
    org_id = ""
    if rows and isinstance(rows[0], dict):
        org_id = str(rows[0].get("target_org_id") or "")
    if not org_id:
        logger.info("sync platform alert has no eligible recipient")
        return False
    return await _fanout(org_id, text, rows)


async def _fanout(org_id: str, text: str, rows: list[object]) -> bool:
    from services.scheduler.push_dispatcher import push_dispatcher
    from services.websocket_manager import ws_manager
    from services.wecom.markdown_adapter import clean_for_stream

    pushed = False
    cleaned = clean_for_stream(text)
    for row in rows:
        if not isinstance(row, dict):
            continue
        user_id = str(row.get("user_id") or "")
        conversation_id = str(row.get("conversation_id") or "")
        wecom_userid = str(row.get("wecom_userid") or "")
        if user_id and conversation_id:
            try:
                await ws_manager.send_to_user(
                    user_id,
                    {
                        "type": "conversation_updated",
                        "conversation_id": conversation_id,
                    },
                    org_id=org_id,
                )
                pushed = True
            except Exception as error:
                logger.warning(
                    "sync alert web fanout failed | "
                    f"org_id={org_id} user_id={user_id} error={error}"
                )
        if wecom_userid:
            status = await push_dispatcher.dispatch(
                org_id=org_id,
                target={
                    "type": "wecom_user",
                    "wecom_userid": wecom_userid,
                },
                text=cleaned,
                files=[],
            )
            pushed = pushed or status == "pushed"
    return pushed
