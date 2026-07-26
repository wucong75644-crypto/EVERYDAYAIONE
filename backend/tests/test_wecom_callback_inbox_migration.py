from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
SQL = (BACKEND_DIR / "migrations" / "201_wecom_callback_inbox.sql").read_text()
ROLLBACK = (
    BACKEND_DIR / "migrations" / "rollback"
    / "201_wecom_callback_inbox_rollback.sql"
).read_text()


def test_callback_configuration_is_enterprise_scoped() -> None:
    assert "'wecom.callback_credentials'" in SQL
    assert "'wecom.callback'" in SQL
    assert "get_wecom_callback_bundle()" in SQL
    assert "TO everydayai_worker" in SQL


def test_callback_inbox_is_durable_leased_and_forced() -> None:
    assert "CREATE TABLE wecom_callback_inbox" in SQL
    assert "UNIQUE (org_id, message_key)" in SQL
    assert "FOR UPDATE SKIP LOCKED" in SQL
    assert "lease_expires_at" in SQL
    assert "attempts < 8" in SQL
    assert "cleanup_wecom_callback_inbox" in SQL
    assert "status IN ('completed', 'failed')" in SQL
    assert "ALTER TABLE wecom_callback_inbox FORCE ROW LEVEL SECURITY" in SQL


def test_callback_roles_have_only_capability_access() -> None:
    assert "REVOKE ALL ON TABLE wecom_callback_inbox" in SQL
    assert "enqueue_wecom_callback(UUID, TEXT, TEXT, JSONB)" in SQL
    assert "claim_wecom_callback(INTEGER)" in SQL
    assert "TO everydayai_runtime" in SQL
    assert "TO everydayai_wecom_runtime" in SQL


def test_callback_rollback_removes_inbox_and_restores_contracts() -> None:
    assert "DROP TABLE IF EXISTS wecom_callback_inbox" in ROLLBACK
    assert "configuration_bundle_definitions SET active = FALSE" in ROLLBACK
    assert "e1a54bb65ae327fa4245fcbd34a0752ed8b5755ee6563da9d4389c44b29bd16b" in ROLLBACK
