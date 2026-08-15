import asyncio

import pytest

from core.db_scope import AsyncScopedDatabaseClient
from services.conversation_db_scope import ActorTaskDatabases
from services.conversation_execution import GenerationClaim
from services.conversation_runtime import (
    ConversationActorRuntime,
    _build_delivery,
    _get_handler_db,
    create_kernel_manager,
)
from services.sandbox.kernel_manager import KernelManager
from services.handlers.chat.actor_sink import ActorWebSink
from services.sandbox.kernel_manager import get_kernel_manager


class _Kernel:
    def __init__(self):
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True

    async def shutdown(self):
        self.stopped = True


class _Worker:
    def __init__(self, *args, **kwargs):
        self.db = args[0]
        self.execution = args[1]
        self.started = asyncio.Event()
        self.stopped = False

    async def start(self):
        self.started.set()
        await asyncio.Event().wait()

    async def stop(self):
        self.stopped = True


@pytest.mark.asyncio
async def test_runtime_owns_agent_loop_sandbox_lifecycle():
    kernel = _Kernel()
    runtime = ConversationActorRuntime(
        object(), object(), kernel, worker_factory=_Worker,
    )

    await runtime.start()
    await asyncio.sleep(0)
    await runtime.stop()

    assert kernel.started is True
    assert kernel.stopped is True
    assert get_kernel_manager() is None


def test_create_kernel_manager_restores_local_sandbox_owner():
    assert isinstance(create_kernel_manager(), KernelManager)


def test_build_delivery_uses_external_task_id():
    claim = GenerationClaim(
        task_id="internal",
        execution_token="token",
        conversation_id="conversation",
        turn_id="turn",
        input_message_id="input",
        base_context_revision=1,
        context_through_message_id="input",
        execution_attempt=1,
        execution_mode="serial",
        user_id="user",
        org_id="org",
    )

    delivery = _build_delivery(
        {
            "id": "internal",
            "external_task_id": "client",
            "assistant_message_id": "assistant",
            "user_id": "user",
            "org_id": "org",
            "model_id": "qwen3.5-plus",
        },
        claim,
    )

    assert delivery.task_id == "internal"
    assert delivery.push_task_id == "client"
    assert delivery.execution_token == "token"


def test_runtime_uses_web_sink_for_wecom_actor():
    websocket = object()
    runtime = ConversationActorRuntime(
        object(), websocket, _Kernel(), worker_factory=_Worker,
    )
    claim = GenerationClaim(
        task_id="internal", execution_token="token",
        conversation_id="conversation", turn_id="turn",
        input_message_id="input", base_context_revision=1,
        context_through_message_id="input", execution_attempt=1,
        execution_mode="serial",
        user_id="user",
        org_id=None,
    )
    task = {
        "id": "internal", "assistant_message_id": "assistant",
        "user_id": "user", "model_id": "model",
        "delivery_context": {"actor": True, "channel": "wecom"},
    }

    sink = runtime._create_sink(task, claim, asyncio.Event())

    assert isinstance(sink, ActorWebSink)
    assert sink._websocket is websocket


def test_runtime_routes_worker_and_task_databases_by_scope():
    runtime = ConversationActorRuntime(
        object(), object(), _Kernel(), worker_factory=_Worker,
    )
    control = object()
    application = object()
    handler = object()
    databases = ActorTaskDatabases(control, application, handler)

    executor = runtime._create_executor(databases)
    observer = runtime._create_terminal_observer(databases)

    assert isinstance(runtime._worker.db, AsyncScopedDatabaseClient)
    assert runtime._worker.db.scope.settings[2] == "worker"
    assert runtime._worker.db.scope.actor_user_id is None
    assert executor._db is application
    assert executor._handler_db_factory() is handler
    assert observer._db is control
    assert observer._post_handler_factory().db is handler


def test_default_handler_database_uses_runtime_role(monkeypatch):
    runtime_db = object()
    worker_db_called = False

    def fail_worker_db():
        nonlocal worker_db_called
        worker_db_called = True
        raise AssertionError("AgentLoop Handler must not use Worker DB")

    monkeypatch.setattr("core.database.get_db", lambda: runtime_db)
    monkeypatch.setattr("core.database.get_worker_db", fail_worker_db)

    assert _get_handler_db() is runtime_db
    assert worker_db_called is False
