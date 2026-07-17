from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "127_actor_tenant_rpc_contract.sql"
ROLLBACK = (
    MIGRATIONS / "rollback" / "127_actor_tenant_rpc_contract_rollback.sql"
)


def test_tenant_rpc_facades_accept_and_validate_org_scope():
    sql = MIGRATION.read_text(encoding="utf-8")

    for function_name in (
        "bind_generation_turn",
        "close_generation_turn",
        "enqueue_generation_turn",
        "enqueue_wecom_generation_turn",
    ):
        assert f"CREATE OR REPLACE FUNCTION {function_name}" in sql
    assert sql.count("p_org_id UUID") == 4
    assert sql.count("IS DISTINCT FROM p_org_id") == 4
    assert sql.count("SECURITY INVOKER") == 4
    assert sql.count("REVOKE ALL ON FUNCTION") == 4


def test_tenant_rpc_facades_delegate_to_existing_atomic_cores():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert sql.count("RETURN bind_generation_turn(") == 1
    assert sql.count("RETURN close_generation_turn(") == 1
    assert sql.count("RETURN enqueue_generation_turn(") == 1
    assert sql.count("RETURN enqueue_wecom_generation_turn(") == 1


def test_tenant_rpc_contract_rollback_only_drops_new_overloads():
    sql = ROLLBACK.read_text(encoding="utf-8")

    assert "JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID" in sql
    assert "JSONB, UUID, UUID, TEXT, JSONB, UUID" in sql
    assert "UUID, UUID, UUID, UUID, TEXT, UUID" in sql
