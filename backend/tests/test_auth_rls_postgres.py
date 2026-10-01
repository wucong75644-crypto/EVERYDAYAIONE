"""Actual PostgreSQL login role, RLS, auth writes and reversible migration.

Uses disposable socket-only databases, never project or production credentials.
"""
from contextlib import contextmanager
import getpass
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from core.exceptions import AuthenticationError, NotFoundError
from core.local_db import LocalDBClient
from core.security import hash_password
from services.auth_service import AuthService
from tests.test_scheduled_task_draft_delete_integration import postgres_socket  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
UP = ROOT / "backend/migrations/266_restore_legacy_auth_service_rls.sql"
DOWN = ROOT / "backend/migrations/rollback/266_restore_legacy_auth_service_rls_rollback.sql"
TABLES = ("users", "organizations", "org_members", "org_configs", "refresh_tokens",
          "wecom_user_mappings", "credits_history")
SPEC = importlib.util.spec_from_file_location("migration_target", ROOT / "deploy/verify-migration-target.py")
TARGET = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TARGET)


@pytest.fixture
def database(postgres_socket):
    name = "auth_" + uuid4().hex
    with psycopg.connect(host=postgres_socket, dbname="postgres", user=getpass.getuser(),
                         autocommit=True) as cluster:
        for role in ("everydayai", "everydayai_owner", "everydayai_runtime", "auth_outsider"):
            if not cluster.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                cluster.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
        cluster.execute("GRANT everydayai TO auth_outsider")
        cluster.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            with psycopg.connect(host=postgres_socket, dbname=name, user=getpass.getuser(),
                                 autocommit=True) as admin:
                admin.execute("""CREATE TABLE users (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), phone text, password_hash text,
                    status text DEFAULT 'active', nickname text DEFAULT 'test', avatar_url text,
                    role text DEFAULT 'user', credits int DEFAULT 100, login_methods jsonb DEFAULT '["phone"]',
                    created_by text, created_at timestamptz DEFAULT now(), last_login_at timestamptz);
                    CREATE TABLE refresh_tokens (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid, token_hash text UNIQUE,
                    expires_at timestamptz, revoked bool DEFAULT false, revoked_at timestamptz);
                    CREATE TABLE credits_history (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid, change_amount int,
                    balance_after int, change_type text, description text);
                """)
                for table in ("organizations", "org_members", "org_configs", "wecom_user_mappings"):
                    admin.execute(sql.SQL("CREATE TABLE {} (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), value text)")
                                  .format(sql.Identifier(table)))
                for table in TABLES:
                    admin.execute(sql.SQL("ALTER TABLE {} OWNER TO everydayai_owner; "
                                          "ALTER TABLE {} ENABLE ROW LEVEL SECURITY; "
                                          "GRANT ALL ON {} TO everydayai, everydayai_runtime, auth_outsider; "
                                          "CREATE POLICY original_tenant ON {} FOR ALL "
                                          "TO everydayai_owner, everydayai_runtime USING (false) WITH CHECK (false)")
                                  .format(*[sql.Identifier(table)] * 4))
                user = admin.execute("INSERT INTO users(phone,password_hash) VALUES (%s,%s) RETURNING id",
                                     ("13800138000", hash_password("Test-password-266"))).fetchone()[0]
                for table in ("organizations", "org_members", "org_configs", "wecom_user_mappings"):
                    admin.execute(sql.SQL("INSERT INTO {}(value) VALUES ('retained')").format(sql.Identifier(table)))
                with psycopg.connect(host=postgres_socket, dbname=name, user="everydayai",
                                     autocommit=True) as app:
                    yield admin, app, user, name
        finally:
            cluster.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def auth_service(app, monkeypatch):
    class Pool:
        @contextmanager
        def connection(self):
            yield app
    db = LocalDBClient.__new__(LocalDBClient)
    db._pool = Pool()
    # Optional telemetry is a separate SECURITY DEFINER RPC, already permitted
    # in production. This fixture exercises the direct auth SQL and real tokens.
    monkeypatch.setattr("services.auth_service.record_user_activity", lambda *a, **kw: None)
    service = AuthService(db)
    monkeypatch.setattr(service, "_verify_code", AsyncMock(return_value=True))
    return service


@pytest.mark.asyncio
async def test_real_password_and_valid_sms_reproduce_then_login_refresh_logout(database, monkeypatch):
    admin, app, user, _ = database
    service = auth_service(app, monkeypatch)
    with pytest.raises(AuthenticationError):
        await service.login_by_password("13800138000", "Test-password-266")
    with pytest.raises(NotFoundError):
        await service.login_by_phone("13800138000", "test-valid-code")
    admin.execute(UP.read_text())
    for kind in ("password", "phone"):
        result = await (service.login_by_password("13800138000", "Test-password-266")
                        if kind == "password" else service.login_by_phone("13800138000", "test-valid-code"))
        assert result["user"]["id"] == str(user)
        assert result["token"]["access_token"]
        raw = result["token"]["refresh_token"]
        assert not app.execute("SELECT 1 FROM refresh_tokens WHERE token_hash=%s", (raw,)).fetchone()
        refreshed = await service.refresh_access_token(raw)
        assert refreshed["token"]["refresh_token"] != raw
        service.revoke_user_refresh_tokens(str(user))
        with pytest.raises(AuthenticationError):
            await service.refresh_access_token(refreshed["token"]["refresh_token"])
    with pytest.raises(AuthenticationError):
        await service.login_by_password("13800138000", "wrong-password")
    service._verify_code.return_value = False
    with pytest.raises(Exception, match="验证码"):
        await service.login_by_phone("13800138000", "invalid")
    app.execute("UPDATE users SET status='disabled' WHERE id=%s", (user,))
    with pytest.raises(AuthenticationError, match="禁用"):
        await service.login_by_password("13800138000", "Test-password-266")


@pytest.mark.asyncio
async def test_registration_insert_returning_and_credit_gift(database, monkeypatch):
    admin, app, _, _ = database
    admin.execute(UP.read_text())
    service = auth_service(app, monkeypatch)
    result = await service.register_by_phone("13900139000", "valid", password="New-test-password")
    assert result["token"]["refresh_token"]
    assert app.execute("SELECT change_amount FROM credits_history WHERE user_id=%s",
                       (result["user"]["id"],)).fetchone() == (100,)


def test_qr_config_reads_mapping_bind_unbind_and_disallowed_commands(database):
    admin, app, user, _ = database
    assert app.execute("SELECT count(*) FROM organizations").fetchone() == (0,)
    assert app.execute("SELECT count(*) FROM org_configs").fetchone() == (0,)
    admin.execute(UP.read_text())
    for table in ("organizations", "org_configs", "org_members", "wecom_user_mappings"):
        assert app.execute(sql.SQL("SELECT value FROM {}").format(sql.Identifier(table))).fetchone() == ("retained",)
    for table in ("org_members", "wecom_user_mappings"):
        assert app.execute(sql.SQL("INSERT INTO {}(value) VALUES ('new') RETURNING value")
                           .format(sql.Identifier(table))).fetchone() == ("new",)
    assert app.execute("DELETE FROM wecom_user_mappings WHERE value='new' RETURNING value").fetchone() == ("new",)
    assert app.execute("DELETE FROM users WHERE id=%s RETURNING id", (user,)).fetchone() is None
    for table in ("organizations", "org_configs", "org_members", "credits_history", "wecom_user_mappings"):
        column = "change_amount" if table == "credits_history" else "value"
        assert app.execute(sql.SQL("UPDATE {} SET {}={} RETURNING id").format(
            sql.Identifier(table), sql.Identifier(column), sql.Identifier(column))).fetchone() is None
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app.execute("INSERT INTO org_configs(value) VALUES ('forbidden')")


@pytest.mark.asyncio
async def test_real_qr_route_reads_and_decrypts_org_credentials(database, monkeypatch):
    from api.routes.wecom_auth import get_qr_url
    from core.config import get_settings
    from core.crypto import aes_encrypt, generate_encrypt_key
    from fastapi import HTTPException
    from services.wecom_oauth_service import WecomOAuthService

    admin, app, _, _ = database
    admin.execute("ALTER TABLE organizations ADD status text DEFAULT 'active', "
                  "ADD wecom_corp_id text, ADD encrypt_key text; "
                  "ALTER TABLE org_configs ADD org_id uuid, ADD config_key text, ADD config_value_encrypted text")
    key = generate_encrypt_key()
    org = admin.execute("UPDATE organizations SET wecom_corp_id='test-corp', encrypt_key=%s RETURNING id", (key,)).fetchone()[0]
    admin.execute("UPDATE org_configs SET org_id=%s, config_key='wecom_agent_id', config_value_encrypted=%s",
                  (org, aes_encrypt("1000001", key)))
    service = auth_service(app, monkeypatch)
    oauth = WecomOAuthService(service.db)
    monkeypatch.setattr(oauth, "generate_state", AsyncMock(return_value="test-state"))
    monkeypatch.setattr(get_settings(), "wecom_oauth_redirect_uri", "https://example.com/callback")
    with pytest.raises(HTTPException) as missing:
        await get_qr_url(user_id=None, db=service.db, org_id=str(org), svc=oauth)
    assert missing.value.status_code == 404
    admin.execute(UP.read_text())
    result = await get_qr_url(user_id=None, db=service.db, org_id=str(org), svc=oauth)
    assert "test-corp" in result["qr_url"] and "1000001" in result["qr_url"]
    oauth.generate_state.assert_awaited_once_with("login", user_id=None, org_id=str(org))


def test_reapply_rollback_preserves_data_rls_and_original_policies(database):
    admin, app, _, _ = database
    original = admin.execute("SELECT tablename,policyname,roles,cmd,qual,with_check FROM pg_policies ORDER BY 1,2").fetchall()
    flags = admin.execute("SELECT relname,relowner,relrowsecurity,relforcerowsecurity FROM pg_class "
                          "WHERE relname=ANY(%s) ORDER BY 1", (list(TABLES),)).fetchall()
    admin.execute(UP.read_text())
    admin.execute(UP.read_text())
    assert app.execute("SELECT count(*) FROM users").fetchone() == (1,)
    admin.execute(DOWN.read_text())
    assert app.execute("SELECT count(*) FROM users").fetchone() == (0,)
    assert admin.execute("SELECT count(*) FROM users").fetchone() == (1,)
    assert admin.execute("SELECT tablename,policyname,roles,cmd,qual,with_check FROM pg_policies ORDER BY 1,2").fetchall() == original
    assert admin.execute("SELECT relname,relowner,relrowsecurity,relforcerowsecurity FROM pg_class "
                         "WHERE relname=ANY(%s) ORDER BY 1", (list(TABLES),)).fetchall() == flags
    admin.execute(UP.read_text())
    assert app.execute("SELECT count(*) FROM users").fetchone() == (1,)


@pytest.mark.parametrize("role", ["everydayai_runtime", "auth_outsider"])
def test_no_access_for_other_login_roles_even_after_set_role(database, postgres_socket, role):
    admin, _, _, name = database
    admin.execute(UP.read_text())
    with psycopg.connect(host=postgres_socket, dbname=name, user=role, autocommit=True) as other:
        if role == "auth_outsider":
            other.execute("SET ROLE everydayai")
        for table in TABLES:
            assert other.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone() == (0,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            other.execute("INSERT INTO users(phone) VALUES ('forbidden')")


def test_local_migration_target_identity_and_lock_cleanup(database, postgres_socket):
    admin, app, _, _ = database
    database_name, port = TARGET.verify_target(app, lambda db, port, query: "t" if admin.execute(query).fetchone()[0] else "f")
    assert database_name == app.info.dbname and port > 0
    with psycopg.connect(host=postgres_socket, dbname="postgres", user=getpass.getuser(), autocommit=True) as wrong:
        with pytest.raises(RuntimeError, match="MISMATCH"):
            TARGET.verify_target(app, lambda db, port, query: "t" if wrong.execute(query).fetchone()[0] else "f")
    assert app.execute("SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'").fetchone() == (0,)
    def unavailable(*args):
        raise RuntimeError("admin unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        TARGET.verify_target(app, unavailable)
    assert app.execute("SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'").fetchone() == (0,)
