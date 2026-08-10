from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT.joinpath(
    "migrations/227_39_agent_runtime_scheduled_wecom_dispatch_prepare.sql",
).read_text()
ROLLBACK = ROOT.joinpath(
    "migrations/rollback/227_39_agent_runtime_scheduled_wecom_dispatch_prepare_rollback.sql",
).read_text()

PUBLIC_FUNCTIONS = (
    "recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1",
    "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
    "start_agent_runtime_scheduled_wecom_dispatch_v1",
    "read_agent_runtime_scheduled_wecom_dispatch_attempt_v1",
)


def _function_body(name: str) -> str:
    marker = f"CREATE FUNCTION {name}("
    start = MIGRATION.find(marker)
    assert start >= 0, name
    sql_end = MIGRATION.find("\n$$;", start)
    plpgsql_end = MIGRATION.find("\nEND $$;", start)
    endings = [value for value in (sql_end, plpgsql_end) if value >= 0]
    assert endings, name
    end = min(endings)
    suffix = "\nEND $$;" if end == plpgsql_end else "\n$$;"
    return MIGRATION[start:end + len(suffix)]


def test_surface_is_narrow_worker_only_and_fixed_search_path() -> None:
    for name in PUBLIC_FUNCTIONS:
        body = _function_body(name)
        assert "SECURITY DEFINER SET search_path=pg_catalog,public" in body
        assert "_assert_agent_runtime_scheduled_wecom_actor()" in body
    assert "TO everydayai_wecom_runtime" in MIGRATION
    assert "GRANT SELECT" not in MIGRATION
    assert "GRANT INSERT" not in MIGRATION
    assert "GRANT UPDATE" not in MIGRATION


def test_prepare_persists_identity_before_start_and_enforces_order() -> None:
    prepare = _function_body("prepare_agent_runtime_scheduled_wecom_dispatch_v1")
    assert "INSERT INTO agent_runtime_scheduled_wecom_dispatch_attempts" in prepare
    assert "'prepared','prepared'" in prepare
    assert "provider_request_id" in prepare and "idempotency_key" in prepare
    assert "read_agent_runtime_scheduled_wecom_dispatch_context_v1" in prepare
    assert "earlier.ordinal<item.ordinal" in prepare
    assert "earlier.status NOT IN('accepted','failed','cancelled')" in prepare
    assert "AGENT_RUNTIME_SCHEDULED_WECOM_PREPARE_CONFLICT" in prepare


def test_start_is_prepared_only_cas_and_readback_is_pure() -> None:
    start = _function_body("start_agent_runtime_scheduled_wecom_dispatch_v1")
    readback = _function_body("read_agent_runtime_scheduled_wecom_dispatch_attempt_v1")
    assert "IF a.status<>'prepared'" in start
    assert "status='dispatch_started'" in start
    assert "dispatch_phase='external_request_started'" in start
    assert "read_agent_runtime_scheduled_wecom_dispatch_context_v1" in start
    assert start.index("d.claim_request_id") < start.index("IF a.status<>'prepared'")
    assert start.index("d.lease_expires_at<=clock_timestamp()") < start.index(
        "IF a.status<>'prepared'",
    )
    assert "STABLE SECURITY DEFINER" in readback
    assert "UPDATE " not in readback and "INSERT " not in readback
    for current_claim_fence in (
        "d.claim_request_id", "d.lease_token", "d.claim_worker_id", "d.lease_expires_at",
    ):
        assert current_claim_fence in readback
    assert "never renews a lease" in MIGRATION
    assert "transport may start only after" in MIGRATION


def test_prepared_recovery_is_durable_narrow_and_evidence_free() -> None:
    recovery = _function_body("recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1")
    for contract in (
        "agent_runtime_scheduled_wecom_prepared_recovery_requests",
        "candidate_attempt.status='prepared'",
        "candidate_item.status='dispatching'",
        "candidate_delivery.lease_expires_at<=clock_timestamp()",
        "candidate_attempt.dispatch_started_at IS NULL",
        "candidate_attempt.unknown_at IS NULL",
        "candidate_attempt.receipt_hash IS NULL",
        "FOR UPDATE OF candidate_delivery,candidate_item,candidate_attempt SKIP LOCKED LIMIT 1",
        "AGENT_RUNTIME_SCHEDULED_WECOM_RECOVERY_REQUEST_CONFLICT",
    ):
        assert contract in recovery
    assert "ENABLE ROW LEVEL SECURITY" in MIGRATION
    assert "FORCE ROW LEVEL SECURITY" in MIGRATION
    assert "RECOVERY_REQUEST_IMMUTABLE" in MIGRATION
    assert "GRANT SELECT" not in MIGRATION and "GRANT UPDATE" not in MIGRATION
    for forbidden in (
        "secret_encrypted", "authorization_header", "http://", "https://", "transport_request",
    ):
        assert forbidden not in MIGRATION.lower()


def test_nullable_scalar_contracts_are_explicit() -> None:
    prepare = _function_body("prepare_agent_runtime_scheduled_wecom_dispatch_v1")
    start = _function_body("start_agent_runtime_scheduled_wecom_dispatch_v1")
    recovery = _function_body("recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1")
    for body in (prepare, start):
        for scalar in (
            "p_expected_delivery_state_version IS NULL",
            "p_expected_item_state_version IS NULL",
            "p_provider_revision IS NULL",
        ):
            assert scalar in body
    assert "p_lease_seconds IS NULL" in recovery
    assert "IS DISTINCT FROM p_expected_delivery_state_version" in start
    assert "IS DISTINCT FROM p_expected_item_state_version" in start


def test_scope_excludes_a2b2_and_rollback_fails_closed_on_attempts() -> None:
    for excluded in (
        "provider_message_id", "mark_unknown",
        "reconcile_token", "retry_agent_runtime", "_aggregate",
    ):
        assert excluded not in MIGRATION
    assert "DISPATCH_ROLLBACK_HAS_FACTS" in ROLLBACK
    assert "EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts)" in ROLLBACK
    assert "EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests)" in ROLLBACK
    assert "agent_runtime_scheduled_wecom_delivery_items WHERE status='dispatching'" in ROLLBACK
    assert "DROP TABLE agent_runtime_scheduled_wecom_prepared_recovery_requests" in ROLLBACK
    for protected_table in (
        "agent_runtime_scheduled_wecom_dispatch_attempts",
        "agent_runtime_scheduled_wecom_delivery_items",
        "agent_runtime_scheduled_wecom_deliveries",
    ):
        assert f"DROP TABLE {protected_table}" not in ROLLBACK
    for name in PUBLIC_FUNCTIONS:
        assert f"DROP FUNCTION {name}" in ROLLBACK
    assert len(MIGRATION.splitlines()) <= 500
