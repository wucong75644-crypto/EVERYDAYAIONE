"""Static guard for the hosted production-composition daemon lifecycle."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/sandbox-linux-security.yml"
HARNESS = Path(__file__).with_name("agent_runtime_sandbox_daemon_e2e.py")
HEALTH = Path(__file__).with_name("sandbox_daemon_health.py")


def test_hosted_contract_uses_real_daemon_postgres_and_nsjail() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    harness = HARNESS.read_text(encoding="utf-8")
    for contract in (
        "initdb", "223", "setup-tenant-db-roles.sh",
        "bootstrap-agent-runtime-roles.sh", "agent_runtime_worker_main",
        "--preserve-environment -u everydayai-sandbox",
        "SANDBOX_NSJAIL_PATH", "SANDBOX_CGROUP_V2_MOUNT",
        "timeout --signal=KILL 300s",
        'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
        "EVERYDAYAI_LOG_DIR=/tmp/everydayai-sandbox-worker/logs",
        "/tmp/everydayai-sandbox-worker/logs",
        "chmod 0750",
        "test -w /tmp/everydayai-sandbox-worker/logs",
        "! test -w /tmp/everydayai-sandbox-worker/source/backend",
        "type f ! -perm -004 -delete",
        "type d ! -perm -001",
        'find "$EVERYDAYAI_LOG_DIR" -maxdepth 1 -type f',
    ):
        assert contract in workflow
    assert "FakeRepository" not in harness
    assert "AsyncMock" not in harness
    assert "build_sandbox_executor_components" in harness
    assert "components.executor.dispatch" in harness
    assert "components.executor.reconcile" in harness
    assert '"-m", "agent_runtime_worker_main"' in harness
    assert 'os.geteuid() != 0 and os.getegid() != 0' in harness


def test_daemon_contract_covers_recovery_cancel_acl_and_zero_residue() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    harness = HARNESS.read_text(encoding="utf-8")
    health = HEALTH.read_text(encoding="utf-8")
    nsjail = (ROOT / "backend/services/agent/runtime/sandbox/nsjail.py").read_text(
        encoding="utf-8",
    )
    for contract in (
        "SIGKILL", "ambiguity_evidence", "retry_after_reconcile",
        "components.executor.cancel", '"cancelled"', "cancel_confirmed_at",
        "has_function_privilege", "has_table_privilege",
        "everydayai_sandbox_worker", "shutil.rmtree",
    ):
        assert contract in harness
    for residue in (
        "Residual nsjail process detected",
        "Residual nsjail cgroup detected",
        "SANDBOX_E2E_PG_BIN/pg_ctl",
        'rm -rf "$SANDBOX_E2E_PG_DATA"',
        "/tmp/everydayai-sandbox-worker",
        "set -uo pipefail",
        "while IFS= read -r child",
        'echo "residue_rc=$residue" >> "$GITHUB_OUTPUT"',
    ):
        assert residue in workflow
    assert "sudo --preserve-env runuser" in workflow
    assert '"--disable_clone_newcgroup"' in nsjail
    assert "wait_ready(" in harness
    assert "SANDBOX_DAEMON_HEALTH_TIMEOUT" in health
    assert "SANDBOX_DAEMON_START_FAILED" in health
    service = (ROOT / "deploy/everydayai-sandbox-worker.service").read_text(
        encoding="utf-8",
    )
    assert 'Environment="EVERYDAYAI_LOG_DIR=/var/log/everydayai/sandbox"' in service
    daemon_segment = workflow.split(
        "- name: Run real Sandbox Worker daemon lifecycle", 1,
    )[1].split("- name:", 1)[0]
    assert "sudo pytest" not in daemon_segment
