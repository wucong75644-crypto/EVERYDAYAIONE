from __future__ import annotations

import asyncio
import json
import pickle
import tempfile
from pathlib import Path

import pytest

from services.adapters.types import StreamChunk
from services.agent.runtime.model_gateway.client import IsolatedModelGatewayClient
from services.agent.runtime.model_gateway.server import FakeModelGatewayServer
from tests.test_agent_runtime_model_gateway_service import (
    SECRET,
    FakeRepository,
    _bundle,
    _collect,
    _request,
    _service,
)


@pytest.mark.asyncio
async def test_claimed_disconnect_before_consumer_leaves_recoverable_claim() -> None:
    request = _request()
    repository = FakeRepository(request, _bundle())
    service, adapters = _service(repository, [StreamChunk(content="never")])
    stream = service.complete(request)

    assert (await anext(stream))["type"] == "accepted"
    await stream.aclose()

    assert repository.timeline == ["claim"]
    assert adapters == []


@pytest.mark.asyncio
async def test_disconnect_after_dispatch_closes_adapter_and_readback_never_resubmits() -> None:
    request = _request()
    repository = FakeRepository(request, _bundle())
    service, adapters = _service(
        repository, [StreamChunk(content="partial"), StreamChunk(content="later")],
    )
    stream = service.complete(request)

    assert (await anext(stream))["type"] == "accepted"
    assert (await anext(stream))["type"] == "delta"
    await stream.aclose()

    assert adapters[0].closed
    assert "mark_dispatched" in repository.timeline
    assert "finalize" not in repository.timeline
    provider_calls = repository.timeline.count("provider_call")
    repository.claim_outcome = "readback"
    readback_service, readback_adapters = _service(repository, [])
    frames = await _collect(readback_service, request)
    assert frames[-1]["type"] == "unknown"
    assert repository.timeline.count("provider_call") == provider_calls
    assert readback_adapters == []


@pytest.mark.asyncio
async def test_completed_response_loss_readback_is_conservative_and_no_resubmit() -> None:
    request = _request()
    repository = FakeRepository(request, _bundle())
    repository.claim_outcome = "readback"
    repository.readback_status = "completed"
    service, adapters = _service(repository, [])

    frames = await _collect(service, request)

    assert [frame["type"] for frame in frames] == ["accepted", "unknown"]
    assert frames[-1]["ambiguity_kind"] == "GATEWAY_COMPLETED_READBACK_ONLY"
    assert adapters == []


@pytest.mark.asyncio
async def test_timeout_and_cancel_after_dispatch_are_unknown_and_closed() -> None:
    started = asyncio.Event()

    async def block() -> None:
        started.set()
        await asyncio.sleep(10)

    request = _request(timeout=10)
    repository = FakeRepository(request, _bundle())
    service, adapters = _service(repository, [block])
    task = asyncio.create_task(_collect(service, request))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert repository.finalize_calls[-1]["terminal_status"] == "unknown"
    assert adapters[0].closed

    timeout_request = _request(timeout=0.01)
    timeout_repository = FakeRepository(timeout_request, _bundle())
    timeout_service, timeout_adapters = _service(timeout_repository, [block])
    frames = await _collect(timeout_service, timeout_request)
    assert frames[-1]["type"] == "unknown"
    assert timeout_adapters[0].closed


@pytest.mark.asyncio
async def test_blocked_provider_stream_renews_fenced_lease() -> None:
    async def pause() -> None:
        await asyncio.sleep(0.035)

    request = _request()
    repository = FakeRepository(request, _bundle())
    service, _ = _service(
        repository,
        [pause, StreamChunk(content="done", finish_reason="stop")],
        renew_interval=0.01,
    )

    frames = await _collect(service, request)

    assert frames[-1]["type"] == "completed"
    assert repository.timeline.count("renew") >= 2
    assert repository.timeline.index("mark_dispatched") < repository.timeline.index("renew")
    assert repository.timeline.index("renew") < repository.timeline.index("finalize")


@pytest.mark.asyncio
async def test_drain_rejects_new_claim_and_health_is_low_cardinality() -> None:
    request = _request()
    repository = FakeRepository(request, _bundle())
    service, _ = _service(repository, [])
    service.drain()

    frames = await _collect(service, request)
    health = service.health({
        "db": "available", "kek": "available",
        "provider_registry": "available", "socket": "available",
        "request_id": SECRET,
    })

    assert frames == [{
        "type": "failed", "error_code": "GATEWAY_DRAINING",
        "retry_class": "terminal", "summary": "gateway request failed",
    }]
    assert repository.timeline == []
    assert health["ready"] is False and health["draining"] is True
    assert SECRET not in json.dumps(health)
    assert set(health["dependencies"]) == {
        "db", "kek", "provider_registry", "socket",
    }


@pytest.mark.asyncio
async def test_local_uds_frames_never_contain_secret_marker() -> None:
    request = _request()
    repository = FakeRepository(request, _bundle())
    service, _ = _service(
        repository, [StreamChunk(content="safe", finish_reason="stop")],
    )

    class Peer:
        def verify(self, _writer: object) -> bool:
            return True

    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        socket_path = Path(directory) / "gateway.sock"
        async with FakeModelGatewayServer(
            str(socket_path), service.complete, Peer(),
        ):
            frames = [item async for item in IsolatedModelGatewayClient(
                str(socket_path),
            ).complete(request)]

    serialized = json.dumps(frames) + repr(frames) + pickle.dumps(frames).hex()
    assert SECRET not in serialized
    assert [frame["type"] for frame in frames] == [
        "accepted", "delta", "delta", "completed",
    ]
