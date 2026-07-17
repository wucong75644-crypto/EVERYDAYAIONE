"""企业微信 WebSocket 出站消息与素材上传能力。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
import uuid
from typing import Any, Dict, Optional

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

    async def upload_media(
        self, data: bytes, *, media_type: str, filename: str,
    ) -> str:
        chunks = [
            data[index:index + 512 * 1024]
            for index in range(0, len(data), 512 * 1024)
        ]
        if not data or len(chunks) > 100:
            raise ValueError("WECOM_MEDIA_SIZE_INVALID")
        initialized = await self._request(
            WecomCommand.UPLOAD_MEDIA_INIT,
            {
                "type": media_type, "filename": filename,
                "total_size": len(data), "total_chunks": len(chunks),
                "md5": hashlib.md5(data, usedforsecurity=False).hexdigest(),
            },
        )
        upload_id = initialized.get("body", {}).get("upload_id")
        if not upload_id:
            raise RuntimeError("WECOM_MEDIA_UPLOAD_INIT_INVALID")
        for index, chunk in enumerate(chunks):
            await self._request(
                WecomCommand.UPLOAD_MEDIA_CHUNK,
                {
                    "upload_id": upload_id, "chunk_index": index,
                    "base64_data": base64.b64encode(chunk).decode("ascii"),
                },
            )
        finished = await self._request(
            WecomCommand.UPLOAD_MEDIA_FINISH, {"upload_id": upload_id},
        )
        media_id = finished.get("body", {}).get("media_id")
        if not media_id:
            raise RuntimeError("WECOM_MEDIA_UPLOAD_FINISH_INVALID")
        return str(media_id)

    async def send_media_message(
        self, chatid: str, media_type: str, media_id: str,
    ) -> bool:
        await self._request(
            WecomCommand.SEND_MSG,
            {
                "chatid": chatid, "msgtype": media_type,
                media_type: {"media_id": media_id},
            },
        )
        return self.is_connected

    async def _safe_send(self, msg: dict) -> None:
        try:
            if self._ws and self.is_connected:
                await self._ws.send(json.dumps(msg))
        except Exception as error:
            logger.warning(f"Wecom WS send failed: {error}")
            await self._force_close()

    async def _request(
        self, cmd: str, body: dict, *, timeout: float = 15,
    ) -> dict:
        if not self._ws or not self.is_connected:
            raise ConnectionError("WECOM_WS_NOT_CONNECTED")
        req_id = _request_id(cmd)
        future = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = future
        try:
            await self._ws.send(json.dumps({
                "cmd": cmd,
                "headers": {"req_id": req_id},
                "body": body,
            }))
            response = await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending_requests.pop(req_id, None)
        errcode = response.get("errcode")
        if errcode is None:
            errcode = response.get("body", {}).get("errcode", 0)
        if errcode != 0:
            raise RuntimeError(f"WECOM_REQUEST_FAILED:{cmd}:{errcode}")
        return response
