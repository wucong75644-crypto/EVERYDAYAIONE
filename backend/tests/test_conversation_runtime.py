import asyncio

import pytest

from services.conversation_execution import GenerationClaim
from services.conversation_runtime import ConversationActorRuntime, _build_delivery
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
        self.started = asyncio.Event()
        self.stopped = False

    async def start(self):
        self.started.set()
        await asyncio.Event().wait()

    async def stop(self):
        self.stopped = True


@pytest.mark.asyncio
async def test_runtime_starts_and_stops_worker_and_kernel():
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
    )
    task = {
        "id": "internal", "assistant_message_id": "assistant",
        "user_id": "user", "model_id": "model",
        "delivery_context": {"actor": True, "channel": "wecom"},
    }

    sink = runtime._create_sink(task, claim, asyncio.Event())

    assert isinstance(sink, ActorWebSink)
    assert sink._websocket is websocket
