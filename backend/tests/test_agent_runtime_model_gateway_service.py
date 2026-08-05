from __future__ import annotations

import asyncio
import hashlib
import json
import pickle
from typing import Any, Mapping

import pytest

from services.adapters.types import StreamChunk, ToolCallDelta
from services.configuration.definitions import CONFIG_REGISTRY
from services.configuration.envelope import LocalKEKProvider
from services.configuration.material_service import SecretMaterialService
from services.agent.runtime.infrastructure.model.projection import resolve_model_revision
from services.agent.runtime.model_gateway.configuration import (
    GatewaySecretBundleConsumer,
)
from services.agent.runtime.model_gateway.provider import GatewayProviderExecutor
from services.agent.runtime.model_gateway.service import (
    ModelGatewayService,
)


SECRET = "gateway-provider-secret-77a1"
REQUEST_ID = "11111111-1111-1111-1111-111111111111"
OPERATION_ID = "22222222-2222-2222-2222-222222222222"
USER_ID = "33333333-3333-3333-3333-333333333333"
ORG_ID = "44444444-4444-4444-4444-444444444444"
RUN_ID = "55555555-5555-5555-5555-555555555555"
STEP_ID = "66666666-6666-6666-6666-666666666666"
ATTEMPT_ID = "77777777-7777-7777-7777-777777777777"
TOKEN = "88888888-8888-8888-8888-888888888888"
CLAIM_TOKEN = "99999999-9999-9999-9999-999999999999"
MODEL_ID = "qwen3.5-plus"
PROVIDER_REVISION = "credential-revision-2026-08-06"


def _material(seed: int = 4) -> SecretMaterialService:
    return SecretMaterialService(LocalKEKProvider(
        current_version="v1", keyring={"v1": bytes([seed]) * 32},
    ))


def _bundle(
    encryptor: SecretMaterialService | None = None,
) -> dict[str, object]:
    envelope = (encryptor or _material()).encrypt_payload(
        scope_kind="organization",
        scope_id=ORG_ID,
        secret_name="ai.dashscope_api_key",
        payload_version=1,
        payload={"api_key": SECRET},
    )
    return {
        "bundle": "ai.provider.dashscope",
        "definition_version": CONFIG_REGISTRY.version,
        "items": [{
            "key": "ai.dashscope.api_key", "required": True,
            "configured": True, "source": "organization",
            "scope_id": ORG_ID, "version": 1, "value_kind": "secret",
            "secret_ref": {
                "secret_name": "ai.dashscope_api_key",
                "payload_ciphertext": envelope.payload_ciphertext,
                "wrapped_dek": envelope.wrapped_dek,
                "kek_version": envelope.kek_version,
                "payload_version": envelope.payload_version,
            },
        }],
    }


def _request(*, timeout: float = 1.0) -> dict[str, object]:
    messages = [{"role": "user", "content": "private prompt"}]
    tools = [{
        "type": "function",
        "function": {"name": "lookup", "parameters": {"type": "object"}},
    }]
    context_hash = hashlib.sha256(json.dumps(
        {"messages": messages, "tools": tools},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return {
        "version": "agent-model-gateway.v1", "type": "request",
        "operation": "model.complete", "request_id": REQUEST_ID,
        "org_id": ORG_ID, "user_id": USER_ID, "run_id": RUN_ID,
        "model_step_id": STEP_ID, "model_attempt_id": ATTEMPT_ID,
        "worker_id": "runtime-worker", "execution_token": TOKEN,
        "request_hash": "a" * 64, "state_version": 0,
        "model_id": MODEL_ID, "provider": "dashscope",
        "model_revision": resolve_model_revision(MODEL_ID),
        "purpose": "model.invoke", "tenant_kill_epoch": 0,
        "provider_kill_epoch": 0, "capability_kill_epoch": 0,
        "deadline_ms": 120_000,
        "input": {
            "messages": messages, "tools": tools,
            "options": {"timeout_seconds": timeout},
            "context_receipt_hash": context_hash,
        },
    }


class FakeRepository:
    def __init__(self, request: dict[str, object], bundle: object) -> None:
        self.timeline: list[str] = []
        self.fail_db = False
        self.mark_outcome = "dispatching"
        self.claim_outcome = "claimed"
        self.read_outcome = "found"
        self.claim_provider_revision: str | None = None
        self.readback_status = "dispatching"
        self.request = request
        self.bundle = bundle
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.finalize_calls: list[dict[str, object]] = []

    def _operation(self, status: str, version: int) -> dict[str, object]:
        return {
            "operation_id": OPERATION_ID, "status": status,
            "state_version": version, "terminal_error_code": None,
            "ambiguity_code": None, "response_started": False,
            "provider_request_id": None,
            "request_id": self.request["request_id"],
            "org_id": self.request["org_id"],
            "user_id": self.request["user_id"],
            "run_id": self.request["run_id"],
            "model_step_id": self.request["model_step_id"],
            "model_attempt_id": self.request["model_attempt_id"],
            "execution_token": self.request["execution_token"],
            "request_hash": self.request["request_hash"],
            "attempt_state_version": self.request["state_version"],
            "model_id": self.request["model_id"],
            "provider": self.request["provider"],
            "provider_revision": PROVIDER_REVISION,
            "model_revision": self.request["model_revision"],
            "purpose": self.request["purpose"],
            "tenant_kill_epoch": self.request["tenant_kill_epoch"],
            "provider_kill_epoch": self.request["provider_kill_epoch"],
            "capability_kill_epoch": self.request["capability_kill_epoch"],
        }

    def _record(self, name: str, kwargs: dict[str, object]) -> None:
        self.timeline.append(name)
        self.calls.append((name, dict(kwargs)))
        if name not in {"read"}:
            assert kwargs["provider_revision"] == PROVIDER_REVISION

    async def read(self, **kwargs: object) -> dict[str, object]:
        self.timeline.append("read")
        self.calls.append(("read", dict(kwargs)))
        assert kwargs == {
            key: self.request[key] for key in (
                "request_id", "org_id", "user_id", "run_id",
                "model_attempt_id", "execution_token", "request_hash",
            )
        }
        return {
            "outcome": self.read_outcome,
            "operation": self._operation("submitted", 0),
        }

    async def claim(self, **kwargs: object) -> dict[str, object]:
        self._record("claim", kwargs)
        assert kwargs["model_revision"] == self.request["model_revision"]
        assert kwargs["model_revision"] != kwargs["provider_revision"]
        if self.claim_outcome == "readback":
            operation = self._operation(self.readback_status, 2)
            if self.claim_provider_revision is not None:
                operation["provider_revision"] = self.claim_provider_revision
            return {
                "outcome": "readback",
                "operation": operation,
            }
        operation = self._operation("claimed", 1)
        if self.claim_provider_revision is not None:
            operation["provider_revision"] = self.claim_provider_revision
        return {
            "outcome": self.claim_outcome,
            "claim_token": CLAIM_TOKEN,
            "operation": operation,
            "input_receipt": {
                "request_hash": self.request["request_hash"],
                "prefix_hash": self.request["input"]["context_receipt_hash"],
                "message_count": len(self.request["input"]["messages"]),
                "tool_count": len(self.request["input"]["tools"]),
            },
            "encrypted_configuration_bundle": self.bundle,
        }

    async def fail_before_dispatch(self, **kwargs: object) -> dict[str, object]:
        self._record("fail_before_dispatch", kwargs)
        if self.fail_db:
            raise RuntimeError("database detail must not escape")
        operation = self._operation("failed", 2)
        operation["terminal_error_code"] = kwargs["error_code"]
        return {"outcome": "failed", "operation": operation}

    async def mark_dispatched(self, **kwargs: object) -> dict[str, object]:
        self._record("mark_dispatched", kwargs)
        if self.fail_db:
            raise RuntimeError("database detail must not escape")
        return {
            "outcome": self.mark_outcome,
            "operation": self._operation("dispatching", 2),
        }

    async def finalize(self, **kwargs: object) -> dict[str, object]:
        self._record("finalize", kwargs)
        self.finalize_calls.append(dict(kwargs))
        if self.fail_db:
            raise RuntimeError("database detail must not escape")
        status = str(kwargs["terminal_status"])
        operation = self._operation(status, 3)
        operation["terminal_error_code"] = kwargs["terminal_error_code"]
        operation["ambiguity_code"] = kwargs["ambiguity_code"]
        operation["response_started"] = kwargs["response_started"]
        operation["provider_request_id"] = kwargs["provider_request_id"]
        return {"outcome": status, "operation": operation}

    async def renew(self, **kwargs: object) -> dict[str, object]:
        self._record("renew", kwargs)
        return {
            "outcome": "renewed",
            "operation": self._operation("dispatching", 3),
        }


class FakeAdapter:
    def __init__(
        self, timeline: list[str], events: list[object], secret: str,
        close_waiter: asyncio.Event | None = None,
    ) -> None:
        self.timeline = timeline
        self.events = events
        self.secret: str | None = secret
        self.closed = False
        self.close_waiter = close_waiter

    async def stream_chat(self, **_kwargs: object):
        self.timeline.append("provider_call")
        for event in self.events:
            if isinstance(event, BaseException):
                raise event
            if callable(event):
                await event()
                continue
            yield event

    async def close(self) -> None:
        self.timeline.append("adapter_close")
        try:
            if self.close_waiter is not None:
                await self.close_waiter.wait()
        finally:
            self.secret = None
            self.closed = True


class ProviderFailure(RuntimeError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__("provider secret response detail")


def _service(
    repository: FakeRepository,
    events: list[object],
    *,
    decryptor: SecretMaterialService | None = None,
    build_fails: bool = False,
    renew_interval: float | None = None,
    close_waiter: asyncio.Event | None = None,
    close_timeout: float = 5.0,
) -> tuple[ModelGatewayService, list[FakeAdapter]]:
    adapters: list[FakeAdapter] = []

    def build(_model_id: str, *, api_key: str, **_kwargs: object) -> FakeAdapter:
        repository.timeline.append("provider_build")
        if build_fails:
            raise RuntimeError("builder secret detail")
        adapter = FakeAdapter(repository.timeline, events, api_key, close_waiter)
        adapters.append(adapter)
        return adapter

    return ModelGatewayService(
        repository,
        GatewaySecretBundleConsumer(decryptor or _material()),
        GatewayProviderExecutor(build, close_timeout_seconds=close_timeout),
        worker_id="gateway-worker",
        release="a" * 40,
        renew_interval_seconds=renew_interval,
    ), adapters


@pytest.mark.asyncio
async def test_claim_dispatch_stream_finalize_and_close_exact_order() -> None:
    request = _request()
    repository = FakeRepository(request, _bundle())
    chunk = StreamChunk(
        content="answer", finish_reason="tool_calls",
        prompt_tokens=8, completion_tokens=3,
        tool_calls=[ToolCallDelta(0, "call-1", "lookup", '{"id":1}')],
    )
    chunk.provider_request_id = "provider-request-1"
    chunk.reasoning_tokens = 2
    service, adapters = _service(repository, [chunk])

    frames = [frame async for frame in service.complete(request)]

    assert [frame["type"] for frame in frames] == [
        "accepted", "delta", "delta", "delta", "delta", "completed",
    ]
    assert repository.timeline == [
        "read", "claim", "provider_build", "mark_dispatched", "provider_call",
        "finalize", "adapter_close",
    ]
    assert frames[-1]["tool_calls"][0]["arguments"] == '{"id":1}'
    assert frames[-1]["usage"] == {
        "input_tokens": 8, "output_tokens": 3,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
    }
    db_usage = repository.finalize_calls[-1]["usage_summary"]
    assert db_usage == {
        "input_tokens": 8, "output_tokens": 3,
        "reasoning_tokens": 2, "total_tokens": 11,
    }
    assert set(db_usage) <= {
        "input_tokens", "output_tokens", "reasoning_tokens", "total_tokens",
        "credits", "unit",
    }
    assert [name for name, _ in repository.calls[:2]] == ["read", "claim"]
    assert all(
        kwargs["provider_revision"] == PROVIDER_REVISION
        for name, kwargs in repository.calls if name != "read"
    )
    assert adapters[0].closed and adapters[0].secret is None
    public = json.dumps(frames) + repr(frames) + pickle.dumps(frames).hex()
    public += json.dumps(repository.finalize_calls, default=str)
    assert SECRET not in public


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("events", "status"),
    (([ProviderFailure(400)], "failed"),
     ([StreamChunk(content="partial"), ProviderFailure(503)], "unknown")),
)
async def test_provider_failure_is_persisted_before_stable_terminal(
    events: list[object], status: str,
) -> None:
    request = _request()
    repository = FakeRepository(request, _bundle())
    service, adapters = _service(repository, events)

    frames = [frame async for frame in service.complete(request)]

    assert frames[-1]["type"] == status
    assert repository.finalize_calls[-1]["terminal_status"] == status
    assert repository.timeline.index("mark_dispatched") < repository.timeline.index("provider_call")
    assert repository.timeline.index("finalize") < repository.timeline.index("adapter_close")
    assert adapters[0].closed
    assert "provider secret response detail" not in json.dumps(frames)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("decrypt", "builder"))
async def test_predispatch_failure_fact_precedes_failed_frame(failure: str) -> None:
    request = _request()
    bundle = _bundle(_material(9)) if failure == "decrypt" else _bundle()
    repository = FakeRepository(request, bundle)
    service, _ = _service(
        repository, [], build_fails=failure == "builder",
    )

    frames = [frame async for frame in service.complete(request)]

    assert [frame["type"] for frame in frames] == ["accepted", "failed"]
    assert repository.timeline[-1] == "fail_before_dispatch"
    assert "mark_dispatched" not in repository.timeline
    assert "provider_call" not in repository.timeline


@pytest.mark.asyncio
async def test_predispatch_fact_write_failure_aborts_without_fake_terminal() -> None:
    request = _request()
    repository = FakeRepository(request, _bundle(_material(9)))
    repository.fail_db = True
    service, _ = _service(repository, [])
    stream = service.complete(request)

    assert (await anext(stream))["type"] == "accepted"
    with pytest.raises(asyncio.CancelledError):
        await anext(stream)


@pytest.mark.asyncio
async def test_mark_failure_never_calls_provider() -> None:
    request = _request()
    repository = FakeRepository(request, _bundle())
    repository.fail_db = True
    service, adapters = _service(repository, [StreamChunk(content="never")])
    stream = service.complete(request)

    assert (await anext(stream))["type"] == "accepted"
    with pytest.raises(asyncio.CancelledError):
        await anext(stream)
    assert "provider_call" not in repository.timeline
    assert adapters[0].closed


@pytest.mark.asyncio
async def test_dispatch_readback_does_not_submit_provider_again() -> None:
    request = _request()
    repository = FakeRepository(request, _bundle())
    repository.claim_outcome = "readback"
    service, adapters = _service(repository, [StreamChunk(content="never")])

    frames = [frame async for frame in service.complete(request)]

    assert [frame["type"] for frame in frames] == ["accepted", "unknown"]
    assert frames[-1]["reconcile_only"] is True
    assert adapters == []
    assert "provider_call" not in repository.timeline


@pytest.mark.asyncio
async def test_busy_is_readback_only_and_never_terminal_or_dispatched() -> None:
    request = _request()
    repository = FakeRepository(request, _bundle())
    repository.claim_outcome = "busy"
    service, adapters = _service(repository, [StreamChunk(content="never")])

    frames = await _collect(service, request)

    assert frames == [
        {"type": "accepted", "operation_id": OPERATION_ID, "status": "readback"},
        {
            "type": "unknown", "ambiguity_kind": "GATEWAY_OPERATION_IN_FLIGHT",
            "response_started": False, "provider_request_id": None,
            "reconcile_only": True,
        },
    ]
    assert repository.timeline == ["read", "claim"]
    assert adapters == [] and repository.finalize_calls == []


@pytest.mark.asyncio
async def test_claim_revision_change_aborts_before_secret_or_provider() -> None:
    request = _request()
    repository = FakeRepository(request, _bundle())
    repository.claim_provider_revision = "different-credential-revision"
    service, adapters = _service(repository, [StreamChunk(content="never")])

    with pytest.raises(asyncio.CancelledError):
        await _collect(service, request)

    assert repository.timeline == ["read", "claim"]
    assert adapters == [] and repository.finalize_calls == []


async def _collect(
    service: ModelGatewayService, request: dict[str, object],
) -> list[Mapping[str, Any]]:
    return [frame async for frame in service.complete(request)]
