from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import USER, database


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "228_08j_agent_runtime_web_scope_owner_atomicity.sql"
ROLLBACK = "228_08j_agent_runtime_web_scope_owner_atomicity_rollback.sql"
LEGACY_USER = UUID("77777777-7777-7777-7777-777777777777")


def _execute(url: str, path: Path) -> None:
    with psycopg.connect(url) as connection:
        with connection.transaction():
            connection.execute(path.read_text())


def _apply(url: str, name: str) -> None:
    _execute(url, ROOT / "migrations" / name)


def _scope(url: str, conversation_id: UUID) -> str | None:
    with psycopg.connect(url) as connection:
        return connection.execute(
            "SELECT scope_id FROM conversations WHERE id=%s",
            (conversation_id,),
        ).fetchone()[0]


def test_apply_readback_rollback_reapply_preserves_legacy_scope(
    database: str,
) -> None:
    for name in (
        "227_01_agent_runtime_production_closure.sql",
        "227_02_agent_runtime_production_catalog_seed.sql",
        "227_06_agent_runtime_tenant_kill_control.sql",
        "227_07_agent_runtime_kill_epoch_fence.sql",
        "227_13_agent_runtime_additive_ingress_compatibility.sql",
        "227_14_agent_runtime_owner_transition.sql",
        "227_15_agent_runtime_owner_rpc_acl_closure.sql",
        "227_61_agent_runtime_web_ingress_required.sql",
    ):
        _apply(database, name)

    legacy_conversation = uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS "
            "source TEXT NOT NULL DEFAULT 'web'"
        )
        connection.execute(
            "INSERT INTO conversations"
            "(id,user_id,org_id,source,scope_type,scope_id) "
            "VALUES(%s,%s,NULL,'web','user',NULL)",
            (legacy_conversation, LEGACY_USER),
        )
        connection.commit()

    _apply(database, MIGRATION)
    assert _scope(database, legacy_conversation) == str(LEGACY_USER)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT prior_scope_id,adopted_scope_id "
            "FROM agent_runtime_conversation_scope_adoptions "
            "WHERE conversation_id=%s",
            (legacy_conversation,),
        ).fetchone() == (None, str(LEGACY_USER))
        function_definition = connection.execute(
            "SELECT pg_get_functiondef(" 
            "'runtime_submit_ingress_v6_required(uuid,uuid,uuid,text,text,uuid,"
            "text,text,text,text,text,text,uuid,text,text,text,jsonb,jsonb,text,jsonb,"
            "uuid,text,uuid,uuid,uuid,text)'::regprocedure)"
        ).fetchone()[0]
        assert "runtime_pending" in function_definition
        assert connection.execute(
            "SELECT has_function_privilege('everydayai_runtime',%s,'execute'),"
            "has_function_privilege('everydayai_runtime',%s,'execute')",
            (
                "runtime_submit_ingress_v6_required(uuid,uuid,uuid,text,text,uuid,"
                "text,text,text,text,text,text,uuid,text,text,text,jsonb,jsonb,text,"
                "jsonb,uuid,text,uuid,uuid,uuid,text)",
                "mark_prepared_task_runtime_owned(uuid,uuid,uuid,uuid,uuid,uuid,"
                "uuid,uuid,text,text,text,uuid,uuid)",
            ),
        ).fetchone() == (True, False)
        connection.execute("SET ROLE everydayai_owner")
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO conversations"
                    "(id,user_id,org_id,source,scope_type,scope_id) "
                    "VALUES(%s,%s,NULL,'web','user',NULL)",
                    (uuid4(), USER),
                )

    _execute(database, ROOT / "migrations/rollback" / ROLLBACK)
    assert _scope(database, legacy_conversation) is None
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT to_regclass('agent_runtime_conversation_scope_adoptions')"
        ).fetchone()[0] is None

    _apply(database, MIGRATION)
    assert _scope(database, legacy_conversation) == str(LEGACY_USER)
