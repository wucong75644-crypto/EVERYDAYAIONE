"""Install/drain revision migrations in disposable local PostgreSQL fixtures."""
from pathlib import Path
import psycopg
ROOT=Path(__file__).parents[2]


def install(dsn):
    with psycopg.connect(dsn) as c:
        c.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
        c.execute((ROOT/'migrations/138_conversation_control_events.sql').read_text().split('CREATE OR REPLACE FUNCTION append_conversation_control_command')[0])
        c.execute('ALTER TABLE conversation_control_events OWNER TO everydayai')
        c.execute('ALTER SEQUENCE conversation_control_event_sequence_seq OWNER TO everydayai')
        c.execute('SET LOCAL ROLE everydayai')
        c.execute((ROOT/'migrations/242_conversation_actor_steer.sql').read_text())
        c.execute('RESET ROLE')
        for name in ('363_agent_task_user_changes','364_agent_task_requirement_revisions','365_agent_task_child_revision_gates'):
            c.execute((ROOT/f'migrations/{name}.sql').read_text())


def drain(dsn):
    with psycopg.connect(dsn) as c:
        # Fixture database is unique and disposable; no production connection.
        roots=[v[0] for v in c.execute("SELECT root_task_id FROM agent_task_states WHERE state->>'image_binding_version'='3'").fetchall()]
        c.execute('DELETE FROM conversation_turn_checkpoints WHERE task_id IN (SELECT owner_task_id FROM agent_task_states WHERE root_task_id=ANY(%s))',(roots,))
        c.execute('DELETE FROM agent_task_states WHERE root_task_id=ANY(%s)',(roots,))
        c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        for name in ('365_agent_task_child_revision_gates','364_agent_task_requirement_revisions','363_agent_task_user_changes'):
            c.execute((ROOT/f'migrations/rollback/{name}_rollback.sql').read_text())
        c.execute("DELETE FROM conversation_control_events WHERE event_type='steer'")
