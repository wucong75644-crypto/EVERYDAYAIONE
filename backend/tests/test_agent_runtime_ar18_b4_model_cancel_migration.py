from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_25_agent_runtime_model_gateway_cancel_fence.sql"
ROLLBACK = ROOT / "migrations/rollback/227_25_agent_runtime_model_gateway_cancel_fence_rollback.sql"
PARENT_CANCEL = ROOT / "migrations/218_04_agent_runtime_action_reconciliation.sql"
PARENT_GATEWAY = ROOT / "migrations/227_18_agent_runtime_model_gateway.sql"
PARENT_CANCEL_WRAPPER = ROOT / "migrations/220_25_agent_runtime_authorization_recovery.sql"


def _body(path: Path, function: str) -> str:
    sql = path.read_text(encoding="utf-8")
    match = re.search(
        rf"CREATE (?:OR REPLACE )?FUNCTION {function}\b.*?AS \$\$(.*?)\$\$;",
        sql, re.DOTALL,
    )
    assert match is not None
    return "".join(match.group(1).split()).rstrip(";")


def test_b4_migration_fences_parent_and_preserves_terminal_facts() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    cancel = _body(MIGRATION, "_cancel_agent_run_action_work")
    wrapper = _body(MIGRATION, "cancel_agent_run")
    parent = _body(MIGRATION, "_agent_model_gateway_parent_active_v1")
    session_lock = parent.index("SELECT*INTOssFROMagent_runtime_sessions")
    run_lock = parent.index("SELECT*INTOrFROMagent_runs")
    step_lock = parent.index("SELECT*INTOsFROMagent_model_steps")
    attempt_lock = parent.index("SELECT*INTOaFROMagent_model_attempts")
    operation_lock = parent.rindex(
        "SELECT*INTOoFROMagent_runtime_model_gateway_operations",
    )
    assert session_lock < run_lock < step_lock < attempt_lock < operation_lock
    assert wrapper.index("FROMagent_runtime_sessions") < wrapper.index(
        "FROMagent_runsWHEREid=p_run_idFORUPDATE",
    )
    assert wrapper.index("_lock_agent_model_gateway_cancel_scope_v1") < wrapper.index(
        "agent_actions",
    )
    assert "GATEWAY_PARENT_RUN_CANCELLED_BEFORE_DISPATCH" in cancel
    assert "GATEWAY_PARENT_RUN_CANCELLED_AFTER_DISPATCH" in cancel
    assert "v_operation.statusIN('submitted','claimed')" in cancel
    assert "v_operation.status='dispatching'" in cancel
    assert "v_operation.statusIN('completed','failed','unknown')" not in cancel
    for function in (
        "mark_agent_runtime_model_gateway_dispatched",
        "renew_agent_runtime_model_gateway_operation",
        "finalize_agent_runtime_model_gateway_operation",
    ):
        body = _body(MIGRATION, function)
        assert "_agent_model_gateway_parent_active_v1" in body
        assert "statusIN('completed','failed','unknown')" in body
        assert "'outcome','readback'" in body
    assert "SECURITY DEFINER SET search_path=pg_catalog,public" in sql
    assert "REVOKE ALL ON FUNCTION _agent_model_gateway_parent_active_v1" in sql
    assert "REVOKE ALL ON FUNCTION _lock_agent_model_gateway_cancel_scope_v1" in sql


def test_b4_rollback_is_guarded_and_restores_parent_contracts() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")
    assert "AGENT_MODEL_GATEWAY_CANCEL_FENCE_ROLLBACK_PENDING_FACTS" in sql
    assert "o.status IN('claimed','dispatching')" in sql
    assert "r.status='cancelled'" in sql
    assert "DROP FUNCTION _agent_model_gateway_parent_active_v1" in sql
    assert "DROP FUNCTION _lock_agent_model_gateway_cancel_scope_v1" in sql
    assert _body(ROLLBACK, "cancel_agent_run") == _body(
        PARENT_CANCEL_WRAPPER, "cancel_agent_run",
    )
    assert _body(ROLLBACK, "_cancel_agent_run_action_work") == _body(
        PARENT_CANCEL, "_cancel_agent_run_action_work",
    )
    for function in (
        "mark_agent_runtime_model_gateway_dispatched",
        "renew_agent_runtime_model_gateway_operation",
        "finalize_agent_runtime_model_gateway_operation",
    ):
        assert _body(ROLLBACK, function) == _body(PARENT_GATEWAY, function)
