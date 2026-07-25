"""迁移 167 的 WeCom runtime 与 Worker Outbox 能力契约。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "migrations" / "167_wecom_role_cutover_completion.sql"
).read_text(encoding="utf-8")
ROLLBACK = (
    ROOT / "migrations" / "rollback"
    / "167_wecom_role_cutover_completion_rollback.sql"
).read_text(encoding="utf-8")


def test_migration_restores_wecom_runtime_facades() -> None:
    assert "resolve_wecom_conversation(UUID, TEXT, TEXT, TEXT, UUID)" in MIGRATION
    assert "record_user_activity(" in MIGRATION
    assert "TO everydayai_wecom_runtime;" in MIGRATION
    assert ") TO everydayai_runtime, everydayai_worker;" in MIGRATION


def test_delivery_functions_are_worker_facades_not_table_grants() -> None:
    for name in (
        "claim_conversation_delivery",
        "renew_conversation_delivery",
        "complete_conversation_delivery",
        "fail_conversation_delivery",
        "worker_get_conversation_delivery_payload",
    ):
        assert f"CREATE FUNCTION {name}(" in MIGRATION
    assert MIGRATION.count("SECURITY DEFINER") == 5
    assert "WECOM_DELIVERY_WORKER_SCOPE_MISMATCH" in MIGRATION
    assert "TO everydayai_worker;" in MIGRATION
    assert "GRANT SELECT" not in MIGRATION
    assert "GRANT UPDATE" not in MIGRATION


def test_payload_requires_active_fencing_token() -> None:
    assert "v_delivery.status <> 'delivering'" in MIGRATION
    assert "v_delivery.lease_token IS DISTINCT FROM p_lease_token" in MIGRATION
    assert "v_delivery.lease_expires_at <= NOW()" in MIGRATION
    assert "'outcome', 'ownership_lost'" in MIGRATION


def test_core_functions_are_hidden_from_service_roles() -> None:
    assert "_claim_conversation_delivery_core" in MIGRATION
    assert (
        "FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, "
        "everydayai_worker;"
    ) in MIGRATION


def test_rollback_removes_facades_and_restores_core_names() -> None:
    assert (
        "DROP FUNCTION worker_get_conversation_delivery_payload(UUID, UUID);"
        in ROLLBACK
    )
    for name in (
        "claim_conversation_delivery",
        "renew_conversation_delivery",
        "complete_conversation_delivery",
        "fail_conversation_delivery",
    ):
        assert f"RENAME TO {name};" in ROLLBACK
