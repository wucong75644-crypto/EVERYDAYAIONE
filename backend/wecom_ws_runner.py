"""
企业微信智能机器人 WS 长连接 — 独立进程

独立于 API 服务运行，避免多 worker 竞争同一个长连接。
由 systemd (everydayai-wecom.service) 管理生命周期。
"""

import asyncio
import signal
import sys
from pathlib import Path

# 确保 backend 目录在 sys.path 中（systemd 启动时 cwd 可能不同）
sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger

from core.config import get_settings
from core.database import get_supabase_client
from core.logging_config import setup_logging
from schemas.wecom import (
    WecomIncomingMessage,
    WecomMsgType,
    WecomReplyContext,
)
from services.wecom.wecom_message_service import WecomMessageService
from services.wecom.ws_client import WecomWSClient

# 模块级 ws_client 引用（主动推送 API 读取）
_ws_client: WecomWSClient | None = None


def get_ws_client() -> WecomWSClient | None:
    """获取 WS 客户端实例（仅在 ws_runner 进程内可用）"""
    return _ws_client


async def main() -> None:
    setup_logging()
    settings = get_settings()

    if not settings.wecom_bot_id or not settings.wecom_bot_secret:
        logger.error("WECOM_BOT_ID / WECOM_BOT_SECRET not configured, exiting")
        return

    db = get_supabase_client()
    msg_svc = WecomMessageService(db)

    # WS 客户端引用（回调闭包中需要）
    ws_client: WecomWSClient | None = None

    async def _on_message(data: dict) -> None:
        body = data.get("body", {})
        req_id = data.get("headers", {}).get("req_id", "")
        msgtype = body.get("msgtype", "")

        text_content = None
        image_urls = []
        file_url = None
        file_name = None
        aeskeys: dict = {}

        if msgtype == WecomMsgType.TEXT:
            text_content = body.get("text", {}).get("content", "")

        elif msgtype == WecomMsgType.VOICE:
            text_content = body.get("voice", {}).get("content", "")

        elif msgtype == WecomMsgType.IMAGE:
            img = body.get("image", {})
            url = img.get("url", "")
            if url:
                image_urls.append(url)
                aes = img.get("aeskey")
                if aes:
                    aeskeys[url] = aes

        elif msgtype == WecomMsgType.FILE:
            f = body.get("file", {})
            file_url = f.get("url", "")
            file_name = f.get("name", "")
            aes = f.get("aeskey")
            if aes and file_url:
                aeskeys[file_url] = aes

        elif msgtype == WecomMsgType.VIDEO:
            vid = body.get("video", {})
            file_url = vid.get("url", "")
            file_name = vid.get("name", "")
            aes = vid.get("aeskey")
            if aes and file_url:
                aeskeys[file_url] = aes

        elif msgtype == WecomMsgType.MIXED:
            for item in body.get("mixed", {}).get("msg_item", []):
                item_type = item.get("type", "")
                if item_type == "text":
                    text_content = (text_content or "") + item.get(
                        "text", {}
                    ).get("content", "")
                elif item_type == "image":
                    img = item.get("image", {})
                    url = img.get("url", "")
                    if url:
                        image_urls.append(url)
                        aes = img.get("aeskey")
                        if aes:
                            aeskeys[url] = aes

        msg = WecomIncomingMessage(
            msgid=body.get("msgid", ""),
            wecom_userid=body.get("from", {}).get("userid", ""),
            corp_id=settings.wecom_corp_id or "",
            chatid=body.get("chatid", ""),
            chattype=body.get("chattype", "single"),
            msgtype=msgtype,
            channel="smart_robot",
            text_content=text_content,
            image_urls=image_urls,
            file_url=file_url,
            file_name=file_name,
            aeskeys=aeskeys,
            raw_data=body,
        )

        reply_ctx = WecomReplyContext(
            channel="smart_robot",
            ws_client=ws_client,
            req_id=req_id,
        )

        await msg_svc.handle_message(msg, reply_ctx)

    async def _on_card_event(data: dict) -> None:
        """处理模板卡片事件回调"""
        body = data.get("body", {})
        event = body.get("event", {})
        card_event = event.get("template_card_event", {})

        event_key = card_event.get("event_key", "")
        task_id = card_event.get("task_id", "")
        card_type = card_event.get("card_type", "")
        selected_items = card_event.get("selected_items")

        wecom_userid = body.get("from", {}).get("userid", "")
        chatid = body.get("chatid", "")
        req_id = data.get("headers", {}).get("req_id", "")

        # 用户映射 + 获取对话 ID
        from services.wecom.user_mapping_service import WecomUserMappingService
        user_svc = WecomUserMappingService(db)
        user_id = await user_svc.get_or_create_user(
            wecom_userid=wecom_userid,
            corp_id=settings.wecom_corp_id or "",
            channel="smart_robot",
        )
        conversation_id = await msg_svc._get_or_create_conversation(
            user_id=user_id,
            chatid=chatid,
            chattype=body.get("chattype", "single"),
        )

        reply_ctx = WecomReplyContext(
            channel="smart_robot",
            ws_client=ws_client,
            req_id=req_id,
        )

        from services.wecom.card_event_handler import WecomCardEventHandler
        handler = WecomCardEventHandler(db)
        await handler.handle(
            event_key=event_key,
            task_id=task_id,
            card_type=card_type,
            selected_items=selected_items,
            user_id=user_id,
            conversation_id=conversation_id,
            reply_ctx=reply_ctx,
        )

    global _ws_client
    ws_client = WecomWSClient(
        bot_id=settings.wecom_bot_id,
        secret=settings.wecom_bot_secret,
        on_message=_on_message,
        on_card_event=_on_card_event,
    )
    _ws_client = ws_client

    # 优雅关闭
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Wecom WS runner: shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await ws_client.start()
    logger.info("Wecom WS runner started")

    # 阻塞直到收到关闭信号
    await stop_event.wait()

    await ws_client.stop()
    logger.info("Wecom WS runner stopped")


if __name__ == "__main__":
    asyncio.run(main())
