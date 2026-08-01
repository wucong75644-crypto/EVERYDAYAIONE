from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FOUNDATION = (
    ROOT / "migrations/220_21_agent_runtime_authorization_foundation.sql"
).read_text()
RPCS = (
    ROOT / "migrations/220_22_agent_runtime_authorization_rpcs.sql"
).read_text()
CANCEL = (
    ROOT / "migrations/220_23_agent_runtime_accepted_cancel_override.sql"
).read_text()
ROLLBACK = (
    ROOT
    / "migrations/rollback/220_21_agent_runtime_authorization_foundation_rollback.sql"
).read_text()


def test_authorization_facts_are_force_rls_owner_only() -> None:
    for table in (
        "agent_interactions",
        "agent_authorization_grants",
        "agent_authorization_grant_uses",
        "agent_policy_receipts",
    ):
        assert f"CREATE TABLE {table}" in FOUNDATION
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in FOUNDATION
    assert "REVOKE ALL ON TABLE" in FOUNDATION
    assert "GRANT " not in FOUNDATION


def test_rpc_permissions_are_least_privilege() -> None:
    worker_grant = RPCS.split("TO everydayai_worker;", 1)[0]
    runtime_grant = RPCS.split("TO everydayai_worker;", 1)[1]

    assert "open_agent_authorization_interaction" in worker_grant
    assert "record_agent_policy_receipt" in worker_grant
    assert "resolve_agent_authorization_interaction" in runtime_grant
    assert "TO everydayai_runtime, everydayai_wecom_runtime;" in runtime_grant
    assert "agent_authorization_grants\nTO everydayai_runtime" not in RPCS


def test_grants_receipts_and_cancel_override_fail_closed() -> None:
    assert "grant_kind IN ('action', 'workflow')" in FOUNDATION
    assert "AGENT_AUTHORIZATION_ROLLBACK_HAS_FACTS" in ROLLBACK
    assert "grant_replay_conflict" in RPCS
    assert "receipt_conflict" in RPCS
    assert "'accepted', 'unknown'" in CANCEL
    assert "'pending_reconciliation_count'" in CANCEL
