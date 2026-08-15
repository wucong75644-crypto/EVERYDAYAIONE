"""企业微信 WebSocket 出站消息能力。"""

from __future__ import annotations

import json
import time
import uuid
from typing import Dict, Optional

from loguru import logger

from schemas.wecom import WecomCommand


def _request_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


class WecomOutboundMixin:
    async def send_reply(self, req_id: str, msgtype: str, content: dict) -> None:
        await self._safe_send({
            "cmd": WecomCommand.RESPOND_MSG,
            "headers": {"req_id": req_id},
            "body": {"msgtype": msgtype, msgtype: content},
        })

    async def send_stream_chunk(
        self,
        req_id: str,
        stream_id: str,
        content: str,
        finish: bool = False,
        feedback_id: Optional[str] = None,
        msg_items: Optional[list] = None,
    ) -> None:
        stream: Dict[str, Any] = {
            "id": stream_id, "finish": finish, "content": content,
        }
        if feedback_id:
            stream["feedback"] = {"id": feedback_id}
        if msg_items and finish:
            stream["msg_item"] = msg_items
        await self._safe_send({
            "cmd": WecomCommand.RESPOND_MSG,
            "headers": {"req_id": req_id},
            "body": {"msgtype": "stream", "stream": stream},
        })

    async def send_template_card(self, req_id: str, card: dict) -> None:
        await self._safe_send({
            "cmd": WecomCommand.RESPOND_MSG,
            "headers": {"req_id": req_id},
            "body": {"msgtype": "template_card", "template_card": card},
        })

    async def send_update_card(self, req_id: str, card: dict) -> None:
        await self._safe_send({
            "cmd": WecomCommand.RESPOND_UPDATE,
            "headers": {"req_id": req_id},
            "body": {
                "response_type": "update_template_card",
                "template_card": card,
            },
        })

    async def send_msg(
        self,
        chatid: str,
        msgtype: str,
        content: dict,
        chattype: str = "single",
    ) -> bool:
        if not self.is_connected:
            return False
        req_id = _request_id("send_msg")
        await self._safe_send({
            "cmd": WecomCommand.SEND_MSG,
            "headers": {"req_id": req_id},
            "body": {
                "chatid": chatid, "chattype": chattype,
                "msgtype": msgtype, msgtype: content,
            },
        })
        return self.is_connected

    async def send_proactive(
        self, chatid: str, msgtype: str, content: dict,
    ) -> bool:
        if not self.is_connected:
            return False
        await self._safe_send({
            "cmd": WecomCommand.SEND_MSG,
            "headers": {"req_id": _request_id("scheduled")},
            "body": {"chatid": chatid, "msgtype": msgtype, msgtype: content},
        })
        return self.is_connected

    async def _safe_send(self, msg: dict) -> None:
        try:
            if self._ws and self.is_connected:
                await self._ws.send(json.dumps(msg))
        except Exception as error:
            logger.warning(f"Wecom WS send failed: {error}")
            await self._force_close()
