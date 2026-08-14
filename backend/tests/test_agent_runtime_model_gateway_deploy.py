"""Static security and release contracts for the single Runtime deployment."""
from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
C7_QUALITY_BASELINE = "25513882a76efdfc77cc9ea6031ed1d52282f18b"
KEK_KEYS = {"CONFIG_KEK_CURRENT_VERSION", "CONFIG_KEK_KEYRING_JSON"}


def _env_keys(path: Path) -> set[str]:
    return {
        line.split("=", 1)[0] for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }


def test_runtime_model_env_is_minimal_and_runtime_only() -> None:
    model_env = DEPLOY / "env-templates/agent-runtime-model.env.template"
    assert _env_keys(model_env) == KEK_KEYS
    text = model_env.read_text(encoding="utf-8")
    assert "<base64-encoded-32-byte-kek>" in text
    for forbidden in ("sk-", "Bearer ", "api_key", "PROVIDER_API_KEY", "DATABASE_URL"):
        assert forbidden not in text

    unit = (DEPLOY / "everydayai-agent-runtime.service").read_text(encoding="utf-8")
    users = (DEPLOY / "provision-runtime-users.sh").read_text(encoding="utf-8")
    assert "EnvironmentFile=/etc/everydayai/agent-runtime-model.env" in unit
    assert "SupplementaryGroups=everydayai-sandbox-io everydayai-runtime-model-secret" in unit
    assert "groupadd --system everydayai-runtime-model-secret" in users
    assert "usermod -a -G everydayai-runtime-model-secret everydayai-agent-runtime" in users
    assert "chown root:everydayai-runtime-model-secret" in users
    assert "Legacy Model Gateway env 必须先完成受审查退役" in users
    assert "agent-runtime agent-projection" in users
    assert "agent-authorization" in users
    assert "-o everydayai-agent-projection" in users
    assert "-o everydayai-agent-authorization" in users
    assert "agent-runtime projection authorization" not in users
    assert "! -name 'agent-model-gateway.env'" in users
    assert "! -name 'agent-model-gateway-kek.env'" in users
    assert not (DEPLOY / "everydayai-agent-model-gateway.service").exists()


def test_runtime_model_env_unix_dac_excludes_other_workers() -> None:
    def can_read(
        mode: int, file_uid: int, file_gid: int, process_uid: int, process_gids: set[int]
    ) -> bool:
        if process_uid == file_uid:
            return bool(mode & 0o400)
        if file_gid in process_gids:
            return bool(mode & 0o040)
        return bool(mode & 0o004)

    root_uid, runtime_uid, projection_uid = 0, 2101, 2102
    secret_gid = 3101
    assert can_read(0o640, root_uid, secret_gid, runtime_uid, {secret_gid})
    assert not can_read(0o640, root_uid, secret_gid, projection_uid, set())


def test_three_unit_transaction_and_ci_entry_are_release_bound() -> None:
    workflow = (ROOT / ".github/workflows/agent-runtime-disposable.yml").read_text(
        encoding="utf-8"
    )
    update = (DEPLOY / "update-control-plane-units.sh").read_text(encoding="utf-8")
    states = (DEPLOY / "check-agent-runtime-unit-states.sh").read_text(
        encoding="utf-8"
    )
    assert "legacy Model Gateway" in update
    assert "everydayai-agent-model-gateway" in states
    for name in ("check-control-plane-unit-manifest.sh", "runtime-flags-off-install.sh"):
        text = (DEPLOY / name).read_text(encoding="utf-8")
        assert "everydayai-agent-model-gateway.service" not in text
    flags_off = (DEPLOY / "runtime-flags-off-install.sh").read_text(encoding="utf-8")
    assert "deploy/control_plane_env_source.py" in flags_off
    assert "test_agent_runtime_single_model_adapter.py" in workflow
    assert "test_agent_runtime_model_configuration_facade_postgres_external.py" in workflow
    assert '- "backend/core/db_scope.py"' in workflow
    assert '- "backend/services/configuration/**"' in workflow
    assert "AGENT_RUNTIME_PRODUCTION_ENABLED" not in workflow
    for gate in ("AGENT_RUNTIME_PRODUCTION_COMPOSITION_ENABLED",):
        assert f'{gate}: "false"' in workflow
        assert f'test "${gate}" = false' in workflow
    assert "production_ready=false" in workflow


def test_scheduled_wecom_disposable_ci_is_flags_off_and_release_bound() -> None:
    workflow = (ROOT / ".github/workflows/agent-runtime-disposable.yml").read_text(
        encoding="utf-8"
    )
    for path_filter in (
        '"backend/wecom_ws_runner.py"',
        '"backend/services/wecom/**"',
        '"backend/migrations/227_*.sql"',
        '"backend/migrations/rollback/227_*.sql"',
        '"backend/tests/test_scheduled_wecom_*.py"',
        '"backend/tests/test_wecom_ws_runner*.py"',
        '"deploy/everydayai-wecom.service"',
        '"deploy/env-templates/wecom-runtime.env.template"',
        '"deploy/*.sh"',
    ):
        assert f"- {path_filter}" in workflow
    assert 'AGENT_RUNTIME_SCHEDULED_WECOM_ENABLED: "false"' in workflow
    assert 'test "$AGENT_RUNTIME_SCHEDULED_WECOM_ENABLED" = false' in workflow
    for test_name in (
        "test_wecom_ws_runner_main.py",
        "test_agent_runtime_scheduled_wecom_worker.py",
        "test_agent_runtime_scheduled_wecom_worker_reconcile_priority.py",
        "test_agent_runtime_scheduled_wecom_reconcile_service.py",
        "test_agent_runtime_scheduled_wecom_reconcile_org_repository.py",
        "test_scheduled_wecom_runtime_composition.py",
        "test_agent_runtime_scheduled_wecom_router.py",
        "test_agent_runtime_scheduled_wecom_app_dispatch.py",
        "test_agent_runtime_scheduled_wecom_smart_dispatch.py",
        "test_agent_runtime_scheduled_wecom_smart_transport_resolution.py",
        "test_scheduled_wecom_app_binding.py",
        "test_tenant_db_env_contract.py",
    ):
        assert test_name in workflow
    ar18_runner = (
        ROOT / "scripts/run_agent_runtime_ar18_disposable.sh"
    ).read_text(encoding="utf-8")
    for postgres_contract in (
        "test_agent_runtime_ar18_b7_s2_b1d2a_wecom_foundation_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_claim_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_dispatch_prepare_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_dispatch_outcome_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_reconcile_claim_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_continuation_claim_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_reconcile_still_unknown_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_reconcile_definitive_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_dispatch_version_readback_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_dispatch_payload_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_unsupported_terminalization_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_started_recovery_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_unicode_payload_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_configuration_facade_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_prepared_payload_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_reconcile_org_postgres_external.py",
    ):
        assert postgres_contract in ar18_runner
    assert "test_agent_runtime_scheduled_wecom_reconcile_org_migration.py" in workflow
    for evidence in ("scheduled-wecom-unit.log", "ar18-postgres.log"):
        assert evidence in workflow
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
            C7_QUALITY_BASELINE, "--",
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
        "backend/migrations/227_53_agent_runtime_model_configuration_facade.sql",
        "backend/services/agent/runtime/infrastructure/model/configured_adapter.py",
        "backend/services/agent/runtime/application/model_loop.py",
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
