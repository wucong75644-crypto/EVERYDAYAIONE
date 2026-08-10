from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT.joinpath(
    "migrations/227_44_agent_runtime_scheduled_wecom_reconcile_definitive.sql",
).read_text()
ROLLBACK = ROOT.joinpath(
    "migrations/rollback/227_44_agent_runtime_scheduled_wecom_reconcile_definitive_rollback.sql",
).read_text()


def _function_body(name: str) -> str:
    marker = f"CREATE FUNCTION {name}("
    start = MIGRATION.find(marker)
    assert start >= 0, name
    end = MIGRATION.find("\nEND $$;", start)
    assert end >= 0, name
    return MIGRATION[start : end + len("\nEND $$;")]


def test_definitive_contract_maps_only_frozen_unknown_attempts() -> None:
    rpc = _function_body(
        "record_agent_runtime_scheduled_wecom_reconcile_definitive_result_v1",
    )
    for contract in (
        "p_reconcile_result NOT IN('accepted','rejected')",
        "d.status NOT IN('unknown','reconcile_required')",
        "item.status NOT IN('unknown','reconcile_required')",
        "IS DISTINCT FROM('unknown','ambiguous'",
        "status=p_reconcile_result",
        "dispatch_phase='receipt_recorded'",
        "resolved_at=resolved",
        "WHEN 'accepted' THEN 'accepted' ELSE 'failed'",
        "status='pending'",
        "WHEN accepted_count=item_count THEN 'completed'",
        "WHEN accepted_count>0 THEN 'partial' ELSE 'failed'",
    ):
        assert contract in rpc
    assert "unknown_at=" not in rpc
    assert "_agent_runtime_scheduled_wecom_live_context" not in rpc
    assert "reconcile_lease_expires_at>" not in rpc


def test_ledger_hash_metadata_and_request_readback_are_failure_closed() -> None:
    rpc = _function_body(
        "record_agent_runtime_scheduled_wecom_reconcile_definitive_result_v1",
    )
    assert "_agent_runtime_scheduled_wecom_receipt_metadata_valid" in MIGRATION
    assert "_agent_runtime_scheduled_wecom_reconcile_readback_hash" in MIGRATION
    assert "WHERE request_id=p_request_id" in rpc
    assert "_reconcile_definitive_json(request,'readback')" in rpc
    assert "WHERE claim_request_id=p_claim_request_id" in rpc
    assert "BEFORE UPDATE OR DELETE" in MIGRATION
    for forbidden in ("still_unknown", "payload", "secret_encrypted", "provider call"):
        assert forbidden not in MIGRATION.lower()


def test_global_namespace_is_bidirectional_and_rollback_restores_227_43() -> None:
    for guard in (
        "_agent_runtime_scheduled_wecom_reconcile_request_guard",
        "_agent_runtime_scheduled_wecom_continuation_request_guard",
        "_agent_runtime_scheduled_wecom_reconcile_result_request_guard",
        "_agent_runtime_scheduled_wecom_legacy_request_guard",
    ):
        assert f"CREATE OR REPLACE FUNCTION {guard}" in MIGRATION
        assert f"CREATE OR REPLACE FUNCTION {guard}" in ROLLBACK
    assert MIGRATION.count(
        "agent_runtime_scheduled_wecom_reconcile_definitive_requests WHERE request_id=",
    ) >= 4
    assert "AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_DEFINITIVE_ROLLBACK_HAS_FACTS" in ROLLBACK
    assert "DROP TABLE agent_runtime_scheduled_wecom_reconcile_definitive_requests" in ROLLBACK
    assert "agent_runtime_scheduled_wecom_reconcile_definitive_requests" not in ROLLBACK.split(
        "DROP TABLE agent_runtime_scheduled_wecom_reconcile_definitive_requests;", 1,
    )[1]


def test_rpc_is_worker_only_fixed_search_path_and_size_bounded() -> None:
    rpc = _function_body(
        "record_agent_runtime_scheduled_wecom_reconcile_definitive_result_v1",
    )
    assert "SECURITY DEFINER SET search_path=pg_catalog,public" in rpc
    assert "_assert_agent_runtime_scheduled_wecom_actor()" in rpc
    assert "TO everydayai_wecom_runtime" in MIGRATION
    assert "GRANT SELECT" not in MIGRATION
    assert len(MIGRATION.splitlines()) <= 500
