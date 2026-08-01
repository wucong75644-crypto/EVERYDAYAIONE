from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/223_agent_runtime_production_composition.sql").read_text()
ROLLBACK = (
    ROOT / "migrations/rollback/223_agent_runtime_production_composition_rollback.sql"
).read_text()


def test_roles_are_bootstrap_only_and_all_switches_default_closed() -> None:
    assert "CREATE ROLE" not in SQL
    assert "ALTER ROLE " not in SQL
    assert "PASSWORD " not in SQL
    for switch in (
        "ingress_enabled", "command_claim_enabled", "action_dispatch_enabled",
        "safe_actions_enabled", "non_safe_actions_enabled",
        "code_execute_enabled", "projection_enabled",
        "authorization_recovery_enabled", "tool_confirmation_enabled",
    ):
        assert f"{switch} BOOLEAN NOT NULL DEFAULT FALSE" in SQL
    assert "IF p_org_id IS NULL OR NOT EXISTS(" in SQL


def test_v3_only_resolves_bound_postgres_interaction() -> None:
    body = SQL[SQL.index("CREATE FUNCTION resolve_agent_tool_confirmation_v3"):]
    assert "arguments_hash" in body
    assert "interaction_id" in body
    assert "action_id" in body
    assert "expires_at" in body
    assert "resolve_agent_authorization_interaction" in body
    assert "agent_action_dispatch_intents" not in body.split("END $$;", 1)[0]
    assert "claim_agent_tool_confirmation_notification" in SQL
    assert "complete_agent_tool_confirmation_notification" in SQL
    assert "TO everydayai_projection_worker" in SQL
    assert "confirmation_notified_at" in SQL


def test_dispatch_wrapper_is_the_only_enabled_path() -> None:
    assert "RENAME TO _gate_agent_action_dispatch_220_24" in SQL
    assert "REVOKE ALL ON FUNCTION _gate_agent_action_dispatch_220_24" in SQL
    assert "NOT c.action_dispatch_enabled" in SQL
    assert "NOT c.code_execute_enabled" in SQL
    assert "a.policy_snapshot->>'safety_level'" in SQL
    assert "NOT c.non_safe_actions_enabled" in SQL
    assert "NOT c.tool_confirmation_enabled" in SQL
    assert "tool_confirmation_v3_redis" in SQL
    assert "agent_runtime_org_rollout" in SQL


def test_admin_mutations_are_scoped_idempotent_and_audited() -> None:
    assert "agent_runtime_admin_audit_immutable" in SQL
    assert "org<>p_org_id" in SQL
    assert "request_id UUID NOT NULL UNIQUE" in SQL
    assert "RUNTIME_ADMIN_REQUIRED" in SQL


def test_model_credential_bundle_is_run_fenced_and_tenant_bound() -> None:
    assert "CREATE FUNCTION get_agent_runtime_ai_bundle(" in SQL
    assert "AGENT_RUNTIME_MODEL_CREDENTIAL_SCOPE_INVALID" in SQL
    assert "tenant_actor_user_id() IS DISTINCT FROM s.user_id" in SQL
    assert "tenant_org_id() IS DISTINCT FROM s.org_id" in SQL
    assert "ra.worker_id=btrim(p_worker_id)" in SQL


def test_rollback_fails_closed_on_facts_and_restores_wrapped_functions() -> None:
    for fact in (
        "agent_tool_confirmation_results",
        "agent_runtime_worker_heartbeats",
        "agent_runtime_admin_audit",
        "agent_projection_dead_recoveries",
        "agent_sandbox_jobs",
        "agent_runtime_sessions",
        "agent_session_commands",
        "agent_actions",
        "agent_interactions",
        "agent_runtime_events",
    ):
        assert f"SELECT 1 FROM {fact}" in ROLLBACK
    assert "RENAME TO gate_agent_action_dispatch" in ROLLBACK
    assert "_agent_runtime_223_grant_snapshot" in ROLLBACK
    assert "REVOKE USAGE ON SCHEMA public FROM everydayai_agent_runtime_worker" in (
        ROLLBACK
    )
    assert "everydayai_authorization_worker,everydayai_runtime_admin" in ROLLBACK
    assert "everydayai_sandbox_worker" in SQL[SQL.index("GRANT USAGE ON SCHEMA public"):]
    assert "everydayai_sandbox_worker" in ROLLBACK[ROLLBACK.index("REVOKE USAGE ON SCHEMA public"):]
    restored = ROLLBACK[
        ROLLBACK.index("ALTER FUNCTION _agent_compat_project_command_220_12"):
        ROLLBACK.index("DO $$", ROLLBACK.index(
            "ALTER FUNCTION _agent_compat_project_command_220_12",
        ))
    ]
    assert "GRANT EXECUTE" not in restored
    assert "REVOKE EXECUTE ON FUNCTION" in restored
    assert (
        "FROM PUBLIC,everydayai_worker,everydayai_runtime,"
        in restored
    )
