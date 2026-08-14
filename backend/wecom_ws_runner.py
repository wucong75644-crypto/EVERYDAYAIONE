"""
企业微信智能机器人 WS 长连接 — 独立进程（多企业版）

每个配了 wecom_bot_id + wecom_bot_secret 的企业独立一条 WS 连接。
独立于 API 服务运行，避免多 worker 竞争同一个长连接。
由 systemd (everydayai-wecom.service) 管理生命周期。
"""

import asyncio
import signal
import sys
from typing import Any
from pathlib import Path

import httpx

# 确保 backend 目录在 sys.path 中（systemd 启动时 cwd 可能不同）
sys.path.insert(0, str(Path(__file__).parent))

# 关键：python wecom_ws_runner.py 启动时，本文件被加载为 __main__ 模块。
# 但 push_dispatcher 里 from wecom_ws_runner import get_ws_client 会重新导入
# 作为独立的 wecom_ws_runner 模块（_manager=None）。
# 注册到 sys.modules 确保 import 拿到同一份模块实例。
if __name__ == "__main__" and "wecom_ws_runner" not in sys.modules:
    sys.modules["wecom_ws_runner"] = sys.modules["__main__"]

from loguru import logger

from core.config import settings
from core.database import (
    close_async_db,
    close_async_worker_db,
    close_db,
    close_worker_db,
    get_async_db,
    get_db,
    get_worker_db,
)
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

    def __init__(self, control_db: Any, runtime_db: Any) -> None:
        self._control_db = control_db
        self._runtime_db = runtime_db
        self._clients: dict[str, WecomWSClient] = {}  # org_id → client

    @property
    def clients(self) -> dict[str, WecomWSClient]:
        return self._clients

    def get_client(self, org_id: str) -> WecomWSClient | None:
        """按企业获取 WS 客户端"""
        return self._clients.get(org_id)

    async def start(self) -> None:
        """扫描所有配了 bot 凭证的企业，逐个建立 WS 连接"""
        from services.configuration.bundles import WecomBotTargetResolver
        targets = WecomBotTargetResolver(self._control_db).list_targets()

        if not targets:
            logger.warning("No org with wecom bot configured, ws_runner idle")
            return

        for target in targets:
            org_id = target.org_id
            msg_svc = WecomMessageService(self._runtime_db)

            client = WecomWSClient(
                bot_id=target.bot_id,
                secret=target.bot_secret,
                org_id=org_id,
                on_message=self._make_message_handler(
                    org_id, target.corp_id, msg_svc,
                ),
                on_card_event=self._make_card_handler(
                    org_id, target.corp_id, msg_svc,
                ),
            )
            self._clients[org_id] = client
            await client.start()
            logger.info(
                f"Wecom bot started | org_id={org_id} | "
                f"corp_id={target.corp_id} | bot_id={target.bot_id[:8]}..."
            )

        logger.info(f"WecomWSManager: {len(self._clients)} bot(s) running")

    async def stop(self) -> None:
        """停止所有连接"""
        for org_id, client in self._clients.items():
            await client.stop()
            logger.info(f"Wecom bot stopped | org_id={org_id}")

    def _make_message_handler(self, org_id: str, corp_id: str, msg_svc: WecomMessageService):
        """为每个企业创建独立的消息处理闭包"""
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
            request_svc = msg_svc._for_request(
                actor_user_id=None,
                org_id=org_id,
                request_id=req_id,
            )
            user_svc = WecomUserMappingService(request_svc.db)
            user_id = await user_svc.get_or_create_user(
                wecom_userid=wecom_userid,
                corp_id=corp_id,
                channel="smart_robot",
                org_id=org_id,
            )
            request_svc._bind_request_db(
                actor_user_id=user_id,
                org_id=org_id,
                request_id=req_id,
            )
            conversation_id = await request_svc._get_or_create_conversation(
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
            card_handler = WecomCardEventHandler(request_svc.db)
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


class _ScheduledWecomRuntimeOwner:
    """Supervise one worker and exclusively own its process-level resources."""

    def __init__(
        self, worker: Any, task: asyncio.Task[None], http_client: httpx.AsyncClient,
    ) -> None:
        self.worker = worker
        self.task = task
        self._http_client = http_client
        self._stop_requested = False
        self._stop_lock = asyncio.Lock()
        self._stop_task: asyncio.Task[None] | None = None
        self._supervisor_task = asyncio.create_task(
            self._supervise(),
            name="scheduled_runtime_wecom_supervisor",
        )

    async def stop(self) -> None:
        async with self._stop_lock:
            if self._stop_task is None:
                self._stop_requested = not self.task.done()
                self._stop_task = asyncio.create_task(
                    self._request_worker_stop(),
                    name="scheduled_runtime_wecom_stop",
                )
            stop_task = self._stop_task
        try:
            await asyncio.shield(stop_task)
        except asyncio.CancelledError:
            await asyncio.shield(stop_task)
            raise

    async def _request_worker_stop(self) -> None:
        try:
            await self.worker.stop()
        except (asyncio.CancelledError, Exception) as error:
            self.task.cancel()
            logger.error(
                "scheduled_runtime_wecom_stop_failed | category={}",
                type(error).__name__,
            )
        finally:
            await asyncio.gather(self.task, return_exceptions=True)
            await asyncio.shield(self._supervisor_task)

    async def _supervise(self) -> None:
        result = (await asyncio.gather(self.task, return_exceptions=True))[0]
        if not self._stop_requested:
            category = (
                type(result).__name__
                if isinstance(result, BaseException)
                else "UnexpectedExit"
            )
            logger.error(
                "scheduled_runtime_wecom_worker_failed | category={}",
                category,
            )
        await _close_scheduled_wecom_dependencies(
            self._http_client,
            runtime_db_opened=True,
        )


async def _start_scheduled_wecom_runtime(
) -> _ScheduledWecomRuntimeOwner | None:
    if not settings.agent_runtime_scheduled_wecom_enabled:
        return None

    http_client: httpx.AsyncClient | None = None
    runtime_db_opened = False
    worker_task: asyncio.Task[None] | None = None
    try:
        from services.configuration.envelope import LocalKEKProvider
        from services.configuration.material_service import SecretMaterialService
        from services.wecom.access_token_manager import get_access_token
        from services.wecom.scheduled_runtime_composition import (
            build_scheduled_wecom_runtime_components,
        )

        material_service = SecretMaterialService(
            LocalKEKProvider.from_environment(),
        )
        runtime_db_opened = True
        runtime_db = await get_async_db()
        http_client = httpx.AsyncClient()
        components = build_scheduled_wecom_runtime_components(
            database=runtime_db,
            get_ws_client=get_ws_client,
            material_service=material_service,
            get_access_token=get_access_token,
            outbound_http_client=http_client,
            worker_id=settings.agent_runtime_scheduled_wecom_worker_id,
        )
        worker_task = asyncio.create_task(
            components.worker.start(),
            name="scheduled_runtime_wecom_worker",
        )
        owner = _ScheduledWecomRuntimeOwner(
            components.worker, worker_task, http_client,
        )
        worker_task = None
        http_client = None
        runtime_db_opened = False
        return owner
    except (asyncio.CancelledError, Exception) as error:
        if worker_task is not None:
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
        await _close_scheduled_wecom_dependencies(
            http_client,
            runtime_db_opened=runtime_db_opened,
        )
        if isinstance(error, asyncio.CancelledError):
            raise
        logger.error(
            "scheduled_runtime_wecom_start_failed | category={}",
            type(error).__name__,
        )
        return None


async def _stop_scheduled_wecom_runtime(
    owner: _ScheduledWecomRuntimeOwner | None,
) -> None:
    if owner is not None:
        await owner.stop()


async def _start_scheduled_wecom_until_stop(
    stop_event: asyncio.Event,
) -> _ScheduledWecomRuntimeOwner | None:
    start_task = asyncio.create_task(
        _start_scheduled_wecom_runtime(),
        name="scheduled_runtime_wecom_start",
    )
    stop_task = asyncio.create_task(
        stop_event.wait(),
        name="scheduled_runtime_wecom_start_stop_wait",
    )
    try:
        done, _ = await asyncio.wait(
            (start_task, stop_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if start_task in done:
            return start_task.result()
        start_task.cancel()
        await asyncio.gather(start_task, return_exceptions=True)
        return None
    finally:
        for task in (start_task, stop_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(start_task, stop_task, return_exceptions=True)


async def _close_scheduled_wecom_dependencies(
    http_client: httpx.AsyncClient | None,
    *,
    runtime_db_opened: bool,
) -> None:
    if http_client is not None:
        try:
            await http_client.aclose()
        except Exception as error:
            logger.error(
                "scheduled_runtime_wecom_http_close_failed | category={}",
                type(error).__name__,
            )
    if runtime_db_opened:
        try:
            await close_async_db()
        except Exception as error:
            logger.error(
                "scheduled_runtime_wecom_db_close_failed | category={}",
                type(error).__name__,
            )


async def _stop_existing_wecom_components(
    *,
    callback_task: asyncio.Task[Any] | None,
    manager: WecomWSManager | None,
) -> None:
    background_tasks = [task for task in (callback_task,) if task is not None]
    for task in background_tasks:
        task.cancel()
    if background_tasks:
        await asyncio.gather(*background_tasks, return_exceptions=True)

    if manager is not None:
        await manager.stop()
    await close_async_worker_db()
    close_worker_db()
    close_db()


async def main() -> None:
    setup_logging()

    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Wecom WS runner: shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    scheduled_runtime = None
    callback_task = None
    manager = None
    try:
        runtime_db = get_db()
        control_db = get_worker_db()
        global _manager
        manager = _manager = WecomWSManager(control_db, runtime_db)
        await manager.start()

        from services.wecom.callback_inbox_worker import WecomCallbackInboxWorker
        callback_worker = WecomCallbackInboxWorker(runtime_db, control_db)
        callback_task = asyncio.create_task(
            callback_worker.run(),
            name="wecom_callback_inbox_worker",
        )
        scheduled_runtime = await _start_scheduled_wecom_until_stop(stop_event)
        if stop_event.is_set():
            return

        if not manager.clients:
            logger.warning("No bots to run, ws_runner will wait for signal")

        logger.info(f"Wecom WS runner started | {len(manager.clients)} bot(s)")
        await stop_event.wait()
    finally:
        try:
            await _stop_scheduled_wecom_runtime(scheduled_runtime)
        finally:
            await _stop_existing_wecom_components(
                callback_task=callback_task,
                manager=manager,
            )
        logger.info("Wecom WS runner stopped")


if __name__ == "__main__":
    asyncio.run(main())
