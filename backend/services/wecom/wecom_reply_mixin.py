"""企业微信结果格式化、通道回复与 Web 更新通知。"""

from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger

from schemas.wecom import WecomReplyContext
from services.websocket_manager import ws_manager


class WecomReplyMixin:
    async def _push_stream_chunk(
        self,
        reply_ctx: WecomReplyContext,
        stream_id: str,
        content: str,
        finish: bool,
        feedback_id: Optional[str] = None,
    ) -> None:
        if reply_ctx.channel == "smart_robot" and reply_ctx.ws_client:
            await reply_ctx.ws_client.send_stream_chunk(
                req_id=reply_ctx.req_id,
                stream_id=stream_id,
                content=content,
                finish=finish,
                feedback_id=feedback_id,
            )
        elif reply_ctx.channel == "app" and finish:
            await self._send_app_message(reply_ctx, content)

    async def _reply_text(
        self, reply_ctx: WecomReplyContext, text: str,
    ) -> None:
        if reply_ctx.channel == "smart_robot" and reply_ctx.ws_client:
            if reply_ctx.active_stream_id:
                await reply_ctx.ws_client.send_stream_chunk(
                    req_id=reply_ctx.req_id,
                    stream_id=reply_ctx.active_stream_id,
                    content=text,
                    finish=True,
                )
                reply_ctx.active_stream_id = None
            else:
                await reply_ctx.ws_client.send_reply(
                    req_id=reply_ctx.req_id,
                    msgtype="text",
                    content={"content": text},
                )
        elif reply_ctx.channel == "app":
            await self._send_app_message(reply_ctx, text)

    async def _reply_credits_insufficient(
        self,
        reply_ctx: WecomReplyContext,
        needed: int,
        balance: int,
        action: str,
    ) -> None:
        if reply_ctx.channel == "smart_robot" and reply_ctx.ws_client:
            from services.wecom.card_builder import WecomCardBuilder
            card = WecomCardBuilder.credits_insufficient_card(
                needed, balance, action,
            )
            await reply_ctx.ws_client.send_template_card(reply_ctx.req_id, card)
        else:
            await self._reply_text(
                reply_ctx,
                f"积分不足，生成{action}需要 {needed} 积分，当前余额 {balance}。",
            )

    async def _send_app_message(
        self, reply_ctx: WecomReplyContext, text: str,
    ) -> None:
        from services.wecom.app_message_sender import (
            OrgWecomCreds,
            send_markdown,
            send_text,
        )
        from services.wecom.markdown_adapter import adapt_for_app, split_long_message

        adapted, msgtype = adapt_for_app(text)
        chunks = split_long_message(adapted, max_bytes=2000)
        creds = OrgWecomCreds(
            org_id=reply_ctx.org_id or "",
            corp_id=reply_ctx.corp_id or "",
            agent_id=reply_ctx.agent_id or 0,
            agent_secret=reply_ctx.agent_secret or "",
        )
        for index, chunk in enumerate(chunks):
            sent = False
            if msgtype == "markdown":
                sent = await send_markdown(
                    wecom_userid=reply_ctx.wecom_userid,
                    content=chunk,
                    creds=creds,
                )
            if not sent:
                await send_text(
                    wecom_userid=reply_ctx.wecom_userid,
                    content=chunk,
                    creds=creds,
                )
            if index < len(chunks) - 1:
                await asyncio.sleep(0.3)

    @staticmethod
    async def _notify_web_conversation_updated(
        user_id: str,
        conversation_id: str,
        org_id: str | None = None,
    ) -> None:
        try:
            await ws_manager.send_to_user(user_id, {
                "type": "conversation_updated",
                "conversation_id": conversation_id,
            }, org_id=org_id)
        except Exception as error:
            logger.warning(
                "WS notify conversation_updated failed | "
                f"user_id={user_id} | error={error}"
            )
