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
PRIVILEGE_MIGRATION = ROOT / "migrations/271_mcp_application_privileges.sql"
PRIVILEGE_ROLLBACK = ROOT / "migrations/rollback/271_mcp_application_privileges_rollback.sql"
CONFIG_HELPER_PRIVILEGE_MIGRATION = ROOT / "migrations/272_mcp_configuration_helper_privileges.sql"
CONFIG_HELPER_PRIVILEGE_ROLLBACK = ROOT / "migrations/rollback/272_mcp_configuration_helper_privileges_rollback.sql"


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
        CREATE TABLE configuration_definitions(id integer);
        CREATE TABLE configuration_bundle_definitions(id integer);
        CREATE TABLE configuration_policies(
            org_id uuid, config_key text, locked boolean,
            allow_user_override boolean
        );
        CREATE TABLE governance_audit_log(
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid, actor_id uuid, authority text, action text,
            target_kind text, target_key text, request_id text,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb
        );
        CREATE FUNCTION _resolve_configuration_bundle(text, text, uuid, uuid)
          RETURNS jsonb LANGUAGE sql AS
          $$ SELECT jsonb_build_object('bundle', $2, 'actor', $3, 'org_id', $4) $$;
        CREATE FUNCTION _validate_configuration_material(
            text, text, text, jsonb, jsonb
          ) RETURNS jsonb LANGUAGE sql AS $$ SELECT '{}'::jsonb $$;
        CREATE FUNCTION _write_configuration_entry(
            text, uuid, uuid, text, text, jsonb, jsonb, bigint, uuid
          ) RETURNS jsonb LANGUAGE sql AS
          $$ SELECT jsonb_build_object('key', $5, 'version', $8 + 1, 'configured', true) $$;
        CREATE FUNCTION _configuration_scope_id(text, uuid, uuid)
          RETURNS text LANGUAGE sql AS $$ SELECT NULL::text $$;
        CREATE FUNCTION _project_configuration_entry(uuid, boolean)
          RETURNS jsonb LANGUAGE sql AS $$ SELECT '{}'::jsonb $$;
        CREATE FUNCTION _resolve_effective_configuration_item(
            text, text, boolean, uuid, uuid
          ) RETURNS jsonb LANGUAGE sql AS $$ SELECT '{}'::jsonb $$;
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
    for table in (
        "users", "organizations", "org_members", "configuration_definitions",
        "configuration_bundle_definitions", "configuration_policies", "configuration_entries",
        "secret_records", "governance_audit_log",
    ):
        conn.execute(f"ALTER TABLE {table} OWNER TO everydayai")
    for signature in (
        "_resolve_configuration_bundle(text,text,uuid,uuid)",
        "_validate_configuration_material(text,text,text,jsonb,jsonb)",
        "_write_configuration_entry(text,uuid,uuid,text,text,jsonb,jsonb,bigint,uuid)",
        "_configuration_scope_id(text,uuid,uuid)",
        "_project_configuration_entry(uuid,boolean)",
        "_resolve_effective_configuration_item(text,text,boolean,uuid,uuid)",
    ):
        conn.execute(f"ALTER FUNCTION {signature} OWNER TO everydayai")
    conn.execute(
        "ALTER TABLE organization_mcp_connectors OWNER TO everydayai_owner"
    )
    conn.execute(
        """ALTER TABLE governance_audit_log ENABLE ROW LEVEL SECURITY;
           ALTER TABLE governance_audit_log FORCE ROW LEVEL SECURITY;
           CREATE POLICY governance_audit_owner_only ON governance_audit_log
             TO everydayai_owner USING (current_user = 'everydayai_owner')
             WITH CHECK (current_user = 'everydayai_owner')"""
    )
    conn.execute("REVOKE ALL ON FUNCTION get_org_mcp_connector_state(uuid,text) FROM PUBLIC")
    conn.execute("GRANT EXECUTE ON FUNCTION get_org_mcp_connector_state(uuid,text) TO everydayai_runtime")
    conn.execute("REVOKE ALL ON FUNCTION set_org_configuration(uuid,text,text,jsonb,jsonb,bigint) FROM PUBLIC")
    conn.execute("GRANT EXECUTE ON FUNCTION set_org_configuration(uuid,text,text,jsonb,jsonb,bigint) TO everydayai_runtime")
    conn.execute("""
        REVOKE ALL ON FUNCTION
            _resolve_configuration_bundle(text,text,uuid,uuid),
            _validate_configuration_material(text,text,text,jsonb,jsonb),
            _write_configuration_entry(text,uuid,uuid,text,text,jsonb,jsonb,bigint,uuid),
            _configuration_scope_id(text,uuid,uuid),
            _project_configuration_entry(uuid,boolean),
            _resolve_effective_configuration_item(text,text,boolean,uuid,uuid)
        FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
             everydayai_worker, everydayai_sync
    """)
    conn.execute(MIGRATION.read_text())
    conn.execute(SCOPE_MIGRATION.read_text())
    conn.execute(PRIVILEGE_MIGRATION.read_text())
    assert not conn.execute(
        "SELECT has_function_privilege('everydayai_owner', "
        "'_write_configuration_entry(text,uuid,uuid,text,text,jsonb,jsonb,bigint,uuid)', 'EXECUTE')"
    ).fetchone()[0]
    assert not conn.execute(
        "SELECT has_table_privilege('everydayai_owner', 'configuration_policies', 'SELECT')"
    ).fetchone()[0]
    conn.execute(CONFIG_HELPER_PRIVILEGE_MIGRATION.read_text())
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
    assert conn.execute(
        "SELECT has_table_privilege('everydayai_owner', 'governance_audit_log', 'INSERT')"
    ).fetchone()[0]
    assert not conn.execute(
        "SELECT has_table_privilege('everydayai_owner', 'governance_audit_log', 'SELECT')"
    ).fetchone()[0]
    assert conn.execute(
        "SELECT has_table_privilege('everydayai_owner', 'configuration_policies', 'SELECT')"
    ).fetchone()[0]
    for signature in (
        "_validate_configuration_material(text,text,text,jsonb,jsonb)",
        "_write_configuration_entry(text,uuid,uuid,text,text,jsonb,jsonb,bigint,uuid)",
        "_configuration_scope_id(text,uuid,uuid)",
        "_project_configuration_entry(uuid,boolean)",
        "_resolve_effective_configuration_item(text,text,boolean,uuid,uuid)",
        "_resolve_configuration_bundle(text,text,uuid,uuid)",
    ):
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_owner', %s, 'EXECUTE')",
            (signature,),
        ).fetchone()[0], signature
        assert not conn.execute(
            "SELECT has_function_privilege('everydayai_runtime', %s, 'EXECUTE')",
            (signature,),
        ).fetchone()[0], signature
    assert conn.execute(
        """SELECT pg_get_userbyid(proowner) = 'everydayai_owner'
             FROM pg_proc WHERE oid = 'api_set_org_mcp_connector_credential(uuid,text,text,jsonb,jsonb,bigint)'::regprocedure"""
    ).fetchone()[0]

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

    deleted = conn.execute(
        "SELECT api_delete_org_mcp_connector_credential(%s,'mcp.test_readonly.bearer_token',8)",
        (org,),
    ).fetchone()[0]
    assert deleted == {"deleted": True, "version": 9}

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

    conn.execute("RESET SESSION AUTHORIZATION")
    audit_rows = conn.execute(
        """SELECT org_id, actor_id, action, target_kind, target_key, metadata
             FROM governance_audit_log ORDER BY action""",
    ).fetchall()
    assert {row[2] for row in audit_rows} == {
        "mcp_connector.delete_credential",
        "mcp_connector.set_credential",
        "mcp_connector.set_enabled",
    }
    assert all(row[0] == org and row[1] == admin for row in audit_rows)
    assert all(row[3:5] == ("mcp_connector", "test-readonly") for row in audit_rows)
    assert "never-return-this" not in str(audit_rows)
    assert "cipher-only" not in str(audit_rows)


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


def test_mcp_privilege_migration_has_an_explicit_rollback():
    from scripts.migration_runner import discover_migrations

    migration = next(
        item for item in discover_migrations()
        if item.identity == PRIVILEGE_MIGRATION.name
    )
    assert migration.rollback_identity == PRIVILEGE_ROLLBACK.name
    rollback = PRIVILEGE_ROLLBACK.read_text()
    assert "REVOKE SELECT ON TABLE public.users" in rollback
    assert "DROP FUNCTION" in rollback
    assert "DROP TABLE" not in rollback


def test_mcp_configuration_helper_privileges_have_an_explicit_rollback():
    from scripts.migration_runner import discover_migrations

    migration = next(
        item for item in discover_migrations()
        if item.identity == CONFIG_HELPER_PRIVILEGE_MIGRATION.name
    )
    assert migration.rollback_identity == CONFIG_HELPER_PRIVILEGE_ROLLBACK.name
    rollback = CONFIG_HELPER_PRIVILEGE_ROLLBACK.read_text()
    assert "REVOKE EXECUTE" in rollback
    assert "REVOKE SELECT ON TABLE public.configuration_policies" in rollback


def test_mcp_configuration_helper_privilege_rollback_restores_narrow_grants(database):
    conn, *_ = database
    assert conn.execute(
        "SELECT has_table_privilege('everydayai_owner', 'configuration_policies', 'SELECT')"
    ).fetchone()[0]
    assert conn.execute(
        "SELECT has_function_privilege('everydayai_owner', "
        "'_write_configuration_entry(text,uuid,uuid,text,text,jsonb,jsonb,bigint,uuid)', 'EXECUTE')"
    ).fetchone()[0]

    conn.execute(CONFIG_HELPER_PRIVILEGE_ROLLBACK.read_text())

    assert not conn.execute(
        "SELECT has_table_privilege('everydayai_owner', 'configuration_policies', 'SELECT')"
    ).fetchone()[0]
    assert not conn.execute(
        "SELECT has_function_privilege('everydayai_owner', "
        "'_write_configuration_entry(text,uuid,uuid,text,text,jsonb,jsonb,bigint,uuid)', 'EXECUTE')"
    ).fetchone()[0]


def test_mcp_privilege_rollback_restores_audit_and_revokes_legacy_grants(database):
    conn, *_ = database
    assert conn.execute(
        "SELECT has_table_privilege('everydayai_owner', 'users', 'SELECT')"
    ).fetchone()[0]

    conn.execute(PRIVILEGE_ROLLBACK.read_text())

    assert conn.execute(
        "SELECT to_regprocedure('public.mcp_record_connector_audit(uuid,text,text,text,text,jsonb)') IS NULL"
    ).fetchone()[0]
    assert not conn.execute(
        "SELECT has_table_privilege('everydayai_owner', 'users', 'SELECT')"
    ).fetchone()[0]
    definition = conn.execute(
        "SELECT pg_get_functiondef('api_set_org_mcp_connector_enabled(uuid,text,boolean)'::regprocedure)"
    ).fetchone()[0]
    assert "_record_governance_audit" in definition
    assert "mcp_record_connector_audit" not in definition
