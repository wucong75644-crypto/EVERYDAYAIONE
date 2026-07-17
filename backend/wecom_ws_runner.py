"""
企业微信智能机器人 WS 长连接 — 独立进程（多企业版）

每个配了 wecom_bot_id + wecom_bot_secret 的企业独立一条 WS 连接。
独立于 API 服务运行，避免多 worker 竞争同一个长连接。
由 systemd (everydayai-wecom.service) 管理生命周期。
"""

import asyncio
import signal
import sys
from pathlib import Path

# 确保 backend 目录在 sys.path 中（systemd 启动时 cwd 可能不同）
sys.path.insert(0, str(Path(__file__).parent))

# 关键：python wecom_ws_runner.py 启动时，本文件被加载为 __main__ 模块。
# 但 push_dispatcher 里 from wecom_ws_runner import get_ws_client 会重新导入
# 作为独立的 wecom_ws_runner 模块（_manager=None）。
# 注册到 sys.modules 确保 import 拿到同一份模块实例。
if __name__ == "__main__" and "wecom_ws_runner" not in sys.modules:
    sys.modules["wecom_ws_runner"] = sys.modules["__main__"]

from loguru import logger

from core.database import close_async_db, get_async_db, get_db
from core.logging_config import setup_logging
from schemas.wecom import WecomReplyContext
from services.wecom.wecom_message_service import WecomMessageService
from services.wecom.message_normalizer import (
    normalize_wecom_message,
    parse_message_content as _parse_message_content,
)
from services.wecom.ws_client import WecomWSClient


# ── 消息解析（从 data 中提取各类消息内容）──────────────


# ── 多企业 WS 管理器 ──────────────────────────────────


class WecomWSManager:
    """管理多个企业的 WS 长连接，每个企业独立一条连接。"""

    def __init__(self, db):
        self._db = db
        self._clients: dict[str, WecomWSClient] = {}  # org_id → client

    @property
    def clients(self) -> dict[str, WecomWSClient]:
        return self._clients

    def get_client(self, org_id: str) -> WecomWSClient | None:
        """按企业获取 WS 客户端"""
        return self._clients.get(org_id)

    async def start(self) -> None:
        """扫描所有配了 bot 凭证的企业，逐个建立 WS 连接"""
        from services.org.config_resolver import OrgConfigResolver
        resolver = OrgConfigResolver(self._db)
        orgs = resolver.list_orgs_with_wecom_bot()

        if not orgs:
            logger.warning("No org with wecom bot configured, ws_runner idle")
            return

        for org in orgs:
            org_id = org["org_id"]
            msg_svc = WecomMessageService(self._db)

            client = WecomWSClient(
                bot_id=org["bot_id"],
                secret=org["bot_secret"],
                org_id=org_id,
                on_message=self._make_message_handler(org_id, org["corp_id"], msg_svc),
                on_card_event=self._make_card_handler(org_id, org["corp_id"], msg_svc),
            )
            self._clients[org_id] = client
            await client.start()
            logger.info(
                f"Wecom bot started | org_id={org_id} | "
                f"corp_id={org['corp_id']} | bot_id={org['bot_id'][:8]}..."
            )

        logger.info(f"WecomWSManager: {len(self._clients)} bot(s) running")

    async def stop(self) -> None:
        """停止所有连接"""
        for org_id, client in self._clients.items():
            await client.stop()
            logger.info(f"Wecom bot stopped | org_id={org_id}")

    def _make_message_handler(self, org_id: str, corp_id: str, msg_svc: WecomMessageService):
        """为每个企业创建独立的消息处理闭包"""
        db = self._db

        async def handler(data: dict) -> None:
            body = data.get("body", {})
            req_id = data.get("headers", {}).get("req_id", "")
            msg = normalize_wecom_message(
                body, org_id=org_id, corp_id=corp_id,
            )

            reply_ctx = WecomReplyContext(
                channel="smart_robot",
                ws_client=self._clients.get(org_id),
                req_id=req_id,
            )

            await msg_svc.handle_message(msg, reply_ctx)

        return handler

    def _make_card_handler(self, org_id: str, corp_id: str, msg_svc: WecomMessageService):
        """为每个企业创建独立的卡片事件处理闭包"""
        db = self._db

        async def handler(data: dict) -> None:
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

            from services.wecom.user_mapping_service import WecomUserMappingService
            user_svc = WecomUserMappingService(db)
            user_id = await user_svc.get_or_create_user(
                wecom_userid=wecom_userid,
                corp_id=corp_id,
                channel="smart_robot",
                org_id=org_id,
            )
            conversation_id = await msg_svc._get_or_create_conversation(
                user_id=user_id,
                chatid=chatid,
                chattype=body.get("chattype", "single"),
                corp_id=corp_id,
                org_id=org_id,
            )

            reply_ctx = WecomReplyContext(
                channel="smart_robot",
                ws_client=self._clients.get(org_id),
                req_id=req_id,
            )

            from services.wecom.card_event_handler import WecomCardEventHandler
            card_handler = WecomCardEventHandler(db)
            await card_handler.handle(
                event_key=event_key,
                task_id=task_id,
                card_type=card_type,
                selected_items=selected_items,
                user_id=user_id,
                conversation_id=conversation_id,
                reply_ctx=reply_ctx,
                org_id=org_id,
                chat_type=body.get("chattype", "single"),
            )

        return handler


# ── 模块级访问（主动推送 API 读取）──────────────────────

_manager: WecomWSManager | None = None


def get_ws_client(org_id: str | None = None) -> WecomWSClient | None:
    """按企业获取 WS 客户端实例（仅在 ws_runner 进程内可用）

    Args:
        org_id: 企业 ID。None 时返回 None（散客无企微 bot）。
    """
    if not _manager or not org_id:
        return None
    return _manager.get_client(org_id)


# ── 主入口 ─────────────────────────────────────────────


async def main() -> None:
    setup_logging()

    db = get_db()

    global _manager
    _manager = WecomWSManager(db)
    await _manager.start()

    async_db = await get_async_db()
    from services.wecom.delivery_sender import WecomDeliverySender
    from services.wecom.delivery_worker import WecomDeliveryWorker
    delivery_worker = WecomDeliveryWorker(
        async_db,
        WecomDeliverySender(async_db, get_ws_client),
    )
    delivery_task = asyncio.create_task(
        delivery_worker.start(),
        name="wecom_delivery_worker",
    )

    # 定时任务推送订阅器（跨进程 IPC：web 进程 publish → ws_runner 这里 subscribe）
    # 设计文档: docs/document/TECH_定时任务心跳系统.md §4.4
    from services.scheduler.push_dispatcher import start_proactive_subscriber
    proactive_task = asyncio.create_task(start_proactive_subscriber())

    if not _manager.clients:
        logger.warning("No bots to run, ws_runner will wait for signal")

    # 优雅关闭
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Wecom WS runner: shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info(f"Wecom WS runner started | {len(_manager.clients)} bot(s)")

    # 阻塞直到收到关闭信号
    await stop_event.wait()

    proactive_task.cancel()
    try:
        await proactive_task
    except asyncio.CancelledError:
        pass

    await delivery_worker.stop()
    try:
        await asyncio.wait_for(delivery_task, timeout=10)
    except asyncio.TimeoutError:
        delivery_task.cancel()
        await asyncio.gather(delivery_task, return_exceptions=True)
    await _manager.stop()
    await close_async_db()
    logger.info("Wecom WS runner stopped")


if __name__ == "__main__":
    asyncio.run(main())
