"""Run existing opt-in Actor SQL contracts in an owned disposable database.

The child process imports the legacy tests with explicit local test opt-ins.
Those tests roll back their data. The installed new close branch and shared
checkpoint function remain present; this is legacy compatibility evidence,
not a complete new-task pause/cancel acceptance test.
"""
from pathlib import Path
import os,subprocess,sys
import psycopg
from tests.test_task_execution_continuation_postgres import continuation_db,children_db,operation_db,task_db,composition_db,ecom_db,isolated_db

ROOT=Path(__file__).parents[1]

def install_legacy_actor_schema(continuation_db):
    with psycopg.connect(continuation_db) as c:
        c.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname text, ALTER COLUMN credits SET DEFAULT 100')
        c.execute('ALTER TABLE conversations ADD COLUMN IF NOT EXISTS title text')
        c.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS accumulated_content text, ADD COLUMN IF NOT EXISTS accumulated_blocks jsonb DEFAULT '[]', ADD COLUMN IF NOT EXISTS fail_code text, ADD COLUMN IF NOT EXISTS client_task_id text")
        schema=(ROOT/'tests/fixtures/conversation_actor_pause_snapshot_schema.sql').read_text()
        if not c.execute("SELECT to_regclass('conversation_control_events')").fetchone()[0]:
            c.execute(schema[schema.index('CREATE SEQUENCE conversation_control_event_sequence_seq'):])
        c.execute((ROOT/'migrations/123_conversation_actor_progress.sql').read_text())
        c.execute('ALTER TABLE conversation_control_events OWNER TO everydayai')
        c.execute('ALTER SEQUENCE conversation_control_event_sequence_seq OWNER TO everydayai')
        c.execute((ROOT/'migrations/239_conversation_actor_production_contract_compat.sql').read_text())
        c.execute((ROOT/'migrations/240_conversation_actor_text_content_compat.sql').read_text())
        c.execute((ROOT/'migrations/241_conversation_delivery_session.sql').read_text())
        c.execute('ALTER TABLE conversation_delivery_sessions OWNER TO everydayai')
        c.execute('ALTER TABLE conversation_delivery_events OWNER TO everydayai')
        c.execute((ROOT/'migrations/248_conversation_delivery_event_idempotency.sql').read_text())
        c.execute((ROOT/'migrations/339_skill_composition.sql').read_text())
        owner=c.execute("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='conversation_turn_checkpoints'::regclass").fetchone()[0]
        for name in ('pause_generation_turn_owned','cancel_generation_turn_owned'):
            c.execute(f'ALTER FUNCTION {name}(uuid,uuid,text) OWNER TO {owner}')

def test_existing_actor_protocols_with_new_close_branch_installed(continuation_db,tmp_path):
    install_legacy_actor_schema(continuation_db)
    with psycopg.connect(continuation_db) as c:
        c.execute((ROOT/'migrations/345_agent_task_controls.sql').read_text())
        c.execute((ROOT/'migrations/346_agent_task_actor_control_lock_order.sql').read_text())
    env=dict(os.environ,RUN_CONVERSATION_ACTOR_DB_TEST='1',CONVERSATION_ACTOR_TEST_DATABASE_URL=continuation_db)
    result=subprocess.run([sys.executable,'-m','pytest',
        'tests/test_conversation_actor_pause_snapshot_integration.py','tests/test_conversation_actor_recovery_integration.py',
        'tests/test_conversation_actor_resume_integration.py','tests/test_conversation_delivery_session_integration.py',
        'tests/test_replay_checkpoint_integration.py','-q','-x'],cwd=ROOT,env=env,capture_output=True,text=True)
    (tmp_path/'actor-legacy-contracts.log').write_text(result.stdout+result.stderr)
    assert result.returncode==0,result.stdout[-10000:]+result.stderr[-1000:]

    assert '7 passed' in result.stdout and 'skipped' not in result.stdout

    with psycopg.connect(continuation_db) as c:
        c.execute((ROOT/'migrations/rollback/346_agent_task_actor_control_lock_order_rollback.sql').read_text())
        c.execute((ROOT/'migrations/rollback/345_agent_task_controls_rollback.sql').read_text())
