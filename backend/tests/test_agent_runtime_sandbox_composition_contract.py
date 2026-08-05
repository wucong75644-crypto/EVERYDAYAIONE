from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import agent_runtime_worker_main as entrypoint
from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.sandbox.composition import (
    SandboxWorkerComponents,
    build_sandbox_worker_components,
)
from services.agent.runtime.sandbox.launcher import IsolationProbe
from services.agent.runtime.ports.sandbox_job import (
    SandboxJobOutcome,
)


class _Launcher:
    def __init__(self, probe: IsolationProbe):
        self._probe = probe
        self.probe_calls = 0

    def probe(self):
        self.probe_calls += 1
        return self._probe

    async def launch(self, _request):
        raise AssertionError("launcher must remain idle in this contract")

    async def query(self, _job_id):
        raise AssertionError("launcher must remain idle in this contract")


class _IdleDatabase:
    def __init__(self):
        self.claim_calls = 0
        self.reconcile_calls = 0
        self.scope = DatabaseScope(
            actor_user_id=None, org_id=None,
            access_kind=DatabaseAccessKind.SANDBOX_WORKER,
            request_id="composition-contract",
        )

    def rpc(self, name, _params):
        if name in {
            "claim_next_sandbox_job", "claim_next_recoverable_sandbox_job",
        }:
            self.claim_calls += 1
        elif name == "claim_next_sandbox_job_reconciliation":
            self.reconcile_calls += 1

        async def execute():
            return SimpleNamespace(
                data={"outcome": SandboxJobOutcome.NOT_FOUND.value},
            )
        return SimpleNamespace(execute=execute)


def _components(tmp_path, probe: IsolationProbe) -> tuple[
    SandboxWorkerComponents, _IdleDatabase, _Launcher
]:
    launcher = _Launcher(probe)
    database = _IdleDatabase()
    components = build_sandbox_worker_components(
        worker_database=database, launcher=launcher,
        workspace_root=tmp_path / "jobs", worker_id="sandbox-contract",
        worker_identity=SimpleNamespace(uid=1001, gid=1001),
    )
    return components, database, launcher


@pytest.mark.asyncio
async def test_sandbox_entrypoint_uses_real_components_and_cycle_surface(
    tmp_path, monkeypatch,
):
    components, jobs, launcher = _components(
        tmp_path, IsolationProbe(ready=True, code="SANDBOX_ISOLATION_READY"),
    )
    monkeypatch.setattr(entrypoint, "build_sandbox", lambda *_args, **_kwargs: components)
    settings = SimpleNamespace(sandbox_partial_retention_seconds=86400)

    owner, cycle = await entrypoint._build_owner_and_cycle(
        "sandbox", object(), settings,
    )
    assert owner is components
    assert await cycle() is False
    assert launcher.probe_calls == 2
    assert jobs.claim_calls == 2
    assert jobs.reconcile_calls == 1


@pytest.mark.asyncio
async def test_sandbox_probe_failure_is_startup_fatal_and_cannot_claim(
    tmp_path, monkeypatch,
):
    components, jobs, launcher = _components(
        tmp_path, IsolationProbe(
            ready=False, code="SANDBOX_ROOTFS_CONTENT_MISMATCH",
        ),
    )
    monkeypatch.setattr(entrypoint, "build_sandbox", lambda *_args, **_kwargs: components)
    with pytest.raises(
        RuntimeError,
        match="SANDBOX_CAPABILITY_PROBE_FAILED:SANDBOX_ROOTFS_CONTENT_MISMATCH",
    ):
        await entrypoint._build_owner_and_cycle(
            "sandbox", object(), SimpleNamespace(),
        )
    assert launcher.probe_calls == 1
    assert jobs.claim_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "owner_factory", "stop_path"),
    (
        ("agent_runtime", lambda: SimpleNamespace(drain=Mock()), "drain"),
        ("projection", lambda: SimpleNamespace(), None),
        ("authorization", lambda: SimpleNamespace(stop=Mock()), "stop"),
        (
            "sandbox",
            lambda: SimpleNamespace(service=SimpleNamespace(stop=Mock())),
            "service.stop",
        ),
    ),
)
async def test_shutdown_stops_owner_removes_health_socket_and_closes_db_once(
    tmp_path, role, owner_factory, stop_path,
):
    owner = owner_factory()
    socket_path = tmp_path / "health.sock"
    socket_path.write_text("socket", encoding="utf-8")
    server = SimpleNamespace(
        close=Mock(), wait_closed=AsyncMock(),
    )
    close_db = AsyncMock()

    await entrypoint._shutdown(
        owner, server, str(socket_path), close_db,
    )
    server.close.assert_called_once_with()
    server.wait_closed.assert_awaited_once_with()
    assert not socket_path.exists()
    close_db.assert_awaited_once_with()
    if stop_path == "drain":
        owner.drain.assert_called_once_with()
    elif stop_path == "stop":
        owner.stop.assert_called_once_with()
    elif stop_path == "service.stop":
        owner.service.stop.assert_called_once_with()


@pytest.mark.asyncio
async def test_shutdown_does_not_stop_owner_twice_after_signal_drain(tmp_path):
    owner = SimpleNamespace(drain=Mock())
    socket_path = tmp_path / "health.sock"
    socket_path.write_text("socket", encoding="utf-8")
    server = SimpleNamespace(close=Mock(), wait_closed=AsyncMock())
    close_db = AsyncMock()

    owner.drain()
    await entrypoint._shutdown(
        owner, server, str(socket_path), close_db,
        owner_already_drained=True,
    )

    owner.drain.assert_called_once_with()
    close_db.assert_awaited_once_with()
