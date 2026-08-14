from types import SimpleNamespace

import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.domain import RuntimeScope, ScopeKind
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

    def __init__(self, *, kind="image", source="model_loop", retry=False):
        self.calls = []
        self.kind = kind
        self.source = source
        self.retry = retry

    def rpc(self, name, params):
        self.calls.append((name, params))
        if name == "prepare_agent_runtime_media_dispatch_v1":
            return _RPC({"outcome": "prepared"})
        if name == "read_agent_runtime_media_retry_binding_v1":
            return _RPC({
                "outcome": "found" if self.retry else "not_retry",
                "binding": {"action_id": "action-1", "task_id": "task-1"},
            })
        if name == "read_agent_runtime_media_provider_request_v1":
            return _RPC({
                "outcome": "found", "kind": self.kind, "source": self.source,
                "provider_request": {"model": "gpt-image-2-image-to-image", "input": {
                    "prompt": "test", "input_urls": ["https://cdn.example/ref.png"],
                    "aspect_ratio": "1:1", "resolution": "1K",
                }}, "provider_request_hash": "e" * 64,
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
async def test_runtime_media_task_port_prepares_and_reads_server_provider_request():
    db = _DB()
    port = RuntimeMediaTaskPort(db)

    prepared = await port.prepare(_attempt(), kind="image")
    readback = await port.read(_attempt(), kind="image")

    assert prepared["provider_request_hash"] == "e" * 64
    assert readback["provider_request"]["model"] == "gpt-image-2-image-to-image"
    assert "task_id" not in readback["provider_request"]
    assert [name for name, _ in db.calls] == [
        "read_agent_runtime_media_retry_binding_v1",
        "prepare_agent_runtime_media_dispatch_v1",
        "read_agent_runtime_media_provider_request_v1",
        "read_agent_runtime_media_provider_request_v1",
    ]
    retry_params = db.calls[0][1]
    assert retry_params["p_execution_token"] == "token-1"
    assert "p_owner_token" not in retry_params


@pytest.mark.asyncio
async def test_runtime_media_task_port_uses_precreated_retry_binding() -> None:
    db = _DB(retry=True)
    prepared = await RuntimeMediaTaskPort(db).prepare(_attempt(), kind="image")

    assert prepared["provider_request_hash"] == "e" * 64
    assert [name for name, _ in db.calls] == [
        "read_agent_runtime_media_retry_binding_v1",
        "read_agent_runtime_media_provider_request_v1",
    ]
    assert "prepare_agent_runtime_media_dispatch_v1" not in {
        name for name, _ in db.calls
    }


@pytest.mark.asyncio
async def test_runtime_media_task_port_rejects_unscoped_database_and_bad_kind():
    with pytest.raises(ValueError, match="WORKER_SCOPED"):
        RuntimeMediaTaskPort(SimpleNamespace())
    with pytest.raises(RuntimeError, match="KIND_NOT_SUPPORTED"):
        await RuntimeMediaTaskPort(_DB()).prepare(_attempt(), kind="audio")


@pytest.mark.asyncio
async def test_runtime_media_task_port_supports_prepared_video():
    db = _DB(kind="video", source="media_ingress")
    result = await RuntimeMediaTaskPort(db).prepare(_attempt(), kind="video")
    assert result["kind"] == "video"
    assert result["source"] == "media_ingress"
    assert db.calls[0][1]["p_execution_token"] == "token-1"
    assert db.calls[1][1]["p_owner_token"] == "token-1"


@pytest.mark.asyncio
async def test_runtime_media_task_port_supports_model_loop_video():
    db = _DB(kind="video", source="model_loop")
    result = await RuntimeMediaTaskPort(db).prepare(_attempt(), kind="video")

    assert result["kind"] == "video"
    assert result["source"] == "model_loop"
    assert [name for name, _ in db.calls] == [
        "read_agent_runtime_media_retry_binding_v1",
        "prepare_agent_runtime_media_dispatch_v1",
        "read_agent_runtime_media_provider_request_v1",
    ]


@pytest.mark.asyncio
async def test_runtime_media_task_port_rejects_invalid_provider_request():
    db = _DB()
    original_rpc = db.rpc

    def drifted_rpc(name, params):
        rpc = original_rpc(name, params)
        if name == "read_agent_runtime_media_provider_request_v1":
            rpc.data = {
                "outcome": "found", "kind": "image", "source": "model_loop",
                "provider_request": {"task_id": "internal"},
                "provider_request_hash": "f" * 64,
            }
        return rpc

    db.rpc = drifted_rpc
    with pytest.raises(RuntimeError, match="RESPONSE_INVALID"):
        await RuntimeMediaTaskPort(db).read(_attempt(), kind="image")
