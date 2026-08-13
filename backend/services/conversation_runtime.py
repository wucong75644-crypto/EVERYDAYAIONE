"""Conversation Actor 独立进程的运行时装配与生命周期。"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Mapping

from services.conversation_db_scope import (
    ActorTaskDatabases,
    build_actor_task_databases,
    build_actor_worker_db,
)
from services.conversation_delivery import ActorTerminalDelivery
from services.conversation_execution import GenerationClaim, ConversationExecutionService
from services.conversation_worker import ConversationWorker, RedisConversationWakeup
from services.handlers.chat.actor_sink import ActorDelivery, ActorWebSink
from services.handlers.chat.executor import ChatGenerationExecutor, _normalize_model_id


class ConversationActorRuntime:
    """装配 Actor 执行链；Actor 永不持有 Sandbox 执行权。"""

    def __init__(
        self,
        db: Any,
        websocket: Any,
        kernel_manager: Any,
        *,
        worker_factory: Callable[..., ConversationWorker] = ConversationWorker,
        handler_db_factory: Callable[[], Any] | None = None,
        runtime_action_executor_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self._db = db
        self._websocket = websocket
        del kernel_manager
        self._handler_db_factory = handler_db_factory or _get_handler_db
        self._runtime_action_executor_factory = runtime_action_executor_factory
        worker_db = build_actor_worker_db(db)
        execution = ConversationExecutionService(
            worker_db,
            ChatGenerationExecutor(
                worker_db,
                runtime_action_executor_factory=runtime_action_executor_factory,
            ),
            renew_interval_seconds=5,
            task_db_factory=self._build_task_databases,
            executor_factory=self._create_executor,
            terminal_observer_factory=self._create_terminal_observer,
        )
        self._worker = worker_factory(
            worker_db,
            execution,
            wakeup_bus=RedisConversationWakeup(),
        )
        self._worker_task: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        if self._worker_task is not None:
            return
        self._worker_task = asyncio.create_task(
            self._worker.start(),
            name="conversation_actor_worker",
        )

    async def stop(self) -> None:
        await self._worker.stop()
        if self._worker_task is not None:
            worker_task, self._worker_task = self._worker_task, None
            if not worker_task.done():
                worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)

    def _create_sink(
        self,
        task: Mapping[str, Any],
        claim: GenerationClaim,
        cancellation_event: asyncio.Event,
        *,
        db: Any | None = None,
    ) -> ActorWebSink:
        delivery = _build_delivery(task, claim)
        return ActorWebSink(
            db or self._db,
            delivery,
            cancellation_event,
            self._websocket,
        )

    def _create_executor(
        self,
        databases: ActorTaskDatabases,
    ) -> ChatGenerationExecutor:
        return ChatGenerationExecutor(
            databases.application,
            handler_db_factory=lambda: databases.handler,
            runtime_action_executor_factory=self._runtime_action_executor_factory,
            sink_factory=lambda task, claim, cancellation_event: (
                self._create_sink(
                    task,
                    claim,
                    cancellation_event,
                    db=databases.control,
                )
            ),
        )

    def _create_terminal_observer(
        self,
        databases: ActorTaskDatabases,
    ) -> ActorTerminalDelivery:
        return ActorTerminalDelivery(
            databases.control,
            self._websocket,
            post_handler_factory=lambda: _create_post_handler(
                databases.handler,
            ),
        )

    def _build_task_databases(
        self,
        task: Mapping[str, Any],
    ) -> ActorTaskDatabases:
        return build_actor_task_databases(
            self._db,
            task,
            handler_db=self._handler_db_factory(),
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


def build_actor_runtime_action_executor(database: Any) -> Any:
    """Build the chat Action boundary with the real Runtime read catalog."""
    from services.agent.runtime.application.chat_action_composition import (
        build_production_chat_action_executor,
    )
    from services.agent.runtime.data_read_composition import (
        build_runtime_data_adapters,
    )
    from services.agent.runtime.executors.real_base import RuntimeReadResources
    from services.agent.runtime.production_composition import (
        build_safe_runtime_composition,
    )
    from services.agent.runtime.executors.erp_factory import (
        OrgScopedErpDispatcherFactory,
    )
    from services.configuration.envelope import LocalKEKProvider
    from services.configuration.material_service import SecretMaterialService

    material_service = SecretMaterialService(LocalKEKProvider.from_environment())
    erp_factory = OrgScopedErpDispatcherFactory(
        database, worker_id="conversation-actor", material_service=material_service,
    )
    data_adapters = build_runtime_data_adapters(
        database, worker_id="conversation-actor",
        erp_dispatcher_factory=erp_factory,
    )
    composition = build_safe_runtime_composition(
        resources=RuntimeReadResources(database=database),
        erp_dispatcher_factory=erp_factory,
        **data_adapters,
    )
    return build_production_chat_action_executor(
        database=database, registry=composition.registry,
    )


def create_kernel_manager() -> Any:
    """Legacy constructor retained for wiring compatibility; execution is disabled."""
    return None


def _create_post_handler(db: Any) -> Any:
    from services.handlers.chat_handler import ChatHandler

    return ChatHandler(db)


def _get_handler_db() -> Any:
    from core.database import get_worker_db

    return get_worker_db()
