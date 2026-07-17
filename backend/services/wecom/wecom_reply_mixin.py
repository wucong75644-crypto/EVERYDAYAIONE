"""企业微信结果格式化、通道回复与 Web 更新通知。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional, TYPE_CHECKING

from loguru import logger

from schemas.message import TextPart
from schemas.wecom import WecomReplyContext
from services.websocket_manager import ws_manager

if TYPE_CHECKING:
    from services.handlers.chat_generate_mixin import GenerateResult


class WecomReplyMixin:
    async def _dispatch_result_to_wecom(
        self,
        gen_result: "GenerateResult",
        reply_ctx: WecomReplyContext,
        message_id: str,
        org_id: Optional[str] = None,
    ) -> None:
        from schemas.message import ImagePart, VideoPart
        from services.wecom.markdown_adapter import clean_for_stream

        result_parts = gen_result.parts
        text_parts = [
            part.text for part in result_parts
            if isinstance(part, TextPart) and part.text
        ]
        media_urls = {
            "image": [
                part.url for part in result_parts
                if isinstance(part, ImagePart) and part.url
            ],
            "video": [
                part.url for part in result_parts
                if isinstance(part, VideoPart) and part.url
            ],
        }
        if text_parts:
            display = clean_for_stream("\n".join(text_parts))
            if reply_ctx.active_stream_id:
                await self._push_stream_chunk(
                    reply_ctx, reply_ctx.active_stream_id,
                    display, finish=True, feedback_id=message_id,
                )
                reply_ctx.active_stream_id = None
            else:
                await self._reply_text(reply_ctx, display)
        for media_type in ("image", "video"):
            if media_urls[media_type]:
                await self._send_media_to_wecom(
                    reply_ctx, media_urls[media_type], media_type, message_id=None,
                )
        if not text_parts and not any(media_urls.values()):
            await self._reply_text(reply_ctx, "抱歉，AI 没有生成回复内容。")
            return
        self.db.table("messages").update({
            "content": _build_content_dicts(gen_result),
            "status": "completed",
            "generation_params": _build_generation_params(gen_result),
        }).eq("id", message_id).execute()

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


def _build_content_dicts(gen_result: "GenerateResult") -> list[dict[str, Any]]:
    from schemas.message import ImagePart, VideoPart

    parts: list[dict[str, Any]] = []
    for block in gen_result.content_blocks or []:
        if block.get("type") in {"tool_step", "text"}:
            parts.append(block)
    if not parts:
        parts.extend(
            part.model_dump(exclude_none=True) for part in gen_result.parts
        )
        return parts
    for part in gen_result.parts:
        if isinstance(part, (ImagePart, VideoPart)) and part.url:
            parts.append({"type": part.type, "url": part.url})
    return parts


def _build_generation_params(gen_result: "GenerateResult") -> dict[str, Any]:
    params: dict[str, Any] = {"type": "chat"}
    if not gen_result.tool_digest:
        return params
    params["tool_digest"] = dict(gen_result.tool_digest)
    size = len(json.dumps(params, sort_keys=True, ensure_ascii=False).encode())
    if size > 8192:
        params["tool_digest"].pop("tools", None)
        size = len(json.dumps(params, sort_keys=True, ensure_ascii=False).encode())
    if size > 8192:
        logger.warning(f"Wecom generation_params truncated | original={size}B")
        return {"type": "chat"}
    return params
