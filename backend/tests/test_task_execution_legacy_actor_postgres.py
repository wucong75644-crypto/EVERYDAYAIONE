"""Run existing opt-in Actor SQL contracts in an owned disposable database.

The child process imports the legacy tests with explicit local test opt-ins.
Those tests roll back their data. The installed new close branch and shared
checkpoint function remain present; this is legacy compatibility evidence,
not a complete new-task pause/cancel acceptance test.
"""
from pathlib import Path
import os,subprocess,sys
import psycopg
import pytest
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

@pytest.mark.parametrize('control_variant',['repository','production'])
def test_existing_actor_protocols_with_new_close_branch_installed(continuation_db,tmp_path,control_variant):
    import xml.etree.ElementTree as ET
    import re
    install_legacy_actor_schema(continuation_db)
    with psycopg.connect(continuation_db) as c:
        if control_variant=='production':
            c.execute((ROOT/'tests/fixtures/conversation_actor_live_control_variants.sql').read_text())
        identities=['public.pause_generation_turn_owned(uuid,uuid,text)','public.cancel_generation_turn_owned(uuid,uuid,text)']
        before={name:c.execute('SELECT proowner,prosrc,proacl,prosecdef FROM pg_proc WHERE oid=%s::regprocedure',(name,)).fetchone() for name in identities}
        def effective_acl(name):
            return c.execute("SELECT grantee,privilege_type,is_grantable FROM pg_proc p, LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) WHERE p.oid=%s::regprocedure ORDER BY grantee,privilege_type,is_grantable",(name,)).fetchall()
        before_acl={name:effective_acl(name) for name in identities}
    env=dict(os.environ,RUN_CONVERSATION_ACTOR_DB_TEST='1',CONVERSATION_ACTOR_TEST_DATABASE_URL=continuation_db)
    def run_original_suite(label):
        xml=tmp_path/(label+'.xml')
        result=subprocess.run([sys.executable,'-m','pytest',
            'tests/test_conversation_actor_pause_snapshot_integration.py','tests/test_conversation_actor_recovery_integration.py',
            'tests/test_conversation_actor_resume_integration.py','tests/test_conversation_delivery_session_integration.py',
            'tests/test_replay_checkpoint_integration.py','-q','--tb=long','--junitxml',str(xml)],cwd=ROOT,env=env,capture_output=True,text=True)
        (tmp_path/(label+'.log')).write_text(result.stdout+result.stderr)
        cases=list(ET.parse(xml).iter('testcase'))
        assert len(cases)==7 and not any(v.find('skipped') is not None or v.find('error') is not None for v in cases)
        failures={v.attrib['name'] for v in cases if v.find('failure') is not None}
        signature=None
        if control_variant=='repository':
            assert result.returncode==0 and failures==set(),result.stdout[-10000:]
        else:
            # Preserve the actual installed behavior, including this pre-existing
            # pause-marker mismatch. Do not weaken the original integration test
            # or silently replace production's snapshot helper during migration.
            assert result.returncode==1 and failures=={'test_pause_defers_running_task_then_owner_saves_snapshot_and_cancel_wins'}
            failure=next(v.find('failure') for v in cases if v.find('failure') is not None)
            location=re.findall(r'(tests/[^\s:]+\.py):(\d+)',failure.text)[-1]
            assert location==('tests/test_conversation_actor_pause_snapshot_integration.py','231')
            message=re.sub(r'0x[0-9a-fA-F]+','0xADDR',failure.attrib['message'])
            assert message.startswith('assert False') or message.startswith('AssertionError: assert False')
            signature=(location,message)
        return failures,signature
    def pause_cancel_roundtrip():
        from tests.test_conversation_actor_pause_snapshot_integration import _fixture,_rpc,_content_blocks
        with psycopg.connect(continuation_db) as connection:
            with connection.cursor() as cur:
                data=_fixture(cur)
                assert _rpc(cur,data['task_id'],None,data['user_id'],'pause')['outcome']=='requested'
                cur.execute("SELECT save_generation_checkpoint(%s,%s,'before_model',%s::jsonb)",(data['task_id'],data['execution_token'],'{"messages":[]}'))
                assert cur.fetchone()[0]['outcome']=='saved'
                paused=_rpc(cur,data['task_id'],data['execution_token'],None,'pause')
                assert paused['outcome']=='paused' and paused['snapshot_saved'] is True
                cur.execute('SELECT status,execution_token,terminal_reason FROM tasks WHERE id=%s',(data['task_id'],))
                assert cur.fetchone()==('paused',None,'user_paused')
                cur.execute('SELECT status,content,context_revision FROM messages WHERE id=%s',(data['output_message_id'],))
                status,content,revision=cur.fetchone();blocks=_content_blocks(content)
                assert status=='interrupted' and revision==1
                texts=tuple(b.get('text') for b in blocks if b.get('type')=='text')
                assert any('最后一段' in (v or '') for v in texts)
                markers=tuple(b.get('reason') for b in blocks if b.get('type')=='interrupt_marker')
                assert markers==('user_cancel' if control_variant=='production' else 'user_pause',)
                cur.execute('SELECT context_revision FROM messages WHERE id=%s',(data['input_message_id'],))
                assert cur.fetchone()==(revision,)
                assert _rpc(cur,data['task_id'],data['execution_token'],None,'pause')['outcome']=='already_paused'
                assert _rpc(cur,data['task_id'],None,data['user_id'],'cancel')['outcome']=='cancelled'
                cur.execute('SELECT status,terminal_reason FROM tasks WHERE id=%s',(data['task_id'],))
                assert cur.fetchone()==('cancelled','user_cancelled')
                connection.rollback()
                return status,revision,texts,markers
    baseline=run_original_suite('before-wrapper') if control_variant=='production' else (set(),None)
    baseline_roundtrip=pause_cancel_roundtrip()
    installed=False
    try:
        with psycopg.connect(continuation_db) as c:
            c.execute((ROOT/'migrations/345_agent_task_controls.sql').read_text())
            c.execute((ROOT/'migrations/346_agent_task_actor_control_lock_order.sql').read_text())
            for name,original in before.items():
                legacy=name.replace('public.','public.agent_task_legacy_')
                assert c.execute('SELECT proowner,prosrc,proacl,prosecdef FROM pg_proc WHERE oid=%s::regprocedure',(legacy,)).fetchone()==original
                wrapper=c.execute('SELECT proowner,prosecdef FROM pg_proc WHERE oid=%s::regprocedure',(name,)).fetchone()
                assert wrapper==(original[0],original[3]) and effective_acl(name)==before_acl[name]
        installed=True
        assert run_original_suite('after-wrapper')==baseline
        assert pause_cancel_roundtrip()==baseline_roundtrip
    finally:
        if installed:
            with psycopg.connect(continuation_db) as c:
                c.execute((ROOT/'migrations/rollback/346_agent_task_actor_control_lock_order_rollback.sql').read_text())
                for name,original in before.items():
                    assert c.execute('SELECT proowner,prosrc,proacl,prosecdef FROM pg_proc WHERE oid=%s::regprocedure',(name,)).fetchone()==original
                c.execute((ROOT/'migrations/rollback/345_agent_task_controls_rollback.sql').read_text())


def test_unreviewed_actor_body_still_fails_closed_without_partial_wrapper(continuation_db):
    from psycopg import sql
    install_legacy_actor_schema(continuation_db)
    with pytest.raises(psycopg.errors.RaiseException,match='TASK_ACTOR_CONTROL_CONTRACT_UNSUPPORTED'):
        with psycopg.connect(continuation_db) as c:
            original=c.execute("SELECT prosrc FROM pg_proc WHERE oid='public.pause_generation_turn_owned(uuid,uuid,text)'::regprocedure").fetchone()[0]
            c.execute(sql.SQL("CREATE OR REPLACE FUNCTION public.pause_generation_turn_owned(p_task_id uuid,p_execution_token uuid,p_reason text DEFAULT 'user_paused') RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS {}").format(sql.Literal(original+'\n-- unreviewed body')))
            c.execute((ROOT/'migrations/345_agent_task_controls.sql').read_text())
            c.execute((ROOT/'migrations/346_agent_task_actor_control_lock_order.sql').read_text())
    with psycopg.connect(continuation_db) as c:
        assert c.execute("SELECT to_regprocedure('public.agent_task_legacy_pause_generation_turn_owned(uuid,uuid,text)'),to_regprocedure('public.agent_task_legacy_cancel_generation_turn_owned(uuid,uuid,text)')").fetchone()==(None,None)
