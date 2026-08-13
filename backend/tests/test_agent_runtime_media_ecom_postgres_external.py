"""Disposable PostgreSQL proof for Runtime media and e-commerce readback RPCs."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import (
    CONVERSATION,
    ORG,
    USER,
    _connect,
    _settings,
    database,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
CHAT_ACTION = ROOT / "migrations/227_63_agent_runtime_chat_action_submission.sql"
CHAT_ACTION_ROLLBACK = (
    ROOT / "migrations/rollback/227_63_agent_runtime_chat_action_submission_rollback.sql"
)
MEDIA = ROOT / "migrations/227_65_agent_runtime_media_ingress.sql"
MEDIA_ROLLBACK = ROOT / "migrations/rollback/227_65_agent_runtime_media_ingress_rollback.sql"
ECOM = ROOT / "migrations/227_66_agent_runtime_ecom_readback.sql"
ECOM_ROLLBACK = ROOT / "migrations/rollback/227_66_agent_runtime_ecom_readback_rollback.sql"
CHAT_ACTION_FIX = ROOT / "migrations/227_67_agent_runtime_chat_action_catalog_fix.sql"
MEDIA_IDEMPOTENCY_FIX = ROOT / "migrations/227_68_agent_runtime_media_idempotency_fix.sql"

MEDIA_SIGNATURE = (
    "submit_agent_runtime_media_action_v1(uuid,uuid,uuid,text,text,uuid,text,text,"
    "uuid,uuid,uuid,uuid,text,jsonb,text,text,text,text,text,text)"
)
CHAT_SIGNATURE = (
    "submit_agent_runtime_chat_action_v1(uuid,uuid,uuid,text,text,text,integer,text,"
    "jsonb,text,text,text,text,text,text,integer,jsonb,jsonb,text)"
)
ECOM_SIGNATURE = "read_agent_runtime_ecom_model_v1(uuid,uuid,uuid,text)"
MEDIA_FUNCTION = "submit_agent_runtime_media_action_v1"
ECOM_FUNCTION = "read_agent_runtime_ecom_model_v1"


def _apply(url: str, path: Path) -> None:
    with psycopg.connect(url) as connection, connection.transaction():
        connection.execute(path.read_text(encoding="utf-8"))


def _function_exists(url: str, signature: str) -> bool:
    with psycopg.connect(url) as connection:
        return bool(connection.execute(
            "SELECT to_regprocedure(%s) IS NOT NULL", (signature,)
        ).fetchone()[0])


def _function_definition(url: str, signature: str) -> str:
    with psycopg.connect(url) as connection:
        return connection.execute(
            "SELECT pg_get_functiondef(to_regprocedure(%s))", (signature,)
        ).fetchone()[0]


def _worker_execute(url: str, function: str, params: tuple[object, ...]):
    with _connect(url, "everydayai_worker") as connection:
        _settings(connection, "everydayai_worker")
        placeholders = ",".join(["%s"] * len(params))
        return connection.execute(
            f"SELECT {function}({placeholders})", params
        ).fetchone()[0]


def _runtime_execute(url: str, function: str, params: tuple[object, ...]):
    with _connect(url, "everydayai_runtime") as connection:
        _settings(connection, "everydayai_runtime")
        placeholders = ",".join(["%s"] * len(params))
        return connection.execute(
            f"SELECT {function}({placeholders})", params
        ).fetchone()[0]


def _seed_task(url: str) -> tuple[UUID, UUID, UUID]:
    task_id, input_id, output_id = uuid4(), uuid4(), uuid4()
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO messages(id,conversation_id,org_id,role,content) "
            "VALUES(%s,%s,%s,'user','media fixture')",
            (input_id, CONVERSATION, ORG),
        )
        connection.execute(
            "INSERT INTO tasks(id,user_id,org_id,conversation_id,type,status,"
            "input_message_id,model_id,delivery_context) "
            "VALUES(%s,%s,%s,%s,'chat','pending',%s,'fixture','{}')",
            (task_id, USER, ORG, CONVERSATION, input_id),
        )
        connection.commit()
    return task_id, input_id, output_id


def _media_params(task_id: UUID, input_id: UUID, output_id: UUID):
    return (
        CONVERSATION, ORG, USER, "user", str(USER), USER,
        "everydayai-default", "v1", task_id, input_id, output_id, uuid4(),
        "generate_image", Jsonb({"prompt": "fixture"}), "fixture-model", "fixture",
        "v1", "catalog-v1", "policy-v1", f"media-fixture:{task_id}",
    )


def _assert_security_contract(url: str) -> None:
    with psycopg.connect(url) as connection:
        media_acl = connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            ("everydayai_runtime", MEDIA_SIGNATURE),
        ).fetchone()[0]
        worker_acl = connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            ("everydayai_worker", MEDIA_SIGNATURE),
        ).fetchone()[0]
        ecom_acl = connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            ("everydayai_runtime", ECOM_SIGNATURE),
        ).fetchone()[0]
        ecom_worker_acl = connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            ("everydayai_worker", ECOM_SIGNATURE),
        ).fetchone()[0]
        assert media_acl and ecom_acl
        assert not worker_acl and not ecom_worker_acl
        for function in (MEDIA_FUNCTION, ECOM_FUNCTION):
            config = connection.execute(
                "SELECT proconfig FROM pg_proc WHERE oid=to_regprocedure(%s)",
                (MEDIA_SIGNATURE if function == MEDIA_FUNCTION else ECOM_SIGNATURE,),
            ).fetchone()[0]
            assert "search_path=pg_catalog, public" in config
        rls = connection.execute(
            "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE relname IN ('agent_runtime_sessions','agent_session_commands',"
            "'agent_runs','agent_model_steps','agent_actions','agent_policy_receipts')"
        ).fetchall()
        assert len(rls) == 6
        assert all(row[1] and row[2] for row in rls)


def test_media_and_ecom_apply_rollback_reapply_security_and_tenant_boundary(
    database: str,
) -> None:
    _apply(database, CHAT_ACTION)
    _apply(database, CHAT_ACTION_FIX)
    task_id, input_id, output_id = _seed_task(database)
    _apply(database, MEDIA)
    _apply(database, MEDIA_IDEMPOTENCY_FIX)
    assert _function_exists(database, MEDIA_SIGNATURE)

    created = _runtime_execute(
        database, MEDIA_FUNCTION, _media_params(task_id, input_id, output_id)
    )
    assert created["outcome"] == "created"
    assert created["runtime_owned"] is True
    task_runtime = _runtime_execute(
        database,
        MEDIA_FUNCTION,
        _media_params(task_id, input_id, output_id),
    )
    assert task_runtime["outcome"] == "already_exists"

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _worker_execute(database, MEDIA_FUNCTION, _media_params(task_id, input_id, output_id))
    with pytest.raises(psycopg.Error, match="SCOPE_MISMATCH|TASK_SCOPE_MISMATCH"):
        with _connect(database, "everydayai_runtime") as connection:
            _settings(connection, "everydayai_runtime")
            bad = list(_media_params(task_id, input_id, output_id))
            bad[1] = UUID("77777777-7777-7777-7777-777777777777")
            placeholders = ",".join(["%s"] * len(bad))
            connection.execute(f"SELECT {MEDIA_FUNCTION}({placeholders})", tuple(bad))

    _apply(database, MEDIA_ROLLBACK)
    assert not _function_exists(database, MEDIA_SIGNATURE)
    _apply(database, MEDIA)
    _apply(database, MEDIA_IDEMPOTENCY_FIX)
    assert _function_exists(database, MEDIA_SIGNATURE)

    _apply(database, ECOM)
    assert _function_exists(database, ECOM_SIGNATURE)
    definition = _function_definition(database, CHAT_SIGNATURE)
    assert "p_catalog_revision, jsonb_build_object('message_id'" in definition
    model_step_definition = definition.split("INSERT INTO agent_model_steps", 1)[1]
    assert "p_catalog_revision" in model_step_definition, model_step_definition[:500]
    _assert_security_contract(database)
    pending = _runtime_execute(
        database, ECOM_FUNCTION,
        (CONVERSATION, ORG, USER, f"media-fixture:{task_id}"),
    )
    assert pending["outcome"] == "pending"
    wrong_tenant = _runtime_execute(
        database, ECOM_FUNCTION,
        (CONVERSATION, UUID("77777777-7777-7777-7777-777777777777"), USER,
         f"media-fixture:{task_id}"),
    )
    assert wrong_tenant["outcome"] == "not_found"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _worker_execute(
            database, ECOM_FUNCTION,
            (CONVERSATION, ORG, USER, f"media-fixture:{task_id}"),
        )

    _apply(database, ECOM_ROLLBACK)
    assert not _function_exists(database, ECOM_SIGNATURE)
    _apply(database, ECOM)
    assert _function_exists(database, ECOM_SIGNATURE)
    _apply(database, ECOM_ROLLBACK)
    _apply(database, MEDIA_ROLLBACK)
    _apply(database, CHAT_ACTION_ROLLBACK)
