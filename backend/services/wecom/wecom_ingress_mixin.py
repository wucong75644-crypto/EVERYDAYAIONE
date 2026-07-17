"""企业微信 AI 入站的 Actor 灰度与旧链路分发。"""

from __future__ import annotations

import time
import uuid
from typing import List

from schemas.message import GenerationType
from schemas.wecom import WecomIncomingMessage, WecomMsgType, WecomReplyContext


class WecomIngressMixin:
    async def _process_incoming_content(
        self,
        msg: WecomIncomingMessage,
        reply_ctx: WecomReplyContext,
        user_id: str,
        conversation_id: str,
        image_urls: List[str],
    ) -> None:
        if msg.msgtype == WecomMsgType.FILE:
            await self._stage_file_message(
                msg, reply_ctx, user_id, conversation_id,
            )
            return
        if msg.msgtype in _ACTOR_MESSAGE_TYPES:
            await self._enqueue_actor_message(
                msg, reply_ctx, user_id, conversation_id, image_urls,
            )
            return

        await self._reply_text(
            reply_ctx, "暂时不支持这种消息类型，发文字、图片或文件给我试试~",
        )

    async def _stage_file_message(
        self,
        msg: WecomIncomingMessage,
        reply_ctx: WecomReplyContext,
        user_id: str,
        conversation_id: str,
    ) -> None:
        if reply_ctx.channel == "smart_robot" and reply_ctx.ws_client:
            stream_id = str(uuid.uuid4())
            reply_ctx.active_stream_id = stream_id
            await self._push_stream_chunk(
                reply_ctx, stream_id, "正在接收文件…", finish=False,
            )
        file_payload = await self._prepare_wecom_file(
            msg, reply_ctx, user_id, msg.org_id,
        )
        if file_payload is None:
            return
        from services.wecom.attachment_service import stage_wecom_attachment

        stage_wecom_attachment(
            self.db,
            msgid=msg.msgid,
            conversation_id=conversation_id,
            sender_user_id=user_id,
            sender_identity=msg.wecom_userid,
            file_payload=file_payload,
            storage_scope=(
                "channel" if msg.chattype == "group" else "user"
            ),
        )
        await self._notify_web_conversation_updated(
            user_id, conversation_id, org_id=msg.org_id,
        )
        await self._reply_text(
            reply_ctx, "文件已收到，请告诉我需要如何处理。",
        )

    async def _enqueue_actor_message(
        self,
        msg: WecomIncomingMessage,
        reply_ctx: WecomReplyContext,
        user_id: str,
        conversation_id: str,
        image_urls: List[str],
    ) -> None:
        if self._get_user_balance(user_id) <= 0:
            await self._reply_credits_insufficient(
                reply_ctx, needed=1, balance=0, action="回复",
            )
            return
        if reply_ctx.channel == "smart_robot" and reply_ctx.ws_client:
            stream_id = str(uuid.uuid4())
            reply_ctx.active_stream_id = stream_id
            await self._push_stream_chunk(
                reply_ctx, stream_id, "🤔 思考中…", finish=False,
            )
            from services.wecom.stream_keepalive import StreamKeepAlive
            keepalive = StreamKeepAlive(reply_ctx, self._push_stream_chunk)
            await keepalive.start()
            stream_context = {
                "req_id": reply_ctx.req_id,
                "stream_id": stream_id,
                "started_at": time.time(),
            }
        else:
            keepalive = None
            stream_context = None
        from services.handlers import get_handler
        from services.wecom.actor_enqueue import (
            enqueue_wecom_message,
            stable_wecom_task_id,
        )
        from services.wecom.stream_keepalive import (
            register_stream_keepalive,
            stop_stream_keepalive,
        )

        handler = get_handler(
            GenerationType.CHAT,
            self.db,
            org_id=msg.org_id,
            user_id=user_id,
            request_id=f"wecom_{msg.msgid}",
        )
        task_id = stable_wecom_task_id(msg, user_id)
        keepalive_registered = bool(
            keepalive and register_stream_keepalive(task_id, keepalive)
        )
        if keepalive and not keepalive_registered:
            await keepalive.stop()
        try:
            result = await enqueue_wecom_message(
                handler=handler,
                msg=msg,
                user_id=user_id,
                conversation_id=conversation_id,
                image_urls=image_urls,
                file_payload=None,
                stream_context=stream_context,
            )
        except Exception:
            if keepalive_registered:
                await stop_stream_keepalive(task_id)
            raise
        await self._notify_web_conversation_updated(
            user_id, conversation_id, org_id=msg.org_id,
        )
        if keepalive_registered and not result.already_enqueued:
            return
        if keepalive_registered:
            await stop_stream_keepalive(task_id)
        acknowledgement = (
            "该消息已经收到，正在处理中。"
            if result.already_enqueued
            else "已收到，正在处理中。"
        )
        await self._reply_text(reply_ctx, acknowledgement)

_ACTOR_MESSAGE_TYPES = {
    WecomMsgType.TEXT,
    WecomMsgType.VOICE,
    WecomMsgType.IMAGE,
    WecomMsgType.MIXED,
}
