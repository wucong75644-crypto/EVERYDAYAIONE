from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT.joinpath(
    "migrations/227_38_agent_runtime_scheduled_wecom_claim.sql",
).read_text()
ROLLBACK = ROOT.joinpath(
    "migrations/rollback/227_38_agent_runtime_scheduled_wecom_claim_rollback.sql",
).read_text()

PUBLIC_FUNCTIONS = (
    "claim_agent_runtime_scheduled_wecom_delivery_v1",
    "renew_agent_runtime_scheduled_wecom_delivery_lease_v1",
    "read_agent_runtime_scheduled_wecom_claim_v1",
    "read_agent_runtime_scheduled_wecom_dispatch_context_v1",
)


def _function_body(name: str) -> str:
    marker = f"CREATE FUNCTION {name}("
    start = MIGRATION.find(marker)
    assert start >= 0, name
    end = MIGRATION.find("\nEND $$;", start)
    assert end >= 0, name
    return MIGRATION[start:end + len("\nEND $$;")]


def test_rpc_surface_is_wecom_only_and_fixed_search_path() -> None:
    for name in PUBLIC_FUNCTIONS:
        body = _function_body(name)
        assert "SECURITY DEFINER SET search_path=pg_catalog,public" in body
        assert "_assert_agent_runtime_scheduled_wecom_actor()" in body
    assert "session_user<>'everydayai_wecom_runtime'" in MIGRATION
    assert "current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'worker'" in MIGRATION
    assert "TO everydayai_wecom_runtime" in MIGRATION
    assert "GRANT SELECT" not in MIGRATION
    assert "GRANT UPDATE" not in MIGRATION
    assert "GRANT INSERT" not in MIGRATION


def test_claim_and_readback_contracts_are_narrow_and_fenced() -> None:
    claim = _function_body("claim_agent_runtime_scheduled_wecom_delivery_v1")
    renew = _function_body("renew_agent_runtime_scheduled_wecom_delivery_lease_v1")
    readback = _function_body("read_agent_runtime_scheduled_wecom_claim_v1")
    context = _function_body("read_agent_runtime_scheduled_wecom_dispatch_context_v1")
    assert "FOR UPDATE OF candidate SKIP LOCKED LIMIT 1" in claim
    assert "candidate.status IN('pending','retry_wait')" in claim
    assert "candidate.status='claimed' AND candidate.lease_expires_at<=clock_timestamp()" in claim
    assert "claim_request_id=p_claim_request_id FOR UPDATE" in claim
    assert "AGENT_RUNTIME_SCHEDULED_WECOM_CLAIM_REQUEST_CONFLICT" in claim
    for fence in ("claim_request_id", "lease_token", "claim_worker_id", "state_version"):
        assert fence in renew and fence in context
    assert "lease_expires_at>clock_timestamp()" in renew
    assert "STABLE SECURITY DEFINER" in readback
    assert "UPDATE " not in readback and "gen_random_uuid" not in readback
    assert "Pure current-claim readback" in MIGRATION
    assert "live target failure atomically marks" in MIGRATION
    assert "_agent_runtime_scheduled_wecom_cancel_unavailable" in context


def test_live_validation_uses_only_identity_and_safe_address_facts() -> None:
    live = _function_body("_agent_runtime_scheduled_wecom_live_context")
    for table in (
        "agent_runtime_scheduled_delivery_intents",
        "agent_runtime_scheduled_delivery_targets",
        "agent_runtime_scheduled_delivery_contents",
        "agent_runtime_scheduled_run_bindings",
        "agent_runs",
        "scheduled_task_runs",
        "scheduled_tasks",
        "organizations",
        "org_members",
        "wecom_user_mappings",
        "wecom_chat_targets",
    ):
        assert table in live
    for field in (
        "mapping_id", "target_id", "corp_id", "wecom_userid", "channel",
        "last_chatid", "last_chat_type", "chatid", "chat_type", "is_active",
    ):
        assert field in live
    for forbidden in (
        "wecom_secret_encrypted", "org_configs", "config_value_encrypted",
        "secret_encrypted", "config_snapshot", "capability_snapshot", "prompt", "template_file",
        "raw_body", "storage_ref", "object_path", "http://", "https://",
    ):
        assert forbidden not in MIGRATION.lower()
    assert "agent_runtime_scheduled_wecom_dispatch_attempts" in MIGRATION
    assert "INSERT INTO agent_runtime_scheduled_wecom_dispatch_attempts" not in MIGRATION


def test_live_validation_requires_applied_terminal_application_contract() -> None:
    live = _function_body("_agent_runtime_scheduled_wecom_live_context")
    for contract in (
        "agent_runtime_scheduled_finalization_intents", "finalization.status='applied'",
        "finalization.application_request_id", "finalization.application_hash",
        "finalization.application_receipt", "i.finalization_request_id",
        "i.finalization_application_hash", "binding.owner_status", "r.state_version",
        "finalization.runtime_run_state_version", "WHEN 'completed' THEN 'success'",
        "WHEN 'failed' THEN 'failed'", "ELSE 'skipped'", "q.id,q.task_id,q.org_id,q.status",
    ):
        assert contract in live


def test_rollback_preserves_a1_and_fails_closed_on_a2a_state() -> None:
    assert "AGENT_RUNTIME_SCHEDULED_WECOM_CLAIM_ROLLBACK_HAS_STATE" in ROLLBACK
    assert "state_version<>0" in ROLLBACK
    assert "claim_request_id IS NOT NULL" in ROLLBACK
    assert "DROP TABLE" not in ROLLBACK
    assert "227_37" not in ROLLBACK.replace("227_37 WeCom delivery facts", "")
    for name in PUBLIC_FUNCTIONS:
        assert f"DROP FUNCTION {name}" in ROLLBACK
    assert len(MIGRATION.splitlines()) <= 500
