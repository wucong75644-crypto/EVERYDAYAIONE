"""Atomic checkpoints, stale acceptance, fencing and rollback on real PostgreSQL."""
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_chat_image_lifecycle_postgres import isolated_db
from tests.test_ecommerce_recovery_postgres import ecom_db

ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def composition_db(ecom_db):
    with psycopg.connect(ecom_db) as c:
        c.execute('ALTER TABLE tasks ADD COLUMN lease_expires_at timestamptz')
        schema=(ROOT/'migrations/239_conversation_actor_production_contract_compat.sql').read_text()
        table=schema[schema.index('CREATE TABLE IF NOT EXISTS public.conversation_turn_checkpoints'):schema.index('CREATE OR REPLACE FUNCTION public.append_conversation_control_command')]
        c.execute(table)
        c.execute('GRANT SELECT,INSERT,UPDATE ON conversation_turn_checkpoints TO everydayai')
        c.execute((ROOT/'migrations/339_skill_composition.sql').read_text())
    return ecom_db


def seed(dsn):
    user,conv,task,message,turn,token=[str(uuid4()) for _ in range(6)]
    with psycopg.connect(dsn) as c:
        c.execute('INSERT INTO users(id,credits) VALUES(%s,100)',(user,))
        c.execute('INSERT INTO conversations(id,user_id) VALUES(%s,%s)',(conv,user))
        c.execute("INSERT INTO messages(id,conversation_id,role,content) VALUES(%s,%s,'user','[]')",(message,conv))
        c.execute("""INSERT INTO tasks(id,user_id,conversation_id,type,status,input_message_id,turn_id,
            execution_token,lease_expires_at,delivery_context,base_context_revision,request_params)
            VALUES(%s,%s,%s,'chat','running',%s,%s,%s,now()+interval '10 minutes','{"actor":true}',0,'{}')""",
            (task,user,conv,message,turn,token))
    return dict(dsn=dsn,user=user,conv=conv,task=task,message=message,turn=turn,token=token)


def connect(f):
    c=psycopg.connect(psycopg.conninfo.make_conninfo(f['dsn'],user='everydayai'))
    c.execute("SELECT set_config('app.actor_user_id',%s,true),set_config('app.org_id','',true),set_config('app.access_kind','runtime',true)",(f['user'],))
    return c


def payload(f,revision=1,expected=0):
    from services.skills.coordination import Composition
    composition=Composition(turn_id=f['turn'],revision=revision).model_dump(mode='json')
    return {'skill_runtime':{'version':2,'composition':composition},'_skill_composition':composition,
            '_skill_composition_expected_revision':expected,'messages':[]}


def save(c,f,p,token=None):
    return c.execute("SELECT save_generation_checkpoint(%s,%s,'after_skill_activation',%s)",
                     (f['task'],token or f['token'],Jsonb(p))).fetchone()[0]


def test_checkpoint_and_projection_commit_together_and_retries_are_idempotent(composition_db):
    f=seed(composition_db)
    p=payload(f)
    with connect(f) as c:
        assert save(c,f,p)['outcome']=='saved'
        assert save(c,f,p)['outcome']=='saved'
        task=c.execute("SELECT request_params->'_skill_composition' FROM tasks WHERE id=%s",(f['task'],)).fetchone()[0]
        checkpoint=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert task==checkpoint['_skill_composition']==p['_skill_composition']


def test_stale_revision_and_wrong_owner_do_not_mutate_projection(composition_db):
    f=seed(composition_db)
    with connect(f) as c:
        assert save(c,f,payload(f))['outcome']=='saved'
    with connect(f) as c:
        assert save(c,f,payload(f,2,1),token=str(uuid4()))['outcome']=='ownership_lost'
    with connect(f) as c:
        with pytest.raises(psycopg.errors.SerializationFailure,match='SKILL_COMPOSITION_STALE'):
            save(c,f,payload(f,2,0))
    with psycopg.connect(composition_db) as c:
        assert c.execute("SELECT request_params->'_skill_composition'->>'revision' FROM tasks WHERE id=%s",(f['task'],)).fetchone()[0]=='1'


def test_checkpoint_insert_failure_rolls_back_task_projection(composition_db):
    f=seed(composition_db)
    with psycopg.connect(composition_db) as c:
        c.execute("""CREATE FUNCTION reject_test_checkpoint() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected checkpoint failure'; END $$""")
        c.execute('CREATE TRIGGER reject_test_checkpoint BEFORE INSERT ON conversation_turn_checkpoints FOR EACH ROW EXECUTE FUNCTION reject_test_checkpoint()')
    try:
        with connect(f) as c:
            with pytest.raises(psycopg.Error,match='injected checkpoint failure'):
                save(c,f,payload(f))
        with psycopg.connect(composition_db) as c:
            assert c.execute("SELECT request_params ? '_skill_composition' FROM tasks WHERE id=%s",(f['task'],)).fetchone()[0] is False
            assert c.execute('SELECT count(*) FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]==0
    finally:
        with psycopg.connect(composition_db) as c:
            c.execute('DROP TRIGGER reject_test_checkpoint ON conversation_turn_checkpoints')
            c.execute('DROP FUNCTION reject_test_checkpoint()')


def test_v2_cannot_be_downgraded_and_missing_revision_is_rejected(composition_db):
    f=seed(composition_db)
    p=payload(f);p['_skill_composition'].pop('revision')
    with connect(f) as c:
        with pytest.raises(psycopg.Error,match='INCONSISTENT'):
            save(c,f,p)
    with connect(f) as c:
        save(c,f,payload(f))
    with connect(f) as c:
        with pytest.raises(psycopg.Error,match='DOWNGRADE_DENIED'):
            save(c,f,{'skill_runtime':{'version':1}})


def test_image_acceptance_uses_saved_composition_and_does_not_double_submit(composition_db):
    from services.handlers.chat_image_request import freeze_image_request
    f=seed(composition_db)
    with connect(f) as c:
        save(c,f,payload(f))
    origin=dict(parent_task_id=f['task'],actor_user_id=f['user'],workspace_owner_id=f['user'],
                org_id=None,context_scope='user',conversation_id=f['conv'],turn_id=f['turn'],
                input_message_id=f['message'],base_context_revision=0,tool_call_id='call-1',skill_composition_revision=1)
    snapshot=freeze_image_request({'mode':'text_to_image','prompt':'exact'},[],origin=origin,max_requests=4,max_credits=100)
    with connect(f) as c:
        first=c.execute('SELECT accept_chat_image_request(%s,%s,%s)',(f['task'],f['token'],Jsonb(snapshot))).fetchone()[0]
        second=c.execute('SELECT accept_chat_image_request(%s,%s,%s)',(f['task'],f['token'],Jsonb(snapshot))).fetchone()[0]
        assert first['task_id']==second['task_id']
        save(c,f,payload(f,2,1))
    origin['tool_call_id']='call-2'
    stale=freeze_image_request({'mode':'text_to_image','prompt':'exact'},[],origin=origin,max_requests=4,max_credits=100)
    with connect(f) as c:
        with pytest.raises(psycopg.Error,match='SKILL_COMPOSITION_STALE'):
            c.execute('SELECT accept_chat_image_request(%s,%s,%s)',(f['task'],f['token'],Jsonb(stale)))
    with psycopg.connect(composition_db) as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE type='image' AND user_id=%s",(f['user'],)).fetchone()[0]==1


def test_rollback_refuses_to_erase_v2_runtime_support(composition_db):
    f=seed(composition_db)
    with connect(f) as c:
        save(c,f,payload(f))
    with psycopg.connect(composition_db) as c:
        with pytest.raises(psycopg.Error,match='REQUIRES_DUAL_READER'):
            c.execute((ROOT/'migrations/rollback/339_skill_composition_rollback.sql').read_text())


def test_workflow_projection_and_composition_identity_are_one_transaction(composition_db):
    from services.agent.image.ecommerce_planner.workflow import WorkflowBinding
    f=seed(composition_db)
    value=WorkflowBinding(kind='ordinary_image',root_task_id=f['task'],window_task_id=f['task'],
        generation_run_id=str(uuid4())).model_dump(mode='json')
    p=payload(f)
    p['_ecom_workflow']=value
    with connect(f) as c:
        with pytest.raises(psycopg.Error,match='WORKFLOW_INCONSISTENT'):
            with c.transaction():
                save(c,f,p)
        assert c.execute("SELECT request_params FROM tasks WHERE id=%s",(f['task'],)).fetchone()[0]=={}
        p['_skill_composition']['workflow_links']={'ecommerce':value}
        assert save(c,f,p)['outcome']=='saved'
        stored=c.execute("SELECT request_params FROM tasks WHERE id=%s",(f['task'],)).fetchone()[0]
        assert stored['_skill_composition']['workflow_links']['ecommerce']==stored['_ecom_workflow']==value
