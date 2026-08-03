from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_06_agent_runtime_tenant_kill_control.sql"
ROLLBACK = ROOT / "migrations/rollback/227_06_agent_runtime_tenant_kill_control_rollback.sql"
ORG = "22222222-2222-2222-2222-222222222222"
ACTOR = "44444444-4444-4444-4444-444444444444"


def _apply(url: str, path: Path) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))


def _admin_call(url: str, function: str, params: tuple[object, ...]) -> object:
    admin_url = url.replace("postgres@", "everydayai_runtime_admin@")
    with psycopg.connect(admin_url) as conn:
        conn.execute("SELECT set_config('app.access_kind','runtime_admin',false)")
        conn.execute("SELECT set_config('app.actor_user_id',%s,false)", (ACTOR,))
        conn.execute("SELECT set_config('app.org_id',%s,false)", (ORG,))
        conn.execute("SELECT set_config('app.request_id',%s,false)", (str(uuid4()),))
        return conn.execute(
            f"SELECT {function}({','.join(['%s'] * len(params))})", params,
        ).fetchone()[0]


def _set_gate(url: str, request_id: str, scope: str, key: str, blocked: bool, expected: int) -> object:
    admin_url = url.replace("postgres@", "everydayai_runtime_admin@")
    with psycopg.connect(admin_url) as conn:
        conn.execute("SELECT set_config('app.access_kind','runtime_admin',false)")
        conn.execute("SELECT set_config('app.actor_user_id',%s,false)", (ACTOR,))
        conn.execute("SELECT set_config('app.org_id',%s,false)", (ORG,))
        return conn.execute(
            "SELECT set_agent_runtime_tenant_gate(%s,%s,%s,%s,%s,%s,%s)",
            (request_id, ORG, scope, key, blocked, expected, "test kill control"),
        ).fetchone()[0]


def test_a_apply_acl_cas_audit_status_and_rollback_guard(database: str) -> None:
    _apply(database, MIGRATION)
    try:
        with psycopg.connect(database) as conn:
            assert conn.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid='agent_runtime_tenant_gate_controls'::regclass"
            ).fetchone() == (True, True)
            for role in (
                "everydayai_agent_runtime_worker", "everydayai_projection_worker",
                "everydayai_authorization_worker", "everydayai_sandbox_worker",
                "everydayai_worker",
            ):
                assert conn.execute(
                    "SELECT has_table_privilege(%s,'agent_runtime_tenant_gate_controls','SELECT')",
                    (role,),
                ).fetchone()[0] is False
            assert conn.execute(
                "SELECT has_function_privilege('everydayai_runtime_admin',"
                "'set_agent_runtime_tenant_gate(uuid,uuid,text,text,boolean,bigint,text)','EXECUTE')"
            ).fetchone()[0] is True
            assert conn.execute(
                "SELECT has_function_privilege('everydayai_worker',"
                "'set_agent_runtime_tenant_gate(uuid,uuid,text,text,boolean,bigint,text)','EXECUTE')"
            ).fetchone()[0] is False
            definition = conn.execute(
                "SELECT proconfig FROM pg_proc WHERE oid='get_agent_runtime_tenant_gate_status(uuid)'::regprocedure"
            ).fetchone()[0]
            assert "search_path=pg_catalog, public" in definition

        request_id = str(uuid4())
        blocked = _set_gate(database, request_id, "tenant", "tenant", True, 0)
        assert blocked["outcome"] == "applied"
        assert blocked["kill_epoch"] == 1
        assert _set_gate(database, request_id, "tenant", "tenant", True, 0)["outcome"] == "already_applied"

        unblocked = _set_gate(database, str(uuid4()), "tenant", "tenant", False, 1)
        assert unblocked["outcome"] == "applied"
        assert unblocked["kill_epoch"] == 1

        def concurrent_provider_gate() -> object:
            return _set_gate(database, str(uuid4()), "provider", "mock-provider", True, 0)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: concurrent_provider_gate(), range(2)))
        assert sorted(item["outcome"] for item in outcomes) == ["applied", "stale_version"]

        status = _admin_call(database, "get_agent_runtime_tenant_gate_status", (ORG,))
        assert status["production_ready"] is False
        assert status["production_enabled"] is False
        assert any(item["scope_key"] == "tenant" for item in status["controls"])

        with pytest.raises(psycopg.Error, match="AR173_A_ROLLBACK_GUARD_FACTS_EXIST"):
            _apply(database, ROLLBACK)
    finally:
        with psycopg.connect(database) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute("DELETE FROM agent_runtime_kill_audit")
            conn.execute("DELETE FROM agent_runtime_owner_fences")
            conn.execute("DELETE FROM agent_runtime_tenant_gate_controls")
            conn.commit()
        _apply(database, ROLLBACK)
        _apply(database, MIGRATION)
        _apply(database, ROLLBACK)
