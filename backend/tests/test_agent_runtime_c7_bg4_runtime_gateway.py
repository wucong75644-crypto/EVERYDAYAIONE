"""C7-BG4 Runtime-to-Gateway production boundary contracts."""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.agent.runtime.application.model_loop import (
    ModelLoopDriver, PreparedModelCall,
)
from services.agent.runtime.context import ProviderContextPlan
from services.agent.runtime.domain import ModelStepId, StopReason
from services.agent.runtime.infrastructure.model.response import canonical_response_hash
from services.agent.runtime.model_gateway.protocol import GatewayProtocolError, VERSION
from services.agent.runtime.model_gateway.runtime_client import ModelGatewayClient
from services.agent.runtime.model_gateway.server import FakeModelGatewayServer
from services.agent.runtime.ports.model import (
    ModelCallUnknownError, ModelInputReceipt, ModelProviderError,
    ModelOutput, ModelOutputKind, ModelRequestOptions, ModelStepRequest,
    ModelToolCall, ModelUsage,
)
from services.agent.runtime.ports.model_gateway import (
    ModelGatewayDispatchBinding, ModelGatewayDispatchOutcome,
    ModelGatewayDispatchReceipt,
)
from services.agent.runtime.ports.coordinator_recovery import RunAggregateSnapshot
from services.agent.runtime.ports.model_attempt import (
    ModelAttemptOutcome, ModelAttemptReceipt,
)


IDS = {
    "operation_id": "11111111-1111-4111-8111-111111111111",
    "request_id": "22222222-2222-4222-8222-222222222222",
    "org_id": "33333333-3333-4333-8333-333333333333",
    "user_id": "44444444-4444-4444-8444-444444444444",
    "session_id": "55555555-5555-4555-8555-555555555555",
    "run_id": "66666666-6666-4666-8666-666666666666",
    "step_id": "77777777-7777-4777-8777-777777777777",
    "attempt_id": "88888888-8888-4888-8888-888888888888",
    "execution_token": "99999999-9999-4999-8999-999999999999",
}
HASH = "a" * 64


def _binding() -> ModelGatewayDispatchBinding:
    return ModelGatewayDispatchBinding(
        operation_id=IDS["operation_id"], request_id=IDS["request_id"],
        org_id=IDS["org_id"], user_id=IDS["user_id"],
        session_id=IDS["session_id"], run_id=IDS["run_id"],
        model_step_id=IDS["step_id"], model_attempt_id=IDS["attempt_id"],
        worker_id="runtime-worker", execution_token=IDS["execution_token"],
        request_hash=HASH, attempt_state_version=4,
        model_id="qwen3.5-plus", provider="dashscope",
        provider_revision="provider-revision-db-only",
        model_revision="model-revision-1", purpose="model.invoke",
        tenant_kill_epoch=7, provider_kill_epoch=8,
        capability_kill_epoch=9,
    )


def _request(binding: ModelGatewayDispatchBinding | None = None) -> ModelStepRequest:
    plan = ProviderContextPlan.build(
        messages=[{"role": "user", "content": "safe fixture"}],
        tools=[{"type": "function", "function": {
            "name": "search", "parameters": {"type": "object"},
        }}],
        context_epoch_id="context-1", model_step=1, stable_prefix_blocks=0,
    )
    return ModelStepRequest(
        model_step_id=ModelStepId(IDS["step_id"]), model_id="qwen3.5-plus",
        request_hash=HASH,
        input_receipt=ModelInputReceipt(
            receipt_id="receipt-1", receipt_hash="c" * 64,
            context_plan_hash=plan.plan_hash,
        ),
        context_plan=plan, model_revision="model-revision-1",
        prompt_revision="prompt-1", tool_catalog_revision="tools-1",
        options=ModelRequestOptions(timeout_seconds=30), org_id=IDS["org_id"],
        gateway_binding=binding,
    )


def _operation(*, status: str = "completed") -> dict[str, object]:
    binding = _binding()
    value = {
        key: getattr(binding, key) for key in binding.__dataclass_fields__
        if key != "worker_id"
    }
    value.update({
        "status": status, "state_version": 6,
        "response_started": status == "completed",
        "provider_request_id": "provider-request-1" if status == "completed" else None,
        "response_hash": _completed()["response_hash"] if status == "completed" else None,
        "usage_summary": (
            {"input_tokens": 11, "output_tokens": 5,
             "reasoning_tokens": 2, "total_tokens": 16}
            if status == "completed" else {}
        ),
        "terminal_error_code": "GATEWAY_CONFIGURATION_INVALID"
        if status == "failed" else None,
        "ambiguity_code": "GATEWAY_DISCONNECT" if status == "unknown" else None,
    })
    return value


def _accepted() -> dict[str, object]:
    return {
        "version": VERSION, "request_id": IDS["request_id"], "sequence": 0,
        "type": "accepted", "operation_id": IDS["operation_id"],
        "status": "claimed",
    }


def _completed() -> dict[str, object]:
    frame: dict[str, object] = {
        "version": VERSION, "request_id": IDS["request_id"], "sequence": 1,
        "type": "completed", "text": "done",
        "tool_calls": [{
            "index": 0, "call_id": "runtime-call-1",
            "provider_call_id": "provider-call-1", "name": "search",
            "arguments": '{"query":"safe"}',
        }],
        "usage": {
            "input_tokens": 11, "output_tokens": 5,
            "cache_read_tokens": 3, "cache_write_tokens": 0,
        },
        "stop_reason": "tool_calls", "provider_stop_reason": "tool_calls",
        "provider_request_id": "provider-request-1",
        "response_hash": "", "operation_state_version": 6,
    }
    frame["response_hash"] = _frame_hash(frame)
    return frame


def _frame_hash(frame: dict[str, object]) -> str:
    wire_usage = frame["usage"]
    assert isinstance(wire_usage, dict)
    usage = ModelUsage(
        input_tokens=wire_usage.get("input_tokens", 0),
        output_tokens=wire_usage.get("output_tokens", 0),
        reasoning_tokens=2,
        cache_read_tokens=wire_usage.get("cache_read_tokens", 0),
        cache_write_tokens=wire_usage.get("cache_write_tokens", 0),
    )
    wire_calls = frame["tool_calls"]
    assert isinstance(wire_calls, list)
    calls = tuple(ModelToolCall(
        index=call["index"], call_id=call["call_id"], name=call["name"],
        arguments_json=call["arguments"], provider_call_id=call["provider_call_id"],
    ) for call in wire_calls)
    text = frame["text"]
    assert isinstance(text, str)
    output = ModelOutput(kind=ModelOutputKind.TEXT, content=text) if text else None
    return canonical_response_hash(
        stop_reason=StopReason(str(frame["stop_reason"])),
        provider_stop_reason=frame["provider_stop_reason"],
        output=output, tool_calls=calls, usage=usage,
    )


def _sync_completed_fact(
    frame: dict[str, object], operation: dict[str, object],
) -> None:
    frame["response_hash"] = _frame_hash(frame)
    operation["response_hash"] = frame["response_hash"]


class _Repository:
    def __init__(self, operation: dict[str, object]) -> None:
        self.operation = operation
        self.reads: list[dict[str, object]] = []

    async def read(self, **binding: object) -> dict[str, object]:
        self.reads.append(binding)
        return {"outcome": "found", "operation": self.operation}


class _Transport:
    def __init__(self, frames=(), error: Exception | None = None) -> None:
        self.frames = frames
        self.error = error
        self.requests: list[dict[str, object]] = []

    async def complete(self, request):
        self.requests.append(request)
        for frame in self.frames:
            yield frame
        if self.error:
            raise self.error


def _client(repository: _Repository, transport: _Transport) -> ModelGatewayClient:
    client = ModelGatewayClient("/tmp/model-gateway.sock", repository)
    client._transport = transport
    return client


@pytest.fixture
def socket_dir():
    with tempfile.TemporaryDirectory(
        prefix="c7-bg4-", dir="/private/tmp",
    ) as directory:
        yield Path(directory)


class _PeerVerifier:
    def verify(self, _writer) -> bool:
        return True


class _Observer:
    def __init__(self) -> None:
        self.provider_ids = []

    async def response_started(self, *, provider, provider_request_id) -> None:
        assert provider == "dashscope"
        self.provider_ids.append(provider_request_id)


@pytest.mark.asyncio
async def test_runtime_client_full_uds_roundtrip(socket_dir) -> None:
    captured = []

    async def handler(payload):
        captured.append(payload)
        yield {
            "type": "accepted", "operation_id": IDS["operation_id"],
            "status": "claimed",
        }
        completed = _completed()
        for key in ("version", "request_id", "sequence"):
            completed.pop(key)
        yield completed

    path = socket_dir / "runtime-gateway.sock"
    repository = _Repository(_operation())
    async with FakeModelGatewayServer(str(path), handler, _PeerVerifier()):
        result = await ModelGatewayClient(str(path), repository).complete(
            _request(_binding()),
        )
    assert result.stop_reason is StopReason.TOOL_CALLS
    assert captured[0]["request_id"] == IDS["request_id"]
    assert "provider_revision" not in captured[0]


@pytest.mark.asyncio
async def test_completed_roundtrip_uses_only_durable_binding_and_db_terminal() -> None:
    repository = _Repository(_operation())
    transport = _Transport((_accepted(), _completed()))
    result = await _client(repository, transport).complete(_request(_binding()))

    assert result.stop_reason is StopReason.TOOL_CALLS
    assert result.output and result.output.content == "done"
    assert result.tool_calls[0].arguments_json == '{"query":"safe"}'
    assert result.tool_calls[0].call_id == "runtime-call-1"
    assert result.tool_calls[0].provider_call_id == "provider-call-1"
    assert result.usage.as_tuple() == (11, 5, 2, 3, 0)
    assert result.response_receipt.provider_request_id == "provider-request-1"
    assert result.response_hash == transport.frames[-1]["response_hash"]
    assert result.response_hash == repository.operation["response_hash"]
    payload = transport.requests[0]
    assert payload["state_version"] == 4
    assert payload["tenant_kill_epoch"] == 7
    assert payload["provider"] == "dashscope"
    assert "provider_revision" not in payload
    assert "provider-revision-db-only" not in json.dumps(payload)
    assert set(repository.reads[0]) == {
        "request_id", "org_id", "user_id", "run_id", "model_attempt_id",
        "execution_token", "request_hash",
    }


@pytest.mark.asyncio
async def test_response_start_observer_receives_late_provider_id() -> None:
    completed = _completed()
    completed["sequence"] = 3
    transport = _Transport((
        _accepted(),
        {
            "version": VERSION, "request_id": IDS["request_id"], "sequence": 1,
            "type": "delta", "delta_kind": "text", "delta": {"text": "done"},
        },
        {
            "version": VERSION, "request_id": IDS["request_id"], "sequence": 2,
            "type": "delta", "delta_kind": "provider_metadata",
            "delta": {"provider_request_id": "provider-request-1"},
        },
        completed,
    ))
    observer = _Observer()
    await _client(_Repository(_operation()), transport).complete(
        _request(_binding()), observer=observer,
    )
    assert observer.provider_ids == [None, "provider-request-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ("disconnect", "terminal_loss", "unknown"))
async def test_ambiguous_transport_and_terminal_loss_are_unknown_without_retry(mode) -> None:
    operation = _operation(status="unknown" if mode == "unknown" else "completed")
    frames = (_accepted(),) if mode != "unknown" else (
        _accepted(), {
            "version": VERSION, "request_id": IDS["request_id"], "sequence": 1,
            "type": "unknown", "ambiguity_kind": "GATEWAY_DISCONNECT",
            "response_started": True, "provider_request_id": None,
            "reconcile_only": True,
        },
    )
    error = GatewayProtocolError("GATEWAY_CONNECT_FAILED") if mode == "disconnect" else None
    transport = _Transport(frames, error)
    with pytest.raises(ModelCallUnknownError):
        await _client(_Repository(operation), transport).complete(_request(_binding()))
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_db_proven_predispatch_failure_is_provider_error() -> None:
    transport = _Transport(error=GatewayProtocolError("GATEWAY_CONNECT_FAILED"))
    with pytest.raises(ModelProviderError) as caught:
        await _client(_Repository(_operation(status="failed")), transport).complete(
            _request(_binding()),
        )
    assert caught.value.attempts[0].response_started is False
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_db_proven_postdispatch_failure_preserves_response_started() -> None:
    operation = _operation(status="failed")
    operation.update(
        response_started=True, provider_request_id="provider-request-before-failure",
    )
    with pytest.raises(ModelProviderError) as caught:
        await _client(_Repository(operation), _Transport((_accepted(),))).complete(
            _request(_binding()),
        )
    assert caught.value.attempts[0].response_started is True
    assert caught.value.attempts[0].provider_request_id == (
        "provider-request-before-failure"
    )


@pytest.mark.asyncio
async def test_missing_or_conflicting_binding_fails_closed_before_uds() -> None:
    transport = _Transport()
    client = _client(_Repository(_operation()), transport)
    with pytest.raises(ModelProviderError):
        await client.complete(_request())
    with pytest.raises(ModelCallUnknownError):
        await client.complete(_request(replace(_binding(), request_hash="d" * 64)))
    assert not transport.requests


@pytest.mark.asyncio
async def test_cancellation_propagates_without_readback_or_redispatch() -> None:
    repository = _Repository(_operation(status="unknown"))
    transport = _Transport(error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await _client(repository, transport).complete(_request(_binding()))
    assert len(transport.requests) == 1
    assert not repository.reads


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ("sequence", "request_id"))
async def test_protocol_sequence_and_request_mismatch_fail_closed(field) -> None:
    completed = _completed()
    completed[field] = 9 if field == "sequence" else IDS["user_id"]
    transport = _Transport((_accepted(), completed))
    with pytest.raises(ModelCallUnknownError):
        await _client(_Repository(_operation()), transport).complete(_request(_binding()))
    assert len(transport.requests) == 1


class _Attempts:
    def __init__(self) -> None:
        self.direct_calls = []

    async def start_dispatch(self, **values):
        self.direct_calls.append(values)
        return ModelAttemptReceipt(
            outcome=ModelAttemptOutcome.DISPATCHING, state_version=5,
        )


class _GatewayRepository:
    def __init__(self) -> None:
        self.calls = []

    async def start_dispatch(self, **values):
        self.calls.append(values)
        binding = replace(
            _binding(), request_id=values["request_id"],
            model_attempt_id=values["model_attempt_id"],
        )
        return ModelGatewayDispatchReceipt(
            outcome=ModelGatewayDispatchOutcome.DISPATCHING, binding=binding,
        )


def _plan(request: ModelStepRequest) -> PreparedModelCall:
    return PreparedModelCall(
        model_id=request.model_id, provider="dashscope",
        model_revision=request.model_revision, prompt_revision="prompt-1",
        tool_catalog_revision="tools-1",
        request_receipt={
            "credential_revision": "provider-revision-db-only",
            "credential_purpose": "model.invoke",
        },
        reserved_credits=1, build_request=lambda _step: request,
        actual_credits=lambda _result: 1,
        build_actions=lambda _result: ("0" * 64, ()),
    )


def _loop(model, attempts, gateway=None) -> ModelLoopDriver:
    return ModelLoopDriver(
        runtime_repository=object(), attempt_repository=attempts,
        action_repository=object(), recovery_repository=object(), model=model,
        call_factory=lambda _snapshot: None, reconciler=lambda _snapshot: None,
        gateway_dispatch_repository=gateway,
    )


@pytest.mark.asyncio
async def test_gateway_dispatch_is_atomic_and_direct_path_stays_compatible() -> None:
    request = _request()
    snapshot = RunAggregateSnapshot(
        run={"session_id": IDS["session_id"]}, latest_model_step=None,
        unresolved_model_attempt=None, latest_model_result=None,
        model_steps=(), actions=(),
    )
    attempts = _Attempts()
    gateway = _GatewayRepository()
    gateway_model = SimpleNamespace(requires_gateway_dispatch=True)
    loop = _loop(gateway_model, attempts, gateway)
    first = await loop._start_dispatch(
        snapshot=snapshot, plan=_plan(request), request=request,
        attempt_id=IDS["attempt_id"], attempt_version=3,
        run_id=IDS["run_id"], run_execution_token=IDS["execution_token"],
    )
    second = await loop._start_dispatch(
        snapshot=snapshot, plan=_plan(request), request=request,
        attempt_id=IDS["attempt_id"], attempt_version=3,
        run_id=IDS["run_id"], run_execution_token=IDS["execution_token"],
    )
    assert not attempts.direct_calls
    assert first and first[0].gateway_binding is not None
    assert gateway.calls[0]["request_id"] == gateway.calls[1]["request_id"]
    assert gateway.calls[0]["provider_revision"] == "provider-revision-db-only"

    direct_attempts = _Attempts()
    direct_model = SimpleNamespace()
    direct = await _loop(direct_model, direct_attempts)._start_dispatch(
        snapshot=snapshot, plan=_plan(request), request=request,
        attempt_id=IDS["attempt_id"], attempt_version=3,
        run_id=IDS["run_id"], run_execution_token=IDS["execution_token"],
    )
    assert direct and direct[0].gateway_binding is None
    assert len(direct_attempts.direct_calls) == 1


def test_request_and_public_receipts_contain_no_secret_material() -> None:
    request = _request(_binding())
    public = repr(request) + json.dumps(_client(
        _Repository(_operation()), _Transport(),
    )._transport.requests)
    for marker in (
        "raw-secret-marker", "api_key", "credential_material",
        "credential_handle", "provider-revision-db-only",
    ):
        assert marker not in public
