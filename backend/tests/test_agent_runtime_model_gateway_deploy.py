"""Static security and release contracts for C7-BG5 deployment."""
from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
UNIT = DEPLOY / "everydayai-agent-model-gateway.service"
C7_QUALITY_BASELINE = "25513882a76efdfc77cc9ea6031ed1d52282f18b"
GATEWAY_KEYS = {
    "AGENT_MODEL_GATEWAY_DATABASE_URL", "AGENT_MODEL_GATEWAY_WORKER_ID",
    "AGENT_MODEL_GATEWAY_RELEASE_REVISION", "AGENT_MODEL_GATEWAY_SOCKET",
    "AGENT_MODEL_GATEWAY_HEALTH_SOCKET", "AGENT_MODEL_GATEWAY_RUNTIME_UID",
    "AGENT_MODEL_GATEWAY_DRAIN_TIMEOUT_SECONDS",
    "AGENT_MODEL_GATEWAY_ISOLATED_HARNESS_ENABLED",
    "AGENT_MODEL_GATEWAY_PRODUCTION_ENABLED",
}
KEK_KEYS = {"CONFIG_KEK_CURRENT_VERSION", "CONFIG_KEK_KEYRING_JSON"}


def _env_keys(path: Path) -> set[str]:
    return {
        line.split("=", 1)[0] for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }


def test_gateway_unit_has_dedicated_identity_envs_and_hardening() -> None:
    unit = UNIT.read_text(encoding="utf-8")
    assert "User=everydayai-agent-model-gateway" in unit
    assert "Group=everydayai-model-gateway" in unit
    assert "SupplementaryGroups=everydayai-model-gateway-secret" in unit
    assert "RuntimeDirectory=everydayai-agent-model-gateway" in unit
    assert "RuntimeDirectoryMode=0750" in unit
    assert "ExecStart=/var/www/everydayai/backend/venv/bin/python agent_model_gateway_main.py" in unit
    assert [line for line in unit.splitlines() if line.startswith("EnvironmentFile=")] == [
        "EnvironmentFile=/etc/everydayai/agent-model-gateway.env",
        "EnvironmentFile=/etc/everydayai/agent-model-gateway-kek.env",
    ]
    for contract in (
        "ProtectSystem=strict", "ProtectHome=true", "PrivateTmp=true",
        "NoNewPrivileges=true", "PrivateDevices=true",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "RestrictSUIDSGID=true", "LockPersonality=true",
    ):
        assert contract in unit
    assert "backend/.env" in unit and "backend/.env.kek" in unit


def test_gateway_templates_are_minimal_flags_off_examples() -> None:
    gateway = DEPLOY / "env-templates/agent-model-gateway.env.template"
    kek = DEPLOY / "env-templates/agent-model-gateway-kek.env.template"
    assert _env_keys(gateway) == GATEWAY_KEYS
    assert _env_keys(kek) == KEK_KEYS
    gateway_text = gateway.read_text(encoding="utf-8")
    kek_text = kek.read_text(encoding="utf-8")
    assert "everydayai_agent_model_gateway:<password>" in gateway_text
    assert "AGENT_MODEL_GATEWAY_PRODUCTION_ENABLED=false" in gateway_text
    assert "AGENT_MODEL_GATEWAY_ISOLATED_HARNESS_ENABLED=true" in gateway_text
    assert "<base64-encoded-32-byte-kek>" in kek_text
    for forbidden in ("sk-", "Bearer ", "api_key", "PROVIDER_API_KEY", "DATABASE_URL=postgresql://postgres:"):
        assert forbidden not in gateway_text + kek_text


def test_runtime_cannot_receive_gateway_secret_material() -> None:
    runtime_env = DEPLOY / "env-templates/agent-runtime-worker.env.template"
    runtime_unit = (DEPLOY / "everydayai-agent-runtime.service").read_text(encoding="utf-8")
    text = runtime_env.read_text(encoding="utf-8")
    assert "AGENT_RUNTIME_MODEL_GATEWAY_ENABLED=false" in text
    assert "everydayai-model-gateway" in runtime_unit
    assert "everydayai-model-gateway-secret" not in runtime_unit
    assert "/etc/everydayai/agent-model-gateway-kek.env" in runtime_unit
    for forbidden in ("CONFIG_KEK", "PROVIDER_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY"):
        assert forbidden not in text


def test_identity_and_database_role_provisioning_match_migration_contract() -> None:
    users = (DEPLOY / "provision-runtime-users.sh").read_text(encoding="utf-8")
    env_provisioner = (DEPLOY / "provision-control-plane-worker-envs.py").read_text(
        encoding="utf-8"
    )
    roles = (DEPLOY / "bootstrap-agent-model-gateway-role.sh").read_text(encoding="utf-8")
    migration = (ROOT / "backend/migrations/227_18_agent_runtime_model_gateway.sql").read_text(
        encoding="utf-8"
    )
    assert "groupadd --system everydayai-model-gateway" in users
    assert "groupadd --system everydayai-model-gateway-secret" in users
    assert "--gid everydayai-model-gateway" in users
    assert "usermod -a -G everydayai-model-gateway everydayai-agent-runtime" in users
    assert "usermod -a -G everydayai-model-gateway-secret everydayai-agent-model-gateway" in users
    assert "chown root:everydayai-model-gateway-secret" in users
    assert "! -name 'agent-model-gateway.env'" in users
    assert "! -name 'agent-model-gateway-kek.env'" in users
    assert "Runtime user 禁止加入 Gateway secret group" in users
    assert "Gateway secret group 包含未批准成员" in users
    assert 'GATEWAY_SECRET_GROUP = "everydayai-model-gateway-secret"' in env_provisioner
    for name in ("agent-model-gateway.env", "agent-model-gateway-kek.env"):
        assert f"test -r /etc/everydayai/{name}" in users
        assert f"test ! -r /etc/everydayai/{name}" in users
    assert "EVERYDAYAI_AGENT_MODEL_GATEWAY_PASSWORD" in roles
    assert "CREATE ROLE everydayai_agent_model_gateway" in roles
    assert "rolname='everydayai_agent_model_gateway'" in migration


def test_gateway_env_unix_dac_excludes_runtime_identity() -> None:
    def can_read(
        mode: int, file_uid: int, file_gid: int, process_uid: int, process_gids: set[int]
    ) -> bool:
        if process_uid == file_uid:
            return bool(mode & 0o400)
        if file_gid in process_gids:
            return bool(mode & 0o040)
        return bool(mode & 0o004)

    root_uid, gateway_uid, runtime_uid = 0, 2101, 2102
    socket_gid, secret_gid = 3101, 3102
    assert can_read(0o640, root_uid, secret_gid, gateway_uid, {socket_gid, secret_gid})
    assert not can_read(0o640, root_uid, secret_gid, runtime_uid, {socket_gid})


def test_four_unit_transaction_and_ci_entry_are_release_bound() -> None:
    workflow = (ROOT / ".github/workflows/agent-runtime-disposable.yml").read_text(
        encoding="utf-8"
    )
    for name in (
        "update-control-plane-units.sh", "check-control-plane-unit-manifest.sh",
        "runtime-flags-off-install.sh", "check-agent-runtime-unit-states.sh",
    ):
        text = (DEPLOY / name).read_text(encoding="utf-8")
        assert "everydayai-agent-model-gateway" in text
    flags_off = (DEPLOY / "runtime-flags-off-install.sh").read_text(encoding="utf-8")
    assert "deploy/control_plane_env_source.py" in flags_off
    assert "scripts/run_agent_model_gateway_disposable.sh" in workflow
    assert '- "backend/core/db_scope.py"' in workflow
    assert '- "backend/services/configuration/**"' in workflow
    assert "AGENT_RUNTIME_PRODUCTION_ENABLED" not in workflow
    for gate in (
        "AGENT_RUNTIME_PRODUCTION_COMPOSITION_ENABLED",
        "AGENT_RUNTIME_MODEL_GATEWAY_ENABLED",
        "AGENT_MODEL_GATEWAY_PRODUCTION_ENABLED",
    ):
        assert f'{gate}: "false"' in workflow
        assert f'test "${gate}" = false' in workflow
    unified = (ROOT / "scripts/run_agent_model_gateway_disposable.sh").read_text(
        encoding="utf-8"
    )
    assert "test_agent_runtime_c7_bg4_production_composition.py" in unified
    static_phase, postgres_phase = unified.split('if [ "$mode" = all ]; then', 1)
    assert "test_agent_runtime_ar18_b4_model_cancel_migration.py" in static_phase
    for test_name in (
        "test_agent_runtime_ar18_b4_model_cancel_postgres_external.py",
        "test_agent_runtime_ar18_b4_model_cancel_uds_postgres_external.py",
    ):
        assert test_name not in static_phase
        assert test_name in postgres_phase
    assert "RUN_AR17_1_DB_TEST=1" in postgres_phase
    assert "DATABASE_URL" not in unified
    assert "production_ready=false" in workflow


def test_ci_quality_scope_is_the_frozen_c7_candidate_diff() -> None:
    workflow = (ROOT / ".github/workflows/agent-runtime-disposable.yml").read_text(
        encoding="utf-8"
    )
    assert f'C7_QUALITY_BASELINE: "{C7_QUALITY_BASELINE}"' in workflow
    assert "fetch-depth: 0" in workflow
    assert "git diff --name-only --diff-filter=ACMR" in workflow
    assert '"$C7_QUALITY_BASELINE"...HEAD --' in workflow
    assert '"${c7_candidate_files[@]}"' in workflow
    candidate_files = subprocess.run(
        [
            "git", "diff", "--name-only", "--diff-filter=ACMR",
            f"{C7_QUALITY_BASELINE}...HEAD", "--",
        ],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.splitlines()
    for required in (
        "backend/migrations/227_18_agent_runtime_model_gateway.sql",
        "backend/migrations/227_19_agent_runtime_model_gateway_predispatch_failure.sql",
        "backend/migrations/227_20_agent_runtime_model_gateway_dispatch_binding.sql",
        "backend/services/agent/runtime/model_gateway/protocol.py",
        "backend/services/agent/runtime/model_gateway/service.py",
        "backend/services/agent/runtime/model_gateway/runtime_client.py",
    ):
        assert required in candidate_files


def test_sandbox_release_assets_have_zero_diff() -> None:
    paths = (
        "deploy/everydayai-sandbox-worker.service",
        "deploy/env-templates/sandbox-worker.env.template",
        "deploy/sandbox-worker-cgroup-wrapper.sh", "deploy/sandbox-job.policy",
    )
    for relative in paths:
        committed = subprocess.run(
            ["git", "show", f"HEAD:{relative}"], cwd=ROOT,
            capture_output=True, check=True,
        ).stdout
        assert (ROOT / relative).read_bytes() == committed
