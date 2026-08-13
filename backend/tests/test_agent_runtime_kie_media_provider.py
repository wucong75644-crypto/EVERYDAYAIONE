from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.agent.runtime.executors.specialist_contracts import (
    ProviderState, receipt_facts,
)
from services.agent.runtime.providers.kie_media import RuntimeKieMediaProvider
from services.agent.runtime.providers.kie_transport import KieHttpResponse


class FakeTransport:
    def __init__(self, *, submit=None, query=None, error: Exception | None = None):
        self.submit_response = submit or KieHttpResponse(
            status_code=200, payload={"code": 200, "data": {"taskId": "kie-1"}},
        )
        self.query_response = query or KieHttpResponse(
            status_code=200,
            payload={"code": 200, "data": {"taskId": "kie-1", "state": "waiting"}},
        )
        self.error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def submit(self, **kwargs):
        self.calls.append(("submit", kwargs))
        if self.error:
            raise self.error
        return self.submit_response

    async def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        if self.error:
            raise self.error
        return self.query_response


class FakeTaskPort:
    def __init__(self, kind: str = "image"):
        self.kind = kind
        self.calls: list[str] = []

    async def prepare(self, attempt, *, kind):
        self.calls.append("prepare")
        return self._facts(kind)

    async def read(self, attempt, *, kind):
        self.calls.append("read")
        return self._facts(kind)

    def _facts(self, kind):
        assert kind == self.kind
        return {
            "kind": kind, "source": "model_loop",
            "provider_request": {
                "model": "gpt-image-2-image-to-image",
                "input": {
                    "prompt": "make it blue",
                    "input_urls": ["https://cdn.example/reference.png"],
                    "aspect_ratio": "1:1", "resolution": "1K",
                },
            },
            "provider_request_hash": "b" * 64,
        }


class FakeCredentials:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.hashes: list[str] = []

    async def api_key(self, attempt, *, provider_request_hash):
        self.hashes.append(provider_request_hash)
        if self.error:
            raise self.error
        return "fixture-key"


def attempt():
    return SimpleNamespace(
        action_id="action-1", attempt_id="attempt-1",
        request_hash="a" * 64, idempotency_key="runtime-idempotency-1",
    )


def provider(transport, *, credentials=None, task_port=None):
    return RuntimeKieMediaProvider(
        transport, task_port=task_port or FakeTaskPort(),
        credentials=credentials or FakeCredentials(), kind="image",
        production_ready=True,
    )


@pytest.mark.asyncio
async def test_submit_is_single_shot_and_uses_only_server_provider_body():
    transport = FakeTransport()
    credentials = FakeCredentials()
    receipt = await provider(transport, credentials=credentials).submit(
        attempt(), {"prompt": "model supplied"}, idempotency_key="ignored",
    )

    assert receipt.state is ProviderState.ACCEPTED
    assert receipt.provider_task_ref == "kie-1"
    assert len(transport.calls) == 1
    assert transport.calls[0][0] == "submit"
    body = transport.calls[0][1]["body"]
    assert body["input"]["prompt"] == "make it blue"
    encoded = str(body)
    assert "fixture-key" not in encoded
    assert not any(name in encoded for name in (
        "task_id", "user_id", "org_id", "reserved_credits", "runtime_task",
    ))
    assert credentials.hashes == ["b" * 64]
    assert receipt.evidence["provider_request_hash"] == "b" * 64


@pytest.mark.asyncio
async def test_submit_timeout_is_unknown_and_never_retries():
    transport = FakeTransport(error=TimeoutError("uncertain"))
    receipt = await provider(transport).submit(
        attempt(), {}, idempotency_key="one-shot",
    )

    assert receipt.state is ProviderState.UNKNOWN
    assert receipt.evidence["error_code"] == "KIE_SUBMIT_RESULT_UNKNOWN"
    assert [name for name, _ in transport.calls] == ["submit"]


@pytest.mark.asyncio
async def test_deterministic_submit_rejection_is_failed():
    transport = FakeTransport(submit=KieHttpResponse(
        status_code=400, payload={"code": 422, "msg": "invalid"},
    ))
    receipt = await provider(transport).submit(
        attempt(), {}, idempotency_key="one-shot",
    )
    assert receipt.state is ProviderState.FAILED
    assert receipt.evidence["error_code"] == "KIE_SUBMIT_REJECTED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "result_json", "expected"),
    [
        ("queuing", None, ProviderState.ACCEPTED),
        ("generating", None, ProviderState.ACCEPTED),
        ("fail", None, ProviderState.FAILED),
        ("success", '{"resultUrls":["https://cdn.example/result.png"]}',
         ProviderState.COMPLETED),
    ],
)
async def test_reconcile_maps_only_kie_readback_states(
    state, result_json, expected,
):
    data = {"taskId": "kie-1", "state": state}
    if result_json is not None:
        data["resultJson"] = result_json
    transport = FakeTransport(query=KieHttpResponse(
        status_code=200, payload={"code": 200, "data": data},
    ))

    receipt = await provider(transport).reconcile(
        attempt(), {"provider_task_ref": "kie-1"},
    )

    assert receipt.state is expected
    assert [name for name, _ in transport.calls] == ["query"]
    if expected is ProviderState.COMPLETED:
        # 228_06 projection consumes image_urls/urls/images from Action result.
        assert receipt.result["image_urls"] == [
            "https://cdn.example/result.png",
        ]
        durable = receipt_facts(receipt)
        assert "image_urls" not in str(durable)
        assert "result_hash" in durable


@pytest.mark.asyncio
async def test_success_with_invalid_result_urls_stays_unknown():
    transport = FakeTransport(query=KieHttpResponse(
        status_code=200, payload={
            "code": 200,
            "data": {"taskId": "kie-1", "state": "success",
                     "resultJson": '{"resultUrls":["file:///secret"]}'},
        },
    ))
    receipt = await provider(transport).reconcile(
        attempt(), {"provider_task_ref": "kie-1"},
    )
    assert receipt.state is ProviderState.UNKNOWN
    assert receipt.evidence["error_code"] == "KIE_RESULT_URLS_AMBIGUOUS"


@pytest.mark.asyncio
async def test_cancel_requires_explicit_provider_confirmation():
    transport = FakeTransport()
    receipt = await provider(transport).cancel(
        attempt(), {"provider_task_ref": "kie-1"},
    )
    assert receipt.state is ProviderState.UNKNOWN
    assert receipt.evidence["error_code"] == "CANCEL_UNPROVEN"
    assert transport.calls == []


@pytest.mark.asyncio
async def test_missing_credential_fails_closed_before_network():
    transport = FakeTransport()
    receipt = await provider(
        transport, credentials=FakeCredentials(RuntimeError("missing")),
    ).submit(attempt(), {}, idempotency_key="blocked")
    assert receipt.state is ProviderState.FAILED
    assert receipt.evidence["error_code"] == "KIE_MEDIA_CONFIGURATION_UNAVAILABLE"
    assert transport.calls == []
