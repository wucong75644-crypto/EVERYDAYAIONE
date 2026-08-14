"""Static surface constraints for the Scheduled WeCom PostgreSQL adapter."""

from pathlib import Path

from core.db_scope import _rpc_sql


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.joinpath(
    "services/agent/runtime/infrastructure/postgres/scheduled_wecom_delivery.py",
).read_text()
PARSER = ROOT.joinpath(
    "services/agent/runtime/infrastructure/postgres/scheduled_wecom_parsing.py",
).read_text()
PAYLOAD_PARSER = ROOT.joinpath(
    "services/agent/runtime/infrastructure/postgres/scheduled_wecom_payload_parsing.py",
).read_text()
STARTED_RECOVERY_PARSER = ROOT.joinpath(
    "services/agent/runtime/application/scheduled_wecom_parsing.py",
).read_text()
DB_SCOPE = ROOT.joinpath("core/db_scope.py").read_text()

EXPECTED_RPCS = {
    "claim_agent_runtime_scheduled_wecom_delivery_v2",
    "recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1",
    "prepare_agent_runtime_scheduled_wecom_dispatch_v2",
    "start_agent_runtime_scheduled_wecom_dispatch_v2",
    "read_agent_runtime_scheduled_wecom_dispatch_attempt_v2",
    "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
    "claim_agent_runtime_scheduled_wecom_reconcile_v1",
    "renew_agent_runtime_scheduled_wecom_reconcile_lease_v1",
    "read_agent_runtime_scheduled_wecom_reconcile_v1",
    "record_agent_runtime_scheduled_wecom_reconcile_result_v1",
    "record_agent_runtime_scheduled_wecom_reconcile_definitive_result_v1",
    "read_agent_runtime_scheduled_wecom_dispatch_payload_v1",
    "read_agent_runtime_scheduled_wecom_prepared_payload_v1",
    "terminalize_agent_runtime_scheduled_wecom_unsupported_item_v1",
    "recover_agent_runtime_scheduled_wecom_started_dispatch_v1",
}


def test_repository_covers_only_the_authorized_rpc_matrix() -> None:
    quoted = {
        line.split('"')[1]
        for line in REPOSITORY.splitlines()
        if '"' in line and "agent_runtime_scheduled_wecom" in line
    }
    assert quoted == EXPECTED_RPCS
    for forbidden in (
        "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        "start_agent_runtime_scheduled_wecom_dispatch_v1",
        "read_agent_runtime_scheduled_wecom_dispatch_attempt_v1",
        "claim_agent_runtime_scheduled_wecom_delivery_v1",
        ".table(",
    ):
        assert forbidden not in REPOSITORY
    assert "DatabaseAccessKind.WORKER" in REPOSITORY


def test_adapter_and_parser_forbid_untyped_or_sensitive_passthrough() -> None:
    assert "p_receipt_metadata\": metadata_params" in REPOSITORY
    assert "p_readback_metadata\": metadata_params" in REPOSITORY
    assert "set(raw).issubset(allowed)" in PARSER
    assert "set(raw) != keys" in PARSER
    assert "set(raw) != keys" in PAYLOAD_PARSER
    assert "set(raw) != _STARTED_RECOVERY_KEYS" in STARTED_RECOVERY_PARSER
    for forbidden in ("p_secret", "p_access_token", "p_payload", "p_raw_body", "p_free_text"):
        assert f'"{forbidden}"' not in REPOSITORY
    assert ".table(" not in REPOSITORY
    assert "+ 1" not in REPOSITORY
    assert "p_expected_delivery_state_version" in DB_SCOPE
    assert "p_expected_item_state_version" in DB_SCOPE
    assert "p_provider_revision" in DB_SCOPE
    assert "p_delay_seconds" in DB_SCOPE
    assert "p_recovery_worker_id" in REPOSITORY


def test_started_recovery_has_no_resubmit_or_transport_surface() -> None:
    for forbidden in ("resubmit", "send(", "prepare_dispatch(", "start_dispatch("):
        assert forbidden not in STARTED_RECOVERY_PARSER


def test_every_attempt_parser_call_is_operation_specific() -> None:
    assert REPOSITORY.count("parse_attempt(") == 3
    for operation in ("PREPARE", "START", "READ"):
        assert REPOSITORY.count(f"operation=AttemptRpcOperation.{operation}") == 1
    assert "_ATTEMPT_OPERATION_MATRIX" in PARSER


def test_scoped_rpc_casts_scheduled_versions_without_changing_text_revisions() -> None:
    scheduled_sql, _ = _rpc_sql(
        "prepare_agent_runtime_scheduled_wecom_dispatch_v2", {
            "p_expected_delivery_state_version": 1,
            "p_expected_item_state_version": 0,
            "p_provider_revision": 1,
        },
    )
    gateway_sql, _ = _rpc_sql(
        "claim_agent_runtime_model_gateway_v2", {"p_provider_revision": "revision-v1"},
    )
    assert "p_expected_delivery_state_version := %s::bigint" in scheduled_sql
    assert "p_expected_item_state_version := %s::bigint" in scheduled_sql
    assert "p_provider_revision := %s::bigint" in scheduled_sql
    assert "p_provider_revision := %s::bigint" not in gateway_sql


def test_payload_and_terminalization_uuid_casts_do_not_change_text_overloads() -> None:
    uuid_params = {
        "p_request_id": "11111111-1111-1111-1111-111111111111",
        "p_intent_id": "22222222-2222-2222-2222-222222222222",
        "p_item_id": "33333333-3333-3333-3333-333333333333",
        "p_claim_request_id": "44444444-4444-4444-4444-444444444444",
        "p_lease_token": "55555555-5555-5555-5555-555555555555",
    }
    payload_sql, _ = _rpc_sql(
        "read_agent_runtime_scheduled_wecom_dispatch_payload_v1",
        {key: value for key, value in uuid_params.items() if key != "p_request_id"},
    )
    terminal_sql, _ = _rpc_sql(
        "terminalize_agent_runtime_scheduled_wecom_unsupported_item_v1", uuid_params,
    )
    text_sql, _ = _rpc_sql(
        "request_agent_runtime_scheduled_execution_v1",
        {"p_request_id": "manual-request-text"},
    )
    for key in ("p_intent_id", "p_item_id", "p_claim_request_id", "p_lease_token"):
        assert f"{key} := %s::uuid" in payload_sql
        assert f"{key} := %s::uuid" in terminal_sql
    assert "p_request_id := %s::uuid" in terminal_sql
    assert "p_request_id := %s::uuid" not in text_sql
    assert "p_request_id := %s" in text_sql
