"""AR-17.1 real PostgreSQL contract, including isolation and cleanup."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
from uuid import UUID, uuid4

import psycopg
import pytest
from types import SimpleNamespace

from tests.test_agent_runtime_model_attempt_postgres_external import CREDITS_BOOTSTRAP

pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
ORG = UUID("22222222-2222-2222-2222-222222222222")
USER = UUID("44444444-4444-4444-4444-444444444444")
CONVERSATION = UUID("55555555-5555-5555-5555-555555555555")
DEFINITION_HASH = "c24430ae6c5e1f4a5062a87eae0369b2249cdca18eedfc275b590c2c5f76eefe"
CATALOG_REVISION = "9ef52c52816e357a4cb2bf03a9893e41127105a3ffb4c2cba18489fa880ce874"
TOOLSET_HASH = "407113c665c9c28d9f34f47a8f1cf6783da8723b44e47773ac1f0403613d651c"
DISABLED_TOOLSET_HASH = "5e61e290d4f6b1cc5772e3d82ed5f8747d7d3147088b6c2381c57fbb66b1b1a6"
V2_DEFINITION_HASH = "1bf28918781f23cb9aaed43fbf16937a301d3092a483c774638f2e8b36a4b28a"
V2_CATALOG_REVISION = "563239a5d5d5d2dbc75600e65067a15f10d2a295adc47ab95742a49fc029781a"
V2_TOOLSET_HASH = "ff04c4ac46838ef8c9ef9781f5d7eb475f6ca877a4547a14dc328067d8a8f55a"
BASE = (
    "212_agent_runtime_core_foundation.sql",
    "213_agent_runtime_session_run_rpcs.sql",
    "214_agent_runtime_run_lifecycle_rpcs.sql",
    "215_agent_runtime_model_event_projection_rpcs.sql",
    "217_01_agent_runtime_model_attempt_foundation.sql",
    "217_02_agent_runtime_model_attempt_credits.sql",
    "217_03_agent_runtime_model_attempt_lifecycle.sql",
    "217_04_agent_runtime_model_attempt_reconciliation.sql",
    "218_01_agent_runtime_action_foundation.sql",
    "218_01a_agent_runtime_action_terminal_helpers.sql",
    "218_02_agent_runtime_action_tool_terminal.sql",
    "218_02a_agent_runtime_action_result_helpers.sql",
    "218_03_agent_runtime_action_lifecycle.sql",
    "218_04_agent_runtime_action_reconciliation.sql",
    "219_01_agent_runtime_command_claim_foundation.sql",
    "219_02_agent_runtime_command_claim_lifecycle.sql",
    "219_02a_agent_runtime_command_claim_terminal_compatibility.sql",
    "220_01_agent_runtime_model_result_foundation.sql",
    "220_02_agent_runtime_coordinator_recovery.sql",
    "220_03_agent_runtime_model_result_terminal.sql",
    "220_04_agent_runtime_action_recovery.sql",
    "220_11_agent_runtime_compat_projection_foundation.sql",
    "220_12_agent_runtime_compat_projection_rpcs.sql",
    "220_21_agent_runtime_authorization_foundation.sql",
    "220_22_agent_runtime_authorization_rpcs.sql",
    "220_23_agent_runtime_accepted_cancel_override.sql",
    "220_24_agent_runtime_authorization_dispatch_gate.sql",
    "220_25_agent_runtime_authorization_recovery.sql",
    "220_26_agent_runtime_projection_dead_recovery.sql",
    "222_01_agent_runtime_sandbox_job_foundation.sql",
    "222_02_agent_runtime_sandbox_job_rpcs.sql",
    "222_03_agent_runtime_sandbox_job_recovery_rpcs.sql",
    "223_agent_runtime_production_composition.sql",
)
AR17_MIGRATIONS = (
    "224_01_agent_runtime_ar17_core.sql",
    "224_02_agent_runtime_ar17_version_seed.sql",
)
AR17_ROLLBACKS = (
    "224_02_agent_runtime_ar17_version_seed_rollback.sql",
    "224_01_agent_runtime_ar17_core_rollback.sql",
)
PG_BIN_DIR = Path(os.getenv("AGENT_RUNTIME_PG_BIN_DIR", "/opt/homebrew/bin"))


def _pg_tool(name: str) -> str:
    return str(PG_BIN_DIR / name)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run(command: list[str], *, cwd: Path | None = None, capture: bool = True) -> None:
    subprocess.run(command, cwd=cwd, check=True,
                   capture_output=capture, text=capture, timeout=30)


def _bootstrap_sql() -> str:
    return """
    DO $roles$
    DECLARE role_name TEXT;
    BEGIN
      IF to_regrole('everydayai_owner') IS NULL THEN CREATE ROLE everydayai_owner NOLOGIN; END IF;
      FOREACH role_name IN ARRAY ARRAY[
        'everydayai_runtime','everydayai_wecom_runtime','everydayai_agent_runtime_worker',
        'everydayai_worker','everydayai_sync','everydayai','everydayai_runtime_admin',
        'everydayai_projection_worker','everydayai_authorization_worker','everydayai_sandbox_worker'
      ] LOOP
        IF to_regrole(role_name) IS NULL THEN EXECUTE format('CREATE ROLE %I LOGIN', role_name); END IF;
      END LOOP;
    END
    $roles$;
    GRANT everydayai_owner TO CURRENT_USER;
    GRANT everydayai_runtime_admin TO CURRENT_USER;
    """


@pytest.fixture(scope="function")
def database():
    if os.getenv("RUN_AR17_1_DB_TEST") != "1":
        pytest.skip("RUN_AR17_1_DB_TEST=1 required")
    port = _free_port()
    data_dir = Path(tempfile.mkdtemp(prefix="ar17-1-pg-", dir="/private/tmp"))
    url = f"postgresql://postgres@127.0.0.1:{port}/postgres"
    try:
        _run([_pg_tool("initdb"), "-D", str(data_dir), "-U", "postgres", "--auth-host=trust", "--auth-local=trust"])
        try:
            _run([_pg_tool("pg_ctl"), "-D", str(data_dir), "-o", f"-p {port}", "-l", str(data_dir / "postgres.log"), "-w", "start"], capture=False)
        except subprocess.CalledProcessError:
            logfile = data_dir / "postgres.log"
            if logfile.exists():
                print(logfile.read_text(errors="replace"))
            raise
        with psycopg.connect(url) as conn:
            conn.execute(_bootstrap_sql())
            conn.execute((ROOT / "tests/fixtures/agent_runtime_core_postgres_bootstrap.sql").read_text())
            conn.execute((ROOT / "tests/fixtures/agent_runtime_compat_projection_legacy.sql").read_text())
            conn.execute(CREDITS_BOOTSTRAP)
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
            conn.execute("UPDATE users SET role='super_admin' WHERE id=%s", (USER,))
            conn.commit()
        for name in BASE:
            with psycopg.connect(url) as conn:
                with conn.transaction():
                    conn.execute((ROOT / "migrations" / name).read_text())
        with _connect(url, "everydayai_runtime") as conn:
            _settings(conn, "everydayai_runtime", user=UUID("11111111-1111-1111-1111-111111111111"), org=None)
            conn.execute("SELECT ensure_agent_runtime_session(%s,%s,%s,'user',%s,%s,'legacy','v1')", (
                UUID("33333333-3333-3333-3333-333333333333"), None,
                UUID("11111111-1111-1111-1111-111111111111"),
                "11111111-1111-1111-1111-111111111111",
                UUID("11111111-1111-1111-1111-111111111111"),
            ))
        for name in AR17_MIGRATIONS:
            with psycopg.connect(url) as conn:
                with conn.transaction():
                    conn.execute((ROOT / "migrations" / name).read_text())
        for name in AR17_ROLLBACKS:
            with psycopg.connect(url) as conn:
                with conn.transaction():
                    conn.execute((ROOT / "migrations/rollback" / name).read_text())
        for name in AR17_MIGRATIONS:
            with psycopg.connect(url) as conn:
                with conn.transaction():
                    conn.execute((ROOT / "migrations" / name).read_text())
        with psycopg.connect(url) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute("""
              CREATE FUNCTION enqueue_wecom_generation_turn_v2(
                JSONB,UUID,UUID,UUID,JSONB,JSONB
              ) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
              SET search_path=pg_catalog,public AS $fn$
              DECLARE task_id UUID := ( $1->>'id' )::uuid;
              BEGIN
                INSERT INTO tasks(id,user_id,org_id,conversation_id,type,status,input_message_id,model_id,delivery_context)
                VALUES(task_id,($1->>'user_id')::uuid,($1->>'org_id')::uuid,
                  ($1->>'conversation_id')::uuid,'chat','pending',$2,'qwen',$6)
                ON CONFLICT (id) DO NOTHING;
                RETURN jsonb_build_object('task_id',task_id);
              END $fn$;
            """)
            conn.commit()
        yield url
    finally:
        if (data_dir / "postmaster.pid").exists():
            _run([_pg_tool("pg_ctl"), "-D", str(data_dir), "-m", "fast", "-w", "stop"], capture=False)
        with socket.socket() as sock:
            sock.settimeout(0.5)
            assert sock.connect_ex(("127.0.0.1", port)) != 0
        shutil.rmtree(data_dir)
        assert not data_dir.exists()


def _connect(url: str, role: str) -> psycopg.Connection:
    return psycopg.connect(url.replace("postgres@", f"{role}@"))


def _settings(conn: psycopg.Connection, role: str, *, org: UUID | None = ORG,
              user: UUID = USER) -> None:
    kind = "agent_runtime" if role == "everydayai_agent_runtime_worker" else "runtime"
    conn.execute("SELECT set_config('app.actor_user_id', %s, false)", (str(user),))
    conn.execute("SELECT set_config('app.org_id', %s, false)", (str(org) if org else "",))
    conn.execute("SELECT set_config('app.access_kind', %s, false)", (kind,))


class _PostgresRuntimeRpc:
    def __init__(self, url: str, role: str, params: dict[str, object]) -> None:
        self.url, self.role, self.params = url, role, params

    async def execute(self) -> SimpleNamespace:
        p = self.params
        with _connect(self.url, self.role) as conn:
            _settings(conn, self.role)
            values = (
                p["p_conversation_id"], p["p_org_id"], p["p_user_id"],
                p["p_scope_kind"], p["p_scope_id"], p["p_created_by_user_id"],
                p["p_agent_definition_id"], p["p_agent_definition_revision"],
                p["p_agent_definition_hash"], p["p_command_type"],
                p["p_idempotency_key"], p["p_channel"], p["p_through_message_id"],
                p["p_base_context_revision"], p["p_effective_toolset_revision"],
                p["p_effective_toolset_hash"], json.dumps(p["p_config_snapshot"]),
                json.dumps(p["p_capability_snapshot"]), p["p_release_revision"],
                json.dumps(p["p_payload"]),
            )
            row = conn.execute("""SELECT runtime_submit_ingress_v2(
              %s::uuid,%s::uuid,%s::uuid,%s,%s,%s::uuid,%s,%s,%s,%s,%s,%s,
              %s::uuid,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb)""", values).fetchone()
            return SimpleNamespace(data=row[0])


class _PostgresRuntimeDatabase:
    def __init__(self, url: str, role: str = "everydayai_runtime") -> None:
        self.url, self.role = url, role

    def rpc(self, name: str, params: dict[str, object]) -> _PostgresRuntimeRpc:
        assert name == "runtime_submit_ingress_v2"
        return _PostgresRuntimeRpc(self.url, self.role, params)


def _set_control(database: str, *, enabled: bool) -> None:
    patch = {
        "ingress_enabled": True, "non_safe_actions_enabled": enabled,
        "code_execute_enabled": enabled,
        "tool_confirmation_enabled": enabled,
        "release_revision": "ar17-test", "config_revision": "ar17-test",
    }
    with _connect(database, "everydayai_runtime_admin") as conn:
        _settings(conn, "everydayai_runtime_admin")
        conn.execute("SELECT set_config('app.access_kind','runtime_admin',false)")
        status = conn.execute("SELECT get_agent_runtime_admin_status()").fetchone()[0]
        version = status["control"]["state_version"]
        conn.execute("SELECT set_agent_runtime_control(%s,%s,%s,%s)",
                     (uuid4(), version, json.dumps(patch), "gate-test"))


def _ingress(url: str, key: str, *, role: str = "everydayai_runtime", channel: str = "web",
             through: UUID | None = None, definition_hash: str = DEFINITION_HASH,
             definition_revision: str = "v1", catalog_revision: str = CATALOG_REVISION,
             conversation: UUID = CONVERSATION, toolset_hash: str | None = None):
    through = through or uuid4()
    with _connect(url, role) as conn:
        _settings(conn, role)
        row = conn.execute("""
          SELECT runtime_submit_ingress_v2(
            %s::uuid,%s::uuid,%s::uuid,'user',%s::text,%s::uuid,
            'everydayai-default',%s::text,%s::text,'submit_input',%s::text,%s::text,
            %s::uuid,%s::text,%s::text,%s::text,'{}'::jsonb,
            '{"requested_groups":["code"]}'::jsonb,%s::text,%s::jsonb)
        """, (conversation, ORG, USER, str(USER), USER, definition_revision, definition_hash, key, channel,
               through, f"message:{through}", catalog_revision, toolset_hash,
               "ar17-test", '{"task_id":null}')).fetchone()
        return row[0]


def _assert_permissions_and_rollback(database: str) -> None:
    with _connect(database, "postgres") as conn:
        security = conn.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid='agent_runtime_definition_facts'::regclass").fetchone()
        privileges = conn.execute("SELECT has_function_privilege('everydayai_runtime','runtime_submit_ingress_v2(uuid,uuid,uuid,text,text,uuid,text,text,text,text,text,text,uuid,text,text,text,jsonb,jsonb,text,jsonb)','execute'), has_table_privilege('everydayai_runtime','agent_runtime_definition_facts','select')").fetchone()
        matrix = conn.execute("""
          SELECT rolname,
            has_function_privilege(rolname,'runtime_submit_ingress_v2(uuid,uuid,uuid,text,text,uuid,text,text,text,text,text,text,uuid,text,text,text,jsonb,jsonb,text,jsonb)','execute'),
            has_function_privilege(rolname,'get_agent_runtime_model_context_v2(uuid,text,uuid)','execute'),
            has_table_privilege(rolname,'agent_runtime_definition_facts','select')
          FROM (VALUES ('public'),('everydayai_runtime'),('everydayai_wecom_runtime'),
                       ('everydayai_agent_runtime_worker'),('everydayai_worker'),('everydayai_sync')) roles(rolname)
        """).fetchall()
    assert security == (True, True)
    assert privileges == (True, False)
    expected = {
        "public": (False, False, False),
        "everydayai_runtime": (True, False, False),
        "everydayai_wecom_runtime": (True, False, False),
        "everydayai_agent_runtime_worker": (False, True, False),
        "everydayai_worker": (False, False, False),
        "everydayai_sync": (False, False, False),
    }
    assert {row[0]: tuple(row[1:]) for row in matrix} == expected
    with pytest.raises(psycopg.Error, match="AGENT_RUNTIME_224_ROLLBACK_GUARD_FACTS_EXIST"):
        with psycopg.connect(database) as conn:
            with conn.transaction():
                conn.execute((ROOT / "migrations/rollback/224_02_agent_runtime_ar17_version_seed_rollback.sql").read_text())


def test_real_runtime_ingress_class_uses_db_selected_empty_toolset(database: str) -> None:
    from services.agent.runtime.ingress import RuntimeIngress

    anchor = uuid4()
    with _connect(database, "postgres") as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("INSERT INTO messages(id,conversation_id,org_id,role,content) VALUES(%s,%s,%s,'user','class')",
                     (anchor, CONVERSATION, ORG))
        conn.commit()
    with _connect(database, "everydayai_runtime_admin") as conn:
        _settings(conn, "everydayai_runtime_admin")
        conn.execute("SELECT set_config('app.access_kind','runtime_admin',false)")
        conn.execute("SELECT set_agent_runtime_control(%s,0,%s,%s)", (
            uuid4(), '{"ingress_enabled":true,"non_safe_actions_enabled":false,"code_execute_enabled":false,"tool_confirmation_enabled":false,"release_revision":"ar17-test","config_revision":"ar17-test"}', "class-test"))
        conn.execute("SELECT set_agent_runtime_org_rollout(%s,%s,true,%s)", (uuid4(), ORG, "class-test"))
    receipt = asyncio.run(RuntimeIngress(_PostgresRuntimeDatabase(database)).submit(
        conversation_id=str(CONVERSATION), org_id=str(ORG), user_id=str(USER),
        scope_kind="user", scope_id=str(USER), agent_definition_id="everydayai-default",
        agent_definition_revision="v1", command_type="submit_input",
        idempotency_key=" class-key ", payload={"input_message_id": str(anchor), "channel": "web"}))
    assert receipt.accepted
    assert receipt.gate_state == "disabled"
    assert receipt.effective_toolset_hash == DISABLED_TOOLSET_HASH


def test_real_ingress_claim_context_permissions_and_rollback(database: str) -> None:
    with _connect(database, "everydayai_runtime_admin") as conn:
        _settings(conn, "everydayai_runtime_admin")
        conn.execute("SELECT set_config('app.access_kind', 'runtime_admin', false)")
    with _connect(database, "everydayai_runtime") as conn:
        _settings(conn, "everydayai_runtime")
        conn.execute("SELECT report_agent_runtime_capability('tool_confirmation_v3_redis',true,'{}'::jsonb)")
    with _connect(database, "everydayai_sandbox_worker") as conn:
        _settings(conn, "everydayai_sandbox_worker")
        conn.execute("SELECT set_config('app.access_kind','sandbox_worker',false)")
        conn.execute("SELECT report_agent_runtime_worker_heartbeat('sandbox','sandbox-test','ar17-test',true,false,'ready','{}'::jsonb)")
    with _connect(database, "everydayai_runtime_admin") as conn:
        _settings(conn, "everydayai_runtime_admin")
        conn.execute("SELECT set_config('app.access_kind', 'runtime_admin', false)")
        conn.execute("SELECT set_agent_runtime_control(%s,0,%s,%s)", (
            uuid4(), '{"ingress_enabled":true,"non_safe_actions_enabled":true,"code_execute_enabled":true,"tool_confirmation_enabled":true,"release_revision":"ar17-test","config_revision":"ar17-test"}', "test",
        ))
        conn.execute("SELECT set_agent_runtime_org_rollout(%s,%s,true,%s)", (uuid4(), ORG, "test"))
    personal_conversation = uuid4()
    personal_anchor = uuid4()
    with _connect(database, "postgres") as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("INSERT INTO conversations(id,user_id,org_id,scope_type,scope_id) VALUES(%s,%s,NULL,'user',%s)",
                     (personal_conversation, UUID("11111111-1111-1111-1111-111111111111"), "11111111-1111-1111-1111-111111111111"))
        conn.execute("INSERT INTO messages(id,conversation_id,org_id,role,content) VALUES(%s,%s,NULL,'user','personal')",
                     (personal_anchor, personal_conversation))
        conn.commit()
    with _connect(database, "everydayai_runtime") as conn:
        _settings(conn, "everydayai_runtime", user=UUID("11111111-1111-1111-1111-111111111111"), org=None)
        personal = conn.execute("SELECT runtime_submit_ingress_v2(%s::uuid,NULL,%s::uuid,'user',%s::text,%s::uuid,'everydayai-default','v1',%s::text,'submit_input','personal-key','web',%s::uuid,%s::text,%s::text,%s::text,'{}'::jsonb,'{}'::jsonb,'ar17-test','{}'::jsonb)",
                                (personal_conversation, UUID("11111111-1111-1111-1111-111111111111"),
                                 "11111111-1111-1111-1111-111111111111",
                                 UUID("11111111-1111-1111-1111-111111111111"), DEFINITION_HASH,
                                 personal_anchor, f"message:{personal_anchor}", CATALOG_REVISION, None)).fetchone()[0]
    assert personal["outcome"] == "org_not_enabled"
    anchor = uuid4()
    with _connect(database, "postgres") as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("INSERT INTO messages(id,conversation_id,org_id,role,content) VALUES(%s,%s,%s,'user','hello')",
                     (anchor, CONVERSATION, ORG))
        conn.execute("INSERT INTO tasks(id,user_id,org_id,conversation_id,type,status,input_message_id,model_id,delivery_context) VALUES(%s,%s,%s,%s,'chat','pending',%s,'qwen','{}')",
                     (uuid4(), USER, ORG, CONVERSATION, anchor))
        conn.commit()
    first = _ingress(database, "ar17-real-key", through=anchor)
    assert first["outcome"] == "created"
    assert _ingress(database, "ar17-real-key", through=anchor)["entity_id"] == first["entity_id"]
    assert _ingress(database, "ar17-real-key", through=anchor, channel="wecom")["outcome"] == "idempotency_conflict"
    with pytest.raises(psycopg.Error, match="RUNTIME_VERSION_FACT_NOT_ENABLED"):
        _ingress(database, "wrong-hash-key", through=anchor, definition_hash="0" * 64)
    with _connect(database, "everydayai_wecom_runtime") as conn:
        _settings(conn, "everydayai_wecom_runtime")
        task_id = uuid4()
        row = conn.execute("""SELECT enqueue_wecom_runtime_turn_v4(
          %s::jsonb,%s::uuid,%s::uuid,%s::uuid,%s::jsonb,%s::jsonb,
          'everydayai-default','v1',%s::text,%s::text,%s::text,'ar17-test','ar17-wecom-key')""",
                           (f'{{"id":"{task_id}","user_id":"{USER}","org_id":"{ORG}","conversation_id":"{CONVERSATION}"}}',
                            anchor, uuid4(), uuid4(), '["hello"]',
                            '{"actor":true,"channel":"wecom","chatid":"group-1","transport":"app"}',
                            DEFINITION_HASH, CATALOG_REVISION, None)).fetchone()
        assert row[0]["runtime_owned"] is True
    with ThreadPoolExecutor(max_workers=20) as pool:
        rows = list(pool.map(lambda _: _ingress(database, "ar17-real-key", through=anchor), range(50)))
    assert {row["session_id"] for row in rows} == {first["session_id"]}
    with _connect(database, "postgres") as conn:
        counts = conn.execute("SELECT count(*) FILTER (WHERE idempotency_key='ar17-real-key'), count(*) FILTER (WHERE payload->'run_envelope'->>'schema_revision'='2') FROM agent_session_commands").fetchone()
    assert counts == (1, 2)
    with _connect(database, "everydayai_agent_runtime_worker") as conn:
        _settings(conn, "everydayai_agent_runtime_worker")
        claimed = conn.execute("SELECT claim_pending_agent_command_and_ensure_run('ar17-worker',90,3)").fetchone()[0]
    run_id = UUID(claimed["run_id"])
    with _connect(database, "everydayai_agent_runtime_worker") as conn:
        _settings(conn, "everydayai_agent_runtime_worker")
        claimed_run = conn.execute("SELECT claim_next_agent_run('ar17-worker',90,3)").fetchone()[0]
        context = conn.execute("SELECT get_agent_runtime_model_context_v2(%s,'ar17-worker',%s)",
                               (run_id, claimed_run["execution_token"])).fetchone()[0]
    assert context["outcome"] == "found"
    assert context["context_hash"]
    assert context["messages"][0]["id"] == str(anchor)
    with _connect(database, "postgres") as conn:
        conn.execute("SET ROLE everydayai_owner")
        later = uuid4()
        conn.execute("INSERT INTO messages(id,conversation_id,org_id,role,content) VALUES(%s,%s,%s,'user','later')",
                     (later, CONVERSATION, ORG))
        conn.commit()
    with _connect(database, "everydayai_agent_runtime_worker") as conn:
        _settings(conn, "everydayai_agent_runtime_worker")
        retry = conn.execute("SELECT get_agent_runtime_model_context_v2(%s,'ar17-worker',%s)",
                             (run_id, claimed_run["execution_token"])).fetchone()[0]
    assert retry["context_hash"] == context["context_hash"]
    assert [message["id"] for message in retry["messages"]] == [str(anchor)]
    with _connect(database, "postgres") as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_runs SET context_receipt=context_receipt||'{\"base_context_revision\":\"message:drift\"}'::jsonb WHERE id=%s", (run_id,))
        conn.commit()
    with _connect(database, "everydayai_agent_runtime_worker") as conn:
        _settings(conn, "everydayai_agent_runtime_worker")
        assert conn.execute("SELECT get_agent_runtime_model_context_v2(%s,'ar17-worker',%s)",
                            (run_id, claimed_run["execution_token"])).fetchone()[0]["outcome"] == "context_revision_mismatch"
    with _connect(database, "postgres") as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_runs SET context_receipt=context_receipt||jsonb_build_object('base_context_revision',%s::text) WHERE id=%s",
                     (f"message:{anchor}", run_id))
        conn.commit()
    _assert_permissions_and_rollback(database)


def test_v1_run_recovers_after_v2_enable_and_uses_frozen_facts(database: str) -> None:
    with _connect(database, "everydayai_runtime") as conn:
        _settings(conn, "everydayai_runtime")
        conn.execute("SELECT report_agent_runtime_capability('tool_confirmation_v3_redis',true,'{}'::jsonb)")
    with _connect(database, "everydayai_sandbox_worker") as conn:
        _settings(conn, "everydayai_sandbox_worker")
        conn.execute("SELECT set_config('app.access_kind','sandbox_worker',false)")
        conn.execute("SELECT report_agent_runtime_worker_heartbeat('sandbox','sandbox-upgrade','ar17-test',true,false,'ready','{}'::jsonb)")
    with _connect(database, "everydayai_runtime_admin") as conn:
        _settings(conn, "everydayai_runtime_admin")
        conn.execute("SELECT set_config('app.access_kind','runtime_admin',false)")
        conn.execute("SELECT set_agent_runtime_control(%s,0,%s,%s)", (
            uuid4(), '{"ingress_enabled":true,"non_safe_actions_enabled":true,"code_execute_enabled":true,"tool_confirmation_enabled":true,"release_revision":"ar17-test","config_revision":"ar17-test"}', "upgrade",
        ))
        conn.execute("SELECT set_agent_runtime_org_rollout(%s,%s,true,%s)", (uuid4(), ORG, "upgrade"))
    anchor = uuid4()
    with _connect(database, "postgres") as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("INSERT INTO messages(id,conversation_id,org_id,role,content) VALUES(%s,%s,%s,'user','v1')",
                     (anchor, CONVERSATION, ORG))
        conn.execute("INSERT INTO tasks(id,user_id,org_id,conversation_id,type,status,input_message_id,model_id,delivery_context) VALUES(%s,%s,%s,%s,'chat','pending',%s,'qwen','{}')",
                     (uuid4(), USER, ORG, CONVERSATION, anchor))
        conversation_v2 = uuid4()
        anchor_v2 = uuid4()
        conn.execute("INSERT INTO conversations(id,user_id,org_id,scope_type,scope_id) VALUES(%s,%s,%s,'user',%s)",
                     (conversation_v2, USER, ORG, str(USER)))
        conn.execute("INSERT INTO messages(id,conversation_id,org_id,role,content) VALUES(%s,%s,%s,'user','v2')",
                     (anchor_v2, conversation_v2, ORG))
        conn.execute("INSERT INTO tasks(id,user_id,org_id,conversation_id,type,status,input_message_id,model_id,delivery_context) VALUES(%s,%s,%s,%s,'chat','pending',%s,'qwen','{}')",
                     (uuid4(), USER, ORG, conversation_v2, anchor_v2))
        conn.commit()
    v1 = _ingress(database, "upgrade-v1", through=anchor)
    with _connect(database, "everydayai_agent_runtime_worker") as conn:
        _settings(conn, "everydayai_agent_runtime_worker")
        claim = conn.execute("SELECT claim_pending_agent_command_and_ensure_run('upgrade-v1-worker',90,3)").fetchone()[0]
        assert claim.get("run_id"), claim
        claimed_run = conn.execute("SELECT claim_next_agent_run('upgrade-v1-worker',90,3)").fetchone()[0]
        v1_context = conn.execute("SELECT get_agent_runtime_model_context_v2(%s,'upgrade-v1-worker',%s)",
                                  (UUID(claim["run_id"]), claimed_run["execution_token"])).fetchone()[0]
    assert v1_context["definition_fact"]["definition_revision"] == "v1"
    assert v1_context["definition_fact"]["definition_document"]["prompt_revision"] == "agent-runtime-production-v1"
    assert v1_context["definition_fact"]["definition_document"]["system_prompt"].startswith("You are EVERYDAYAI.\n")
    v1_hash = v1_context["effective_toolset_fact"]["effective_toolset_hash"]
    with _connect(database, "everydayai_runtime_admin") as conn:
        _settings(conn, "everydayai_runtime_admin")
        conn.execute("SELECT set_config('app.access_kind','runtime_admin',false)")
        conn.execute("SELECT set_agent_runtime_definition_ingress_enabled('everydayai-default','v1',false)")
        conn.execute("SELECT set_agent_runtime_definition_ingress_enabled('everydayai-default','v2',true)")
    with _connect(database, "everydayai_agent_runtime_worker") as conn:
        _settings(conn, "everydayai_agent_runtime_worker")
        recovered = conn.execute("SELECT get_agent_runtime_model_context_v2(%s,'upgrade-v1-worker',%s)",
                                 (UUID(claim["run_id"]), claimed_run["execution_token"])).fetchone()[0]
    assert recovered["outcome"] == "found"
    assert recovered["definition_fact"]["definition_revision"] == "v1"
    assert recovered["effective_toolset_fact"]["effective_toolset_hash"] == v1_hash
    with pytest.raises(psycopg.Error, match="RUNTIME_VERSION_FACT_NOT_ENABLED"):
        _ingress(database, "upgrade-v1-after-disable", through=anchor)
    v2 = _ingress(database, "upgrade-v2", through=anchor_v2,
                   definition_hash=V2_DEFINITION_HASH, definition_revision="v2",
                   catalog_revision=V2_CATALOG_REVISION, conversation=conversation_v2,
                   toolset_hash=V2_TOOLSET_HASH)
    assert v2["outcome"] == "created"
    with _connect(database, "everydayai_agent_runtime_worker") as conn:
        _settings(conn, "everydayai_agent_runtime_worker")
        v2_claim = conn.execute("SELECT claim_pending_agent_command_and_ensure_run('upgrade-v2-worker',90,3)").fetchone()[0]
        v2_run = conn.execute("SELECT claim_next_agent_run('upgrade-v2-worker',90,3)").fetchone()[0]
        v2_context = conn.execute("SELECT get_agent_runtime_model_context_v2(%s,'upgrade-v2-worker',%s)",
                                  (UUID(v2_claim["run_id"]), v2_run["execution_token"])).fetchone()[0]
    assert v2_context["definition_fact"]["definition_revision"] == "v2"
    assert v2_context["definition_fact"]["definition_document"]["prompt_revision"] == "agent-runtime-production-v2"
    assert "Runtime v2" in v2_context["definition_fact"]["definition_document"]["system_prompt"]
    assert v2_context["catalog_fact"]["catalog_revision"] == V2_CATALOG_REVISION
    assert "catalog_probe" in [tool["canonical_name"] for tool in v2_context["catalog_fact"]["catalog_document"]["tools"]]
    assert v2_context["effective_toolset_fact"]["toolset_document"]["tool_names"] == ["code_execute"]
    from services.agent.runtime.catalog import restore_frozen_toolset
    restored = restore_frozen_toolset(
        v2_context["definition_fact"]["definition_document"],
        v2_context["catalog_fact"]["catalog_document"],
        v2_context["effective_toolset_fact"]["toolset_document"],
        catalog_revision=v2_context["catalog_fact"]["catalog_revision"],
    )
    assert [tool.canonical_name for tool in restored.definitions] == ["code_execute"]


def test_gate_drift_returns_original_command_and_toolset(database: str) -> None:
    with _connect(database, "everydayai_runtime") as conn:
        _settings(conn, "everydayai_runtime")
        conn.execute("SELECT report_agent_runtime_capability('tool_confirmation_v3_redis',true,'{}'::jsonb)")
    with _connect(database, "everydayai_sandbox_worker") as conn:
        _settings(conn, "everydayai_sandbox_worker")
        conn.execute("SELECT set_config('app.access_kind','sandbox_worker',false)")
        conn.execute("SELECT report_agent_runtime_worker_heartbeat('sandbox','gate-test','ar17-test',true,false,'ready','{}'::jsonb)")
    with _connect(database, "everydayai_runtime_admin") as conn:
        _settings(conn, "everydayai_runtime_admin")
        conn.execute("SELECT set_config('app.access_kind','runtime_admin',false)")
        conn.execute("SELECT set_agent_runtime_org_rollout(%s,%s,true,%s)", (uuid4(), ORG, "gate-test"))
    first_anchor = uuid4()
    second_anchor = uuid4()
    with _connect(database, "postgres") as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("INSERT INTO messages(id,conversation_id,org_id,role,content) VALUES(%s,%s,%s,'user','gate')", (first_anchor, CONVERSATION, ORG))
        conn.execute("INSERT INTO messages(id,conversation_id,org_id,role,content) VALUES(%s,%s,%s,'user','gate-2')", (second_anchor, CONVERSATION, ORG))
        conn.commit()
    _set_control(database, enabled=False)
    closed = _ingress(database, "gate-closed", through=first_anchor)
    assert closed["outcome"] == "created"
    assert closed["effective_toolset_hash"] == DISABLED_TOOLSET_HASH
    _set_control(database, enabled=True)
    reopened = list(ThreadPoolExecutor(max_workers=20).map(
        lambda _: _ingress(database, "gate-closed", through=first_anchor), range(50)))
    assert {row["entity_id"] for row in reopened} == {closed["entity_id"]}
    assert {row["outcome"] for row in reopened} == {"already_exists"}
    open_command = _ingress(database, "gate-open", through=second_anchor)
    assert open_command["outcome"] == "created"
    assert open_command["effective_toolset_hash"] == TOOLSET_HASH
    _set_control(database, enabled=False)
    closed_retry = list(ThreadPoolExecutor(max_workers=20).map(
        lambda _: _ingress(database, "gate-open", through=second_anchor), range(50)))
    assert {row["entity_id"] for row in closed_retry} == {open_command["entity_id"]}
    assert {row["outcome"] for row in closed_retry} == {"already_exists"}
    with _connect(database, "postgres") as conn:
        counts = conn.execute("SELECT count(*) FROM agent_session_commands WHERE idempotency_key IN ('gate-closed','gate-open')").fetchone()[0]
    assert counts == 2


def test_shared_catalog_disable_only_affects_definition(database: str) -> None:
    shared_revision = "v1-shared-test"
    shared_hash = "a" * 64
    with _connect(database, "postgres") as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("""INSERT INTO agent_runtime_definition_facts(
          agent_key,definition_revision,definition_hash,prompt_revision,
          catalog_revision,effective_toolset_hash,definition_document,
          enabled_for_new_ingress,recoverable)
          VALUES('everydayai-default',%s,%s,'agent-runtime-production-v1',%s,%s,%s::jsonb,true,true)""",
          (shared_revision, shared_hash, CATALOG_REVISION, TOOLSET_HASH,
           json.dumps({"canonical_key": "everydayai-default", "revision": shared_revision})))
        conn.execute("""INSERT INTO agent_runtime_effective_toolset_facts(
          agent_key,definition_revision,catalog_revision,scope_kind,channel,gate_state,
          effective_toolset_hash,toolset_document,enabled_for_new_ingress,recoverable)
          VALUES('everydayai-default',%s,%s,'user','web','enabled',%s,'{}'::jsonb,true,true)""",
          (shared_revision, CATALOG_REVISION, TOOLSET_HASH))
        conn.commit()
    with _connect(database, "everydayai_runtime_admin") as conn:
        _settings(conn, "everydayai_runtime_admin")
        conn.execute("SELECT set_config('app.access_kind','runtime_admin',false)")
        conn.execute("SELECT set_agent_runtime_definition_ingress_enabled('everydayai-default','v1',true)")
        conn.execute("SELECT set_agent_runtime_definition_ingress_enabled('everydayai-default','v1',false)")
    with _connect(database, "everydayai_runtime") as conn:
        _settings(conn, "everydayai_runtime")
        facts = conn.execute("SELECT get_agent_runtime_version_facts(%s,%s,%s,%s,%s,%s)", (
            "everydayai-default", shared_revision, CATALOG_REVISION, "user", "web", TOOLSET_HASH,
        )).fetchone()[0]
    assert facts["definition_fact"]["enabled_for_new_ingress"] is True
    assert facts["catalog_fact"]["enabled_for_new_ingress"] is True
    assert facts["effective_toolset_fact"]["enabled_for_new_ingress"] is True


def test_seed_rollback_preserves_later_version_facts(database: str) -> None:
    later_revision = "v3-test-later"
    later_hash = "b" * 64
    with _connect(database, "postgres") as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("""INSERT INTO agent_runtime_definition_facts(
          agent_key,definition_revision,definition_hash,prompt_revision,
          catalog_revision,effective_toolset_hash,definition_document,
          enabled_for_new_ingress,recoverable)
          VALUES('later-agent',%s,%s,'prompt-v3',%s,%s,'{}'::jsonb,true,true)""",
          (later_revision, later_hash, CATALOG_REVISION, TOOLSET_HASH))
        conn.commit()
    with _connect(database, "postgres") as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations/rollback/224_02_agent_runtime_ar17_version_seed_rollback.sql").read_text())
    with _connect(database, "postgres") as conn:
        seed_count = conn.execute("SELECT count(*) FROM agent_runtime_definition_facts WHERE agent_key='everydayai-default' AND definition_revision IN ('v1','v2')").fetchone()[0]
        later_count = conn.execute("SELECT count(*) FROM agent_runtime_definition_facts WHERE agent_key='later-agent' AND definition_revision=%s", (later_revision,)).fetchone()[0]
        catalog_count = conn.execute("SELECT count(*) FROM agent_runtime_catalog_facts WHERE catalog_revision=%s", (CATALOG_REVISION,)).fetchone()[0]
    assert seed_count == 0
    assert later_count == 1
    assert catalog_count == 1
