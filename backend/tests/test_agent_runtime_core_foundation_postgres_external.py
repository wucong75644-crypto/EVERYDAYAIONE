"""Real PostgreSQL psql contract for migration 212 in a disposable database."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import unittest
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/212_agent_runtime_core_foundation.sql"
ROLLBACK = ROOT / "migrations/rollback/212_agent_runtime_core_foundation_rollback.sql"
DATABASE_URL = os.getenv("AR06_TEST_DATABASE_URL", ""); USER_ID, ORG_ID = (
                   "11111111-1111-1111-1111-111111111111",
                   "22222222-2222-2222-2222-222222222222")
PERSONAL_CONVERSATION_ID = "33333333-3333-3333-3333-333333333333"
ORG_USER_ID = "44444444-4444-4444-4444-444444444444"
ORG_CONVERSATION_ID = "55555555-5555-5555-5555-555555555555"
CHANNEL_CONVERSATION_ID = "66666666-6666-6666-6666-666666666666"
SECOND_MEMBER_ID = "77777777-7777-7777-7777-777777777777"
def _psql(sql: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "psql",
            "--no-psqlrc",
            "--set=ON_ERROR_STOP=1",
            "--tuples-only",
            "--no-align",
            "--dbname",
            DATABASE_URL,
            "--command",
            sql,
        ],
        check=check,
        capture_output=True,
        text=True,
    )
def _file(path: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "psql",
            "--no-psqlrc",
            "--set=ON_ERROR_STOP=1",
            "--single-transaction",
            "--dbname",
            DATABASE_URL,
            "--file",
            str(path),
        ],
        check=check,
        capture_output=True,
        text=True,
    )
def _value(sql: str) -> str:
    return _psql(sql).stdout.strip().splitlines()[-1]
def _runtime(sql: str, *, user_id: str, org_id: str = "") -> str:
    prefix = f"""
        SET SESSION AUTHORIZATION everydayai_runtime;
        SELECT set_config('app.actor_user_id', '{user_id}', false);
        SELECT set_config('app.org_id', '{org_id}', false);
        SELECT set_config('app.access_kind', 'runtime', false);
        SELECT set_config('app.request_id', 'ar06-runtime', false);
    """
    return _value(prefix + sql)
def _worker(sql: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    prefix = """
        SET SESSION AUTHORIZATION everydayai_worker;
        SELECT set_config('app.actor_user_id', '', false);
        SELECT set_config('app.org_id', '', false);
        SELECT set_config('app.access_kind', 'worker', false);
        SELECT set_config('app.request_id', 'ar06-worker', false);
    """
    return _psql(prefix + sql, check=check)
BOOTSTRAP = """
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
DO $roles$
BEGIN
    IF to_regrole('everydayai_owner') IS NULL
        THEN CREATE ROLE everydayai_owner NOLOGIN; END IF;
    IF to_regrole('everydayai_runtime') IS NULL
        THEN CREATE ROLE everydayai_runtime NOLOGIN; END IF;
    IF to_regrole('everydayai_wecom_runtime') IS NULL
        THEN CREATE ROLE everydayai_wecom_runtime NOLOGIN; END IF;
    IF to_regrole('everydayai_worker') IS NULL
        THEN CREATE ROLE everydayai_worker NOLOGIN; END IF;
    IF to_regrole('everydayai_sync') IS NULL
        THEN CREATE ROLE everydayai_sync NOLOGIN; END IF;
    IF to_regrole('everydayai') IS NULL
        THEN CREATE ROLE everydayai NOLOGIN; END IF;
END
$roles$;
GRANT everydayai_owner, everydayai_runtime, everydayai_wecom_runtime,
      everydayai_worker, everydayai_sync, everydayai TO CURRENT_USER;
GRANT USAGE, CREATE ON SCHEMA public TO everydayai_owner;
GRANT USAGE ON SCHEMA public TO everydayai_runtime,
    everydayai_wecom_runtime, everydayai_worker;
SET ROLE everydayai_owner;
CREATE TABLE users(id UUID PRIMARY KEY, status TEXT NOT NULL DEFAULT 'active');
CREATE TABLE organizations(
    id UUID PRIMARY KEY, status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE org_members(
    org_id UUID NOT NULL REFERENCES organizations(id),
    user_id UUID NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'active', PRIMARY KEY(org_id, user_id)
);
CREATE TABLE conversations(
    id UUID PRIMARY KEY, user_id UUID REFERENCES users(id),
    org_id UUID REFERENCES organizations(id), scope_type TEXT NOT NULL,
    scope_id TEXT
);
CREATE FUNCTION tenant_actor_user_id() RETURNS UUID
LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN pg_input_is_valid(current_setting(
            'app.actor_user_id', TRUE), 'uuid')
        THEN current_setting('app.actor_user_id', TRUE)::UUID
    END
$$;
CREATE FUNCTION tenant_org_id() RETURNS UUID
LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN pg_input_is_valid(current_setting('app.org_id', TRUE), 'uuid')
        THEN current_setting('app.org_id', TRUE)::UUID
    END
$$;
INSERT INTO users(id) VALUES ('11111111-1111-1111-1111-111111111111'),
    ('44444444-4444-4444-4444-444444444444'),
    ('77777777-7777-7777-7777-777777777777');
INSERT INTO organizations(id)
VALUES ('22222222-2222-2222-2222-222222222222');
INSERT INTO org_members(org_id, user_id) VALUES
    ('22222222-2222-2222-2222-222222222222',
     '44444444-4444-4444-4444-444444444444'), ('22222222-2222-2222-2222-222222222222',
     '77777777-7777-7777-7777-777777777777');
INSERT INTO conversations(id, user_id, org_id, scope_type, scope_id) VALUES
    ('33333333-3333-3333-3333-333333333333',
     '11111111-1111-1111-1111-111111111111',
     NULL, 'user', '11111111-1111-1111-1111-111111111111'),
    ('55555555-5555-5555-5555-555555555555',
     '44444444-4444-4444-4444-444444444444',
     '22222222-2222-2222-2222-222222222222',
     'user', '44444444-4444-4444-4444-444444444444'),
    ('66666666-6666-6666-6666-666666666666', NULL,
     '22222222-2222-2222-2222-222222222222', 'channel', 'wecom:group:test');
RESET ROLE;
"""
@unittest.skipUnless(
    os.getenv("RUN_AR06_DB_TEST") == "1" and DATABASE_URL,
    "RUN_AR06_DB_TEST=1 and AR06_TEST_DATABASE_URL are required",
)
class AgentRuntimeCorePostgresContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if "ar06" not in DATABASE_URL.lower():
            raise unittest.SkipTest("dedicated AR06 database name required")
        _psql(BOOTSTRAP)
        _file(MIGRATION)
    def test_complete_real_postgres_contract(self) -> None:
        personal = json.loads(_runtime(
            f"""
            SELECT ensure_agent_runtime_session(
                '{PERSONAL_CONVERSATION_ID}', NULL, '{USER_ID}',
                'user', '{USER_ID}', '{USER_ID}', 'default', 'v1'
            );
            """,
            user_id=USER_ID,
        ))
        self.assertEqual(personal["outcome"], "created")
        session_id = personal["entity_id"]
        repeated = json.loads(_runtime(
            f"""
            SELECT ensure_agent_runtime_session(
                '{PERSONAL_CONVERSATION_ID}', NULL, '{USER_ID}',
                'user', '{USER_ID}', '{USER_ID}', 'default', 'v1'
            );
            """,
            user_id=USER_ID,
        ))
        self.assertEqual(repeated["entity_id"], session_id)
        self.assertEqual(repeated["outcome"], "already_exists")
        org_session = json.loads(_runtime(
            f"""
            SELECT ensure_agent_runtime_session(
                '{ORG_CONVERSATION_ID}', '{ORG_ID}', '{ORG_USER_ID}',
                'user', '{ORG_USER_ID}', '{ORG_USER_ID}', 'default', 'v1'
            );
            """,
            user_id=ORG_USER_ID,
            org_id=ORG_ID,
        ))
        self.assertEqual(org_session["outcome"], "created")
        channel_session = json.loads(_runtime(
            f"""
            SELECT ensure_agent_runtime_session(
                '{CHANNEL_CONVERSATION_ID}', '{ORG_ID}', NULL,
                'channel', 'wecom:group:test', '{ORG_USER_ID}',
                'default', 'v1'
            );
            """,
            user_id=ORG_USER_ID,
            org_id=ORG_ID,
        ))
        self.assertEqual(channel_session["outcome"], "created")
        shared_channel_command = json.loads(_runtime(
            f"""
            SELECT submit_session_command(
                '{channel_session["entity_id"]}', 'user_turn',
                'second-member', '{{}}'
            );
            """,
            user_id=SECOND_MEMBER_ID,
            org_id=ORG_ID,
        ))
        self.assertEqual(shared_channel_command["outcome"], "created")
        conflict = _psql(
            f"""
            SET SESSION AUTHORIZATION everydayai_runtime;
            SELECT set_config('app.actor_user_id', '{USER_ID}', false);
            SELECT set_config('app.org_id', '{ORG_ID}', false);
            SELECT set_config('app.access_kind', 'runtime', false);
            SELECT set_config('app.request_id', 'scope-conflict', false);
            SELECT ensure_agent_runtime_session(
                '{PERSONAL_CONVERSATION_ID}', '{ORG_ID}', '{USER_ID}',
                'user', '{USER_ID}', '{USER_ID}', 'default', 'v1'
            );
            """,
            check=False,
        )
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn("SCOPE_MISMATCH", conflict.stderr)
        command = json.loads(_runtime(
            f"""
            SELECT submit_session_command(
                '{session_id}', 'user_turn', 'command-1', '{{"text":"hi"}}'
            );
            """,
            user_id=USER_ID,
        ))
        repeated_command = json.loads(_runtime(
            f"""
            SELECT submit_session_command(
                '{session_id}', 'user_turn', 'command-1', '{{"text":"hi"}}'
            );
            """,
            user_id=USER_ID,
        ))
        self.assertEqual(repeated_command["entity_id"], command["entity_id"])
        self.assertEqual(repeated_command["outcome"], "already_exists")
        run = json.loads(_worker(
            f"""
            SELECT create_agent_run(
                '{session_id}', '{command["entity_id"]}', 'run-1', 'user',
                '{{}}', '{{}}', '{{}}'
            );
            """
        ).stdout.strip().splitlines()[-1])
        run_id = run["entity_id"]
        claim_sql = f"""
            SET SESSION AUTHORIZATION everydayai_worker;
            SELECT set_config('app.actor_user_id', '', false);
            SELECT set_config('app.org_id', '', false);
            SELECT set_config('app.access_kind', 'worker', false);
            SELECT set_config('app.request_id', 'concurrent-claim', false);
            SELECT claim_agent_run('{run_id}', 'worker-a', 90, 3);
        """
        processes = [
            subprocess.Popen(
                [
                    "psql", "--no-psqlrc", "--set=ON_ERROR_STOP=1",
                    "--tuples-only", "--no-align",
                    "--dbname", DATABASE_URL, "--command", claim_sql,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        claims = [
            json.loads(process.communicate()[0].strip().splitlines()[-1])
            for process in processes
        ]
        self.assertEqual(
            sorted(item["outcome"] for item in claims),
            ["busy", "claimed"],
        )
        first_claim = next(
            item for item in claims if item["outcome"] == "claimed"
        )
        first_token = first_claim["execution_token"]
        _psql(
            "SET ROLE everydayai_owner;"
            f"UPDATE agent_runs SET lease_expires_at = clock_timestamp()"
            f" - INTERVAL '1 second' WHERE id = '{run_id}';"
        )
        second_claim = json.loads(_worker(
            f"SELECT claim_agent_run('{run_id}', 'worker-b', 90, 3);"
        ).stdout.strip().splitlines()[-1])
        second_token = second_claim["execution_token"]
        self.assertNotEqual(first_token, second_token)
        fenced = json.loads(_worker(
            f"SELECT renew_agent_run('{run_id}', '{first_token}', 90);"
        ).stdout.strip().splitlines()[-1])
        self.assertEqual(fenced["outcome"], "ownership_lost")
        step = json.loads(_worker(
            f"""
            SELECT create_model_step(
                '{run_id}', '{second_token}', 'model', 'provider',
                'model-v1', 'prompt-v1', 'tools-v1', '{{}}'
            );
            """
        ).stdout.strip().splitlines()[-1])
        completed_step = json.loads(_worker(
            f"""
            SELECT complete_model_step(
                '{step["entity_id"]}', '{second_token}', 0, '{{"hash":"x"}}',
                'final', 'stop', 1, 2, 0
            );
            """
        ).stdout.strip().splitlines()[-1])
        self.assertEqual(completed_step["outcome"], "completed")
        run_version = _value(
            "SET ROLE everydayai_owner;"
            f"SELECT state_version FROM agent_runs WHERE id = '{run_id}';"
        )
        completed_run = json.loads(_worker(
            f"""
            SELECT complete_agent_run(
                '{run_id}', '{second_token}', {run_version}, 'result-hash'
            );
            """
        ).stdout.strip().splitlines()[-1])
        self.assertEqual(completed_run["outcome"], "completed")
        irreversible = json.loads(_worker(
            f"""
            SELECT fail_agent_run(
                '{run_id}', '{second_token}',
                {completed_run["state_version"]}, 'late-failure'
            );
            """
        ).stdout.strip().splitlines()[-1])
        self.assertEqual(irreversible["outcome"], "terminal_conflict")
        sequence_contract = _value(
            "SET ROLE everydayai_owner;"
            f"SELECT count(*) = max(sequence)"
            f" AND min(sequence) = 1"
            f" AND count(*) = count(DISTINCT sequence)"
            f" FROM agent_runtime_events WHERE session_id = '{session_id}';"
        )
        self.assertEqual(sequence_contract, "t")
        outbox_claims = json.loads(_worker(
            "SELECT claim_agent_projection_outbox(100, 60);"
        ).stdout.strip().splitlines()[-1])
        self.assertGreaterEqual(len(outbox_claims), 2)
        first_outbox, second_outbox = outbox_claims[:2]
        outbox_completed = json.loads(_worker(
            f"""
            SELECT complete_agent_projection_outbox(
                '{first_outbox["id"]}', '{first_outbox["lease_token"]}',
                '{{"sequence":1}}'
            );
            """
        ).stdout.strip().splitlines()[-1])
        self.assertEqual(outbox_completed["outcome"], "completed")
        outbox_failed = json.loads(_worker(
            f"""
            SELECT fail_agent_projection_outbox(
                '{second_outbox["id"]}', '{second_outbox["lease_token"]}',
                'transport'
            );
            """
        ).stdout.strip().splitlines()[-1])
        self.assertEqual(outbox_failed["outcome"], "failed")
        stale_outbox = json.loads(_worker(
            f"""
            SELECT complete_agent_projection_outbox(
                '{second_outbox["id"]}', '{second_outbox["lease_token"]}',
                '{{}}'
            );
            """
        ).stdout.strip().splitlines()[-1])
        self.assertEqual(stale_outbox["outcome"], "ownership_lost")
        direct_table = _psql(
            "SET SESSION AUTHORIZATION everydayai_runtime;"
            "SELECT * FROM agent_runs LIMIT 1;",
            check=False,
        )
        self.assertNotEqual(direct_table.returncode, 0)
        self.assertIn("permission denied", direct_table.stderr)
        matrix = _value("""
            SELECT
                has_function_privilege('everydayai_runtime',
                    'submit_session_command(uuid,text,text,jsonb)', 'EXECUTE')
                AND NOT has_function_privilege('everydayai_runtime',
                    'claim_agent_run(uuid,text,integer,integer)', 'EXECUTE')
                AND has_function_privilege('everydayai_worker',
                    'claim_agent_run(uuid,text,integer,integer)', 'EXECUTE')
                AND has_function_privilege('everydayai_wecom_runtime',
                    'submit_session_command(uuid,text,text,jsonb)', 'EXECUTE')
                AND NOT has_table_privilege('everydayai_wecom_runtime',
                    'agent_runs', 'SELECT')
                AND NOT has_function_privilege(
                    'everydayai_worker',
                    'append_agent_runtime_event('
                    'uuid,text,uuid,uuid,uuid,text,text,jsonb,text[])',
                    'EXECUTE'
                )
                AND NOT has_function_privilege(
                    'public',
                    'append_agent_runtime_event('
                    'uuid,text,uuid,uuid,uuid,text,text,jsonb,text[])',
                    'EXECUTE'
                )
                AND NOT has_table_privilege('public', 'agent_runs', 'SELECT');
        """)
        self.assertEqual(matrix, "t")
        force_rls = _value("""
            SELECT count(*) = 7
              FROM pg_class
             WHERE relname = ANY(ARRAY[
                 'agent_runtime_sessions', 'agent_session_commands',
                 'agent_runs', 'agent_run_attempts', 'agent_model_steps',
                 'agent_runtime_events', 'agent_projection_outbox'
             ])
               AND relrowsecurity AND relforcerowsecurity;
        """)
        self.assertEqual(force_rls, "t")
        _psql("""
            SET ROLE everydayai_owner;
            CREATE FUNCTION reject_agent_outbox() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'FORCED_OUTBOX_FAILURE'; END
            $$;
            CREATE TRIGGER reject_agent_outbox
            BEFORE INSERT ON agent_projection_outbox
            FOR EACH ROW EXECUTE FUNCTION reject_agent_outbox();
        """)
        event_count_before = _value(
            "SET ROLE everydayai_owner;"
            f"SELECT count(*) FROM agent_runtime_events"
            f" WHERE session_id = '{session_id}';"
        )
        atomic_failure = _psql(
            f"""
            SET SESSION AUTHORIZATION everydayai_runtime;
            SELECT set_config('app.actor_user_id', '{USER_ID}', false);
            SELECT set_config('app.org_id', '', false);
            SELECT set_config('app.access_kind', 'runtime', false);
            SELECT set_config('app.request_id', 'forced-failure', false);
            SELECT submit_session_command(
                '{session_id}', 'user_turn', 'forced-failure', '{{}}'
            );
            """,
            check=False,
        )
        self.assertNotEqual(atomic_failure.returncode, 0)
        self.assertIn("FORCED_OUTBOX_FAILURE", atomic_failure.stderr)
        atomic_state = _value(
            "SET ROLE everydayai_owner;"
            f"SELECT (SELECT count(*) FROM agent_session_commands"
            f" WHERE idempotency_key = 'forced-failure') = 0"
            f" AND (SELECT count(*) FROM agent_runtime_events"
            f" WHERE session_id = '{session_id}') = {event_count_before};"
        )
        self.assertEqual(atomic_state, "t")
        _psql(
            "SET ROLE everydayai_owner;"
            "DROP TRIGGER reject_agent_outbox ON agent_projection_outbox;"
            "DROP FUNCTION reject_agent_outbox();"
        )
        rollback_with_facts = _file(ROLLBACK, check=False)
        self.assertNotEqual(rollback_with_facts.returncode, 0)
        self.assertIn(
            "AGENT_RUNTIME_ROLLBACK_FACTS_PRESENT",
            rollback_with_facts.stderr,
        )
        _psql(
            "SET ROLE everydayai_owner;"
            "TRUNCATE agent_runtime_sessions CASCADE;"
        )
        _file(ROLLBACK)
        self.assertEqual(
            _value(
                "SELECT count(*) FROM pg_class"
                " WHERE relname LIKE 'agent_%' AND relkind = 'r';"
            ),
            "0",
        )
        _file(MIGRATION)
        self.assertEqual(
            _value(
                "SELECT count(*) FROM pg_class"
                " WHERE relname = ANY(ARRAY["
                "'agent_runtime_sessions','agent_session_commands',"
                "'agent_runs','agent_run_attempts','agent_model_steps',"
                "'agent_runtime_events','agent_projection_outbox']);"
            ),
            "7",
        )
