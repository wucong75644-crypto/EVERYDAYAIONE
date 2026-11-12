"""Real checkpoint transactions: optional notes never own execution facts."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_task_execution_postgres import (
    task_db, composition_db, ecom_db, isolated_db, seed, connect, save, task_payload,
)
from tests.test_model_directed_records import bootstrap, Registry
from services.task_execution.contracts import TaskState
from services.task_execution.model_directed import record_calls, update_working_plan
from services.tools import ToolCall

ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def direct_db(task_db):
    with psycopg.connect(task_db) as c:
        c.execute((ROOT/'migrations/371_agent_task_model_directed_records.sql').read_text())
    yield task_db
    with psycopg.connect(task_db) as c:
        c.execute("DELETE FROM conversation_turn_checkpoints WHERE state ? '_task_execution'")
        c.execute('DELETE FROM agent_task_states')
        c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        c.execute((ROOT/'migrations/rollback/371_agent_task_model_directed_records_rollback.sql').read_text())


def initial(dsn):
    f=seed(dsn)
    b=replace(bootstrap(), task_id=UUID(f['task']), conversation_id=UUID(f['conv']),
        actor_user_id=UUID(f['user']), input_message_id=UUID(f['message']))
    b=replace(b, execution_identity=b.execution_identity.model_copy(update={'workspace_owner_id':f['user']}))
    s=record_calls(None,b,[ToolCall('call-1','generate_image',{'prompt':'原提示词'})],Registry())
    p={'messages':[{'role':'user','content':s.original_goal}], '_task_execution':s.model_dump(mode='json'),
       'context_extension':{'original':'保留完整上下文'}}
    with connect(f) as c:assert save(c,f,p)['outcome']=='saved'
    return f,b,s,p


def read(f):
    with connect(f) as c:
        state=c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',(f['task'],)).fetchone()[0]
        checkpoint=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
    return state,checkpoint


def test_append_calls_and_change_optional_plan_in_the_original_atomic_checkpoint(direct_db):
    f,b,s,p=initial(direct_db)
    s=record_calls(s,b,[ToolCall('call-2','generate_image',{'prompt':'第二张原提示词'})],Registry())
    s=update_working_plan(s,{'steps':[{'step':'完成规划','status':'completed'}]})
    p={**p,'_task_execution':s.model_dump(mode='json')}
    with connect(f) as c:assert save(c,f,p,expected=0)['outcome']=='saved'
    stored,checkpoint=read(f)
    assert stored==checkpoint['_task_execution']==s.model_dump(mode='json')
    assert len(stored['steps'])==2 and stored['results']==[] and stored['status']=='active'
    assert checkpoint['context_extension']=={'original':'保留完整上下文'}
    s=update_working_plan(s,{'steps':[]})
    with connect(f) as c:assert save(c,f,{**p,'_task_execution':s.model_dump(mode='json')},expected=p['_task_execution']['revision'])['outcome']=='saved'
    stored,_=read(f)
    assert len(stored['steps'])==2 and stored['working_plan']['steps']==[]


@pytest.mark.parametrize('mutation',['call_arguments','step_title','remove_call','remove_step','erase_goal','policy'])
def test_saved_call_identity_and_user_input_cannot_be_rewritten_by_plan_updates(direct_db,mutation):
    f,b,s,p=initial(direct_db);before=read(f)
    candidate=deepcopy(p);raw=candidate['_task_execution'];raw['revision']+=1
    if mutation=='call_arguments':raw['call_bindings'][0]['arguments_sha256']='f'*64
    elif mutation=='step_title':raw['steps'][0]['title']='改计划来重复派发'
    elif mutation=='remove_call':raw['call_bindings']=[]
    elif mutation=='remove_step':raw['steps']=[]
    elif mutation=='erase_goal':raw['original_goal']='另一个目标'
    elif mutation=='policy':raw.pop('model_directed');raw.pop('call_bindings')
    with pytest.raises(psycopg.Error):
        with connect(f) as c:save(c,f,candidate,expected=s.revision)
    assert read(f)==before


def test_legacy_tasks_keep_the_old_schema_and_cannot_switch_policy_in_a_resume(direct_db):
    f=seed(direct_db);p=task_payload(f)
    with connect(f) as c:assert save(c,f,p)['outcome']=='saved'
    raw=deepcopy(p['_task_execution']);raw['revision']+=1
    raw['working_plan']={'explanation':'','steps':[]}
    with pytest.raises(psycopg.Error,match='TASK_DIRECT_POLICY_REQUIRED'):
        with connect(f) as c:save(c,f,{**p,'_task_execution':raw},expected=p['_task_execution']['revision'])
    assert read(f)[0]==p['_task_execution']


def test_checkpoint_failure_rolls_back_new_calls_and_plan(direct_db):
    f,b,s,p=initial(direct_db);before=read(f)
    s=record_calls(s,b,[ToolCall('call-2','generate_image',{'prompt':'second'})],Registry())
    with psycopg.connect(direct_db) as c:
        c.execute("CREATE FUNCTION reject_direct_checkpoint() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'checkpoint failed'; END $$")
        c.execute('CREATE TRIGGER reject_direct_checkpoint BEFORE INSERT OR UPDATE ON conversation_turn_checkpoints FOR EACH ROW EXECUTE FUNCTION reject_direct_checkpoint()')
    try:
        with pytest.raises(psycopg.Error,match='checkpoint failed'):
            with connect(f) as c:save(c,f,{**p,'_task_execution':s.model_dump(mode='json')},expected=0)
        assert read(f)==before
    finally:
        with psycopg.connect(direct_db) as c:
            c.execute('DROP TRIGGER reject_direct_checkpoint ON conversation_turn_checkpoints')
            c.execute('DROP FUNCTION reject_direct_checkpoint()')


def test_rollback_cannot_strand_a_new_checkpoint_on_a_legacy_binary(direct_db):
    initial(direct_db)
    with pytest.raises(psycopg.Error,match='TASK_DIRECT_COMPATIBLE_RUNTIME_REQUIRED'):
        with psycopg.connect(direct_db) as c:
            c.execute((ROOT/'migrations/rollback/371_agent_task_model_directed_records_rollback.sql').read_text())


@pytest.mark.parametrize('kind',['bootstrap','notes'])
def test_rollback_also_protects_context_before_first_business_call(direct_db,kind):
    # This database is unique to this module and disposable; remove older cases
    # so the checkpoint-only guard must prove itself with zero logical states.
    with psycopg.connect(direct_db) as c:
        c.execute('DELETE FROM conversation_turn_checkpoints');c.execute('DELETE FROM agent_task_states')
    f=seed(direct_db)
    cp={'messages':[{'role':'user','content':'保留原上下文'}]}
    if kind=='bootstrap':cp['_task_bootstrap']={'input':{'model_directed':True}}
    else:cp['_task_direct_plan']={'steps':[]}
    try:
        with connect(f) as c:c.execute("SELECT save_generation_checkpoint(%s,%s,'before_model',%s)",(f['task'],f['token'],Jsonb(cp)))
        with pytest.raises(psycopg.Error,match='TASK_DIRECT_COMPATIBLE_RUNTIME_REQUIRED'):
            with psycopg.connect(direct_db) as c:c.execute((ROOT/'migrations/rollback/371_agent_task_model_directed_records_rollback.sql').read_text())
        with connect(f) as c:assert c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]['messages']==cp['messages']
    finally:
        with psycopg.connect(direct_db) as c:c.execute('DELETE FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],))


def test_model_directed_image_plan_append_is_atomic_and_preserves_prior_native_records(direct_db):
    from types import SimpleNamespace
    from services.skills.contracts import SkillCatalogMetadata
    from services.task_execution.direct_loop import save_image_plan
    from tests.test_task_execution_methods import document

    migration = ROOT / 'migrations/376_agent_task_image_method_handoff.sql'
    rollback = ROOT / 'migrations/rollback/376_agent_task_image_method_handoff_rollback.sql'
    with psycopg.connect(direct_db) as c:
        c.execute(migration.read_text())
    try:
        f, b, s, payload = initial(direct_db)
        candidate = SimpleNamespace(package_id=UUID(f['user']), catalog_metadata=SkillCatalogMetadata(task_stage={
            'output_profile': 'reference-prompts-v1',
        }))
        skills = SimpleNamespace(directory={'planner': candidate}, active={'planner': SimpleNamespace(revision='v1', body_sha256='a' * 64)})
        runtime = SimpleNamespace(task_id=f['task'], task_bootstrap=b, task_execution_state=s, skill_runtime=skills)
        from services.task_execution.model_directed import SAVE_IMAGE_PLAN
        import json
        call = {'name': SAVE_IMAGE_PLAN, 'id': 'save-five',
                'arguments': json.dumps({'image_count': 5, 'document': json.loads(document(5))})}
        proposed, artifact = save_image_plan(runtime, call, Registry())
        with connect(f) as c:
            assert save(c, f, {**payload, '_task_execution': proposed.model_dump(mode='json')},
                        expected=s.revision)['outcome'] == 'saved'
        stored, checkpoint = read(f)
        assert stored == checkpoint['_task_execution']
        assert stored['root_task_id'] == f['task']
        assert stored['artifacts'][-1]['id'] == artifact.id
        assert stored['call_bindings'][0] == s.call_bindings[0].model_dump(mode='json')
    finally:
        with psycopg.connect(direct_db) as c:
            c.execute("DELETE FROM conversation_turn_checkpoints WHERE state ? '_task_execution'")
            c.execute('DELETE FROM agent_task_states')
            c.execute('SET CONSTRAINTS ALL IMMEDIATE')
            c.execute(rollback.read_text())
