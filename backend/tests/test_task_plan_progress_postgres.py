"""Plan-only presentation reuses the original fenced checkpoint and scoped read."""
from pathlib import Path
from uuid import uuid4
import psycopg
import pytest
from tests.test_task_execution_progress_postgres import progress_db,task_db,composition_db,ecom_db,isolated_db,seed,connect
from tests.test_skill_composition_postgres import save
from core.local_db import LocalDBClient
from core.db_scope import ScopedDatabaseClient,DatabaseScope,DatabaseAccessKind
from services.task_execution.progress import read

ROOT=Path(__file__).parents[1]
PLAN={'explanation':'沿用原文件','steps':[{'step':'读取表格','status':'completed'},{'step':'统计并形成报告','status':'in_progress'}]}

@pytest.fixture(scope='module')
def plan_db(progress_db):
    with psycopg.connect(progress_db) as c:c.execute((ROOT/'migrations/373_agent_task_plan_progress.sql').read_text())
    yield progress_db
    with psycopg.connect(progress_db) as c:c.execute((ROOT/'migrations/rollback/373_agent_task_plan_progress_rollback.sql').read_text())


def prepare(dsn, plan=PLAN):
    f=seed(dsn);f['assistant']=str(uuid4())
    with psycopg.connect(dsn) as c:
        c.execute("INSERT INTO messages(id,conversation_id,role,content) VALUES(%s,%s,'assistant','[]')",(f['assistant'],f['conv']))
        c.execute('UPDATE tasks SET assistant_message_id=%s WHERE id=%s',(f['assistant'],f['task']))
    payload={'messages':[{'role':'system','content':'PRIVATE original context'}],
        '_task_direct_plan':plan,'_task_direct_plan_owner':f['task']}
    with connect(f) as c:assert save(c,f,payload)['outcome']=='saved'
    return f


def test_plan_before_tools_has_names_without_creating_operations_or_claiming_delivery(plan_db):
    f=prepare(plan_db)
    db=LocalDBClient(psycopg.conninfo.make_conninfo(plan_db,user='everydayai'),min_size=1,max_size=2)
    scoped=ScopedDatabaseClient(db,DatabaseScope(f['user'],None,DatabaseAccessKind.RUNTIME))
    try:
        value=read(scoped,root_task_id=f['task'],message_id=f['assistant'],user_id=f['user'],org_id=None)
        assert value['plan_only'] and value['working_plan']==PLAN
        assert value['status']=='active' and value['completed_steps']==value['total_steps']==0
        assert value['completed_requirements']==0 and 'PRIVATE' not in str(value)
        with psycopg.connect(plan_db) as c:
            assert c.execute('SELECT count(*) FROM agent_task_states WHERE root_task_id=%s',(f['task'],)).fetchone()[0]==0
            c.execute("UPDATE conversation_turn_checkpoints SET status='paused' WHERE task_id=%s",(f['task'],))
        assert read(scoped,root_task_id=f['task'],message_id=f['assistant'],user_id=f['user'],org_id=None)['status']=='paused'
        with psycopg.connect(plan_db) as c:
            c.execute("UPDATE conversation_turn_checkpoints SET status='consumed' WHERE task_id=%s",(f['task'],))
            c.execute("UPDATE tasks SET status='completed' WHERE id=%s",(f['task'],))
        value=read(scoped,root_task_id=f['task'],message_id=f['assistant'],user_id=f['user'],org_id=None)
        assert value['status']=='completed' and value['working_plan']['steps'][1]['status']=='in_progress'
    finally:db.close()


@pytest.mark.parametrize('fault',['other_user','other_org','user_message','other_message','wrong_plan_owner','invalid_checkpoint','deleted_message','inactive_user','wrong_turn','channel'])
def test_checkpoint_plan_read_does_not_weaken_source_or_current_identity_checks(plan_db,fault):
    f=prepare(plan_db);message=f['assistant'];owner=dict(f)
    if fault=='other_user':owner['user']=str(uuid4())
    elif fault=='other_org':
        owner['org']=str(uuid4())
    elif fault=='user_message':message=f['message']
    elif fault=='other_message':message=str(uuid4())
    else:
        with psycopg.connect(plan_db) as c:
            if fault=='wrong_plan_owner':c.execute("UPDATE conversation_turn_checkpoints SET state=jsonb_set(state,'{_task_direct_plan_owner}',to_jsonb(%s::text)) WHERE task_id=%s",(str(uuid4()),f['task']))
            if fault=='invalid_checkpoint':c.execute("UPDATE conversation_turn_checkpoints SET status='invalid' WHERE task_id=%s",(f['task'],))
            if fault=='deleted_message':c.execute('DELETE FROM messages WHERE id=%s',(f['assistant'],))
            if fault=='inactive_user':c.execute("UPDATE users SET status='inactive' WHERE id=%s",(f['user'],))
            if fault=='wrong_turn':c.execute('UPDATE conversation_turn_checkpoints SET turn_id=%s WHERE task_id=%s',(str(uuid4()),f['task']))
            if fault=='channel':c.execute('UPDATE conversations SET user_id=NULL WHERE id=%s',(f['conv'],))
    with connect(owner) as c:
        if fault=='other_org':c.execute("SELECT set_config('app.org_id',%s,true)",(owner['org'],))
        assert c.execute('SELECT read_agent_task_progress(%s,%s)',(f['task'],message)).fetchone()[0]=={'outcome':'not_found'}


def test_original_execution_view_and_migration_rollback_remain_compatible(plan_db):
    from tests.test_task_execution_progress_postgres import prepared,view
    f=prepared(plan_db);notes=prepare(plan_db)
    with connect(f) as c:
        original=view(c,f)
        assert original['outcome']=='found' and 'state' in original and 'plan_only' not in original
    try:
        with psycopg.connect(plan_db) as c:c.execute((ROOT/'migrations/rollback/373_agent_task_plan_progress_rollback.sql').read_text())
        with connect(f) as c:assert view(c,f)==original
        with connect(notes) as c:
            assert c.execute('SELECT read_agent_task_progress(%s,%s)',(notes['task'],notes['assistant'])).fetchone()[0]=={'outcome':'not_found'}
    finally:
        with psycopg.connect(plan_db) as c:c.execute((ROOT/'migrations/373_agent_task_plan_progress.sql').read_text())
    with connect(notes) as c:
        result=c.execute('SELECT read_agent_task_progress(%s,%s)',(notes['task'],notes['assistant'])).fetchone()[0]
        assert result['plan_only'] and result['working_plan']==PLAN


def test_enterprise_plan_read_rechecks_active_membership(plan_db):
    f=prepare(plan_db);org=str(uuid4())
    with psycopg.connect(plan_db) as c:
        c.execute('INSERT INTO organizations(id) VALUES(%s)',(org,))
        c.execute('INSERT INTO org_members(org_id,user_id) VALUES(%s,%s)',(org,f['user']))
        c.execute('UPDATE tasks SET org_id=%s WHERE id=%s',(org,f['task']))
        c.execute('UPDATE conversations SET org_id=%s WHERE id=%s',(org,f['conv']))
    with connect(f) as c:
        c.execute("SELECT set_config('app.org_id',%s,true)",(org,))
        assert c.execute('SELECT read_agent_task_progress(%s,%s)',(f['task'],f['assistant'])).fetchone()[0]['working_plan']==PLAN
    with psycopg.connect(plan_db) as c:
        c.execute("UPDATE org_members SET status='inactive' WHERE org_id=%s AND user_id=%s",(org,f['user']))
    with connect(f) as c:
        c.execute("SELECT set_config('app.org_id',%s,true)",(org,))
        assert c.execute('SELECT read_agent_task_progress(%s,%s)',(f['task'],f['assistant'])).fetchone()[0]=={'outcome':'not_found'}


@pytest.fixture(scope='module')
def plan_followup_db(plan_db):
    with psycopg.connect(plan_db) as connection:
        connection.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS execution_mode text DEFAULT 'serial', "
                           "ADD COLUMN IF NOT EXISTS queue_sequence bigint")
        connection.execute('ALTER TABLE messages ADD COLUMN IF NOT EXISTS turn_id uuid')
        connection.execute((ROOT/'migrations/374_agent_working_plan_followups.sql').read_text())
    yield plan_db
    with psycopg.connect(plan_db) as connection:
        connection.execute((ROOT/'migrations/rollback/374_agent_working_plan_followups_rollback.sql').read_text())


def next_plan_turn(first, sequence):
    turn = {**first, **{name: str(uuid4()) for name in ('task', 'turn', 'token', 'message', 'assistant')}}
    with psycopg.connect(turn['dsn']) as connection:
        connection.execute("""INSERT INTO messages(id,conversation_id,turn_id,role,content)
            VALUES(%s,%s,%s,'user','[]'),(%s,%s,%s,'assistant','[]')""",
            (turn['message'], turn['conv'], turn['turn'], turn['assistant'], turn['conv'], turn['turn']))
        connection.execute("""INSERT INTO tasks(id,user_id,conversation_id,type,status,input_message_id,
            assistant_message_id,turn_id,execution_token,lease_expires_at,delivery_context,base_context_revision,
            request_params,execution_mode,queue_sequence)
            VALUES(%s,%s,%s,'chat','running',%s,%s,%s,%s,now()+interval '10 minutes','{"actor":true}',
                0,'{}','serial',%s)""",
            (turn['task'], turn['user'], turn['conv'], turn['message'], turn['assistant'],
             turn['turn'], turn['token'], sequence))
    return turn


def plan_candidates(turn, token=None):
    with connect(turn) as connection:
        return connection.execute('SELECT read_agent_working_plan_candidates(%s,%s)',
            (turn['task'], token or turn['token'])).fetchone()[0]


@pytest.mark.asyncio
async def test_edit_then_create_independent_plan_across_real_checkpoints(plan_followup_db):
    import json
    from dataclasses import replace
    from uuid import UUID
    from tests.test_model_directed_loop import case, execute
    from tests.test_task_execution_model_bridge import call
    from services.task_execution.direct_loop import restore_plan

    original_plan = {'explanation': '', 'steps': [
        {'step': name, 'status': 'pending'}
        for name in ('梳理需求要点', '编写文字方案', '检查方案完整性')
    ]}
    first = prepare(plan_followup_db, original_plan)  # Old record with no explicit plan ID.
    with psycopg.connect(plan_followup_db) as connection:
        connection.execute("UPDATE tasks SET status='completed', execution_mode='serial', queue_sequence=1 WHERE id=%s",
            (first['task'],))
    second = next_plan_turn(first, 2)
    assert plan_candidates(second, str(uuid4())) == {'outcome': 'not_found'}
    assert plan_candidates({**second, 'user': str(uuid4())}) == {'outcome': 'not_found'}
    original, = plan_candidates(second)['candidates']

    async def record(turn, arguments):
        async def checkpoint(point, payload):
            with connect(turn) as connection:
                return save(connection, turn, payload)
        runtime, prepared, handler, request = case(checkpoint)
        runtime.task_id = turn['task']
        runtime.task_bootstrap = replace(runtime.task_bootstrap, task_id=UUID(turn['task']))
        runtime.task_plan_candidates = {p['plan_id']: p for p in plan_candidates(turn)['candidates']}
        blocks = await execute(runtime, prepared, handler, request, [call(arguments=arguments)])
        handler._execute_tool_calls.assert_not_awaited()
        assert runtime.task_execution_state is None
        return runtime, blocks

    edited_plan = {**original_plan, 'steps': [
        original_plan['steps'][0], {'step': '编写简短文字方案', 'status': 'pending'}, original_plan['steps'][2],
    ]}
    edited, blocks = await record(second, {**edited_plan,
        'plan_id': original['plan_id'], 'expected_plan_version': original['version']})
    marker, = [b for b in blocks if b['type']=='task_progress']
    assert marker['plan_id']==first['task'] and marker['root_task_id']==second['task']
    with connect(second) as connection:
        persisted = connection.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',
            (second['task'],)).fetchone()[0]
        value = connection.execute('SELECT read_agent_task_progress(%s,%s)',
            (second['task'], second['assistant'])).fetchone()[0]
    assert value['working_plan']==edited_plan
    restore_plan(edited, persisted)
    assert edited.task_direct_plan_id==first['task']

    with psycopg.connect(plan_followup_db) as connection:
        connection.execute("UPDATE tasks SET status='completed' WHERE id=%s", (second['task'],))
    third = next_plan_turn(first, 3)
    latest, = plan_candidates(third)['candidates']
    assert latest['plan_id']==first['task'] and latest['working_plan']==edited_plan
    assert latest['version']!=original['version']
    _, stale = await record(third, {**edited_plan,
        'plan_id': original['plan_id'], 'expected_plan_version': original['version']})
    assert json.loads(stale[0]['output'])['code']=='TASK_WORKING_PLAN_TARGET_CHANGED'
    _, independent = await record(third, PLAN)
    marker, = [b for b in independent if b['type']=='task_progress']
    assert 'plan_id' not in marker and marker['root_task_id']==third['task']

    with psycopg.connect(plan_followup_db) as connection:
        connection.execute("UPDATE tasks SET status='completed' WHERE id=%s", (third['task'],))
    fourth = next_plan_turn(first, 4)
    assert [p['plan_id'] for p in plan_candidates(fourth)['candidates']]==[third['task'], first['task']]
