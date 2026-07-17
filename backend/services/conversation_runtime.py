"""Conversation Actor 独立进程的运行时装配与生命周期。"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Mapping

from services.conversation_delivery import ActorTerminalDelivery
from services.conversation_execution import GenerationClaim, ConversationExecutionService
from services.conversation_worker import ConversationWorker, RedisConversationWakeup
from services.handlers.chat.actor_sink import ActorDelivery, ActorWebSink
from services.handlers.chat.executor import ChatGenerationExecutor, _normalize_model_id


class ConversationActorRuntime:
    """装配 Actor 执行链，并保证 Worker 与 Kernel 有序关闭。"""

    def __init__(
        self,
        db: Any,
        websocket: Any,
        kernel_manager: Any,
        *,
        worker_factory: Callable[..., ConversationWorker] = ConversationWorker,
    ) -> None:
        self._db = db
        self._websocket = websocket
        self._kernel_manager = kernel_manager
        executor = ChatGenerationExecutor(db, sink_factory=self._create_sink)
        execution = ConversationExecutionService(
            db,
            executor,
            renew_interval_seconds=5,
            terminal_observer=ActorTerminalDelivery(db, websocket),
        )
        self._worker = worker_factory(
            db,
            execution,
            wakeup_bus=RedisConversationWakeup(),
        )
        self._worker_task: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        if self._worker_task is not None:
            return
        from services.sandbox.kernel_manager import set_kernel_manager

        await self._kernel_manager.start()
        set_kernel_manager(self._kernel_manager)
        self._worker_task = asyncio.create_task(
            self._worker.start(),
            name="conversation_actor_worker",
        )

    async def stop(self) -> None:
        from services.sandbox.kernel_manager import set_kernel_manager

        await self._worker.stop()
        if self._worker_task is not None:
            worker_task, self._worker_task = self._worker_task, None
            if not worker_task.done():
                worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
        await self._kernel_manager.shutdown()
        set_kernel_manager(None)

    def _create_sink(
        self,
        task: Mapping[str, Any],
        claim: GenerationClaim,
        cancellation_event: asyncio.Event,
    ) -> ActorWebSink:
        delivery = _build_delivery(task, claim)
        return ActorWebSink(
            self._db,
            delivery,
            cancellation_event,
            self._websocket,
        )


def _build_delivery(
    task: Mapping[str, Any],
    claim: GenerationClaim,
) -> ActorDelivery:
    push_task_id = (
        task.get("client_task_id")
        or task.get("external_task_id")
        or task.get("id")
    )
    required = {
        "push_task_id": push_task_id,
        "assistant_message_id": task.get("assistant_message_id"),
        "user_id": task.get("user_id"),
    }
    if any(not value for value in required.values()):
        raise RuntimeError("ACTOR_DELIVERY_DATA_MISSING")
    return ActorDelivery(
        task_id=claim.task_id,
        push_task_id=str(push_task_id),
        execution_token=claim.execution_token,
        conversation_id=claim.conversation_id,
        message_id=str(task["assistant_message_id"]),
        user_id=str(task["user_id"]),
        org_id=str(task["org_id"]) if task.get("org_id") else None,
        model_id=_normalize_model_id(task.get("model_id")),
    )
def create_kernel_manager() -> Any:
    from services.sandbox.kernel_manager import KernelManager

    config = os.path.join(
        os.path.dirname(__file__), "..", "..", "deploy", "sandbox.cfg",
    )
    return KernelManager(nsjail_cfg=config if os.path.exists(config) else None)
