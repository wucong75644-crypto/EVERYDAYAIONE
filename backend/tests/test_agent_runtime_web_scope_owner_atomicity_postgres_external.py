from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import (
    CATALOG_REVISION,
    CONVERSATION,
    DEFINITION_HASH,
    ORG,
    USER,
    _connect,
    _settings,
    database,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "228_08j_agent_runtime_web_scope_owner_atomicity.sql"
ROLLBACK = "228_08j_agent_runtime_web_scope_owner_atomicity_rollback.sql"
TERMINAL_MIGRATION = "228_08k_agent_runtime_web_ingress_binding_terminal.sql"
TERMINAL_ROLLBACK = (
    "228_08k_agent_runtime_web_ingress_binding_terminal_rollback.sql"
)
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
    _apply(database, TERMINAL_MIGRATION)
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
        assert connection.execute(
            "SELECT has_function_privilege('everydayai_runtime',%s,'execute'),"
            "has_function_privilege('everydayai_worker',%s,'execute')",
            (
                "fail_web_runtime_ingress_task(uuid,uuid,uuid,uuid,uuid,uuid,"
                "uuid,text,text)",
                "fail_web_runtime_ingress_task(uuid,uuid,uuid,uuid,uuid,uuid,"
                "uuid,text,text)",
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

    _execute(database, ROOT / "migrations/rollback" / TERMINAL_ROLLBACK)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT to_regprocedure(%s)",
            (
                "fail_web_runtime_ingress_task(uuid,uuid,uuid,uuid,uuid,uuid,"
                "uuid,text,text)",
            ),
        ).fetchone()[0] is None
    _execute(database, ROOT / "migrations/rollback" / ROLLBACK)
    assert _scope(database, legacy_conversation) is None
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT to_regclass('agent_runtime_conversation_scope_adoptions')"
        ).fetchone()[0] is None

    _apply(database, MIGRATION)
    _apply(database, TERMINAL_MIGRATION)
    assert _scope(database, legacy_conversation) == str(LEGACY_USER)


def _prepare_web_runtime_tasks(database: str) -> tuple[UUID, ...]:
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

    task_id, failed_task_id = uuid4(), uuid4()
    input_id, output_id, failed_input_id, failed_output_id = (
        uuid4() for _ in range(4)
    )
    turn_id, failed_turn_id = uuid4(), uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS "
            "source TEXT NOT NULL DEFAULT 'web'"
        )
        connection.execute(
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS "
            "context_through_message_id UUID"
        )
        connection.execute(
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS terminal_reason TEXT, "
            "ADD COLUMN IF NOT EXISTS error_message TEXT, "
            "ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ"
        )
        connection.execute(
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS "
            "is_error BOOLEAN NOT NULL DEFAULT FALSE"
        )
        connection.execute(
            "UPDATE conversations SET source='web',scope_type='user',scope_id=NULL "
            "WHERE id=%s", (CONVERSATION,),
        )
        connection.commit()
    _apply(database, MIGRATION)
    _apply(database, TERMINAL_MIGRATION)

    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runtime_definition_facts SET enabled_for_new_ingress=true "
            "WHERE agent_key='everydayai-default' AND definition_revision='v1'"
        )
        connection.execute(
            "UPDATE agent_runtime_catalog_facts SET enabled_for_new_ingress=true "
            "WHERE catalog_revision=%s", (CATALOG_REVISION,),
        )
        connection.execute(
            "UPDATE agent_runtime_effective_toolset_facts "
            "SET enabled_for_new_ingress=true WHERE catalog_revision=%s",
            (CATALOG_REVISION,),
        )
        connection.execute(
            "INSERT INTO agent_runtime_rollout_subjects"
            "(subject_kind,subject_id,channel,enabled,capabilities) "
            "VALUES('user',%s,'web',true,'[\"runtime_ingress\"]'::jsonb)",
            (str(USER),),
        )
        connection.execute(
            "UPDATE agent_runtime_control SET ingress_enabled=true WHERE singleton"
        )
        for current_task, current_input, current_output, current_turn, key in (
            (task_id, input_id, output_id, turn_id, "web-null-anchor"),
            (
                failed_task_id, failed_input_id, failed_output_id,
                failed_turn_id, "web-failure",
            ),
        ):
            connection.execute(
                "INSERT INTO messages"
                "(id,conversation_id,org_id,role,content,status,turn_id,"
                "reply_to_message_id) VALUES"
                "(%s,%s,%s,'user','[]','completed',%s,NULL),"
                "(%s,%s,%s,'assistant','[]','generating',%s,%s)",
                (
                    current_input, CONVERSATION, ORG, current_turn,
                    current_output, CONVERSATION, ORG, current_turn,
                    current_input,
                ),
            )
            connection.execute(
                "INSERT INTO tasks"
                "(id,client_task_id,user_id,org_id,conversation_id,type,status,"
                "assistant_message_id,input_message_id,turn_id,"
                "context_through_message_id,delivery_context) VALUES"
                "(%s,%s,%s,%s,%s,'chat','pending',%s,%s,%s,NULL,"
                "'{\"actor\":false,\"runtime\":false,"
                "\"runtime_pending\":true}'::jsonb)",
                (
                    current_task, key, USER, ORG, CONVERSATION,
                    current_output, current_input, current_turn,
                ),
            )
        connection.commit()

    return (
        task_id, failed_task_id, input_id, output_id, failed_input_id,
        failed_output_id, turn_id, failed_turn_id,
    )


def test_web_runtime_accepts_legacy_null_anchor_and_failure_closes_placeholder(
    database: str,
) -> None:
    (
        task_id, failed_task_id, input_id, output_id, failed_input_id,
        failed_output_id, turn_id, failed_turn_id,
    ) = _prepare_web_runtime_tasks(database)

    with _connect(database, "everydayai_runtime") as connection:
        _settings(connection, "everydayai_runtime")
        receipt = connection.execute(
            "SELECT runtime_submit_ingress_v6_required("
            "%s,%s,%s,'user',%s,%s,'everydayai-default','v1',%s,"
            "'submit_input','web-null-anchor','web',%s,%s,%s,NULL,"
            "'{}'::jsonb,'{}'::jsonb,'test',"
            "jsonb_build_object('channel','web','task_id',%s::text,"
            "'client_task_id','web-null-anchor','input_message_id',%s::text,"
            "'output_message_id',%s::text,'turn_id',%s::text,"
            "'request_id','web-null-anchor'),%s,"
            "'web-null-anchor',%s,%s,%s,'web-null-anchor')",
            (
                CONVERSATION, ORG, USER, str(USER), USER, DEFINITION_HASH,
                input_id, f"message:{input_id}", CATALOG_REVISION,
                task_id, input_id, output_id, turn_id, task_id,
                input_id, output_id, turn_id,
            ),
        ).fetchone()[0]
        failure = connection.execute(
            "SELECT fail_web_runtime_ingress_task("
            "%s,%s,%s,%s,%s,%s,%s,'web-failure','RUNTIME_INGRESS_EXCEPTION')",
            (
                failed_task_id, CONVERSATION, USER, ORG, failed_input_id,
                failed_output_id, failed_turn_id,
            ),
        ).fetchone()[0]
        connection.commit()

    with psycopg.connect(database) as connection:
        accepted = connection.execute(
            "SELECT context_through_message_id,delivery_context FROM tasks "
            "WHERE id=%s", (task_id,),
        ).fetchone()
        failed = connection.execute(
            "SELECT task.status,task.terminal_reason,message.status,message.is_error "
            "FROM tasks task JOIN messages message "
            "ON message.id=task.assistant_message_id WHERE task.id=%s",
            (failed_task_id,),
        ).fetchone()
    assert receipt["outcome"] == "marked"
    assert accepted[0] is None
    assert accepted[1]["runtime"] is True
    assert accepted[1]["actor"] is False
    assert failure == {"task_id": str(failed_task_id), "already_failed": False}
    assert failed == ("failed", "runtime_ingress_failed", "failed", True)
