"""Exercise the narrow MCP application RPC contract in disposable PostgreSQL."""

import getpass
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from tests.test_scheduled_task_draft_delete_integration import postgres_socket


ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "migrations/269_mcp_scoped_application_facades.sql"
ROLLBACK = ROOT / "migrations/rollback/269_mcp_scoped_application_facades_rollback.sql"
SCOPE_MIGRATION = ROOT / "migrations/270_mcp_application_scope_context.sql"
SCOPE_ROLLBACK = ROOT / "migrations/rollback/270_mcp_application_scope_context_rollback.sql"


@pytest.fixture
def database(postgres_socket):
    conn = psycopg.connect(
        host=postgres_socket, dbname="postgres", user=getpass.getuser(),
    )
    conn.execute("CREATE ROLE everydayai LOGIN")
    conn.execute("CREATE ROLE everydayai_runtime")
    conn.execute("CREATE ROLE everydayai_wecom_runtime")
    conn.execute("CREATE ROLE everydayai_worker")
    conn.execute("CREATE ROLE everydayai_sync")
    conn.execute("CREATE ROLE everydayai_owner")
    conn.execute("GRANT USAGE, CREATE ON SCHEMA public TO everydayai_owner")
    conn.execute("""
        CREATE TABLE users(id uuid PRIMARY KEY, status text NOT NULL);
        CREATE TABLE organizations(id uuid PRIMARY KEY, status text NOT NULL);
        CREATE TABLE org_members(
            org_id uuid NOT NULL, user_id uuid NOT NULL,
            role text NOT NULL, status text NOT NULL,
            PRIMARY KEY(org_id, user_id)
        );
        CREATE TABLE organization_mcp_connectors(
            org_id uuid NOT NULL, connector_id text NOT NULL,
            enabled boolean NOT NULL DEFAULT false, health_status text NOT NULL DEFAULT 'unknown',
            last_checked_at timestamptz, last_error_code text,
            updated_by uuid, updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY(org_id, connector_id)
        );
        CREATE TABLE configuration_entries(
            scope_kind text NOT NULL, org_id uuid, user_id uuid,
            config_key text NOT NULL, secret_id uuid, status text NOT NULL,
            version bigint NOT NULL, updated_at timestamptz
        );
        CREATE TABLE secret_records(
            id uuid PRIMARY KEY, scope_kind text NOT NULL, org_id uuid,
            secret_name text NOT NULL, status text NOT NULL,
            payload_ciphertext text, wrapped_dek text, kek_version text,
            payload_version bigint
        );
        CREATE TABLE governance_audit_log(id uuid);
        CREATE FUNCTION _resolve_configuration_bundle(text, text, uuid, uuid)
          RETURNS jsonb LANGUAGE sql AS
          $$ SELECT jsonb_build_object('bundle', $2, 'actor', $3, 'org_id', $4) $$;
        CREATE FUNCTION _write_configuration_entry(
            text, uuid, uuid, text, text, jsonb, jsonb, bigint, uuid
          ) RETURNS jsonb LANGUAGE sql AS
          $$ SELECT jsonb_build_object('key', $5, 'version', $8 + 1, 'configured', true) $$;
        CREATE FUNCTION _record_governance_audit(uuid, text, text, text, text, jsonb)
          RETURNS uuid LANGUAGE sql AS $$ SELECT NULL::uuid $$;
        CREATE FUNCTION get_org_mcp_connector_state(uuid, text)
          RETURNS jsonb LANGUAGE sql AS $$ SELECT '{}'::jsonb $$;
        CREATE FUNCTION set_org_configuration(uuid, text, text, jsonb, jsonb, bigint)
          RETURNS jsonb LANGUAGE sql AS $$ SELECT '{}'::jsonb $$;
        CREATE FUNCTION _disable_configuration_entry(text, uuid, uuid, text, bigint, uuid)
          RETURNS jsonb LANGUAGE sql AS
          $$ SELECT jsonb_build_object('deleted', true, 'version', $5 + 1) $$;
    """)
    conn.execute("GRANT SELECT ON users, organizations, org_members TO everydayai_owner")
    conn.execute("GRANT SELECT, INSERT, UPDATE ON organization_mcp_connectors TO everydayai_owner")
    conn.execute("GRANT SELECT ON configuration_entries, secret_records TO everydayai_owner")
    conn.execute("REVOKE ALL ON FUNCTION get_org_mcp_connector_state(uuid,text) FROM PUBLIC")
    conn.execute("GRANT EXECUTE ON FUNCTION get_org_mcp_connector_state(uuid,text) TO everydayai_runtime")
    conn.execute("REVOKE ALL ON FUNCTION set_org_configuration(uuid,text,text,jsonb,jsonb,bigint) FROM PUBLIC")
    conn.execute("GRANT EXECUTE ON FUNCTION set_org_configuration(uuid,text,text,jsonb,jsonb,bigint) TO everydayai_runtime")
    conn.execute(MIGRATION.read_text())
    conn.execute(SCOPE_MIGRATION.read_text())
    org, other_org, admin, member = [uuid4() for _ in range(4)]
    conn.execute("INSERT INTO organizations VALUES (%s,'active'),(%s,'active')", (org, other_org))
    conn.execute("INSERT INTO users VALUES (%s,'active'),(%s,'active')", (admin, member))
    conn.execute("INSERT INTO org_members VALUES (%s,%s,'owner','active'),(%s,%s,'member','active')",
                 (org, admin, org, member))
    secret_id = uuid4()
    conn.execute("""
        INSERT INTO secret_records(id,scope_kind,org_id,secret_name,status,payload_ciphertext)
        VALUES (%s,'organization',%s,'mcp.test_readonly_bearer_token','active','never-return-this')
    """, (secret_id, org))
    conn.execute("""
        INSERT INTO configuration_entries(scope_kind,org_id,config_key,secret_id,status,version)
        VALUES ('organization',%s,'mcp.test_readonly.bearer_token',%s,'active',7)
    """, (org, secret_id))
    try:
        yield conn, org, other_org, admin, member
    finally:
        try:
            conn.rollback()
            conn.execute("RESET SESSION AUTHORIZATION")
            conn.rollback()
        finally:
            conn.close()


def set_scope(conn, actor, org):
    conn.execute("SELECT set_config('app.actor_user_id', %s, true)", (str(actor),))
    conn.execute("SELECT set_config('app.org_id', %s, true)", (str(org),))
    conn.execute("SELECT set_config('app.access_kind', 'runtime', true)")


def test_application_facades_recheck_scope_and_admin_without_broad_grants(database):
    conn, org, other_org, admin, member = database
    assert conn.execute(
        "SELECT to_regprocedure('public.tenant_actor_user_id()') IS NULL"
    ).fetchone()[0]
    assert conn.execute(
        "SELECT to_regprocedure('public.tenant_org_id()') IS NULL"
    ).fetchone()[0]
    assert not conn.execute("""SELECT has_function_privilege('everydayai',
        'mcp_application_actor_user_id()', 'EXECUTE')""").fetchone()[0]
    assert conn.execute("""SELECT has_function_privilege('everydayai',
        'api_get_org_mcp_connector_state(uuid,text)', 'EXECUTE')""").fetchone()[0]
    assert not conn.execute("""SELECT has_function_privilege('everydayai',
        'get_org_mcp_connector_state(uuid,text)', 'EXECUTE')""").fetchone()[0]
    assert not conn.execute("""SELECT has_function_privilege('everydayai',
        'set_org_configuration(uuid,text,text,jsonb,jsonb,bigint)', 'EXECUTE')""").fetchone()[0]

    conn.execute("SET SESSION AUTHORIZATION everydayai")
    set_scope(conn, admin, org)
    state = conn.execute(
        "SELECT api_get_org_mcp_connector_state(%s,'test-readonly')", (org,),
    ).fetchone()[0]
    assert state["state"] == "disabled" and state["enabled"] is False

    credential = conn.execute(
        "SELECT api_get_org_mcp_connector_credential_status(%s)", (org,),
    ).fetchone()[0]
    assert credential == {
        "key": "mcp.test_readonly.bearer_token", "configured": True,
        "version": 7, "source": "organization", "updated_at": None,
    }
    assert "never-return-this" not in str(credential)

    bundle = conn.execute(
        "SELECT api_get_mcp_test_readonly_bundle()",
    ).fetchone()[0]
    assert bundle["actor"] == str(admin)
    assert bundle["org_id"] == str(org)

    from psycopg.types.json import Jsonb
    stored = conn.execute(
        """SELECT api_set_org_mcp_connector_credential(
             %s,'v1','mcp.test_readonly.bearer_token',NULL,%s,7)""",
        (org, Jsonb({"payload_ciphertext": "cipher-only"})),
    ).fetchone()[0]
    assert stored == {
        "key": "mcp.test_readonly.bearer_token", "version": 8,
        "configured": True,
    }
    assert "cipher-only" not in str(stored)

    enabled = conn.execute(
        "SELECT api_set_org_mcp_connector_enabled(%s,'test-readonly',true)",
        (org,),
    ).fetchone()[0]
    assert enabled["enabled"] is True

    health = conn.execute(
        "SELECT api_record_org_mcp_connector_health(%s,'test-readonly','ready',NULL)",
        (org,),
    ).fetchone()[0]
    assert health["health_status"] == "ready"

    set_scope(conn, admin, org)
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="MCP_CONNECTOR_SCOPE_DENIED"):
        with conn.transaction():
            conn.execute(
                "SELECT api_get_org_mcp_connector_state(%s,'test-readonly')",
                (other_org,),
            )
    set_scope(conn, member, org)
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="MCP_CONNECTOR_AUTHORITY_DENIED"):
        with conn.transaction():
            conn.execute(
                "SELECT api_set_org_mcp_connector_enabled(%s,'test-readonly',false)",
                (org,),
            )


def test_migration_has_an_explicit_rollback():
    from scripts.migration_runner import discover_migrations

    migration = next(
        item for item in discover_migrations() if item.identity == MIGRATION.name
    )
    assert migration.rollback_identity == ROLLBACK.name
    rollback = ROLLBACK.read_text()
    assert "DROP FUNCTION" in rollback
    assert "DROP TABLE" not in rollback


def test_mcp_scope_context_migration_has_an_explicit_rollback():
    from scripts.migration_runner import discover_migrations

    migration = next(
        item for item in discover_migrations()
        if item.identity == SCOPE_MIGRATION.name
    )
    assert migration.rollback_identity == SCOPE_ROLLBACK.name
    rollback = SCOPE_ROLLBACK.read_text()
    assert "tenant_actor_user_id()" in rollback
    assert "DROP FUNCTION" in rollback
    assert "DROP TABLE" not in rollback


def test_mcp_scope_context_rollback_restores_previous_facades(database):
    conn, *_ = database
    conn.execute(SCOPE_ROLLBACK.read_text())
    assert conn.execute(
        "SELECT to_regprocedure('public.mcp_application_actor_user_id()') IS NULL"
    ).fetchone()[0]
    assert conn.execute(
        "SELECT to_regprocedure('public.mcp_application_org_id()') IS NULL"
    ).fetchone()[0]
