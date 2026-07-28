from unittest.mock import AsyncMock

import pytest

from services.agent.runtime.sandbox.contracts import SandboxResourceLimits
from services.agent.runtime.sandbox.launcher import SandboxLaunchRequest
from services.agent.runtime.sandbox.nsjail import _command
from services.agent.runtime.sandbox.service import SandboxJobWorkerService


def test_nsjail_command_has_readonly_input_writable_output_and_limits(
    tmp_path,
) -> None:
    input_dir, output_dir = tmp_path / "input", tmp_path / "output"
    command = _command(
        nsjail="/usr/bin/nsjail", rootfs=tmp_path / "rootfs",
        python_path="/usr/bin/python3",
        seccomp_policy=tmp_path / "sandbox.policy",
        request=SandboxLaunchRequest(
            job_id="11111111-1111-1111-1111-111111111111",
            code=b"print(1)", input_dir=input_dir, output_dir=output_dir,
            limits=SandboxResourceLimits.from_request({}),
        ),
    )
    assert ["-R", f"{input_dir}:/job/input"] == command[
        command.index("-R"):command.index("-R") + 2
    ]
    assert ["-B", f"{output_dir}:/job/output"] == command[
        command.index("-B"):command.index("-B") + 2
    ]
    assert "--clone_newnet" in command
    assert "--seccomp_policy" in command
    assert "--cgroup_mem_max" in command
    assert "--cgroup_pids_max" in command
    assert "--cgroup_cpu_ms_per_sec" in command


@pytest.mark.asyncio
async def test_worker_service_drain_stops_without_another_claim() -> None:
    worker = type("_Worker", (), {})()
    worker.drain = lambda: None
    calls = []

    async def run_once():
        calls.append("execution")
        service.stop()
        return type("_Result", (), {"worked": False})()

    worker.run_once = run_once
    worker.reconcile_next = AsyncMock()
    service = SandboxJobWorkerService(worker)
    await service.run()
    assert calls == ["execution"]
    worker.reconcile_next.assert_not_awaited()
