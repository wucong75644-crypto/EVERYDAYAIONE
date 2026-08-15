from pathlib import Path


ROOT = Path(__file__).parents[1]
SQL = (
    ROOT / "migrations/228_08q_agent_runtime_single_owner_convergence.sql"
).read_text()
ROLLBACK = (
    ROOT / "migrations/rollback/"
    "228_08q_agent_runtime_single_owner_convergence_rollback.sql"
).read_text()


def test_final_production_contracts_do_not_read_rollout_state() -> None:
    definitions = SQL.split("REVOKE ALL ON FUNCTION", 1)[0]

    assert "agent_runtime_org_rollout" not in definitions
    assert "agent_runtime_rollout_subjects" not in definitions
    assert "submit_runtime_ingress_required_v1" in definitions
    assert "enqueue_wecom_runtime_turn_required_v1" in definitions
    assert "gate_agent_action_dispatch_final_v1" in definitions
    assert "claim_agent_action_dispatch_final_v1" in definitions


def test_final_safe_dispatch_has_no_activation_table_dependency() -> None:
    definitions = SQL.split("REVOKE ALL ON FUNCTION", 1)[0]

    assert "agent_safe_action_activations" not in definitions
    assert "attempt_id" in definitions
    assert "_record_safe_attempt_policy_receipt_v1" in definitions


def test_old_live_contracts_are_revoked_and_rollback_is_available() -> None:
    for name in (
        "runtime_submit_ingress_v6_required",
        "runtime_submit_ingress_v5_owner_transition",
        "enqueue_wecom_runtime_turn_v6",
        "gate_agent_action_dispatch_v2",
        "activate_agent_safe_action",
        "set_agent_runtime_org_rollout",
        "set_agent_runtime_rollout_subject",
    ):
        assert name in SQL
        assert name in ROLLBACK

    assert "DROP FUNCTION submit_runtime_ingress_required_v1" in ROLLBACK
    assert "DROP FUNCTION gate_agent_action_dispatch_final_v1" in ROLLBACK
