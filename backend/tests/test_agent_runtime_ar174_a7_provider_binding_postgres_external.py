from pathlib import Path

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
USER_ID = "44444444-4444-4444-4444-444444444444"


def _apply(url: str, name: str) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations" / name).read_text())


def _rollback(url: str, name: str) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations/rollback" / name).read_text())


def test_a7_22703_runtime_worker_apply_readback_and_rollback(database: str) -> None:
    _apply(database, "227_01_agent_runtime_production_closure.sql")
    _apply(database, "227_02_agent_runtime_production_catalog_seed.sql")
    _apply(database, "227_03_agent_runtime_tenant_provider_bindings.sql")
    try:
        with psycopg.connect(database) as conn:
            assert conn.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid='agent_runtime_tenant_provider_bindings'::regclass"
            ).fetchone() == (True, True)
            assert conn.execute(
                "SELECT has_table_privilege('everydayai_agent_runtime_worker', "
                "'agent_runtime_tenant_provider_bindings','SELECT')"
            ).fetchone()[0] is False
            assert conn.execute(
                "SELECT has_table_privilege('everydayai_worker', "
                "'agent_runtime_tenant_provider_bindings','SELECT')"
            ).fetchone()[0] is False
            assert conn.execute(
                "SELECT has_function_privilege('everydayai_agent_runtime_worker', "
                "'resolve_agent_runtime_tenant_provider_binding(text,text,text,text,uuid)', 'EXECUTE')"
            ).fetchone()[0] is True
            assert conn.execute(
                "SELECT has_function_privilege('everydayai_worker', "
                "'resolve_agent_runtime_tenant_provider_binding(text,text,text,text,uuid)', 'EXECUTE')"
            ).fetchone()[0] is False
            catalog_revision = conn.execute(
                "SELECT catalog_revision FROM agent_runtime_catalog_facts LIMIT 1"
            ).fetchone()[0]
            conn.execute("SET ROLE everydayai_owner")
            conn.execute(
                "INSERT INTO agent_runtime_tenant_provider_bindings "
                "(catalog_revision,tool_name,provider_revision,scope_kind,scope_id,"
                "readiness_hash,service_wiring_ready,credential_available,"
                "capability_enabled,probe_passed) VALUES(%s,'generate_image',"
                "'provider-v1','user',%s,%s,TRUE,FALSE,FALSE,FALSE)",
                (catalog_revision, USER_ID, "a" * 64),
            )
            conn.commit()

        worker_url = database.replace("postgres@", "everydayai_agent_runtime_worker@")
        with psycopg.connect(worker_url) as conn:
            conn.execute("SELECT set_config('app.request_id','ar174-a7-22703',false)")
            conn.execute("SELECT set_config('app.access_kind','agent_runtime',false)")
            conn.execute("SELECT set_config('app.actor_user_id',%s,false)", (USER_ID,))
            result = conn.execute(
                "SELECT resolve_agent_runtime_tenant_provider_binding(%s,'generate_image','user',%s,NULL)",
                (catalog_revision, USER_ID),
            ).fetchone()[0]
            assert result["outcome"] == "found"
            assert result["ready"] is False
            assert result["credential_available"] is False
            missing = conn.execute(
                "SELECT resolve_agent_runtime_tenant_provider_binding(%s,'generate_image','user',%s,NULL)",
                (catalog_revision, "55555555-5555-5555-5555-555555555555"),
            ).fetchone()[0]
            assert missing["outcome"] == "not_found"

        with pytest.raises(psycopg.Error, match="AR174_03_ROLLBACK_GUARD"):
            _rollback(database, "227_03_agent_runtime_tenant_provider_bindings_rollback.sql")

        with psycopg.connect(database) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute("DELETE FROM agent_runtime_tenant_provider_bindings")
            conn.commit()
        _rollback(database, "227_03_agent_runtime_tenant_provider_bindings_rollback.sql")
        _rollback(database, "227_02_agent_runtime_production_catalog_seed_rollback.sql")
        _rollback(database, "227_01_agent_runtime_production_closure_rollback.sql")
        _apply(database, "227_01_agent_runtime_production_closure.sql")
        _apply(database, "227_02_agent_runtime_production_catalog_seed.sql")
        _apply(database, "227_03_agent_runtime_tenant_provider_bindings.sql")
        with psycopg.connect(worker_url) as conn:
            conn.execute("SELECT set_config('app.request_id','ar174-a7-22703-reapply',false)")
            conn.execute("SELECT set_config('app.access_kind','agent_runtime',false)")
            conn.execute("SELECT set_config('app.actor_user_id',%s,false)", (USER_ID,))
            assert conn.execute(
                "SELECT resolve_agent_runtime_tenant_provider_binding(%s,'generate_image','user',%s,NULL)",
                (catalog_revision, USER_ID),
            ).fetchone()[0]["outcome"] == "not_found"
    finally:
        with psycopg.connect(database) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute("DELETE FROM agent_runtime_tenant_provider_bindings")
            conn.commit()
        _rollback(database, "227_03_agent_runtime_tenant_provider_bindings_rollback.sql")
        _rollback(database, "227_02_agent_runtime_production_catalog_seed_rollback.sql")
        _rollback(database, "227_01_agent_runtime_production_closure_rollback.sql")
