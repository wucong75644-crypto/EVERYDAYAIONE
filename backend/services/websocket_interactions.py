"""WebSocket 交互等待状态与跨进程 Redis 响应通道。"""

import asyncio
import hashlib
import json
import time
from typing import List

from loguru import logger


_INTERACTION_TTL_SECONDS = 120
_POLL_INTERVAL_SECONDS = 0.1


def _interaction_key(kind: str, *scope_parts: str | None) -> str:
    """生成不暴露用户与企业标识的稳定 Redis key。"""
    raw_scope = json.dumps(scope_parts, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(raw_scope.encode("utf-8")).hexdigest()
    return f"ws:interaction:{kind}:{digest}"


class WebSocketInteractionMixin:
    """管理绑定用户/企业上下文的本地与跨进程交互等待键。"""

    def _init_interaction_state(self) -> None:
        self._pending_confirms: dict[
            tuple[str, str, str | None],
            tuple[asyncio.Event, List],
        ] = {}
        self._steer_signals: dict[
            tuple[str, str | None], asyncio.Event
        ] = {}
        self._steer_messages: dict[tuple[str, str | None], str] = {}
        self._remote_steer_tasks: dict[
            tuple[str, str | None], asyncio.Task
        ] = {}

    async def wait_for_confirm(
        self,
        tool_call_id: str,
        user_id: str,
        org_id: str | None,
        timeout: float = 60.0,
    ) -> bool:
        """等待本进程或其他进程发来的同租户工具确认。"""
        scope = (tool_call_id, user_id, org_id)
        event = asyncio.Event()
        result_holder: List = [None]
        self._pending_confirms[scope] = (event, result_holder)
        local_wait = asyncio.create_task(event.wait())
        remote_wait = asyncio.create_task(self._poll_redis(
            _interaction_key("confirm", *scope), timeout,
        ))
        try:
            done, _ = await asyncio.wait(
                {local_wait, remote_wait},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if local_wait in done:
                return result_holder[0] is True
            if remote_wait in done:
                return remote_wait.result() == "1"
            logger.info(
                "Tool confirm timeout | "
                f"tool_call_id={tool_call_id} | user={user_id} | org={org_id}"
            )
            return False
        finally:
            local_wait.cancel()
            remote_wait.cancel()
            self._pending_confirms.pop(scope, None)

    async def resolve_confirm(
        self,
        tool_call_id: str,
        user_id: str,
        org_id: str | None,
        approved: bool,
    ) -> bool:
        """解析本地等待者；不存在时投递到其他 Worker/Actor。"""
        scope = (tool_call_id, user_id, org_id)
        pending = self._pending_confirms.get(scope)
        if pending:
            event, result_holder = pending
            result_holder[0] = approved
            event.set()
            return True
        return await self._push_redis(
            _interaction_key("confirm", *scope),
            "1" if approved else "0",
        )

    def register_steer_listener(
        self, task_id: str, org_id: str | None = None,
    ) -> None:
        """注册任务打断监听，并启动跨进程 Redis 轮询。"""
        scope = (task_id, org_id)
        self.unregister_steer_listener(task_id, org_id)
        self._steer_signals[scope] = asyncio.Event()
        try:
            loop = asyncio.get_running_loop()
            self._remote_steer_tasks[scope] = loop.create_task(
                self._listen_remote_steer(scope)
            )
        except RuntimeError:
            logger.debug(
                f"Steer remote listener skipped without event loop | scope={scope}"
            )

    def check_steer(
        self, task_id: str, org_id: str | None = None,
    ) -> str | None:
        """消费指定任务与企业上下文的打断消息。"""
        scope = (task_id, org_id)
        event = self._steer_signals.get(scope)
        if event and event.is_set():
            message = self._steer_messages.pop(scope, None)
            self.unregister_steer_listener(task_id, org_id)
            return message
        return None

    def resolve_steer(
        self,
        task_id: str,
        message: str,
        org_id: str | None = None,
    ) -> bool:
        """解析本地等待者；不存在时投递到 Actor 进程。"""
        scope = (task_id, org_id)
        event = self._steer_signals.get(scope)
        if event:
            self._steer_messages[scope] = message
            event.set()
            return True
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._push_redis(
                _interaction_key("steer", *scope), message,
            ))
            return True
        except RuntimeError:
            logger.warning(
                f"Steer remote push skipped without event loop | scope={scope}"
            )
            return False

    def unregister_steer_listener(
        self, task_id: str, org_id: str | None = None,
    ) -> None:
        """清理指定任务与企业上下文的本地和远程监听。"""
        scope = (task_id, org_id)
        remote_task = self._remote_steer_tasks.pop(scope, None)
        if remote_task:
            remote_task.cancel()
        self._steer_signals.pop(scope, None)
        self._steer_messages.pop(scope, None)

    async def _listen_remote_steer(
        self, scope: tuple[str, str | None],
    ) -> None:
        value = await self._poll_redis(
            _interaction_key("steer", *scope), None,
        )
        event = self._steer_signals.get(scope)
        if value is not None and event:
            self._steer_messages[scope] = value
            event.set()

    async def _poll_redis(
        self, key: str, timeout: float | None,
    ) -> str | None:
        from core.redis import RedisClient

        deadline = time.monotonic() + timeout if timeout is not None else None
        logged_failure = False
        while deadline is None or time.monotonic() < deadline:
            try:
                client = await RedisClient.get_client()
                value = await client.lpop(key)
                if value is not None:
                    return str(value)
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if not logged_failure:
                    logger.warning(
                        "WS interaction Redis poll failed | "
                        f"error={type(error).__name__}"
                    )
                    logged_failure = True
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        return None

    async def _push_redis(self, key: str, value: str) -> bool:
        from core.redis import RedisClient

        try:
            client = await RedisClient.get_client()
            pipeline = client.pipeline()
            pipeline.rpush(key, value)
            pipeline.expire(key, _INTERACTION_TTL_SECONDS)
            await pipeline.execute()
            return True
        except Exception as error:
            logger.warning(
                f"WS interaction Redis push failed | error={type(error).__name__}"
            )
            return False
