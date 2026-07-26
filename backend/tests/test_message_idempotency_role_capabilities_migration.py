"""Isolated-role contract for message generation idempotency."""

from pathlib import Path


ROOT = Path(__file__).parent.parent
MIGRATION = ROOT / "migrations/190_message_idempotency_role_capabilities.sql"
ROLLBACK = (
    ROOT
    / "migrations/rollback/190_message_idempotency_role_capabilities_rollback.sql"
)
TRANSFER = ROOT.parent / "deploy/transfer-runtime-message-ownership.sh"
TRANSFER_ROLLBACK = ROOT.parent / "deploy/rollback-runtime-message-ownership.sh"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_runtime_claim_is_scoped_and_only_runtime_can_execute() -> None:
    sql = _read(MIGRATION)

    assert "session_user <> 'everydayai_runtime'" in sql
    assert "current_setting('app.access_kind', TRUE) <> 'runtime'" in sql
    assert "tenant_actor_user_id() IS DISTINCT FROM p_user_id" in sql
    assert "tenant_org_id() IS DISTINCT FROM p_org_id" in sql
    assert "SECURITY INVOKER" in sql
    assert ") TO everydayai_runtime;" in sql


def test_worker_cleanup_is_actorless_security_definer_capability() -> None:
    sql = _read(MIGRATION)

    assert "session_user <> 'everydayai_worker'" in sql
    assert "current_setting('app.access_kind', TRUE) <> 'worker'" in sql
    assert "tenant_actor_user_id() IS NOT NULL" in sql
    assert "tenant_org_id() IS NOT NULL" in sql
    assert "SECURITY DEFINER" in sql
    assert "TO everydayai_worker;" in sql


def test_both_functions_are_transferred_and_rollback_is_symmetric() -> None:
    signatures = (
        "claim_message_generation_request"
        "(uuid,uuid,uuid,character varying,character,character varying,uuid)",
        "cleanup_expired_message_generation_requests()",
    )
    transfer = _read(TRANSFER)
    transfer_rollback = _read(TRANSFER_ROLLBACK)
    for signature in signatures:
        assert signature in transfer
        assert signature in transfer_rollback

    rollback = _read(ROLLBACK)
    assert "FROM everydayai_runtime;" in rollback
    assert "FROM everydayai_worker;" in rollback
    assert rollback.count("TO everydayai;") == 2
