"""Static contract for the flags-off AR-18 disposable verification lane."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT.parent / ".github/workflows/agent-runtime-disposable.yml").read_text(
    encoding="utf-8"
)
RUNNER = (ROOT.parent / "scripts/run_agent_runtime_ar18_disposable.sh").read_text(
    encoding="utf-8"
)


def test_ar18_runner_is_the_ci_entrypoint_and_keeps_flags_off() -> None:
    assert "scripts/run_agent_runtime_ar18_disposable.sh unit" in WORKFLOW
    assert "scripts/run_agent_runtime_ar18_disposable.sh migration" in WORKFLOW
    assert "scripts/run_agent_runtime_ar18_disposable.sh external" in WORKFLOW
    assert 'AGENT_RUNTIME_INGRESS_ENABLED: "false"' in WORKFLOW
    assert 'AGENT_RUNTIME_PRODUCTION_COMPOSITION_ENABLED: "false"' in WORKFLOW
    assert 'AGENT_RUNTIME_SCHEDULED_WECOM_ENABLED: "false"' in WORKFLOW
    assert 'test "$AGENT_RUNTIME_INGRESS_ENABLED" = false' in WORKFLOW
    assert 'test "$AGENT_RUNTIME_PRODUCTION_COMPOSITION_ENABLED" = false' in WORKFLOW
    assert 'test "$AGENT_RUNTIME_SCHEDULED_WECOM_ENABLED" = false' in WORKFLOW
    assert "production_ready=false" in WORKFLOW


def test_ar18_runner_contains_each_required_lane() -> None:
    required = (
        "test_agent_runtime_task_cancel_intent_postgres_external.py",
        "test_agent_runtime_task_cancel_facade_v2_postgres_external.py",
        "test_agent_runtime_web_task_cancel.py",
        "test_agent_runtime_ar18_b3_provider_cancel_postgres_external.py",
        "test_agent_runtime_ar18_b4_model_cancel_postgres_external.py",
        "test_agent_runtime_ar18_b5_sandbox_cancel_postgres_external.py",
        "test_agent_runtime_ar18_b6_child_cancel_postgres_external.py",
        "test_agent_runtime_ar18_b7_scheduler_control_postgres_external.py",
        "test_agent_runtime_ar18_b7_s2_b1a_terminal_intent_postgres_external.py",
        "test_agent_runtime_ar18_b7_s2_b1b_finalizer_postgres_external.py",
        "test_agent_runtime_ar18_b7_s2_b1c_budget_postgres_external.py",
        "test_agent_runtime_ar18_b7_s2_b1d1_delivery_postgres_external.py",
        "test_agent_runtime_ar18_b7_s2_b1d1b_projection_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_claim_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_dispatch_outcome_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_reconcile_still_unknown_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_terminal_fence_postgres_external.py",
        "test_agent_runtime_model_attempt_postgres_external.py",
        "test_agent_runtime_model_attempt_permissions_postgres_external.py",
        "test_agent_runtime_model_attempt_credits_postgres_external.py",
        "test_agent_runtime_ar18_migration_continuity.py",
        "test_agent_runtime_ar18_migration_continuity_postgres_external.py",
    )
    for test_name in required:
        assert test_name in RUNNER


def test_workflow_runs_the_scheduled_wecom_disposable_lane() -> None:
    assert "Run Scheduled WeCom disposable PostgreSQL contracts" in WORKFLOW
    assert "test_agent_runtime_scheduled_wecom_dispatch_prepare_postgres_external.py" in WORKFLOW
    assert "test_agent_runtime_scheduled_wecom_reconcile_definitive_postgres_external.py" in WORKFLOW
    assert "test_agent_runtime_scheduled_wecom_prepared_recovery_postgres_external.py" in WORKFLOW


def test_model_attempt_lane_explicitly_covers_unknown_and_reconcile() -> None:
    assert "test_unknown_is_non_terminal_and_fencing_fails_closed" in (
        (ROOT / "tests/test_agent_runtime_model_attempt_postgres_external.py").read_text(
            encoding="utf-8"
        )
    )
    assert "test_reconcile_completed_is_atomic" in (
        (ROOT / "tests/test_agent_runtime_model_attempt_permissions_postgres_external.py").read_text(
            encoding="utf-8"
        )
    )


def test_workflow_keeps_scheduled_wecom_and_continuity_lanes() -> None:
    for test_name in (
        "test_agent_runtime_ar18_b7_s2_b1d2a_wecom_foundation_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_dispatch_outcome_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_reconcile_definitive_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_reconcile_org_migration.py",
        "test_agent_runtime_ar18_migration_continuity_postgres_external.py",
    ):
        assert test_name in RUNNER
