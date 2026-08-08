from __future__ import annotations

import asyncio
import os
import struct
import tempfile
from pathlib import Path
from uuid import UUID

import pytest

from services.agent.runtime.model_gateway.client import IsolatedModelGatewayClient
from services.agent.runtime.model_gateway.protocol import (
    REQUEST_FRAME_LIMIT, VERSION, GatewayProtocolError, encode_frame, read_frame,
)
from services.agent.runtime.model_gateway.server import FakeModelGatewayServer, LinuxPeerCredentialVerifier


def request_payload(seed: int = 1) -> dict[str, object]:
    def uid(offset: int) -> str:
        return str(UUID(int=seed * 100 + offset))

    return {
        "version": VERSION, "type": "request", "operation": "model.complete",
        "request_id": uid(1), "org_id": uid(2), "user_id": uid(3), "run_id": uid(4),
        "model_step_id": uid(5), "model_attempt_id": uid(6), "worker_id": "runtime-worker-1",
        "execution_token": uid(7), "request_hash": "a" * 64, "state_version": 1,
        "model_id": "model-v1", "provider": "fake", "model_revision": "revision-1",
        "purpose": "model.invoke", "tenant_kill_epoch": 0, "provider_kill_epoch": 0,
        "capability_kill_epoch": 0, "deadline_ms": 1000,
        "input": {"messages": [{"role": "user", "content": "hello"}], "tools": [],
                  "options": {}, "context_receipt_hash": "b" * 64},
    }


class FakePeerVerifier:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def verify(self, writer: asyncio.StreamWriter) -> bool:
        return self.allowed


@pytest.fixture
def socket_dir():
    with tempfile.TemporaryDirectory(prefix="c7-bg1-", dir="/private/tmp") as directory:
        yield Path(directory)


async def complete_handler(request):
    yield {"type": "accepted", "operation_id": str(UUID(int=800)), "status": "claimed"}
    yield {"type": "delta", "delta_kind": "text", "delta": {"text": "hello"}}
    yield {
        "type": "delta", "delta_kind": "tool_call",
        "delta": {"index": 0, "id": "provider-call-1", "name": "safe_read", "arguments": "{}"},
    }
    yield {"type": "delta", "delta_kind": "usage", "delta": {"input_tokens": 2, "output_tokens": 3}}
    yield {
        "type": "completed", "text": "hello",
        "tool_calls": [{"index": 0, "call_id": "call-1",
                        "provider_call_id": "provider-call-1",
                        "name": "safe_read", "arguments": "{}"}],
        "usage": {"input_tokens": 2, "output_tokens": 3},
        "stop_reason": "tool_calls", "provider_stop_reason": "tool_calls",
        "provider_request_id": "provider-1", "response_hash": "c" * 64,
        "operation_state_version": 2,
    }


@pytest.mark.asyncio
async def test_text_tool_usage_roundtrip_and_socket_cleanup(socket_dir: Path) -> None:
    path = socket_dir / "gateway.sock"
    server = FakeModelGatewayServer(str(path), complete_handler, FakePeerVerifier())
    async with server:
        assert path.exists() and (path.stat().st_mode & 0o777) == 0o660
        client = IsolatedModelGatewayClient(str(path))
        responses = [item async for item in client.complete(request_payload())]
    assert [item["type"] for item in responses] == ["accepted", "delta", "delta", "delta", "completed"]
    assert [item["sequence"] for item in responses] == list(range(5))
    assert not path.exists()
    assert client.production_ready is False and server.isolated_only is True


@pytest.mark.asyncio
async def test_personal_and_large_request_frames_roundtrip(socket_dir: Path) -> None:
    path = socket_dir / "large.sock"
    personal = request_payload()
    personal["org_id"] = None
    personal["deadline_ms"] = 5000
    personal["input"]["messages"] = [
        {"role": "user", "content": "x" * 400_000} for _ in range(3)
    ]
    encoded = encode_frame(personal, limit=REQUEST_FRAME_LIMIT)
    assert len(encoded) - 4 > 1024 * 1024
    async with FakeModelGatewayServer(str(path), complete_handler, FakePeerVerifier()):
        responses = [item async for item in IsolatedModelGatewayClient(str(path)).complete(personal)]
    assert responses[-1]["type"] == "completed"

    oversized = request_payload()
    oversized["input"]["messages"] = [
        {"role": "user", "content": "x" * 400_000} for _ in range(11)
    ]
    with pytest.raises(GatewayProtocolError, match="GATEWAY_REQUEST_TOO_LARGE"):
        _ = [item async for item in IsolatedModelGatewayClient(str(path)).complete(oversized)]


@pytest.mark.asyncio
async def test_concurrent_clients_do_not_cross_streams(socket_dir: Path) -> None:
    path = socket_dir / "gateway.sock"

    async def handler(request):
        yield {"type": "accepted", "operation_id": request["model_attempt_id"], "status": "claimed"}
        await asyncio.sleep(0)
        yield {
            "type": "completed", "text": request["worker_id"], "tool_calls": [], "usage": {},
            "stop_reason": "final", "provider_stop_reason": "stop",
            "provider_request_id": None, "response_hash": "d" * 64,
            "operation_state_version": 1,
        }

    async with FakeModelGatewayServer(str(path), handler, FakePeerVerifier()):
        async def run(seed: int):
            request = request_payload(seed)
            request["worker_id"] = f"worker-{seed}"
            return [item async for item in IsolatedModelGatewayClient(str(path)).complete(request)]

        results = await asyncio.gather(*(run(seed) for seed in range(1, 9)))
    assert [items[-1]["text"] for items in results] == [f"worker-{seed}" for seed in range(1, 9)]


@pytest.mark.asyncio
async def test_peer_reject_and_unavailable_are_failure_closed(socket_dir: Path) -> None:
    path = socket_dir / "gateway.sock"
    async with FakeModelGatewayServer(str(path), complete_handler, FakePeerVerifier(False)):
        with pytest.raises(GatewayProtocolError):
            _ = [item async for item in IsolatedModelGatewayClient(str(path)).complete(request_payload())]

    verifier = LinuxPeerCredentialVerifier(os.getuid())
    assert verifier.verify(type("Writer", (), {"get_extra_info": lambda *_: None})()) is False


def test_linux_peer_uid_accept_and_reject(monkeypatch) -> None:
    monkeypatch.setattr("services.agent.runtime.model_gateway.server.socket.SO_PEERCRED", 17, raising=False)

    class PeerSocket:
        def getsockopt(self, *_):
            return struct.pack("3i", 123, os.getuid(), os.getgid())

    class Writer:
        def get_extra_info(self, name):
            return PeerSocket() if name == "socket" else None

    assert LinuxPeerCredentialVerifier(os.getuid()).verify(Writer()) is True
    assert LinuxPeerCredentialVerifier(os.getuid() + 10000).verify(Writer()) is False


@pytest.mark.asyncio
async def test_connect_first_frame_and_provider_deadlines(socket_dir: Path) -> None:
    missing = IsolatedModelGatewayClient(str(socket_dir / "missing.sock"), connect_timeout=0.01)
    with pytest.raises(GatewayProtocolError, match="GATEWAY_CONNECT_FAILED"):
        _ = [item async for item in missing.complete(request_payload())]

    async def slow_handler(request):
        await asyncio.sleep(0.1)
        yield {"type": "accepted", "operation_id": str(UUID(int=801)), "status": "claimed"}

    path = socket_dir / "slow.sock"
    async with FakeModelGatewayServer(str(path), slow_handler, FakePeerVerifier()):
        client = IsolatedModelGatewayClient(str(path), first_frame_timeout=0.01)
        with pytest.raises(GatewayProtocolError, match="GATEWAY_RESPONSE_TIMEOUT"):
            _ = [item async for item in client.complete(request_payload())]

    async def provider_slow_handler(request):
        yield {"type": "accepted", "operation_id": str(UUID(int=804)), "status": "claimed"}
        await asyncio.sleep(0.05)
        yield {
            "type": "completed", "text": "", "tool_calls": [], "usage": {},
            "stop_reason": "protocol_error", "provider_stop_reason": "stop",
            "provider_request_id": None, "response_hash": "f" * 64,
            "operation_state_version": 1,
        }

    path = socket_dir / "provider-slow.sock"
    request = request_payload()
    request["deadline_ms"] = 10
    async with FakeModelGatewayServer(str(path), provider_slow_handler, FakePeerVerifier()):
        with pytest.raises(GatewayProtocolError, match="GATEWAY_RESPONSE_TIMEOUT"):
            _ = [item async for item in IsolatedModelGatewayClient(str(path)).complete(request)]


@pytest.mark.asyncio
async def test_continuous_deltas_cannot_extend_absolute_deadline(socket_dir: Path) -> None:
    async def endless_delta_handler(request):
        yield {"type": "accepted", "operation_id": str(UUID(int=805)), "status": "claimed"}
        for _ in range(100):
            await asyncio.sleep(0.005)
            yield {"type": "delta", "delta_kind": "text", "delta": {"text": "x"}}

    path = socket_dir / "deadline.sock"
    request = request_payload()
    request["deadline_ms"] = 30
    started = asyncio.get_running_loop().time()
    async with FakeModelGatewayServer(str(path), endless_delta_handler, FakePeerVerifier()):
        with pytest.raises(GatewayProtocolError, match="GATEWAY_RESPONSE_TIMEOUT"):
            _ = [item async for item in IsolatedModelGatewayClient(str(path)).complete(request)]
    assert asyncio.get_running_loop().time() - started < 0.2


@pytest.mark.asyncio
async def test_response_limit_and_backpressure_are_failure_closed(socket_dir: Path) -> None:
    async def large_handler(request):
        yield {"type": "accepted", "operation_id": str(UUID(int=802)), "status": "claimed"}
        for _ in range(4):
            yield {"type": "delta", "delta_kind": "text", "delta": {"text": "x" * 4096}}
        yield {
            "type": "completed", "text": "", "tool_calls": [], "usage": {},
            "stop_reason": "protocol_error", "provider_stop_reason": "stop",
            "provider_request_id": None, "response_hash": "e" * 64,
            "operation_state_version": 1,
        }

    path = socket_dir / "backpressure.sock"
    async with FakeModelGatewayServer(str(path), large_handler, FakePeerVerifier()):
        client = IsolatedModelGatewayClient(str(path), response_limit=5000)
        with pytest.raises(GatewayProtocolError, match="GATEWAY_RESPONSE_TOO_LARGE"):
            _ = [item async for item in client.complete(request_payload())]

    server_path = socket_dir / "server-limit.sock"
    async with FakeModelGatewayServer(
        str(server_path), large_handler, FakePeerVerifier(), response_limit=10_000,
    ):
        responses = [
            item async for item in IsolatedModelGatewayClient(str(server_path)).complete(request_payload())
        ]
    assert responses[-1]["type"] == "failed"
    assert responses[-1]["error_code"] == "GATEWAY_RESPONSE_TOO_LARGE"


@pytest.mark.asyncio
async def test_server_disconnect_and_client_disconnect_are_contained(socket_dir: Path) -> None:
    async def disconnect(reader, writer):
        await reader.read(1)
        writer.close()

    path = socket_dir / "disconnect.sock"
    raw_server = await asyncio.start_unix_server(disconnect, path=str(path))
    try:
        with pytest.raises(GatewayProtocolError, match="GATEWAY_UNEXPECTED_EOF"):
            _ = [item async for item in IsolatedModelGatewayClient(str(path)).complete(request_payload())]
    finally:
        raw_server.close()
        await raw_server.wait_closed()
        path.unlink(missing_ok=True)

    path = socket_dir / "client-disconnect.sock"
    async with FakeModelGatewayServer(str(path), complete_handler, FakePeerVerifier()):
        reader, writer = await asyncio.open_unix_connection(str(path))
        writer.write(encode_frame(request_payload(), limit=REQUEST_FRAME_LIMIT))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_partial_request_and_multiple_requests_fail_closed(socket_dir: Path) -> None:
    path = socket_dir / "gateway.sock"
    async with FakeModelGatewayServer(str(path), complete_handler, FakePeerVerifier()):
        reader, writer = await asyncio.open_unix_connection(str(path))
        writer.write(struct.pack(">I", 10) + b"{}")
        await writer.drain()
        writer.write_eof()
        response = await read_frame(reader)
        assert response["type"] == "failed" and response["error_code"] == "GATEWAY_UNEXPECTED_EOF"
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_socket_symlink_and_regular_file_are_never_replaced(socket_dir: Path) -> None:
    regular = socket_dir / "regular"
    regular.write_text("preserve", encoding="utf-8")
    for unsafe in (regular, socket_dir / "link"):
        if unsafe.name == "link":
            unsafe.symlink_to(regular)
        server = FakeModelGatewayServer(str(unsafe), complete_handler, FakePeerVerifier())
        with pytest.raises(GatewayProtocolError, match="GATEWAY_SOCKET_PATH_UNSAFE"):
            await server.start()
    assert regular.read_text(encoding="utf-8") == "preserve"

    owner_mismatch = socket_dir / "owner.sock"
    server = FakeModelGatewayServer(
        str(owner_mismatch), complete_handler, FakePeerVerifier(), expected_uid=os.getuid() + 10000,
    )
    with pytest.raises(GatewayProtocolError, match="GATEWAY_SOCKET_SECURITY_INVALID"):
        await server.start()
    assert not owner_mismatch.exists()


@pytest.mark.asyncio
async def test_socket_postcheck_failure_and_unsafe_parent_leave_no_socket(
    socket_dir: Path, monkeypatch,
) -> None:
    failed_socket = socket_dir / "chmod-failed.sock"
    monkeypatch.setattr(
        "services.agent.runtime.model_gateway.server.os.chmod",
        lambda *_: (_ for _ in ()).throw(PermissionError()),
    )
    server = FakeModelGatewayServer(str(failed_socket), complete_handler, FakePeerVerifier())
    with pytest.raises(GatewayProtocolError, match="GATEWAY_SOCKET_SECURITY_INVALID"):
        await server.start()
    assert not failed_socket.exists()

    real_parent = socket_dir / "real-parent"
    real_parent.mkdir()
    linked_parent = socket_dir / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    server = FakeModelGatewayServer(
        str(linked_parent / "gateway.sock"), complete_handler, FakePeerVerifier(),
    )
    with pytest.raises(GatewayProtocolError, match="GATEWAY_SOCKET_PARENT_UNSAFE"):
        await server.start()
    assert not (real_parent / "gateway.sock").exists()


@pytest.mark.asyncio
async def test_terminal_frame_followed_by_extra_frame_is_rejected(socket_dir: Path) -> None:
    request = request_payload()

    async def raw(reader, writer):
        await read_frame(reader)
        accepted = {
            "version": VERSION, "request_id": request["request_id"], "sequence": 0,
            "type": "accepted", "operation_id": str(UUID(int=803)), "status": "claimed",
        }
        terminal = {
            "version": VERSION, "request_id": request["request_id"], "sequence": 1,
            "type": "unknown", "ambiguity_kind": "GATEWAY_DISCONNECT", "response_started": True,
            "provider_request_id": None, "reconcile_only": True,
        }
        writer.write(
            encode_frame(accepted) + encode_frame(terminal) + encode_frame(terminal | {"sequence": 2})
        )
        await writer.drain()
        writer.close()

    path = socket_dir / "extra.sock"
    server = await asyncio.start_unix_server(raw, path=str(path))
    try:
        with pytest.raises(GatewayProtocolError, match="GATEWAY_FRAME_AFTER_TERMINAL"):
            _ = [item async for item in IsolatedModelGatewayClient(str(path)).complete(request)]
    finally:
        server.close()
        await server.wait_closed()
        path.unlink(missing_ok=True)
