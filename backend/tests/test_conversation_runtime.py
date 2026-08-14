import asyncio
from types import SimpleNamespace

import pytest

from core.db_scope import AsyncScopedDatabaseClient
from services.conversation_db_scope import ActorTaskDatabases
from services.conversation_execution import GenerationClaim
from services.conversation_runtime import (
    ConversationActorRuntime,
    _build_delivery,
    _get_handler_db,
    build_actor_runtime_action_executor,
)
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
async def test_runtime_starts_worker_without_becoming_sandbox_owner():
    kernel = _Kernel()
    runtime = ConversationActorRuntime(
        object(), object(), kernel, worker_factory=_Worker,
    )

    await runtime.start()
    await asyncio.sleep(0)
    await runtime.stop()

    assert kernel.started is False
    assert kernel.stopped is False
    assert get_kernel_manager() is None


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


def test_runtime_injects_runtime_action_executor_factory_into_chat_executor():
    marker = object()
    runtime = ConversationActorRuntime(
        object(), object(), _Kernel(), worker_factory=_Worker,
        runtime_action_executor_factory=lambda _db: marker,
    )
    databases = ActorTaskDatabases(object(), object(), object())

    executor = runtime._create_executor(databases)

    assert executor._runtime_action_executor_factory(databases.application) is marker


def test_default_runtime_action_executor_resolves_real_composition(monkeypatch):
    registry = object()
    expected = object()
    monkeypatch.setattr(
        "services.configuration.envelope.LocalKEKProvider.from_environment",
        lambda: object(),
    )
    monkeypatch.setattr(
        "services.configuration.material_service.SecretMaterialService",
        lambda _provider: object(),
    )
    monkeypatch.setattr(
        "services.agent.runtime.executors.erp_factory.OrgScopedErpDispatcherFactory",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "services.agent.runtime.data_read_composition.build_runtime_data_adapters",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "services.agent.runtime.production_composition.build_safe_runtime_composition",
        lambda **_kwargs: SimpleNamespace(registry=registry),
    )
    monkeypatch.setattr(
        "services.agent.runtime.chat_action_composition.build_production_chat_action_executor",
        lambda **kwargs: expected if kwargs["registry"] is registry else None,
    )

    assert build_actor_runtime_action_executor(object()) is expected


def test_default_handler_database_uses_worker_role(monkeypatch):
    worker_db = object()
    runtime_db_called = False

    def fail_runtime_db():
        nonlocal runtime_db_called
        runtime_db_called = True
        raise AssertionError("Actor Handler must not use runtime DB")

    monkeypatch.setattr("core.database.get_db", fail_runtime_db)
    monkeypatch.setattr("core.database.get_worker_db", lambda: worker_db)

    assert _get_handler_db() is worker_db
    assert runtime_db_called is False
