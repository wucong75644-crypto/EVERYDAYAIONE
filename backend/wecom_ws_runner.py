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
        if msgtype == WecomMsgType.TEXT:
            text_content = body.get("text", {}).get("content", "")
        elif msgtype == WecomMsgType.VOICE:
            text_content = body.get("voice", {}).get("content", "")

        msg = WecomIncomingMessage(
            msgid=body.get("msgid", ""),
            wecom_userid=body.get("from", {}).get("userid", ""),
            corp_id=settings.wecom_corp_id or "",
            chatid=body.get("chatid", ""),
            chattype=body.get("chattype", "single"),
            msgtype=msgtype,
            channel="smart_robot",
            text_content=text_content,
            raw_data=body,
        )

        reply_ctx = WecomReplyContext(
            channel="smart_robot",
            ws_client=ws_client,
            req_id=req_id,
        )

        await msg_svc.handle_message(msg, reply_ctx)

    ws_client = WecomWSClient(
        bot_id=settings.wecom_bot_id,
        secret=settings.wecom_bot_secret,
        on_message=_on_message,
    )

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
