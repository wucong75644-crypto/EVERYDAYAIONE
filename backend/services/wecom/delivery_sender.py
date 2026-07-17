"""Conversation Actor 企微 Outbox 的通道发送适配器。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from loguru import logger
from services.message_utils import parse_content
from services.org.config_resolver import AsyncOrgConfigResolver
from services.wecom.markdown_adapter import (
    adapt_for_app,
    clean_for_stream,
    split_long_message,
)
from services.wecom.stream_keepalive import (
    KEEPALIVE_TIMEOUT,
    stop_stream_keepalive,
)


@dataclass(frozen=True)
class WecomDeliveryItem:
    key: str
    kind: str
    content: str


class WecomDeliverySender:
    """把持久消息展开为稳定分项，并按智能机器人/自建应用发送。"""

    def __init__(
        self,
        db: Any,
        ws_client_getter: Callable[[str | None], Any],
    ) -> None:
        self._resolver = AsyncOrgConfigResolver(db)
        self._ws_client_getter = ws_client_getter

    def build_items(
        self,
        task: Mapping[str, Any],
        message: Mapping[str, Any] | None,
        context: Mapping[str, Any],
    ) -> list[WecomDeliveryItem]:
        if task.get("status") == "failed":
            text = str(task.get("error_message") or "生成失败，请稍后重试。")
            key = "stream:text" if context.get("stream_id") else "error:0"
            return self._text_items(key, text, context)

        items: list[WecomDeliveryItem] = []
        chart_skipped = False
        parts = parse_content((message or {}).get("content"))
        stream_text = "\n\n".join(
            str(part["text"])
            for part in parts
            if part.get("type") == "text" and part.get("text")
        )
        stream_text_added = False
        for index, part in enumerate(parts):
            kind = part.get("type")
            if kind == "chart":
                chart_skipped = True
                logger.info(
                    "wecom_chart_skipped | "
                    f"task_id={task.get('id')} | content_index={index} | "
                    f"spec_format={part.get('spec_format', 'echarts')}"
                )
                continue
            value = part.get("text") if kind == "text" else part.get("url")
            if kind not in {"text", "image", "video"} or not value:
                continue
            if kind == "text":
                if context.get("stream_id"):
                    if stream_text_added:
                        continue
                    items.append(
                        WecomDeliveryItem("stream:text", "text", stream_text)
                    )
                    stream_text_added = True
                    continue
                items.extend(
                    self._text_items(f"text:{index}", str(value), context)
                )
            else:
                items.append(WecomDeliveryItem(f"{kind}:{index}", kind, str(value)))
        if context.get("stream_id") and not stream_text_added and (
            items or chart_skipped
        ):
            items.insert(
                0,
                WecomDeliveryItem("stream:text", "text", "分析已完成。"),
            )
        if not items and not chart_skipped:
            items.extend(
                self._text_items(
                    "empty:0", "抱歉，AI 没有生成回复内容。", context,
                )
            )
        return items

    @staticmethod
    def _text_items(
        key: str,
        text: str,
        context: Mapping[str, Any],
    ) -> list[WecomDeliveryItem]:
        if context.get("transport") != "app":
            return [WecomDeliveryItem(key, "text", text)]
        adapted, msgtype = adapt_for_app(text)
        return [
            WecomDeliveryItem(f"{key}:{index}", msgtype, chunk)
            for index, chunk in enumerate(
                split_long_message(adapted, max_bytes=2000)
            )
        ]

    async def send(
        self,
        context: Mapping[str, Any],
        item: WecomDeliveryItem,
    ) -> bool:
        transport = context.get("transport")
        if transport == "smart_robot":
            return await self._send_smart_robot(context, item)
        if transport == "app":
            return await self._send_app(context, item)
        raise RuntimeError("WECOM_DELIVERY_TRANSPORT_INVALID")

    async def _send_smart_robot(
        self,
        context: Mapping[str, Any],
        item: WecomDeliveryItem,
    ) -> bool:
        client = self._ws_client_getter(_optional_str(context.get("org_id")))
        chatid = _required_str(context, "chatid")
        if not client or not client.is_connected:
            return False
        if item.kind == "text" and context.get("stream_task_id"):
            task_id = str(context["stream_task_id"])
            await stop_stream_keepalive(task_id)
            if _stream_is_current(context):
                await client.send_stream_chunk(
                    req_id=_required_str(context, "stream_req_id"),
                    stream_id=_required_str(context, "stream_id"),
                    content=clean_for_stream(item.content),
                    finish=True,
                )
                return bool(client.is_connected)
        msgtype = "markdown" if item.kind in {"text", "image"} else "text"
        content = item.content
        if item.kind == "text":
            content = clean_for_stream(content)
        elif item.kind == "image":
            content = f"![图片]({content})"
        else:
            content = f"视频已生成：{content}"
        sent = await client.send_proactive(
            chatid=chatid,
            msgtype=msgtype,
            content={"content": content},
        )
        return bool(sent and client.is_connected)

    async def _send_app(
        self,
        context: Mapping[str, Any],
        item: WecomDeliveryItem,
    ) -> bool:
        from services.wecom.app_message_sender import (
            OrgWecomCreds,
            send_image,
            send_markdown,
            send_text,
            send_video,
            upload_temp_media,
        )

        org_id = _required_str(context, "org_id")
        agent_id = await self._resolver.get(org_id, "wecom_agent_id")
        secret = await self._resolver.get(org_id, "wecom_agent_secret")
        if not agent_id or not secret:
            raise RuntimeError("WECOM_APP_CREDENTIALS_MISSING")
        creds = OrgWecomCreds(
            org_id=org_id,
            corp_id=_required_str(context, "corp_id"),
            agent_id=int(agent_id),
            agent_secret=secret,
        )
        userid = _required_str(context, "wecom_userid")
        if item.kind in {"text", "markdown"}:
            if item.kind == "markdown":
                return await send_markdown(userid, item.content, creds)
            return await send_text(userid, item.content, creds)
        media_id = await upload_temp_media(item.content, creds, item.kind)
        if not media_id:
            return await send_text(
                userid,
                f"{'图片' if item.kind == 'image' else '视频'}已生成：{item.content}",
                creds,
            )
        if item.kind == "image":
            return await send_image(userid, media_id, creds)
        return await send_video(userid, media_id, creds)


def _required_str(context: Mapping[str, Any], key: str) -> str:
    value = context.get(key)
    if not value:
        raise RuntimeError(f"WECOM_DELIVERY_CONTEXT_MISSING:{key}")
    return str(value)


def _optional_str(value: Any) -> str | None:
    return str(value) if value else None


def _stream_is_current(context: Mapping[str, Any]) -> bool:
    required = ("stream_task_id", "stream_req_id", "stream_id")
    if any(not context.get(key) for key in required):
        return False
    started_at = context.get("stream_started_at")
    return isinstance(started_at, (int, float)) and (
        0 <= time.time() - started_at < KEEPALIVE_TIMEOUT
    )
