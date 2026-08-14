from inspect import Parameter, signature
from pathlib import Path
from types import SimpleNamespace
from typing import get_type_hints
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

import agent_runtime_worker_main as entrypoint
from agent_runtime_worker_main import AgentRuntimeProcessSettings
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.sandbox_job import (
    SandboxJobExecutor,
    register_sandbox_job_executor,
)
from services.agent.runtime.production_factory import (
    ProductionCompositionNotReady,
    build_agent_runtime_production_components,
)
from services.agent.runtime.production_composition import (
    ProductionRuntimeComponents,
    build_production_components_for_worker,
)
from services.agent.runtime.runtime_assembly import (
    CapabilityReadinessState,
    RuntimeAssemblyReadiness,
)
from services.agent.runtime.composition import (
    build_authorization, build_projection, build_runtime, build_sandbox,
)


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"


def _sandbox_registry() -> ExecutorRegistry:
    registry = ExecutorRegistry()
    register_sandbox_job_executor(registry, SandboxJobExecutor())
    return registry


def test_all_production_services_are_non_root() -> None:
    names = (
        "everydayai-backend.service",
        "everydayai-conversation-actor.service",
        "everydayai-wecom.service",
        "everydayai-sync.service",
        "everydayai-agent-runtime.service",
        "everydayai-agent-projection.service",
        "everydayai-agent-authorization.service",
        "everydayai-sandbox-worker.service",
    )
    for name in names:
        text = (DEPLOY / name).read_text()
        assert "User=root" not in text
        assert "Group=root" not in text


def test_sandbox_environment_has_no_forbidden_credentials() -> None:
    text = (DEPLOY / "env-templates/sandbox-worker.env.template").read_text()
    for forbidden in ("REDIS", "OSS_", "MODEL_", "JWT", "WECOM"):
        assert forbidden not in text
    assert "WORKER_DATABASE_URL=" in text
    entrypoint = (ROOT / "backend" / "agent_runtime_worker_main.py").read_text()
    assert "class SandboxProcessSettings" in entrypoint
    assert "env_file=None" in entrypoint
    assert '"liveness": True' in entrypoint
    assert '"status": "unavailable"' in entrypoint
    assert 'HEARTBEAT_FAILED' in entrypoint


def test_runtime_worker_has_no_dynamic_composition_setting() -> None:
    fields = set(AgentRuntimeProcessSettings.model_fields)
    assert not any("factory" in name or "components" in name for name in fields)


def test_runtime_worker_environment_does_not_receive_provider_secrets() -> None:
    unit = (DEPLOY / "everydayai-agent-runtime.service").read_text()
    environment_files = tuple(
        line.removeprefix("EnvironmentFile=").strip()
        for line in unit.splitlines()
        if line.startswith("EnvironmentFile=")
    )

    assert environment_files == (
        "/etc/everydayai/agent-runtime-worker.env",
        "/etc/everydayai/agent-runtime-model.env",
    )
    model_template = DEPLOY / "env-templates/agent-runtime-model.env.template"
    assert model_template.exists()
    assert "CONFIG_KEK_CURRENT_VERSION=" in model_template.read_text()

    worker_template = (
        DEPLOY / "env-templates/agent-runtime-worker.env.template"
    ).read_text()
    forbidden = (
        "KIE_API_KEY", "GOOGLE_API_KEY", "DASHSCOPE_API_KEY",
        "APP_OPENROUTER_API_KEY", "CONFIG_KEK", "credential",
    )
    runtime_environment = worker_template.lower()
    assert not any(item.lower() in runtime_environment for item in forbidden)
    assert "class AgentRuntimeProcessSettings" in (
        ROOT / "backend" / "agent_runtime_worker_main.py"
    ).read_text()
    assert "AGENT_RUNTIME_PRODUCTION_COMPOSITION_ENABLED=false" in worker_template


def test_production_factory_fails_closed_until_scoped_services_exist() -> None:
    with pytest.raises(ProductionCompositionNotReady) as caught:
        build_production_components_for_worker(
            database=object(), settings=object(),
            sandbox_registry=_sandbox_registry(),
        )
    assert caught.value.readiness.production_ready is False
    assert caught.value.readiness.error_code == "SAFETY_SERVICE_WIRING_NOT_READY"
    assert caught.value.readiness.capabilities["runtime.model"].state is (
        CapabilityReadinessState.UNAVAILABLE
    )
    assert caught.value.readiness.capabilities["runtime.media"].state is (
        CapabilityReadinessState.DISABLED
    )


def test_production_factory_ignores_injected_callable() -> None:
    calls = []

    def injected(**_kwargs):
        calls.append(True)
        raise AssertionError("dynamic factory must be unreachable")

    hook_name = "_".join(("agent", "runtime", "production", "service", "factory"))
    settings = SimpleNamespace(**{hook_name: injected})
    with pytest.raises(ProductionCompositionNotReady):
        build_production_components_for_worker(
            database=object(), settings=settings,
            sandbox_registry=_sandbox_registry(),
        )
    assert calls == []


def test_production_factory_requires_sandbox_registry() -> None:
    with pytest.raises(
        ProductionCompositionNotReady,
        match="RUNTIME_PRODUCTION_COMPOSITION_NOT_READY:SANDBOX_REGISTRY_REQUIRED",
    ) as caught:
        build_agent_runtime_production_components(object(), object(), None)
    assert caught.value.readiness.production_ready is False
    assert caught.value.readiness.capabilities["runtime.sandbox"].state is (
        CapabilityReadinessState.UNAVAILABLE
    )


def test_production_factory_requires_sandbox_executor() -> None:
    with pytest.raises(
        ProductionCompositionNotReady,
        match="RUNTIME_PRODUCTION_COMPOSITION_NOT_READY:SANDBOX_EXECUTOR_REQUIRED",
    ) as caught:
        build_agent_runtime_production_components(
            object(), object(), ExecutorRegistry(),
        )
    assert caught.value.readiness.capabilities["runtime.sandbox"].error_code == (
        "SANDBOX_EXECUTOR_REQUIRED"
    )


def test_production_components_require_typed_readiness_and_explicit_bundle() -> None:
    parameters = signature(ProductionRuntimeComponents).parameters
    assert get_type_hints(ProductionRuntimeComponents)["readiness"] is (
        RuntimeAssemblyReadiness
    )
    assert parameters["readiness"].default is Parameter.empty
    assert parameters["service_bundle"].default is Parameter.empty


@pytest.mark.asyncio
async def test_runtime_worker_entry_cannot_promote_unwired_composition(tmp_path) -> None:
    class DatabaseWithoutGateRead:
        def __getattr__(self, _name):
            raise AssertionError("database gates must not manufacture readiness")

    settings = SimpleNamespace(
        agent_runtime_process_role="agent_runtime",
        agent_runtime_worker_id="c7-b31-worker",
        agent_runtime_release_revision="c7-b31",
        agent_runtime_production_composition_enabled=True,
        sandbox_job_root=str(tmp_path / "jobs"),
        sandbox_runtime_revision="sandbox-c7-b31",
    )

    with pytest.raises(
        RuntimeError, match="RUNTIME_MODEL_CONFIGURATION_NOT_READY",
    ):
        await entrypoint._build_owner_and_cycle(
            "agent_runtime", DatabaseWithoutGateRead(), settings,
        )


def test_runtime_rejects_explicit_component_injection() -> None:
    settings = SimpleNamespace(
        agent_runtime_production_composition_enabled=True,
        sandbox_runtime_revision="sandbox-c7-b31",
    )
    with pytest.raises(
        RuntimeError, match="RUNTIME_PRODUCTION_COMPONENT_INJECTION_FORBIDDEN",
    ):
        build_runtime(
            object(), settings, production_components=object(),
            process_role="agent_runtime",
        )


@pytest.mark.asyncio
async def test_projection_process_starts_supervised_media_owner() -> None:
    settings = SimpleNamespace(
        agent_runtime_worker_id="projection-test",
        agent_runtime_scheduled_web_projection_enabled=False,
        agent_runtime_media_enabled=True,
        agent_runtime_media_provider_probe_passed=True,
        media_workspace_root="/mnt/nas-workspace",
        media_cdn_domain="cdn.example.test",
        media_result_allowed_hosts="provider.example.test,*.provider-cdn.test",
    )
    owner = MagicMock(run_once=AsyncMock(return_value=True))
    probe = SimpleNamespace(ready=True, code="READY")

    with patch.object(entrypoint, "_configure_projection_redis"), patch.object(
        entrypoint, "build_projection", return_value=owner,
    ) as builder, patch(
        "services.tool_confirmation.capability_probe.probe_tool_confirmation_redis",
        new=AsyncMock(return_value=probe),
    ):
        built, cycle = await entrypoint._build_owner_and_cycle(
            "projection", object(), settings,
        )

    assert built is owner
    assert await cycle() is True
    builder.assert_called_once_with(
        ANY, "projection-test", process_role="projection",
        scheduled_web_projection_enabled=False,
        media_projection_enabled=True,
        media_workspace_root="/mnt/nas-workspace",
        media_cdn_domain="cdn.example.test",
        media_result_allowed_hosts=(
            "provider.example.test", "*.provider-cdn.test",
        ),
    )


@pytest.mark.parametrize(
    ("builder", "args", "expected"),
    (
        (build_runtime, (object(), object()), "agent_runtime"),
        (build_projection, (object(), "worker"), "projection"),
        (build_authorization, (object(), "worker"), "authorization"),
        (build_sandbox, (object(), object()), "sandbox"),
    ),
)
def test_composition_roots_reject_wrong_process_role(builder, args, expected) -> None:
    with pytest.raises(
        RuntimeError,
        match=f"RUNTIME_COMPOSITION_ROLE_MISMATCH:{expected}:wrong-role",
    ):
        builder(*args, process_role="wrong-role")


def test_sandbox_probe_checks_fixed_capabilities() -> None:
    text = (DEPLOY / "runtime-capability-probe.sh").read_text()
    for contract in (
        "cgroup2fs", "cpu", "memory", "pids", "SANDBOX_NSJAIL_SHA256",
        "SANDBOX_ROOTFS_SHA256", "SANDBOX_SECCOMP_SHA256",
        "cgroup_mem_swap_max", "iface_no_lo", "pgrep",
        "SANDBOX_CGROUP_V2_MOUNT", "rootfs_manifest verify",
        'findmnt -T "$SANDBOX_ROOTFS"',
        'test "$(id -un)" = everydayai-sandbox',
        'test "$(id -gn)" = everydayai-sandbox',
        'test "$(id -u)" -ne 0',
        'test "$(id -g)" -ne 0',
        '65534:$(id -u):1',
        '65534:$(id -g):1',
        'SANDBOX_CGROUP_V2_RUNNER',
        'test -z "$(cat "$SANDBOX_CGROUP_V2_MOUNT/cgroup.procs")"',
        'grep -qx "$$" "$SANDBOX_CGROUP_V2_RUNNER/cgroup.procs"',
    ):
        assert contract in text


def test_sandbox_unit_delegates_only_required_cgroup_controllers() -> None:
    text = (DEPLOY / "everydayai-sandbox-worker.service").read_text()
    assert "Delegate=cpu memory pids" in text
    assert "MemoryMax=768M" in text
    assert "MemorySwapMax=0" in text
    assert "TasksMax=128" in text
    assert "LimitFSIZE=268435456" in text
    assert "BindReadOnlyPaths=/var/lib/everydayai/sandbox-rootfs" in text
    assert "PrivateNetwork=true" not in text
    assert "User=everydayai-sandbox" in text
    assert "Group=everydayai-sandbox" in text
    assert "SupplementaryGroups=everydayai-sandbox-io" in text
    assert "/usr/local/libexec/everydayai-sandbox-worker-cgroup-wrapper" in text
    assert text.count("/usr/local/libexec/everydayai-sandbox-worker-cgroup-wrapper") == 2
    wrapper = DEPLOY / "sandbox-worker-cgroup-wrapper.sh"
    assert wrapper.stat().st_mode & 0o111
    wrapper_text = wrapper.read_text()
    assert 'echo "$$" > "$runner/cgroup.procs"' in wrapper_text
    assert 'test -z "$(cat "$root/cgroup.procs")"' in wrapper_text
    assert "StateDirectory=" not in text
    assert (DEPLOY / "sandbox-job.policy").is_file()
    installer = (DEPLOY / "install-sandbox-rootfs.sh").read_text()
    assert "sha256sum -c SHA256SUMS" in installer
    assert "rootfs_manifest.py" in installer
    assert "test ! -e \"$target\"" in installer
    preflight = (DEPLOY / "preflight-agent-runtime-release.sh").read_text()
    assert "runuser --preserve-environment -u everydayai-sandbox" in preflight
    assert "sandbox-worker-cgroup-wrapper.sh" in preflight
    assert "systemctl is-active everydayai-sandbox-worker" in preflight
    assert "sandbox cgroup probe deferred to unit ExecStartPre" in preflight
    env = (DEPLOY / "env-templates/sandbox-worker.env.template").read_text()
    assert "SANDBOX_CGROUP_V2_RUNNER=" in env
