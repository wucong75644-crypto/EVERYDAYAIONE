from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply
from tests.test_agent_runtime_ar18_b7_scheduler_control_postgres_external import _rpc
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import ORG, USER
from tests.test_agent_runtime_ar18_b7_s2_a2_submission_postgres_external import (
    _enable,
    _prepare_runtime_due,
    _worker_rpc,
)
from tests.test_agent_runtime_ar18_b7_s2_b1a_terminal_intent_postgres_external import (
    _install_final_result,
    _request_rpc,
)
from tests.test_agent_runtime_ar18_b7_s2_b1b_finalizer_postgres_external import (
    _claim,
    _prepare_b1b,
)
from tests.test_agent_runtime_ar18_b7_s2_b1b1_context_postgres_external import _apply_v2


pytestmark = pytest.mark.external
MIGRATION = "227_35_agent_runtime_scheduled_delivery_intents.sql"
ROLLBACK = "rollback/227_35_agent_runtime_scheduled_delivery_intents_rollback.sql"


def _setup(url: str, *, delivery: bool = True) -> None:
    _prepare_b1b(url)
    for name in (
        "227_33_agent_runtime_scheduled_finalization_context.sql",
        "227_34_agent_runtime_scheduled_run_credit_budget.sql",
    ):
        _apply(url, name)
    if delivery:
        _apply(url, MIGRATION)


def _seed_wecom_targets(url: str) -> None:
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO wecom_user_mappings(wecom_userid,corp_id,user_id,org_id) "
            "VALUES('runtime-user','runtime-corp',%s,%s) "
            "ON CONFLICT(wecom_userid,corp_id) DO UPDATE SET user_id=EXCLUDED.user_id,"
            "org_id=EXCLUDED.org_id",
            (USER, ORG),
        )
        conn.execute(
            "INSERT INTO wecom_chat_targets(chatid,chat_type,corp_id,org_id,is_active) "
            "VALUES('runtime-group','group','runtime-corp',%s,true) "
            "ON CONFLICT(chatid,corp_id) DO UPDATE SET org_id=EXCLUDED.org_id,is_active=true",
            (ORG,),
        )
        conn.commit()


def _bound_run(url: str, target: dict | None = None) -> dict[str, str | int]:
    _enable(url)
    task_id = _prepare_runtime_due(url)
    if target is not None:
        with psycopg.connect(url) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute(
                "UPDATE scheduled_tasks SET push_target=%s WHERE id=%s",
                (Jsonb(target), task_id),
            )
            conn.commit()
    [submission] = _worker_rpc(
        url,
        "worker_claim_due_scheduled_executions_v1",
        (datetime.now(timezone.utc), 5),
    )
    command_id = submission["command_id"]
    claimed = None
    for _ in range(20):
        candidate = _rpc(
            url, "claim_pending_agent_command_and_ensure_run", ("b1d1-command", 90, 3),
        )
        if candidate.get("command_id") == command_id:
            claimed = candidate
            break
    assert claimed is not None
    run_claim = _rpc(url, "claim_agent_run", (
        claimed["run_id"], "b1d1-runtime", 90, 3,
    ))
    assert run_claim["outcome"] == "claimed"
    return {
        "task_id": task_id,
        "scheduled_run_id": submission["binding"]["scheduled_run_id"],
        "command_id": command_id,
        "run_id": claimed["run_id"],
        "token": run_claim["execution_token"],
        "version": run_claim["state_version"],
    }


def _projection_read(url: str, facts: dict, *, org_id: str = ORG,
                     scheduled_run_id: str | None = None,
                     runtime_run_id: str | None = None) -> dict:
    projection_url = url.replace("postgres@", "everydayai_projection_worker@")
    with psycopg.connect(projection_url) as conn:
        conn.execute("SELECT set_config('app.access_kind','projection',false)")
        return conn.execute(
            "SELECT read_agent_runtime_scheduled_delivery_intents_v1(%s,%s,%s)",
            (
                org_id,
                scheduled_run_id or facts["scheduled_run_id"],
                runtime_run_id or facts["run_id"],
            ),
        ).fetchone()[0]


def _terminal(url: str, facts: dict, terminal: str) -> tuple[dict, dict]:
    if terminal == "completed":
        result_hash = _install_final_result(url, facts)
        result = _rpc(url, "complete_agent_run", (
            facts["run_id"], facts["token"], facts["version"], result_hash,
        ))
    elif terminal == "failed":
        result = _rpc(url, "fail_agent_run", (
            facts["run_id"], facts["token"], facts["version"],
            "Authorization: Bearer should-not-survive",
        ))
    else:
        with psycopg.connect(url) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute(
                "UPDATE agent_runtime_sessions SET scope_kind='user',scope_id=%s "
                "WHERE id=(SELECT session_id FROM agent_runs WHERE id=%s)",
                (USER, facts["run_id"]),
            )
            conn.commit()
        result = _request_rpc(url, "test_b1a_cancel_agent_run", (
            facts["run_id"], facts["version"], "runtime_cancel",
        ))
    assert result["outcome"] == terminal
    claim = _claim(url)
    return result, claim


@pytest.mark.parametrize(
    ("target", "expected"),
    (
        ({"type": "web", "user_id": USER, "conversation_id": "ignored"},
         [("web", f"web:{USER}")]),
        ({"type": "wecom_user", "wecom_userid": " runtime-user ", "name": "ignored"},
         [("wecom_user", "wecom_user:runtime-user")]),
        ({"type": "wecom_group", "chatid": " runtime-group ", "chat_name": "ignored"},
         [("wecom_group", "wecom_group:runtime-group")]),
        ({"type": "multi", "targets": [
            {"type": "wecom_group", "chatid": "runtime-group"},
            {"type": "web", "user_id": USER},
            {"type": "wecom_user", "wecom_userid": "runtime-user"},
            {"type": "web", "user_id": USER},
        ]}, [
            ("web", f"web:{USER}"),
            ("wecom_group", "wecom_group:runtime-group"),
            ("wecom_user", "wecom_user:runtime-user"),
        ]),
    ),
)
def test_real_push_target_shapes_freeze_deterministically(
    database: str, target: dict, expected: list[tuple[str, str]],
) -> None:
    _setup(database)
    _seed_wecom_targets(database)
    facts = _bound_run(database, target)
    read = _projection_read(database, facts)
    assert read["outcome"] == "found"
    assert read["snapshot"]["target_count"] == len(expected)
    assert [(item["target_type"], item["target_key"]) for item in read["targets"]] == expected
    assert read["intents"] == []
    payload = json.dumps(read, sort_keys=True)
    for forbidden in ("ignored", "conversation_id", "secret", "token", "password"):
        assert forbidden not in payload.lower()


@pytest.mark.parametrize("terminal", ("completed", "failed", "cancelled"))
def test_finalization_atomically_creates_safe_idempotent_intent(
    database: str, terminal: str,
) -> None:
    _setup(database)
    facts = _bound_run(database)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE scheduled_tasks SET push_target=%s WHERE id=%s",
            (Jsonb({"type": "unknown", "secret": "changed-after-submit"}), facts["task_id"]),
        )
        conn.commit()
    _, claim = _terminal(database, facts, terminal)
    request_id = str(uuid4())
    next_run_at = datetime.now(timezone.utc) + timedelta(hours=1)
    first = _apply_v2(
        database, claim, request_id=request_id, next_run_at=next_run_at,
    )
    assert first["outcome"] == "applied"
    second = _apply_v2(
        database, claim, request_id=request_id, next_run_at=next_run_at,
    )
    assert second["outcome"] == "already_applied"
    read = _projection_read(database, facts)
    assert read["snapshot"]["target_count"] == 1
    assert read["targets"][0]["target"]["type"] == "web"
    assert len(read["intents"]) == 1
    intent = read["intents"][0]
    assert intent["terminal_status"] == terminal and intent["status"] == "pending"
    assert intent["state_version"] == 0
    assert len(intent["content_identity_hash"]) == 64
    if terminal == "completed":
        assert intent["result_hash"] and intent["reason_code"] is None
    else:
        assert intent["result_hash"] is None
        assert intent["reason_code"] in {"redacted_terminal_reason", "runtime_cancel"}
    serialized = json.dumps(read, sort_keys=True).lower()
    for forbidden in (
        "scheduled result", "changed-after-submit", "should-not-survive",
        "authorization", "bearer", "prompt", "stack", "/private/",
    ):
        assert forbidden not in serialized


def test_readback_tenant_run_fences_acl_rls_and_no_side_effect(database: str) -> None:
    _setup(database)
    facts = _bound_run(database)
    before = _projection_read(database, facts)
    after = _projection_read(database, facts)
    assert before == after
    assert _projection_read(database, facts, org_id=str(uuid4()))["outcome"] == "not_found"
    assert _projection_read(
        database, facts, runtime_run_id=str(uuid4()),
    )["outcome"] == "fenced"

    legacy_url = database.replace("postgres@", "everydayai_worker@")
    with psycopg.connect(legacy_url) as conn:
        conn.execute("SELECT set_config('app.access_kind','projection',false)")
        with pytest.raises(Exception, match="permission denied"):
            conn.execute(
                "SELECT read_agent_runtime_scheduled_delivery_intents_v1(%s,%s,%s)",
                (ORG, facts["scheduled_run_id"], facts["run_id"]),
            ).fetchone()
    with psycopg.connect(database) as conn:
        for table in (
            "agent_runtime_scheduled_delivery_snapshots",
            "agent_runtime_scheduled_delivery_targets",
            "agent_runtime_scheduled_delivery_runtime_bindings",
            "agent_runtime_scheduled_delivery_intents",
        ):
            assert conn.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
                (table,),
            ).fetchone() == (True, True)
            assert conn.execute(
                "SELECT has_table_privilege('everydayai_projection_worker',%s,'SELECT')",
                (table,),
            ).fetchone()[0] is False
            assert conn.execute(
                "SELECT has_table_privilege('everydayai_agent_runtime_worker',%s,'SELECT')",
                (table,),
            ).fetchone()[0] is False
        assert conn.execute(
            "SELECT proconfig FROM pg_proc WHERE oid="
            "'read_agent_runtime_scheduled_delivery_intents_v1(uuid,uuid,uuid)'::regprocedure"
        ).fetchone()[0] == ["search_path=pg_catalog, public"]


def test_legacy_run_produces_no_runtime_delivery_intent(database: str) -> None:
    _setup(database)
    legacy_task = str(uuid4())
    legacy_run = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO scheduled_tasks(id,org_id,user_id,name,prompt,cron_expr,timezone,"
            "push_target,status,runtime_state_version) VALUES(%s,%s,%s,'Legacy','Read',"
            "'0 9 * * *','Asia/Shanghai',%s,'running',0)",
            (legacy_task, ORG, USER, Jsonb({"type": "web", "user_id": USER})),
        )
        conn.execute(
            "INSERT INTO scheduled_task_runs(id,task_id,org_id,status) VALUES(%s,%s,%s,'success')",
            (legacy_run, legacy_task, ORG),
        )
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_scheduled_delivery_intents",
        ).fetchone()[0] == 0
        conn.commit()


def test_existing_runtime_fact_blocks_unsafe_apply(database: str) -> None:
    _setup(database, delivery=False)
    _bound_run(database)
    with pytest.raises(Exception, match="DELIVERY_BACKFILL_REQUIRED"):
        _apply(database, MIGRATION)
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT to_regclass('agent_runtime_scheduled_delivery_snapshots')",
        ).fetchone()[0] is None


def test_rollback_guard_cleanup_reapply_and_exact_rollback(database: str) -> None:
    _setup(database)
    _apply(database, ROLLBACK)
    _apply(database, MIGRATION)
    facts = _bound_run(database)
    with pytest.raises(Exception, match="DELIVERY_ROLLBACK_FACTS_EXIST"):
        _apply(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "TRUNCATE agent_runtime_scheduled_delivery_intents,"
            "agent_runtime_scheduled_delivery_runtime_bindings,"
            "agent_runtime_scheduled_delivery_targets,"
            "agent_runtime_scheduled_delivery_snapshots"
        )
        conn.commit()
    _apply(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT to_regprocedure("
            "'read_agent_runtime_scheduled_delivery_intents_v1(uuid,uuid,uuid)')",
        ).fetchone()[0] is None
