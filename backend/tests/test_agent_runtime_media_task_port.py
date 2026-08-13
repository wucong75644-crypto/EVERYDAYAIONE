from types import SimpleNamespace

import pytest

from services.agent.runtime.domain import RuntimeScope, ScopeKind
from services.agent.runtime.executors.provider_adapters import KieMediaProvider
from services.agent.runtime.executors.specialist_contracts import ProviderReceipt, ProviderState
from services.agent.runtime.media_task_port import RuntimeMediaTaskPort


class _RPC:
    def __init__(self, data):
        self.data = data

    async def execute(self):
        return self


class _DB:
    def __init__(self):
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _RPC({"task_id": params.get("p_task_id"), "already_attached": False})


def _attempt():
    return SimpleNamespace(
        attempt_id="attempt-1", action_id="action-1", run_id="run-1",
        request_hash="a" * 64,
        scope=RuntimeScope(
            kind=ScopeKind.USER, scope_id="user-1", user_id="user-1", org_id="org-1",
        ),
    )


@pytest.mark.asyncio
async def test_runtime_media_task_port_attaches_using_existing_lifecycle_rpc():
    db = _DB()
    port = RuntimeMediaTaskPort(db)
    attempt = _attempt()
    request = {
        "kind": "image", "task_id": "local-task-1", "user_id": "user-1",
        "org_id": "org-1", "credit_transaction_id": "credit-tx-1",
        "prompt": "test", "model": "model-1",
    }
    prepared = await port.prepare(attempt, request, kind="image")
    result = await port.attach(
        attempt, request,
        ProviderReceipt(
            state=ProviderState.ACCEPTED, provider="kie", request_hash="a" * 64,
            provider_task_ref="kie-task-1",
        ),
        kind="image",
    )

    assert prepared["task_id"] == "local-task-1"
    assert result["external_task_id"] == "kie-task-1"
    assert db.calls[0][0] == "attach_generation_external_task"
    assert db.calls[0][1]["p_credit_transaction_id"] == "credit-tx-1"
    assert "p_user_id" not in db.calls[0][1]
    assert db.calls[0][1]["p_actual_request_params"].obj == {
        "kind": "image", "prompt": "test", "model": "model-1",
    }


@pytest.mark.asyncio
async def test_runtime_media_task_port_rejects_scope_conflict_and_missing_identity():
    port = RuntimeMediaTaskPort(_DB())
    with pytest.raises(RuntimeError, match="TASK_ID_REQUIRED"):
        await port.prepare(_attempt(), {"kind": "image"}, kind="image")
    with pytest.raises(RuntimeError, match="ORG_SCOPE_CONFLICT"):
        await port.prepare(
            _attempt(), {
                "kind": "image", "task_id": "task-1", "credit_transaction_id": "tx-1",
                "org_id": "other-org",
            }, kind="image",
        )


@pytest.mark.asyncio
async def test_kie_provider_turns_attach_failure_into_unknown_without_retry():
    class _Transport:
        calls = 0

        async def request(self, **_kwargs):
            self.calls += 1
            return {"state": "accepted", "provider_task_ref": "kie-task-1"}

    class _Port:
        async def prepare(self, attempt, request, *, kind):
            return {"task_id": "task-1"}

        async def attach(self, attempt, request, receipt, *, kind):
            raise RuntimeError("lifecycle unavailable")

    transport = _Transport()
    provider = KieMediaProvider(transport, kind="image", task_port=_Port())
    receipt = await provider.submit(
        _attempt(), {"kind": "image", "prompt": "test"}, idempotency_key="key-1",
    )

    assert receipt.state is ProviderState.UNKNOWN
    assert receipt.provider_task_ref == "kie-task-1"
    assert receipt.evidence["error_code"] == "MEDIA_TASK_ATTACH_UNKNOWN"
    assert transport.calls == 1
