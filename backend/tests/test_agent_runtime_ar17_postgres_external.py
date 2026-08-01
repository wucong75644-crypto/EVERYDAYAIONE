"""AR-17.1 real PostgreSQL contract, including isolation and cleanup."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
from uuid import UUID, uuid4

import psycopg
import pytest

from tests.test_agent_runtime_model_attempt_postgres_external import CREDITS_BOOTSTRAP

pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
ORG = UUID("22222222-2222-2222-2222-222222222222")
USER = UUID("44444444-4444-4444-4444-444444444444")
CONVERSATION = UUID("55555555-5555-5555-5555-555555555555")
DEFINITION_HASH = "b5818e976876aa8c0ead0b50ebea8439fe0e230e9d55dfac9e7d5580d18895ff"
CATALOG_REVISION = "7e449bf4ca2a4827d5fa96df4721c4978d9a1d96e0215012500669e5ac2eb131"
TOOLSET_HASH = "897d940de4aa6ebca9a5df0197824ac906f6cea2469461c5ec0ae88e595d90fc"
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


@pytest.fixture(scope="module")
def database():
    if os.getenv("RUN_AR17_1_DB_TEST") != "1":
        pytest.skip("RUN_AR17_1_DB_TEST=1 required")
    port = _free_port()
    data_dir = Path(tempfile.mkdtemp(prefix="ar17-1-pg-", dir="/private/tmp"))
    url = f"postgresql://postgres@127.0.0.1:{port}/postgres"
    try:
        _run(["/opt/homebrew/bin/initdb", "-D", str(data_dir), "-U", "postgres", "--auth-host=trust", "--auth-local=trust"])
        _run(["/opt/homebrew/bin/pg_ctl", "-D", str(data_dir), "-o", f"-p {port}", "-l", str(data_dir / "postgres.log"), "-w", "start"], capture=False)
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
        with psycopg.connect(url) as conn:
            with conn.transaction():
                conn.execute((ROOT / "migrations/224_01_agent_runtime_ar17_core.sql").read_text())
        with psycopg.connect(url) as conn:
            with conn.transaction():
                conn.execute((ROOT / "migrations/rollback/224_01_agent_runtime_ar17_core_rollback.sql").read_text())
        with psycopg.connect(url) as conn:
            with conn.transaction():
                conn.execute((ROOT / "migrations/224_01_agent_runtime_ar17_core.sql").read_text())
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
        _run(["/opt/homebrew/bin/pg_ctl", "-D", str(data_dir), "-m", "fast", "-w", "stop"], capture=False)
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


def _ingress(url: str, key: str, *, role: str = "everydayai_runtime", channel: str = "web",
             through: UUID | None = None, definition_hash: str = DEFINITION_HASH):
    through = through or uuid4()
    with _connect(url, role) as conn:
        _settings(conn, role)
        row = conn.execute("""
          SELECT runtime_submit_ingress_v2(
            %s::uuid,%s::uuid,%s::uuid,'user',%s::text,%s::uuid,
            'everydayai-default','v1',%s::text,'submit_input',%s::text,%s::text,
            %s::uuid,%s::text,%s::text,%s::text,'{}'::jsonb,
            '{"requested_groups":["code"]}'::jsonb,%s::text,%s::jsonb)
        """, (CONVERSATION, ORG, USER, str(USER), USER, definition_hash, key, channel,
               through, f"message:{through}", CATALOG_REVISION, TOOLSET_HASH,
               "ar17-test", '{"task_id":null}')).fetchone()
        return row[0]


def test_real_ingress_claim_context_permissions_and_rollback(database: str) -> None:
    with _connect(database, "everydayai_runtime_admin") as conn:
        _settings(conn, "everydayai_runtime_admin")
        conn.execute("SELECT set_config('app.access_kind', 'runtime_admin', false)")
        conn.execute("SELECT set_agent_runtime_control(%s,0,%s,%s)", (
            uuid4(), '{"ingress_enabled":true,"release_revision":"ar17-test","config_revision":"ar17-test"}', "test",
        ))
        conn.execute("SELECT set_agent_runtime_org_rollout(%s,%s,true,%s)", (uuid4(), ORG, "test"))
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
    with pytest.raises(psycopg.Error, match="RUNTIME_INGRESS_V2_BINDING_INVALID"):
        _ingress(database, "ar17-real-key", through=anchor, definition_hash="0" * 64)
    with _connect(database, "everydayai_wecom_runtime") as conn:
        _settings(conn, "everydayai_wecom_runtime")
        task_id = uuid4()
        row = conn.execute("""SELECT enqueue_wecom_runtime_turn_v4(
          %s::jsonb,%s::uuid,%s::uuid,%s::uuid,%s::jsonb,%s::jsonb,
          'everydayai-default','v1',%s::text,%s::text,%s::text,'ar17-test','ar17-wecom-key')""",
                           (f'{{"id":"{task_id}","user_id":"{USER}","org_id":"{ORG}","conversation_id":"{CONVERSATION}"}}',
                            anchor, uuid4(), uuid4(), '["hello"]',
                            '{"actor":true,"channel":"wecom","chatid":"group-1","transport":"app"}',
                            DEFINITION_HASH, CATALOG_REVISION, TOOLSET_HASH)).fetchone()
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
        security = conn.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid='agent_runtime_definition_facts'::regclass").fetchone()
        privileges = conn.execute("SELECT has_function_privilege('everydayai_runtime','runtime_submit_ingress_v2(uuid,uuid,uuid,text,text,uuid,text,text,text,text,text,text,uuid,text,text,text,jsonb,jsonb,text,jsonb)','execute'), has_table_privilege('everydayai_runtime','agent_runtime_definition_facts','select')").fetchone()
    assert security == (True, True)
    assert privileges == (True, False)
    with pytest.raises(psycopg.Error, match="AGENT_RUNTIME_224_ROLLBACK_GUARD_FACTS_EXIST"):
        with psycopg.connect(database) as conn:
            with conn.transaction():
                conn.execute((ROOT / "migrations/rollback/224_01_agent_runtime_ar17_core_rollback.sql").read_text())
