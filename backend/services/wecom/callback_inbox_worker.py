"""Durable consumer for per-organization WeCom application callbacks."""

from __future__ import annotations

import asyncio
import time
import xml.etree.ElementTree as ET
from typing import Any

from loguru import logger

from core.db_scope import (
    DatabaseAccessKind,
    DatabaseScope,
    ScopedDatabaseClient,
)
from schemas.wecom import (
    WecomChatType,
    WecomIncomingMessage,
    WecomMsgType,
    WecomReplyContext,
)
from services.wecom.callback_config import resolve_wecom_callback_config
from services.wecom.wecom_message_service import WecomMessageService


class WecomCallbackInboxWorker:
    def __init__(self, runtime_db: Any, worker_db: Any) -> None:
        self._runtime_db = runtime_db
        self._worker_db = worker_db
        self._control_db = ScopedDatabaseClient(
            runtime_db,
            DatabaseScope(
                actor_user_id=None,
                org_id=None,
                access_kind=DatabaseAccessKind.RUNTIME,
                request_id="wecom-callback-inbox",
            ),
        )
        self._maintenance_db = ScopedDatabaseClient(
            worker_db,
            DatabaseScope(
                actor_user_id=None,
                org_id=None,
                access_kind=DatabaseAccessKind.WORKER,
                request_id="wecom-callback-maintenance",
            ),
        )
        self._next_cleanup_at = 0.0

    async def run(self) -> None:
        """Poll until cancelled; database leases recover interrupted work."""
        retry_delay = 1.0
        while True:
            try:
                await self._cleanup_if_due()
                item = await asyncio.to_thread(self._claim)
                retry_delay = 1.0
                if not item:
                    await asyncio.sleep(1)
                    continue
                await self._process(item)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error(
                    "wecom_callback_worker_iteration_failed | "
                    f"error={type(error).__name__}"
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30.0)

    async def _cleanup_if_due(self) -> None:
        now = time.monotonic()
        if now < self._next_cleanup_at:
            return
        try:
            await asyncio.to_thread(
                lambda: self._maintenance_db.rpc(
                    "cleanup_wecom_callback_inbox",
                    {"p_retention_days": 30},
                ).execute(),
            )
            self._next_cleanup_at = now + 3600
        except Exception as error:
            logger.warning(
                "wecom_callback_cleanup_failed | "
                f"error={type(error).__name__}"
            )
            self._next_cleanup_at = now + 60

    def _claim(self) -> dict[str, Any] | None:
        response = self._control_db.rpc(
            "claim_wecom_callback", {"p_lease_seconds": 120},
        ).execute()
        return response.data if isinstance(response.data, dict) else None

    async def _process(self, item: dict[str, Any]) -> None:
        try:
            config = await asyncio.to_thread(
                resolve_wecom_callback_config,
                self._worker_db,
                str(item["org_id"]),
            )
            message = _parse_callback_message(
                item["payload"]["xml_content"],
                org_id=str(item["org_id"]),
                corp_id=config.corp_id,
            )
            if message is not None:
                reply = WecomReplyContext(
                    channel="app",
                    wecom_userid=message.wecom_userid,
                    org_id=str(item["org_id"]),
                    agent_id=config.agent_id,
                    corp_id=config.corp_id,
                    agent_secret=config.agent_secret,
                )
                handled = await WecomMessageService(
                    self._runtime_db,
                ).handle_message(
                    message, reply,
                )
                if not handled:
                    raise RuntimeError("WECOM_CALLBACK_MESSAGE_FAILED")
            await asyncio.to_thread(self._complete, item)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "wecom_callback_processing_failed | "
                f"inbox_id={item.get('id')} | org_id={item.get('org_id')} | "
                f"error={type(error).__name__}"
            )
            await asyncio.to_thread(self._fail, item, error)

    def _complete(self, item: dict[str, Any]) -> None:
        self._control_db.rpc("complete_wecom_callback", {
            "p_id": item["id"],
            "p_lease_token": item["lease_token"],
        }).execute()

    def _fail(self, item: dict[str, Any], error: Exception) -> None:
        self._control_db.rpc("fail_wecom_callback", {
            "p_id": item["id"],
            "p_lease_token": item["lease_token"],
            "p_error": f"{type(error).__name__}: {error}",
        }).execute()


def _parse_callback_message(
    xml_content: str,
    *,
    org_id: str,
    corp_id: str,
) -> WecomIncomingMessage | None:
    root = ET.fromstring(xml_content)
    msg_type = _xml_text(root, "MsgType")
    if msg_type == "event":
        return None
    from_user = _xml_text(root, "FromUserName") or ""
    return WecomIncomingMessage(
        msgid=_xml_text(root, "MsgId") or _xml_text(root, "NewMsgId") or "",
        wecom_userid=from_user,
        corp_id=corp_id,
        chatid=from_user,
        chattype=WecomChatType.SINGLE,
        msgtype=msg_type or WecomMsgType.TEXT,
        channel="app",
        org_id=org_id,
        text_content=_xml_text(root, "Content"),
    )


def _xml_text(root: ET.Element, tag: str) -> str | None:
    node = root.find(tag)
    return node.text if node is not None else None
