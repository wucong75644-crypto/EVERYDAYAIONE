from pathlib import Path

import pytest

from services.agent.runtime.production_composition import (
    ProductionRuntimeComponents,
    build_production_components_for_worker,
)
from services.agent.runtime.composition import (
    build_authorization, build_projection, build_runtime, build_sandbox,
)


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"


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


def test_runtime_worker_requires_explicit_production_composition() -> None:
    entrypoint = (ROOT / "backend" / "agent_runtime_worker_main.py").read_text()
    assert "agent_runtime_production_composition_enabled" in entrypoint
    assert "build_production_components_for_worker" in entrypoint
    assert "production_components=production_components" in entrypoint


def test_production_factory_fails_closed_until_scoped_services_exist() -> None:
    with pytest.raises(
        RuntimeError, match="RUNTIME_PRODUCTION_COMPONENT_FACTORY_NOT_READY",
    ):
        build_production_components_for_worker(
            database=object(), settings=object(), sandbox_registry=object(),
        )


def test_production_factory_rejects_missing_service_bundle() -> None:
    settings = type("Settings", (), {
        "agent_runtime_production_service_factory": staticmethod(
            lambda **_kwargs: ProductionRuntimeComponents(
                registry=object(), catalog=object(), callback_inbox=object(),
                specialist_repository=object(), service_bundle=None,
            ),
        ),
    })()
    with pytest.raises(
        RuntimeError,
        match="RUNTIME_PRODUCTION_COMPONENT_FACTORY_NOT_READY:SERVICE_BUNDLE_REQUIRED",
    ):
        build_production_components_for_worker(
            database=object(), settings=settings, sandbox_registry=object(),
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
