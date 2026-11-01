"""Legacy source identity and frozen closed-history enforcement on PostgreSQL."""
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace
import psycopg
from psycopg.types.json import Jsonb
import pytest
from tests.test_task_execution_continuation_postgres import continuation_db,children_db,operation_db,task_db,composition_db,ecom_db,isolated_db
from tests.test_task_execution_postgres import task_payload,save
from tests.test_skill_composition_postgres import seed,connect
from tests.test_task_execution_methods import document

ROOT=Path(__file__).parents[1]

@pytest.fixture(scope='module')
def source_db(continuation_db):
    with psycopg.connect(continuation_db) as c:
        c.execute('SET LOCAL ROLE everydayai_owner')
        c.execute((ROOT/'migrations/344_agent_task_legacy_sources.sql').read_text())
    yield continuation_db
    with psycopg.connect(continuation_db) as c:
        c.execute('SET LOCAL ROLE everydayai_owner')
        c.execute((ROOT/'migrations/rollback/344_agent_task_legacy_sources_rollback.sql').read_text())


def case(dsn):
    f=seed(dsn);payload=task_payload(f);source_task,message=str(uuid4()),str(uuid4())
    with connect(f) as c: save(c,f,payload)
    with psycopg.connect(dsn) as c:
        c.execute('UPDATE tasks SET base_context_revision=2 WHERE id=%s',(f['task'],))
        c.execute("INSERT INTO messages(id,conversation_id,role,content,status,context_revision) VALUES(%s,%s,'assistant',%s,'completed',2)",
            (message,f['conv'],Jsonb([{'type':'text','text':document()}])))
        c.execute("INSERT INTO tasks(id,user_id,conversation_id,type,status,assistant_message_id) VALUES(%s,%s,%s,'chat','completed',%s)",
            (source_task,f['user'],f['conv'],message))
    return f,payload,source_task,message


def read(f,payload,source_id,*,kind='legacy_message',revision=None,token=None):
    with connect(f) as c:
        return c.execute('SELECT read_agent_task_legacy_source(%s,%s,%s,%s,%s,%s,%s)',
            (f['task'],token or f['token'],f['task'],payload['_task_execution']['revision'],kind,source_id,revision)).fetchone()[0]


def test_real_old_message_is_read_exactly_with_its_closed_owner(source_db):
    f,payload,old,message=case(source_db)
    result=read(f,payload,message)
    assert result['outcome']=='found' and result['text']==document()
    assert result['source_task_id']==old


@pytest.mark.parametrize('current',[False,True])
def test_discovery_lists_only_ready_plans_in_the_same_closed_history(source_db,current):
    from tests.test_task_execution_legacy_sources import plan
    f,payload,old,message=case(source_db)
    original=plan()
    with psycopg.connect(source_db) as c:
        c.execute("""INSERT INTO ecom_image_plans(id,user_id,conversation_id,parent_task_id,input_message_id,
            base_context_revision,invocation_key,input_digest,status,current_stage,image_count,input_snapshot,
            prompt_versions,model_settings,stage_outputs,items,target_size,plan_revision)
            VALUES(%s,%s,%s,%s,%s,0,'original',%s,'ready',3,5,'{}','{}','{}',%s,%s,%s,4)""",
            (original['id'],f['user'],f['conv'],f['task'] if current else old,f['message'],'a'*64,
             Jsonb(original['stage_outputs']),Jsonb(original['items']),Jsonb(original['target_size'])))
    found=read(f,payload,None,kind='discover')['sources']
    entries=[s for s in found if s['kind']=='ecom_plan']
    if current:assert entries==[]  # Current ready IDs come from the native tool return.
    else:
        assert entries[0]['source_id']==original['id'] and entries[0]['source_revision']==4 and entries[0]['image_count']==5
    imported=read(f,payload,original['id'],kind='ecom_plan',revision=4)
    assert imported['plan']['items']==original['items'] and imported['source_task_id']==(f['task'] if current else old)
    with psycopg.connect(source_db) as c:
        c.execute('UPDATE messages SET context_revision=3 WHERE id=%s',(message,))
    assert read(f,payload,None,kind='discover')['sources']==[]
    assert read(f,payload,original['id'],kind='ecom_plan',revision=4)['outcome']==('found' if current else 'not_found')


@pytest.mark.parametrize('changed',['future','other_actor','pending','wrong_token','branch'])
def test_legacy_source_cannot_escape_frozen_history_or_scope(source_db,changed):
    f,payload,old,message=case(source_db)
    with psycopg.connect(source_db) as c:
        if changed=='future': c.execute('UPDATE messages SET context_revision=3 WHERE id=%s',(message,))
        elif changed=='other_actor':
            user=uuid4();c.execute('INSERT INTO users(id,credits) VALUES(%s,100)',(user,))
            c.execute('UPDATE tasks SET user_id=%s WHERE id=%s',(user,old))
        elif changed=='pending': c.execute("UPDATE tasks SET status='running' WHERE id=%s",(old,))
        elif changed=='branch': c.execute("UPDATE tasks SET execution_mode='branch' WHERE id=%s",(f['task'],))
    if changed=='branch':
        with pytest.raises(psycopg.errors.InsufficientPrivilege): read(f,payload,message)
    else:
        result=read(f,payload,message,token=str(uuid4()) if changed=='wrong_token' else None)
        assert result['outcome']==('ownership_lost' if changed=='wrong_token' else 'not_found')
