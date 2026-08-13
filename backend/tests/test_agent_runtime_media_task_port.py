from types import SimpleNamespace

import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.domain import RuntimeScope, ScopeKind
from services.agent.runtime.executors.provider_adapters import KieMediaProvider
from services.agent.runtime.executors.specialist_contracts import ProviderState
from services.agent.runtime.media_task_port import RuntimeMediaTaskPort


class _RPC:
    def __init__(self, data):
        self.data = data

    async def execute(self):
        return self


class _DB:
    scope = DatabaseScope(
        actor_user_id=None,
        org_id=None,
        access_kind=DatabaseAccessKind.AGENT_RUNTIME,
    )

    def __init__(self):
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        if name == "read_agent_runtime_media_manifest_v1":
            return _RPC({
                "outcome": "found",
                "reference_manifest_hash": "b" * 64,
                "input_image_count": 1,
            })
        binding = {
            "action_id": "action-1", "task_id": "task-1",
            "run_id": "run-1", "model_step_id": "step-1",
            "batch_hash": "c" * 64, "action_index": 0,
            "action_arguments_hash": "d" * 64,
            "action_request_hash": "a" * 64,
            "input_message_id": "input-1", "output_message_id": "output-1",
            "credit_transaction_id": "credit-1",
            "pricing_model_id": "gpt-image-2-image-to-image",
            "pricing_resolution": "1K", "provider_request_hash": "e" * 64,
            "unit_credits": 6,
            "reference_manifest_hash": "b" * 64,
        }
        if name == "prepare_agent_runtime_media_batch_v1":
            return _RPC({"outcome": "prepared", "binding": binding})
        if name == "read_agent_runtime_media_binding_v1":
            return _RPC({
                "outcome": "found", "binding": binding,
                "request_params": {
                    "prompt": "test", "model": "gpt-image-2-image-to-image",
                    "reference_image_indexes": [0],
                },
            })
        raise AssertionError(name)


def _attempt():
    return SimpleNamespace(
        attempt_id="attempt-1", action_id="action-1", run_id="run-1",
        session_id="session-1", worker_id="runtime-worker",
        state_version=3, request_hash="a" * 64,
        lease=SimpleNamespace(fencing_token="token-1"),
        scope=RuntimeScope(
            kind=ScopeKind.USER, scope_id="user-1",
            user_id="user-1", org_id="org-1",
        ),
    )


@pytest.mark.asyncio
async def test_runtime_media_task_port_prepares_and_reads_server_binding():
    db = _DB()
    port = RuntimeMediaTaskPort(db)

    prepared = await port.prepare(_attempt(), kind="image")
    readback = await port.read(_attempt(), kind="image")

    assert prepared["task_id"] == "task-1"
    assert readback["credit_transaction_id"] == "credit-1"
    assert not {
        "task_id", "user_id", "org_id", "credit_transaction_id",
    }.intersection(readback["request_params"])
    assert [name for name, _ in db.calls] == [
        "read_agent_runtime_media_manifest_v1",
        "prepare_agent_runtime_media_batch_v1",
        "read_agent_runtime_media_binding_v1",
    ]
    prepare_params = db.calls[1][1]
    assert prepare_params["p_action_id"] == "action-1"
    assert prepare_params["p_reference_manifest_hash"] == "b" * 64
    assert not {
        "task_id", "user_id", "org_id", "credit_transaction_id",
    }.intersection(prepare_params)


@pytest.mark.asyncio
async def test_runtime_media_task_port_rejects_unscoped_database_and_video():
    with pytest.raises(ValueError, match="WORKER_SCOPED"):
        RuntimeMediaTaskPort(SimpleNamespace())
    with pytest.raises(RuntimeError, match="KIND_NOT_SUPPORTED"):
        await RuntimeMediaTaskPort(_DB()).prepare(_attempt(), kind="video")


@pytest.mark.asyncio
async def test_kie_provider_never_sends_runtime_binding_facts():
    class _Transport:
        body = None

        async def request(self, **kwargs):
            self.body = kwargs["body"]
            return {"state": "accepted", "provider_task_ref": "kie-task-1"}

    class _Port:
        calls = 0

        async def prepare(self, attempt, *, kind):
            self.calls += 1
            return {"task_id": "must-not-leak", "credit_transaction_id": "secret"}

        async def read(self, attempt, *, kind):
            raise AssertionError("readback is not part of provider submit")

    transport = _Transport()
    port = _Port()
    provider = KieMediaProvider(transport, kind="image", task_port=port)
    request = {
        "kind": "image", "prompt": "test",
        "model": "gpt-image-2-text-to-image",
    }

    receipt = await provider.submit(_attempt(), request, idempotency_key="key-1")

    assert receipt.state is ProviderState.ACCEPTED
    assert port.calls == 1
    assert transport.body == request
    assert "runtime_task" not in transport.body
    assert not {
        "task_id", "user_id", "org_id", "credit_transaction_id",
    }.intersection(transport.body)
