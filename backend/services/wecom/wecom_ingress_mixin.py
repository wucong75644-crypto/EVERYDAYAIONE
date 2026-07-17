"""企业微信 AI 入站的 Actor 灰度与旧链路分发。"""

from __future__ import annotations

import uuid
from typing import List, Optional

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
        if (
            msg.msgtype == WecomMsgType.FILE
            or (
                self.settings.conversation_actor_wecom_enabled
                and msg.msgtype in _ACTOR_MESSAGE_TYPES
            )
        ):
            await self._enqueue_actor_message(
                msg, reply_ctx, user_id, conversation_id, image_urls,
            )
            return

        turn_id = str(uuid.uuid4())
        input_message_id: Optional[str] = None
        if msg.msgtype != WecomMsgType.FILE:
            input_message_id = await self._save_user_message(
                conversation_id=conversation_id,
                user_id=user_id,
                text_content=msg.text_content or "",
                image_urls=image_urls,
                turn_id=turn_id,
            )
        await self._notify_web_conversation_updated(
            user_id, conversation_id, org_id=msg.org_id,
        )
        output_message_id = await self._create_assistant_placeholder(
            conversation_id=conversation_id,
            input_message_id=input_message_id,
            turn_id=turn_id,
        )
        await self._dispatch_legacy_message(
            msg=msg,
            reply_ctx=reply_ctx,
            user_id=user_id,
            conversation_id=conversation_id,
            input_message_id=input_message_id,
            output_message_id=output_message_id,
            turn_id=turn_id,
            image_urls=image_urls,
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
        from services.handlers import get_handler
        from services.wecom.actor_enqueue import enqueue_wecom_message

        file_payload = None
        if msg.msgtype == WecomMsgType.FILE:
            file_payload = await self._prepare_actor_file(
                msg, reply_ctx, user_id, msg.org_id,
            )
            if file_payload is None:
                return
        handler = get_handler(
            GenerationType.CHAT,
            self.db,
            org_id=msg.org_id,
            user_id=user_id,
            request_id=f"wecom_{msg.msgid}",
        )
        result = await enqueue_wecom_message(
            handler=handler,
            msg=msg,
            user_id=user_id,
            conversation_id=conversation_id,
            image_urls=image_urls,
            file_payload=file_payload,
        )
        await self._notify_web_conversation_updated(
            user_id, conversation_id, org_id=msg.org_id,
        )
        if not result.already_enqueued:
            await self._reply_text(reply_ctx, "已收到，正在处理中。")

    async def _dispatch_legacy_message(
        self,
        *,
        msg: WecomIncomingMessage,
        reply_ctx: WecomReplyContext,
        user_id: str,
        conversation_id: str,
        input_message_id: Optional[str],
        output_message_id: str,
        turn_id: str,
        image_urls: List[str],
    ) -> None:
        if msg.msgtype in _ACTOR_MESSAGE_TYPES:
            await self._handle_text(
                user_id=user_id,
                conversation_id=conversation_id,
                message_id=output_message_id,
                text_content=msg.text_content or "",
                reply_ctx=reply_ctx,
                image_urls=image_urls,
                org_id=msg.org_id,
                input_message_id=input_message_id,
                turn_id=turn_id,
            )
        elif msg.msgtype == WecomMsgType.VIDEO:
            await self._reply_text(
                reply_ctx,
                "收到你的视频，目前暂不支持视频内容分析，发文字或图片给我试试~",
            )
        else:
            await self._reply_text(
                reply_ctx, "暂时不支持这种消息类型，发文字或图片给我试试~",
            )


_ACTOR_MESSAGE_TYPES = {
    WecomMsgType.TEXT,
    WecomMsgType.VOICE,
    WecomMsgType.IMAGE,
    WecomMsgType.MIXED,
}
